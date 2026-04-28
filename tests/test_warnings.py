"""Unit tests for cogs.warnings — VTEC and LAT...LON polygon parsers,
narrative extraction, and the iembot fast-path entry point."""

from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from cogs.warnings import (
    WarningsCog,
    _extract_narrative,
    _polygon_from_nws_feature,
    is_severe_sps,
    parse_vtec,
    parse_warning_polygon,
    radar_loop_url,
    resolve_radar_for_polygon,
)
...
def test_is_severe_sps_detects_convective_tags():
    assert is_severe_sps("STRONG THUNDERSTORM WILL IMPACT...") is True
    assert is_severe_sps("HAIL...0.88IN") is True
    assert is_severe_sps("WIND...50MPH") is True
    assert is_severe_sps("Dense fog advisory") is False
    assert is_severe_sps("") is False
from utils import nexrad


# Sample raw VTEC product — typical Severe Thunderstorm Warning shape.
SAMPLE_RAW = """\
WUUS54 KOUN 272018
SVRHGX

BULLETIN - IMMEDIATE BROADCAST REQUESTED
Severe Thunderstorm Warning
National Weather Service Norman OK
318 PM CDT Mon Apr 27 2026

The National Weather Service in Norman has issued a

* Severe Thunderstorm Warning for...
  Northern Cleveland County in central Oklahoma...
  Northwestern Pottawatomie County in central Oklahoma...

* Until 415 PM CDT.

* At 318 PM CDT, severe thunderstorms were located along a line
  extending from 7 miles west of Norman to 7 miles south of Shawnee,
  moving east at 35 mph.

  HAZARD...60 mph wind gusts and quarter size hail.

  SOURCE...Radar indicated.

  IMPACT...Hail damage to vehicles is expected. Expect wind damage to
  roofs, siding, and trees.

LAT...LON 3528 9756 3528 9700 3493 9712 3493 9756
TIME...MOT...LOC 2018Z 270DEG 30KT 3514 9750

HAIL...1.00IN
WIND...60MPH

$$

ABC
/O.NEW.KOUN.SV.W.0042.260427T2018Z-260427T2115Z/
"""


# ── parse_vtec ───────────────────────────────────────────────────────────────


def test_parse_vtec_new_issuance():
    """The standard NEW VTEC for a Tornado Warning unpacks into action,
    office, phenomenon, significance, ETN, and a stable vtec_id."""
    text = "/O.NEW.KOUN.TO.W.0042.260427T2018Z-260427T2100Z/"
    parsed = parse_vtec(text)
    assert parsed is not None
    assert parsed["action"] == "NEW"
    assert parsed["office"] == "KOUN"
    assert parsed["phenom"] == "TO"
    assert parsed["sig"] == "W"
    assert parsed["etn"] == "0042"
    assert parsed["vtec_id"] == "KOUN.TO.W.0042"


def test_parse_vtec_handles_continue_and_cancel_actions():
    """SVS updates carry CON; cancellations carry CAN. Same dedup key
    as the original issuance because the ETN doesn't change."""
    new = parse_vtec("/O.NEW.KOUN.TO.W.0042.260427T2018Z-260427T2100Z/")
    con = parse_vtec("/O.CON.KOUN.TO.W.0042.260427T2030Z-260427T2100Z/")
    can = parse_vtec("/O.CAN.KOUN.TO.W.0042.260427T2050Z-260427T2100Z/")
    assert new["vtec_id"] == con["vtec_id"] == can["vtec_id"]
    assert con["action"] == "CON"
    assert can["action"] == "CAN"


def test_parse_vtec_finds_first_in_multiline_product():
    """A real product has the VTEC line embedded in plain-text headers;
    the parser must find it without a strict line-anchor."""
    body = (
        "WFUS54 KOUN 272018\n"
        "TORHGX\n"
        "/O.NEW.KOUN.TO.W.0042.260427T2018Z-260427T2100Z/\n"
        "/O.NEW.KOUN.SV.A.0162.000000T0000Z-260428T0500Z/\n"
        "BULLETIN - IMMEDIATE BROADCAST REQUESTED\n"
    )
    parsed = parse_vtec(body)
    # First match wins; the second VTEC line is a watch reference and
    # is irrelevant to which warning we're parsing.
    assert parsed is not None
    assert parsed["action"] == "NEW"
    assert parsed["phenom"] == "TO"


def test_parse_vtec_returns_none_for_garbage():
    assert parse_vtec("") is None
    assert parse_vtec("not a vtec string") is None
    assert parse_vtec(None) is None
    # Almost-but-not-quite — wrong field count:
    assert parse_vtec("/O.NEW.KOUN.TO.W.42.260427T2018Z-260427T2100Z/") is None


# ── parse_warning_polygon ───────────────────────────────────────────────────


def test_parse_polygon_basic():
    """Standard 4-vertex polygon, single line."""
    body = (
        "TIME...MOT...LOC ...\n"
        "LAT...LON 4119 8902 4135 8845 4187 8862 4173 8918\n"
        "$$\n"
    )
    poly = parse_warning_polygon(body)
    assert poly is not None
    assert len(poly) == 4
    # First vertex: 4119 / 100 = 41.19 N, -89.02 W
    assert poly[0] == (41.19, -89.02)
    assert poly[3] == (41.73, -89.18)


def test_parse_polygon_multiline():
    """Real products often wrap the LAT...LON block across multiple
    lines. The parser must keep collecting until a section break."""
    body = (
        "LAT...LON 4119 8902 4135 8845\n"
        "      4187 8862 4173 8918\n"
        "TIME...MOT...LOC 0030Z 200DEG ...\n"
    )
    poly = parse_warning_polygon(body)
    assert poly is not None
    assert len(poly) == 4
    assert poly[2] == (41.87, -88.62)


def test_parse_polygon_clips_implausible_coords():
    """If the LAT...LON block somehow contains nonsensical values
    (corrupt product, parser confusion), out-of-CONUS pairs are
    dropped silently rather than producing absurd geometry."""
    body = "LAT...LON 4119 8902 9999 9999 4135 8845\n$$\n"
    poly = parse_warning_polygon(body)
    assert poly == [(41.19, -89.02), (41.35, -88.45)]


def test_parse_polygon_returns_none_when_absent():
    assert parse_warning_polygon("BULLETIN ONLY, NO COORDS") is None
    assert parse_warning_polygon("") is None
    assert parse_warning_polygon(None) is None


def test_parse_polygon_odd_number_of_values_drops_orphan():
    """If someone hands us 5 numbers (lat lat lat lat lat), the last
    unmatched value is silently dropped — we never invent a coordinate."""
    body = "LAT...LON 4119 8902 4135 8845 4187\n$$\n"
    poly = parse_warning_polygon(body)
    assert poly == [(41.19, -89.02), (41.35, -88.45)]


# ── _extract_narrative ──────────────────────────────────────────────────────


def test_extract_narrative_drops_headers_and_footer():
    """The narrative starts at \"BULLETIN\" or \"The National Weather
    Service\", and ends before LAT...LON / ATTN / $$. Transmission
    metadata at the top and tag boilerplate at the bottom must be
    stripped — we want only the human-readable warning body."""
    narrative = _extract_narrative(SAMPLE_RAW)
    assert narrative is not None
    # Bulletin headers preserved (start of narrative)
    assert narrative.startswith("BULLETIN")
    # WMO header and AFOS PIL trimmed
    assert "WUUS54 KOUN" not in narrative
    assert "SVRHGX\n" not in narrative
    # Substantive content preserved
    assert "318 PM CDT" in narrative
    assert "Severe Thunderstorm Warning for..." in narrative
    assert "HAZARD...60 mph wind gusts" in narrative
    # Footer/tag boilerplate trimmed
    assert "LAT...LON" not in narrative
    assert "$$" not in narrative
    assert "/O.NEW." not in narrative


def test_extract_narrative_returns_none_for_empty():
    assert _extract_narrative(None) is None
    assert _extract_narrative("") is None


def test_extract_narrative_falls_back_when_no_bulletin_header():
    """Some products skip the BULLETIN line. The footer-stripping path
    still produces something useful even if we can't trim the header."""
    body = "Some narrative text\nMore narrative.\n\nLAT...LON 1 2 3 4\n$$"
    narrative = _extract_narrative(body)
    assert narrative is not None
    assert "Some narrative text" in narrative
    assert "LAT...LON" not in narrative


# ── post_warning_now (iembot fast-path) ──────────────────────────────────────


def _make_cog(posted: set | None = None) -> WarningsCog:
    """Build a WarningsCog with mocked bot/channel for unit testing the
    iembot path without touching Discord, the DB, or the network."""
    cog = WarningsCog.__new__(WarningsCog)
    cog.bot = MagicMock()
    cog.bot.state.is_primary = True
    cog.bot.state.posted_warnings = posted if posted is not None else {}
    channel = MagicMock()
    channel.send = AsyncMock()
    cog.bot.get_channel = MagicMock(return_value=channel)
    return cog


@pytest.mark.asyncio
async def test_post_warning_now_dedups_against_posted_set(monkeypatch):
    """If a vtec_id is already in posted_warnings, the iembot path is
    a no-op — this is what prevents the NWS API poll from
    double-posting after iembot's fast trigger."""
    cog = _make_cog(posted={"KOUN.SV.W.0042": {"message_id": 1, "channel_id": 2}})

    # Patch the persistence helpers to no-ops so the test never touches
    # the DB. add_posted_warning would also be skipped on the dedup
    # path anyway — the assertion is that channel.send was never called.
    import cogs.warnings as warnings_mod
    monkeypatch.setattr(warnings_mod, "add_posted_warning", AsyncMock())
    monkeypatch.setattr(warnings_mod, "prune_posted_warnings", AsyncMock())

    await cog.post_warning_now(
        "202604272018-KOUN-WUUS54-SVRHGX",
        SAMPLE_RAW,
        "Severe Thunderstorm Warning",
    )
    cog.bot.get_channel.return_value.send.assert_not_called()


@pytest.mark.asyncio
async def test_post_warning_now_skips_non_NEW_actions(monkeypatch):
    """SVS / CON / CAN updates arrive via the same iembot stream but
    aren't initial issuances — PR B only handles NEW. Updates land in
    PR D."""
    cog = _make_cog()

    import cogs.warnings as warnings_mod
    monkeypatch.setattr(warnings_mod, "add_posted_warning", AsyncMock())
    monkeypatch.setattr(warnings_mod, "prune_posted_warnings", AsyncMock())

    con_text = SAMPLE_RAW.replace(
        "/O.NEW.KOUN.SV.W.0042.", "/O.CON.KOUN.SV.W.0042."
    )
    await cog.post_warning_now(
        "202604272030-KOUN-WWUS54-SVSOUN",
        con_text,
        "Severe Thunderstorm Warning",
    )
    cog.bot.get_channel.return_value.send.assert_not_called()
    assert "KOUN.SV.W.0042" not in cog.bot.state.posted_warnings


# ── _polygon_from_nws_feature ───────────────────────────────────────────────


def test_polygon_from_nws_feature_extracts_polygon():
    """NWS API gives geometry.type='Polygon' with [lon, lat] tuples;
    our internal convention is (lat, lon)."""
    feature = {
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [-89.02, 41.19],
                [-88.45, 41.35],
                [-88.62, 41.87],
                [-89.18, 41.73],
                [-89.02, 41.19],
            ]],
        }
    }
    coords = _polygon_from_nws_feature(feature)
    assert coords is not None
    assert coords[0] == (41.19, -89.02)
    assert coords[2] == (41.87, -88.62)


def test_polygon_from_nws_feature_handles_multipolygon():
    feature = {
        "geometry": {
            "type": "MultiPolygon",
            "coordinates": [[
                [[-89.0, 41.0], [-88.0, 41.0], [-88.0, 42.0], [-89.0, 41.0]],
            ]],
        }
    }
    coords = _polygon_from_nws_feature(feature)
    assert coords is not None
    assert coords[0] == (41.0, -89.0)


def test_polygon_from_nws_feature_returns_none_for_unknown_geom():
    """A Point geometry isn't a polygon — radar lookup should fall
    back gracefully rather than guess."""
    assert _polygon_from_nws_feature({"geometry": {"type": "Point"}}) is None
    assert _polygon_from_nws_feature({}) is None


# ── radar_loop_url + resolve_radar_for_polygon ───────────────────────────────


def test_radar_loop_url_format():
    """Pin the URL pattern so the live NWS Ridge2 endpoint can be
    swapped in tests without a hidden type mismatch."""
    assert radar_loop_url("KTLX") == \
        "https://radar.weather.gov/ridge/standard/KTLX_loop.gif"


@pytest.mark.asyncio
async def test_resolve_radar_picks_nearest_site(monkeypatch):
    """Stub the NEXRAD site list with a tiny fixed set and confirm we
    pick the closest by haversine distance."""
    fake_sites = [
        ("KTLX", 35.33, -97.28),  # Norman, OK
        ("KFWS", 32.57, -97.30),  # Dallas/Ft Worth
        ("KIND", 39.71, -86.28),  # Indianapolis
    ]

    async def fake_list():
        return fake_sites

    nexrad.reset_cache_for_tests()
    with patch.object(nexrad, "get_nexrad_sites", side_effect=fake_list):
        # Polygon centered near Norman — should pick KTLX
        coords = [(35.4, -97.3), (35.4, -97.0), (35.6, -97.1)]
        icao, dist = await resolve_radar_for_polygon(coords)
    assert icao == "KTLX"
    assert dist < 30  # ~0–25 km from Norman


@pytest.mark.asyncio
async def test_resolve_radar_returns_none_for_empty_polygon():
    icao, dist = await resolve_radar_for_polygon(None)
    assert icao is None
    assert dist is None
    icao, dist = await resolve_radar_for_polygon([])
    assert icao is None


@pytest.mark.asyncio
async def test_resolve_radar_handles_empty_site_list(monkeypatch):
    """If the IEM fetch is down on cog_load, we serve the warning
    without radar rather than refusing to post."""
    nexrad.reset_cache_for_tests()
    with patch.object(nexrad, "get_nexrad_sites", AsyncMock(return_value=[])):
        coords = [(35.4, -97.3), (35.4, -97.0)]
        icao, dist = await resolve_radar_for_polygon(coords)
    assert icao is None


@pytest.mark.asyncio
async def test_handle_cancellation_edits_message(monkeypatch):
    """Marking a warning as cancelled should fetch the message and
    edit the embed with a strike-through description."""
    vtec_id = "KOUN.SV.W.0042"
    mapping = {vtec_id: {"message_id": 123, "channel_id": 456}}
    cog = _make_cog(posted=mapping)
    cog.bot.state.active_warnings = {vtec_id}

    mock_msg = AsyncMock()
    mock_msg.embeds = [discord.Embed(title="Storm", description="Heavy rain")]
    cog.bot.get_channel.return_value.fetch_message = AsyncMock(return_value=mock_msg)

    await cog._handle_cancellation(vtec_id, reason="Cancelled")

    # Verify message was fetched and edited
    cog.bot.get_channel.return_value.fetch_message.assert_called_with(123)
    mock_msg.edit.assert_called_once()
    
    # Verify embed was modified
    edited_embed = mock_msg.edit.call_args[1]["embeds"][0]
    assert "✅" in edited_embed.title
    assert "Cancelled" in edited_embed.title
    assert "~~Heavy rain~~" in edited_embed.description


@pytest.mark.asyncio
async def test_post_warning_now_claims_key_before_send(monkeypatch):
    """Dedup key must be added to posted_warnings BEFORE the Discord
    send. Otherwise a concurrent NWS API poll could fire while the
    iembot path is still in-flight and double-post."""
    cog = _make_cog()

    import cogs.warnings as warnings_mod
    monkeypatch.setattr(warnings_mod, "add_posted_warning", AsyncMock())
    monkeypatch.setattr(warnings_mod, "prune_posted_warnings", AsyncMock())

    # Stash the membership state observed at the moment of send.
    observed: list = []

    async def _record_send(*args, **kwargs):
        observed.append("KOUN.SV.W.0042" in cog.bot.state.posted_warnings)
        m = MagicMock()
        m.id = 123
        m.channel.id = 456
        return m

    cog.bot.get_channel.return_value.send.side_effect = _record_send

    await cog.post_warning_now(
        "202604272018-KOUN-WUUS54-SVRHGX",
        SAMPLE_RAW,
        "Severe Thunderstorm Warning",
    )
    assert observed == [True], "vtec_id must be claimed before send"
