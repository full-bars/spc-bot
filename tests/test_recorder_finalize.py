import pytest
from unittest.mock import AsyncMock, MagicMock, patch, ANY
from cogs.recorder import RecorderCog, VADRecordingMission

@pytest.mark.asyncio
async def test_finalize_mission_flow():
    # Setup mock bot and cog
    bot = MagicMock()
    bot.state = MagicMock()
    
    with patch("cogs.recorder.get_executor"), \
         patch("cogs.recorder.os.makedirs"):
        cog = RecorderCog(bot)

    mission = VADRecordingMission("KOUN", 1600000000.0)
    mission.dir = "/tmp/fake_mission_dir"
    mission.event_ids = {"event1"}

    # Mock the executor calls
    cog.executor = MagicMock()
    
    # Mock os.listdir to return some files
    with patch("asyncio.get_running_loop") as mock_loop:
        mock_run = AsyncMock()
        mock_loop.return_value.run_in_executor = mock_run
        
        # 1. os.listdir
        # 2. _render_frame_worker (loop)
        # 3. _make_gif
        # 4. _calc_srh
        # 5. _cleanup
        mock_run.side_effect = [
            ["file1.has", "file2.has"], # os.listdir
            True, True,                 # _render_frame_worker (twice)
            None,                       # _make_gif
            150.0,                      # _calc_srh
            None                        # _cleanup
        ]

        # Mock dependencies
        with patch("utils.events_db.update_event_environment", new_callable=AsyncMock) as mock_update_db, \
             patch.object(cog, "_post_forensic_summary", new_callable=AsyncMock) as mock_post:
            
            await cog._finalize_mission(mission)

            # Verify flow
            assert mock_run.call_count >= 5
            mock_update_db.assert_called_once_with("event1", ANY, 150.0)
            mock_post.assert_called_once()
