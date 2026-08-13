"""Coverage round 8: radar download helpers and history time helpers (pure logic)."""

import os
import time
from datetime import datetime

import pytz

from cogs.radar import downloads
from cogs import radar_history
from cogs import historical


# ── Radar download helpers ───────────────────────────────────────────────────


def test_format_file_size_units():
    assert downloads.format_file_size(512) == "512.00 B"
    assert downloads.format_file_size(2048) == "2.00 KB"
    assert downloads.format_file_size(5 * 1024 * 1024) == "5.00 MB"
    assert downloads.format_file_size(3 * 1024**3) == "3.00 GB"
    assert downloads.format_file_size(2 * 1024**4) == "2.00 TB"


def test_get_progress_bar():
    assert downloads.get_progress_bar(0) == "░" * 30 + " 0.0%"
    assert downloads.get_progress_bar(50) == "█" * 15 + "░" * 15 + " 50.0%"
    assert downloads.get_progress_bar(100) == "█" * 30 + " 100.0%"
    assert downloads.get_progress_bar(150).endswith("100.0%")  # clamped
    assert downloads.get_progress_bar(25, length=10) == "█" * 2 + "░" * 8 + " 25.0%"


async def test_cleanup_old_files_deletes_old_keeps_new(tmp_path):
    old = tmp_path / "old.gif"
    new = tmp_path / "new.gif"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    past = time.time() - 10 * 86400
    os.utime(old, (past, past))

    await downloads.cleanup_old_files(tmp_path, age_threshold=7 * 86400)

    assert not old.exists()
    assert new.exists()


async def test_cleanup_old_files_missing_dir(tmp_path):
    await downloads.cleanup_old_files(tmp_path / "nope", age_threshold=100)  # no raise


# ── Radar history time helpers ───────────────────────────────────────────────


def test_parse_timezone():
    assert radar_history._parse_timezone("America/Chicago").zone == "America/Chicago"
    assert radar_history._parse_timezone("Not/AZone") == pytz.UTC


def test_parse_local_time_12h():
    tz = pytz.timezone("America/Chicago")
    dt = radar_history._parse_local_time("2026-08-11", "2:30 PM", tz)
    assert dt is not None
    assert dt.year == 2026 and dt.month == 8 and dt.day == 11
    assert dt.hour == 14 and dt.minute == 30
    assert dt.tzinfo is not None


def test_parse_local_time_24h_and_bad():
    tz = pytz.timezone("America/Chicago")
    dt = radar_history._parse_local_time("2026-08-11", "14:30", tz)
    assert dt is not None and dt.hour == 14
    assert radar_history._parse_local_time("2026-08-11", "garbage", tz) is None


def test_round_down_to_minute():
    dt = datetime(2026, 8, 11, 12, 37, 42, 123456)
    assert radar_history._round_down_to_minute(dt) == datetime(2026, 8, 11, 12, 35)
    assert radar_history._round_down_to_minute(dt, interval=10) == datetime(2026, 8, 11, 12, 30)


def test_iem_frame_url():
    dt = datetime(2026, 8, 11, 12, 35)
    url = radar_history._iem_frame_url(dt)
    assert url.endswith("/2026/08/11/GIS/uscomp/n0q_202608111235.png")


# ── Historical archive URL builder ───────────────────────────────────────────


def test_build_url_legacy_categorical():
    url = historical._build_url(2026, "20260220", 1, "categorical", "1630")
    assert url.endswith("/2026/day1otlk_20260220_1630.gif")


def test_build_url_legacy_probabilistic():
    url = historical._build_url(2026, "20260220", 2, "tornado", "1630")
    assert url.endswith("/2026/day2probotlk_20260220_1630_torn.gif")


def test_build_url_post_cutoff_returns_none_and_invalid_raises():
    assert historical._build_url(2026, "20260310", 1, "categorical", "1630") is None
    try:
        historical._build_url(2026, "20260220", 3, "tornado", "1630")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unsupported day/product combo")
