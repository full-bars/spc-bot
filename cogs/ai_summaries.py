import asyncio
import json
import re
from typing import Any
import discord
from discord import app_commands
from discord.ext import commands
import logging

from config import GEMINI_API_KEY, OPENCODE_API_KEY
from utils.ai import generate_morning_briefing
from utils.http import http_get_text

logger = logging.getLogger("spc_bot.ai_summaries")


class RegionalAnalysisView(discord.ui.View):
    """Persistent paginated view for regional analysis.

    Uses timeout=None and explicit custom_ids encoding day+page.
    Button clicks are handled via on_interaction in AISummariesCog,
    so pagination survives View timeouts and bot restarts.
    """

    def __init__(self, day: str, regions: list[dict], current_page: int = 0):
        super().__init__(timeout=None)
        self.day = day
        self.regions = regions
        self.current_page = current_page
        self._rebuild_children()

    def _rebuild_children(self):
        self.clear_items()
        prev_btn = discord.ui.Button(
            label="◀ Previous",
            style=discord.ButtonStyle.secondary,
            custom_id=f"ai_region_prev:{self.day}:{self.current_page}",
            disabled=self.current_page == 0,
        )
        self.add_item(prev_btn)
        next_btn = discord.ui.Button(
            label="Next ▶",
            style=discord.ButtonStyle.secondary,
            custom_id=f"ai_region_next:{self.day}:{self.current_page}",
            disabled=self.current_page >= len(self.regions) - 1,
        )
        self.add_item(next_btn)

    def _create_embed(self) -> discord.Embed:
        region_data = self.regions[self.current_page]
        region_name = region_data.get("region", "Unknown Region")

        embed = discord.Embed(
            title=f"🪄 AI Analysis (Day {self.day} Outlook)",
            description=f"**Region {self.current_page + 1} of {len(self.regions)}: {region_name}**",
            color=discord.Color.blue(),
        )

        sections = [
            ("Favorable Factors", "favorable_factors"),
            ("Fail Modes", "fail_modes"),
            ("Primary Hazards & Storm Mode", "hazards_mode"),
            ("Timing", "timing"),
            ("Geographic Focus & Confidence", "confidence"),
        ]

        for label, key in sections:
            val = region_data.get(key, "N/A")
            if len(val) > 1024:
                val = val[:1021] + "..."
            embed.add_field(name=label, value=val, inline=False)

        embed.set_footer(text="Navigate between regions using the buttons below.")
        return embed


async def _get_regions_from_redis(day: str) -> list[dict] | None:
    """Fetch cached regions data from Redis for pagination."""
    try:
        from utils.state_store import get_product_cache

        raw = await get_product_cache(f"ai_regions_day{day}")
        if raw:
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return None
    except Exception as e:
        logger.debug(f"Failed to fetch regions from Redis for day {day}: {e}")
    return None


async def ensure_md_summary(md_num: str, raw_text: str = None) -> str | None:
    """Fetches or generates an AI summary for a Mesoscale Discussion (MD)."""
    try:
        from utils.state_store import get_product_cache, set_product_cache, _get_redis_client
        import datetime

        today_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
        cache_key = f"ai_summary_md_{md_num}"

        # 1. Check Cache
        summary = await get_product_cache(cache_key)
        redis = _get_redis_client()

        if summary:
            if redis:
                await redis.incr(f"ai_cache_hits_{today_str}")
            return summary

        # 2. Not cached, generate it.
        if redis:
            await redis.incr(f"ai_api_calls_{today_str}")

        if not raw_text:
            # Check if we have the raw text cached
            raw_text = await get_product_cache(f"md_{str(md_num).zfill(4)}")
            if not raw_text:
                # Fallback to fetching from SPC
                url = f"https://www.spc.noaa.gov/products/md/md{str(md_num).zfill(4)}.html"
                html = await http_get_text(url)
                if html:
                    if "<pre>" in html.lower():
                        parts = re.split(r"<pre[^>]*>", html, flags=re.IGNORECASE)
                        if len(parts) > 1:
                            raw_text = parts[1].split("</pre>")[0]
                        else:
                            raw_text = html
                    else:
                        raw_text = html

        if raw_text:
            raw_text = re.sub(r"<[^>]*>", "", raw_text).strip()
            from utils.ai import summarize_md

            summary = await summarize_md(raw_text)
            if summary:
                await set_product_cache(cache_key, summary, ttl=86400 * 3)  # 3 days
                return summary

        return None
    except Exception as e:
        logger.error(f"Error in ensure_md_summary for MD {md_num}: {e}")
        return None


async def _get_environmental_context(lat: float, lon: float, bot: commands.Bot) -> dict:
    """Gather SPC products and regional context near a specific location."""
    context = {}
    from utils.state_store import get_product_cache
    from cogs.sounding_utils import haversine

    # 1. Outlook
    outlook_raw = await get_product_cache("ai_summary_outlook_day1")
    if outlook_raw:
        try:
            context["day1_outlook"] = json.loads(outlook_raw)
        except Exception:
            pass

    # 2. Active MDs
    active_mds = []
    for md_num in list(bot.state.active_mds):
        # MDs are typically large enough that we can just include the AI summary if it exists
        summary = await get_product_cache(f"ai_summary_md_{md_num}")
        if summary:
            active_mds.append(f"MD #{md_num}: {summary}")
    if active_mds:
        context["active_mds"] = active_mds

    # 3. Active Watches
    applicable_watches = []
    from utils.db import get_watch_centroid_cache

    for watch_num, _info in list(bot.state.active_watches.items()):
        centroid = await get_watch_centroid_cache(watch_num)
        if centroid:
            dist = haversine(lat, lon, centroid[0], centroid[1])
            if dist < 500:  # 500km radius
                watch_summary = await get_product_cache(f"watch_{watch_num.zfill(4)}")
                if watch_summary:
                    # Clean up the watch summary for the prompt
                    clean_ws = watch_summary.replace("**", "").split("Threats:")[0].strip()
                    applicable_watches.append(f"Watch #{watch_num}: {clean_ws}")
    if applicable_watches:
        context["active_watches"] = applicable_watches

    return context


# Inflight lock to prevent duplicate AI calls for the same cache_key
_sounding_inflight: dict[str, asyncio.Event] = {}


async def ensure_sounding_summary(
    cache_key: str,
    raw_text: str = None,
    lat: float = None,
    lon: float = None,
    location_name: str = "Unknown",
    bot: commands.Bot = None,
) -> str | None:
    """Fetches or generates an AI summary for a sounding/hodograph."""
    try:
        from utils.state_store import get_product_cache, set_product_cache, _get_redis_client
        import datetime

        today_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")

        # 1. Check Cache
        summary = await get_product_cache(cache_key)
        redis = _get_redis_client()

        if summary:
            if redis:
                await redis.incr(f"ai_cache_hits_{today_str}")
            return summary

        # 2. Inflight dedup — if another task is already generating for this
        #    cache_key, wait for it instead of making a duplicate AI call.
        event = _sounding_inflight.get(cache_key)
        if event:
            try:
                await asyncio.wait_for(event.wait(), timeout=60)
            except asyncio.TimeoutError:
                return None
            summary = await get_product_cache(cache_key)
            if summary:
                if redis:
                    await redis.incr(f"ai_cache_hits_{today_str}")
                return summary
        else:
            event = asyncio.Event()
            _sounding_inflight[cache_key] = event

        # 3. Not cached, generate it.
        if not raw_text:
            # Try fetching raw_text from cache (e.g., stored by hodograph/sounding creation)
            raw_text = await get_product_cache(f"raw_text_{cache_key}")
            if not raw_text:
                return None

        if redis:
            await redis.incr(f"ai_api_calls_{today_str}")

        # Attempt to resolve lat/lon from cache_key if missing
        if lat is None or lon is None:
            try:
                from cogs.sounding_utils import resolve_location

                if cache_key.startswith("hodo_"):
                    # hodo_{site}_{now_str}
                    site = cache_key.split("_")[1]
                    lat, lon, _ = await resolve_location(site)
                    location_name = f"radar site {site}"
                elif cache_key.startswith("acars_"):
                    # acars_{airport}_{year}{month}{day}_{acars_hour}
                    parts = cache_key.split("_")
                    sid = parts[1]
                    lat, lon, _ = await resolve_location(sid)
                    location_name = f"aircraft profile near {sid}"
                elif "_" in cache_key:
                    # {station_id}_{year}{month}{day}_{hour}
                    sid = cache_key.split("_")[0]
                    lat, lon, _ = await resolve_location(sid)
                    location_name = sid
            except Exception:
                pass

        outlook_context = None
        md_context = None
        watch_context = None
        sounding_context = None

        if lat is not None and lon is not None and bot is not None:
            ctx_data = await _get_environmental_context(lat, lon, bot)
            outlook_context = ctx_data.get("day1_outlook")
            md_context = ctx_data.get("active_mds")
            watch_context = ctx_data.get("active_watches")

            # If this is a radar hodograph, attempt to fetch nearby sounding thermodynamics
            if cache_key.startswith("hodo_"):
                try:
                    from cogs.sounding_utils import (
                        get_raob_stations,
                        find_nearest_stations,
                        get_available_sounding_times,
                        fetch_sounding,
                        get_sounding_params_text,
                    )

                    stations_df = await get_raob_stations()
                    nearest = find_nearest_stations(lat, lon, stations_df, n=3)
                    nearest = [s for s in nearest if s.get("icao") or s.get("wmo")]

                    for station in nearest:
                        sid = station.get("icao") or station.get("wmo")
                        avail = await get_available_sounding_times(sid, hours_back=12)
                        if avail:
                            y, mo, d, h = avail[0]
                            data = await fetch_sounding(sid, y, mo, d, h)
                            if data:
                                sounding_context = await get_sounding_params_text(data)
                                if sounding_context:
                                    sounding_context = (
                                        f"Station: {station['name']} ({sid})\n{sounding_context}"
                                    )
                                    break
                except Exception as e:
                    logger.debug(f"Failed to fetch nearby sounding for hodo context: {e}")

        from utils.ai import summarize_sounding, summarize_sounding_enhanced

        if any([outlook_context, md_context, watch_context, sounding_context]):
            summary = await summarize_sounding_enhanced(
                raw_text,
                location_name=location_name,
                outlook_context=outlook_context,
                md_context=md_context,
                watch_context=watch_context,
                sounding_context=sounding_context,
            )
        else:
            summary = await summarize_sounding(raw_text)

        if summary:
            await set_product_cache(cache_key, summary, ttl=86400)  # 1 day
            return summary

        return None
    except Exception as e:
        logger.error(f"Error in ensure_sounding_summary for {cache_key}: {e}")
        return None
    finally:
        if cache_key in _sounding_inflight:
            _sounding_inflight[cache_key].set()
            del _sounding_inflight[cache_key]


async def _fetch_outlook_text(day: str) -> str | None:
    """Fetch the raw outlook text for a given day (1, 2, 3, 48)."""
    url_map = {
        "1": "https://www.spc.noaa.gov/products/outlook/day1otlk.txt",
        "2": "https://www.spc.noaa.gov/products/outlook/day2otlk.txt",
        "3": "https://www.spc.noaa.gov/products/outlook/day3otlk.txt",
        "48": "https://www.spc.noaa.gov/products/exper/day4-8/",
    }
    url = url_map.get(day)
    if not url:
        return None
    html = await http_get_text(url)
    if not html:
        return None

    if url.endswith(".txt"):
        raw_text = html
    elif "<pre>" in html.lower():
        parts = re.split(r"<pre[^>]*>", html, flags=re.IGNORECASE)
        if len(parts) > 1:
            raw_text = parts[1].split("</pre>")[0]
        else:
            raw_text = html
    else:
        raw_text = html

    raw_text = re.sub(r"<[^>]*>", "", raw_text).strip()
    return raw_text


async def ensure_outlook_summary(day: str, raw_text: str = None) -> Any | None:
    """Fetches or generates an AI analysis for an SPC Outlook."""
    try:
        from utils.state_store import get_product_cache, set_product_cache, _get_redis_client
        from utils.change_detection import calculate_hash_bytes
        import datetime

        today_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")

        # 1. Fetch raw text if not provided (to determine cache key)
        if not raw_text:
            raw_text = await _fetch_outlook_text(day)

        if not raw_text:
            return None

        # 2. Version the cache key based on content hash
        text_hash = calculate_hash_bytes(raw_text.encode())
        cache_key = f"ai_summary_outlook_day{day}_{text_hash[:16]}"

        summary_raw = await get_product_cache(cache_key)
        redis = _get_redis_client()

        if summary_raw:
            if redis:
                await redis.incr(f"ai_cache_hits_{today_str}")
            try:
                return json.loads(summary_raw)
            except json.JSONDecodeError:
                return summary_raw

        # 3. Not cached, generate it
        if redis:
            await redis.incr(f"ai_api_calls_{today_str}")

        from utils.ai import summarize_outlook

        summary = await summarize_outlook(raw_text)
        if summary:
            # Store with long TTL, it's content-addressed
            await set_product_cache(cache_key, json.dumps(summary), ttl=86400 * 7)
            # Also store under day-based key for pagination after timeouts/restarts
            await set_product_cache(f"ai_regions_day{day}", json.dumps(summary), ttl=86400 * 7)
            return summary

        return None
    except Exception as e:
        logger.error(f"Error in ensure_outlook_summary for Day {day}: {e}")
        return None


async def _resolve_message_thread(
    message: discord.Message | None,
) -> discord.Thread | None:
    """Resolve the thread attached to a message, with API fallback for cache misses."""
    if not message:
        return None
    thread = message.thread
    if thread is not None:
        return thread
    try:
        return await message.fetch_thread()
    except (discord.NotFound, discord.HTTPException):
        return None


async def autopost_outlook_summary(channel: discord.abc.Messageable, day: str, delay: float = 0.5):
    """Wait for outlook summary to be ready and post it as a follow-up message."""
    try:
        import asyncio
        from utils.state_store import (
            get_previous_outlook_text,
            set_previous_outlook_text,
        )

        await asyncio.sleep(delay)

        raw_text = await _fetch_outlook_text(day)
        if not raw_text:
            logger.warning(f"[Day {day}] Could not fetch outlook text for AI summary")
            return

        # Check for CORR (correction) marker
        is_correction = bool(re.search(r"CORR\s*\d+", raw_text, re.IGNORECASE))

        if is_correction:
            previous_text = await get_previous_outlook_text(day)
            if previous_text:
                # Generate revision summary
                from utils.ai import summarize_outlook_revision

                revision_summary = await summarize_outlook_revision(previous_text, raw_text)
                if revision_summary:
                    # Extract CORR number for display
                    corr_match = re.search(r"CORR\s*(\d+)", raw_text, re.IGNORECASE)
                    corr_num = corr_match.group(1) if corr_match else "1"

                    embed = discord.Embed(
                        title=f"🔄 SPC Day {day} Outlook — Updated — CORR {corr_num}",
                        description=revision_summary,
                        color=discord.Color.orange(),
                    )
                    await channel.send(embed=embed)
                    # Update stored text for next comparison
                    await set_previous_outlook_text(day, raw_text)
                    logger.info(f"[Day {day}] Posted CORR {corr_num} revision summary")
                    return

        # Not a correction, or no previous text — generate full summary
        summary = await ensure_outlook_summary(day, raw_text)
        if not summary:
            logger.warning(f"[Day {day}] AI summary returned None (text fetch or API call failed)")
            return

        # Store current text as previous for next comparison
        await set_previous_outlook_text(day, raw_text)

        if isinstance(summary, list) and len(summary) > 0:
            view = RegionalAnalysisView(day, summary, current_page=0)
            await channel.send(embed=view._create_embed(), view=view)
        else:
            embed = discord.Embed(
                title=f"🪄 AI Analysis (Day {day} Outlook)",
                description=str(summary),
                color=discord.Color.blue(),
            )
            await channel.send(embed=embed)
    except Exception as e:
        logger.exception(f"Error autoposting outlook summary for Day {day}: {e}")


async def autopost_md_summary(
    md_msg: discord.Message,
    md_num: str,
    delay: float = 0.5,
):
    """Wait for MD summary to be ready and post it in a thread on the MD message."""
    try:
        import asyncio

        await asyncio.sleep(delay)
        summary = await ensure_md_summary(md_num)
        if not summary:
            logger.warning(
                f"[MD #{md_num}] AI summary returned None (text fetch or API call failed)"
            )
            return

        embed = discord.Embed(
            title=f"🪄 AI Summary (MD #{md_num})",
            description=summary,
            color=discord.Color.purple(),
        )

        thread = None
        try:
            thread = await md_msg.create_thread(
                name=f"MD #{md_num}",
                auto_archive_duration=1440,
            )
        except Exception as e:
            logger.warning(f"[MD #{md_num}] Failed to create thread: {e}")

        if thread:
            await thread.send(embed=embed)
        else:
            await md_msg.channel.send(embed=embed)
    except Exception as e:
        logger.exception(f"Error autoposting MD summary for MD {md_num}: {e}")


async def autopost_sounding_summary(
    sounding_msg: discord.Message,
    cache_key: str,
    sounding_label: str = None,
    delay: float = 0.5,
    raw_text: str = None,
    lat: float = None,
    lon: float = None,
    location_name: str = "Unknown",
):
    """Wait for sounding summary to be ready and post it in a thread on the sounding message."""
    try:
        import asyncio
        from utils.state_store import set_product_cache

        await asyncio.sleep(delay)
        summary = await ensure_sounding_summary(
            cache_key,
            raw_text=raw_text,
            lat=lat,
            lon=lon,
            location_name=location_name,
        )
        if not summary:
            logger.warning(
                f"[Sounding {cache_key}] AI summary returned None (text fetch or API call failed)"
            )
            return

        title = "🪄 AI Analysis (Environment)"
        if sounding_label:
            title = f"{title}\n{sounding_label}"

        embed = discord.Embed(
            title=title,
            description=summary,
            color=discord.Color.teal(),
        )

        thread = None
        try:
            thread = await sounding_msg.create_thread(
                name=f"Sounding: {sounding_label if sounding_label else location_name}"[:100],
                auto_archive_duration=1440,
            )
        except Exception as e:
            logger.warning(f"[Sounding {cache_key}] Failed to create thread: {e}")

        if thread:
            msg = await thread.send(embed=embed)
        else:
            msg = await sounding_msg.reply(embed=embed)

        await set_product_cache(f"sounding_summary_message_{cache_key}", msg.jump_url)
    except Exception as e:
        logger.exception(f"Error autoposting sounding summary for {cache_key}: {e}")


class AISummariesCog(commands.Cog, name="AI Summaries"):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return

        if not interaction.data:
            return

        # For component interactions, data is a dict containing custom_id
        custom_id = interaction.data.get("custom_id")
        if not isinstance(custom_id, str):
            return

        if custom_id.startswith("ai_md:"):
            md_num = custom_id.split(":")[1]
            await self._handle_md_summary(interaction, md_num)
        elif custom_id.startswith("ai_outlook:"):
            day_str = custom_id.split(":")[1]
            await self._handle_outlook_summary(interaction, day_str)
        elif custom_id.startswith("ai_snd:"):
            cache_key = custom_id.split(":", 1)[1]
            await self._handle_sounding_summary(interaction, cache_key)
        elif custom_id.startswith("ai_region_prev:") or custom_id.startswith("ai_region_next:"):
            parts = custom_id.split(":")
            day_str = parts[1]
            target_page = int(parts[2])
            if custom_id.startswith("ai_region_prev:"):
                target_page -= 1
            else:
                target_page += 1
            await self._handle_region_pagination(interaction, day_str, target_page)

    async def _handle_sounding_summary(self, interaction: discord.Interaction, cache_key: str):
        try:
            from utils.state_store import get_product_cache

            await interaction.response.defer(thinking=True)

            # Check if summary was already auto-posted
            jump_url = await get_product_cache(f"sounding_summary_message_{cache_key}")
            if jump_url:
                await interaction.followup.send(
                    f"AI analysis already posted! [View it here]({jump_url})",
                    ephemeral=True,
                )
                return

            summary = await ensure_sounding_summary(cache_key, bot=self.bot)

            if not summary:
                # Try Gemini directly (fast) as fallback
                try:
                    raw_text = await get_product_cache(f"raw_text_{cache_key}")
                    if raw_text:
                        from utils.ai import call_gemini

                        is_hodo = cache_key.startswith("hodo_")
                        if is_hodo:
                            prompt = (
                                "You are an expert severe weather meteorologist analyzing a "
                                f"radar-derived VAD wind profile (hodograph). "
                                "Discuss the wind shear profile, low-level shear/SRH, and "
                                "what they imply about storm organization. Limit to 3 sentences.\n\n"
                                f"DATA:\n{raw_text}"
                            )
                        else:
                            prompt = (
                                "You are an expert severe weather meteorologist. "
                                "Provide a concise 3-4 sentence plain-English summary "
                                "of this atmospheric environment.\n\n"
                                f"DATA:\n{raw_text}"
                            )

                        gemini_result = await call_gemini(prompt)
                        if gemini_result:
                            from utils.state_store import set_product_cache

                            summary = gemini_result
                            await set_product_cache(cache_key, summary, ttl=86400)
                except Exception as e:
                    logger.debug(f"Gemini fallback failed for {cache_key}: {e}")

            if not summary:
                await interaction.followup.send(
                    "AI analysis is still being generated. Click the button again in a moment.",
                    ephemeral=True,
                )
                return

            embed = discord.Embed(
                title="🪄 AI Analysis (Environment)",
                description=summary,
                color=discord.Color.teal(),
            )

            thread = await _resolve_message_thread(interaction.message)
            if thread:
                await thread.send(embed=embed)
                await interaction.followup.send(
                    f"AI analysis posted in the [thread]({thread.jump_url})!",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.exception(f"Error in _handle_sounding_summary: {e}")
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(
                        "An error occurred while generating AI analysis.", ephemeral=True
                    )
                else:
                    await interaction.response.send_message(
                        "An error occurred while generating AI analysis.", ephemeral=True
                    )
            except Exception:
                pass

    async def _handle_md_summary(self, interaction: discord.Interaction, md_num: str):
        try:
            await interaction.response.defer(thinking=True)
            summary = await ensure_md_summary(md_num)

            if not summary:
                await interaction.followup.send("Failed to generate AI summary.", ephemeral=True)
                return

            embed = discord.Embed(
                title=f"🪄 AI Summary (MD #{md_num})",
                description=summary,
                color=discord.Color.purple(),
            )

            thread = await _resolve_message_thread(interaction.message)
            if thread:
                await thread.send(embed=embed)
                await interaction.followup.send(
                    f"AI analysis posted in the [thread]({thread.jump_url})!",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.exception(f"Error in _handle_md_summary: {e}")
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(
                        "An error occurred while generating AI summary.", ephemeral=True
                    )
                else:
                    await interaction.response.send_message(
                        "An error occurred while generating AI summary.", ephemeral=True
                    )
            except Exception:
                pass

    async def _handle_outlook_summary(self, interaction: discord.Interaction, day: str):
        try:
            await interaction.response.defer(thinking=True)
            summary = await ensure_outlook_summary(day)

            if not summary:
                await interaction.followup.send("Failed to generate AI analysis.", ephemeral=True)
                return

            if isinstance(summary, list) and len(summary) > 0:
                view = RegionalAnalysisView(day, summary, current_page=0)
                embed = view._create_embed()
            else:
                embed = discord.Embed(
                    title=f"🪄 AI Analysis (Day {day} Outlook)",
                    description=str(summary),
                    color=discord.Color.blue(),
                )
                view = None

            thread = await _resolve_message_thread(interaction.message)
            if thread:
                await thread.send(embed=embed, view=view)
                await interaction.followup.send(
                    f"AI analysis posted in the [thread]({thread.jump_url})!",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(embed=embed, view=view)
        except Exception as e:
            logger.exception(f"Error in _handle_outlook_summary: {e}")
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(
                        "An error occurred while generating AI analysis.", ephemeral=True
                    )
                else:
                    await interaction.response.send_message(
                        "An error occurred while generating AI analysis.", ephemeral=True
                    )
            except Exception:
                pass

    async def _handle_region_pagination(
        self, interaction: discord.Interaction, day: str, target_page: int
    ):
        """Handle pagination buttons for regional analysis views."""
        try:
            regions = await _get_regions_from_redis(day)
            if not regions:
                await interaction.response.send_message(
                    "Analysis data has expired. Click the 'AI Analysis' button to regenerate.",
                    ephemeral=True,
                )
                return

            if target_page < 0 or target_page >= len(regions):
                await interaction.response.send_message(
                    "Page out of range. The analysis may have been updated.",
                    ephemeral=True,
                )
                return

            view = RegionalAnalysisView(day, regions, current_page=target_page)
            await interaction.response.edit_message(embed=view._create_embed(), view=view)
        except Exception as e:
            logger.exception(f"Error in _handle_region_pagination: {e}")
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "An error occurred while navigating. Click 'AI Analysis' to regenerate.",
                        ephemeral=True,
                    )
            except Exception:
                pass

    @app_commands.command(
        name="dailybriefing", description="Generate an AI-powered morning severe weather briefing."
    )
    async def daily_briefing(self, interaction: discord.Interaction):
        try:
            if not GEMINI_API_KEY and not OPENCODE_API_KEY:
                await interaction.response.send_message(
                    "AI features are not configured.", ephemeral=True
                )
                return

            await interaction.response.defer(thinking=True)

            # Fetch Day 1 Outlook
            url = "https://www.spc.noaa.gov/products/outlook/day1otlk.html"
            html = await http_get_text(url)
            if not html:
                await interaction.followup.send("Failed to fetch Day 1 outlook.", ephemeral=True)
                return

            # Fetch active watches from bot state
            active_watches = self.bot.state.active_watches
            watch_text = "None"
            if active_watches:
                watch_text = "\n".join([f"Watch #{w}" for w in active_watches])

            from utils.state_store import _get_redis_client
            import datetime

            today_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
            redis = _get_redis_client()
            if redis:
                await redis.incr(f"ai_api_calls_{today_str}")

            briefing = await generate_morning_briefing(html, watch_text)
            if not briefing:
                await interaction.followup.send(
                    "Failed to generate morning briefing.", ephemeral=True
                )
                return

            embed = discord.Embed(
                title="🌅 Morning Severe Weather Briefing",
                description=briefing,
                color=discord.Color.yellow(),
            )
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.exception(f"Error in daily_briefing: {e}")
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(
                        "An error occurred while generating morning briefing.", ephemeral=True
                    )
                else:
                    await interaction.response.send_message(
                        "An error occurred while generating morning briefing.", ephemeral=True
                    )
            except Exception:
                pass


async def setup(bot):
    await bot.add_cog(AISummariesCog(bot))
