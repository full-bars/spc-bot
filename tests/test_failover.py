import pytest
from unittest.mock import AsyncMock, MagicMock
from cogs.failover import Failover

@pytest.mark.asyncio
async def test_sync_loop_binary_push():
    """Test primary logic: heartbeat and binary push."""
    bot = MagicMock()
    bot.state.is_primary = True
    bot.wait_until_ready = AsyncMock()
    
    cog = Failover(bot)
    cog.check_for_promotion = AsyncMock()
    cog.update_heartbeat = AsyncMock()
    cog.push_binary_db = AsyncMock()
    
    await cog.sync_loop()
    
    # Primary shouldn't check for promotion, it's already primary
    cog.update_heartbeat.assert_called_once()
    cog.push_binary_db.assert_called_once()

@pytest.mark.asyncio
async def test_sync_loop_binary_pull():
    """Test standby logic: check promotion and binary pull."""
    bot = MagicMock()
    bot.state.is_primary = False
    bot.wait_until_ready = AsyncMock()
    
    cog = Failover(bot)
    cog.check_for_promotion = AsyncMock()
    cog.pull_binary_db = AsyncMock()
    
    await cog.sync_loop()
    
    cog.check_for_promotion.assert_called_once()
    cog.pull_binary_db.assert_called_once()
