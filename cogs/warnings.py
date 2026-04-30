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

import asyncio  # noqa: F401  # used by future PRs
import json as _json
import logging
import re
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import NWS_ALERTS_WARNINGS_URL, WARNINGS_CHANNEL_ID
from utils.backoff import TaskBackoff
from utils.http import http_get_bytes, http_get_bytes_conditional
from utils.state_store import (
    add_posted_warning,
    add_significant_event,
    get_all_posted_warnings,
    get_recent_significant_events,
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
    action, office, phenom, sig, etn, start, end = m.groups()
    return {
        "action": action,
        "office": office,
        "phenom": phenom,
        "sig": sig,
        "etn": etn,
        "start": start,
        "end": end,
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

def get_warning_style(event: str, text: str, params: dict = None) -> Tuple[str, str, discord.Color, Optional[str]]:
    """Determine (emoji, display_event_name, color, footer_id) based on event type and severity tags."""
    emoji, color = _WARNING_STYLE.get(event, ("⚠️", discord.Color.orange()))
    display_event = event
    footer_id = None
    
    # Text-based detection (works for both iembot and NWS API paths)
    text_upper = (text or "").upper()
    
    if event == "Tornado Warning":
        if "TORNADO EMERGENCY" in text_upper:
            return "🚨🚨", "Tornado Emergency", discord.Color.from_rgb(139, 0, 0), "EMERG"
        if "PARTICULARLY DANGEROUS SITUATION" in text_upper:
            return "⚠️", "Tornado Warning (PDS)", discord.Color.red(), "PDS"
            
    if event == "Severe Thunderstorm Warning":
        if "THUNDERSTORM DAMAGE THREAT...DESTRUCTIVE" in text_upper:
             return "🚨", "DESTRUCTIVE Severe Tstorm Warning", discord.Color.purple(), "EWX"
        if "THUNDERSTORM DAMAGE THREAT...CONSIDERABLE" in text_upper:
             return "⚠️", "CONSIDERABLE Severe Tstorm Warning", discord.Color.gold(), "EWX"

    if event == "Flash Flood Warning":
        if "FLASH FLOOD EMERGENCY" in text_upper:
            return "🚨🚨", "Flash Flood Emergency", discord.Color.from_rgb(139, 0, 0), "EMERG"

    # Param-based detection (NWS API specific)
    if params:
        t_threat = params.get("tornadoDamageThreat") or []
        if "CATASTROPHIC" in t_threat:
            return "🚨🚨", "Tornado Emergency", discord.Color.from_rgb(139, 0, 0), "EMERG"
        if "CONSIDERABLE" in t_threat:
            # Note: CONSIDERABLE tag for TOR usually means PDS
            return "⚠️", "Tornado Warning (PDS)", discord.Color.red(), "PDS"

        s_threat = params.get("thunderstormDamageThreat") or []
        if "DESTRUCTIVE" in s_threat:
             return "🚨", "DESTRUCTIVE Severe Tstorm Warning", discord.Color.purple(), "EWX"
        if "CONSIDERABLE" in s_threat:
             return "⚠️", "CONSIDERABLE Severe Tstorm Warning", discord.Color.gold(), "EWX"
             
        f_threat = params.get("flashFloodDamageThreat") or []
        if "CATASTROPHIC" in f_threat:
            return "🚨🚨", "Flash Flood Emergency", discord.Color.from_rgb(139, 0, 0), "EMERG"

    return emoji, display_event, color, footer_id

# Hard-cap on the description block we render inside a code-block —
# Discord embed descriptions cap at 4096 chars total, fences add 8.
_DESCRIPTION_LIMIT = 4000


def iem_autoplot_url(vtec: dict) -> str:
    """Return the IEM Autoplot URL (#208 for VTEC, #217 for SPS)."""
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

    # SPS (Special Weather Statements) use Autoplot 217 which requires the PID
    if phenom == "SPS" and "-" in vtec["vtec_id"]:
        return (
            f"https://mesonet.agron.iastate.edu/plotting/auto/plot/217/"
            f"pid:{vtec['vtec_id']}::segnum:0.png"
        )

    # Standard VTEC events use Autoplot 208
    return (
        f"https://mesonet.agron.iastate.edu/plotting/auto/plot/208/"
        f"wfo:{office}::year:{year}::phenomena:{phenom}::significance:{sig}::"
        f"etn:{etn.lstrip('0') or '0'}.png"
    )


def _vtec_url(vtec: dict) -> str:
    """Build an IEM VTEC event page URL from a parsed vtec dict."""
    start = vtec.get("start", "")
    if start and len(start) >= 11:
        # '260429T0228Z' → '2026-04-29T02:28Z'
        try:
            year = 2000 + int(start[:2])
            iso = f"{year}-{start[2:4]}-{start[4:6]}T{start[7:9]}:{start[9:11]}Z"
        except (ValueError, IndexError):
            now = datetime.now(timezone.utc)
            year = now.year
            iso = now.strftime("%Y-%m-%dT%H:%MZ")
    else:
        now = datetime.now(timezone.utc)
        year = now.year
        iso = now.strftime("%Y-%m-%dT%H:%MZ")
    action = vtec.get("action", "NEW")
    office = vtec.get("office", "")
    phenom = vtec.get("phenom", "")
    sig = vtec.get("sig", "")
    etn = int(vtec.get("etn", "0") or "0")
    return (
        f"https://mesonet.agron.iastate.edu/vtec/f/"
        f"{year}-O-{action}-{office}-{phenom}-{sig}-{etn:04d}_{iso}"
    )


def _vtec_unix_ts(vtec: dict) -> int:
    """Return the Unix timestamp for the VTEC start time, or now if unavailable."""
    start = vtec.get("start", "")
    if start and len(start) >= 11:
        try:
            year = 2000 + int(start[:2])
            month = int(start[2:4])
            day = int(start[4:6])
            hour = int(start[7:9])
            minute = int(start[9:11])
            return int(datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp())
        except (ValueError, IndexError):
            pass
    return int(time.time())


def _area_with_state(area_desc: str, ugc_codes: List[str]) -> str:
    """Append [STATE] abbreviations to the area string, grouping counties by state.

    Uses the NWS API geocode.UGC list (e.g. ['MSC023', 'ARC001']) to determine
    which counties belong to which state, then formats them as:
        'Clarke, Jasper, Jones [MS]'                         (single state)
        'Ashley, Chicot [AR] and Washington [MS]'            (two states)
    County names come from area_desc (already comma/semicolon separated).
    The UGC ordering matches the area_desc ordering in NWS API responses.
    """
    if not ugc_codes:
        return area_desc

    # Parse county names from areaDesc
    counties = [c.strip() for c in re.split(r'[;,]\s*', area_desc) if c.strip()]
    if not counties:
        return area_desc

    # Group UGC codes by state (first 2 chars), preserving order of first appearance
    from collections import OrderedDict
    state_counts: dict = OrderedDict()
    for ugc in ugc_codes:
        if len(ugc) >= 2:
            state = ugc[:2].upper()
            state_counts[state] = state_counts.get(state, 0) + 1

    if not state_counts:
        return area_desc

    # Split county list by state group counts
    parts = []
    idx = 0
    for state, count in state_counts.items():
        group = counties[idx:idx + count]
        if group:
            parts.append(f"{', '.join(group)} [{state}]")
        idx += count

    # Any leftover counties (mismatch in UGC/areaDesc lengths) appended to last group
    if idx < len(counties):
        remainder = counties[idx:]
        if parts:
            parts[-1] = parts[-1] + f", {', '.join(remainder)}"
        else:
            return area_desc

    if len(parts) == 1:
        return parts[0]
    elif len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    else:
        return ", ".join(parts[:-1]) + f" and {parts[-1]}"


def build_concise_warning_text(
    display_event: str,
    vtec: dict,
    raw_text: Optional[str] = None,
    feature: Optional[dict] = None,
    ugc_codes: Optional[List[str]] = None,
    is_update: bool = False,
    prev_area: str = "",
) -> str:
    """Build the warning description for Discord.

    Format: {office} [{verb} {display_event}](vtec_url) [{tags}] for {area} [STATE] till HH:MMZ
            {narrative}
            [<t:unix_ts:R>]
    """
    office = vtec["office"]
    if office.startswith("K") and len(office) == 4:
        office = office[1:]

    # 1. Action verb
    action_map = {
        "NEW": "issues",
        "CON": "continues",
        "CAN": "cancels",
        "EXP": "expires",
        "EXT": "extends time of",
        "UPG": "upgrades",
    }
    action_verb = action_map.get(vtec["action"], "updates")
    if is_update:
        action_verb = "updates"

    # 2. Tags (tornado, hail, wind, flash flood)
    tags = []
    text_to_search = raw_text or ""
    params = {}
    if feature:
        props = feature.get("properties", {})
        text_to_search += " " + (props.get("description") or "")
        params = props.get("parameters", {})

    # Tornado Warning tags: [tornado: RADAR INDICATED, hail: 1.25 IN]
    if "Tornado" in display_event:
        if params.get("tornadoDetection"):
            tags.append(f"tornado: {params['tornadoDetection'][0]}")
        if params.get("tornadoDamageThreat"):
            tags.append(f"damage threat: {params['tornadoDamageThreat'][0]}")
        if params.get("maxHailSize"):
            tags.append(f"hail: {params['maxHailSize'][0]} IN")

    # Severe Thunderstorm Warning tags: [wind: 60 MPH (RADAR INDICATED), hail: 1.25 IN (RADAR INDICATED)]
    elif "Severe Thunderstorm" in display_event:
        w_method = ""
        if params.get("windDetection"):
            w_method = f" ({params['windDetection'][0]})"
        if params.get("maxWindGust"):
            tags.append(f"wind: {params['maxWindGust'][0]}{w_method}")
            
        h_method = ""
        if params.get("hailDetection"):
            h_method = f" ({params['hailDetection'][0]})"
        if params.get("maxHailSize"):
            tags.append(f"hail: {params['maxHailSize'][0]} IN{h_method}")

        if params.get("thunderstormDamageThreat"):
            tags.append(f"damage threat: {params['thunderstormDamageThreat'][0]}")

    # Flash Flood Warning tags: [flash flood: radar indicated] (all lowercase)
    elif "Flash Flood" in display_event:
        if params.get("flashFloodDetection"):
            tags.append(f"flash flood: {params['flashFloodDetection'][0].lower()}")
        if params.get("flashFloodDamageThreat"):
            tags.append(f"flash flood damage threat: {params['flashFloodDamageThreat'][0].lower()}")

    # Fallback to regex if no params (iembot path)
    if not tags and text_to_search:
        m_tor = re.search(r"TORNADO\.\.\.(.+?)(?:\n|$)", text_to_search, re.I)
        if m_tor:
            tags.append(f"tornado: {m_tor.group(1).strip().upper()}")
        m_hail = re.search(r"HAIL\.\.\.(.+?)(?:\n|$)", text_to_search, re.I)
        if m_hail:
            tags.append(f"hail: {m_hail.group(1).strip().upper()}")
        m_wind = re.search(r"WIND\.\.\.(.+?)(?:\n|$)", text_to_search, re.I)
        if m_wind:
            tags.append(f"wind: {m_wind.group(1).strip().upper()}")

    tag_str = f" [{', '.join(tags)}]" if tags else ""

    # 3. Area (with [STATE] grouping when UGC codes are available)
    area = "affected area"
    if feature:
        area = feature.get("properties", {}).get("areaDesc", area)
    elif raw_text:
        m_area = re.search(r"(?:Warning for|Statement for|IMPACT)\s+(.+?)(?=\n\s*\*|\n\s*At\s+|$)", raw_text, re.I | re.DOTALL)
        if m_area:
            raw_list = m_area.group(1)
            parts = re.split(r"\n|\.\.\.|\s+AND\s+", raw_list, flags=re.I)
            counties = []
            for p in parts:
                c = p.strip().strip(".")
                if not c or len(c) < 3:
                    continue
                if any(x in c.upper() for x in ["THROUGH", "UNTIL", "PORTIONS", "AM", "PM", "EDT", "CDT", "MDT", "PDT", "HST", "AKDT"]):
                    continue
                c = re.sub(r"^(?:Northeastern|Northwestern|Southeastern|Southwestern|Northern|Southern|Eastern|Western|Central)\s+", "", c, flags=re.I)
                c = re.split(r"\s+in\s+", c, flags=re.I)[0]
                c = re.sub(r"\s+Count[iy].*$", "", c, flags=re.I)
                c = c.strip()
                if c and c.upper() not in ["CENTRAL", "NORTH", "SOUTH", "EAST", "WEST"] and c not in counties:
                    counties.append(c)
            if counties:
                area = ", ".join(counties)

    if is_update and prev_area:
        # Calculate cancels/continues
        prev_parts = [c.strip() for c in re.split(r'[;,]\s*', prev_area) if c.strip()]
        curr_parts = [c.strip() for c in re.split(r'[;,]\s*', area) if c.strip()]
        
        prev_set = set(prev_parts)
        curr_set = set(curr_parts)
        
        cancelled = sorted([c for c in prev_parts if c in (prev_set - curr_set)])
        continuing = sorted([c for c in curr_parts if c in curr_set])
        
        if cancelled:
            area_formatted = f" (**cancels** {', '.join(cancelled)}, **continues** {', '.join(continuing)})"
        else:
            area_formatted = f" for {_area_with_state(area, ugc_codes or [])}"
    else:
        area_formatted = f" for {_area_with_state(area, ugc_codes or [])}"

    # 4. Expiration time (VTEC end field: '260428T0530Z')
    expires_str = ""
    if vtec.get("end"):
        try:
            z_time = vtec["end"].split("T")[1]
            expires_str = f" till {z_time[:2]}:{z_time[2:4]}Z"
        except (IndexError, ValueError):
            pass

    # 5. Narrative bullet
    narrative = ""
    if text_to_search:
        m_nat = re.search(r"(?:\*\s*)?At\s+(.+?)(?=\n\s*\*|\n\s*LAT\.\.\.LON|\n[A-Z]{4,}\b\.{3,}|$)", text_to_search, re.I | re.DOTALL)
        if m_nat:
            val = m_nat.group(1).strip()
            val = re.sub(r"([A-Z]{4,}\b\.{3,})", r"**\1**", val)
            val = re.sub(r"\s+", " ", val).strip()
            val = val.lstrip(".").strip()
            narrative = f"\nAt {val}"

    # 6. Hyperlinked verb + relative timestamp
    vtec_link = _vtec_url(vtec)
    unix_ts = _vtec_unix_ts(vtec)
    linked_verb = f"[{action_verb} {display_event}]({vtec_link})"

    # Period after area block for updates
    suffix = "." if is_update else ""

    return f"{office} {linked_verb}{tag_str}{area_formatted}{expires_str}{suffix}{narrative}\n[<t:{unix_ts}:R>]"


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

        # Log significant events (tornadoes, hail, wind) to DB
        await self._check_and_log_significant_event(event, raw_text, vtec)

        emoji, display_event, color, footer_id = get_warning_style(event, raw_text)
        concise_text = build_concise_warning_text(display_event, vtec, raw_text=raw_text)

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
            area_desc = ""
            if "properties" in vtec: # iembot path doesn't usually have this
                 pass # extracted logic needed?
            # Actually, area_desc was returned by _post_warning in NWS API path.
            # iembot path is NEW only for now.
            
            # Simple area extraction for iembot path persistence
            area_m = re.search(r"for (.+?) till", concise_text)
            area_desc = area_m.group(1) if area_m else "affected area"

            self.bot.state.posted_warnings[vtec_id] = {
                "message_id": msg.id,
                "channel_id": msg.channel.id,
                "area": area_desc,
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
            await add_posted_warning(vtec_id, msg.id, msg.channel.id, time.time(), area=area_desc)
            await prune_posted_warnings()
        except Exception as e:
            logger.warning(f"[WARN] Failed to persist {vtec_id}: {e}")

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
            logger.info(f"[WARN] Logged confirmed tornado for {vtec_id} (match: {match_id is not None})")

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

        embed = discord.Embed(
            title=f"🌪️ Confirmed Tornadoes (Last {range}h)",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc)
        )
        
        # Sort by timestamp DESC just in case
        events.sort(key=lambda x: x["timestamp"], reverse=True)
        
        for e in events[:10]: # Limit to 10 for the embed
            rel_time = f"<t:{int(e['timestamp'])}:R>"
            mag_str = f" ({e['magnitude']})" if e['magnitude'] and e['magnitude'] != "Confirmed" else ""
            
            val = (
                f"**Location:** {e['location']}\n"
                f"**Office:** {e['source']}\n"
                f"**Time:** {rel_time}\n"
            )
            if e.get("vtec_id"):
                val += f"**VTEC:** `{e['vtec_id']}`"
            
            embed.add_field(name=f"Tornado{mag_str}", value=val, inline=False)

        if len(events) > 10:
            embed.set_footer(text=f"Showing 10 of {len(events)} events.")

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="significantwx", description="View recent significant weather events (Tornado, Giant Hail, Hurricane-force Wind).")
    @app_commands.describe(range="Time range to look back")
    @app_commands.choices(range=[
        app_commands.Choice(name="Last 6 Hours", value=6),
        app_commands.Choice(name="Last 12 Hours", value=12),
        app_commands.Choice(name="Last 24 Hours", value=24),
        app_commands.Choice(name="Last 48 Hours", value=48),
        app_commands.Choice(name="Last 72 Hours", value=72),
        app_commands.Choice(name="Last 7 Days", value=168),
    ])
    async def significant_wx(self, interaction: discord.Interaction, range: int = 24):
        await interaction.response.defer()
        
        # Fetch all types and filter or fetch individually? individually is safer for current API
        tornadoes = await get_recent_significant_events(event_type="Tornado", since_hours=range)
        hail = await get_recent_significant_events(event_type="Hail", since_hours=range)
        wind = await get_recent_significant_events(event_type="Wind", since_hours=range)
        
        events = tornadoes + hail + wind
        if not events:
            await interaction.followup.send("No significant weather events logged in the requested time frame.")
            return

        # Sort by timestamp DESC
        events.sort(key=lambda x: x["timestamp"], reverse=True)
        
        embed = discord.Embed(
            title=f"⚠️ Significant Weather (Last {range}h)",
            color=discord.Color.dark_red(),
            timestamp=datetime.now(timezone.utc)
        )
        
        for e in events[:15]: # Limit to 15
            rel_time = f"<t:{int(e['timestamp'])}:R>"
            
            emoji = "🌪️"
            if e["event_type"] == "Hail":
                emoji = "🧊"
            elif e["event_type"] == "Wind":
                emoji = "🌬️"
            
            mag_str = f" ({e['magnitude']})" if e['magnitude'] else ""
            
            val = (
                f"**Location:** {e['location']}\n"
                f"**Office:** {e['source']} | **Time:** {rel_time}"
            )
            
            embed.add_field(name=f"{emoji} {e['event_type']}{mag_str}", value=val, inline=False)

        if len(events) > 15:
            embed.set_footer(text=f"Showing 15 of {len(events)} events.")

        await interaction.followup.send(embed=embed)

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
        _, display_event, _, footer_id = get_warning_style(event_base, "")
        
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
                    from io import BytesIO
                    files.append(discord.File(BytesIO(content), filename=filename))
            except Exception as e:
                logger.debug(f"[WARN] No cancellation image for {vtec_id}: {e}")

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
            logger.info(f"[WARN] Posted cancellation for {vtec_id}")
        except Exception as e:
            logger.warning(f"[WARN] Failed to post cancellation for {vtec_id}: {e}")

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
            from models.nws import NWSAlertResponse
            alert_response = NWSAlertResponse.model_validate(data)
        except Exception as e:
            logger.warning(f"[WARN] JSON/Pydantic parse failed: {e}")
            return

        current_vtec_data = {}
        current_vtec_ids = set()
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
                
                # Check for area change (partial cancellation) on CON products
                if vtec_dict["action"] == "CON":
                    stored_info = self.bot.state.posted_warnings[issuance_id]
                    prev_area = stored_info.get("area", "")
                    curr_area = props.areaDesc or ""
                    
                    if prev_area and curr_area and prev_area != curr_area:
                        # Area changed - likely a partial cancellation
                        try:
                            await self._post_warning(feature, channel, vtec_dict, event, is_update=True)
                        except discord.HTTPException as e:
                            logger.exception(f"[WARN] Update send failed for {issuance_id}: {e}")
                        
                        # Update stored area so we don't spam updates for every poll
                        self.bot.state.posted_warnings[issuance_id]["area"] = curr_area
                        await add_posted_warning(
                            issuance_id, 
                            stored_info["message_id"], 
                            stored_info["channel_id"], 
                            area=curr_area
                        )
                continue

            if vtec_dict["action"] != "NEW":
                continue

            try:
                msg, area_desc = await self._post_warning(feature, channel, vtec_dict, event)
            except discord.HTTPException as e:
                logger.exception(f"[WARN] Send failed for {issuance_id}: {e}")
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
        feature,
        channel: discord.abc.Messageable,
        vtec: dict,
        event: str,
        is_update: bool = False,
    ) -> Tuple[discord.Message, str]:
        props = feature.properties
        description = props.description or ""
        params = props.parameters.model_dump() if props.parameters else {}
        emoji, display_event, color, footer_id = get_warning_style(event, description, params)
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
                f"[TASK] auto_poll_warnings stopped: "
                f"{type(exc).__name__}: {exc}",
                exc_info=exc,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(WarningsCog(bot))
