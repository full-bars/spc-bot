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
  bot_state     — key/value store for simple state (ncar, csu_mlp, prefs, etc.)
"""

import asyncio
import logging
import os
from typing import Optional

import aiosqlite

from config import CACHE_DIR

logger = logging.getLogger("spc_bot")

DB_PATH = os.path.join(CACHE_DIR, "bot_state.db")
_LOCK = asyncio.Lock()
_db: Optional[aiosqlite.Connection] = None
_connecting: bool = False


async def get_db() -> aiosqlite.Connection:
    """Get or create the shared database connection (singleton)."""
    global _db, _connecting
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
        logger.error(f"[DB] Integrity check error: {e}")
        return False


# ── Image hash operations ─────────────────────────────────────────────────────

async def get_hash(url: str) -> Optional[str]:
    """Get stored hash for a URL."""
    try:
        db = await get_db()
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
    try:
        async with _LOCK:
            db = await get_db()
            await db.execute(
                """INSERT INTO image_hashes (url, hash, cache_type)
                   VALUES (?, ?, ?)
                   ON CONFLICT(url) DO UPDATE SET hash=excluded.hash""",
                (url, hash_val, cache_type),
            )
            await db.commit()
    except Exception as e:
        logger.warning(f"[DB] set_hash failed for {url}: {e}")


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
    try:
        async with _LOCK:
            db = await get_db()
            await db.executemany(
                """INSERT INTO image_hashes (url, hash, cache_type)
                   VALUES (?, ?, ?)
                   ON CONFLICT(url) DO UPDATE SET hash=excluded.hash""",
                [(url, h, cache_type) for url, h in hashes.items()],
            )
            await db.commit()
    except Exception as e:
        logger.warning(f"[DB] set_hashes_batch failed: {e}")


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
    try:
        async with _LOCK:
            db = await get_db()
            await db.execute(
                "INSERT OR IGNORE INTO posted_mds (md_number) VALUES (?)",
                (md_number,),
            )
            await db.commit()
    except Exception as e:
        logger.warning(f"[DB] add_posted_md failed for {md_number}: {e}")


async def prune_posted_mds(max_size: int = 200):
    """Keep only the most recent MD numbers."""
    try:
        async with _LOCK:
            db = await get_db()
            await db.execute("""
                DELETE FROM posted_mds
                WHERE md_number NOT IN (
                    SELECT md_number FROM posted_mds
                    ORDER BY CAST(md_number AS INTEGER) DESC
                    LIMIT ?
                )
            """, (max_size,))
            await db.commit()
    except Exception as e:
        logger.warning(f"[DB] prune_posted_mds failed: {e}")


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
    try:
        async with _LOCK:
            db = await get_db()
            await db.execute(
                "INSERT OR IGNORE INTO posted_watches (watch_number) VALUES (?)",
                (watch_number,),
            )
            await db.commit()
    except Exception as e:
        logger.warning(f"[DB] add_posted_watch failed for {watch_number}: {e}")


async def prune_posted_watches(max_size: int = 200):
    """Keep only the most recent watch numbers."""
    try:
        async with _LOCK:
            db = await get_db()
            await db.execute("""
                DELETE FROM posted_watches
                WHERE watch_number NOT IN (
                    SELECT watch_number FROM posted_watches
                    ORDER BY CAST(watch_number AS INTEGER) DESC
                    LIMIT ?
                )
            """, (max_size,))
            await db.commit()
    except Exception as e:
        logger.warning(f"[DB] prune_posted_watches failed: {e}")


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


async def set_state(key: str, value: str):
    """Set a value in the key/value store."""
    try:
        async with _LOCK:
            db = await get_db()
            await db.execute(
                """INSERT INTO bot_state (key, value)
                   VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (key, value),
            )
            await db.commit()
    except Exception as e:
        logger.warning(f"[DB] set_state failed for {key}: {e}")


async def delete_state(key: str):
    """Delete a key from the key/value store."""
    try:
        async with _LOCK:
            db = await get_db()
            await db.execute(
                "DELETE FROM bot_state WHERE key = ?", (key,)
            )
            await db.commit()
    except Exception as e:
        logger.warning(f"[DB] delete_state failed for {key}: {e}")


# ── Migration from JSON ───────────────────────────────────────────────────────

async def migrate_from_json():
    """
    One-time migration: import existing JSON state into SQLite.
    Only runs if the database is empty to avoid overwriting current state.
    """
    import json
    from config import (
        AUTO_CACHE_FILE, MANUAL_CACHE_FILE,
    )
    from utils.cache import MD_CACHE_FILE, WATCH_CACHE_FILE

    # Skip migration if DB already has data
    try:
        db = await get_db()
        async with db.execute("SELECT COUNT(*) as cnt FROM image_hashes") as cursor:
            row = await cursor.fetchone()
            if row and row["cnt"] > 0:
                logger.info("[DB] Skipping JSON migration — DB already populated")
                return
    except Exception as e:
        logger.warning(f"[DB] Migration check failed: {e}")

    migrated = []

    # Image hashes
    for path, cache_type in [
        (AUTO_CACHE_FILE, "auto"),
        (MANUAL_CACHE_FILE, "manual"),
    ]:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    data = json.load(f)
                if data:
                    await set_hashes_batch(data, cache_type)
                    migrated.append(f"{cache_type} hashes ({len(data)})")
            except Exception as e:
                logger.warning(f"[DB] Migration failed for {path}: {e}")

    # Posted MDs
    if os.path.exists(MD_CACHE_FILE):
        try:
            with open(MD_CACHE_FILE) as f:
                data = json.load(f)
            if isinstance(data, list):
                for md in data:
                    await add_posted_md(str(md))
                migrated.append(f"posted MDs ({len(data)})")
        except Exception as e:
            logger.warning(f"[DB] Migration failed for MDs: {e}")

    # Posted watches
    if os.path.exists(WATCH_CACHE_FILE):
        try:
            with open(WATCH_CACHE_FILE) as f:
                data = json.load(f)
            if isinstance(data, list):
                for w in data:
                    await add_posted_watch(str(w))
                migrated.append(f"posted watches ({len(data)})")
        except Exception as e:
            logger.warning(f"[DB] Migration failed for watches: {e}")

    if migrated:
        logger.info(f"[DB] Migrated from JSON: {', '.join(migrated)}")
    else:
        logger.info("[DB] No JSON migration needed")


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
                import json
                return json.loads(row["urls"])
    except Exception as e:
        logger.warning(f"[DB] get_posted_urls failed for {day_key}: {e}")
    return []


async def set_posted_urls(day_key: str, urls: list):
    """Store last posted URLs for a day key."""
    import json
    try:
        async with _LOCK:
            db = await get_db()
            await db.execute(
                """INSERT INTO posted_urls (day_key, urls)
                   VALUES (?, ?)
                   ON CONFLICT(day_key) DO UPDATE SET urls=excluded.urls""",
                (day_key, json.dumps(urls)),
            )
            await db.commit()
    except Exception as e:
        logger.warning(f"[DB] set_posted_urls failed for {day_key}: {e}")


# ── Product text cache (IEMBot fast-path) ───────────────────────────────────

async def get_product_cache(product_id: str) -> Optional[str]:
    """Get cached product text if not expired."""
    import time
    try:
        db = await get_db()
        now = time.time()
        # Delete expired entries while we're here
        async with _LOCK:
            await db.execute("DELETE FROM product_text_cache WHERE expires_at < ?", (now,))
            await db.commit()

        async with db.execute(
            "SELECT text FROM product_text_cache WHERE product_id = ?", (product_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row["text"] if row else None
    except Exception as e:
        logger.warning(f"[DB] get_product_cache failed for {product_id}: {e}")
        return None


async def set_product_cache(product_id: str, text: str, ttl: int = 600):
    """Store product text with a TTL (seconds)."""
    import time
    try:
        expires_at = time.time() + ttl
        async with _LOCK:
            db = await get_db()
            await db.execute(
                """INSERT INTO product_text_cache (product_id, text, expires_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(product_id) DO UPDATE SET 
                     text=excluded.text, 
                     expires_at=excluded.expires_at""",
                (product_id, text, expires_at),
            )
            await db.commit()
    except Exception as e:
        logger.warning(f"[DB] set_product_cache failed for {product_id}: {e}")
