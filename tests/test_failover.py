import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from cogs.failover import Failover

@pytest.mark.asyncio
async def test_sync_loop_primary_pushes():
    """Test that the sync loop triggers heartbeat and push if the bot is primary."""
    bot = MagicMock()
    bot.state = MagicMock()
    bot.state.is_primary = True
    bot.wait_until_ready = AsyncMock()
    
    cog = Failover(bot)
    cog.update_heartbeat = AsyncMock()
    cog.perform_push = AsyncMock()
    cog.perform_hydration = AsyncMock()
    
    # Manually trigger the loop
    await cog.sync_loop()
    
    cog.update_heartbeat.assert_called_once()
    cog.perform_push.assert_called_once()
    cog.perform_hydration.assert_not_called()

@pytest.mark.asyncio
async def test_sync_loop_standby_hydrates():
    """Test that the sync loop triggers hydration if the bot is standby."""
    bot = MagicMock()
    bot.state = MagicMock()
    bot.state.is_primary = False
    bot.wait_until_ready = AsyncMock()
    
    cog = Failover(bot)
    cog.update_heartbeat = AsyncMock()
    cog.perform_push = AsyncMock()
    cog.perform_hydration = AsyncMock()
    
    await cog.sync_loop()
    
    cog.perform_hydration.assert_called_once()
    cog.update_heartbeat.assert_not_called()
    cog.perform_push.assert_not_called()
