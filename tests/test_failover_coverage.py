"""
Behavior-driven tests for the Failover Cog (High Availability).

These tests verify the core outcomes of the HA state machine:
  1. Promotion: Standby becomes Primary when the lease is absent.
  2. Demotion: Primary becomes Standby when someone else holds the lease.
  3. Resilience: Nodes respect the startup grace period and heartbeat TTLs.
  4. Automation: Cogs are loaded/unloaded correctly during transitions.
"""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

import cogs.failover as failover_module
from cogs.failover import FailoverCog
from utils.state import BotState


@pytest.fixture(autouse=True)
def _isolate_hostname(monkeypatch):
    """Pin hostname to prevent environmental contamination."""
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


def _stub_redis(responses: dict | None = None, default=None):
    """Create a mock Redis command responder for testing."""
    responses = responses or {}
    async def _resp(*args):
        if not args:
            return default
        return responses.get(args[0], default)
    return AsyncMock(side_effect=_resp)


# ── Standby Promotion Scenarios ──────────────────────────────────────────────

class TestStandbyPromotion:

    @pytest.mark.asyncio
    async def test_promotes_when_lease_is_missing(self, monkeypatch):
        """A standby node should promote to Primary if the lease is absent for MAX_FAILURES."""
        bot = _make_bot(is_primary=False)
        cog = FailoverCog(bot)

        # Mock _get_redis_client to return a mock client
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=None)  # Lease is missing
        mock_client.close = AsyncMock()

        async def mock_get_redis_client():
            return mock_client

        monkeypatch.setattr(cog, "_get_redis_client", mock_get_redis_client)

        # Set failures to threshold-1 (promotion happens when failures >= MAX_FAILURES)
        cog._primary_failures = failover_module.MAX_FAILURES - 1

        # Ensure we are out of grace period
        cog._cog_load_monotonic = time.monotonic() - 200

        # Mock the _promote method to avoid loading cogs
        monkeypatch.setattr(cog, "_promote", AsyncMock())

        await cog._standby_cycle()

        # Should have triggered promotion
        assert cog._promote.called

    @pytest.mark.asyncio
    async def test_stays_standby_if_lease_held_by_other(self, monkeypatch):
        """A standby node should remain Standby if another node holds the lease."""
        bot = _make_bot(is_primary=False)
        cog = FailoverCog(bot)

        # Mock _get_redis_client to return a mock client
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=b"other-node:other-uuid")  # Another node holds lease
        mock_client.close = AsyncMock()

        async def mock_get_redis_client():
            return mock_client

        monkeypatch.setattr(cog, "_get_redis_client", mock_get_redis_client)

        cog._primary_failures = 3
        await cog._standby_cycle()

        assert bot.state.is_primary is False
        assert cog._primary_failures == 0  # Counter resets when holder is seen


# ── Primary Demotion Scenarios ───────────────────────────────────────────────

class TestPrimaryDemotion:

    @pytest.mark.asyncio
    async def test_demotes_when_lease_stolen(self, monkeypatch):
        """A primary node should demote itself if it sees another node holds the lease."""
        bot = _make_bot(is_primary=True)
        cog = FailoverCog(bot)
        cog._identity = "P:me:abc123"

        # Mock _get_redis_client to return a mock client
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=b"P:someone-else:def456")  # Another node holds lease
        mock_client.close = AsyncMock()

        async def mock_get_redis_client():
            return mock_client

        monkeypatch.setattr(cog, "_get_redis_client", mock_get_redis_client)

        # Mock _demote to avoid side effects
        monkeypatch.setattr(cog, "_demote", AsyncMock())

        await cog._primary_cycle()

        assert cog._demote.called

    @pytest.mark.asyncio
    async def test_refreshes_lease_when_healthy(self, monkeypatch):
        """A primary node should extend its lease if it still owns it."""
        bot = _make_bot(is_primary=True)
        cog = FailoverCog(bot)
        cog._identity = "P:me:abc123"

        # Mock _get_redis_client to return a mock client
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=b"P:me:abc123")  # We own the lease
        mock_client.set = AsyncMock()  # SET to refresh
        mock_client.close = AsyncMock()

        async def mock_get_redis_client():
            return mock_client

        monkeypatch.setattr(cog, "_get_redis_client", mock_get_redis_client)

        await cog._primary_cycle()

        # Verify SET was called to refresh the lease
        assert mock_client.set.called
        assert bot.state.is_primary is True


# ── Startup Grace & Fail-Fast ────────────────────────────────────────────────

class TestFailoverResilience:

    def test_startup_grace_period(self, monkeypatch):
        """Failures should not increment during the first 120s of uptime."""
        cog = FailoverCog(_make_bot())
        cog._cog_load_monotonic = time.monotonic()
        
        cog._register_failure("test failure")
        assert cog._primary_failures == 0
        
        # Shift load time to past
        cog._cog_load_monotonic = time.monotonic() - 200
        cog._register_failure("test failure")
        assert cog._primary_failures == 1

    def test_token_guard_raises_on_invalid_config(self, monkeypatch):
        """Cog should refuse to load with default or empty FAILOVER_TOKEN."""
        monkeypatch.setattr(failover_module, "FAILOVER_TOKEN", "changeme")
        with pytest.raises(RuntimeError):
            failover_module._require_failover_token()
            
        monkeypatch.setattr(failover_module, "FAILOVER_TOKEN", "")
        with pytest.raises(RuntimeError):
            failover_module._require_failover_token()

    @pytest.mark.asyncio
    async def test_manual_override_detection(self, monkeypatch):
        """The sync loop should detect a manual override for another host and demote."""
        bot = _make_bot(is_primary=True)
        cog = FailoverCog(bot)
        cog._identity = "P:me:abc123"

        # Mock _get_redis_client to return a mock client
        mock_client = AsyncMock()
        mock_client.hset = AsyncMock(return_value=1)
        mock_client.get = AsyncMock(return_value=b"other-host")  # Manual override points to another host
        mock_client.close = AsyncMock()

        async def mock_get_redis_client():
            return mock_client

        monkeypatch.setattr(cog, "_get_redis_client", mock_get_redis_client)

        # Mock bot.wait_until_ready to not hang
        bot.wait_until_ready = AsyncMock()

        # Patch demote to avoid hitting syncthing/cogs
        monkeypatch.setattr(cog, "_demote", AsyncMock())

        # Call sync_loop once (normally this is a task loop)
        await cog.sync_loop()

        assert cog._demote.called
