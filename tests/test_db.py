"""
Specialized tests for `utils.db` (SQLite backend).

These tests focus on SQLite-specific logic that isn't visible through
the unified state_store interface:
  1. SQL-based pruning queries (MDs, watches, warnings, soundings).
  2. Database integrity checks.
  3. Concurrent write serialization via the internal asyncio Lock.
  4. Dirty-write queue management (Upstash reconciler storage).
  5. Significant Events archive (events_db).
"""

import asyncio
import time
from utils import db


# ── SQLite Pruning Logic ───────────────────────────────────────────────────

async def test_prune_posted_mds_keeps_most_recent(isolated_db):
    for i in range(10):
        await db.add_posted_md(f"{i:04d}")
    await db.prune_posted_mds(max_size=3)
    remaining = await db.get_posted_mds()
    # Ordered by CAST(md_number AS INTEGER) DESC — newest 3
    assert remaining == {"0007", "0008", "0009"}


async def test_prune_posted_watches_keeps_most_recent(isolated_db):
    for i in range(5):
        await db.add_posted_watch(f"{i:04d}")
    await db.prune_posted_watches(max_size=2)
    assert await db.get_posted_watches() == {"0003", "0004"}


async def test_prune_posted_warnings_keeps_most_recent(isolated_db):
    for i in range(10):
        await db.add_posted_warning(f"ID:{i}", 100+i, 200, posted_at=float(i))
    await db.prune_posted_warnings(max_size=3)
    remaining = await db.get_all_posted_warnings()
    assert len(remaining) == 3
    assert "ID:9" in remaining
    assert "ID:7" in remaining


# ── Integrity & Serialization ──────────────────────────────────────────────

async def test_check_integrity_on_fresh_db(isolated_db):
    assert await db.check_integrity() is True


async def test_concurrent_writes_serialized(isolated_db):
    """Fire 50 concurrent writes; all must land via the internal Lock."""
    await asyncio.gather(*[db.add_posted_md(f"{i:04d}") for i in range(50)])
    assert len(await db.get_posted_mds()) == 50


# ── Dirty Writes (Upstash Reconciler Storage) ──────────────────────────────

async def test_dirty_write_queue_ops(isolated_db):
    await db.add_dirty_write("set_state", ("k", "v"))
    await db.add_dirty_write("add_posted_md", ("0100",))
    
    queue = await db.get_dirty_writes()
    assert len(queue) == 2
    assert queue[0]["op"] == "set_state"
    
    await db.delete_dirty_write(queue[0]["id"])
    assert len(await db.get_dirty_writes()) == 1


async def test_delete_dirty_writes_batch(isolated_db):
    await db.add_dirty_write("op1", ("arg1",))
    await db.add_dirty_write("op2", ("arg2",))
    queue = await db.get_dirty_writes()
    ids = [r["id"] for r in queue]
    
    await db.delete_dirty_writes_batch(ids)
    assert len(await db.get_dirty_writes()) == 0


# ── Significant Events (utils.events_db) ───────────────────────────────────

async def test_significant_events_roundtrip(isolated_events_db):
    from utils import events_db
    await events_db.add_significant_event(
        event_id="TEST:1", event_type="Tornado", location="Somewhere, OK",
        magnitude="Confirmed", source="OUN", timestamp=1000.0, raw_text="Test tornado"
    )
    events = await events_db.get_recent_significant_events(event_type="Tornado", since_hours=24)
    assert len(events) == 0  # timestamp 1000.0 is way in the past

    now = time.time()
    await events_db.add_significant_event(
        event_id="TEST:2", event_type="Tornado", location="Near Moore, OK",
        magnitude="Confirmed", source="OUN", timestamp=now, raw_text="Recent tornado"
    )
    events = await events_db.get_recent_significant_events(event_type="Tornado", since_hours=1)
    assert len(events) == 1
    assert events[0]["location"] == "Near Moore, OK"


async def test_significant_events_conflict_update(isolated_events_db):
    from utils import events_db
    await events_db.add_significant_event(
        event_id="CONFLICT:1", event_type="Tornado", location="Old Location",
        magnitude="Confirmed", source="OUN", timestamp=2000.0
    )
    await events_db.add_significant_event(
        event_id="CONFLICT:1", event_type="Tornado", location="New Location",
        magnitude="EF-2", source="OUN", timestamp=2000.0
    )
    events = await events_db.get_recent_significant_events(event_type="Tornado", since_hours=999999)
    assert len(events) == 1
    assert events[0]["location"] == "New Location"
    assert events[0]["magnitude"] == "EF-2"


async def test_find_matching_tornado(isolated_events_db):
    from utils import events_db
    now = 1714300000.0
    await events_db.add_significant_event(
        event_id="LSR:1", event_type="Tornado", location="3 SE FLORENCE",
        source="HUN", timestamp=now
    )
    match = await events_db.find_matching_tornado(
        source="HUN", timestamp=now + 600, location_query="LAUDERDALE COUNTY TORNADO"
    )
    assert match is not None
    match_id, vtec_id = match
    assert match_id == "LSR:1"
