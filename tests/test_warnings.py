"""Unit tests for cogs.warnings — VTEC and LAT...LON polygon parsers,
narrative extraction, and the iembot fast-path entry point."""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs.warnings import (
    WarningsCog,
    _extract_narrative,
    get_warning_style,
    parse_vtec,
    parse_warning_polygon,
)


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
    v = parse_vtec(SAMPLE_RAW)
    assert v is not None
    assert v["action"] == "NEW"
    assert v["office"] == "KOUN"
    assert v["phenom"] == "SV"
    assert v["sig"] == "W"
    assert v["etn"] == "0042"
    assert v["vtec_id"] == "KOUN.SV.W.0042"


def test_parse_vtec_handles_continue_and_cancel_actions():
    # CON (continue)
    con = SAMPLE_RAW.replace("/O.NEW.", "/O.CON.")
    v = parse_vtec(con)
    assert v["action"] == "CON"

    # CAN (cancel)
    can = SAMPLE_RAW.replace("/O.NEW.", "/O.CAN.")
    v = parse_vtec(can)
    assert v["action"] == "CAN"


def test_parse_vtec_finds_first_in_multiline_product():
    """VTEC might be buried in the middle/end of the product; the regex
    must be multiline-aware."""
    v = parse_vtec(SAMPLE_RAW)
    assert v["vtec_id"] == "KOUN.SV.W.0042"


def test_parse_vtec_returns_none_for_garbage():
    assert parse_vtec("") is None
    assert parse_vtec("NO VTEC HERE") is None


# ── parse_warning_polygon ───────────────────────────────────────────────────


def test_parse_polygon_basic():
    coords = parse_warning_polygon(SAMPLE_RAW)
    assert coords is not None
    # Four pairs in SAMPLE_RAW
    assert len(coords) == 4
    # Flipped from US convention (lat first, lon negative)
    assert coords[0] == (35.28, -97.56)
    assert coords[3] == (34.93, -97.56)


def test_parse_polygon_multiline():
    body = "LAT...LON 3528 9756 3528 9700\n          3493 9712 3493 9756"
    coords = parse_warning_polygon(body)
    assert len(coords) == 4
    assert coords[2] == (34.93, -97.12)


def test_parse_polygon_clips_implausible_coords():
    """Coords outside the US (e.g. lat=0 or lon=0) should be dropped."""
    body = "LAT...LON 3528 9756 0000 0000 3493 9756"
    coords = parse_warning_polygon(body)
    # The (0, 0) pair is dropped
    assert len(coords) == 2


def test_parse_polygon_returns_none_when_absent():
    assert parse_warning_polygon("No polygon block") is None
    assert parse_warning_polygon("") is None


def test_parse_polygon_odd_number_of_values_drops_orphan():
    """If the LAT...LON block has an odd number of integers, we pair
    what we can and drop the trailing orphan."""
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


def _make_cog(posted: dict | None = None) -> WarningsCog:
    """Build a WarningsCog with mocked bot/channel for unit testing the
    iembot path without touching Discord, the DB, or the network."""
    cog = WarningsCog.__new__(WarningsCog)
    cog.bot = MagicMock()
    cog.bot.state.is_primary = True
    cog.bot.state.posted_warnings = posted if posted is not None else {}
    cog.bot.state.active_warnings = {}
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
    monkeypatch.setattr(warnings_mod, "http_get_bytes", AsyncMock(return_value=(None, 404)))

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


@pytest.mark.asyncio
async def test_handle_cancellation_posts_new_message(monkeypatch):
    """Cancellation must post a NEW message in channel — original post is left untouched."""
    vtec_id = "KOUN.SV.W.0042"
    # area is now stored in posted_warnings at post time
    mapping = {vtec_id: {"message_id": 123, "channel_id": 456, "area": "Garfield, Noble"}}
    vtec = {"office": "KOUN", "phenom": "SV", "sig": "W", "etn": "0042", "vtec_id": vtec_id}
    cog = _make_cog(posted=mapping)
    cog.bot.state.active_warnings = {vtec_id: vtec}

    channel = cog.bot.get_channel.return_value

    monkeypatch.setattr("cogs.warnings.http_get_bytes", AsyncMock(return_value=(None, 404)))

    await cog._handle_cancellation(vtec_id, reason="Cancelled", vtec=vtec)

    # A NEW message must be sent
    channel.send.assert_called_once()
    sent_embed = channel.send.call_args.kwargs["embed"]
    assert "cancels" in sent_embed.description
    assert "Severe Thunderstorm Warning" in sent_embed.description
    assert "Garfield" in sent_embed.description
    assert sent_embed.color == discord.Color.dark_gray()


@pytest.mark.asyncio
async def test_post_warning_now_claims_key_before_send(monkeypatch):
    """Dedup key must be added to posted_warnings BEFORE the Discord
    send. Otherwise a concurrent NWS API poll could fire while the
    iembot path is still in-flight and double-post."""
    cog = _make_cog()

    import cogs.warnings as warnings_mod
    monkeypatch.setattr(warnings_mod, "add_posted_warning", AsyncMock())
    monkeypatch.setattr(warnings_mod, "prune_posted_warnings", AsyncMock())
    monkeypatch.setattr(warnings_mod, "http_get_bytes", AsyncMock(return_value=(None, 404)))

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


# ── get_warning_style ─────────────────────────────────────────────────────────

class TestGetWarningStyle:
    """NWS API sometimes returns explicit null for damage-threat params.
    Regression: .get("tornadoDamageThreat", []) returns None (not []) when
    the key is present with value null, causing 'in None' TypeError."""

    def test_null_tornado_damage_threat_does_not_raise(self):
        _, display_event, _, _ = get_warning_style(
            "Tornado Warning", "", params={"tornadoDamageThreat": None}
        )
        assert "Tornado Warning" in display_event

    def test_null_thunderstorm_damage_threat_does_not_raise(self):
        _, display_event, _, _ = get_warning_style(
            "Severe Thunderstorm Warning", "", params={"thunderstormDamageThreat": None}
        )
        assert "Severe Thunderstorm Warning" in display_event

    def test_both_null_does_not_raise(self):
        emoji, display_event, _, _ = get_warning_style(
            "Tornado Warning", "",
            params={"tornadoDamageThreat": None, "thunderstormDamageThreat": None},
        )
        assert emoji and display_event  # just shouldn't raise

    def test_catastrophic_tornado_threat_detected(self):
        emoji, display_event, color, footer_id = get_warning_style(
            "Tornado Warning", "",
            params={"tornadoDamageThreat": "CATASTROPHIC"},
        )
        assert "Tornado Emergency" in display_event
        assert emoji == "🚨🚨"
        assert footer_id == "EMERG"

    def test_destructive_thunderstorm_threat_detected(self):
        emoji, display_event, _, footer_id = get_warning_style(
            "Severe Thunderstorm Warning", "",
            params={"thunderstormDamageThreat": "DESTRUCTIVE"},
        )
        assert "DESTRUCTIVE" in display_event
        assert footer_id == "EWX"


def test_build_concise_warning_text_updates_format():
    """Verify that is_update=True produces the detailed 'updates' format."""
    from cogs.warnings import build_concise_warning_text
    
    vtec = {
        "action": "CON",
        "office": "KJAN",
        "phenom": "SV",
        "sig": "W",
        "etn": "0001",
        "start": "260429T2200Z",
        "end": "260429T2300Z",
        "vtec_id": "KJAN.SV.W.0001"
    }
    
    # Previous area: Clarke, Jasper, Jones
    # Current area: Jasper, Jones (Clarke cancelled)
    prev_area = "Clarke, Jasper, Jones"
    feature = {
        "properties": {
            "areaDesc": "Jasper, Jones",
            "parameters": {
                "maxWindGust": ["60 MPH"]
            }
        }
    }
    
    text = build_concise_warning_text(
        "Severe Thunderstorm Warning",
        vtec,
        feature=feature,
        is_update=True,
        prev_area=prev_area
    )
    
    assert "updates Severe Thunderstorm Warning" in text
    assert "(**cancels** Clarke, **continues** Jasper, Jones)" in text
    assert "till 23:00Z." in text
    assert text.endswith("]") # unix timestamp tag
