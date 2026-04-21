# cogs/scp.py
import logging
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

from config import MANUAL_CACHE_FILE, PACIFIC, MODELS_CHANNEL_ID, SCP_IMAGE_URLS
from utils.cache import (
    download_images_parallel,
)

logger = logging.getLogger("spc_bot")


class SCPCog(commands.Cog):
    MANAGED_TASK_NAMES = [("auto_post_scp", "auto_post_scp_daily")]

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._next_post_time: datetime | None = None
        self.auto_post_scp.start()

    def cog_unload(self):
        self.auto_post_scp.cancel()

    def _compute_next_post_time(self) -> datetime:
        """Compute the next 6am or 6pm Pacific post time."""
        now = datetime.now(PACIFIC)
        post_hours = [6, 18]
        future_times = [
            now.replace(hour=h, minute=0, second=0, microsecond=0)
            for h in post_hours
        ]
        future_times = [
            t if t > now else t + timedelta(days=1) for t in future_times
        ]
        return min(future_times)

    @tasks.loop(minutes=1)
    async def auto_post_scp(self):
        """
        Check every minute if it's time to post SCP graphics.
        Posts at 6am and 6pm Pacific if the images have changed.
        """
        await self.bot.wait_until_ready()
        if not self.bot.state.is_primary:
            return

        if self._next_post_time is None:
            self._next_post_time = self._compute_next_post_time()
            logger.info(
                f"[SCP_DAILY] Next SCP post scheduled for {self._next_post_time}"
            )

        now = datetime.now(PACIFIC)
        if now < self._next_post_time:
            return

        # Time to post
        channel = self.bot.get_channel(MODELS_CHANNEL_ID)
        if not channel:
            logger.warning("[SCP_DAILY] SCP channel not found")
            self._next_post_time = self._compute_next_post_time()
            return

        try:
            files = await download_images_parallel(
                SCP_IMAGE_URLS,
                MANUAL_CACHE_FILE,
                self.bot.state.manual_cache,
                use_cached=False,
            )
            if files:
                await channel.send(
                    "**New SCP Forecast Graphics Available**\n"
                    "Supercell Composite Parameter — NIU/Gensini CFSv2",
                    files=[discord.File(fp) for fp in files],
                )
                self.bot.state.last_post_times["scp"] = datetime.now(timezone.utc)
                logger.info(f"[SCP_DAILY] Posted {len(files)} SCP images")
            else:
                logger.info("[SCP_DAILY] No SCP images could be downloaded")
        except Exception as e:
            logger.error(f"[SCP_DAILY] Unexpected error: {e}", exc_info=True)

        self._next_post_time = self._compute_next_post_time()
        logger.info(
            f"[SCP_DAILY] Next SCP post scheduled for {self._next_post_time}"
        )

    @auto_post_scp.after_loop
    async def after_scp_loop(self):
        if self.auto_post_scp.is_being_cancelled():
            return
        task = self.auto_post_scp.get_task()
        try:
            exc = task.exception() if task else None
        except Exception:
            exc = None
        if exc:
            logger.error(
                f"[TASK] auto_post_scp stopped: {type(exc).__name__}: {exc}",
                exc_info=exc,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(SCPCog(bot))
