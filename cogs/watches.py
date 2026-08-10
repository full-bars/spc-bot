# cogs/watches.py
import asyncio
import logging
import os
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from cogs.watch_fetch import (
    fetch_active_watches_nws,
    fetch_latest_watch_numbers,
    fetch_watch_details,
)
from cogs.watch_format import _build_watch_embed, _watch_files
from config import (
    AUTO_CACHE_FILE,
    MANUAL_CACHE_FILE,
    SPC_CHANNEL_ID,
    SPC_VALID_WATCHES_URL,
)
from utils.backoff import TaskBackoff
from utils.cache import (
    download_single_image,
)
from utils.change_detection import get_cache_path_for_url, is_placeholder_image
from utils.discord_send import safe_send
from utils.http import http_get_bytes

logger = logging.getLogger("spc_bot")


def _read_is_placeholder(path: str) -> bool:
    """Read a file and check whether it is a placeholder image."""
    with open(path, "rb") as f:
        return is_placeholder_image(f.read())


def _log_task_exception(task: asyncio.Task) -> None:
    try:
        exc = task.exception()
    except (asyncio.CancelledError, RuntimeError):
        return
    if exc:
        logger.error("Background task failed", exc_info=exc)


_WATCH_FAST_POLL_INTERVAL_SEC = (
    5  # Stage 1: poll every 5s for image + probs (aggressive for probabilities)
)
_WATCH_SLOW_POLL_INTERVAL_SEC = 60  # Stage 2: poll every 60s for image only


class WatchPaginatorView(discord.ui.View):
    def __init__(self, watch_data, overview_path):
        super().__init__(timeout=300)
        self.watch_data = watch_data
        self.overview_path = overview_path
        self.index = 0
        self.message = None
        self._update_buttons()

    def _update_buttons(self):
        self.prev_btn.disabled = self.index == 0
        self.next_btn.disabled = self.index >= len(self.watch_data) - 1

    def build_embed(self):
        (
            watch_num,
            nws_info,
            image_url,
            text_summary,
            probs,
            cache_path,
            is_pds,
        ) = self.watch_data[self.index]
        wtype = nws_info.get("type", "SVR") if isinstance(nws_info, dict) else nws_info
        expires = nws_info.get("expires") if isinstance(nws_info, dict) else None
        is_tornado = wtype == "TORNADO"
        return _build_watch_embed(
            watch_num,
            is_tornado=is_tornado,
            watch_label="Tornado Watch" if is_tornado else "Severe Thunderstorm Watch",
            color=discord.Color.red() if is_tornado else discord.Color.orange(),
            timestamp=datetime.now(timezone.utc),
            expires=expires,
            text_summary=text_summary,
            probs=probs,
            cache_path=cache_path,
            paginator_index=(self.index, len(self.watch_data)),
            is_pds=is_pds,
        )

    def build_files(self):
        watch_num, _, _, _, _, cache_path, _ = self.watch_data[self.index]
        return _watch_files(watch_num, cache_path)

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = max(0, self.index - 1)
        self._update_buttons()
        embed = self.build_embed()
        files = self.build_files()
        await interaction.response.edit_message(embed=embed, attachments=files, view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = min(len(self.watch_data) - 1, self.index + 1)
        self._update_buttons()
        embed = self.build_embed()
        files = self.build_files()
        await interaction.response.edit_message(embed=embed, attachments=files, view=self)

    @discord.ui.button(label="🗺️ Overview", style=discord.ButtonStyle.primary)
    async def overview_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.overview_path:
            await interaction.response.send_message(
                "**Current Active Watches Overview**",
                file=discord.File(self.overview_path, filename="current_watches.png"),
            )
        else:
            await interaction.response.send_message("Overview map unavailable.")

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException as e:
                logger.debug(f"Could not disable view on timeout: {e}")


async def _execute_watches(interaction: discord.Interaction, bot: commands.Bot):
    """Shared implementation for /watches and /ww slash commands."""
    await interaction.response.defer()
    nws_watches = await fetch_active_watches_nws()
    if nws_watches is None or not nws_watches:
        entries = await fetch_latest_watch_numbers()
        nws_watches = {num: {"type": wtype, "expires": None} for num, wtype in entries}
    if not nws_watches:
        await interaction.followup.send("No active watches found.")
        return

    overview_content, overview_status = await http_get_bytes(SPC_VALID_WATCHES_URL)
    overview_path = None
    if overview_content and overview_status == 200 and not is_placeholder_image(overview_content):
        overview_path = get_cache_path_for_url(SPC_VALID_WATCHES_URL)
        try:
            with open(overview_path, "wb") as ovf:
                ovf.write(overview_content)
        except Exception as e:
            logger.warning(f"[/watches] Could not save overview image: {e}")
            overview_path = None

    async def _hydrate(watch_num: str, nws_info: dict):
        image_url, text_summary, probs, is_pds = await fetch_watch_details(watch_num)
        cache_path = None
        if image_url:
            cache_path, _, _ = await download_single_image(
                image_url, MANUAL_CACHE_FILE, bot.state.manual_cache
            )
        return (watch_num, nws_info, image_url, text_summary, probs, cache_path, is_pds)

    watch_data = list(
        await asyncio.gather(*[_hydrate(num, info) for num, info in nws_watches.items()])
    )

    view = WatchPaginatorView(watch_data, overview_path)
    if len(watch_data) == 1:
        view.prev_btn.disabled = True
        view.next_btn.disabled = True
    embed = view.build_embed()
    files = view.build_files()
    msg = await interaction.followup.send(embed=embed, files=files, view=view, wait=True)
    view.message = msg


class WatchesCog(commands.Cog):
    MANAGED_TASK_NAMES = [("auto_post_watches", "auto_post_watches")]

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._watches_backoff = TaskBackoff("auto_post_watches")
        self._pending_tasks: set[asyncio.Task] = set()
        self._watch_inflight: set = set()  # watches mid-post — guards the check→send→mark race

    async def cog_load(self):
        self.auto_post_watches.start()

    def cog_unload(self):
        self.auto_post_watches.cancel()
        for t in list(self._pending_tasks):
            t.cancel()
        self._pending_tasks.clear()

    async def _upgrade_watch_embed(
        self,
        watch_num: str,
        message: discord.Message,
        is_tornado: bool,
        watch_label: str,
        color: discord.Color,
        expires,
    ):
        """
        Polls for full SPC watch details (probs + image) and edits the original
        message once available.

        Stage 1: Fast poll (30s) for up to 10 minutes.
        Stage 2: Slow poll (60s) for up to 20 minutes more (image only).
        """
        # Instrumentation: track when probabilities become available
        issuance_time = datetime.now(timezone.utc)
        probs_arrival_time = None

        # ── Stage 1: Fast poll for Probs + Image ───────────────────────────
        for attempt in range(20):
            # First check is immediate (no sleep), then poll at intervals
            if attempt > 0:
                await asyncio.sleep(_WATCH_FAST_POLL_INTERVAL_SEC)
            try:
                image_url, text_summary, probs, is_pds = await fetch_watch_details(watch_num)
                has_real_probs = probs and "preliminary" not in probs

                # Instrumentation: log when probs first become available
                if has_real_probs and not probs_arrival_time:
                    probs_arrival_time = datetime.now(timezone.utc)
                    elapsed = (probs_arrival_time - issuance_time).total_seconds()
                    logger.info(
                        f"[WATCH-TIMING] #{watch_num} probs available after {elapsed:.1f}s (attempt {attempt + 1})"
                    )

                cache_path = None
                if image_url:
                    cache_path, _, _ = await download_single_image(
                        image_url, AUTO_CACHE_FILE, self.bot.state.auto_cache
                    )

                image_missing = True
                if cache_path and os.path.exists(cache_path):
                    image_missing = await asyncio.to_thread(_read_is_placeholder, cache_path)

                # If we have BOTH, we are fully upgraded.
                if not image_missing and has_real_probs:
                    embed = _build_watch_embed(
                        watch_num,
                        is_tornado=is_tornado,
                        watch_label=watch_label,
                        color=color,
                        timestamp=datetime.now(timezone.utc),
                        expires=expires,
                        text_summary=text_summary,
                        probs=probs,
                        cache_path=cache_path,
                        is_pds=is_pds,
                    )
                    files = _watch_files(watch_num, cache_path)
                    await message.edit(embed=embed, attachments=files)
                    logger.info(f"Full upgrade complete for #{watch_num}")
                    return

                # If we only have Probs, or we're on the last attempt, do a
                # partial edit and transition to Stage 2 if needed.
                if has_real_probs or attempt == 19:
                    embed = _build_watch_embed(
                        watch_num,
                        is_tornado=is_tornado,
                        watch_label=watch_label,
                        color=color,
                        timestamp=datetime.now(timezone.utc),
                        expires=expires,
                        text_summary=text_summary,
                        probs=probs,
                        cache_path=cache_path if not image_missing else None,
                        is_pds=is_pds,
                    )
                    files = _watch_files(watch_num, cache_path) if not image_missing else []
                    await message.edit(embed=embed, attachments=files)

                    if not image_missing:
                        # We have image but only preliminary probs?
                        # Continue fast-polling until probs are real.
                        continue

                    if has_real_probs:
                        # We have real probs but still no image.
                        # Break to Stage 2 for dedicated image slow-poll.
                        logger.info(
                            f"Probs updated for #{watch_num}; transitioning to slow-poll for image"
                        )
                        break

            except Exception as e:
                logger.warning(f"Upgrade attempt {attempt + 1} failed for #{watch_num}: {e}")

        # ── Stage 2: Slow poll specifically for the Image ──────────────────
        # Sometimes SPC takes 15-20 minutes to generate the GIF during high load.
        for _attempt in range(20):
            await asyncio.sleep(_WATCH_SLOW_POLL_INTERVAL_SEC)
            try:
                # We only care about the image now
                image_url, text_summary, probs, is_pds = await fetch_watch_details(watch_num)
                if not image_url:
                    continue

                cache_path, _, _ = await download_single_image(
                    image_url, AUTO_CACHE_FILE, self.bot.state.auto_cache
                )

                if cache_path and os.path.exists(cache_path):
                    if await asyncio.to_thread(_read_is_placeholder, cache_path):
                        continue
                else:
                    continue

                # Got it! Do the final edit.
                embed = _build_watch_embed(
                    watch_num,
                    is_tornado=is_tornado,
                    watch_label=watch_label,
                    color=color,
                    timestamp=datetime.now(timezone.utc),
                    expires=expires,
                    text_summary=text_summary,
                    probs=probs,
                    cache_path=cache_path,
                    is_pds=is_pds,
                )
                files = _watch_files(watch_num, cache_path)
                await message.edit(embed=embed, attachments=files)
                logger.info(f"Image finally backfilled for #{watch_num} after slow-poll")
                return

            except Exception as e:
                logger.debug(f"Slow-poll image check failed for #{watch_num}: {e}")

        logger.info(f"Gave up on image backfill for #{watch_num} after 30 minutes")

    async def post_watch_now(self, watch_num: str, nws_info: dict):
        """
        Immediately post a specific watch if it hasn't been posted yet.
        Called by NWWS-OI and IEMBotCog when a new watch is seen on the feed.

        Both feeds can fire for the same watch. posted_watches is only marked
        after a successful send, so the check→mark gap spans network awaits.
        Reserve the slot synchronously to prevent a concurrent double-post — the
        same race confirmed on MDs. Watches are sparse so it has not been seen
        in the wild, but a duplicate during an outbreak would be costly. The
        reservation is released in finally so a failed send still retries.
        """
        watch_num = watch_num.zfill(4)
        if watch_num in self.bot.state.posted_watches or watch_num in self._watch_inflight:
            return
        self._watch_inflight.add(watch_num)
        try:
            await self._post_watch_now_inner(watch_num, nws_info)
        finally:
            self._watch_inflight.discard(watch_num)

    async def _post_watch_now_inner(self, watch_num: str, nws_info: dict):
        channel = self.bot.get_channel(int(SPC_CHANNEL_ID)) if SPC_CHANNEL_ID else None
        if not channel:
            return

        wtype = nws_info.get("type", "SVR") if isinstance(nws_info, dict) else "SVR"
        expires = nws_info.get("expires") if isinstance(nws_info, dict) else None
        is_tornado = wtype == "TORNADO"
        watch_label = "Tornado Watch" if is_tornado else "Severe Thunderstorm Watch"
        color = discord.Color.red() if is_tornado else discord.Color.orange()
        now_utc = datetime.now(timezone.utc)

        # Instrumentation: log timing of probability availability from each source
        issuance_time = now_utc
        logger.info(f"[WATCH-TIMING] #{watch_num} detected at {issuance_time.isoformat()}")

        # Run diagnostic to test all sources
        from cogs.watch_fetch import log_watch_source_timing

        t = asyncio.create_task(log_watch_source_timing(watch_num))
        t.add_done_callback(_log_task_exception)

        logger.info(f"iembot-triggered post for #{watch_num} ({wtype})")
        image_url, text_summary, probs, is_pds = await fetch_watch_details(watch_num)
        cache_path = None
        if image_url:
            cache_path, _, _ = await download_single_image(
                image_url, AUTO_CACHE_FILE, self.bot.state.auto_cache
            )

        embed = _build_watch_embed(
            watch_num,
            is_tornado=is_tornado,
            watch_label=watch_label,
            color=color,
            timestamp=now_utc,
            expires=expires,
            text_summary=text_summary,
            probs=probs,
            cache_path=cache_path,
            is_pds=is_pds,
        )

        files = _watch_files(watch_num, cache_path)
        message = await safe_send(
            channel, context=f"watch #{watch_num} ({wtype})", embed=embed, files=files
        )
        if not message:
            return
        # Do NOT add to active_watches here — let the NWS API poll
        # populate it with real expiry/zone data on the next cycle.
        # Adding partial nws_info now causes false cancellations when
        # the NWS API hasn't indexed the watch yet.
        await self.bot.state.add_posted_watch(str(watch_num))
        self.bot.state.last_post_times["watch"] = now_utc
        logger.info(f"iembot-triggered: posted watch #{watch_num}")
        sounding_cog = self.bot.cogs.get("SoundingCog")
        if sounding_cog and isinstance(nws_info, dict) and nws_info.get("affected_zones"):
            t = asyncio.create_task(
                sounding_cog.post_soundings_for_watch(watch_num, nws_info, channel)
            )
            t.add_done_callback(_log_task_exception)
        # Schedule upgrade edit once SPC data is available
        has_prelim = probs and "preliminary" in probs
        if not cache_path or has_prelim:
            t = asyncio.create_task(
                self._upgrade_watch_embed(
                    watch_num, message, is_tornado, watch_label, color, expires
                )
            )
            self._pending_tasks.add(t)
            t.add_done_callback(self._pending_tasks.discard)

    @tasks.loop(minutes=2)
    async def auto_post_watches(self):
        try:
            await self.bot.wait_until_ready()

            if not self.bot.state.is_primary:
                return

            channel = self.bot.get_channel(SPC_CHANNEL_ID)
            if not channel:
                logger.warning("SPC channel not found for auto_post_watches")
                return

            nws_watches = await fetch_active_watches_nws()
            if nws_watches is None:
                logger.warning("NWS API fetch failed — skipping cycle, active set unchanged")
                return
            now_utc = datetime.now(timezone.utc)

            # ── Cancellations ──────────────────────────────────────────────
            spc_active_watches = None
            for watch_num, info in list(self.bot.state.active_watches.items()):
                wtype = info["type"] if isinstance(info, dict) else info
                expires = info.get("expires") if isinstance(info, dict) else None

                expired_by_time = expires is not None and now_utc >= expires
                missing_from_api = watch_num not in nws_watches

                if not (expired_by_time or (missing_from_api and nws_watches)):
                    continue

                # Double check SPC website to prevent NWS API glitch cancellations
                if missing_from_api and not expired_by_time:
                    if spc_active_watches is None:
                        from cogs.watch_fetch import get_spc_active_watch_numbers

                        spc_active_watches = await get_spc_active_watch_numbers()

                    if spc_active_watches is not None and watch_num in spc_active_watches:
                        logger.warning(
                            f"NWS API dropped watch #{watch_num} but it is still active on SPC. Ignoring NWS glitch."
                        )
                        continue

                self.bot.state.active_watches.pop(watch_num, None)
                reason = "expired" if expired_by_time else "no longer active"
                logger.info(f"Watch #{watch_num} {reason} — posting cancellation")
                watch_label = "Tornado Watch" if wtype == "TORNADO" else "Severe Thunderstorm Watch"
                embed = discord.Embed(
                    title=(f"✅  {watch_label} #{int(watch_num)} — Expired / Cancelled"),
                    color=discord.Color.green(),
                    timestamp=now_utc,
                )
                embed.set_footer(text="SPC Watch Monitor")
                msg = await safe_send(
                    channel,
                    context=f"watch cancellation #{watch_num}",
                    embed=embed,
                )
                if msg is None:
                    logger.warning(f"Failed to send cancellation for #{watch_num}")
                    self.bot.state.active_watches[watch_num] = info
                else:
                    logger.info(f"Posted cancellation for #{watch_num}")

            # ── New watches ────────────────────────────────────────────────
            for watch_num, nws_info in nws_watches.items():
                self.bot.state.active_watches[watch_num] = nws_info
                if watch_num in self.bot.state.posted_watches or watch_num in self._watch_inflight:
                    sounding_cog = self.bot.cogs.get("SoundingCog")
                    if (
                        sounding_cog
                        and isinstance(nws_info, dict)
                        and nws_info.get("affected_zones")
                        and watch_num not in self.bot.state.sounding_handled_watches
                    ):
                        t = asyncio.create_task(
                            sounding_cog.post_soundings_for_watch(watch_num, nws_info, channel)
                        )
                        t.add_done_callback(_log_task_exception)
                    continue

                self._watch_inflight.add(watch_num)
                try:
                    wtype = nws_info.get("type", "SVR")
                    expires = nws_info.get("expires")
                    is_tornado = wtype == "TORNADO"
                    watch_label = "Tornado Watch" if is_tornado else "Severe Thunderstorm Watch"
                    color = discord.Color.red() if is_tornado else discord.Color.orange()

                    logger.info(f"New watch detected: #{watch_num} ({wtype})")
                    image_url, text_summary, probs, is_pds = await fetch_watch_details(watch_num)
                    cache_path = None
                    if image_url:
                        cache_path, _, _ = await download_single_image(
                            image_url, AUTO_CACHE_FILE, self.bot.state.auto_cache
                        )

                    embed = _build_watch_embed(
                        watch_num,
                        is_tornado=is_tornado,
                        watch_label=watch_label,
                        color=color,
                        timestamp=now_utc,
                        expires=expires,
                        text_summary=text_summary,
                        probs=probs,
                        cache_path=cache_path,
                        is_pds=is_pds,
                    )

                    files = _watch_files(watch_num, cache_path)
                    message = await safe_send(
                        channel, context=f"watch #{watch_num} ({wtype})", embed=embed, files=files
                    )
                    if message:
                        await self.bot.state.add_posted_watch(str(watch_num))
                        self.bot.state.last_post_times["watch"] = datetime.now(timezone.utc)
                        logger.info(f"Posted watch #{watch_num}")
                        sounding_cog = self.bot.cogs.get("SoundingCog")
                        if sounding_cog:
                            t = asyncio.create_task(
                                sounding_cog.post_soundings_for_watch(watch_num, nws_info, channel)
                            )
                            t.add_done_callback(_log_task_exception)
                        # Schedule upgrade edit once SPC data is available
                        has_prelim = probs and "preliminary" in probs
                        if not cache_path or has_prelim:
                            t = asyncio.create_task(
                                self._upgrade_watch_embed(
                                    watch_num, message, is_tornado, watch_label, color, expires
                                )
                            )
                            self._pending_tasks.add(t)
                            t.add_done_callback(self._pending_tasks.discard)
                finally:
                    self._watch_inflight.discard(watch_num)

            self._watches_backoff.success()

        except Exception as e:
            logger.exception(
                f"Unexpected error in auto_post_watches: {e}",
            )
            await self._watches_backoff.failure(self.bot)

    @auto_post_watches.after_loop
    async def after_watches_loop(self):
        if self.auto_post_watches.is_being_cancelled():
            return
        task = self.auto_post_watches.get_task()
        try:
            exc = task.exception() if task else None
        except Exception:
            exc = None
        if exc:
            logger.error(
                f"[TASK] auto_post_watches stopped due to exception: {type(exc).__name__}: {exc}",
                exc_info=exc,
            )

    @discord.app_commands.command(
        name="watches",
        description="Show all currently active SPC watches",
    )
    async def watches_slash(self, interaction: discord.Interaction):
        await _execute_watches(interaction, self.bot)

    @discord.app_commands.command(
        name="ww",
        description="Show all currently active SPC watches",
    )
    async def ww_slash(self, interaction: discord.Interaction):
        await _execute_watches(interaction, self.bot)


async def setup(bot: commands.Bot):
    await bot.add_cog(WatchesCog(bot))
