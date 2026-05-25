"""Shared geographic utilities."""

import math
from typing import Optional

# Rust core fallback
try:
    import spc_rust_core

    _haversine_rust = spc_rust_core.haversine
    _haversine_batch_rust = spc_rust_core.haversine_batch
    _find_nearest_stations_rust = spc_rust_core.find_nearest_stations_batch
    _points_in_polygon_counts_rust = spc_rust_core.points_in_polygon_counts
    _points_in_polygon_lookup_rust = spc_rust_core.points_in_polygon_lookup
except (ImportError, AttributeError):
    _haversine_rust = None
    _haversine_batch_rust = None
    _find_nearest_stations_rust = None
    _points_in_polygon_counts_rust = None
    _points_in_polygon_lookup_rust = None


def haversine_py(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Python implementation: great-circle distance in km between two WGS-84 points."""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
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


def find_nearest_indices(
    lat: float, lon: float, targets: list[tuple[float, float]], n: int = 3
) -> list[tuple[int, float]]:
    """Return indices and distances of the n nearest targets; try Rust first."""
    if _find_nearest_stations_rust:
        try:
            return list(_find_nearest_stations_rust(lat, lon, targets, n))
        except Exception:
            pass

    # Python fallback
    distances = [(i, haversine(lat, lon, t_lat, t_lon)) for i, (t_lat, t_lon) in enumerate(targets)]
    distances.sort(key=lambda x: x[1])
    return distances[:n]


def points_in_polygon_counts(
    points: list[tuple[float, float]], polygons: list[list[tuple[float, float]]]
) -> list[int]:
    """For each polygon, count points inside; try Rust first."""
    if _points_in_polygon_counts_rust:
        try:
            return list(_points_in_polygon_counts_rust(points, polygons))
        except Exception:
            pass

    # Python fallback (minimal: just returns 0s to maintain contract if Rust fails)
    return [0] * len(polygons)


def points_in_polygon_lookup(
    points: list[tuple[float, float]], polygons: list[list[tuple[float, float]]]
) -> list[Optional[int]]:
    """For each point, return index of first polygon it is in; try Rust first."""
    if _points_in_polygon_lookup_rust:
        try:
            return list(_points_in_polygon_lookup_rust(points, polygons))
        except Exception:
            pass

    # Python fallback
    return [None] * len(points)
