from unittest.mock import MagicMock, patch, AsyncMock
import pytest

from cogs.nwws import NWWSCog


@pytest.mark.asyncio
async def test_trigger_connection_prioritizes_rust():
    """Verify that trigger_connection starts Rust drain and NOT legacy loop when Rust is enabled."""
    bot = MagicMock()
    # Mock bot.state.is_primary as a property
    bot.state.is_primary = True

    with patch("cogs.nwws._USE_RUST_NWWS", True), patch("cogs.nwws.spc_rust_core", create=True):
        cog = NWWSCog(bot)
        cog._use_rust = True

        # Mock the tasks.loop objects
        cog.monitor_connection = MagicMock()
        cog.monitor_connection.is_running.return_value = False
        cog.monitor_connection.start = MagicMock()
        cog.monitor_connection.cancel = MagicMock()

        cog._drain_rust_nwws = MagicMock()
        cog._drain_rust_nwws.is_running.return_value = False
        cog._drain_rust_nwws.start = MagicMock()

        # 1. Trigger connection
        await cog.trigger_connection()

        # Assertions
        assert cog._should_be_connected is True
        cog._drain_rust_nwws.start.assert_called_once()
        cog.monitor_connection.start.assert_not_called()
        # await cog.monitor_connection() should NOT have been called
        # (It's hard to mock the call itself since it's a decorator-wrapped method,
        # but we can check if the logic bypassed it)


@pytest.mark.asyncio
async def test_trigger_connection_cancels_legacy_if_running():
    """Verify that trigger_connection cancels legacy loop if Rust is enabled."""
    bot = MagicMock()
    bot.state.is_primary = True

    with patch("cogs.nwws._USE_RUST_NWWS", True), patch("cogs.nwws.spc_rust_core", create=True):
        cog = NWWSCog(bot)
        cog._use_rust = True

        # Mock the tasks.loop objects
        cog.monitor_connection = MagicMock()
        cog.monitor_connection.is_running.return_value = True  # Simulate legacy loop running
        cog.monitor_connection.cancel = MagicMock()

        cog._drain_rust_nwws = MagicMock()
        cog._drain_rust_nwws.is_running.return_value = True

        # Trigger connection
        await cog.trigger_connection()

        # Assertions
        cog.monitor_connection.cancel.assert_called_once()


@pytest.mark.asyncio
async def test_trigger_connection_falls_back_to_legacy():
    """Verify that trigger_connection starts legacy loop when Rust is disabled."""
    bot = MagicMock()
    bot.state.is_primary = True

    # Force Rust disabled
    with patch("cogs.nwws._USE_RUST_NWWS", False):
        cog = NWWSCog(bot)
        cog._use_rust = False

        # Mock the tasks.loop objects
        # monitor_connection is a tasks.loop, which is also a callable that can be awaited
        cog.monitor_connection = AsyncMock()
        cog.monitor_connection.is_running = MagicMock(return_value=False)
        cog.monitor_connection.start = MagicMock()

        cog._drain_rust_nwws = MagicMock()
        cog._drain_rust_nwws.is_running.return_value = False
        cog._drain_rust_nwws.start = MagicMock()

        # Trigger connection
        await cog.trigger_connection()

        # Assertions
        cog.monitor_connection.start.assert_called_once()
        cog._drain_rust_nwws.start.assert_not_called()
        # Verify it actually awaited the connection attempt
        cog.monitor_connection.assert_called_once()
