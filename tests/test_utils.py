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


# ── persistence tests ────────────────────────────────────────────────────────

class TestPersistence:
    def test_atomic_json_dump_creates_file(self, tmp_path):
        from utils.persistence import atomic_json_dump

        filepath = str(tmp_path / "test.json")
        data = {"key": "value", "num": 42}
        atomic_json_dump(data, filepath)

        assert os.path.exists(filepath)
        with open(filepath) as f:
            loaded = json.load(f)
        assert loaded == data

    def test_atomic_json_dump_overwrites(self, tmp_path):
        from utils.persistence import atomic_json_dump

        filepath = str(tmp_path / "test.json")
        atomic_json_dump({"old": True}, filepath)
        atomic_json_dump({"new": True}, filepath)

        with open(filepath) as f:
            loaded = json.load(f)
        assert loaded == {"new": True}

    def test_atomic_json_dump_creates_dirs(self, tmp_path):
        from utils.persistence import atomic_json_dump

        filepath = str(tmp_path / "sub" / "dir" / "test.json")
        atomic_json_dump([1, 2, 3], filepath)

        with open(filepath) as f:
            assert json.load(f) == [1, 2, 3]

    def test_load_json_if_exists_missing(self, tmp_path):
        from utils.persistence import load_json_if_exists

        result = load_json_if_exists(str(tmp_path / "nope.json"))
        assert result == {}

    def test_load_json_if_exists_valid(self, tmp_path):
        from utils.persistence import load_json_if_exists

        filepath = str(tmp_path / "data.json")
        with open(filepath, "w") as f:
            json.dump({"a": 1}, f)

        result = load_json_if_exists(filepath)
        assert result == {"a": 1}

    def test_load_json_if_exists_corrupt(self, tmp_path):
        from utils.persistence import load_json_if_exists

        filepath = str(tmp_path / "bad.json")
        with open(filepath, "w") as f:
            f.write("not json {{{")

        result = load_json_if_exists(filepath)
        assert result == {}

    def test_load_set_if_exists(self, tmp_path):
        from utils.persistence import load_set_if_exists

        filepath = str(tmp_path / "set.json")
        with open(filepath, "w") as f:
            json.dump(["a", "b", "c"], f)

        result = load_set_if_exists(filepath)
        assert result == {"a", "b", "c"}

    def test_load_set_if_exists_missing(self, tmp_path):
        from utils.persistence import load_set_if_exists

        result = load_set_if_exists(str(tmp_path / "nope.json"))
        assert result == set()

    def test_save_set(self, tmp_path):
        from utils.persistence import save_set

        filepath = str(tmp_path / "set.json")
        save_set({"c", "a", "b"}, filepath)

        with open(filepath) as f:
            loaded = json.load(f)
        assert loaded == ["a", "b", "c"]  # sorted


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

    def test_prune_tracked_set_under_limit(self):
        from utils.cache import prune_tracked_set

        s = {"0001", "0002", "0003"}
        # Should not prune — under limit
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            filepath = f.name
        try:
            prune_tracked_set(s, 10, filepath)
            assert len(s) == 3
        finally:
            if os.path.exists(filepath):
                os.unlink(filepath)

    def test_prune_tracked_set_over_limit(self):
        from utils.cache import prune_tracked_set

        s = {str(i).zfill(4) for i in range(1, 11)}  # 0001..0010
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            filepath = f.name
        try:
            prune_tracked_set(s, 5, filepath)
            assert len(s) == 5
            # Should keep the 5 highest
            assert "0010" in s
            assert "0009" in s
            assert "0006" in s
            assert "0001" not in s
        finally:
            if os.path.exists(filepath):
                os.unlink(filepath)


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
