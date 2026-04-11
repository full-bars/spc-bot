# cogs/watches.py
import json as _json
import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import discord
from discord.ext import commands, tasks
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
    MAX_TRACKED_WATCHES,
    WATCH_CACHE_FILE,
    active_watches,
    auto_cache,
    download_single_image,
    last_post_times,
    manual_cache,
    posted_watches,
    prune_tracked_set,
)
from utils.change_detection import get_cache_path_for_url, is_placeholder_image
from utils.http import http_get_bytes, http_get_text
from utils.db import add_posted_watch, prune_posted_watches

logger = logging.getLogger("spc_bot")


async def fetch_active_watches_nws() -> Optional[Dict[str, dict]]:
    """
    Fetch active SPC watches from the NWS Alerts API.
    Returns dict: watch_num -> {"type": "SVR"|"TORNADO", "expires": datetime}
    Deduplicates by watch number from the VTEC string.
    """
    content, status = await http_get_bytes(NWS_ALERTS_URL, retries=5, timeout=30)
    if not content or status != 200:
        logger.warning(
            f"[WATCH] NWS API returned status {status} — will retry next cycle"
        )
        return None
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
            m = re.search(
                r"/[^.]+\.[^.]+\.[^.]+\.(SV|TO)\.A\.(\d{4})\.",
                vtec,
                re.IGNORECASE,
            )
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
                except Exception:
                    pass
            logger.info(
                f"[WATCH] NWS API: #{watch_num} ({wtype}) expires {expires_dt}"
            )
            result[watch_num] = {"type": wtype, "expires": expires_dt}
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
    results = []
    seen = set()
    for m in re.finditer(
        r'href="ww(\d+)\.html"[^>]*alt="([^"]*)"', html, re.IGNORECASE
    ):
        num = m.group(1).zfill(4)
        if num in seen:
            continue
        seen.add(num)
        alt = m.group(2).lower()
        wtype = "TORNADO" if "tornado" in alt else "SVR"
        results.append((num, wtype))
    return results


async def fetch_watch_details(
    watch_number: str,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Fetch an individual watch page and return (image_url, text_summary, probs)."""
    page_url = f"https://www.spc.noaa.gov/products/watch/ww{watch_number}.html"
    html = await http_get_text(page_url)

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
    prob_url = (
        f"https://www.spc.noaa.gov/products/watch/ww{watch_number}_prob.html"
    )
    prob_html = await http_get_text(prob_url)
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

    return image_url, text_summary, probs


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
        watch_label = (
            "Tornado Watch" if is_tornado else "Severe Thunderstorm Watch"
        )
        color = discord.Color.red() if is_tornado else discord.Color.orange()

        embed = discord.Embed(
            title=(
                f"{'🌪️' if is_tornado else '⛈️'}  "
                f"{watch_label} #{int(watch_num)}"
            ),
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        if expires:
            embed.add_field(
                name="Expires",
                value=f"<t:{int(expires.timestamp())}:R>",
                inline=True,
            )
        if text_summary:
            embed.add_field(
                name="Details", value=text_summary[:1024], inline=False
            )
        if probs:
            embed.add_field(
                name="Probabilities", value=probs[:1024], inline=False
            )

        embed.set_footer(
            text=(
                f"Watch {self.index + 1} of {len(self.watch_data)} "
                f"· SPC Watch Monitor"
            )
        )
        if cache_path:
            embed.set_image(url=f"attachment://watch_{watch_num}.gif")
        return embed

    def build_files(self):
        watch_num, _, _, _, _, cache_path = self.watch_data[self.index]
        if cache_path:
            return [
                discord.File(
                    cache_path, filename=f"watch_{watch_num}.gif"
                )
            ]
        return []

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
            except Exception:
                pass


async def _execute_watches(interaction: discord.Interaction):
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

    watch_data = []
    for watch_num, nws_info in nws_watches.items():
        image_url, text_summary, probs = await fetch_watch_details(watch_num)
        cache_path = None
        if image_url:
            cache_path, _, _ = await download_single_image(
                image_url, MANUAL_CACHE_FILE, manual_cache
            )
        watch_data.append(
            (watch_num, nws_info, image_url, text_summary, probs, cache_path)
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
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._watches_backoff = TaskBackoff("auto_post_watches")
        self.auto_post_watches.start()

    def cog_unload(self):
        self.auto_post_watches.cancel()

    @tasks.loop(minutes=2)
    async def auto_post_watches(self):
        await self.bot.wait_until_ready()
        if self._watches_backoff.should_skip():
            return
        channel = self.bot.get_channel(SPC_CHANNEL_ID)
        if not channel:
            logger.warning("SPC channel not found for auto_post_watches")
            return

        try:
            nws_watches = await fetch_active_watches_nws()
            if nws_watches is None:
                logger.warning(
                    "[WATCH] NWS API fetch failed — skipping cycle, active set unchanged"
                )
                return
            now_utc = datetime.now(timezone.utc)

            # ── Cancellations ──────────────────────────────────────────────
            for watch_num, info in list(active_watches.items()):
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

                active_watches.pop(watch_num, None)
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
                    logger.error(
                        f"[WATCH] Failed to send cancellation "
                        f"for #{watch_num}: {e}"
                    )
                    active_watches[watch_num] = info

            # ── New watches ────────────────────────────────────────────────
            for watch_num, nws_info in nws_watches.items():
                active_watches[watch_num] = nws_info
                if watch_num in posted_watches:
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
                        image_url, AUTO_CACHE_FILE, auto_cache
                    )

                embed = discord.Embed(
                    title=(
                        f"{'🌪️' if is_tornado else '⛈️'}  "
                        f"{watch_label} #{int(watch_num)}"
                    ),
                    color=color,
                    timestamp=now_utc,
                )
                if expires:
                    embed.add_field(
                        name="Expires",
                        value=f"<t:{int(expires.timestamp())}:R>",
                        inline=True,
                    )
                if text_summary:
                    embed.add_field(
                        name="Details",
                        value=text_summary[:1024],
                        inline=False,
                    )
                if probs:
                    embed.add_field(
                        name="Probabilities",
                        value=probs[:1024],
                        inline=False,
                    )
                embed.set_footer(text="SPC Watch Monitor")
                if cache_path:
                    embed.set_image(
                        url=f"attachment://watch_{watch_num}.gif"
                    )

                try:
                    files = (
                        [
                            discord.File(
                                cache_path,
                                filename=f"watch_{watch_num}.gif",
                            )
                        ]
                        if cache_path
                        else []
                    )
                    await channel.send(embed=embed, files=files)
                    posted_watches.add(watch_num)
                    asyncio.create_task(add_posted_watch(str(watch_num)))
                    asyncio.create_task(prune_posted_watches())
                    last_post_times["watch"] = datetime.now(timezone.utc)
                    logger.info(f"[WATCH] Posted watch #{watch_num}")
                except discord.HTTPException as e:
                    logger.error(
                        f"[WATCH] Discord send failed for #{watch_num}: {e}"
                    )

            # Prune tracked watches
            prune_tracked_set(
                posted_watches, MAX_TRACKED_WATCHES, WATCH_CACHE_FILE
            )

            self._watches_backoff.success()
        except Exception as e:
            logger.error(
                f"[WATCH] Unexpected error in auto_post_watches: {e}",
            )
            await self._watches_backoff.failure(self.bot)
            logger.error(
                f"[WATCH] Unexpected error in auto_post_watches: {e}",
                exc_info=True,
            )

    @auto_post_watches.after_loop
    async def after_watches_loop(self):
        if self.auto_post_watches.is_being_cancelled():
            return
        task = self.auto_post_watches.get_task()
        exc = task.exception() if task else None
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
        await _execute_watches(interaction)

    @discord.app_commands.command(
        name="ww",
        description="Show all currently active SPC watches",
    )
    async def ww_slash(self, interaction: discord.Interaction):
        await _execute_watches(interaction)


async def setup(bot: commands.Bot):
    await bot.add_cog(WatchesCog(bot))
