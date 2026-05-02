"""
Unit tests for cogs/radar/ — NEXRAD file cleanup.
"""

import os
import time
import pytest
from unittest.mock import MagicMock, patch
from cogs.radar import RadarCog

@pytest.mark.asyncio
async def test_radar_periodic_cleanup_deletes_old_files(tmp_path):
    bot = MagicMock()
    
    # Setup mock radar output dir
    radar_dir = tmp_path / "radar"
    radar_dir.mkdir()
    
    # Create old file
    old_file = radar_dir / "old_radar_KICT"
    old_file.write_text("data")
    # Set mtime to 3 days ago
    old_mtime = time.time() - (48 * 3600)
    os.utime(old_file, (old_mtime, old_mtime))
    
    # Create new file
    new_file = radar_dir / "new_radar_KICT"
    new_file.write_text("data")
    
    with patch("cogs.radar.OUTPUT_DIR", str(radar_dir)), \
         patch("cogs.radar.CLEANUP_AGE_THRESHOLD", 24 * 3600):
        # Patch __init__ to avoid starting real loop
        with patch.object(RadarCog, "__init__", lambda s, b: setattr(s, "bot", b)):
            cog = RadarCog(bot)
            # Manually trigger cleanup
            await cog.periodic_cleanup()
        
    assert not old_file.exists()
    assert new_file.exists()
