# cogs/sounding.py
"""
Sounding cog — observed RAOB sounding plots via SounderPy.
Supports city names, radar site codes, and RAOB station IDs.
"""

import logging
from typing import Optional

import discord
from discord.ext import commands

from cogs.sounding_utils import (
    filter_stations_with_data,
    find_nearest_stations,
    get_raob_stations,
    get_recent_sounding_times,
    get_user_dark_mode,
    parse_sounding_time,
    resolve_location,
    set_user_dark_mode,
)
from cogs.sounding_views import StationSelectionView, post_sounding

logger = logging.getLogger("spc_bot")


class SoundingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

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
