# cogs/ncar.py
import json
import logging
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

from config import MANUAL_CACHE_FILE, MODELS_CHANNEL_ID, WXNEXT_BASE
from utils.cache import (
    calculate_hash_bytes,
    download_single_image,
    get_cache_path_for_url,
    is_placeholder_image,
)
from utils.state_store import delete_state, get_state, set_hash, set_state
from utils.http import ensure_session

import aiohttp

logger = logging.getLogger("spc_bot")


def _wxnext_url(date: datetime) -> str:
    return f"{WXNEXT_BASE}/predictions_grid_wxnext_mean_any_{date.strftime('%Y%m%d')}00.png"


async def _load_state() -> dict:
    """Load posted state from DB."""
    try:
        raw = await get_state("ncar_posted")
        if raw:
            return json.loads(raw)
    except Exception as e:
        logger.warning(f"[NCAR] Failed to load state: {e}")
    return {}


async def _save_state(date_str: str, image_hash: str):
    """Persist state to DB."""
    value = json.dumps({"date": date_str, "hash": image_hash})
    try:
        await set_state("ncar_posted", value)
    except Exception as e:
        logger.warning(f"[NCAR] Failed to save state: {e}")


async def _fetch_wxnext_image(url: str) -> tuple[bytes | None, str | None]:
    """Fetch image bytes and return (content, hash) or (None, None)."""
    try:
        session = await ensure_session()
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            ct = resp.headers.get("Content-Type", "")
            if resp.status == 200 and "image" in ct:
                content = await resp.read()
                return content, calculate_hash_bytes(content)
    except Exception as e:
        logger.debug(f"[NCAR] Fetch failed for {url}: {e}")
    return None, None


async def _resolve_wxnext() -> tuple[str | None, bytes | None, str | None]:
    """
    Try today then yesterday. Returns (url, content, hash) or (None, None, None).
    """
    now_utc = datetime.now(timezone.utc)
    today = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    candidates = [today, today - timedelta(days=1)]

    for date in candidates:
        url = _wxnext_url(date)
        content, h = await _fetch_wxnext_image(url)
        if content and h:
            return url, content, h

    return None, None, None


# ── State ─────────────────────────────────────────────────────────────────────
_posted_state: dict = {}  # loaded async on first poll
_timing_logged: bool = False


class NCARCog(commands.Cog):
    MANAGED_TASK_NAMES = [("wxnext_daily_poll", "wxnext_daily_poll")]

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.wxnext_daily_poll.start()

    def cog_unload(self):
        self.wxnext_daily_poll.cancel()

    # ── Slash command ─────────────────────────────────────────────────────────

    @discord.app_commands.command(
        name="wxnext", description="NCAR WxNext2 Mean AI severe weather forecast"
    )
    async def wxnext(self, interaction: discord.Interaction):
        await interaction.response.defer()
        url, content, h = await _resolve_wxnext()
        if not url or not content:
            await interaction.followup.send(
                "WxNext2 forecast isn't available yet. Try again later."
            )
            return
        cache_path, _, _ = await download_single_image(
            url, MANUAL_CACHE_FILE, self.bot.state.manual_cache
        )
        if not cache_path:
            await interaction.followup.send(
                "Failed to download WxNext2 forecast image."
            )
            return
        await interaction.followup.send(
            "**NCAR WxNext2 Mean — AI Convective Hazard Forecast (Days 1-8)**",
            files=[discord.File(cache_path)],
        )
        logger.info(f"[NCAR] /wxnext posted by {interaction.user}")

    # ── Auto-post polling loop ────────────────────────────────────────────────

    @tasks.loop(minutes=10)
    async def wxnext_daily_poll(self):
        await self.bot.wait_until_ready()
        if not self.bot.state.is_primary:
            return
        global _posted_state, _timing_logged
        if not _posted_state:
            _posted_state = await _load_state()

        now_utc = datetime.now(timezone.utc)
        today_str = now_utc.strftime("%Y-%m-%d")

        # Reset state at 03 UTC daily (before products start appearing).
        # Fires when stored state is from a prior UTC day.
        if now_utc.hour == 3 and now_utc.minute < 10:
            stored_date = _posted_state.get("date")
            if stored_date and stored_date != today_str:
                logger.info("[NCAR] Resetting daily posted state")
                _posted_state = {}
                _timing_logged = False
                await delete_state("ncar_posted")

        # Only poll 06-18 UTC
        if not (5 <= now_utc.hour < 12):
            return

        # Already posted today's image
        if _posted_state.get("date") == today_str:
            return

        url, content, h = await _resolve_wxnext()
        if not url or not content or not h:
            return

        # Timing log
        if not _timing_logged:
            logger.info(
                f"[NCAR] 📊 TIMING LOG — WxNext2 first available at "
                f"{now_utc.strftime('%Y-%m-%d %H:%MZ')}"
            )
            _timing_logged = True

        # Check if this is actually a new image vs what we last posted
        if _posted_state.get("hash") == h:
            logger.debug("[NCAR] Image hash unchanged, skipping")
            _posted_state["date"] = today_str
            await _save_state(today_str, h)
            return

        channel = self.bot.get_channel(MODELS_CHANNEL_ID)
        if not channel:
            logger.warning("[NCAR] SCP channel not found")
            return

        # We already fetched `content` in _resolve_wxnext for hashing;
        # save it directly instead of refetching via download_single_image.
        if is_placeholder_image(content):
            logger.warning("[NCAR] WxNext2 image looks like a placeholder, skipping")
            return
        cache_path = get_cache_path_for_url(url)
        try:
            with open(cache_path, "wb") as f:
                f.write(content)
            self.bot.state.manual_cache[url] = h
            await set_hash(url, h, "manual")
        except Exception as e:
            logger.exception(f"[NCAR] Failed to save WxNext2 image: {e}")
            return

        try:
            await channel.send(
                "**NCAR WxNext2 Mean — AI Convective Hazard Forecast (Days 1-8)**",
                files=[discord.File(cache_path)],
            )
            _posted_state = {"date": today_str, "hash": h}
            await _save_state(today_str, h)
            self.bot.state.last_post_times["wxnext"] = now_utc
            logger.info("[NCAR] Auto-posted WxNext2")
        except Exception as e:
            logger.exception(f"[NCAR] Failed to post WxNext2: {e}")

    @wxnext_daily_poll.after_loop
    async def after_wxnext_poll(self):
        if self.wxnext_daily_poll.is_being_cancelled():
            return
        task = self.wxnext_daily_poll.get_task()
        try:
            exc = task.exception() if task else None
        except Exception:
            exc = None

        if exc:
            logger.error(
                f"[TASK] wxnext_daily_poll stopped: {type(exc).__name__}: {exc}",
                exc_info=exc,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(NCARCog(bot))
