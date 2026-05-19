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


# ── Fault Injection: _do_promote cog-load rollback ────────────────────────────
#
# These tests prove that a partial cog-load failure inside _do_promote leaves
# the bot in a clean state (demoted, with previously-loaded cogs unloaded in
# reverse order). Without this coverage, the transactional rollback at
# cogs/failover.py:480-498 has no test pinning its behaviour — exactly the
# scenario that bit v5.28.0 before #412 added the rollback at all.

def _stub_do_promote_prereqs(monkeypatch, cog):
    """Mock out every side-effecting call _do_promote makes BEFORE the
    cog-load loop, so the test isolates the rollback path."""
    monkeypatch.setattr(cog, "_cleanup_own_stale_entries", AsyncMock())
    monkeypatch.setattr(cog, "_build_local_redis",
                        MagicMock(side_effect=Exception("skip redis flip")))
    monkeypatch.setattr(cog, "_write_lease", AsyncMock())
    monkeypatch.setattr(cog, "_rehydrate_bot_state", AsyncMock())

    import utils.state_store as state_store
    monkeypatch.setattr(state_store, "invalidate_all_caches", MagicMock())
    monkeypatch.setattr(state_store, "mirror_to_sqlite", AsyncMock())
    monkeypatch.setattr(state_store, "resync_to_redis", AsyncMock())

    import utils.events_db as events_db
    monkeypatch.setattr(events_db, "restore_from_sync", MagicMock())
    monkeypatch.setattr(events_db, "set_syncthing_folder_mode", AsyncMock())

    # Compress the 2s sleep _do_promote takes before rehydrate
    import asyncio
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())


class TestPromoteFaultInjection:

    @pytest.mark.asyncio
    async def test_partial_cog_load_failure_rolls_back_in_reverse(self, monkeypatch):
        """If load_extension raises on the 4th cog, the 3 already-loaded
        cogs must be unloaded in reverse order and the bot must demote."""
        bot = _make_bot(is_primary=False)
        cog = FailoverCog(bot)
        _stub_do_promote_prereqs(monkeypatch, cog)

        fake_exts = ["cog_a", "cog_b", "cog_c", "cog_boom", "cog_d"]
        monkeypatch.setattr(failover_module, "ALL_EXTENSIONS", fake_exts)

        # Succeed for the first 3, raise on cog_boom
        async def _load(ext):
            if ext == "cog_boom":
                raise RuntimeError("simulated load failure")

        bot.load_extension = AsyncMock(side_effect=_load)
        bot.unload_extension = AsyncMock()
        demote_mock = AsyncMock()
        monkeypatch.setattr(cog, "_demote", demote_mock)

        await cog._do_promote()

        # Unloads happened in reverse order of successful loads
        unload_calls = [c.args[0] for c in bot.unload_extension.await_args_list]
        assert unload_calls == ["cog_c", "cog_b", "cog_a"], \
            f"expected reverse-order unload, got {unload_calls}"
        demote_mock.assert_awaited_once()
        # tree.sync must NOT happen on a rolled-back promotion
        bot.tree.sync.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rollback_continues_when_unload_itself_fails(self, monkeypatch):
        """If unload_extension raises during rollback, the loop must keep
        going and still call _demote — a stuck unload cannot strand the bot
        in a half-rolled-back state."""
        bot = _make_bot(is_primary=False)
        cog = FailoverCog(bot)
        _stub_do_promote_prereqs(monkeypatch, cog)

        fake_exts = ["cog_a", "cog_b", "cog_boom"]
        monkeypatch.setattr(failover_module, "ALL_EXTENSIONS", fake_exts)

        async def _load(ext):
            if ext == "cog_boom":
                raise RuntimeError("load failure triggering rollback")

        bot.load_extension = AsyncMock(side_effect=_load)

        # Unload of cog_b raises; cog_a's unload must still happen
        async def _unload(ext):
            if ext == "cog_b":
                raise RuntimeError("unload also failed")

        bot.unload_extension = AsyncMock(side_effect=_unload)
        demote_mock = AsyncMock()
        monkeypatch.setattr(cog, "_demote", demote_mock)

        await cog._do_promote()

        unload_calls = [c.args[0] for c in bot.unload_extension.await_args_list]
        assert unload_calls == ["cog_b", "cog_a"], \
            f"rollback must continue past unload failure; got {unload_calls}"
        demote_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_full_cog_load_success_syncs_tree_and_does_not_demote(self, monkeypatch):
        """Happy path: every cog loads, slash commands sync, no demote."""
        bot = _make_bot(is_primary=False)
        cog = FailoverCog(bot)
        _stub_do_promote_prereqs(monkeypatch, cog)

        fake_exts = ["cog_a", "cog_b", "cog_c"]
        monkeypatch.setattr(failover_module, "ALL_EXTENSIONS", fake_exts)

        bot.load_extension = AsyncMock()
        bot.unload_extension = AsyncMock()
        bot.get_cog = MagicMock(return_value=None)  # no NWWSCog to trigger
        demote_mock = AsyncMock()
        monkeypatch.setattr(cog, "_demote", demote_mock)

        await cog._do_promote()

        assert bot.load_extension.await_count == 3
        bot.unload_extension.assert_not_awaited()
        demote_mock.assert_not_awaited()
        bot.tree.sync.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_first_cog_failure_demotes_with_no_unloads(self, monkeypatch):
        """If the very first cog fails, there's nothing to roll back — but
        _demote still has to fire."""
        bot = _make_bot(is_primary=False)
        cog = FailoverCog(bot)
        _stub_do_promote_prereqs(monkeypatch, cog)

        fake_exts = ["cog_boom", "cog_b"]
        monkeypatch.setattr(failover_module, "ALL_EXTENSIONS", fake_exts)

        bot.load_extension = AsyncMock(side_effect=RuntimeError("first cog dies"))
        bot.unload_extension = AsyncMock()
        demote_mock = AsyncMock()
        monkeypatch.setattr(cog, "_demote", demote_mock)

        await cog._do_promote()

        bot.unload_extension.assert_not_awaited()
        demote_mock.assert_awaited_once()
