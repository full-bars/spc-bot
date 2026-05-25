import pytest
import pandas as pd
from cogs.sounding_utils import find_nearest_stations
import spc_rust_core


def test_find_nearest_stations_parity():
    # Mock station data
    data = {
        "ICAO": ["KOUN", "KAMA", "KOKC", "KDAL"],
        "WMO": ["72357", "72363", "72353", "72259"],
        "NAME": ["NORMAN", "AMARILLO", "OKC", "DALLAS"],
        "LOC": ["OK", "TX", "OK", "TX"],
        "lat": [35.24, 35.23, 35.39, 32.85],
        "lon": [-97.46, -101.71, -97.60, -96.85],
    }
    df = pd.DataFrame(data)

    # Norman, OK approx coords
    lat, lon = 35.2, -97.5

    # Python-only reference (manual)
    def find_nearest_stations_py(lat, lon, df, n=3):
        from utils.geo import haversine_py

        targets = list(zip(df["lat"], df["lon"]))
        distances = [haversine_py(lat, lon, t_lat, t_lon) for t_lat, t_lon in targets]
        df_copy = df.assign(dist_km=distances)
        nearest = df_copy.nsmallest(n, "dist_km")
        results = []
        for _, row in nearest.iterrows():
            icao = str(row["ICAO"])
            results.append(
                {
                    "icao": icao if icao != "----" else None,
                    "wmo": str(row["WMO"]),
                    "name": str(row["NAME"]),
                    "loc": str(row["LOC"]),
                    "lat": row["lat"],
                    "lon": row["lon"],
                    "dist_km": round(row["dist_km"], 1),
                }
            )
        return results

    py_results = find_nearest_stations_py(lat, lon, df, n=2)
    rust_results = find_nearest_stations(lat, lon, df, n=2)

    assert len(rust_results) == len(py_results)
    for r, p in zip(rust_results, py_results):
        assert r["icao"] == p["icao"]
        assert r["dist_km"] == pytest.approx(p["dist_km"], abs=0.1)


def test_points_in_polygon_batch():
    # Points clearly inside
    points = [(35.0, -97.0), (35.5, -97.5), (32.5, -96.0)]
    # Polygon around OKC
    poly1 = [(34.0, -98.0), (34.0, -96.0), (36.0, -96.0), (36.0, -98.0), (34.0, -98.0)]
    # Polygon around Dallas
    poly2 = [(32.0, -97.0), (32.0, -95.0), (33.0, -95.0), (33.0, -97.0), (32.0, -97.0)]

    polygons = [poly1, poly2]

    counts = spc_rust_core.points_in_polygon_counts(points, polygons)
    assert counts == [2, 1]

    lookup = spc_rust_core.points_in_polygon_lookup(points, polygons)
    assert lookup == [0, 0, 1]
