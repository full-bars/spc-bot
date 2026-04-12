"""Tests for the failover cog."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from utils.state import BotState


def make_mock_bot(is_primary=True):
    bot = MagicMock()
    bot.state = BotState()
    bot.state.is_primary = is_primary
    return bot


class TestFailoverCog:
    @pytest.mark.asyncio
    async def test_failover_cog_instantiates(self):
        from cogs.failover import FailoverCog
        bot = make_mock_bot()
        cog = FailoverCog(bot)
        assert cog.bot.state.is_primary is True

    @pytest.mark.asyncio
    async def test_standby_instantiates(self):
        from cogs.failover import FailoverCog
        bot = make_mock_bot(is_primary=False)
        cog = FailoverCog(bot)
        assert cog.bot.state.is_primary is False

    @pytest.mark.asyncio
    async def test_hydrate_updates_state(self):
        from cogs.failover import FailoverCog
        bot = make_mock_bot(is_primary=False)
        cog = FailoverCog(bot)
        data = {
            "posted_mds": ["0001", "0002"],
            "posted_watches": ["0042"],
            "auto_cache": {"http://example.com": "abc123"},
            "last_posted_urls": {"day1": ["http://example.com/day1.png"]},
        }
        cog._hydrate(data)
        assert "0001" in cog.bot.state.posted_mds
        assert "0042" in cog.bot.state.posted_watches
        assert "http://example.com" in cog.bot.state.auto_cache

    @pytest.mark.asyncio
    async def test_check_token(self):
        from cogs.failover import FailoverCog
        import cogs.failover as failover_module
        bot = make_mock_bot()
        cog = FailoverCog(bot)
        request = MagicMock()
        with patch.object(failover_module, "FAILOVER_TOKEN", "testtoken"):
            request.headers = {"Authorization": "Bearer testtoken"}
            assert cog._check_token(request) is True
            request.headers = {"Authorization": "Bearer wrongtoken"}
            assert cog._check_token(request) is False
