# cogs/sounding.py
"""
Sounding cog — observed RAOB sounding plots via SounderPy.
Supports city names, radar site codes, and RAOB station IDs.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ext import commands, tasks

from cogs.sounding_utils import (
    fetch_acars_sounding,
    fetch_sounding,
    filter_stations_with_data,
    find_nearest_stations,
    generate_plot,
    get_acars_profiles_near,
    get_available_sounding_times_iem,
    get_md_area_centroid,
    get_raob_stations,
    get_user_dark_mode,
    get_watch_area_centroid,
    parse_sounding_time,
    resolve_location,
    set_user_dark_mode,
    sounding_quality_warning,
)
from cogs.sounding_views import CombinedSoundingView, post_sounding
from config import CACHE_DIR, SOUNDING_CHANNEL_ID

logger = logging.getLogger("spc_bot")


class SoundingCog(commands.Cog):
    MANAGED_TASK_NAMES = [("auto_sounding_watches", "auto_sounding_watches")]

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._posted_watch_soundings: set = set()  # "watch_num:station:time"
        self._handled_watches: set = set()  # "watch_num"
        self.auto_sounding_watches.start()

    async def cog_unload(self):
        self.auto_sounding_watches.cancel()

    async def prewarm_soundings_for_md(self, md_num: str, raw_text: str):
        """
        Background task to 'warm' the cache for soundings near an MD.
        Triggered when MesoscaleCog detects a high probability of watch issuance.
        """
        centroid = await get_md_area_centroid(raw_text)
        if not centroid:
            logger.debug(f"[SOUNDING-PREWARM] No centroid for MD #{md_num}")
            return

        lat, lon = centroid
        logger.info(f"[SOUNDING-PREWARM] Warming cache for MD #{md_num} at {lat:.2f}, {lon:.2f}")

        try:
            stations_df = await get_raob_stations()
        except Exception as e:
            logger.exception(f"[SOUNDING-PREWARM] Failed to load station list: {e}")
            return

        candidates = find_nearest_stations(lat, lon, stations_df, n=4)
        candidates = [s for s in candidates if s.get("icao") or s.get("wmo")]

        # We don't post, just fetch to fill the internal/disk caches
        for station in candidates:
            station_id = station.get("icao") or station.get("wmo")
            logger.debug(f"[SOUNDING-PREWARM] Pre-fetching IEM availability for {station_id}")
            # This fills the get_available_sounding_times_iem internal cache
            await get_available_sounding_times_iem(station_id, hours_back=24, skip_cache=False)

    async def post_soundings_for_watch(self, watch_num: str, nws_info: dict, channel):

        """
        Triggered immediately when a new watch is posted.
        Finds nearest RAOB stations using the most recent IEM-available sounding
        time (any hour, not locked to 00z/12z), posts up to 3 RAOB + 2 ACARS.
        """
        if watch_num in self._handled_watches:
            return

        # Use SOUNDING_CHANNEL_ID if configured, fallback to passed channel
        target_channel = self.bot.get_channel(SOUNDING_CHANNEL_ID) or channel
        
        affected_zones = nws_info.get("affected_zones", []) if isinstance(nws_info, dict) else []
        if not affected_zones:
            logger.warning(f"[SOUNDING-AUTO] No affected zones for watch #{watch_num} — skipping")
            return

        self._handled_watches.add(watch_num)
        wtype = nws_info.get("type", "SVR") if isinstance(nws_info, dict) else "SVR"
        watch_label = "Tornado Watch" if wtype == "TORNADO" else "SVR Watch"

        centroid = await get_watch_area_centroid(affected_zones)
        if not centroid:
            logger.warning(f"[SOUNDING-AUTO] Could not get centroid for watch #{watch_num}")
            return
        lat, lon = centroid

        try:
            stations_df = await get_raob_stations()
        except Exception as e:
            logger.exception(f"[SOUNDING-AUTO] Failed to load station list: {e}")
            return

        candidates = find_nearest_stations(lat, lon, stations_df, n=6)
        candidates = [s for s in candidates if s.get("icao") or s.get("wmo")]
        verified = await filter_stations_with_data(candidates)

        # ── Phase 1: gather IEM availability for all stations concurrently ──
        async def _check_avail(station):
            sid = station.get("icao") or station.get("wmo")
            avail = await get_available_sounding_times_iem(sid, hours_back=24, skip_cache=True)
            if not avail:
                return None
            y, mo, d, h = avail[0]
            tkey = f"{y}-{mo}-{d}_{h}z"
            pkey = f"{watch_num}:{sid}:{tkey}"
            if pkey in self._posted_watch_soundings:
                return None
            return station, sid, y, mo, d, h, tkey, pkey

        avail_results = await asyncio.gather(*[_check_avail(s) for s in verified[:3]])
        to_fetch = [r for r in avail_results if r]

        # Claim post keys before launching parallel fetches to prevent double-posts.
        for *_, pkey in to_fetch:
            self._posted_watch_soundings.add(pkey)

        # ── Phase 1: fetch all sounding data concurrently ─────────────────
        async def _fetch_raob(station, sid, y, mo, d, h, tkey, pkey):
            logger.info(f"[SOUNDING-AUTO] Fetching {sid} {h}z near {watch_label} #{watch_num}")
            data = await fetch_sounding(
                sid, y, mo, d, h,
                station_name=station["name"],
                lat=station["lat"], lon=station["lon"],
            )
            if not data:
                logger.warning(f"[SOUNDING-AUTO] No data for {sid} at {tkey}")
            return station, sid, y, mo, d, h, data

        fetch_results = await asyncio.gather(*[_fetch_raob(*r) for r in to_fetch])

        # ── Phase 2: generate all plots concurrently (ProcessPoolExecutor) ─
        plot_jobs = []
        for station, sid, y, mo, d, h, data in fetch_results:
            if not data:
                continue
            opath = os.path.join(CACHE_DIR, f"sounding_{sid}_{y}{mo}{d}_{h}z")
            plot_jobs.append((station, sid, y, mo, d, h, data, opath))

        plot_results = await asyncio.gather(*[
            generate_plot(data, opath)
            for *_, data, opath in plot_jobs
        ])

        for (station, sid, y, mo, d, h, data, opath), success in zip(plot_jobs, plot_results):
            png_path = opath + ".png"
            if not success or not os.path.exists(png_path):
                continue
            caption = (
                f"**Auto Sounding — {station['name']} ({sid})**\n"
                f"Valid: {mo}-{d}-{y} {h}z | Near active {watch_label} #{watch_num}"
            )
            qwarn = sounding_quality_warning(data)
            if qwarn:
                caption += f"\n{qwarn}"
            try:
                await target_channel.send(caption, files=[discord.File(png_path)])
                logger.info(f"[SOUNDING-AUTO] Posted {sid} for watch #{watch_num}")
            except Exception as e:
                logger.exception(f"[SOUNDING-AUTO] Failed to post {sid}: {e}")

        acars_profiles = await get_acars_profiles_near(lat, lon, max_dist_km=300, hours_back=1)
        acars_eligible = []
        for profile in acars_profiles[:2]:
            post_key = (
                f"acars:{watch_num}:{profile['airport']}:"
                f"{profile['year']}{profile['month']}{profile['day']}_{profile['acars_hour']}z"
            )
            if post_key not in self._posted_watch_soundings:
                self._posted_watch_soundings.add(post_key)
                acars_eligible.append(profile)

        async def _fetch_acars(p):
            logger.info(f"[SOUNDING-AUTO] Fetching ACARS {p['airport']} near {watch_label} #{watch_num}")
            data = await fetch_acars_sounding(
                p["profile_id"], p["year"], p["month"], p["day"], p["acars_hour"]
            )
            return p, data

        acars_fetched = await asyncio.gather(*[_fetch_acars(p) for p in acars_eligible])

        acars_plot_jobs = []
        for p, data in acars_fetched:
            if not data:
                continue
            opath = os.path.join(
                CACHE_DIR,
                f"acars_{p['airport']}_{p['year']}{p['month']}{p['day']}_{p['acars_hour']}z"
            )
            acars_plot_jobs.append((p, data, opath))

        acars_plot_results = await asyncio.gather(*[
            generate_plot(data, opath) for _, data, opath in acars_plot_jobs
        ])

        for (p, data, opath), success in zip(acars_plot_jobs, acars_plot_results):
            png_path = opath + ".png"
            if not success or not os.path.exists(png_path):
                continue
            caption = (
                f"**Auto ACARS — {p['airport']}**\n"
                f"Valid: {p['time_label']} | Near active {watch_label} #{watch_num}"
            )
            try:
                await channel.send(caption, files=[discord.File(png_path)])
                logger.info(f"[SOUNDING-AUTO] Posted ACARS {p['airport']} for watch #{watch_num}")
            except Exception as e:
                logger.exception(f"[SOUNDING-AUTO] Failed to post ACARS: {e}")

    @tasks.loop(minutes=30)
    async def auto_sounding_watches(self):
        """Post soundings for RAOB stations near active watches at 00z/12z."""
        await self.bot.wait_until_ready()
        now = datetime.now(timezone.utc)
        hour = now.hour

        # Only run within 30 minutes after 00z or 12z
        if not ((0 <= hour < 1) or (12 <= hour < 13)):
            return

        sounding_time = "00" if hour < 12 else "12"
        date_key = now.strftime("%Y-%m-%d")
        time_key = f"{date_key}_{sounding_time}z"

        if not self.bot.state.active_watches:
            return

        channel = self.bot.get_channel(SOUNDING_CHANNEL_ID)
        if not channel:
            return

        logger.info(f"[SOUNDING-AUTO] Checking {len(self.bot.state.active_watches)} active watches for {time_key}")

        try:
            stations_df = await get_raob_stations()
        except Exception as e:
            logger.exception(f"[SOUNDING-AUTO] Failed to load station list: {e}")
            return

        year = now.strftime("%Y")
        month = now.strftime("%m")
        day = now.strftime("%d")

        for watch_num, info in list(self.bot.state.active_watches.items()):
            affected_zones = info.get("affected_zones", []) if isinstance(info, dict) else []
            if not affected_zones:
                continue

            centroid = await get_watch_area_centroid(affected_zones)
            if not centroid:
                logger.warning(f"[SOUNDING-AUTO] Could not get centroid for watch #{watch_num}")
                continue

            lat, lon = centroid
            candidates = find_nearest_stations(lat, lon, stations_df, n=6)
            candidates = [s for s in candidates if s.get("icao") or s.get("wmo")]

            verified = await filter_stations_with_data(candidates)
            if not verified:
                logger.info(f"[SOUNDING-AUTO] No verified stations near watch #{watch_num}")
                continue

            wtype = info.get("type", "SVR") if isinstance(info, dict) else "SVR"
            watch_label = "Tornado Watch" if wtype == "TORNADO" else "SVR Watch"

            # Collect eligible stations and claim their post keys before
            # launching parallel fetches to prevent double-posts.
            eligible = []
            for station in verified[:3]:
                station_id = station.get("icao") or station.get("wmo")
                post_key = f"{watch_num}:{station_id}:{time_key}"
                if post_key not in self._posted_watch_soundings:
                    self._posted_watch_soundings.add(post_key)
                    eligible.append((station, station_id))

            # ── Phase 1: fetch all RAOB data concurrently ─────────────────
            async def _fetch_std(station, sid):
                logger.info(
                    f"[SOUNDING-AUTO] Fetching {sid} {sounding_time}z"
                    f" near {watch_label} #{watch_num}"
                )
                data = await fetch_sounding(sid, year, month, day, sounding_time)
                if not data:
                    logger.warning(f"[SOUNDING-AUTO] No data for {sid} at {time_key}")
                return station, sid, data

            fetch_results = await asyncio.gather(*[_fetch_std(s, sid) for s, sid in eligible])

            # ── Phase 2: generate all plots concurrently ───────────────────
            import config as _cfg
            plot_jobs = []
            for station, sid, data in fetch_results:
                if not data:
                    continue
                opath = os.path.join(
                    _cfg.CACHE_DIR,
                    f"sounding_{sid}_{year}{month}{day}_{sounding_time}z"
                )
                plot_jobs.append((station, sid, data, opath))

            plot_results = await asyncio.gather(*[
                generate_plot(data, opath)
                for *_, data, opath in plot_jobs
            ])

            for (station, sid, data, opath), success in zip(plot_jobs, plot_results):
                png_path = opath + ".png"
                if not success or not os.path.exists(png_path):
                    continue
                caption = (
                    f"**Auto Sounding — {station['name']} ({sid})**\n"
                    f"Valid: {month}-{day}-{year} {sounding_time}z | "
                    f"Near active {watch_label} #{watch_num}"
                )
                qwarn = sounding_quality_warning(data)
                if qwarn:
                    caption += f"\n{qwarn}"
                try:
                    await channel.send(caption, files=[discord.File(png_path)])
                    logger.info(f"[SOUNDING-AUTO] Posted {sid} for watch #{watch_num}")
                except Exception as e:
                    logger.exception(f"[SOUNDING-AUTO] Failed to post: {e}")

        # ── ACARS auto-posting ────────────────────────────────────────────
            acars_profiles = await get_acars_profiles_near(lat, lon, max_dist_km=300, hours_back=1)
            acars_eligible2 = []
            for profile in acars_profiles[:2]:
                post_key = f"acars:{watch_num}:{profile['airport']}:{time_key}"
                if post_key not in self._posted_watch_soundings:
                    self._posted_watch_soundings.add(post_key)
                    acars_eligible2.append(profile)

            async def _fetch_acars2(p):
                logger.info(
                    f"[SOUNDING-AUTO] Fetching ACARS {p['airport']}"
                    f" near {watch_label} #{watch_num}"
                )
                data = await fetch_acars_sounding(
                    p["profile_id"], p["year"], p["month"], p["day"], p["acars_hour"]
                )
                return p, data

            acars_fetched2 = await asyncio.gather(*[_fetch_acars2(p) for p in acars_eligible2])

            acars_plot_jobs2 = []
            for p, data in acars_fetched2:
                if not data:
                    continue
                opath = os.path.join(
                    _cfg.CACHE_DIR,
                    f"acars_{p['airport']}_{p['year']}{p['month']}{p['day']}_{p['acars_hour']}z"
                )
                acars_plot_jobs2.append((p, data, opath))

            acars_plot_results2 = await asyncio.gather(*[
                generate_plot(data, opath) for _, data, opath in acars_plot_jobs2
            ])

            for (p, data, opath), success in zip(acars_plot_jobs2, acars_plot_results2):
                png_path = opath + ".png"
                if not success or not os.path.exists(png_path):
                    continue
                caption = (
                    f"**Auto ACARS \u2014 {p['airport']}**\n"
                    f"Valid: {p['time_label']} | Near active {watch_label} #{watch_num}"
                )
                try:
                    await channel.send(caption, files=[discord.File(png_path)])
                    logger.info(f"[SOUNDING-AUTO] Posted ACARS {p['airport']} for watch #{watch_num}")
                except Exception as e:
                    logger.exception(f"[SOUNDING-AUTO] Failed to post ACARS: {e}")

    @discord.app_commands.command(
        name="sounding",
        description="Plot an observed RAOB sounding for a location",
    )
    @discord.app_commands.describe(
        location="City name, state, radar site (e.g. KTLX), or RAOB station (e.g. OUN)",
        time="Sounding time: MM-DD-YYYY HHz (e.g. 04-11-2026 18z) — any hour supported",
        dark="Use dark mode (saves your preference)",
    )
    async def sounding(
        self,
        interaction: discord.Interaction,
        location: str,
        time: Optional[str] = None,
        dark: Optional[bool] = None,
    ):
        await interaction.response.defer(thinking=True)

        # Resolve dark mode preference
        if dark is not None:
            await set_user_dark_mode(interaction.user.id, dark)
            dark_mode = dark
        else:
            dark_mode = await get_user_dark_mode(interaction.user.id)

        # Parse time if provided
        time_args = None
        if time:
            try:
                time_args = parse_sounding_time(time)
            except ValueError as e:
                await interaction.followup.send(str(e), ephemeral=True)
                return

        # Resolve location to lat/lon
        try:
            lat, lon, location_desc = await resolve_location(location)
        except ValueError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return
        except Exception as e:
            logger.exception(f"[SOUNDING] Location resolution error: {e}")
            await interaction.followup.send(
                "Could not resolve that location. Try a city name or station code.",
                ephemeral=True,
            )
            return

        # Find nearest RAOB stations
        try:
            stations_df = await get_raob_stations()
            nearest = find_nearest_stations(lat, lon, stations_df, n=3)
        except Exception as e:
            logger.exception(f"[SOUNDING] Station lookup error: {e}")
            await interaction.followup.send(
                "Could not load station list. Try again later.",
                ephemeral=True,
            )
            return

        # Filter to stations with valid IDs
        nearest = [s for s in nearest if s.get("icao") or s.get("wmo")]

        if not nearest:
            await interaction.followup.send(
                "No upper air stations found near that location.",
                ephemeral=True,
            )
            return

        # Verify stations actually have data in Wyoming archive
        # Search wider if needed (up to 10 candidates)
        candidates = find_nearest_stations(lat, lon, stations_df, n=6)
        candidates = [s for s in candidates if s.get("icao") or s.get("wmo")]

        checking_embed = discord.Embed(
            title="⏳ Checking Station Availability...",
            description="Verifying which nearby stations have sounding data...",
            color=discord.Color.blurple(),
        )
        await interaction.followup.send(embed=checking_embed)
        status_msg = await interaction.original_response()

        verified = await filter_stations_with_data(candidates)
        nearest = verified[:3]

        # Run RAOB verification and ACARS search concurrently
        acars_task = asyncio.create_task(get_acars_profiles_near(lat, lon, max_dist_km=400))
        verified = await filter_stations_with_data(candidates)
        acars_profiles = await acars_task
        nearest = verified[:3]

        if not nearest and not acars_profiles:
            error_embed = discord.Embed(
                title="❌ No Sounding Data Available",
                description=(
                    "No nearby upper air stations or aircraft profiles found.\n"
                    "Try a different location."
                ),
                color=discord.Color.red(),
            )
            await status_msg.edit(embed=error_embed)
            return

        await status_msg.delete()

        # If only one RAOB station, no ACARS, and time specified — go straight to plot
        if len(nearest) == 1 and not acars_profiles and time_args:
            year, month, day, hour = time_args
            await post_sounding(
                interaction, nearest[0],
                year, month, day, hour,
                dark_mode, followup=True,
            )
            return

        # Build combined embed
        description_lines = []

        if nearest:
            description_lines.append("**\U0001f4e1 RAOB Upper Air Stations:**")
            for s in nearest:
                sid = s.get("icao") or s.get("wmo")
                avail = await get_available_sounding_times_iem(sid, hours_back=36, skip_cache=True)
                time_strs = [f"`{t[3]}z`" for t in avail[:4]]
                times_note = " | ".join(time_strs) if time_strs else "no recent data"
                description_lines.append(
                    "• {} `{}` — {} km | {}".format(s["name"], sid, s["dist_km"], times_note)
                )

        if acars_profiles:
            description_lines.append("")
            description_lines.append("**\u2708\ufe0f ACARS Aircraft Profiles:**")
            for p in acars_profiles:
                description_lines.append(
                    "• `{}` — {} km | {}".format(p["airport"], p["dist_km"], p["time_label"])
                )

        embed = discord.Embed(
            title="Nearest Sounding Data to {}".format(location_desc),
            description="\n".join(description_lines),
            color=discord.Color.blurple(),
        )
        mode_str = "\U0001f319 Dark" if dark_mode else "\u2600\ufe0f Light"
        embed.set_footer(text="Mode: {} | Select below".format(mode_str))

        view = CombinedSoundingView(
            raob_stations=nearest,
            acars_profiles=acars_profiles,
            time_args=time_args,
            dark_mode=dark_mode,
            original_user=interaction.user,
        )
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)



async def setup(bot: commands.Bot):
    await bot.add_cog(SoundingCog(bot))
