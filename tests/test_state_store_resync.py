"""
Tests for `state_store.resync_to_redis()` dead-letter quarantine path.

The replay loop used to silently delete any dirty_write whose `_replay`
call raised a non-`_RedisUnavailable` exception. That's dangerous: a
schema change that makes a row malformed would silently nuke the entire
queue on the next startup. The new behavior bumps retry_count and, after
`_MAX_REPLAY_RETRIES` attempts, moves the row to a dead-letter table.
"""

import pytest

from utils import state_store, db as sqlite_backend


@pytest.fixture(autouse=True)
def _reset_cache():
    state_store._cache.clear()
    yield
    state_store._cache.clear()


async def test_resync_empty_queue_is_noop(isolated_db):
    result = await state_store.resync_to_redis()
    assert result == {"dirty": 0}


async def test_resync_success_deletes_rows(isolated_db, monkeypatch):
    """Successful replays should drain the queue completely."""

    async def _ok(op, args):
        return None

    monkeypatch.setattr(state_store, "_replay", _ok)

    await sqlite_backend.add_dirty_write("add_posted_md", ("0001",))
    await sqlite_backend.add_dirty_write("add_posted_md", ("0002",))

    result = await state_store.resync_to_redis()
    assert result["dirty"] == 2
    assert result["retried"] == 0
    assert result["quarantined"] == 0
    assert await sqlite_backend.get_dirty_writes() == []


async def test_resync_bumps_retry_on_unexpected_exception(isolated_db, monkeypatch):
    """A non-RedisUnavailable failure should bump retry_count, not delete."""

    async def _boom(op, args):
        raise ValueError("malformed args")

    monkeypatch.setattr(state_store, "_replay", _boom)

    await sqlite_backend.add_dirty_write("add_posted_md", ("0001",))

    result = await state_store.resync_to_redis()
    assert result["dirty"] == 0
    assert result["retried"] == 1
    assert result["quarantined"] == 0

    pending = await sqlite_backend.get_dirty_writes()
    assert len(pending) == 1
    assert pending[0]["retry_count"] == 1


async def test_resync_quarantines_after_max_retries(isolated_db, monkeypatch):
    """After _MAX_REPLAY_RETRIES failures, the row must be quarantined,
    not silently dropped — operator needs visibility into bad rows."""

    async def _boom(op, args):
        raise ValueError("still malformed")

    monkeypatch.setattr(state_store, "_replay", _boom)
    monkeypatch.setattr(state_store, "_MAX_REPLAY_RETRIES", 3)

    await sqlite_backend.add_dirty_write("add_posted_md", ("0001",))

    # First two failed runs just bump.
    await state_store.resync_to_redis()
    await state_store.resync_to_redis()
    pending = await sqlite_backend.get_dirty_writes()
    assert len(pending) == 1
    assert pending[0]["retry_count"] == 2

    # Third failure should trip the quarantine.
    result = await state_store.resync_to_redis()
    assert result["quarantined"] == 1
    assert result["retried"] == 0

    assert await sqlite_backend.get_dirty_writes() == []
    dead = await sqlite_backend.get_quarantined_writes()
    assert len(dead) == 1
    assert dead[0]["op"] == "add_posted_md"
    assert dead[0]["args"] == ["0001"]
    assert dead[0]["retry_count"] == 2


async def test_resync_pauses_on_redis_unavailable(isolated_db, monkeypatch):
    """A RedisUnavailable exception should stop the loop without bumping
    or quarantining — the row will be retried cleanly next startup."""

    async def _down(op, args):
        raise state_store._RedisUnavailable("simulated outage")

    monkeypatch.setattr(state_store, "_replay", _down)

    await sqlite_backend.add_dirty_write("add_posted_md", ("0001",))
    await sqlite_backend.add_dirty_write("add_posted_md", ("0002",))

    result = await state_store.resync_to_redis()
    assert result["dirty"] == 0
    assert result["retried"] == 0
    assert result["quarantined"] == 0

    pending = await sqlite_backend.get_dirty_writes()
    assert len(pending) == 2
    assert all(p["retry_count"] == 0 for p in pending)
