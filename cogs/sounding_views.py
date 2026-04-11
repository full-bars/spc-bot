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
    status_msg: discord.Message = None,
    messages_to_delete: list = None,
):
    """
    Fetch data, generate plot, and post to channel.
    If data is unavailable, automatically tries previous sounding times.
    status_msg: an existing message to edit for status updates (optional)
    messages_to_delete: list of ephemeral messages to delete on success
    """
    station_id = station.get("icao") or station.get("wmo")
    label = f"{station['name']} ({station_id})"
    messages_to_delete = messages_to_delete or []

    # Try the requested time, then fall back to previous times
    all_times = [(year, month, day, hour)] + [
        (y, mo, d, h) for y, mo, d, h in get_recent_sounding_times(6)
        if (y, mo, d, h) != (year, month, day, hour)
    ]

    clean_data = None
    used_year, used_month, used_day, used_hour = year, month, day, hour
    fallback_note = ""

    for y, mo, d, h in all_times:
        time_label = f"{mo}-{d}-{y} {h}z"

        # Update status
        thinking_embed = discord.Embed(
            title="⏳ Fetching Sounding Data...",
            description=f"Checking **{label}** at **{time_label}**...",
            color=discord.Color.blurple(),
        )
        if status_msg:
            await status_msg.edit(embed=thinking_embed)
        else:
            await interaction.followup.send(embed=thinking_embed, ephemeral=True)
            status_msg = await interaction.original_response()

        clean_data = await fetch_sounding(station_id, y, mo, d, h)
        if clean_data:
            used_year, used_month, used_day, used_hour = y, mo, d, h
            if (y, mo, d, h) != (year, month, day, hour):
                orig_label = f"{month}-{day}-{year} {hour}z"
                fallback_note = f" (no data for {orig_label}, showing {time_label})"
            break

    if not clean_data:
        error_embed = discord.Embed(
            title="❌ No Sounding Data Available",
            description=(
                "Could not find data for **{}** at any recent time.\n"
                "The Wyoming archive may be temporarily unavailable.".format(label)
            ),
            color=discord.Color.red(),
        )
        if status_msg:
            await status_msg.edit(embed=error_embed)
        return

    # Generate plot
    time_label = f"{used_month}-{used_day}-{used_year} {used_hour}z"
    plotting_embed = discord.Embed(
        title="⏳ Generating Sounding Plot...",
        description=f"Plotting **{label}** at **{time_label}**. This takes ~15 seconds.",
        color=discord.Color.blurple(),
    )
    if status_msg:
        await status_msg.edit(embed=plotting_embed)

    output_path = _plot_path(station_id, used_year, used_month, used_day, used_hour)
    success = await generate_plot(clean_data, output_path, dark_mode)
    png_path = output_path + ".png"

    if not success or not os.path.exists(png_path):
        error_embed = discord.Embed(
            title="❌ Plot Generation Failed",
            description="Something went wrong generating the sounding plot. Try again.",
            color=discord.Color.red(),
        )
        if status_msg:
            await status_msg.edit(embed=error_embed)
        return

    mode_label = "\U0001f319 Dark" if dark_mode else "\u2600\ufe0f Light"
    caption = "**RAOB Sounding \u2014 {}**\nValid: {} | {} mode{}".format(
        label, time_label, mode_label, fallback_note
    )

    try:
        await interaction.channel.send(caption, files=[discord.File(png_path)])
        logger.info(f"[SOUNDING] Posted {station_id} {used_year}/{used_month}/{used_day} {used_hour}z")

        # Clean up ephemeral messages on success
        if status_msg:
            try:
                await status_msg.delete()
            except Exception:
                pass
        for msg in messages_to_delete:
            try:
                await msg.delete()
            except Exception:
                pass

    except Exception as e:
        logger.error(f"[SOUNDING] Failed to post: {e}", exc_info=True)


# ── Time selection view ───────────────────────────────────────────────────────

class TimeSelectionView(View):
    def __init__(
        self,
        station: dict,
        dark_mode: bool,
        original_user: discord.User,
        messages_to_delete: list = None,
    ):
        super().__init__(timeout=120)
        self.station = station
        self.dark_mode = dark_mode
        self.original_user = original_user
        self.messages_to_delete = messages_to_delete or []
        self._build_buttons()

    def _build_buttons(self):
        self.clear_items()
        times = get_recent_sounding_times(4)
        for year, month, day, hour in times:
            label = f"{month}-{day}-{year} {hour}z"
            btn = Button(label=label, style=ButtonStyle.green)

            async def cb(
                interaction, y=year, mo=month, d=day, h=hour
            ):
                # Immediately show loading state by editing this message
                loading_embed = discord.Embed(
                    title="⏳ Fetching Sounding Data...",
                    description="Contacting Wyoming archive...",
                    color=discord.Color.blurple(),
                )
                await interaction.response.edit_message(
                    embed=loading_embed, view=None
                )
                status_msg = await interaction.original_response()

                await post_sounding(
                    interaction, self.station,
                    y, mo, d, h,
                    self.dark_mode,
                    status_msg=status_msg,
                    messages_to_delete=self.messages_to_delete,
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
        stations: list,
        time_args: tuple | None,
        dark_mode: bool,
        original_user: discord.User,
    ):
        super().__init__(timeout=120)
        self.stations = stations
        self.time_args = time_args
        self.dark_mode = dark_mode
        self.original_user = original_user
        self._build_buttons()

    def _build_buttons(self):
        self.clear_items()
        for i, station in enumerate(self.stations):
            station_id = station.get("icao") or station.get("wmo")
            dist = station["dist_km"]
            label = f"{station['name']} ({station_id}) \u2014 {dist}km"
            btn = Button(
                label=label[:80],
                style=ButtonStyle.blurple,
                row=i,
            )

            async def cb(interaction, s=station):
                if self.time_args:
                    loading_embed = discord.Embed(
                        title="⏳ Fetching Sounding Data...",
                        description="Contacting Wyoming archive...",
                        color=discord.Color.blurple(),
                    )
                    await interaction.response.edit_message(
                        embed=loading_embed, view=None
                    )
                    status_msg = await interaction.original_response()
                    year, month, day, hour = self.time_args
                    await post_sounding(
                        interaction, s,
                        year, month, day, hour,
                        self.dark_mode,
                        status_msg=status_msg,
                        messages_to_delete=[],
                    )
                else:
                    # Show time picker, pass this message for cleanup
                    view = TimeSelectionView(
                        s, self.dark_mode, self.original_user,
                        messages_to_delete=[],
                    )
                    station_id = s.get("icao") or s.get("wmo")
                    embed = discord.Embed(
                        title=f"Select Time \u2014 {s['name']} ({station_id})",
                        description="Choose a sounding time:",
                        color=discord.Color.blurple(),
                    )
                    await interaction.response.edit_message(
                        embed=embed, view=view
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
