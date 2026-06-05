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
