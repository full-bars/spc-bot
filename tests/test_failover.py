import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from cogs.failover import Failover

@pytest.mark.asyncio
async def test_sync_on_load():
    """Test that the cog starts the sync loop and triggers a push on load for Primary."""
    bot = MagicMock()
    bot.state = MagicMock()
    bot.state.is_primary = True
    
    cog = Failover(bot)
    
    # Mock the push/pull methods
    cog.push_state_to_redis = AsyncMock()
    cog.hydrate_local_state = AsyncMock()
    
    # We mock tasks.Loop.start specifically on the instance created by the decorator
    with patch.object(cog.sync_loop, 'start') as mock_start:
        await cog.cog_load()
        
        # Verify loop started
        mock_start.assert_called_once()
        # Verify primary tried to push
        cog.push_state_to_redis.assert_called_once()

@pytest.mark.asyncio
async def test_hydration_on_load_for_standby():
    """Test that standby rank pulls data on load."""
    bot = MagicMock()
    bot.state = MagicMock()
    bot.state.is_primary = False
    
    cog = Failover(bot)
    cog.push_state_to_redis = AsyncMock()
    cog.hydrate_local_state = AsyncMock()
    
    with patch.object(cog.sync_loop, 'start'):
        await cog.cog_load()
        
        # Verify standby tried to pull (hydrate)
        cog.hydrate_local_state.assert_called_once()
        # Verify standby DID NOT push
        cog.push_state_to_redis.assert_not_called()
