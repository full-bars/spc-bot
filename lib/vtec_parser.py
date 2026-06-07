"""VTEC (Valid Time Extent Codegroup) parsing utilities.

Parses NWS VTEC strings (NWS Directive 10-1703) which encode warning/watch
event metadata in a compact format. Also includes polygon parsing for
geographic warning regions.

These utilities have zero Discord dependencies and can be reused in CLI tools,
batch processors, and other contexts.
"""
import logging
from typing import List, Optional, Tuple

logger = logging.getLogger("spc_bot.vtec_parser")

# Rust core mandatory
try:
    import spc_rust_core
    RUST_AVAILABLE = True
    _parse_vtec_rust = spc_rust_core.parse_vtec
    _parse_warning_polygon_rust = spc_rust_core.parse_warning_polygon
    logger.info("Spatial Engine initialized: using Rust core (parse_vtec, parse_warning_polygon)")
except (ImportError, AttributeError) as e:
    RUST_AVAILABLE = False
    _parse_vtec_rust = None
    _parse_warning_polygon_rust = None
    logger.error(f"Rust core NOT available! VTEC/Spatial parsing will fail: {e}")


def parse_vtec(text: str) -> Optional[dict]:
    """Parse VTEC string using Rust engine."""
    if _parse_vtec_rust:
        try:
            return _parse_vtec_rust(text)
        except Exception as e:
            logger.error(f"Rust parse_vtec failed: {e}")
    return None


def parse_warning_polygon(
    text: str,
) -> Optional[List[Tuple[float, float]]]:
    """Parse the ``LAT...LON`` polygon block using Rust engine."""
    if not text:
        return None

    if _parse_warning_polygon_rust:
        try:
            result = _parse_warning_polygon_rust(text)
            return result or None
        except Exception as e:
            logger.error(f"Rust parse_warning_polygon failed: {e}")

    return None


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
