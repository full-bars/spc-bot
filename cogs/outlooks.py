# cogs/outlooks.py
import asyncio
import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from config import AUTO_CACHE_FILE, SPC_CHANNEL_ID, SPC_URLS, SPC_URLS_FALLBACK
from utils.backoff import TaskBackoff
from utils.state_store import set_posted_urls
from utils.cache import (
    check_all_urls_exist_parallel,
    check_partial_updates_parallel,
    save_downloaded_images,
)
from utils.spc_urls import get_spc_urls

logger = logging.getLogger("spc_bot")


async def check_and_post_day(channel: discord.TextChannel, day: int, state):
    """
    Check and post a SPC outlook day, resolving current PNG URLs dynamically.
    Detects partial updates and waits up to 20 minutes for all images before
    posting whatever is available.
    """
    urls = await get_spc_urls(day)
    day_key = f"day{day}"

    fallback_urls = SPC_URLS_FALLBACK.get(day, [])
    if urls == fallback_urls and state.last_posted_urls.get(day_key) == urls:
        logger.info(f"[Day {day}] Fallback URLs unchanged from last post — skipping")
        return

    if not await check_all_urls_exist_parallel(urls):
        return

    updated_count, total_count, downloaded_data = (
        await check_partial_updates_parallel(urls, state.auto_cache)
    )

    if updated_count == 0:
        if day_key in state.partial_update_state:
            elapsed = (
                datetime.now() - state.partial_update_state[day_key]["start_time"]
            ).total_seconds() / 60
            if elapsed > 20:
                logger.warning(
                    f"[Day {day}] Timeout after {elapsed:.1f} min with no further "
                    f"updates — clearing partial state without posting"
                )
                state.partial_update_state.pop(day_key, None)
            else:
                logger.debug(
                    f"[Day {day}] No new updates this cycle, still waiting "
                    f"({elapsed:.1f} min elapsed)"
                )
        return

    if updated_count < total_count:
        if day_key not in state.partial_update_state:
            state.partial_update_state[day_key] = {
                "start_time": datetime.now(),
                "downloaded_data": downloaded_data,
            }
            logger.info(
                f"[Day {day}] Partial update ({updated_count}/{total_count}). "
                f"Entering aggressive check mode."
            )
        else:
            stored = state.partial_update_state[day_key]["downloaded_data"]
            stored.update({k: v for k, v in downloaded_data.items() if v is not None})
            elapsed = (
                datetime.now() - state.partial_update_state[day_key]["start_time"]
            ).total_seconds() / 60

            if elapsed > 20:
                logger.warning(
                    f"[Day {day}] Timeout after {elapsed:.1f} min. "
                    f"Posting {len(stored)}/{total_count} images."
                )
                files = await save_downloaded_images(
                    urls, stored, AUTO_CACHE_FILE, state.auto_cache
                )
                if files:
                    try:
                        await channel.send(
                            f"**Latest SPC Day {day} Outlooks**",
                            files=[discord.File(fp) for fp in files],
                        )
                        state.last_post_times[day_key] = datetime.now(timezone.utc)
                    except Exception as e:
                        logger.exception(
                            f"Failed to send partial post for Day {day}: {e}"
                        )
                state.partial_update_state.pop(day_key, None)
            else:
                logger.info(
                    f"[Day {day}] Waiting: {updated_count}/{total_count} updated "
                    f"({elapsed:.1f} min elapsed)"
                )
        return

    # All images updated
    if day_key in state.partial_update_state:
        saved = state.partial_update_state[day_key]["downloaded_data"]
        saved.update({k: v for k, v in downloaded_data.items() if v is not None})
        downloaded_data = saved
        elapsed = (
            datetime.now() - state.partial_update_state[day_key]["start_time"]
        ).total_seconds() / 60
        logger.info(
            f"[Day {day}] All images ready after {elapsed:.1f} min. Posting."
        )
        state.partial_update_state.pop(day_key, None)

    files = await save_downloaded_images(
        urls, downloaded_data, AUTO_CACHE_FILE, state.auto_cache
    )
    if files:
        try:
            await channel.send(
                f"**Latest SPC Day {day} Outlooks**",
                files=[discord.File(fp) for fp in files],
            )
            state.last_post_times[day_key] = datetime.now(timezone.utc)
            state.last_posted_urls[day_key] = urls
            await set_posted_urls(day_key, urls)
            logger.info(f"[Day {day}] Posted {len(files)} images. URLs: {urls}")
        except Exception as e:
            logger.exception(f"Failed to send post for Day {day}: {e}")


class OutlooksCog(commands.Cog):
    MANAGED_TASK_NAMES = [
        ("auto_post_spc", "auto_post_spc"),
        ("aggressive_check_spc", "aggressive_check_spc"),
        ("auto_post_spc48", "auto_post_spc48"),
    ]

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._spc_backoff = TaskBackoff("auto_post_spc")
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
        if not self.bot.state.is_primary:
            return
        channel = self.bot.get_channel(SPC_CHANNEL_ID)
        if not channel:
            logger.warning("SPC channel not found for auto_post_spc")
            return
        try:
            await asyncio.gather(
                check_and_post_day(channel, 1, self.bot.state),
                check_and_post_day(channel, 2, self.bot.state),
                check_and_post_day(channel, 3, self.bot.state),
            )
            self._spc_backoff.success()
        except Exception as e:
            logger.warning(f"[auto_post_spc] cycle failed: {e}")
            await self._spc_backoff.failure(self.bot)

    @tasks.loop(seconds=20)
    async def aggressive_check_spc(self):
        await self.bot.wait_until_ready()
        if not self.bot.state.is_primary:
            return
        if not self.bot.state.partial_update_state:
            return
        channel = self.bot.get_channel(SPC_CHANNEL_ID)
        if not channel:
            logger.warning("SPC channel not found for aggressive_check_spc")
            return
        # Run partial checks concurrently
        day_keys = list(self.bot.state.partial_update_state.keys())
        tasks_ = []
        for day_key in day_keys:
            try:
                day = int(day_key.replace("day", ""))
            except Exception:
                continue
            tasks_.append(check_and_post_day(channel, day, self.bot.state))
        if tasks_:
            await asyncio.gather(*tasks_)

    @tasks.loop(minutes=30)
    async def auto_post_spc48(self):
        await self.bot.wait_until_ready()
        if not self.bot.state.is_primary:
            return
        channel = self.bot.get_channel(SPC_CHANNEL_ID)
        if not channel:
            logger.warning("SPC channel not found for auto_post_spc48")
            return
        urls = SPC_URLS["48"]
        if not await check_all_urls_exist_parallel(urls):
            return
        updated_count, total_count, downloaded_data = (
            await check_partial_updates_parallel(urls, self.bot.state.auto_cache)
        )
        if updated_count > 0:
            files = await save_downloaded_images(
                urls, downloaded_data, AUTO_CACHE_FILE, self.bot.state.auto_cache
            )
            if files:
                try:
                    await channel.send(
                        "**Latest SPC Day 4-8 Outlook**",
                        files=[discord.File(fp) for fp in files],
                    )
                    self.bot.state.last_post_times["day48"] = datetime.now(timezone.utc)
                except Exception as e:
                    logger.exception(f"Failed to send SPC48 post: {e}")

    @auto_post_spc.after_loop
    async def after_spc_loop(self):
        if self.auto_post_spc.is_being_cancelled():
            return
        task = self.auto_post_spc.get_task()
        try:
            exc = task.exception() if task else None
        except Exception:
            exc = None
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
        try:
            exc = task.exception() if task else None
        except Exception:
            exc = None
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
        try:
            exc = task.exception() if task and task.done() else None
        except Exception:
            exc = None
        if exc:
            logger.error(
                f"[TASK] aggressive_check_spc stopped: "
                f"{type(exc).__name__}: {exc}",
                exc_info=exc,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(OutlooksCog(bot))
