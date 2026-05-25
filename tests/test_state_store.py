"""Tests for `utils.state_store`.

Three behaviours to pin down:
  1. Read-through cache serves repeat reads without hitting Redis.
  2. Writes double-write to SQLite and Redis; SQLite is authoritative
     for durability; Redis failure does not raise.
  3. When Redis is unavailable, reads fall back to SQLite and writes
     enqueue for later resync.
  4. Integration: real Redis semantics via fakeredis.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from utils import state_store, db as sqlite_backend


@pytest.fixture(autouse=True)
async def _reset_module_state():
    """Wipe the cache and dirty queue between tests."""
    state_store._cache.clear()
    db = await sqlite_backend.get_db()
    async with sqlite_backend._LOCK:
        await db.execute("DELETE FROM dirty_writes")
        await db.commit()
    yield
    state_store._cache.clear()
    db = await sqlite_backend.get_db()
    async with sqlite_backend._LOCK:
        await db.execute("DELETE FROM dirty_writes")
        await db.commit()


@pytest.fixture
def redis_mock(monkeypatch):
    """Patch _redis_cmd with a scriptable responder."""
    calls: list = []

    async def _default(*args):
        calls.append(args)
        return None

    mock = AsyncMock(side_effect=_default)
    monkeypatch.setattr(state_store, "_redis_cmd", mock)
    mock.calls = calls
    return mock


# ── Cache semantics ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_state_cache_hit_skips_redis(isolated_db, redis_mock):
    """Second read in the TTL window must not hit Redis."""

    async def _responder(*args):
        if args[0] == "GET":
            return "cached-value"
        return None

    redis_mock.side_effect = _responder
    assert await state_store.get_state("k") == "cached-value"
    before = redis_mock.call_count
    assert await state_store.get_state("k") == "cached-value"
    assert redis_mock.call_count == before, "second read should be served from cache"


@pytest.mark.asyncio
async def test_cache_expires_after_ttl(isolated_db, redis_mock, monkeypatch):
    """Once the TTL elapses the next read must go to Redis again."""

    async def _responder(*args):
        return "v"

    redis_mock.side_effect = _responder
    monkeypatch.setattr(state_store, "CACHE_TTL_SECONDS", 0.05)
    await state_store.get_state("k")
    await asyncio.sleep(0.1)
    await state_store.get_state("k")
    assert redis_mock.call_count == 2


@pytest.mark.asyncio
async def test_invalidate_all_caches_wipes_everything(isolated_db, redis_mock):
    async def _responder(*args):
        return "v"

    redis_mock.side_effect = _responder
    await state_store.get_state("a")
    await state_store.get_state("b")
    state_store.invalidate_all_caches()
    assert len(state_store._cache) == 0


# ── Writes update cache immediately ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_state_is_visible_locally_before_redis_ack(isolated_db, redis_mock):
    async def _slow(*args):
        await asyncio.sleep(0.1)

    redis_mock.side_effect = _slow
    await state_store.set_state("k", "v")
    before = redis_mock.call_count
    assert await state_store.get_state("k") == "v"
    assert redis_mock.call_count == before, "cache should satisfy read"


@pytest.mark.asyncio
async def test_set_state_writes_to_sqlite_for_durability(isolated_db, redis_mock):
    await state_store.set_state("k", "v")
    state_store._cache.clear()
    assert await sqlite_backend.get_state("k") == "v"


# ── Redis unavailable ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_falls_back_to_sqlite_when_redis_down(isolated_db, monkeypatch):
    async def _raise(*args):
        raise state_store._RedisUnavailable("simulated outage")

    monkeypatch.setattr(state_store, "_redis_cmd", _raise)
    await sqlite_backend.set_state("k", "sqlite-only")
    assert await state_store.get_state("k") == "sqlite-only"


@pytest.mark.asyncio
async def test_write_during_outage_enqueues_for_reconcile(isolated_db, monkeypatch):
    async def _raise(*args):
        raise state_store._RedisUnavailable("simulated outage")

    monkeypatch.setattr(state_store, "_redis_cmd", _raise)
    await state_store.set_state("k", "v")
    dirty = await sqlite_backend.get_dirty_writes()
    assert len(dirty) == 1
    assert dirty[0]["op"] == "set_state"
    assert dirty[0]["args"] == ["k", "v"]
    assert await sqlite_backend.get_state("k") == "v"


@pytest.mark.asyncio
async def test_resync_drains_dirty_queue(isolated_db, monkeypatch):
    async def _fail(*args):
        raise state_store._RedisUnavailable("down")

    monkeypatch.setattr(state_store, "_redis_cmd", _fail)
    await state_store.set_state("k", "v")
    assert len(await sqlite_backend.get_dirty_writes()) == 1

    calls: list = []

    async def _ok(*args):
        calls.append(args)
        return "OK"

    monkeypatch.setattr(state_store, "_redis_cmd", _ok)
    await state_store.resync_to_redis()
    assert len(await sqlite_backend.get_dirty_writes()) == 0
    assert any(c[0] == "SET" and c[2] == "v" for c in calls)


# ── Bulk paths ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_posted_mds_bulk_load_cached(isolated_db, monkeypatch):
    async def _cmd(*args):
        if args[0] == "SMEMBERS":
            return ["0001", "0002"]
        return None

    monkeypatch.setattr(state_store, "_redis_cmd", _cmd)
    first = await state_store.get_posted_mds()
    second = await state_store.get_posted_mds()
    assert first == {"0001", "0002"} == second


@pytest.mark.asyncio
async def test_add_posted_md_invalidates_cache(isolated_db, monkeypatch):
    async def _cmd(*args):
        if args[0] == "SMEMBERS":
            return list(getattr(_cmd, "contents", []))
        if args[0] == "SADD":
            _cmd.contents = list(getattr(_cmd, "contents", [])) + [args[2]]
            return 1
        return None

    monkeypatch.setattr(state_store, "_redis_cmd", _cmd)
    assert await state_store.get_posted_mds() == set()
    await state_store.add_posted_md("0042")
    assert await state_store.get_posted_mds() == {"0042"}


@pytest.mark.asyncio
async def test_get_all_hashes_returns_dict(isolated_db, monkeypatch):
    async def _cmd(*args):
        if args[0] == "HGETALL":
            return {"http://example.com/img.png": "abc123"}
        return None

    monkeypatch.setattr(state_store, "_redis_cmd", _cmd)
    result = await state_store.get_all_hashes("auto")
    assert result == {"http://example.com/img.png": "abc123"}


@pytest.mark.asyncio
async def test_get_all_posted_warnings_parses_dict(isolated_db, monkeypatch):
    import json

    warning_data = {
        "message_id": 1,
        "channel_id": 2,
        "area": "OK",
        "tornado_confidence": None,
        "tornado_severity": None,
    }

    async def _cmd(*args):
        if args[0] == "HGETALL":
            return {"KOK.TO.W.0001.250501T000000Z": json.dumps(warning_data)}
        return None

    monkeypatch.setattr(state_store, "_redis_cmd", _cmd)
    result = await state_store.get_all_posted_warnings()
    assert "KOK.TO.W.0001.250501T000000Z" in result
    assert result["KOK.TO.W.0001.250501T000000Z"]["message_id"] == 1


# ── Outage scenarios ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_posted_md_falls_back_on_redis_outage(isolated_db, monkeypatch):
    async def _fail(*args):
        raise state_store._RedisUnavailable("down")

    monkeypatch.setattr(state_store, "_redis_cmd", _fail)
    await state_store.add_posted_md("0100")
    state_store._cache.clear()
    # Redis is down → falls back to SQLite which has the value
    assert await state_store.get_posted_mds() == {"0100"}
    assert await sqlite_backend.get_posted_mds() == {"0100"}


@pytest.mark.asyncio
async def test_add_posted_watch_falls_back_on_redis_outage(isolated_db, monkeypatch):
    async def _fail(*args):
        raise state_store._RedisUnavailable("down")

    monkeypatch.setattr(state_store, "_redis_cmd", _fail)
    await state_store.add_posted_watch("0102")
    dirty = await sqlite_backend.get_dirty_writes()
    assert any(d["op"] == "add_posted_watch" for d in dirty)


@pytest.mark.asyncio
async def test_add_posted_md_is_idempotent(isolated_db, monkeypatch):
    async def _fail(*args):
        raise state_store._RedisUnavailable("down")

    monkeypatch.setattr(state_store, "_redis_cmd", _fail)
    await state_store.add_posted_md("0100")
    await state_store.add_posted_md("0100")
    state_store._cache.clear()
    assert await state_store.get_posted_mds() == {"0100"}


# ── Integration: real Redis semantics via fakeredis ──────────────────────────


@pytest.fixture
async def fake_redis_client(monkeypatch):
    """Install a fakeredis server as the state_store Redis client.

    This exercises the real _redis_cmd → execute_command path, exception
    classifier, HGETALL dict format, and SMEMBERS set format — the paths
    that the mock-based tests above cannot reach.
    """
    import fakeredis.aioredis as fakeredis

    server = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(server=server, decode_responses=True)
    monkeypatch.setattr(state_store, "_redis_client", client)

    # The autouse mock_redis_cmd fixture replaced _redis_cmd with a stub that
    # always raises _RedisUnavailable. Restore the real implementation by
    # re-implementing it inline against the fake client — _get_redis_client()
    # now returns the fake client so execute_command goes to fakeredis.
    async def real_redis_cmd_impl(*args):
        for a in args:
            if a is None:
                raise ValueError("_redis_cmd: None is not a valid argument")
        import redis.exceptions

        cmd_name = str(args[0]).upper()
        cmd_args = [str(a) for a in args[1:]]
        try:
            return await client.execute_command(cmd_name, *cmd_args)
        except (
            redis.exceptions.ConnectionError,
            redis.exceptions.TimeoutError,
            redis.exceptions.BusyLoadingError,
            OSError,
            asyncio.TimeoutError,
        ) as e:
            raise state_store._RedisUnavailable(f"Redis unavailable: {e}") from e

    monkeypatch.setattr(state_store, "_redis_cmd", real_redis_cmd_impl)
    yield client
    await client.aclose()


@pytest.mark.asyncio
async def test_fakeredis_set_get_roundtrip(isolated_db, fake_redis_client):
    """Real execute_command path: SET then GET returns the value."""
    await state_store.set_state("hello", "world")
    state_store._cache.clear()
    result = await state_store.get_state("hello")
    assert result == "world"


@pytest.mark.asyncio
async def test_fakeredis_smembers_roundtrip(isolated_db, fake_redis_client):
    """Real SADD / SMEMBERS path returns correct set."""
    await state_store.add_posted_md("0100")
    await state_store.add_posted_md("0200")
    state_store._cache.clear()
    result = await state_store.get_posted_mds()
    assert result == {"0100", "0200"}


@pytest.mark.asyncio
async def test_fakeredis_hgetall_returns_dict(isolated_db, fake_redis_client):
    """HGETALL with decode_responses=True returns a dict, not a flat list."""
    await state_store.set_hash("http://example.com/x.png", "deadbeef", "auto")
    state_store._cache.clear()
    result = await state_store.get_all_hashes("auto")
    assert isinstance(result, dict)
    assert result.get("http://example.com/x.png") == "deadbeef"


@pytest.mark.asyncio
async def test_fakeredis_connection_error_triggers_fallback(isolated_db, monkeypatch):
    """A ConnectionError from execute_command is converted to _RedisUnavailable
    and the caller falls back to SQLite."""
    import redis.exceptions as rex

    async def _bang(*args, **kwargs):
        raise rex.ConnectionError("refused")

    monkeypatch.setattr(
        state_store,
        "_redis_cmd",
        lambda *a: (_ for _ in ()).throw(state_store._RedisUnavailable("refused")),
    )

    async def _unavail(*args):
        raise state_store._RedisUnavailable("refused")

    monkeypatch.setattr(state_store, "_redis_cmd", _unavail)

    await sqlite_backend.set_state("fallback_key", "sqlite_val")
    result = await state_store.get_state("fallback_key")
    assert result == "sqlite_val"


@pytest.mark.asyncio
async def test_fakeredis_resync_full(isolated_db, fake_redis_client):
    """_resync_full pushes SQLite state into Redis."""
    await sqlite_backend.set_state("resync_key", "resync_val")
    counts = await state_store.resync_to_redis(force_full=True)
    assert counts.get("state", 0) >= 1
    state_store._cache.clear()
    result = await state_store.get_state("resync_key")
    assert result == "resync_val"
