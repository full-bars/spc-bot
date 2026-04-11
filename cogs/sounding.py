# cogs/sounding.py
"""
Sounding cog — observed RAOB sounding plots via SounderPy.
Supports city names, radar site codes, and RAOB station IDs.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ext import commands, tasks

from cogs.sounding_utils import (
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
from cogs.sounding_utils import fetch_sounding, generate_plot

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
        from utils.cache import active_watches

        now = datetime.now(timezone.utc)
        hour = now.hour
        minute = now.minute

        # Only run within 30 minutes after 00z or 12z
        if not ((0 <= hour < 1) or (12 <= hour < 13)):
            return

        sounding_time = "00" if hour < 12 else "12"
        date_key = now.strftime("%Y-%m-%d")
        time_key = f"{date_key}_{sounding_time}z"

        if not active_watches:
            return

        channel = self.bot.get_channel(SPC_CHANNEL_ID)
        if not channel:
            return

        logger.info(f"[SOUNDING-AUTO] Checking {len(active_watches)} active watches for {time_key}")

        try:
            stations_df = await get_raob_stations()
        except Exception as e:
            logger.error(f"[SOUNDING-AUTO] Failed to load station list: {e}")
            return

        year = now.strftime("%Y")
        month = now.strftime("%m")
        day = now.strftime("%d")

        for watch_num, info in list(active_watches.items()):
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

    @discord.app_commands.command(
        name="sounding",
        description="Plot an observed RAOB sounding for a location",
    )
    @discord.app_commands.describe(
        location="City name, state, radar site (e.g. KTLX), or RAOB station (e.g. OUN)",
        time="Sounding time: MM-DD-YYYY 00z or MM-DD-YYYY 12z (default: most recent)",
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

        if not nearest:
            error_embed = discord.Embed(
                title="❌ No Sounding Data Available",
                description=(
                    "No nearby upper air stations have recent sounding data "
                    "in the Wyoming archive.\nTry a different location."
                ),
                color=discord.Color.red(),
            )
            await status_msg.edit(embed=error_embed)
            return

        await status_msg.delete()

        # If only one station and time specified, go straight to plot
        if len(nearest) == 1 and time_args:
            year, month, day, hour = time_args
            await post_sounding(
                interaction, nearest[0],
                year, month, day, hour,
                dark_mode, followup=True,
            )
            return

        # Build station selection embed
        lines = []
        for i, s in enumerate(nearest, 1):
            sid = s.get("icao") or s.get("wmo")
            lines.append(
                f"**{i}.** {s['name']} `{sid}` — {s['dist_km']} km away"
            )

        time_note = ""
        if time_args:
            y, mo, d, h = time_args
            time_note = "\nTime: **{}-{}-{} {}z**".format(mo, d, y, h)
        else:
            recent = get_recent_sounding_times(2)
            time_strs = [f"`{mo}-{d}-{y} {h}z`" for y, mo, d, h in recent]
            time_note = "\nWill show time picker \u2014 recent times: {}".format(', '.join(time_strs))

        embed = discord.Embed(
            title=f"Nearest Upper Air Stations to {location_desc}",
            description="\n".join(lines) + time_note,
            color=discord.Color.blurple(),
        )
        mode_str = "🌙 Dark" if dark_mode else "☀️ Light"
        embed.set_footer(text=f"Mode: {mode_str} | Select a station below")

        view = StationSelectionView(
            stations=nearest,
            time_args=time_args,
            dark_mode=dark_mode,
            original_user=interaction.user,
        )
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(SoundingCog(bot))
