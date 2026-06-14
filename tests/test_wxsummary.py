import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import discord

from cogs.wxsummary import _strip_html, _build_embed, WxSummaryCog, setup


def test_strip_html():
    assert _strip_html("<p>Hello</p> <b>world</b>") == "Hello world"
    assert _strip_html("No html here") == "No html here"
    assert _strip_html("   <br> Trimmed   ") == "Trimmed"
    assert _strip_html("HTML &amp; entities") == "HTML & entities"


def test_build_embed_full_data():
    data = {
        "text": "Storms expected today.",
        "signals": {
            "spc_day1_label": "Moderate",
            "wai": 7.5,
            "top_state_name": "Oklahoma",
            "chasers": 42,
            "live": 5,
        },
        "tags": ["tornado", "wind"],
        "tone": "active",
    }
    embed = _build_embed(data)
    assert embed.title == "Weather Briefing"
    assert embed.description == "Storms expected today."
    assert embed.color == discord.Color.red()

    fields = {f.name: f.value for f in embed.fields}
    assert fields["SPC Day 1 Risk"] == "Moderate"
    assert fields["WAI"] == "7.5"
    assert fields["Top State"] == "Oklahoma"
    assert fields["Field Activity"] == "42 chasers · 5 live"
    assert fields["Tags"] == "tornado · wind"


def test_build_embed_missing_data():
    data = {"html": "<p>Fallback html</p>"}
    embed = _build_embed(data)
    assert embed.description == "Fallback html"
    assert embed.color == discord.Color.blurple()

    fields = {f.name: f.value for f in embed.fields}
    assert fields["SPC Day 1 Risk"] == "—"
    assert fields["Top State"] == "—"
    assert "WAI" not in fields
    assert "Field Activity" not in fields
    assert "Tags" not in fields


@pytest.mark.asyncio
async def test_wxsummary_command_success():
    bot = MagicMock()
    cog = WxSummaryCog(bot)

    interaction = MagicMock(spec=discord.Interaction)
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    mock_data = {"text": "Briefing text"}

    with patch("cogs.wxsummary._http.http_get_json", AsyncMock(return_value=mock_data)):
        await cog.wxsummary.callback(cog, interaction)

    interaction.response.defer.assert_called_once()
    interaction.followup.send.assert_called_once()

    # Check that an embed was sent
    kwargs = interaction.followup.send.call_args[1]
    assert "embed" in kwargs
    assert kwargs["embed"].description == "Briefing text"


@pytest.mark.asyncio
async def test_wxsummary_command_failure():
    bot = MagicMock()
    cog = WxSummaryCog(bot)

    interaction = MagicMock(spec=discord.Interaction)
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    with patch("cogs.wxsummary._http.http_get_json", AsyncMock(return_value=None)):
        await cog.wxsummary.callback(cog, interaction)

    interaction.response.defer.assert_called_once()
    interaction.followup.send.assert_called_once()

    args = interaction.followup.send.call_args[0]
    assert "Could not fetch" in args[0]


@pytest.mark.asyncio
async def test_setup():
    bot = MagicMock()
    bot.add_cog = AsyncMock()
    await setup(bot)
    bot.add_cog.assert_called_once()
