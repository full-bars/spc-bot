"""
Tests for cogs/tropical.py — product classification and posting.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cogs.tropical import (
    TropicalCog,
    TROPICAL_CHANNEL_ID,
    _classify_product,
    _classify_storm_type,
    _extract_storm_name,
)


# ── _classify_product ───────────────────────────────────────────────────────


def test_classify_product_matches_known_pil():
    assert _classify_product("202607220600-KNHC-WTNT31-TCVAT1") == "ADVISORY"


def test_classify_product_returns_none_for_unknown_pil():
    assert _classify_product("202607220600-KNHC-WTNT31-XXXXAT1") is None


# ── _classify_storm_type / _extract_storm_name ──────────────────────────────


def test_classify_storm_type_hurricane():
    assert _classify_storm_type("HURRICANE ANNA ADVISORY NUMBER 5") == "HURRICANE"


def test_classify_storm_type_none_when_absent():
    assert _classify_storm_type("SOME UNRELATED TEXT") is None


def test_extract_storm_name():
    assert _extract_storm_name("HURRICANE ANNA") == "Anna"


# ── post_tropical_product ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_tropical_product_handles_missing_summary():
    """A product with no 'SUMMARY OF' section (most NHC PILs) parses to
    summary=None — this must not crash the embed-building loop."""
    bot = MagicMock()
    channel = AsyncMock()
    bot.get_channel.return_value = channel
    cog = TropicalCog(bot)

    parsed = {
        "raw_text": "HURRICANE ANNA ADVISORY NUMBER 5",
        "summary": None,
        "storm_type": "HURRICANE",
        "storm_name": "Anna",
    }

    with patch("cogs.tropical._fetch_nhc_product", AsyncMock(return_value=parsed)), patch(
        "cogs.tropical.get_state", AsyncMock(return_value=None)
    ):
        await cog.post_tropical_product("202607220600-KNHC-WTNT31-TCVAT1", "", "ADVISORY")

    channel.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_tropical_product_posts_to_prod_channel():
    bot = MagicMock()
    channel = AsyncMock()
    bot.get_channel.return_value = channel
    cog = TropicalCog(bot)

    parsed = {
        "raw_text": "HURRICANE ANNA ADVISORY NUMBER 5",
        "summary": None,
        "storm_type": "HURRICANE",
        "storm_name": "Anna",
    }

    with patch("cogs.tropical._fetch_nhc_product", AsyncMock(return_value=parsed)), patch(
        "cogs.tropical.get_state", AsyncMock(return_value=None)
    ):
        await cog.post_tropical_product("202607220600-KNHC-WTNT31-TCVAT1", "", "ADVISORY")

    bot.get_channel.assert_called_once_with(TROPICAL_CHANNEL_ID)
