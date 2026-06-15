import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone
from cogs.radar.views import ZRangeModal, StartPlusDurationModal, ExplicitRangeModal, NumFilesModal


@pytest.fixture
def mock_interaction():
    interaction = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    return interaction


@pytest.mark.asyncio
@patch("cogs.radar.views.run_download")
async def test_zrange_modal(mock_run_download, mock_interaction):
    date = datetime(2026, 4, 2, tzinfo=timezone.utc)
    modal = ZRangeModal(["KTLX"], date, [])
    # Set the value on the TextInput instance manually
    modal.time_range._value = "22Z-04Z"

    await modal.on_submit(mock_interaction)
    mock_run_download.assert_called_once()
    mock_interaction.response.defer.assert_called_once()


@pytest.mark.asyncio
@patch("cogs.radar.views.run_download")
async def test_start_plus_duration_modal(mock_run_download, mock_interaction):
    date = datetime(2026, 4, 2, tzinfo=timezone.utc)
    modal = StartPlusDurationModal(["KTLX"], date, [])
    modal.start_time._value = "22Z"
    modal.duration._value = "6"

    await modal.on_submit(mock_interaction)
    mock_run_download.assert_called_once()
    mock_interaction.response.defer.assert_called_once()


@pytest.mark.asyncio
@patch("cogs.radar.views.run_download")
async def test_explicit_range_modal(mock_run_download, mock_interaction):
    date = datetime(2026, 4, 2, tzinfo=timezone.utc)
    modal = ExplicitRangeModal(["KTLX"], date, [])
    modal.start._value = "2026-04-02 22:00"
    modal.end._value = "2026-04-03 04:00"

    await modal.on_submit(mock_interaction)
    mock_run_download.assert_called_once()
    mock_interaction.response.defer.assert_called_once()


@pytest.mark.asyncio
@patch("cogs.radar.views.run_download")
async def test_num_files_modal(mock_run_download, mock_interaction):
    date = datetime(2026, 4, 2, tzinfo=timezone.utc)
    modal = NumFilesModal(["KTLX"], date, [])
    modal.num._value = "10"

    await modal.on_submit(mock_interaction)
    mock_run_download.assert_called_once()
    mock_interaction.response.defer.assert_called_once()
