import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import discord

from cogs.warning_channels import (
    DisableWarningsView,
    WarningChannelsCog,
    setup,
)


@pytest.mark.asyncio
async def test_disable_warnings_view():
    view = DisableWarningsView([("tor", "Tornado Warning"), ("svr", "Severe Thunderstorm Warning")])
    assert len(view.select.options) == 2

    interaction = MagicMock(spec=discord.Interaction)
    interaction.response.edit_message = AsyncMock()

    view.select._values = ["tor"]
    with patch("cogs.warning_channels.set_state", AsyncMock()) as mock_set_state:
        await view.on_select(interaction)

    mock_set_state.assert_called_once_with("warning_channel:tor", "disabled")
    interaction.response.edit_message.assert_called_once()
    assert "Tornado Warning" in interaction.response.edit_message.call_args[1]["content"]


@pytest.mark.asyncio
async def test_enable_warnings():
    cog = WarningChannelsCog()
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response.send_message = AsyncMock()

    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 12345
    channel.mention = "<#12345>"

    with patch("cogs.warning_channels.set_state", AsyncMock()) as mock_set_state:
        await cog.enable_warnings.callback(cog, interaction, "svr", channel)

    mock_set_state.assert_called_once_with("warning_channel:svr", "12345")
    interaction.response.send_message.assert_called_once()
    kwargs = interaction.response.send_message.call_args[1]
    assert "<#12345>" in interaction.response.send_message.call_args[0][0]
    assert kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_display_setup():
    cog = WarningChannelsCog()
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
        await cog.display_setup.callback(cog, interaction)

    interaction.response.defer.assert_called_once()
    interaction.followup.send.assert_called_once()

    embed = interaction.followup.send.call_args[1]["embed"]
    assert "Tornado Warning**: 🔕 Disabled" in embed.description
    assert "Severe Thunderstorm Warning**: <#54321>" in embed.description
    assert "Flash Flood Warning**: <#" in embed.description
    assert "*(default)*" in embed.description


@pytest.mark.asyncio
async def test_disable_warnings_command():
    cog = WarningChannelsCog()
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    # all enabled
    with patch("cogs.warning_channels.get_state", AsyncMock(return_value=None)):
        await cog.disable_warnings.callback(cog, interaction)

    interaction.followup.send.assert_called_once()
    kwargs = interaction.followup.send.call_args[1]
    assert "view" in kwargs
    assert isinstance(kwargs["view"], DisableWarningsView)
    assert len(kwargs["view"].select.options) == 4


@pytest.mark.asyncio
async def test_disable_warnings_command_all_disabled():
    cog = WarningChannelsCog()
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    # all disabled
    with patch("cogs.warning_channels.get_state", AsyncMock(return_value="disabled")):
        await cog.disable_warnings.callback(cog, interaction)

    interaction.followup.send.assert_called_once()
    assert "already disabled" in interaction.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_setup():
    bot = MagicMock()
    bot.add_cog = AsyncMock()
    await setup(bot)
    bot.add_cog.assert_called_once()
