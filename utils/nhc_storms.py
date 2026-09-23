"""NHC storm list fetching and advisory text parsing.

Shared utilities for tropical cyclone products — used by both the
auto-posting tropical cog and the tropical tracker.
"""

import logging
import re
import time

import aiohttp

from config import IEM_NWSTEXT_URL
from utils.http import http_get_bytes

logger = logging.getLogger("spc_bot")

# ── Storm classification constants ────────────────────────────────────────────

STORM_TYPE_ORDER = [
    "REMNANTS",
    "POST-TROPICAL CYCLONE",
    "TROPICAL DEPRESSION",
    "SUBTROPICAL DEPRESSION",
    "SUBTROPICAL STORM",
    "TROPICAL STORM",
    "HURRICANE",
    "MAJOR HURRICANE",
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
    lines = text.splitlines()
    for _i, line in enumerate(lines):
        upper = line.upper()
        for t in STORM_TYPE_ORDER:
            if t in upper:
                parts = upper.split(t, 1)
                if len(parts) > 1:
                    name = parts[1].strip().strip(".").strip()
                    return name.title()
    return None


def classify_product(product_id: str) -> str | None:
    pid = product_id.upper()
    for pil, name in NHC_PRODUCT_NAMES.items():
        if pil in pid:
            return name
    return None


# ── Active storm list scraping ────────────────────────────────────────────────


def _extract_storms_from_html(html: str) -> dict[str, dict]:
    """Parse storm IDs and names from the NHC cyclones page HTML."""
    import re as _re

    storms: dict[str, dict] = {}

    # Extract storm IDs from star.nesdis.noaa.gov floater links
    for m in _re.finditer(r"stormid=([A-Z]{2}\d{2}\d{4})", html):
        storm_id = m.group(1)
        if storm_id not in storms:
            storms[storm_id] = {"storm_id": storm_id, "name": None, "type": None}

    # Try to extract storm names from surrounding text — pattern is
    # "...HURRICANE POLO..." or "...TROPICAL STORM FELICIA..."
    # We look for lines that contain both a storm type and a name.
    name_pattern = _re.compile(
        r"(HURRICANE|TROPICAL STORM|TROPICAL DEPRESSION)\s+([A-Z][A-Z\s]+?)(?:\s*\(|$|\n)",
        _re.IGNORECASE,
    )
    for m in name_pattern.finditer(html):
        stype = m.group(1).strip().title()
        sname = m.group(2).strip().title()
        # Match to the closest storm ID by finding the last stormid= before this match
        pos = m.start()
        best_id = None
        for sid_m in _re.finditer(r"stormid=([A-Z]{2}\d{2}\d{4})", html):
            if sid_m.start() <= pos:
                best_id = sid_m.group(1)
            else:
                break
        if best_id and best_id in storms:
            storms[best_id]["name"] = sname
            storms[best_id]["type"] = stype

    return storms


async def get_active_storms() -> dict[str, dict]:
    """Fetch the list of active tropical cyclones from NHC.

    Returns a dict keyed by storm ID (e.g., ``AL062026``) with values:
    ``{"storm_id": str, "name": str|None, "type": str|None}``
    """
    global _active_storms_cache, _active_storms_fetched_at

    now = time.monotonic()
    if _active_storms_cache and (now - _active_storms_fetched_at) < _ACTIVE_STORMS_TTL:
        return _active_storms_cache

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                _ACTIVE_CYCLONES_URL,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"NHC cyclones page returned {resp.status}")
                    return _active_storms_cache
                html = await resp.text()
    except Exception as e:
        logger.warning(f"Failed to fetch NHC cyclones page: {e}")
        return _active_storms_cache

    storms = _extract_storms_from_html(html)
    if storms:
        _active_storms_cache = storms
        _active_storms_fetched_at = now
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


def build_advisory_etn(product_id: str) -> str:
    """Extract an advisory tracking number from a product ID for deduplication.

    Falls back to the full product ID if no ETN pattern is found.
    """
    m = re.search(r"(\d{4,})", product_id)
    return m.group(1) if m else product_id
