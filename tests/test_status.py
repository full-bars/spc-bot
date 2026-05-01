"""Tests for cogs/status.py — Help and status commands."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
    content = interaction.followup.send.call_args.args[0]
    
    assert f"Version        : v{__version__}" in content
    assert "Node Role      : PRIMARY" in content
    assert "Open Circuits  : NONE" in content


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
        content = interaction.followup.send.call_args.args[0]
        assert "Open Circuits  : test.host" in content
    finally:
        circuit_breaker.record_success("test.host")
