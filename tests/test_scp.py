"""
Unit tests for cogs/scp.py — daily hazard map posting.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timedelta
from config import PACIFIC
from cogs.scp import SCPCog

@pytest.mark.asyncio
async def test_auto_post_scp_triggers_at_time():
    bot = MagicMock()
    bot.state.is_primary = True
    bot.wait_until_ready = AsyncMock()
    
    # Patch __init__ to avoid starting the real loop
    with patch.object(SCPCog, "__init__", lambda s, b: setattr(s, "bot", b)):
        cog = SCPCog(bot)
        cog._next_post_time = None
    
    # Mock current time to be exactly the next post time
    now = datetime.now(PACIFIC)
    # Set _next_post_time to 1 second ago so it triggers
    cog._next_post_time = now - timedelta(seconds=1)
    
    mock_channel = AsyncMock()
    bot.get_channel.return_value = mock_channel
    
    with patch("cogs.scp.download_images_parallel", AsyncMock(return_value=["test.png"])), \
         patch("discord.File", MagicMock()):
        await cog.auto_post_scp()
        
    mock_channel.send.assert_called_once()
    # Check that it scheduled the next post in the future
    assert cog._next_post_time > now

@pytest.mark.asyncio
async def test_auto_post_scp_skips_early():
    bot = MagicMock()
    bot.state.is_primary = True
    bot.wait_until_ready = AsyncMock()
    
    with patch.object(SCPCog, "__init__", lambda s, b: setattr(s, "bot", b)):
        cog = SCPCog(bot)
    
    now = datetime.now(PACIFIC)
    # Set _next_post_time to 1 hour in the future
    cog._next_post_time = now + timedelta(hours=1)
    
    mock_channel = AsyncMock()
    bot.get_channel.return_value = mock_channel
    
    await cog.auto_post_scp()
    
    mock_channel.send.assert_not_called()
