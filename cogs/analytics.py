import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("spc_bot")

class AnalyticsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="topstats", description="Show leading states or WFOs for tornado counts (IEM Autoplot 92/141)")
    @app_commands.describe(
        by="Rank by State or NWS Office (WFO)",
        year="Year to query (default: current year)",
        source="Source of data (Warnings vs Reports)"
    )
    @app_commands.choices(by=[
        app_commands.Choice(name="State", value="state"),
        app_commands.Choice(name="WFO", value="wfo"),
    ])
    @app_commands.choices(source=[
        app_commands.Choice(name="Warnings (VTEC)", value="92"),
        app_commands.Choice(name="Reports (LSR)", value="141"),
    ])
    async def top_stats(
        self, 
        interaction: discord.Interaction, 
        by: str = "state", 
        year: Optional[int] = None,
        source: str = "92"
    ):
        await interaction.response.defer()
        
        current_year = datetime.now(timezone.utc).year
        year = year or current_year
        
        # Build URL for IEM Autoplot
        # #92: v:TO.W (Tornado Warning), s: (state/wfo), year
        # #141: v:TO (Tornado LSR), s: (state/wfo), year
        v_param = "TO.W" if source == "92" else "TO"
        unit_param = "state" if by == "state" else "wfo"
        
        url = f"https://mesonet.agron.iastate.edu/plotting/auto/plot/{source}/v:{v_param}::{unit_param}:all::year:{year}.png"
        
        embed = discord.Embed(
            title=f"📊 Top Tornado {'Warnings' if source == '92' else 'Reports'} by {by.upper()} ({year})",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_image(url=url)
        embed.set_footer(text=f"Data provided by IEM Autoplot #{source}")
        
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="dayssince", description="Show the streak since the last Tornado Warning (IEM Autoplot 235)")
    @app_commands.describe(
        wfo="4-letter WFO code (e.g. KOUN, leave blank for national map)",
        state="2-letter State code (e.g. OK, used if WFO is blank)"
    )
    async def days_since(
        self, 
        interaction: discord.Interaction, 
        wfo: Optional[str] = None,
        state: Optional[str] = None
    ):
        await interaction.response.defer()
        
        param = "national"
        if wfo:
            wfo = wfo.upper()
            if wfo.startswith("K") and len(wfo) == 4:
                wfo = wfo[1:]
            param = f"wfo:{wfo}"
        elif state:
            param = f"state:{state.upper()}"
            
        url = f"https://mesonet.agron.iastate.edu/plotting/auto/plot/235/v:TO.W::{param}.png"
        
        embed = discord.Embed(
            title="⏳ Days Since Last Tornado Warning",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_image(url=url)
        embed.set_footer(text="Data provided by IEM Autoplot #235")
        
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="dailyrecap", description="Visual summary of all tornado warning polygons for a day (IEM Autoplot 203)")
    @app_commands.describe(date="Date in YYYY-MM-DD format (default: yesterday)")
    async def daily_recap(self, interaction: discord.Interaction, date: Optional[str] = None):
        await interaction.response.defer()
        
        if not date:
            yesterday = datetime.now(timezone.utc) - timedelta(days=1)
            date = yesterday.strftime("%Y-%m-%d")
            
        url = f"https://mesonet.agron.iastate.edu/plotting/auto/plot/203/date:{date}::v:TO.W.png"
        
        embed = discord.Embed(
            title=f"🗺️ Tornado Warning Recap: {date}",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_image(url=url)
        embed.set_footer(text="Data provided by IEM Autoplot #203")
        
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="tornadoheatmap", description="Generate a density map of tornado reports (IEM Autoplot 108)")
    @app_commands.describe(
        days="Number of days to look back",
        state="2-letter State code (optional)"
    )
    async def tornado_heatmap(self, interaction: discord.Interaction, days: int = 30, state: Optional[str] = None):
        await interaction.response.defer()
        
        now = datetime.now(timezone.utc)
        sts = (now - timedelta(days=days)).strftime("%Y-%m-%d%%200000")
        ets = now.strftime("%Y-%m-%d%%202359")
        
        state_param = f"::state:{state.upper()}" if state else ""
        url = f"https://mesonet.agron.iastate.edu/plotting/auto/plot/108/v:TO::sts:{sts}::ets:{ets}{state_param}.png"
        
        embed = discord.Embed(
            title=f"🔥 Tornado Report Heatmap (Last {days} Days)",
            color=discord.Color.orange(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_image(url=url)
        embed.set_footer(text="Data provided by IEM Autoplot #108")
        
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="riskmap", description="Visualize historical SPC Day 1 outlook risk frequency (IEM Autoplot 232)")
    @app_commands.describe(
        threshold="Risk threshold (SLGT, MDT, HIGH)",
        state="2-letter State code (optional)",
        years="Number of years to look back (default: 10)"
    )
    @app_commands.choices(threshold=[
        app_commands.Choice(name="Slight Risk", value="SLGT"),
        app_commands.Choice(name="Enhanced Risk", value="ENH"),
        app_commands.Choice(name="Moderate Risk", value="MDT"),
        app_commands.Choice(name="High Risk", value="HIGH"),
    ])
    async def risk_map(
        self, 
        interaction: discord.Interaction, 
        threshold: str = "SLGT", 
        state: Optional[str] = None,
        years: int = 10
    ):
        await interaction.response.defer()
        
        now = datetime.now(timezone.utc)
        sts = (now - timedelta(days=365 * years)).strftime("%Y-%m-%d")
        ets = now.strftime("%Y-%m-%d")
        
        state_param = f"::state:{state.upper()}" if state else ""
        url = f"https://mesonet.agron.iastate.edu/plotting/auto/plot/232/t:threshold::threshold:{threshold}::sts:{sts}::ets:{ets}{state_param}.png"
        
        embed = discord.Embed(
            title=f"📈 Historical {threshold} Risk Frequency (Last {years} Years)",
            color=discord.Color.dark_green(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_image(url=url)
        embed.set_footer(text="Data provided by IEM Autoplot #232")
        
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="verify", description="Storm-based warning verification metrics via IEM Cow")
    @app_commands.describe(
        wfo="3-letter WFO code (e.g. OUN, BMX)",
        days="Number of days to look back (default: 30)",
        phenomena="VTEC phenomena (TO for Tornado, SV for Severe Thunderstorm)"
    )
    @app_commands.choices(phenomena=[
        app_commands.Choice(name="Tornado (TO)", value="TO"),
        app_commands.Choice(name="Severe Thunderstorm (SV)", value="SV"),
        app_commands.Choice(name="Flash Flood (FF)", value="FF"),
    ])
    async def verify(
        self, 
        interaction: discord.Interaction, 
        wfo: str, 
        days: int = 30,
        phenomena: str = "TO"
    ):
        await interaction.response.defer()
        
        wfo = wfo.upper()
        if wfo.startswith("K") and len(wfo) == 4:
            wfo = wfo[1:]
            
        now = datetime.now(timezone.utc)
        sts = (now - timedelta(days=days)).strftime("%Y-%m-%dT00:00Z")
        ets = now.strftime("%Y-%m-%dT23:59Z")
        
        # IEM Cow API
        url = (
            f"https://mesonet.agron.iastate.edu/api/1/cow.json"
            f"?wfo={wfo}&begints={sts}&endts={ets}&phenomena={phenomena}&lsrtype={phenomena}"
        )
        
        from utils.http import http_get_json
        data = await http_get_json(url)
        if not data or "stats" not in data:
            await interaction.followup.send(f"Could not fetch verification data for {wfo}.")
            return
            
        stats = data["stats"]
        
        embed = discord.Embed(
            title=f"🐄 IEM Cow Verification: {wfo} ({phenomena})",
            description=f"Verification metrics for the last {days} days.",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc)
        )
        
        # POD (Probability of Detection)
        pod = stats.get("POD[1]", 0.0)
        # FAR (False Alarm Ratio)
        far = stats.get("FAR[1]", 0.0)
        # Lead Time
        avg_lt = stats.get("avg_leadtime[min]")
        
        embed.add_field(name="POD (Detection)", value=f"{pod:.2f}", inline=True)
        embed.add_field(name="FAR (False Alarm)", value=f"{far:.2f}", inline=True)
        embed.add_field(name="CSI (Success Index)", value=f"{stats.get('CSI[1]', 0.0):.2f}", inline=True)
        
        if avg_lt is not None:
            embed.add_field(name="Avg Lead Time", value=f"{avg_lt:.1f} min", inline=True)
            
        embed.add_field(name="Warnings", value=f"{stats.get('events_total', 0)}", inline=True)
        embed.add_field(name="Verified", value=f"{stats.get('events_verified', 0)}", inline=True)
        
        embed.set_footer(text=f"IEM Cow | Interval: {sts} to {ets}")
        await interaction.followup.send(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(AnalyticsCog(bot))
