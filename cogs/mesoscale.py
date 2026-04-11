# cogs/mesoscale.py
import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import discord
from discord.ext import commands, tasks
from utils.backoff import TaskBackoff

from config import AUTO_CACHE_FILE, SPC_CHANNEL_ID, SPC_MD_INDEX_URL
from utils.cache import (
    MD_CACHE_FILE,
    MAX_TRACKED_MDS,
    active_mds,
    auto_cache,
    download_single_image,
    last_post_times,
    posted_mds,
    prune_tracked_set,
)
from utils.change_detection import get_cache_path_for_url
from utils.http import http_get_text, http_head_meta
from utils.db import add_posted_md, prune_posted_mds

logger = logging.getLogger("spc_bot")

_md_index_head: Dict[str, str] = {}


async def fetch_latest_md_numbers() -> List[str]:
    """
    Scrape the SPC MD index page and return a list of current MD number strings.
    Uses a HEAD check first — if the index page hasn't changed since last poll,
    skips the full HTML fetch entirely.
    """
    meta = await http_head_meta(SPC_MD_INDEX_URL)
    if meta is not None and _md_index_head:
        unchanged = (
            (meta["etag"] and meta["etag"] == _md_index_head.get("etag"))
            or (
                meta["last_modified"]
                and meta["last_modified"] == _md_index_head.get("last_modified")
            )
            or (
                meta["content_length"]
                and meta["content_length"] == _md_index_head.get("content_length")
            )
        )
        if unchanged and any(meta.values()):
            return []
    if meta:
        _md_index_head.update(meta)

    html = await http_get_text(SPC_MD_INDEX_URL)
    if not html:
        return []

    numbers = re.findall(
        r'href="(?:/products/md/)?md(\d+)\.html"', html, re.IGNORECASE
    )
    seen = set()
    result = []
    for n in numbers:
        if n not in seen:
            seen.add(n)
            result.append(n.zfill(4))
    return result


async def fetch_md_details(
    md_number: str,
) -> Tuple[Optional[str], Optional[str], bool]:
    """
    Fetch an individual MD page and return (image_url, summary_text, from_cache).
    Falls back to cached image if SPC is unreachable.
    """
    page_url = f"https://www.spc.noaa.gov/products/md/md{md_number}.html"
    html = await http_get_text(page_url)

    if not html:
        fallback_url = f"https://www.spc.noaa.gov/products/md/mcd{md_number}.png"
        cached_path = get_cache_path_for_url(fallback_url)
        if os.path.exists(cached_path):
            logger.info(
                f"[MD] SPC unreachable for #{md_number}, serving from cache"
            )
            return fallback_url, None, True
        logger.warning(
            f"[MD] SPC unreachable for #{md_number} and no cache available"
        )
        return None, None, False

    img_match = re.search(
        rf'src="(mcd{md_number}(?:_full)?\.(?:png|gif))"', html, re.IGNORECASE
    )
    if img_match:
        image_url = (
            f"https://www.spc.noaa.gov/products/md/{img_match.group(1)}"
        )
    else:
        image_url = f"https://www.spc.noaa.gov/products/md/mcd{md_number}.png"

    summary = None
    concerning = re.search(r"(CONCERNING[^\n<]{10,120})", html, re.IGNORECASE)
    if concerning:
        summary = concerning.group(1).strip()
    else:
        text_blocks = re.findall(
            r"<pre[^>]*>(.*?)</pre>", html, re.DOTALL | re.IGNORECASE
        )
        for block in text_blocks:
            clean = re.sub(r"<[^>]+>", "", block).strip()
            lines = [line.strip() for line in clean.splitlines() if line.strip()]
            if lines:
                summary = " ".join(lines[:3])[:200]
                break

    return image_url, summary, False


class MesoscaleCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._md_backoff = TaskBackoff("auto_post_md")
        self.auto_post_md.start()

    def cog_unload(self):
        self.auto_post_md.cancel()

    @tasks.loop(seconds=30)
    async def auto_post_md(self):
        await self.bot.wait_until_ready()
        channel = self.bot.get_channel(SPC_CHANNEL_ID)
        if not channel:
            logger.warning("SPC channel not found for auto_post_md")
            return

        try:
            md_numbers = await fetch_latest_md_numbers()
            current_mds = set(md_numbers)

            # ── MD cancellations ───────────────────────────────────────────
            if current_mds:
                for md_num in list(active_mds):
                    if md_num not in current_mds:
                        active_mds.discard(md_num)
                        logger.info(
                            f"[MD] MD #{md_num} no longer on index — "
                            f"posting cancellation"
                        )
                        embed = discord.Embed(
                            title=(
                                f"✅  Mesoscale Discussion #{int(md_num)} "
                                f"— Cancelled"
                            ),
                            color=discord.Color.green(),
                            timestamp=datetime.now(timezone.utc),
                        )
                        embed.set_footer(text="SPC MD Monitor")
                        try:
                            await channel.send(embed=embed)
                            logger.info(
                                f"[MD] Posted cancellation for #{md_num}"
                            )
                        except discord.HTTPException as e:
                            logger.error(
                                f"[MD] Failed to send cancellation "
                                f"for #{md_num}: {e}"
                            )
                            active_mds.add(md_num)

            # ── New MDs ────────────────────────────────────────────────────
            for md_num in md_numbers:
                active_mds.add(md_num)
                if md_num in posted_mds:
                    continue

                logger.info(f"[MD] New MD detected: #{md_num}")
                image_url, summary, from_cache = await fetch_md_details(md_num)

                if not image_url:
                    logger.warning(
                        f"[MD] Could not resolve image URL for MD #{md_num}"
                    )
                    continue

                cache_path, img_content, h = await download_single_image(
                    image_url, AUTO_CACHE_FILE, auto_cache
                )

                md_page_url = (
                    f"https://www.spc.noaa.gov/products/md/mcd{md_num}.html"
                )
                header = f"**🌩️ SPC Mesoscale Discussion #{md_num}**"
                if summary:
                    header += f"\n{summary}"
                header += f"\n<{md_page_url}>"

                try:
                    if cache_path:
                        await channel.send(
                            header, files=[discord.File(cache_path)]
                        )
                    else:
                        await channel.send(header)
                    posted_mds.add(md_num)
                    asyncio.create_task(add_posted_md(str(md_num)))
                    asyncio.create_task(prune_posted_mds())
                    last_post_times["md"] = datetime.now(timezone.utc)
                    logger.info(f"[MD] Posted MD #{md_num}")
                except discord.HTTPException as e:
                    logger.error(
                        f"[MD] Discord send failed for MD #{md_num}: {e}"
                    )

            # Prune tracked MDs
            prune_tracked_set(posted_mds, MAX_TRACKED_MDS, MD_CACHE_FILE)

        except Exception as e:
            logger.error(
                f"[MD] Unexpected error in auto_post_md: {e}", exc_info=True
            )

    @auto_post_md.after_loop
    async def after_md_loop(self):
        if self.auto_post_md.is_being_cancelled():
            return
        task = self.auto_post_md.get_task()
        exc = task.exception() if task else None
        if exc:
            logger.error(
                f"[TASK] auto_post_md stopped due to exception: "
                f"{type(exc).__name__}: {exc}",
                exc_info=exc,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(MesoscaleCog(bot))
