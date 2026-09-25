"""NHC storm list fetching and advisory text parsing.

Shared utilities for tropical cyclone products — used by both the
auto-posting tropical cog and the tropical tracker.
"""

import logging
import re
import time

from config import IEM_NWSTEXT_URL
from utils.http import http_get_bytes

logger = logging.getLogger("spc_bot")

# ── Storm classification constants ────────────────────────────────────────────

# Order matters: more specific types must come before their substring matches
# (e.g. "SUBTROPICAL DEPRESSION" before "TROPICAL DEPRESSION", "MAJOR
# HURRICANE" before "HURRICANE") so classify/extract return the correct one.
STORM_TYPE_ORDER = [
    "REMNANTS",
    "POST-TROPICAL CYCLONE",
    "SUBTROPICAL DEPRESSION",
    "SUBTROPICAL STORM",
    "TROPICAL DEPRESSION",
    "TROPICAL STORM",
    "MAJOR HURRICANE",
    "HURRICANE",
]

NHC_PRODUCT_NAMES = {
    "TCP": "ADVISORY",
    "TCD": "DISCUSSION",
    "TWD": "TROPICAL WEATHER DISCUSSION",
    "TWO": "TROPICAL WEATHER OUTLOOK",
    "TCU": "UPDATE",
    "TCE": "POSITION ESTIMATE",
    "TCV": "WATCH/WARNING SUMMARY",
}

SAFFIR_SIMPSON_COLORS = {
    "TD": 0x5DBAFF,
    "TS": 0x00FBF4,
    "CAT1": 0xFFFFCD,
    "CAT2": 0xFEE775,
    "CAT3": 0xFFC140,
    "CAT4": 0xFF8F21,
    "CAT5": 0xFF6060,
}

SAFFIR_EMOJI = {
    "TD": "☁️",
    "TS": "🌧️",
    "CAT1": "🌀",
    "CAT2": "🌀",
    "CAT3": "⚠️🌀⚠️",
    "CAT4": "⚠️🌀⚠️",
    "CAT5": "⚠️🌀⚠️",
}

# ── Active storm list cache ───────────────────────────────────────────────────

_active_storms_cache: dict[str, dict] = {}
_active_storms_fetched_at: float = 0.0
# None = never fetched, True = last fetch authoritative, False = last fetch failed
_last_fetch_ok: bool | None = None
_ACTIVE_STORMS_TTL = 300  # 5 minutes

_ACTIVE_CYCLONES_URL = "https://www.nhc.noaa.gov/cyclones"


def winds_to_category(wind_mph: float) -> str:
    if wind_mph < 39:
        return "TD"
    if wind_mph < 74:
        return "TS"
    if wind_mph < 96:
        return "CAT1"
    if wind_mph < 111:
        return "CAT2"
    if wind_mph < 130:
        return "CAT3"
    if wind_mph < 157:
        return "CAT4"
    return "CAT5"


def category_label(cat: str) -> str:
    labels = {
        "TD": "Tropical Depression",
        "TS": "Tropical Storm",
        "CAT1": "Hurricane (Cat 1)",
        "CAT2": "Hurricane (Cat 2)",
        "CAT3": "Hurricane (Cat 3)",
        "CAT4": "Hurricane (Cat 4)",
        "CAT5": "Hurricane (Cat 5)",
    }
    return labels.get(cat, cat)


def parse_max_wind(text: str) -> float | None:
    """Extract maximum sustained wind speed in MPH from advisory text."""
    m = re.search(r"MAXIMUM\s+SUSTAINED\s+WINDS[\.\s:]+?(\d+)\s*MPH", text, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


def clean_dots(s: str) -> str:
    return re.sub(r"\.{2,}", " ", s).strip()


def parse_location(text: str) -> str | None:
    m = re.search(r"LOCATION[\.\s:]+?([\d.]+[NS])\s+([\d.]+[EW])", text)
    if m:
        return clean_dots(f"{m.group(1)} {m.group(2)}")
    return None


def parse_location_desc(text: str) -> str | None:
    m = re.search(r"ABOUT\s+(.+?)(?:\n|$)", text)
    if m:
        return clean_dots(m.group(1))
    return None


def parse_pressure(text: str) -> int | None:
    m = re.search(r"MINIMUM\s+CENTRAL\s+PRESSURE[\.\s:]+?(\d+)\s*MB", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def parse_movement(text: str) -> str | None:
    m = re.search(r"PRESENT\s+MOVEMENT[\.\s:]+?(.+?\d+)\s*MPH", text, re.IGNORECASE)
    if m:
        return clean_dots(m.group(1))
    return None


def classify_storm_type(text: str) -> str | None:
    upper = text.upper()
    for t in STORM_TYPE_ORDER:
        if t in upper:
            return t
    if "TROPICAL CYCLONE" in upper:
        return "TROPICAL CYCLONE"
    return None


def extract_storm_name(text: str) -> str | None:
    """Extract the storm name from a product header line.

    Strips trailing product suffixes ("ADVISORY NUMBER 14", "DISCUSSION",
    "UPDATE") without truncating multi-word names, and handles headers such
    as "REMNANTS OF FAY" and "POTENTIAL TROPICAL CYCLONE NINE".
    """
    for line in text.splitlines():
        upper = line.upper()
        for t in STORM_TYPE_ORDER + ["POTENTIAL TROPICAL CYCLONE"]:
            if t in upper:
                parts = upper.split(t, 1)
                if len(parts) > 1:
                    remainder = parts[1].strip().strip(".").strip()
                    name = re.split(r"\s+(?:ADVISORY|DISCUSSION|UPDATE)\b", remainder, maxsplit=1)[
                        0
                    ]
                    name = re.sub(r"^OF\s+", "", name).strip()
                    return name.title() or None
    return None


def classify_product(product_id: str) -> str | None:
    pid = product_id.upper()
    for pil, name in NHC_PRODUCT_NAMES.items():
        if pil in pid:
            return name
    return None


def split_type_name(full_name: str) -> tuple[str, str | None]:
    """Split a full storm label like "Hurricane Polo" into (type, name).

    Labels from the NHC cyclones page are all-caps; results are title-cased
    for display (e.g. "HURRICANE POLO" → ("Hurricane", "Polo")).
    """
    upper = full_name.upper()
    for t in STORM_TYPE_ORDER + ["POTENTIAL TROPICAL CYCLONE"]:
        idx = upper.find(t)
        if idx >= 0:
            stype = full_name[idx : idx + len(t)].title()
            name = full_name[idx + len(t) :].strip().strip(".").strip().title() or None
            return stype, name
    return full_name.title(), None


# ── Active storm list scraping ────────────────────────────────────────────────

_STORM_BLOCK_RE = re.compile(
    r'<g\s+class="[^"]*storm-system[^"]*"\s+'
    r'data-name="([^"]+)"\s+'
    r'data-risk="([^"]*)"\s+'
    r'data-prob="([^"]*)"\s+'
    r'data-details="((?:(?!<g\b|</g>).)*?)"\s+'
    r'data-action="([^"]+)"',
    re.S,
)

_STORM_IDENT_RE = re.compile(
    r"<!--storm serial number:\s*([A-Z]{2}\d{2})-->\s*"
    r"<!--storm identification:\s*([A-Z]{2}\d{2}\d{4})\s+([^<]+)-->"
)

_STORM_FLOATER_RE = re.compile(
    r'href="(https://www\.star\.nesdis\.noaa\.gov/goes/floater\.php\?stormid=([A-Z]{2}\d{2}\d{4}))"'
)


def _parse_advisory_details(details: str) -> dict:
    """Parse the data-details payload (advisory number, winds, pressure...)."""
    out: dict = {}
    m = re.search(r"Advisory\s*#?\s*([0-9]+[A-Z]?)", details, re.IGNORECASE)
    if m:
        out["advisory"] = m.group(1)
    m = re.search(r"Maximum\s+Sustained\s+Winds:.*?(\d+)\s*mph", details, re.IGNORECASE)
    if m:
        out["winds_mph"] = float(m.group(1))
    m = re.search(r"Minimum\s+Central\s+Pressure:.*?(\d+)\s*mb", details, re.IGNORECASE)
    if m:
        out["pressure"] = int(m.group(1))
    m = re.search(r"Located\s+at:\s*([0-9.]+[NS]\s+[0-9.]+[EW])", details, re.IGNORECASE)
    if m:
        out["position"] = m.group(1)
    m = re.search(r"Movement:\s*(.+?)(?:<br>|$)", details, re.IGNORECASE)
    if m:
        out["movement"] = m.group(1).strip()
    m = re.search(r"As\s+of\s+([^<]+?)\s*\(", details, re.IGNORECASE)
    if m:
        out["issuance"] = m.group(1).strip()
    return out


def _signed_coords(position: str) -> tuple[float, float] | None:
    """Parse an NHC position like ``22.9N 128.1W`` into signed (lat, lon)."""
    m = re.fullmatch(
        r"\s*([0-9]+(?:\.[0-9]+)?)\s*([NSns])\s+([0-9]+(?:\.[0-9]+)?)\s*([EWew])\s*",
        position,
    )
    if not m:
        return None
    lat = float(m.group(1)) * (-1 if m.group(2).upper() == "S" else 1)
    lon = float(m.group(3)) * (-1 if m.group(4).upper() == "W" else 1)
    return lat, lon


def zoom_earth_url(position: str) -> str | None:
    """Build a zoom.earth satellite-view URL from an NHC position string.

    Returns e.g. ``https://zoom.earth/maps/satellite/#view=22.9,-128.1,6z``
    (6z regional zoom, matching NHC's storm-scale framing), or None if the
    position doesn't parse.
    """
    coords = _signed_coords(position)
    if not coords:
        return None
    lat, lon = coords
    return f"https://zoom.earth/maps/satellite/#view={lat:g},{lon:g},6z"


def zoom_earth_gusts_url(position: str) -> str | None:
    """Build a zoom.earth GFS wind-gusts URL from an NHC position string.

    Returns e.g. ``https://zoom.earth/maps/wind-gusts/#view=22.9,-128.1,6z/model=gfs``,
    or None if the position doesn't parse.
    """
    coords = _signed_coords(position)
    if not coords:
        return None
    lat, lon = coords
    return f"https://zoom.earth/maps/wind-gusts/#view={lat:g},{lon:g},6z/model=gfs"


def zoom_earth_pressure_url(position: str) -> str | None:
    """Build a zoom.earth GFS pressure URL from an NHC position string.

    Returns e.g. ``https://zoom.earth/maps/pressure/#view=22.9,-128.1,6z/model=gfs``,
    or None if the position doesn't parse.
    """
    coords = _signed_coords(position)
    if not coords:
        return None
    lat, lon = coords
    return f"https://zoom.earth/maps/pressure/#view={lat:g},{lon:g},6z/model=gfs"


def _extract_storms_from_html(html: str) -> dict[str, dict]:
    """Parse active storms from the NHC cyclones page.

    Combines the SVG storm-system blocks (full name, risk, advisory details,
    graphics page URL) with the table-row comments (storm serial number and
    storm identification, e.g. ``EP17`` / ``EP172026 Hurricane Polo``) keyed
    by full name. Disturbances (data-risk != "storm") are excluded.
    """
    ident_by_name: dict[str, tuple[str, str]] = {}
    for m in _STORM_IDENT_RE.finditer(html):
        serial, storm_id, full_name = m.group(1), m.group(2), m.group(3).strip()
        ident_by_name[full_name.lower()] = (storm_id, serial)

    floater_by_id = {storm_id: url for url, storm_id in _STORM_FLOATER_RE.findall(html)}

    storms: dict[str, dict] = {}
    for m in _STORM_BLOCK_RE.finditer(html):
        full_name, risk, _prob, details, action = m.groups()
        if risk != "storm":
            continue
        key = full_name.strip().lower()
        ident = ident_by_name.get(key)
        if not ident:
            logger.debug(f"NHC cyclones page: no storm ID found for {full_name!r}")
            continue
        storm_id, serial = ident

        stype, name = split_type_name(full_name.strip())
        graphics_match = re.search(r"graphics_([a-z]+\d+)\+", action)
        graphics_url = (
            f"https://www.nhc.noaa.gov/graphics_{graphics_match.group(1)}.shtml?cone"
            if graphics_match
            else None
        )

        info: dict = {
            "storm_id": storm_id,
            "name": name,
            "type": stype,
            "full_name": full_name.strip(),
            "serial": serial,
            "graphics_url": graphics_url,
            "satellite_url": floater_by_id.get(storm_id),
        }
        info.update(_parse_advisory_details(details))
        storms[storm_id] = info

    return storms


def active_storms_authoritative() -> bool:
    """True when the active-storm cache reflects a successful NHC fetch.

    Callers must check this before acting on an *absent* storm (e.g. removing
    a subscription) so a failed fetch with an empty cache doesn't wipe state.
    """
    return _last_fetch_ok is True


async def get_active_storms() -> dict[str, dict]:
    """Fetch the list of active tropical cyclones from NHC.

    Returns a dict keyed by storm ID (e.g. ``EP172026``) with values
    including ``name``, ``type``, ``serial``, ``graphics_url`` and advisory
    details (``advisory``, ``winds_mph``, ``pressure``, ``position``,
    ``movement``, ``issuance``).

    The result is cached for 5 minutes. An explicitly parsed empty result is
    cached as authoritative (storms all dissipated); a failed fetch retains
    the previous cache so callers can distinguish via
    :func:`active_storms_authoritative`.
    """
    global _active_storms_cache, _active_storms_fetched_at, _last_fetch_ok

    now = time.monotonic()
    if _active_storms_fetched_at and (now - _active_storms_fetched_at) < _ACTIVE_STORMS_TTL:
        return _active_storms_cache

    content, status = await http_get_bytes(_ACTIVE_CYCLONES_URL, retries=2, timeout=15)
    if not content or status != 200:
        _last_fetch_ok = False
        logger.warning(f"NHC cyclones page returned status {status}")
        return _active_storms_cache

    storms = _extract_storms_from_html(content.decode("utf-8", errors="ignore"))
    _active_storms_cache = storms
    _active_storms_fetched_at = now
    _last_fetch_ok = True
    logger.debug(f"Updated active storm list: {len(storms)} storm(s)")
    return _active_storms_cache


async def fetch_nhc_product(product_id: str) -> dict | None:
    """Fetch and parse an NHC product from the IEM archive."""
    url = IEM_NWSTEXT_URL.format(product_id=product_id)
    content, status = await http_get_bytes(url, retries=2, timeout=10)
    if not content or status != 200:
        return None

    text = content.decode("utf-8", errors="ignore")
    if "not found" in text.lower() and len(text) < 100:
        return None

    lines = text.splitlines()
    header_text = []
    body_start = 0
    for i, line in enumerate(lines):
        header_text.append(line)
        if line.strip().startswith("ATTENTION") or line.strip().startswith("000"):
            body_start = i
            break

    body_lines = lines[body_start:]
    summary_lines = []
    for idx, line in enumerate(body_lines):
        upper = line.strip().upper()
        if "SUMMARY OF" in upper or "SUMMARY INFORMATION" in upper:
            end = len(body_lines)
            for j in range(idx + 2, len(body_lines) - 1):
                underline = body_lines[j + 1].strip()
                if underline and set(underline) == {"-"} and len(underline) > 3:
                    end = j
                    break
            summary_lines = body_lines[idx:end]
            break

    summary = "\n".join(summary_lines).strip() if summary_lines else None

    storm_type = classify_storm_type(text)
    storm_name = extract_storm_name(text)

    return {
        "raw_text": text,
        "summary": summary,
        "storm_type": storm_type,
        "storm_name": storm_name,
    }
