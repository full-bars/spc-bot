"""
Unit tests for cogs/maintenance.py — cache cleanup and DB pruning.
"""

import os
import time
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from cogs.maintenance import MaintenanceCog


@pytest.mark.asyncio
@pytest.mark.usefixtures("maintenance_dirs")
async def test_cleanup_cache_loop_prunes_files(tmp_path):
    bot = MagicMock()
    bot.state.is_primary = True
    bot.wait_until_ready = AsyncMock()

    # Setup a mock cache dir
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    # Create an old file
    old_file = cache_dir / "old.png"
    old_file.write_text("junk")
    # Set mtime to 3 days ago
    old_mtime = time.time() - (72 * 3600)
    os.utime(old_file, (old_mtime, old_mtime))

    # Create a new file
    new_file = cache_dir / "new.png"
    new_file.write_text("junk")

    with patch("cogs.maintenance.CACHE_DIR", str(cache_dir)), patch(
        "utils.events_db.prune_old_significant_events", AsyncMock()
    ) as mock_prune_db:
        # Patch __init__ to avoid starting the real loop
        with patch.object(MaintenanceCog, "__init__", lambda s, b: setattr(s, "bot", b)):
            cog = MaintenanceCog(bot)
            # Manually trigger the loop once
            await cog.cleanup_cache_loop()

    assert not old_file.exists()
    assert new_file.exists()
    assert mock_prune_db.called


@pytest.fixture
def maintenance_dirs(tmp_path, monkeypatch):
    """Point every directory the daily pass touches at a tmp tree."""
    import utils.compare_utils as compare_utils

    root = tmp_path / "isolated_root"
    root.mkdir()
    versions_dir = root / "outlook_versions"
    versions_dir.mkdir()
    monkeypatch.setattr("cogs.maintenance.CACHE_DIR", str(root))
    monkeypatch.setattr("cogs.maintenance.RECORDING_DIR", str(tmp_path / "recordings"))
    monkeypatch.setattr("cogs.maintenance.ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setattr(compare_utils, "OUTLOOK_VERSIONS_DIR", str(versions_dir))
    return versions_dir


def _make_aged_file(path, age_hours):
    path.write_bytes(b"x" * 2048)
    mtime = time.time() - age_hours * 3600
    os.utime(path, (mtime, mtime))
    return path


def _make_cog():
    bot = MagicMock()
    bot.state.is_primary = True
    bot.wait_until_ready = AsyncMock()
    with patch.object(MaintenanceCog, "__init__", lambda s, b: setattr(s, "bot", b)):
        return MaintenanceCog(bot)


@pytest.mark.asyncio
async def test_cleanup_cache_loop_invokes_outlook_version_prune(maintenance_dirs):
    # Regression: cleanup_old_versions had no caller, so outlook_versions grew without bound.
    old = _make_aged_file(maintenance_dirs / "day1_categorical_20260701_120000_000000.png", 60)
    newest_stale = _make_aged_file(maintenance_dirs / "day3_hail_20260701_120000_000000.png", 70)
    recent = _make_aged_file(maintenance_dirs / "day1_categorical_20260702_120000_000000.png", 1)

    with patch("utils.events_db.prune_old_significant_events", AsyncMock()), patch(
        "utils.events_db.backfill_dat_guids", AsyncMock()
    ):
        await _make_cog().cleanup_cache_loop()

    assert not old.exists()
    assert newest_stale.exists()
    assert recent.exists()


@pytest.mark.asyncio
@pytest.mark.usefixtures("maintenance_dirs")
async def test_outlook_prune_failure_does_not_block_rest_of_pass():
    mock_prune_db = AsyncMock()
    mock_backfill = AsyncMock()
    failing_prune = AsyncMock(side_effect=OSError("disk gone"))

    with patch("utils.compare_utils.cleanup_old_versions", failing_prune), patch(
        "utils.events_db.prune_old_significant_events", mock_prune_db
    ), patch("utils.events_db.backfill_dat_guids", mock_backfill):
        await _make_cog().cleanup_cache_loop()

    assert failing_prune.called
    assert mock_prune_db.called
    assert mock_backfill.called
