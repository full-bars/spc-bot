"""
Integration tests for critical multi-module code paths.
These tests execute full function bodies to catch NameErrors, missing
attributes, and broken call signatures that unit tests miss.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
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


# ── Watches cog Integration ───────────────────────────────────────────────────

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


# ── Outlooks cog Integration ──────────────────────────────────────────────────

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
