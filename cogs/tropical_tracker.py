"""Tropical storm tracker — subscribe to active cyclones for periodic updates."""

import io
import json
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.discord_send import safe_send
from utils.http import http_get_bytes
from utils.nhc_storms import (
    SAFFIR_EMOJI,
    SAFFIR_SIMPSON_COLORS,
    build_advisory_etn,
    category_label,
    fetch_nhc_product,
    get_active_storms,
    parse_location,
    parse_location_desc,
    parse_max_wind,
    parse_movement,
    parse_pressure,
    winds_to_category,
)
from utils.state_store import get_state, set_state

logger = logging.getLogger("spc_bot")

NHC_GRAPHICS_BASE = "https://www.nhc.noaa.gov/storm_graphics"

# NHC product PIL codes that carry advisory data (winds, position, etc.)
_ADVISORY_PILS = {"TCP", "TCU", "TCE"}


# ── Storm list for autocomplete ───────────────────────────────────────────────


def _storm_display_name(storm: dict) -> str:
    name = storm.get("name") or storm["storm_id"]
    stype = storm.get("type") or ""
    return f"{name} ({storm['storm_id']}) — {stype}" if stype else f"{name} ({storm['storm_id']})"


def _storm_emoji(storm: dict) -> str:
    stype = (storm.get("type") or "").upper()
    if "HURRICANE" in stype or "MAJOR" in stype:
        return "⚠️🌀"
    if "TROPICAL STORM" in stype:
        return "🌧️"
    if "DEPRESSION" in stype:
        return "☁️"
    return "🌀"


# ── Subscription state helpers ────────────────────────────────────────────────


def _state_key(channel_id: int) -> str:
    return f"tracked_storms:channel:{channel_id}"


async def get_tracked_storms(channel_id: int) -> list[dict]:
    raw = await get_state(_state_key(channel_id))
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    if isinstance(raw, list):
        return raw
    return []


async def set_tracked_storms(channel_id: int, storms: list[dict]) -> None:
    await set_state(_state_key(channel_id), json.dumps(storms))


async def add_tracked_storm(channel_id: int, storm_id: str) -> bool:
    """Add a storm to a channel's tracked list. Returns True if newly added."""
    storms = await get_tracked_storms(channel_id)
    if any(s["storm_id"] == storm_id for s in storms):
        return False
    storms.append({"storm_id": storm_id, "last_etn": None})
    await set_tracked_storms(channel_id, storms)
    return True


async def remove_tracked_storm(channel_id: int, storm_id: str) -> bool:
    """Remove a storm from a channel's tracked list. Returns True if removed."""
    storms = await get_tracked_storms(channel_id)
    before = len(storms)
    storms = [s for s in storms if s["storm_id"] != storm_id]
    if len(storms) == before:
        return False
    await set_tracked_storms(channel_id, storms)
    return True


# ── Image download ────────────────────────────────────────────────────────────


async def _download_cone_image(storm_id: str, advisory_num: str) -> bytes | None:
    """Download the 5-day forecast cone PNG from NHC.

    URL pattern: /storm_graphics/{BASIN}/{STORM_ID}_5day_cone_sm+png/{ADVISORY}_5day_cone_sm.png
    """
    basin = storm_id[:2]  # "AL" or "EP"
    url = (
        f"{NHC_GRAPHICS_BASE}/{basin}/{storm_id}"
        f"_5day_cone_sm+png/{advisory_num}_5day_cone_sm.png"
    )
    content, status = await http_get_bytes(url, retries=2, timeout=15)
    if content and status == 200 and len(content) > 1000:
        return content
    return None


# ── Cog ───────────────────────────────────────────────────────────────────────


class TropicalTrackerCog(commands.Cog, name="TropicalTracker"):
    """Subscribe to active tropical cyclones for periodic status updates."""

    MANAGED_TASK_NAMES = [("update_loop", "tropical_tracker_updates")]

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── /track ────────────────────────────────────────────────────────────

    track_group = app_commands.Group(
        name="track",
        description="Track active tropical cyclones",
    )

    @track_group.command(name="storm", description="Track a tropical cyclone for periodic updates")
    @app_commands.describe(storm="Storm to track (leave blank to pick from dropdown)")
    async def track_storm(
        self,
        interaction: discord.Interaction,
        storm: str | None = None,
    ):
        channel = interaction.channel

        # If no storm specified, show dropdown of active storms
        if not storm:
            active = await get_active_storms()
            if not active:
                await interaction.response.send_message(
                    "No active tropical cyclones found right now.", ephemeral=True
                )
                return

            options = []
            for sid, info in sorted(active.items()):
                emoji = _storm_emoji(info)
                name = info.get("name") or sid
                stype = info.get("type") or "Unknown"
                options.append(
                    discord.SelectOption(
                        label=f"{name} ({sid})",
                        value=sid,
                        description=stype,
                        emoji=emoji,
                    )
                )

            select = discord.ui.Select(
                placeholder="Choose a storm to track...",
                min_values=1,
                max_values=1,
                options=options[:25],
            )

            async def _on_select(select_interaction: discord.Interaction):
                selected_id = select_values[0]
                added = await add_tracked_storm(channel.id, selected_id)
                info = active.get(selected_id, {})
                name = info.get("name") or selected_id
                if added:
                    await select_interaction.response.edit_message(
                        content=f"Now tracking **{name}** ({selected_id}). Updates posted every 30 minutes.",
                        view=None,
                    )
                else:
                    await select_interaction.response.edit_message(
                        content=f"Already tracking **{name}** ({selected_id}).",
                        view=None,
                    )

            select_values: list[str] = []
            select.callback = _on_select

            # Wrap callback to capture value
            _orig = select.callback

            async def _capture(interaction: discord.Interaction):
                select_values.extend(select.values)
                await _orig(interaction)

            select.callback = _capture

            view = discord.ui.View()
            view.add_item(select)
            await interaction.response.send_message(
                "Select a storm to track:", view=view, ephemeral=True
            )
            return

        # Resolve storm ID from name
        storm_id = await self._resolve_storm_id(storm)
        if not storm_id:
            await interaction.response.send_message(
                f"Could not find a storm matching `{storm}`. "
                "Use the storm ID (e.g., AL062026) or pick from the dropdown by running `/track storm` without an argument.",
                ephemeral=True,
            )
            return

        added = await add_tracked_storm(channel.id, storm_id)
        if added:
            await interaction.response.send_message(
                f"Now tracking **{storm_id}**. Updates posted every 30 minutes.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"Already tracking **{storm_id}**.",
                ephemeral=True,
            )

    # ── /untrack ──────────────────────────────────────────────────────────

    @track_group.command(name="untrack", description="Stop tracking a tropical cyclone")
    @app_commands.describe(storm="Storm to untrack (leave blank to pick from tracked storms)")
    async def untrack_storm(
        self,
        interaction: discord.Interaction,
        storm: str | None = None,
    ):
        channel = interaction.channel

        if not storm:
            tracked = await get_tracked_storms(channel.id)
            if not tracked:
                await interaction.response.send_message(
                    "No storms are being tracked in this channel.", ephemeral=True
                )
                return

            active = await get_active_storms()
            options = []
            for t in tracked:
                sid = t["storm_id"]
                info = active.get(sid, {})
                name = info.get("name") or sid
                stype = info.get("type") or ""
                emoji = _storm_emoji(info)
                options.append(
                    discord.SelectOption(
                        label=f"{name} ({sid})",
                        value=sid,
                        description=stype or "Tracked",
                        emoji=emoji,
                    )
                )

            select = discord.ui.Select(
                placeholder="Choose storm(s) to stop tracking...",
                min_values=1,
                max_values=len(options),
                options=options[:25],
            )

            async def _on_select(select_interaction: discord.Interaction):
                removed = []
                for sid in select.values:
                    await remove_tracked_storm(channel.id, sid)
                    removed.append(sid)
                names = ", ".join(removed)
                await select_interaction.response.edit_message(
                    content=f"Stopped tracking: {names}.", view=None
                )

            select.callback = _on_select
            view = discord.ui.View()
            view.add_item(select)
            await interaction.response.send_message(
                "Select storm(s) to stop tracking:", view=view, ephemeral=True
            )
            return

        storm_id = await self._resolve_storm_id(storm)
        if not storm_id:
            await interaction.response.send_message(
                f"Could not find a storm matching `{storm}`.", ephemeral=True
            )
            return

        removed = await remove_tracked_storm(channel.id, storm_id)
        if removed:
            await interaction.response.send_message(
                f"Stopped tracking **{storm_id}**.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"**{storm_id}** is not being tracked in this channel.", ephemeral=True
            )

    # ── /tracked ──────────────────────────────────────────────────────────

    @track_group.command(name="tracked", description="List storms being tracked in this channel")
    async def list_tracked(self, interaction: discord.Interaction):
        tracked = await get_tracked_storms(interaction.channel.id)
        if not tracked:
            await interaction.response.send_message(
                "No storms are being tracked in this channel. Use `/track storm` to start tracking.",
                ephemeral=True,
            )
            return

        active = await get_active_storms()
        lines = []
        for t in tracked:
            sid = t["storm_id"]
            info = active.get(sid, {})
            name = info.get("name") or sid
            stype = info.get("type") or "Unknown"
            emoji = _storm_emoji(info)
            last_etn = t.get("last_etn")
            status = f"Last update: advisory {last_etn}" if last_etn else "Awaiting first update"
            lines.append(f"{emoji} **{name}** ({sid}) — {stype}\n  ↳ {status}")

        embed = discord.Embed(
            title="🌀 Tracked Tropical Cyclones",
            description="\n\n".join(lines),
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"Channel: #{interaction.channel.name}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── Autocomplete ──────────────────────────────────────────────────────

    @track_storm.autocomplete("storm")
    @untrack_storm.autocomplete("storm")
    async def storm_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        current = current.lower().strip()
        active = await get_active_storms()
        if not active:
            return []

        scored: list[tuple[int, str, dict]] = []
        for sid, info in active.items():
            name = (info.get("name") or "").lower()
            stype = (info.get("type") or "").lower()
            display = f"{info.get('name') or sid} ({sid})"

            if current in name or current in sid.lower():
                priority = 0 if name.startswith(current) or sid.lower().startswith(current) else 1
                scored.append((priority, display, info))
            elif current in stype:
                scored.append((2, display, info))

        scored.sort(key=lambda x: x[0])
        results = []
        for _, display, info in scored[:25]:
            emoji = _storm_emoji(info)
            stype = info.get("type") or ""
            results.append(
                app_commands.Choice(
                    name=f"{emoji} {display} — {stype}",
                    value=info["storm_id"],
                )
            )
        return results

    # ── Periodic update loop ──────────────────────────────────────────────

    @tasks.loop(minutes=30)
    async def update_loop(self):
        await self.bot.wait_until_ready()
        if not self.bot.state.is_primary:
            return

        # Collect all unique storm IDs across all channels
        channel_storms: dict[int, list[dict]] = {}
        # Scan all text channels for tracked storms
        for guild in self.bot.guilds:
            for ch in guild.text_channels:
                tracked = await get_tracked_storms(ch.id)
                if tracked:
                    channel_storms[ch.id] = tracked

        if not channel_storms:
            return

        active = await get_active_storms()

        for channel_id, tracked_list in channel_storms.items():
            channel = self.bot.get_channel(channel_id)
            if not channel:
                continue

            for entry in tracked_list:
                storm_id = entry["storm_id"]
                info = active.get(storm_id)

                # If storm no longer active, post final notice and untrack
                if not info:
                    await self._post_dissipation_notice(channel, storm_id)
                    await remove_tracked_storm(channel_id, storm_id)
                    continue

                await self._post_storm_update(channel, storm_id, info, entry)

    @update_loop.before_loop
    async def before_update_loop(self):
        await self.bot.wait_until_ready()

    # ── Internal helpers ──────────────────────────────────────────────────

    async def _resolve_storm_id(self, query: str) -> str | None:
        """Resolve a user query (name or ID) to a storm ID."""
        query_upper = query.upper().strip()

        # Direct ID match
        if len(query_upper) >= 6 and query_upper[:2] in ("AL", "EP", "CP") and query_upper[2:4].isdigit():
            return query_upper

        active = await get_active_storms()
        for sid, info in active.items():
            if sid.upper() == query_upper:
                return sid
            name = (info.get("name") or "").upper()
            if name == query_upper:
                return sid
            if query_upper in name or query_upper in sid.upper():
                return sid
        return None

    async def _post_storm_update(
        self,
        channel: discord.abc.Messageable,
        storm_id: str,
        info: dict,
        entry: dict,
    ) -> None:
        """Post a status update for a tracked storm."""
        name = info.get("name") or storm_id
        stype = info.get("type") or "Unknown"

        # Find the latest advisory product for this storm
        product_id = self._latest_advisory_pid(storm_id)
        if not product_id:
            return

        parsed = await fetch_nhc_product(product_id)
        if not parsed or not parsed.get("raw_text"):
            return

        raw = parsed["raw_text"]
        wind_mph = parse_max_wind(raw)
        ss_cat = winds_to_category(wind_mph) if wind_mph else None

        # Dedupe: check if we already posted this advisory
        etn = build_advisory_etn(product_id)
        if entry.get("last_etn") == etn:
            return

        # Build embed
        emoji = SAFFIR_EMOJI.get(ss_cat or "", "🌀")
        color = SAFFIR_SIMPSON_COLORS.get(ss_cat or "", 0xF39C12)

        loc = parse_location(raw) or ""
        loc_desc = parse_location_desc(raw) or ""
        pressure = parse_pressure(raw)
        movement = parse_movement(raw)

        desc_parts = []
        if loc:
            line = f"📍 {loc}"
            if loc_desc:
                line += f" — {loc_desc}"
            desc_parts.append(line)

        data_bits = []
        if wind_mph:
            cat_label = category_label(ss_cat) if ss_cat else ""
            data_bits.append(f"💨 {wind_mph:.0f} MPH ({cat_label})")
        if pressure:
            data_bits.append(f"🌀 {pressure} MB")
        if movement:
            data_bits.append(f"➡️ {movement}")
        if data_bits:
            desc_parts.append(" | ".join(data_bits))

        if stype:
            desc_parts.append(f"**Type:** {stype}")

        description = "\n".join(desc_parts) if desc_parts else f"Storm ID: {storm_id}"

        embed = discord.Embed(
            title=f"{emoji} {name} ({storm_id})",
            description=description,
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"Advisory {etn} • NHC via IEM")

        # Try to download the forecast cone image
        cone_bytes = await _download_cone_image(storm_id, etn)
        file = None
        if cone_bytes:
            file = discord.File(io.BytesIO(cone_bytes), filename=f"{storm_id}_forecast.png")
            embed.set_image(url=f"attachment://{storm_id}_forecast.png")

        msg = await safe_send(
            channel,
            context=f"tracker update for {storm_id}",
            embed=embed,
            file=file,
        )

        if msg:
            # Update last_etn
            await self._update_last_etn(channel.id if isinstance(channel, discord.TextChannel) else 0, storm_id, etn)

    async def _update_last_etn(self, channel_id: int, storm_id: str, etn: str) -> None:
        if not channel_id:
            return
        storms = await get_tracked_storms(channel_id)
        for s in storms:
            if s["storm_id"] == storm_id:
                s["last_etn"] = etn
                break
        await set_tracked_storms(channel_id, storms)

    async def _post_dissipation_notice(self, channel: discord.abc.Messageable, storm_id: str) -> None:
        embed = discord.Embed(
            title=f"Storm Dissipated: {storm_id}",
            description=f"**{storm_id}** is no longer listed as active by NHC. Tracking stopped.",
            color=discord.Color.greyple(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text="Tropical Tracker")
        await safe_send(channel, context=f"tracker dissipation notice for {storm_id}", embed=embed)

    def _latest_advisory_pid(self, storm_id: str) -> str | None:
        """Build the latest advisory product ID for a storm.

        NHC advisory PILs follow the pattern MIATCP{basin_code}{storm_num},
        e.g., MIATCPAT1 for Atlantic storm 01. The actual latest advisory
        number isn't known ahead of time, so we try the most common products
        and let IEM return the latest one.
        """
        basin = storm_id[:2]  # "AL" or "EP"
        num = storm_id[2:4]  # "06"

        basin_map = {"AL": "AT", "EP": "EP", "CP": "CP"}
        basin_code = basin_map.get(basin)
        if not basin_code:
            return None

        # Try TCP (full advisory) first, then TCU (update)
        return f"MIATCP{basin_code}{num}"


async def setup(bot: commands.Bot):
    await bot.add_cog(TropicalTrackerCog(bot))
