# cogs/radar/__init__.py
"""NEXRAD Level 2 radar data downloader from NOAA AWS S3."""

import asyncio
import logging
import time

import aiohttp
import discord
from discord.ext import commands, tasks

from cogs.radar.downloads import cleanup_old_files, OUTPUT_DIR, CLEANUP_AGE_THRESHOLD
from cogs.radar.s3 import _s3, get_radar_sites
from cogs.radar.views import StartView

logger = logging.getLogger("spc_bot")


class RadarCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.periodic_cleanup.start()

    def cog_unload(self):
        self.periodic_cleanup.cancel()

    async def _start_download_flow(self, ctx_or_interaction, original_user):
        embed = discord.Embed(
            title="AWS NEXRAD Data Downloader",
            description="Click to start downloading radar data.",
            color=0x0000FF,
        )
        view = StartView(original_user=original_user)
        if isinstance(ctx_or_interaction, discord.Interaction):
            await ctx_or_interaction.response.send_message(
                embed=embed, view=view
            )
            msg = await ctx_or_interaction.original_response()
        else:
            msg = await ctx_or_interaction.send(embed=embed, view=view)
        view.messages_to_delete.append(msg)

    @commands.command(name="download")
    async def download_prefix(self, ctx):
        await self._start_download_flow(ctx, ctx.author)

    @commands.command(name="dl")
    async def dl_prefix(self, ctx):
        await self._start_download_flow(ctx, ctx.author)

    @discord.app_commands.command(
        name="download",
        description="Download NEXRAD Level 2 radar data from AWS S3",
    )
    async def download_slash(self, interaction: discord.Interaction):
        await self._start_download_flow(interaction, interaction.user)

    @discord.app_commands.command(
        name="downloaderstatus",
        description="Check AWS downloader and S3 latency",
    )
    async def downloaderstatus_slash(
        self, interaction: discord.Interaction
    ):
        await interaction.response.defer(ephemeral=True)
        ws_latency = round(self.bot.latency * 1000)
        ws_icon = (
            "🟢" if ws_latency < 100 else "🟡" if ws_latency < 200 else "🔴"
        )
        try:
            s3_start = time.time()
            async with _s3() as s3:
                await s3.list_objects_v2(
                    Bucket="unidata-nexrad-level2",
                    Prefix="2026/",
                    Delimiter="/",
                    MaxKeys=1,
                )
            s3_latency = round((time.time() - s3_start) * 1000)
            s3_icon = (
                "🟢"
                if s3_latency < 500
                else "🟡" if s3_latency < 1000 else "🔴"
            )
            s3_status = f"{s3_latency}ms"
        except Exception as e:
            s3_status = f"Error: {e}"
            s3_icon = "🔴"
        embed = discord.Embed(
            title="AWS NEXRAD Downloader Status", color=discord.Color.blue()
        )
        embed.add_field(
            name=f"{ws_icon} Discord WS Latency",
            value=f"`{ws_latency}ms`",
            inline=True,
        )
        embed.add_field(
            name=f"{s3_icon} S3 Bucket Latency",
            value=f"`{s3_status}`",
            inline=True,
        )
        embed.set_footer(text=f"Logged in as {self.bot.user}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @tasks.loop(hours=1)
    async def periodic_cleanup(self):
        await cleanup_old_files(OUTPUT_DIR, CLEANUP_AGE_THRESHOLD)


async def setup(bot: commands.Bot):
    await bot.add_cog(RadarCog(bot))
