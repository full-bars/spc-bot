# cogs/csu_mlp.py
import json
import logging
import os

from utils.db import get_state, set_state
from datetime import datetime, timedelta, timezone

import discord
from discord.app_commands import Choice
from discord.ext import commands, tasks

from config import MANUAL_CACHE_FILE, MODELS_CHANNEL_ID
from utils.cache import (
    download_single_image,
)

logger = logging.getLogger("spc_bot")

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


async def _resolve_panel_url(product: str) -> tuple[str | None, str]:
    """Resolve best available 6-panel URL. 00z only, tries today then yesterday."""
    now_utc = datetime.now(timezone.utc)
    today = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    candidates = [
        (today, "00z"),
        (today - timedelta(days=1), "yesterday 00z"),
    ]
    for init_date, label in candidates:
        url = _build_panel_url(product, init_date)
        if await _url_is_image(url):
            logger.debug(f"[CSU-MLP] {product}: resolved {label} -> {url}")
            return url, label
    logger.warning(f"[CSU-MLP] {product}: no URL available")
    return None, ""


async def _url_is_image(url: str) -> bool:
    """
    Check if a URL actually serves an image by inspecting Content-Type.
    The CSU server returns 200+HTML for missing files instead of 404.
    """
    from utils.http import ensure_session
    import aiohttp
    try:
        session = await ensure_session()
        async with session.head(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            ct = resp.headers.get("Content-Type", "")
            return resp.status == 200 and "image" in ct
    except Exception as e:
        logger.debug(f"[CSU-MLP] HEAD check failed for {url}: {e}")
        return False


async def _resolve_best_url(day: int) -> tuple[str | None, str]:
    """
    Try 12z init first for days 1-3 (if likely ready), always 00z for days 4-8.
    Falls back to yesterday's init as last resort.
    Returns (url, label) or (None, "").
    """
    now_utc = datetime.now(timezone.utc)
    today = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)

    candidates = []
    if day <= 3:
        # 12z init only available for days 1-3, try after 18 UTC
        if now_utc.hour >= 18:
            candidates.append((today, "12", "12z"))
        candidates.append((today, "00", "00z"))
        candidates.append((today - timedelta(days=1), "12", "yesterday 12z"))
    else:
        # Days 4-8: 00z only
        candidates.append((today, "00", "00z"))
        candidates.append((today - timedelta(days=1), "00", "yesterday 00z"))

    for init_date, init_hour, label in candidates:
        url = _build_url(day, init_date, init_hour)
        if await _url_is_image(url):
            logger.debug(f"[CSU-MLP] Day {day}: resolved {label} -> {url}")
            return url, label

    logger.warning(f"[CSU-MLP] Day {day}: no URL available")
    return None, ""



async def _load_posted_today() -> set:
    """Load posted days for today from DB. Returns empty set if stale or missing."""
    try:
        raw = await get_state("csu_mlp_posted")
        if raw:
            data = json.loads(raw)
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if data.get("date") == today:
                return set(data.get("days", []))
    except Exception as e:
        logger.warning(f"[CSU-MLP] Failed to load posted state: {e}")
    return set()

async def _save_posted_today(posted: set):
    """Persist posted days for today to DB."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    value = json.dumps({"date": today, "days": sorted(posted, key=str)})
    try:
        await set_state("csu_mlp_posted", value)
    except Exception as e:
        logger.warning(f"[CSU-MLP] Failed to save posted state: {e}")

# Auto-post state — which days posted today, persisted to disk
_posted_today: set = set()  # loaded async on first poll
_availability_log: dict[int, str] = {}  # day -> first-seen time string


class CSUMLPCog(commands.Cog):
    MANAGED_TASK_NAMES = [("csu_mlp_daily_poll", "csu_mlp_daily_poll")]

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.csu_mlp_daily_poll.start()

    def cog_unload(self):
        self.csu_mlp_daily_poll.cancel()

    # ── Shared fetch+send helper ──────────────────────────────────────────


    async def _fetch_and_send(self, source, day: int):
        url, label = await _resolve_best_url(day)
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
            logger.error(f"[CSU-MLP] Send failed for Day {day}: {e}")

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
            url, label = await _resolve_panel_url("hazards_fcst_6panel")
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
            url, label = await _resolve_panel_url("severe_fcst_6panel")
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
        
        # Hydrate from DB if memory is empty
        if not self.bot.state.csu_posted:
            db_posted = await _load_posted_today()
            if db_posted:
                self.bot.state.csu_posted.update(str(d) for d in db_posted)

        now_utc = datetime.now(timezone.utc)

        # Reset at 15 UTC daily before products start appearing
        if now_utc.hour == 15 and now_utc.minute < 10:
            if self.bot.state.csu_posted:
                logger.info("[CSU-MLP] Resetting daily posted state")
                self.bot.state.csu_posted.clear()
                _availability_log.clear()
                await _save_posted_today(self.bot.state.csu_posted)

        # Only poll 16-23 UTC
        if not (15 <= now_utc.hour < 22):
            return

        channel = self.bot.get_channel(MODELS_CHANNEL_ID)
        if not channel:
            logger.warning("[CSU-MLP] SCP channel not found")
            return

        for day in range(1, 9):
            if str(day) in self.bot.state.csu_posted:
                continue

            url, label = await _resolve_best_url(day)
            if not url:
                continue

            # Timing research log — first time each day's product is seen
            if day not in _availability_log:
                first_seen = now_utc.strftime("%Y-%m-%d %H:%MZ")
                _availability_log[day] = first_seen
                logger.info(
                    f"[CSU-MLP] \U0001f4ca TIMING LOG — "
                    f"Day {day} first available at {first_seen} ({label})"
                )

            cache_path, _, _ = await download_single_image(
                url, MANUAL_CACHE_FILE, self.bot.state.manual_cache
            )
            if not cache_path:
                logger.warning(f"[CSU-MLP] Download failed for Day {day}")
                continue

            try:
                day_range = "Medium Range " if day >= 4 else ""
                await channel.send(
                    f"**CSU-MLP {day_range}Day {day} Severe Weather Forecast**"
                    f" (init: {label})",
                    files=[discord.File(cache_path)],
                )
                self.bot.state.csu_posted.add(str(day))
                await _save_posted_today(self.bot.state.csu_posted)
                self.bot.state.last_post_times[f"csu_day{day}"] = now_utc
                logger.info(f"[CSU-MLP] Auto-posted Day {day} ({label})")
            except Exception as e:
                logger.error(
                    f"[CSU-MLP] Failed to post Day {day}: {e}", exc_info=True
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
                logger.info(f"[CSU-MLP] 📊 TIMING LOG — {label_name} first available at {first_seen} ({label})")
            cache_path, _, _ = await download_single_image(url, MANUAL_CACHE_FILE, self.bot.state.manual_cache)
            if not cache_path:
                logger.warning(f"[CSU-MLP] Download failed for {label_name}")
                continue
            try:
                await channel.send(
                    f"**CSU-MLP {label_name}** (init: {label})",
                    files=[discord.File(cache_path)]
                )
                self.bot.state.csu_posted.add(state_key)
                await _save_posted_today(self.bot.state.csu_posted)
                self.bot.state.last_post_times[f"csu_{state_key}"] = now_utc
                logger.info(f"[CSU-MLP] Auto-posted {label_name} ({label})")
            except Exception as e:
                logger.error(f"[CSU-MLP] Failed to post {label_name}: {e}", exc_info=True)

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
