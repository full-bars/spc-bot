"""User-specific Warning Subscriptions."""

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.db import (
    add_user_subscription,
    get_user_subscriptions,
    remove_user_subscription,
)
from utils.http import ensure_session

logger = logging.getLogger("spc_bot.subscriptions")

_STATES = {
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
}


class SubscriptionsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(name="subscribe", description="Subscribe to severe weather warnings")

    @group.command(name="state", description="Subscribe to all warnings in a specific state")
    @app_commands.describe(state_code="Two-letter state code (e.g. OK, TX)")
    async def subscribe_state(self, interaction: discord.Interaction, state_code: str):
        state_code = state_code.upper()
        if state_code not in _STATES:
            await interaction.response.send_message(
                f"Invalid 2-letter state code: `{state_code}`", ephemeral=True
            )
            return

        await add_user_subscription(interaction.user.id, "state", state_code)
        await interaction.response.send_message(
            f"✅ Subscribed to all warnings in **{state_code}**.", ephemeral=True
        )

    @group.command(name="local", description="Subscribe to warnings within a radius of a city")
    @app_commands.describe(
        city="City name and state (e.g., 'Norman, OK')",
        radius_miles="Alert radius in miles (default 30, max 100)",
    )
    async def subscribe_local(
        self, interaction: discord.Interaction, city: str, radius_miles: int = 30
    ):
        if not (1 <= radius_miles <= 100):
            await interaction.response.send_message(
                "Radius must be between 1 and 100 miles.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        # Geocode city
        lat, lon = await self._geocode(city)
        if lat is None or lon is None:
            await interaction.followup.send(
                f"Could not find coordinates for `{city}`. Please try adding the state (e.g., 'Dallas, TX').",
                ephemeral=True,
            )
            return

        radius_km = radius_miles * 1.60934
        await add_user_subscription(interaction.user.id, "local", city, lat, lon, radius_km)
        await interaction.followup.send(
            f"✅ Subscribed to warnings within **{radius_miles} miles** of **{city}**.",
            ephemeral=True,
        )

    @app_commands.command(name="unsubscribe", description="Remove a warning subscription")
    async def unsubscribe(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        subs = await get_user_subscriptions(interaction.user.id)

        if not subs:
            await interaction.followup.send("You have no active subscriptions.", ephemeral=True)
            return

        # Build a selection view
        options = []
        for s in subs[:25]:  # Discord max select options is 25
            val = f"{s['sub_type']}:{s['sub_value']}"
            label = (
                f"State: {s['sub_value']}"
                if s["sub_type"] == "state"
                else f"Local: {s['sub_value']} ({s['radius_km'] / 1.60934:.0f}mi)"
            )
            options.append(discord.SelectOption(label=label, value=val))

        select = discord.ui.Select(
            placeholder="Choose subscription(s) to remove...",
            min_values=1,
            max_values=len(options),
            options=options,
        )

        async def _on_select(select_interaction: discord.Interaction):
            for val in select.values:
                stype, sval = val.split(":", 1)
                await remove_user_subscription(interaction.user.id, stype, sval)
            await select_interaction.response.edit_message(
                content="✅ Subscriptions removed.", view=None
            )

        select.callback = _on_select
        view = discord.ui.View()
        view.add_item(select)
        await interaction.followup.send(
            "Select subscriptions to remove:", view=view, ephemeral=True
        )

    @app_commands.command(
        name="subscriptions", description="List your current warning subscriptions"
    )
    async def list_subscriptions(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        subs = await get_user_subscriptions(interaction.user.id)

        if not subs:
            await interaction.followup.send("You have no active subscriptions.", ephemeral=True)
            return

        lines = []
        for s in subs:
            if s["sub_type"] == "state":
                lines.append(f"🗺️ **State**: {s['sub_value']}")
            else:
                lines.append(
                    f"📍 **Local**: {s['sub_value']} ({s['radius_km'] / 1.60934:.0f} miles radius)"
                )

        embed = discord.Embed(
            title="🔔 Your Warning Subscriptions",
            description="\n".join(lines),
            color=discord.Color.blue(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _geocode(self, query: str) -> tuple[Optional[float], Optional[float]]:
        try:
            session = await ensure_session()
            url = "https://nominatim.openstreetmap.org/search"
            params = {"q": query, "format": "json", "limit": "1"}
            headers = {"User-Agent": "spc-bot/5.36 (Discord Weather Bot)"}
            async with session.get(url, params=params, headers=headers, timeout=10) as r:
                if r.status == 200:
                    data = await r.json()
                    if data and len(data) > 0:
                        return float(data[0]["lat"]), float(data[0]["lon"])
        except Exception as e:
            logger.error(f"Geocoding failed for {query}: {e}")
        return None, None


async def setup(bot: commands.Bot):
    await bot.add_cog(SubscriptionsCog(bot))
