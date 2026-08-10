"""Coverage round 2: events_db pure-logic tests.

Covers the event write/read helpers, DAT-guid linking, prune/sync helpers,
and the photo-cache file helpers — no live network (HTTP and DAT calls are
mocked or bypassed).
"""

import os
import time
from unittest.mock import AsyncMock, MagicMock

import utils.http as http
from utils import events_db


# ── Writes / upsert ──────────────────────────────────────────────────────────


async def test_upsert_preserves_existing_dat_guid(isolated_events_db):
    await events_db.add_significant_event(
        event_id="evt-1",
        event_type="Tornado",
        location="NORMAN, OK",
        magnitude="EF2",
        vtec_id="KOUN.TO.W.0001",
        coords="35.22N 97.44W",
        timestamp=time.time(),
        dat_guid="guid-orig",
    )
    # Re-add with empty dat_guid: the ON CONFLICT clause must keep the old one.
    await events_db.add_significant_event(
        event_id="evt-1",
        event_type="Tornado",
        location="NORMAN, OK",
        timestamp=time.time(),
    )
    row = await events_db.get_significant_event_by_vtec("KOUN.TO.W.0001")
    assert row is not None
    assert row["dat_guid"] == "guid-orig"


async def test_get_significant_event_missing_rows_return_none(isolated_events_db):
    assert await events_db.get_significant_event_raw_text("no-such-event") is None
    assert await events_db.get_significant_event_by_vtec("NO.SUCH.VTEC") is None


# ── DAT guid linking ─────────────────────────────────────────────────────────


async def test_link_dat_guid_matches_by_word_overlap(isolated_events_db):
    await events_db.add_significant_event(
        event_id="evt-1",
        event_type="Tornado",
        location="NORMAN, OK",
        magnitude="EF2",
        coords="35.22N 97.44W",
        timestamp=time.time(),
    )
    today = time.strftime("%Y-%m-%d", time.gmtime())

    result = await events_db.link_dat_guid_to_tornado(today, "guid-new", "TORNADO IN NORMAN OK")

    assert result is not None
    assert result[0] == "evt-1"
    assert result[1] == "NORMAN, OK"
    async with isolated_events_db.execute(
        "SELECT dat_guid FROM significant_events WHERE event_id = ?", ("evt-1",)
    ) as cur:
        row = await cur.fetchone()
    assert row["dat_guid"] == "guid-new"


async def test_link_dat_guid_no_match_returns_none(isolated_events_db):
    await events_db.add_significant_event(
        event_id="evt-1",
        event_type="Tornado",
        location="NORMAN, OK",
        timestamp=time.time(),
    )
    today = time.strftime("%Y-%m-%d", time.gmtime())

    assert await events_db.link_dat_guid_to_tornado(today, "guid-new", "SOMETHING ELSE") is None


async def test_link_dat_guid_no_events_returns_none(isolated_events_db):
    today = time.strftime("%Y-%m-%d", time.gmtime())
    assert await events_db.link_dat_guid_to_tornado(today, "guid-new", "NORMAN") is None


# ── Reads ────────────────────────────────────────────────────────────────────


async def test_recent_events_filters_by_type_and_since(isolated_events_db):
    now = time.time()
    await events_db.add_significant_event("evt-old", "Tornado", "OLD, OK", timestamp=now - 100000)
    await events_db.add_significant_event("evt-1h", "Tornado", "ONE, OK", timestamp=now - 3600)
    await events_db.add_significant_event("evt-now", "Flood", "NOW, OK", timestamp=now)

    recent = await events_db.get_recent_significant_events(event_type="Tornado", since_hours=24)

    ids = {e["event_id"] for e in recent}
    assert "evt-old" not in ids
    assert "evt-1h" in ids


async def test_find_matching_tornado_matches_by_location(isolated_events_db):
    now = time.time()
    await events_db.add_significant_event(
        "evt-1",
        "Tornado",
        "NORMAN, OK",
        vtec_id="KOUN.TO.W.0001",
        timestamp=now,
        source="NWS",
    )

    found = await events_db.find_matching_tornado("NWS", now, "TORNADO NORMAN")

    assert found == ("evt-1", "KOUN.TO.W.0001")


async def test_find_matching_tornado_no_rows_returns_none(isolated_events_db):
    now = time.time()
    assert await events_db.find_matching_tornado("NWS", now, "NORMAN") is None


# ── Prune ────────────────────────────────────────────────────────────────────


async def test_prune_old_events_removes_expired_only(isolated_events_db):
    now = time.time()
    await events_db.add_significant_event(
        "evt-old", "Tornado", "OLD, OK", timestamp=now - 100 * 86400
    )
    await events_db.add_significant_event("evt-new", "Tornado", "NEW, OK", timestamp=now)

    deleted = await events_db.prune_old_significant_events(days=30)

    assert deleted == 1
    recent = await events_db.get_recent_significant_events(since_hours=24 * 400)
    assert {e["event_id"] for e in recent} == {"evt-new"}


# ── Syncthing snapshot / restore ─────────────────────────────────────────────


async def test_snapshot_for_sync_and_restore(isolated_events_db):
    await events_db.add_significant_event(
        "evt-snap",
        "Tornado",
        "SNAP, OK",
        vtec_id="KOUN.TO.W.9999",
        timestamp=time.time(),
    )
    await events_db.snapshot_for_sync()
    assert os.path.exists(events_db._SYNC_PATH)

    await events_db.close_events_db()
    events_db.restore_from_sync()
    await events_db.get_events_db()

    row = await events_db.get_significant_event_by_vtec("KOUN.TO.W.9999")
    assert row is not None
    assert row["event_id"] == "evt-snap"


async def test_restore_from_sync_no_snapshot(isolated_events_db):
    # No snapshot file exists yet — restore must return early, not raise.
    events_db.restore_from_sync()


async def test_restore_from_sync_skips_tiny_snapshot(isolated_events_db, monkeypatch, tmp_path):
    await events_db.close_events_db()
    snap = tmp_path / "tiny.db"
    snap.write_bytes(b"x" * 10)
    monkeypatch.setattr(events_db, "_SYNC_PATH", str(snap))

    events_db.restore_from_sync()

    # The events db was created by the fixture with a real schema — restore
    # must NOT have overwritten it with the 10-byte junk file.
    assert os.path.getsize(events_db._EVENTS_DB_PATH) != 10


# ── Syncthing folder mode ────────────────────────────────────────────────────


async def test_syncthing_mode_no_key_returns_early(isolated_events_db, monkeypatch):
    monkeypatch.delenv("SYNCTHING_API_KEY", raising=False)
    await events_db.set_syncthing_folder_mode("sendonly")


async def test_syncthing_mode_updates_folder(isolated_events_db, monkeypatch):
    monkeypatch.setenv("SYNCTHING_API_KEY", "test-key")
    session = MagicMock()
    resp = MagicMock()
    resp.status = 200
    resp.json = AsyncMock(return_value={"type": "sendonly"})
    session.get.return_value.__aenter__ = AsyncMock(return_value=resp)
    session.put.return_value.__aenter__ = AsyncMock(return_value=resp)
    monkeypatch.setattr(events_db, "ensure_session", AsyncMock(return_value=session))

    await events_db.set_syncthing_folder_mode("receiveonly")

    assert session.get.call_count == 1
    assert session.put.call_count == 1


# ── DAT photo fetch / cache helpers ──────────────────────────────────────────


async def test_fetch_dat_photos_requires_coords():
    assert await events_db.fetch_dat_photos(location="", magnitude="", coords="") == []
    assert (
        await events_db.fetch_dat_photos(location="NORMAN", magnitude="EF2", coords="garbage") == []
    )


async def test_fetch_dat_photos_returns_image_urls(monkeypatch):
    query_resp = {"features": [{"attributes": {"objectid": 1}}, {"attributes": {"objectid": 2}}]}
    bulk_resp = {
        "attachmentGroups": [
            {
                "parentObjectId": 1,
                "attachmentInfos": [{"id": 11, "contentType": "image/jpeg"}],
            },
            {
                "parentObjectId": 2,
                "attachmentInfos": [{"id": 22, "contentType": "application/pdf"}],
            },
        ]
    }
    monkeypatch.setattr(http, "http_get_json", AsyncMock(side_effect=[query_resp, bulk_resp]))

    urls = await events_db.fetch_dat_photos(
        location="NORMAN", magnitude="EF2", coords="35.22N 97.44W"
    )

    assert len(urls) == 1
    assert urls[0].endswith("/1/attachments/11")


async def test_cache_dat_photos_downloads_and_lists(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(events_db, "fetch_dat_photos", AsyncMock(return_value=["http://dat/1"]))
    monkeypatch.setattr(http, "http_get_bytes", AsyncMock(return_value=(b"\x89PNG\r\n", 200)))

    count = await events_db.cache_dat_photos(
        "evt-photo", location="NORMAN", magnitude="EF2", coords="35.22N 97.44W"
    )

    assert count == 1
    photos = events_db.get_cached_dat_photos("evt-photo")
    assert len(photos) == 1
    assert photos[0].endswith(".png")


async def test_cache_dat_photos_skips_when_already_cached(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cached_dir = os.path.join("cache", "tornado_photos", "evt-already")
    os.makedirs(cached_dir, exist_ok=True)
    with open(os.path.join(cached_dir, "photo_01.jpg"), "wb") as f:
        f.write(b"junk")

    count = await events_db.cache_dat_photos(
        "evt-already", location="NORMAN", coords="35.22N 97.44W"
    )

    assert count == 0


async def test_cache_dat_photos_no_urls_returns_zero(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(events_db, "fetch_dat_photos", AsyncMock(return_value=[]))

    count = await events_db.cache_dat_photos("evt-none", location="NORMAN", coords="35.22N 97.44W")

    assert count == 0


async def test_cleanup_old_photos_deletes_expired(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    base = os.path.join("cache", "tornado_photos")
    old = os.path.join(base, "evt-old")
    new = os.path.join(base, "evt-new")
    os.makedirs(old, exist_ok=True)
    os.makedirs(new, exist_ok=True)
    past = time.time() - 10 * 86400
    os.utime(old, (past, past))
    os.utime(new, (time.time(), time.time()))

    deleted = await events_db.cleanup_old_photos(days=1)

    assert deleted == 1
    assert not os.path.exists(old)
    assert os.path.exists(new)


def test_get_cached_dat_photos_missing_dir_returns_empty(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert events_db.get_cached_dat_photos("no-dir") == []
