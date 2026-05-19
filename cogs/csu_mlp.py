# cogs/csu_mlp.py
import asyncio
import logging
from datetime import datetime, timedelta, timezone

import aiohttp
import discord
from discord.app_commands import Choice
from discord.ext import commands, tasks

from config import MANUAL_CACHE_FILE, MODELS_CHANNEL_ID
from utils.cache import (
    download_single_image,
)
from utils.http import ensure_session
from utils.state_store import set_state

logger = logging.getLogger("spc_bot.csu_mlp")

BASE = "https://schumacher.atmos.colostate.edu/weather/csu_mlp/archive"
VERSION = "2021"

# Days 1-3 use all-hazard slug; 4-8 use aggregate slug
def _product_slug(day: int) -> str:
    if day <= 3:
        return f"severe_ml_day{day}_all_gefso"
    return f"severe_ml_day{day}_gefso"

def _build_url(day: int, init_date: datetime, init_hour: str) -> str:
    date_str = init_date.strftime("%Y%m%d")
    valid_date = init_date + timedelta(days=day)
    valid_str = valid_date.strftime("%m%d")
    product = _product_slug(day)
    folder = f"severe_gefso_{VERSION}_day{day}"
    return f"{BASE}/{folder}/{date_str}{init_hour}/{product}_{valid_str}12.png"


def _build_panel_url(product: str, init_date: datetime) -> str:
    """Build URL for 6-panel products. Always 00z, folder is always day1."""
    date_str = init_date.strftime("%Y%m%d")
    valid_str = init_date.strftime("%m%d")
    return f"{BASE}/severe_gefso_{VERSION}_day1/{date_str}00/{product}_{valid_str}12.png"


async def _resolve_panel_url(product: str, allow_yesterday: bool = False) -> tuple[str | None, str]:
    """Resolve today's (or optionally yesterday's) 6-panel URL. 00z only.

    NOTE: allow_yesterday=True is used for manual commands to handle the
    'UTC dead zone' (between 00:00 UTC and ~17:00 UTC when today's data is
    actually posted). Auto-poll uses False to prevent re-posting old data.
    """
    now_utc = datetime.now(timezone.utc)
    today = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)

    dates = [(today, "00z")]
    if allow_yesterday:
        dates.append((today - timedelta(days=1), "yesterday 00z"))

    for init_date, label in dates:
        url = _build_panel_url(product, init_date)
        if await _url_is_image(url):
            logger.debug(f"{product}: resolved {label} -> {url}")
            return url, label

    logger.warning(f"{product}: no recent URL available")
    return None, ""


async def _url_is_image(url: str) -> bool:
    """
    Check if a URL actually serves an image by inspecting Content-Type.
    The CSU server returns 200+HTML for missing files instead of 404.
    """
    try:
        session = await ensure_session()
        async with session.head(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            ct = resp.headers.get("Content-Type", "")
            is_img = resp.status == 200 and "image" in ct
            return is_img
    except Exception as e:
        logger.debug(f"HEAD check failed for {url}: {e}")
        return False


async def _resolve_best_url(day: int, force_hour: str | None = None, allow_yesterday: bool = False) -> tuple[str | None, str]:
    """
    Try latest runs first.
    Days 1-3 have 12z and 00z. Days 4-8 only 00z.
    Returns (url, label) or (None, "").
    """
    now_utc = datetime.now(timezone.utc)
    today = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)

    candidates = []
    if day <= 3:
        if force_hour:
            candidates.append((today, force_hour, f"{force_hour}z"))
        else:
            # Latest possible is today 12z (available after ~18 UTC)
            if now_utc.hour >= 18:
                candidates.append((today, "12", "12z"))
            # Then today 00z
            candidates.append((today, "00", "00z"))
    else:
        # Days 4-8: 00z only
        candidates.append((today, "00", "00z"))

    if allow_yesterday:
        yesterday = today - timedelta(days=1)
        if day <= 3:
            candidates.append((yesterday, "12", "yesterday 12z"))
            candidates.append((yesterday, "00", "yesterday 00z"))
        else:
            candidates.append((yesterday, "00", "yesterday 00z"))

    for init_date, init_hour, label in candidates:
        url = _build_url(day, init_date, init_hour)
        if await _url_is_image(url):
            logger.debug(f"Day {day}: resolved {label} -> {url}")
            return url, label

    logger.warning(f"Day {day}: no recent URL available")
    return None, ""


# Per-day first-seen timestamps; purely diagnostic.
_availability_log: dict[int, str] = {}


class CSUMLPCog(commands.Cog):
    MANAGED_TASK_NAMES = [("csu_mlp_daily_poll", "csu_mlp_daily_poll")]

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Pre-set to today if we're already past the reset hour so a restart
        # after 15 UTC doesn't trigger a second reset on the same day.
        now_utc = datetime.now(timezone.utc)
        self._last_reset_date: str = (
            now_utc.strftime("%Y-%m-%d") if now_utc.hour >= 15 else ""
        )
        self.csu_mlp_daily_poll.start()

    def cog_unload(self):
        self.csu_mlp_daily_poll.cancel()

    # ── Shared fetch+send helper ──────────────────────────────────────────


    async def _fetch_and_send(self, source, day: int):
        url, label = await _resolve_best_url(day, allow_yesterday=True)
        if not url:
            msg = (
                f"CSU-MLP Day {day} isn't available yet. "
                f"Try again after ~11am MT."
            )
            if hasattr(source, "followup"):
                await source.followup.send(msg)
            else:
                await source.send(msg)
            return

        cache_path, _, _ = await download_single_image(
            url, MANUAL_CACHE_FILE, self.bot.state.manual_cache
        )
        if not cache_path:
            msg = f"Failed to download CSU-MLP Day {day} image."
            if hasattr(source, "followup"):
                await source.followup.send(msg)
            else:
                await source.send(msg)
            return

        day_range = "Medium Range " if day >= 4 else ""
        title = f"**CSU-MLP {day_range}Day {day} Severe Weather Forecast** (init: {label})"
        try:
            if hasattr(source, "followup"):
                await source.followup.send(
                    title, files=[discord.File(cache_path)]
                )
            else:
                await source.send(title, files=[discord.File(cache_path)])
        except discord.HTTPException as e:
            logger.exception(f"[CSU-MLP] Send failed for Day {day}: {e}")

    # ── Slash command ─────────────────────────────────────────────────────

    @discord.app_commands.command(name="csu", description="CSU-MLP severe weather ML forecast")
    @discord.app_commands.describe(product="Which CSU-MLP product to display")
    @discord.app_commands.choices(product=[
        Choice(name="Day 1", value="1"),
        Choice(name="Day 2", value="2"),
        Choice(name="Day 3", value="3"),
        Choice(name="Day 4 (Medium Range)", value="4"),
        Choice(name="Day 5 (Medium Range)", value="5"),
        Choice(name="Day 6 (Medium Range)", value="6"),
        Choice(name="Day 7 (Medium Range)", value="7"),
        Choice(name="Day 8 (Medium Range)", value="8"),
        Choice(name="6-Panel Days 1-2", value="panel12"),
        Choice(name="6-Panel Days 3-8", value="panel38"),
    ])
    async def csu(self, interaction: discord.Interaction, product: Choice[str]):
        await interaction.response.defer()
        val = product.value
        if val == "panel12":
            url, label = await _resolve_panel_url("hazards_fcst_6panel", allow_yesterday=True)
            if not url:
                await interaction.followup.send("CSU-MLP Days 1-2 6-panel isn't available yet. Try after ~11am MT.")
                return
            cache_path, _, _ = await download_single_image(url, MANUAL_CACHE_FILE, self.bot.state.manual_cache)
            if not cache_path:
                await interaction.followup.send("Failed to download CSU-MLP Days 1-2 6-panel.")
                return
            await interaction.followup.send(
                f"**CSU-MLP Days 1-2 Hazard 6-Panel** (init: {label})",
                files=[discord.File(cache_path)]
            )
        elif val == "panel38":
            url, label = await _resolve_panel_url("severe_fcst_6panel", allow_yesterday=True)
            if not url:
                await interaction.followup.send("CSU-MLP Days 3-8 6-panel isn't available yet. Try after ~11am MT.")
                return
            cache_path, _, _ = await download_single_image(url, MANUAL_CACHE_FILE, self.bot.state.manual_cache)
            if not cache_path:
                await interaction.followup.send("Failed to download CSU-MLP Days 3-8 6-panel.")
                return
            await interaction.followup.send(
                f"**CSU-MLP Days 3-8 Severe 6-Panel** (init: {label})",
                files=[discord.File(cache_path)]
            )
        else:
            await self._fetch_and_send(interaction, int(val))

    # ── Auto-post polling loop ────────────────────────────────────────────

    @tasks.loop(minutes=10)
    async def csu_mlp_daily_poll(self):
        await self.bot.wait_until_ready()
        if not self.bot.state.is_primary:
            return

        now_utc = datetime.now(timezone.utc)
        today_str = now_utc.strftime("%Y-%m-%d")

        # Reset once per day at 15 UTC, tracked by date so the 10-minute
        # loop interval can't cause the window to be missed.
        if now_utc.hour >= 15 and self._last_reset_date != today_str:
            if self.bot.state.csu_posted:
                logger.info("Resetting daily posted state")
                self.bot.state.csu_posted.clear()
                _availability_log.clear()
                # Use BotState method or manual persistence for reset
                await set_state("csu_mlp_posted", "") 
            self._last_reset_date = today_str

        # Only poll 16-23 UTC
        if not (15 <= now_utc.hour < 22):
            return

        channel = self.bot.get_channel(MODELS_CHANNEL_ID)
        if not channel:
            logger.warning("SCP channel not found")
            return

        # Use the first available missing day to determine the best init hour
        # for the current batch (either 12z or 00z).
        #
        # NOTE: We do NOT use allow_yesterday=True here. The auto-poll only
        # cares about fresh data for the current operational day. Manual
        # commands are more lenient to handle requests after midnight UTC.
        current_init_hour = None

        for day in range(1, 9):
            if str(day) in self.bot.state.csu_posted:
                continue

            # Resolve URL using the batch's init hour if already determined
            url, label = await _resolve_best_url(day, force_hour=current_init_hour)
            if not url:
                continue

            # Lock in the init hour for this poll cycle based on the first success
            if current_init_hour is None and "z" in label:
                current_init_hour = label.replace("z", "")

            # Timing research log — first time each day's product is seen
            if day not in _availability_log:
                first_seen = now_utc.strftime("%Y-%m-%d %H:%MZ")
                _availability_log[day] = first_seen
                logger.info(
                    f"\U0001f4ca TIMING LOG — "
                    f"Day {day} first available at {first_seen} ({label})"
                )

            cache_path, _, _ = await download_single_image(
                url, MANUAL_CACHE_FILE, self.bot.state.manual_cache
            )
            if not cache_path:
                logger.warning(f"Download failed for Day {day}")
                continue

            try:
                day_range = "Medium Range " if day >= 4 else ""
                # Simple retry logic for transient network/Discord issues
                for attempt in range(3):
                    try:
                        await channel.send(
                            f"**CSU-MLP {day_range}Day {day} Severe Weather Forecast**"
                            f" (init: {label})",
                            files=[discord.File(cache_path)],
                        )
                        break # Success
                    except (discord.DiscordServerError, discord.HTTPException) as e:
                        if attempt < 2:
                            logger.warning(f"Retrying Day {day} (attempt {attempt+2}) after error: {e}")
                            await asyncio.sleep(2 * (attempt + 1))
                        else:
                            raise

                await self.bot.state.add_csu_posted(str(day))
                self.bot.state.last_post_times[f"csu_day{day}"] = now_utc
                logger.info(f"Auto-posted Day {day} ({label})")

                # Small pause to avoid hammering Discord API/local networking stack
                await asyncio.sleep(1.0)
            except Exception as e:
                logger.exception(
                    f"Failed to post Day {day}: {e}"
                )

        # Auto-post 6-panel products
        for product, label_name, state_key in [
            ("hazards_fcst_6panel", "Days 1-2 Hazard 6-Panel", "panel12"),
            ("severe_fcst_6panel", "Days 3-8 Severe 6-Panel", "panel38"),
        ]:
            if state_key in self.bot.state.csu_posted:
                continue
            url, label = await _resolve_panel_url(product)
            if not url:
                continue
            if state_key not in _availability_log:
                first_seen = now_utc.strftime("%Y-%m-%d %H:%MZ")
                _availability_log[state_key] = first_seen
                logger.info(f"📊 TIMING LOG — {label_name} first available at {first_seen} ({label})")
            cache_path, _, _ = await download_single_image(url, MANUAL_CACHE_FILE, self.bot.state.manual_cache)
            if not cache_path:
                logger.warning(f"Download failed for {label_name}")
                continue
            try:
                # Simple retry logic for transient network/Discord issues
                for attempt in range(3):
                    try:
                        await channel.send(
                            f"**CSU-MLP {label_name}** (init: {label})",
                            files=[discord.File(cache_path)]
                        )
                        break # Success
                    except (discord.DiscordServerError, discord.HTTPException) as e:
                        if attempt < 2:
                            logger.warning(f"Retrying {label_name} (attempt {attempt+2}) after error: {e}")
                            await asyncio.sleep(2 * (attempt + 1))
                        else:
                            raise

                await self.bot.state.add_csu_posted(state_key)
                self.bot.state.last_post_times[f"csu_{state_key}"] = now_utc
                logger.info(f"Auto-posted {label_name} ({label})")

                # Small pause to avoid hammering Discord API/local networking stack
                await asyncio.sleep(1.0)
            except Exception as e:
                logger.exception(f"Failed to post {label_name}: {e}")

    @csu_mlp_daily_poll.after_loop
    async def after_csu_mlp_poll(self):
        if self.csu_mlp_daily_poll.is_being_cancelled():
            return
        task = self.csu_mlp_daily_poll.get_task()
        try:
            exc = task.exception() if task else None
        except Exception:
            exc = None
        if exc:
            logger.error(
                f"[TASK] csu_mlp_daily_poll stopped: "
                f"{type(exc).__name__}: {exc}",
                exc_info=exc,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(CSUMLPCog(bot))
