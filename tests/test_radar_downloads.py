import pytest
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import discord
from datetime import datetime, timezone, timedelta

from cogs.radar.downloads import (
    format_file_size,
    get_progress_bar,
    download_file,
    cleanup_old_files,
    split_and_zip_files,
    send_error,
    run_download,
    download_and_zip,
)


def test_format_file_size():
    assert format_file_size(500) == "500.00 B"
    assert format_file_size(1024) == "1.00 KB"
    assert format_file_size(1024 * 1024 * 1.5) == "1.50 MB"
    assert format_file_size(1024 * 1024 * 1024 * 2) == "2.00 GB"


def test_get_progress_bar():
    bar = get_progress_bar(50, length=10)
    assert bar == "█████░░░░░ 50.0%"


@pytest.mark.asyncio
async def test_download_file(tmp_path):
    output_dir = tmp_path / "radar_data"

    async def dummy_s3_download(key, path, callback):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            f.write("data")
        callback(4)

    with patch("cogs.radar.downloads.s3_download_file", new=dummy_s3_download):
        out_path, dtime, speed = await download_file(
            "test_key", str(output_dir), time.time(), 4, "test.bin"
        )
        assert out_path.exists()
        assert out_path.name == "test.bin"


@pytest.mark.asyncio
async def test_cleanup_old_files(tmp_path):
    test_file = tmp_path / "old.bin"
    test_file.write_text("old")
    # Set mtime to 2 days ago
    old_time = time.time() - (48 * 3600)
    os.utime(test_file, (old_time, old_time))

    new_file = tmp_path / "new.bin"
    new_file.write_text("new")

    await cleanup_old_files(str(tmp_path), 24 * 3600)

    assert not test_file.exists()
    assert new_file.exists()


@pytest.mark.asyncio
async def test_split_and_zip_files(tmp_path):
    f1 = tmp_path / "f1.bin"
    f1.write_text("A" * 1000)
    f2 = tmp_path / "f2.bin"
    f2.write_text("B" * 1000)

    file_paths = [
        (f1, {"RadarSite": "KTLX"}),
        (f2, {"RadarSite": "KTLX"}),
    ]

    # Split size of 1500 means they will be in two separate zips
    zips = await split_and_zip_files(file_paths, ["KTLX"], 1500, tmp_path)
    assert len(zips) == 2
    assert zips[0].name.startswith("KTLX_part")


@pytest.fixture
def mock_interaction():
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.followup = AsyncMock()
    interaction.channel = AsyncMock()
    return interaction


@pytest.mark.asyncio
async def test_send_error(mock_interaction):
    await send_error(mock_interaction, "Test Title", "Test Desc")
    mock_interaction.followup.send.assert_called_once()
    embed = mock_interaction.followup.send.call_args.kwargs["embed"]
    assert embed.title == "❌ Test Title"


@pytest.mark.asyncio
async def test_run_download_no_files(mock_interaction):
    start = datetime.now(timezone.utc) - timedelta(hours=1)
    end = datetime.now(timezone.utc)

    with patch("cogs.radar.downloads.list_files", return_value=[]):
        await run_download(mock_interaction, ["KTLX"], [], start, end)

    mock_interaction.followup.send.assert_called_once()
    assert "No Files Found" in mock_interaction.followup.send.call_args.kwargs["embed"].title
