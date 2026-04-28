# cogs/mesoscale.py
import asyncio
import html as _html
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import discord
from discord.ext import commands, tasks

from cogs.iembot import get_cached_md_text
from config import AUTO_CACHE_FILE, SPC_CHANNEL_ID, SPC_MD_INDEX_URL
from utils.backoff import TaskBackoff
from utils.cache import (
    download_single_image,
)
from utils.change_detection import get_cache_path_for_url
from utils.http import http_get_bytes, http_get_text, http_head_meta
from utils.state_store import add_posted_md, prune_posted_mds

logger = logging.getLogger("spc_bot")

_md_index_head: Dict[str, str] = {}
_md_index_unreachable: bool = False


async def fetch_latest_md_numbers(fresh: bool = False) -> List[str]:
    """
    Scrape the SPC MD index page and return a list of current MD number strings.
    Uses a HEAD check first — if the index page hasn't changed since last poll,
    skips the full HTML fetch entirely. Falls back to IEM if SPC is unreachable.
    """
    global _md_index_head
    logger.debug(f"[MD] Fetching MD numbers (fresh={fresh})")

    if not fresh:
        meta = await http_head_meta(SPC_MD_INDEX_URL)
        if meta is not None and _md_index_head:
            # Require ALL non-empty validators to match. OR was too loose —
            # if content_length happened to line up while the page actually
            # changed, we'd silently drop the new MD.
            checks = []
            for key in ("etag", "last_modified", "content_length"):
                if meta.get(key):
                    checks.append(meta[key] == _md_index_head.get(key))
            if checks and all(checks):
                logger.debug("[MD] Index unchanged (HEAD match)")
                return []
        if meta:
            _md_index_head.update(meta)
    else:
        # Clear the cached HEAD info if fresh is requested
        _md_index_head = {}

    logger.debug(f"[MD] Requesting SPC index: {SPC_MD_INDEX_URL}")
    html = await http_get_text(SPC_MD_INDEX_URL)

    global _md_index_unreachable
    # If SPC is unreachable, try to scrape from IEM's nwstext API
    if not html:
        logger.warning("[MD] SPC index HTML empty/failed, falling back to IEM")
        if not _md_index_unreachable:
            logger.warning("[MD] SPC index unreachable — falling back to IEM for active MD list")
            _md_index_unreachable = True
        try:
            content, status = await http_get_bytes(
                "https://mesonet.agron.iastate.edu/api/1/nwstext.json?product=MCD&limit=40",
                retries=2, timeout=15
            )
            if content and status == 200:
                data = json.loads(content)
                md_nums = set()
                for entry in data.get("data", []):
                    m = re.search(r"MESOSCALE DISCUSSION\s+(\d+)", entry.get("data", ""), re.IGNORECASE)
                    if m:
                        md_nums.add(m.group(1).zfill(4))
                logger.info(f"[MD] IEM fallback returned {len(md_nums)} MDs")
                # Only return MDs from today/recent hours if possible, 
                # but for simplicity we'll just return the unique ones in the feed.
                return sorted(list(md_nums), reverse=True)
        except Exception as e:
            logger.exception(f"[MD] IEM fallback for index failed: {e}")
        return []

    if _md_index_unreachable:
        logger.info("[MD] SPC index reachable again")
        _md_index_unreachable = False

    numbers = re.findall(
        r'href="(?:/products/md/)?md(\d+)\.html"', html, re.IGNORECASE
    )
    seen = set()
    result = []
    for n in numbers:
        if n not in seen:
            seen.add(n)
            result.append(n.zfill(4))
    
    logger.debug(f"[MD] Scraped {len(result)} MD numbers from SPC index")
    return result



async def fetch_md_details_iem(md_number: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Fallback: fetch MD image and summary from IEM when SPC is unreachable.
    IEM mirrors SPC MCD images at a predictable URL.
    Returns (image_url, summary_text, raw_text).
    """
    padded = md_number.zfill(4)
    num_int = int(md_number)

    # IEM mirrors SPC MCD PNGs
    iem_img_url = f"https://mesonet.agron.iastate.edu/pickup/mcd/mcd{padded}.png"
    img_bytes, img_status = await http_get_bytes(iem_img_url, retries=2, timeout=15)
    iem_image_url = iem_img_url if (img_bytes and img_status == 200 and len(img_bytes) > 2048) else None

    # IEM nwstext API for MCD text
    summary = None
    raw_text = None
    try:
        content, status = await http_get_bytes(
            "https://mesonet.agron.iastate.edu/api/1/nwstext.json?product=MCD&limit=20",
            retries=2, timeout=15
        )
        if content and status == 200:
            data = json.loads(content)
            for entry in data.get("data", []):
                text = entry.get("data", "")
                if (
                    f"MESOSCALE DISCUSSION {num_int}" in text.upper()
                    or f"MESOSCALE DISCUSSION {padded}" in text
                ):
                    raw_text = text
                    concerning = re.search(r"(CONCERNING[^\n<]{10,120})", text, re.IGNORECASE)
                    if concerning:
                        summary = concerning.group(1).strip()
                    else:
                        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
                        summary = " ".join(lines[:3])[:200]
                    break
    except Exception as e:
        logger.warning(f"[MD] IEM text fallback failed for #{md_number}: {e}")

    return iem_image_url, summary, raw_text

async def fetch_md_details(
    md_number: str,
) -> Tuple[Optional[str], Optional[str], bool, Optional[str]]:
    """
    Fetch an individual MD page and return (image_url, summary_text, from_cache, raw_text).
    Races SPC and IEM simultaneously — whichever returns first wins.
    Falls back to cache if both fail.
    """
    page_url = f"https://www.spc.noaa.gov/products/md/md{md_number}.html"

    async def _fetch_spc():
        return await http_get_text(page_url)

    async def _fetch_iem_early():
        # Self-import via module object so tests can monkeypatch
        # cogs.mesoscale.fetch_md_details_iem at runtime.
        import cogs.mesoscale as _self  # noqa: PLC0415
        iem_img, iem_summary, iem_raw = await _self.fetch_md_details_iem(md_number)
        return (iem_img, iem_summary, iem_raw)

    spc_task = asyncio.create_task(_fetch_spc())
    iem_task = asyncio.create_task(_fetch_iem_early())

    done, pending = await asyncio.wait(
        [spc_task, iem_task],
        return_when=asyncio.FIRST_COMPLETED,
    )

    html = None
    iem_result = None

    first = done.pop()
    if first is spc_task:
        html = first.result()
        if html:
            logger.debug(f"[MD] SPC won race for #{md_number}")
            for t in pending:
                t.cancel()
        else:
            logger.warning(f"[MD] SPC page failed for #{md_number} — waiting for IEM")
            if pending:
                try:
                    iem_result = await pending.pop()
                except Exception as e:
                    logger.debug(f"[MD] IEM fallback also failed for #{md_number}: {e}")
    else:
        iem_result = first.result()
        try:
            html = await asyncio.wait_for(asyncio.shield(spc_task), timeout=5.0)
            if html:
                logger.debug(f"[MD] SPC caught up for #{md_number}")
                iem_result = None
        except asyncio.TimeoutError:
            logger.warning(f"[MD] SPC timed out for #{md_number} — using IEM")
            spc_task.cancel()

    if not html:
        fallback_url = f"https://www.spc.noaa.gov/products/md/mcd{md_number}.png"
        cached_path = get_cache_path_for_url(fallback_url)
        if os.path.exists(cached_path):
            logger.info(f"[MD] SPC unreachable for #{md_number}, serving from cache")
            return fallback_url, None, True, None
        if iem_result:
            iem_img, iem_summary, iem_raw = iem_result
            if iem_img:
                logger.info(f"[MD] Got MD #{md_number} from IEM")
                return iem_img, iem_summary, True, iem_raw
        logger.warning(f"[MD] SPC unreachable for #{md_number} and no cache or IEM available")
        return None, None, False, None

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

    # Check iembot real-time cache first (populated within seconds of issuance)
    summary = await get_cached_md_text(md_number)
    if summary:
        logger.info(f"[MD] Got summary from iembot cache for #{md_number}")

    if not summary:
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

    return image_url, summary, False, html


# ── MD body extraction & embed formatting ────────────────────────────────────

# Discord embed description max is 4096 chars. We reserve room for the
# code-block fences (``` + newlines) so the visible body stays inside the
# limit. Splitting kicks in above this threshold.
EMBED_BODY_LIMIT = 4000


def extract_md_body(raw_text: Optional[str]) -> Optional[str]:
    """Return the plain-text MD body from the SPC HTML page or IEM text.

    Truncates at common footer blocks and strips the redundant header 
    to keep only the meat of the discussion.
    """
    if not raw_text:
        return None
    
    # 1. Extraction from HTML if needed
    clean = None
    if "<pre" in raw_text.lower() or "<p>" in raw_text.lower():
        text_blocks = re.findall(
            r"<pre[^>]*>(.*?)</pre>", raw_text, re.DOTALL | re.IGNORECASE
        )
        for block in text_blocks:
            candidate = re.sub(r"<[^>]+>", "", block)
            candidate = _html.unescape(candidate).strip()
            if "MESOSCALE DISCUSSION" in candidate.upper() or "PROBABILITY OF WATCH ISSUANCE" in candidate.upper():
                clean = candidate
                break
    else:
        clean = raw_text.strip()

    if not clean:
        return None

    # 2. Strip technical footers
    footers = [
        "...Please see www.spc.noaa.gov",
        "ATTN...WFO",
        "LAT...LON",
    ]
    for footer in footers:
        idx = clean.find(footer)
        if idx != -1:
            clean = clean[:idx].strip()

    # 3. Strip redundant top header (everything before 'Areas affected' or 'Concerning')
    # This removes the "Discussion 0XXX", "Norman OK", and "Timestamp" lines.
    markers = ["Areas affected...", "Concerning...", "Valid ", "SUMMARY..."]
    for marker in markers:
        idx = clean.find(marker)
        if idx != -1:
            clean = clean[idx:].strip()
            break

    return clean


def clean_md_text_for_discord(text: str) -> str:
    """Un-wraps SPC's hard-wrapped lines and tightens spacing."""
    if not text:
        return ""
    
    lines = text.splitlines()
    cleaned_lines = []
    
    current_para = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current_para:
                cleaned_lines.append(" ".join(current_para))
                current_para = []
            continue
        
        # Detect headers like SUMMARY... or DISCUSSION...
        is_header = stripped.endswith("...") and any(
            stripped.startswith(m) for m in ["SUMMARY", "DISCUSSION", "Concerning", "Areas affected", "Valid", "Probability"]
        )
        
        if is_header:
            if current_para:
                cleaned_lines.append(" ".join(current_para))
                current_para = []
            # Bold the header but don't add a newline yet, so the text can follow it
            cleaned_lines.append(f"**{stripped}**")
        else:
            current_para.append(stripped)
            
    if current_para:
        cleaned_lines.append(" ".join(current_para))
        
    # Join with single newline for maximum vertical compactness
    return "\n".join(cleaned_lines)


def chunk_md_text(text: str, max_chars: int = EMBED_BODY_LIMIT) -> List[str]:
    """Split ``text`` into chunks that each fit inside ``max_chars``.

    Splits on paragraph boundaries (blank lines) first, then on line
    boundaries inside any paragraph that's still too large. We never
    break mid-line because SPC formats areas/threats as fixed-width
    columns that are unreadable when wrapped.
    """
    if not text:
        return []
    text = text.strip()
    if len(text) <= max_chars:
        return [text]

    paragraphs = re.split(r"\n\s*\n", text)
    chunks: List[str] = []
    current = ""

    def _flush():
        nonlocal current
        if current.strip():
            chunks.append(current.rstrip())
        current = ""

    for p in paragraphs:
        # Single paragraph too big — fall through to per-line splitting.
        if len(p) > max_chars:
            _flush()
            for line in p.splitlines():
                # Even a single line might exceed the limit; in that
                # absurd case we hard-truncate it rather than dropping it.
                if len(line) > max_chars:
                    line = line[: max_chars - 3] + "..."
                if len(current) + len(line) + 1 > max_chars:
                    _flush()
                current += line + "\n"
            _flush()
            continue

        addition_len = len(p) + (2 if current else 0)
        if len(current) + addition_len > max_chars:
            _flush()
        if current:
            current += "\n\n"
        current += p

    _flush()
    return chunks


def build_md_embeds(
    md_num: str,
    full_text: Optional[str],
    image_filename: Optional[str] = None,
) -> List[discord.Embed]:
    """Build the list of embeds for an MD post.

    A short MD becomes a single embed: title links to the SPC page,
    description is a code-block-wrapped body to preserve SPC's column
    alignment, image attached via ``attachment://``. Long MDs (over
    ``EMBED_BODY_LIMIT`` chars) split into multiple embeds — paragraph
    boundaries are preferred. The image lives only on the first embed.
    """
    md_page_url = f"https://www.spc.noaa.gov/products/md/mcd{md_num}.html"
    color = discord.Color.orange()
    base_title = f"🌩️ SPC Mesoscale Discussion #{md_num}"

    chunks = chunk_md_text(full_text, EMBED_BODY_LIMIT) if full_text else [None]
    if not chunks:
        chunks = [None]

    embeds: List[discord.Embed] = []
    n = len(chunks)
    for i, chunk in enumerate(chunks):
        title = base_title if n == 1 else f"{base_title} ({i + 1}/{n})"
        embed = discord.Embed(title=title, url=md_page_url, color=color)
        if chunk:
            embed.description = f"```\n{chunk}\n```"
        if i == 0 and image_filename:
            embed.set_image(url=f"attachment://{image_filename}")
        embeds.append(embed)
    return embeds


class MesoscaleCog(commands.Cog):
    MANAGED_TASK_NAMES = [("auto_post_md", "auto_post_md")]

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._md_backoff = TaskBackoff("auto_post_md")

    async def cog_load(self):
        self.auto_post_md.start()

    def cog_unload(self):
        self.auto_post_md.cancel()

    async def _check_prewarm(self, md_num: str, raw_text: str):
        """Parse probability of watch issuance and signal SoundingCog if high."""
        if not raw_text:
            return
        
        # Look for "PROBABILITY OF WATCH ISSUANCE...80 PERCENT" etc.
        m = re.search(r"PROBABILITY OF WATCH ISSUANCE\s*\.\.\.\s*(\d+)\s*PERCENT", raw_text, re.IGNORECASE)
        if not m:
            return
        
        try:
            prob = int(m.group(1))
            if prob >= 80:
                logger.info(f"[MD] High watch probability ({prob}%) for MD #{md_num} — triggering sounding pre-warm")
                sounding_cog = self.bot.cogs.get("SoundingCog")
                if sounding_cog:
                    asyncio.create_task(sounding_cog.prewarm_soundings_for_md(md_num, raw_text))
        except Exception as e:
            logger.debug(f"[MD] Could not parse probability for MD #{md_num}: {e}")

    async def _upgrade_md_message(
        self,
        md_num: str,
        message: discord.Message,
        full_text: Optional[str],
    ):
        """Poll for the MD graphic and body text, editing the message
        as either becomes available.

        Two separate things can be missing when the iembot fast-path
        fires: the SPC graphic (slow CDN), and/or the SPC HTML body
        text (404 until SPC publishes the discussion). Each tick we try
        to recover whichever is still missing and edit the message
        immediately on improvement, so the user never sits looking at
        an empty embed for the full poll window. Stops once we have
        both the graphic and the body text.
        """
        image_url = f"https://www.spc.noaa.gov/products/md/mcd{md_num}.png"
        filename = f"mcd_{md_num}.png"
        cache_path: Optional[str] = None

        async def _push_edit():
            embeds = build_md_embeds(
                md_num, full_text,
                image_filename=filename if cache_path else None,
            )
            try:
                if cache_path:
                    await message.edit(
                        embeds=embeds,
                        attachments=[discord.File(cache_path, filename=filename)],
                    )
                else:
                    await message.edit(embeds=embeds)
                return True
            except discord.HTTPException as e:
                logger.warning(f"[MD] Failed to edit MD message for #{md_num}: {e}")
                return False

        for attempt in range(10):
            await asyncio.sleep(30)

            text_recovered = False
            if not full_text:
                try:
                    _, _, _, raw = await fetch_md_details(md_num)
                    recovered = extract_md_body(raw)
                    if recovered:
                        full_text = recovered
                        text_recovered = True
                        logger.info(
                            f"[MD] Recovered body text for #{md_num} during upgrade"
                        )
                except Exception as e:
                    logger.debug(f"[MD] Body recovery failed for #{md_num}: {e}")

            image_recovered = False
            if not cache_path:
                try:
                    cp, _, _ = await download_single_image(
                        image_url, AUTO_CACHE_FILE, self.bot.state.auto_cache
                    )
                    if cp:
                        cache_path = cp
                        image_recovered = True
                except Exception as e:
                    logger.debug(f"[MD] Upgrade poll error for #{md_num}: {e}")

            if text_recovered or image_recovered:
                ok = await _push_edit()
                if not ok:
                    break
                if image_recovered:
                    logger.info(f"[MD] Upgraded MD #{md_num} with graphic")

            if cache_path and full_text:
                # We've got both — nothing left to backfill.
                break

    async def post_md_now(self, md_num: str):
        """
        Immediately post a specific MD if it hasn't been posted yet.
        Called by IEMBotCog when it detects a new MD from the real-time feed.
        """
        md_num = md_num.zfill(4)
        if md_num in self.bot.state.posted_mds:
            return
        channel = self.bot.get_channel(SPC_CHANNEL_ID)
        if not channel:
            return

        logger.info(f"[MD] iembot-triggered post for #{md_num}")
        image_url, summary, from_cache, raw_text = await fetch_md_details(md_num)

        if raw_text:
            asyncio.create_task(self._check_prewarm(md_num, raw_text))

        full_text = extract_md_body(raw_text)

        cache_path = None
        if image_url:
            cache_path, _, _ = await download_single_image(
                image_url, AUTO_CACHE_FILE, self.bot.state.auto_cache
            )
        else:
            logger.info(
                f"[MD] iembot trigger: no image yet for #{md_num} — "
                f"posting text and backfilling graphic"
            )

        filename = f"mcd_{md_num}.png"
        embeds = build_md_embeds(
            md_num, full_text, image_filename=filename if cache_path else None
        )

        try:
            if cache_path:
                msg = await channel.send(
                    embeds=embeds,
                    files=[discord.File(cache_path, filename=filename)],
                )
            else:
                msg = await channel.send(embeds=embeds)
                # Graphic missing (SPC index lag or 403); backfill once SPC catches up
                asyncio.create_task(
                    self._upgrade_md_message(md_num, msg, full_text)
                )

            self.bot.state.active_mds.add(md_num)
            self.bot.state.posted_mds.add(md_num)
            await add_posted_md(str(md_num))
            await prune_posted_mds()
            self.bot.state.last_post_times["md"] = datetime.now(timezone.utc)
            logger.info(f"[MD] iembot-triggered: posted MD #{md_num}")
        except discord.HTTPException as e:
            logger.exception(f"[MD] iembot-triggered send failed for #{md_num}: {e}")

    @tasks.loop(seconds=30)
    async def auto_post_md(self):
        try:
            await self.bot.wait_until_ready()
            if not self.bot.state.is_primary:
                return

            channel = self.bot.get_channel(SPC_CHANNEL_ID)
            if not channel:
                logger.warning("SPC channel not found for auto_post_md")
                return

            md_numbers = await fetch_latest_md_numbers()
            current_mds = set(md_numbers)

            # ── MD cancellations ───────────────────────────────────────────
            if current_mds:
                current_max = max(int(m) for m in current_mds)
                for md_num in list(self.bot.state.active_mds):
                    if md_num not in current_mds:
                        # Protect against index lag: if the active MD is newer than
                        # anything on the index, it means the index hasn't caught up.
                        num_int = int(md_num)
                        # Handle year wraparound (e.g. 0001 is newer than 9999)
                        is_newer = (num_int > current_max and num_int - current_max < 1000) or \
                                   (num_int < current_max and current_max - num_int > 8000)
                        
                        if is_newer:
                            logger.info(
                                f"[MD] Index lagging (highest is {current_max:04d}) — "
                                f"sparing #{md_num} from cancellation"
                            )
                            continue

                        self.bot.state.active_mds.discard(md_num)
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
                            self.bot.state.last_post_times["md"] = datetime.now(timezone.utc)
                            logger.info(
                                f"[MD] Posted cancellation for #{md_num}"
                            )
                        except discord.HTTPException as e:
                            logger.exception(
                                f"[MD] Failed to send cancellation "
                                f"for #{md_num}: {e}"
                            )
                            self.bot.state.active_mds.add(md_num)

            # ── New MDs ────────────────────────────────────────────────────
            for md_num in md_numbers:
                self.bot.state.active_mds.add(md_num)
                if md_num in self.bot.state.posted_mds:
                    continue

                logger.info(f"[MD] New MD detected: #{md_num}")
                image_url, summary, from_cache, raw_text = await fetch_md_details(md_num)

                if not image_url:
                    logger.warning(
                        f"[MD] Could not resolve image URL for MD #{md_num}"
                    )
                    continue

                if raw_text:
                    asyncio.create_task(self._check_prewarm(md_num, raw_text))

                full_text = extract_md_body(raw_text)

                cache_path, img_content, h = await download_single_image(
                    image_url, AUTO_CACHE_FILE, self.bot.state.auto_cache
                )

                filename = f"mcd_{md_num}.png"
                embeds = build_md_embeds(
                    md_num, full_text,
                    image_filename=filename if cache_path else None,
                )

                try:
                    if cache_path:
                        msg = await channel.send(
                            embeds=embeds,
                            files=[discord.File(cache_path, filename=filename)],
                        )
                    else:
                        msg = await channel.send(embeds=embeds)
                        # Graphic missing (likely 403), try to fetch it later
                        asyncio.create_task(
                            self._upgrade_md_message(md_num, msg, full_text)
                        )

                    self.bot.state.posted_mds.add(md_num)
                    await add_posted_md(str(md_num))
                    await prune_posted_mds()
                    self.bot.state.last_post_times["md"] = datetime.now(timezone.utc)
                    logger.info(f"[MD] Posted MD #{md_num}")
                except discord.HTTPException as e:
                    logger.exception(
                        f"[MD] Discord send failed for MD #{md_num}: {e}"
                    )
            
            self._md_backoff.success()

        except Exception as e:
            logger.exception(
                f"[MD] Unexpected error in auto_post_md: {e}"
            )
            await self._md_backoff.failure(self.bot)

    @auto_post_md.after_loop
    async def after_md_loop(self):
        if self.auto_post_md.is_being_cancelled():
            return
        task = self.auto_post_md.get_task()
        try:
            exc = task.exception() if task else None
        except Exception:
            exc = None
        if exc:
            logger.error(
                f"[TASK] auto_post_md stopped due to exception: "
                f"{type(exc).__name__}: {exc}",
                exc_info=exc,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(MesoscaleCog(bot))
