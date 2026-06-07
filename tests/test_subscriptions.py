import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import discord
from cogs.subscriptions import SubscriptionsCog


@pytest.fixture
def bot():
    mock_bot = MagicMock(spec=discord.ext.commands.Bot)
    return mock_bot


@pytest.fixture
def cog(bot):
    return SubscriptionsCog(bot)


@pytest.mark.asyncio
async def test_subscribe_state_success(cog):
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.user.id = 12345
    interaction.response = AsyncMock()

    with patch("cogs.subscriptions.add_user_subscription", AsyncMock()) as mock_add:
        await cog.subscribe_state.callback(cog, interaction, "OK")
        mock_add.assert_called_once_with(12345, "state", "OK")
        interaction.response.send_message.assert_called_once()
        assert "OK" in interaction.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_subscribe_state_invalid(cog):
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.response = AsyncMock()

    await cog.subscribe_state.callback(cog, interaction, "ZZ")
    interaction.response.send_message.assert_called_once()
    assert "Invalid" in interaction.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_geocode_success(cog):
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value=[{"lat": "35.22", "lon": "-97.44"}])

    mock_session = MagicMock()
    mock_session.get.return_value.__aenter__.return_value = mock_response

    with patch("cogs.subscriptions.ensure_session", AsyncMock(return_value=mock_session)):
        lat, lon = await cog._geocode("Norman, OK")
        assert lat == 35.22
        assert lon == -97.44


@pytest.mark.asyncio
async def test_geocode_fail(cog):
    mock_response = AsyncMock()
    mock_response.status = 404

    mock_session = MagicMock()
    mock_session.get.return_value.__aenter__.return_value = mock_response

    with patch("cogs.subscriptions.ensure_session", AsyncMock(return_value=mock_session)):
        lat, lon = await cog._geocode("Nonexistent City")
        assert lat is None
        assert lon is None
