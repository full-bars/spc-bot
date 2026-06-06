import discord
from discord import app_commands
from discord.ext import commands
import logging

from config import GEMINI_API_KEY
from utils.ai import generate_morning_briefing
from utils.http import http_get_text

logger = logging.getLogger("spc_bot.ai_summaries")


class AISummariesCog(commands.Cog, name="AI Summaries"):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return

        custom_id = interaction.data.get("custom_id", "")
        if custom_id.startswith("ai_md:"):
            md_num = custom_id.split(":")[1]
            await self._handle_md_summary(interaction, md_num)
        elif custom_id.startswith("ai_outlook:"):
            day_str = custom_id.split(":")[1]
            await self._handle_outlook_summary(interaction, day_str)

    async def _handle_md_summary(self, interaction: discord.Interaction, md_num: str):
        await interaction.response.defer(thinking=True)

        # Check Redis cache first
        from utils.state_store import get_product_cache, set_product_cache, _get_redis_client
        import datetime

        today_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")

        cache_key = f"ai_summary_md_{md_num}"
        summary = await get_product_cache(cache_key)

        redis = _get_redis_client()
        if summary:
            if redis:
                await redis.incr(f"ai_cache_hits_{today_str}")
        else:
            if redis:
                await redis.incr(f"ai_api_calls_{today_str}")
            # Not cached, we need to generate it. Check if we have the raw text cached
            raw_text = await get_product_cache(f"md_{str(md_num).zfill(4)}")
            if not raw_text:
                # Fallback to fetching from SPC
                url = f"https://www.spc.noaa.gov/products/md/md{str(md_num).zfill(4)}.html"
                from utils.http import http_get_text

                html = await http_get_text(url)
                if html and "<pre>" in html:
                    raw_text = html.split("<pre>")[1].split("</pre>")[0]

            if raw_text:
                from utils.ai import summarize_md

                summary = await summarize_md(raw_text)
                if summary:
                    await set_product_cache(cache_key, summary, ttl=86400 * 3)  # 3 days

        if not summary:
            await interaction.followup.send("Failed to generate AI summary.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"🪄 AI Summary (MD #{md_num})",
            description=summary,
            color=discord.Color.purple(),
        )
        await interaction.followup.send(embed=embed)

    async def _handle_outlook_summary(self, interaction: discord.Interaction, day: str):
        await interaction.response.defer(thinking=True)

        from utils.state_store import get_product_cache, set_product_cache, _get_redis_client
        import datetime

        today_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")

        cache_key = f"ai_summary_outlook_day{day}"
        summary = await get_product_cache(cache_key)

        redis = _get_redis_client()
        if summary:
            if redis:
                await redis.incr(f"ai_cache_hits_{today_str}")
        else:
            if redis:
                await redis.incr(f"ai_api_calls_{today_str}")
            # Need to generate it
            url_map = {
                "1": "https://www.spc.noaa.gov/products/outlook/day1otlk.html",
                "2": "https://www.spc.noaa.gov/products/outlook/day2otlk.html",
                "3": "https://www.spc.noaa.gov/products/outlook/day3otlk.html",
                "48": "https://www.spc.noaa.gov/products/exper/day4-8/day48prob.html",
            }
            url = url_map.get(day)
            if not url:
                await interaction.followup.send("Invalid outlook day.", ephemeral=True)
                return

            from utils.http import http_get_text

            raw_text = await http_get_text(url)
            if raw_text:
                from utils.ai import summarize_outlook

                summary = await summarize_outlook(raw_text, day)
                if summary:
                    await set_product_cache(cache_key, summary, ttl=86400)  # 1 day

        if not summary:
            await interaction.followup.send("Failed to generate AI analysis.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"🪄 AI Analysis (Day {day} Outlook)",
            description=summary,
            color=discord.Color.blue(),
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="dailybriefing", description="Generate an AI-powered morning severe weather briefing."
    )
    async def daily_briefing(self, interaction: discord.Interaction):
        if not GEMINI_API_KEY:
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
            await interaction.followup.send("Failed to generate morning briefing.", ephemeral=True)
            return

        embed = discord.Embed(
            title="🌅 Morning Severe Weather Briefing",
            description=briefing,
            color=discord.Color.yellow(),
        )
        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(AISummariesCog(bot))
