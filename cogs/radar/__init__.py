# cogs/radar/__init__.py
"""NEXRAD Level 2 radar data downloader from NOAA AWS S3."""

import asyncio
import difflib
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord
from discord.app_commands import Choice
from discord.ext import commands, tasks

from cogs.radar.downloads import CLEANUP_AGE_THRESHOLD, OUTPUT_DIR, cleanup_old_files, run_download
from cogs.radar.s3 import _s3
from cogs.radar.views import StartView, TimeRangeView

logger = logging.getLogger("spc_bot")


class RadarCog(commands.Cog):
    MANAGED_TASK_NAMES = [("periodic_cleanup", "periodic_cleanup")]

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
            await ctx_or_interaction.response.send_message(embed=embed, view=view)
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
    @discord.app_commands.choices(
        time=[
            Choice(name="Last 1 hour", value="1h"),
            Choice(name="Last 2 hours", value="2h"),
            Choice(name="Last 3 hours", value="3h"),
            Choice(name="Last 4 hours", value="4h"),
            Choice(name="Custom / Other (Z-to-Z, explicit, etc.)", value="custom"),
        ]
    )
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
                interaction,
                radar_sites,
                [],
                start_dt=None,
                end_dt=None,
                dates_to_query=[now],
                max_files=count,
            )
            return

        # Sites only (or 'custom' selected) — show time preset buttons
        if not time or time.value == "custom":
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
            msg = await interaction.followup.send(embed=embed, view=view, wait=True)
            view.messages_to_delete.append(msg)
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
            interaction,
            radar_sites,
            messages_to_delete,
            start_dt=start_dt,
            end_dt=now,
            dates_to_query=dates_to_query,
        )

    @discord.app_commands.command(
        name="downloaderstatus",
        description="Check AWS downloader and S3 latency",
    )
    async def downloaderstatus_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        ws_latency = round(self.bot.latency * 1000)
        ws_icon = "🟢" if ws_latency < 100 else "🟡" if ws_latency < 200 else "🔴"
        try:
            s3_start = time.time()
            async with _s3() as s3:
                await s3.list_objects_v2(
                    Bucket="unidata-nexrad-level2",
                    Prefix=f"{datetime.now(timezone.utc).year}/",
                    Delimiter="/",
                    MaxKeys=1,
                )
            s3_latency = round((time.time() - s3_start) * 1000)
            s3_icon = "🟢" if s3_latency < 500 else "🟡" if s3_latency < 1000 else "🔴"
            s3_status = f"{s3_latency}ms"
        except Exception as e:
            s3_status = f"Error: {e}"
            s3_icon = "🔴"
        embed = discord.Embed(title="AWS NEXRAD Downloader Status", color=discord.Color.blue())
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

    RADAR_GIF_CACHE = Path("cache") / "radar_gifs"

    TDWR_SITES = {
        "TATL": "Atlanta, GA",
        "TBNA": "Nashville, TN",
        "TBOS": "Boston, MA",
        "TBWI": "Baltimore, MD",
        "TCLT": "Charlotte, NC",
        "TCMH": "Columbus, OH",
        "TCVG": "Covington, KY",
        "TDAL": "Dallas Love Field, TX",
        "TDAY": "Dayton, OH",
        "TDCA": "Washington National, VA",
        "TDEN": "Denver, CO",
        "TDFW": "Dallas/Ft. Worth, TX",
        "TDTW": "Detroit, MI",
        "TEWR": "Newark, NJ",
        "TFLL": "Fort Lauderdale, FL",
        "THOU": "Houston Hobby, TX",
        "TIAD": "Dulles, VA",
        "TIAH": "Houston Intercontinental, TX",
        "TICH": "Wichita, KS",
        "TIDS": "Indianapolis, IN",
        "TJFK": "New York City, NY",
        "TJUA": "San Juan, PR",
        "TLAS": "Las Vegas, NV",
        "TLVE": "Cleveland, OH",
        "TMCI": "Kansas City, MO",
        "TMCO": "Orlando, FL",
        "TMDW": "Chicago Midway, IL",
        "TMEM": "Memphis, TN",
        "TMIA": "Miami, FL",
        "TMKE": "Milwaukee, WI",
        "TMSP": "Minneapolis, MN",
        "TMSY": "New Orleans, LA",
        "TOKC": "Oklahoma City, OK",
        "TORD": "Chicago O'Hare, IL",
        "TPBI": "West Palm Beach, FL",
        "TPHL": "Philadelphia, PA",
        "TPHX": "Phoenix, AZ",
        "TPIT": "Pittsburgh, PA",
        "TRDU": "Raleigh-Durham, NC",
        "TSDF": "Louisville, KY",
        "TSJU": "San Juan, PR",
        "TSLC": "Salt Lake City, UT",
        "TSTL": "St Louis, MO",
        "TTPA": "Tampa, FL",
        "TTUL": "Tulsa, OK",
    }

    RADAR_SITES = {
        "KABR": "Aberdeen, SD",
        "KABX": "Albuquerque, NM",
        "KAKQ": "Norfolk, VA",
        "KAMA": "Amarillo, TX",
        "KAMX": "Miami, FL",
        "KAPX": "Gaylord, MI",
        "KARX": "La Crosse, WI",
        "KATX": "Seattle, WA",
        "KBBX": "Beale AFB, CA",
        "KBGM": "Binghamton, NY",
        "KBHX": "Eureka, CA",
        "KBIS": "Bismarck, ND",
        "KBLX": "Billings, MT",
        "KBMX": "Birmingham, AL",
        "KBOX": "Boston, MA",
        "KBRO": "Brownsville, TX",
        "KBUF": "Buffalo, NY",
        "KBYX": "Key West, FL",
        "KCAE": "Columbia, SC",
        "KCBW": "Caribou, ME",
        "KCBX": "Boise, ID",
        "KCCX": "State College, PA",
        "KCLE": "Cleveland, OH",
        "KCLX": "Charleston, SC",
        "KCRP": "Corpus Christi, TX",
        "KCXX": "Burlington, VT",
        "KCYS": "Cheyenne, WY",
        "KDAX": "Sacramento, CA",
        "KDDC": "Dodge City, KS",
        "KDFX": "Laughlin AFB, TX",
        "KDGX": "Jackson, MS",
        "KDIX": "Philadelphia, PA",
        "KDLH": "Duluth, MN",
        "KDMX": "Des Moines, IA",
        "KDOX": "Dover AFB, DE",
        "KDTX": "Detroit, MI",
        "KDVN": "Quad Cities, IA",
        "KDYX": "Abilene, TX",
        "KEAX": "Kansas City, MO",
        "KEMX": "Tucson, AZ",
        "KENX": "Albany, NY",
        "KEOX": "Fort Rucker, AL",
        "KEPZ": "El Paso, TX",
        "KESX": "Las Vegas, NV",
        "KEVX": "Eglin AFB, FL",
        "KEWX": "Austin, TX",
        "KEYX": "Edwards AFB, CA",
        "KFCX": "Roanoke, VA",
        "KFDR": "Altus AFB, OK",
        "KFDX": "Cannon AFB, NM",
        "KFFC": "Atlanta, GA",
        "KFSD": "Sioux Falls, SD",
        "KFSX": "Flagstaff, AZ",
        "KFTG": "Denver, CO",
        "KFWS": "Dallas/Fort Worth, TX",
        "KGGW": "Glasgow, MT",
        "KGJX": "Grand Junction, CO",
        "KGLD": "Goodland, KS",
        "KGRB": "Green Bay, WI",
        "KGRK": "Fort Hood, TX",
        "KGRR": "Grand Rapids, MI",
        "KGSP": "Greer, SC",
        "KGWX": "Columbus AFB, MS",
        "KGYX": "Portland, ME",
        "KHDC": "Hammond, LA",
        "KHDX": "Holloman AFB, NM",
        "KHGX": "Houston, TX",
        "KHNX": "San Joaquin Valley, CA",
        "KHPX": "Fort Campbell, KY",
        "KHTX": "Huntsville, AL",
        "KICT": "Wichita, KS",
        "KICX": "Cedar City, UT",
        "KILN": "Cincinnati, OH",
        "KILX": "Lincoln, IL",
        "KIND": "Indianapolis, IN",
        "KINX": "Tulsa, OK",
        "KIWA": "Phoenix, AZ",
        "KIWX": "Northern Indiana, IN",
        "KJAX": "Jacksonville, FL",
        "KJGX": "Macon, GA",
        "KJKL": "Jackson, KY",
        "KLBB": "Lubbock, TX",
        "KLCH": "Lake Charles, LA",
        "KLGX": "Langley Hill, WA",
        "KLNX": "North Platte, NE",
        "KLOT": "Chicago, IL",
        "KLRX": "Elko, NV",
        "KLSX": "St Louis, MO",
        "KLTX": "Wilmington, NC",
        "KLVX": "Louisville, KY",
        "KLWX": "Sterling, VA",
        "KLZK": "Little Rock, AR",
        "KMAF": "Midland, TX",
        "KMAX": "Medford, OR",
        "KMBX": "Minot, ND",
        "KMHX": "Morehead City, NC",
        "KMKX": "Milwaukee, WI",
        "KMLB": "Melbourne, FL",
        "KMOB": "Mobile, AL",
        "KMPX": "Minneapolis, MN",
        "KMQT": "Marquette, MI",
        "KMRX": "Knoxville, TN",
        "KMSX": "Missoula, MT",
        "KMTX": "Salt Lake City, UT",
        "KMUX": "San Francisco, CA",
        "KMVX": "Fargo, ND",
        "KMXX": "Maxwell AFB, AL",
        "KNKX": "San Diego, CA",
        "KNQA": "Memphis, TN",
        "KOAX": "Omaha, NE",
        "KOHX": "Nashville, TN",
        "KOKX": "Brookhaven, NY",
        "KOTX": "Spokane, WA",
        "KPAH": "Paducah, KY",
        "KPBZ": "Pittsburgh, PA",
        "KPDT": "Pendleton, OR",
        "KPOE": "Fort Polk, LA",
        "KPUX": "Pueblo, CO",
        "KRAX": "Raleigh, NC",
        "KRGX": "Reno, NV",
        "KRIW": "Riverton, WY",
        "KRLX": "Charleston, WV",
        "KRTX": "Portland, OR",
        "KSFX": "Pocatello, ID",
        "KSGF": "Springfield, MO",
        "KSHV": "Shreveport, LA",
        "KSJT": "San Angelo, TX",
        "KSOX": "Santa Ana, CA",
        "KSRX": "Fort Smith, AR",
        "KTBW": "Tampa, FL",
        "KTFX": "Great Falls, MT",
        "KTLH": "Tallahassee, FL",
        "KTLX": "Oklahoma City, OK",
        "KTWX": "Topeka, KS",
        "KTYX": "Fort Drum, NY",
        "KUDX": "Rapid City, SD",
        "KUEX": "Grand Island, NE",
        "KVAX": "Moody AFB, GA",
        "KVBX": "Vandenberg AFB, CA",
        "KVNX": "Vance AFB, OK",
        "KVTX": "Los Angeles, CA",
        "KVWX": "Evansville, IN",
        "KYUX": "Yuma, AZ",
    }

    @discord.app_commands.command(
        name="radar",
        description="Get a radar animation loop for a NEXRAD site",
    )
    @discord.app_commands.describe(
        site="4-letter NEXRAD site ID (e.g. KTLX, KFWS)",
        product="Radar product to display",
        frames="Number of frames (default 6, max 20)",
        zoom="View range — tighter zoom shows more storm-scale detail (default Regional)",
    )
    @discord.app_commands.choices(
        product=[
            Choice(name="Reflectivity", value="reflectivity"),
            Choice(name="Velocity", value="velocity"),
            Choice(name="Spectrum Width", value="spectrum-width"),
            Choice(name="Differential Reflectivity (ZDR)", value="zdr"),
            Choice(name="Correlation Coefficient (CC)", value="cc"),
            Choice(name="Differential Phase (PHIDP)", value="phidp"),
            Choice(name="Specific KDP", value="kdp"),
        ],
        zoom=[
            Choice(name="Storm-Scale (~75km) — couplets/TDS", value=75.0),
            Choice(name="Regional (~150km)", value=150.0),
            Choice(name="Wide (~300km)", value=300.0),
            Choice(name="Full Range (~460km)", value=460.0),
        ],
    )
    async def radar_slash(
        self,
        interaction: discord.Interaction,
        site: str,
        product: Choice[str] = None,
        frames: int = 6,
        zoom: Choice[float] = None,
    ):
        await interaction.response.defer(thinking=True)

        site = site.upper().strip()
        product_value = product.value if product else "reflectivity"
        frames = max(2, min(frames, 20))
        range_km = zoom.value if zoom else 150.0

        all_sites = {**self.RADAR_SITES, **self.TDWR_SITES}
        if site not in all_sites:
            suggestions = difflib.get_close_matches(site, list(all_sites), n=3, cutoff=0.5)
            if suggestions:
                msg = "`{}` not recognized. Did you mean?\n{}".format(
                    site,
                    "\n".join("  `{}` — {}".format(s, all_sites[s]) for s in suggestions),
                )
            else:
                msg = "`{}` is not a recognized radar site.".format(site)
            await interaction.followup.send(msg, ephemeral=True)
            return

        self.RADAR_GIF_CACHE.mkdir(parents=True, exist_ok=True)
        out_path = self.RADAR_GIF_CACHE / "{}_{}_{}_{:.0f}km.gif".format(site, product_value, frames, range_km)

        try:
            await _run_radar_cli(site, product_value, frames, out_path, range_km)
        except Exception as e:
            logger.exception("[RADAR] Failed to generate for {}: {}".format(site, e))
            await interaction.followup.send(
                "Failed to generate radar loop for `{}`.".format(site), ephemeral=True
            )
            return

        if not out_path.exists():
            await interaction.followup.send(
                "No radar data available for `{}`.".format(site), ephemeral=True
            )
            return

        site_name = self.RADAR_SITES.get(site) or self.TDWR_SITES.get(site) or site
        product_label = product.name if product else "Reflectivity"
        zoom_label = zoom.name if zoom else "Regional (~150km)"
        embed = discord.Embed(
            title="{} Loop — {} ({})".format(product_label, site, site_name),
            description="{} frames · {}".format(frames, zoom_label),
            color=discord.Color.blue(),
        )
        embed.set_footer(text="NEXRAD Level II Archive")

        file_size_mb = out_path.stat().st_size / (1024 * 1024)
        if file_size_mb > 25:
            await interaction.followup.send(
                "File too large ({:.1f} MB). Try fewer frames.".format(file_size_mb), ephemeral=True
            )
            return

        try:
            await interaction.followup.send(embed=embed, file=discord.File(str(out_path)))
        except discord.HTTPException:
            await interaction.followup.send(
                "Generated but file too large to send. Try fewer frames.", ephemeral=True
            )

    @tasks.loop(hours=1)
    async def periodic_cleanup(self):
        await cleanup_old_files(OUTPUT_DIR, CLEANUP_AGE_THRESHOLD)


RADAR_GIF_BIN = next(
    (
        p
        for p in [
            Path(__file__).parent.parent.parent / "target/release/radar_gif",
            Path(__file__).parent.parent.parent / "target/debug/radar_gif",
            Path("/usr/local/bin/radar_gif"),
        ]
        if p.exists()
    ),
    None,
)


async def _run_radar_cli(site: str, product: str, frames: int, out_path: Path, range_km: float = 150.0):
    if RADAR_GIF_BIN is None:
        raise RuntimeError(
            "radar_gif binary not found (build with: cargo build --release -p radar_gif)"
        )
    proc = await asyncio.create_subprocess_exec(
        str(RADAR_GIF_BIN),
        "--site",
        site,
        "--product",
        product,
        "--frames",
        str(frames),
        "--range-km",
        str(range_km),
        "--output",
        str(out_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("Radar generation timed out after 180s")
    if proc.returncode != 0:
        stderr_text = stderr.decode() if stderr else "unknown error"
        logger.warning("[RADAR] CLI failed (exit {}): {}".format(proc.returncode, stderr_text))
        raise RuntimeError("radar_gif exited with code {}".format(proc.returncode))


async def setup(bot: commands.Bot):
    await bot.add_cog(RadarCog(bot))
