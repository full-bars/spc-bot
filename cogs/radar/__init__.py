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

    RADAR_SITES = {
        "KTLX": "Oklahoma City/Twin Lakes, OK",
        "KFWS": "Dallas/Ft. Worth, TX",
        "KSHV": "Shreveport, LA",
        "KHTX": "Huntsville, AL",
        "KMOB": "Mobile, AL",
        "KEOX": "Enterprise, AL",
        "KBMX": "Birmingham, AL",
        "KAMA": "Amarillo, TX",
        "KAMX": "Miami, FL",
        "KBBX": "Beale AFB, CA",
        "KBHX": "Eureka, CA",
        "KBIS": "Bismarck, ND",
        "KBLX": "Billings, MT",
        "KBRX": "Belle Plaine, IA",
        "KBRO": "Brownsville, TX",
        "KBUF": "Buffalo, NY",
        "KCAE": "Columbia, SC",
        "KCLE": "Cleveland, OH",
        "KCLX": "Charleston, SC",
        "KCRP": "Corpus Christi, TX",
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
        "KDYX": "Abilene/Sweetwater, TX",
        "KEAX": "Kansas City, MO",
        "KEMX": "Tucson, AZ",
        "KENX": "Albany, NY",
        "KEPZ": "El Paso, TX",
        "KESX": "Las Vegas, NV",
        "KEVX": "Eglin AFB, FL",
        "KEWX": "Austin/San Antonio, TX",
        "KEYX": "Edwards AFB, CA",
        "KFCX": "Roanoke, VA",
        "KFDR": "Frederick, OK",
        "KFDX": "Cannon AFB, NM",
        "KFFC": "Atlanta, GA",
        "KFSD": "Sioux Falls, SD",
        "KFSX": "Flagstaff, AZ",
        "KFTG": "Denver, CO",
        "KGGW": "Glasgow, MT",
        "KGJX": "Grand Junction, CO",
        "KGLD": "Goodland, KS",
        "KGRB": "Green Bay, WI",
        "KGRK": "Fort Hood, TX",
        "KGRR": "Grand Rapids, MI",
        "KGVX": "Gray/Portland, ME",
        "KGWX": "Columbus AFB, MS",
        "KHDX": "Holloman AFB, NM",
        "KHGX": "Houston/Galveston, TX",
        "KHKI": "Hickory, NC",
        "KHPX": "Hopkinsville, KY",
        "KICT": "Wichita, KS",
        "KILN": "Cincinnati/Wilmington, OH",
        "KILX": "Lincoln, IL",
        "KIND": "Indianapolis, IN",
        "KINX": "Tulsa/Inola, OK",
        "KIWA": "Phoenix, AZ",
        "KJAX": "Jacksonville, FL",
        "KJGX": "Macon/Perry, GA",
        "KJKL": "Jackson/Julian, KY",
        "KLBB": "Lubbock, TX",
        "KLCH": "Lake Charles, LA",
        "KLGX": "Langley Hill, WA",
        "KLIX": "New Orleans/Slidell, LA",
        "KLNX": "North Platte, NE",
        "KLOT": "Chicago, IL",
        "KLRX": "Elko, NV",
        "KLSX": "St. Louis, MO",
        "KLTX": "Wilmington, NC",
        "KLVX": "Louisville/Fort Knox, KY",
        "KLWX": "Washington D.C./Sterling, VA",
        "KMHX": "Morehead City, NC",
        "KMKX": "Milwaukee, WI",
        "KMLB": "Melbourne, FL",
        "KMPX": "Minneapolis/Chanhassen, MN",
        "KMQT": "Marquette, MI",
        "KMRX": "Knoxville/Morristown, TN",
        "KMSX": "Missoula, MT",
        "KMTX": "Salt Lake City, UT",
        "KMUX": "San Francisco, CA",
        "KMVX": "Fargo/Grand Forks, ND",
        "KMXX": "Maxwell AFB, AL",
        "KNKX": "San Diego, CA",
        "KNOA": "Memphis, TN",
        "KOAX": "Omaha, NE",
        "KOHX": "Nashville, TN",
        "KOKX": "New York City/Brookhaven, NY",
        "KOTX": "Spokane, WA",
        "KPAH": "Paducah, KY",
        "KPBZ": "Pittsburgh, PA",
        "KPDT": "Pendleton, OR",
        "KPOE": "Fort Polk, LA",
        "KRAX": "Raleigh/Durham, NC",
        "KRGX": "Reno, NV",
        "KRIW": "Riverton, WY",
        "KRLX": "Charleston, WV",
        "KRTX": "Portland, OR",
        "KSFX": "Pocatello/Idaho Falls, ID",
        "KSGF": "Springfield, MO",
    }

    @discord.app_commands.command(
        name="radar",
        description="Get a radar animation loop for a NEXRAD site",
    )
    @discord.app_commands.describe(
        site="4-letter NEXRAD site ID (e.g. KTLX, KFWS)",
        product="Radar product to display",
        frames="Number of frames (default 6, max 20)",
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
        ]
    )
    async def radar_slash(
        self,
        interaction: discord.Interaction,
        site: str,
        product: Choice[str] = None,
        frames: int = 6,
    ):
        await interaction.response.defer(thinking=True)

        site = site.upper().strip()
        product_value = product.value if product else "reflectivity"
        frames = max(2, min(frames, 20))

        if site not in self.RADAR_SITES:
            suggestions = difflib.get_close_matches(site, list(self.RADAR_SITES), n=3, cutoff=0.5)
            if suggestions:
                msg = "`{}` not recognized. Did you mean?\n{}".format(
                    site,
                    "\n".join("  `{}` — {}".format(s, self.RADAR_SITES[s]) for s in suggestions),
                )
            else:
                msg = "`{}` is not a recognized NEXRAD site.".format(site)
            await interaction.followup.send(msg, ephemeral=True)
            return

        self.RADAR_GIF_CACHE.mkdir(parents=True, exist_ok=True)
        out_path = self.RADAR_GIF_CACHE / "{}_{}_{}.gif".format(site, product_value, frames)

        try:
            await _run_radar_cli(site, product_value, frames, out_path)
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

        site_name = self.RADAR_SITES.get(site, site)
        product_label = product.name if product else "Reflectivity"
        embed = discord.Embed(
            title="{} Loop — {} ({})".format(product_label, site, site_name),
            description="{} frames".format(frames),
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


async def _run_radar_cli(site: str, product: str, frames: int, out_path: Path):
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
        "--output",
        str(out_path),
        "--width",
        "800",
        "--height",
        "800",
        "--days-back",
        "3",
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
