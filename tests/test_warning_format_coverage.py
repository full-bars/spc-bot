"""Coverage round 8: warning_format pure helpers (style, severity, URLs, areas).

Pure-logic tests for cogs/warning_format.py (56% covered): the VTEC helpers,
warning style/severity/tornado-attribute classification, IEM autoplot and
VTEC URLs, area-with-state formatting, county-set canonicalization, and
narrative extraction.
"""

import time
from datetime import datetime, timezone

import discord

from cogs import warning_format as wf


# ── VTEC null-time sentinel ──────────────────────────────────────────────────


def test_is_null_vtec_time():
    assert wf._is_null_vtec_time("000000T0000Z") is True
    assert wf._is_null_vtec_time("260811T1200Z") is False
    assert wf._is_null_vtec_time("") is False


# ── Warning style ────────────────────────────────────────────────────────────


def test_get_warning_style_vtec_override():
    # NWS labels a CON as "Statement" but VTEC shows TO/W.
    emoji, name, _color, _footer = wf.get_warning_style(
        "Statement", "text", vtec={"phenom": "TO", "sig": "W"}
    )
    assert name == "Tornado Warning"
    assert emoji == "🌪️"


def test_get_warning_style_tornado_emergency():
    emoji, name, color, footer = wf.get_warning_style("Tornado Warning", "TORNADO EMERGENCY")
    assert emoji == "🚨🚨"
    assert name == "Tornado Emergency"
    assert footer == "EMERG"
    assert color == discord.Color.from_rgb(139, 0, 0)


def test_get_warning_style_pds():
    emoji, name, color, footer = wf.get_warning_style(
        "Tornado Warning", "PARTICULARLY DANGEROUS SITUATION"
    )
    assert name == "Tornado Warning (PDS)"
    assert color == discord.Color.red()
    assert footer == "PDS"


def test_get_warning_style_severe_damage_threat():
    _e, name, color, footer = wf.get_warning_style(
        "Severe Thunderstorm Warning", "THUNDERSTORM DAMAGE THREAT...DESTRUCTIVE"
    )
    assert "DESTRUCTIVE" in name
    assert color == discord.Color.purple()
    assert footer == "EWX"

    _e, name, _c, _f = wf.get_warning_style(
        "Severe Thunderstorm Warning", "THUNDERSTORM DAMAGE THREAT...CONSIDERABLE"
    )
    assert "CONSIDERABLE" in name


def test_get_warning_style_flash_flood_emergency():
    emoji, name, _c, footer = wf.get_warning_style("Flash Flood Warning", "FLASH FLOOD EMERGENCY")
    assert emoji == "🚨🚨"
    assert name == "Flash Flood Emergency"
    assert footer == "EMERG"


def test_get_warning_style_param_threats():
    _e, name, _c, footer = wf.get_warning_style(
        "Tornado Warning", "", params={"tornadoDamageThreat": ["CATASTROPHIC"]}
    )
    assert name == "Tornado Emergency"
    assert footer == "EMERG"

    _e, name, _c, _f = wf.get_warning_style(
        "Severe Thunderstorm Warning", "", params={"thunderstormDamageThreat": ["DESTRUCTIVE"]}
    )
    assert "DESTRUCTIVE" in name

    _e, name, _c, _f = wf.get_warning_style(
        "Flash Flood Warning", "", params={"flashFloodDamageThreat": ["CATASTROPHIC"]}
    )
    assert name == "Flash Flood Emergency"


def test_get_warning_style_default_fallback():
    emoji, name, color, footer = wf.get_warning_style("Blizzard Warning", "")
    assert emoji == "⚠️"
    assert name == "Blizzard Warning"
    assert color == discord.Color.orange()
    assert footer is None


# ── Tornado attributes ───────────────────────────────────────────────────────


def test_get_tornado_attributes_not_tornado():
    assert wf.get_tornado_attributes("Severe Thunderstorm Warning", "text") == (None, None)


def test_get_tornado_attributes_text_based():
    conf, sev = wf.get_tornado_attributes("Tornado Warning", "TORNADO...OBSERVED")
    assert conf == "observed"
    assert sev == "standard"

    conf, sev = wf.get_tornado_attributes("Tornado Warning", "PARTICULARLY DANGEROUS SITUATION")
    assert conf == "radar_indicated"
    assert sev == "pds"


def test_get_tornado_attributes_params_override():
    conf, sev = wf.get_tornado_attributes(
        "Tornado Warning",
        "",
        params={"tornadoDetection": ["OBSERVED"], "tornadoDamageThreat": ["CATASTROPHIC"]},
    )
    assert conf == "observed"
    assert sev == "emergency"


# ── Generic severity ─────────────────────────────────────────────────────────


def test_get_warning_severity_tornado():
    assert wf.get_warning_severity("Tornado Warning", "text") == "standard"
    assert wf.get_warning_severity("Tornado Warning", "TORNADO EMERGENCY") == "emergency"
    assert (
        wf.get_warning_severity(
            "Tornado Warning", "", params={"tornadoDamageThreat": ["CONSIDERABLE"]}
        )
        == "pds"
    )


def test_get_warning_severity_severe_and_flash():
    assert wf.get_warning_severity("Severe Thunderstorm Warning", "text") == "standard"
    assert (
        wf.get_warning_severity(
            "Severe Thunderstorm Warning", "THUNDERSTORM DAMAGE THREAT...DESTRUCTIVE"
        )
        == "destructive"
    )
    assert wf.get_warning_severity("Flash Flood Warning", "FLASH FLOOD EMERGENCY") == "emergency"
    assert wf.get_warning_severity("Flash Flood Warning", "text") == "standard"


def test_get_warning_severity_display_names_and_unknown():
    assert wf.get_warning_severity("DESTRUCTIVE Severe Tstorm Warning", "") == "destructive"
    assert wf.get_warning_severity("Flash Flood Emergency", "") == "emergency"
    assert wf.get_warning_severity("Blizzard Warning", "") is None


# ── IEM autoplot URL ─────────────────────────────────────────────────────────


def test_iem_autoplot_url_with_valid_time():
    url = wf.iem_autoplot_url(
        {"office": "OUN", "phenom": "TO", "sig": "W", "etn": "5", "start": "260811T1200Z"},
        valid_time="2026-08-11 1200",
    )
    assert "network:WFO::wfo:OUN" in url
    assert "::valid:2026-08-11%201200" in url
    assert "phenomenav:TO::significancev:W::etn:0005" in url


def test_iem_autoplot_url_parses_start():
    url = wf.iem_autoplot_url(
        {"office": "OUN", "phenom": "TO", "sig": "W", "etn": "5", "start": "260811T1200Z"}
    )
    assert "::valid:2026-08-11%201200" in url


def test_iem_autoplot_url_null_start_no_valid_param():
    url = wf.iem_autoplot_url(
        {"office": "OUN", "phenom": "TO", "sig": "W", "etn": "5", "start": "000000T0000Z"}
    )
    assert "::valid:" not in url


# ── VTEC URL / timestamp ─────────────────────────────────────────────────────


def test_vtec_url_with_start():
    url = wf._vtec_url(
        {
            "action": "NEW",
            "office": "OUN",
            "phenom": "TO",
            "sig": "W",
            "etn": "5",
            "start": "260811T1200Z",
        }
    )
    assert url == (
        "https://mesonet.agron.iastate.edu/vtec/f/2026-O-NEW-OUN-TO-W-0005_2026-08-11T12:00Z"
    )


def test_vtec_url_uses_end_when_start_null():
    url = wf._vtec_url(
        {
            "action": "EXP",
            "office": "OUN",
            "phenom": "TO",
            "sig": "W",
            "etn": "5",
            "start": "000000T0000Z",
            "end": "260811T2000Z",
        }
    )
    assert "2026-O-EXP-OUN-TO-W-0005_2026-08-11T20:00Z" in url


def test_vtec_url_missing_times_uses_now():
    url = wf._vtec_url({"action": "NEW", "office": "OUN", "phenom": "TO", "sig": "W", "etn": "5"})
    assert url.startswith("https://mesonet.agron.iastate.edu/vtec/f/2026-O-NEW-OUN-TO-W-0005_")


def test_vtec_unix_ts():
    ts = wf._vtec_unix_ts({"start": "260811T1200Z"})
    expected = int(datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc).timestamp())
    assert ts == expected


def test_vtec_unix_ts_null_uses_now():
    before = int(time.time())
    ts = wf._vtec_unix_ts({"start": "000000T0000Z"})
    assert before <= ts <= int(time.time())


# ── Area with state / county sets ────────────────────────────────────────────


def test_area_with_state_fallback():
    assert wf._area_with_state("Caddo; Grady", [], state_fallback="OK") == "Caddo; Grady [OK]"
    assert wf._area_with_state("Caddo; Grady", [], None) == "Caddo; Grady"


def test_area_with_state_single_state():
    result = wf._area_with_state("Clarke; Jones", ["MSC023", "MSC001"])
    assert result == "Clarke, Jones [MS]"


def test_area_with_state_two_states():
    result = wf._area_with_state("Ashley; Washington", ["ARC001", "MSC023"])
    assert result == "Ashley [AR] and Washington [MS]"


def test_canonical_county_set():
    assert wf._canonical_county_set("Caddo, Grady [OK]") == {"Caddo", "Grady"}
    assert wf._canonical_county_set("Caddo; Grady") == {"Caddo", "Grady"}
    assert wf._canonical_county_set("Ashley and Washington") == {"Ashley", "Washington"}
    assert wf._canonical_county_set("") == set()


# ── Narrative extraction ─────────────────────────────────────────────────────


def test_extract_narrative_drops_header_and_footers():
    raw = (
        "WMO HEADER\n"
        "BULLETIN - IMMEDIATE BROADCAST REQUESTED\n"
        "The National Weather Service in Norman OK\n"
        "A TORNADO WARNING HAS BEEN ISSUED...\n"
        "LAT...LON 34.5, -97.4\n"
        "ATTN...WFO OUN\n"
        "$$"
    )
    narrative = wf._extract_narrative(raw)
    assert narrative is not None
    assert "TORNADO WARNING" in narrative
    assert "LAT" not in narrative
    assert "ATTN" not in narrative
    assert "$$" not in narrative


def test_extract_narrative_empty():
    assert wf._extract_narrative("") is None
    assert wf._extract_narrative(None) is None
