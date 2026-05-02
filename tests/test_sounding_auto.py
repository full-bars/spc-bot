"""
Unit tests for cogs/sounding.py — auto-posting logic.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from cogs.sounding import SoundingCog

@pytest.mark.asyncio
async def test_auto_sounding_watches_skips_standby():
    bot = MagicMock()
    bot.state.is_primary = False
    bot.wait_until_ready = AsyncMock()
    
    # Patch __init__ to avoid starting real loops
    def mock_init(s, b):
        s.bot = b
        s._restore_attempted = False
        
    with patch.object(SoundingCog, "__init__", mock_init):
        cog = SoundingCog(bot)
        await cog.auto_sounding_watches()
        
    # Should return early BEFORE wait_until_ready
    assert not bot.wait_until_ready.called
    assert not bot.get_channel.called

@pytest.mark.asyncio
async def test_auto_sounding_watches_skips_outside_window():
    bot = MagicMock()
    bot.state.is_primary = True
    bot.wait_until_ready = AsyncMock()
    
    with patch.object(SoundingCog, "__init__", lambda s, b: setattr(s, "bot", b)), \
         patch("cogs.sounding.datetime") as mock_dt:
        
        # Mock time to be 15z (outside 00/12z windows)
        mock_now = MagicMock()
        mock_now.hour = 15
        mock_dt.now.return_value = mock_now
        
        cog = SoundingCog(bot)
        # Mock _ensure_restored
        cog._ensure_restored = AsyncMock()
        
        await cog.auto_sounding_watches()
        
    # Should return early after wait_until_ready
    assert bot.wait_until_ready.called
    assert not bot.get_channel.called
