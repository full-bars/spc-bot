# cogs/sounding.py
"""
Sounding cog — observed RAOB sounding plots via SounderPy.
Supports city names, radar site codes, and RAOB station IDs.
"""

import json
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ext import commands, tasks

from cogs.sounding_utils import (
    get_acars_profiles_near,
    get_available_sounding_times_iem,
    get_watch_area_centroid,
    filter_stations_with_data,
    find_nearest_stations,
    get_raob_stations,
    get_recent_sounding_times,
    get_user_dark_mode,
    parse_sounding_time,
    resolve_location,
    set_user_dark_mode,
)
import os
from cogs.sounding_views import StationSelectionView, post_sounding
from cogs.sounding_utils import fetch_acars_sounding, fetch_sounding, generate_plot

logger = logging.getLogger("spc_bot")


class SoundingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._posted_watch_soundings: set = set()  # "watch_num:station:time"
        self.auto_sounding_watches.start()

    def cog_unload(self):
        self.auto_sounding_watches.cancel()

    @tasks.loop(minutes=30)
    async def auto_sounding_watches(self):
        """Post soundings for RAOB stations near active watches at 00z/12z."""
        await self.bot.wait_until_ready()
        from config import SPC_CHANNEL_ID

        now = datetime.now(timezone.utc)
        hour = now.hour
        minute = now.minute

        # Only run within 30 minutes after 00z or 12z
        if not ((0 <= hour < 1) or (12 <= hour < 13)):
            return

        sounding_time = "00" if hour < 12 else "12"
        date_key = now.strftime("%Y-%m-%d")
        time_key = f"{date_key}_{sounding_time}z"

        if not self.bot.state.active_watches:
            return

        channel = self.bot.get_channel(SPC_CHANNEL_ID)
        if not channel:
            return

        logger.info(f"[SOUNDING-AUTO] Checking {len(self.bot.state.active_watches)} active watches for {time_key}")

        try:
            stations_df = await get_raob_stations()
        except Exception as e:
            logger.error(f"[SOUNDING-AUTO] Failed to load station list: {e}")
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

            for station in verified[:3]:
                station_id = station.get("icao") or station.get("wmo")
                post_key = f"{watch_num}:{station_id}:{time_key}"
                if post_key in self._posted_watch_soundings:
                    continue

                logger.info(f"[SOUNDING-AUTO] Posting sounding for {station_id} near {watch_label} #{watch_num}")
                self._posted_watch_soundings.add(post_key)

                clean_data = await fetch_sounding(station_id, year, month, day, sounding_time)
                if not clean_data:
                    logger.warning(f"[SOUNDING-AUTO] No data for {station_id} at {time_key}")
                    continue

                output_path = os.path.join(
                    __import__("config").CACHE_DIR,
                    f"sounding_{station_id}_{year}{month}{day}_{sounding_time}z"
                )
                success = await generate_plot(clean_data, output_path)
                png_path = output_path + ".png"
                if not success or not __import__("os").path.exists(png_path):
                    continue

                caption = (
                    f"**Auto Sounding — {station['name']} ({station_id})**\n"
                    f"Valid: {month}-{day}-{year} {sounding_time}z | "
                    f"Near active {watch_label} #{watch_num}"
                )
                try:
                    await channel.send(caption, files=[discord.File(png_path)])
                    logger.info(f"[SOUNDING-AUTO] Posted {station_id} for watch #{watch_num}")
                except Exception as e:
                    logger.error(f"[SOUNDING-AUTO] Failed to post: {e}")

        # ── ACARS auto-posting ────────────────────────────────────────────
        acars_profiles = await get_acars_profiles_near(lat, lon, max_dist_km=300, hours_back=1)
        for profile in acars_profiles[:2]:
            post_key = f"acars:{watch_num}:{profile['airport']}:{time_key}"
            if post_key in self._posted_watch_soundings:
                continue

            logger.info(f"[SOUNDING-AUTO] Posting ACARS {profile['airport']} near {watch_label} #{watch_num}")
            self._posted_watch_soundings.add(post_key)

            clean_data = await fetch_acars_sounding(
                profile["profile_id"], profile["year"], profile["month"],
                profile["day"], profile["acars_hour"]
            )
            if not clean_data:
                continue

            output_path = __import__("os").path.join(
                __import__("config").CACHE_DIR,
                f"acars_{profile['airport']}_{profile['year']}{profile['month']}{profile['day']}_{profile['acars_hour']}z"
            )
            success = await generate_plot(clean_data, output_path)
            png_path = output_path + ".png"
            if not success or not __import__("os").path.exists(png_path):
                continue

            caption = (
                f"**Auto ACARS \u2014 {profile['airport']}**\n"
                f"Valid: {profile['time_label']} | Near active {watch_label} #{watch_num}"
            )
            try:
                await channel.send(caption, files=[discord.File(png_path)])
                logger.info(f"[SOUNDING-AUTO] Posted ACARS {profile['airport']} for watch #{watch_num}")
            except Exception as e:
                logger.error(f"[SOUNDING-AUTO] Failed to post ACARS: {e}")

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
            logger.error(f"[SOUNDING] Location resolution error: {e}", exc_info=True)
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
            logger.error(f"[SOUNDING] Station lookup error: {e}", exc_info=True)
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
                avail = await get_available_sounding_times_iem(sid, hours_back=36)
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

        from cogs.sounding_views import CombinedSoundingView
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
