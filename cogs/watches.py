# cogs/watches.py
import json as _json
import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import discord
from discord.ext import commands, tasks
from cogs.iembot import get_cached_watch_text
from utils.backoff import TaskBackoff

from config import (
    AUTO_CACHE_FILE,
    MANUAL_CACHE_FILE,
    NWS_ALERTS_URL,
    SPC_CHANNEL_ID,
    SPC_VALID_WATCHES_URL,
    SPC_WATCH_INDEX_URL,
)
from utils.cache import (
    download_single_image,
)
from utils.change_detection import get_cache_path_for_url, is_placeholder_image
from utils.http import http_get_bytes, http_get_bytes_conditional, http_get_text
from utils.state_store import add_posted_watch, prune_posted_watches

logger = logging.getLogger("spc_bot")

# Hoisted patterns — VTEC is scanned in a loop over every active feature,
# so caching the compiled form measurably helps on large NWS payloads.
_VTEC_RE = re.compile(
    r"/[^.]+\.[^.]+\.[^.]+\.(SV|TO)\.A\.(\d{4})\.",
    re.IGNORECASE,
)
_WW_HREF_RE = re.compile(r'href="[^"]*ww(\d+)\.html"', re.IGNORECASE)
_TORNADO_WATCH_RE = re.compile(r"Tornado Watch", re.IGNORECASE)

# Conditional-GET state for the NWS active-alerts feed. Validators let us
# 304 most 2-minute polls; _last_parsed holds the parsed dict to return
# on 304 so callers see no behavioral difference vs. a fresh fetch.
_nws_validators: Dict[str, str] = {}
_nws_last_parsed: Optional[Dict[str, dict]] = None



async def fetch_active_watches_nws() -> Optional[Dict[str, dict]]:
    """
    Fetch active SPC watches from the NWS Alerts API.
    Returns dict: watch_num -> {"type": "SVR"|"TORNADO", "expires": datetime}
    Deduplicates by watch number from the VTEC string.
    """
    global _nws_last_parsed
    # Keep retries×timeout well under the 2-minute auto_post_watches
    # cycle so a bad NWS API window doesn't stall successive ticks.
    content, status, validators = await http_get_bytes_conditional(
        NWS_ALERTS_URL,
        etag=_nws_validators.get("etag") or None,
        last_modified=_nws_validators.get("last_modified") or None,
        retries=2,
        timeout=15,
    )
    if status == 304 and _nws_last_parsed is not None:
        return _nws_last_parsed
    if not content or status != 200:
        logger.warning(
            f"[WATCH] NWS API returned status {status} — will retry next cycle"
        )
        return None
    if validators and (validators.get("etag") or validators.get("last_modified")):
        _nws_validators["etag"] = validators.get("etag", "")
        _nws_validators["last_modified"] = validators.get("last_modified", "")
    try:
        data = _json.loads(content)
    except Exception as e:
        logger.warning(f"[WATCH] NWS API JSON parse error: {e}")
        return None

    result = {}
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        vtec_list = props.get("parameters", {}).get("VTEC", [])
        expires_str = props.get("expires") or props.get("ends")
        for vtec in vtec_list:
            m = _VTEC_RE.search(vtec)
            if not m:
                continue
            watch_num = m.group(2).zfill(4)
            wtype = "TORNADO" if m.group(1).upper() == "TO" else "SVR"
            if watch_num in result:
                continue
            expires_dt = None
            if expires_str:
                try:
                    expires_dt = datetime.fromisoformat(expires_str).astimezone(
                        timezone.utc
                    )
                except (ValueError, TypeError) as e:
                    logger.debug(f"[WATCH] Could not parse expires {expires_str!r}: {e}")
            logger.debug(
                f"[WATCH] NWS API: #{watch_num} ({wtype}) expires {expires_dt}"
            )
            affected_zones = props.get("affectedZones", [])
            result[watch_num] = {
                "type": wtype,
                "expires": expires_dt,
                "affected_zones": affected_zones,
            }
    _nws_last_parsed = result
    return result


async def fetch_latest_watch_numbers() -> List[Tuple[str, str]]:
    """
    Returns (watch_num, watch_type) list. Uses NWS API as primary source,
    falls back to SPC HTML scrape if API fails.
    """
    nws = await fetch_active_watches_nws()
    if nws is None:
        logger.warning("[WATCH] NWS API fetch failed — skipping, no fallback for auto loop")
        return []
    if nws:
        return [(num, info["type"]) for num, info in nws.items()]

    logger.warning("[WATCH] NWS API empty, falling back to SPC HTML scrape")
    html = await http_get_text(SPC_WATCH_INDEX_URL)
    if not html:
        return []

    seen = []
    seen_set = set()
    for m in _WW_HREF_RE.finditer(html):
        num = m.group(1).zfill(4)
        if num in seen_set:
            continue
        seen_set.add(num)
        seen.append(num)

    async def _classify(num: str) -> Tuple[str, str]:
        watch_html = await http_get_text(
            f"https://www.spc.noaa.gov/products/watch/ww{num}.html"
        )
        wtype = "SVR"
        if watch_html and _TORNADO_WATCH_RE.search(watch_html):
            wtype = "TORNADO"
        return num, wtype

    return list(await asyncio.gather(*[_classify(n) for n in seen]))



async def fetch_watch_details_iem(watch_number: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Fallback: fetch watch details from IEM watches API when SPC is unreachable.
    Uses mesonet.agron.iastate.edu/json/watches.py which has structured data
    including states, probabilities, hail size, and wind gusts.
    Returns (text_summary, image_url).
    """
    num_int = int(watch_number)
    year = datetime.now(timezone.utc).year

    text_summary = None
    probs = None
    try:
        url = f"https://mesonet.agron.iastate.edu/json/watches.py?year={year}"
        content, status = await http_get_bytes(url, retries=2, timeout=15)
        if content and status == 200:
            data = _json.loads(content)
            for event in data.get("events", []):
                if event.get("num") == num_int:
                    states = event.get("states", "")
                    state_list = ", ".join(states.split(",")) if states else "Unknown"
                    is_pds = event.get("is_pds", False)

                    tor_pct = event.get("tornadoes_1m_strong", 0)
                    hail_pct = event.get("hail_1m_2inch", 0)
                    max_hail = event.get("max_hail_size", 0)
                    max_wind = event.get("max_wind_gust_knots", 0)
                    max_wind_mph = round(max_wind * 1.15078) if max_wind else 0

                    parts = [f"**Areas:** {state_list}"]
                    if is_pds:
                        parts.append("⚠️ **Particularly Dangerous Situation (PDS)**")

                    text_summary = "\n".join(parts)

                    # Probabilities go only in the probs field (rendered as a
                    # separate embed field) — not in text_summary — to avoid
                    # the same numbers appearing twice in the embed.
                    prelim_lines = ["**Probabilities (preliminary — will update)**"]
                    if tor_pct:
                        prelim_lines.append(f"🔴 Sig. tornado (EF2+): **{tor_pct}%**")
                    if hail_pct:
                        prelim_lines.append(f"🟢 2\"+ hail: **{hail_pct}%** | Max: **{max_hail}\"**")
                    if max_wind_mph:
                        prelim_lines.append(f"🔵 Max gusts: **{max_wind_mph} mph ({int(max_wind)} kt)**")
                    if len(prelim_lines) > 1:
                        probs = "\n".join(prelim_lines)

                    logger.info(f"[WATCH] Got details from IEM watches API for #{watch_number}")
                    break
    except Exception as e:
        logger.warning(f"[WATCH] IEM watches API failed for #{watch_number}: {e}")

    # No image available from IEM — SPC image will be retried separately
    return text_summary, None, probs

async def fetch_watch_details(
    watch_number: str,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Fetch an individual watch page and return (image_url, text_summary, probs).
    Races SPC and IEM page fetches simultaneously — whichever returns first wins.
    """
    page_url = f"https://www.spc.noaa.gov/products/watch/ww{watch_number}.html"
    prob_url = (
        f"https://www.spc.noaa.gov/products/watch/ww{watch_number}_prob.html"
    )

    # SPC main page and prob page are independent — fetch them in parallel.
    html, prob_html = await asyncio.gather(
        http_get_text(page_url),
        http_get_text(prob_url),
    )

    image_url = None
    if html:
        for pattern in [
            rf"ww{watch_number}_overview\.gif",
            rf"ww{watch_number}_radar\.gif",
            rf'ww{watch_number}[^"<\s]*\.gif',
        ]:
            m = re.search(pattern, html, re.IGNORECASE)
            if m:
                fname = m.group(0)
                image_url = (
                    f"https://www.spc.noaa.gov/products/watch/{fname}"
                )
                break
        if not image_url:
            image_url = (
                f"https://www.spc.noaa.gov/products/watch/"
                f"ww{watch_number}_overview.gif"
            )

    text_summary = None
    if html:
        text_blocks = re.findall(
            r"<pre[^>]*>(.*?)</pre>", html, re.DOTALL | re.IGNORECASE
        )
        for block in text_blocks:
            clean = re.sub(r"<[^>]+>", "", block).strip()
            if len(clean) < 100:
                continue
            if not re.search(r"SEL\d|Watch Number", clean, re.IGNORECASE):
                continue

            lines = [
                line.strip() for line in clean.splitlines() if line.strip()
            ]

            states = []
            in_states = False
            for line in lines:
                if re.search(r"Watch for portions of", line, re.IGNORECASE):
                    in_states = True
                    continue
                if in_states:
                    if re.search(
                        r"Effective|until|Primary", line, re.IGNORECASE
                    ):
                        break
                    if line and not re.search(r"\*", line):
                        states.append(line)

            time_line = None
            for line in lines:
                if re.search(r"Effective this", line, re.IGNORECASE):
                    idx = lines.index(line)
                    combined = " ".join(lines[idx : idx + 3])
                    combined = re.sub(r"\s+", " ", combined).strip()
                    time_line = combined
                    break

            threats = []
            in_threats = False
            for line in lines:
                if re.search(r"Primary threats", line, re.IGNORECASE):
                    in_threats = True
                    continue
                if in_threats:
                    if re.search(
                        r"SUMMARY|PRECAUTIONARY|ATTN", line, re.IGNORECASE
                    ):
                        break
                    if line and not line.startswith("*"):
                        if threats and not re.search(
                            r"possible$|mph$|diameter$",
                            threats[-1],
                            re.IGNORECASE,
                        ):
                            threats[-1] += " " + line
                        else:
                            threats.append(line)

            parts = []
            if states:
                parts.append("**Areas:** " + ", ".join(states))
            if time_line:
                parts.append("**Time:** " + time_line)
            if threats:
                parts.append(
                    "**Threats:**\n"
                    + "\n".join(f"• {t}" for t in threats[:5])
                )
            if parts:
                text_summary = "\n".join(parts)
                break

    probs = None
    if prob_html:
        cells = re.findall(
            r"<td[^>]*>(.*?)</td>", prob_html, re.DOTALL | re.IGNORECASE
        )
        pairs = []
        for cell in cells:
            clean = re.sub(r"<[^>]+>", " ", cell).strip()
            clean = re.sub(r"\s+", " ", clean)
            clean = (
                clean.replace("&gt;", ">")
                .replace("&lt;", "<")
                .replace("&amp;", "&")
            )
            label_m = re.search(
                r"Probability of (.{5,80})", clean, re.IGNORECASE
            )
            value_m = re.search(
                r"(Low|Mod|High)\s*\(([^)]+)\)", clean, re.IGNORECASE
            )
            if label_m and not value_m:
                pairs.append([label_m.group(1).strip(), None, None])
            elif value_m and pairs and pairs[-1][1] is None:
                pairs[-1][1] = value_m.group(1)
                pairs[-1][2] = value_m.group(2)

        pairs = [p for p in pairs if p[1] is not None]

        if pairs:
            sections = {
                "Tornado": [],
                "Wind": [],
                "Hail": [],
                "Combined": [],
            }
            for label, level, pct in pairs:
                ll = label.lower()
                if "tornado" in ll:
                    sections["Tornado"].append((label, level, pct))
                elif "wind" in ll:
                    sections["Wind"].append((label, level, pct))
                elif "hail" in ll and "combined" not in ll:
                    sections["Hail"].append((label, level, pct))
                else:
                    sections["Combined"].append((label, level, pct))

            section_emoji = {
                "Tornado": "🔴",
                "Wind": "🔵",
                "Hail": "🟢",
                "Combined": "🟣",
            }
            prob_lines = []
            for section, entries in sections.items():
                if not entries:
                    continue
                prob_lines.append(f"**{section}**")
                for label, level, pct in entries:
                    emoji = section_emoji.get(section, "⚪")
                    prob_lines.append(
                        f"{emoji} {label}: **{level} ({pct})**"
                    )
            if prob_lines:
                probs = "\n".join(prob_lines)
            logger.info(
                f"[WATCH] Parsed {len(pairs)} prob entries "
                f"for #{watch_number}"
            )
        else:
            logger.warning(
                f"[WATCH] No prob pairs parsed for #{watch_number}"
            )

    # Check iembot real-time cache first (populated within seconds of issuance)
    cached_text = await get_cached_watch_text(watch_number)
    if cached_text and not text_summary:
        text_summary = cached_text
        logger.info(f"[WATCH] Got text from iembot cache for #{watch_number}")

    # IEM fallback: if SPC page was unreachable, try IEM watches API
    if not html:
        logger.warning(f"[WATCH] SPC unreachable for #{watch_number} — using IEM data")
        iem_summary, iem_img, iem_probs = await fetch_watch_details_iem(watch_number)
        if iem_summary and not text_summary:
            text_summary = iem_summary
            logger.info(f"[WATCH] Got text from IEM for #{watch_number}")
        if iem_img and not image_url:
            image_url = iem_img
            logger.info(f"[WATCH] Got image from IEM for #{watch_number}")
        if iem_probs and not probs:
            probs = iem_probs
            logger.info(f"[WATCH] Got preliminary probs from IEM for #{watch_number}")

    return image_url, text_summary, probs


def _build_watch_embed(
    watch_num: str,
    *,
    is_tornado: bool,
    watch_label: str,
    color: discord.Color,
    timestamp: datetime,
    expires=None,
    text_summary: Optional[str] = None,
    probs: Optional[str] = None,
    cache_path: Optional[str] = None,
    footer: str = "SPC Watch Monitor",
    paginator_index: Optional[Tuple[int, int]] = None,
) -> discord.Embed:
    """Canonical watch embed used by paginator, auto-post, iembot fast-path,
    and the upgrade-edit. One place to fix styling drift."""
    embed = discord.Embed(
        title=(
            f"{'🌪️' if is_tornado else '⛈️'}  "
            f"{watch_label} #{int(watch_num)}"
        ),
        color=color,
        timestamp=timestamp,
    )
    if expires:
        embed.add_field(
            name="Expires",
            value=f"<t:{int(expires.timestamp())}:R>",
            inline=True,
        )
    if text_summary:
        embed.add_field(name="Details", value=text_summary[:1024], inline=False)
    if probs:
        embed.add_field(name="Probabilities", value=probs[:1024], inline=False)
    if paginator_index is not None:
        i, n = paginator_index
        embed.set_footer(text=f"Watch {i + 1} of {n} · {footer}")
    else:
        embed.set_footer(text=footer)
    if cache_path:
        embed.set_image(url=f"attachment://watch_{watch_num}.gif")
    return embed


def _watch_files(watch_num: str, cache_path: Optional[str]) -> List[discord.File]:
    if not cache_path:
        return []
    return [discord.File(cache_path, filename=f"watch_{watch_num}.gif")]


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
        watch_num, nws_info, image_url, text_summary, probs, cache_path = (
            self.watch_data[self.index]
        )
        wtype = (
            nws_info.get("type", "SVR")
            if isinstance(nws_info, dict)
            else nws_info
        )
        expires = (
            nws_info.get("expires") if isinstance(nws_info, dict) else None
        )
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
        )

    def build_files(self):
        watch_num, _, _, _, _, cache_path = self.watch_data[self.index]
        return _watch_files(watch_num, cache_path)

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.index = max(0, self.index - 1)
        self._update_buttons()
        embed = self.build_embed()
        files = self.build_files()
        await interaction.response.edit_message(
            embed=embed, attachments=files, view=self
        )

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.index = min(len(self.watch_data) - 1, self.index + 1)
        self._update_buttons()
        embed = self.build_embed()
        files = self.build_files()
        await interaction.response.edit_message(
            embed=embed, attachments=files, view=self
        )

    @discord.ui.button(
        label="🗺️ Overview", style=discord.ButtonStyle.primary
    )
    async def overview_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if self.overview_path:
            await interaction.response.send_message(
                "**Current Active Watches Overview**",
                file=discord.File(
                    self.overview_path, filename="current_watches.png"
                ),
            )
        else:
            await interaction.response.send_message(
                "Overview map unavailable."
            )

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException as e:
                logger.debug(f"[WATCH] Could not disable view on timeout: {e}")


async def _execute_watches(interaction: discord.Interaction, bot: commands.Bot):
    """Shared implementation for /watches and /ww slash commands."""
    await interaction.response.defer()
    nws_watches = await fetch_active_watches_nws()
    if nws_watches is None or not nws_watches:
        entries = await fetch_latest_watch_numbers()
        nws_watches = {
            num: {"type": wtype, "expires": None} for num, wtype in entries
        }
    if not nws_watches:
        await interaction.followup.send("No active watches found.")
        return

    overview_content, overview_status = await http_get_bytes(
        SPC_VALID_WATCHES_URL
    )
    overview_path = None
    if (
        overview_content
        and overview_status == 200
        and not is_placeholder_image(overview_content)
    ):
        overview_path = get_cache_path_for_url(SPC_VALID_WATCHES_URL)
        try:
            with open(overview_path, "wb") as ovf:
                ovf.write(overview_content)
        except Exception as e:
            logger.warning(
                f"[/watches] Could not save overview image: {e}"
            )
            overview_path = None

    async def _hydrate(watch_num: str, nws_info: dict):
        image_url, text_summary, probs = await fetch_watch_details(watch_num)
        cache_path = None
        if image_url:
            cache_path, _, _ = await download_single_image(
                image_url, MANUAL_CACHE_FILE, bot.state.manual_cache
            )
        return (watch_num, nws_info, image_url, text_summary, probs, cache_path)

    watch_data = list(
        await asyncio.gather(
            *[_hydrate(num, info) for num, info in nws_watches.items()]
        )
    )

    view = WatchPaginatorView(watch_data, overview_path)
    if len(watch_data) == 1:
        view.prev_btn.disabled = True
        view.next_btn.disabled = True
    embed = view.build_embed()
    files = view.build_files()
    msg = await interaction.followup.send(
        embed=embed, files=files, view=view
    )
    view.message = msg


class WatchesCog(commands.Cog):
    MANAGED_TASK_NAMES = [("auto_post_watches", "auto_post_watches")]

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._watches_backoff = TaskBackoff("auto_post_watches")

    async def cog_load(self):
        self.auto_post_watches.start()

    def cog_unload(self):
        self.auto_post_watches.cancel()

    async def _upgrade_watch_embed(self, watch_num: str, message: discord.Message,
                                    is_tornado: bool, watch_label: str,
                                    color: discord.Color, expires):
        """
        Polls for full SPC watch details (probs + image) and edits the original
        message once available. Retries every 30s for up to 5 minutes.
        """
        for attempt in range(10):
            await asyncio.sleep(30)
            try:
                image_url, text_summary, probs = await fetch_watch_details(watch_num)
                has_real_probs = probs and "preliminary" not in probs
                if not has_real_probs and image_url is None:
                    continue

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
                    timestamp=datetime.now(timezone.utc),
                    expires=expires,
                    text_summary=text_summary,
                    probs=probs,
                    cache_path=cache_path,
                )
                files = _watch_files(watch_num, cache_path)
                await message.edit(embed=embed, attachments=files)
                logger.info(f"[WATCH] Upgraded embed for #{watch_num} with full SPC data")
                return
            except Exception as e:
                logger.warning(f"[WATCH] Upgrade attempt {attempt+1} failed for #{watch_num}: {e}")
        logger.info(f"[WATCH] Gave up upgrading embed for #{watch_num} after 5 minutes")

    async def post_watch_now(self, watch_num: str, nws_info: dict):
        """
        Immediately post a specific watch if it hasn't been posted yet.
        Called by IEMBotCog when it detects a new watch from the real-time feed.
        """
        watch_num = watch_num.zfill(4)
        if watch_num in self.bot.state.posted_watches:
            return
        channel = self.bot.get_channel(SPC_CHANNEL_ID)
        if not channel:
            return

        wtype = nws_info.get("type", "SVR") if isinstance(nws_info, dict) else "SVR"
        expires = nws_info.get("expires") if isinstance(nws_info, dict) else None
        is_tornado = wtype == "TORNADO"
        watch_label = "Tornado Watch" if is_tornado else "Severe Thunderstorm Watch"
        color = discord.Color.red() if is_tornado else discord.Color.orange()
        now_utc = datetime.now(timezone.utc)

        logger.info(f"[WATCH] iembot-triggered post for #{watch_num} ({wtype})")
        image_url, text_summary, probs = await fetch_watch_details(watch_num)
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
        )

        try:
            files = _watch_files(watch_num, cache_path)
            message = await channel.send(embed=embed, files=files)
            # Do NOT add to active_watches here — let the NWS API poll
            # populate it with real expiry/zone data on the next cycle.
            # Adding partial nws_info now causes false cancellations when
            # the NWS API hasn't indexed the watch yet.
            self.bot.state.posted_watches.add(watch_num)
            await add_posted_watch(str(watch_num))
            await prune_posted_watches()
            self.bot.state.last_post_times["watch"] = now_utc
            logger.info(f"[WATCH] iembot-triggered: posted watch #{watch_num}")
            sounding_cog = self.bot.cogs.get("SoundingCog")
            if sounding_cog and isinstance(nws_info, dict) and nws_info.get("affected_zones"):
                asyncio.create_task(
                    sounding_cog.post_soundings_for_watch(watch_num, nws_info, channel)
                )
            # Schedule upgrade edit once SPC data is available
            has_prelim = probs and "preliminary" in probs
            if not cache_path or has_prelim:
                asyncio.create_task(
                    self._upgrade_watch_embed(watch_num, message, is_tornado, watch_label, color, expires)
                )
        except discord.HTTPException as e:
            logger.exception(f"[WATCH] iembot-triggered send failed for #{watch_num}: {e}")

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
                logger.warning(
                    "[WATCH] NWS API fetch failed — skipping cycle, active set unchanged"
                )
                return
            now_utc = datetime.now(timezone.utc)
            
            # ── Cancellations ──────────────────────────────────────────────
            for watch_num, info in list(self.bot.state.active_watches.items()):
                wtype = info["type"] if isinstance(info, dict) else info
                expires = (
                    info.get("expires") if isinstance(info, dict) else None
                )

                expired_by_time = expires is not None and now_utc >= expires
                missing_from_api = watch_num not in nws_watches

                if not (
                    expired_by_time or (missing_from_api and nws_watches)
                ):
                    continue

                self.bot.state.active_watches.pop(watch_num, None)
                reason = "expired" if expired_by_time else "no longer active"
                logger.info(
                    f"[WATCH] Watch #{watch_num} {reason} — "
                    f"posting cancellation"
                )
                watch_label = (
                    "Tornado Watch"
                    if wtype == "TORNADO"
                    else "Severe Thunderstorm Watch"
                )
                embed = discord.Embed(
                    title=(
                        f"✅  {watch_label} #{int(watch_num)} "
                        f"— Expired / Cancelled"
                    ),
                    color=discord.Color.green(),
                    timestamp=now_utc,
                )
                embed.set_footer(text="SPC Watch Monitor")
                try:
                    await channel.send(embed=embed)
                    logger.info(
                        f"[WATCH] Posted cancellation for #{watch_num}"
                    )
                except discord.HTTPException as e:
                    logger.exception(
                        f"[WATCH] Failed to send cancellation "
                        f"for #{watch_num}: {e}"
                    )
                    self.bot.state.active_watches[watch_num] = info

            # ── New watches ────────────────────────────────────────────────
            for watch_num, nws_info in nws_watches.items():
                self.bot.state.active_watches[watch_num] = nws_info
                if watch_num in self.bot.state.posted_watches:
                    # Still notify SoundingCog in case we missed it earlier due to missing affected_zones
                    sounding_cog = self.bot.cogs.get("SoundingCog")
                    if sounding_cog and isinstance(nws_info, dict) and nws_info.get("affected_zones"):
                        asyncio.create_task(
                            sounding_cog.post_soundings_for_watch(watch_num, nws_info, channel)
                        )
                    continue

                wtype = nws_info.get("type", "SVR")
                expires = nws_info.get("expires")
                is_tornado = wtype == "TORNADO"
                watch_label = (
                    "Tornado Watch"
                    if is_tornado
                    else "Severe Thunderstorm Watch"
                )
                color = (
                    discord.Color.red()
                    if is_tornado
                    else discord.Color.orange()
                )

                logger.info(
                    f"[WATCH] New watch detected: #{watch_num} ({wtype})"
                )
                image_url, text_summary, probs = await fetch_watch_details(
                    watch_num
                )
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
                )

                try:
                    files = _watch_files(watch_num, cache_path)
                    await channel.send(embed=embed, files=files)
                    self.bot.state.posted_watches.add(watch_num)
                    await add_posted_watch(str(watch_num))
                    await prune_posted_watches()
                    self.bot.state.last_post_times["watch"] = datetime.now(timezone.utc)
                    logger.info(f"[WATCH] Posted watch #{watch_num}")
                    sounding_cog = self.bot.cogs.get("SoundingCog")
                    if sounding_cog:
                        asyncio.create_task(
                            sounding_cog.post_soundings_for_watch(watch_num, nws_info, channel)
                        )
                except discord.HTTPException as e:
                    logger.exception(
                        f"[WATCH] Discord send failed for #{watch_num}: {e}"
                    )

            self._watches_backoff.success()

        except Exception as e:
            logger.exception(
                f"[WATCH] Unexpected error in auto_post_watches: {e}",
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
                f"[TASK] auto_post_watches stopped due to exception: "
                f"{type(exc).__name__}: {exc}",
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
