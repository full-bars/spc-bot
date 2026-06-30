# cogs/sounding_utils.py
"""
Utility functions for the sounding cog:
- Location resolution (city, radar site, RAOB station)
- Nearest station lookup
- SounderPy data fetch and plot generation
- User preference persistence
"""

import asyncio
import io
import logging
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

from utils.geo import find_nearest_indices, haversine  # noqa: E402
from utils.http import (  # noqa: E402
    CircuitOpenError,
    circuit_breaker,
    ensure_session,
    http_get_json,
)
from utils.state_store import get_state, set_state  # noqa: E402  # follows sounderpy import

logger = logging.getLogger("spc_bot")

RAOB_STATIONS_URL = (
    "https://raw.githubusercontent.com/kylejgillett/sounderpy/main/src/RAOB-STATIONS.txt"
)
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "spc-bot-sounding/1.0"

# Cache the station list in memory so we don't fetch it every time
_station_cache: Optional[pd.DataFrame] = None
_station_cache_lock: asyncio.Lock = asyncio.Lock()


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

    async with _station_cache_lock:
        if _station_cache is not None:
            return _station_cache
        loop = asyncio.get_running_loop()
        df = await loop.run_in_executor(None, _fetch_stations)
        _station_cache = df
    return _station_cache


def get_sounding_params_text(clean_data: dict) -> Optional[str]:
    """Extract all computed thermodynamic and kinematic parameters for AI analysis."""
    try:
        import sounderpy as spy
        import os

        # SounderPy/SHARPPy can be noisy on stdout
        old_stdout = sys.stdout
        sys.stdout = open(os.devnull, "w")
        try:
            params = spy.sounding_params(clean_data).calc()
        finally:
            sys.stdout.close()
            sys.stdout = old_stdout

        thermo = params[1]
        kinematics = params[2]

        def _fmt_val(val, unit=""):
            """Format any value, handling masked/missing cases. Show actual values, not filters."""
            if val is None:
                return "N/A"
            if hasattr(val, "mask") and val.mask:
                return "N/A"
            if str(val) == "--":
                return "N/A"
            try:
                if isinstance(val, (int, float)):
                    return f"{float(val):.0f}" + (f" {unit}" if unit else "")
                return str(val)
            except (ValueError, TypeError):
                return "N/A"

        summary = "SOUNDING PARAMETERS:\n\n"
        summary += "--- THERMODYNAMICS ---\n"

        # Include all CAPE variants (some may be N/A, but show everything)
        summary += f"SBCAPE: {_fmt_val(thermo.get('sbcape'), 'J/kg')} | "
        summary += f"MUCAPE: {_fmt_val(thermo.get('mucape'), 'J/kg')} | "
        summary += f"MLCAPE: {_fmt_val(thermo.get('mlcape'), 'J/kg')}\n"

        # Low-level CAPE (critical for incomplete profiles)
        summary += f"SB 0-3km CAPE: {_fmt_val(thermo.get('sb3cape'), 'J/kg')} | "
        summary += f"MU 0-3km CAPE: {_fmt_val(thermo.get('mu3cape'), 'J/kg')}\n"

        # CIN measures
        summary += f"SBCIN: {_fmt_val(thermo.get('sbcin'), 'J/kg')} | "
        summary += f"MUCIN: {_fmt_val(thermo.get('mucin'), 'J/kg')}\n"

        # Downdraft and elevated convection
        summary += f"DCAPE: {_fmt_val(thermo.get('dcape'), 'J/kg')} | "
        summary += f"MUECAPE: {_fmt_val(thermo.get('mu_ecape'), 'J/kg')}\n"

        # LCL/LFC in pressure coordinates (higher pressure value = closer to surface = favorable for surface convection)
        summary += f"SB LCL Pressure: {_fmt_val(thermo.get('sb_lcl_p'), 'hPa')} | "
        summary += f"SB LFC Pressure: {_fmt_val(thermo.get('sb_lfc_p'), 'hPa')}\n"

        # Lapse rates
        summary += f"Lapse Rate (0-3km): {_fmt_val(thermo.get('lr_03km'), 'K/km')} | "
        summary += f"Lapse Rate (3-6km): {_fmt_val(thermo.get('lr_36km'), 'K/km')}\n\n"

        summary += "--- KINEMATICS ---\n"
        summary += f"Effective Inflow Layer (EIL): {_fmt_val(kinematics.get('eil_z'), 'mb')}\n"
        summary += f"Bulk Shear (0-1km): {_fmt_val(kinematics.get('shear_0_to_1000'), 'kts')} | "
        summary += f"Bulk Shear (0-6km): {_fmt_val(kinematics.get('shear_0_to_6000'), 'kts')}\n"
        summary += f"SRH (0-1km): {_fmt_val(kinematics.get('srh_0_to_1000'), 'm²/s²')} | "
        summary += f"SRH (0-3km): {_fmt_val(kinematics.get('srh_0_to_3000'), 'm²/s²')}\n"
        summary += f"STP (Effective): {_fmt_val(kinematics.get('eil_stp'))} | "
        summary += f"SCP: {_fmt_val(kinematics.get('eil_scp'))}\n"

        return summary
    except Exception as e:
        logger.debug(f"[SOUNDING] Failed to compute parameters for AI: {e}")
        return None


def _fetch_stations() -> pd.DataFrame:
    df = pd.read_csv(
        RAOB_STATIONS_URL,
        skiprows=8,
        sep=",",
        names=["WMO", "ICAO", "NAME", "LOC", "EL", "LAT", "A", "LON", "B", "X"],
        skipinitialspace=True,
    )
    df = df[pd.to_numeric(df["LAT"], errors="coerce").notna()].copy()

    # Strip string columns once at load so lookups never need per-call .strip()
    df["ICAO"] = df["ICAO"].str.strip()
    df["NAME"] = df["NAME"].str.strip()
    df["LOC"] = df["LOC"].str.strip()
    df["A"] = df["A"].str.strip()
    df["B"] = df["B"].str.strip()

    # Vectorized hemisphere sign: N/E → +1, S/W → -1
    df["lat"] = np.where(
        df["A"].isin(("N", "E")), df["LAT"].astype(float), -df["LAT"].astype(float)
    )
    df["lon"] = np.where(
        df["B"].isin(("N", "E")), df["LON"].astype(float), -df["LON"].astype(float)
    )
    return df


def find_nearest_stations(lat: float, lon: float, df: pd.DataFrame, n: int = 3) -> list[dict]:
    """Return the n nearest RAOB stations as a list of dicts."""
    targets = list(zip(df["lat"], df["lon"]))
    nearest_data = find_nearest_indices(lat, lon, targets, n)

    results = []
    for idx, dist_km in nearest_data:
        row = df.iloc[idx]
        # Columns are pre-stripped at station-list load; no per-call .strip() needed
        icao = str(row["ICAO"])
        results.append(
            {
                "icao": icao if icao != "----" else None,
                "wmo": str(row["WMO"]),
                "name": str(row["NAME"]),
                "loc": str(row["LOC"]),
                "lat": row["lat"],
                "lon": row["lon"],
                "dist_km": round(dist_km, 1),
            }
        )
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
    from urllib.parse import urlparse

    host = urlparse(NOMINATIM_URL).netloc
    if circuit_breaker.is_open(host):
        raise CircuitOpenError(f"Circuit breaker is open for {host}")
    session = await ensure_session()
    async with session.get(
        NOMINATIM_URL,
        params={"q": query, "format": "json", "limit": 1},
        headers={"User-Agent": USER_AGENT},
        timeout=aiohttp.ClientTimeout(total=10),
    ) as resp:
        data = await resp.json()
        circuit_breaker.record_success(host)

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
            dt = now.replace(hour=hour, minute=0, second=0, microsecond=0) - timedelta(
                days=days_back
            )
            if dt < now:
                times.append(
                    (
                        str(dt.year),
                        str(dt.month).zfill(2),
                        str(dt.day).zfill(2),
                        str(dt.hour).zfill(2),
                    )
                )
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

    async with aiohttp.ClientSession(headers={"User-Agent": "spc-bot-sounding/1.0"}) as session:
        for zone_url in affected_zones[:10]:  # cap at 10 zones
            try:
                async with session.get(zone_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
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
        part = coords_str[i : i + 8]
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


def _iem_to_clean_data(
    profile: dict,
    station_id: str,
    station_name: str,
    lat: float,
    lon: float,
    elev: float,
    valid: str,
) -> dict | None:
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
            f"[IEM] QC dropped {raw_count - len(levels)}/{raw_count} levels for {station_id}"
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

        run_time = [
            str(dt.year),
            str(dt.month).zfill(2),
            str(dt.day).zfill(2),
            f"{dt.hour:02d}:{dt.minute:02d}",
        ]
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
            "site-elv": str(int(elev)) if pd.notna(elev) else "0",
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


async def fetch_iem_sounding(
    station_id: str,
    year: str,
    month: str,
    day: str,
    hour: str,
    station_name: str = "",
    lat: float = 0,
    lon: float = 0,
    elev: float = 0,
) -> Optional[dict]:
    """
    Fetch a sounding from IEM and convert to SounderPy clean_data format.
    Falls back to SounderPy (Wyoming) if IEM fails.
    """
    # Skip 5-digit WMO IDs as IEM's json/raob.py has a 4-char limit
    if station_id.isdigit() and len(station_id) > 4:
        return None

    ts = f"{year}-{month}-{day}T{hour}:00:00Z"
    url = f"{IEM_RAOB_URL}?station={station_id}&ts={ts}"
    try:
        data = await http_get_json(url, retries=1, timeout=15)
        if not data:
            return None

        profiles = data.get("profiles", [])
        if not profiles or not profiles[0].get("profile"):
            return None

        profile_data = profiles[0]
        clean = _iem_to_clean_data(
            profile_data["profile"],
            station_id=station_id,
            station_name=station_name or station_id,
            lat=lat,
            lon=lon,
            elev=elev,
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
    Limits concurrency to avoid overwhelming IEM's rate limits. Results are cached for 15 minutes.
    """
    now = datetime.now(timezone.utc)

    # Check cache
    cache_key = f"{station_id}:{hours_back}"
    if not skip_cache and cache_key in _AVAILABILITY_CACHE:
        cached_time, cached_result = _AVAILABILITY_CACHE[cache_key]
        if (now - cached_time).total_seconds() < AVAILABILITY_CACHE_TTL:
            logger.info(f"[IEM] Cache hit for {station_id} availability — skipping IEM check")
            return cached_result

    # Skip 5-digit WMO IDs as IEM's json/raob.py has a 4-char limit
    # and auto-prepends 'K' to numeric IDs, triggering 422 errors.
    if station_id.isdigit() and len(station_id) > 4:
        _AVAILABILITY_CACHE[cache_key] = (now, [])
        return []

    async def check_hour(dt: datetime, semaphore: asyncio.Semaphore) -> Optional[tuple]:
        async with semaphore:
            ts = dt.strftime("%Y-%m-%dT%H:00:00Z")
            url = f"{IEM_RAOB_URL}?station={station_id}&ts={ts}"
            try:
                data = await http_get_json(url, retries=1, timeout=8)
                if not data:
                    return None
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

    times_to_check = [now - timedelta(hours=h) for h in range(hours_back + 1)]

    # Limit concurrency to 5 simultaneous requests to avoid overwhelming IEM
    semaphore = asyncio.Semaphore(5)
    results = await asyncio.gather(*[check_hour(dt, semaphore) for dt in times_to_check])
    found = [r for r in results if r is not None]

    # Sort most recent first
    found.sort(key=lambda x: (x[0], x[1], x[2], x[3]), reverse=True)
    # Store in cache; evict expired entries if cache grows too large
    _AVAILABILITY_CACHE[cache_key] = (now, found)
    if len(_AVAILABILITY_CACHE) > 2000:
        expired = [
            k
            for k, (ts, _) in _AVAILABILITY_CACHE.items()
            if (now - ts).total_seconds() >= AVAILABILITY_CACHE_TTL
        ]
        for k in expired:
            del _AVAILABILITY_CACHE[k]
    return found


# ── ACARS functions ───────────────────────────────────────────────────────────

_ACARS_STATION_COORDS: dict = {}


async def get_acars_profiles_near(
    lat: float,
    lon: float,
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
                # High-precision deduplication key used by SoundingCog
                pkey = f"acars:{airport_code}:{year}{month}{day}_{time_part}z"
                results.append(
                    {
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
                        "pkey": pkey,
                        "time_label": f"{time_part[:2]}:{time_part[2:]}z"
                        if len(time_part) >= 4
                        else f"{time_part}z",
                    }
                )

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

    from utils.spc_outlook import is_inside_polygon

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

            if is_inside_polygon(airport_latlon[0], airport_latlon[1], polygon):
                seen_airports.add(airport_code)
                time_part = profile_id.split("_")[1] if "_" in profile_id else hour + "00"
                # High-precision deduplication key used by SoundingCog
                pkey = f"acars:{airport_code}:{year}{month}{day}_{time_part}z"
                results.append(
                    {
                        "profile_id": profile_id,
                        "airport": airport_code,
                        "name": airport_code,
                        "lat": airport_latlon[0],
                        "lon": airport_latlon[1],
                        "year": year,
                        "month": month,
                        "day": day,
                        "acars_hour": hour,
                        "pkey": pkey,
                        "time_label": f"{time_part[:2]}:{time_part[2:]}z"
                        if len(time_part) >= 4
                        else f"{time_part}z",
                    }
                )
            if len(results) >= max_results:
                return results

    return results


def _fsl_to_clean_data(
    text: str,
    station_id: str,
    station_name: str,
    lat: float,
    lon: float,
    elev: float,
    run_time: list[str],
) -> Optional[dict]:
    """Parse GSL 'FSL' format ASCII text into SounderPy clean_data."""
    lines = text.splitlines()
    levels = []

    # FSL format data lines (Type 4, 5, 6) have 7 fields:
    # Type, Pressure (1/10 mb), Height (m), Temp (1/10 C), Dewpt (1/10 C), Wind Dir (deg), Wind Spd (kt)
    for line in lines:
        parts = line.split()
        if len(parts) < 7:
            continue
        try:
            ltype = int(parts[0])
            if ltype not in (4, 5, 6):
                continue

            p_raw = int(parts[1])
            z_raw = int(parts[2])
            t_raw = int(parts[3])
            td_raw = int(parts[4])
            wdir_raw = int(parts[5])
            wspd_raw = int(parts[6])

            if p_raw == 99999 or z_raw == 99999:
                continue

            levels.append(
                {
                    "pres": p_raw / 10.0,
                    "hght": float(z_raw),
                    "tmpc": t_raw / 10.0 if t_raw != 99999 else np.nan,
                    "dwpc": td_raw / 10.0 if td_raw != 99999 else np.nan,
                    "drct": float(wdir_raw) if wdir_raw != 99999 else np.nan,
                    "sknt": float(wspd_raw) if wspd_raw != 99999 else np.nan,
                }
            )
        except (ValueError, IndexError):
            continue

    if not levels:
        return None

    # Standardize: sort descending pressure and deduplicate near-duplicate pressures
    levels.sort(key=lambda lv: lv["pres"], reverse=True)
    deduped = []
    last_p = None
    for lv in levels:
        if last_p is not None and abs(last_p - lv["pres"]) < 0.1:
            continue
        deduped.append(lv)
        last_p = lv["pres"]
    levels = deduped

    pres = np.array([lv["pres"] for lv in levels])
    hght = np.array([lv["hght"] for lv in levels])
    tmpc = np.array([lv["tmpc"] for lv in levels])
    dwpc = np.array([lv["dwpc"] for lv in levels])
    drct = np.array([lv["drct"] for lv in levels])
    sknt = np.array([lv["sknt"] for lv in levels])

    # Wind components
    u = -sknt * np.sin(np.deg2rad(drct))
    v = -sknt * np.cos(np.deg2rad(drct))

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
            "site-elv": str(int(elev)) if pd.notna(elev) else "0",
            "source": "RAOB OBSERVED (GSL)",
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


async def fetch_gsl_sounding(
    station_id: str,
    year: str,
    month: str,
    day: str,
    hour: str,
    station_name: str = "",
    lat: float = 0,
    lon: float = 0,
    elev: float = 0,
) -> Optional[dict]:
    """
    Fetch a sounding from NOAA/GSL and convert to SounderPy clean_data format.
    Uses the rucsoundings.noaa.gov ASCII 'FSL' format.
    """
    month_map = {
        "01": "JAN",
        "02": "FEB",
        "03": "MAR",
        "04": "APR",
        "05": "MAY",
        "06": "JUN",
        "07": "JUL",
        "08": "AUG",
        "09": "SEP",
        "10": "OCT",
        "11": "NOV",
        "12": "DEC",
    }
    month_name = month_map.get(month, "JAN")

    url = (
        "https://rucsoundings.noaa.gov/get_raobs.cgi?"
        f"data_source=RAOB&latest=latest&start_year={year}&"
        f"start_month_name={month_name}&start_mday={day}&"
        f"start_hour={hour}&start_min=0&n_hrs=1.0&fcst_len=0&"
        f"select_mdstns={station_id}&fsl_data_last=yes"
    )

    try:
        from utils.http import http_get_text

        text = await http_get_text(url, retries=1, timeout=15)
        if not text or "No data available" in text:
            return None

        clean = _fsl_to_clean_data(
            text,
            station_id,
            station_name or station_id,
            lat,
            lon,
            elev,
            [year, month, day, f"{hour}:00"],
        )
        if clean:
            logger.debug(f"[GSL] Got sounding for {station_id} at {year}-{month}-{day} {hour}z")
        return clean
    except Exception as e:
        logger.debug(f"[GSL] Failed for {station_id} at {hour}z: {e}")
        return None


async def fetch_acars_sounding(
    profile_id: str,
    year: str,
    month: str,
    day: str,
    hour: str,
) -> Optional[dict]:
    """Fetch an ACARS sounding profile and return SounderPy clean_data."""
    import sounderpy as spy  # noqa: PLC0415  # deferred; see fetch_sounding

    loop = asyncio.get_running_loop()
    try:
        acars = await loop.run_in_executor(None, lambda: spy.acars_data(year, month, day, hour))
        await loop.run_in_executor(None, acars.list_profiles)
        clean_data = await loop.run_in_executor(None, lambda: acars.get_profile(profile_id))
        if validate_sounding_data(clean_data, min_levels=8):  # ACARS needs a bit more depth
            return clean_data
        logger.warning(f"[ACARS] Profile {profile_id} failed validation (shallow or empty)")
        return None
    except Exception as e:
        logger.warning(f"[ACARS] Fetch failed for {profile_id}: {e}")
        return None


# ── SounderPy fetch and plot ──────────────────────────────────────────────────


async def get_available_sounding_times(
    station_id: str,
    hours_back: int = 24,
    skip_cache: bool = False,
) -> list[tuple[str, str, str, str]]:
    """
    Unified availability check: Try IEM first, fall back to a direct
    Wyoming/GSL probe for standard hours.
    """
    # 1. Try IEM (Fastest, covers all hours)
    avail = await get_available_sounding_times_iem(station_id, hours_back, skip_cache=skip_cache)
    if avail:
        return avail

    # 2. Try Wyoming Probe for standard hours (00z/12z)
    # This is needed for international stations like SBSM that aren't in IEM.
    # US/Canada domestic stations (K/P/C prefix) are fully covered by IEM — skip
    # the probe for them to avoid slow full-chain fetches when IEM has no data.
    if station_id and (station_id[0].upper() in ("K", "P", "C") or station_id.isdigit()):
        return []

    probe_times = get_recent_sounding_times(n=max(2, (hours_back // 12) + 1))

    # Filter probe times to only those within hours_back
    now = datetime.now(timezone.utc)
    valid_probe_times = []
    for y, mo, d, h in probe_times:
        dt = datetime(int(y), int(mo), int(d), int(h), tzinfo=timezone.utc)
        if (now - dt).total_seconds() / 3600 <= hours_back:
            valid_probe_times.append((y, mo, d, h))

    if not valid_probe_times:
        return []

    # Parallel probe Wyoming/GSL
    results = await asyncio.gather(
        *[fetch_sounding(station_id, y, mo, d, h) for y, mo, d, h in valid_probe_times]
    )

    return [valid_probe_times[i] for i, res in enumerate(results) if res is not None]


async def filter_stations_with_data(stations: list[dict]) -> list[dict]:
    """
    Check each station in parallel for available sounding data.
    Uses unified availability check (IEM + Wyoming probe).
    """

    async def has_data(station: dict) -> tuple[dict, bool]:
        station_id = station.get("icao") or station.get("wmo")
        available = await get_available_sounding_times(station_id, hours_back=24)
        return station, len(available) > 0

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
    year: str,
    month: str,
    day: str,
    hour: str,
    station_name: str = "",
    lat: float = 0,
    lon: float = 0,
    elev: float = 0,
) -> Optional[dict]:
    """
    Fetch sounding data with multi-source fallback and circuit-awareness.
    Returns clean_data dict or None on failure.

    Strategy:
    1. Standard Hours (00z/12z): Try Wyoming first (cleanest data).
    2. IEM: Try IEM if Wyoming fails or for non-standard hours.
    3. GSL: High-authority redundant fallback if IEM is down or circuit is open.
    """
    loop = asyncio.get_running_loop()

    # 1. Wyoming (Preferred for standard hours)
    if hour in ("00", "12"):
        logger.debug(f"[SOUNDING] Fetching Wyoming for {station_id} {hour}z")
        try:
            # SounderPy retries Wyoming up to 10 times internally — cap it so
            # we can fall through to IEM when Wyoming is unreachable.
            wyo_data = await asyncio.wait_for(
                loop.run_in_executor(
                    None, lambda: spy.get_obs_data(station_id, year, month, day, hour)
                ),
                timeout=20.0,
            )
            if validate_sounding_data(wyo_data):
                logger.debug(f"[SOUNDING] Wyoming success for {station_id} {hour}z")
                return wyo_data
        except asyncio.TimeoutError:
            logger.debug(
                f"[SOUNDING] Wyoming timed out for {station_id} {hour}z, falling through to IEM"
            )
        except Exception as e:
            logger.debug(f"[SOUNDING] Wyoming failed for {station_id} {hour}z: {e}")

    # 2. IEM (Primary source for all other times, fallback for standard hours)
    iem_host = "mesonet.agron.iastate.edu"
    if not circuit_breaker.is_open(iem_host):
        logger.debug(f"[SOUNDING] Fetching IEM for {station_id} {hour}z")
        iem_data = await fetch_iem_sounding(
            station_id,
            year,
            month,
            day,
            hour,
            station_name=station_name,
            lat=lat,
            lon=lon,
            elev=elev,
        )
        if validate_sounding_data(iem_data):
            return iem_data
    else:
        logger.info(f"[SOUNDING] IEM circuit open — skipping to GSL for {station_id}")

    # 3. GSL (High-authority redundant fallback)
    logger.debug(f"[SOUNDING] Fetching GSL fallback for {station_id} {hour}z")
    gsl_data = await fetch_gsl_sounding(
        station_id, year, month, day, hour, station_name=station_name, lat=lat, lon=lon, elev=elev
    )
    if validate_sounding_data(gsl_data):
        return gsl_data

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
    from utils.worker_pool import get_sounding_executor

    loop = asyncio.get_running_loop()
    try:
        # Wrap in wait_for to prevent infinite hangs in the subprocess
        await asyncio.wait_for(
            loop.run_in_executor(
                get_sounding_executor(),
                _plot_sync,
                clean_data,
                output_path,
                dark_mode,
            ),
            timeout=60.0,
        )
        return True
    except asyncio.TimeoutError:
        logger.error(f"[SOUNDING] Plot generation timed out for {output_path}")
        return False
    except ValueError as e:
        # SounderPy/MetPy raise ValueError with "zero-size array to reduction"
        # when upstream data quality is insufficient (issue #87). Treat as a
        # clean failure rather than a crash — validation should have caught
        # this, but guard in case a profile slips through.
        msg = str(e)
        if "zero-size array" in msg or "fmin" in msg or "fmax" in msg:
            logger.warning(f"[SOUNDING] Plot failed due to insufficient data quality: {e}")
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
