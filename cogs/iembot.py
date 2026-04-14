# cogs/iembot.py
"""
IEMBot feed poller — fetches SPC watch and MD text products in real-time
from the IEM iembot SSE feed (weather.im/iembot-sse/room/spcchat).

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

from utils.db import (
    get_state, set_state, 
    get_product_cache, set_product_cache
)
from utils.http import http_get_bytes

logger = logging.getLogger("spc_bot")

IEMBOT_SSE_URL = "https://weather.im/iembot-sse/room/spcchat"
IEM_NWSTEXT_URL = "https://mesonet.agron.iastate.edu/api/1/nwstext/{product_id}"
CACHE_TTL = 600  # 10 minutes


async def get_cached_watch_text(watch_number: str) -> Optional[str]:
    """Return cached watch text if available and not expired."""
    return await get_product_cache(f"watch_{watch_number.zfill(4)}")


async def get_cached_md_text(md_number: str) -> Optional[str]:
    """Return cached MD text if available and not expired."""
    return await get_product_cache(f"md_{md_number.zfill(4)}")


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
        self._listener_task: Optional[asyncio.Task] = None
        self._running = True

    async def cog_load(self):
        self._listener_task = asyncio.create_task(self.listen_to_iembot())

    async def cog_unload(self):
        self._running = False
        if self._listener_task:
            self._listener_task.cancel()

    async def listen_to_iembot(self):
        """Persistent SSE listener for IEM iembot feed."""
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

        import json as _json
        import aiohttp

        backoff = 5
        while self._running:
            try:
                url = f"{IEMBOT_SSE_URL}?seqnum={self._last_seqnum}"
                logger.info(f"[IEMBOT] Connecting to SSE: {url}")

                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_read=300)) as session:
                    async with session.get(url) as response:
                        if response.status != 200:
                            logger.warning(f"[IEMBOT] SSE connection failed: {response.status}")
                            await asyncio.sleep(backoff)
                            backoff = min(backoff * 2, 60)
                            continue

                        backoff = 5  # Reset backoff on success
                        async for line in response.content:
                            if not self._running:
                                break

                            line_str = line.decode("utf-8").strip()
                            if not line_str.startswith("data:"):
                                continue

                            try:
                                data = _json.loads(line_str[5:].strip())
                                # IEM SSE sometimes returns messages in a list or single object
                                messages = data if isinstance(data, list) else [data]

                                for msg in messages:
                                    seqnum = msg.get("seqnum", 0)
                                    if seqnum <= self._last_seqnum:
                                        continue
                                    self._last_seqnum = max(self._last_seqnum, seqnum)

                                    product_id = msg.get("product_id", "")
                                    if not product_id:
                                        continue

                                    if "WWUS20-SEL" in product_id or "WWUS40-SEL" in product_id:
                                        asyncio.create_task(self._handle_watch(product_id))
                                    elif "ACUS11-SWOMCD" in product_id:
                                        asyncio.create_task(self._handle_md(product_id))

                                # Persist seqnum after each valid batch
                                asyncio.create_task(set_state("iembot_last_seqnum", str(self._last_seqnum)))

                            except _json.JSONDecodeError:
                                continue
                            except Exception as e:
                                logger.error(f"[IEMBOT] Error processing SSE line: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._running:
                    logger.warning(f"[IEMBOT] SSE loop error: {e}. Reconnecting in {backoff}s...")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 60)

        logger.info("[IEMBOT] SSE listener stopped")

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
            asyncio.create_task(watches_cog.post_watch_now(watch_num, nws_info))

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
            await set_product_cache(f"md_{md_num}", text, ttl=CACHE_TTL)
            logger.info(f"[IEMBOT] Cached MD text for #{md_num}")

        # Signal MesoscaleCog to post immediately
        mesoscale_cog = self.bot.cogs.get("MesoscaleCog")
        if mesoscale_cog:
            asyncio.create_task(mesoscale_cog.post_md_now(md_num))


async def setup(bot: commands.Bot):
    await bot.add_cog(IEMBotCog(bot))
