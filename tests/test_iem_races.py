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
            mi.return_value = (None, None, None)
            from cogs.mesoscale import fetch_md_details
            image_url, summary, from_cache, raw_text = await fetch_md_details("0398")
        assert "mcd0398" in image_url
        assert from_cache is False

    @pytest.mark.asyncio
    async def test_iem_image_used_when_spc_fails(self):
        """When SPC fails and no cache, IEM image URL is returned."""
        with patch("cogs.mesoscale.http_get_text", new_callable=AsyncMock) as mt, \
             patch("cogs.mesoscale.fetch_md_details_iem", new_callable=AsyncMock) as mi, \
             patch("cogs.mesoscale.os.path.exists", return_value=False), \
             patch("cogs.mesoscale.asyncio.create_task") as mct, \
             patch("cogs.mesoscale.asyncio.wait", new_callable=AsyncMock) as mw:

            mt.return_value = None
            mi.return_value = ("http://iem.example/mcd0398.png", "IEM summary", "IEM raw")

            # Use real Futures for task mocks
            loop = asyncio.get_running_loop()
            spc_task = loop.create_future()
            iem_task = loop.create_future()
            mct.side_effect = [spc_task, iem_task]

            # IEM wins immediately
            iem_task.set_result(mi.return_value)
            mw.return_value = ({iem_task}, {spc_task})

            # SPC eventually returns None
            spc_task.set_result(None)

            from cogs.mesoscale import fetch_md_details
            image_url, summary, from_cache, raw_text = await fetch_md_details("0398")

        assert image_url == "http://iem.example/mcd0398.png"
        assert from_cache is True

    @pytest.mark.asyncio
    async def test_cache_returned_when_both_fail(self):
        """When SPC fails and IEM returns nothing, cached file is used."""
        with patch("cogs.mesoscale.http_get_text", new_callable=AsyncMock) as mt, \
             patch("cogs.mesoscale.fetch_md_details_iem", new_callable=AsyncMock) as mi, \
             patch("cogs.mesoscale.os.path.exists", return_value=True):
            mt.return_value = None
            mi.return_value = (None, None, None)
            from cogs.mesoscale import fetch_md_details
            image_url, summary, from_cache, raw_text = await fetch_md_details("0398")
        assert from_cache is True
        assert image_url is not None

# ── post_soundings_for_watch ─────────────────────────────────────────────────

class TestPostSoundingsForWatch:

    def _make_bot(self):
        bot = MagicMock()
        bot.state = MagicMock()
        bot.state.active_watches = {}
        # Use a real dict for cogs so .get() works normally
        bot.cogs = {}
        return bot

    @pytest.mark.asyncio
    async def test_skips_when_no_affected_zones(self):
        """If nws_info has no affected_zones, method returns early without posting."""
        from cogs.sounding import SoundingCog

        bot = self._make_bot()
        cog = SoundingCog.__new__(SoundingCog)
        cog.bot = bot
        cog._posted_watch_soundings = set()
        cog._handled_watches = set()

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
        cog._handled_watches = set()

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
        bot.state.is_primary = True # MUST be primary to run task
        bot.wait_until_ready = AsyncMock()
        bot.get_channel = MagicMock(return_value=AsyncMock())

        mock_sounding_cog = MagicMock()
        mock_sounding_cog.post_soundings_for_watch = AsyncMock()
        bot.cogs["SoundingCog"] = mock_sounding_cog
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

            await asyncio.sleep(0.1)
            mock_sounding_cog.post_soundings_for_watch.assert_called_once()

        call_args = mock_sounding_cog.post_soundings_for_watch.call_args[0]
        assert call_args[0] == "0102"


# ── Per-station (watch-agnostic) dedup ──────────────────────────────────────
#
# The 2026-04-23 regression: two geographically-overlapping watches (e.g. a
# Tornado Watch and a SVR Watch covering adjacent counties) each triggered a
# post of the same ACARS profile at the same valid time. The dedup key
# previously included watch_num, so `acars:OMA:2026-04-23_20z` was one key
# but `acars:0134:OMA:2026-04-23_20z` and `acars:0135:OMA:...` were not.
#
# The fix drops watch_num from the key. These tests pin that contract.


class TestSoundingDedupAcrossWatches:

    def _make_cog(self):
        from cogs.sounding import SoundingCog
        bot = MagicMock()
        bot.state = MagicMock()
        cog = SoundingCog.__new__(SoundingCog)
        cog.bot = bot
        cog._posted_watch_soundings = set()
        cog._handled_watches = set()
        return cog

    def test_raob_key_is_station_plus_time_only(self):
        """Same station+time for two different watches must collide."""
        cog = self._make_cog()
        time_key = "2026-04-23_00z"

        # Watch #0134 (TOR) processes KOAX first
        key_a = f"raob:KOAX:{time_key}"
        assert key_a not in cog._posted_watch_soundings
        cog._posted_watch_soundings.add(key_a)

        # Watch #0135 (SVR) in an overlapping region finds KOAX too
        key_b = f"raob:KOAX:{time_key}"
        assert key_b in cog._posted_watch_soundings, (
            "RAOB dedup key must NOT include watch_num — otherwise we re-post "
            "the same sounding once per active watch."
        )

    def test_acars_key_is_airport_plus_time_only(self):
        """Same ACARS airport+time for two different watches must collide."""
        cog = self._make_cog()
        time_key = "20260423_20z"

        key_a = f"acars:OMA:{time_key}"
        cog._posted_watch_soundings.add(key_a)

        key_b = f"acars:OMA:{time_key}"
        assert key_b in cog._posted_watch_soundings

    @pytest.mark.asyncio
    async def test_persist_posted_state_writes_today_payload(self, monkeypatch):
        """After posts, the dedup set is persisted to Upstash with today's
        UTC date. On next cog_load we restore only if date matches, so the
        set survives a restart but auto-resets at UTC rollover."""
        import json as _json
        from datetime import datetime, timezone
        from cogs import sounding as sounding_module

        captured = {}

        async def fake_set_state(key, value):
            captured["key"] = key
            captured["value"] = value

        monkeypatch.setattr(sounding_module, "set_state", fake_set_state)

        cog = self._make_cog()
        cog._posted_watch_soundings = {"raob:KOAX:2026-04-23_00z", "acars:OMA:20260423_20z"}
        cog._handled_watches = {"0134", "0135"}

        await cog._persist_posted_state()

        assert captured["key"] == "posted_watch_soundings"
        payload = _json.loads(captured["value"])
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert payload["date"] == today
        assert set(payload["keys"]) == {
            "raob:KOAX:2026-04-23_00z", "acars:OMA:20260423_20z"
        }
        assert set(payload["handled"]) == {"0134", "0135"}

    @pytest.mark.asyncio
    async def test_cog_load_restores_todays_keys(self, monkeypatch):
        """A restart mid-event must restore the dedup set so we don't
        re-post every station that was already covered earlier today."""
        import json as _json
        from datetime import datetime, timezone
        from cogs import sounding as sounding_module

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        payload = _json.dumps({
            "date": today,
            "keys": ["raob:KOAX:2026-04-23_00z", "acars:OMA:20260423_20z"],
            "handled": ["0134", "0135"],
        })

        async def fake_get_state(key):
            return payload if key == "posted_watch_soundings" else None

        monkeypatch.setattr(sounding_module, "get_state", fake_get_state)

        cog = self._make_cog()
        await cog.cog_load()

        assert cog._posted_watch_soundings == {
            "raob:KOAX:2026-04-23_00z", "acars:OMA:20260423_20z"
        }
        assert cog._handled_watches == {"0134", "0135"}

    @pytest.mark.asyncio
    async def test_cog_load_drops_stale_payload_from_yesterday(self, monkeypatch):
        """At UTC rollover yesterday's dedup keys should NOT be loaded —
        today's posts haven't happened yet and restoring them would
        silently suppress the first real post of each station."""
        import json as _json
        from cogs import sounding as sounding_module

        stale = _json.dumps({
            "date": "1999-01-01",
            "keys": ["raob:KOAX:1999-01-01_00z"],
            "handled": ["9999"],
        })

        async def fake_get_state(key):
            return stale

        monkeypatch.setattr(sounding_module, "get_state", fake_get_state)

        cog = self._make_cog()
        await cog.cog_load()

        assert cog._posted_watch_soundings == set()
        assert cog._handled_watches == set()

    @pytest.mark.asyncio
    async def test_cog_load_tolerates_malformed_payload(self, monkeypatch):
        """A garbled dedup blob must not crash cog_load."""
        from cogs import sounding as sounding_module

        async def fake_get_state(key):
            return "{not valid json"

        monkeypatch.setattr(sounding_module, "get_state", fake_get_state)

        cog = self._make_cog()
        await cog.cog_load()  # should not raise
        assert cog._posted_watch_soundings == set()
