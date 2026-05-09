"""
Unit tests for SoundingCog._resolve_watch_centroid — in-memory memoization
and graceful handling of missing zone data.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def bot():
    b = MagicMock()
    b.state.active_watches = {}
    return b


@pytest.fixture
def cog(bot):
    from cogs.sounding import SoundingCog
    c = SoundingCog.__new__(SoundingCog)
    c.bot = bot
    c._watch_centroids = {}
    c._posted_watch_soundings = set()
    c._handled_watches = set()
    c._restore_attempted = True
    return c


@pytest.mark.asyncio
async def test_returns_from_memory_cache(cog):
    """Returns immediately from in-memory dict without hitting NWS API."""
    cog._watch_centroids["0042"] = (35.5, -97.5)
    info = {"affected_zones": ["https://api.weather.gov/zones/county/OKC031"]}

    with patch("cogs.sounding.get_watch_area_centroid") as nws_get:
        result = await cog._resolve_watch_centroid("0042", info)

    assert result == (35.5, -97.5)
    nws_get.assert_not_called()


@pytest.mark.asyncio
async def test_fetches_from_nws_on_memory_miss(cog):
    """Calls get_watch_area_centroid when not in memory; stores result."""
    info = {"affected_zones": ["https://api.weather.gov/zones/county/OKC031"]}

    with patch("cogs.sounding.get_watch_centroid_cache", AsyncMock(return_value=None)), \
         patch("cogs.sounding.get_watch_area_centroid", AsyncMock(return_value=(35.5, -97.5))), \
         patch("cogs.sounding.set_watch_centroid_cache", AsyncMock()):
        result = await cog._resolve_watch_centroid("0042", info)

    assert result == (35.5, -97.5)
    assert cog._watch_centroids["0042"] == (35.5, -97.5)


@pytest.mark.asyncio
async def test_second_call_uses_memory(cog):
    """Second call for the same watch uses the memoized value, not NWS."""
    info = {"affected_zones": ["https://api.weather.gov/zones/county/OKC031"]}

    with patch("cogs.sounding.get_watch_area_centroid", AsyncMock(return_value=(35.5, -97.5))):
        await cog._resolve_watch_centroid("0042", info)

    with patch("cogs.sounding.get_watch_area_centroid") as nws_get:
        result = await cog._resolve_watch_centroid("0042", info)

    assert result == (35.5, -97.5)
    nws_get.assert_not_called()


@pytest.mark.asyncio
async def test_returns_none_when_no_zones(cog):
    """Returns None immediately when the watch has no affected zones."""
    info = {"affected_zones": []}

    with patch("cogs.sounding.get_watch_centroid_cache", AsyncMock(return_value=None)), \
         patch("cogs.sounding.get_watch_area_centroid") as nws_get:
        result = await cog._resolve_watch_centroid("0042", info)

    assert result is None
    nws_get.assert_not_called()


@pytest.mark.asyncio
async def test_does_not_cache_none_from_api(cog):
    """Does not store None in memory when NWS API returns None."""
    info = {"affected_zones": ["https://api.weather.gov/zones/county/OKC031"]}

    with patch("cogs.sounding.get_watch_centroid_cache", AsyncMock(return_value=None)), \
         patch("cogs.sounding.get_watch_area_centroid", AsyncMock(return_value=None)), \
         patch("cogs.sounding.set_watch_centroid_cache", AsyncMock()):
        result = await cog._resolve_watch_centroid("0042", info)

    assert result is None
    assert "0042" not in cog._watch_centroids


@pytest.mark.asyncio
async def test_handles_non_dict_info(cog):
    """Returns None gracefully when info is not a dict (e.g. legacy string value)."""
    with patch("cogs.sounding.get_watch_centroid_cache", AsyncMock(return_value=None)), \
         patch("cogs.sounding.get_watch_area_centroid") as nws_get:
        result = await cog._resolve_watch_centroid("0042", "TORNADO")

    assert result is None
    nws_get.assert_not_called()
