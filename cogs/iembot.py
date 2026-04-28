# cogs/iembot.py
"""
IEMBot feed poller — fetches SPC watch and MD text products in real-time
from the IEM iembot JSON feed (weather.im/iembot-json/room/spcchat).

Provides a fast-path data source delivering watch/MD text within seconds
of issuance, before SPC website or NWS API have caught up.

Cache is ephemeral (10-min TTL), not persisted — only bridges the gap
between issuance and SPC/IEM REST API availability.
Only the last-seen seqnum is persisted to DB to avoid reprocessing on restart.
"""
import asyncio
import json as _json
import logging
import re
from typing import Optional

from discord.ext import commands, tasks

from config import IEMBOT_BOTSTALK_URL, IEMBOT_FEED_URL, IEM_NWSTEXT_URL
from utils.state_store import (
    get_state, set_state, 
    get_product_cache, set_product_cache
)
from utils.http import http_get_bytes

logger = logging.getLogger("spc_bot")

CACHE_TTL = 600  # 10 minutes


def _log_task_exception(task: "asyncio.Task") -> None:
    if not task.cancelled() and (exc := task.exception()):
        logger.exception("[IEMBOT] Unhandled exception in background task", exc_info=exc)


async def get_cached_watch_text(watch_number: str) -> Optional[str]:
    """Return cached watch text if available and not expired."""
    return await get_product_cache(f"watch_{watch_number.zfill(4)}")


async def get_cached_md_text(md_number: str) -> Optional[str]:
    """Return cached MD text if available and not expired."""
    return await get_product_cache(f"md_{md_number.zfill(4)}")


def _parse_watch_text(raw: str) -> Optional[str]:
    """Parse SEL product text into a formatted summary string."""
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    parts = []
    for i, line in enumerate(lines):
        if re.search(r"Watch for portions of", line, re.IGNORECASE):
            area_lines = []
            for ll in lines[i+1:i+6]:
                if re.search(r"Effective|until|Primary", ll, re.IGNORECASE):
                    break
                area_lines.append(ll)
            if area_lines:
                parts.append("**Areas:** " + ", ".join(area_lines))
        if re.search(r"Effective this", line, re.IGNORECASE):
            combined = " ".join(lines[i:i+3])
            parts.append("**Time:** " + re.sub(r"\s+", " ", combined).strip())
        if re.search(r"Primary threats", line, re.IGNORECASE):
            threats = []
            for ll in lines[i+1:i+6]:
                if re.search(r"SUMMARY|PRECAUTIONARY|ATTN", ll, re.IGNORECASE):
                    break
                threats.append(ll)
            if threats:
                parts.append("**Threats:**\n" + "\n".join(f"• {t}" for t in threats))
        if re.search(r"^SUMMARY\.\.\.", line, re.IGNORECASE):
            summary_lines = []
            for ll in lines[i:i+4]:
                if re.search(r"^DISCUSSION\.\.\.", ll, re.IGNORECASE):
                    break
                summary_lines.append(ll)
            if summary_lines:
                parts.append("**Summary:** " + " ".join(summary_lines)[:300])
    return "\n".join(parts) if parts else None


def _parse_md_text(raw: str) -> Optional[str]:
    """Parse SWOMCD product text into a formatted summary string."""
    concerning = re.search(r"(CONCERNING[^\n]{10,120})", raw, re.IGNORECASE)
    if concerning:
        return concerning.group(1).strip()
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    return " ".join(lines[:3])[:200] if lines else None


async def _fetch_product_text(product_id: str) -> Optional[str]:
    """Fetch raw NWS product text from IEM archive."""
    url = IEM_NWSTEXT_URL.format(product_id=product_id)
    content, status = await http_get_bytes(url, retries=2, timeout=10)
    if not content or status != 200:
        return None
    text = content.decode("utf-8", errors="ignore")
    if "not found" in text.lower() and len(text) < 100:
        return None
    return text


class IEMBotCog(commands.Cog):
    MANAGED_TASK_NAMES = [
        ("poll_iembot_feed", "poll_iembot_feed"),
        ("poll_botstalk_feed", "poll_botstalk_feed"),
    ]

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._seqnum_loaded = False
        self._botstalk_seqnum_loaded = False

    async def cog_load(self):
        self.poll_iembot_feed.start()
        self.poll_botstalk_feed.start()

    def cog_unload(self):
        self.poll_iembot_feed.cancel()
        self.poll_botstalk_feed.cancel()

    @tasks.loop(seconds=15)
    async def poll_iembot_feed(self):
        await self.bot.wait_until_ready()

        if not self.bot.state.is_primary:
            return

        if not self._seqnum_loaded:
            try:
                # state_store already handles Upstash-first, SQLite-fallback
                # via its own read path — no need for a separate Upstash
                # round-trip here.
                val = await get_state("iembot_last_seqnum")
                if val:
                    self.bot.state.iembot_last_seqnum = int(val)
                    logger.info(
                        f"[IEMBOT] Resuming from seqnum "
                        f"{self.bot.state.iembot_last_seqnum}"
                    )
            except Exception as e:
                logger.warning(f"[IEMBOT] Could not load seqnum: {e}")
            self._seqnum_loaded = True

        try:
            url = f"{IEMBOT_FEED_URL}?seqnum={self.bot.state.iembot_last_seqnum}"
            content, status = await http_get_bytes(url, retries=2, timeout=10)
            if not content or status != 200:
                return

            data = _json.loads(content)
            messages = data.get("messages", [])
            if not messages:
                return

            new_seqnum = self.bot.state.iembot_last_seqnum
            for msg in messages:
                seqnum = msg.get("seqnum", 0)
                if seqnum <= self.bot.state.iembot_last_seqnum:
                    continue
                new_seqnum = max(new_seqnum, seqnum)

                product_id = msg.get("product_id", "")
                if not product_id:
                    continue

                if "WWUS20-SEL" in product_id or "WWUS40-SEL" in product_id:
                    t = asyncio.create_task(self._handle_watch(product_id))
                    t.add_done_callback(_log_task_exception)
                elif "ACUS11-SWOMCD" in product_id:
                    t = asyncio.create_task(self._handle_md(product_id))
                    t.add_done_callback(_log_task_exception)

            if new_seqnum > self.bot.state.iembot_last_seqnum:
                self.bot.state.iembot_last_seqnum = new_seqnum
                # state_store.set_state double-writes to SQLite and Upstash,
                # so this single call replaces the old dual-path pattern.
                await set_state("iembot_last_seqnum", str(new_seqnum))

        except Exception as e:
            logger.warning(f"[IEMBOT] Poll error: {e}")

    async def _handle_watch(self, product_id: str):
        raw = await _fetch_product_text(product_id)
        if not raw:
            logger.warning(f"[IEMBOT] Could not fetch watch text for {product_id}")
            return
        m = re.search(r"(?:Tornado|Severe Thunderstorm)\s+Watch\s+Number\s+(\d+)", raw, re.IGNORECASE)
        if not m:
            return
        watch_num = m.group(1).zfill(4)
        text = _parse_watch_text(raw)
        if text:
            await set_product_cache(f"watch_{watch_num}", text, ttl=CACHE_TTL)
            logger.info(f"[IEMBOT] Cached watch text for #{watch_num}")

        # Determine watch type from raw text
        wtype = "TORNADO" if re.search(r"Tornado Watch", raw, re.IGNORECASE) else "SVR"
        nws_info = {"type": wtype, "expires": None, "affected_zones": []}

        # Signal WatchesCog to post immediately
        watches_cog = self.bot.cogs.get("WatchesCog")
        if watches_cog:
            t = asyncio.create_task(watches_cog.post_watch_now(watch_num, nws_info))
            t.add_done_callback(_log_task_exception)

    async def _handle_md(self, product_id: str):
        raw = await _fetch_product_text(product_id)
        if not raw:
            logger.warning(f"[IEMBOT] Could not fetch MD text for {product_id}")
            return
        m = re.search(r"Mesoscale Discussion\s+(\d+)", raw, re.IGNORECASE)
        if not m:
            return
        md_num = m.group(1).zfill(4)
        # Cache the FULL raw text so mesoscale.py can extract the body properly
        await set_product_cache(f"md_{md_num}", raw, ttl=CACHE_TTL)
        logger.info(f"[IEMBOT] Cached full MD text for #{md_num}")

        # Signal MesoscaleCog to post immediately
        mesoscale_cog = self.bot.cogs.get("MesoscaleCog")
        if mesoscale_cog:
            t = asyncio.create_task(mesoscale_cog.post_md_now(md_num))
            t.add_done_callback(_log_task_exception)

    @poll_iembot_feed.after_loop
    async def after_poll_loop(self):
        if self.poll_iembot_feed.is_being_cancelled():
            return
        task = self.poll_iembot_feed.get_task()
        try:
            exc = task.exception() if task else None
        except Exception:
            exc = None
        if exc:
            logger.error(
                f"[TASK] poll_iembot_feed stopped: {type(exc).__name__}: {exc}",
                exc_info=exc,
            )

    # ── botstalk (national) poller — warning fast-path ──────────────────────
    #
    # The national ``botstalk`` room aggregates every NWS text product from
    # every WFO. We use it as the fast-trigger for warnings (TOR/SVR/FFW) so
    # they hit Discord seconds after issuance, ahead of the 30-second NWS
    # API loop in WarningsCog. Iembot's botstalk endpoint requires an
    # explicit seqnum query parameter — calling the bare URL returns the
    # literal string "ERROR".

    # Match the AFOS PIL embedded in product_id like
    # ``YYYYMMDDHHMM-OFFICE-WMO-AFOSPIL`` (e.g. ``-SVRIND``, ``-TORHGX``).
    # The WFO suffix is always 3 letters; the prefix gives us the product
    # type. Initial issuances are TOR/SVR/FFW only — SVS/FFS/SPS come in
    # later PRs.
    _ISSUANCE_PIL_RE = re.compile(r"-(TOR|SVR|FFW)([A-Z]{3})$")

    @tasks.loop(seconds=15)
    async def poll_botstalk_feed(self):
        await self.bot.wait_until_ready()

        if not self.bot.state.is_primary:
            return

        if not self._botstalk_seqnum_loaded:
            try:
                val = await get_state("iembot_botstalk_last_seqnum")
                if val:
                    self.bot.state.iembot_botstalk_last_seqnum = int(val)
                    logger.info(
                        f"[IEMBOT] Resuming botstalk from seqnum "
                        f"{self.bot.state.iembot_botstalk_last_seqnum}"
                    )
            except Exception as e:
                logger.warning(f"[IEMBOT] Could not load botstalk seqnum: {e}")
            self._botstalk_seqnum_loaded = True

        try:
            url = (
                f"{IEMBOT_BOTSTALK_URL}"
                f"?seqnum={self.bot.state.iembot_botstalk_last_seqnum}"
            )
            content, status = await http_get_bytes(url, retries=2, timeout=10)
            if not content or status != 200:
                return

            data = _json.loads(content)
            messages = data.get("messages", [])
            if not messages:
                return

            new_seqnum = self.bot.state.iembot_botstalk_last_seqnum
            for msg in messages:
                seqnum = msg.get("seqnum", 0)
                if seqnum <= self.bot.state.iembot_botstalk_last_seqnum:
                    continue
                new_seqnum = max(new_seqnum, seqnum)

                product_id = msg.get("product_id", "")
                if not product_id:
                    continue

                pil_match = self._ISSUANCE_PIL_RE.search(product_id)
                if pil_match:
                    t = asyncio.create_task(
                        self._handle_warning(product_id, pil_match.group(1))
                    )
                    t.add_done_callback(_log_task_exception)

            if new_seqnum > self.bot.state.iembot_botstalk_last_seqnum:
                self.bot.state.iembot_botstalk_last_seqnum = new_seqnum
                await set_state("iembot_botstalk_last_seqnum", str(new_seqnum))

        except Exception as e:
            logger.warning(f"[IEMBOT] Botstalk poll error: {e}")

    _PIL_TO_EVENT = {
        "TOR": "Tornado Warning",
        "SVR": "Severe Thunderstorm Warning",
        "FFW": "Flash Flood Warning",
    }

    async def _handle_warning(self, product_id: str, pil_prefix: str):
        raw = await _fetch_product_text(product_id)
        if not raw:
            logger.warning(
                f"[IEMBOT] Could not fetch warning text for {product_id}"
            )
            return

        warnings_cog = self.bot.cogs.get("WarningsCog")
        if not warnings_cog:
            return

        event = self._PIL_TO_EVENT.get(pil_prefix)
        if not event:
            return

        t = asyncio.create_task(
            warnings_cog.post_warning_now(product_id, raw, event)
        )
        t.add_done_callback(_log_task_exception)

    @poll_botstalk_feed.after_loop
    async def after_botstalk_loop(self):
        if self.poll_botstalk_feed.is_being_cancelled():
            return
        task = self.poll_botstalk_feed.get_task()
        try:
            exc = task.exception() if task else None
        except Exception:
            exc = None
        if exc:
            logger.error(
                f"[TASK] poll_botstalk_feed stopped: {type(exc).__name__}: {exc}",
                exc_info=exc,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(IEMBotCog(bot))
