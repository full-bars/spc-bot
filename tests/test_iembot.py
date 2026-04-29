"""
Tests for cogs/iembot.py — seqnum persistence, feed filtering, and text parsing.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from cogs.iembot import (
    IEMBotCog,
    _parse_watch_text,
    _parse_md_text,
    _fetch_product_text,
)


# ── _parse_watch_text ─────────────────────────────────────────────────────────

SAMPLE_SEL = """
WWUS20 KWNS 281545
SEL5

SEVERE THUNDERSTORM WATCH NUMBER 42
NWS STORM PREDICTION CENTER NORMAN OK
1145 AM CDT TUE APR 28 2026

THE NWS STORM PREDICTION CENTER HAS ISSUED A

* SEVERE THUNDERSTORM WATCH FOR PORTIONS OF
  CENTRAL AND SOUTH CENTRAL KANSAS

* EFFECTIVE THIS TUESDAY AFTERNOON AND EVENING FROM 1145 AM UNTIL
  800 PM CDT.

* PRIMARY THREATS INCLUDE
  LARGE HAIL LIKELY WITH ISOLATED VERY LARGE HAIL EVENTS TO 3 INCHES
  IN DIAMETER POSSIBLE
  SCATTERED DAMAGING WINDS LIKELY WITH ISOLATED SIGNIFICANT GUSTS TO
  75 MPH POSSIBLE

SUMMARY...SUPERCELL THUNDERSTORMS ARE EXPECTED TO DEVELOP DURING THE
AFTERNOON HOURS.

DISCUSSION...THIS IS A DISCUSSION LINE.
"""


def test_parse_watch_text_extracts_areas():
    result = _parse_watch_text(SAMPLE_SEL)
    assert result is not None
    assert "Areas" in result


def test_parse_watch_text_extracts_time():
    result = _parse_watch_text(SAMPLE_SEL)
    assert result is not None
    assert "Time" in result


def test_parse_watch_text_extracts_threats():
    result = _parse_watch_text(SAMPLE_SEL)
    assert result is not None
    assert "Threats" in result


def test_parse_watch_text_returns_none_for_garbage():
    assert _parse_watch_text("not a watch product at all") is None


def test_parse_watch_text_returns_none_for_empty():
    assert _parse_watch_text("") is None


# ── _parse_md_text ────────────────────────────────────────────────────────────

SAMPLE_MCD = """
ACUS11 KWNS 281200
SWOMCD

SPC MCD 281200

AREAS AFFECTED...PORTIONS OF OKLAHOMA AND KANSAS

CONCERNING...TORNADO...HAIL...WIND

VALID 281200Z - 281800Z

PROBABILITY OF WATCH ISSUANCE...40 PERCENT

SUMMARY...THUNDERSTORMS WILL DEVELOP ALONG A DRYLINE.

DISCUSSION...ONGOING CONVECTION IS CONSOLIDATING.
"""


def test_parse_md_text_extracts_concerning_line():
    result = _parse_md_text(SAMPLE_MCD)
    assert result is not None
    assert "TORNADO" in result.upper()


def test_parse_md_text_falls_back_to_first_lines():
    plain = "Line one content.\nLine two content.\nLine three content.\n"
    result = _parse_md_text(plain)
    assert result is not None
    assert len(result) > 0


def test_parse_md_text_returns_none_for_empty():
    assert _parse_md_text("") is None


# ── _fetch_product_text ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_product_text_returns_text_on_200():
    with patch("cogs.iembot.http_get_bytes", AsyncMock(return_value=(b"product text", 200))):
        result = await _fetch_product_text("202604281200-KWNS-SWOMCD")
    assert result == "product text"


@pytest.mark.asyncio
async def test_fetch_product_text_returns_none_on_non_200():
    with patch("cogs.iembot.http_get_bytes", AsyncMock(return_value=(b"error", 404))):
        result = await _fetch_product_text("202604281200-KWNS-SWOMCD")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_product_text_returns_none_when_not_found_message():
    with patch("cogs.iembot.http_get_bytes", AsyncMock(return_value=(b"product not found", 200))):
        result = await _fetch_product_text("202604281200-KWNS-SWOMCD")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_product_text_returns_none_on_no_content():
    with patch("cogs.iembot.http_get_bytes", AsyncMock(return_value=(None, 200))):
        result = await _fetch_product_text("202604281200-KWNS-SWOMCD")
    assert result is None


# ── poll_iembot_feed: seqnum persistence ─────────────────────────────────────

def _make_bot(seqnum=0, is_primary=True):
    bot = MagicMock()
    bot.state.is_primary = is_primary
    bot.state.iembot_last_seqnum = seqnum
    bot.wait_until_ready = AsyncMock()
    bot.cogs = {}
    return bot


@pytest.mark.asyncio
async def test_seqnum_loaded_from_state_store_on_first_call():
    bot = _make_bot(seqnum=0)
    feed_data = json.dumps({"messages": []}).encode()

    with patch("cogs.iembot.get_state", AsyncMock(return_value="12345")), \
         patch("cogs.iembot.http_get_bytes", AsyncMock(return_value=(feed_data, 200))), \
         patch("cogs.iembot.set_state", AsyncMock()):
        cog = IEMBotCog(bot)
        await cog.poll_iembot_feed()

    assert bot.state.iembot_last_seqnum == 12345


@pytest.mark.asyncio
async def test_seqnum_loaded_only_once():
    bot = _make_bot(seqnum=0)
    feed_data = json.dumps({"messages": []}).encode()

    with patch("cogs.iembot.get_state", AsyncMock(return_value="100")) as mock_get, \
         patch("cogs.iembot.http_get_bytes", AsyncMock(return_value=(feed_data, 200))), \
         patch("cogs.iembot.set_state", AsyncMock()):
        cog = IEMBotCog(bot)
        await cog.poll_iembot_feed()
        await cog.poll_iembot_feed()

    assert mock_get.call_count == 1


@pytest.mark.asyncio
async def test_seqnum_persisted_after_new_messages():
    bot = _make_bot(seqnum=100)
    messages = [
        {"seqnum": 101, "product_id": "202604281200-UNKNOWN-NOOP"},
        {"seqnum": 103, "product_id": "202604281200-UNKNOWN-NOOP"},
    ]
    feed_data = json.dumps({"messages": messages}).encode()

    with patch("cogs.iembot.get_state", AsyncMock(return_value=None)), \
         patch("cogs.iembot.http_get_bytes", AsyncMock(return_value=(feed_data, 200))), \
         patch("cogs.iembot.set_state", AsyncMock()) as mock_set:
        cog = IEMBotCog(bot)
        await cog.poll_iembot_feed()

    assert bot.state.iembot_last_seqnum == 103
    mock_set.assert_called_once_with("iembot_last_seqnum", "103")


@pytest.mark.asyncio
async def test_seqnum_not_updated_when_no_new_messages():
    bot = _make_bot(seqnum=200)
    feed_data = json.dumps({"messages": []}).encode()

    with patch("cogs.iembot.get_state", AsyncMock(return_value=None)), \
         patch("cogs.iembot.http_get_bytes", AsyncMock(return_value=(feed_data, 200))), \
         patch("cogs.iembot.set_state", AsyncMock()) as mock_set:
        cog = IEMBotCog(bot)
        await cog.poll_iembot_feed()

    mock_set.assert_not_called()
    assert bot.state.iembot_last_seqnum == 200


# ── poll_iembot_feed: message filtering ──────────────────────────────────────

@pytest.mark.asyncio
async def test_sel_product_dispatches_handle_watch():
    bot = _make_bot(seqnum=0)
    messages = [{"seqnum": 1, "product_id": "202604281200-KWNS-WWUS20-SEL5"}]
    feed_data = json.dumps({"messages": messages}).encode()

    with patch("cogs.iembot.get_state", AsyncMock(return_value=None)), \
         patch("cogs.iembot.http_get_bytes", AsyncMock(return_value=(feed_data, 200))), \
         patch("cogs.iembot.set_state", AsyncMock()):
        cog = IEMBotCog(bot)
        cog._handle_watch = AsyncMock()
        with patch("cogs.iembot.asyncio.create_task") as mock_task:
            await cog.poll_iembot_feed()
            # Verify a task was created (watch handler dispatched)
            assert mock_task.called


@pytest.mark.asyncio
async def test_swomcd_product_dispatches_handle_md():
    bot = _make_bot(seqnum=0)
    messages = [{"seqnum": 1, "product_id": "202604281200-KWNS-ACUS11-SWOMCD"}]
    feed_data = json.dumps({"messages": messages}).encode()

    with patch("cogs.iembot.get_state", AsyncMock(return_value=None)), \
         patch("cogs.iembot.http_get_bytes", AsyncMock(return_value=(feed_data, 200))), \
         patch("cogs.iembot.set_state", AsyncMock()):
        cog = IEMBotCog(bot)
        with patch("cogs.iembot.asyncio.create_task") as mock_task:
            await cog.poll_iembot_feed()
            assert mock_task.called


@pytest.mark.asyncio
async def test_unknown_product_dispatches_nothing():
    bot = _make_bot(seqnum=0)
    messages = [{"seqnum": 1, "product_id": "202604281200-KWNS-NWUS53-LSRICT"}]
    feed_data = json.dumps({"messages": messages}).encode()

    with patch("cogs.iembot.get_state", AsyncMock(return_value=None)), \
         patch("cogs.iembot.http_get_bytes", AsyncMock(return_value=(feed_data, 200))), \
         patch("cogs.iembot.set_state", AsyncMock()):
        cog = IEMBotCog(bot)
        with patch("cogs.iembot.asyncio.create_task") as mock_task:
            await cog.poll_iembot_feed()
            # LSR product is not handled by spcchat feed
            assert not mock_task.called


@pytest.mark.asyncio
async def test_old_seqnum_messages_skipped():
    bot = _make_bot(seqnum=500)
    messages = [
        {"seqnum": 498, "product_id": "202604281200-KWNS-WWUS20-SEL5"},
        {"seqnum": 499, "product_id": "202604281200-KWNS-ACUS11-SWOMCD"},
    ]
    feed_data = json.dumps({"messages": messages}).encode()

    with patch("cogs.iembot.get_state", AsyncMock(return_value=None)), \
         patch("cogs.iembot.http_get_bytes", AsyncMock(return_value=(feed_data, 200))), \
         patch("cogs.iembot.set_state", AsyncMock()):
        cog = IEMBotCog(bot)
        with patch("cogs.iembot.asyncio.create_task") as mock_task:
            await cog.poll_iembot_feed()
            assert not mock_task.called


@pytest.mark.asyncio
async def test_standby_skips_poll():
    bot = _make_bot(is_primary=False)

    with patch("cogs.iembot.http_get_bytes", AsyncMock()) as mock_http:
        cog = IEMBotCog(bot)
        await cog.poll_iembot_feed()
        mock_http.assert_not_called()


@pytest.mark.asyncio
async def test_http_failure_does_not_raise():
    bot = _make_bot(seqnum=0)

    with patch("cogs.iembot.get_state", AsyncMock(return_value=None)), \
         patch("cogs.iembot.http_get_bytes", AsyncMock(return_value=(None, 503))):
        cog = IEMBotCog(bot)
        await cog.poll_iembot_feed()  # should not raise


# ── _handle_watch / _handle_md ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_watch_caches_text_and_signals_cog():
    bot = _make_bot()
    watches_cog = MagicMock()
    watches_cog.post_watch_now = AsyncMock()
    bot.cogs = {"WatchesCog": watches_cog}

    raw = "Tornado Watch Number 0042\nWatch for portions of KANSAS\n"
    with patch("cogs.iembot._fetch_product_text", AsyncMock(return_value=raw)), \
         patch("cogs.iembot.set_product_cache", AsyncMock()), \
         patch("cogs.iembot.asyncio.create_task") as mock_task:
        cog = IEMBotCog(bot)
        await cog._handle_watch("202604281200-KWNS-WWUS20-SEL5")
        assert mock_task.called


@pytest.mark.asyncio
async def test_handle_watch_does_nothing_if_no_text():
    bot = _make_bot()
    with patch("cogs.iembot._fetch_product_text", AsyncMock(return_value=None)), \
         patch("cogs.iembot.asyncio.create_task") as mock_task:
        cog = IEMBotCog(bot)
        await cog._handle_watch("202604281200-KWNS-WWUS20-SEL5")
        mock_task.assert_not_called()


@pytest.mark.asyncio
async def test_handle_md_caches_text_and_signals_cog():
    bot = _make_bot()
    mesoscale_cog = MagicMock()
    mesoscale_cog.post_md_now = AsyncMock()
    bot.cogs = {"MesoscaleCog": mesoscale_cog}

    raw = "Mesoscale Discussion 0590\nConcerning tornado activity.\n"
    with patch("cogs.iembot._fetch_product_text", AsyncMock(return_value=raw)), \
         patch("cogs.iembot.set_product_cache", AsyncMock()), \
         patch("cogs.iembot.asyncio.create_task") as mock_task:
        cog = IEMBotCog(bot)
        await cog._handle_md("202604281200-KWNS-ACUS11-SWOMCD")
        assert mock_task.called


@pytest.mark.asyncio
async def test_handle_md_does_nothing_if_no_text():
    bot = _make_bot()
    with patch("cogs.iembot._fetch_product_text", AsyncMock(return_value=None)), \
         patch("cogs.iembot.asyncio.create_task") as mock_task:
        cog = IEMBotCog(bot)
        await cog._handle_md("202604281200-KWNS-ACUS11-SWOMCD")
        mock_task.assert_not_called()
