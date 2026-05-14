"""
Behaviour-driven tests for the Failover Cog (High Availability).

These tests verify the core outcomes of the HA state machine:
  1. Promotion: Standby becomes Primary when the lease is absent.
  2. Demotion: Primary becomes Standby when someone else holds the lease.
  3. Resilience: Nodes respect the startup grace period and heartbeat TTLs.
  4. Lease safety: atomic Lua release/renewal paths.
"""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

import cogs.failover as failover_module
from cogs.failover import FailoverCog
from utils.state import BotState


@pytest.fixture(autouse=True)
def _isolate_hostname(monkeypatch):
    monkeypatch.setattr(failover_module.socket, "gethostname", lambda: "test-node")


def _make_bot(is_primary: bool = True):
    bot = MagicMock()
    bot.state = BotState()
    bot.state.is_primary = is_primary
    bot.load_extension = AsyncMock()
    bot.unload_extension = AsyncMock()
    bot.wait_until_ready = AsyncMock()
    bot.tree = MagicMock()
    bot.tree.sync = AsyncMock(return_value=[])
    return bot


def _stub_exec(responses: dict | None = None, default=None):
    """Return an AsyncMock for `_exec` keyed on the first arg (command name)."""
    responses = responses or {}

    async def _resp(*args):
        if not args:
            return default
        return responses.get(str(args[0]).upper(), default)

    return AsyncMock(side_effect=_resp)


# ── Standby Promotion Scenarios ───────────────────────────────────────────────

class TestStandbyPromotion:

    @pytest.mark.asyncio
    async def test_promotes_when_lease_is_missing(self, monkeypatch):
        """Standby should promote to Primary after MAX_FAILURES consecutive misses."""
        bot = _make_bot(is_primary=False)
        cog = FailoverCog(bot)
        cog._cog_load_monotonic = time.monotonic() - 200  # outside grace period

        monkeypatch.setattr(cog, "_exec", _stub_exec({"GET": None}))
        monkeypatch.setattr(cog, "_promote", AsyncMock())

        cog._primary_failures = failover_module.MAX_FAILURES - 1
        await cog._standby_cycle()

        cog._promote.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stays_standby_if_lease_held_by_other(self, monkeypatch):
        """Standby should remain Standby if another node holds the lease."""
        bot = _make_bot(is_primary=False)
        cog = FailoverCog(bot)
        cog._primary_failures = 3

        monkeypatch.setattr(cog, "_exec", _stub_exec({"GET": "P:other-node:1234"}))

        await cog._standby_cycle()

        assert bot.state.is_primary is False
        assert cog._primary_failures == 0

    @pytest.mark.asyncio
    async def test_no_promotion_during_startup_grace(self, monkeypatch):
        """Lease misses during the 120s startup grace should not count toward promotion."""
        bot = _make_bot(is_primary=False)
        cog = FailoverCog(bot)
        cog._cog_load_monotonic = time.monotonic()  # just started

        monkeypatch.setattr(cog, "_exec", _stub_exec({"GET": None}))
        monkeypatch.setattr(cog, "_promote", AsyncMock())

        # Even if we call the cycle many times, grace period blocks it
        for _ in range(failover_module.MAX_FAILURES + 5):
            await cog._standby_cycle()

        cog._promote.assert_not_awaited()
        assert cog._primary_failures == 0


# ── Primary Demotion Scenarios ────────────────────────────────────────────────

class TestPrimaryDemotion:

    @pytest.mark.asyncio
    async def test_demotes_when_lease_stolen(self, monkeypatch):
        """Primary should demote if it sees another node holds the lease."""
        bot = _make_bot(is_primary=True)
        cog = FailoverCog(bot)
        cog._identity = "P:test-node:abc"

        monkeypatch.setattr(cog, "_exec", _stub_exec({"GET": "P:other-node:xyz"}))
        monkeypatch.setattr(cog, "_demote", AsyncMock())

        await cog._primary_cycle()

        cog._demote.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_renews_lease_when_healthy(self, monkeypatch):
        """Primary should renew its lease if it still owns it."""
        bot = _make_bot(is_primary=True)
        cog = FailoverCog(bot)
        cog._identity = "P:test-node:abc"

        monkeypatch.setattr(cog, "_exec", _stub_exec({"GET": "P:test-node:abc"}))
        renew_mock = AsyncMock(return_value=True)
        monkeypatch.setattr(cog, "_renew_lease", renew_mock)
        monkeypatch.setattr(cog, "_demote", AsyncMock())

        await cog._primary_cycle()

        renew_mock.assert_awaited_once()
        cog._demote.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_demotes_when_renewal_fails(self, monkeypatch):
        """Primary should demote if conditional renewal reports loss of lease."""
        bot = _make_bot(is_primary=True)
        cog = FailoverCog(bot)
        cog._identity = "P:test-node:abc"

        # First GET shows us holding it, renewal fails, re-read shows competitor
        exec_responses = iter(["P:test-node:abc", "P:other-node:xyz"])

        async def _exec_iter(*args):
            if str(args[0]).upper() == "GET":
                try:
                    return next(exec_responses)
                except StopIteration:
                    return None
            return None

        monkeypatch.setattr(cog, "_exec", AsyncMock(side_effect=_exec_iter))
        monkeypatch.setattr(cog, "_renew_lease", AsyncMock(return_value=False))
        monkeypatch.setattr(cog, "_demote", AsyncMock())

        await cog._primary_cycle()

        cog._demote.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reclaims_expired_lease_with_nx(self, monkeypatch):
        """Primary should use NX when reclaiming an expired lease."""
        bot = _make_bot(is_primary=True)
        cog = FailoverCog(bot)
        cog._identity = "P:test-node:abc"

        exec_calls = []

        async def _exec_recorder(*args):
            exec_calls.append(args)
            cmd = str(args[0]).upper()
            if cmd == "GET":
                return None  # Lease expired
            if cmd == "SET":
                return "OK"  # NX succeeded
            return None

        monkeypatch.setattr(cog, "_exec", AsyncMock(side_effect=_exec_recorder))

        await cog._primary_cycle()

        # Should have called SET ... NX
        set_calls = [c for c in exec_calls if str(c[0]).upper() == "SET"]
        assert any("NX" in [str(a).upper() for a in c] for c in set_calls)


# ── Startup Grace & Fail-Fast ─────────────────────────────────────────────────

class TestFailoverResilience:

    def test_startup_grace_period(self):
        """Failures must not count during the first 120s of uptime."""
        cog = FailoverCog(_make_bot())
        cog._cog_load_monotonic = time.monotonic()

        cog._register_failure("test failure")
        assert cog._primary_failures == 0

        cog._cog_load_monotonic = time.monotonic() - 200
        cog._register_failure("test failure")
        assert cog._primary_failures == 1

    def test_token_guard_raises_on_invalid_config(self, monkeypatch):
        """Cog must refuse to load with a default or empty FAILOVER_TOKEN."""
        monkeypatch.setattr(failover_module, "FAILOVER_TOKEN", "changeme")
        with pytest.raises(RuntimeError):
            failover_module._require_failover_token()

        monkeypatch.setattr(failover_module, "FAILOVER_TOKEN", "")
        with pytest.raises(RuntimeError):
            failover_module._require_failover_token()

    @pytest.mark.asyncio
    async def test_manual_override_demotes_primary(self, monkeypatch):
        """sync_loop should detect a manual override naming another host and demote."""
        bot = _make_bot(is_primary=True)
        cog = FailoverCog(bot)
        cog._identity = "P:test-node:abc"

        # HSET for heartbeat registry, GET for manual_primary → "other-host"
        async def _exec_mock(*args):
            cmd = str(args[0]).upper()
            if cmd == "HSET":
                return 1
            if cmd == "GET":
                return "other-host"
            return None

        monkeypatch.setattr(cog, "_exec", AsyncMock(side_effect=_exec_mock))
        monkeypatch.setattr(cog, "_demote", AsyncMock())

        await cog.sync_loop()

        cog._demote.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_manual_override_promotes_standby(self, monkeypatch):
        """sync_loop should promote if manual override names this node."""
        bot = _make_bot(is_primary=False)
        cog = FailoverCog(bot)
        cog._identity = "S:test-node:abc"

        async def _exec_mock(*args):
            cmd = str(args[0]).upper()
            if cmd == "HSET":
                return 1
            if cmd == "GET":
                return "test-node"  # matches our hostname
            return None

        monkeypatch.setattr(cog, "_exec", AsyncMock(side_effect=_exec_mock))
        monkeypatch.setattr(cog, "_promote", AsyncMock())

        await cog.sync_loop()

        cog._promote.assert_awaited_once()


# ── Lease safety (Lua scripts) ────────────────────────────────────────────────

class TestLeaseSafety:

    @pytest.mark.asyncio
    async def test_release_lease_uses_lua(self, monkeypatch):
        """_release_lease must call client.eval (Lua) not a plain DEL."""
        bot = _make_bot(is_primary=True)
        cog = FailoverCog(bot)
        cog._identity = "P:test-node:abc"

        mock_client = AsyncMock()
        mock_client.eval = AsyncMock(return_value=1)
        monkeypatch.setattr(cog, "_get_redis", AsyncMock(return_value=mock_client))

        await cog._release_lease()

        mock_client.eval.assert_awaited_once()
        # First arg to eval is the Lua script
        script_arg = mock_client.eval.call_args[0][0]
        assert "redis.call" in script_arg
        assert "del" in script_arg.lower()

    @pytest.mark.asyncio
    async def test_renew_lease_uses_lua(self, monkeypatch):
        """_renew_lease must call client.eval (Lua) not a plain SET."""
        bot = _make_bot(is_primary=True)
        cog = FailoverCog(bot)
        cog._identity = "P:test-node:abc"

        mock_client = AsyncMock()
        mock_client.eval = AsyncMock(return_value="OK")
        monkeypatch.setattr(cog, "_get_redis", AsyncMock(return_value=mock_client))

        result = await cog._renew_lease()

        assert result is True
        mock_client.eval.assert_awaited_once()
        script_arg = mock_client.eval.call_args[0][0]
        assert "redis.call" in script_arg

    @pytest.mark.asyncio
    async def test_renew_lease_returns_false_when_lost(self, monkeypatch):
        """_renew_lease returns False when Lua script returns nil (lost lease)."""
        bot = _make_bot(is_primary=True)
        cog = FailoverCog(bot)
        cog._identity = "P:test-node:abc"

        mock_client = AsyncMock()
        mock_client.eval = AsyncMock(return_value=None)  # Lua returns nil
        monkeypatch.setattr(cog, "_get_redis", AsyncMock(return_value=mock_client))

        result = await cog._renew_lease()

        assert result is False

    @pytest.mark.asyncio
    async def test_release_lease_noop_when_not_holder(self, monkeypatch):
        """_release_lease should not DEL if Lua script returns 0 (not our key)."""
        bot = _make_bot(is_primary=True)
        cog = FailoverCog(bot)
        cog._identity = "P:test-node:abc"

        mock_client = AsyncMock()
        mock_client.eval = AsyncMock(return_value=0)  # Lua: key belonged to someone else
        monkeypatch.setattr(cog, "_get_redis", AsyncMock(return_value=mock_client))

        # Should not raise; returns silently
        await cog._release_lease()
        mock_client.eval.assert_awaited_once()
