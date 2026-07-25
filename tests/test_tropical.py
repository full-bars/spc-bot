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
    _fetch_nhc_product,
)

# Real TCP text (Hurricane Fausto Advisory 14) — the "Summary" section must
# stop before "WATCHES AND WARNINGS", not swallow the rest of the product.
FAUSTO_TCP = """WTPZ31 KNHC 220900
TCPEP1

BULLETIN
HURRICANE FAUSTO ADVISORY NUMBER  14
NWS NATIONAL HURRICANE CENTER MIAMI FL       EP072026
1100 PM HST TUE JUL 21 2026

SUMMARY OF 1100 PM HST...0900 UTC...INFORMATION
-----------------------------------------------
LOCATION...16.7N 120.7W
ABOUT 820 MI...1320 KM WSW OF THE SOUTHERN TIP OF BAJA CALIFORNIA
MAXIMUM SUSTAINED WINDS...85 MPH...140 KM/H
PRESENT MOVEMENT...WNW OR 285 DEGREES AT 8 MPH...13 KM/H
MINIMUM CENTRAL PRESSURE...981 MB...28.97 INCHES


WATCHES AND WARNINGS
--------------------
There are no coastal watches or warnings in effect.


DISCUSSION AND OUTLOOK
----------------------
At 1100 PM HST (0900 UTC), the center of Hurricane Fausto was
located near latitude 16.7 North, longitude 120.7 West.

NEXT ADVISORY
-------------
Next complete advisory at 500 AM HST.

$$
Forecaster Kelly
"""


# ── _fetch_nhc_product ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_nhc_product_summary_stops_before_next_section():
    """Regression test: the summary block must not swallow WATCHES AND
    WARNINGS / DISCUSSION AND OUTLOOK / NEXT ADVISORY / the forecaster
    signature — only the location/movement/pressure block."""
    with patch(
        "cogs.tropical.http_get_bytes",
        AsyncMock(return_value=(FAUSTO_TCP.encode(), 200)),
    ):
        parsed = await _fetch_nhc_product("202607220900-KNHC-WTPZ31-TCPEP1")

    assert parsed is not None
    assert "LOCATION...16.7N 120.7W" in parsed["summary"]
    assert "WATCHES AND WARNINGS" not in parsed["summary"]
    assert "DISCUSSION AND OUTLOOK" not in parsed["summary"]
    assert "Forecaster Kelly" not in parsed["summary"]


# ── _classify_product ───────────────────────────────────────────────────────


def test_classify_product_matches_known_pil():
    assert _classify_product("202607220600-KNHC-WTNT31-TCPAT1") == "ADVISORY"


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
        await cog.post_tropical_product("202607220600-KNHC-WTNT31-TCPAT1", "", "ADVISORY")

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
        await cog.post_tropical_product("202607220600-KNHC-WTNT31-TCPAT1", "", "ADVISORY")

    bot.get_channel.assert_called_once_with(TROPICAL_CHANNEL_ID)


@pytest.mark.asyncio
async def test_post_tropical_product_posts_full_text_in_thread():
    """The channel message should carry only the short summary; the full
    raw product text goes to a thread on that message, not the channel."""
    bot = MagicMock()
    channel = AsyncMock()
    main_msg = AsyncMock()
    thread = AsyncMock()
    channel.send.return_value = main_msg
    main_msg.create_thread.return_value = thread
    bot.get_channel.return_value = channel
    cog = TropicalCog(bot)

    parsed = {
        "raw_text": "FULL RAW PRODUCT TEXT " * 50,
        "summary": "SUMMARY OF 1100 PM HST...INFORMATION\nLOCATION...16.7N 120.7W",
        "storm_type": "HURRICANE",
        "storm_name": "Anna",
    }

    with patch("cogs.tropical._fetch_nhc_product", AsyncMock(return_value=parsed)), patch(
        "cogs.tropical.get_state", AsyncMock(return_value=None)
    ):
        await cog.post_tropical_product("202607220600-KNHC-WTNT31-TCPAT1", "", "ADVISORY")

    channel.send.assert_awaited_once()
    main_embed = channel.send.call_args[1]["embed"]
    assert "FULL RAW PRODUCT TEXT" not in (main_embed.description or "")

    main_msg.create_thread.assert_awaited_once()
    assert thread.send.await_count == 2
    second_thread_embed = thread.send.await_args_list[1].kwargs["embed"]
    assert "FULL RAW PRODUCT TEXT" in second_thread_embed.description
