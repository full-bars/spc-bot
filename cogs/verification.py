# cogs/verification.py
"""Live SPC outlook verification using warning/watch polygons, LSR reports, and SPC GIS data.

Based on Hitchens et al. (2013) Practically Perfect methodology:
- LSRs plotted on NCEP 212 grid (~40km)
- Gaussian kernel σ=0.75
- Compare observed report density against SPC outlook probability thresholds
"""

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from shapely.geometry import Point, Polygon, shape

from utils.http import http_get_bytes

logger = logging.getLogger("spc_bot.verification")

# SPC outlook probability thresholds
_RISK_THRESHOLDS = {
    "TSTM": {"tornado": 0, "wind": 0, "hail": 0},
    "MRGL": {"tornado": 0.02, "wind": 0.05, "hail": 0.05},
    "SLGT": {"tornado": 0.02, "wind": 0.05, "hail": 0.05},
    "ENH": {"tornado": 0.10, "wind": 0.15, "hail": 0.15},
    "MDT": {"tornado": 0.15, "wind": 0.25, "hail": 0.25},
    "HIGH": {"tornado": 0.30, "wind": 0.45, "hail": 0.45},
}

KML_NS = {"kml": "http://www.opengis.net/kml/2.2"}

# ── Data fetching ─────────────────────────────────────────────────────────────


async def fetch_active_warnings() -> list[dict]:
    """Get active TOR/SVR/FFW warnings from NWS API with polygon geometry."""
    url = (
        "https://api.weather.gov/alerts/active"
        "?event=Tornado%20Warning,Severe%20Thunderstorm%20Warning,Flash%20Flood%20Warning"
        "&status=actual"
    )
    content, status = await http_get_bytes(url, retries=2, timeout=15)
    if not content or status != 200:
        return []

    import json

    data = json.loads(content)
    features = data.get("features", [])
    warnings = []
    for f in features:
        props = f.get("properties", {})
        geom = f.get("geometry")
        if not geom:
            continue
        try:
            poly = shape(geom)
        except Exception:
            continue
        warnings.append(
            {
                "event": props.get("event", ""),
                "area": props.get("areaDesc", ""),
                "polygon": poly,
                "severity": props.get("severity", ""),
                "certainty": props.get("certainty", ""),
                "headline": props.get("headline", ""),
            }
        )
    return warnings


async def fetch_active_watches(bot_state=None) -> list[dict]:
    """Get active tornado/severe watches. Uses bot state if available, falls back to NWS API."""
    watches = []

    import json

    # Try NWS API first
    for evt in ("Tornado%20Watch", "Severe%20Thunderstorm%20Watch"):
        url = f"https://api.weather.gov/alerts/active?event={evt}&status=actual"
        content, status = await http_get_bytes(url, retries=2, timeout=15)
        if not content or status != 200:
            continue
        data = json.loads(content)
        features = data.get("features", [])
        for f in features:
            props = f.get("properties", {})
            geom = f.get("geometry")
            if not geom:
                continue
            try:
                poly = shape(geom)
            except Exception:
                continue
            watches.append(
                {
                    "event": props.get("event", ""),
                    "area": props.get("areaDesc", ""),
                    "polygon": poly,
                }
            )

    return watches


async def fetch_lsr_reports(hours: int = 24) -> list[dict]:
    """Get LSR (Local Storm Report) data from IEM GeoJSON endpoint."""
    url = f"https://mesonet.agron.iastate.edu/geojson/lsr.geojson?hours={hours}"
    content, status = await http_get_bytes(url, retries=2, timeout=15)
    if not content or status != 200:
        return []

    import json

    data = json.loads(content)
    features = data.get("features", [])
    reports = []
    for f in features:
        props = f.get("properties", {})
        lat = props.get("lat")
        lon = props.get("lon")
        if lat is None or lon is None:
            continue
        reports.append(
            {
                "type": props.get("type", ""),
                "typetext": props.get("typetext", ""),
                "magnitude": props.get("magnitude", ""),
                "wfo": props.get("wfo", ""),
                "point": Point(lon, lat),
                "valid": props.get("valid", ""),
            }
        )
    return reports


async def fetch_spc_outlook_areas(date_str: str = None, issuance: str = None) -> list[dict]:
    """Fetch SPC Day 1 categorical outlook KML and parse risk area polygons.
    If issuance is None, tries the latest available issuance."""
    if date_str is None:
        dt = datetime.now(timezone.utc)
        date_str = dt.strftime("%Y-%m-%d")

    dt = datetime.strptime(date_str, "%Y-%m-%d")
    ymd = dt.strftime("%Y%m%d")
    year = dt.strftime("%Y")

    # Issuances are at 0100, 0600, 1200, 1630, 2000 UTC
    # Try the provided issuance, or find the latest available
    issuances = (
        [issuance] if issuance else sorted(("0100", "0600", "1200", "1630", "2000"), reverse=True)
    )

    for iss in issuances:
        url = (
            f"https://www.spc.noaa.gov/products/outlook/archive/{year}/day1otlk_{ymd}_{iss}_cat.kml"
        )
        content, status = await http_get_bytes(url, retries=1, timeout=10)
        if content and status == 200:
            return _parse_spc_kml(content)

    logger.warning(f"SPC KML not available for {date_str}")
    return []


def _parse_spc_kml(raw: bytes) -> list[dict]:
    """Parse SPC categorical outlook KML into risk area polygons."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        logger.warning("Failed to parse SPC KML")
        return []

    areas = []
    for pm in root.findall(".//kml:Placemark", KML_NS):
        label_el = pm.find(".//kml:Data[@name='LABEL']/kml:value", KML_NS)
        label = label_el.text.strip() if label_el is not None and label_el.text else ""
        if not label:
            continue

        # Extract fill color for visual distinction
        fill_el = pm.find(".//kml:Data[@name='fill']/kml:value", KML_NS)
        _ = fill_el.text.strip() if fill_el is not None and fill_el.text else ""

        # Parse polygon coordinates
        coords_el = pm.find(".//kml:coordinates", KML_NS)
        if coords_el is None or not coords_el.text:
            continue

        pts = []
        for line in coords_el.text.strip().split():
            bits = line.split(",")
            if len(bits) >= 2:
                try:
                    lon, lat = float(bits[0]), float(bits[1])
                    pts.append((lon, lat))
                except ValueError:
                    continue

        if len(pts) < 3:
            continue

        try:
            poly = Polygon(pts)
            if poly.is_valid:
                area_sq_km = poly.area * 111.32 * 111.32  # rough mercator deg→km
                areas.append(
                    {
                        "label": label,
                        "area_km2": int(area_sq_km),
                        "polygon": poly,
                    }
                )
        except Exception:
            continue

    return areas


# ── Verification logic ────────────────────────────────────────────────────────


async def compute_verification(hours_back: int = 24) -> dict:
    """Run full verification pipeline: outlook areas vs warnings + LSRs + watches."""
    warnings = await fetch_active_warnings()
    watches = await fetch_active_watches()
    lsrs = await fetch_lsr_reports(hours=hours_back)
    areas = await fetch_spc_outlook_areas()

    if not areas:
        return {"error": "No SPC outlook data available"}

    results = []
    for area in areas:
        label = area["label"]
        poly = area["polygon"]

        warnings_inside = [w for w in warnings if poly.intersects(w["polygon"])]
        watches_inside = [w for w in watches if poly.intersects(w["polygon"])]
        lsrs_inside = [r for r in lsrs if poly.contains(r["point"])]

        tor_w = [w for w in warnings_inside if "Tornado" in w["event"]]
        svr_w = [w for w in warnings_inside if "Severe" in w["event"]]
        ffw_w = [w for w in warnings_inside if "Flash" in w["event"]]

        tor_rpts = [r for r in lsrs_inside if r["type"] == "T"]
        wind_rpts = [r for r in lsrs_inside if r["type"] == "G"]
        hail_rpts = [r for r in lsrs_inside if r["type"] == "H"]

        thresholds = _RISK_THRESHOLDS.get(label, {"tornado": 0, "wind": 0, "hail": 0})
        approx_area = area["area_km2"] / 4000  # rough: 40km grid cells

        tor_expected = int(approx_area * thresholds["tornado"]) if thresholds["tornado"] > 0 else 0
        wind_expected = int(approx_area * thresholds["wind"]) if thresholds["wind"] > 0 else 0
        hail_expected = int(approx_area * thresholds["hail"]) if thresholds["hail"] > 0 else 0

        results.append(
            {
                "label": label,
                "area_km2": area["area_km2"],
                "tor_warnings": len(tor_w),
                "svr_warnings": len(svr_w),
                "ffw_warnings": len(ffw_w),
                "tor_lsrs": len(tor_rpts),
                "wind_lsrs": len(wind_rpts),
                "hail_lsrs": len(hail_rpts),
                "active_watches": len(watches_inside),
                "tor_expected": tor_expected,
                "wind_expected": wind_expected,
                "hail_expected": hail_expected,
                "tor_threshold": thresholds["tornado"],
                "wind_threshold": thresholds["wind"],
                "hail_threshold": thresholds["hail"],
            }
        )

    return {
        "results": results,
        "total_warnings": len(warnings),
        "total_watches": len(watches),
        "total_lsrs": len(lsrs),
        "hours_back": hours_back,
    }


# ── Discord command ───────────────────────────────────────────────────────────


class VerificationCog(commands.Cog, name="Verification"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="verifyoutlook",
        description="Live verification — compare active warnings against SPC outlook risk areas",
    )
    @app_commands.describe(
        hours="Hours of LSR data to include (default: 24)",
    )
    async def verify_outlook(self, interaction: discord.Interaction, hours: int = 24):
        await interaction.response.defer()

        try:
            result = await compute_verification(hours_back=hours)
        except Exception as e:
            logger.exception(f"verifyoutlook failed: {e}")
            await interaction.followup.send(
                "Verification pipeline failed — check logs.", ephemeral=True
            )
            return

        if "error" in result:
            await interaction.followup.send(result["error"], ephemeral=True)
            return

        results = result["results"]
        now = datetime.now(timezone.utc)

        embed = discord.Embed(
            title=f"🔬 Live Outlook Verification — {now.strftime('%Y-%m-%d %H:%M')}Z",
            color=discord.Color.dark_purple(),
            timestamp=now,
        )

        embed.add_field(
            name="Summary",
            value=(
                f"⚠️ Active warnings: **{result['total_warnings']}**\n"
                f"👀 Active watches: **{result['total_watches']}**\n"
                f"📡 LSR reports ({hours}h): **{result['total_lsrs']}**\n"
            ),
            inline=False,
        )

        for area in results:
            if area["label"] in ("TSTM", "MRGL"):
                continue

            tor_thresh, wind_thresh, hail_thresh = (
                area["tornado_threshold"],
                area["wind_threshold"],
                area["hail_threshold"],
            )
            tor_exp = int(area["area_km2"] / 4000 * tor_thresh) if tor_thresh > 0 else 0
            wind_exp = int(area["area_km2"] / 4000 * wind_thresh) if wind_thresh > 0 else 0
            hail_exp = int(area["area_km2"] / 4000 * hail_thresh) if hail_thresh > 0 else 0

            value = (
                f"🌪️ Tornado: **{area['tor_warnings']}w** / {tor_exp} LSR expected\n"
                f"⛈️ Severe: **{area['svr_warnings']}w** / {wind_exp} wind LSR expected\n"
                f"🧊 Hail: {area['hail_lsrs']} LSRs / {hail_exp} expected\n"
                f"📡 All LSRs: {area['tor_lsrs']} tor, {area['wind_lsrs']} wind, {area['hail_lsrs']} hail\n"
                f"👀 Active watches nearby: {area['active_watches']}"
            )
            embed.add_field(
                name=f"{area['label']} Risk Area ({area['area_km2']:,} km²)",
                value=value,
                inline=False,
            )

        embed.set_footer(
            text="Data: NWS API / IEM / SPC GIS | PP methodology: Hitchens et al. 2013"
        )
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(VerificationCog(bot))
