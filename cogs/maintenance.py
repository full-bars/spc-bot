import logging
import os
import time
from discord.ext import commands, tasks
from config import CACHE_DIR

logger = logging.getLogger("spc_bot")

class MaintenanceCog(commands.Cog):
    MANAGED_TASK_NAMES = [("cleanup_cache_loop", "cleanup_cache_loop")]

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.cleanup_cache_loop.start()

    def cog_unload(self):
        self.cleanup_cache_loop.cancel()

    @tasks.loop(hours=24)
    async def cleanup_cache_loop(self):
        await self.bot.wait_until_ready()
        
        # Only the primary node performs cleanup to prevent race conditions on shared storage
        if getattr(self.bot.state, "is_primary", False) is False:
            return

        logger.info("[MAINTENANCE] Starting routine cache cleanup")
        
        now = time.time()
        # 48 hours in seconds
        cutoff = now - (48 * 3600)
        
        deleted_count = 0
        total_size_freed = 0
        
        if not os.path.exists(CACHE_DIR):
            return

        extensions_to_prune = (".png", ".gif", ".jpg", ".jpeg", ".tmp")
        
        try:
            for filename in os.listdir(CACHE_DIR):
                if not filename.lower().endswith(extensions_to_prune):
                    continue
                    
                filepath = os.path.join(CACHE_DIR, filename)
                
                # Double check it's a file
                if not os.path.isfile(filepath):
                    continue
                    
                file_stat = os.stat(filepath)
                # Check modification time
                if file_stat.st_mtime < cutoff:
                    try:
                        os.remove(filepath)
                        deleted_count += 1
                        total_size_freed += file_stat.st_size
                    except OSError as e:
                        logger.warning(f"[MAINTENANCE] Failed to delete {filename}: {e}")
                        
            if deleted_count > 0:
                mb_freed = total_size_freed / (1024 * 1024)
                logger.info(f"[MAINTENANCE] Cleanup complete. Removed {deleted_count} files ({mb_freed:.2f} MB freed)")
            else:
                logger.info("[MAINTENANCE] Cleanup complete. No files needed deletion.")

            # Prune significant_events older than 365 days
            from utils.events_db import prune_old_significant_events
            await prune_old_significant_events(days=365)
                
        except Exception as e:
            logger.exception(f"[MAINTENANCE] Error during cache cleanup: {e}")

async def setup(bot: commands.Bot):
    await bot.add_cog(MaintenanceCog(bot))
