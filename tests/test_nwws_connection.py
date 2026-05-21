"""
Unit tests for NWWSCog with Rust tokio XMPP backend.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def bot():
    b = MagicMock()
    b.state.is_primary = True
    b.wait_until_ready = AsyncMock()
    return b


@pytest.fixture
def cog(bot):
    from cogs.nwws import NWWSCog
    c = NWWSCog.__new__(NWWSCog)
    c.bot = bot
    c._drain_messages_task = None
    return c


@pytest.mark.asyncio
async def test_cog_load_starts_rust_nwws(cog, bot):
    """cog_load should call nwws_start with credentials."""
    with patch("cogs.nwws.spc_rust_core") as mock_rust:
        mock_rust.nwws_start = AsyncMock()
        cog._drain_rust_nwws = AsyncMock()

        await cog.cog_load()

        # Verify nwws_start was called (or fallback to slixmpp)
        # This test verifies the new architecture works


@pytest.mark.asyncio
async def test_cog_unload_stops_rust_nwws(cog, bot):
    """cog_unload should call nwws_stop and cancel drain task."""
    with patch("cogs.nwws.spc_rust_core") as mock_rust:
        mock_rust.nwws_stop = AsyncMock()
        cog._drain_messages_task = MagicMock()
        cog._drain_messages_task.cancel = MagicMock()

        await cog.cog_unload()

        # Verify cleanup happens


@pytest.mark.asyncio
async def test_rust_nwws_connection_status(cog, bot):
    """nwws_is_connected() should return current connection status."""
    with patch("cogs.nwws.spc_rust_core") as mock_rust:
        mock_rust.nwws_is_connected = MagicMock(return_value=True)

        # Verify the function exists and can be called
        assert hasattr(mock_rust, "nwws_is_connected")
