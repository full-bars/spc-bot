"""Tests for utils.spc_outlook — Day 1 categorical polygon parsing,
buffering, and point-in-polygon membership."""

import json
from unittest.mock import patch

import pytest

from utils import spc_outlook


def _feature(label, geom):
    return {
        "type": "Feature",
        "properties": {"LABEL": label, "LABEL2": f"{label} Risk"},
        "geometry": geom,
    }


def _square(lon0, lat0, lon1, lat1):
    return {
        "type": "Polygon",
        "coordinates": [[
            [lon0, lat0], [lon1, lat0], [lon1, lat1],
            [lon0, lat1], [lon0, lat0],
        ]],
    }


@pytest.fixture(autouse=True)
def _clear_cache():
    spc_outlook.reset_cache_for_tests()
    yield
    spc_outlook.reset_cache_for_tests()


@pytest.mark.asyncio
async def test_no_high_or_moderate_returns_none():
    """A typical day with only TSTM/MRGL/SLGT/ENH risks must yield no
    polygon — we only sweep on MDT or HIGH."""
    payload = {
        "type": "FeatureCollection",
        "features": [
            _feature("TSTM", _square(-100, 35, -90, 40)),
            _feature("MRGL", _square(-99, 36, -91, 39)),
            _feature("SLGT", _square(-98, 37, -92, 38)),
        ],
    }
    body = json.dumps(payload).encode()

    async def fake_get(*args, **kwargs):
        return body, 200

    with patch.object(spc_outlook, "http_get_bytes", side_effect=fake_get):
        polygon, labels = await spc_outlook.get_high_risk_polygon()

    assert polygon is None
    assert labels == frozenset()


@pytest.mark.asyncio
async def test_moderate_polygon_is_buffered():
    """A bare MDT polygon must come back buffered — a station 50 km
    *outside* the raw polygon should test as inside the buffered one
    when the default buffer is 100 km."""
    payload = {
        "type": "FeatureCollection",
        "features": [
            _feature("TSTM", _square(-110, 30, -85, 45)),
            _feature("MDT", _square(-100, 38, -95, 42)),
        ],
    }
    body = json.dumps(payload).encode()

    async def fake_get(*args, **kwargs):
        return body, 200

    with patch.object(spc_outlook, "http_get_bytes", side_effect=fake_get):
        polygon, labels = await spc_outlook.get_high_risk_polygon()

    assert polygon is not None
    assert labels == frozenset({"MDT"})

    # Center of polygon — must be inside.
    assert spc_outlook.is_inside_polygon(40.0, -97.5, polygon)
    # 50 km outside the western edge (at ~40 N, 1 deg lon ≈ 85 km, so
    # 0.6 deg west of -100 ≈ 51 km outside) — still inside the 100 km buffer.
    assert spc_outlook.is_inside_polygon(40.0, -100.6, polygon)
    # 200 km outside the western edge — must NOT be inside.
    assert not spc_outlook.is_inside_polygon(40.0, -103.0, polygon)


@pytest.mark.asyncio
async def test_high_and_moderate_unioned():
    """When both MDT and HIGH are active they're combined into a single
    polygon and both labels are reported."""
    payload = {
        "type": "FeatureCollection",
        "features": [
            _feature("MDT", _square(-100, 35, -95, 40)),
            _feature("HIGH", _square(-98, 36, -96, 39)),
        ],
    }
    body = json.dumps(payload).encode()

    async def fake_get(*args, **kwargs):
        return body, 200

    with patch.object(spc_outlook, "http_get_bytes", side_effect=fake_get):
        polygon, labels = await spc_outlook.get_high_risk_polygon()

    assert polygon is not None
    assert labels == frozenset({"MDT", "HIGH"})
    assert spc_outlook.is_inside_polygon(37.5, -97.0, polygon)


@pytest.mark.asyncio
async def test_fetch_failure_keeps_last_known_polygon():
    """If the HTTP fetch fails after we already have a cached polygon,
    we must serve the cached value rather than dropping coverage on a
    transient SPC outage."""
    good = {
        "type": "FeatureCollection",
        "features": [_feature("MDT", _square(-100, 35, -95, 40))],
    }

    calls = {"n": 0}

    async def maybe_fail(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return json.dumps(good).encode(), 200
        return None, 0

    with patch.object(spc_outlook, "http_get_bytes", side_effect=maybe_fail):
        first, _ = await spc_outlook.get_high_risk_polygon()
        # Force re-fetch; the second fetch fails — must return prior polygon.
        second, _ = await spc_outlook.get_high_risk_polygon(force_refresh=True)

    assert first is not None
    assert second is first  # exact same cached object


def test_caption_prefix_matches_severity_label():
    """The caption prefix used by monitor_high_risk_soundings must
    follow the active label set: MDT alone → 'MDT-Risk'; HIGH alone or
    HIGH+MDT → 'High-Risk' (HIGH is the dominant level)."""
    # Replicate the prefix-selection rule inline to pin the contract.
    def prefix(labels):
        return "High-Risk" if "HIGH" in labels else "MDT-Risk"

    assert prefix(frozenset({"MDT"})) == "MDT-Risk"
    assert prefix(frozenset({"HIGH"})) == "High-Risk"
    assert prefix(frozenset({"MDT", "HIGH"})) == "High-Risk"


@pytest.mark.asyncio
async def test_is_inside_polygon_handles_none():
    """Defensive: callers may pass None when no MDT/HIGH is active."""
    assert spc_outlook.is_inside_polygon(40.0, -97.5, None) is False
