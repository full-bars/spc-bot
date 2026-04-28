"""SPC Day 1 categorical outlook polygon helper.

Fetches the SPC Day 1 categorical outlook GeoJSON, extracts the MDT/HIGH
risk areas (the only levels at which we trigger broad sounding sweeps),
and exposes a buffered polygon plus point-in-polygon membership tests.

The GeoJSON is in EPSG:4326 (lat/lon). To get an accurate ~100 km buffer
we project to EPSG:5070 (CONUS Albers Equal Area, units = meters), buffer
in meters, and project the result back to lat/lon. This keeps the buffer
geometrically meaningful at all CONUS latitudes — a flat-degree buffer
would be ~30% too large in the south and ~30% too small in the north.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from shapely.geometry import Point, shape
from shapely.ops import transform, unary_union

from config import SPC_DAY1_CATEGORICAL_GEOJSON_URL
from utils.http import http_get_bytes

logger = logging.getLogger("spc_bot")

# Risk levels at which we trigger broad sounding coverage. SPC publishes
# these as `LABEL` values in the categorical GeoJSON.
HIGH_RISK_LABELS = frozenset({"MDT", "HIGH"})

# All possible risk labels in increasing order of severity
RISK_LEVELS = ["TSTM", "MRGL", "SLGT", "ENH", "MDT", "HIGH"]
RISK_MAP = {label: i for i, label in enumerate(RISK_LEVELS)}
RISK_DISPLAY = {
    "TSTM": "General Thunderstorm",
    "MRGL": "Marginal",
    "SLGT": "Slight",
    "ENH": "Enhanced",
    "MDT": "Moderate",
    "HIGH": "High",
}

# Buffer applied to the polygon when filtering RAOB stations / ACARS
# airports. RAOB stations are sparse (~400 km apart across CONUS) and a
# tightly-drawn MDT can otherwise miss the only relevant station.
DEFAULT_BUFFER_KM = 100.0

# Re-fetch interval. SPC issues outlooks at 06z, 13z, 1630z, 20z, 01z.
# 30 minutes catches every issuance shortly after it lands without
# hammering the endpoint.
CACHE_TTL_SECONDS = 30 * 60

_cache: dict = {
    "fetched_at": 0.0,
    "polygon_latlon": None,  # buffered polygon in EPSG:4326 (None = no MDT/HIGH)
    "labels": frozenset(),   # which of MDT/HIGH were active at fetch
    "max_risk": "TSTM",      # highest overall risk level on Day 1
}


def _project_to_albers(geom):
    """Project a lat/lon geometry to EPSG:5070 for buffering in meters."""
    # Lazy import — pyproj is heavy and only needed when we have a polygon
    from pyproj import Transformer  # noqa: PLC0415

    fwd = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True).transform
    return transform(fwd, geom)


def _project_to_latlon(geom):
    from pyproj import Transformer  # noqa: PLC0415

    rev = Transformer.from_crs("EPSG:5070", "EPSG:4326", always_xy=True).transform
    return transform(rev, geom)


def _build_buffered_polygon(features: list, buffer_km: float):
    """Return (buffered_polygon_in_latlon, set_of_labels_present, max_risk_label) 
    or (None, frozenset(), max_risk) when no MDT/HIGH polygons are in the set.
    """
    selected = []
    labels_present = set()
    max_risk_idx = 0
    
    for feat in features:
        props = feat.get("properties", {}) or {}
        label = props.get("LABEL", "").strip().upper()
        
        # Track overall highest risk
        if label in RISK_MAP:
            max_risk_idx = max(max_risk_idx, RISK_MAP[label])
        
        if label not in HIGH_RISK_LABELS:
            continue
        geom = feat.get("geometry")
        if not geom:
            continue
        try:
            shp = shape(geom)
        except Exception as e:
            logger.warning(f"[OUTLOOK] Could not parse {label} geometry: {e}")
            continue
        if shp.is_empty:
            continue
        selected.append(shp)
        labels_present.add(label)

    max_risk = RISK_LEVELS[max_risk_idx]

    if not selected:
        return None, frozenset(), max_risk

    union_latlon = unary_union(selected)
    union_albers = _project_to_albers(union_latlon)
    buffered_albers = union_albers.buffer(buffer_km * 1000.0)
    buffered_latlon = _project_to_latlon(buffered_albers)
    return buffered_latlon, frozenset(labels_present), max_risk


async def get_high_risk_polygon(
    buffer_km: float = DEFAULT_BUFFER_KM,
    force_refresh: bool = False,
):
    """Return (buffered_polygon, frozenset_of_active_labels).
    
    The polygon is the union of all MDT and HIGH categorical regions on
    today's Day 1 outlook. Result is cached for ``CACHE_TTL_SECONDS``.
    """
    now = time.monotonic()
    if (
        not force_refresh
        and _cache["fetched_at"] > 0
        and now - _cache["fetched_at"] < CACHE_TTL_SECONDS
    ):
        return _cache["polygon_latlon"], _cache["labels"]

    content, status = await http_get_bytes(
        SPC_DAY1_CATEGORICAL_GEOJSON_URL, retries=2, timeout=15
    )
    if not content or status != 200:
        logger.warning(
            f"[OUTLOOK] Day 1 categorical fetch failed (status={status}); "
            f"keeping last known polygon"
        )
        return _cache["polygon_latlon"], _cache["labels"]

    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning(f"[OUTLOOK] Day 1 GeoJSON parse failed: {e}")
        return _cache["polygon_latlon"], _cache["labels"]

    features = data.get("features", []) or []
    polygon, labels, max_risk = await asyncio.get_running_loop().run_in_executor(
        None, lambda: _build_buffered_polygon(features, buffer_km)
    )

    _cache["fetched_at"] = now
    _cache["polygon_latlon"] = polygon
    _cache["labels"] = labels
    _cache["max_risk"] = max_risk

    if polygon is None:
        logger.debug(f"[OUTLOOK] No MDT/HIGH on Day 1 (max: {max_risk})")
    else:
        logger.info(
            f"[OUTLOOK] Day 1 high-risk polygon refreshed "
            f"(active: {sorted(labels)}, max: {max_risk}, buffered {buffer_km:.0f} km)"
        )
    return polygon, labels


def is_inside_polygon(lat: float, lon: float, polygon) -> bool:
    """True if (lat, lon) lies inside ``polygon`` (EPSG:4326). Returns
    False when ``polygon`` is None."""
    if polygon is None:
        return False
    return polygon.contains(Point(lon, lat))


def peek_active_labels() -> frozenset:
    """Return whichever of {MDT, HIGH} is currently active per the
    last-fetched outlook, without triggering a fetch. Empty set when
    none are active or when the cache hasn't been populated yet.

    Intended for ``/status`` and other read-only consumers that want to
    surface risk state without paying the GeoJSON fetch latency.
    """
    return _cache["labels"]


def get_current_risk_display() -> str:
    """Return a human-readable string for the overall max Day 1 risk."""
    raw = _cache.get("max_risk", "TSTM")
    return RISK_DISPLAY.get(raw, raw)


def reset_cache_for_tests() -> None:
    """Drop the module-level cache. Tests use this to force a fresh fetch."""
    _cache["fetched_at"] = 0.0
    _cache["polygon_latlon"] = None
    _cache["labels"] = frozenset()
