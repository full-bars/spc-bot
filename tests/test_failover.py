import pytest
import os
import json
from unittest.mock import AsyncMock, MagicMock, patch
from cogs.failover import FailoverCog
from utils.state import BotState

class MockBot:
    def __init__(self):
        self.state = BotState()
        self.user = AsyncMock()
        self.user.id = 12345

@pytest.mark.asyncio
async def test_rank_2_promotes_on_missing_lock():
    """Verify Rank 2 promotes and hydrates when Upstash result is None."""
    with patch.dict(os.environ, {
        "FAILOVER_RANK": "2",
        "UPSTASH_REDIS_REST_URL": "https://mock.upstash.io",
        "UPSTASH_REDIS_REST_TOKEN": "mock-token"
    }):
        bot = MockBot()
        cog = FailoverCog(bot)
        
        # Mock responses for lock check and state hydration
        mock_lock_resp = AsyncMock()
        mock_lock_resp.json.return_value = {"result": None}
        mock_lock_resp.status = 200
        
        mock_state_resp = AsyncMock()
        mock_state_resp.json.return_value = {"result": json.dumps({"posted_mds": ["MD1"], "posted_watches": {}})}
        mock_state_resp.status = 200

        # Handle 'async with session.get() as resp' lifecycle
        mock_get_ctx = MagicMock()
        mock_get_ctx.__aenter__ = AsyncMock()
        mock_get_ctx.__aenter__.side_effect = [mock_lock_resp, mock_state_resp]
        mock_get_ctx.__aexit__ = AsyncMock()

        with patch("aiohttp.ClientSession.get", return_value=mock_get_ctx):
            await cog.heartbeat_loop()
            
        assert bot.state.is_primary is True
        assert "MD1" in bot.state.posted_mds
