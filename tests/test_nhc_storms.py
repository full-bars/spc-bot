"""
Tests for utils/nhc_storms.py — NHC cyclone page parsing and storm extraction.
"""

from unittest.mock import AsyncMock, patch

import pytest

from utils.nhc_storms import (
    classify_storm_type,
    extract_storm_name,
    get_active_storms,
    split_type_name,
)

# ── extract_storm_name ───────────────────────────────────────────────────────


def test_extract_storm_name_strips_advisory_suffix():
    assert extract_storm_name("HURRICANE FAUSTO ADVISORY NUMBER 14") == "Fausto"


def test_extract_storm_name_plain():
    assert extract_storm_name("HURRICANE ANNA") == "Anna"


def test_extract_storm_name_remnants_of():
    assert extract_storm_name("REMNANTS OF FAY") == "Fay"


def test_extract_storm_name_potential_tropical_cyclone():
    assert extract_storm_name("POTENTIAL TROPICAL CYCLONE NINE") == "Nine"


def test_extract_storm_name_discussion_suffix():
    assert extract_storm_name("TROPICAL STORM FRED DISCUSSION") == "Fred"


def test_extract_storm_name_none_when_absent():
    assert extract_storm_name("SOME UNRELATED TEXT") is None


# ── classify_storm_type ordering ─────────────────────────────────────────────


def test_classify_storm_type_subtropical_before_tropical():
    assert classify_storm_type("SUBTROPICAL DEPRESSION NINE") == "SUBTROPICAL DEPRESSION"


def test_classify_storm_type_major_before_hurricane():
    assert classify_storm_type("MAJOR HURRICANE IRMA") == "MAJOR HURRICANE"


def test_classify_storm_type_plain_hurricane():
    assert classify_storm_type("HURRICANE IRMA") == "HURRICANE"


# ── split_type_name ──────────────────────────────────────────────────────────


def test_split_type_name_hurricane():
    assert split_type_name("Hurricane Polo") == ("Hurricane", "Polo")


def test_split_type_name_post_tropical():
    assert split_type_name("Post-Tropical Cyclone Fay") == ("Post-Tropical Cyclone", "Fay")


def test_split_type_name_no_type():
    assert split_type_name("Some Label") == ("Some Label", None)


# ── _extract_storms_from_html ────────────────────────────────────────────────

# Mirrors the real NHC cyclones page structure (verified Sep 2026): SVG
# storm-system blocks with data-name/data-risk/data-details/data-action plus
# table-row comments mapping serial numbers and storm IDs to full names.
_CYCLONES_HTML = """<!doctype html><html><body>
<table>
<tr><td>
<a href="https://www.star.nesdis.noaa.gov/goes/floater.php?stormid=EP172026">Satellite</a>
<!--storm serial number: EP17-->
<!--storm identification: EP172026 Hurricane Polo-->
</td></tr>
<tr><td>
<!--storm serial number: EP16-->
<!--storm identification: EP162026 Tropical Storm Odalys-->
</td></tr>
</table>
<div class="gtwo-map">
<svg>
<g class="storm-system cursor-pointer"
    data-name="HURRICANE POLO"
    data-risk="storm"
    data-prob="N/A"
    data-details="
  <b>As of 0600 PM CST Tue Sep 22 (Advisory #9A)</b><br>Maximum Sustained Winds: 150 knots; 175 mph<br>Minimum Central Pressure: 900 mb<br>Located at: 14.6N 101.4W<br>Movement: east at 1 knots; 1 mph<br>
"
    data-action="/refresh/graphics_ep2+shtml/222335.shtml?key_messages#contents">
  <circle cx="1" cy="1" r="24" />
</g>
<g class="storm-system"
    data-name="Disturbance 1: 20% Chance of Cyclone Formation in 48 Hours"
    data-risk="low"
    data-prob="20%"
    data-details="some details">
  <circle cx="2" cy="2" r="24" />
</g>
<g class="storm-system cursor-pointer"
    data-name="TROPICAL STORM ODALYS"
    data-risk="storm"
    data-prob="N/A"
    data-details="
  <b>As of 1000 AM CST Tue Sep 22 (Advisory #8)</b><br>Maximum Sustained Winds: 60 knots; 70 mph<br>Minimum Central Pressure: 992 mb<br>Located at: 15.0N 128.5W<br>Movement: west-northwest at 10 knots; 12 mph<br>
"
    data-action="/refresh/graphics_ep1+shtml/222330.shtml?key_messages#contents">
  <circle cx="3" cy="3" r="24" />
</g>
</svg>
</div>
</body></html>"""


def test_extract_storms_from_html_parses_structured_data():
    from utils.nhc_storms import _extract_storms_from_html

    storms = _extract_storms_from_html(_CYCLONES_HTML)

    assert set(storms) == {"EP172026", "EP162026"}

    polo = storms["EP172026"]
    assert polo["name"] == "Polo"
    assert polo["type"] == "Hurricane"
    assert polo["serial"] == "EP17"
    assert polo["advisory"] == "9A"
    assert polo["winds_mph"] == 175.0
    assert polo["pressure"] == 900
    assert polo["position"] == "14.6N 101.4W"
    assert "east" in polo["movement"]
    assert polo["graphics_url"] == "https://www.nhc.noaa.gov/graphics_ep2.shtml?cone"
    assert "0600 PM CST" in polo["issuance"]
    assert (
        polo["satellite_url"]
        == "https://www.star.nesdis.noaa.gov/goes/floater.php?stormid=EP172026"
    )

    odalys = storms["EP162026"]
    assert odalys["name"] == "Odalys"
    assert odalys["type"] == "Tropical Storm"
    assert odalys["serial"] == "EP16"
    assert odalys["advisory"] == "8"


def test_extract_storms_from_html_empty():
    from utils.nhc_storms import _extract_storms_from_html

    assert _extract_storms_from_html("<html><body>no storms here</body></html>") == {}


# ── cone image extraction ────────────────────────────────────────────────────

_GRAPHICS_HTML = """<html><body>
<img src="/storm_graphics/EP17/refresh/EP172026_5day_cone_sm+png/222335_5day_cone_sm.png" width="60" height="48" alt="Warnings and 5-Day Cone">
<img id="coneimage" src = "/storm_graphics/EP17/refresh/EP172026_5day_cone+png/222335_5day_cone.png" alt="cone graphic" />
</body></html>"""


@pytest.mark.asyncio
async def test_download_cone_image_uses_full_coneimage_not_thumbnail():
    """The embed must use the full-size cone graphic, not the 60px _sm thumb."""
    from cogs.tropical_tracker import _download_cone_image

    cone_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 4096

    def _fake_get_bytes(url, retries=2, timeout=15):
        if url.endswith(".shtml?cone"):
            return _GRAPHICS_HTML.encode(), 200
        return cone_png, 200

    with patch("cogs.tropical_tracker.http_get_bytes", side_effect=_fake_get_bytes):
        result = await _download_cone_image("https://www.nhc.noaa.gov/graphics_ep2.shtml?cone")

    assert result == cone_png


@pytest.mark.asyncio
async def test_download_satellite_image_extracts_selected_product():
    """The floater page's FloaterStatic{PRODUCT} frame for the chosen product."""
    from cogs.tropical_tracker import _download_satellite_image

    sat_jpg = b"\xff\xd8\xff\xe0" + b"\x00" * 4096
    floater_html = (
        "<html>"
        "<input type='hidden' id='FloaterStaticGEOCOLOR' value='https://cdn/geocolor.jpg'>"
        "<input type='hidden' id='FloaterStaticSandwich' value='https://cdn/sandwich.jpg'>"
        "</html>"
    )

    def _fake_get_bytes(url, retries=2, timeout=15):
        if "floater.php" in url:
            return floater_html.encode(), 200
        return sat_jpg, 200

    with patch("cogs.tropical_tracker.http_get_bytes", side_effect=_fake_get_bytes):
        result = await _download_satellite_image(
            "https://www.star.nesdis.noaa.gov/goes/floater.php?stormid=EP172026",
            product="Sandwich",
        )

    assert result == sat_jpg


# ── get_active_storms caching ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_active_storms_caches_empty_result_as_authoritative():
    """An explicitly parsed empty page is a valid (empty) cache, and the
    previous cache must not be retained when parsing succeeds with no storms."""
    from utils import nhc_storms

    with patch(
        "utils.nhc_storms.http_get_bytes",
        AsyncMock(return_value=(b"<html>quiet basin</html>", 200)),
    ):
        nhc_storms._active_storms_cache = {"EP172026": {"storm_id": "EP172026"}}
        nhc_storms._active_storms_fetched_at = 0.0
        nhc_storms._last_fetch_ok = None
        result = await get_active_storms()

    assert result == {}
    assert nhc_storms.active_storms_authoritative() is True


@pytest.mark.asyncio
async def test_get_active_storms_retains_cache_on_failure():
    from utils import nhc_storms

    with patch("utils.nhc_storms.http_get_bytes", AsyncMock(return_value=(None, 503))):
        nhc_storms._active_storms_cache = {"EP172026": {"storm_id": "EP172026"}}
        nhc_storms._active_storms_fetched_at = 0.0
        nhc_storms._last_fetch_ok = None
        result = await get_active_storms()

    assert result == {"EP172026": {"storm_id": "EP172026"}}
    assert nhc_storms.active_storms_authoritative() is False


# ── tracker state helpers ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_remove_tracked_storm_uses_per_storm_keys():
    """Mutations are per-channel-per-storm keys: add twice is idempotent,
    remove returns True once, and state round-trips through JSON."""
    from cogs import tropical_tracker as tracker

    stored: dict[str, str] = {}

    async def _fake_get(key):
        return stored.get(key)

    async def _fake_set(key, value):
        stored[key] = value

    async def _fake_del(key):
        stored.pop(key, None)

    async def _fake_list(prefix):
        return [k[len(prefix) :] for k in stored if k.startswith(prefix)]

    with patch.object(tracker, "get_state", side_effect=_fake_get), patch.object(
        tracker, "set_state", side_effect=_fake_set
    ), patch.object(tracker, "delete_state", side_effect=_fake_del), patch.object(
        tracker, "list_state_keys", side_effect=_fake_list
    ):
        assert await tracker.add_tracked_storm(123, "EP172026") is True
        assert await tracker.add_tracked_storm(123, "EP172026") is False
        assert await tracker.add_tracked_storm(123, "EP162026") is True

        tracked = await tracker.get_tracked_storms(123)
        assert {t["storm_id"] for t in tracked} == {"EP172026", "EP162026"}

        assert await tracker.remove_tracked_storm(123, "EP172026") is True
        assert await tracker.remove_tracked_storm(123, "EP172026") is False

        all_channels = await tracker.get_all_tracked_channels()
        assert all_channels == {123: ["EP162026"]}


def test_storm_id_regex_rejects_path_traversal():
    from cogs.tropical_tracker import _STORM_ID_RE

    assert _STORM_ID_RE.match("AL062026")
    assert _STORM_ID_RE.match("EP172026")
    assert not _STORM_ID_RE.match("AL06../../path")
    assert not _STORM_ID_RE.match("AL06202")
    assert not _STORM_ID_RE.match("EP172026extra")
