"""VTEC (Valid Time Extent Codegroup) parsing utilities.

Parses NWS VTEC strings (NWS Directive 10-1703) which encode warning/watch
event metadata in a compact format. Also includes polygon parsing for
geographic warning regions.

These utilities have zero Discord dependencies and can be reused in CLI tools,
batch processors, and other contexts.
"""
import re
import logging
from typing import List, Optional, Tuple

# VTEC string format (NWS Directive 10-1703):
#
#   /O.NEW.KOUN.TO.W.0042.260427T2018Z-260427T2100Z/
#    │ │   │    │  │ │    │
#    │ │   │    │  │ │    issuance / expiration timestamps
#    │ │   │    │  │ event tracking number (ETN, 4-digit, stable across the
#    │ │   │    │  │ warning's full lifecycle — our dedup key)
#    │ │   │    │  significance (W=warning, A=watch, Y=advisory, S=statement)
#    │ │   │    phenomenon (TO=tornado, SV=svr tstm, FF=flash flood, etc.)
#    │ │   issuing office (4-letter ICAO, e.g. KOUN = Norman)
#    │ action (NEW, CON, EXP, CAN, UPG, EXA, EXT)
#    fixed: O = operational
_VTEC_RE = re.compile(
    r"/O\.(NEW|CON|EXP|CAN|UPG|EXA|EXT|ROU)\."  # action
    r"([A-Z]{4})\."                              # office
    r"([A-Z]{2})\."                              # phenomenon
    r"([A-Z])\."                                 # significance
    r"(\d{4})\."                                 # ETN
    r"(\d{6}T\d{4}Z)-(\d{6}T\d{4}Z)/"            # start/end
)


def parse_vtec_py(text: str) -> Optional[dict]:
    """Parse the first VTEC string in ``text`` and return its components.

    Returns a dict with ``action``, ``office``, ``phenom``, ``sig``,
    ``etn``, plus the dedup key ``vtec_id`` (``OFFICE.PH.S.ETN``).
    Returns ``None`` if no VTEC is present.
    """
    if not text:
        return None
    m = _VTEC_RE.search(text)
    if not m:
        return None
    action, office, phenom, sig, etn, start, end = m.groups()

    # Normalize office to 4-letter ICAO (common for US WFOs in VTEC)
    if len(office) == 3 and office[0] in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        office = "K" + office

    return {
        "action": action,
        "office": office,
        "phenom": phenom,
        "sig": sig,
        "etn": etn,
        "start": start,
        "end": end,
        "vtec_id": f"{office}.{phenom}.{sig}.{etn}",
    }


def parse_vtec(text: str) -> Optional[dict]:
    """Parse VTEC string; try Rust first, fall back to Python."""
    if _parse_vtec_rust:
        try:
            return _parse_vtec_rust(text)
        except Exception:
            pass
    return parse_vtec_py(text)


# ── Polygon parsing (LAT...LON block) ────────────────────────────────────────

_LATLON_RE = re.compile(
    r"LAT\.\.\.LON\s+([\d\s]+?)(?=\n\s*[A-Z\$]|$)",
    re.IGNORECASE | re.DOTALL,
)


logger = logging.getLogger("spc_bot.vtec_parser")

# Rust core fallback
try:
    import spc_rust_core
    RUST_AVAILABLE = True
    _parse_vtec_rust = spc_rust_core.parse_vtec
    logger.info("Spatial Engine initialized: using Rust hybrid core (extract_latlon_coords, parse_vtec)")
except (ImportError, AttributeError):
    RUST_AVAILABLE = False
    _parse_vtec_rust = None
    logger.debug("Rust core not available, using pure-python fallback for polygon parsing")


def parse_warning_polygon(
    text: str,
) -> Optional[List[Tuple[float, float]]]:
    """Parse the ``LAT...LON`` polygon block from a VTEC product."""
    if not text:
        return None
    m = _LATLON_RE.search(text)
    if not m:
        return None
    
    raw_coords_str = m.group(1)

    # Try Rust optimized parser first
    if RUST_AVAILABLE:
        try:
            coords = spc_rust_core.extract_latlon_coords(raw_coords_str)
            if coords:
                return coords
        except Exception as e:
            logger.debug(f"Rust extract_latlon_coords failed: {e}. Falling back to Python.")

    # Fallback to Python
    nums = raw_coords_str.split()
    coords: List[Tuple[float, float]] = []
    for i in range(0, len(nums) - 1, 2):
        try:
            lat = int(nums[i]) / 100.0
            lon = -(int(nums[i + 1]) / 100.0)
        except ValueError:
            continue
        if not (15.0 <= lat <= 75.0 and -170.0 <= lon <= -60.0):
            # Sanity-clip — NWS warnings only fire over US territory.
            continue
        coords.append((lat, lon))
    return coords or None


def get_polygon_centroid(
    coords: List[Tuple[float, float]]
) -> Optional[Tuple[float, float]]:
    """Calculate the simple arithmetic centroid of a list of (lat, lon) pairs."""
    if not coords:
        return None
    lat_sum = sum(c[0] for c in coords)
    lon_sum = sum(c[1] for c in coords)
    n = len(coords)
    return (lat_sum / n, lon_sum / n)
