# cogs/status.py
import asyncio
import logging
import resource
import socket
from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ext import commands, tasks

from cogs.mesoscale import (
    clean_md_text_for_discord,
    extract_md_body,
    fetch_latest_md_numbers,
    fetch_md_details,
)
from config import MANUAL_CACHE_FILE, SCP_IMAGE_URLS, SPC_URLS, WPC_IMAGE_URLS, __version__
import utils.http as _http
from utils.cache import (
    check_all_urls_exist_parallel,
    download_images_parallel,
    download_single_image,
    format_timedelta,
)
from utils.spc_outlook import get_high_risk_polygon, peek_active_labels, get_current_risk_display
from utils.spc_urls import get_spc_urls

logger = logging.getLogger("spc_bot")


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
            logger.exception(f"Discord file size limit exceeded: {e}")
        else:
            logger.exception(f"Discord send failed: {e}")
    except Exception as e:
        logger.exception(f"Unexpected error sending message: {e}")


async def fetch_and_send_weather_images(
    source, urls, title: str, state, use_cached: bool = False
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
        except discord.HTTPException as e:
            logger.debug(f"[STATUS] Could not send fallback message: {e}")
        return

    files = await download_images_parallel(
        urls, MANUAL_CACHE_FILE, state.manual_cache, use_cached=use_cached
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
        except discord.HTTPException as e:
            logger.debug(f"[STATUS] Could not send fallback message: {e}")


class MDPaginatorView(discord.ui.View):
    def __init__(self, bot, interaction, md_data):
        super().__init__(timeout=300)
        self.bot = bot
        self.interaction = interaction
        self.md_data = md_data
        self.index = 0
        self.message = None
        self._update_buttons()

    def _update_buttons(self):
        self.prev_btn.disabled = self.index == 0
        self.next_btn.disabled = self.index >= len(self.md_data) - 1

    def build_response(self):
        """Returns (content, embeds, files) for the current MD."""
        data = self.md_data[self.index]
        md_num = data["num"]
        raw_text = data["raw_text"]
        from_cache = data["from_cache"]
        cache_path = data["cache_path"]

        # 1. First embed: The Image (on top)
        md_page_url = f"https://www.spc.noaa.gov/products/md/mcd{md_num}.html"
        img_embed = discord.Embed(
            title=f"🌩️ SPC Mesoscale Discussion #{int(md_num)}",
            url=md_page_url,
            color=discord.Color.dark_orange(),
        )
        
        files = []
        if cache_path:
            files.append(discord.File(cache_path, filename=f"md_{md_num}.png"))
            img_embed.set_image(url=f"attachment://md_{md_num}.png")

        # 2. Second embed: The Text (below image)
        cleaned_text = clean_md_text_for_discord(raw_text)
        text_embed = discord.Embed(
            description=cleaned_text[:4090], # Stay under Discord embed limit
            color=discord.Color.dark_orange(),
        )
        
        footer_text = f"MD {self.index + 1} of {len(self.md_data)}"
        if from_cache:
            footer_text = f"⚠️ SPC website unreachable — image served from cache | {footer_text}"
        text_embed.set_footer(text=footer_text)
        
        return None, [img_embed, text_embed], files

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.index = max(0, self.index - 1)
        self._update_buttons()
        content, embeds, files = self.build_response()
        await interaction.response.edit_message(
            content=content, embeds=embeds, attachments=files, view=self
        )

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.index = min(len(self.md_data) - 1, self.index + 1)
        self._update_buttons()
        content, embeds, files = self.build_response()
        await interaction.response.edit_message(
            content=content, embeds=embeds, attachments=files, view=self
        )

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


async def is_owner(interaction: discord.Interaction) -> bool:
    """Check if the user is the bot owner."""
    if interaction.user.id == interaction.client.owner_id:
        return True
    if not interaction.client.application:
        await interaction.client.application_info()
    
    owner = interaction.client.application.owner
    if isinstance(owner, discord.Team):
        return any(m.id == interaction.user.id for m in owner.members)
    return owner.id == interaction.user.id


class StatusView(discord.ui.View):
    def __init__(self, bot, interaction):
        super().__init__(timeout=300)
        self.bot = bot
        self.interaction = interaction
        self.detailed = False

    async def build_embeds(self):
        now = datetime.now(timezone.utc)
        hostname = socket.gethostname()
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            host_ip = s.getsockname()[0]
            s.close()
        except Exception:
            host_ip = "unknown"

        role = "PRIMARY" if self.bot.state.is_primary else "STANDBY"
        color = discord.Color.green() if self.bot.state.is_primary else discord.Color.gold()

        embed = discord.Embed(
            title="🛰️ SPCBot System Status",
            description=f"Operational Overview | **{role}** node",
            color=color,
            timestamp=now
        )

        # System Info
        uptime_str = format_timedelta(now - self.bot.state.bot_start_time) if self.bot.state.bot_start_time else "unknown"
        rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        system_val = (
            f"**Host:** `{hostname}` (`{host_ip}`)\n"
            f"**Uptime:** `{uptime_str}`\n"
            f"**Memory:** `{rss_kb / 1024:.1f} MB`"
        )
        embed.add_field(name="🖥️ System", value=system_val, inline=True)

        # Connectivity
        nwws_status = "🔴 DISCONNECTED"
        nwws_cog = self.bot.get_cog("NWWSCog")
        if nwws_cog and nwws_cog.xmpp_client:
            nwws_status = "🟢 CONNECTED" if nwws_cog.xmpp_client.is_connected else "🟡 CONNECTING..."
        elif nwws_cog:
            nwws_status = "🟡 CONNECTING..." if self.bot.state.is_primary else "⚪ STANDBY"

        iembot_status = "🔴 STOPPED"
        iembot_cog = self.bot.get_cog("IEMBotCog")
        if iembot_cog and iembot_cog.poll_iembot_feed.is_running():
            iembot_status = "🟢 POLLING"
        elif iembot_cog:
            iembot_status = "⚪ STANDBY" if not self.bot.state.is_primary else "🔴 STOPPED"

        session_ok = _http.http_session is not None and not _http.http_session.closed
        conn_val = (
            f"**NWWS-OI:** {nwws_status}\n"
            f"**IEMBot:** {iembot_status}\n"
            f"**HTTP:** {'🟢 OK' if session_ok else '🔴 CLOSED'}"
        )
        embed.add_field(name="📡 Connectivity", value=conn_val, inline=True)

        # Latency Metrics
        nwws_lat = self.bot.state.nwws_latency
        iem_lat = self.bot.state.iembot_latency
        http_lat = self.bot.state.http_latency
        discord_rtt = self.bot.latency * 1000  # Convert to ms

        latency_val = (
            f"**Discord RTT:** `{discord_rtt:.1f}ms`\n"
            f"**NWWS Wire:** `{nwws_lat:.1f}s`*" if nwws_lat is not None else f"**NWWS Wire:** `---`"
        )
        latency_val += f"\n**IEMBot Wire:** `{iem_lat:.1f}s`*" if iem_lat is not None else f"\n**IEMBot Wire:** `---`"
        latency_val += f"\n**HTTP Avg:** `{http_lat * 1000:.1f}ms`" if http_lat is not None else f"\n**HTTP Avg:** `---`"
        
        embed.add_field(name="⏱️ Latency", value=latency_val, inline=True)

        # Environment
        risk_label = get_current_risk_display()
        active_high_risk = peek_active_labels()
        env_val = (
            f"**SPC Day 1:** `{risk_label}`\n"
        )
        if active_high_risk:
            env_val += "*(Sounding Sweep Armed)*\n"
        env_val += (
            f"**Active MDs:** `{len(self.bot.state.active_mds)}`\n"
            f"**Active Watches:** `{len(self.bot.state.active_watches)}`\n"
            f"**Active Warnings:** `{len(self.bot.state.active_warnings)}`"
        )
        embed.add_field(name="🌩️ Environment", value=env_val, inline=True)

        # Circuits
        open_circuits = [h for h in _http.circuit_breaker.failures if _http.circuit_breaker.is_open(h)]
        if open_circuits:
            embed.add_field(name="🔌 Open Circuits", value=", ".join(f"`{h}`" for h in open_circuits), inline=False)
            embed.color = discord.Color.red()

        # Recent activity (condensed)
        recent_lines = []
        sorted_posts = sorted(
            [(k, v) for k, v in self.bot.state.last_post_times.items() if v],
            key=lambda x: x[1],
            reverse=True
        )[:5]

        for key, dt in sorted_posts:
            ago = now - dt
            recent_lines.append(f"**{key}:** {format_timedelta(ago)} ago")

        if recent_lines:
            embed.add_field(name="🔄 Recent Activity", value="\n".join(recent_lines), inline=False)

        embed.set_footer(text=f"WXModelBot v{__version__} | *Wire latency has minute-precision error")
        
        embeds = [embed]

        if self.detailed:
            task_embed = discord.Embed(title="📋 Bot Task Details", color=color)
            task_labels = {
                "auto_post_spc":        "NOAA-SPC outlooks",
                "aggressive_check_spc": "NOAA-SPC aggressive check",
                "auto_post_spc48":      "NOAA-SPC Day 4-8",
                "auto_post_md":         "NOAA-SPC mesoscale discussions",
                "auto_post_watches":    "NOAA-SPC watches",
                "auto_post_scp":        "NIU/Gensini SCP graphics",
                "csu_mlp_daily_poll":   "CSU-MLP forecasts",
                "wxnext_daily_poll":    "NCAR WxNext2",
                "periodic_cleanup":     "periodic cache cleanup",
                "poll_iembot_feed":     "IEMBot real-time feed",
                "sync_loop":            "Failover standby sync",
                "auto_sounding_watches": "Sounding monitor",
                "monitor_special_soundings": "Special-release sounding monitor",
                "monitor_high_risk_soundings": "High-risk sounding sweep",
            }
            task_lines = []
            for cog_name, cog in self.bot.cogs.items():
                for task_name in dir(cog):
                    task = getattr(cog, task_name, None)
                    if isinstance(task, tasks.Loop):
                        status = "🟢" if task.is_running() else "🔴"
                        label = task_labels.get(task_name, task_name)
                        task_lines.append(f"{status} `{label}`")
            
            if task_lines:
                task_embed.description = "\n".join(task_lines)
            embeds.append(task_embed)

        return embeds

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary, emoji="🔄")
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embeds = await self.build_embeds()
        await interaction.response.edit_message(embeds=embeds, view=self)

    @discord.ui.button(label="Show Task Details", style=discord.ButtonStyle.secondary, emoji="📋")
    async def toggle_details_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.detailed = not self.detailed
        button.label = "Hide Task Details" if self.detailed else "Show Task Details"
        embeds = await self.build_embeds()
        await interaction.response.edit_message(embeds=embeds, view=self)


class TaskMgrView(discord.ui.View):
    def __init__(self, bot, interaction):
        super().__init__(timeout=600)
        self.bot = bot
        self.interaction = interaction
        self.message = None
        self.should_update = True

    async def build_embed(self):
        now = datetime.now(timezone.utc)
        color = discord.Color.blue()
        embed = discord.Embed(
            title="🖥️ SPCBot Task Manager",
            description="Real-time background task monitoring",
            color=color,
            timestamp=now
        )

        task_labels = {
            "auto_post_spc":        "NOAA-SPC outlooks",
            "aggressive_check_spc": "NOAA-SPC aggressive check",
            "auto_post_spc48":      "NOAA-SPC Day 4-8",
            "auto_post_md":         "NOAA-SPC mesoscale discussions",
            "auto_post_watches":    "NOAA-SPC watches",
            "auto_post_scp":        "NIU/Gensini SCP graphics",
            "csu_mlp_daily_poll":   "CSU-MLP forecasts",
            "wxnext_daily_poll":    "NCAR WxNext2",
            "periodic_cleanup":     "periodic cache cleanup",
            "poll_iembot_feed":     "IEMBot real-time feed",
            "sync_loop":            "Failover standby sync",
            "auto_sounding_watches": "Sounding monitor",
            "monitor_special_soundings": "Special-release sounding monitor",
            "monitor_high_risk_soundings": "High-risk sounding sweep",
        }

        task_lines = []
        for cog_name, cog in self.bot.cogs.items():
            for task_name in dir(cog):
                task = getattr(cog, task_name, None)
                if isinstance(task, tasks.Loop):
                    status = "🟢" if task.is_running() else "🔴"
                    label = task_labels.get(task_name, task_name)
                    # Show next iteration time if running
                    next_iter = ""
                    if task.is_running() and task.next_iteration:
                        diff = task.next_iteration - now
                        next_iter = f" (next in {format_timedelta(diff)})"
                    
                    task_lines.append(f"{status} `{label:<28}`{next_iter}")

        if task_lines:
            embed.description = f"```\n" + "\n".join(task_lines) + "\n```"

        rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        embed.set_footer(text=f"Memory: {rss_kb / 1024:.1f} MB | Auto-refreshing every 30s")
        return embed

    async def start_auto_update(self):
        while self.should_update:
            await asyncio.sleep(30)
            if not self.should_update:
                break
            try:
                embed = await self.build_embed()
                await self.message.edit(embed=embed, view=self)
            except Exception:
                self.should_update = False
                break

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary, emoji="🔄")
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = await self.build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Stop Refresh", style=discord.ButtonStyle.danger, emoji="🛑")
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.should_update = False
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    async def on_timeout(self):
        self.should_update = False
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


class LogView(discord.ui.View):
    def __init__(self, bot, interaction):
        super().__init__(timeout=300)
        self.bot = bot
        self.interaction = interaction
        self.message = None
        self.should_update = True

    def build_content(self):
        logs = []
        if hasattr(self.bot, "log_handler"):
            logs = self.bot.log_handler.get_logs()
        
        if not logs:
            logs = ["No logs captured yet..."]
        
        content = "🛰️ **SPCBot Live Console Output**\n"
        content += f"```ansi\n"
        # Discord supports ANSI color codes in ```ansi blocks
        # We'll just provide the raw text for now, but in the future we could colorize
        content += "\n".join(logs)
        content += "\n```\n*Refreshing every 5 seconds...*"
        return content

    async def start_auto_update(self):
        while self.should_update:
            await asyncio.sleep(5)
            if not self.should_update:
                break
            try:
                content = self.build_content()
                await self.message.edit(content=content, view=self)
            except Exception:
                self.should_update = False
                break

    @discord.ui.button(label="Stop Stream", style=discord.ButtonStyle.danger, emoji="🛑")
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.should_update = False
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    async def on_timeout(self):
        self.should_update = False
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
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
            self.bot.state,
            use_cached=True,
        )

    @commands.command(name="spc1")
    async def spc1_prefix(self, ctx):
        urls = await get_spc_urls(1)
        await fetch_and_send_weather_images(
            ctx, urls, "**Latest SPC Day 1 Outlooks**", self.bot.state, use_cached=True
        )

    @commands.command(name="spc2")
    async def spc2_prefix(self, ctx):
        urls = await get_spc_urls(2)
        await fetch_and_send_weather_images(
            ctx, urls, "**Latest SPC Day 2 Outlooks**", self.bot.state, use_cached=True
        )

    @commands.command(name="spc3")
    async def spc3_prefix(self, ctx):
        urls = await get_spc_urls(3)
        await fetch_and_send_weather_images(
            ctx, urls, "**Latest SPC Day 3 Outlooks**", self.bot.state, use_cached=True
        )

    @commands.command(name="spc48")
    async def spc48_prefix(self, ctx):
        await fetch_and_send_weather_images(
            ctx,
            SPC_URLS["48"],
            "**Latest SPC Day 4-8 Outlook**",
            self.bot.state,
            use_cached=False,
        )

    @commands.command(name="wpc")
    async def wpc_prefix(self, ctx):
        await fetch_and_send_weather_images(
            ctx, WPC_IMAGE_URLS, "**WPC Excessive Rainfall Outlooks (Day 1-3)**",
            self.bot.state,
            use_cached=False,
        )

    # ── Slash commands ───────────────────────────────────────────────────

    @discord.app_commands.command(
        name="help",
        description="Show all available weather and bot commands",
    )
    async def help_slash(self, interaction: discord.Interaction):
        """Display a comprehensive list of bot commands."""
        embed = discord.Embed(
            title="🛰️ WXModelBot Command Help",
            description=(
                "I monitor SPC, WPC, and experimental models to provide real-time "
                "weather updates and analysis tools."
            ),
            color=discord.Color.blue(),
        )

        # Outlooks & Discussions
        embed.add_field(
            name="📅 Outlooks & Discussions",
            value=(
                "`/spc1` [fresh] - Day 1 Convective Outlook\n"
                "`/spc2` [fresh] - Day 2 Convective Outlook\n"
                "`/spc3` [fresh] - Day 3 Convective Outlook\n"
                "`/spc48` - Day 4-8 Probability Outlook\n"
                "`/md` - Show active Mesoscale Discussions"
            ),
            inline=True,
        )

        # Watches & Tornado Tracking
        embed.add_field(
            name="🚨 Watches & Tornadoes",
            value=(
                "`/watches` - Active SPC watches overview\n"
                "`/recenttornadoes` - List recent confirmed tornadoes\n"
                "`/sigtor` - List significant (EF2+) tornadoes"
            ),
            inline=True,
        )

        # Analysis & Analytics
        embed.add_field(
            name="📊 Analysis & Analytics",
            value=(
                "`/sounding` <loc> [time] - Observed soundings\n"
                "`/hodograph` <site> - Radar VWP plots\n"
                "`/topstats` [by] [year] - Tornado leaderboards\n"
                "`/verify` <wfo> [days] - Warning metrics (IEM Cow)\n"
                "`/riskmap` [days] - Historical risk frequency\n"
                "`/dayssince` - Streak since last TOR warning\n"
                "`/dailyrecap` [date] - Daily warning polygon summary\n"
                "`/tornadoheatmap` [days] - Tornado density map"
            ),
            inline=False,
        )

        # Models & System
        embed.add_field(
            name="🧪 Models & System",
            value=(
                "`/csu` <product> - CSU-MLP ML forecasts\n"
                "`/wxnext` - NCAR WxNext2 AI forecasts\n"
                "`/scp` - NIU Supercell Composite\n"
                "`/wpc` - WPC Flash Flood Outlooks\n"
                "`/download` <site> - Raw Level 2 Radar data\n"
                "`/status` - Bot health & circuit status\n"
                "`/taskmgr` - Live task manager\n"
                "`/logs` - Live log stream"
            ),
            inline=False,
        )

        embed.set_footer(
            text=f"WXModelBot v{__version__} | Host: {socket.gethostname()}"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

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
            self.bot.state,
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
            interaction, urls, "**Latest SPC Day 1 Outlooks**", self.bot.state, use_cached=not fresh
        )

    @discord.app_commands.command(
        name="spc2", description="Get latest SPC Day 2 Outlooks"
    )
    @discord.app_commands.describe(fresh="Bypass cache and fetch the latest images directly")
    async def spc2_slash(self, interaction: discord.Interaction, fresh: Optional[bool] = False):
        await interaction.response.defer()
        urls = await get_spc_urls(2)
        await fetch_and_send_weather_images(
            interaction, urls, "**Latest SPC Day 2 Outlooks**", self.bot.state, use_cached=not fresh
        )

    @discord.app_commands.command(
        name="spc3", description="Get latest SPC Day 3 Outlooks"
    )
    @discord.app_commands.describe(fresh="Bypass cache and fetch the latest images directly")
    async def spc3_slash(self, interaction: discord.Interaction, fresh: Optional[bool] = False):
        await interaction.response.defer()
        urls = await get_spc_urls(3)
        await fetch_and_send_weather_images(
            interaction, urls, "**Latest SPC Day 3 Outlooks**", self.bot.state, use_cached=not fresh
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
            self.bot.state,
            use_cached=False,
        )

    @discord.app_commands.command(
        name="wpc", description="Get WPC Day 1-3 Rainfall Outlooks"
    )
    async def wpc_slash(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await fetch_and_send_weather_images(
            interaction, WPC_IMAGE_URLS, "**WPC Excessive Rainfall Outlooks (Day 1-3)**",
            self.bot.state,
            use_cached=False,
        )

    @discord.app_commands.command(
        name="md",
        description="Show all currently active SPC Mesoscale Discussions",
    )
    async def md_slash(self, interaction: discord.Interaction):
        await interaction.response.defer()
        logger.info("[/md] Command invoked (always fresh)")
        try:
            # We'll use a local fetch that doesn't mess with the auto-poster's cache
            md_numbers = await fetch_latest_md_numbers(fresh=True)
            logger.info(f"[/md] Fetched {len(md_numbers)} MD numbers")
        except Exception as e:
            logger.error(f"[/md] fetch_latest_md_numbers failed: {e}")
            await interaction.followup.send("Failed to fetch MD index.")
            return

        if not md_numbers:
            logger.info("[/md] No active MDs found")
            await interaction.followup.send(
                "No active Mesoscale Discussions found."
            )
            return

        async def _hydrate(md_num: str):
            logger.info(f"[/md] Starting hydration for #{md_num}...")
            try:
                # Add a per-MD timeout to ensure one bad MD doesn't kill the whole command
                res = await asyncio.wait_for(fetch_md_details(md_num), timeout=15.0)
                image_url, summary, from_cache, raw_text = res
                
                # Extract the actual body text from the HTML
                body_text = extract_md_body(raw_text)
                logger.info(f"[/md] Fetched details for #{md_num} (body size: {len(body_text) if body_text else 0})")
                
                cache_path = None
                if image_url:
                    logger.info(f"[/md] Downloading image for #{md_num}...")
                    cache_path, _, _ = await download_single_image(
                        image_url, MANUAL_CACHE_FILE, self.bot.state.manual_cache
                    )
                    logger.info(f"[/md] Finished image download for #{md_num} (path: {cache_path})")
                return {
                    "num": md_num,
                    "summary": summary,
                    "from_cache": from_cache,
                    "raw_text": body_text, # Use extracted body
                    "cache_path": cache_path
                }
            except asyncio.TimeoutError:
                logger.warning(f"[/md] Hydration timed out for #{md_num}")
                return None
            except Exception as e:
                logger.error(f"[/md] Hydration failed for #{md_num}: {e}")
                return None

        # Hydrate all active MDs (usually 1-5, rarely >10)
        try:
            md_data = await asyncio.wait_for(
                asyncio.gather(*[_hydrate(num) for num in md_numbers]),
                timeout=45.0
            )
            md_data = [d for d in md_data if d is not None]
        except asyncio.TimeoutError:
            logger.error("[/md] Hydration timed out after 45s")
            await interaction.followup.send("Timed out fetching MD details from SPC.")
            return
        except Exception as e:
            logger.error(f"[/md] Hydration failed: {e}")
            await interaction.followup.send("Failed to load MD details.")
            return
        
        if not md_data:
            await interaction.followup.send("No MD data could be retrieved.")
            return

        view = MDPaginatorView(self.bot, interaction, md_data)
        if len(md_data) == 1:
            view.prev_btn.disabled = True
            view.next_btn.disabled = True
        
        content, embeds, files = view.build_response()
        msg = await interaction.followup.send(
            content=content, embeds=embeds, files=files, view=view
        )
        view.message = msg
        logger.info("[/md] Successfully sent paginated response")

    @discord.app_commands.command(
        name="status",
        description="Show bot health, last post times, and current task state",
    )
    async def status_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        # Proactively refresh — the 30-min TTL inside get_high_risk_polygon
        # makes this a no-op most of the time, but it guarantees /status is
        # accurate immediately after a restart.
        try:
            await get_high_risk_polygon()
        except Exception as e:
            logger.debug(f"[STATUS] Outlook peek failed: {e}")

        view = StatusView(self.bot, interaction)
        embeds = await view.build_embeds()
        await interaction.followup.send(embeds=embeds, view=view, ephemeral=True)

    @discord.app_commands.command(
        name="taskmgr",
        description="Live-updating background task manager (htop style)",
    )
    @discord.app_commands.check(is_owner)
    async def taskmgr_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        view = TaskMgrView(self.bot, interaction)
        embed = await view.build_embed( )
        msg = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        view.message = msg
        asyncio.create_task(view.start_auto_update())

    @discord.app_commands.command(
        name="logs",
        description="Live-streaming bot console output (virtual terminal)",
    )
    @discord.app_commands.check(is_owner)
    async def logs_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        view = LogView(self.bot, interaction)
        content = view.build_content()
        msg = await interaction.followup.send(content=content, view=view, ephemeral=True)
        view.message = msg
        asyncio.create_task(view.start_auto_update())

    # ── Error Handling ───────────────────────────────────────────────────

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError
    ):
        if isinstance(error, discord.app_commands.CheckFailure):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ You are not authorized to use this command.", ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "❌ You are not authorized to use this command.", ephemeral=True
                )
        else:
            logger.error(f"[STATUS] Command error: {error}")



async def setup(bot: commands.Bot):
    await bot.add_cog(StatusCog(bot))
