import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from cogs.failover import Failover

@pytest.mark.asyncio
async def test_sync_on_load():
    """Test that the cog starts the sync loop and triggers a push for Primary."""
    bot = MagicMock()
    bot.state = MagicMock()
    bot.state.is_primary = True
    bot.wait_until_ready = AsyncMock()
    
    cog = Failover(bot)
    cog.push_state_to_redis = AsyncMock()
    cog.hydrate_local_state = AsyncMock()
    
    with patch.object(cog.sync_loop, 'start') as mock_start:
        # We test the logic inside initialize_sync directly to avoid task timing issues
        await cog.initialize_sync()
        
        bot.wait_until_ready.assert_called_once()
        mock_start.assert_called_once()
        cog.push_state_to_redis.assert_called_once()

@pytest.mark.asyncio
async def test_hydration_on_load_for_standby():
    """Test that standby rank pulls data on load."""
    bot = MagicMock()
    bot.state = MagicMock()
    bot.state.is_primary = False
    bot.wait_until_ready = AsyncMock()
    
    cog = Failover(bot)
    cog.push_state_to_redis = AsyncMock()
    cog.hydrate_local_state = AsyncMock()
    
    with patch.object(cog.sync_loop, 'start'):
        await cog.initialize_sync()
        
        cog.hydrate_local_state.assert_called_once()
        cog.push_state_to_redis.assert_not_called()
