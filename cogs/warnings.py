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

from io import BytesIO
import asyncio  # noqa: F401  # used by future PRs
import json as _json
import logging
import re
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import discord
from discord.ext import commands, tasks

from config import NWS_ALERTS_WARNINGS_URL, WARNINGS_CHANNEL_ID
from utils.backoff import TaskBackoff
from utils.http import http_get_bytes, http_get_bytes_conditional
from utils.state_store import (
    add_posted_warning,
    get_all_posted_warnings,
    prune_posted_warnings,
)

logger = logging.getLogger("spc_bot")


# ── VTEC parsing ─────────────────────────────────────────────────────────────

# VTEC string format (NWS Directive 10-1703):
#
#   /O.NEW.KOUN.TO.W.0042.260427T2018Z-260427T2100Z/
#    │ │   │    │  │ │    │
#    │ │   │    │  │ │    issuance / expiration timestamps
#    │ │   │    │  │ event tracking number (ETN, 4-digit, stable across the
#    │ │   │    │  │ warning's full lifecycle — our dedup key)
#    │ │   │    │  significance (W=warning, A=watch, Y=advisory, S=statement)
#    │ │   │    phenomenon (TO=tornado, SV=svr tstm, FF=flash flood, etc.)
#    │ │   issuing office (4-letter ICAO, e.g. KOUN = Norman)
#    │ action (NEW, CON, EXP, CAN, UPG, EXA, EXT)
#    fixed: O = operational
_VTEC_RE = re.compile(
    r"/O\.(NEW|CON|EXP|CAN|UPG|EXA|EXT|ROU)\."  # action
    r"([A-Z]{4})\."                              # office
    r"([A-Z]{2})\."                              # phenomenon
    r"([A-Z])\."                                 # significance
    r"(\d{4})\."                                 # ETN
    r"(\d{6}T\d{4}Z)-(\d{6}T\d{4}Z)/"            # start/end
)


def parse_vtec(text: str) -> Optional[dict]:
    """Parse the first VTEC string in ``text`` and return its components.

    Returns a dict with ``action``, ``office``, ``phenom``, ``sig``,
    ``etn``, plus the dedup key ``vtec_id`` (``OFFICE.PH.S.ETN``).
    Returns ``None`` if no VTEC is present.
    """
    if not text:
        return None
    m = _VTEC_RE.search(text)
    if not m:
        return None
    action, office, phenom, sig, etn, _start, _end = m.groups()
    return {
        "action": action,
        "office": office,
        "phenom": phenom,
        "sig": sig,
        "etn": etn,
        "vtec_id": f"{office}.{phenom}.{sig}.{etn}",
    }


# ── Polygon parsing (LAT...LON block) ────────────────────────────────────────

_LATLON_RE = re.compile(
    r"LAT\.\.\.LON\s+([\d\s]+?)(?=\n\s*[A-Z\$]|$)",
    re.IGNORECASE | re.DOTALL,
)


def parse_warning_polygon(
    text: str,
) -> Optional[List[Tuple[float, float]]]:
    """Parse the ``LAT...LON`` polygon block from a VTEC product.

    Format: pairs of integer values, lat then lon, in degrees * 100,
    space- or newline-delimited. Longitudes are reported as positive
    integers; for the US they convert to negative decimal degrees.

    Returns a list of (lat, lon) decimal-degree pairs, or ``None`` if
    the block is missing or unparseable. Used by PR B's iembot
    fallback to derive a polygon centroid when NWS API hasn't picked
    up the alert yet.
    """
    if not text:
        return None
    m = _LATLON_RE.search(text)
    if not m:
        return None
    nums = m.group(1).split()
    coords: List[Tuple[float, float]] = []
    for i in range(0, len(nums) - 1, 2):
        try:
            lat = int(nums[i]) / 100.0
            lon = -(int(nums[i + 1]) / 100.0)
        except ValueError:
            continue
        if not (15.0 <= lat <= 75.0 and -170.0 <= lon <= -60.0):
            # Sanity-clip — NWS warnings only fire over US territory.
            continue
        coords.append((lat, lon))
    return coords or None


# ── Cog ──────────────────────────────────────────────────────────────────────

# (emoji, color) for each event type.
_WARNING_STYLE = {
    "Tornado Warning":             ("🌪️", discord.Color.red()),
    "Severe Thunderstorm Warning": ("⛈️", discord.Color.gold()),
    "Flash Flood Warning":         ("🌊", discord.Color.dark_blue()),
    "Special Weather Statement":   ("☁️", discord.Color.blue()),
}

def get_warning_style(event: str, text: str, params: dict = None) -> Tuple[str, discord.Color]:
    """Determine (emoji_prefix, color) based on event type and severity tags."""
    base_emoji, color = _WARNING_STYLE.get(event, ("⚠️", discord.Color.orange()))
    
    # Text-based detection (works for both iembot and NWS API paths)
    text_upper = (text or "").upper()
    
    if event == "Tornado Warning":
        if "TORNADO EMERGENCY" in text_upper:
            return "🚨🚨 TORNADO EMERGENCY", discord.Color.from_rgb(139, 0, 0)
        if "PARTICULARLY DANGEROUS SITUATION" in text_upper:
            return "⚠️ PDS Tornado Warning", discord.Color.red()
            
    if event == "Severe Thunderstorm Warning":
        if "THUNDERSTORM DAMAGE THREAT...DESTRUCTIVE" in text_upper:
             return "🚨 DESTRUCTIVE Severe Tstorm Warning", discord.Color.purple()
        if "THUNDERSTORM DAMAGE THREAT...CONSIDERABLE" in text_upper:
             return "⚠️ CONSIDERABLE Severe Tstorm Warning", discord.Color.gold()

    # Param-based detection (NWS API specific)
    if params:
        t_threat = params.get("tornadoDamageThreat", [])
        if "CATASTROPHIC" in t_threat:
            return "🚨🚨 TORNADO EMERGENCY", discord.Color.from_rgb(139, 0, 0)
        if "CONSIDERABLE" in t_threat:
            return "⚠️ PDS Tornado Warning", discord.Color.red()
            
        s_threat = params.get("thunderstormDamageThreat", [])
        if "DESTRUCTIVE" in s_threat:
             return "🚨 DESTRUCTIVE Severe Tstorm Warning", discord.Color.purple()
        if "CONSIDERABLE" in s_threat:
             return "⚠️ CONSIDERABLE Severe Tstorm Warning", discord.Color.gold()

    return f"{base_emoji} {event}", color

# Hard-cap on the description block we render inside a code-block —
# Discord embed descriptions cap at 4096 chars total, fences add 8.
_DESCRIPTION_LIMIT = 4000


def iem_autoplot_url(vtec: dict) -> str:
    """Return the IEM Autoplot #208 URL for a given VTEC dict."""
    office = vtec["office"]
    phenom = vtec["phenom"]
    sig = vtec["sig"]
    etn = vtec["etn"]
    year = datetime.now(timezone.utc).year
    if vtec.get("start"):
        try:
             year = 2000 + int(vtec["start"][:2])
        except (ValueError, IndexError):
             pass

    # IEM expectations:
    # 1. 3-letter SID for the WFO (e.g. KOUN -> OUN)
    if office.startswith("K") and len(office) == 4:
        office = office[1:]

    return (
        f"https://mesonet.agron.iastate.edu/plotting/auto/plot/208/"
        f"network:WFO::wfo:{office}::year:{year}::"
        f"phenomenav:{phenom}::significancev:{sig}::"
        f"etn:{etn.lstrip('0') or '0'}.png"
    )


def build_concise_warning_text(
    event: str,
    vtec: dict,
    raw_text: Optional[str] = None,
    feature: Optional[dict] = None,
) -> str:
    """Build a one-line concise warning string for Discord."""
    office = vtec["office"]
    if office.startswith("K") and len(office) == 4:
        office = office[1:]

    # 1. Action Verb
    action_map = {
        "NEW": "issues",
        "CON": "continues",
        "CAN": "cancels",
        "EXP": "expired",
        "UPG": "upgrades",
    }
    action_verb = action_map.get(vtec["action"], "updates")
    
    # 2. Extract Tags (tornado, hail, wind)
    tags = []
    text_to_search = raw_text or ""
    if feature:
        props = feature.get("properties", {})
        text_to_search += " " + (props.get("description") or "")
        params = props.get("parameters", {})
        if params.get("tornadoDetection"):
             tags.append(f"tornado: {params['tornadoDetection'][0]}")
        if params.get("maxHailSize"):
             tags.append(f"hail: {params['maxHailSize'][0]} IN")
        if params.get("maxWindGust"):
             tags.append(f"wind: {params['maxWindGust'][0]}")
    
    if not tags and text_to_search:
        # Regex fallback for iembot path
        m_tor = re.search(r"TORNADO\.\.\.(.+?)(?:\n|$)", text_to_search, re.I)
        if m_tor: tags.append(f"tornado: {m_tor.group(1).strip()}")
        m_hail = re.search(r"HAIL\.\.\.(.+?)(?:\n|$)", text_to_search, re.I)
        if m_hail: tags.append(f"hail: {m_hail.group(1).strip()}")
        m_wind = re.search(r"WIND\.\.\.(.+?)(?:\n|$)", text_to_search, re.I)
        if m_wind: tags.append(f"wind: {m_wind.group(1).strip()}")

    tag_str = f" [{', '.join(tags)}]" if tags else ""

    # 3. Area Description
    area = "affected area"
    if feature:
        area = feature.get("properties", {}).get("areaDesc", area)
    elif raw_text:
        # Greedy search for the area list between a start keyword and the narrative/bullets
        m_area = re.search(r"(?:Warning for|Statement for|IMPACT)\s+(.+?)(?=\n\s*\*|\n\s*At\s+|$)", raw_text, re.I | re.DOTALL)
        if m_area:
            raw_list = m_area.group(1)
            # Split by dots, newlines, or " AND "
            parts = re.split(r"\n|\.\.\.|\s+AND\s+", raw_list, flags=re.I)
            counties = []
            for p in parts:
                c = p.strip().strip(".")
                if not c or len(c) < 3: continue
                # Skip common NWS boilerplate and time phrases
                if any(x in c.upper() for x in ["THROUGH", "UNTIL", "PORTIONS", "AM", "PM", "EDT", "CDT", "MDT", "PDT", "HST", "AKDT"]):
                    continue
                # Remove regional prefixes
                c = re.sub(r"^(?:Northeastern|Northwestern|Southeastern|Southwestern|Northern|Southern|Eastern|Western|Central)\s+", "", c, flags=re.I)
                # Remove region/state suffixes
                c = re.split(r"\s+in\s+", c, flags=re.I)[0]
                # Remove "County" or "Counties"
                c = re.sub(r"\s+Count[iy].*$", "", c, flags=re.I)
                # Final clean and avoid pure direction words
                c = c.strip()
                if c and c.upper() not in ["CENTRAL", "NORTH", "SOUTH", "EAST", "WEST"] and c not in counties:
                    counties.append(c)
            if counties:
                area = ", ".join(counties)

    # 4. Expiration Time
    # VTEC end: 260428T0530Z
    expires_str = ""
    if vtec.get("end"):
        try:
            z_time = vtec["end"].split("T")[1] # e.g. 0530Z
            expires_str = f" till {z_time[:2]}:{z_time[2:4]}Z"
        except (IndexError, ValueError):
            pass

    # 5. Narrative Bullet
    narrative = ""
    if text_to_search:
        # Find the paragraph starting with "At" (optional bullet *)
        # Refined lookahead to stop at CAPS... tags or next bullet.
        m_nat = re.search(r"(?:\*\s*)?At\s+(.+?)(?=\n\s*\*|\n\s*LAT\.\.\.LON|\n[A-Z]{4,}\b\.{3,}|$)", text_to_search, re.I | re.DOTALL)
        if m_nat:
            val = m_nat.group(1).strip()
            # Bold the NWS tags (HAZARD, SOURCE, IMPACT, etc.)
            val = re.sub(r"([A-Z]{4,}\b\.{3,})", r"**\1**", val)
            val = re.sub(r"\s+", " ", val).strip()
            # Clean up leading/trailing dots
            val = val.lstrip(".").strip()
            # No bullet per user request
            narrative = f"\nAt {val}"

    return f"{office} {action_verb} {event}{tag_str} for {area}{expires_str}{narrative}"


def _extract_narrative(raw: str) -> Optional[str]:
    """Pull the human-readable narrative out of a raw VTEC product.

    The narrative is the section after the bulletin headers and before
    the boilerplate footer (LAT...LON, ATTN, $$). Used by the iembot
    fast-path when we don't yet have NWS API's pre-formatted description.
    """
    if not raw:
        return None

    text = raw
    # Drop the WMO header / AFOS header / VTEC line block at the top so
    # we lead with the substantive narrative rather than transmission
    # metadata. Heuristic: find the line that begins "BULLETIN -" or
    # the first line starting with "The National Weather Service in".
    nws_idx = re.search(
        r"(?m)^(?:BULLETIN.*|The National Weather Service\b.*)$", text
    )
    if nws_idx:
        text = text[nws_idx.start():]

    # Trim known footers — order matters because LAT...LON usually
    # precedes ATTN.
    for footer in ("LAT...LON", "ATTN...WFO", "TIME...MOT...LOC", "$$"):
        m = re.search(re.escape(footer), text, re.IGNORECASE)
        if m:
            text = text[: m.start()]
    text = text.strip()
    return text or None


class WarningsCog(commands.Cog):
    MANAGED_TASK_NAMES = [("auto_poll_warnings", "auto_poll_warnings")]

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._backoff = TaskBackoff("auto_poll_warnings")
        self._validators = {"etag": "", "last_modified": ""}

    async def cog_load(self):
        # Restore the dedup mapping so a restart during active wx doesn't
        # replay every warning the bot has already posted today.
        try:
            persisted = await get_all_posted_warnings()
            self.bot.state.posted_warnings.update(persisted)
            logger.info(
                f"[WARN] Restored {len(persisted)} posted warning(s) from store"
            )
        except Exception as e:
            logger.warning(f"[WARN] Could not restore posted_warnings: {e}")

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
                    f"[WARN] iembot trigger: no VTEC in {product_id} — skipping"
                )
                return

        vtec_id = vtec["vtec_id"]
        action = vtec["action"]
        office = vtec["office"]

        # PR D: Lifecycle fast-path (cancel/expire/upgrade)
        if action in ("CAN", "EXP", "UPG"):
            if vtec_id in self.bot.state.active_warnings:
                reason = "Cancelled" if action == "CAN" else ("Upgraded" if action == "UPG" else "Expired")
                # Use stored vtec if available, or current one
                vtec_to_use = vtec or self.bot.state.active_warnings.get(vtec_id)
                await self._handle_cancellation(vtec_id, reason=reason, vtec=vtec_to_use)
                self.bot.state.active_warnings.pop(vtec_id, None)
            return

        if action != "NEW":
            return

        if vtec_id in self.bot.state.posted_warnings:
            return
        # Claim the dedup key BEFORE the (possibly slow) Discord send so
        # a concurrent NWS API poll can't double-post.
        self.bot.state.posted_warnings[vtec_id] = {} # placeholder
        self.bot.state.active_warnings[vtec_id] = vtec

        concise_text = build_concise_warning_text(event, vtec, raw_text=raw_text)
        title, color = get_warning_style(event, raw_text)

        embed = discord.Embed(
            title=title,
            description=concise_text,
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"VTEC {vtec_id}")

        # Download IEM Autoplot image (only if we have a real ETN, or it's an SPS)
        files = []
        if (vtec.get("etn") and vtec["etn"] != "0") or vtec.get("phenom") == "SPS":
            image_url = iem_autoplot_url(vtec)
            filename = f"warning_{vtec_id.replace('.', '_')}.png"
            # IEM maps can take a few seconds to generate after a NEW issuance.
            # Retry on 404 with a small delay.
            for attempt in range(3):
                try:
                    content, status = await http_get_bytes(image_url, retries=1, timeout=15)
                    if content and status == 200:
                        from io import BytesIO
                        files.append(discord.File(BytesIO(content), filename=filename))
                        embed.set_image(url=f"attachment://{filename}")
                        break
                    elif status == 404:
                        if attempt < 2:
                            await asyncio.sleep(5)
                            continue
                    logger.warning(f"[WARN] Failed to download IEM image: {image_url} (status={status})")
                except Exception as e:
                    logger.warning(f"[WARN] Error downloading IEM image: {e}")
                    if attempt < 2:
                        await asyncio.sleep(2)
                        continue
                    break

        try:
            msg = await channel.send(embed=embed, files=files)
            logger.info(f"[WARN] Posted (iembot) {event} {vtec_id}")
            # Update the in-memory mapping with the message info
            self.bot.state.posted_warnings[vtec_id] = {
                "message_id": msg.id,
                "channel_id": msg.channel.id,
            }
        except discord.HTTPException as e:
            # Roll back the dedup claim on a hard send failure
            if vtec_id in self.bot.state.posted_warnings:
                del self.bot.state.posted_warnings[vtec_id]
            logger.exception(
                f"[WARN] iembot send failed for {vtec_id}: {e}"
            )
            return

        try:
            await add_posted_warning(vtec_id, msg.id, msg.channel.id, time.time())
            await prune_posted_warnings()
        except Exception as e:
            logger.warning(f"[WARN] Failed to persist {vtec_id}: {e}")

    async def _handle_cancellation(
        self, vtec_id: str, reason: str = "Expired / Cancelled", vtec: dict | None = None
    ):
        """Edit an existing warning post to mark it as inactive."""
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

        try:
            msg = await channel.fetch_message(message_id)
            if not msg.embeds:
                return

            embed = msg.embeds[0]
            # Avoid double-cancellation logic
            if "✅" in (embed.title or ""):
                return

            # Update title and color
            embed.title = f"✅ {embed.title or ''} — {reason}"
            embed.color = discord.Color.green()

            # Add status to footer
            footer = embed.footer.text or ""
            embed.set_footer(text=f"{footer} | {reason}")

            # Try to fetch updated IEM image for the cancellation
            files = []
            if vtec and ((vtec.get("etn") and vtec["etn"] != "0") or vtec.get("phenom") == "SPS"):
                image_url = iem_autoplot_url(vtec)
                filename = f"cancel_{vtec_id.replace('.', '_')}.png"
                try:
                    content, status = await http_get_bytes(image_url, retries=1, timeout=10)
                    if content and status == 200:
                        from io import BytesIO
                        files.append(discord.File(BytesIO(content), filename=filename))
                        embed.set_image(url=f"attachment://{filename}")
                except Exception as e:
                    logger.debug(f"[WARN] No cancellation image for {vtec_id}: {e}")

            await msg.edit(embed=embed, attachments=files)
            logger.info(f"[WARN] Marked {vtec_id} as {reason} in Discord")

        except discord.NotFound:
            logger.debug(
                f"[WARN] Message for {vtec_id} not found, skipping cancel edit"
            )
        except Exception as e:
            logger.warning(f"[WARN] Failed to cancel {vtec_id}: {e}")

    @tasks.loop(seconds=30)
    async def auto_poll_warnings(self):
        # The body is wrapped so a single bad alert can't kill the loop
        # — same pattern we use for monitor_high_risk_soundings.
        try:
            await self._tick()
        except Exception as e:
            logger.exception(f"[WARN] Tick failed: {e}")
            await self._backoff.failure(self.bot)

    async def _tick(self):
        await self.bot.wait_until_ready()
        if not self.bot.state.is_primary:
            return

        channel = self.bot.get_channel(WARNINGS_CHANNEL_ID)
        if not channel:
            logger.warning("[WARN] Warnings channel not found — skipping poll")
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
                f"[WARN] NWS API returned status {status} — will retry next cycle"
            )
            await self._backoff.failure(self.bot)
            return
        if validators and (validators.get("etag") or validators.get("last_modified")):
            self._validators["etag"] = validators.get("etag", "")
            self._validators["last_modified"] = validators.get("last_modified", "")

        try:
            data = _json.loads(content)
        except Exception as e:
            logger.warning(f"[WARN] JSON parse failed: {e}")
            return

        current_vtec_data = {}
        current_vtec_ids = set()
        for feature in data.get("features", []) or []:
            props = feature.get("properties", {}) or {}
            event = props.get("event", "")
            if event not in _WARNING_STYLE:
                continue

            vtec_list = props.get("parameters", {}).get("VTEC", []) or []
            vtec_dict: Optional[dict] = None
            for v in vtec_list:
                parsed = parse_vtec(v)
                if parsed:
                    vtec_dict = parsed
                    # We prefer NEW for the initial tracking, but take any for metadata
                    if parsed["action"] == "NEW":
                        break
            if not vtec_dict:
                continue
            
            issuance_id = vtec_dict["vtec_id"]
            # Store the vtec dict so disappeared path can use it for graphics
            current_vtec_data[issuance_id] = vtec_dict

            if vtec_dict["action"] == "NEW":
                current_vtec_ids.add(issuance_id)

            if issuance_id in self.bot.state.posted_warnings:
                # Still active, ensures it stays in the active set
                if issuance_id not in self.bot.state.active_warnings:
                    self.bot.state.active_warnings[issuance_id] = vtec_dict
                continue

            if vtec_dict["action"] != "NEW":
                continue

            try:
                msg = await self._post_warning(feature, channel, vtec_dict, event)
            except discord.HTTPException as e:
                logger.exception(f"[WARN] Send failed for {issuance_id}: {e}")
                continue

            self.bot.state.active_warnings[issuance_id] = vtec_dict
            self.bot.state.posted_warnings[issuance_id] = {
                "message_id": msg.id,
                "channel_id": msg.channel.id,
            }
            try:
                await add_posted_warning(issuance_id, msg.id, msg.channel.id, time.time())
                await prune_posted_warnings()
            except Exception as e:
                logger.warning(
                    f"[WARN] Failed to persist {issuance_id}: {e}"
                )

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
        feature: dict,
        channel: discord.abc.Messageable,
        vtec: dict,
        event: str,
    ) -> discord.Message:
        props = feature.get("properties", {}) or {}
        description = props.get("description", "") or ""
        params = props.get("parameters", {})
        title, color = get_warning_style(event, description, params)
        vtec_id = vtec["vtec_id"]

        concise_text = build_concise_warning_text(event, vtec, feature=feature)

        embed = discord.Embed(
            title=title,
            description=concise_text,
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"VTEC {vtec_id}")

        # Download IEM Autoplot image (only if we have a real ETN, or it's an SPS)
        files = []
        if (vtec.get("etn") and vtec["etn"] != "0") or vtec.get("phenom") == "SPS":
            image_url = iem_autoplot_url(vtec)
            filename = f"warning_{vtec_id.replace('.', '_')}.png"
            # IEM maps can take a few seconds to generate after a NEW issuance.
            # Retry on 404 with a small delay.
            for attempt in range(3):
                try:
                    content, status = await http_get_bytes(image_url, retries=1, timeout=15)
                    if content and status == 200:
                        from io import BytesIO
                        files.append(discord.File(BytesIO(content), filename=filename))
                        embed.set_image(url=f"attachment://{filename}")
                        break
                    elif status == 404:
                        if attempt < 2:
                            await asyncio.sleep(5)
                            continue
                    logger.warning(f"[WARN] Failed to download IEM image: {image_url} (status={status})")
                except Exception as e:
                    logger.warning(f"[WARN] Error downloading IEM image: {e}")
                    if attempt < 2:
                        await asyncio.sleep(2)
                        continue
                    break

        msg = await channel.send(embed=embed, files=files)
        logger.info(f"[WARN] Posted {event} {vtec_id}")
        return msg

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
                f"[TASK] auto_poll_warnings stopped: "
                f"{type(exc).__name__}: {exc}",
                exc_info=exc,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(WarningsCog(bot))
