"""Tests for `utils.state_store`.

Three behaviours to pin down:
  1. Read-through cache serves repeat reads without hitting Upstash.
  2. Writes double-write to SQLite and Upstash; SQLite is authoritative
     for durability; Upstash failure does not raise.
  3. When Upstash is unavailable, reads fall back to SQLite and writes
     enqueue for later resync (on startup or promotion).
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from utils import state_store, db as sqlite_backend


@pytest.fixture(autouse=True)
async def _reset_module_state():
    """Wipe the cache and dirty queue between tests."""
    state_store._cache.clear()
    
    # Truncate the dirty_writes table in SQLite
    db = await sqlite_backend.get_db()
    async with sqlite_backend._LOCK:
        await db.execute("DELETE FROM dirty_writes")
        await db.commit()
    
    yield
    state_store._cache.clear()
    
    # Repeat cleanup after test
    db = await sqlite_backend.get_db()
    async with sqlite_backend._LOCK:
        await db.execute("DELETE FROM dirty_writes")
        await db.commit()


@pytest.fixture
def upstash_mock(monkeypatch):
    """Patch _upstash_cmd with a scriptable responder."""
    calls: list = []

    async def _default(*args):
        calls.append(args)
        return None

    mock = AsyncMock(side_effect=_default)
    monkeypatch.setattr(state_store, "_upstash_cmd", mock)
    mock.calls = calls  # attach for assertions
    return mock


# ── Cache semantics ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_state_cache_hit_skips_upstash(isolated_db, upstash_mock):
    """Second read in the TTL window must not hit Upstash."""
    async def _responder(*args):
        if args[0] == "GET":
            return "cached-value"
        return None

    upstash_mock.side_effect = _responder

    assert await state_store.get_state("k") == "cached-value"
    before = upstash_mock.call_count
    assert await state_store.get_state("k") == "cached-value"
    after = upstash_mock.call_count
    assert after == before, "second read should be served from cache"


@pytest.mark.asyncio
async def test_cache_expires_after_ttl(isolated_db, upstash_mock, monkeypatch):
    """Once the TTL elapses the next read must go to Upstash again."""
    async def _responder(*args):
        return "v"

    upstash_mock.side_effect = _responder

    # Collapse TTL so the test is fast.
    monkeypatch.setattr(state_store, "CACHE_TTL_SECONDS", 0.05)
    await state_store.get_state("k")
    await asyncio.sleep(0.1)
    await state_store.get_state("k")
    # Two reads, cache expired in between → two commands.
    assert upstash_mock.call_count == 2


@pytest.mark.asyncio
async def test_invalidate_all_caches_wipes_everything(isolated_db, upstash_mock):
    async def _responder(*args):
        return "v"

    upstash_mock.side_effect = _responder
    await state_store.get_state("a")
    await state_store.get_state("b")
    state_store.invalidate_all_caches()
    assert len(state_store._cache) == 0


# ── Writes update cache immediately ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_state_is_visible_locally_before_upstash_ack(
    isolated_db, upstash_mock
):
    """The caller should see its own write without waiting on a read."""
    # Slow Upstash: even before it completes, local cache should answer.
    async def _slow(*args):
        await asyncio.sleep(0.1)

    upstash_mock.side_effect = _slow

    await state_store.set_state("k", "v")
    # get_state should be served from the write-populated cache (0 ms).
    before = upstash_mock.call_count
    assert await state_store.get_state("k") == "v"
    assert upstash_mock.call_count == before, "cache should satisfy read"


@pytest.mark.asyncio
async def test_set_state_writes_to_sqlite_for_durability(
    isolated_db, upstash_mock
):
    await state_store.set_state("k", "v")
    # Bypass our cache; go straight to SQLite backend to prove persistence.
    state_store._cache.clear()
    from utils import db as sqlite_backend
    assert await sqlite_backend.get_state("k") == "v"


# ── Upstash unavailable ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_falls_back_to_sqlite_when_upstash_down(isolated_db, monkeypatch):
    async def _raise(*args):
        raise state_store._UpstashUnavailable("simulated outage")

    monkeypatch.setattr(state_store, "_upstash_cmd", _raise)

    await sqlite_backend.set_state("k", "sqlite-only")

    assert await state_store.get_state("k") == "sqlite-only"


@pytest.mark.asyncio
async def test_write_during_outage_enqueues_for_reconcile(
    isolated_db, monkeypatch
):
    """SQLite still ACKs, Upstash raises → dirty queue grows."""
    async def _raise(*args):
        raise state_store._UpstashUnavailable("simulated outage")

    monkeypatch.setattr(state_store, "_upstash_cmd", _raise)

    await state_store.set_state("k", "v")
    dirty = await sqlite_backend.get_dirty_writes()
    assert len(dirty) == 1
    assert dirty[0]["op"] == "set_state"
    assert dirty[0]["args"] == ["k", "v"]

    # SQLite still has it.
    assert await sqlite_backend.get_state("k") == "v"


@pytest.mark.asyncio
async def test_resync_drains_dirty_queue(isolated_db, monkeypatch):
    """Verify that resync_to_upstash drains the dirty writes created during an outage."""
    # 1. Simulate outage
    async def _fail(*args):
        raise state_store._UpstashUnavailable("down")

    monkeypatch.setattr(state_store, "_upstash_cmd", _fail)
    await state_store.set_state("k", "v")
    
    dirty = await sqlite_backend.get_dirty_writes()
    assert len(dirty) == 1

    # 2. Upstash recovers; trigger manual resync
    calls: list = []
    async def _ok(*args):
        calls.append(args)
        return "OK"

    monkeypatch.setattr(state_store, "_upstash_cmd", _ok)
    
    await state_store.resync_to_upstash()
    
    dirty = await sqlite_backend.get_dirty_writes()
    assert len(dirty) == 0
    assert any(c[0] == "SET" and c[2] == "v" for c in calls)


# ── Bulk paths ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_posted_mds_bulk_load_cached(isolated_db, monkeypatch):
    async def _cmd(*args):
        if args[0] == "SMEMBERS":
            return ["0001", "0002"]
        return None

    monkeypatch.setattr(state_store, "_upstash_cmd", _cmd)

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

    monkeypatch.setattr(state_store, "_upstash_cmd", _cmd)

    before = await state_store.get_posted_mds()
    assert before == set()

    await state_store.add_posted_md("0042")

    after = await state_store.get_posted_mds()
    assert after == {"0042"}, "cache should have been invalidated by the write"


# ── Posted URLs roundtrip ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_posted_urls_roundtrip(isolated_db, monkeypatch):
    storage: dict = {}

    async def _cmd(*args):
        if args[0] == "SET":
            storage[args[1]] = args[2]
            return "OK"
        if args[0] == "GET":
            return storage.get(args[1])
        return None

    monkeypatch.setattr(state_store, "_upstash_cmd", _cmd)

    await state_store.set_posted_urls("day1", ["u1", "u2"])
    # Force cache miss.
    state_store._cache.clear()
    out = await state_store.get_posted_urls("day1")
    assert out == ["u1", "u2"]


# ── Resync ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resync_pushes_sqlite_contents_to_upstash(isolated_db, monkeypatch):
    await sqlite_backend.add_posted_md("0100")
    await sqlite_backend.add_posted_watch("0200")
    await sqlite_backend.set_hash("https://x/a.png", "h1", "auto")

    calls: list = []

    async def _cmd(*args):
        calls.append(args)
        return "OK"

    monkeypatch.setattr(state_store, "_upstash_cmd", _cmd)

    counts = await state_store.resync_to_upstash(force_full=True)

    assert counts["posted_mds"] == 1
    assert counts["posted_watches"] == 1
    assert counts["hashes"] == 1
    
    cmds = [c[0] for c in calls]
    assert "SADD" in cmds
    assert "HSET" in cmds
