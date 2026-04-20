# cogs/radar/__init__.py
"""NEXRAD Level 2 radar data downloader from NOAA AWS S3."""

from datetime import datetime, timedelta, timezone
import logging
import time

import discord
from discord.app_commands import Choice
from discord.ext import commands, tasks

from cogs.radar.downloads import cleanup_old_files, run_download, OUTPUT_DIR, CLEANUP_AGE_THRESHOLD
from cogs.radar.s3 import _s3, get_radar_sites as get_radar_sites
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
    @discord.app_commands.describe(
        sites="Site code(s) e.g. KICT or KICT KUEX (leave blank for interactive list)",
        time="Time preset — Choose 'Custom/Other' or leave blank for custom Z-to-Z range",
        count="Number of most recent files to download (overrides time)",
    )
    @discord.app_commands.choices(time=[
        Choice(name="Last 1 hour", value="1h"),
        Choice(name="Last 2 hours", value="2h"),
        Choice(name="Last 3 hours", value="3h"),
        Choice(name="Last 4 hours", value="4h"),
        Choice(name="Custom / Other (Z-to-Z, explicit, etc.)", value="custom"),
    ])
    async def download_slash(
        self,
        interaction: discord.Interaction,
        sites: str = None,
        time: Choice[str] = None,
        count: int = None,
    ):
        # No args — full interactive flow
        if not sites:
            await self._start_download_flow(interaction, interaction.user)
            return

        # Parse site codes — accept space or comma separated, uppercase
        import re
        raw_sites = re.split(r"[,\s]+", sites.strip().upper())
        radar_sites = [s for s in raw_sites if s]

        if not radar_sites:
            await interaction.response.send_message(
                "Please enter at least one valid radar site code.", ephemeral=True
            )
            return

        # count overrides time — go straight to N most recent download
        if count is not None:
            await interaction.response.defer()
            now = datetime.now(timezone.utc)
            await run_download(
                interaction, radar_sites, [],
                start_dt=None, end_dt=None,
                dates_to_query=[now],
                max_files=count,
            )
            return

        # Sites only (or 'custom' selected) — show time preset buttons
        if not time or time.value == "custom":
            from cogs.radar.views import TimeRangeView
            await interaction.response.defer()
            view = TimeRangeView(
                radar_sites=radar_sites,
                messages_to_delete=[],
                original_user=interaction.user,
            )
            embed = discord.Embed(
                title="AWS NEXRAD Data Downloader",
                description="Sites: **{}**\nSelect a time range:".format(", ".join(radar_sites)),
                color=0x0000FF,
            )
            await interaction.followup.send(embed=embed, view=view)
            return

        # Both sites and time — go straight to download
        await interaction.response.defer()
        now = datetime.now(timezone.utc)
        messages_to_delete = []

        hours = int(time.value.replace("h", ""))
        start_dt = now - timedelta(hours=hours)
        dates_to_query = [now]
        if start_dt.date() < now.date():
            dates_to_query.insert(0, now - timedelta(days=1))
        await run_download(
            interaction, radar_sites, messages_to_delete,
            start_dt=start_dt, end_dt=now,
            dates_to_query=dates_to_query,
        )

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
