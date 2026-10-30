import asyncio
import logging
import os
import shutil
import time

from discord.ext import commands, tasks

from config import ARCHIVE_DIR, CACHE_DIR, RECORDING_DIR

logger = logging.getLogger("spc_bot.maintenance")


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

        try:
            loop = asyncio.get_running_loop()
            deleted_count, total_size_freed = await loop.run_in_executor(
                None, self._run_cleanup_worker, now, cutoff
            )

            # Outlook panel versions (cache/outlook_versions). Kept in its own try so a
            # failure here cannot stop the event prune or DAT backfill below.
            # 48h, not the 24h default: /compare needs the latest and previous version of each
            # product, and products refresh on the order of hours, so 48h keeps a second version.
            # The newest version per product is kept even when older (see cleanup_old_versions).
            from utils.compare_utils import cleanup_old_versions

            try:
                versions_deleted, versions_freed = await cleanup_old_versions(max_age_hours=48)
            except Exception as e:
                logger.warning(f"[MAINTENANCE] Outlook version prune failed: {e}")
                versions_deleted, versions_freed = 0, 0

            total_deleted = deleted_count + versions_deleted
            total_freed = total_size_freed + versions_freed
            if total_deleted > 0:
                mb_freed = total_freed / (1024 * 1024)
                versions_mb = versions_freed / (1024 * 1024)
                logger.info(
                    f"[MAINTENANCE] Cleanup complete. Removed {total_deleted} items ({mb_freed:.2f} MB freed; "
                    f"outlook versions {versions_deleted} / {versions_mb:.2f} MB)"
                )
            else:
                logger.info("[MAINTENANCE] Cleanup complete. No files needed deletion.")

            # Prune significant_events older than 365 days
            from utils.events_db import backfill_dat_guids, prune_old_significant_events

            await prune_old_significant_events(days=365)

            # Backfill missing DAT GUIDs via geographic matching
            await backfill_dat_guids(days=30)

        except Exception as e:
            logger.exception(f"[MAINTENANCE] Error during cache cleanup: {e}")

    def _run_cleanup_worker(self, now, cutoff):
        deleted_count = 0
        total_size_freed = 0

        if not os.path.exists(CACHE_DIR):
            return 0, 0

        extensions_to_prune = (".png", ".gif", ".jpg", ".jpeg", ".tmp", ".has")

        # 1. Prune root cache files
        for filename in os.listdir(CACHE_DIR):
            if not filename.lower().endswith(extensions_to_prune):
                continue

            filepath = os.path.join(CACHE_DIR, filename)
            if not os.path.isfile(filepath):
                continue

            try:
                file_stat = os.stat(filepath)
                if file_stat.st_mtime < cutoff:
                    os.remove(filepath)
                    deleted_count += 1
                    total_size_freed += file_stat.st_size
            except OSError as e:
                logger.error(f"[MAINTENANCE] Failed to delete cache file {filepath}: {e}")

        # 2. Cleanup orphaned VAD recording mission directories
        if os.path.exists(RECORDING_DIR):
            for entry in os.scandir(RECORDING_DIR):
                if entry.is_dir():
                    # Mission dirs are named site_timestamp
                    # If older than 24h, it's either finished or stuck
                    try:
                        if entry.stat().st_mtime < (now - 86400):
                            shutil.rmtree(entry.path)
                            logger.info(f"[MAINTENANCE] Pruned orphaned mission dir: {entry.name}")
                    except Exception as e:
                        logger.warning(
                            f"[MAINTENANCE] Failed to prune orphaned mission dir {entry.name}: {e}"
                        )

        # 3. Enforce budget on Event Archive (GIFs)
        # Default to 1GB budget for archived GIFs
        ARCHIVE_BUDGET_MB = 1024
        if os.path.exists(ARCHIVE_DIR):
            gif_files = []
            for entry in os.scandir(ARCHIVE_DIR):
                if entry.is_file() and entry.name.endswith(".gif"):
                    try:
                        gif_files.append((entry.path, entry.stat()))
                    except OSError:
                        continue

            # Sort by mtime (oldest first)
            gif_files.sort(key=lambda x: x[1].st_mtime)

            total_archive_size = sum(f[1].st_size for f in gif_files)
            if (total_archive_size / (1024 * 1024)) > ARCHIVE_BUDGET_MB:
                logger.info(
                    f"[MAINTENANCE] Event archive ({total_archive_size / (1024 * 1024):.1f}MB) exceeds budget ({ARCHIVE_BUDGET_MB}MB). Pruning oldest..."
                )
                while (total_archive_size / (1024 * 1024)) > ARCHIVE_BUDGET_MB and gif_files:
                    path, stat = gif_files.pop(0)
                    try:
                        os.remove(path)
                        total_archive_size -= stat.st_size
                        deleted_count += 1
                    except OSError as e:
                        logger.warning(f"[MAINTENANCE] Failed to delete archive file {path}: {e}")

        return deleted_count, total_size_freed


async def setup(bot: commands.Bot):
    await bot.add_cog(MaintenanceCog(bot))
