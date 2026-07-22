import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import discord

from cogs.warning_channels import (
    ChannelsCog,
    setup,
)


@pytest.mark.asyncio
async def test_assign_sets_channel():
    cog = ChannelsCog()
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response.send_message = AsyncMock()

    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 12345
    channel.mention = "<#12345>"

    with patch("cogs.warning_channels.set_state", AsyncMock()) as mock_set_state:
        await cog.assign.callback(cog, interaction, "svr", channel)

    mock_set_state.assert_called_once_with("warning_channel:svr", "12345")
    interaction.response.send_message.assert_called_once()
    kwargs = interaction.response.send_message.call_args[1]
    assert "<#12345>" in interaction.response.send_message.call_args[0][0]
    assert kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_assign_without_channel_disables():
    cog = ChannelsCog()
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response.send_message = AsyncMock()

    with patch("cogs.warning_channels.set_state", AsyncMock()) as mock_set_state:
        await cog.assign.callback(cog, interaction, "sps", None)

    mock_set_state.assert_called_once_with("warning_channel:sps", "disabled")
    interaction.response.send_message.assert_called_once()
    assert "no longer be posted" in interaction.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_list_channels():
    cog = ChannelsCog()
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    # tor=disabled, svr=channel_id, ffw=default (None)
    async def mock_get_state(key):
        if key == "warning_channel:tor":
            return "disabled"
        if key == "warning_channel:svr":
            return "54321"
        return None

    with patch("cogs.warning_channels.get_state", AsyncMock(side_effect=mock_get_state)):
        await cog.list_channels.callback(cog, interaction)

    interaction.response.defer.assert_called_once()
    interaction.followup.send.assert_called_once()

    embed = interaction.followup.send.call_args[1]["embed"]
    assert "Tornado Warning**: 🔕 Disabled" in embed.description
    assert "Severe Thunderstorm Warning**: <#54321>" in embed.description
    assert "Flash Flood Warning**: <#" in embed.description
    assert "*(default)*" in embed.description


@pytest.mark.asyncio
async def test_setup():
    bot = MagicMock()
    bot.add_cog = AsyncMock()
    await setup(bot)
    bot.add_cog.assert_called_once()
