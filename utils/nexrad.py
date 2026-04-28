"""NEXRAD WSR-88D site lookup — used by WarningsCog to attach the
nearest radar's loop GIF to each warning post.

The IEM mesonet exposes the full station list as GeoJSON; we cache it
in-process for the bot's lifetime since the network is essentially
static (commissioning/decommissioning happens on multi-year timescales).

The 4-letter ICAO is derived from the state because IEM publishes only
the 3-letter ``sid`` field. Mapping:

  - K{sid}  for CONUS sites
  - P{sid}  for AK / HI / GU sites
  - T{sid}  for PR / VI sites

Used downstream as ``https://radar.weather.gov/ridge/standard/{ICAO}_loop.gif``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from typing import List, Optional, Tuple

from utils.http import http_get_bytes

logger = logging.getLogger("spc_bot")

NEXRAD_GEOJSON_URL = (
    "https://mesonet.agron.iastate.edu/geojson/network/NEXRAD.geojson"
)

# Per-state ICAO prefix. The default is "K" for any CONUS state.
_NON_CONUS_PREFIX = {
    "AK": "P",
    "HI": "P",
    "GU": "P",
    "PR": "T",
    "VI": "T",
}

# Module-level cache populated on first lookup. Each entry is
# (icao, lat, lon).
_sites_cache: Optional[List[Tuple[str, float, float]]] = None
_sites_lock = asyncio.Lock()


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def _icao_for(sid: str, state: str) -> str:
    prefix = _NON_CONUS_PREFIX.get((state or "").upper(), "K")
    return f"{prefix}{sid.upper()}"


async def get_nexrad_sites() -> List[Tuple[str, float, float]]:
    """Return the cached list of (icao, lat, lon) for all WSR-88D sites.

    Fetches from IEM on first call and caches indefinitely. Returns an
    empty list if the fetch fails — callers should fall through
    gracefully rather than refusing to post a warning.
    """
    global _sites_cache
    if _sites_cache is not None:
        return _sites_cache

    async with _sites_lock:
        if _sites_cache is not None:
            return _sites_cache

        content, status = await http_get_bytes(
            NEXRAD_GEOJSON_URL, retries=2, timeout=15
        )
        if not content or status != 200:
            logger.warning(
                f"[NEXRAD] Site list fetch failed (status={status}); "
                f"warnings will post without radar GIFs until next retry"
            )
            return []

        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            logger.warning(f"[NEXRAD] Site list parse failed: {e}")
            return []

        sites: List[Tuple[str, float, float]] = []
        for feat in data.get("features", []) or []:
            props = feat.get("properties", {}) or {}
            sid = props.get("sid")
            state = props.get("state", "")
            geom = feat.get("geometry", {}) or {}
            coords = geom.get("coordinates")
            if not (sid and coords and len(coords) == 2):
                continue
            lon, lat = coords
            try:
                sites.append((_icao_for(sid, state), float(lat), float(lon)))
            except (TypeError, ValueError):
                continue

        _sites_cache = sites
        logger.info(f"[NEXRAD] Cached {len(sites)} site coordinates")
        return sites


async def find_nearest_radar(
    lat: float, lon: float
) -> Optional[Tuple[str, float]]:
    """Return ``(icao, distance_km)`` for the WSR-88D nearest to
    (lat, lon), or ``None`` if the site list is unavailable.

    No 'maximum range' cutoff is applied — even if the closest radar
    is 300 km away (rare but possible in the Mountain West) we still
    return it. The user can always pull a different site manually.
    """
    sites = await get_nexrad_sites()
    if not sites:
        return None
    nearest_icao = ""
    nearest_dist = float("inf")
    for icao, slat, slon in sites:
        d = _haversine_km(lat, lon, slat, slon)
        if d < nearest_dist:
            nearest_dist = d
            nearest_icao = icao
    if not nearest_icao:
        return None
    return nearest_icao, nearest_dist


def polygon_centroid(coords: List[Tuple[float, float]]) -> Optional[Tuple[float, float]]:
    """Return the centroid (lat, lon) of a list of polygon vertices.

    Uses the simple-average method rather than the area-weighted
    centroid — good enough for the small (~tens of km) polygons that
    NWS warnings draw, and avoids needing shapely just for this.
    Returns ``None`` for an empty input.
    """
    if not coords:
        return None
    n = len(coords)
    lat = sum(p[0] for p in coords) / n
    lon = sum(p[1] for p in coords) / n
    return lat, lon


def reset_cache_for_tests() -> None:
    """Drop the module-level cache. Tests use this to force a fresh fetch."""
    global _sites_cache
    _sites_cache = None
