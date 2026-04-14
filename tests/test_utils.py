# tests/test_utils.py
"""
Unit tests for utility modules.

Run with: python -m pytest tests/ -v
"""

import asyncio
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest


# ── change_detection tests ───────────────────────────────────────────────────

class TestChangeDetection:
    def test_calculate_hash_bytes(self):
        from utils.change_detection import calculate_hash_bytes

        h1 = calculate_hash_bytes(b"hello")
        h2 = calculate_hash_bytes(b"hello")
        h3 = calculate_hash_bytes(b"world")

        assert h1 == h2
        assert h1 != h3
        assert len(h1) == 64  # SHA-256 hex

    def test_is_placeholder_image_empty(self):
        from utils.change_detection import is_placeholder_image

        assert is_placeholder_image(b"") is True
        assert is_placeholder_image(b"tiny") is True
        assert is_placeholder_image(b"x" * 100) is True
        assert is_placeholder_image(b"x" * 2047) is True

    def test_is_placeholder_image_real(self):
        from utils.change_detection import is_placeholder_image

        assert is_placeholder_image(b"x" * 2048) is False
        assert is_placeholder_image(b"x" * 50000) is False

    def test_get_cache_path_for_url(self):
        from utils.change_detection import get_cache_path_for_url

        path1 = get_cache_path_for_url("https://example.com/img.png")
        path2 = get_cache_path_for_url("https://example.com/img.png")
        path3 = get_cache_path_for_url("https://example.com/other.gif")

        assert path1 == path2
        assert path1 != path3
        assert path1.endswith(".png")
        assert path3.endswith(".gif")
        assert "cached_" in path1


# ── cache helper tests ───────────────────────────────────────────────────────

class TestCacheHelpers:
    def test_format_timedelta(self):
        from utils.cache import format_timedelta

        assert format_timedelta(timedelta(seconds=30)) == "0m"
        assert format_timedelta(timedelta(minutes=5)) == "5m"
        assert format_timedelta(timedelta(hours=2, minutes=15)) == "2h 15m"
        assert format_timedelta(timedelta(days=1, hours=3)) == "1d 3h 0m"
        assert format_timedelta(timedelta(seconds=-5)) == "just now"

    def test_format_timedelta_edge_cases(self):
        from utils.cache import format_timedelta

        assert format_timedelta(timedelta(0)) == "0m"
        assert format_timedelta(timedelta(days=7)) == "7d 0m"


# ── radar s3 helper tests ────────────────────────────────────────────────────

class TestRadarS3:
    def test_parse_z_time_hhmm(self):
        from cogs.radar.s3 import parse_z_time

        ref = datetime(2026, 4, 8, 0, 0, 0, tzinfo=timezone.utc)
        result = parse_z_time("22:30", ref)
        assert result.hour == 22
        assert result.minute == 30
        assert result.second == 0

    def test_parse_z_time_4digit(self):
        from cogs.radar.s3 import parse_z_time

        ref = datetime(2026, 4, 8, 0, 0, 0, tzinfo=timezone.utc)
        result = parse_z_time("1845Z", ref)
        assert result.hour == 18
        assert result.minute == 45

    def test_parse_z_time_hour_only(self):
        from cogs.radar.s3 import parse_z_time

        ref = datetime(2026, 4, 8, 0, 0, 0, tzinfo=timezone.utc)
        result = parse_z_time("22Z", ref)
        assert result.hour == 22
        assert result.minute == 0

    def test_resolve_z_range_same_day(self):
        from cogs.radar.s3 import resolve_z_range

        ref = datetime(2026, 4, 8, 0, 0, 0, tzinfo=timezone.utc)
        start, end, dates = resolve_z_range("12", "18", ref)
        assert start.hour == 12
        assert end.hour == 18
        assert len(dates) >= 1

    def test_resolve_z_range_cross_midnight(self):
        from cogs.radar.s3 import resolve_z_range

        ref = datetime(2026, 4, 8, 0, 0, 0, tzinfo=timezone.utc)
        start, end, dates = resolve_z_range("22", "04", ref)
        assert start.hour == 22
        assert end.hour == 4
        assert end.day == 9  # next day
        assert len(dates) == 2

    def test_resolve_z_range_same_time_raises(self):
        from cogs.radar.s3 import resolve_z_range

        ref = datetime(2026, 4, 8, 0, 0, 0, tzinfo=timezone.utc)
        with pytest.raises(ValueError, match="same"):
            resolve_z_range("12", "12", ref)


# ── spc_urls tests ───────────────────────────────────────────────────────────

class TestSpcUrls:
    @pytest.mark.asyncio
    async def test_get_spc_urls_day1_with_tabs(self):
        """Test URL resolution when SPC page has the expected tab structure."""
        from utils.spc_urls import get_spc_urls

        fake_html = (
            b"<html><script>"
            b"show_tab('otlk_1200');"
            b"show_tab('probotlk_1200_torn');"
            b"show_tab('probotlk_1200_wind');"
            b"show_tab('probotlk_1200_hail');"
            b"</script></html>"
        )
        with patch(
            "utils.spc_urls.http_get_bytes",
            new_callable=AsyncMock,
            return_value=(fake_html, 200),
        ):
            urls = await get_spc_urls(1)

        assert len(urls) == 4
        assert "day1otlk_1200.png" in urls[0]
        assert "probotlk_1200_torn" in urls[1]
        assert "probotlk_1200_wind" in urls[2]
        assert "probotlk_1200_hail" in urls[3]

    @pytest.mark.asyncio
    async def test_get_spc_urls_day3_with_tabs(self):
        from utils.spc_urls import get_spc_urls

        fake_html = (
            b"<html><script>"
            b"show_tab('otlk_0730');"
            b"show_tab('prob_0730');"
            b"</script></html>"
        )
        with patch(
            "utils.spc_urls.http_get_bytes",
            new_callable=AsyncMock,
            return_value=(fake_html, 200),
        ):
            urls = await get_spc_urls(3)

        assert len(urls) == 2
        assert "day3otlk_0730.png" in urls[0]
        assert "day3prob_0730.png" in urls[1]

    @pytest.mark.asyncio
    async def test_get_spc_urls_fetch_failure(self):
        from utils.spc_urls import get_spc_urls

        with patch(
            "utils.spc_urls.http_get_bytes",
            new_callable=AsyncMock,
            return_value=(None, 500),
        ):
            urls = await get_spc_urls(1)

        assert urls == []

    @pytest.mark.asyncio
    async def test_get_spc_urls_no_tabs_found(self):
        from utils.spc_urls import get_spc_urls

        fake_html = b"<html><body>No tabs here</body></html>"
        with patch(
            "utils.spc_urls.http_get_bytes",
            new_callable=AsyncMock,
            return_value=(fake_html, 200),
        ):
            urls = await get_spc_urls(1)

        assert urls == []


# ── http tests ───────────────────────────────────────────────────────────────

class TestHttp:
    def test_get_retry_after_numeric(self):
        from utils.http import _get_retry_after
        from unittest.mock import MagicMock

        response = MagicMock()
        response.headers = {"Retry-After": "30"}
        assert _get_retry_after(response) == 30.0

    def test_get_retry_after_missing(self):
        from utils.http import _get_retry_after
        from unittest.mock import MagicMock

        response = MagicMock()
        response.headers = {}
        assert _get_retry_after(response) is None

    def test_get_retry_after_non_numeric(self):
        from utils.http import _get_retry_after
        from unittest.mock import MagicMock

        response = MagicMock()
        response.headers = {"Retry-After": "Wed, 09 Apr 2026 12:00:00 GMT"}
        assert _get_retry_after(response) is None


# ── csu_mlp tests ─────────────────────────────────────────────────────────────

class TestCSUMLPUrls:
    def test_build_url_day1_00z(self):
        from cogs.csu_mlp import _build_url
        from datetime import datetime, timezone

        date = datetime(2026, 4, 8, tzinfo=timezone.utc)
        url = _build_url(1, date, "00")
        assert "severe_gefso_2021_day1" in url
        assert "2026040800" in url
        assert "severe_ml_day1_all_gefso" in url
        assert "040912.png" in url

    def test_build_url_day1_12z(self):
        from cogs.csu_mlp import _build_url
        from datetime import datetime, timezone

        date = datetime(2026, 4, 8, tzinfo=timezone.utc)
        url = _build_url(1, date, "12")
        assert "2026040812" in url

    def test_build_url_day4_uses_aggregate_slug(self):
        from cogs.csu_mlp import _build_url
        from datetime import datetime, timezone

        date = datetime(2026, 4, 8, tzinfo=timezone.utc)
        url = _build_url(4, date, "00")
        assert "severe_ml_day4_gefso" in url
        assert "severe_ml_day4_all_gefso" not in url

    def test_build_url_day3_uses_all_slug(self):
        from cogs.csu_mlp import _build_url
        from datetime import datetime, timezone

        date = datetime(2026, 4, 8, tzinfo=timezone.utc)
        url = _build_url(3, date, "00")
        assert "severe_ml_day3_all_gefso" in url

    def test_build_url_valid_date_offset(self):
        from cogs.csu_mlp import _build_url
        from datetime import datetime, timezone

        date = datetime(2026, 4, 8, tzinfo=timezone.utc)
        url = _build_url(2, date, "00")
        # valid date = init + 2 days = Apr 10
        assert "041012.png" in url

    def test_build_url_day8(self):
        from cogs.csu_mlp import _build_url
        from datetime import datetime, timezone

        date = datetime(2026, 4, 8, tzinfo=timezone.utc)
        url = _build_url(8, date, "00")
        assert "severe_gefso_2021_day8" in url
        assert "severe_ml_day8_gefso" in url
        assert "041612.png" in url

    def test_build_panel_url_hazards(self):
        from cogs.csu_mlp import _build_panel_url
        from datetime import datetime, timezone

        date = datetime(2026, 4, 8, tzinfo=timezone.utc)
        url = _build_panel_url("hazards_fcst_6panel", date)
        assert "severe_gefso_2021_day1" in url
        assert "2026040800" in url
        assert "hazards_fcst_6panel_040812.png" in url

    def test_build_panel_url_severe(self):
        from cogs.csu_mlp import _build_panel_url
        from datetime import datetime, timezone

        date = datetime(2026, 4, 8, tzinfo=timezone.utc)
        url = _build_panel_url("severe_fcst_6panel", date)
        assert "severe_fcst_6panel_040812.png" in url

    def test_load_posted_today_missing_file(self):
        from unittest.mock import AsyncMock, patch
        with patch("cogs.csu_mlp.get_state", new=AsyncMock(return_value=None)):
            from cogs.csu_mlp import _load_posted_today
            import asyncio
            result = asyncio.run(_load_posted_today())
        assert result == set()

    def test_load_posted_today_stale_date(self):
        import json
        from unittest.mock import AsyncMock, patch
        value = json.dumps({"date": "2020-01-01", "days": [1, 2, 3]})
        with patch("cogs.csu_mlp.get_state", new=AsyncMock(return_value=value)):
            from cogs.csu_mlp import _load_posted_today
            import asyncio
            result = asyncio.run(_load_posted_today())
        assert result == set()

    def test_load_posted_today_current_date(self):
        import json
        from datetime import datetime, timezone
        from unittest.mock import AsyncMock, patch
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        value = json.dumps({"date": today, "days": [1, 2, "panel12"]})
        with patch("cogs.csu_mlp.get_state", new=AsyncMock(return_value=value)):
            from cogs.csu_mlp import _load_posted_today
            import asyncio
            result = asyncio.run(_load_posted_today())
        assert 1 in result
        assert 2 in result
        assert "panel12" in result

# ── ncar tests ────────────────────────────────────────────────────────────────

class TestNCARWxNext:
    def test_wxnext_url_format(self):
        from cogs.ncar import _wxnext_url
        from datetime import datetime, timezone

        date = datetime(2026, 4, 8, tzinfo=timezone.utc)
        url = _wxnext_url(date)
        assert url == (
            "https://www2.mmm.ucar.edu/projects/ncar_ensemble/ainwp/img"
            "/predictions_grid_wxnext_mean_any_2026040800.png"
        )

    def test_wxnext_url_different_date(self):
        from cogs.ncar import _wxnext_url
        from datetime import datetime, timezone

        date = datetime(2026, 1, 1, tzinfo=timezone.utc)
        url = _wxnext_url(date)
        assert "2026010100.png" in url

    def test_load_state_missing_file(self):
        from unittest.mock import AsyncMock, patch
        with patch("cogs.ncar.get_state", new=AsyncMock(return_value=None)):
            from cogs.ncar import _load_state
            import asyncio
            result = asyncio.run(_load_state())
        assert result == {}

    def test_load_state_valid(self):
        import json
        from unittest.mock import AsyncMock, patch
        value = json.dumps({"date": "2026-04-08", "hash": "abc123"})
        with patch("cogs.ncar.get_state", new=AsyncMock(return_value=value)):
            from cogs.ncar import _load_state
            import asyncio
            result = asyncio.run(_load_state())
        assert result["date"] == "2026-04-08"
        assert result["hash"] == "abc123"

    def test_load_state_corrupt(self):
        from unittest.mock import AsyncMock, patch
        with patch("cogs.ncar.get_state", new=AsyncMock(return_value="not json {{{")):
            from cogs.ncar import _load_state
            import asyncio
            result = asyncio.run(_load_state())
        assert result == {}

    def test_save_state(self):
        from unittest.mock import AsyncMock, patch
        with patch("cogs.ncar.set_state", new=AsyncMock()):
            from cogs.ncar import _save_state
            import asyncio
            asyncio.run(_save_state("2026-04-08", "hashvalue123"))
        assert True

# ── sounding_utils tests ──────────────────────────────────────────────────────

class TestSoundingUtils:
    def test_haversine_known_distance(self):
        from cogs.sounding_utils import haversine
        # OKC to Norman, OK — roughly 27km
        dist = haversine(35.47, -97.52, 35.22, -97.44)
        assert 25 < dist < 35

    def test_haversine_same_point(self):
        from cogs.sounding_utils import haversine
        assert haversine(35.0, -97.0, 35.0, -97.0) == 0.0

    def test_find_nearest_stations(self):
        from cogs.sounding_utils import find_nearest_stations
        import pandas as pd
        # Mini fake station dataframe
        df = pd.DataFrame([
            {"ICAO": "KOKC", "WMO": "72357", "NAME": "OKLAHOMA CITY",
             "LOC": "OK US", "lat": 35.23, "lon": -97.47, "dist_km": 0},
            {"ICAO": "KOUN", "WMO": "72353", "NAME": "NORMAN",
             "LOC": "OK US", "lat": 35.22, "lon": -97.44, "dist_km": 0},
            {"ICAO": "KTLX", "WMO": "72364", "NAME": "TULSA",
             "LOC": "OK US", "lat": 36.20, "lon": -95.98, "dist_km": 0},
        ])
        results = find_nearest_stations(35.47, -97.52, df, n=2)
        assert len(results) == 2
        assert results[0]["icao"] == "KOKC"

    def test_parse_sounding_time_valid(self):
        from cogs.sounding_utils import parse_sounding_time
        result = parse_sounding_time("04-10-2026 12z")
        assert result == ("2026", "04", "10", "12")

    def test_parse_sounding_time_00z(self):
        from cogs.sounding_utils import parse_sounding_time
        result = parse_sounding_time("04-10-2026 00z")
        assert result == ("2026", "04", "10", "00")

    def test_parse_sounding_time_invalid_hour(self):
        from cogs.sounding_utils import parse_sounding_time
        import pytest
        with pytest.raises(ValueError):
            parse_sounding_time("04-10-2026 25z")  # invalid hour > 23

    def test_parse_sounding_time_bad_format(self):
        from cogs.sounding_utils import parse_sounding_time
        import pytest
        with pytest.raises(ValueError):
            parse_sounding_time("2026-04-10 12z")

    def test_parse_sounding_time_none(self):
        from cogs.sounding_utils import parse_sounding_time
        assert parse_sounding_time(None) is None

    def test_get_recent_sounding_times(self):
        from cogs.sounding_utils import get_recent_sounding_times
        times = get_recent_sounding_times(4)
        assert len(times) == 4
        for year, month, day, hour in times:
            assert hour in ("00", "12")
            assert len(year) == 4
            assert len(month) == 2
            assert len(day) == 2

    def test_user_dark_mode_prefs(self):
        from unittest.mock import AsyncMock, patch
        from cogs.sounding_utils import get_user_dark_mode
        # Default (no entry) should be False
        with patch("cogs.sounding_utils.get_state", new=AsyncMock(return_value=None)):
            import asyncio
            assert asyncio.run(get_user_dark_mode(12345)) is False
        # Entry "1" should be True
        with patch("cogs.sounding_utils.get_state", new=AsyncMock(return_value="1")):
            import asyncio
            assert asyncio.run(get_user_dark_mode(12345)) is True
        # Entry "0" should be False
        with patch("cogs.sounding_utils.get_state", new=AsyncMock(return_value="0")):
            import asyncio
            assert asyncio.run(get_user_dark_mode(12345)) is False

    def test_plot_path_format(self):
        from cogs.sounding_views import _plot_path
        path = _plot_path("OUN", "2026", "04", "10", "12")
        assert "sounding_OUN_20260410_12z" in path


# ── Import smoke tests ────────────────────────────────────────────────────────

class TestCogImports:
    """Verify all cogs can be imported without errors."""
    def test_import_mesoscale(self):
        import cogs.mesoscale
    def test_import_watches(self):
        import cogs.watches
    def test_import_outlooks(self):
        import cogs.outlooks
    def test_import_csu_mlp(self):
        import cogs.csu_mlp
    def test_import_ncar(self):
        import cogs.ncar
    def test_import_sounding(self):
        import cogs.sounding
    def test_import_sounding_utils(self):
        import cogs.sounding_utils
    def test_import_status(self):
        import cogs.status
    def test_import_scp(self):
        import cogs.scp
