import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

import discord

from cogs.sounding import SoundingCog


@pytest.fixture
def bot_mock():
    bot = MagicMock()
    bot.state = MagicMock()
    bot.state.posted_soundings = set()
    bot.state.sounding_handled_watches = set()
    bot.state.active_watches = {}
    return bot


@pytest.fixture
def cog(bot_mock):
    cog = SoundingCog(bot_mock)
    return cog


@pytest.mark.asyncio
async def test_ensure_restored_idempotency(cog):
    with patch.object(cog, "cog_load", new=AsyncMock()) as mock_load:
        await cog._ensure_restored()
        assert mock_load.call_count == 1
        await cog._ensure_restored()
        assert mock_load.call_count == 1  # Called only once


@pytest.mark.asyncio
async def test_format_watches_caption(cog):
    # Empty
    assert "Near active Watch #1234" in cog._format_watches_caption([], "1234", "Watch")

    # One watch
    applicable = [("001", "Tornado", 50.0)]
    res = cog._format_watches_caption(applicable, "0000", "Watch")
    assert "Near active Tornado Watch #001" in res

    # Multiple
    applicable = [("001", "Tornado", 50.0), ("002", "SVR", 60.0)]
    res = cog._format_watches_caption(applicable, "0000", "Watch")
    assert "Near active watches #001 (Tornado), #002 (SVR)" in res


@pytest.mark.asyncio
async def test_mark_sounding_posted(cog):
    cog.bot.state.add_posted_sounding = AsyncMock()
    await cog._mark_sounding_posted("test_key")
    cog.bot.state.add_posted_sounding.assert_called_once_with("test_key")


@pytest.mark.asyncio
async def test_mark_watch_handled(cog):
    cog.bot.state.add_sounding_handled_watch = AsyncMock()
    await cog._mark_watch_handled("1234")
    cog.bot.state.add_sounding_handled_watch.assert_called_once_with("1234")
