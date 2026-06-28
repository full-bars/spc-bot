"""VTEC (Valid Time Extent Codegroup) parsing utilities.

Parses NWS VTEC strings (NWS Directive 10-1703) which encode warning/watch
event metadata in a compact format. Also includes polygon parsing for
geographic warning regions.

These utilities have zero Discord dependencies and can be reused in CLI tools,
batch processors, and other contexts.
"""
import logging
import re
from typing import List, Optional, Tuple

logger = logging.getLogger("spc_bot.vtec_parser")

# Rust core — optional (Python fallbacks exist)
try:
    import spc_rust_core
    _RUST_AVAILABLE = True
    _parse_vtec_rust = spc_rust_core.parse_vtec
    _parse_warning_polygon_rust = spc_rust_core.parse_warning_polygon
    logger.info("Spatial Engine initialized: using Rust core (parse_vtec, parse_warning_polygon)")
except (ImportError, AttributeError):
    _RUST_AVAILABLE = False
    _parse_vtec_rust = None
    _parse_warning_polygon_rust = None
    logger.warning("Rust core not available — using Python fallbacks for VTEC parsing")

# VTEC pattern:   /O.{action}.{office}.{phenom}.{sig}.{etn}.{start}-{end}/
#                  /O.NEW.KOUN.TO.W.0042.260427T2018Z-260427T2100Z/
_VTEC_RE = re.compile(
    r"/O\.(?P<action>NEW|CON|EXP|CAN|UPG|EXA|EXT|ROU)"
    r"\.(?P<office>[A-Z0-9]{3,4})"
    r"\.(?P<phenom>[A-Z]{2})"
    r"\.(?P<sig>[A-Z])"
    r"\.(?P<etn>\d{4})"
    r"\.(?P<start>\d{6}T\d{4}Z)"
    r"-(?P<end>\d{6}T\d{4}Z)/"
)


def _parse_vtec_py(text: str) -> Optional[dict]:
    """Python fallback for parse_vtec — regex-based, mirrors Rust nom parser."""
    low = text
    pos = 0
    while True:
        idx = low.find("/O.", pos)
        if idx < 0:
            return None
        m = _VTEC_RE.search(low, idx)
        if m:
            office = m.group("office")
            if len(office) == 3 and office[0].isascii() and office[0].isupper():
                office = f"K{office}"
            elif office.startswith(" "):
                office = office.strip()
            phenom = m.group("phenom")
            sig = m.group("sig")
            etn = m.group("etn")
            vtec_id = f"{office}.{phenom}.{sig}.{etn}"
            return {
                "action": m.group("action"),
                "office": office,
                "phenom": phenom,
                "sig": sig,
                "etn": etn,
                "start": m.group("start"),
                "end": m.group("end"),
                "vtec_id": vtec_id,
            }
        pos = idx + 3


def _parse_warning_polygon_py(text: str) -> Optional[List[Tuple[float, float]]]:
    """Python fallback for parse_warning_polygon — mirrors Rust parser."""
    idx = text.lower().find("lat...lon")
    if idx < 0:
        return None
    cursor = text[idx + 9:]

    raw_ints: list[int] = []
    while cursor:
        cursor = cursor.lstrip(" \t\r\n")
        if not cursor:
            break
        ch = cursor[0]
        if ch.isascii() and (ch.isupper() or ch == "$" or ch == "\0"):
            break
        m = re.match(r"\d+", cursor)
        if not m:
            break
        raw_ints.append(int(m.group()))
        cursor = cursor[m.end():]

    coords: list[tuple[float, float]] = []
    for i in range(0, len(raw_ints) - 1, 2):
        lat = raw_ints[i] / 100.0
        lon = -(raw_ints[i + 1] / 100.0)
        if 15.0 <= lat <= 75.0 and -170.0 <= lon <= -60.0:
            coords.append((lat, lon))
    return coords or None


def parse_vtec(text: str) -> Optional[dict]:
    """Parse VTEC string using Rust engine (falls back to Python)."""
    if _parse_vtec_rust:
        try:
            return _parse_vtec_rust(text)
        except Exception as e:
            logger.warning(f"Rust parse_vtec failed, falling back to Python: {e}")
    return _parse_vtec_py(text)


def parse_warning_polygon(
    text: str,
) -> Optional[List[Tuple[float, float]]]:
    """Parse the ``LAT...LON`` polygon block using Rust engine (falls back to Python)."""
    if not text:
        return None
    if _parse_warning_polygon_rust:
        try:
            result = _parse_warning_polygon_rust(text)
            return result or None
        except Exception as e:
            logger.warning(f"Rust parse_warning_polygon failed, falling back to Python: {e}")
    return _parse_warning_polygon_py(text)


def get_polygon_centroid(
    coords: List[Tuple[float, float]]
) -> Optional[Tuple[float, float]]:
    """Calculate the simple arithmetic centroid of a list of (lat, lon) pairs."""
    if not coords:
        return None
    n = len(coords)
    if n == 0:
        return None
    sum_lat = sum(p[0] for p in coords)
    sum_lon = sum(p[1] for p in coords)
    return (sum_lat / n, sum_lon / n)
