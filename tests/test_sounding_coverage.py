"""Coverage round 7: sounding cog — watch caption/near logic and centroid memo.

Pure-logic tests for cogs/sounding.py (21% covered): the watch caption
formatter, the watches-near filter, the memoized watch-centroid resolver, and
the posted-sounding/ handled-watch markers.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from cogs import sounding


def _sounding_cog(bot=None):
    return sounding.SoundingCog(bot or MagicMock())


# ── Watch caption formatting ─────────────────────────────────────────────────


def test_format_watches_caption_empty_falls_back():
    cog = _sounding_cog()
    text = cog._format_watches_caption([], "0001", "SVR")
    assert text == "Near active SVR #0001"


def test_format_watches_caption_single():
    cog = _sounding_cog()
    text = cog._format_watches_caption([("0045", "Tornado", 123.4)], "0001", "SVR")
    assert text == "Near active Tornado Watch #0045"


def test_format_watches_caption_multiple():
    cog = _sounding_cog()
    applicable = [("0001", "SVR", 10.0), ("0045", "Tornado", 20.0), ("0100", "SVR", 30.0)]
    text = cog._format_watches_caption(applicable, "0001", "SVR")
    assert text == "Near active watches #0001 (SVR), #0045 (Tornado), #0100 (SVR)"


# ── Watches near a station ───────────────────────────────────────────────────


async def test_watches_near_filters_sorts_and_labels():
    bot = MagicMock()
    from utils.state import BotState

    bot.state = BotState()
    bot.state.active_watches = {
        "0100": {"type": "SVR"},
        "0045": {"type": "TORNADO"},
        "9999": {"type": "SVR"},  # far away -> excluded
    }
    cog = _sounding_cog(bot)

    async def fake_centroid(watch_num, info):
        centroids = {
            "0100": (35.0, -97.0),
            "0045": (35.2, -97.4),
            "9999": (60.0, -150.0),  # > max_km from the station
        }
        return centroids.get(watch_num)

    with patch.object(cog, "_resolve_watch_centroid", side_effect=fake_centroid):
        applicable = await cog._watches_near(35.0, -97.0, max_km=200)

    # Sorted by watch number ascending; distance ascending check per entry.
    assert [a[0] for a in applicable] == ["0045", "0100"]
    assert applicable[0][1] == "Tornado"
    assert applicable[1][1] == "SVR"
    assert all(a[2] < 200 for a in applicable)


async def test_watches_near_no_centroid_skipped():
    bot = MagicMock()
    from utils.state import BotState

    bot.state = BotState()
    bot.state.active_watches = {"0045": {"type": "TORNADO"}}
    cog = _sounding_cog(bot)

    with patch.object(cog, "_resolve_watch_centroid", new_callable=AsyncMock, return_value=None):
        applicable = await cog._watches_near(35.0, -97.0)

    assert applicable == []


# ── Watch centroid memoization ───────────────────────────────────────────────


async def test_resolve_watch_centroid_memoized():
    cog = _sounding_cog()
    cog._watch_centroids["0045"] = (35.2, -97.4)

    with patch("cogs.sounding.get_watch_centroid_cache", new_callable=AsyncMock) as mock_cache:
        result = await cog._resolve_watch_centroid("0045", {"affected_zones": ["OKC005"]})

    assert result == (35.2, -97.4)
    mock_cache.assert_not_awaited()


async def test_resolve_watch_centroid_db_cache():
    cog = _sounding_cog()
    with patch(
        "cogs.sounding.get_watch_centroid_cache", new_callable=AsyncMock
    ) as mock_cache, patch(
        "cogs.sounding.get_watch_area_centroid", new_callable=AsyncMock
    ) as mock_area:
        mock_cache.return_value = (34.0, -98.0)
        result = await cog._resolve_watch_centroid("0045", {"affected_zones": ["OKC005"]})

    assert result == (34.0, -98.0)
    assert cog._watch_centroids["0045"] == (34.0, -98.0)
    mock_area.assert_not_awaited()


async def test_resolve_watch_centroid_no_zones():
    cog = _sounding_cog()
    with patch("cogs.sounding.get_watch_centroid_cache", new_callable=AsyncMock) as mock_cache:
        mock_cache.return_value = None
        result = await cog._resolve_watch_centroid("0045", {})

    assert result is None


async def test_resolve_watch_centroid_fetches_and_caches():
    cog = _sounding_cog()
    with patch(
        "cogs.sounding.get_watch_centroid_cache", new_callable=AsyncMock
    ) as mock_cache, patch(
        "cogs.sounding.get_watch_area_centroid", new_callable=AsyncMock
    ) as mock_area, patch(
        "cogs.sounding.set_watch_centroid_cache", new_callable=AsyncMock
    ) as mock_set:
        mock_cache.return_value = None
        mock_area.return_value = (33.5, -96.5)
        result = await cog._resolve_watch_centroid("0045", {"affected_zones": ["OKC005"]})

    assert result == (33.5, -96.5)
    assert cog._watch_centroids["0045"] == (33.5, -96.5)
    mock_area.assert_awaited_once_with(["OKC005"])
    mock_set.assert_awaited_once_with("0045", (33.5, -96.5))


# ── Posted-sounding markers ──────────────────────────────────────────────────


async def test_mark_sounding_posted_and_watch_handled():
    from utils.state import BotState

    bot = MagicMock()
    bot.state = BotState()
    cog = _sounding_cog(bot)

    await cog._mark_sounding_posted("KOUN_20260811_12z")
    assert "KOUN_20260811_12z" in bot.state.posted_soundings

    await cog._mark_watch_handled("0045")
    assert "0045" in bot.state.sounding_handled_watches
