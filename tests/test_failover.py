import pytest
from unittest.mock import AsyncMock, MagicMock
from cogs.failover import Failover

@pytest.mark.asyncio
async def test_sync_loop_binary_push():
    bot = MagicMock()
    bot.state.is_primary = True
    bot.wait_until_ready = AsyncMock()
    cog = Failover(bot)
    cog.update_heartbeat = AsyncMock()
    cog.push_binary_db = AsyncMock()
    await cog.sync_loop()
    cog.push_binary_db.assert_called_once()

@pytest.mark.asyncio
async def test_sync_loop_binary_pull():
    bot = MagicMock()
    bot.state.is_primary = False
    bot.wait_until_ready = AsyncMock()
    cog = Failover(bot)
    cog.pull_binary_db = AsyncMock()
    await cog.sync_loop()
    cog.pull_binary_db.assert_called_once()
