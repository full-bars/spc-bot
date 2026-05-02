"""
Unit tests for cogs/maintenance.py — cache cleanup and DB pruning.
"""

import os
import time
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from cogs.maintenance import MaintenanceCog

@pytest.mark.asyncio
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
    
    with patch("cogs.maintenance.CACHE_DIR", str(cache_dir)), \
         patch("utils.events_db.prune_old_significant_events", AsyncMock()) as mock_prune_db:
        # Patch __init__ to avoid starting the real loop
        with patch.object(MaintenanceCog, "__init__", lambda s, b: setattr(s, "bot", b)):
            cog = MaintenanceCog(bot)
            # Manually trigger the loop once
            await cog.cleanup_cache_loop()
        
    assert not old_file.exists()
    assert new_file.exists()
    assert mock_prune_db.called
