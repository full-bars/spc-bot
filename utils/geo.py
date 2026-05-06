"""Shared geographic utilities."""

import math

# Rust core fallback
try:
    import spc_rust_core
    _haversine_rust = spc_rust_core.haversine
    _haversine_batch_rust = spc_rust_core.haversine_batch
except (ImportError, AttributeError):
    _haversine_rust = None
    _haversine_batch_rust = None


def haversine_py(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Python implementation: great-circle distance in km between two WGS-84 points."""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two WGS-84 points; try Rust first."""
    if _haversine_rust:
        try:
            return float(_haversine_rust(lat1, lon1, lat2, lon2))
        except Exception:
            pass
    return haversine_py(lat1, lon1, lat2, lon2)


def haversine_batch(
    origin_lat: float, origin_lon: float, targets: list[tuple[float, float]]
) -> list[float]:
    """Batch haversine calculation; try Rust first."""
    if _haversine_batch_rust:
        try:
            return list(_haversine_batch_rust(origin_lat, origin_lon, targets))
        except Exception:
            pass
    return [haversine(origin_lat, origin_lon, lat, lon) for lat, lon in targets]
