"""Tropical storm tracker — subscribe to active cyclones for periodic updates."""

import asyncio
import io
import json
import logging
import re
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.change_detection import is_placeholder_image
from utils.discord_send import safe_send
from utils.http import http_get_bytes
from utils.nhc_storms import (
    SAFFIR_EMOJI,
    SAFFIR_SIMPSON_COLORS,
    active_storms_authoritative,
    category_label,
    get_active_storms,
    winds_to_category,
)
from utils.state_store import delete_state, get_state, list_state_keys, set_state

logger = logging.getLogger("spc_bot")

NHC_BASE = "https://www.nhc.noaa.gov"

# Full storm ID format: basin (AL/EP/CP) + two-digit number + four-digit year.
_STORM_ID_RE = re.compile(r"^(AL|EP|CP)\d{6}$")

# NESDIS/STAR floater products available for every active storm (verified live).
# Values are the exact suffix of the `FloaterStatic{PRODUCT}` input on the
# floater page. GEOCOLOR is the default.
SATELLITE_PRODUCTS: dict[str, str] = {
    "GEOCOLOR": "GeoColor (visible + color)",
    "AirMass": "Air Mass RGB",
    "Sandwich": "Sandwich (visible + IR)",
    "DayConvection": "Day Convection RGB",
    "DayNightCloudMicroCombo": "Day/Night Cloud Microcombo",
    "EXTENT3": "Lightning (GLM)",
    "02": "Visible (Band 02)",
    "07": "Shortwave IR (Band 07)",
    "08": "Water Vapor (Band 08)",
    "13": "Clean IR (Band 13)",
    "14": "IR Longwave (Band 14)",
}
DEFAULT_SATELLITE_PRODUCT = "GEOCOLOR"


def _storm_emoji(storm: dict) -> str:
    stype = (storm.get("type") or "").upper()
    if "HURRICANE" in stype or "MAJOR" in stype:
        return "⚠️🌀"
    if "TROPICAL STORM" in stype:
        return "🌧️"
    if "DEPRESSION" in stype:
        return "☁️"
    return "🌀"


def _storm_display_name(storm: dict) -> str:
    name = storm.get("name") or storm["storm_id"]
    stype = storm.get("type") or ""
    return f"{name} ({storm['storm_id']}) — {stype}" if stype else f"{name} ({storm['storm_id']})"


# ── Subscription state (atomic per-channel-per-storm records) ─────────────────


def _state_key(channel_id: int, storm_id: str) -> str:
    return f"tracked_storms:channel:{channel_id}:{storm_id}"


async def get_tracked_storms(channel_id: int) -> list[dict]:
    """List tracked storms for a channel as records with sat_product."""
    storm_ids = await list_state_keys(f"tracked_storms:channel:{channel_id}:")
    storms: list[dict] = []
    for storm_id in storm_ids:
        record = {"storm_id": storm_id, "last_etn": None, "sat_product": DEFAULT_SATELLITE_PRODUCT}
        raw = await get_state(_state_key(channel_id, storm_id))
        if isinstance(raw, str):
            try:
                data = json.loads(raw)
                record["last_etn"] = data.get("last_etn")
                record["sat_product"] = data.get("sat_product") or DEFAULT_SATELLITE_PRODUCT
            except (json.JSONDecodeError, TypeError, AttributeError):
                record["last_etn"] = raw
        storms.append(record)
    return storms


async def get_all_tracked_channels() -> dict[int, list[str]]:
    """Map every channel with trackers to its list of storm IDs (one SCAN)."""
    keys = await list_state_keys("tracked_storms:channel:")
    result: dict[int, list[str]] = {}
    for key in keys:
        channel_s, _, storm_id = key.partition(":")
        if not channel_s.isdigit() or not storm_id:
            continue
        result.setdefault(int(channel_s), []).append(storm_id)
    return result


async def add_tracked_storm(
    channel_id: int, storm_id: str, sat_product: str = DEFAULT_SATELLITE_PRODUCT
) -> bool:
    """Add a storm to a channel's trackers. Returns True if newly added.

    Each subscription is its own state key so add/remove are atomic SET/DEL
    — safe across concurrent commands and HA instances (no read-modify-write).
    """
    key = _state_key(channel_id, storm_id)
    if await get_state(key) is not None:
        return False
    await set_state(key, json.dumps({"last_etn": None, "sat_product": sat_product}))
    return True


async def set_satellite_product(channel_id: int, storm_id: str, product: str) -> bool:
    """Set the satellite product for a tracked storm. Returns True if tracked."""
    key = _state_key(channel_id, storm_id)
    raw = await get_state(key)
    if raw is None:
        return False
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        data = {}
    data["sat_product"] = product
    await set_state(key, json.dumps(data))
    return True


async def remove_tracked_storm(channel_id: int, storm_id: str) -> bool:
    """Remove a storm from a channel's trackers. Returns True if removed."""
    key = _state_key(channel_id, storm_id)
    if await get_state(key) is None:
        return False
    await delete_state(key)
    return True


async def _update_last_etn(channel_id: int, storm_id: str, etn: str) -> None:
    await set_state(_state_key(channel_id, storm_id), json.dumps({"last_etn": etn}))


# ── Image download ────────────────────────────────────────────────────────────


async def _download_cone_image(graphics_url: str | None) -> bytes | None:
    """Fetch the storm's graphics page and download the full 5-day cone PNG.

    The page exposes the full graphic as ``<img id="coneimage">`` (e.g.
    ``.../EP172026_5day_cone+png/222335_5day_cone.png``); the ``_sm``
    variants referenced elsewhere are 60px thumbnails and are not used.
    """
    if not graphics_url:
        return None
    content, status = await http_get_bytes(graphics_url, retries=2, timeout=15)
    if not content or status != 200:
        return None
    html = content.decode("utf-8", errors="ignore")
    m = re.search(r'<img[^>]*id="coneimage"[^>]*src\s*=\s*"([^"]*)"', html)
    if not m:
        return None
    img_url = NHC_BASE + m.group(1)
    img, img_status = await http_get_bytes(img_url, retries=2, timeout=15)
    if img and img_status == 200 and not is_placeholder_image(img):
        return img
    return None


def _compress_gif(data: bytes, target: int = 7_500_000) -> bytes | None:
    """Downscale an animated GIF loop so it fits Discord's upload limit (~8 MB).

    Tries progressively smaller sizes; keeps all frames so the loop stays
    animated. Returns None if Pillow is unavailable or nothing fits.
    """
    try:
        from PIL import Image, ImageSequence
    except ImportError:
        return None
    try:
        with Image.open(io.BytesIO(data)) as im:
            if len(data) <= target:
                return data
            duration = im.info.get("duration", 100)
            for size in (640, 560, 480, 400):
                frames = []
                for frame in ImageSequence.Iterator(im):
                    frames.append(
                        frame.convert("RGBA")
                        .resize((size, size), Image.LANCZOS)
                        .convert("P", palette=Image.ADAPTIVE, colors=96)
                    )
                if not frames:
                    continue
                out = io.BytesIO()
                frames[0].save(
                    out,
                    format="GIF",
                    save_all=True,
                    append_images=frames[1:],
                    optimize=True,
                    duration=duration,
                    loop=0,
                )
                result = out.getvalue()
                if len(result) <= target:
                    return result
        return None
    except Exception:
        return None


async def _download_satellite_image(
    satellite_url: str | None, product: str = DEFAULT_SATELLITE_PRODUCT
) -> bytes | None:
    """Fetch the NESDIS/STAR floater page and download the satellite imagery.

    Prefers the **animated loop** (``FloaterGIF{PRODUCT}``) at full quality —
    the boosted upload limit accepts the ~16 MB file as-is; compression only
    happens if Discord rejects the upload (see ``_send_tracker_update``).
    Falls back to the latest static frame (``FloaterStatic{PRODUCT}``) if the
    loop can't be retrieved.
    """
    if not satellite_url:
        return None
    content, status = await http_get_bytes(satellite_url, retries=2, timeout=40)
    if not content or status != 200:
        return None
    html = content.decode("utf-8", errors="ignore")

    gif_match = re.search(rf"id='FloaterGIF{re.escape(product)}'[^>]*value='([^']+)'", html)
    if gif_match:
        gif, gif_status = await http_get_bytes(gif_match.group(1), retries=2, timeout=60)
        if gif and gif_status == 200 and not is_placeholder_image(gif):
            return gif

    static_match = re.search(rf"id='FloaterStatic{re.escape(product)}'[^>]*value='([^']+)'", html)
    if static_match:
        img, img_status = await http_get_bytes(static_match.group(1), retries=2, timeout=30)
        if img and img_status == 200 and not is_placeholder_image(img):
            return img
    return None


async def _send_tracker_update(
    channel: discord.abc.Messageable,
    *,
    context: str,
    embed: discord.Embed,
    cone_bytes: bytes | None,
    sat_bytes: bytes | None,
    storm_id: str,
):
    """Send a tracker update, compressing the satellite GIF only on a 413.

    The boosted guild upload limit (100 MB) normally accepts the full-quality
    NESDIS loop (~16 MB). If Discord still rejects the upload as too large,
    downscale the loop with Pillow and retry once; if compression fails the
    update posts with the cone only.
    """

    def build(sat: bytes | None) -> list[discord.File] | None:
        out: list[discord.File] = []
        if cone_bytes:
            out.append(discord.File(io.BytesIO(cone_bytes), filename=f"{storm_id}_forecast.png"))
        if sat:
            ext = "gif" if sat.startswith(b"GIF8") else "jpg"
            out.append(discord.File(io.BytesIO(sat), filename=f"{storm_id}_satellite.{ext}"))
        return out or None

    try:
        return await channel.send(embed=embed, files=build(sat_bytes))
    except discord.Forbidden as e:
        logger.error(f"Missing permissions to post {context} in #{channel.id} ({channel}): {e}")
        return None
    except discord.HTTPException as e:
        if e.status != 413 or not (sat_bytes and sat_bytes.startswith(b"GIF8")):
            logger.exception(f"Failed to post {context} in #{channel.id} ({channel}): {e}")
            return None
        logger.info(
            f"Satellite loop rejected as too large ({len(sat_bytes)} bytes) "
            f"for #{channel.id}; compressing and retrying"
        )
        compressed = _compress_gif(sat_bytes)
        return await safe_send(channel, context=context, embed=embed, files=build(compressed))
    except Exception as e:
        logger.exception(f"Failed to post {context} in #{channel.id} ({channel}): {e}")
        return None


# ── Cog ───────────────────────────────────────────────────────────────────────


class TropicalTrackerCog(commands.Cog, name="TropicalTracker"):
    """Subscribe to active tropical cyclones for periodic status updates."""

    MANAGED_TASK_NAMES = [("update_loop", "tropical_tracker_updates")]

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.update_loop.start()

    # ── /nhc ─────────────────────────────────────────────────────────────

    track_group = app_commands.Group(
        name="nhc",
        description="Track active tropical cyclones",
    )

    async def _require_guild_channel(
        self, interaction: discord.Interaction
    ) -> discord.TextChannel | None:
        """Return the channel or reply with an error and return None."""
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Storm tracking must be set up in a server text channel.",
                ephemeral=True,
            )
            return None
        return channel

    @track_group.command(name="storm", description="Track a tropical cyclone for periodic updates")
    @app_commands.describe(
        storm="Storm to track (leave blank to pick from dropdown)",
        satproduct="NESDIS satellite product for the update image (default GeoColor)",
    )
    @app_commands.choices(
        satproduct=[
            app_commands.Choice(name=label, value=key) for key, label in SATELLITE_PRODUCTS.items()
        ]
    )
    async def track_storm(
        self,
        interaction: discord.Interaction,
        storm: str | None = None,
        satproduct: app_commands.Choice[str] | None = None,
    ):
        channel = await self._require_guild_channel(interaction)
        if not channel:
            return
        sat_product = satproduct.value if satproduct else DEFAULT_SATELLITE_PRODUCT

        # If no storm specified, show dropdown of active storms
        if not storm:
            active = await get_active_storms()
            if not active:
                await interaction.response.send_message(
                    "No active tropical cyclones found right now.", ephemeral=True
                )
                return

            options = [
                discord.SelectOption(
                    label=f"{info.get('name') or sid} ({sid})",
                    value=sid,
                    description=info.get("type") or "Unknown",
                    emoji=_storm_emoji(info),
                )
                for sid, info in sorted(active.items())
            ]
            truncated = len(options) > 25
            select = discord.ui.Select(
                placeholder="Choose a storm to track...",
                min_values=1,
                max_values=1,
                options=options[:25],
            )

            async def _on_select(select_interaction: discord.Interaction):
                selected_id = select.values[0]
                added = await add_tracked_storm(channel.id, selected_id, sat_product)
                info = active.get(selected_id, {})
                name = info.get("name") or selected_id
                if added:
                    msg = (
                        f"Now tracking **{name}** ({selected_id}). Posts a new update on each "
                        f"NHC advisory with **{SATELLITE_PRODUCTS.get(sat_product, sat_product)}** "
                        "satellite imagery. Sending the current status now..."
                    )
                    asyncio.create_task(self._post_immediate_update(channel, selected_id))
                else:
                    msg = f"Already tracking **{name}** ({selected_id})."
                await select_interaction.response.edit_message(content=msg, view=None)

            select.callback = _on_select
            view = discord.ui.View()
            view.add_item(select)
            text = "Select a storm to track:"
            if truncated:
                text += (
                    "\n_(only the first 25 are shown — type `/nhc storm` with a name to pick any)_"
                )
            await interaction.response.send_message(text, view=view, ephemeral=True)
            return

        storm_id = await self._resolve_storm_id(storm)
        if not storm_id:
            await interaction.response.send_message(
                f"Could not find an active storm matching `{storm}`. "
                "Use a storm ID (e.g. EP172026) or run `/nhc storm` with no argument to pick from a dropdown.",
                ephemeral=True,
            )
            return

        added = await add_tracked_storm(channel.id, storm_id, sat_product)
        if added:
            await interaction.response.send_message(
                f"Now tracking **{storm_id}**. Posts a new update on each NHC advisory "
                f"with **{SATELLITE_PRODUCTS.get(sat_product, sat_product)}** satellite "
                "imagery. Sending the current status now...",
                ephemeral=True,
            )
            asyncio.create_task(self._post_immediate_update(channel, storm_id))
        else:
            await interaction.response.send_message(
                f"Already tracking **{storm_id}**.", ephemeral=True
            )

    # ── /untrack ──────────────────────────────────────────────────────────

    @track_group.command(name="untrack", description="Stop tracking a tropical cyclone")
    @app_commands.describe(storm="Storm to untrack (leave blank to pick from tracked storms)")
    async def untrack_storm(
        self,
        interaction: discord.Interaction,
        storm: str | None = None,
    ):
        channel = await self._require_guild_channel(interaction)
        if not channel:
            return

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
                options.append(
                    discord.SelectOption(
                        label=f"{info.get('name') or sid} ({sid})",
                        value=sid,
                        description=info.get("type") or "Tracked",
                        emoji=_storm_emoji(info),
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
                    if await remove_tracked_storm(channel.id, sid):
                        removed.append(sid)
                names = ", ".join(removed) if removed else "nothing"
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
                f"Could not find an active storm matching `{storm}`.", ephemeral=True
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
                "No storms are being tracked in this channel. Use `/nhc storm` to start tracking.",
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
            last_etn = t.get("last_etn")
            status = f"Last update: advisory {last_etn}" if last_etn else "Awaiting first update"
            lines.append(f"{_storm_emoji(info)} **{name}** ({sid}) — {stype}\n  ↳ {status}")

        embed = discord.Embed(
            title="🌀 Tracked Tropical Cyclones",
            description="\n\n".join(lines),
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"Channel: #{interaction.channel.name}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /nesdis ───────────────────────────────────────────────────────────

    @app_commands.command(
        name="nesdis", description="Choose the NESDIS satellite product for tracked storms"
    )
    @app_commands.describe(
        product="NESDIS satellite product to use for tracked-storm updates",
        storm="Optional storm to change (defaults to all tracked storms in this channel)",
    )
    @app_commands.choices(
        product=[
            app_commands.Choice(name=label, value=key) for key, label in SATELLITE_PRODUCTS.items()
        ]
    )
    async def nesdis(
        self,
        interaction: discord.Interaction,
        product: app_commands.Choice[str],
        storm: str | None = None,
    ):
        channel = await self._require_guild_channel(interaction)
        if not channel:
            return

        if storm:
            storm_id = await self._resolve_storm_id(storm)
            if not storm_id:
                await interaction.response.send_message(
                    f"Could not find an active storm matching `{storm}`.", ephemeral=True
                )
                return
            if not await set_satellite_product(channel.id, storm_id, product.value):
                await interaction.response.send_message(
                    f"**{storm_id}** is not being tracked in this channel.", ephemeral=True
                )
                return
            names = [f"**{storm_id}**"]
        else:
            tracked = await get_tracked_storms(channel.id)
            if not tracked:
                await interaction.response.send_message(
                    "No storms are being tracked in this channel. Use `/nhc storm` to start tracking.",
                    ephemeral=True,
                )
                return
            names = []
            for t in tracked:
                if await set_satellite_product(channel.id, t["storm_id"], product.value):
                    names.append(f"**{t['storm_id']}**")
            if not names:
                await interaction.response.send_message(
                    "No tracked storms were updated.", ephemeral=True
                )
                return

        label = SATELLITE_PRODUCTS.get(product.value, product.value)
        await interaction.response.send_message(
            f"🛰️ Satellite product for {', '.join(names)} set to **{label}**.",
            ephemeral=True,
        )

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
        return [
            app_commands.Choice(
                name=f"{_storm_emoji(info)} {display} — {info.get('type') or ''}",
                value=info["storm_id"],
            )
            for _, display, info in scored[:25]
        ]

    # ── Periodic update loop ──────────────────────────────────────────────

    @tasks.loop(minutes=30)
    async def update_loop(self):
        await self.bot.wait_until_ready()
        if not self.bot.state.is_primary:
            return

        channel_storms = await get_all_tracked_channels()
        if not channel_storms:
            return

        active = await get_active_storms()

        for channel_id, storm_ids in channel_storms.items():
            channel = self.bot.get_channel(channel_id)
            if not channel:
                continue
            records = {r["storm_id"]: r for r in await get_tracked_storms(channel_id)}
            for storm_id in storm_ids:
                try:
                    info = active.get(storm_id)
                    if info is None:
                        # Only remove subscriptions on an authoritative response —
                        # a failed fetch must not wipe tracking for every storm.
                        if active_storms_authoritative():
                            await self._post_dissipation_notice(channel, storm_id)
                            await remove_tracked_storm(channel_id, storm_id)
                        continue
                    record = records.get(storm_id) or {}
                    sat_product = record.get("sat_product") or DEFAULT_SATELLITE_PRODUCT
                    await self._post_storm_update(channel, storm_id, info, channel_id, sat_product)
                except Exception as e:
                    logger.exception(f"Tracker update failed for {storm_id} in {channel_id}: {e}")

    @update_loop.before_loop
    async def before_update_loop(self):
        await self.bot.wait_until_ready()

    # ── Internal helpers ──────────────────────────────────────────────────

    async def _post_immediate_update(self, channel: discord.abc.Messageable, storm_id: str) -> None:
        """Post the current status for a just-tracked storm without waiting for the loop."""
        await self.bot.wait_until_ready()
        if not self.bot.state.is_primary:
            return
        try:
            info = (await get_active_storms()).get(storm_id)
            if not info:
                return
            record = next(
                (r for r in await get_tracked_storms(channel.id) if r["storm_id"] == storm_id),
                None,
            )
            sat_product = (
                (record.get("sat_product") or DEFAULT_SATELLITE_PRODUCT)
                if record
                else DEFAULT_SATELLITE_PRODUCT
            )
            await self._post_storm_update(channel, storm_id, info, channel.id, sat_product)
        except Exception as e:
            logger.exception(f"Immediate tracker update failed for {storm_id}: {e}")

    async def _resolve_storm_id(self, query: str) -> str | None:
        """Resolve a user query (name or storm ID) to a validated active storm ID."""
        query_upper = query.upper().strip()

        if _STORM_ID_RE.match(query_upper):
            active = await get_active_storms()
            if query_upper in active:
                return query_upper
            return None

        active = await get_active_storms()
        for sid, info in active.items():
            name = (info.get("name") or "").upper()
            if name == query_upper or query_upper in name or query_upper in sid.upper():
                return sid
        return None

    async def _post_storm_update(
        self,
        channel: discord.abc.Messageable,
        storm_id: str,
        info: dict,
        channel_id: int,
        sat_product: str = DEFAULT_SATELLITE_PRODUCT,
    ) -> None:
        """Post a status update for a tracked storm from NHC page data.

        The latest 5-day forecast cone is always the main embed image; the
        requested NESDIS satellite product is attached alongside it.
        """
        name = info.get("name") or storm_id
        stype = info.get("type") or "Unknown"

        advisory = info.get("advisory") or info.get("issuance") or "latest"
        if await self._already_posted(channel_id, storm_id, advisory):
            return

        wind_mph = info.get("winds_mph")
        ss_cat = winds_to_category(wind_mph) if wind_mph else None
        is_major = ss_cat in ("CAT3", "CAT4", "CAT5")

        emoji = SAFFIR_EMOJI.get(ss_cat or "", "🌀")
        color = SAFFIR_SIMPSON_COLORS.get(ss_cat or "", 0xF39C12)

        desc_parts = []
        # Severity headline — make category 5 / major hurricanes unmissable.
        if ss_cat == "CAT5":
            desc_parts.append("🔥 **CATEGORY 5 HURRICANE**")
        elif is_major:
            desc_parts.append(f"⚠️ **MAJOR HURRICANE** — Category {ss_cat[-1]}")
        elif ss_cat:
            desc_parts.append(f"**{category_label(ss_cat)}**")
        elif stype:
            desc_parts.append(f"**{stype}**")

        if info.get("position"):
            line = f"📍 {info['position']}"
            if info.get("movement"):
                line += f" — moving {info['movement']}"
            desc_parts.append(line)

        data_bits = []
        if wind_mph:
            data_bits.append(f"💨 **{wind_mph:.0f} MPH**")
        if info.get("pressure"):
            data_bits.append(f"🌀 **{info['pressure']} MB**")
        if data_bits:
            desc_parts.append(" | ".join(data_bits))

        if info.get("issuance"):
            desc_parts.append(f"🕐 {info['issuance']}")

        description = "\n".join(desc_parts) if desc_parts else f"Storm ID: {storm_id}"

        embed = discord.Embed(
            title=f"{emoji} {name} ({storm_id})",
            description=description,
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"Advisory {advisory} • NHC")

        # Cone is the primary graphic; the requested satellite product is
        # fetched in parallel and attached alongside it.
        cone_bytes, sat_bytes = await asyncio.gather(
            _download_cone_image(info.get("graphics_url")),
            _download_satellite_image(info.get("satellite_url"), sat_product),
            return_exceptions=True,
        )
        cone_bytes = cone_bytes if isinstance(cone_bytes, bytes) else None
        sat_bytes = sat_bytes if isinstance(sat_bytes, bytes) else None

        if cone_bytes:
            embed.set_image(url=f"attachment://{storm_id}_forecast.png")

        msg = await _send_tracker_update(
            channel,
            context=f"tracker update for {storm_id}",
            embed=embed,
            cone_bytes=cone_bytes,
            sat_bytes=sat_bytes,
            storm_id=storm_id,
        )
        if msg:
            await _update_last_etn(channel_id, storm_id, advisory)

    async def _already_posted(self, channel_id: int, storm_id: str, advisory: str) -> bool:
        tracked = await get_tracked_storms(channel_id)
        for t in tracked:
            if t["storm_id"] == storm_id:
                return t.get("last_etn") == advisory
        return False

    async def _post_dissipation_notice(
        self, channel: discord.abc.Messageable, storm_id: str
    ) -> None:
        embed = discord.Embed(
            title=f"Storm Dissipated: {storm_id}",
            description=f"**{storm_id}** is no longer listed as active by NHC. Tracking stopped.",
            color=discord.Color.greyple(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text="Tropical Tracker")
        await safe_send(channel, context=f"tracker dissipation notice for {storm_id}", embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(TropicalTrackerCog(bot))
