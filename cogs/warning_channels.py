# cogs/warning_channels.py
"""Slash commands for configuring per-product-type channel routing."""

from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import (
    FFW_CHANNEL_ID,
    SPS_CHANNEL_ID,
    SVR_CHANNEL_ID,
    TOR_CHANNEL_ID,
    TROPICAL_CHANNEL_ID,
    WARNINGS_CHANNEL_ID,
)
from utils.state_store import get_state, set_state

logger = logging.getLogger("spc_bot.warning_channels")

_PHENOM_LABELS = {
    "tor": "Tornado Warning",
    "svr": "Severe Thunderstorm Warning",
    "ffw": "Flash Flood Warning",
    "sps": "Special Weather Statement",
    "tropical": "NHC Tropical Products",
}

_STATIC_DEFAULTS = {
    "tor": TOR_CHANNEL_ID,
    "svr": SVR_CHANNEL_ID,
    "ffw": FFW_CHANNEL_ID,
    "sps": SPS_CHANNEL_ID,
    "tropical": TROPICAL_CHANNEL_ID,
}


class ChannelsCog(commands.Cog):
    group = app_commands.Group(
        name="channels", description="Configure which channel each product type posts to"
    )

    @group.command(name="assign", description="Assign a product type to a channel, or unassign it")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        product_type="Product type to configure",
        channel="Channel to post this product type to. Omit to stop posting it entirely.",
    )
    @app_commands.choices(
        product_type=[
            app_commands.Choice(name="Tornado Warning", value="tor"),
            app_commands.Choice(name="Severe Thunderstorm Warning", value="svr"),
            app_commands.Choice(name="Flash Flood Warning", value="ffw"),
            app_commands.Choice(name="Special Weather Statement", value="sps"),
            app_commands.Choice(name="NHC Tropical Products", value="tropical"),
        ]
    )
    async def assign(
        self,
        interaction: discord.Interaction,
        product_type: str,
        channel: Optional[discord.TextChannel] = None,
    ):
        label = _PHENOM_LABELS[product_type]
        if channel is None:
            await set_state(f"warning_channel:{product_type}", "disabled")
            await interaction.response.send_message(
                f"🔕 **{label}** will no longer be posted.",
                ephemeral=True,
            )
            return

        await set_state(f"warning_channel:{product_type}", str(channel.id))
        await interaction.response.send_message(
            f"✅ **{label}** will now post to {channel.mention}",
            ephemeral=True,
        )

    @group.command(name="list", description="Show current product channel routing configuration")
    async def list_channels(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        lines = []
        for code, label in _PHENOM_LABELS.items():
            override = await get_state(f"warning_channel:{code}")
            if override == "disabled":
                lines.append(f"**{label}**: 🔕 Disabled")
            elif override:
                lines.append(f"**{label}**: <#{override}>")
            else:
                static_id = _STATIC_DEFAULTS.get(code, WARNINGS_CHANNEL_ID)
                lines.append(f"**{label}**: <#{static_id}> *(default)*")

        embed = discord.Embed(
            title="⚙️ Channel Routing Configuration",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ChannelsCog())
