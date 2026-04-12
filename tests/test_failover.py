import pytest
from unittest.mock import AsyncMock, MagicMock
from cogs.failover import Failover

@pytest.mark.asyncio
async def test_sync_loop_binary_push():
    """Test primary logic (Rank 1): heartbeat and binary push."""
    bot = MagicMock()
    bot.state.is_primary = True
    bot.state.rank = 1  # Portland
    bot.wait_until_ready = AsyncMock()
    
    cog = Failover(bot)
    cog.reconcile_rank = AsyncMock()
    cog.update_heartbeat = AsyncMock()
    cog.push_binary_db = AsyncMock()
    
    await cog.sync_loop()
    
    # Rank 1 doesn't call reconcile_rank
    cog.update_heartbeat.assert_called_once()
    cog.push_binary_db.assert_called_once()

@pytest.mark.asyncio
async def test_sync_loop_binary_pull():
    """Test standby logic (Rank 2): reconcile and binary pull."""
    bot = MagicMock()
    bot.state.is_primary = False
    bot.state.rank = 2  # Phoenix
    bot.wait_until_ready = AsyncMock()
    
    cog = Failover(bot)
    cog.reconcile_rank = AsyncMock()
    cog.pull_binary_db = AsyncMock()
    
    await cog.sync_loop()
    
    cog.reconcile_rank.assert_called_once()
    cog.pull_binary_db.assert_called_once()
