"""Tests for the progressive nearest-station expansion in /sounding.

Regression coverage for the "all three nearest stations silent" case
(e.g. the Tucson-area stations nearest KEMX): the search must widen until a
station with live data is found, and fall back to the nearest stations when
even a wide search finds nothing.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from cogs import sounding_utils


def _station_df(n: int = 9) -> pd.DataFrame:
    """Synthetic stations; index 0 is nearest to (35.0, -97.0)."""
    rows = []
    for i in range(n):
        rows.append(
            {
                "ICAO": f"KS{i:02d}",
                "WMO": f"72{i:03d}",
                "NAME": f"STATION {i}",
                "LOC": "US",
                "lat": 35.0 - 0.1 * i,
                "lon": -97.0 + 0.1 * i,
            }
        )
    return pd.DataFrame(rows)


def _iem_response(has_data: bool) -> dict:
    if has_data:
        return {"profiles": [{"profile": {"p": [850.0], "T": [10.0]}}]}
    return {"profiles": []}


def _mock_iem_for(stations_with_data: set[str]) -> AsyncMock:
    """http_get_json mock: only the listed ICAO stations return profiles."""

    async def _get_json(url, **kwargs):
        station = url.split("station=")[1].split("&")[0]
        return _iem_response(station in stations_with_data)

    return AsyncMock(side_effect=_get_json)


@pytest.fixture(autouse=True)
def _clear_hour_cache():
    sounding_utils._HOUR_CACHE.clear()
    yield
    sounding_utils._HOUR_CACHE.clear()


async def test_expands_until_station_with_data_found():
    """Nearest stations silent -> search widens and returns a data station."""
    df = _station_df(6)
    mock_iem = _mock_iem_for({"KS03"})  # only the 4th-nearest has data
    with patch("cogs.sounding_utils.http_get_json", mock_iem):
        verified, candidates = await sounding_utils.find_nearest_stations_with_data(
            35.0, -97.0, df, max_n=6, hours_back=4
        )

    assert [s["icao"] for s in verified] == ["KS03"]
    assert {s["icao"] for s in candidates} == {f"KS{i:02d}" for i in range(6)}


async def test_widens_past_empty_first_step():
    """Nothing in the first window -> keeps widening until data is found."""
    df = _station_df(9)
    mock_iem = _mock_iem_for({"KS08"})  # only the 9th-nearest has data
    with patch("cogs.sounding_utils.http_get_json", mock_iem):
        verified, candidates = await sounding_utils.find_nearest_stations_with_data(
            35.0, -97.0, df, max_n=9, hours_back=4
        )

    assert [s["icao"] for s in verified] == ["KS08"]
    assert len(candidates) == 9


async def test_caps_verified_stations_at_three():
    """Even with many data stations, only the 3 nearest are returned."""
    df = _station_df(12)
    mock_iem = _mock_iem_for({f"KS{i:02d}" for i in range(6)})  # all data
    with patch("cogs.sounding_utils.http_get_json", mock_iem):
        verified, _ = await sounding_utils.find_nearest_stations_with_data(
            35.0, -97.0, df, max_n=6, hours_back=4
        )

    assert [s["icao"] for s in verified] == ["KS00", "KS01", "KS02"]


async def test_returns_empty_with_fallback_candidates_when_no_data():
    """No station has data anywhere -> ([], nearest candidates) so the caller
    can still offer the nearest stations (post_sounding handles the miss)."""
    df = _station_df(6)
    mock_iem = _mock_iem_for(set())
    with patch("cogs.sounding_utils.http_get_json", mock_iem):
        verified, candidates = await sounding_utils.find_nearest_stations_with_data(
            35.0, -97.0, df, max_n=6, hours_back=4
        )

    assert verified == []
    assert [s["icao"] for s in candidates[:3]] == ["KS00", "KS01", "KS02"]


async def test_any_only_uses_cached_hit_without_probing():
    """any_only returns a cached hit without any new HTTP requests."""
    now = datetime.now(timezone.utc)
    newest = (
        str(now.year),
        str(now.month).zfill(2),
        str(now.day).zfill(2),
        str(now.hour).zfill(2),
    )
    key = f"KS00:{now.strftime('%Y%m%d%H')}"
    sounding_utils._HOUR_CACHE[key] = (now, newest)

    mock_iem = AsyncMock()
    with patch("cogs.sounding_utils.http_get_json", mock_iem):
        avail = await sounding_utils.get_available_sounding_times_iem(
            "KS00", hours_back=4, any_only=True
        )

    assert avail == [newest]
    mock_iem.assert_not_called()


async def test_any_only_probes_fewer_hours_than_full_check():
    """any_only stops at the first hit instead of probing the whole window."""
    now = datetime.now(timezone.utc)
    newest = (
        str(now.year),
        str(now.month).zfill(2),
        str(now.day).zfill(2),
        str(now.hour).zfill(2),
    )
    newest_ts = now.strftime("%Y-%m-%dT%H:00:00Z")

    async def _get_json(url, **kwargs):
        # Data only at the newest hour in the window.
        return _iem_response(url.split("ts=")[1].startswith(newest_ts))

    mock_full = AsyncMock(side_effect=_get_json)
    with patch("cogs.sounding_utils.http_get_json", mock_full):
        avail_full = await sounding_utils.get_available_sounding_times_iem(
            "KS00", hours_back=11, any_only=False
        )
    full_calls = mock_full.call_count

    sounding_utils._HOUR_CACHE.clear()
    mock_any = AsyncMock(side_effect=_get_json)
    with patch("cogs.sounding_utils.http_get_json", mock_any):
        avail_any = await sounding_utils.get_available_sounding_times_iem(
            "KS00", hours_back=11, any_only=True
        )
    any_calls = mock_any.call_count

    assert avail_full[0] == newest
    assert full_calls == 12  # one request per hour in the window
    assert avail_any == [newest]
    assert any_calls < full_calls
