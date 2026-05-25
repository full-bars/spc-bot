# cogs/warning_channels.py
"""Slash commands for configuring per-type warning channel routing."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import (
    FFW_CHANNEL_ID,
    SPS_CHANNEL_ID,
    SVR_CHANNEL_ID,
    TOR_CHANNEL_ID,
    WARNINGS_CHANNEL_ID,
)
from utils.state_store import get_state, set_state

logger = logging.getLogger("spc_bot.warning_channels")

_PHENOM_LABELS = {
    "tor": "Tornado Warning",
    "svr": "Severe Thunderstorm Warning",
    "ffw": "Flash Flood Warning",
    "sps": "Special Weather Statement",
}

_STATIC_DEFAULTS = {
    "tor": TOR_CHANNEL_ID,
    "svr": SVR_CHANNEL_ID,
    "ffw": FFW_CHANNEL_ID,
    "sps": SPS_CHANNEL_ID,
}


class DisableWarningsView(discord.ui.View):
    def __init__(self, enabled: list[tuple[str, str]]):
        super().__init__(timeout=120)
        options = [discord.SelectOption(label=label, value=code) for code, label in enabled]
        self.select = discord.ui.Select(
            placeholder="Choose warning type(s) to disable…",
            min_values=1,
            max_values=len(options),
            options=options,
        )
        self.select.callback = self.on_select
        self.add_item(self.select)

    async def on_select(self, interaction: discord.Interaction):
        for code in self.select.values:
            await set_state(f"warning_channel:{code}", "disabled")
        disabled_labels = [_PHENOM_LABELS[c] for c in self.select.values]
        await interaction.response.edit_message(
            content=f"🔕 Disabled: {', '.join(disabled_labels)}",
            view=None,
        )


class WarningChannelsCog(commands.Cog):
    @app_commands.command(
        name="enablewarnings", description="Route a warning type to a specific channel"
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        warning_type="Warning product type to configure",
        channel="Channel where these warnings will be posted",
    )
    @app_commands.choices(
        warning_type=[
            app_commands.Choice(name="Tornado Warning", value="tor"),
            app_commands.Choice(name="Severe Thunderstorm Warning", value="svr"),
            app_commands.Choice(name="Flash Flood Warning", value="ffw"),
            app_commands.Choice(name="Special Weather Statement", value="sps"),
        ]
    )
    async def enable_warnings(
        self,
        interaction: discord.Interaction,
        warning_type: str,
        channel: discord.TextChannel,
    ):
        await set_state(f"warning_channel:{warning_type}", str(channel.id))
        label = _PHENOM_LABELS[warning_type]
        await interaction.response.send_message(
            f"✅ **{label}** will now post to {channel.mention}",
            ephemeral=True,
        )

    @app_commands.command(
        name="displaysetup", description="Show current warning channel routing configuration"
    )
    async def display_setup(self, interaction: discord.Interaction):
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
            title="⚙️ Warning Channel Configuration",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="disablewarnings", description="Stop posting a warning type")
    @app_commands.default_permissions(manage_guild=True)
    async def disable_warnings(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        enabled: list[tuple[str, str]] = []
        for code, label in _PHENOM_LABELS.items():
            override = await get_state(f"warning_channel:{code}")
            if override != "disabled":
                enabled.append((code, label))

        if not enabled:
            await interaction.followup.send(
                "All warning types are already disabled.", ephemeral=True
            )
            return

        view = DisableWarningsView(enabled)
        await interaction.followup.send(
            "Select warning type(s) to disable:", view=view, ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(WarningChannelsCog())
