"""
Behaviour-driven tests for the Failover Cog (High Availability).

These tests verify the core outcomes of the HA state machine:
  1. Promotion: Standby becomes Primary when the lease is absent.
  2. Demotion: Primary becomes Standby when someone else holds the lease.
  3. Resilience: Nodes respect the startup grace period and heartbeat TTLs.
  4. Lease safety: atomic Lua release/renewal paths.
"""

import asyncio
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


# ── Reconciler drain ──────────────────────────────────────────────────────────


class TestReconcilerDrain:
    @pytest.mark.asyncio
    async def test_sync_loop_drains_dirty_writes_when_primary(self, monkeypatch):
        """A healthy Primary should drain the dirty-write queue each cycle."""
        bot = _make_bot(is_primary=True)
        cog = FailoverCog(bot)
        cog._identity = "P:test-node:abc"

        monkeypatch.setattr(cog, "_exec", _stub_exec({"HSET": 1, "GET": None}))
        monkeypatch.setattr(cog, "_primary_cycle", AsyncMock())
        resync = AsyncMock(return_value={"dirty": 2})
        monkeypatch.setattr(failover_module.state_store, "resync_to_redis", resync)

        await cog.sync_loop()

        resync.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_drain_when_primary_cycle_demotes(self, monkeypatch):
        """If _primary_cycle demotes us mid-cycle, we must not drain as Primary."""
        bot = _make_bot(is_primary=True)
        cog = FailoverCog(bot)
        cog._identity = "P:test-node:abc"

        async def _demote_side_effect():
            bot.state.is_primary = False

        monkeypatch.setattr(cog, "_exec", _stub_exec({"HSET": 1, "GET": None}))
        monkeypatch.setattr(cog, "_primary_cycle", AsyncMock(side_effect=_demote_side_effect))
        resync = AsyncMock(return_value={"dirty": 0})
        monkeypatch.setattr(failover_module.state_store, "resync_to_redis", resync)

        await cog.sync_loop()

        resync.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_drain_swallows_resync_errors(self, monkeypatch):
        """A failed resync must not propagate out of the drain helper."""
        bot = _make_bot(is_primary=True)
        cog = FailoverCog(bot)

        monkeypatch.setattr(
            failover_module.state_store,
            "resync_to_redis",
            AsyncMock(side_effect=RuntimeError("redis exploded")),
        )

        # Should not raise.
        await cog._drain_dirty_writes()


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
    monkeypatch.setattr(
        cog, "_build_local_redis", MagicMock(side_effect=Exception("skip redis flip"))
    )
    monkeypatch.setattr(cog, "_write_lease", AsyncMock())
    monkeypatch.setattr(cog, "_rehydrate_bot_state", AsyncMock())

    # SET NX at the top of _do_promote returns "OK" so these tests exercise
    # the cog-load path (without the stub the network call aborts promotion).
    monkeypatch.setattr(cog, "_exec", _stub_exec({"SET": "OK"}))

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
        # _do_promote calls _do_demote() directly (not _demote()) to avoid
        # re-acquiring the non-reentrant _role_lock — see the deadlock
        # regression test below.
        demote_mock = AsyncMock()
        monkeypatch.setattr(cog, "_do_demote", demote_mock)

        await cog._do_promote()

        # Unloads happened in reverse order of successful loads
        unload_calls = [c.args[0] for c in bot.unload_extension.await_args_list]
        assert unload_calls == ["cog_c", "cog_b", "cog_a"], (
            f"expected reverse-order unload, got {unload_calls}"
        )
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
        monkeypatch.setattr(cog, "_do_demote", demote_mock)

        await cog._do_promote()

        unload_calls = [c.args[0] for c in bot.unload_extension.await_args_list]
        assert unload_calls == ["cog_b", "cog_a"], (
            f"rollback must continue past unload failure; got {unload_calls}"
        )
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
        monkeypatch.setattr(cog, "_do_demote", demote_mock)

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
        monkeypatch.setattr(cog, "_do_demote", demote_mock)

        await cog._do_promote()

        bot.unload_extension.assert_not_awaited()
        demote_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_promote_rollback_does_not_deadlock_on_role_lock(self, monkeypatch):
        """Regression: _do_promote's cog-load rollback must NOT re-acquire
        _role_lock, because the lock is already held by _promote() and
        asyncio.Lock is not reentrant. Going through the public _promote()
        entry point with a real lock would deadlock forever if rollback
        called _demote() (which also acquires the lock).

        We bound the test with wait_for; a timeout fails loudly rather than
        hanging the test session."""
        bot = _make_bot(is_primary=False)
        cog = FailoverCog(bot)
        _stub_do_promote_prereqs(monkeypatch, cog)

        fake_exts = ["cog_a", "cog_boom"]
        monkeypatch.setattr(failover_module, "ALL_EXTENSIONS", fake_exts)

        async def _load(ext):
            if ext == "cog_boom":
                raise RuntimeError("trigger rollback")

        bot.load_extension = AsyncMock(side_effect=_load)
        bot.unload_extension = AsyncMock()
        # Do NOT mock _do_demote here — we want the real demote path to run
        # so it acquires/releases state appropriately. But stub the extension
        # unload-loop side of _do_demote to keep the test isolated.
        from discord.ext.commands import ExtensionNotLoaded

        bot.unload_extension = AsyncMock(side_effect=ExtensionNotLoaded("noop"))

        # Real lock semantics — must complete within 2 seconds.
        try:
            await asyncio.wait_for(cog._promote(), timeout=2.0)
        except asyncio.TimeoutError:
            pytest.fail(
                "_promote() rollback deadlocked on _role_lock — the rollback "
                "path must call _do_demote() directly (lock already held), "
                "not _demote() which tries to re-acquire the same lock."
            )

        # After rollback the node should be back in standby state.
        assert bot.state.is_primary is False, (
            "rollback must leave the node demoted, not stuck as primary"
        )


# ── Fault Injection: side-effects during _do_promote ──────────────────────────
#
# The promotion sequence runs four side-effects between writing the lease and
# loading cogs:
#   1. invalidate_all_caches()  (sync, in-process)
#   2. mirror_to_sqlite()       (async, Redis → SQLite)
#   3. _rehydrate_bot_state()   (async, Redis → BotState)
#   4. resync_to_redis()        (async, SQLite dirty queue → Redis)
#
# Each is wrapped in a try/except inside _do_promote because none of them is
# fatal — losing them at promotion only degrades the new primary's freshness,
# it doesn't break correctness. These tests pin that contract: if any of (2)
# (3) or (4) raises, the bot still finishes promotion with all cogs loaded
# and `tree.sync()` called. Without these tests an over-zealous refactor
# could accidentally make any of these failures fatal and strand a node
# that should have been a healthy primary.


def _stub_for_full_promotion(monkeypatch, cog):
    """Mock prereqs so _do_promote runs end-to-end with a normal cog list."""
    monkeypatch.setattr(cog, "_cleanup_own_stale_entries", AsyncMock())
    monkeypatch.setattr(
        cog, "_build_local_redis", MagicMock(side_effect=Exception("skip redis flip"))
    )
    monkeypatch.setattr(cog, "_write_lease", AsyncMock())
    # SET NX at top of _do_promote returns "OK" so these tests can exercise
    # the post-claim side-effect paths.
    monkeypatch.setattr(cog, "_exec", _stub_exec({"SET": "OK"}))

    import utils.events_db as events_db

    monkeypatch.setattr(events_db, "restore_from_sync", MagicMock())
    monkeypatch.setattr(events_db, "set_syncthing_folder_mode", AsyncMock())

    import asyncio

    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    cog.bot.get_cog = MagicMock(return_value=None)


class TestPromoteSideEffectFaultInjection:
    @pytest.mark.asyncio
    async def test_mirror_to_sqlite_failure_does_not_abort_promotion(self, monkeypatch):
        """If mirror_to_sqlite() raises, the bot still loads cogs and syncs."""
        bot = _make_bot(is_primary=False)
        cog = FailoverCog(bot)
        _stub_for_full_promotion(monkeypatch, cog)

        import utils.state_store as state_store

        monkeypatch.setattr(state_store, "invalidate_all_caches", MagicMock())
        monkeypatch.setattr(
            state_store,
            "mirror_to_sqlite",
            AsyncMock(side_effect=RuntimeError("redis SCAN exploded")),
        )
        monkeypatch.setattr(state_store, "resync_to_redis", AsyncMock(return_value={"dirty": 0}))
        monkeypatch.setattr(cog, "_rehydrate_bot_state", AsyncMock())

        monkeypatch.setattr(failover_module, "ALL_EXTENSIONS", ["cog_a", "cog_b"])
        bot.load_extension = AsyncMock()

        await cog._do_promote()

        assert bot.state.is_primary is True
        assert bot.load_extension.await_count == 2
        bot.tree.sync.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rehydrate_failure_does_not_abort_promotion(self, monkeypatch):
        """If _rehydrate_bot_state() raises, the bot still loads cogs and syncs.
        BotState may be partially populated but the cogs come up — they'll fill
        the gaps on their first poll cycle."""
        bot = _make_bot(is_primary=False)
        cog = FailoverCog(bot)
        _stub_for_full_promotion(monkeypatch, cog)

        import utils.state_store as state_store

        monkeypatch.setattr(state_store, "invalidate_all_caches", MagicMock())
        monkeypatch.setattr(state_store, "mirror_to_sqlite", AsyncMock())
        monkeypatch.setattr(state_store, "resync_to_redis", AsyncMock(return_value={"dirty": 0}))
        monkeypatch.setattr(
            cog, "_rehydrate_bot_state", AsyncMock(side_effect=RuntimeError("HGETALL failed"))
        )

        monkeypatch.setattr(failover_module, "ALL_EXTENSIONS", ["cog_a"])
        bot.load_extension = AsyncMock()

        await cog._do_promote()

        assert bot.state.is_primary is True
        bot.load_extension.assert_awaited_once()
        bot.tree.sync.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_resync_failure_sends_critical_alert_but_continues(self, monkeypatch):
        """If resync_to_redis() raises, an alert is sent (dirty writes from
        the standby period may be lost) — but promotion still completes."""
        bot = _make_bot(is_primary=False)
        cog = FailoverCog(bot)
        _stub_for_full_promotion(monkeypatch, cog)

        import utils.state_store as state_store

        monkeypatch.setattr(state_store, "invalidate_all_caches", MagicMock())
        monkeypatch.setattr(state_store, "mirror_to_sqlite", AsyncMock())
        monkeypatch.setattr(cog, "_rehydrate_bot_state", AsyncMock())
        monkeypatch.setattr(
            state_store, "resync_to_redis", AsyncMock(side_effect=RuntimeError("replay died"))
        )

        # main.send_bot_alert is imported lazily inside _do_promote; patch the
        # module-level symbol so the import inside the except sees our mock.
        import main as main_module

        send_alert = AsyncMock()
        monkeypatch.setattr(main_module, "send_bot_alert", send_alert)

        monkeypatch.setattr(failover_module, "ALL_EXTENSIONS", ["cog_a"])
        bot.load_extension = AsyncMock()

        await cog._do_promote()

        assert bot.state.is_primary is True
        bot.load_extension.assert_awaited_once()
        bot.tree.sync.assert_awaited_once()
        send_alert.assert_awaited_once()
        # The alert must be flagged critical so operators see it red, not orange.
        assert send_alert.await_args.kwargs.get("critical") is True

    @pytest.mark.asyncio
    async def test_all_three_side_effects_fail_promotion_still_completes(self, monkeypatch):
        """Worst-case: every non-cog side-effect raises. Bot still reaches
        a functional primary state — degraded freshness, but operational."""
        bot = _make_bot(is_primary=False)
        cog = FailoverCog(bot)
        _stub_for_full_promotion(monkeypatch, cog)

        import utils.state_store as state_store

        monkeypatch.setattr(state_store, "invalidate_all_caches", MagicMock())
        monkeypatch.setattr(
            state_store, "mirror_to_sqlite", AsyncMock(side_effect=RuntimeError("mirror down"))
        )
        monkeypatch.setattr(
            cog, "_rehydrate_bot_state", AsyncMock(side_effect=RuntimeError("rehydrate down"))
        )
        monkeypatch.setattr(
            state_store, "resync_to_redis", AsyncMock(side_effect=RuntimeError("resync down"))
        )

        import main as main_module

        monkeypatch.setattr(main_module, "send_bot_alert", AsyncMock())

        monkeypatch.setattr(failover_module, "ALL_EXTENSIONS", ["cog_a", "cog_b"])
        bot.load_extension = AsyncMock()

        await cog._do_promote()

        assert bot.state.is_primary is True
        assert bot.load_extension.await_count == 2
        bot.tree.sync.assert_awaited_once()


# ── Split-brain prevention: atomic lease claim at _do_promote start ──────────
#
# These tests pin the contract that `_do_promote()` MUST claim the lease as
# its first action (via `SET ... NX EX HEARTBEAT_TTL`) before mutating any
# in-process state. If two standbys promote concurrently, the NX loser must
# abort cleanly — no `is_primary=True`, no cache invalidation, no cog load.
# The `force=True` path (manual override) bypasses NX so an operator can
# forcibly take the lease from any currently-held node.


class TestPromoteSplitBrainPrevention:
    @pytest.mark.asyncio
    async def test_nx_loser_aborts_promotion_cleanly(self, monkeypatch):
        """If SET NX returns None (lease held), the cog must NOT mutate
        is_primary, must NOT invalidate caches, must NOT load any cogs."""
        bot = _make_bot(is_primary=False)
        cog = FailoverCog(bot)
        _stub_do_promote_prereqs(monkeypatch, cog)
        # Override the prereq stub: SET returns None (NX failed),
        # GET (subsequent holder lookup) returns the other node's identity.
        monkeypatch.setattr(
            cog,
            "_exec",
            _stub_exec(
                {
                    "SET": None,
                    "GET": "P:other-node:beef",
                }
            ),
        )

        import utils.state_store as state_store

        invalidate = MagicMock()
        monkeypatch.setattr(state_store, "invalidate_all_caches", invalidate)
        monkeypatch.setattr(failover_module, "ALL_EXTENSIONS", ["cog_a"])
        bot.load_extension = AsyncMock()

        await cog._do_promote()

        assert bot.state.is_primary is False, "NX loser must not flip is_primary"
        invalidate.assert_not_called()
        bot.load_extension.assert_not_awaited()
        bot.tree.sync.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_nx_winner_completes_promotion(self, monkeypatch):
        """If SET NX returns 'OK', the cog promotes normally."""
        bot = _make_bot(is_primary=False)
        cog = FailoverCog(bot)
        _stub_do_promote_prereqs(monkeypatch, cog)  # already stubs SET → "OK"

        monkeypatch.setattr(failover_module, "ALL_EXTENSIONS", ["cog_a", "cog_b"])
        bot.load_extension = AsyncMock()

        await cog._do_promote()

        assert bot.state.is_primary is True
        assert bot.load_extension.await_count == 2
        bot.tree.sync.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_force_true_bypasses_nx_and_takes_lease(self, monkeypatch):
        """With force=True the lease write is unconditional (no NX) — the
        manual override path must be able to claim from a held primary."""
        bot = _make_bot(is_primary=False)
        cog = FailoverCog(bot)
        _stub_do_promote_prereqs(monkeypatch, cog)

        # Record every SET call so we can assert NX was NOT used.
        set_calls = []

        async def _record(*args):
            if str(args[0]).upper() == "SET":
                set_calls.append(tuple(str(a).upper() for a in args[1:]))
                return "OK"
            return None

        monkeypatch.setattr(cog, "_exec", AsyncMock(side_effect=_record))
        monkeypatch.setattr(failover_module, "ALL_EXTENSIONS", ["cog_a"])
        bot.load_extension = AsyncMock()

        await cog._do_promote(force=True)

        assert bot.state.is_primary is True
        # At least one SET on LEASE_KEY happened, and the first one (the claim)
        # did NOT include NX as an argument.
        lease_sets = [
            args for args in set_calls if any(a == failover_module.LEASE_KEY.upper() for a in args)
        ]
        assert lease_sets, "force=True must still write the lease"
        assert "NX" not in lease_sets[0], f"force=True must NOT use NX; got {lease_sets[0]}"

    @pytest.mark.asyncio
    async def test_concurrent_promote_only_one_winner(self, monkeypatch):
        """Two cogs sharing one shared 'redis' (a dict) — only one of them
        must end up primary. This is the actual split-brain scenario the
        SET NX prevents."""
        bot_a = _make_bot(is_primary=False)
        bot_b = _make_bot(is_primary=False)
        cog_a = FailoverCog(bot_a)
        cog_b = FailoverCog(bot_b)
        # Give the cogs distinct identities — otherwise both look like the
        # same node, NX wouldn't distinguish them anyway.
        cog_a._process_uuid = "aaa"
        cog_b._process_uuid = "bbb"
        _stub_do_promote_prereqs(monkeypatch, cog_a)
        _stub_do_promote_prereqs(monkeypatch, cog_b)

        # Shared in-memory "redis" — only the first SET NX wins.
        shared: dict[str, str] = {}

        def _make_exec(cog):
            async def _exec(*args):
                cmd = str(args[0]).upper()
                if cmd == "SET":
                    key = str(args[1])
                    val = str(args[2])
                    use_nx = any(str(a).upper() == "NX" for a in args[3:])
                    if use_nx and key in shared:
                        return None
                    shared[key] = val
                    return "OK"
                if cmd == "GET":
                    return shared.get(str(args[1]))
                return None

            return AsyncMock(side_effect=_exec)

        monkeypatch.setattr(cog_a, "_exec", _make_exec(cog_a))
        monkeypatch.setattr(cog_b, "_exec", _make_exec(cog_b))

        monkeypatch.setattr(failover_module, "ALL_EXTENSIONS", ["cog_x"])
        bot_a.load_extension = AsyncMock()
        bot_b.load_extension = AsyncMock()

        # Race them. asyncio.gather schedules both before either runs to
        # completion — the SET NX inside _do_promote is the contention point.
        await asyncio.gather(cog_a._do_promote(), cog_b._do_promote())

        winners = [b.state.is_primary for b in (bot_a, bot_b)]
        assert winners.count(True) == 1, (
            f"exactly one cog must win the lease; got is_primary={winners}"
        )
        # Only the winner should have loaded cogs.
        assert (bot_a.load_extension.await_count == 1) ^ (bot_b.load_extension.await_count == 1)


class TestConfiguredPrimaryReclaim:
    @pytest.mark.asyncio
    async def test_configured_primary_reclaims_from_standby_with_force(self, monkeypatch):
        """When a configured primary (P:) sees a standby (S:) holding the
        lease, it must reclaim with force=True so the SET NX doesn't fail
        on the already-held key."""
        bot = _make_bot(is_primary=False)
        cog = FailoverCog(bot)
        cog._identity = "P:test-node:abc"

        monkeypatch.setattr(cog, "_exec", _stub_exec({"GET": "S:other-host:xyz"}))
        monkeypatch.setattr(cog, "_promote", AsyncMock())

        await cog._standby_cycle()

        cog._promote.assert_awaited_once_with(force=True)
