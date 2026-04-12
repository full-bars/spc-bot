"""
Integration tests for BotState and cog initialization.
Tests that cogs can be instantiated and their methods called without runtime errors.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from utils.state import BotState


def make_mock_bot():
    """Create a mock bot with bot.state properly initialized."""
    bot = MagicMock()
    bot.state = BotState()
    bot.latency = 0.05
    bot.guilds = []
    return bot


class TestBotStateInit:
    def test_botstate_has_all_fields(self):
        state = BotState()
        assert hasattr(state, 'auto_cache')
        assert hasattr(state, 'manual_cache')
        assert hasattr(state, 'partial_update_state')
        assert hasattr(state, 'posted_mds')
        assert hasattr(state, 'posted_watches')
        assert hasattr(state, 'active_mds')
        assert hasattr(state, 'active_watches')
        assert hasattr(state, 'last_post_times')
        assert hasattr(state, 'last_posted_urls')

    def test_botstate_defaults(self):
        state = BotState()
        assert isinstance(state.auto_cache, dict)
        assert isinstance(state.manual_cache, dict)
        assert isinstance(state.posted_mds, set)
        assert isinstance(state.posted_watches, set)
        assert isinstance(state.active_watches, dict)
        assert isinstance(state.last_post_times, dict)
        assert 'day1' in state.last_post_times
        assert 'day2' in state.last_post_times
        assert 'day3' in state.last_post_times

    def test_botstate_to_dict(self):
        state = BotState()
        state.posted_mds.add("0001")
        state.posted_watches.add("0042")
        state.auto_cache["http://example.com"] = "abc123"
        d = state.to_dict()
        assert "0001" in d["posted_mds"]
        assert "0042" in d["posted_watches"]
        assert "http://example.com" in d["auto_cache"]


class TestCogInstantiation:
    @pytest.mark.asyncio
    async def test_watches_cog_instantiates(self):
        from cogs.watches import WatchesCog
        bot = make_mock_bot()
        cog = WatchesCog(bot)
        assert cog.bot.state is not None
        assert isinstance(cog.bot.state.active_watches, dict)

    @pytest.mark.asyncio
    async def test_outlooks_cog_instantiates(self):
        from cogs.outlooks import OutlooksCog
        bot = make_mock_bot()
        cog = OutlooksCog(bot)
        assert cog.bot.state is not None

    @pytest.mark.asyncio
    async def test_mesoscale_cog_instantiates(self):
        from cogs.mesoscale import MesoscaleCog
        bot = make_mock_bot()
        cog = MesoscaleCog(bot)
        assert cog.bot.state is not None

    @pytest.mark.asyncio
    async def test_status_cog_instantiates(self):
        from cogs.status import StatusCog
        bot = make_mock_bot()
        cog = StatusCog(bot)
        assert cog.bot.state is not None


class TestCheckAndPostDay:
    @pytest.mark.asyncio
    async def test_check_and_post_day_accepts_state(self):
        """Ensure check_and_post_day accepts state parameter without TypeError."""
        from cogs.outlooks import check_and_post_day
        import inspect
        sig = inspect.signature(check_and_post_day)
        assert 'state' in sig.parameters

    @pytest.mark.asyncio
    async def test_fetch_and_send_weather_images_accepts_state(self):
        """Ensure fetch_and_send_weather_images accepts state parameter."""
        from cogs.status import fetch_and_send_weather_images
        import inspect
        sig = inspect.signature(fetch_and_send_weather_images)
        assert 'state' in sig.parameters
