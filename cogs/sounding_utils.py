# cogs/sounding_utils.py
"""
Utility functions for the sounding cog:
- Location resolution (city, radar site, RAOB station)
- Nearest station lookup
- SounderPy data fetch and plot generation
- User preference persistence
"""

import asyncio
import json
import logging
import math
import os
from datetime import datetime, timezone
from typing import Optional

import aiohttp
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Lock to serialize matplotlib plot generation (plt is not thread-safe)
_PLOT_LOCK = asyncio.Lock()
import pandas as pd
import io
import sys

# Suppress SounderPy's startup banner
_stdout = sys.stdout
sys.stdout = io.StringIO()
try:
    import sounderpy as spy
finally:
    sys.stdout = _stdout

from config import CACHE_DIR
from utils.db import get_state, set_state

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
    except Exception:
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
        except Exception:
            pass

        # Try as METAR/radar site (K-prefix 4-letter)
        if len(loc) == 4 and loc.startswith("K"):
            try:
                latlon = await asyncio.get_running_loop().run_in_executor(
                    None, spy.get_latlon, "metar", loc
                )
                return float(latlon[0]), float(latlon[1]), f"radar site {loc}"
            except Exception:
                pass

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
    except Exception:
        raise ValueError(
            f"Invalid time format: **{time_str}**\n"
            f"Use: `MM-DD-YYYY 00z` or `MM-DD-YYYY 12z`\n"
            f"Example: `04-10-2026 12z`"
        )

def get_recent_sounding_times(n: int = 4) -> list[tuple[str, str, str, str]]:
    """
    Return the n most recent 00z/12z sounding times that are in the past.
    """
    from datetime import timedelta
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
    import aiohttp
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


# ── IEM sounding functions ────────────────────────────────────────────────────

IEM_RAOB_URL = "https://mesonet.agron.iastate.edu/json/raob.py"

# Cache for station availability results (station_id -> (timestamp, times_list))
_AVAILABILITY_CACHE: dict = {}
AVAILABILITY_CACHE_TTL = 900  # 15 minutes


def _iem_to_clean_data(profile: dict, station_id: str, station_name: str,
                        lat: float, lon: float, elev: float, valid: str) -> dict:
    """
    Convert IEM RAOB profile dict to SounderPy clean_data format.
    IEM fields: pres, hght, tmpc, dwpc, drct, sknt
    SounderPy fields: p, z, T, Td, u, v, site_info, titles
    """
    import numpy as np
    from metpy.units import units

    levels = [lv for lv in profile if lv.get("pres") is not None
              and lv.get("tmpc") is not None
              and lv.get("dwpc") is not None]

    if not levels:
        return None

    pres = np.array([lv["pres"] for lv in levels], dtype=float)
    hght = np.array([lv.get("hght") or 0 for lv in levels], dtype=float)
    tmpc = np.array([lv["tmpc"] for lv in levels], dtype=float)
    dwpc = np.array([lv["dwpc"] for lv in levels], dtype=float)

    # Convert wind direction/speed to u/v components
    drct = np.array([lv.get("drct") or 0 for lv in levels], dtype=float)
    sknt = np.array([lv.get("sknt") or 0 for lv in levels], dtype=float)
    u = -sknt * np.sin(np.deg2rad(drct))
    v = -sknt * np.cos(np.deg2rad(drct))

    # Parse valid time
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(valid.replace("Z", "+00:00"))
        time_str = dt.strftime("%Hz %d %b %Y").upper()
        run_time = [str(dt.year), str(dt.month).zfill(2),
                    str(dt.day).zfill(2), f"{dt.hour:02d}:{dt.minute:02d}"]
    except Exception:
        time_str = valid
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
    from datetime import timedelta
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
        except Exception:
            pass
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
    import sounderpy as spy
    from datetime import timedelta

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
                except Exception:
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


async def fetch_acars_sounding(
    profile_id: str,
    year: str, month: str, day: str, hour: str,
) -> Optional[dict]:
    """Fetch an ACARS sounding profile and return SounderPy clean_data."""
    import sounderpy as spy
    loop = asyncio.get_running_loop()
    try:
        acars = await loop.run_in_executor(
            None, lambda: spy.acars_data(year, month, day, hour)
        )
        await loop.run_in_executor(None, acars.list_profiles)
        clean_data = await loop.run_in_executor(
            None, lambda: acars.get_profile(profile_id)
        )
        return clean_data
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
    import asyncio
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

async def fetch_sounding(
    station_id: str,
    year: str, month: str, day: str, hour: str,
    station_name: str = "",
    lat: float = 0, lon: float = 0, elev: float = 0,
) -> Optional[dict]:
    """
    Fetch sounding data. Returns clean_data dict or None on failure.

    Strategy:
    - 00z/12z: Try Wyoming first (better hodograph data quality),
                fall back to IEM if Wyoming fails.
    - Other hours: IEM only (Wyoming doesn't have special soundings).
    """
    loop = asyncio.get_running_loop()

    if hour in ("00", "12"):
        # Race Wyoming and IEM simultaneously — whichever returns valid data first wins.
        # Wyoming has quality preference: if both succeed, Wyoming result is used.
        async def _try_wyoming():
            try:
                data = await loop.run_in_executor(
                    None,
                    lambda: spy.get_obs_data(station_id, year, month, day, hour)
                )
                return data
            except Exception as e:
                logger.debug(f"[SOUNDING] Wyoming failed for {station_id} {hour}z: {e}")
                return None

        async def _try_iem():
            return await fetch_iem_sounding(
                station_id, year, month, day, hour,
                station_name=station_name, lat=lat, lon=lon, elev=elev
            )

        wyoming_task = asyncio.create_task(_try_wyoming())
        iem_task = asyncio.create_task(_try_iem())

        done, pending = await asyncio.wait(
            [wyoming_task, iem_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Check first result
        first = done.pop()
        result = first.result()
        if result:
            # Cancel the slower one and return
            for t in pending:
                t.cancel()
            source = "Wyoming" if first is wyoming_task else "IEM"
            logger.debug(f"[SOUNDING] {source} won race for {station_id} {hour}z")
            return result

        # First result was None — wait for the second
        if pending:
            second = pending.pop()
            try:
                result = await second
                if result:
                    source = "Wyoming" if second is wyoming_task else "IEM"
                    logger.debug(f"[SOUNDING] {source} fallback for {station_id} {hour}z")
                    return result
            except Exception:
                pass
        return None

    # Non-standard hours: IEM only (Wyoming doesn't carry special soundings)
    iem_data = await fetch_iem_sounding(
        station_id, year, month, day, hour,
        station_name=station_name, lat=lat, lon=lon, elev=elev
    )
    return iem_data

async def generate_plot(
    clean_data: dict,
    output_path: str,
    dark_mode: bool = False,
) -> bool:
    """Generate sounding plot headlessly. Returns True on success."""
    loop = asyncio.get_running_loop()
    try:
        async with _PLOT_LOCK:
          await loop.run_in_executor(
            None,
            lambda: _plot_sync(clean_data, output_path, dark_mode)
        )
        return True
    except Exception as e:
        logger.error(f"[SOUNDING] Plot generation failed: {e}", exc_info=True)
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
