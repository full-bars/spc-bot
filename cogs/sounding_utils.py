# cogs/sounding_utils.py
"""
Utility functions for the sounding cog:
- Location resolution (city, radar site, RAOB station)
- Nearest station lookup
- SounderPy data fetch and plot generation
- User preference persistence
"""

import asyncio
import concurrent.futures
import io
import logging
import math
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiohttp
import matplotlib
import numpy as np
import pandas as pd
from metpy.units import units

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402  # must follow matplotlib.use()

# Suppress SounderPy's startup banner by redirecting stdout during import.
_stdout = sys.stdout
sys.stdout = io.StringIO()
try:
    import sounderpy as spy  # noqa: E402  # silenced banner import
finally:
    sys.stdout = _stdout

from utils.state_store import get_state, set_state  # noqa: E402  # follows sounderpy import

# ProcessPoolExecutor for parallel sounding plots. Each worker process gets
# its own matplotlib instance so plots run concurrently without lock contention.
# Workers are capped at 3 — more than the typical per-watch batch size gains
# nothing and wastes RAM.
_PLOT_EXECUTOR: Optional[concurrent.futures.ProcessPoolExecutor] = None
_PLOT_EXECUTOR_WORKERS = min(3, (os.cpu_count() or 2))


def _plot_worker_init():
    """Pre-import sounderpy/matplotlib in each worker so the first plot call
    doesn't pay cold-import overhead (~2s).

    Also clears inherited logging handlers to prevent duplicate log entries
    from child processes writing to the same file/stderr as the primary."""
    import io as _io
    import logging as _logging
    import sys as _sys

    # Silent inherited loggers
    for name in ("spc_bot", None):  # spc_bot and root
        l = _logging.getLogger(name)
        for h in l.handlers[:]:
            l.removeHandler(h)
    _logging.getLogger().setLevel(_logging.WARNING)

    import matplotlib as _mpl
    _mpl.use("Agg")
    _stdout = _sys.stdout
    _sys.stdout = _io.StringIO()
    try:
        import sounderpy  # noqa: F401
    finally:
        _sys.stdout = _stdout


def _get_plot_executor() -> concurrent.futures.ProcessPoolExecutor:
    global _PLOT_EXECUTOR
    if _PLOT_EXECUTOR is None:
        _PLOT_EXECUTOR = concurrent.futures.ProcessPoolExecutor(
            max_workers=_PLOT_EXECUTOR_WORKERS,
            initializer=_plot_worker_init,
        )
    return _PLOT_EXECUTOR


def shutdown_plot_executor():
    """Shut down the plot worker pool cleanly on bot exit."""
    global _PLOT_EXECUTOR
    if _PLOT_EXECUTOR is not None:
        _PLOT_EXECUTOR.shutdown(wait=False, cancel_futures=True)
        _PLOT_EXECUTOR = None

logger = logging.getLogger("spc_bot")

RAOB_STATIONS_URL = "https://raw.githubusercontent.com/kylejgillett/sounderpy/main/src/RAOB-STATIONS.txt"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "spc-bot-sounding/1.0"

# Cache the station list in memory so we don't fetch it every time
_station_cache: Optional[pd.DataFrame] = None


# ── User preferences ──────────────────────────────────────────────────────────

async def get_user_dark_mode(user_id: int) -> bool:
    """Get user dark mode preference from DB."""
    try:
        raw = await get_state(f"sounding_dark_{user_id}")
        return raw == "1" if raw is not None else False
    except Exception as e:
        logger.debug(f"[SOUNDING] Dark mode lookup failed for {user_id}: {e}")
        return False

async def set_user_dark_mode(user_id: int, dark: bool):
    """Save user dark mode preference to DB."""
    try:
        await set_state(f"sounding_dark_{user_id}", "1" if dark else "0")
    except Exception as e:
        logger.warning(f"[SOUNDING] Failed to save dark mode pref: {e}")


# ── Station list ──────────────────────────────────────────────────────────────

async def get_raob_stations() -> pd.DataFrame:
    """Fetch and cache the RAOB station list."""
    global _station_cache
    if _station_cache is not None:
        return _station_cache

    loop = asyncio.get_running_loop()
    df = await loop.run_in_executor(None, _fetch_stations)
    _station_cache = df
    return df

def _fetch_stations() -> pd.DataFrame:
    df = pd.read_csv(
        RAOB_STATIONS_URL,
        skiprows=8, sep=",",
        names=["WMO","ICAO","NAME","LOC","EL","LAT","A","LON","B","X"],
        skipinitialspace=True,
    )
    df = df[pd.to_numeric(df["LAT"], errors="coerce").notna()].copy()

    def to_decimal(val, hemi):
        val = float(val)
        hemi = str(hemi).strip()
        return val if hemi in ("N", "E") else -val

    df["lat"] = df.apply(lambda r: to_decimal(r["LAT"], r["A"]), axis=1)
    df["lon"] = df.apply(lambda r: to_decimal(r["LON"], r["B"]), axis=1)
    df["ICAO"] = df["ICAO"].str.strip()
    df["NAME"] = df["NAME"].str.strip()
    df["LOC"] = df["LOC"].str.strip()
    return df


# ── Distance ──────────────────────────────────────────────────────────────────

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon/2)**2)
    return R * 2 * math.asin(math.sqrt(a))

def find_nearest_stations(lat: float, lon: float, df: pd.DataFrame, n: int = 3) -> list[dict]:
    """Return the n nearest RAOB stations as a list of dicts."""
    df = df.copy()
    df["dist_km"] = df.apply(
        lambda r: haversine(lat, lon, r["lat"], r["lon"]), axis=1
    )
    nearest = df.nsmallest(n, "dist_km")
    results = []
    for _, row in nearest.iterrows():
        icao = str(row["ICAO"]).strip()
        results.append({
            "icao": icao if icao != "----" else None,
            "wmo": str(row["WMO"]).strip(),
            "name": str(row["NAME"]).strip(),
            "loc": str(row["LOC"]).strip(),
            "lat": row["lat"],
            "lon": row["lon"],
            "dist_km": round(row["dist_km"], 1),
        })
    return results


# ── Location resolution ───────────────────────────────────────────────────────

async def resolve_location(location: str) -> tuple[float, float, str]:
    """
    Resolve a location string to (lat, lon, description).
    Handles:
    - 4-letter K-site (radar): KTLX
    - RAOB station ID: OUN, KOKC
    - City/keywords: Oklahoma City
    Returns (lat, lon, description) or raises ValueError.
    """
    loc = location.strip().upper()

    # Try as RAOB station first (3-5 chars, all alpha)
    if len(loc) <= 5 and loc.isalpha():
        try:
            stations = await get_raob_stations()
            # Try ICAO match
            match = stations[stations["ICAO"].str.strip() == loc]
            if not match.empty:
                row = match.iloc[0]
                return float(row["lat"]), float(row["lon"]), f"RAOB station {loc}"
        except Exception as e:
            logger.debug(f"[SOUNDING] RAOB lookup for {loc!r} failed: {e}")

        # Try as METAR/radar site (K-prefix 4-letter)
        if len(loc) == 4 and loc.startswith("K"):
            try:
                latlon = await asyncio.get_running_loop().run_in_executor(
                    None, spy.get_latlon, "metar", loc
                )
                return float(latlon[0]), float(latlon[1]), f"radar site {loc}"
            except Exception as e:
                logger.debug(f"[SOUNDING] METAR lookup for {loc!r} failed: {e}")

    # Fall back to Nominatim geocoding
    lat, lon, display = await geocode_city(location)
    return lat, lon, display

async def geocode_city(query: str) -> tuple[float, float, str]:
    """Geocode a city name using Nominatim. Returns (lat, lon, display_name)."""
    async with aiohttp.ClientSession() as session:
        async with session.get(
            NOMINATIM_URL,
            params={"q": query, "format": "json", "limit": 1},
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            data = await resp.json()

    if not data:
        raise ValueError(
            f"Could not find location: **{query}**\n"
            f"Try a city name, state abbreviation, or a RAOB/radar station code."
        )

    result = data[0]
    # Shorten display name to city, state/country
    display = result.get("display_name", query).split(",")
    display = ", ".join(display[:2]).strip()
    return float(result["lat"]), float(result["lon"]), display


# ── Time resolution ───────────────────────────────────────────────────────────

def parse_sounding_time(time_str: Optional[str]) -> Optional[tuple[str, str, str, str]]:
    """
    Parse a time string like "04-10-2026 00z" or "04-10-2026 12z".
    Returns (year, month, day, hour) or None if not provided.
    """
    if not time_str:
        return None
    time_str = time_str.strip().upper()
    try:
        # Strip the Z
        time_str = time_str.replace("Z", "").strip()
        parts = time_str.split()
        if len(parts) != 2:
            raise ValueError
        date_part, hour_part = parts
        dt = datetime.strptime(date_part, "%m-%d-%Y")
        hour = int(hour_part)
        if not (0 <= hour <= 23):
            raise ValueError("Hour must be 00-23")
        return (
            str(dt.year),
            str(dt.month).zfill(2),
            str(dt.day).zfill(2),
            str(hour).zfill(2),
        )
    except Exception as e:
        raise ValueError(
            f"Invalid time format: **{time_str}**\n"
            f"Use: `MM-DD-YYYY 00z` or `MM-DD-YYYY 12z`\n"
            f"Example: `04-10-2026 12z`"
        ) from e

def get_recent_sounding_times(n: int = 4) -> list[tuple[str, str, str, str]]:
    """
    Return the n most recent 00z/12z sounding times that are in the past.
    """
    now = datetime.now(timezone.utc)
    times = []
    for days_back in range(5):
        for hour in [12, 0]:
            dt = now.replace(
                hour=hour, minute=0, second=0, microsecond=0
            ) - timedelta(days=days_back)
            if dt < now:
                times.append((
                    str(dt.year),
                    str(dt.month).zfill(2),
                    str(dt.day).zfill(2),
                    str(dt.hour).zfill(2),
                ))
            if len(times) >= n:
                return times
    return times



async def get_watch_area_centroid(affected_zones: list) -> tuple[float, float] | None:
    """
    Fetch zone polygons from NWS and return the centroid of the watch area.
    Returns (lat, lon) or None on failure.
    """
    all_lats = []
    all_lons = []

    async with aiohttp.ClientSession(
        headers={"User-Agent": "spc-bot-sounding/1.0"}
    ) as session:
        for zone_url in affected_zones[:10]:  # cap at 10 zones
            try:
                async with session.get(
                    zone_url, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    data = await resp.json()
                geometry = data.get("geometry")
                if not geometry:
                    continue
                coords = geometry.get("coordinates", [])
                if geometry["type"] == "Polygon":
                    for lon, lat in coords[0]:
                        all_lats.append(lat)
                        all_lons.append(lon)
                elif geometry["type"] == "MultiPolygon":
                    for polygon in coords:
                        for lon, lat in polygon[0]:
                            all_lats.append(lat)
                            all_lons.append(lon)
            except Exception as e:
                logger.warning(f"[SOUNDING] Zone fetch failed for {zone_url}: {e}")

    if not all_lats:
        return None
    return sum(all_lats) / len(all_lats), sum(all_lons) / len(all_lons)


async def get_md_area_centroid(raw_text: str) -> tuple[float, float] | None:
    """
    Parse the LAT...LON block from SPC MD text and return the centroid.
    Format is 8-digit pairs: DDMMDDMM...
    """
    m = re.search(r"LAT\.\.\.LON\s+((?:\d{8}\s*)+)", raw_text, re.MULTILINE)
    if not m:
        return None

    coords_str = re.sub(r"\s+", "", m.group(1))
    lats = []
    lons = []

    for i in range(0, len(coords_str), 8):
        part = coords_str[i:i+8]
        if len(part) < 8:
            continue
        try:
            # Format: DDMMDDMM (LatDDMM LonDDMM)
            # SPC lons are often 3 digits if > 100, but in this block 
            # they are usually 4 digits (e.g. 9845 means -98.45)
            lat_raw = int(part[:4])
            lon_raw = int(part[4:])

            lat = lat_raw / 100.0
            lon = lon_raw / 100.0

            # Central/Western US lons are negative
            if lon > 0:
                lon = -lon

            lats.append(lat)
            lons.append(lon)
        except Exception as e:
            logger.debug(f"[CENTROID] Coordinate parse failed for part {part!r}: {e}")
            continue

    if not lats:
        return None

    return sum(lats) / len(lats), sum(lons) / len(lons)


# ── IEM sounding functions ────────────────────────────────────────────────────

IEM_RAOB_URL = "https://mesonet.agron.iastate.edu/json/raob.py"

# Cache for station availability results (station_id -> (timestamp, times_list))
_AVAILABILITY_CACHE: dict = {}
AVAILABILITY_CACHE_TTL = 900  # 15 minutes


def _iem_level_is_valid(lv: dict) -> bool:
    """Per-level QC for IEM RAOB data. Rejects levels with physically
    implausible values that produce jagged hodographs or plot crashes."""
    try:
        pres = lv.get("pres")
        tmpc = lv.get("tmpc")
        dwpc = lv.get("dwpc")
        drct = lv.get("drct")
        sknt = lv.get("sknt")
        if None in (pres, tmpc, dwpc, drct, sknt):
            return False
        pres = float(pres)
        tmpc = float(tmpc)
        dwpc = float(dwpc)
        drct = float(drct)
        sknt = float(sknt)
        if not (1.0 <= pres <= 1100.0):
            return False
        if not (-120.0 <= tmpc <= 60.0):
            return False
        if not (-150.0 <= dwpc <= 60.0) or dwpc > tmpc + 0.5:
            return False
        if not (0.0 <= drct <= 360.0):
            return False
        if not (0.0 <= sknt <= 300.0):
            return False
    except (TypeError, ValueError):
        return False
    return True


def _iem_to_clean_data(profile: dict, station_id: str, station_name: str,
                        lat: float, lon: float, elev: float, valid: str) -> dict:
    """
    Convert IEM RAOB profile dict to SounderPy clean_data format.
    IEM fields: pres, hght, tmpc, dwpc, drct, sknt
    SounderPy fields: p, z, T, Td, u, v, site_info, titles
    """

    raw_count = len(profile) if profile else 0

    # Per-level QC — reject physically implausible values that cause jagged
    # hodographs or downstream plot crashes (issue #87).
    levels = [lv for lv in profile if _iem_level_is_valid(lv)]

    if not levels:
        return None

    # Sort by pressure descending (surface → top) and dedupe near-duplicate
    # pressures. IEM sometimes returns multiple wind vectors at the same
    # pressure, which produces starburst hodograph artifacts.
    levels.sort(key=lambda lv: float(lv["pres"]), reverse=True)
    deduped = []
    last_p = None
    for lv in levels:
        p = float(lv["pres"])
        if last_p is not None and abs(last_p - p) < 0.1:
            continue
        deduped.append(lv)
        last_p = p
    levels = deduped

    if len(levels) < raw_count:
        logger.debug(
            f"[IEM] QC dropped {raw_count - len(levels)}/{raw_count} levels "
            f"for {station_id}"
        )

    pres = np.array([lv["pres"] for lv in levels], dtype=float)
    hght = np.array([lv.get("hght") or 0 for lv in levels], dtype=float)
    tmpc = np.array([lv["tmpc"] for lv in levels], dtype=float)
    dwpc = np.array([lv["dwpc"] for lv in levels], dtype=float)

    # Convert wind direction/speed to u/v components
    drct = np.array([lv["drct"] for lv in levels], dtype=float)
    sknt = np.array([lv["sknt"] for lv in levels], dtype=float)
    u = -sknt * np.sin(np.deg2rad(drct))
    v = -sknt * np.cos(np.deg2rad(drct))

    # Parse valid time
    try:
        if "Z" in valid:
            dt = datetime.fromisoformat(valid.replace("Z", "+00:00"))
        else:
            # Handle possible alternate formats from IEM
            dt = datetime.strptime(valid, "%Y-%m-%dT%H:%M:%S")
            dt = dt.replace(tzinfo=timezone.utc)
            
        run_time = [str(dt.year), str(dt.month).zfill(2),
                    str(dt.day).zfill(2), f"{dt.hour:02d}:{dt.minute:02d}"]
    except Exception as e:
        logger.debug(f"[IEM] Datetime parse failed for {valid!r}: {e}")
        run_time = ["none", "none", "none", "none"]

    return {
        "p": pres * units("hPa"),
        "z": hght * units("meter"),
        "T": tmpc * units("degC"),
        "Td": dwpc * units("degC"),
        "u": u * units("knot"),
        "v": v * units("knot"),
        "site_info": {
            "site-id": station_id,
            "site-name": station_name,
            "site-lctn": "United States",
            "site-latlon": [lat, lon],
            "site-elv": str(int(elev)) if elev else "0",
            "source": "RAOB OBSERVED (IEM)",
            "model": "no-model",
            "fcst-hour": "no-fcst-hour",
            "run-time": run_time,
            "valid-time": run_time,
        },
        "titles": {
            "top_title": "RAOB OBSERVED VERTICAL PROFILE",
            "left_title": f"VALID: {run_time[1]}-{run_time[2]}-{run_time[0]} {run_time[3]}Z",
            "right_title": f"{station_id} - {station_name} | {lat:.2f}, {lon:.2f}",
        },
    }


async def fetch_iem_sounding(station_id: str, year: str, month: str,
                              day: str, hour: str,
                              station_name: str = "",
                              lat: float = 0, lon: float = 0,
                              elev: float = 0) -> Optional[dict]:
    """
    Fetch a sounding from IEM and convert to SounderPy clean_data format.
    Falls back to SounderPy (Wyoming) if IEM fails.
    """
    ts = f"{year}-{month}-{day}T{hour}:00:00Z"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                IEM_RAOB_URL,
                params={"station": station_id, "ts": ts},
                headers={"User-Agent": USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

        profiles = data.get("profiles", [])
        if not profiles or not profiles[0].get("profile"):
            return None

        profile_data = profiles[0]
        clean = _iem_to_clean_data(
            profile_data["profile"],
            station_id=station_id,
            station_name=station_name or station_id,
            lat=lat, lon=lon, elev=elev,
            valid=profile_data.get("valid", ts),
        )
        if clean:
            logger.debug(f"[IEM] Got sounding for {station_id} at {ts}")
        return clean
    except Exception as e:
        logger.debug(f"[IEM] Failed for {station_id} at {ts}: {e}")
        return None


async def get_available_sounding_times_iem(
    station_id: str,
    hours_back: int = 24,
    skip_cache: bool = False,
) -> list[tuple[str, str, str, str]]:
    """
    Check IEM for all available sounding times for a station
    in the last N hours. Returns list of (year, month, day, hour) tuples.
    Checks all hours concurrently for speed. Results are cached for 15 minutes.
    Use skip_cache=True for auto-posting tasks that need fresh data.
    """
    now = datetime.now(timezone.utc)

    # Check cache (skip for auto-post tasks)
    cache_key = f"{station_id}:{hours_back}"
    if not skip_cache and cache_key in _AVAILABILITY_CACHE:
        cached_time, cached_result = _AVAILABILITY_CACHE[cache_key]
        if (now - cached_time).total_seconds() < AVAILABILITY_CACHE_TTL:
            logger.info(f"[IEM] Cache hit for {station_id} availability — skipping IEM check")
            return cached_result

    async def check_hour(dt: datetime) -> Optional[tuple]:
        ts = dt.strftime("%Y-%m-%dT%H:00:00Z")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    IEM_RAOB_URL,
                    params={"station": station_id, "ts": ts},
                    headers={"User-Agent": USER_AGENT},
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
            profiles = data.get("profiles", [])
            if profiles and profiles[0].get("profile"):
                return (
                    str(dt.year),
                    str(dt.month).zfill(2),
                    str(dt.day).zfill(2),
                    str(dt.hour).zfill(2),
                )
        except Exception as e:
            logger.debug(f"[SOUNDING] IEM profile probe failed for {station_id}: {e}")
        return None

    times_to_check = [
        now - timedelta(hours=h)
        for h in range(hours_back + 1)
        if (now - timedelta(hours=h)) < now
    ]

    results = await asyncio.gather(*[check_hour(dt) for dt in times_to_check])
    found = [r for r in results if r is not None]
    # Sort most recent first
    found.sort(key=lambda x: (x[0], x[1], x[2], x[3]), reverse=True)
    # Store in cache
    _AVAILABILITY_CACHE[cache_key] = (now, found)
    return found


# ── ACARS functions ───────────────────────────────────────────────────────────

_ACARS_STATION_COORDS: dict = {}


async def get_acars_profiles_near(
    lat: float, lon: float,
    max_dist_km: float = 400,
    hours_back: int = 3,
) -> list[dict]:
    """
    Find available ACARS profiles near a location.
    Returns list of dicts sorted by distance.
    """
    # sounderpy import is deferred: importing it eagerly triggers
    # network I/O (station list fetch) that we want to avoid at startup.
    import sounderpy as spy  # noqa: PLC0415

    now = datetime.now(timezone.utc)
    results = []
    seen_airports = set()

    for h_back in range(hours_back + 1):
        check_time = now - timedelta(hours=h_back)
        year = check_time.strftime("%Y")
        month = check_time.strftime("%m")
        day = check_time.strftime("%d")
        hour = check_time.strftime("%H")

        try:
            loop = asyncio.get_running_loop()
            acars = await loop.run_in_executor(
                None, lambda y=year, mo=month, d=day, hr=hour: spy.acars_data(y, mo, d, hr)
            )
            profiles = await loop.run_in_executor(None, acars.list_profiles)
        except Exception as e:
            logger.debug(f"[ACARS] No profiles for {year}/{month}/{day} {hour}z: {e}")
            continue

        for profile_id in profiles:
            airport_code = profile_id.split("_")[0]
            if airport_code in seen_airports:
                continue

            airport_latlon = _ACARS_STATION_COORDS.get(airport_code)
            if airport_latlon is None:
                try:
                    # ACARS uses 3-letter codes; get_latlon needs K prefix for US airports
                    metar_code = airport_code if len(airport_code) == 4 else "K" + airport_code
                    latlon = await loop.run_in_executor(
                        None, lambda code=metar_code: spy.get_latlon("metar", code)
                    )
                    airport_latlon = (float(latlon[0]), float(latlon[1]))
                    _ACARS_STATION_COORDS[airport_code] = airport_latlon
                except Exception as e:
                    logger.debug(f"[ACARS] Airport lookup failed for {airport_code!r}: {e}")
                    continue

            dist = haversine(lat, lon, airport_latlon[0], airport_latlon[1])
            if dist <= max_dist_km:
                seen_airports.add(airport_code)
                time_part = profile_id.split("_")[1] if "_" in profile_id else hour + "00"
                results.append({
                    "profile_id": profile_id,
                    "airport": airport_code,
                    "name": airport_code,
                    "lat": airport_latlon[0],
                    "lon": airport_latlon[1],
                    "dist_km": round(dist, 1),
                    "year": year,
                    "month": month,
                    "day": day,
                    "acars_hour": hour,
                    "time_label": f"{time_part[:2]}:{time_part[2:]}z" if len(time_part) >= 4 else f"{time_part}z",
                })

    results.sort(key=lambda x: x["dist_km"])
    return results[:5]


async def get_acars_profiles_in_polygon(
    polygon,
    hours_back: int = 3,
    max_results: int = 25,
) -> list[dict]:
    """Find ACARS profiles whose airport sits inside ``polygon`` (EPSG:4326).

    Mirrors :func:`get_acars_profiles_near` but filters by polygon
    membership instead of distance. Used on MDT/HIGH risk days to sweep
    every airport inside the (buffered) categorical polygon — RAOB
    coverage alone misses the convective boundary layer detail that
    ACARS provides at hub airports.

    ``polygon`` should already include any desired buffer.
    """
    if polygon is None:
        return []

    import sounderpy as spy  # noqa: PLC0415
    from shapely.geometry import Point  # noqa: PLC0415

    now = datetime.now(timezone.utc)
    results: list[dict] = []
    seen_airports: set[str] = set()

    for h_back in range(hours_back + 1):
        check_time = now - timedelta(hours=h_back)
        year = check_time.strftime("%Y")
        month = check_time.strftime("%m")
        day = check_time.strftime("%d")
        hour = check_time.strftime("%H")

        try:
            loop = asyncio.get_running_loop()
            acars = await loop.run_in_executor(
                None, lambda y=year, mo=month, d=day, hr=hour: spy.acars_data(y, mo, d, hr)
            )
            profiles = await loop.run_in_executor(None, acars.list_profiles)
        except Exception as e:
            logger.debug(f"[ACARS] No profiles for {year}/{month}/{day} {hour}z: {e}")
            continue

        for profile_id in profiles:
            airport_code = profile_id.split("_")[0]
            if airport_code in seen_airports:
                continue

            airport_latlon = _ACARS_STATION_COORDS.get(airport_code)
            if airport_latlon is None:
                try:
                    metar_code = airport_code if len(airport_code) == 4 else "K" + airport_code
                    latlon = await loop.run_in_executor(
                        None, lambda code=metar_code: spy.get_latlon("metar", code)
                    )
                    airport_latlon = (float(latlon[0]), float(latlon[1]))
                    _ACARS_STATION_COORDS[airport_code] = airport_latlon
                except Exception as e:
                    logger.debug(f"[ACARS] Airport lookup failed for {airport_code!r}: {e}")
                    continue

            if not polygon.contains(Point(airport_latlon[1], airport_latlon[0])):
                continue

            seen_airports.add(airport_code)
            time_part = profile_id.split("_")[1] if "_" in profile_id else hour + "00"
            results.append({
                "profile_id": profile_id,
                "airport": airport_code,
                "name": airport_code,
                "lat": airport_latlon[0],
                "lon": airport_latlon[1],
                "year": year,
                "month": month,
                "day": day,
                "acars_hour": hour,
                "time_label": f"{time_part[:2]}:{time_part[2:]}z" if len(time_part) >= 4 else f"{time_part}z",
            })
            if len(results) >= max_results:
                return results

    return results


async def fetch_acars_sounding(
    profile_id: str,
    year: str, month: str, day: str, hour: str,
) -> Optional[dict]:
    """Fetch an ACARS sounding profile and return SounderPy clean_data."""
    import sounderpy as spy  # noqa: PLC0415  # deferred; see fetch_sounding
    loop = asyncio.get_running_loop()
    try:
        acars = await loop.run_in_executor(
            None, lambda: spy.acars_data(year, month, day, hour)
        )
        await loop.run_in_executor(None, acars.list_profiles)
        clean_data = await loop.run_in_executor(
            None, lambda: acars.get_profile(profile_id)
        )
        if validate_sounding_data(clean_data, min_levels=8): # ACARS needs a bit more depth
            return clean_data
        logger.warning(f"[ACARS] Profile {profile_id} failed validation (shallow or empty)")
        return None
    except Exception as e:
        logger.warning(f"[ACARS] Fetch failed for {profile_id}: {e}")
        return None

# ── SounderPy fetch and plot ──────────────────────────────────────────────────


async def filter_stations_with_data(stations: list[dict], n_times: int = 1) -> list[dict]:
    """
    Check each station in parallel against the single most recent sounding time.
    If the most recent time has no data, try the previous one — but all stations
    are checked concurrently to keep total wait time minimal.
    Returns only stations that have at least one available sounding.
    """
    times = get_recent_sounding_times(n_times)

    async def has_data(station: dict) -> tuple[dict, bool]:
        station_id = station.get("icao") or station.get("wmo")
        # Use IEM to check availability (faster, more reliable than Wyoming)
        available = await get_available_sounding_times_iem(station_id, hours_back=36)
        if available:
            return station, True
        # Fall back to Wyoming check
        results = await asyncio.gather(*[
            fetch_sounding(station_id, y, mo, d, h)
            for y, mo, d, h in times
        ])
        return station, any(r is not None for r in results)

    results = await asyncio.gather(*[has_data(s) for s in stations])
    return [s for s, ok in results if ok]

def validate_sounding_data(data: Optional[dict], min_levels: int = 5) -> bool:
    """Check if sounding data dict is valid and has enough levels for plotting."""
    if not data or not isinstance(data, dict):
        return False
    
    # Check for required SounderPy keys
    for key in ("p", "z", "T", "Td", "u", "v"):
        if key not in data or data[key] is None:
            return False
            
    # Check level count and array consistency
    try:
        p_len = len(data["p"])
        if p_len < min_levels:
            return False
            
        for key in ("z", "T", "Td", "u", "v"):
            if len(data[key]) != p_len:
                return False
    except (TypeError, KeyError) as e:
        logger.debug(f"[SOUNDING] Structural validation failed: {e}")
        return False
        
    # Check for sufficient valid data (prevent crashes in SounderPy/ecape-parcel)
    try:
        # Check if we have at least SOME non-zero wind data (prevent jagged hodographs)
        u_vals = np.asarray(getattr(data["u"], "magnitude", data["u"]), dtype=float)
        v_vals = np.asarray(getattr(data["v"], "magnitude", data["v"]), dtype=float)
        if np.all(np.isnan(u_vals) | (u_vals == 0)) and np.all(np.isnan(v_vals) | (v_vals == 0)):
            return False

        # Check temperature validity (prevent fmin/fmax errors on empty/NaN arrays)
        t_vals = np.asarray(getattr(data["T"], "magnitude", data["T"]), dtype=float)
        if np.all(np.isnan(t_vals)):
            return False
    except (KeyError, TypeError, ValueError) as e:
        logger.debug(f"[SOUNDING] Data validation check failed (accepting): {e}")

    return True


def sounding_quality_warning(data: Optional[dict]) -> Optional[str]:
    """
    Return a short human-readable warning string if the sounding is plottable
    but low-quality (sparse winds or shallow pressure coverage). Returns None
    when data looks healthy. Used to annotate Discord captions rather than
    suppress the plot entirely (issue #87).
    """
    if not data:
        return None
    try:
        u_vals = np.asarray(getattr(data["u"], "magnitude", data["u"]), dtype=float)
        v_vals = np.asarray(getattr(data["v"], "magnitude", data["v"]), dtype=float)
        p_vals = np.asarray(getattr(data["p"], "magnitude", data["p"]), dtype=float)

        finite_wind = np.isfinite(u_vals) & np.isfinite(v_vals) & np.isfinite(p_vals)
        n_wind = int(finite_wind.sum())
        if n_wind < 8:
            return f"⚠️ Low-quality data: only {n_wind} valid wind levels — hodograph may be sparse."

        wind_p = p_vals[finite_wind]
        span = float(wind_p.max() - wind_p.min())
        if span < 300.0:
            return f"⚠️ Low-quality data: wind coverage only spans {span:.0f} hPa — hodograph may be shallow."
    except (KeyError, TypeError, ValueError) as e:
        logger.debug(f"[SOUNDING] Quality assessment failed: {e}")
    return None

async def fetch_sounding(
    station_id: str,
    year: str, month: str, day: str, hour: str,
    station_name: str = "",
    lat: float = 0, lon: float = 0, elev: float = 0,
) -> Optional[dict]:
    """
    Fetch sounding data. Returns clean_data dict or None on failure.

    Strategy:
    - 00z/12z: Try Wyoming first (cleanest data), fall back to IEM only if 
                Wyoming is unavailable or invalid.
    - Other hours: IEM only (Wyoming doesn't have special soundings).
    """
    loop = asyncio.get_running_loop()

    if hour in ("00", "12"):
        # Preferred source: Wyoming
        logger.debug(f"[SOUNDING] Fetching Wyoming for {station_id} {hour}z")
        try:
            wyo_data = await loop.run_in_executor(
                None,
                lambda: spy.get_obs_data(station_id, year, month, day, hour)
            )
            if validate_sounding_data(wyo_data):
                logger.debug(f"[SOUNDING] Wyoming success for {station_id} {hour}z")
                return wyo_data
            else:
                logger.debug(f"[SOUNDING] Wyoming data invalid for {station_id} {hour}z")
        except Exception as e:
            logger.debug(f"[SOUNDING] Wyoming failed for {station_id} {hour}z: {e}")

        # Fallback source: IEM
        logger.debug(f"[SOUNDING] Falling back to IEM for {station_id} {hour}z")
        iem_data = await fetch_iem_sounding(
            station_id, year, month, day, hour,
            station_name=station_name, lat=lat, lon=lon, elev=elev
        )
        if validate_sounding_data(iem_data):
            return iem_data
        
        return None

    # Non-standard hours: IEM only
    iem_data = await fetch_iem_sounding(
        station_id, year, month, day, hour,
        station_name=station_name, lat=lat, lon=lon, elev=elev
    )
    if validate_sounding_data(iem_data):
        return iem_data
        
    return None

async def generate_plot(
    clean_data: dict,
    output_path: str,
    dark_mode: bool = False,
) -> bool:
    """Generate sounding plot headlessly. Returns True on success.

    Runs in a ProcessPoolExecutor worker so multiple plots can execute in
    parallel without matplotlib thread-safety concerns.
    """
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(
            _get_plot_executor(),
            _plot_sync,
            clean_data,
            output_path,
            dark_mode,
        )
        return True
    except ValueError as e:
        # SounderPy/MetPy raise ValueError with "zero-size array to reduction"
        # when upstream data quality is insufficient (issue #87). Treat as a
        # clean failure rather than a crash — validation should have caught
        # this, but guard in case a profile slips through.
        msg = str(e)
        if "zero-size array" in msg or "fmin" in msg or "fmax" in msg:
            logger.warning(
                f"[SOUNDING] Plot failed due to insufficient data quality: {e}"
            )
            return False
        logger.exception(f"[SOUNDING] Plot generation failed: {e}")
        return False
    except Exception as e:
        logger.exception(f"[SOUNDING] Plot generation failed: {e}")
        return False

def _plot_sync(clean_data: dict, output_path: str, dark_mode: bool):
    """Synchronous plot generation — runs in executor."""
    spy.build_sounding(
        clean_data,
        style="full",
        dark_mode=dark_mode,
        radar=None,
        map_zoom=0,
        save=True,
        filename=output_path,
    )
    plt.close("all")
