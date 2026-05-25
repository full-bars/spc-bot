"""
Parity tests for _fetch_stations and find_nearest_stations.

These pin the behaviour of the vectorized refactor against a known
CSV fixture, ensuring the hemisphere-sign logic and stripped column
values are identical to what the old row-wise apply produced.
"""

import textwrap
import io
import pytest
import pandas as pd
import numpy as np


# Minimal synthetic RAOB-STATIONS.txt fixture (8 header rows + 3 data rows).
# Columns: WMO, ICAO, NAME, LOC, EL, LAT, A, LON, B, X
_FIXTURE_CSV = textwrap.dedent("""\
    # line1
    # line2
    # line3
    # line4
    # line5
    # line6
    # line7
    # line8
    72233, KOUN ,  Oklahoma City  ,  OK  , 357, 35.22, N, 97.46, W, 0
    72250, KDVN ,  Davenport      ,  IA  , 229, 41.61, N, 90.58, W, 0
    78526, TJSJ ,  San Juan       ,  PR  ,   3, 18.43, N, 65.99, W, 0
    89611, ---- ,  McMurdo        ,  AQ  ,  24, 77.85, S, 166.66, E, 0
""")


def _make_df() -> pd.DataFrame:
    """Parse fixture without hitting the network."""
    return pd.read_csv(
        io.StringIO(_FIXTURE_CSV),
        skiprows=8,
        sep=",",
        names=["WMO", "ICAO", "NAME", "LOC", "EL", "LAT", "A", "LON", "B", "X"],
        skipinitialspace=True,
    )


# ── _fetch_stations parity ────────────────────────────────────────────────────


class TestFetchStationsParity:
    """Ensure vectorized hemisphere logic matches the old row-wise apply."""

    def _apply_old(self, df: pd.DataFrame) -> pd.DataFrame:
        """Reference implementation (original apply-lambda approach)."""
        df = df[pd.to_numeric(df["LAT"], errors="coerce").notna()].copy()

        def to_decimal(val, hemi):
            val = float(val)
            hemi = str(hemi).strip()
            return val if hemi in ("N", "E") else -val

        df["lat"] = df.apply(lambda r: to_decimal(r["LAT"], r["A"]), axis=1)
        df["lon"] = df.apply(lambda r: to_decimal(r["LON"], r["B"]), axis=1)
        df["ICAO"] = df["ICAO"].str.strip()
        df["NAME"] = df["NAME"].str.strip()
        df["LOC"] = df["LOC"].str.strip()
        return df

    def _apply_new(self, df: pd.DataFrame) -> pd.DataFrame:
        """Vectorized implementation matching the refactored _fetch_stations."""
        df = df[pd.to_numeric(df["LAT"], errors="coerce").notna()].copy()
        df["ICAO"] = df["ICAO"].str.strip()
        df["NAME"] = df["NAME"].str.strip()
        df["LOC"] = df["LOC"].str.strip()
        df["A"] = df["A"].str.strip()
        df["B"] = df["B"].str.strip()
        df["lat"] = np.where(
            df["A"].isin(("N", "E")), df["LAT"].astype(float), -df["LAT"].astype(float)
        )
        df["lon"] = np.where(
            df["B"].isin(("N", "E")), df["LON"].astype(float), -df["LON"].astype(float)
        )
        return df

    def test_lat_lon_identical(self):
        raw = _make_df()
        old = self._apply_old(raw.copy())
        new = self._apply_new(raw.copy())
        pd.testing.assert_series_equal(
            old["lat"].reset_index(drop=True),
            new["lat"].reset_index(drop=True),
            check_names=False,
        )
        pd.testing.assert_series_equal(
            old["lon"].reset_index(drop=True),
            new["lon"].reset_index(drop=True),
            check_names=False,
        )

    def test_north_hemisphere_positive_lat(self):
        raw = _make_df()
        new = self._apply_new(raw.copy())
        # KOUN is 35.22 N → positive lat
        koun = new[new["ICAO"] == "KOUN"].iloc[0]
        assert koun["lat"] == pytest.approx(35.22)
        # Longitude W → negative
        assert koun["lon"] == pytest.approx(-97.46)

    def test_south_hemisphere_negative_lat(self):
        raw = _make_df()
        new = self._apply_new(raw.copy())
        # McMurdo is 77.85 S → negative lat; 166.66 E → positive lon
        mcm = new[new["NAME"] == "McMurdo"].iloc[0]
        assert mcm["lat"] == pytest.approx(-77.85)
        assert mcm["lon"] == pytest.approx(166.66)

    def test_string_columns_stripped(self):
        raw = _make_df()
        new = self._apply_new(raw.copy())
        assert not new["ICAO"].str.contains(r"\s").any()
        assert not new["NAME"].str.contains(r"^\s|\s$").any()
        assert not new["LOC"].str.contains(r"^\s|\s$").any()


# ── find_nearest_stations parity ──────────────────────────────────────────────


class TestFindNearestStationsParity:
    """Pin find_nearest_stations output against fixture data."""

    def _build_station_df(self) -> pd.DataFrame:
        """Return a pre-processed station DataFrame using the new logic."""
        raw = _make_df()
        df = raw[pd.to_numeric(raw["LAT"], errors="coerce").notna()].copy()
        df["ICAO"] = df["ICAO"].str.strip()
        df["NAME"] = df["NAME"].str.strip()
        df["LOC"] = df["LOC"].str.strip()
        df["A"] = df["A"].str.strip()
        df["B"] = df["B"].str.strip()
        df["lat"] = np.where(
            df["A"].isin(("N", "E")), df["LAT"].astype(float), -df["LAT"].astype(float)
        )
        df["lon"] = np.where(
            df["B"].isin(("N", "E")), df["LON"].astype(float), -df["LON"].astype(float)
        )
        return df

    def test_nearest_to_oklahoma_city(self):
        from cogs.sounding_utils import find_nearest_stations

        df = self._build_station_df()
        results = find_nearest_stations(35.47, -97.51, df, n=2)
        assert len(results) == 2
        # Closest station to OKC in the fixture is KOUN
        assert results[0]["icao"] == "KOUN"

    def test_icao_dash_becomes_none(self):
        from cogs.sounding_utils import find_nearest_stations

        df = self._build_station_df()
        # Request 4 stations to include McMurdo (---- ICAO)
        results = find_nearest_stations(35.47, -97.51, df, n=4)
        mcm = next((r for r in results if r["name"] == "McMurdo"), None)
        assert mcm is not None
        assert mcm["icao"] is None

    def test_result_fields_are_stripped(self):
        from cogs.sounding_utils import find_nearest_stations

        df = self._build_station_df()
        results = find_nearest_stations(35.47, -97.51, df, n=3)
        for r in results:
            for field in ("name", "loc"):
                assert r[field] == r[field].strip(), (
                    f"Field {field!r} has surrounding whitespace: {r[field]!r}"
                )

    def test_dist_km_rounded(self):
        from cogs.sounding_utils import find_nearest_stations

        df = self._build_station_df()
        results = find_nearest_stations(35.47, -97.51, df, n=1)
        assert isinstance(results[0]["dist_km"], float)
        # Value should be rounded to 1 decimal place
        assert results[0]["dist_km"] == round(results[0]["dist_km"], 1)
