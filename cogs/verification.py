# cogs/verification.py
"""Live SPC outlook verification using warning polygons, LSR reports, and SPC GIS data.

Verification math (Hitchens et al. 2013 / SPC):
- SPC probability = % chance of event within 25 mi of a point
- Each LSR "covers" a 25-mi-radius circle: π × (40.23 km)² ≈ 5,077 km²
- Expected LSRs = (risk_area_km² × probability) / 5,077
- If actual ≥ expected: the risk area is verifying
"""

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from pyproj import Transformer
from shapely.geometry import Point, Polygon, shape

from utils.http import http_get_bytes

logger = logging.getLogger("spc_bot.verification")

# 25-mile radius circle area in km² (SPC's "within 25 miles of a point")
_COVERAGE_KM2 = 5077  # π × (25 × 1.60934)²

# SPC probability thresholds per risk category
# Tornado: 2% for SLGT, 10% for ENH, 15% for MDT, 30% for HIGH
# Wind/Hail: 5% for SLGT, 15% for ENH, 25% for MDT, 45% for HIGH
_RISK_THRESHOLDS = {
    "TSTM": {"tornado": 0, "wind": 0, "hail": 0},
    "MRGL": {"tornado": 0.02, "wind": 0.05, "hail": 0.05},
    "SLGT": {"tornado": 0.02, "wind": 0.05, "hail": 0.05},
    "ENH": {"tornado": 0.10, "wind": 0.15, "hail": 0.15},
    "MDT": {"tornado": 0.15, "wind": 0.25, "hail": 0.25},
    "HIGH": {"tornado": 0.30, "wind": 0.45, "hail": 0.45},
}

# Albers Equal Area projection for CONUS — accurate area in m²
_ALBERS = "ESRI:102003"  # USA_Contiguous_Albers_Equal_Area_Conic
_transformer = Transformer.from_crs("EPSG:4326", _ALBERS, always_xy=True)


def _geodesic_area_sq_km(polygon: Polygon) -> float:
    """Compute area in km² using Albers Equal Area projection."""
    projected = shape(
        {"type": "Polygon", "coordinates": [_transform_ring(polygon.exterior.coords)]}
    )
    return projected.area / 1_000_000  # m² → km²


def _transform_ring(coords):
    return [_transformer.transform(x, y) for x, y in coords]


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
    """Get active tornado/severe watches from NWS API with polygon geometry."""
    url = (
        "https://api.weather.gov/alerts/active"
        "?event=Severe%20Thunderstorm%20Watch,Tornado%20Watch"
        "&status=actual"
    )
    content, status = await http_get_bytes(url, retries=2, timeout=15)
    if not content or status != 200:
        return []

    import json

    data = json.loads(content)
    features = data.get("features", [])
    watches = []
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
            {"event": props.get("event", ""), "area": props.get("areaDesc", ""), "polygon": poly}
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
                area_sq_km = int(_geodesic_area_sq_km(poly))
                areas.append(
                    {
                        "label": label,
                        "area_km2": area_sq_km,
                        "polygon": poly,
                    }
                )
        except Exception:
            continue

    return areas


# ── Verification logic ────────────────────────────────────────────────────────


async def compute_verification(
    hours_back: int = 24,
    date_str: str = None,
) -> dict:
    """Run full verification pipeline: outlook areas vs warnings + LSRs + watches.

    If date_str is set (YYYY-MM-DD), fetches SPC outlook for that date.
    hours_back controls the LSR window."""
    from utils.geo import points_in_polygon_lookup

    warnings = await fetch_active_warnings()
    watches = await fetch_active_watches()
    lsrs = await fetch_lsr_reports(hours=hours_back)
    areas = await fetch_spc_outlook_areas(date_str=date_str)

    if not areas:
        return {"error": "No SPC outlook data available"}

    # Convert risk area polygons to Rust format: list of (lat, lon) rings.
    # Reverse so smallest/nested areas (ENH) are checked first.
    rust_polys = [[(y, x) for x, y in a["polygon"].exterior.coords] for a in reversed(areas)]

    # ── LSR point-in-polygon via Rust (fast) ──
    lsr_points = [(r["point"].y, r["point"].x) for r in lsrs]  # (lat, lon)
    rust_assign = points_in_polygon_lookup(lsr_points, rust_polys) if lsr_points else []

    # Map Rust-assigned indices back to original area order
    reversal_map = {i: len(areas) - 1 - i for i in range(len(areas))}
    lsr_per_area = [[] for _ in areas]
    for pt_idx, poly_idx in enumerate(rust_assign):
        if poly_idx is not None:
            area_idx = reversal_map[poly_idx]
            lsr_per_area[area_idx].append(lsrs[pt_idx])

    # ── Warning polygon intersection via Shapely ──
    results = []
    for idx, area in enumerate(areas):
        label = area["label"]
        poly = area["polygon"]

        # Count warnings intersecting this risk polygon
        warnings_inside = [w for w in warnings if poly.intersects(w["polygon"])]
        tor_w = [w for w in warnings_inside if "Tornado" in w["event"]]
        svr_w = [w for w in warnings_inside if "Severe" in w["event"]]
        ffw_w = [w for w in warnings_inside if "Flash" in w["event"]]

        # Count watches intersecting
        watches_inside = [w for w in watches if poly.intersects(w["polygon"])]

        # Count LSRs assigned to this area by Rust
        lsrs_in = lsr_per_area[idx]
        tor_rpts = [r for r in lsrs_in if r["type"] == "T"]
        wind_rpts = [r for r in lsrs_in if r["type"] == "G"]
        hail_rpts = [r for r in lsrs_in if r["type"] == "H"]

        thresholds = _RISK_THRESHOLDS.get(label, {"tornado": 0, "wind": 0, "hail": 0})
        area_km2 = area["area_km2"]

        # Expected LSRs per hazard to "verify" the SPC probability threshold.
        # Each report covers a 25-mile circle (5,077 km²), so:
        # expected = (area × probability) / coverage_per_report
        def _expected(prob: float) -> int:
            return max(1, int(area_km2 * prob / _COVERAGE_KM2)) if prob > 0 else 0

        tor_exp = _expected(thresholds["tornado"])
        wind_exp = _expected(thresholds["wind"])
        hail_exp = _expected(thresholds["hail"])

        tor_actual = len(tor_rpts)
        wind_actual = len(wind_rpts)
        hail_actual = len(hail_rpts)

        def _verdict(actual: int, expected: int) -> str:
            if expected == 0:
                return ""
            if actual >= expected:
                pct = min(999, int(actual / expected * 100))
                return f"✅ ({pct}%)"
            pct = int(actual / expected * 100) if actual > 0 else 0
            return f"⚠️ ({pct}%)"

        results.append(
            {
                "label": label,
                "area_km2": area_km2,
                "tor_warnings": len(tor_w),
                "svr_warnings": len(svr_w),
                "ffw_warnings": len(ffw_w),
                "tor_lsrs": tor_actual,
                "wind_lsrs": wind_actual,
                "hail_lsrs": hail_actual,
                "active_watches": len(watches_inside),
                "tor_expected": tor_exp,
                "wind_expected": wind_exp,
                "hail_expected": hail_exp,
                "tor_verdict": _verdict(tor_actual, tor_exp),
                "wind_verdict": _verdict(wind_actual, wind_exp),
                "hail_verdict": _verdict(hail_actual, hail_exp),
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
        "label": f"last {hours_back}h" if not date_str else date_str,
    }


# ── Discord command ───────────────────────────────────────────────────────────


class VerificationCog(commands.Cog, name="Verification"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="verifyoutlook",
        description="Compare active warnings against SPC outlook risk areas",
    )
    @app_commands.describe(
        date="Date in YYYY-MM-DD (UTC). Default: today.",
        hours="Hours of LSR data (default: 6, ignored if date set)",
    )
    async def verify_outlook(
        self, interaction: discord.Interaction, date: str = None, hours: int = 6
    ):
        await interaction.response.defer()

        try:
            if date:
                result = await compute_verification(date_str=date, hours_back=hours)
                label = f"for {date} (LSRs: {hours}h)"
            else:
                result = await compute_verification(hours_back=hours)
                label = f"(LSRs: {hours}h)"
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
            title=f"🔬 Live Outlook Verification {label}",
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

            tor_v = area.get("tor_verdict", "")
            wind_v = area.get("wind_verdict", "")
            hail_v = area.get("hail_verdict", "")

            value = (
                f"⚠️ **{area['tor_warnings']}** tornado / **{area['svr_warnings']}** severe / **{area['ffw_warnings']}** FFW\n"
                f"🌪️ Tornado: {area['tor_lsrs']} / {area['tor_expected']} report(s) needed {tor_v}\n"
                f"💨 Wind: {area['wind_lsrs']} / {area['wind_expected']} report(s) needed {wind_v}\n"
                f"🧊 Hail: {area['hail_lsrs']} / {area['hail_expected']} report(s) needed {hail_v}\n"
                f"👀 Watches active: {area['active_watches']}"
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
