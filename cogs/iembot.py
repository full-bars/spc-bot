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
import logging
import re
import time
from typing import Optional, Dict, Tuple

from discord.ext import commands, tasks

from utils.db import get_state, set_state
from utils.http import http_get_bytes

logger = logging.getLogger("spc_bot")

IEMBOT_FEED_URL = "https://weather.im/iembot-json/room/spcchat"
IEM_NWSTEXT_URL = "https://mesonet.agron.iastate.edu/api/1/nwstext/{product_id}"
CACHE_TTL = 600  # 10 minutes

# Module-level caches: padded_num -> (text, monotonic_timestamp)
_watch_text_cache: Dict[str, Tuple[str, float]] = {}
_md_text_cache: Dict[str, Tuple[str, float]] = {}


def get_cached_watch_text(watch_number: str) -> Optional[str]:
    """Return cached watch text if available and not expired."""
    padded = watch_number.zfill(4)
    entry = _watch_text_cache.get(padded)
    if entry and (time.monotonic() - entry[1]) < CACHE_TTL:
        return entry[0]
    return None


def get_cached_md_text(md_number: str) -> Optional[str]:
    """Return cached MD text if available and not expired."""
    padded = md_number.zfill(4)
    entry = _md_text_cache.get(padded)
    if entry and (time.monotonic() - entry[1]) < CACHE_TTL:
        return entry[0]
    return None


def _parse_watch_text(raw: str) -> Optional[str]:
    """Parse SEL product text into a formatted summary string."""
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
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
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
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
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_seqnum: int = 0
        self._seqnum_loaded = False
        self.poll_iembot_feed.start()

    def cog_unload(self):
        self.poll_iembot_feed.cancel()

    @tasks.loop(seconds=15)
    async def poll_iembot_feed(self):
        await self.bot.wait_until_ready()

        if not self._seqnum_loaded:
            try:
                val = await get_state("iembot_last_seqnum")
                if val:
                    self._last_seqnum = int(val)
                    logger.info(f"[IEMBOT] Resuming from seqnum {self._last_seqnum}")
            except Exception as e:
                logger.warning(f"[IEMBOT] Could not load seqnum: {e}")
            self._seqnum_loaded = True

        try:
            import json as _json
            url = f"{IEMBOT_FEED_URL}?seqnum={self._last_seqnum}"
            content, status = await http_get_bytes(url, retries=2, timeout=10)
            if not content or status != 200:
                return

            data = _json.loads(content)
            messages = data.get("messages", [])
            if not messages:
                return

            new_seqnum = self._last_seqnum
            for msg in messages:
                seqnum = msg.get("seqnum", 0)
                if seqnum <= self._last_seqnum:
                    continue
                new_seqnum = max(new_seqnum, seqnum)

                product_id = msg.get("product_id", "")
                if not product_id:
                    continue

                if "WWUS20-SEL" in product_id or "WWUS40-SEL" in product_id:
                    asyncio.create_task(self._handle_watch(product_id))
                elif "ACUS11-SWOMCD" in product_id:
                    asyncio.create_task(self._handle_md(product_id))

            if new_seqnum > self._last_seqnum:
                self._last_seqnum = new_seqnum
                asyncio.create_task(set_state("iembot_last_seqnum", str(new_seqnum)))

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
            _watch_text_cache[watch_num] = (text, time.monotonic())
            logger.info(f"[IEMBOT] Cached watch text for #{watch_num}")

    async def _handle_md(self, product_id: str):
        raw = await _fetch_product_text(product_id)
        if not raw:
            logger.warning(f"[IEMBOT] Could not fetch MD text for {product_id}")
            return
        m = re.search(r"Mesoscale Discussion\s+(\d+)", raw, re.IGNORECASE)
        if not m:
            return
        md_num = m.group(1).zfill(4)
        text = _parse_md_text(raw)
        if text:
            _md_text_cache[md_num] = (text, time.monotonic())
            logger.info(f"[IEMBOT] Cached MD text for #{md_num}")

    @poll_iembot_feed.after_loop
    async def after_poll_loop(self):
        if self.poll_iembot_feed.is_being_cancelled():
            return
        task = self.poll_iembot_feed.get_task()
        exc = task.exception() if task else None
        if exc:
            logger.error(
                f"[TASK] poll_iembot_feed stopped: {type(exc).__name__}: {exc}",
                exc_info=exc,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(IEMBotCog(bot))
