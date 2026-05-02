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

import logging
import os
import re
import shutil
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import aiosqlite

logger = logging.getLogger("spc_bot")

_EVENTS_DB_PATH = os.getenv("EVENTS_DB_PATH", "cache/events.db")
_SYNC_DIR = os.getenv("EVENTS_SYNC_DIR", "cache/events_sync")
_SYNC_PATH = os.path.join(_SYNC_DIR, "events.db")

_db: Optional[aiosqlite.Connection] = None
_db_dirty: bool = False


def _mark_dirty():
    global _db_dirty
    _db_dirty = True


async def get_events_db() -> aiosqlite.Connection:
    global _db
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
            raw_text    TEXT,
            dat_guid    TEXT,
            lead_time   REAL
        );
        CREATE INDEX IF NOT EXISTS idx_sig_events_type_ts
            ON significant_events (event_type, timestamp DESC);
    """)

    # Migration
    try:
        await db.execute("ALTER TABLE significant_events ADD COLUMN dat_guid TEXT")
    except Exception:
        logger.debug("Migration already applied: dat_guid column exists")

    try:
        await db.execute("ALTER TABLE significant_events ADD COLUMN lead_time REAL")
    except Exception:
        logger.debug("Migration already applied: lead_time column exists")


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
    dat_guid: str = "",
    lead_time: float = None,
) -> None:
    db = await get_events_db()
    try:
        await db.execute(
            """INSERT INTO significant_events
               (event_id, event_type, location, magnitude, vtec_id, coords,
                timestamp, source, raw_text, dat_guid, lead_time)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(event_id) DO UPDATE SET
                 magnitude = excluded.magnitude,
                 location  = excluded.location,
                 coords    = excluded.coords,
                 raw_text  = excluded.raw_text,
                 dat_guid  = CASE WHEN excluded.dat_guid != '' THEN excluded.dat_guid ELSE significant_events.dat_guid END,
                 lead_time = CASE WHEN excluded.lead_time IS NOT NULL THEN excluded.lead_time ELSE significant_events.lead_time END""",
            (event_id, event_type, location, magnitude, vtec_id, coords,
             timestamp or time.time(), source, raw_text, dat_guid, lead_time),
        )
        await db.commit()
        _mark_dirty()
    except Exception as e:
        logger.warning(f"[EVENTS-DB] add_significant_event({event_id}) failed: {e}")

async def link_dat_guid_to_tornado(date_str: str, guid: str, label: str) -> None:
    """Attempt to link a DAT guid to a tornado based on the label."""
    db = await get_events_db()
    try:
        # Date str is YYYY-MM-DD
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        start_ts = dt.timestamp()
        end_ts = start_ts + 86400
        
        async with db.execute(
            """SELECT event_id, location FROM significant_events
               WHERE event_type = 'Tornado'
                 AND timestamp >= ? AND timestamp < ?""",
            (start_ts - 43200, end_ts + 43200) # Give 12h buffer
        ) as cur:
            rows = await cur.fetchall()
            
        if not rows:
            return

        query_words = set(re.findall(r"\w+", label.upper()))
        best_id, best_score = None, -1
        for row in rows:
            overlap = len(query_words & set(re.findall(r"\w+", row["location"].upper())))
            if overlap > best_score and overlap > 0:
                best_score, best_id = overlap, row["event_id"]
                
        if best_id:
            await db.execute(
                "UPDATE significant_events SET dat_guid = ? WHERE event_id = ?",
                (guid, best_id)
            )
            await db.commit()
            _mark_dirty()
            logger.info(f"[EVENTS-DB] Linked DAT {guid} to event {best_id}")

    except Exception as e:
        logger.warning(f"[EVENTS-DB] link_dat_guid_to_tornado failed: {e}")

async def fetch_dat_photos(
    location: str = "",
    magnitude: str = "",
    coords: str = "",
) -> List[str]:
    """Retrieve damage photo URLs for a tornado by searching DAT Layer 0.

    Searches DAT by location (lat/lon from coords) and magnitude (EF rating).
    Returns URLs for image attachments found at matching damage points.
    """
    from utils.http import http_get_json

    if not coords or not location:
        return []

    # Parse coords format: "37.67N 85.74W" -> lat, lon
    try:
        parts = coords.replace("N", "").replace("S", "").replace("W", "").replace("E", "").split()
        lat = float(parts[0])
        lon = -float(parts[1]) if "W" in coords else float(parts[1])
    except (ValueError, IndexError):
        logger.warning(f"[DAT-API] Could not parse coords: {coords}")
        return []

    # Extract EF rating from magnitude (e.g., "EF0", "EF1", or "Confirmed")
    ef_rating = None
    magnitude_upper = (magnitude or "").upper()
    if any(f"EF{i}" in magnitude_upper for i in range(6)):
        for i in range(5, -1, -1):
            if f"EF{i}" in magnitude_upper:
                ef_rating = f"EF{i}"
                break

    # Query DAT Layer 0 for damage points in the area
    # Use a 0.3 degree buffer (~21 miles) around the tornado location
    buffer = 0.3
    bbox = f"{lon-buffer},{lat-buffer},{lon+buffer},{lat+buffer}"

    query_url = (
        "https://services.dat.noaa.gov/arcgis/rest/services/nws_damageassessmenttoolkit/DamageViewer/FeatureServer/0/query"
        f"?geometry={bbox}&geometryType=esriGeometryEnvelope&inSR=4326&spatialRel=esriSpatialRelIntersects"
    )

    # Add EF filter if we have a specific rating
    if ef_rating:
        query_url += f"&where=efscale='{ef_rating}'"

    query_url += "&outFields=objectid&f=json"

    try:
        data = await http_get_json(query_url, retries=1, timeout=10)
        if not data or "features" not in data:
            logger.debug(f"[DAT-API] No damage points found near {location}")
            return []

        object_ids = [f["attributes"]["objectid"] for f in data.get("features", [])]
        if not object_ids:
            logger.debug(f"[DAT-API] No damage points found near {location}")
            return []

        logger.debug(f"[DAT-API] Found {len(object_ids)} damage points near {location}")

        # Fetch attachments for each damage point (limit to 15 to avoid excessive API calls)
        urls = []
        for oid in object_ids[:15]:
            attach_url = (
                "https://services.dat.noaa.gov/arcgis/rest/services/nws_damageassessmenttoolkit/DamageViewer/FeatureServer/0"
                f"/{oid}/attachments?f=json"
            )

            try:
                attach_data = await http_get_json(attach_url, retries=0, timeout=5)
                for att in attach_data.get("attachmentInfos", []):
                    if att.get("contentType", "").startswith("image/"):
                        photo_url = (
                            f"https://services.dat.noaa.gov/arcgis/rest/services/nws_damageassessmenttoolkit/DamageViewer/FeatureServer/0"
                            f"/{oid}/attachments/{att['id']}"
                        )
                        urls.append(photo_url)
            except Exception as e:
                logger.debug(f"[DAT-API] Error fetching attachments for ObjectID {oid}: {e}")
                continue

        if urls:
            logger.info(f"[DAT-API] Found {len(urls)} photo(s) for {location}")
        return urls

    except Exception as e:
        logger.warning(f"[DAT-API] Error fetching photos for {location}: {e}")
        return []


# ── Reads ────────────────────────────────────────────────────────────────────

async def get_recent_significant_events(
    event_type: Optional[str] = None,
    since_hours: int = 24,
    limit: int = 1000,
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
) -> Optional[Tuple[str, Optional[str]]]:
    db = await get_events_db()
    try:
        window = window_hours * 3600
        async with db.execute(
            """SELECT event_id, vtec_id, location FROM significant_events
               WHERE event_type = 'Tornado'
                 AND source = ?
                 AND timestamp BETWEEN ? AND ?""",
            (source, timestamp - window, timestamp + window),
        ) as cur:
            rows = await cur.fetchall()
        if not rows:
            return None

        # Location-based matching if multiple
        query_words = set(re.findall(r"\w+", location_query.upper()))
        best_row, best_score = None, -1
        for row in rows:
            overlap = len(query_words & set(re.findall(r"\w+", row["location"].upper())))
            if overlap > best_score:
                best_score, best_row = overlap, row

        if best_row:
            return best_row["event_id"], best_row["vtec_id"]
        return None

    except Exception as e:
        logger.warning(f"[EVENTS-DB] find_matching_tornado failed: {e}")
        return None


async def prune_old_significant_events(days: int = 365) -> int:
    """Remove events older than N days to keep the database size manageable."""
    db = await get_events_db()
    try:
        cutoff = time.time() - (days * 86400)
        async with db.execute(
            "DELETE FROM significant_events WHERE timestamp < ?",
            (cutoff,)
        ) as cur:
            count = cur.rowcount
        await db.commit()
        if count > 0:
            _mark_dirty()
            logger.info(f"[EVENTS-DB] Pruned {count} events older than {days} days")
        return count
    except Exception as e:
        logger.warning(f"[EVENTS-DB] Pruning failed: {e}")
        return 0


# ── syncthing snapshot ───────────────────────────────────────────────────────


async def snapshot_for_sync() -> None:
    """Copy events.db to the Syncthing-watched directory as a clean snapshot."""
    global _db_dirty
    if not _db_dirty:
        return

    try:
        os.makedirs(_SYNC_DIR, exist_ok=True)
        db = await get_events_db()
        # Flush the WAL into the main database file before backup so the
        # snapshot is consistent with all committed writes. RESTART ensures
        # we wait for any readers/writers to finish.
        await db.execute("PRAGMA wal_checkpoint(RESTART)")
        await db.commit()

        tmp = _SYNC_PATH + ".tmp"
        async with aiosqlite.connect(tmp) as dst:
            await db.backup(dst)
        os.replace(tmp, _SYNC_PATH)
        _db_dirty = False
        logger.debug("[EVENTS-DB] Snapshot written to sync dir")
    except Exception as e:
        logger.warning(f"[EVENTS-DB] Snapshot failed: {e}")

def restore_from_sync() -> None:
    """Copy the Syncthing-received snapshot into events.db before cogs load.
    Called synchronously at promotion time before the event loop is busy."""
    if not os.path.exists(_SYNC_PATH):
        logger.info("[EVENTS-DB] No sync snapshot found — starting fresh events.db")
        return
    
    global _db
    if _db is not None:
        logger.warning("[EVENTS-DB] Cannot restore while DB is open — close it first")
        return

    try:
        os.makedirs(os.path.dirname(_EVENTS_DB_PATH), exist_ok=True)
        # Ensure we don't copy over a corrupted or partial sync file
        if os.path.getsize(_SYNC_PATH) < 4096: # Minimal SQLite file size
             logger.warning("[EVENTS-DB] Sync snapshot too small, skipping restore")
             return
             
        shutil.copy2(_SYNC_PATH, _EVENTS_DB_PATH)
        logger.info("[EVENTS-DB] Restored events.db from sync snapshot")
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
