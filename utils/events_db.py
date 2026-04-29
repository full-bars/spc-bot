# utils/events_db.py
"""
Standalone SQLite database for the significant weather events archive.

Intentionally separate from bot_state.db / Upstash so the historical
tornado record never touches the Redis free-tier budget and can grow
indefinitely without impacting operational state sync.

File location: cache/events.db  (configurable via EVENTS_DB_PATH env var)
Sync snapshot:  cache/events_sync/events.db  — written every snapshot cycle,
                watched by Syncthing for cross-node replication.
"""

import asyncio
import logging
import os
import re
import shutil
import time
from typing import Optional

import aiosqlite

logger = logging.getLogger("spc_bot")

_EVENTS_DB_PATH = os.getenv("EVENTS_DB_PATH", "cache/events.db")
_SYNC_DIR = os.getenv("EVENTS_SYNC_DIR", "cache/events_sync")
_SYNC_PATH = os.path.join(_SYNC_DIR, "events.db")

_db: Optional[aiosqlite.Connection] = None
_db_lock = asyncio.Lock()


async def get_events_db() -> aiosqlite.Connection:
    global _db
    if _db is not None:
        return _db
    async with _db_lock:
        if _db is not None:
            return _db
        os.makedirs(os.path.dirname(_EVENTS_DB_PATH), exist_ok=True)
        conn = await aiosqlite.connect(_EVENTS_DB_PATH)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        await _create_tables(conn)
        await conn.commit()
        _db = conn
        logger.info(f"[EVENTS-DB] Connected to {_EVENTS_DB_PATH}")
        return _db


async def close_events_db() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None


async def _create_tables(db: aiosqlite.Connection) -> None:
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS significant_events (
            event_id    TEXT PRIMARY KEY,
            event_type  TEXT NOT NULL,
            location    TEXT NOT NULL,
            magnitude   TEXT,
            vtec_id     TEXT,
            coords      TEXT,
            timestamp   REAL NOT NULL,
            source      TEXT NOT NULL,
            raw_text    TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sig_events_type_ts
            ON significant_events (event_type, timestamp DESC);
    """)


# ── Writes ───────────────────────────────────────────────────────────────────

async def add_significant_event(
    event_id: str,
    event_type: str,
    location: str,
    magnitude: str = "",
    vtec_id: str = "",
    coords: str = "",
    timestamp: float = 0.0,
    source: str = "",
    raw_text: str = "",
) -> None:
    db = await get_events_db()
    try:
        await db.execute(
            """INSERT INTO significant_events
               (event_id, event_type, location, magnitude, vtec_id, coords,
                timestamp, source, raw_text)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(event_id) DO UPDATE SET
                 magnitude = excluded.magnitude,
                 location  = excluded.location,
                 coords    = excluded.coords,
                 raw_text  = excluded.raw_text""",
            (event_id, event_type, location, magnitude, vtec_id, coords,
             timestamp or time.time(), source, raw_text),
        )
        await db.commit()
    except Exception as e:
        logger.warning(f"[EVENTS-DB] add_significant_event({event_id}) failed: {e}")


# ── Reads ────────────────────────────────────────────────────────────────────

async def get_recent_significant_events(
    event_type: Optional[str] = None,
    since_hours: int = 24,
    limit: int = 50,
) -> list:
    db = await get_events_db()
    try:
        start_ts = time.time() - (since_hours * 3600)
        sql = "SELECT * FROM significant_events WHERE timestamp >= ?"
        params: list = [start_ts]
        if event_type:
            sql += " AND event_type = ?"
            params.append(event_type)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        async with db.execute(sql, tuple(params)) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[EVENTS-DB] get_recent_significant_events failed: {e}")
        return []


async def find_matching_tornado(
    source: str,
    timestamp: float,
    location_query: str,
    window_hours: float = 12.0,
) -> Optional[str]:
    db = await get_events_db()
    try:
        window = window_hours * 3600
        async with db.execute(
            """SELECT event_id, location FROM significant_events
               WHERE event_type = 'Tornado'
                 AND source = ?
                 AND timestamp BETWEEN ? AND ?""",
            (source, timestamp - window, timestamp + window),
        ) as cur:
            rows = await cur.fetchall()
        if not rows:
            return None
        if len(rows) == 1:
            return rows[0]["event_id"]
        query_words = set(re.findall(r"\w+", location_query.upper()))
        best_id, best_score = None, -1
        for row in rows:
            overlap = len(query_words & set(re.findall(r"\w+", row["location"].upper())))
            if overlap > best_score:
                best_score, best_id = overlap, row["event_id"]
        return best_id
    except Exception as e:
        logger.warning(f"[EVENTS-DB] find_matching_tornado failed: {e}")
        return None


# ── Syncthing snapshot ───────────────────────────────────────────────────────

async def snapshot_for_sync() -> None:
    """Copy events.db to the Syncthing-watched directory as a clean snapshot."""
    try:
        os.makedirs(_SYNC_DIR, exist_ok=True)
        db = await get_events_db()
        tmp = _SYNC_PATH + ".tmp"
        async with aiosqlite.connect(tmp) as dst:
            await db.backup(dst)
        os.replace(tmp, _SYNC_PATH)
        logger.debug("[EVENTS-DB] Snapshot written to sync dir")
    except Exception as e:
        logger.warning(f"[EVENTS-DB] Snapshot failed: {e}")


def restore_from_sync() -> None:
    """Copy the Syncthing-received snapshot into events.db before cogs load.
    Called synchronously at promotion time before the event loop is busy."""
    if not os.path.exists(_SYNC_PATH):
        logger.info("[EVENTS-DB] No sync snapshot found — starting fresh events.db")
        return
    try:
        os.makedirs(os.path.dirname(_EVENTS_DB_PATH), exist_ok=True)
        shutil.copy2(_SYNC_PATH, _EVENTS_DB_PATH)
        logger.info(f"[EVENTS-DB] Restored events.db from sync snapshot")
    except Exception as e:
        logger.warning(f"[EVENTS-DB] Restore from sync failed: {e}")


# ── Syncthing folder-mode flipping ───────────────────────────────────────────

async def set_syncthing_folder_mode(mode: str) -> None:
    """Flip the Syncthing folder to 'sendonly' on promotion or 'receiveonly' on demotion."""
    api_key = os.getenv("SYNCTHING_API_KEY", "")
    folder_id = os.getenv("SYNCTHING_FOLDER_ID", "spcbot-events")
    if not api_key:
        return
    import aiohttp as _aiohttp
    url_base = "http://127.0.0.1:8384"
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    try:
        async with _aiohttp.ClientSession() as session:
            async with session.get(
                f"{url_base}/rest/config/folders/{folder_id}", headers=headers, timeout=_aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"[EVENTS-DB] Syncthing folder fetch failed: {resp.status}")
                    return
                folder_cfg = await resp.json()
            folder_cfg["type"] = mode
            async with session.put(
                f"{url_base}/rest/config/folders/{folder_id}",
                headers=headers,
                json=folder_cfg,
                timeout=_aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status in (200, 201):
                    logger.info(f"[EVENTS-DB] Syncthing folder set to {mode}")
                else:
                    logger.warning(f"[EVENTS-DB] Syncthing folder update failed: {resp.status}")
    except Exception as e:
        logger.warning(f"[EVENTS-DB] Syncthing API call failed: {e}")
