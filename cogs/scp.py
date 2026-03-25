# cogs/scp.py
import logging
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands

from config import SCP_CHANNEL_ID, SCP_IMAGE_URLS, AUTO_CACHE_FILE, PACIFIC
from utils.cache import (
    auto_cache,
    last_post_times,
    check_partial_updates_parallel,
    save_downloaded_images,
)

logger = logging.getLogger("scp_bot")


class SCPCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.task = bot.loop.create_task(self.auto_post_scp_daily())

    def cog_unload(self):
        self.task.cancel()

    async def auto_post_scp_daily(self):
        """
        Wake up at 6am and 6pm Pacific and post updated SCP forecast
        graphics from NIU/Gensini if the images have changed.
        """
        await self.bot.wait_until_ready()
        post_hours = [6, 18]

        while not self.bot.is_closed():
            now = datetime.now(PACIFIC)
            future_times = [
                now.replace(hour=h, minute=0, second=0, microsecond=0)
                for h in post_hours
            ]
            future_times = [
                t if t > now else t + timedelta(days=1)
                for t in future_times
            ]
            target_time = min(future_times)
            sleep_secs = (target_time - now).total_seconds()
            logger.info(
                f"[SCP_DAILY] Sleeping {sleep_secs/3600:.2f} hours until "
                f"next SCP post at {target_time}"
            )
            await discord.utils.sleep_until(target_time)

            channel = self.bot.get_channel(SCP_CHANNEL_ID)
            if not channel:
                logger.warning("[SCP_DAILY] SCP channel not found")
                continue

            try:
                updated_count, total_count, downloaded_data = (
                    await check_partial_updates_parallel(SCP_IMAGE_URLS, auto_cache)
                )
                if updated_count > 0:
                    files = await save_downloaded_images(
                        SCP_IMAGE_URLS, downloaded_data, AUTO_CACHE_FILE, auto_cache
                    )
                    if files:
                        await channel.send(
                            "**New SCP Forecast Graphics Available**\n"
                            "Supercell Composite Parameter — NIU/Gensini CFSv2",
                            files=[discord.File(fp) for fp in files],
                        )
                        last_post_times["scp"] = datetime.now(timezone.utc)
                        logger.info(f"[SCP_DAILY] Posted {len(files)} SCP images")
                    else:
                        logger.info("[SCP_DAILY] Images updated but no files saved")
                else:
                    logger.info("[SCP_DAILY] No SCP image updates detected")
            except Exception as e:
                logger.error(f"[SCP_DAILY] Unexpected error: {e}", exc_info=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(SCPCog(bot))
