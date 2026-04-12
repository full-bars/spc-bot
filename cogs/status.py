# cogs/status.py
import logging
import socket
from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ext import commands, tasks

from config import MANUAL_CACHE_FILE, SCP_IMAGE_URLS, SPC_URLS, WPC_IMAGE_URLS
import utils.http as _http
from utils.cache import (
    check_all_urls_exist_parallel,
    download_images_parallel,
    download_single_image,
    format_timedelta,
    posted_mds,
    posted_watches,
)
from utils.spc_urls import get_spc_urls

logger = logging.getLogger("spc_bot")

BOT_START_TIME: Optional[datetime] = None


async def send_with_handling(source, content: str, file_paths=None):
    file_paths = file_paths or []
    files = []
    for fp in file_paths:
        try:
            files.append(discord.File(fp))
        except Exception as e:
            logger.warning(f"Could not create discord.File from {fp}: {e}")
    try:
        if (
            hasattr(source, "response")
            and getattr(source, "response", None) is not None
        ):
            await source.followup.send(content, files=files)
        else:
            await source.send(content, files=files)
    except discord.HTTPException as e:
        if e.status == 413:
            logger.error(f"Discord file size limit exceeded: {e}")
        else:
            logger.error(f"Discord send failed: {e}")
    except Exception as e:
        logger.error(f"Unexpected error sending message: {e}")


async def fetch_and_send_weather_images(
    source, urls, title: str, use_cached: bool = False
):
    if not await check_all_urls_exist_parallel(urls):
        msg = (
            f"{title.replace('**Latest ', '').replace('**', '')} "
            f"not currently available."
        )
        try:
            if (
                hasattr(source, "response")
                and getattr(source, "response", None) is not None
            ):
                await source.followup.send(msg)
            else:
                await source.send(msg)
        except Exception:
            pass
        return

    files = await download_images_parallel(
        urls, MANUAL_CACHE_FILE, self.bot.state.manual_cache, use_cached=use_cached
    )
    if files:
        await send_with_handling(source, title, file_paths=files)
    else:
        msg = (
            f"No new "
            f"{title.replace('**Latest ', '').replace('**', '').lower()} "
            f"available."
        )
        try:
            if (
                hasattr(source, "response")
                and getattr(source, "response", None) is not None
            ):
                await source.followup.send(msg)
            else:
                await source.send(msg)
        except Exception:
            pass


class StatusCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── Prefix commands ──────────────────────────────────────────────────

    @commands.command(name="scp_cmd")
    async def scp_prefix(self, ctx):
        await fetch_and_send_weather_images(
            ctx,
            SCP_IMAGE_URLS,
            "**Latest SCP Forecast Graphics**",
            use_cached=True,
        )

    @commands.command(name="spc1")
    async def spc1_prefix(self, ctx):
        urls = await get_spc_urls(1)
        await fetch_and_send_weather_images(
            ctx, urls, "**Latest SPC Day 1 Outlooks**", use_cached=True
        )

    @commands.command(name="spc2")
    async def spc2_prefix(self, ctx):
        urls = await get_spc_urls(2)
        await fetch_and_send_weather_images(
            ctx, urls, "**Latest SPC Day 2 Outlooks**", use_cached=True
        )

    @commands.command(name="spc3")
    async def spc3_prefix(self, ctx):
        urls = await get_spc_urls(3)
        await fetch_and_send_weather_images(
            ctx, urls, "**Latest SPC Day 3 Outlooks**", use_cached=True
        )

    @commands.command(name="spc48")
    async def spc48_prefix(self, ctx):
        await fetch_and_send_weather_images(
            ctx,
            SPC_URLS["48"],
            "**Latest SPC Day 4-8 Outlook**",
            use_cached=False,
        )

    @commands.command(name="wpc")
    async def wpc_prefix(self, ctx):
        await fetch_and_send_weather_images(
            ctx,
            WPC_IMAGE_URLS,
            "**WPC Excessive Rainfall Outlooks (Day 1-3)**",
            use_cached=False,
        )

    # ── Slash commands ───────────────────────────────────────────────────

    @discord.app_commands.command(
        name="scp", description="Get latest SCP Forecast Graphics"
    )
    @discord.app_commands.describe(fresh="Bypass cache and fetch the latest images directly")
    async def scp_slash(self, interaction: discord.Interaction, fresh: Optional[bool] = False):
        await interaction.response.defer()
        await fetch_and_send_weather_images(
            interaction,
            SCP_IMAGE_URLS,
            "**Latest SCP Forecast Graphics**",
            use_cached=not fresh,
        )

    @discord.app_commands.command(
        name="spc1", description="Get latest SPC Day 1 Outlooks"
    )
    @discord.app_commands.describe(fresh="Bypass cache and fetch the latest images directly")
    async def spc1_slash(self, interaction: discord.Interaction, fresh: Optional[bool] = False):
        await interaction.response.defer()
        urls = await get_spc_urls(1)
        await fetch_and_send_weather_images(
            interaction, urls, "**Latest SPC Day 1 Outlooks**", use_cached=not fresh
        )

    @discord.app_commands.command(
        name="spc2", description="Get latest SPC Day 2 Outlooks"
    )
    @discord.app_commands.describe(fresh="Bypass cache and fetch the latest images directly")
    async def spc2_slash(self, interaction: discord.Interaction, fresh: Optional[bool] = False):
        await interaction.response.defer()
        urls = await get_spc_urls(2)
        await fetch_and_send_weather_images(
            interaction, urls, "**Latest SPC Day 2 Outlooks**", use_cached=not fresh
        )

    @discord.app_commands.command(
        name="spc3", description="Get latest SPC Day 3 Outlooks"
    )
    @discord.app_commands.describe(fresh="Bypass cache and fetch the latest images directly")
    async def spc3_slash(self, interaction: discord.Interaction, fresh: Optional[bool] = False):
        await interaction.response.defer()
        urls = await get_spc_urls(3)
        await fetch_and_send_weather_images(
            interaction, urls, "**Latest SPC Day 3 Outlooks**", use_cached=not fresh
        )

    @discord.app_commands.command(
        name="spc48", description="Get latest SPC Day 4-8 Outlook"
    )
    async def spc48_slash(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await fetch_and_send_weather_images(
            interaction,
            SPC_URLS["48"],
            "**Latest SPC Day 4-8 Outlook**",
            use_cached=False,
        )

    @discord.app_commands.command(
        name="wpc", description="Get WPC Day 1-3 Rainfall Outlooks"
    )
    async def wpc_slash(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await fetch_and_send_weather_images(
            interaction,
            WPC_IMAGE_URLS,
            "**WPC Excessive Rainfall Outlooks (Day 1-3)**",
            use_cached=False,
        )

    @discord.app_commands.command(
        name="md",
        description="Show all currently active SPC Mesoscale Discussions",
    )
    async def md_slash(self, interaction: discord.Interaction):
        await interaction.response.defer()
        from cogs.mesoscale import fetch_latest_md_numbers, fetch_md_details

        md_numbers = await fetch_latest_md_numbers()
        if not md_numbers:
            await interaction.followup.send(
                "No active Mesoscale Discussions found."
            )
            return
        for md_num in md_numbers:
            image_url, summary, from_cache = await fetch_md_details(md_num)
            cache_path = None
            if image_url:
                cache_path, _, _ = await download_single_image(
                    image_url, MANUAL_CACHE_FILE, self.bot.state.manual_cache
                )
            md_page_url = (
                f"https://www.spc.noaa.gov/products/md/mcd{md_num}.html"
            )
            embed = discord.Embed(
                title=f"🌩️ SPC Mesoscale Discussion #{int(md_num)}",
                url=md_page_url,
                color=discord.Color.dark_orange(),
            )
            if summary:
                embed.description = summary
            if from_cache:
                embed.set_footer(
                    text=(
                        "⚠️ SPC website unreachable — "
                        "image served from cache"
                    )
                )
            files_to_send = []
            if cache_path:
                files_to_send.append(
                    discord.File(
                        cache_path, filename=f"md_{md_num}.png"
                    )
                )
                embed.set_image(url=f"attachment://md_{md_num}.png")
            try:
                await interaction.followup.send(
                    embed=embed, files=files_to_send
                )
            except discord.HTTPException as e:
                logger.error(f"[/md] Failed to send MD #{md_num}: {e}")

    @discord.app_commands.command(
        name="status",
        description="Show bot health, last post times, and current task state",
    )
    async def status_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        now = datetime.now(timezone.utc)
        hostname = socket.gethostname()
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            host_ip = s.getsockname()[0]
            s.close()
        except Exception:
            host_ip = "unknown"
        lines = [
            "```",
            "═══ SPC/SPC Bot Status ═══",
            f"Host           : {hostname} ({host_ip})",
            "",
        ]

        if BOT_START_TIME:
            uptime = now - BOT_START_TIME
            lines.append(f"Uptime         : {format_timedelta(uptime)}")
        else:
            lines.append("Uptime         : unknown")

        session_ok = (
            _http.http_session is not None
            and not _http.http_session.closed
        )
        lines.append(
            f"HTTP Session   : {'OK' if session_ok else 'CLOSED/MISSING'}"
        )
        lines.append("")

        lines.append("── Tasks ──────────────────────────────")
        task_labels = {
            "auto_post_spc":        "NOAA-SPC outlooks",
            "aggressive_check_spc": "NOAA-SPC aggressive check",
            "auto_post_spc48":      "NOAA-SPC Day 4-8",
            "auto_post_md":         "NOAA-SPC mesoscale discussions",
            "auto_post_watches":    "NOAA-SPC watches",
            "auto_post_scp":        "NIU/Gensini SCP graphics",
            "csu_mlp_daily_poll":   "CSU-MLP forecasts",
            "wxnext_daily_poll":    "NCAR WxNext2",
            "watchdog_task":        "watchdog",
            "periodic_cleanup":      "periodic cache cleanup",
        }
        for cog_name, cog in self.bot.cogs.items():
            for task_name in dir(cog):
                task = getattr(cog, task_name, None)
                if isinstance(task, tasks.Loop):
                    status = "running" if task.is_running() else "STOPPED"
                    label = task_labels.get(task_name, task_name)
                    lines.append(f"  {label:<35} {status}")
        lines.append("")

        lines.append("── Last Auto-Posts ─────────────────────")
        for key, dt in self.bot.state.last_post_times.items():
            if dt:
                ago = now - dt
                lines.append(
                    f"  {key:<10} {format_timedelta(ago)} ago  "
                    f"({dt.strftime('%m/%d %H:%MZ')})"
                )
            else:
                lines.append(f"  {key:<10} never this session")
        lines.append("")

        if self.bot.state.partial_update_state:
            lines.append("── Partial Update State ────────────────")
            for day_key, state in self.bot.state.partial_update_state.items():
                elapsed = (
                    datetime.now() - state["start_time"]
                ).total_seconds() / 60
                lines.append(
                    f"  {day_key}: {elapsed:.1f} min elapsed, "
                    f"{len(state['downloaded_data'])} imgs cached"
                )
            lines.append("")

        lines.append(f"MDs tracked    : {len(posted_mds)}")
        lines.append(f"Watches tracked: {len(posted_watches)}")
        lines.append("```")

        await interaction.followup.send("\n".join(lines), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(StatusCog(bot))
