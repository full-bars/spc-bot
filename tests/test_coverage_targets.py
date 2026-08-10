"""Targeted coverage tests for DB-backed stats/events (no network).

Covers the severity-bucket branches of get_warning_stats (the /status
Warning Labels source), the dirty-writes quarantine path, and the
events DB roundtrip — pure-logic surfaces that previously had little or
no coverage.
"""

import pytest

from utils import db
from utils import events_db


# ── get_warning_stats severity buckets ───────────────────────────────────────


async def test_warning_stats_severity_buckets_sum_to_total(isolated_db):
    """TOR severity buckets (standard/pds/emergency) sum to total."""
    await db.add_posted_warning("KOUN.TO.W.0001", 1, 2, posted_at=1.0, tornado_severity="standard")
    await db.add_posted_warning("KOUN.TO.W.0002", 2, 2, posted_at=2.0, tornado_severity="pds")
    await db.add_posted_warning("KOUN.TO.W.0003", 3, 2, posted_at=3.0, tornado_severity="emergency")

    stats = await db.get_warning_stats()

    t = stats["tor"]
    assert t["total"] == 3
    assert t["standard"] == 1
    assert t["pds"] == 1
    assert t["emergency"] == 1
    assert t["standard"] + t["pds"] + t["emergency"] == t["total"]


async def test_warning_stats_svr_and_ffw_severity_buckets(isolated_db):
    await db.add_posted_warning("KOUN.SV.W.0001", 1, 2, posted_at=1.0, severity="destructive")
    await db.add_posted_warning("KOUN.SV.W.0002", 2, 2, posted_at=2.0, severity="considerable")
    await db.add_posted_warning("KOUN.SV.W.0003", 3, 2, posted_at=3.0, severity="standard")
    await db.add_posted_warning("KOUN.FF.W.0001", 1, 2, posted_at=4.0, severity="emergency")
    await db.add_posted_warning("KOUN.FF.W.0002", 2, 2, posted_at=5.0, severity="considerable")
    await db.add_posted_warning("KOUN.FF.W.0003", 3, 2, posted_at=6.0, severity="standard")

    stats = await db.get_warning_stats()

    s = stats["svr"]
    assert s["total"] == 3
    assert s["destructive"] == 1
    assert s["considerable"] == 1
    assert s["standard"] == 1

    f = stats["ffw"]
    assert f["total"] == 3
    assert f["emergency"] == 1
    assert f["considerable"] == 1
    assert f["standard"] == 1


async def test_warning_stats_since_window(isolated_db):
    """Rows outside the since window are excluded."""
    await db.add_posted_warning("KOUN.TO.W.0001", 1, 2, posted_at=100.0)
    await db.add_posted_warning("KOUN.TO.W.0002", 2, 2, posted_at=200.0)

    stats = await db.get_warning_stats(since=150.0)

    assert stats["tor"]["total"] == 1


# ── dirty-writes quarantine path ─────────────────────────────────────────────


async def test_dirty_write_quarantine_moves_rows(isolated_db):
    """Quarantining a failed write moves it to dirty_writes_dead."""
    await db.add_dirty_write("op", ("a",))

    rows = await db.get_dirty_writes()
    assert rows, "expected a queued dirty write"

    await db.quarantine_dirty_writes([r["id"] for r in rows])

    async with db.get_read_db() as read_db:
        dead = await read_db.execute_fetchall("SELECT original_id, op FROM dirty_writes_dead")
        live = await read_db.execute_fetchall("SELECT id FROM dirty_writes")
    assert len(dead) == 1
    assert dead[0][1] == "op"
    assert live == []


# ── events DB roundtrip ──────────────────────────────────────────────────────


async def test_events_db_add_and_fetch_by_vtec():
    try:
        await events_db.add_significant_event(
            event_id="evt-1",
            event_type="TOR",
            location="NORMAN, OK",
            vtec_id="KOUN.TO.W.0001",
            timestamp=1.0,
            raw_text="TORNADO WARNING",
        )
        row = await events_db.get_significant_event_by_vtec("KOUN.TO.W.0001")
        assert row is not None
        assert row["event_id"] == "evt-1"

        raw = await events_db.get_significant_event_raw_text("evt-1")
        assert raw == "TORNADO WARNING"

        await events_db.update_event_environment("evt-1", "/tmp/gif.gif", 150.0)
        row2 = await events_db.get_significant_event_by_vtec("KOUN.TO.W.0001")
        assert row2["srh_0_1"] == pytest.approx(150.0)
    finally:
        await events_db.close_events_db()
