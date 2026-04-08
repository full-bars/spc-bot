# cogs/outlooks.py
import asyncio
import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from config import SPC_CHANNEL_ID, SPC_URLS
from utils.cache import (
    auto_cache,
    check_all_urls_exist_parallel,
    check_partial_updates_parallel,
    last_post_times,
    last_posted_urls,
    partial_update_state,
    save_downloaded_images,
)
from utils.spc_urls import get_spc_urls

logger = logging.getLogger("spc_bot")


async def check_and_post_day(channel: discord.TextChannel, day: int):
    """
    Check and post a SPC outlook day, resolving current PNG URLs dynamically.
    Detects partial updates and waits up to 20 minutes for all images before
    posting whatever is available.
    """
    from config import AUTO_CACHE_FILE, SPC_URLS_FALLBACK

    urls = await get_spc_urls(day)
    day_key = f"day{day}"

    fallback_urls = SPC_URLS_FALLBACK.get(day, [])
    if urls == fallback_urls and last_posted_urls.get(day_key) == urls:
        logger.info(f"[Day {day}] Fallback URLs unchanged from last post — skipping")
        return

    if not await check_all_urls_exist_parallel(urls):
        return

    updated_count, total_count, downloaded_data = (
        await check_partial_updates_parallel(urls, auto_cache)
    )

    if updated_count == 0:
        if day_key in partial_update_state:
            elapsed = (
                datetime.now() - partial_update_state[day_key]["start_time"]
            ).total_seconds() / 60
            if elapsed < 2:
                logger.debug(
                    f"[Day {day}] No new updates this cycle but partial state "
                    f"is fresh ({elapsed:.1f} min), keeping"
                )
            else:
                logger.info(
                    f"[Day {day}] No updates found after {elapsed:.1f} min; "
                    f"clearing partial state"
                )
                partial_update_state.pop(day_key, None)
        return

    if updated_count < total_count:
        if day_key not in partial_update_state:
            partial_update_state[day_key] = {
                "start_time": datetime.now(),
                "downloaded_data": downloaded_data,
            }
            logger.info(
                f"[Day {day}] Partial update ({updated_count}/{total_count}). "
                f"Entering aggressive check mode."
            )
        else:
            stored = partial_update_state[day_key]["downloaded_data"]
            stored.update({k: v for k, v in downloaded_data.items() if v is not None})
            elapsed = (
                datetime.now() - partial_update_state[day_key]["start_time"]
            ).total_seconds() / 60

            if elapsed > 20:
                logger.warning(
                    f"[Day {day}] Timeout after {elapsed:.1f} min. "
                    f"Posting {len(stored)}/{total_count} images."
                )
                files = await save_downloaded_images(
                    urls, stored, AUTO_CACHE_FILE, auto_cache
                )
                if files:
                    try:
                        await channel.send(
                            f"**Latest SPC Day {day} Outlooks**",
                            files=[discord.File(fp) for fp in files],
                        )
                        last_post_times[day_key] = datetime.now(timezone.utc)
                    except Exception as e:
                        logger.error(
                            f"Failed to send partial post for Day {day}: {e}"
                        )
                partial_update_state.pop(day_key, None)
            else:
                logger.info(
                    f"[Day {day}] Waiting: {updated_count}/{total_count} updated "
                    f"({elapsed:.1f} min elapsed)"
                )
        return

    # All images updated
    if day_key in partial_update_state:
        saved = partial_update_state[day_key]["downloaded_data"]
        saved.update({k: v for k, v in downloaded_data.items() if v is not None})
        downloaded_data = saved
        elapsed = (
            datetime.now() - partial_update_state[day_key]["start_time"]
        ).total_seconds() / 60
        logger.info(
            f"[Day {day}] All images ready after {elapsed:.1f} min. Posting."
        )
        partial_update_state.pop(day_key, None)

    from config import AUTO_CACHE_FILE

    files = await save_downloaded_images(
        urls, downloaded_data, AUTO_CACHE_FILE, auto_cache
    )
    if files:
        try:
            await channel.send(
                f"**Latest SPC Day {day} Outlooks**",
                files=[discord.File(fp) for fp in files],
            )
            last_post_times[day_key] = datetime.now(timezone.utc)
            last_posted_urls[day_key] = urls
            logger.info(f"[Day {day}] Posted {len(files)} images. URLs: {urls}")
        except Exception as e:
            logger.error(f"Failed to send post for Day {day}: {e}")


class OutlooksCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.auto_post_spc.start()
        self.aggressive_check_spc.start()
        self.auto_post_spc48.start()

    def cog_unload(self):
        self.auto_post_spc.cancel()
        self.aggressive_check_spc.cancel()
        self.auto_post_spc48.cancel()

    @tasks.loop(seconds=30)
    async def auto_post_spc(self):
        await self.bot.wait_until_ready()
        channel = self.bot.get_channel(SPC_CHANNEL_ID)
        if not channel:
            logger.warning("SPC channel not found for auto_post_spc")
            return
        # Check all three days concurrently
        await asyncio.gather(
            check_and_post_day(channel, 1),
            check_and_post_day(channel, 2),
            check_and_post_day(channel, 3),
        )

    @tasks.loop(seconds=20)
    async def aggressive_check_spc(self):
        await self.bot.wait_until_ready()
        if not partial_update_state:
            return
        channel = self.bot.get_channel(SPC_CHANNEL_ID)
        if not channel:
            logger.warning("SPC channel not found for aggressive_check_spc")
            return
        # Run partial checks concurrently
        day_keys = list(partial_update_state.keys())
        tasks_ = []
        for day_key in day_keys:
            try:
                day = int(day_key.replace("day", ""))
            except Exception:
                continue
            tasks_.append(check_and_post_day(channel, day))
        if tasks_:
            await asyncio.gather(*tasks_)

    @tasks.loop(minutes=30)
    async def auto_post_spc48(self):
        await self.bot.wait_until_ready()
        channel = self.bot.get_channel(SPC_CHANNEL_ID)
        if not channel:
            logger.warning("SPC channel not found for auto_post_spc48")
            return
        urls = SPC_URLS["48"]
        if not await check_all_urls_exist_parallel(urls):
            return
        updated_count, total_count, downloaded_data = (
            await check_partial_updates_parallel(urls, auto_cache)
        )
        if updated_count > 0:
            from config import AUTO_CACHE_FILE

            files = await save_downloaded_images(
                urls, downloaded_data, AUTO_CACHE_FILE, auto_cache
            )
            if files:
                try:
                    await channel.send(
                        "**Latest SPC Day 4-8 Outlook**",
                        files=[discord.File(fp) for fp in files],
                    )
                    last_post_times["day48"] = datetime.now(timezone.utc)
                except Exception as e:
                    logger.error(f"Failed to send SPC48 post: {e}")

    @auto_post_spc.after_loop
    async def after_spc_loop(self):
        if self.auto_post_spc.is_being_cancelled():
            return
        task = self.auto_post_spc.get_task()
        exc = task.exception() if task else None
        if exc:
            logger.error(
                f"[TASK] auto_post_spc stopped: {type(exc).__name__}: {exc}",
                exc_info=exc,
            )

    @auto_post_spc48.after_loop
    async def after_spc48_loop(self):
        if self.auto_post_spc48.is_being_cancelled():
            return
        task = self.auto_post_spc48.get_task()
        exc = task.exception() if task else None
        if exc:
            logger.error(
                f"[TASK] auto_post_spc48 stopped: {type(exc).__name__}: {exc}",
                exc_info=exc,
            )

    @aggressive_check_spc.after_loop
    async def after_aggressive_loop(self):
        if self.aggressive_check_spc.is_being_cancelled():
            return
        task = self.aggressive_check_spc.get_task()
        exc = task.exception() if task else None
        if exc:
            logger.error(
                f"[TASK] aggressive_check_spc stopped: "
                f"{type(exc).__name__}: {exc}",
                exc_info=exc,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(OutlooksCog(bot))
