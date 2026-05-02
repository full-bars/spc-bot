"""Tests for cogs/reports.py — LSR parsing, PNS damage survey handling,
tornado deduplication, and _check_for_surveys DAT integration."""

import json
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from cogs.reports import ReportsCog


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_cog(posted_reports=None):
    """ReportsCog with mocked bot — does NOT start poll_lsrs."""
    cog = ReportsCog.__new__(ReportsCog)
    cog.bot = MagicMock()
    cog.bot.wait_until_ready = AsyncMock()
    cog.bot.state.is_primary = True
    cog.bot.state.posted_reports = set(posted_reports or [])
    cog.posted_surveys = set()
    cog._surveys_loaded = True  # skip DB load in tests
    channel = AsyncMock()
    cog.bot.get_channel.return_value = channel
    return cog, channel


# LSR where header and report are NOT separated by a blank line so the
# whole product stays as one section and passes the "LOCAL STORM REPORT" filter.
SAMPLE_LSR = """\
WOUS54 KOUN 292130
LSROUN

             PRELIMINARY LOCAL STORM REPORT
             NATIONAL WEATHER SERVICE NORMAN OK
             416 PM CDT TUE APR 29 2026
0131 AM     TORNADO              CHICKASHA              35.05N  97.94W
04/29/2026                    GRADY CO            OK   PUBLIC

REMARKS... EF-1 TORNADO CONFIRMED BY DAMAGE SURVEY.

$$
"""

ASOS_LSR = """\
WOUS54 KOUN 292130
LSROUN

             PRELIMINARY LOCAL STORM REPORT
             NATIONAL WEATHER SERVICE NORMAN OK
             416 PM CDT TUE APR 29 2026
0131 AM     TSTM WND DMG         WILL ROGERS AP         35.39N  97.60W
04/29/2026                    OKLAHOMA CO          OK   ASOS OKCI

REMARKS... PK WND 27045/2220. AUTOMATED OBSERVATION.

$$
"""

SAMPLE_PNS = """\
NOUS44 KOUN 292200
PNSOUN

...NWS DAMAGE SURVEY FOR 04/29/2026 TORNADO EVENT...

Rating: EF-2
Estimated Peak Wind: 120 MPH
Path Length: 4.5 miles
Path Width: 400 yards

START LAT/LON: 35.05 / -97.94

SUMMARY: AN EF-2 TORNADO CAUSED SIGNIFICANT STRUCTURAL DAMAGE NEAR CHICKASHA OK.

$$
"""


# ── _handle_lsr ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_lsr_posts_embed_for_non_tornado():
    """Non-tornado LSR events are posted unconditionally."""
    cog, channel = _make_cog()
    raw = SAMPLE_LSR.replace("TORNADO", "TSTM WND DMG")

    with patch("utils.state_store.find_matching_tornado", AsyncMock(return_value=None)):
        await cog._handle_lsr("202604292130-KOUN-LSROUN", raw)

    channel.send.assert_called_once()


@pytest.mark.asyncio
async def test_handle_lsr_skips_discord_post_for_matched_tornado():
    """Tornado LSR is suppressed from Discord when a matching warning event exists."""
    cog, channel = _make_cog()

    with patch("utils.state_store.find_matching_tornado",
               AsyncMock(return_value=("NWS:WARN:KOUN.TO.W.0001", "KOUN.TO.W.0001"))), \
         patch("utils.db.get_posted_warning_timestamp", AsyncMock(return_value=None)), \
         patch("cogs.reports.add_significant_event", AsyncMock()):
        await cog._handle_lsr("202604292130-KOUN-LSROUN", SAMPLE_LSR)

    channel.send.assert_not_called()


@pytest.mark.asyncio
async def test_handle_lsr_calculates_lead_time():
    """Lead time is calculated and stored when a matching warning timestamp exists."""
    cog, channel = _make_cog()

    captured_kwargs = {}

    async def _capture(**kwargs):
        captured_kwargs.update(kwargs)

    # warning issued at t=100, LSR arrives at t=1000
    # Patch it in both places it might be imported from/to
    with patch("utils.state_store.find_matching_tornado",
               AsyncMock(return_value=("ev1", "KOUN.TO.W.0001"))), \
         patch("utils.db.get_posted_warning_timestamp",
               AsyncMock(return_value=100.0)), \
         patch("cogs.reports.add_significant_event",
               AsyncMock(side_effect=_capture)):
        # Force lsr_ts to 480 via product_id timestamp
        await cog._handle_lsr("202604292130-KOUN-LSROUN", SAMPLE_LSR)

    assert "lead_time" in captured_kwargs, f"Captured keys: {list(captured_kwargs.keys())}"
    assert captured_kwargs["lead_time"] > 0


@pytest.mark.asyncio
async def test_handle_lsr_no_channel_returns_early():
    """Missing Discord channel is handled without raising."""
    cog, _ = _make_cog()
    cog.bot.get_channel.return_value = None

    await cog._handle_lsr("pid", SAMPLE_LSR)  # must not raise


@pytest.mark.asyncio
async def test_handle_lsr_asos_peak_wind_extraction():
    """ASOS-sourced LSR surfaces peak wind from the PK WND remark."""
    cog, channel = _make_cog()

    with patch("utils.state_store.find_matching_tornado", AsyncMock(return_value=None)):
        await cog._handle_lsr("202604292130-KOUN-LSROUN", ASOS_LSR)

    channel.send.assert_called_once()
    embed: discord.Embed = channel.send.call_args.kwargs["embed"]
    assert "45kt" in embed.description
    assert "22:20Z" in embed.description


@pytest.mark.asyncio
async def test_handle_lsr_skips_product_with_no_parseable_reports():
    """Product that doesn't match the expected LSR line format produces no Discord posts."""
    cog, channel = _make_cog()

    junk = "WOUS54 KOUN 292130\nLSROUN\n\nGARBAGE CONTENT\n$$\n"
    await cog._handle_lsr("pid", junk)

    channel.send.assert_not_called()


# ── post_report_now dedup ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_report_now_dedup_skips_already_posted():
    """product_id already in posted_reports is dropped before any processing."""
    cog, channel = _make_cog(posted_reports={"pid-already"})

    await cog.post_report_now("pid-already", SAMPLE_PNS, "PNS")

    channel.send.assert_not_called()


# ── _handle_pns ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_pns_ignores_non_survey():
    """PNS without 'DAMAGE SURVEY' is silently dropped."""
    cog, channel = _make_cog()

    await cog._handle_pns("pid", "PUBLIC INFORMATION STATEMENT\n\nSome routine text.")
    channel.send.assert_not_called()


@pytest.mark.asyncio
async def test_handle_pns_posts_and_records():
    """Valid damage survey PNS is posted and added to posted_reports."""
    cog, channel = _make_cog()

    with patch("cogs.reports.add_posted_report", AsyncMock()), \
         patch("cogs.reports.prune_posted_reports", AsyncMock()), \
         patch("cogs.reports.add_significant_event", AsyncMock()), \
         patch("utils.state_store.find_matching_tornado", AsyncMock(return_value=None)), \
         patch.object(cog, "_check_for_surveys", AsyncMock()):
        await cog._handle_pns("202604292200-KOUN-PNSOUN", SAMPLE_PNS)

    channel.send.assert_called_once()
    assert "202604292200-KOUN-PNSOUN" in cog.bot.state.posted_reports


@pytest.mark.asyncio
async def test_handle_pns_extracts_max_ef_rating():
    """Highest EF rating across a multi-tornado PNS is shown in the embed."""
    cog, channel = _make_cog()

    multi_pns = """\
...NWS DAMAGE SURVEY FOR 04/29/2026 TORNADO EVENT...

Rating: EF1
Estimated Peak Wind: 100 mph

Rating: EF3
Estimated Peak Wind: 145 mph

Rating: EF0
Estimated Peak Wind: 65 mph

SUMMARY: Three tornadoes confirmed near Example City OK.
$$
"""
    with patch("cogs.reports.add_posted_report", AsyncMock()), \
         patch("cogs.reports.prune_posted_reports", AsyncMock()), \
         patch("cogs.reports.add_significant_event", AsyncMock()), \
         patch("utils.state_store.find_matching_tornado", AsyncMock(return_value=None)), \
         patch.object(cog, "_check_for_surveys", AsyncMock()):
        await cog._handle_pns("202604292200-KOUN-PNSOUN", multi_pns)

    embed: discord.Embed = channel.send.call_args.kwargs["embed"]
    assert "EF3" in embed.description


@pytest.mark.asyncio
async def test_handle_pns_parses_numerical_date_for_survey_check():
    """MM/DD/YYYY date triggers _check_for_surveys with the correct ISO date."""
    cog, channel = _make_cog()

    mock_check = AsyncMock()

    with patch("cogs.reports.add_posted_report", AsyncMock()), \
         patch("cogs.reports.prune_posted_reports", AsyncMock()), \
         patch("cogs.reports.add_significant_event", AsyncMock()), \
         patch("utils.state_store.find_matching_tornado", AsyncMock(return_value=None)), \
         patch.object(cog, "_check_for_surveys", mock_check):
        await cog._handle_pns("202604292200-KOUN-PNSOUN", SAMPLE_PNS)
        # Wait a tiny bit for the background task to be created
        await asyncio.sleep(0.01)

    mock_check.assert_called_once_with("2026-04-29")


@pytest.mark.asyncio
async def test_handle_pns_no_channel_returns_early():
    """Missing Discord channel is handled without raising."""
    cog, _ = _make_cog()
    cog.bot.get_channel.return_value = None

    await cog._handle_pns("pid", SAMPLE_PNS)  # must not raise


# ── _check_for_surveys ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_for_surveys_posts_track_embed():
    """Valid DAT metadata triggers a tornado-track embed post."""
    cog, channel = _make_cog()

    meta = {
        "arguments": [
            {"id": "datglobalid", "options": {"{GUID-XYZ}": "OUN EF2 Chickasha"}}
        ]
    }
    with patch("cogs.reports.http_get_bytes",
               AsyncMock(return_value=(json.dumps(meta).encode(), 200))), \
         patch("cogs.reports.add_posted_survey", AsyncMock()), \
         patch("cogs.reports.prune_posted_surveys", AsyncMock()), \
         patch("utils.events_db.link_dat_guid_to_tornado", AsyncMock()):
        await cog._check_for_surveys("2026-04-29")

    channel.send.assert_called_once()
    embed: discord.Embed = channel.send.call_args.kwargs["embed"]
    assert "{GUID-XYZ}" in embed.image.url


@pytest.mark.asyncio
async def test_check_for_surveys_dedup_skips_known_guid():
    """Already-posted survey GUID is not re-posted."""
    cog, channel = _make_cog()
    cog.posted_surveys = {"{KNOWN-GUID}"}

    meta = {
        "arguments": [
            {"id": "datglobalid", "options": {"{KNOWN-GUID}": "OUN EF1 Test"}}
        ]
    }
    with patch("cogs.reports.http_get_bytes",
               AsyncMock(return_value=(json.dumps(meta).encode(), 200))):
        await cog._check_for_surveys("2026-04-29")

    channel.send.assert_not_called()


@pytest.mark.asyncio
async def test_check_for_surveys_handles_api_failure():
    """HTTP failure from IEM Autoplot 253 does not raise."""
    cog, channel = _make_cog()

    with patch("cogs.reports.http_get_bytes", AsyncMock(return_value=(None, 503))):
        await cog._check_for_surveys("2026-04-29")  # must not raise

    channel.send.assert_not_called()


@pytest.mark.asyncio
async def test_check_for_surveys_no_datglobalid_arg_returns_early():
    """API response with no datglobalid argument produces no posts."""
    cog, channel = _make_cog()

    meta = {"arguments": [{"id": "somethingelse", "options": {}}]}
    with patch("cogs.reports.http_get_bytes",
               AsyncMock(return_value=(json.dumps(meta).encode(), 200))):
        await cog._check_for_surveys("2026-04-29")

    channel.send.assert_not_called()

@pytest.mark.asyncio
async def test_check_for_surveys_falls_back_to_local_render():
    """If IEM Autoplot returns 404, bot renders and posts a local map from DAT geometry."""
    cog, channel = _make_cog()

    meta = {
        "arguments": [
            {"id": "datglobalid", "options": {"{GUID-LOCAL}": "OUN EF2 Test"}}
        ]
    }
    
    # IEM meta call succeeds (200), but IEM image call fails (404)
    with patch("cogs.reports.http_get_bytes", side_effect=[
        (json.dumps(meta).encode(), 200), # meta call
        (None, 404)                      # image call
    ]), \
    patch("utils.dat_api.fetch_dat_track_geometry", AsyncMock(return_value=[[(35.0, -97.0), (35.1, -97.1)]])), \
    patch("utils.map_utils.render_tornado_track", MagicMock()), \
    patch("os.path.exists", return_value=True), \
    patch("discord.File", return_value=MagicMock(spec=discord.File, filename="track_{GUID-LOCAL}.png")), \
    patch("cogs.reports.add_posted_survey", AsyncMock()), \
    patch("cogs.reports.prune_posted_surveys", AsyncMock()), \
    patch("utils.events_db.link_dat_guid_to_tornado", AsyncMock()):

        await cog._check_for_surveys("2026-04-29")

    channel.send.assert_called_once()
    kwargs = channel.send.call_args.kwargs
    embed = kwargs["embed"]
    assert "Local DAT Render" in embed.footer.text
    # Verify file attachment
    assert "file" in kwargs
