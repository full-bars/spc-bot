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
import pandas as pd
import sounderpy as spy

from config import CACHE_DIR

logger = logging.getLogger("spc_bot")

PREFS_FILE = os.path.join(CACHE_DIR, "sounding_prefs.json")
RAOB_STATIONS_URL = "https://raw.githubusercontent.com/kylejgillett/sounderpy/main/src/RAOB-STATIONS.txt"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "spc-bot-sounding/1.0"

# Cache the station list in memory so we don't fetch it every time
_station_cache: Optional[pd.DataFrame] = None


# ── User preferences ──────────────────────────────────────────────────────────

def load_prefs() -> dict:
    try:
        with open(PREFS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_prefs(prefs: dict):
    try:
        with open(PREFS_FILE, "w") as f:
            json.dump(prefs, f)
    except Exception as e:
        logger.warning(f"[SOUNDING] Failed to save prefs: {e}")

def get_user_dark_mode(user_id: int) -> bool:
    prefs = load_prefs()
    return prefs.get(str(user_id), {}).get("dark", False)

def set_user_dark_mode(user_id: int, dark: bool):
    prefs = load_prefs()
    prefs.setdefault(str(user_id), {})["dark"] = dark
    save_prefs(prefs)


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
        if hour not in (0, 12):
            raise ValueError("Hour must be 00 or 12")
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


# ── SounderPy fetch and plot ──────────────────────────────────────────────────


async def filter_stations_with_data(stations: list[dict], n_times: int = 2) -> list[dict]:
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
        # Check all times concurrently for this station
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
) -> Optional[dict]:
    """Fetch sounding data. Returns clean_data dict or None on failure."""
    loop = asyncio.get_running_loop()
    try:
        clean_data = await loop.run_in_executor(
            None,
            lambda: spy.get_obs_data(station_id, year, month, day, hour)
        )
        return clean_data
    except Exception as e:
        logger.warning(f"[SOUNDING] Fetch failed for {station_id} {year}/{month}/{day} {hour}z: {e}")
        return None

async def generate_plot(
    clean_data: dict,
    output_path: str,
    dark_mode: bool = False,
) -> bool:
    """Generate sounding plot headlessly. Returns True on success."""
    loop = asyncio.get_running_loop()
    try:
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
