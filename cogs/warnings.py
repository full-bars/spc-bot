# cogs/warnings.py
"""NWS warning posting (PR A — foundation).

Polls the NWS API for active TOR / SVR / FFW warnings every 30 seconds,
deduplicates by VTEC event-tracking number (ETN), and posts each new
issuance as a Discord embed in the warnings channel.

This is the v1 baseline. Subsequent PRs add:
  - PR B: iembot fast-trigger fallback (sub-15s latency).
  - PR C: nearest-NEXRAD radar loop GIF on each post.
  - PR D: lifecycle (cancellation/expiration → edit message).
  - PR E: PDS / Tornado Emergency styling.
  - PR F: SPS path with severe-only filter.
"""
from __future__ import annotations

import json as _json
import logging
import re
import time
from datetime import datetime, timezone
from io import BytesIO
from typing import Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import NWS_ALERTS_WARNINGS_URL, WARNINGS_CHANNEL_ID
from lib.vad_plotter.radar_coords import get_nearest_radar
from lib.vtec_parser import parse_vtec, parse_warning_polygon, get_polygon_centroid
from utils.backoff import TaskBackoff
from utils.http import http_get_bytes, http_get_bytes_conditional
from utils.state_store import (
    add_posted_warning,
    add_significant_event,
    get_all_posted_warnings,
    get_recent_significant_events,
    prune_posted_warnings,
)
from cogs.warning_format import (
    get_warning_style,
    iem_autoplot_url,
    build_concise_warning_text,
    _vtec_url,
    _vtec_unix_ts,
    _area_with_state,
    _download_warning_image,
)
from cogs.warning_ui import (
    EnvironmentalView,
    TornadoDashboardView,
)

logger = logging.getLogger("spc_bot.warnings")


class WarningsCog(commands.Cog):
    MANAGED_TASK_NAMES = [("auto_poll_warnings", "auto_poll_warnings")]

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._backoff = TaskBackoff("auto_poll_warnings")
        self._validators = {"etag": "", "last_modified": ""}
        self._cancelled_warnings: set[str] = set()
        self._in_flight_vtecs: set[str] = set()

    async def cog_load(self):
        # Restore the dedup mapping so a restart during active wx doesn't
        # replay every warning the bot has already posted today.
        try:
            persisted = await get_all_posted_warnings()
            self.bot.state.posted_warnings.update(persisted)
            logger.info(
                f"Restored {len(persisted)} posted warning(s) from store"
            )
        except Exception as e:
            logger.warning(f"Could not restore posted_warnings: {e}")

        self.auto_poll_warnings.start()

    def cog_unload(self):
        self.auto_poll_warnings.cancel()

    # ── iembot fast-trigger path ───────────────────────────────────────────
    #
    # IEMBotCog calls this when a TOR/SVR/FFW product hits the botstalk
    # seqnum stream. Latency is typically 5-15s vs. the 30s NWS API
    # poll, so for severe wx the iembot path is the one that lands first.
    # We dedup against the same posted_warnings set as the NWS API
    # path; whichever fires first wins, the other is a no-op.

    async def post_warning_now(
        self, product_id: str, raw_text: str, event: str
    ):
        """Post a warning triggered by iembot. ``raw_text`` is the full
        VTEC product as plain text from the IEM nwstext API."""
        if not self.bot.state.is_primary:
            return
        channel = self.bot.get_channel(WARNINGS_CHANNEL_ID)
        if not channel:
            return

        vtec = parse_vtec(raw_text)
        if vtec:
            logger.info(f"[WARN_VTEC] iembot {event}: {vtec['vtec_id']} phenom={vtec.get('phenom')}")
            if "TO" in event or vtec.get("phenom") == "TO":
                logger.info(f"[WARN_VTEC_RAW] iembot tornado: first 500 chars of raw_text: {raw_text[:500]}")
        if not vtec:
            if event == "Special Weather Statement":
                # SPS usually lacks VTEC. Create a mock dict so formatting works.
                vtec_id = product_id
                office = product_id.split("-")[1] if "-" in product_id else "NWS"
                vtec = {
                    "vtec_id": vtec_id,
                    "action": "NEW",
                    "office": office,
                    "phenom": "SPS",
                    "sig": "S",
                    "etn": "0",
                    "start": None,
                    "end": None
                }
            else:
                logger.warning(
                    f"iembot trigger: no VTEC in {product_id} — skipping"
                )
                return

        vtec_id = vtec["vtec_id"]
        action = vtec["action"]
        office = vtec["office"]

        # ── Pipeline fast-path for product deduplication ──────────────────────
        # NWWS and IEMBot both send the same products. Normalize by product_id.
        if product_id in self.bot.state.posted_product_ids:
            return

        # PR D: Lifecycle fast-path (cancel/expire/upgrade)
        if action in ("CAN", "EXP", "UPG"):
            if vtec_id in self.bot.state.active_warnings:
                reason = "Cancelled" if action == "CAN" else ("Upgraded" if action == "UPG" else "Expired")
                # Use stored vtec if available, or current one
                vtec_to_use = vtec or self.bot.state.active_warnings.get(vtec_id)
                await self._handle_cancellation(vtec_id, reason=reason, vtec=vtec_to_use)
                self.bot.state.active_warnings.pop(vtec_id, None)
                self.bot.state.posted_product_ids.add(product_id)
            return

        # ── Pipeline fast-path for updates (CON, EXT, EXA) ─────────────────────
        is_update = action in ("CON", "EXT", "EXA")

        # We only treat it as an update if we have actually posted the issuance.
        # Otherwise (e.g. startup discovery), it proceeds as an issuance.
        if is_update and vtec_id not in self.bot.state.posted_warnings:
            is_update = False

        if not is_update and action != "NEW":
            return

        if not is_update and vtec_id in self.bot.state.posted_warnings:
            return

        # ── In-Flight Deduplication ──────────────────────────────────────────
        # Check if this VTEC ID is already being processed by another task
        # (e.g. concurrent NWWS and IEMBot triggers).
        if vtec_id in self._in_flight_vtecs:
            return

        # Check product_id again after potential await context switches
        if product_id in self.bot.state.posted_product_ids:
            return

        self._in_flight_vtecs.add(vtec_id)

        try:
            # Claim the dedup key BEFORE any awaits so concurrent tasks
            # hitting the same path see the state immediately.
            # For updates, we don't overwrite the dict yet, but we mark the product.
            self.bot.state.posted_product_ids.add(product_id)
            if not is_update:
                self.bot.state.posted_warnings[vtec_id] = {}  # placeholder

            self.bot.state.active_warnings[vtec_id] = vtec

            # Log significant events (tornadoes, hail, wind) to DB
            event_id = await self._check_and_log_significant_event(event, raw_text, vtec)

            emoji, display_event, color, footer_id = get_warning_style(event, raw_text, vtec=vtec)

            prev_area = self.bot.state.posted_warnings.get(vtec_id, {}).get("area", "")
            concise_text = build_concise_warning_text(
                display_event, vtec, raw_text=raw_text, is_update=is_update, prev_area=prev_area
            )

            embed = discord.Embed(
                title=f"{emoji} {display_event}",
                description=concise_text,
                color=color,
                timestamp=datetime.now(timezone.utc),
            )
            footer_text = f"VTEC {vtec_id}"
            if footer_id:
                footer_text += f" | {footer_id}"
            embed.set_footer(text=footer_text)

            # Add Environmental Button for Tornado Warnings
            view = None
            if event == "Tornado Warning" and event_id:
                view = EnvironmentalView(event_id)

            # Download IEM Autoplot image (only if we have a real ETN, or it's an SPS)
            files = []
            if (vtec.get("etn") and vtec["etn"] != "0") or vtec.get("phenom") == "SPS":
                image_url = iem_autoplot_url(vtec)
                logger.info(f"[WARN_IMG_IEMBOT] {vtec['vtec_id']}: {image_url}")
                filename = f"warning_{vtec_id.replace('.', '_')}.png"
                f = await _download_warning_image(image_url, filename)
                if f:
                    files.append(f)
                    embed.set_image(url=f"attachment://{filename}")

            try:
                msg = await channel.send(embed=embed, files=files, view=view)
                logger.info(f"Posted (iembot) {event} {vtec_id} ({'Update' if is_update else 'Issuance'})")

                # Simple area extraction for persistence
                area_m = re.search(r"for (.+?) till", concise_text)
                area_desc = area_m.group(1) if area_m else "affected area"

                self.bot.state.posted_warnings[vtec_id] = {
                    "message_id": msg.id,
                    "channel_id": msg.channel.id,
                    "area": area_desc,
                }
            except discord.HTTPException as e:
                # Roll back the dedup claim on a hard send failure
                if product_id in self.bot.state.posted_product_ids:
                    self.bot.state.posted_product_ids.discard(product_id)
                if not is_update and vtec_id in self.bot.state.posted_warnings:
                    del self.bot.state.posted_warnings[vtec_id]
                logger.exception(
                    f"iembot send failed for {vtec_id}: {e}"
                )
                return

            try:
                await add_posted_warning(vtec_id, msg.id, msg.channel.id, time.time(), area=area_desc)
                # Keep product IDs pruned to today's set (roughly)
                if len(self.bot.state.posted_product_ids) > 1000:
                    # Very crude pruning — ideally we'd use a TTL but this is in-memory
                    self.bot.state.posted_product_ids.clear()
                await prune_posted_warnings()
            except Exception as e:
                logger.warning(f"Failed to persist {vtec_id}: {e}")
        finally:
            self._in_flight_vtecs.discard(vtec_id)

    async def _check_and_log_significant_event(self, event: str, raw_text: str, vtec: dict):
        """Parse warning text for confirmed tornadoes and log to DB."""
        text_upper = (raw_text or "").upper()
        vtec_id = vtec.get("vtec_id", "Unknown")

        # 1. Confirmed Tornado Detection
        # Check for OBSERVED tag or CONFIRMED wording
        is_confirmed = False
        if "TORNADO...OBSERVED" in text_upper or "CONFIRMED TORNADO" in text_upper:
            is_confirmed = True

        if event == "Tornado Warning" and is_confirmed:
            # Extract location (rough approximation from first line of narrative)
            location = "Unknown Area"
            m_area = re.search(r"(?:near|over)\s+(.+?)(?:,)", raw_text, re.I)
            if m_area:
                location = m_area.group(1).strip()

            # Extract coords
            coords = ""
            m_poly = re.search(r"LAT\.\.\.LON\s+(.+?)(?=\n|\$\$|$)", raw_text, re.DOTALL)
            if m_poly:
                coords = m_poly.group(1).replace("\n", " ").strip()

            office = vtec.get("office", "NWS")
            from utils.state_store import find_matching_tornado
            match_id = await find_matching_tornado(office, time.time(), location, window_hours=1.0)

            event_id = match_id if match_id else f"NWS:WARN:{vtec_id}"

            await add_significant_event(
                event_id=event_id,
                event_type="Tornado",
                location=location,
                magnitude="Confirmed",
                vtec_id=vtec_id,
                coords=coords,
                source=office,
                raw_text=raw_text
            )
            logger.info(f"Logged confirmed tornado for {vtec_id} (match: {match_id is not None})")

            # 2. Trigger VAD Recorder mission
            try:
                poly_coords = parse_warning_polygon(raw_text)
                centroid = get_polygon_centroid(poly_coords)
                if centroid:
                    lat, lon = centroid
                    radar_id = get_nearest_radar(lat, lon)
                    if radar_id:
                        recorder = self.bot.get_cog("RecorderCog")
                        if recorder:
                            recorder.start_mission(radar_id, time.time(), event_id=event_id)
                            logger.info(f"Triggered VAD recorder for {radar_id} near {lat:.2f}, {lon:.2f} (Event: {event_id})")
            except Exception as e:
                logger.warning(f"Failed to trigger VAD recorder for {vtec_id}: {e}")

            return event_id

        return None

    @app_commands.command(name="recenttornadoes", description="List confirmed tornadoes from recent warnings and reports")
    @app_commands.describe(range="Time range to look back")
    @app_commands.choices(range=[
        app_commands.Choice(name="Last Hour", value=1),
        app_commands.Choice(name="Last 3 Hours", value=3),
        app_commands.Choice(name="Last 6 Hours", value=6),
        app_commands.Choice(name="Last 12 Hours", value=12),
        app_commands.Choice(name="Last 24 Hours", value=24),
        app_commands.Choice(name="Last 48 Hours", value=48),
        app_commands.Choice(name="Last 72 Hours", value=72),
        app_commands.Choice(name="Last 7 Days (Week)", value=168),
        app_commands.Choice(name="Last 30 Days (Month)", value=720),
    ])
    async def recent_tornadoes(self, interaction: discord.Interaction, range: int = 24):
        await interaction.response.defer()

        events = await get_recent_significant_events(event_type="Tornado", since_hours=range)
        if not events:
            await interaction.followup.send("No confirmed tornadoes logged in the requested time frame.")
            return

        # Sort by timestamp DESC just in case
        events.sort(key=lambda x: x["timestamp"], reverse=True)

        view = TornadoDashboardView(events, f"🌪️ Confirmed Tornadoes (Last {range}h)")
        embed = view.build_summary_embed()

        await interaction.followup.send(embed=embed, view=view)

    @app_commands.command(name="sigtor", description="List significant (EF2+) tornadoes from recent surveys")
    @app_commands.describe(range="Time range to look back (hours)")
    @app_commands.choices(range=[
        app_commands.Choice(name="Last 24 Hours", value=24),
        app_commands.Choice(name="Last 48 Hours", value=48),
        app_commands.Choice(name="Last 72 Hours", value=72),
        app_commands.Choice(name="Last 7 Days", value=168),
        app_commands.Choice(name="Last 30 Days", value=720),
    ])
    async def sig_tor(self, interaction: discord.Interaction, range: int = 168):
        await interaction.response.defer()

        events = await get_recent_significant_events(event_type="Tornado", since_hours=range)
        if not events:
            await interaction.followup.send("No confirmed tornadoes logged in the requested time frame.")
            return

        # Filter for EF2+ or 'Significant' wording
        sig_events = []
        for e in events:
            mag = (e.get("magnitude") or "").upper()
            is_sig = False
            # Match EF2, EF3, EF4, EF5
            if re.search(r"EF[2-5]", mag):
                is_sig = True
            elif "SIGNIFICANT" in mag or "PDS" in mag:
                is_sig = True

            if is_sig:
                sig_events.append(e)

        if not sig_events:
            await interaction.followup.send(f"No significant (EF2+) tornadoes found in the last {range} hours.")
            return

        # Sort by timestamp DESC
        sig_events.sort(key=lambda x: x["timestamp"], reverse=True)

        view = TornadoDashboardView(sig_events, f"🚨 Significant Tornadoes (Last {range}h)")
        embed = view.build_summary_embed()

        await interaction.followup.send(embed=embed, view=view)

    # phenom+sig → human-readable event name, for cancellation posts
    _PHENOM_EVENT = {
        ("TO", "W"): "Tornado Warning",
        ("SV", "W"): "Severe Thunderstorm Warning",
        ("FF", "W"): "Flash Flood Warning",
        ("FF", "A"): "Flash Flood Watch",
        ("TO", "A"): "Tornado Watch",
        ("SV", "A"): "Severe Thunderstorm Watch",
        ("SPS", "S"): "Special Weather Statement",
    }

    async def _handle_cancellation(
        self, vtec_id: str, reason: str = "Expired / Cancelled", vtec: dict | None = None
    ):
        """Post a new cancellation notice; leave the original warning post untouched."""
        info = self.bot.state.posted_warnings.get(vtec_id)
        if not info:
            return

        channel_id = info.get("channel_id")
        message_id = info.get("message_id")
        if not (channel_id and message_id):
            return

        channel = self.bot.get_channel(channel_id)
        if not channel:
            return

        # Area was stored in posted_warnings when the warning was first posted.
        area = info.get("area", "")

        phenom = (vtec or {}).get("phenom", "")
        sig = (vtec or {}).get("sig", "")
        office = (vtec or {}).get("office", vtec_id.split(".")[0])
        if office.startswith("K") and len(office) == 4:
            office = office[1:]

        # Use style logic to get display name and footer ID (EMERG, PDS, EWX)
        # Note: we don't have the raw text here usually, so it falls back to basic name
        # unless it's a Tornado Warning, but we can't easily distinguish Emergency vs PDS
        # without the text or params.
        event_base = self._PHENOM_EVENT.get((phenom, sig), f"{phenom}.{sig} Warning")
        _, display_event, _, footer_id = get_warning_style(event_base, "", vtec=vtec)

        action_verb = "cancels" if reason == "Cancelled" else "expires"
        area_str = f" for {area}" if area else ""

        # Build a cancellation vtec dict with CAN/EXP action for the URL
        cancel_vtec = dict(vtec or {})
        cancel_vtec["action"] = "CAN" if reason == "Cancelled" else "EXP"
        if not cancel_vtec.get("start"):
            now = datetime.now(timezone.utc)
            cancel_vtec["start"] = now.strftime("%y%m%dT%H%MZ")
        vtec_link = _vtec_url(cancel_vtec)
        unix_ts = _vtec_unix_ts(cancel_vtec)

        description = (
            f"{office} [{action_verb} {display_event}]({vtec_link}){area_str}\n"
            f"[<t:{unix_ts}:R>]"
        )

        # Fetch the IEM Autoplot image — for cancelled events IEM marks it
        # "Event No Longer Active" automatically.
        files = []
        if vtec and ((vtec.get("etn") and vtec["etn"] != "0") or phenom == "SPS"):
            image_url = iem_autoplot_url(vtec)
            filename = f"cancel_{vtec_id.replace('.', '_')}.png"
            try:
                content, status = await http_get_bytes(image_url, retries=1, timeout=10)
                if content and status == 200:
                    files.append(discord.File(BytesIO(content), filename=filename))
            except Exception as e:
                logger.debug(f"No cancellation image for {vtec_id}: {e}")

        embed = discord.Embed(
            description=description,
            color=discord.Color.dark_gray(),
            timestamp=datetime.now(timezone.utc),
        )
        if files:
            embed.set_image(url=f"attachment://{files[0].filename}")

        footer_text = f"VTEC {vtec_id}"
        if footer_id:
            footer_text += f" | {footer_id}"
        embed.set_footer(text=footer_text)

        try:
            await channel.send(embed=embed, files=files)
            self._cancelled_warnings.add(vtec_id)
            logger.info(f"Posted cancellation for {vtec_id}")
        except Exception as e:
            logger.warning(f"Failed to post cancellation for {vtec_id}: {e}")

    @tasks.loop(seconds=30)
    async def auto_poll_warnings(self):
        # The body is wrapped so a single bad alert can't kill the loop
        # — same pattern we use for monitor_high_risk_soundings.
        try:
            await self._tick()
        except Exception as e:
            logger.exception(f"Tick failed: {e}")
            await self._backoff.failure(self.bot)

    async def _tick(self):
        await self.bot.wait_until_ready()
        if not self.bot.state.is_primary:
            return

        channel = self.bot.get_channel(WARNINGS_CHANNEL_ID)
        if not channel:
            logger.warning("Warnings channel not found — skipping poll")
            return

        content, status, validators = await http_get_bytes_conditional(
            NWS_ALERTS_WARNINGS_URL,
            etag=self._validators.get("etag") or None,
            last_modified=self._validators.get("last_modified") or None,
            retries=2,
            timeout=15,
        )
        if status == 304:
            self._backoff.success()
            return
        if not content or status != 200:
            logger.warning(
                f"NWS API returned status {status} — will retry next cycle"
            )
            await self._backoff.failure(self.bot)
            return
        if validators and (validators.get("etag") or validators.get("last_modified")):
            self._validators["etag"] = validators.get("etag", "")
            self._validators["last_modified"] = validators.get("last_modified", "")

        try:
            data = _json.loads(content)
            from models.nws import NWSAlertResponse
            alert_response = NWSAlertResponse.model_validate(data)
        except Exception as e:
            logger.warning(f"JSON/Pydantic parse failed: {e}")
            return

        current_vtec_data = {}
        current_vtec_ids = set()
        from cogs.warning_format import _WARNING_STYLE
        for feature in alert_response.features:
            props = feature.properties
            event = props.event
            if event not in _WARNING_STYLE:
                continue

            vtec_list = props.parameters.VTEC if props.parameters else []
            vtec_dict: Optional[dict] = None
            for v in vtec_list:
                parsed = parse_vtec(v)
                if parsed:
                    vtec_dict = parsed
                    logger.info(f"[WARN_VTEC] nws-api {event}: {parsed['vtec_id']} phenom={parsed.get('phenom')}")
                    # We prefer NEW for the initial tracking, but take any for metadata
                    if parsed["action"] == "NEW":
                        break
            if not vtec_dict:
                continue

            issuance_id = vtec_dict["vtec_id"]
            # Store the vtec dict so disappeared path can use it for graphics
            current_vtec_data[issuance_id] = vtec_dict

            # If it's in the poll at all, it hasn't disappeared.
            if vtec_dict["action"] in ("NEW", "CON", "EXT", "UPG"):
                current_vtec_ids.add(issuance_id)

            # 1. Skip if we already processed this as cancelled in this session.
            # This prevents mass-cancellation spam when the NWS index lags.
            if issuance_id in self._cancelled_warnings:
                continue

            if issuance_id in self.bot.state.posted_warnings:
                # Still active, ensures it stays in the active set
                logger.info(f"[WARN_SKIP] {issuance_id} already posted (action={vtec_dict['action']})")
                if issuance_id not in self.bot.state.active_warnings:
                    self.bot.state.active_warnings[issuance_id] = vtec_dict

                # Check for area change (partial cancellation) on CON products
                if vtec_dict["action"] == "CON":
                    stored_info = self.bot.state.posted_warnings[issuance_id]
                    prev_area = stored_info.get("area", "")
                    curr_area = props.areaDesc or ""

                    if prev_area and curr_area and prev_area != curr_area:
                        # Area changed - likely a partial cancellation
                        if issuance_id not in self._in_flight_vtecs:
                            self._in_flight_vtecs.add(issuance_id)
                            try:
                                try:
                                    await self._post_warning(feature, channel, vtec_dict, event, is_update=True)
                                except discord.HTTPException as e:
                                    logger.exception(f"Update send failed for {issuance_id}: {e}")

                                # Update stored area so we don't spam updates for every poll
                                self.bot.state.posted_warnings[issuance_id]["area"] = curr_area
                                await add_posted_warning(
                                    issuance_id,
                                    stored_info["message_id"],
                                    stored_info["channel_id"],
                                    area=curr_area
                                )
                            finally:
                                self._in_flight_vtecs.discard(issuance_id)
                continue

            # 2. If NOT in posted_warnings, we should post it!
            # Check in-flight set to avoid racing with concurrent NWWS triggers
            if issuance_id in self._in_flight_vtecs:
                continue

            # Final check of posted_warnings to be absolutely sure
            if issuance_id in self.bot.state.posted_warnings:
                continue

            self._in_flight_vtecs.add(issuance_id)

            try:
                # Allow NEW, CON, EXT, and UPG to trigger initial discovery posts.
                # This ensures we catch warnings issued while the bot was down/starting.
                if vtec_dict["action"] not in ("NEW", "CON", "EXT", "UPG"):
                    continue

                # Claim the dedup key BEFORE any awaits
                self.bot.state.posted_warnings[issuance_id] = {}  # placeholder

                try:
                    msg, area_desc = await self._post_warning(feature, channel, vtec_dict, event)
                except discord.HTTPException as e:
                    logger.exception(f"Send failed for {issuance_id}: {e}")
                    # Roll back placeholder on failure
                    if issuance_id in self.bot.state.posted_warnings and not self.bot.state.posted_warnings[issuance_id]:
                        del self.bot.state.posted_warnings[issuance_id]
                    continue

                self.bot.state.active_warnings[issuance_id] = vtec_dict
                self.bot.state.posted_warnings[issuance_id] = {
                    "message_id": msg.id,
                    "channel_id": msg.channel.id,
                    "area": area_desc,
                }
                try:
                    await add_posted_warning(issuance_id, msg.id, msg.channel.id, time.time(), area=area_desc)
                    await prune_posted_warnings()
                except Exception as e:
                    logger.warning(
                        f"Failed to persist {issuance_id}: {e}"
                    )
            finally:
                self._in_flight_vtecs.discard(issuance_id)

        # Detect disappeared warnings (cancellations/expirations)
        disappeared = set(self.bot.state.active_warnings.keys()) - current_vtec_ids
        for vtec_id in disappeared:
            # SPS are often absent from the NWS API poll but shouldn't be auto-cancelled
            if ".SPS." in vtec_id or vtec_id.startswith("20"):
                continue

            # Prefer the vtec context from the current poll features list if it's there
            # (e.g. it's in the list as CAN/EXP), otherwise fallback to our cache
            vtec_context = current_vtec_data.get(vtec_id) or self.bot.state.active_warnings.get(vtec_id)
            await self._handle_cancellation(vtec_id, reason="Expired", vtec=vtec_context)
            self.bot.state.active_warnings.pop(vtec_id, None)

        self._backoff.success()

    async def _post_warning(
        self,
        feature,
        channel: discord.abc.Messageable,
        vtec: dict,
        event: str,
        is_update: bool = False,
    ) -> Tuple[discord.Message, str]:
        props = feature.properties
        description = props.description or ""
        params = props.parameters.model_dump() if props.parameters else {}
        emoji, display_event, color, footer_id = get_warning_style(event, description, params, vtec=vtec)
        vtec_id = vtec["vtec_id"]

        ugc_codes = (props.geocode.UGC or []) if props.geocode else []
        area_desc = _area_with_state(props.areaDesc or "", ugc_codes)

        prev_area = ""
        if is_update:
            prev_area = self.bot.state.posted_warnings.get(vtec_id, {}).get("area", "")

        concise_text = build_concise_warning_text(
            display_event, vtec, feature=feature.model_dump(), ugc_codes=ugc_codes,
            is_update=is_update, prev_area=prev_area
        )

        # Log significant events (tornadoes, hail, wind) to DB
        await self._check_and_log_significant_event(event, description, vtec)

        embed = discord.Embed(
            title=f"{emoji} {display_event}",
            description=concise_text,
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        footer_text = f"VTEC {vtec_id}"
        if footer_id:
            footer_text += f" | {footer_id}"
        embed.set_footer(text=footer_text)

        # Download IEM Autoplot image (only if we have a real ETN, or it's an SPS)
        files = []
        if (vtec.get("etn") and vtec["etn"] != "0") or vtec.get("phenom") == "SPS":
            image_url = iem_autoplot_url(vtec)
            logger.info(f"[WARN_IMG_NWSAPI] {vtec_id}: {image_url}")
            filename = f"warning_{vtec_id.replace('.', '_')}.png"
            f = await _download_warning_image(image_url, filename)
            if f:
                files.append(f)
                embed.set_image(url=f"attachment://{filename}")

        msg = await channel.send(embed=embed, files=files)
        logger.info(f"Posted {event} {vtec_id}")
        return msg, area_desc

    @auto_poll_warnings.after_loop
    async def after_loop(self):
        if self.auto_poll_warnings.is_being_cancelled():
            return
        task = self.auto_poll_warnings.get_task()
        try:
            exc = task.exception() if task else None
        except Exception:
            exc = None
        if exc:
            logger.error(
                f"auto_poll_warnings stopped: "
                f"{type(exc).__name__}: {exc}",
                exc_info=exc,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(WarningsCog(bot))
