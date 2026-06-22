# utils/db.py
"""
Async SQLite state management for WxAlert SPCBot.

All persistent bot state is stored in a single SQLite database using WAL mode
for safety. The DatabaseManager class provides a thread-safe async interface
with automatic retry on lock contention and graceful degradation on failure.

Tables:
  image_hashes  — URL -> hash mapping for change detection
  posted_mds    — set of posted MD numbers
  posted_watches — set of posted watch numbers
  posted_warnings — set of posted NWS warning VTEC ETNs (e.g. "KOUN.TO.W.0042")
  bot_state     — key/value store for simple state (ncar, csu_mlp, prefs, etc.)
"""

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Iterable, Optional

import aiosqlite

from config import CACHE_DIR

logger = logging.getLogger("spc_bot")

DB_PATH = os.path.join(CACHE_DIR, "bot_state.db")
_LOCK = asyncio.Lock()
_db: Optional[aiosqlite.Connection] = None
_read_pool: asyncio.Queue[aiosqlite.Connection] = asyncio.Queue()
_READ_POOL_SIZE = 10  # raised from 3 — multiple slash commands + watchdog reads
_last_product_cache_prune: float = 0.0
_PRODUCT_CACHE_PRUNE_INTERVAL = 3600.0  # 1 hour

# Failure counter so the watchdog / health surface can notice when the
# DB is silently dropping writes. Swallowing every exception by itself
# would turn a full disk or schema drift into an invisible outage.
_write_failure_count: int = 0
_WRITE_FAILURE_ALERT_THRESHOLD = 5


def get_write_failure_count() -> int:
    return _write_failure_count


def reset_write_failure_count() -> None:
    global _write_failure_count
    _write_failure_count = 0


async def _write(sql: str, params: tuple, op: str) -> None:
    """Serialized write helper. Logs and swallows errors so callers degrade gracefully."""
    global _write_failure_count
    try:
        db = await get_db()
        async with _LOCK:
            await db.execute(sql, params)
            await db.commit()
        if _write_failure_count:
            _write_failure_count = 0
    except Exception as e:
        _write_failure_count += 1
        level = (
            logger.error
            if _write_failure_count >= _WRITE_FAILURE_ALERT_THRESHOLD
            else logger.warning
        )
        level(f"{op} failed ({_write_failure_count} consecutive): {e}")


async def _write_many(sql: str, rows: Iterable[tuple], op: str) -> None:
    """Serialized batch-write helper."""
    global _write_failure_count
    try:
        db = await get_db()
        async with _LOCK:
            await db.executemany(sql, rows)
            await db.commit()
        if _write_failure_count:
            _write_failure_count = 0
    except Exception as e:
        _write_failure_count += 1
        level = (
            logger.error
            if _write_failure_count >= _WRITE_FAILURE_ALERT_THRESHOLD
            else logger.warning
        )
        level(f"{op} failed ({_write_failure_count} consecutive): {e}")


async def get_db() -> aiosqlite.Connection:
    """Get or create the shared write connection (singleton)."""
    global _db
    if _db is not None:
        return _db
    async with _LOCK:
        # Check again inside lock in case another coroutine connected first
        if _db is None:
            _db = await _connect()

            # Populate read pool once write connection is established
            for _ in range(_READ_POOL_SIZE):
                conn = await _connect(read_only=True)
                await _read_pool.put(conn)

    return _db


@asynccontextmanager
async def get_read_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """Check out a read-only connection from the pool."""
    # Ensure pool is initialized (get_db ensures this)
    await get_db()

    conn = await _read_pool.get()
    try:
        yield conn
    finally:
        await _read_pool.put(conn)


async def _connect(read_only: bool = False) -> aiosqlite.Connection:
    """Open database connection with safe settings."""
    os.makedirs(CACHE_DIR, exist_ok=True)

    # Use URI format for read-only access
    path = f"file:{DB_PATH}?mode=ro" if read_only else DB_PATH
    db = await aiosqlite.connect(path, timeout=10, uri=read_only)
    db.row_factory = aiosqlite.Row

    # Safety pragmas
    await db.execute("PRAGMA journal_mode = WAL")
    await db.execute("PRAGMA synchronous = NORMAL")
    await db.execute("PRAGMA foreign_keys = ON")
    await db.execute("PRAGMA busy_timeout = 5000")  # 5s timeout on lock

    if not read_only:
        await _create_tables(db)
        await db.commit()
        logger.info(f"Connected to {DB_PATH} (RW)")
    return db


async def _create_tables(db: aiosqlite.Connection):
    """Create tables if they don't exist."""
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS image_hashes (
            url      TEXT PRIMARY KEY,
            hash     TEXT NOT NULL,
            cache_type TEXT NOT NULL DEFAULT 'auto'
        );

        CREATE TABLE IF NOT EXISTS posted_mds (
            md_number TEXT PRIMARY KEY
        );

        CREATE TABLE IF NOT EXISTS posted_watches (
            watch_number TEXT PRIMARY KEY
        );

        CREATE TABLE IF NOT EXISTS posted_surveys (
            dat_guid TEXT PRIMARY KEY,
            posted_at REAL NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS posted_reports (
            product_id TEXT PRIMARY KEY,
            posted_at REAL NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS posted_warnings (
            vtec_id    TEXT PRIMARY KEY,
            message_id INTEGER NOT NULL DEFAULT 0,
            channel_id INTEGER NOT NULL DEFAULT 0,
            posted_at  REAL NOT NULL DEFAULT 0,
            area       TEXT,
            tornado_confidence TEXT,
            tornado_severity TEXT
        );

        CREATE TABLE IF NOT EXISTS posted_soundings (
            pkey      TEXT PRIMARY KEY,
            posted_at REAL NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS sounding_handled_watches (
            watch_number TEXT PRIMARY KEY
        );

        CREATE TABLE IF NOT EXISTS posted_product_ids (
            product_id TEXT PRIMARY KEY,
            posted_at  REAL NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS bot_state (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS posted_urls (
            day_key TEXT PRIMARY KEY,
            urls    TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS product_text_cache (
            product_id  TEXT PRIMARY KEY,
            text        TEXT NOT NULL,
            expires_at  REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS http_validators (
            url           TEXT PRIMARY KEY,
            etag          TEXT NOT NULL DEFAULT '',
            last_modified TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS dirty_writes (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            op      TEXT NOT NULL,
            args    TEXT NOT NULL,
            created REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS dirty_writes_dead (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            original_id   INTEGER NOT NULL,
            op            TEXT NOT NULL,
            args          TEXT NOT NULL,
            created       REAL NOT NULL,
            retry_count   INTEGER NOT NULL DEFAULT 0,
            quarantined   REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS user_subscriptions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            sub_type   TEXT NOT NULL,
            sub_value  TEXT NOT NULL,
            lat        REAL,
            lon        REAL,
            radius_km  REAL
        );
    """)

    # Migrations: add columns if they don't exist
    for col_def in (
        "ALTER TABLE posted_warnings ADD COLUMN area TEXT",
        "ALTER TABLE posted_warnings ADD COLUMN tornado_confidence TEXT",
        "ALTER TABLE posted_warnings ADD COLUMN tornado_severity TEXT",
        "ALTER TABLE posted_warnings ADD COLUMN severity TEXT",
        "ALTER TABLE posted_warnings ADD COLUMN raw_text TEXT",
        "ALTER TABLE dirty_writes ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0",
    ):
        try:
            await db.execute(col_def)
        except Exception as e:
            if "duplicate column" not in str(e).lower() and "already exists" not in str(e).lower():
                logger.error(f"[DB] Migration failed — {col_def!r}: {e}")


async def close_db():
    """Close the database connection and read pool gracefully."""
    global _db
    if _db is not None:
        try:
            await _db.close()
            logger.info("Write database connection closed")
        except Exception as e:
            logger.warning(f"Error closing write database: {e}")
        _db = None

    # Drain and close read pool
    while not _read_pool.empty():
        conn = await _read_pool.get()
        try:
            await conn.close()
        except Exception as e:
            logger.warning(f"Error closing pooled read connection: {e}")
    logger.info("Read connection pool closed")


async def check_integrity() -> bool:
    """Run integrity check. Returns True if database is healthy."""
    try:
        async with get_read_db() as db:
            async with db.execute("PRAGMA integrity_check") as cursor:
                row = await cursor.fetchone()
                ok = bool(row and row[0] == "ok")
                if not ok:
                    logger.error(f"Integrity check failed: {row}")
                return ok
    except Exception as e:
        logger.exception(f"Integrity check error: {e}")
        return False


# ── Image hash operations ─────────────────────────────────────────────────────


async def get_hash(url: str, cache_type: Optional[str] = None) -> Optional[str]:
    """Get stored hash for a URL. When cache_type is given, scope the
    lookup — saves a second Upstash round-trip at the state-store layer."""
    try:
        async with get_read_db() as db:
            if cache_type:
                async with db.execute(
                    "SELECT hash FROM image_hashes WHERE url = ? AND cache_type = ?",
                    (url, cache_type),
                ) as cursor:
                    row = await cursor.fetchone()
                    return row["hash"] if row else None
            async with db.execute("SELECT hash FROM image_hashes WHERE url = ?", (url,)) as cursor:
                row = await cursor.fetchone()
                return row["hash"] if row else None
    except Exception as e:
        logger.warning(f"get_hash failed for {url}: {e}")
        return None


async def set_hash(url: str, hash_val: str, cache_type: str = "auto"):
    """Store or update hash for a URL."""
    await _write(
        """INSERT INTO image_hashes (url, hash, cache_type)
           VALUES (?, ?, ?)
           ON CONFLICT(url) DO UPDATE SET hash=excluded.hash""",
        (url, hash_val, cache_type),
        f"set_hash({url})",
    )


async def get_all_hashes(cache_type: Optional[str] = None) -> dict:
    """Get all stored hashes, optionally filtered by cache_type."""
    try:
        async with get_read_db() as db:
            if cache_type:
                async with db.execute(
                    "SELECT url, hash FROM image_hashes WHERE cache_type = ?",
                    (cache_type,),
                ) as cursor:
                    rows = await cursor.fetchall()
            else:
                async with db.execute("SELECT url, hash FROM image_hashes") as cursor:
                    rows = await cursor.fetchall()
            return {row["url"]: row["hash"] for row in rows}
    except Exception as e:
        logger.warning(f"get_all_hashes failed: {e}")
        return {}


async def set_hashes_batch(hashes: dict, cache_type: str = "auto"):
    """Store multiple hashes in a single transaction."""
    if not hashes:
        return
    await _write_many(
        """INSERT INTO image_hashes (url, hash, cache_type)
           VALUES (?, ?, ?)
           ON CONFLICT(url) DO UPDATE SET hash=excluded.hash""",
        [(url, h, cache_type) for url, h in hashes.items()],
        "set_hashes_batch",
    )


# ── Posted MDs ────────────────────────────────────────────────────────────────


async def get_posted_mds() -> set:
    """Get all posted MD numbers."""
    try:
        async with get_read_db() as db:
            async with db.execute("SELECT md_number FROM posted_mds") as cursor:
                rows = await cursor.fetchall()
                return {row["md_number"] for row in rows}
    except Exception as e:
        logger.warning(f"get_posted_mds failed: {e}")
        return set()


async def add_posted_md(md_number: str):
    """Mark an MD as posted."""
    await _write(
        "INSERT OR IGNORE INTO posted_mds (md_number) VALUES (?)",
        (md_number,),
        f"add_posted_md({md_number})",
    )


async def add_posted_mds_batch(items: Iterable[str]):
    """Mark multiple MDs as posted in a single transaction."""
    rows = [(m,) for m in items]
    if not rows:
        return
    await _write_many(
        "INSERT OR IGNORE INTO posted_mds (md_number) VALUES (?)",
        rows,
        "add_posted_mds_batch",
    )


async def prune_posted_mds(max_size: int = 200):
    """Keep only the most recent MD numbers."""
    await _write(
        """DELETE FROM posted_mds
           WHERE md_number NOT IN (
               SELECT md_number FROM posted_mds
               ORDER BY CAST(md_number AS INTEGER) DESC
               LIMIT ?
           )""",
        (max_size,),
        "prune_posted_mds",
    )


# ── Posted watches ────────────────────────────────────────────────────────────


async def get_posted_watches() -> set:
    """Get all posted watch numbers."""
    try:
        async with get_read_db() as db:
            async with db.execute("SELECT watch_number FROM posted_watches") as cursor:
                rows = await cursor.fetchall()
                return {row["watch_number"] for row in rows}
    except Exception as e:
        logger.warning(f"get_posted_watches failed: {e}")
        return set()


async def add_posted_watch(watch_number: str):
    """Mark a watch as posted."""
    await _write(
        "INSERT OR IGNORE INTO posted_watches (watch_number) VALUES (?)",
        (watch_number,),
        f"add_posted_watch({watch_number})",
    )


async def add_posted_watches_batch(items: Iterable[str]):
    """Mark multiple watches as posted in a single transaction."""
    rows = [(w,) for w in items]
    if not rows:
        return
    await _write_many(
        "INSERT OR IGNORE INTO posted_watches (watch_number) VALUES (?)",
        rows,
        "add_posted_watches_batch",
    )


async def prune_posted_watches(max_size: int = 200):
    """Keep only the most recent watch numbers."""
    await _write(
        """DELETE FROM posted_watches
           WHERE watch_number NOT IN (
               SELECT watch_number FROM posted_watches
               ORDER BY CAST(watch_number AS INTEGER) DESC
               LIMIT ?
           )""",
        (max_size,),
        "prune_posted_watches",
    )


# ── Posted surveys (DAT tracks) ──────────────────────────────────────────────


async def get_posted_surveys() -> set:
    """Get all posted DAT survey GUIDs."""
    try:
        async with get_read_db() as db:
            async with db.execute("SELECT dat_guid FROM posted_surveys") as cursor:
                rows = await cursor.fetchall()
                return {row["dat_guid"] for row in rows}
    except Exception as e:
        logger.warning(f"get_posted_surveys failed: {e}")
        return set()


async def add_posted_survey(dat_guid: str, posted_at: float = 0.0):
    """Mark a DAT survey as posted."""
    await _write(
        "INSERT OR IGNORE INTO posted_surveys (dat_guid, posted_at) VALUES (?, ?)",
        (dat_guid, posted_at or time.time()),
        f"add_posted_survey({dat_guid})",
    )


async def prune_posted_surveys(max_size: int = 100):
    """Keep only the most recent DAT survey GUIDs."""
    await _write(
        """DELETE FROM posted_surveys
           WHERE dat_guid NOT IN (
               SELECT dat_guid FROM posted_surveys
               ORDER BY posted_at DESC
               LIMIT ?
           )""",
        (max_size,),
        "prune_posted_surveys",
    )


# ── Posted reports (LSRs) ────────────────────────────────────────────────────


async def get_posted_reports() -> set:
    """Get all posted LSR product IDs."""
    try:
        async with get_read_db() as db:
            async with db.execute("SELECT product_id FROM posted_reports") as cursor:
                rows = await cursor.fetchall()
                return {row["product_id"] for row in rows}
    except Exception as e:
        logger.warning(f"get_posted_reports failed: {e}")
        return set()


async def add_posted_report(product_id: str, posted_at: float = 0.0):
    """Mark an LSR as posted."""
    await _write(
        "INSERT OR IGNORE INTO posted_reports (product_id, posted_at) VALUES (?, ?)",
        (product_id, posted_at or time.time()),
        f"add_posted_report({product_id})",
    )


async def add_posted_reports_batch(items: Iterable[str]):
    """Mark multiple LSR product IDs as posted in a single transaction.

    Uses a shared timestamp (now) for all rows — the mirror path doesn't
    track per-row posted_at, and a single now() call is cheaper anyway.
    """
    now = time.time()
    rows = [(p, now) for p in items]
    if not rows:
        return
    await _write_many(
        "INSERT OR IGNORE INTO posted_reports (product_id, posted_at) VALUES (?, ?)",
        rows,
        "add_posted_reports_batch",
    )


async def prune_posted_reports(max_size: int = 500):
    """Keep only the most recent LSR product IDs."""
    await _write(
        """DELETE FROM posted_reports
           WHERE product_id NOT IN (
               SELECT product_id FROM posted_reports
               ORDER BY posted_at DESC
               LIMIT ?
           )""",
        (max_size,),
        "prune_posted_reports",
    )


# ── Posted product IDs (cross-feed dedup) ───────────────────────────────────


async def get_posted_product_ids() -> set:
    """Get all posted product IDs (for cross-feed deduplication)."""
    try:
        async with get_read_db() as db:
            async with db.execute("SELECT product_id FROM posted_product_ids") as cursor:
                rows = await cursor.fetchall()
                return {row["product_id"] for row in rows}
    except Exception as e:
        logger.warning(f"get_posted_product_ids failed: {e}")
        return set()


async def add_posted_product_id(product_id: str, posted_at: float = 0.0):
    """Mark a product ID as posted."""
    await _write(
        "INSERT OR IGNORE INTO posted_product_ids (product_id, posted_at) VALUES (?, ?)",
        (product_id, posted_at or time.time()),
        f"add_posted_product_id({product_id})",
    )


async def add_posted_product_ids_batch(items: Iterable[str]):
    """Mark multiple product IDs as posted in a single transaction."""
    now = time.time()
    rows = [(p, now) for p in items]
    if not rows:
        return
    await _write_many(
        "INSERT OR IGNORE INTO posted_product_ids (product_id, posted_at) VALUES (?, ?)",
        rows,
        "add_posted_product_ids_batch",
    )


async def prune_posted_product_ids(max_size: int = 1000):
    """Keep only the most recent product IDs."""
    await _write(
        """DELETE FROM posted_product_ids
           WHERE product_id NOT IN (
               SELECT product_id FROM posted_product_ids
               ORDER BY posted_at DESC
               LIMIT ?
           )""",
        (max_size,),
        "prune_posted_product_ids",
    )


async def remove_posted_product_id(product_id: str):
    """Drop a single product ID — used to roll back a claim when the
    downstream post fails."""
    await _write(
        "DELETE FROM posted_product_ids WHERE product_id = ?",
        (product_id,),
        f"remove_posted_product_id({product_id})",
    )


# ── Posted warnings ───────────────────────────────────────────────────────────


async def get_all_posted_warnings() -> dict:
    """Get all posted warning mappings: {vtec_id: {'message_id': ..., 'channel_id': ..., 'area': ...}}."""
    try:
        async with get_read_db() as db:
            async with db.execute(
                "SELECT vtec_id, message_id, channel_id, area FROM posted_warnings"
            ) as cursor:
                rows = await cursor.fetchall()
                return {
                    row["vtec_id"]: {
                        "message_id": row["message_id"],
                        "channel_id": row["channel_id"],
                        "area": row["area"],
                    }
                    for row in rows
                }
    except Exception as e:
        logger.warning(f"get_all_posted_warnings failed: {e}")
        return {}


async def get_warning_stats(
    since: Optional[float] = None,
) -> dict:
    """Aggregate posted warning counts by VTEC phenom and severity.

    Returns::
        {
            "tor": {"total": N, "emergency": N, "pds": N, "standard": N,
                    "observed": N, "radar_indicated": N},
            "svr": {"total": N, "destructive": N, "considerable": N, "standard": N},
            "ffw": {"total": N, "emergency": N, "standard": N},
        }
    """
    try:
        async with get_read_db() as db:
            if since:
                rows = await db.execute_fetchall(
                    "SELECT vtec_id, tornado_confidence, tornado_severity, severity "
                    "FROM posted_warnings WHERE posted_at >= ?",
                    (since,),
                )
            else:
                rows = await db.execute_fetchall(
                    "SELECT vtec_id, tornado_confidence, tornado_severity, severity "
                    "FROM posted_warnings"
                )

            stats = {
                "tor": {
                    "total": 0,
                    "emergency": 0,
                    "pds": 0,
                    "standard": 0,
                    "observed": 0,
                    "radar_indicated": 0,
                },
                "svr": {"total": 0, "destructive": 0, "considerable": 0, "standard": 0},
                "ffw": {"total": 0, "emergency": 0, "standard": 0},
            }

            for row in rows:
                vtec = row[0]
                confidence = row[1]
                tor_severity = row[2]
                severity = row[3]

                parts = vtec.split(".")
                phenom = parts[1] if len(parts) >= 2 else ""

                if phenom == "TO":
                    stats["tor"]["total"] += 1
                    s = severity or tor_severity
                    if s == "emergency":
                        stats["tor"]["emergency"] += 1
                    elif s == "pds":
                        stats["tor"]["pds"] += 1
                    else:
                        stats["tor"]["standard"] += 1
                    if confidence == "observed":
                        stats["tor"]["observed"] += 1
                    elif confidence == "radar_indicated":
                        stats["tor"]["radar_indicated"] += 1

                elif phenom == "SV":
                    stats["svr"]["total"] += 1
                    if severity == "destructive":
                        stats["svr"]["destructive"] += 1
                    elif severity == "considerable":
                        stats["svr"]["considerable"] += 1
                    else:
                        stats["svr"]["standard"] += 1

                elif phenom == "FF":
                    stats["ffw"]["total"] += 1
                    if severity == "emergency":
                        stats["ffw"]["emergency"] += 1
                    else:
                        stats["ffw"]["standard"] += 1

            return stats
    except Exception as e:
        logger.warning(f"get_warning_stats failed: {e}")
        return {}


async def get_posted_warning_timestamp(vtec_id: str) -> Optional[float]:
    """Get the posted_at timestamp for a specific VTEC ID."""
    try:
        async with get_read_db() as db:
            async with db.execute(
                "SELECT posted_at FROM posted_warnings WHERE vtec_id = ?", (vtec_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return row["posted_at"] if row else None
    except Exception as e:
        logger.warning(f"get_posted_warning_timestamp failed: {e}")
        return None


async def get_warning_counts_for_date(since: float, until: float) -> dict:
    """Count posted warnings in a time range, grouped by VTEC phenom."""
    try:
        async with get_read_db() as db:
            async with db.execute(
                "SELECT vtec_id, tornado_confidence FROM posted_warnings "
                "WHERE posted_at >= ? AND posted_at < ?",
                (since, until),
            ) as cursor:
                rows = await cursor.fetchall()

        counts = {"tor": 0, "svr": 0, "ffw": 0, "tor_observed": 0}
        for row in rows:
            vtec = row["vtec_id"]
            confidence = row["tornado_confidence"]
            parts = vtec.split(".")
            phenom = parts[1] if len(parts) >= 2 else ""
            if phenom == "TO":
                counts["tor"] += 1
                if confidence == "observed":
                    counts["tor_observed"] += 1
            elif phenom == "SV":
                counts["svr"] += 1
            elif phenom == "FF":
                counts["ffw"] += 1
        return counts
    except Exception as e:
        logger.warning(f"get_warning_counts_for_date failed: {e}")
        return {}


async def add_posted_warning(
    vtec_id: str,
    message_id: int,
    channel_id: int,
    posted_at: float = 0.0,
    area: str = "",
    tornado_confidence: Optional[str] = None,
    tornado_severity: Optional[str] = None,
    severity: Optional[str] = None,
    raw_text: Optional[str] = None,
):
    """Mark a warning as posted. ``vtec_id`` is the VTEC event identity
    (office.phenom.sig.etn), which stays stable across the warning's
    lifecycle so it doubles as our dedup key.

    For tornado warnings, ``tornado_confidence`` is 'observed' or 'radar_indicated',
    and ``tornado_severity`` is 'standard', 'pds', or 'emergency'.

    ``severity`` stores the generic severity for any warning type:
    tornado → 'standard'|'pds'|'emergency'
    severe  → 'standard'|'considerable'|'destructive'
    flash flood → 'standard'|'emergency'
    """
    await _write(
        """INSERT INTO posted_warnings (vtec_id, message_id, channel_id, posted_at, area, tornado_confidence, tornado_severity, severity, raw_text)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(vtec_id) DO UPDATE SET
             message_id=excluded.message_id,
             channel_id=excluded.channel_id,
             area=excluded.area,
             tornado_confidence=excluded.tornado_confidence,
             tornado_severity=excluded.tornado_severity,
             severity=excluded.severity,
             raw_text=excluded.raw_text""",
        (
            vtec_id,
            message_id,
            channel_id,
            posted_at,
            area,
            tornado_confidence,
            tornado_severity,
            severity,
            raw_text,
        ),
        f"add_posted_warning({vtec_id})",
    )


async def remove_posted_warning(vtec_id: str):
    """Drop a single posted-warning row — used to roll back a claim when
    the Discord post fails."""
    await _write(
        "DELETE FROM posted_warnings WHERE vtec_id = ?",
        (vtec_id,),
        f"remove_posted_warning({vtec_id})",
    )


async def prune_posted_warnings(max_size: int = 500):
    """Keep only the most recently-posted warnings. Warnings churn far
    faster than watches, so the default cap is higher."""
    await _write(
        """DELETE FROM posted_warnings
           WHERE vtec_id NOT IN (
               SELECT vtec_id FROM posted_warnings
               ORDER BY posted_at DESC
               LIMIT ?
           )""",
        (max_size,),
        "prune_posted_warnings",
    )


async def backfill_warning_severity(days: int = 7):
    """Query IEM VTEC events API for recent warnings and backfill severity + raw_text
    for rows where raw_text is NULL."""
    from cogs.warning_format import get_warning_severity
    from utils.http import http_get_bytes, http_get_text
    import json
    from datetime import datetime, timezone

    try:
        async with get_read_db() as db:
            rows = await db.execute_fetchall(
                "SELECT vtec_id FROM posted_warnings WHERE raw_text IS NULL",
            )
    except Exception:
        logger.warning("[BACKFILL] Could not query posted_warnings (migrating?)")
        return

    missing = {r[0] for r in rows}
    if not missing:
        logger.info("[BACKFILL] No warnings need backfill")
        return

    # Build set of expected VTEC IDs: K{wfo}.{phen}.{sig}.{etn:04d}
    # so we can match IEM events quickly
    logger.info(f"[BACKFILL] {len(missing)} warnings need backfill from IEM")

    updated = 0
    now = datetime.now(timezone.utc)
    for months_back in range(3):  # check up to 3 months
        y = now.year
        mo = now.month - months_back
        if mo < 1:
            mo += 12
            y -= 1

        for phen in ("TO", "SV", "FF"):
            url = (
                f"https://mesonet.agron.iastate.edu/json/vtec_events.py"
                f"?phenomena={phen}&significance=W&year={y}&month={mo}&limit=500"
            )
            content, status = await http_get_bytes(url, retries=2, timeout=20)
            if not content or status != 200:
                continue

            data = json.loads(content)
            events = data.get("events", [])
            if not events:
                continue

            for ev in events:
                wfo = ev.get("wfo", "")
                eventid = ev.get("eventid")
                if not wfo or eventid is None:
                    continue
                vtec_id = f"K{wfo}.{phen}.W.{int(eventid):04d}"
                if vtec_id not in missing:
                    continue

                # Fetch product text from IEM VTEC page
                vtec_url = f"https://mesonet.agron.iastate.edu/vtec/f/{y}/{wfo}/{phen}/W/{int(eventid):04d}"
                html = await http_get_text(vtec_url, retries=1, timeout=15)
                if not html:
                    continue

                # Extract the raw product text from the IEM page
                import re

                text_match = re.search(r"<pre[^>]*>(.*?)</pre>", html, re.DOTALL | re.IGNORECASE)
                raw_text = text_match.group(1) if text_match else ""

                if not raw_text:
                    continue

                # Determine severity from the event type + text
                event_display = ""
                if phen == "TO":
                    event_display = "Tornado Warning"
                elif phen == "SV":
                    event_display = "Severe Thunderstorm Warning"
                elif phen == "FF":
                    event_display = "Flash Flood Warning"

                severity = get_warning_severity(event_display, raw_text)
                if severity:
                    await _write(
                        "UPDATE posted_warnings SET severity = ?, raw_text = ? WHERE vtec_id = ?",
                        (severity, raw_text[:5000], vtec_id),
                        f"backfill({vtec_id})",
                    )
                    updated += 1
                    missing.discard(vtec_id)

                    if updated % 50 == 0:
                        logger.info(f"[BACKFILL] {updated} warnings updated so far...")

    logger.info(f"[BACKFILL] Updated {updated} warnings with severity")
    if missing:
        logger.info(f"[BACKFILL] {len(missing)} warnings still unmatched")


# ── Soundings ─────────────────────────────────────────────────────────────────


async def get_posted_soundings() -> set[str]:
    """Get all posted sounding keys."""
    try:
        async with get_read_db() as db:
            async with db.execute("SELECT pkey FROM posted_soundings") as cursor:
                rows = await cursor.fetchall()
                return {row["pkey"] for row in rows}
    except Exception as e:
        logger.warning(f"get_posted_soundings failed: {e}")
        return set()


async def add_posted_sounding(pkey: str, posted_at: float = 0.0):
    """Mark a sounding as posted."""
    await _write(
        "INSERT OR IGNORE INTO posted_soundings (pkey, posted_at) VALUES (?, ?)",
        (pkey, posted_at or time.time()),
        f"add_posted_sounding({pkey})",
    )


async def prune_posted_soundings(max_days: int = 2):
    """Remove sounding keys older than max_days (soundings are ephemeral)."""
    cutoff = time.time() - (max_days * 86400)
    await _write(
        "DELETE FROM posted_soundings WHERE posted_at < ?",
        (cutoff,),
        "prune_posted_soundings",
    )


async def get_sounding_handled_watches() -> set[str]:
    """Get all watches that have had soundings handled."""
    try:
        async with get_read_db() as db:
            async with db.execute("SELECT watch_number FROM sounding_handled_watches") as cursor:
                rows = await cursor.fetchall()
                return {row["watch_number"] for row in rows}
    except Exception as e:
        logger.warning(f"get_sounding_handled_watches failed: {e}")
        return set()


async def add_sounding_handled_watch(watch_number: str):
    """Mark a watch as having soundings handled."""
    await _write(
        "INSERT OR IGNORE INTO sounding_handled_watches (watch_number) VALUES (?)",
        (watch_number,),
        f"add_sounding_handled_watch(#{watch_number})",
    )


async def clear_sounding_handled_watches():
    """Clear the sounding_handled_watches table (usually daily)."""
    await _write(
        "DELETE FROM sounding_handled_watches",
        (),
        "clear_sounding_handled_watches",
    )


# ── Key/value state ───────────────────────────────────────────────────────────


async def get_state(key: str) -> Optional[str]:
    """Get a value from the key/value store."""
    try:
        async with get_read_db() as db:
            async with db.execute("SELECT value FROM bot_state WHERE key = ?", (key,)) as cursor:
                row = await cursor.fetchone()
                return row["value"] if row else None
    except Exception as e:
        logger.warning(f"get_state failed for {key}: {e}")
        return None


async def get_all_state() -> dict:
    """Get all key/value pairs from the bot_state table."""
    try:
        async with get_read_db() as db:
            async with db.execute("SELECT key, value FROM bot_state") as cursor:
                rows = await cursor.fetchall()
                return {row["key"]: row["value"] for row in rows}
    except Exception as e:
        logger.warning(f"get_all_state failed: {e}")
        return {}


async def set_state(key: str, value: str):
    """Set a value in the key/value store."""
    await _write(
        """INSERT INTO bot_state (key, value)
           VALUES (?, ?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
        (key, value),
        f"set_state({key})",
    )


async def delete_state(key: str):
    """Delete a key from the key/value store."""
    await _write(
        "DELETE FROM bot_state WHERE key = ?",
        (key,),
        f"delete_state({key})",
    )


# ── Posted URLs ───────────────────────────────────────────────────────────────


async def get_posted_urls(day_key: str) -> list:
    """Get last posted URLs for a day key."""
    try:
        async with get_read_db() as db:
            async with db.execute(
                "SELECT urls FROM posted_urls WHERE day_key = ?", (day_key,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return json.loads(row["urls"])
    except Exception as e:
        logger.warning(f"get_posted_urls failed for {day_key}: {e}")
    return []


async def get_all_posted_urls() -> dict:
    """Get all day_key -> urls mapping from the posted_urls table."""
    try:
        async with get_read_db() as db:
            async with db.execute("SELECT day_key, urls FROM posted_urls") as cursor:
                rows = await cursor.fetchall()
                return {row["day_key"]: json.loads(row["urls"]) for row in rows}
    except Exception as e:
        logger.warning(f"get_all_posted_urls failed: {e}")
        return {}


async def set_posted_urls(day_key: str, urls: list):
    """Store last posted URLs for a day key."""
    await _write(
        """INSERT INTO posted_urls (day_key, urls)
           VALUES (?, ?)
           ON CONFLICT(day_key) DO UPDATE SET urls=excluded.urls""",
        (day_key, json.dumps(urls)),
        f"set_posted_urls({day_key})",
    )


# ── Product text cache (IEMBot fast-path) ───────────────────────────────────


async def get_product_cache(product_id: str) -> Optional[str]:
    """Get cached product text if not expired. Prunes expired rows at most once per hour."""
    global _last_product_cache_prune
    try:
        now = time.time()
        if now - _last_product_cache_prune > _PRODUCT_CACHE_PRUNE_INTERVAL:
            _last_product_cache_prune = now
            await _write(
                "DELETE FROM product_text_cache WHERE expires_at < ?",
                (now,),
                "prune product_text_cache",
            )
        async with get_read_db() as db:
            async with db.execute(
                "SELECT text, expires_at FROM product_text_cache WHERE product_id = ?",
                (product_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if row and row["expires_at"] >= now:
                    return row["text"]
                return None
    except Exception as e:
        logger.warning(f"get_product_cache failed for {product_id}: {e}")
        return None


# ── HTTP validators (ETag / Last-Modified) ─────────────────────────────────


async def get_validators(url: str) -> Optional[dict]:
    """Return {'etag': ..., 'last_modified': ...} for a URL, or None."""
    try:
        async with get_read_db() as db:
            async with db.execute(
                "SELECT etag, last_modified FROM http_validators WHERE url = ?",
                (url,),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                return {"etag": row["etag"], "last_modified": row["last_modified"]}
    except Exception as e:
        logger.warning(f"get_validators failed for {url}: {e}")
        return None


async def get_all_validators() -> dict:
    """Bulk-load every stored validator for startup hydration."""
    try:
        async with get_read_db() as db:
            async with db.execute("SELECT url, etag, last_modified FROM http_validators") as cursor:
                rows = await cursor.fetchall()
                return {
                    row["url"]: {
                        "etag": row["etag"],
                        "last_modified": row["last_modified"],
                    }
                    for row in rows
                }
    except Exception as e:
        logger.warning(f"get_all_validators failed: {e}")
        return {}


async def set_validators(url: str, etag: str, last_modified: str) -> None:
    await _write(
        """INSERT INTO http_validators (url, etag, last_modified)
           VALUES (?, ?, ?)
           ON CONFLICT(url) DO UPDATE SET
             etag=excluded.etag,
             last_modified=excluded.last_modified""",
        (url, etag or "", last_modified or ""),
        f"set_validators({url})",
    )


async def set_product_cache(product_id: str, text: str, ttl: int = 600):
    """Store product text with a TTL (seconds)."""
    expires_at = time.time() + ttl
    await _write(
        """INSERT INTO product_text_cache (product_id, text, expires_at)
           VALUES (?, ?, ?)
           ON CONFLICT(product_id) DO UPDATE SET
             text=excluded.text,
             expires_at=excluded.expires_at""",
        (product_id, text, expires_at),
        f"set_product_cache({product_id})",
    )


# ── Upstash Quota ──────────────────────────────────────────────────────────


async def get_upstash_commands_today() -> int:
    """Get the number of Upstash commands used today (UTC)."""
    today = time.strftime("%Y-%m-%d", time.gmtime())
    val = await get_state(f"upstash_cmds:{today}")
    return int(val) if val else 0


async def increment_upstash_commands(count: int = 1):
    """Increment the daily Upstash command counter."""
    today = time.strftime("%Y-%m-%d", time.gmtime())
    current = await get_upstash_commands_today()
    await set_state(f"upstash_cmds:{today}", str(current + count))


# ── Watch centroid cache ──────────────────────────────────────────────────────
# Reuses product_text_cache with a 12h TTL so zone-geometry HTTP calls
# (up to 10 per watch) survive bot restarts during active weather.

_CENTROID_TTL = 12 * 3600  # 12 hours


async def get_watch_centroid_cache(watch_num: str) -> Optional[tuple]:
    """Return cached (lat, lon) for a watch number, or None if absent/expired."""
    text = await get_product_cache(f"centroid:{watch_num}")
    if text is None:
        return None
    try:
        lat, lon = json.loads(text)
        return float(lat), float(lon)
    except Exception:
        return None


async def set_watch_centroid_cache(watch_num: str, centroid: tuple) -> None:
    """Persist (lat, lon) for a watch number with a 12h TTL."""
    await set_product_cache(
        f"centroid:{watch_num}",
        json.dumps(list(centroid)),
        ttl=_CENTROID_TTL,
    )


# ── Dirty writes (Upstash sync queue) ───────────────────────────────────────


async def add_dirty_write(op: str, args: tuple):
    """Add a pending Upstash write to the queue."""
    await _write(
        "INSERT INTO dirty_writes (op, args, created) VALUES (?, ?, ?)",
        (op, json.dumps(args), time.time()),
        f"add_dirty_write({op})",
    )


async def get_dirty_writes() -> list:
    """Get all pending Upstash writes (includes retry_count)."""
    try:
        async with get_read_db() as db:
            async with db.execute(
                "SELECT id, op, args, retry_count FROM dirty_writes ORDER BY created ASC"
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    {
                        "id": r["id"],
                        "op": r["op"],
                        "args": json.loads(r["args"]),
                        "retry_count": r["retry_count"] if "retry_count" in r.keys() else 0,
                    }
                    for r in rows
                ]
    except Exception as e:
        logger.warning(f"get_dirty_writes failed: {e}")
        return []


async def bump_dirty_retry(ids: list[int]):
    """Increment retry_count on the given dirty_writes rows."""
    if not ids:
        return
    await _write_many(
        "UPDATE dirty_writes SET retry_count = retry_count + 1 WHERE id = ?",
        [(i,) for i in ids],
        "bump_dirty_retry",
    )


async def quarantine_dirty_writes(ids: list[int]):
    """Move dirty_writes rows to the dead-letter table and delete them
    from the active queue. Used after a row has failed replay enough
    times that retrying further would just keep blocking the queue."""
    if not ids:
        return
    global _write_failure_count
    try:
        db = await get_db()
        placeholders = ",".join("?" for _ in ids)
        async with _LOCK:
            await db.execute(
                f"""INSERT INTO dirty_writes_dead
                    (original_id, op, args, created, retry_count, quarantined)
                    SELECT id, op, args, created, retry_count, ?
                    FROM dirty_writes WHERE id IN ({placeholders})""",
                (time.time(), *ids),
            )
            await db.execute(
                f"DELETE FROM dirty_writes WHERE id IN ({placeholders})",
                tuple(ids),
            )
            await db.commit()
        if _write_failure_count:
            _write_failure_count = 0
    except Exception as e:
        _write_failure_count += 1
        level = (
            logger.error
            if _write_failure_count >= _WRITE_FAILURE_ALERT_THRESHOLD
            else logger.warning
        )
        level(f"quarantine_dirty_writes failed ({_write_failure_count} consecutive): {e}")


async def get_quarantined_writes() -> list:
    """Read the dead-letter table — for operator inspection / tests."""
    try:
        async with get_read_db() as db:
            async with db.execute(
                "SELECT id, original_id, op, args, created, retry_count, quarantined "
                "FROM dirty_writes_dead ORDER BY quarantined ASC"
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    {
                        "id": r["id"],
                        "original_id": r["original_id"],
                        "op": r["op"],
                        "args": json.loads(r["args"]),
                        "created": r["created"],
                        "retry_count": r["retry_count"],
                        "quarantined": r["quarantined"],
                    }
                    for r in rows
                ]
    except Exception as e:
        logger.warning(f"get_quarantined_writes failed: {e}")
        return []


async def delete_dirty_write(write_id: int):
    """Remove a finished write from the queue."""
    await _write(
        "DELETE FROM dirty_writes WHERE id = ?",
        (write_id,),
        f"delete_dirty_write({write_id})",
    )


async def delete_dirty_writes_batch(ids: list[int]):
    """Remove multiple finished writes from the queue."""
    if not ids:
        return
    await _write_many(
        "DELETE FROM dirty_writes WHERE id = ?",
        [(i,) for i in ids],
        "delete_dirty_writes_batch",
    )


# ── User Subscriptions ───────────────────────────────────────────────────────


async def get_all_user_subscriptions() -> list[dict]:
    """Retrieve all user subscriptions."""
    try:
        async with get_read_db() as db:
            async with db.execute(
                "SELECT id, user_id, sub_type, sub_value, lat, lon, radius_km "
                "FROM user_subscriptions"
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"get_all_user_subscriptions failed: {e}")
        return []


async def get_user_subscriptions(user_id: int) -> list[dict]:
    """Retrieve all subscriptions for a specific user."""
    try:
        async with get_read_db() as db:
            async with db.execute(
                "SELECT id, user_id, sub_type, sub_value, lat, lon, radius_km "
                "FROM user_subscriptions WHERE user_id = ?",
                (user_id,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"get_user_subscriptions failed: {e}")
        return []


async def add_user_subscription(
    user_id: int,
    sub_type: str,
    sub_value: str,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    radius_km: Optional[float] = None,
) -> None:
    """Add a new user subscription."""
    # First delete any existing exact match to avoid dupes
    await _write(
        "DELETE FROM user_subscriptions WHERE user_id = ? AND sub_type = ? AND sub_value = ?",
        (user_id, sub_type, sub_value),
        "delete_duplicate_subscription",
    )
    await _write(
        "INSERT INTO user_subscriptions (user_id, sub_type, sub_value, lat, lon, radius_km) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, sub_type, sub_value, lat, lon, radius_km),
        "add_user_subscription",
    )


async def remove_user_subscription(user_id: int, sub_type: str, sub_value: str) -> None:
    """Remove a specific user subscription."""
    await _write(
        "DELETE FROM user_subscriptions WHERE user_id = ? AND sub_type = ? AND sub_value = ?",
        (user_id, sub_type, sub_value),
        "remove_user_subscription",
    )
