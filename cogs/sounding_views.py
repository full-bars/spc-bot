# cogs/sounding_views.py
"""Discord UI views and buttons for the sounding cog."""

import logging
import os
from datetime import datetime, timezone

import discord
from discord import ButtonStyle
from discord.ui import Button, View

from cogs.sounding_utils import (
    fetch_sounding,
    generate_plot,
    get_recent_sounding_times,
    get_user_dark_mode,
)
from config import CACHE_DIR

logger = logging.getLogger("spc_bot")


def _plot_path(station: str, year: str, month: str, day: str, hour: str) -> str:
    return os.path.join(
        CACHE_DIR, f"sounding_{station}_{year}{month}{day}_{hour}z"
    )


async def post_sounding(
    interaction: discord.Interaction,
    station: dict,
    year: str, month: str, day: str, hour: str,
    dark_mode: bool,
    followup: bool = False,
):
    """Fetch data, generate plot, and post to channel."""
    station_id = station.get("icao") or station.get("wmo")
    label = f"{station['name']} ({station_id})"
    time_label = f"{month}-{day}-{year} {hour}z"

    send = interaction.followup.send if followup else interaction.response.send_message

    # Fetch data
    clean_data = await fetch_sounding(station_id, year, month, day, hour)
    if not clean_data:
        # Try adjacent times and suggest them
        times = get_recent_sounding_times(4)
        suggestions = []
        for y, m, d, h in times:
            if (y, m, d, h) != (year, month, day, hour):
                suggestions.append(f"`{m}-{d}-{y} {h}z`")
            if len(suggestions) >= 3:
                break

        embed = discord.Embed(
            title="❌ No Sounding Data Available",
            description=(
                "No data found for **{}** at **{}**.\n\n**Try one of these recent times:**\n{}".format(
                    label, time_label, "\n".join(suggestions)
                )
            ),
            color=discord.Color.red(),
        )
        if followup:
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    # Generate plot
    thinking_embed = discord.Embed(
        title="⏳ Generating Sounding...",
        description=f"Plotting **{label}** at **{time_label}**. This takes ~15 seconds.",
        color=discord.Color.blurple(),
    )
    if followup:
        thinking_msg = await interaction.followup.send(embed=thinking_embed)
    else:
        await interaction.response.send_message(embed=thinking_embed)
        thinking_msg = await interaction.original_response()

    output_path = _plot_path(station_id, year, month, day, hour)
    success = await generate_plot(clean_data, output_path, dark_mode)

    png_path = output_path + ".png"
    if not success or not os.path.exists(png_path):
        error_embed = discord.Embed(
            title="❌ Plot Generation Failed",
            description="Something went wrong generating the sounding plot. Try again.",
            color=discord.Color.red(),
        )
        await thinking_msg.edit(embed=error_embed)
        return

    mode_label = "🌙 Dark" if dark_mode else "☀️ Light"
    caption = (
        f"**RAOB Sounding \u2014 {label}**\n"
        f"Valid: {time_label} | {mode_label} mode"
    )
    try:
        await thinking_msg.delete()
        await interaction.channel.send(caption, files=[discord.File(png_path)])
        logger.info(f"[SOUNDING] Posted {station_id} {year}/{month}/{day} {hour}z")
    except Exception as e:
        logger.error(f"[SOUNDING] Failed to post: {e}", exc_info=True)


# ── Time selection view ───────────────────────────────────────────────────────

class TimeSelectionView(View):
    def __init__(self, station: dict, dark_mode: bool, original_user: discord.User):
        super().__init__(timeout=120)
        self.station = station
        self.dark_mode = dark_mode
        self.original_user = original_user
        self._build_buttons()

    def _build_buttons(self):
        self.clear_items()
        times = get_recent_sounding_times(4)
        for year, month, day, hour in times:
            label = f"{month}-{day}-{year} {hour}z"
            btn = Button(label=label, style=ButtonStyle.green)

            async def cb(interaction, y=year, mo=month, d=day, h=hour):
                await interaction.response.defer()
                await post_sounding(
                    interaction, self.station,
                    y, mo, d, h,
                    self.dark_mode,
                    followup=True,
                )
            btn.callback = cb
            self.add_item(btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.original_user:
            await interaction.response.send_message(
                "This interaction is not yours.", ephemeral=True
            )
            return False
        return True


# ── Station selection view ────────────────────────────────────────────────────

class StationSelectionView(View):
    def __init__(
        self,
        stations: list[dict],
        time_args: tuple | None,
        dark_mode: bool,
        original_user: discord.User,
    ):
        super().__init__(timeout=120)
        self.stations = stations
        self.time_args = time_args  # (year, month, day, hour) or None
        self.dark_mode = dark_mode
        self.original_user = original_user
        self._build_buttons()

    def _build_buttons(self):
        self.clear_items()
        for i, station in enumerate(self.stations):
            station_id = station.get("icao") or station.get("wmo")
            dist = station["dist_km"]
            label = f"{station['name']} ({station_id}) — {dist}km"
            btn = Button(
                label=label[:80],  # Discord label limit
                style=ButtonStyle.blurple,
                row=i,
            )

            async def cb(interaction, s=station):
                if self.time_args:
                    await interaction.response.defer()
                    year, month, day, hour = self.time_args
                    await post_sounding(
                        interaction, s,
                        year, month, day, hour,
                        self.dark_mode,
                        followup=True,
                    )
                else:
                    # Show time picker
                    view = TimeSelectionView(s, self.dark_mode, self.original_user)
                    station_id = s.get("icao") or s.get("wmo")
                    embed = discord.Embed(
                        title=f"Select Time — {s['name']} ({station_id})",
                        description="Choose a sounding time:",
                        color=discord.Color.blurple(),
                    )
                    await interaction.response.send_message(
                        embed=embed, view=view, ephemeral=True
                    )

            btn.callback = cb
            self.add_item(btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.original_user:
            await interaction.response.send_message(
                "This interaction is not yours.", ephemeral=True
            )
            return False
        return True
