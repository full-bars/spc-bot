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
from typing import Iterable, Optional

import aiosqlite

from config import CACHE_DIR

logger = logging.getLogger("spc_bot")

DB_PATH = os.path.join(CACHE_DIR, "bot_state.db")
_LOCK = asyncio.Lock()
_db: Optional[aiosqlite.Connection] = None
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
        level = logger.error if _write_failure_count >= _WRITE_FAILURE_ALERT_THRESHOLD else logger.warning
        level(f"[DB] {op} failed ({_write_failure_count} consecutive): {e}")


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
        level = logger.error if _write_failure_count >= _WRITE_FAILURE_ALERT_THRESHOLD else logger.warning
        level(f"[DB] {op} failed ({_write_failure_count} consecutive): {e}")


async def get_db() -> aiosqlite.Connection:
    """Get or create the shared database connection (singleton)."""
    global _db
    if _db is not None:
        return _db
    async with _LOCK:
        # Check again inside lock in case another coroutine connected first
        if _db is None:
            _db = await _connect()
    return _db


async def _connect() -> aiosqlite.Connection:
    """Open database connection with safe settings."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    db = await aiosqlite.connect(DB_PATH, timeout=10)
    db.row_factory = aiosqlite.Row

    # Safety pragmas
    await db.execute("PRAGMA journal_mode = WAL")
    await db.execute("PRAGMA synchronous = NORMAL")
    await db.execute("PRAGMA foreign_keys = ON")
    await db.execute("PRAGMA busy_timeout = 5000")  # 5s timeout on lock

    await _create_tables(db)
    await db.commit()
    logger.info(f"[DB] Connected to {DB_PATH}")
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

        CREATE TABLE IF NOT EXISTS posted_warnings (
            vtec_id    TEXT PRIMARY KEY,
            message_id INTEGER NOT NULL DEFAULT 0,
            channel_id INTEGER NOT NULL DEFAULT 0,
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
    """)


async def close_db():
    """Close the database connection gracefully."""
    global _db
    if _db is not None:
        try:
            await _db.close()
            logger.info("[DB] Database connection closed")
        except Exception as e:
            logger.warning(f"[DB] Error closing database: {e}")
        _db = None


async def check_integrity() -> bool:
    """Run integrity check. Returns True if database is healthy."""
    try:
        db = await get_db()
        async with db.execute("PRAGMA integrity_check") as cursor:
            row = await cursor.fetchone()
            ok = row and row[0] == "ok"
            if not ok:
                logger.error(f"[DB] Integrity check failed: {row}")
            return ok
    except Exception as e:
        logger.exception(f"[DB] Integrity check error: {e}")
        return False


# ── Image hash operations ─────────────────────────────────────────────────────

async def get_hash(url: str, cache_type: Optional[str] = None) -> Optional[str]:
    """Get stored hash for a URL. When cache_type is given, scope the
    lookup — saves a second Upstash round-trip at the state-store layer."""
    try:
        db = await get_db()
        if cache_type:
            async with db.execute(
                "SELECT hash FROM image_hashes WHERE url = ? AND cache_type = ?",
                (url, cache_type),
            ) as cursor:
                row = await cursor.fetchone()
                return row["hash"] if row else None
        async with db.execute(
            "SELECT hash FROM image_hashes WHERE url = ?", (url,)
        ) as cursor:
            row = await cursor.fetchone()
            return row["hash"] if row else None
    except Exception as e:
        logger.warning(f"[DB] get_hash failed for {url}: {e}")
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
        db = await get_db()
        if cache_type:
            async with db.execute(
                "SELECT url, hash FROM image_hashes WHERE cache_type = ?",
                (cache_type,),
            ) as cursor:
                rows = await cursor.fetchall()
        else:
            async with db.execute(
                "SELECT url, hash FROM image_hashes"
            ) as cursor:
                rows = await cursor.fetchall()
        return {row["url"]: row["hash"] for row in rows}
    except Exception as e:
        logger.warning(f"[DB] get_all_hashes failed: {e}")
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
        db = await get_db()
        async with db.execute("SELECT md_number FROM posted_mds") as cursor:
            rows = await cursor.fetchall()
            return {row["md_number"] for row in rows}
    except Exception as e:
        logger.warning(f"[DB] get_posted_mds failed: {e}")
        return set()


async def add_posted_md(md_number: str):
    """Mark an MD as posted."""
    await _write(
        "INSERT OR IGNORE INTO posted_mds (md_number) VALUES (?)",
        (md_number,),
        f"add_posted_md({md_number})",
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
        db = await get_db()
        async with db.execute(
            "SELECT watch_number FROM posted_watches"
        ) as cursor:
            rows = await cursor.fetchall()
            return {row["watch_number"] for row in rows}
    except Exception as e:
        logger.warning(f"[DB] get_posted_watches failed: {e}")
        return set()


async def add_posted_watch(watch_number: str):
    """Mark a watch as posted."""
    await _write(
        "INSERT OR IGNORE INTO posted_watches (watch_number) VALUES (?)",
        (watch_number,),
        f"add_posted_watch({watch_number})",
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
        db = await get_db()
        async with db.execute("SELECT dat_guid FROM posted_surveys") as cursor:
            rows = await cursor.fetchall()
            return {row["dat_guid"] for row in rows}
    except Exception as e:
        logger.warning(f"[DB] get_posted_surveys failed: {e}")
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


# ── Posted warnings ───────────────────────────────────────────────────────────

async def get_all_posted_warnings() -> dict:
    """Get all posted warning mappings: {vtec_id: {'message_id': ..., 'channel_id': ...}}."""
    try:
        db = await get_db()
        async with db.execute(
            "SELECT vtec_id, message_id, channel_id FROM posted_warnings"
        ) as cursor:
            rows = await cursor.fetchall()
            return {
                row["vtec_id"]: {
                    "message_id": row["message_id"],
                    "channel_id": row["channel_id"],
                }
                for row in rows
            }
    except Exception as e:
        logger.warning(f"[DB] get_all_posted_warnings failed: {e}")
        return {}


async def add_posted_warning(
    vtec_id: str, message_id: int, channel_id: int, posted_at: float = 0.0
):
    """Mark a warning as posted. ``vtec_id`` is the VTEC event identity
    (office.phenom.sig.etn), which stays stable across the warning's
    lifecycle so it doubles as our dedup key."""
    await _write(
        """INSERT INTO posted_warnings (vtec_id, message_id, channel_id, posted_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(vtec_id) DO UPDATE SET
             message_id=excluded.message_id,
             channel_id=excluded.channel_id""",
        (vtec_id, message_id, channel_id, posted_at),
        f"add_posted_warning({vtec_id})",
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


# ── Key/value state ───────────────────────────────────────────────────────────

async def get_state(key: str) -> Optional[str]:
    """Get a value from the key/value store."""
    try:
        db = await get_db()
        async with db.execute(
            "SELECT value FROM bot_state WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()
            return row["value"] if row else None
    except Exception as e:
        logger.warning(f"[DB] get_state failed for {key}: {e}")
        return None


async def get_all_state() -> dict:
    """Get all key/value pairs from the bot_state table."""
    try:
        db = await get_db()
        async with db.execute("SELECT key, value FROM bot_state") as cursor:
            rows = await cursor.fetchall()
            return {row["key"]: row["value"] for row in rows}
    except Exception as e:
        logger.warning(f"[DB] get_all_state failed: {e}")
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
        db = await get_db()
        async with db.execute(
            "SELECT urls FROM posted_urls WHERE day_key = ?", (day_key,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return json.loads(row["urls"])
    except Exception as e:
        logger.warning(f"[DB] get_posted_urls failed for {day_key}: {e}")
    return []


async def get_all_posted_urls() -> dict:
    """Get all day_key -> urls mapping from the posted_urls table."""
    try:
        db = await get_db()
        async with db.execute("SELECT day_key, urls FROM posted_urls") as cursor:
            rows = await cursor.fetchall()
            return {row["day_key"]: json.loads(row["urls"]) for row in rows}
    except Exception as e:
        logger.warning(f"[DB] get_all_posted_urls failed: {e}")
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
        db = await get_db()
        now = time.time()
        if now - _last_product_cache_prune > _PRODUCT_CACHE_PRUNE_INTERVAL:
            _last_product_cache_prune = now
            await _write(
                "DELETE FROM product_text_cache WHERE expires_at < ?",
                (now,),
                "prune product_text_cache",
            )
        async with db.execute(
            "SELECT text, expires_at FROM product_text_cache WHERE product_id = ?",
            (product_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row and row["expires_at"] >= now:
                return row["text"]
            return None
    except Exception as e:
        logger.warning(f"[DB] get_product_cache failed for {product_id}: {e}")
        return None


# ── HTTP validators (ETag / Last-Modified) ─────────────────────────────────

async def get_validators(url: str) -> Optional[dict]:
    """Return {'etag': ..., 'last_modified': ...} for a URL, or None."""
    try:
        db = await get_db()
        async with db.execute(
            "SELECT etag, last_modified FROM http_validators WHERE url = ?",
            (url,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return {"etag": row["etag"], "last_modified": row["last_modified"]}
    except Exception as e:
        logger.warning(f"[DB] get_validators failed for {url}: {e}")
        return None


async def get_all_validators() -> dict:
    """Bulk-load every stored validator for startup hydration."""
    try:
        db = await get_db()
        async with db.execute(
            "SELECT url, etag, last_modified FROM http_validators"
        ) as cursor:
            rows = await cursor.fetchall()
            return {
                row["url"]: {
                    "etag": row["etag"],
                    "last_modified": row["last_modified"],
                }
                for row in rows
            }
    except Exception as e:
        logger.warning(f"[DB] get_all_validators failed: {e}")
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
