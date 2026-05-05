"""VTEC (Valid Time Extent Codegroup) parsing utilities.

Parses NWS VTEC strings (NWS Directive 10-1703) which encode warning/watch
event metadata in a compact format. Also includes polygon parsing for
geographic warning regions.

These utilities have zero Discord dependencies and can be reused in CLI tools,
batch processors, and other contexts.
"""
import re
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


def parse_vtec(text: str) -> Optional[dict]:
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


# ── Polygon parsing (LAT...LON block) ────────────────────────────────────────

_LATLON_RE = re.compile(
    r"LAT\.\.\.LON\s+([\d\s]+?)(?=\n\s*[A-Z\$]|$)",
    re.IGNORECASE | re.DOTALL,
)


def parse_warning_polygon(
    text: str,
) -> Optional[List[Tuple[float, float]]]:
    """Parse the ``LAT...LON`` polygon block from a VTEC product.

    Format: pairs of integer values, lat then lon, in degrees * 100,
    space- or newline-delimited. Longitudes are reported as positive
    integers; for the US they convert to negative decimal degrees.

    Returns a list of (lat, lon) decimal-degree pairs, or ``None`` if
    the block is missing or unparseable. Used by PR B's iembot
    fallback to derive a polygon centroid when NWS API hasn't picked
    up the alert yet.
    """
    if not text:
        return None
    m = _LATLON_RE.search(text)
    if not m:
        return None
    nums = m.group(1).split()
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
