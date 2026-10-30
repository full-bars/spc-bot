"""
Unit tests for utils/compare_utils.cleanup_old_versions — outlook version retention.
"""

import os
import time

import pytest

import utils.compare_utils as compare_utils


@pytest.fixture
def versions_dir(tmp_path, monkeypatch):
    d = tmp_path / "outlook_versions"
    d.mkdir()
    monkeypatch.setattr(compare_utils, "OUTLOOK_VERSIONS_DIR", str(d))
    return d


def _make_version(directory, name, age_hours):
    path = directory / name
    path.write_bytes(b"x" * 1024)
    mtime = time.time() - age_hours * 3600
    os.utime(path, (mtime, mtime))
    return path


@pytest.mark.asyncio
async def test_cleanup_deletes_old_versions_and_keeps_recent(versions_dir):
    old = _make_version(versions_dir, "day1_categorical_20260701_120000_000000.png", 100)
    recent_prev = _make_version(versions_dir, "day1_categorical_20260702_120000_000000.png", 30)
    newest = _make_version(versions_dir, "day1_categorical_20260702_180000_000000.png", 2)

    deleted, freed = await compare_utils.cleanup_old_versions(max_age_hours=48)

    assert not old.exists()
    assert recent_prev.exists()
    assert newest.exists()
    assert deleted == 1
    assert freed == 1024


@pytest.mark.asyncio
async def test_cleanup_keeps_newest_version_of_stale_product(versions_dir):
    # Product not updated for three days: every version is past the cutoff.
    older = _make_version(versions_dir, "day3_hail_20260701_060000_000000.png", 120)
    newest_stale = _make_version(versions_dir, "day3_hail_20260701_120000_000000.png", 80)

    deleted, _ = await compare_utils.cleanup_old_versions(max_age_hours=48)

    assert not older.exists()
    assert newest_stale.exists()
    assert deleted == 1


@pytest.mark.asyncio
async def test_cleanup_retains_latest_per_product_independently(versions_dir):
    stale_a = _make_version(versions_dir, "day1_wind_20260701_000000_000000.png", 90)
    stale_b = _make_version(versions_dir, "day2_wind_20260701_000000_000000.png", 60)
    stale_a_newest = _make_version(versions_dir, "day1_wind_20260701_060000_000000.png", 85)

    deleted, _ = await compare_utils.cleanup_old_versions(max_age_hours=48)

    assert not stale_a.exists()
    assert stale_a_newest.exists()
    assert stale_b.exists()  # sole (and therefore newest) version of day2_wind
    assert deleted == 1
