"""Coverage round 2: state_store public-API roundtrips.

Uses fakeredis for real Redis semantics (execute_command path) plus the
SQLite mirror, following the fixture pattern in test_state_store.py.
Targets the get/set/add/prune family that had no direct coverage.
"""

import json

import pytest

from utils import db as sqlite_backend
from utils import state_store


@pytest.fixture
async def fake_redis(monkeypatch):
    """Install fakeredis as the state_store Redis client (real execute_command path)."""

    import asyncio

    import fakeredis.aioredis as fakeredis
    import redis.exceptions as rex

    server = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(server=server, decode_responses=True)
    monkeypatch.setattr(state_store, "_redis_client", client)

    # The autouse mock_redis_cmd fixture stubbed _redis_cmd to always raise;
    # restore the real implementation against the fake client.
    async def real_redis_cmd(*args):
        for a in args:
            if a is None:
                raise ValueError("_redis_cmd: None is not a valid argument")
        cmd_name = str(args[0]).upper()
        cmd_args = [str(a) for a in args[1:]]
        try:
            return await client.execute_command(cmd_name, *cmd_args)
        except (
            rex.ConnectionError,
            rex.TimeoutError,
            rex.BusyLoadingError,
            OSError,
            asyncio.TimeoutError,
        ) as e:
            raise state_store._RedisUnavailable(f"Redis unavailable: {e}") from e

    monkeypatch.setattr(state_store, "_redis_cmd", real_redis_cmd)
    yield client
    await client.aclose()


# ── Posted watches ───────────────────────────────────────────────────────────


async def test_watches_roundtrip_and_prune_redis(isolated_db, fake_redis):
    await state_store.add_posted_watch("W0001")
    await state_store.add_posted_watch("W0002")
    await state_store.add_posted_watch("W0003")
    state_store._cache.clear()
    assert await state_store.get_posted_watches() == {"W0001", "W0002", "W0003"}

    # Redis holds extras SQLite does not — prune must SREM them to match.
    await state_store._redis_cmd("SADD", state_store._k_posted_watches(), "W0098", "W0099")
    await state_store.prune_posted_watches(max_size=3)
    state_store._cache.clear()
    assert await state_store.get_posted_watches() == {"W0001", "W0002", "W0003"}


# ── Posted surveys ───────────────────────────────────────────────────────────


async def test_surveys_roundtrip_and_prune_redis(isolated_db, fake_redis):
    await state_store.add_posted_survey("guid-a")
    await state_store.add_posted_survey("guid-b")
    state_store._cache.clear()
    assert await state_store.get_posted_surveys() == {"guid-a", "guid-b"}

    await state_store._redis_cmd("SADD", state_store._k_posted_surveys(), "guid-zz")
    await state_store.prune_posted_surveys(max_size=2)
    state_store._cache.clear()
    assert await state_store.get_posted_surveys() == {"guid-a", "guid-b"}


# ── Posted reports ───────────────────────────────────────────────────────────


async def test_reports_roundtrip_and_prune_redis(isolated_db, fake_redis):
    await state_store.add_posted_report("RP-1")
    await state_store.add_posted_report("RP-2")
    state_store._cache.clear()
    assert await state_store.get_posted_reports() == {"RP-1", "RP-2"}

    await state_store._redis_cmd("SADD", state_store._k_posted_reports(), "RP-zz")
    await state_store.prune_posted_reports(max_size=2)
    state_store._cache.clear()
    assert await state_store.get_posted_reports() == {"RP-1", "RP-2"}


# ── Posted product IDs ───────────────────────────────────────────────────────


async def test_product_ids_roundtrip_remove_and_prune(isolated_db, fake_redis):
    await state_store.add_posted_product_id("P1")
    await state_store.add_posted_product_id("P2")
    state_store._cache.clear()
    assert await state_store.get_posted_product_ids() == {"P1", "P2"}

    await state_store.remove_posted_product_id("P1")
    state_store._cache.clear()
    assert await state_store.get_posted_product_ids() == {"P2"}

    await state_store._redis_cmd("SADD", state_store._k_posted_product_ids(), "P-zz")
    await state_store.prune_posted_product_ids(max_size=1)
    state_store._cache.clear()
    assert await state_store.get_posted_product_ids() == {"P2"}


# ── Soundings ────────────────────────────────────────────────────────────────


async def test_soundings_roundtrip_and_prune(isolated_db, fake_redis):
    await state_store.add_posted_sounding("KOUN_20260810_00z")
    await state_store.add_posted_sounding("KOUN_20260810_12z")
    state_store._cache.clear()
    assert await state_store.get_posted_soundings() == {
        "KOUN_20260810_00z",
        "KOUN_20260810_12z",
    }
    await state_store.prune_posted_soundings(max_days=2)
    state_store._cache.clear()
    assert await state_store.get_posted_soundings() == {
        "KOUN_20260810_00z",
        "KOUN_20260810_12z",
    }


async def test_sounding_handled_watches_roundtrip_and_clear(isolated_db, fake_redis):
    await state_store.add_sounding_handled_watch("W0001")
    await state_store.add_sounding_handled_watch("W0002")
    state_store._cache.clear()
    assert await state_store.get_sounding_handled_watches() == {"W0001", "W0002"}

    await state_store.clear_sounding_handled_watches()
    state_store._cache.clear()
    assert await state_store.get_sounding_handled_watches() == set()


# ── Key/value state ──────────────────────────────────────────────────────────


async def test_delete_state_roundtrip(isolated_db, fake_redis):
    await state_store.set_state("chan", "111")
    state_store._cache.clear()
    assert await state_store.get_state("chan") == "111"

    await state_store.delete_state("chan")
    state_store._cache.clear()
    assert await state_store.get_state("chan") is None


# ── Posted URLs ──────────────────────────────────────────────────────────────


async def test_posted_urls_roundtrip(isolated_db, fake_redis):
    await state_store.set_posted_urls("day1", ["https://example.com/a", "https://example.com/b"])
    state_store._cache.clear()
    assert await state_store.get_posted_urls("day1") == [
        "https://example.com/a",
        "https://example.com/b",
    ]


async def test_posted_urls_malformed_redis_falls_back_to_sqlite(isolated_db, fake_redis):
    await sqlite_backend.set_posted_urls("day2", ["https://sqlite.example.com"])
    await state_store._redis_cmd("SET", state_store._k_posted_urls("day2"), "not-json{")
    state_store._cache.clear()

    urls = await state_store.get_posted_urls("day2")

    assert urls == ["https://sqlite.example.com"]


# ── Product cache / outlook text ─────────────────────────────────────────────


async def test_product_cache_roundtrip_and_miss(isolated_db, fake_redis):
    await state_store.set_product_cache("prod-1", "cached text", ttl=100)
    state_store._cache.clear()
    assert await state_store.get_product_cache("prod-1") == "cached text"
    assert await state_store.get_product_cache("prod-missing") is None


async def test_previous_outlook_roundtrip(isolated_db, fake_redis):
    await state_store.set_previous_outlook_text("2026-08-10", "OUTLOOK TEXT")
    state_store._cache.clear()
    assert await state_store.get_previous_outlook_text("2026-08-10") == "OUTLOOK TEXT"


# ── Validators (SQLite-only) ─────────────────────────────────────────────────


async def test_validators_roundtrip(isolated_db):
    await state_store.set_validators("https://example.com/p", "etag-1", "2026-08-10")
    v = await state_store.get_validators("https://example.com/p")
    assert v is not None
    assert v.get("etag") == "etag-1"
    assert v.get("last_modified") == "2026-08-10"
    allv = await state_store.get_all_validators()
    assert "https://example.com/p" in allv


# ── Posted warnings ──────────────────────────────────────────────────────────


async def test_warnings_roundtrip_and_remove(isolated_db, fake_redis):
    ok = await state_store.add_posted_warning(
        "KOUN.TO.W.0001",
        101,
        202,
        posted_at=100.0,
        area="NORMAN, OK",
        tornado_confidence="observed",
        tornado_severity="pds",
        severity="pds",
        raw_text="TORNADO WARNING",
    )
    assert ok is True
    state_store._cache.clear()
    allw = await state_store.get_all_posted_warnings()
    assert allw["KOUN.TO.W.0001"]["message_id"] == 101
    assert allw["KOUN.TO.W.0001"]["area"] == "NORMAN, OK"

    await state_store.remove_posted_warning("KOUN.TO.W.0001")
    state_store._cache.clear()
    assert await state_store.get_all_posted_warnings() == {}


async def test_warnings_drops_corrupt_redis_entry(isolated_db, fake_redis):
    await state_store._redis_cmd("HSET", state_store._k_posted_warnings(), "BOGUS", "not-json{")
    state_store._cache.clear()

    allw = await state_store.get_all_posted_warnings()

    assert "BOGUS" not in allw


async def test_prune_warnings_trims_redis_extras(isolated_db, fake_redis):
    await state_store.add_posted_warning("KOUN.TO.W.0001", 1, 2, posted_at=1.0)
    await state_store._redis_cmd(
        "HSET", state_store._k_posted_warnings(), "EXTRA.W.0001", '{"message_id": 9}'
    )

    await state_store.prune_posted_warnings(max_size=1)
    state_store._cache.clear()
    assert set(await state_store.get_all_posted_warnings()) == {"KOUN.TO.W.0001"}


# ── Hashes ───────────────────────────────────────────────────────────────────


async def test_hashes_batch_roundtrip(isolated_db, fake_redis):
    await state_store.set_hashes_batch(
        {"https://example.com/a.png": "hash-a", "https://example.com/b.png": "hash-b"},
        "manual",
    )
    state_store._cache.clear()
    hashes = await state_store.get_all_hashes("manual")
    assert hashes.get("https://example.com/a.png") == "hash-a"
    assert hashes.get("https://example.com/b.png") == "hash-b"


# ── Replay / mirror / full resync ────────────────────────────────────────────


async def test_replay_unknown_op_raises(isolated_db):
    with pytest.raises(ValueError):
        await state_store._replay("bogus_op", ())


async def test_mirror_to_sqlite_pulls_redis_state(isolated_db, fake_redis):
    # Seed Redis directly (bypassing state_store writes) to prove the mirror pulls.
    await state_store._redis_cmd("SET", state_store._k_state("mirror_key"), "mirror_val")
    await state_store._redis_cmd(
        "HSET", state_store._k_hash_url_lookup("auto"), "http://mirror/x.png", "abc123"
    )
    await state_store._redis_cmd("SADD", state_store._k_posted_mds(), "9900")
    await state_store._redis_cmd(
        "SET", state_store._k_posted_urls("day1"), json.dumps(["http://mirror/u1"])
    )
    state_store._cache.clear()

    await state_store.mirror_to_sqlite()

    assert await sqlite_backend.get_state("mirror_key") == "mirror_val"
    assert (await sqlite_backend.get_all_hashes("auto")).get("http://mirror/x.png") == "abc123"
    assert "9900" in await sqlite_backend.get_posted_mds()
    assert await sqlite_backend.get_posted_urls("day1") == ["http://mirror/u1"]


async def test_full_resync_pushes_all_kinds_to_redis(isolated_db, fake_redis):
    await sqlite_backend.set_hash("http://full/x.png", "def456", "auto")
    await sqlite_backend.add_posted_md("8800")
    await sqlite_backend.add_posted_watch("WW0001")
    await sqlite_backend.add_posted_survey("guid-full")
    await sqlite_backend.add_posted_report("RP-FULL")
    await sqlite_backend.add_posted_product_id("P-FULL")
    await sqlite_backend.set_state("full_key", "full_val")
    await sqlite_backend.set_posted_urls("day2", ["http://full/u2"])

    counts = await state_store.resync_to_redis(force_full=True)

    assert counts["hashes"] == 1
    assert counts["posted_mds"] == 1
    assert counts["posted_watches"] == 1
    assert counts["posted_surveys"] == 1
    assert counts["posted_reports"] == 1
    assert counts["posted_product_ids"] == 1
    assert counts["state"] == 1
    assert counts["urls"] == 1

    state_store._cache.clear()
    assert await state_store.get_state("full_key") == "full_val"
    assert await state_store.get_posted_mds() == {"8800"}
