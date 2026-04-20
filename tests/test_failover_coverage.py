"""Coverage tests for the simplified failover cog.

Post-v5 architecture: the cog no longer ships state over HTTP. Its
whole job is leader election via an Upstash lease key. These tests
cover:

  1. Lease-writing / -reading / -releasing against a mock Upstash.
  2. Standby cycle: holder present → counter resets; holder missing →
     counter advances subject to the startup grace window and MAX_FAILURES.
  3. Primary cycle: still our lease → refresh; someone else's lease →
     demote.
  4. Promotion: is_primary flips, cache invalidates, resync runs, cogs
     load.
  5. Demotion: is_primary flips back, cogs unload, counter clears.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

import cogs.failover as failover_module
from cogs.failover import FailoverCog
from utils.state import BotState


def _make_bot(is_primary: bool = True):
    bot = MagicMock()
    bot.state = BotState()
    bot.state.is_primary = is_primary
    bot.load_extension = AsyncMock()
    bot.unload_extension = AsyncMock()
    bot.tree = MagicMock()
    bot.tree.sync = AsyncMock(return_value=[])
    return bot


def _stub_upstash(responses: dict | None = None, default=None):
    """Build an AsyncMock that answers `_upstash` by command keyword.

    `responses` is {command_string: value_to_return}. Unmatched commands
    return `default`.
    """
    responses = responses or {}
    calls: list = []

    async def _resp(*args):
        calls.append(args)
        if not args:
            return default
        return responses.get(args[0], default)

    mock = AsyncMock(side_effect=_resp)
    mock.calls = calls
    return mock


# ── Lease-writing / -reading / -releasing ───────────────────────────────────

async def test_write_lease_uses_set_with_ttl(monkeypatch):
    mock = _stub_upstash()
    monkeypatch.setattr(FailoverCog, "_upstash", mock)
    cog = FailoverCog(_make_bot())

    await cog._write_lease()

    assert mock.calls, "expected a call"
    args = mock.calls[0]
    assert args[0] == "SET"
    assert args[1] == failover_module.LEASE_KEY
    assert args[2] == cog._identity
    assert args[3] == "EX"
    assert int(args[4]) == failover_module.HEARTBEAT_TTL


async def test_read_lease_returns_holder(monkeypatch):
    mock = _stub_upstash({"GET": "some-host:abcd1234"})
    monkeypatch.setattr(FailoverCog, "_upstash", mock)
    cog = FailoverCog(_make_bot(is_primary=False))

    assert await cog._read_lease_holder() == "some-host:abcd1234"


async def test_release_lease_only_deletes_if_ours(monkeypatch):
    """Never DEL someone else's lease — that would strand the cluster."""
    cog = FailoverCog(_make_bot())
    cog._identity = "me"

    mock = _stub_upstash({"GET": "someone-else"})
    monkeypatch.setattr(FailoverCog, "_upstash", mock)

    await cog._release_lease()
    # No DEL should have been issued.
    del_calls = [c for c in mock.calls if c and c[0] == "DEL"]
    assert del_calls == []


async def test_release_lease_deletes_when_ours(monkeypatch):
    cog = FailoverCog(_make_bot())
    cog._identity = "me"

    mock = _stub_upstash({"GET": "me"})
    monkeypatch.setattr(FailoverCog, "_upstash", mock)

    await cog._release_lease()
    del_calls = [c for c in mock.calls if c and c[0] == "DEL"]
    assert del_calls and del_calls[0][1] == failover_module.LEASE_KEY


# ── Fail-fast token guard ───────────────────────────────────────────────────

def test_require_failover_token_rejects_empty(monkeypatch):
    monkeypatch.setattr(failover_module, "FAILOVER_TOKEN", "")
    with pytest.raises(RuntimeError, match="FAILOVER_TOKEN"):
        failover_module._require_failover_token()


def test_require_failover_token_rejects_default_placeholder(monkeypatch):
    monkeypatch.setattr(failover_module, "FAILOVER_TOKEN", "changeme")
    with pytest.raises(RuntimeError, match="FAILOVER_TOKEN"):
        failover_module._require_failover_token()


def test_require_failover_token_accepts_real_value(monkeypatch):
    monkeypatch.setattr(failover_module, "FAILOVER_TOKEN", "real-secret-xyz")
    assert failover_module._require_failover_token() == "real-secret-xyz"


# ── Standby cycle ───────────────────────────────────────────────────────────

async def test_standby_resets_failures_when_lease_present(monkeypatch):
    mock = _stub_upstash({"GET": "holder"})
    monkeypatch.setattr(FailoverCog, "_upstash", mock)

    cog = FailoverCog(_make_bot(is_primary=False))
    cog._primary_failures = 3

    await cog._standby_cycle()
    assert cog._primary_failures == 0
    assert cog.bot.state.is_primary is False


async def test_standby_increments_failures_when_lease_missing(monkeypatch):
    mock = _stub_upstash({"GET": None})
    monkeypatch.setattr(FailoverCog, "_upstash", mock)

    cog = FailoverCog(_make_bot(is_primary=False))
    cog._cog_load_monotonic = 0.0  # past grace

    await cog._standby_cycle()
    assert cog._primary_failures == 1
    assert cog.bot.state.is_primary is False


async def test_standby_startup_grace_does_not_count(monkeypatch):
    import time as _time

    mock = _stub_upstash({"GET": None})
    monkeypatch.setattr(FailoverCog, "_upstash", mock)

    cog = FailoverCog(_make_bot(is_primary=False))
    cog._cog_load_monotonic = _time.monotonic()  # grace window open

    for _ in range(failover_module.MAX_FAILURES + 3):
        await cog._standby_cycle()
    assert cog._primary_failures == 0


async def test_standby_promotes_after_max_failures(monkeypatch):
    """MAX_FAILURES consecutive missing-lease cycles → promote."""
    mock = _stub_upstash({"GET": None})
    monkeypatch.setattr(FailoverCog, "_upstash", mock)
    monkeypatch.setattr(FailoverCog, "_promote", AsyncMock())

    cog = FailoverCog(_make_bot(is_primary=False))
    cog._cog_load_monotonic = 0.0

    for _ in range(failover_module.MAX_FAILURES):
        await cog._standby_cycle()

    FailoverCog._promote.assert_awaited_once()


async def test_standby_does_not_promote_one_short_of_threshold(monkeypatch):
    mock = _stub_upstash({"GET": None})
    monkeypatch.setattr(FailoverCog, "_upstash", mock)
    monkeypatch.setattr(FailoverCog, "_promote", AsyncMock())

    cog = FailoverCog(_make_bot(is_primary=False))
    cog._cog_load_monotonic = 0.0

    for _ in range(failover_module.MAX_FAILURES - 1):
        await cog._standby_cycle()

    FailoverCog._promote.assert_not_awaited()


# ── Primary cycle ───────────────────────────────────────────────────────────

async def test_primary_refreshes_own_lease(monkeypatch):
    cog = FailoverCog(_make_bot(is_primary=True))
    cog._identity = "me"

    mock = _stub_upstash({"GET": "me"})
    monkeypatch.setattr(FailoverCog, "_upstash", mock)

    await cog._primary_cycle()
    # Saw GET, then SET (refresh).
    cmds = [c[0] for c in mock.calls]
    assert cmds[0] == "GET"
    assert "SET" in cmds


async def test_primary_demotes_when_other_holder_wins(monkeypatch):
    cog = FailoverCog(_make_bot(is_primary=True))
    cog._identity = "me"

    mock = _stub_upstash({"GET": "other-holder"})
    monkeypatch.setattr(FailoverCog, "_upstash", mock)
    monkeypatch.setattr(FailoverCog, "_demote", AsyncMock())

    await cog._primary_cycle()
    FailoverCog._demote.assert_awaited_once()


# ── Promotion / demotion ────────────────────────────────────────────────────

async def test_promote_sets_flag_invalidates_cache_loads_cogs(monkeypatch):
    from utils import state_store

    # Seed a cache entry so we can verify invalidation.
    state_store._cache["sentinel"] = state_store._CacheEntry("x", 1e9)

    monkeypatch.setattr(FailoverCog, "_upstash", _stub_upstash({"GET": None}))
    monkeypatch.setattr(
        state_store, "resync_to_upstash", AsyncMock(return_value={})
    )

    cog = FailoverCog(_make_bot(is_primary=False))
    await cog._promote()

    assert cog.bot.state.is_primary is True
    assert "sentinel" not in state_store._cache
    assert cog.bot.load_extension.call_count == len(failover_module.ALL_EXTENSIONS)


async def test_demote_flips_flag_unloads_cogs(monkeypatch):
    cog = FailoverCog(_make_bot(is_primary=True))
    cog._primary_failures = 4

    await cog._demote()

    assert cog.bot.state.is_primary is False
    assert cog.bot.unload_extension.call_count == len(failover_module.ALL_EXTENSIONS)
    assert cog._primary_failures == 0
