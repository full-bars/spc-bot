import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.db import get_warning_stats

logger = logging.getLogger("spc_bot")


class AnalyticsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="topstats",
        description="Show leading states or WFOs for warning counts (IEM Autoplot 109/163)",
    )
    @app_commands.describe(
        by="Rank by State or NWS Office (WFO)",
        year="Year to query (default: current year)",
        source="Source of data (Warnings vs Reports)",
        phenomenon="Weather phenomenon (default: Tornado)",
    )
    @app_commands.choices(
        by=[
            app_commands.Choice(name="State", value="state"),
            app_commands.Choice(name="WFO", value="wfo"),
        ]
    )
    @app_commands.choices(
        source=[
            app_commands.Choice(name="Warnings (VTEC)", value="109"),
            app_commands.Choice(name="Reports (LSR)", value="163"),
        ]
    )
    @app_commands.choices(
        phenomenon=[
            app_commands.Choice(name="Tornado", value="TO"),
            app_commands.Choice(name="Severe Thunderstorm", value="SV"),
        ]
    )
    async def top_stats(
        self,
        interaction: discord.Interaction,
        by: str = "state",
        year: Optional[int] = None,
        source: str = "109",
        phenomenon: str = "TO",
    ):
        await interaction.response.defer()

        current_year = datetime.now(timezone.utc).year
        year = year or current_year

        from urllib.parse import quote

        # Build URL for IEM Autoplot
        if source == "109":
            # #109: WFO/State VTEC Event Counts
            # phenomenav1: (TO/SV), significancev1: W, by: (state/wfo), sdate, edate, var: count, w: set
            sdate = quote(f"{year}/01/01 0000")
            edate = quote(f"{year}/12/31 2359")
            url = (
                f"https://mesonet.agron.iastate.edu/plotting/auto/plot/109/"
                f"phenomenav1:{phenomenon}::significancev1:W::by:{by}::sdate:{sdate}::edate:{edate}::var:count::w:set.png"
            )
        else:
            # #163: Local Storm Reports Issued by WFO/State
            # filter: (TORNADO/SVR), by: (state/wfo), sdate, edate
            lsr_filter = "TORNADO" if phenomenon == "TO" else "SVR"
            sdate = quote(f"{year}/01/01 0000")
            edate = quote(f"{year}/12/31 2359")
            url = f"https://mesonet.agron.iastate.edu/plotting/auto/plot/163/filter:{lsr_filter}::by:{by}::sdate:{sdate}::edate:{edate}.png"

        phenom_label = "Tornado" if phenomenon == "TO" else "Severe Thunderstorm"
        embed = discord.Embed(
            title=f"📊 Top {phenom_label} {'Warnings' if source == '109' else 'Reports'} by {by.upper()} ({year})",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_image(url=url)
        embed.set_footer(text=f"Data provided by IEM Autoplot #{source}")

        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="dayssince",
        description="Show the streak since the last Tornado Warning (IEM Autoplot 92)",
    )
    @app_commands.describe(
        wfo="4-letter WFO code (e.g. KOUN, leave blank for national map)",
        state="2-letter State code (e.g. OK, used if WFO is blank)",
    )
    async def days_since(
        self,
        interaction: discord.Interaction,
        wfo: Optional[str] = None,
        state: Optional[str] = None,
    ):
        await interaction.response.defer()

        # #92: Days since Last Watch/Warning/Advisory by WFO
        # phenomena: TO, significance: W
        url = "https://mesonet.agron.iastate.edu/plotting/auto/plot/92/phenomena:TO::significance:W.png"

        embed = discord.Embed(
            title="⏳ Days Since Last Tornado Warning",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_image(url=url)
        embed.set_footer(text="Data provided by IEM Autoplot #92")

        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="dailyrecap",
        description="Visual summary of all tornado warning polygons for a day (IEM Autoplot 203)",
    )
    @app_commands.describe(date="Date in YYYY-MM-DD format (default: yesterday)")
    async def daily_recap(self, interaction: discord.Interaction, date: Optional[str] = None):
        await interaction.response.defer()

        if not date:
            yesterday = datetime.now(timezone.utc) - timedelta(days=1)
            date = yesterday.strftime("%Y-%m-%d")

        url = f"https://mesonet.agron.iastate.edu/plotting/auto/plot/203/date:{date}::typ:W.png"

        embed = discord.Embed(
            title=f"🗺️ Tornado Warning Recap: {date}",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_image(url=url)
        embed.set_footer(text="Data provided by IEM Autoplot #203")

        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="tornadoheatmap",
        description="Generate a density map of tornado reports (IEM Autoplot 163)",
    )
    @app_commands.describe(
        days="Number of days to look back", state="2-letter State code (optional)"
    )
    async def tornado_heatmap(
        self, interaction: discord.Interaction, days: int = 30, state: Optional[str] = None
    ):
        await interaction.response.defer()

        from urllib.parse import quote

        now = datetime.now(timezone.utc)
        sdate = quote((now - timedelta(days=days)).strftime("%Y/%m/%d 0000"))
        edate = quote(now.strftime("%Y/%m/%d 2359"))

        by_param = "state" if state else "wfo"
        state_param = f"::csector:{state.upper()}" if state else ""

        # #163: Local Storm Reports Issued by WFO/State [map]
        # var: count, filter: TORNADO
        url = f"https://mesonet.agron.iastate.edu/plotting/auto/plot/163/var:count::filter:TORNADO::by:{by_param}::sdate:{sdate}::edate:{edate}{state_param}.png"

        embed = discord.Embed(
            title=f"🔥 Tornado Report Heatmap (Last {days} Days)",
            color=discord.Color.orange(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_image(url=url)
        embed.set_footer(text="Data provided by IEM Autoplot #163")

        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="riskmap",
        description="Visualize historical SPC Day 1 outlook risk frequency (IEM Autoplot 200)",
    )
    @app_commands.describe(
        threshold="Risk threshold (SLGT, MDT, HIGH)",
        state="2-letter State code (optional)",
        years="Number of years to look back (default: 10)",
    )
    @app_commands.choices(
        threshold=[
            app_commands.Choice(name="Slight Risk", value="CATEGORICAL.SLGT"),
            app_commands.Choice(name="Enhanced Risk", value="CATEGORICAL.ENH"),
            app_commands.Choice(name="Moderate Risk", value="CATEGORICAL.MDT"),
            app_commands.Choice(name="High Risk", value="CATEGORICAL.HIGH"),
        ]
    )
    async def risk_map(
        self,
        interaction: discord.Interaction,
        threshold: str = "CATEGORICAL.SLGT",
        state: Optional[str] = None,
        years: int = 10,
    ):
        await interaction.response.defer()

        from urllib.parse import quote

        now = datetime.now(timezone.utc)
        sdate = quote((now - timedelta(days=365 * years)).strftime("%Y-%m-%d"))
        edate = quote(now.strftime("%Y-%m-%d"))

        # #200: SPC + WPC Outlook Heatmap
        # p: 1.C.A (Day 1 Convective All Issuances), level: (threshold), t: (state/cwa)
        t_param = "state" if state else "cwa"
        state_param = f"::csector:{state.upper()}" if state else ""
        url = f"https://mesonet.agron.iastate.edu/plotting/auto/plot/200/p:1.C.A::level:{threshold}::t:{t_param}::sdate:{sdate}::edate:{edate}{state_param}.png"

        embed = discord.Embed(
            title=f"📈 Historical {threshold.rsplit('.', maxsplit=1)[-1]} Risk Frequency (Last {years} Years)",
            color=discord.Color.dark_green(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_image(url=url)
        embed.set_footer(text="Data provided by IEM Autoplot #200")

        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="verify", description="Storm-based warning verification metrics via IEM Cow"
    )
    @app_commands.describe(
        wfo="3-letter WFO code (e.g. OUN, BMX)",
        days="Number of days to look back (default: 30)",
        phenomena="VTEC phenomena (TO for Tornado, SV for Severe Thunderstorm)",
    )
    @app_commands.choices(
        phenomena=[
            app_commands.Choice(name="Tornado (TO)", value="TO"),
            app_commands.Choice(name="Severe Thunderstorm (SV)", value="SV"),
            app_commands.Choice(name="Flash Flood (FF)", value="FF"),
        ]
    )
    async def verify(
        self, interaction: discord.Interaction, wfo: str, days: int = 30, phenomena: str = "TO"
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
            timestamp=datetime.now(timezone.utc),
        )

        # POD (Probability of Detection)
        pod = stats.get("POD[1]", 0.0)
        # FAR (False Alarm Ratio)
        far = stats.get("FAR[1]", 0.0)
        # Lead Time
        avg_lt = stats.get("avg_leadtime[min]")

        embed.add_field(name="POD (Detection)", value=f"{pod:.2f}", inline=True)
        embed.add_field(name="FAR (False Alarm)", value=f"{far:.2f}", inline=True)
        embed.add_field(
            name="CSI (Success Index)", value=f"{stats.get('CSI[1]', 0.0):.2f}", inline=True
        )

        if avg_lt is not None:
            embed.add_field(name="Avg Lead Time", value=f"{avg_lt:.1f} min", inline=True)

        embed.add_field(name="Warnings", value=f"{stats.get('events_total', 0)}", inline=True)
        embed.add_field(name="Verified", value=f"{stats.get('events_verified', 0)}", inline=True)

        embed.set_footer(text=f"IEM Cow | Interval: {sts} to {ets}")
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="howmany",
        description="Show warning counts by type and severity (tornado, severe, flash flood)",
    )
    @app_commands.describe(
        period="Time period to count (default: 24h)",
    )
    @app_commands.choices(
        period=[
            app_commands.Choice(name="Last 24 hours", value="24h"),
            app_commands.Choice(name="Last 7 days", value="7d"),
            app_commands.Choice(name="Last 30 days", value="30d"),
            app_commands.Choice(name="All time", value="all"),
        ]
    )
    async def how_many(self, interaction: discord.Interaction, period: str = "24h"):
        await interaction.response.defer()

        now = datetime.now(timezone.utc)
        since_map = {
            "24h": now - timedelta(hours=24),
            "7d": now - timedelta(days=7),
            "30d": now - timedelta(days=30),
            "all": None,
        }
        since = since_map[period]
        since_ts = since.timestamp() if since else None

        stats = await get_warning_stats(since=since_ts)
        if not stats:
            await interaction.followup.send("No warning data available.")
            return

        tor = stats["tor"]
        svr = stats["svr"]
        ffw = stats["ffw"]

        period_label = {"24h": "24h", "7d": "7 days", "30d": "30 days", "all": "all time"}[period]

        embed = discord.Embed(
            title=f"📊 Warning Counts — Last {period_label}",
            color=discord.Color.blue(),
            timestamp=now,
        )

        # Tornado
        tor_val = (
            f"**{tor['total']}** total\n"
            f"🚨 TOR E: {tor['emergency']}\n"
            f"⚠️ PDS: {tor['pds']}\n"
            f"🔴 Confirmed: {tor['observed']}\n"
            f"📡 Radar Indicated: {tor['radar_indicated']}\n"
            f"Baseline: {tor['standard']}"
        )
        embed.add_field(name="🌪️ Tornado Warnings", value=tor_val, inline=True)

        # Severe Tstorm
        svr_val = (
            f"**{svr['total']}** total\n"
            f"🚨 SVR D: {svr['destructive']}\n"
            f"⚠️ SVR C: {svr['considerable']}\n"
            f"Baseline: {svr['standard']}"
        )
        embed.add_field(name="⛈️ Severe Tstorm", value=svr_val, inline=True)

        # Flash Flood
        ffw_val = (
            f"**{ffw['total']}** total\n"
            f"🚨 FFE: {ffw['emergency']}\n"
            f"⚠️ FFW-C: {ffw['considerable']}\n"
            f"Baseline: {ffw['standard']}"
        )
        embed.add_field(name="🌊 Flash Flood", value=ffw_val, inline=True)

        embed.set_footer(text="SPC Bot Warning Tracker")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(AnalyticsCog(bot))
