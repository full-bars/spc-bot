# tests/test_watches.py
"""
Unit tests for watches VTEC parsing and API failure handling.

Run with: python -m pytest tests/ -v
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestFetchActiveWatchesNWS:
    """Tests for fetch_active_watches_nws() parsing and failure handling."""

    def _make_response(self, features):
        """Build a minimal NWS API response payload."""
        return json.dumps({"features": features}).encode()

    def _make_feature(self, vtec, expires=None):
        """Build a minimal NWS alert feature with a VTEC string."""
        return {
            "properties": {
                "parameters": {"VTEC": [vtec]},
                "expires": expires,
                "ends": None,
            }
        }

    @staticmethod
    def _spc_html(*watch_nums):
        """Build a minimal SPC watch index HTML listing the given watch numbers."""
        links = "".join(
            f'<a href="/products/watch/ww{n.zfill(4)}.html">Watch {n}</a>' for n in watch_nums
        )
        return f"<html><body>{links}</body></html>"

    @pytest.mark.asyncio
    async def test_valid_tornado_watch(self):
        """Valid TO.A VTEC string is parsed as TORNADO watch."""
        from cogs.watches import fetch_active_watches_nws

        feature = self._make_feature(
            "/O.NEW.KWNS.TO.A.0042.260409T1800Z-260410T0000Z/",
            expires="2026-04-09T18:00:00+00:00",
        )
        payload = self._make_response([feature])

        with patch(
            "cogs.watch_fetch.http_get_bytes_conditional",
            new_callable=AsyncMock,
            return_value=(payload, 200, None),
        ), patch(
            "cogs.watch_fetch.http_get_text",
            new_callable=AsyncMock,
            return_value=self._spc_html("0042"),
        ):
            result = await fetch_active_watches_nws()

        assert result is not None
        assert "0042" in result
        assert result["0042"]["type"] == "TORNADO"
        assert result["0042"]["expires"] is not None

    @pytest.mark.asyncio
    async def test_valid_severe_watch(self):
        """Valid SV.A VTEC string is parsed as SVR watch."""
        from cogs.watches import fetch_active_watches_nws

        feature = self._make_feature(
            "/O.NEW.KWNS.SV.A.0101.260409T1800Z-260410T0000Z/",
        )
        payload = self._make_response([feature])

        with patch(
            "cogs.watch_fetch.http_get_bytes_conditional",
            new_callable=AsyncMock,
            return_value=(payload, 200, None),
        ), patch(
            "cogs.watch_fetch.http_get_text",
            new_callable=AsyncMock,
            return_value=self._spc_html("0101"),
        ):
            result = await fetch_active_watches_nws()

        assert result is not None
        assert "0101" in result
        assert result["0101"]["type"] == "SVR"

    @pytest.mark.asyncio
    async def test_watch_number_zero_padded(self):
        """Watch numbers are zero-padded to 4 digits."""
        from cogs.watches import fetch_active_watches_nws

        feature = self._make_feature(
            "/O.NEW.KWNS.SV.A.0007.260409T1800Z-260410T0000Z/",
        )
        payload = self._make_response([feature])

        with patch(
            "cogs.watch_fetch.http_get_bytes_conditional",
            new_callable=AsyncMock,
            return_value=(payload, 200, None),
        ), patch(
            "cogs.watch_fetch.http_get_text",
            new_callable=AsyncMock,
            return_value=self._spc_html("0007"),
        ):
            result = await fetch_active_watches_nws()

        assert "0007" in result

    @pytest.mark.asyncio
    async def test_duplicate_watch_number_deduplicated(self):
        """Duplicate watch numbers from multiple features are deduplicated."""
        from cogs.watches import fetch_active_watches_nws

        vtec = "/O.NEW.KWNS.TO.A.0042.260409T1800Z-260410T0000Z/"
        payload = self._make_response(
            [
                self._make_feature(vtec),
                self._make_feature(vtec),
            ]
        )

        with patch(
            "cogs.watch_fetch.http_get_bytes_conditional",
            new_callable=AsyncMock,
            return_value=(payload, 200, None),
        ), patch(
            "cogs.watch_fetch.http_get_text",
            new_callable=AsyncMock,
            return_value=self._spc_html("0042"),
        ):
            result = await fetch_active_watches_nws()

        assert result is not None
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_malformed_vtec_skipped(self):
        """Features with unparseable VTEC strings are skipped gracefully."""
        from cogs.watches import fetch_active_watches_nws

        feature = self._make_feature("not-a-vtec-string")
        payload = self._make_response([feature])

        with patch(
            "cogs.watch_fetch.http_get_bytes_conditional",
            new_callable=AsyncMock,
            return_value=(payload, 200, None),
        ):
            result = await fetch_active_watches_nws()

        assert result == {}

    @pytest.mark.asyncio
    async def test_missing_expires_still_returns_entry(self):
        """Watch with no expires field is still returned with expires=None."""
        from cogs.watches import fetch_active_watches_nws

        feature = self._make_feature(
            "/O.NEW.KWNS.SV.A.0055.260409T1800Z-260410T0000Z/",
            expires=None,
        )
        payload = self._make_response([feature])

        with patch(
            "cogs.watch_fetch.http_get_bytes_conditional",
            new_callable=AsyncMock,
            return_value=(payload, 200, None),
        ), patch(
            "cogs.watch_fetch.http_get_text",
            new_callable=AsyncMock,
            return_value=self._spc_html("0055"),
        ):
            result = await fetch_active_watches_nws()

        assert "0055" in result
        assert result["0055"]["expires"] is None

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self):
        """HTTP non-200 response returns None, not empty dict."""
        from cogs.watches import fetch_active_watches_nws

        with patch(
            "cogs.watch_fetch.http_get_bytes_conditional",
            new_callable=AsyncMock,
            return_value=(None, 500, None),
        ):
            result = await fetch_active_watches_nws()

        assert result is None

    @pytest.mark.asyncio
    async def test_json_parse_error_returns_none(self):
        """Unparseable JSON response returns None, not empty dict."""
        from cogs.watches import fetch_active_watches_nws

        with patch(
            "cogs.watch_fetch.http_get_bytes_conditional",
            new_callable=AsyncMock,
            return_value=(b"not json {{{", 200, None),
        ):
            result = await fetch_active_watches_nws()

        assert result is None

    @pytest.mark.asyncio
    async def test_empty_features_returns_empty_dict(self):
        """API success with zero features returns {} not None."""
        from cogs.watches import fetch_active_watches_nws

        payload = self._make_response([])

        with patch(
            "cogs.watch_fetch.http_get_bytes_conditional",
            new_callable=AsyncMock,
            return_value=(payload, 200, None),
        ), patch(
            "cogs.watch_fetch.http_get_text",
            new_callable=AsyncMock,
            return_value=self._spc_html(),
        ):
            result = await fetch_active_watches_nws()

        assert result == {}
        assert result is not None

    @pytest.mark.asyncio
    async def test_stale_wfo_etns_filtered_by_spc_index(self):
        """WFO WCN features with ETNs absent from the SPC index are dropped."""
        from cogs.watches import fetch_active_watches_nws

        # NWS API returns two features: one valid (0230), one stale WFO ETN (0001)
        valid_feature = self._make_feature(
            "/O.CON.KCLE.SV.A.0230.000000T0000Z-260520T0200Z/",
            expires="2026-05-20T02:00:00+00:00",
        )
        stale_feature = self._make_feature(
            "/O.CON.KILN.SV.A.0001.000000T0000Z-260520T0200Z/",
            expires="2026-05-20T02:00:00+00:00",
        )
        payload = self._make_response([valid_feature, stale_feature])

        with patch(
            "cogs.watch_fetch.http_get_bytes_conditional",
            new_callable=AsyncMock,
            return_value=(payload, 200, None),
        ), patch(
            "cogs.watch_fetch.http_get_text",
            new_callable=AsyncMock,
            return_value=self._spc_html("0230"),  # only 0230 is on the SPC page
        ):
            result = await fetch_active_watches_nws()

        assert "0230" in result
        assert "0001" not in result


# ── post_watch_now (iembot fast-path) ────────────────────────────────────────


def _make_watch_bot(posted_watches=None):
    bot = MagicMock()
    bot.state.posted_watches = set(posted_watches or [])
    bot.state.auto_cache = {}
    bot.state.watch_image_cache = {}
    bot.state.last_post_times = {}
    bot.cogs = {}
    bot.wait_until_ready = AsyncMock()
    bot.state.add_posted_watch = AsyncMock()
    bot.state.add_posted_product_id = AsyncMock()
    channel = AsyncMock()
    bot.get_channel.return_value = channel
    return bot, channel


@pytest.mark.asyncio
async def test_post_watch_now_dedup_skips_already_posted():
    """post_watch_now returns immediately if the watch is already posted."""
    from cogs.watches import WatchesCog

    bot, channel = _make_watch_bot(posted_watches={"0102"})
    cog = WatchesCog.__new__(WatchesCog)
    cog._pending_tasks = set()
    cog._watch_inflight = set()
    cog.bot = bot
    cog._watches_backoff = MagicMock()

    await cog.post_watch_now("0102", {"type": "SVR", "expires": None, "affected_zones": []})

    channel.send.assert_not_called()


@pytest.mark.asyncio
async def test_post_watch_now_sends_and_marks_posted():
    """post_watch_now posts an embed and records the watch in state."""
    from cogs.watches import WatchesCog

    bot, channel = _make_watch_bot()
    cog = WatchesCog.__new__(WatchesCog)
    cog._pending_tasks = set()
    cog._watch_inflight = set()
    cog.bot = bot
    cog._watches_backoff = MagicMock()

    # Configure the mock state to actually add to the set when add_posted_watch is called
    # to maintain the behavior of the existing test assertion.
    async def _mock_add(wn):
        bot.state.posted_watches.add(wn)

    bot.state.add_posted_watch = AsyncMock(side_effect=_mock_add)

    nws_info = {"type": "SVR", "expires": None, "affected_zones": []}

    with patch(
        "cogs.watches.fetch_watch_details",
        AsyncMock(return_value=("http://img.png", "summary", None, False)),
    ), patch("cogs.watches.download_single_image", AsyncMock(return_value=(None, False, None))):
        await cog.post_watch_now("0102", nws_info)

    channel.send.assert_called_once()
    bot.state.add_posted_watch.assert_called_with("0102")
    assert "0102" in bot.state.posted_watches


@pytest.mark.asyncio
async def test_post_watch_now_no_channel_returns_early():
    """post_watch_now silently returns if the channel is not found."""
    from cogs.watches import WatchesCog

    bot, _ = _make_watch_bot()
    bot.get_channel.return_value = None
    cog = WatchesCog.__new__(WatchesCog)
    cog._pending_tasks = set()
    cog._watch_inflight = set()
    cog.bot = bot
    cog._watches_backoff = MagicMock()

    await cog.post_watch_now("0102", {"type": "SVR", "expires": None, "affected_zones": []})


@pytest.mark.asyncio
async def test_post_watch_now_dispatches_to_sounding_cog():
    """When affected_zones is non-empty, post_soundings_for_watch is scheduled."""
    from cogs.watches import WatchesCog

    bot, channel = _make_watch_bot()
    mock_sounding = MagicMock()
    mock_sounding.post_soundings_for_watch = AsyncMock()
    bot.cogs["SoundingCog"] = mock_sounding

    nws_info = {
        "type": "TORNADO",
        "expires": None,
        "affected_zones": ["https://api.weather.gov/zones/county/IAC001"],
    }

    cog = WatchesCog.__new__(WatchesCog)
    cog._pending_tasks = set()
    cog._watch_inflight = set()
    cog.bot = bot
    cog._watches_backoff = MagicMock()

    with patch(
        "cogs.watches.fetch_watch_details", AsyncMock(return_value=(None, None, None, False))
    ), patch("cogs.watches.download_single_image", AsyncMock(return_value=(None, False, None))):
        await cog.post_watch_now("0102", nws_info)

    # post_soundings_for_watch is called to build the coroutine arg for create_task
    mock_sounding.post_soundings_for_watch.assert_called_once_with("0102", nws_info, channel)


@pytest.mark.asyncio
async def test_post_watch_now_concurrent_calls_post_once():
    """Two near-simultaneous triggers for the same watch (NWWS push + iembot
    poll) must result in exactly one send — the in-flight guard closes the
    race. Latent in practice (watches are sparse) but proven on MDs."""
    from cogs.watches import WatchesCog

    bot, channel = _make_watch_bot()
    cog = WatchesCog.__new__(WatchesCog)
    cog._pending_tasks = set()
    cog._watch_inflight = set()
    cog.bot = bot
    cog._watches_backoff = MagicMock()

    async def _mock_add(wn):
        bot.state.posted_watches.add(wn)

    bot.state.add_posted_watch = AsyncMock(side_effect=_mock_add)

    # fetch_watch_details awaits, yielding the loop between the dedup check and
    # the send — the window the race exploited. Both coroutines start before
    # either marks posted_watches.
    async def _slow_fetch(_wn):
        await asyncio.sleep(0)
        return ("http://img.png", "summary", None, False)

    nws_info = {"type": "SVR", "expires": None, "affected_zones": []}

    with patch("cogs.watches.fetch_watch_details", AsyncMock(side_effect=_slow_fetch)), patch(
        "cogs.watches.download_single_image", AsyncMock(return_value=(None, False, None))
    ):
        await asyncio.gather(
            cog.post_watch_now("0102", nws_info), cog.post_watch_now("0102", nws_info)
        )

    channel.send.assert_called_once()


@pytest.mark.asyncio
async def test_auto_post_watches_fallback_keeps_glitched_watch_alive():
    """If NWS API drops an active watch but returns others, it checks SPC. If SPC has it, it doesn't cancel."""
    from cogs.watches import WatchesCog

    bot, channel = _make_watch_bot()
    bot.state.is_primary = True
    bot.state.active_watches = {"0336": {"type": "TORNADO", "expires": None, "affected_zones": []}}
    bot.state.posted_watches = {"0337"}  # so it doesn't try to post a new one

    cog = WatchesCog.__new__(WatchesCog)
    cog._pending_tasks = set()
    cog._watch_inflight = set()
    cog.bot = bot
    cog._watches_backoff = MagicMock()

    # NWS API returns 0337 but drops 0336
    nws_mock = {"0337": {"type": "SVR", "expires": None, "affected_zones": []}}

    with patch("cogs.watches.fetch_active_watches_nws", AsyncMock(return_value=nws_mock)), patch(
        "cogs.watch_fetch.get_spc_active_watch_numbers", AsyncMock(return_value={"0336", "0337"})
    ):
        await WatchesCog.auto_post_watches.coro(cog)

    # Should STILL be in active watches!
    assert "0336" in bot.state.active_watches
    channel.send.assert_not_called()  # No cancellation message sent


@pytest.mark.asyncio
async def test_auto_post_watches_cancels_when_missing_from_both():
    """If NWS API drops a watch AND it's missing from SPC, it cancels."""
    from cogs.watches import WatchesCog

    bot, channel = _make_watch_bot()
    bot.state.is_primary = True
    bot.state.active_watches = {"0336": {"type": "TORNADO", "expires": None, "affected_zones": []}}
    bot.state.posted_watches = {"0337"}  # so it doesn't try to post a new one

    cog = WatchesCog.__new__(WatchesCog)
    cog._pending_tasks = set()
    cog._watch_inflight = set()
    cog.bot = bot
    cog._watches_backoff = MagicMock()

    nws_mock = {"0337": {"type": "SVR", "expires": None, "affected_zones": []}}

    with patch("cogs.watches.fetch_active_watches_nws", AsyncMock(return_value=nws_mock)), patch(
        "cogs.watch_fetch.get_spc_active_watch_numbers", AsyncMock(return_value={"0337"})
    ):  # 0336 missing
        await WatchesCog.auto_post_watches.coro(cog)

    # Should be REMOVED from active watches!
    assert "0336" not in bot.state.active_watches
    channel.send.assert_called_once()  # Cancellation message sent


@pytest.mark.asyncio
async def test_auto_post_watches_early_cancel_uses_cancel_time_not_expiry():
    """An early-cancelled watch says 'no longer active' and timestamps NOW,
    not the original expiration (regression pin for watch #584, 2026-08-15)."""
    from cogs.watches import WatchesCog

    bot, channel = _make_watch_bot()
    bot.state.is_primary = True
    future_expiry = datetime.now(timezone.utc) + timedelta(minutes=43)
    bot.state.active_watches = {
        "0584": {"type": "SVR", "expires": future_expiry, "affected_zones": []}
    }
    bot.state.posted_watches = {"0585"}

    cog = WatchesCog.__new__(WatchesCog)
    cog._pending_tasks = set()
    cog._watch_inflight = set()
    cog.bot = bot
    cog._watches_backoff = MagicMock()

    nws_mock = {"0585": {"type": "SVR", "expires": None, "affected_zones": []}}
    with patch("cogs.watches.fetch_active_watches_nws", AsyncMock(return_value=nws_mock)), patch(
        "cogs.watch_fetch.get_spc_active_watch_numbers", AsyncMock(return_value={"0585"})
    ):
        await WatchesCog.auto_post_watches.coro(cog)

    channel.send.assert_called_once()
    content = channel.send.await_args.kwargs["content"]
    # Says "no longer active", not "expired".
    assert "no longer active" in content
    assert "expired" not in content
    # Timestamp is the cancellation moment (now), not the original expiry.
    # Compare with tolerance: the timestamp truncates to whole seconds and
    # the loop's now_utc is captured a moment before the send.
    now_reference = datetime.now(timezone.utc)
    ts = int(content.split("<t:")[1].split(":R>")[0])
    ts_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    assert abs((ts_dt - now_reference).total_seconds()) < 5


@pytest.mark.asyncio
async def test_auto_post_watches_time_expiry_uses_original_expiry():
    """A watch that hit its scheduled expiry says 'expired' and timestamps
    the ORIGINAL expiration."""
    from cogs.watches import WatchesCog

    bot, channel = _make_watch_bot()
    bot.state.is_primary = True
    past_expiry = datetime.now(timezone.utc) - timedelta(minutes=5)
    bot.state.active_watches = {
        "0586": {"type": "SVR", "expires": past_expiry, "affected_zones": []}
    }
    bot.state.posted_watches = {"0587"}

    cog = WatchesCog.__new__(WatchesCog)
    cog._pending_tasks = set()
    cog._watch_inflight = set()
    cog.bot = bot
    cog._watches_backoff = MagicMock()

    nws_mock = {"0587": {"type": "SVR", "expires": None, "affected_zones": []}}
    with patch("cogs.watches.fetch_active_watches_nws", AsyncMock(return_value=nws_mock)):
        await WatchesCog.auto_post_watches.coro(cog)

    channel.send.assert_called_once()
    content = channel.send.await_args.kwargs["content"]
    assert "expired" in content
    ts = int(content.split("<t:")[1].split(":R>")[0])
    ts_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    assert abs((ts_dt - past_expiry).total_seconds()) < 2  # original expiry


@pytest.mark.asyncio
async def test_auto_post_watches_cancellation_reuses_cached_graphic(tmp_path):
    """When a graphic was cached for the watch during issuance, the
    cancellation message attaches it as an embed image instead of going
    text-only."""
    from cogs.watches import WatchesCog

    image_path = tmp_path / "watch_0588.gif"
    image_path.write_bytes(b"GIF89a")

    bot, channel = _make_watch_bot()
    bot.state.is_primary = True
    past_expiry = datetime.now(timezone.utc) - timedelta(minutes=5)
    bot.state.active_watches = {
        "0588": {"type": "SVR", "expires": past_expiry, "affected_zones": []}
    }
    bot.state.watch_image_cache = {"0588": str(image_path)}
    bot.state.posted_watches = {"0589"}

    cog = WatchesCog.__new__(WatchesCog)
    cog._pending_tasks = set()
    cog._watch_inflight = set()
    cog.bot = bot
    cog._watches_backoff = MagicMock()

    nws_mock = {"0589": {"type": "SVR", "expires": None, "affected_zones": []}}
    with patch("cogs.watches.fetch_active_watches_nws", AsyncMock(return_value=nws_mock)):
        await WatchesCog.auto_post_watches.coro(cog)

    channel.send.assert_called_once()
    kwargs = channel.send.await_args.kwargs
    assert kwargs.get("embed") is not None
    assert kwargs.get("files")
    assert "expired" in kwargs["content"]
    # Cached path is consumed on a successful post.
    assert "0588" not in bot.state.watch_image_cache


@pytest.mark.asyncio
async def test_auto_post_watches_cancellation_without_cached_graphic_is_text_only():
    """No cached graphic for the watch → cancellation stays plain text, as
    before this feature was added."""
    from cogs.watches import WatchesCog

    bot, channel = _make_watch_bot()
    bot.state.is_primary = True
    past_expiry = datetime.now(timezone.utc) - timedelta(minutes=5)
    bot.state.active_watches = {
        "0590": {"type": "SVR", "expires": past_expiry, "affected_zones": []}
    }
    bot.state.posted_watches = {"0591"}

    cog = WatchesCog.__new__(WatchesCog)
    cog._pending_tasks = set()
    cog._watch_inflight = set()
    cog.bot = bot
    cog._watches_backoff = MagicMock()

    nws_mock = {"0591": {"type": "SVR", "expires": None, "affected_zones": []}}
    with patch("cogs.watches.fetch_active_watches_nws", AsyncMock(return_value=nws_mock)):
        await WatchesCog.auto_post_watches.coro(cog)

    channel.send.assert_called_once()
    kwargs = channel.send.await_args.kwargs
    assert kwargs.get("embed") is None
    assert kwargs.get("files") is None
