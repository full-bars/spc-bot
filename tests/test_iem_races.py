# tests/test_iem_races.py
"""
Tests for IEM/SPC race logic in fetch_watch_details, fetch_md_details,
and the watch-triggered sounding auto-post.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── fetch_watch_details ───────────────────────────────────────────────────────

class TestFetchWatchDetailsRace:

    @pytest.mark.asyncio
    async def test_returns_tuple_of_three(self):
        """fetch_watch_details always returns a 3-tuple regardless of outcome."""
        with patch("cogs.watches.http_get_text", new_callable=AsyncMock) as mt, \
             patch("cogs.watches.http_get_bytes", new_callable=AsyncMock) as mb, \
             patch("cogs.watches.fetch_watch_details_iem", new_callable=AsyncMock) as mi:
            mt.return_value = None
            mb.return_value = (None, 404)
            mi.return_value = (None, None, None)
            from cogs.watches import fetch_watch_details
            result = await fetch_watch_details("0102")
        assert isinstance(result, tuple)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_iem_text_used_when_spc_fails(self):
        """When SPC page fails, IEM text summary is surfaced in the result."""
        with patch("cogs.watches.http_get_text", new_callable=AsyncMock) as mt, \
             patch("cogs.watches.http_get_bytes", new_callable=AsyncMock) as mb, \
             patch("cogs.watches.fetch_watch_details_iem", new_callable=AsyncMock) as mi:
            mt.return_value = None
            mb.return_value = (None, 404)
            mi.return_value = ("IEM summary", "http://iem.example/img.png", None)
            from cogs.watches import fetch_watch_details
            image_url, text_summary, probs = await fetch_watch_details("0102")
        assert text_summary == "IEM summary"

    @pytest.mark.asyncio
    async def test_both_fail_no_crash(self):
        """When both SPC and IEM fail, function returns without raising."""
        with patch("cogs.watches.http_get_text", new_callable=AsyncMock) as mt, \
             patch("cogs.watches.http_get_bytes", new_callable=AsyncMock) as mb, \
             patch("cogs.watches.fetch_watch_details_iem", new_callable=AsyncMock) as mi:
            mt.return_value = None
            mb.return_value = (None, 404)
            mi.return_value = (None, None, None)
            from cogs.watches import fetch_watch_details
            result = await fetch_watch_details("0102")
        assert result == (None, None, None)


# ── fetch_md_details race ────────────────────────────────────────────────────

class TestFetchMdDetailsRace:

    @pytest.mark.asyncio
    async def test_spc_wins_returns_image(self):
        """fetch_md_details returns image URL from SPC when available."""
        fake_html = '<img src="mcd0398.png">'
        with patch("cogs.mesoscale.http_get_text", new_callable=AsyncMock) as mt, \
             patch("cogs.mesoscale.fetch_md_details_iem", new_callable=AsyncMock) as mi:
            mt.return_value = fake_html
            mi.return_value = (None, None)
            from cogs.mesoscale import fetch_md_details
            image_url, summary, from_cache = await fetch_md_details("0398")
        assert "mcd0398" in image_url
        assert from_cache is False

    @pytest.mark.asyncio
    async def test_iem_image_used_when_spc_fails(self):
        """When SPC fails and no cache, IEM image URL is returned."""
        with patch("cogs.mesoscale.http_get_text", new_callable=AsyncMock) as mt, \
             patch("cogs.mesoscale.fetch_md_details_iem", new_callable=AsyncMock) as mi, \
             patch("cogs.mesoscale.os.path.exists", return_value=False):
            mt.return_value = None
            mi.return_value = ("http://iem.example/mcd0398.png", "IEM summary")
            from cogs.mesoscale import fetch_md_details
            image_url, summary, from_cache = await fetch_md_details("0398")
        assert image_url == "http://iem.example/mcd0398.png"
        assert from_cache is True

    @pytest.mark.asyncio
    async def test_cache_returned_when_both_fail(self):
        """When SPC fails and IEM returns nothing, cached file is used."""
        with patch("cogs.mesoscale.http_get_text", new_callable=AsyncMock) as mt, \
             patch("cogs.mesoscale.fetch_md_details_iem", new_callable=AsyncMock) as mi, \
             patch("cogs.mesoscale.os.path.exists", return_value=True):
            mt.return_value = None
            mi.return_value = (None, None)
            from cogs.mesoscale import fetch_md_details
            image_url, summary, from_cache = await fetch_md_details("0398")
        assert from_cache is True
        assert image_url is not None


# ── post_soundings_for_watch ─────────────────────────────────────────────────

class TestPostSoundingsForWatch:

    def _make_bot(self):
        bot = MagicMock()
        bot.state = MagicMock()
        bot.state.active_watches = {}
        return bot

    @pytest.mark.asyncio
    async def test_skips_when_no_affected_zones(self):
        """If nws_info has no affected_zones, method returns early without posting."""
        from cogs.sounding import SoundingCog

        bot = self._make_bot()
        cog = SoundingCog.__new__(SoundingCog)
        cog.bot = bot
        cog._posted_watch_soundings = set()

        channel = AsyncMock()
        nws_info = {"type": "SVR", "expires": None, "affected_zones": []}

        await cog.post_soundings_for_watch("0102", nws_info, channel)
        channel.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_centroid_fails(self):
        """If centroid resolution fails, method returns without posting."""
        from cogs.sounding import SoundingCog

        bot = self._make_bot()
        cog = SoundingCog.__new__(SoundingCog)
        cog.bot = bot
        cog._posted_watch_soundings = set()

        channel = AsyncMock()
        nws_info = {"type": "SVR", "expires": None, "affected_zones": ["https://api.weather.gov/zones/county/IAC001"]}

        with patch("cogs.sounding.get_watch_area_centroid", new=AsyncMock(return_value=None)):
            await cog.post_soundings_for_watch("0102", nws_info, channel)

        channel.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_fires_after_watch_posted(self):
        """SoundingCog.post_soundings_for_watch is called after a new watch is posted."""
        from cogs.watches import WatchesCog

        bot = self._make_bot()
        bot.wait_until_ready = AsyncMock()
        bot.get_channel = MagicMock(return_value=AsyncMock())

        mock_sounding_cog = MagicMock()
        mock_sounding_cog.post_soundings_for_watch = AsyncMock()
        bot.cogs = {"SoundingCog": mock_sounding_cog}

        nws_result = {
            "0102": {
                "type": "SVR",
                "expires": datetime(2026, 4, 14, 2, 0, tzinfo=timezone.utc),
                "affected_zones": ["https://api.weather.gov/zones/county/IAC001"],
            }
        }

        with patch("cogs.watches.fetch_active_watches_nws", new=AsyncMock(return_value=nws_result)), \
             patch("cogs.watches.fetch_watch_details", new=AsyncMock(return_value=(None, None, None))), \
             patch("cogs.watches.download_single_image", new=AsyncMock(return_value=(None, False, None))), \
             patch("cogs.watches.add_posted_watch", new=AsyncMock()), \
             patch("cogs.watches.prune_posted_watches", new=AsyncMock()):

            cog = WatchesCog(bot)
            cog.auto_post_watches.cancel()
            await cog.auto_post_watches()

        await asyncio.sleep(0)
        mock_sounding_cog.post_soundings_for_watch.assert_called_once()
        call_args = mock_sounding_cog.post_soundings_for_watch.call_args[0]
        assert call_args[0] == "0102"
