"""
Integration tests for BotState, cog initialization, and critical code paths.
These tests actually execute function bodies to catch NameErrors, missing
attributes, and broken call signatures that unit tests miss.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from utils.state import BotState


def make_mock_bot():
    """Create a mock bot with bot.state properly initialized."""
    bot = MagicMock()
    bot.state = BotState()
    bot.latency = 0.05
    bot.guilds = []
    bot.wait_until_ready = AsyncMock()
    bot.cogs = {}
    return bot


def make_mock_interaction(bot):
    """Create a mock Discord interaction linked to a bot."""
    interaction = MagicMock()
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.client = bot
    return interaction


# ── BotState ─────────────────────────────────────────────────────────────────

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


# ── Watches cog ───────────────────────────────────────────────────────────────

class TestWatchesCogIntegration:
    async def test_auto_post_watches_no_nameerror_on_new_watch(self):
        """
        Simulate a new watch being detected. Ensures the full auto_post_watches
        code path executes without NameError — catches missing cache references.
        """
        from cogs.watches import WatchesCog
        bot = make_mock_bot()
        bot.state.is_primary = True
        bot.get_channel = MagicMock(return_value=AsyncMock())

        nws_result = {
            "0100": {
                "type": "SVR",
                "expires": datetime(2026, 4, 12, 9, 0, tzinfo=timezone.utc),
                "affected_zones": [],
            }
        }

        with patch("cogs.watches.fetch_active_watches_nws", new=AsyncMock(return_value=nws_result)), \
             patch("cogs.watches.fetch_watch_details", new=AsyncMock(return_value=(None, None, None))), \
             patch("cogs.watches.download_single_image", new=AsyncMock(return_value=(None, False, None))), \
             patch("cogs.watches.add_posted_watch", new=AsyncMock()):

            cog = WatchesCog(bot)
            cog.auto_post_watches.cancel()
            await cog.auto_post_watches()

    async def test_execute_watches_no_nameerror_on_slash_command(self):
        """
        Simulate /watches being invoked. Ensures _execute_watches executes
        without NameError — catches missing cache references in slash path.
        """
        from cogs.watches import _execute_watches
        bot = make_mock_bot()
        interaction = make_mock_interaction(bot)

        nws_result = {
            "0100": {
                "type": "SVR",
                "expires": datetime(2026, 4, 12, 9, 0, tzinfo=timezone.utc),
                "affected_zones": [],
            }
        }

        with patch("cogs.watches.fetch_active_watches_nws", new=AsyncMock(return_value=nws_result)), \
             patch("cogs.watches.fetch_watch_details", new=AsyncMock(return_value=(None, None, None))), \
             patch("cogs.watches.download_single_image", new=AsyncMock(return_value=(None, False, None))), \
             patch("cogs.watches.http_get_bytes", new=AsyncMock(return_value=(None, 404))):

            await _execute_watches(interaction, bot)

    async def test_execute_watches_no_active_watches(self):
        """
        When NWS API and SPC scrape both return empty, /watches should
        send 'No active watches found.' without raising.
        """
        from cogs.watches import _execute_watches
        bot = make_mock_bot()
        interaction = make_mock_interaction(bot)

        with patch("cogs.watches.fetch_active_watches_nws", new=AsyncMock(return_value={})), \
             patch("cogs.watches.fetch_latest_watch_numbers", new=AsyncMock(return_value=[])):

            await _execute_watches(interaction, bot)
            interaction.followup.send.assert_called_once_with("No active watches found.")

    async def test_auto_post_watches_skips_already_posted(self):
        """
        If a watch is already in posted_watches, it should not be posted again.
        """
        from cogs.watches import WatchesCog
        bot = make_mock_bot()
        bot.state.is_primary = True
        bot.get_channel = MagicMock(return_value=AsyncMock())
        bot.state.posted_watches.add("0100")

        nws_result = {
            "0100": {
                "type": "SVR",
                "expires": datetime(2026, 4, 12, 9, 0, tzinfo=timezone.utc),
                "affected_zones": [],
            }
        }

        with patch("cogs.watches.fetch_active_watches_nws", new=AsyncMock(return_value=nws_result)), \
             patch("cogs.watches.fetch_watch_details", new=AsyncMock()) as mock_details:

            cog = WatchesCog(bot)
            cog.auto_post_watches.cancel()
            await cog.auto_post_watches()
            mock_details.assert_not_called()


# ── Outlooks cog ──────────────────────────────────────────────────────────────

class TestOutlooksCogIntegration:
    async def test_check_and_post_day_no_urls_returned(self):
        """
        If get_spc_urls returns empty, check_and_post_day should return
        without posting — no AttributeError on state access.
        """
        from cogs.outlooks import check_and_post_day
        bot = make_mock_bot()
        channel = AsyncMock()

        with patch("cogs.outlooks.get_spc_urls", new=AsyncMock(return_value=[])):
            await check_and_post_day(channel, 1, bot.state)

    async def test_outlooks_cog_instantiates(self):
        from cogs.outlooks import OutlooksCog
        bot = make_mock_bot()
        cog = OutlooksCog(bot)
        assert cog.bot.state is not None
        cog.auto_post_spc.cancel()
        cog.aggressive_check_spc.cancel()
        cog.auto_post_spc48.cancel()


# ── Mesoscale cog ─────────────────────────────────────────────────────────────

class TestMesoscaleCogIntegration:
    async def test_mesoscale_cog_instantiates(self):
        from cogs.mesoscale import MesoscaleCog
        bot = make_mock_bot()
        cog = MesoscaleCog(bot)
        assert cog.bot.state is not None
        cog.auto_post_md.cancel()
