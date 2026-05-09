"""
Unit tests for NWWSCog.monitor_connection — the reconnect state machine.
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
    c.xmpp_client = None
    c._should_be_connected = True
    return c


@pytest.mark.asyncio
async def test_monitor_skips_when_standby(cog, bot):
    """Does nothing when the node is on standby."""
    bot.state.is_primary = False
    cog.xmpp_client = None

    with patch("cogs.nwws.NWWSClient") as MockClient:
        await cog.monitor_connection()
        MockClient.assert_not_called()


@pytest.mark.asyncio
async def test_monitor_disconnects_client_on_standby(cog, bot):
    """Disconnects an existing XMPP client when demoted to standby."""
    bot.state.is_primary = False
    mock_client = MagicMock()
    mock_client.is_connected = True
    cog.xmpp_client = mock_client

    with patch("cogs.nwws.NWWSClient"):
        await cog.monitor_connection()

    mock_client.disconnect.assert_called_once()


@pytest.mark.asyncio
async def test_monitor_noop_when_already_connected(cog, bot):
    """Returns early without creating a new client if already connected."""
    mock_client = MagicMock()
    mock_client.is_connected = True
    cog.xmpp_client = mock_client

    with patch("cogs.nwws.NWWSClient") as MockClient:
        await cog.monitor_connection()
        MockClient.assert_not_called()


@pytest.mark.asyncio
async def test_monitor_noop_when_connection_in_flight(cog, bot):
    """Returns early if transport is non-None (connection attempt ongoing)."""
    mock_client = MagicMock()
    mock_client.is_connected = False
    mock_client.transport = MagicMock()  # in-flight
    cog.xmpp_client = mock_client

    with patch("cogs.nwws.NWWSClient") as MockClient:
        await cog.monitor_connection()
        MockClient.assert_not_called()


@pytest.mark.asyncio
async def test_monitor_cleans_up_and_reconnects(cog, bot):
    """Disconnects stale client and creates a new one when transport is None."""
    from config import NWWS_SERVER, NWWS_USER, NWWS_PASSWORD
    mock_client = MagicMock()
    mock_client.is_connected = False
    mock_client.transport = None  # stale — not in-flight
    cog.xmpp_client = mock_client

    with patch("cogs.nwws.NWWSClient") as MockClient:
        new_client = MagicMock()
        MockClient.return_value = new_client

        await cog.monitor_connection()

    mock_client.disconnect.assert_called_once()
    MockClient.assert_called_once()
    new_client.connect.assert_called_once()


@pytest.mark.asyncio
async def test_monitor_creates_client_when_none(cog, bot):
    """Creates NWWSClient and calls connect() when no client exists."""
    assert cog.xmpp_client is None

    with patch("cogs.nwws.NWWSClient") as MockClient:
        new_client = MagicMock()
        MockClient.return_value = new_client

        await cog.monitor_connection()

    MockClient.assert_called_once()
    new_client.connect.assert_called_once()
    assert cog.xmpp_client is new_client


@pytest.mark.asyncio
async def test_monitor_clears_client_on_connect_exception(cog, bot):
    """Clears xmpp_client if connect() raises so next cycle retries cleanly."""
    with patch("cogs.nwws.NWWSClient") as MockClient:
        new_client = MagicMock()
        new_client.connect.side_effect = Exception("connection refused")
        MockClient.return_value = new_client

        await cog.monitor_connection()

    assert cog.xmpp_client is None
