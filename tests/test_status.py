"""Tests for cogs/status.py — Help and status commands."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import discord

from cogs.status import StatusCog
from config import __version__


def _make_interaction():
    interaction = MagicMock()
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.user.id = 12345
    return interaction


def _make_cog():
    bot = MagicMock()
    bot.state = MagicMock()
    bot.state.is_primary = True
    bot.state.bot_start_time = None
    cog = StatusCog(bot)
    return cog


@pytest.mark.asyncio
async def test_help_contains_new_sections():
    """/help should contain the new 'Tornado Tracking & Analytics' section."""
    cog = _make_cog()
    interaction = _make_interaction()

    await cog.help_slash.callback(cog, interaction)

    interaction.response.send_message.assert_called_once()
    embed = interaction.response.send_message.call_args.kwargs["embed"]
    
    # Check for the new section
    found_section = False
    for field in embed.fields:
        if "Watches & Tornadoes" in field.name:
            found_section = True
            assert "/recenttornadoes" in field.value
            assert "/sigtor" in field.value
    
    assert found_section
    assert f"v{__version__}" in embed.footer.text


@pytest.mark.asyncio
async def test_status_output_contains_version():
    """/status output should include the version number."""
    cog = _make_cog()
    interaction = _make_interaction()

    with patch("socket.socket") as mock_socket:
        mock_socket.return_value.getsockname.return_value = ["127.0.0.1"]
        await cog.status_slash.callback(cog, interaction)

    interaction.followup.send.assert_called_once()
    embeds = interaction.followup.send.call_args.kwargs["embeds"]
    main_embed = embeds[0]
    
    assert f"v{__version__}" in main_embed.footer.text
    assert "PRIMARY" in main_embed.description


@pytest.mark.asyncio
async def test_status_output_shows_open_circuits():
    """/status should list open circuits when they exist."""
    cog = _make_cog()
    interaction = _make_interaction()

    from utils.http import circuit_breaker
    circuit_breaker.record_failure("test.host")
    circuit_breaker.record_failure("test.host")
    circuit_breaker.record_failure("test.host")
    circuit_breaker.record_failure("test.host")
    circuit_breaker.record_failure("test.host") # 5 failures = Open
    
    try:
        with patch("socket.socket") as mock_socket:
            mock_socket.return_value.getsockname.return_value = ["127.0.0.1"]
            await cog.status_slash.callback(cog, interaction)

        interaction.followup.send.assert_called_once()
        embeds = interaction.followup.send.call_args.kwargs["embeds"]
        main_embed = embeds[0]
        
        found = False
        for field in main_embed.fields:
            if "Open Circuits" in field.name:
                assert "test.host" in field.value
                found = True
        assert found
    finally:
        circuit_breaker.record_success("test.host")


@pytest.mark.asyncio
async def test_taskmgr_initialization():
    """/taskmgr should initialize with an embed and start auto-update."""
    cog = _make_cog()
    interaction = _make_interaction()
    cog.bot.cogs = {"TestCog": MagicMock()}

    await cog.taskmgr_slash.callback(cog, interaction)

    interaction.followup.send.assert_called_once()
    kwargs = interaction.followup.send.call_args.kwargs
    assert "embed" in kwargs
    assert kwargs["embed"].title == "🖥️ SPCBot Task Manager"
    assert "view" in kwargs
    assert kwargs["view"].should_update is True


@pytest.mark.asyncio
async def test_logs_initialization():
    """/logs should initialize with content from the log handler."""
    cog = _make_cog()
    interaction = _make_interaction()
    mock_handler = MagicMock()
    mock_handler.get_logs.return_value = ["Line 1", "Line 2"]
    cog.bot.log_handler = mock_handler

    await cog.logs_slash.callback(cog, interaction)

    interaction.followup.send.assert_called_once()
    kwargs = interaction.followup.send.call_args.kwargs
    assert "Line 1" in kwargs["content"]
    assert "Line 2" in kwargs["content"]
    assert "view" in kwargs


@pytest.mark.asyncio
async def test_is_owner_check():
    """is_owner check should correctly identify the bot owner."""
    from cogs.status import is_owner
    
    interaction = MagicMock()
    interaction.user.id = 123
    interaction.client.owner_id = 123
    
    assert await is_owner(interaction) is True
    
    interaction.client.owner_id = 456
    interaction.client.application = MagicMock()
    interaction.client.application.owner.id = 123
    assert await is_owner(interaction) is True
    
    interaction.client.application.owner.id = 999
    interaction.client.application.owner = MagicMock(spec=discord.User)
    assert await is_owner(interaction) is False
