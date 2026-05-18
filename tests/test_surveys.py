"""Tests for DAT survey logic integration in cogs/reports.py."""

import json
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cogs.reports import ReportsCog

@pytest.mark.asyncio
async def test_pns_triggers_survey_check(isolated_events_db):
    bot = MagicMock()
    bot.get_channel.return_value = AsyncMock()
    bot.state.posted_surveys = set()
    bot.state.add_posted_report = AsyncMock()
    bot.state.add_posted_survey = AsyncMock()
    cog = ReportsCog(bot)

    # Mock state_store functions
    raw_pns = """
    ...NWS DAMAGE SURVEY FOR 05/21/2024 TORNADO EVENT...
    RATING: EF-4
    ESTIMATED PEAK WIND: 185 MPH
    SUMMARY: The Greenfield tornado...
    $$
    """

    # Mock http_get_bytes for the metadata call
    mock_meta = {
        "arguments": [
            {
                "id": "datglobalid",
                "options": {
                    "{GUID-123}": "DMX EF4 Greenfield"
                }
            }
        ]
    }

    mock_content = json.dumps(mock_meta).encode()

    with patch("cogs.reports.http_get_bytes", side_effect=[
        # 1. Metadata call
        (mock_content, 200),
        # 2. Image check call (IEM image exists)
        (b"fake-image-data", 200)
    ]), \
    patch("utils.events_db.link_dat_guid_to_tornado", AsyncMock(return_value=("E1", "L1", "M1", "C1"))), \
    patch.object(ReportsCog, "_check_for_surveys", AsyncMock()) as mock_check:
        # We need to await _handle_pns, which will call _check_for_surveys in a task.
        await cog._handle_pns("20240521-KDMX-PNS", raw_pns)
        # Give background task a moment
        await asyncio.sleep(0.1)

    # Verify report was marked posted
    bot.state.add_posted_report.assert_called_with("20240521-KDMX-PNS")
    
    # Verify _check_for_surveys was called with the date
    mock_check.assert_called_with("2024-05-21")
