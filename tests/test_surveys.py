import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from cogs.reports import ReportsCog

@pytest.mark.asyncio
async def test_pns_triggers_survey_check(isolated_events_db):
    bot = MagicMock()
    bot.get_channel.return_value = AsyncMock()
    cog = ReportsCog(bot)
    
    # Mock state_store functions
    with patch("cogs.reports.get_posted_surveys", return_value=AsyncMock(return_value=set())), \
         patch("cogs.reports.add_posted_survey", return_value=AsyncMock()), \
         patch("cogs.reports.prune_posted_surveys", return_value=AsyncMock()):
        
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
        
        import json
        mock_content = json.dumps(mock_meta).encode()
        
        with patch("cogs.reports.http_get_bytes", side_effect=[
            # We don't need to mock the image download here, 
            # just the metadata call in _check_for_surveys
            (mock_content, 200)
        ]):
            # Use a mock for _check_for_surveys to avoid background task issues in test
            # or just await it directly for the test
            with patch.object(ReportsCog, "_check_for_surveys", wraps=cog._check_for_surveys) as mock_check:
                await cog._handle_pns("20240521-KDMX-PNS", raw_pns)
                
                # Verify date extraction
                assert "2024-05-21" in str(mock_check.call_args)
                
                # Manually await the background task if needed, or check if send was called
                # In our code it's asyncio.create_task, so we might need to wait or mock it.
                # For this test, let's just await the helper directly to verify it works.
                await cog._check_for_surveys("2024-05-21")
                
                # Check if channel.send was called with the embed
                bot.get_channel.return_value.send.assert_called()
                args, kwargs = bot.get_channel.return_value.send.call_args
                embed = kwargs.get("embed")
                assert embed.title == "🌪️ Tornado Track + Lead Time"
                assert "{GUID-123}" in embed.image.url
