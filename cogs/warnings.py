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

import asyncio
import json as _json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO
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
    "Severe Weather Statement":    ("⛈️", discord.Color.gold()),
    "Flash Flood Statement":       ("🌊", discord.Color.dark_blue()),
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


async def _download_warning_image(image_url: str, filename: str) -> discord.File | None:
    """Fetch an IEM Autoplot image with up to 6 attempts (~30s window).

    Returns a ready-to-send discord.File, or None if all attempts fail.
    Retries on 404 (IEM map not yet generated) or network errors with
    a 5-second delay between attempts.
    """
    for attempt in range(6):
        try:
            content, status = await http_get_bytes(image_url, retries=1, timeout=15)
            if content and status == 200:
                return discord.File(BytesIO(content), filename=filename)
            
            # If map not found (404) or other error, wait 5s and retry
            if attempt < 5:
                await asyncio.sleep(5)
                continue
            
            logger.warning(
                f"[WARN] Failed to download IEM image after 6 attempts: {image_url} (status={status})"
            )
        except Exception as e:
            if attempt < 5:
                await asyncio.sleep(5)
                continue
            logger.warning(f"[WARN] Error downloading IEM image after 6 attempts: {e}")
        break
    return None


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


class TornadoPhotoView(discord.ui.View):
    def __init__(self, urls: list, parent_view: discord.ui.View, location: str):
        super().__init__(timeout=300)
        self.urls = urls
        self.parent_view = parent_view
        self.location = location
        self.index = 0
        self._update_buttons()

    def _update_buttons(self):
        self.prev_btn.disabled = self.index <= 0
        self.next_btn.disabled = self.index >= len(self.urls) - 1

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"📸 Damage Photos: {self.location}",
            color=discord.Color.teal(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_image(url=self.urls[self.index])
        embed.set_footer(text=f"Photo {self.index + 1} of {len(self.urls)}")
        return embed

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = max(0, self.index - 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = min(len(self.urls) - 1, self.index + 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="🔙 Back to Card", style=discord.ButtonStyle.primary)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=self.parent_view.build_card_embed(), view=self.parent_view)


class TornadoDashboardView(discord.ui.View):
    def __init__(self, events: list, title: str, mode: str = "card"):
        super().__init__(timeout=300)
        self.events = events
        self.title = title
        self.mode = mode # "summary" or "card"
        self.index = 0   # Index for card mode
        
        # Group by date (UTC) for summary mode
        self.grouped = {}
        for e in events:
            dt = datetime.fromtimestamp(e['timestamp'], timezone.utc)
            date_str = dt.strftime("%Y-%m-%d")
            if date_str not in self.grouped:
                self.grouped[date_str] = []
            self.grouped[date_str].append(e)
            
        self.dates = sorted(list(self.grouped.keys()), reverse=True)
        self._update_items()

    def _update_items(self):
        self.clear_items()
        
        if self.mode == "summary":
            # Build select options for summary
            options = [discord.SelectOption(label="Summary Dashboard", value="summary", default=True)]
            for d in self.dates[:24]:
                options.append(discord.SelectOption(label=f"Events for {d}", value=d))
            
            select = discord.ui.Select(options=options, custom_id="date_select")
            select.callback = self.on_select
            self.add_item(select)
        else:
            # Card mode navigation
            self.add_item(self.first_btn)
            self.add_item(self.prev_btn)
            self.add_item(self.next_btn)
            self.add_item(self.last_btn)
            self.add_item(self.summary_btn)

            # Add Photos button if event has location and coords for DAT search
            e = self.events[self.index]
            if e.get("location") and e.get("coords"):
                self.add_item(self.photos_btn)
            
            self.first_btn.disabled = self.index <= 0
            self.prev_btn.disabled = self.index <= 0
            self.next_btn.disabled = self.index >= len(self.events) - 1
            self.last_btn.disabled = self.index >= len(self.events) - 1

        # Global Archive Button
        if self.events:
            min_ts = min(e['timestamp'] for e in self.events)
            max_ts = max(e['timestamp'] for e in self.events)
            min_dt = datetime.fromtimestamp(min_ts, timezone.utc).strftime("%Y-%m-%d")
            max_dt = (datetime.fromtimestamp(max_ts, timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
            
            # Stable URL format using query parameters
            url = f"https://tornadoarchive.com/explorer/?start={min_dt}&end={max_dt}&domain=north_america"
            self.add_item(discord.ui.Button(label="Tornado Archive", url=url, style=discord.ButtonStyle.link))

    async def on_select(self, interaction: discord.Interaction):
        val = interaction.data["values"][0]
        if val == "summary":
            await interaction.response.edit_message(embed=self.build_summary_embed(), view=self)
        else:
            # Switch to card mode at the first event of that day
            day_events = self.grouped[val]
            first_event = sorted(day_events, key=lambda x: x["timestamp"], reverse=True)[0]
            self.index = self.events.index(first_event)
            self.mode = "card"
            self._update_items()
            await interaction.response.edit_message(embed=self.build_card_embed(), view=self)

    @discord.ui.button(label="⏮️ First", style=discord.ButtonStyle.secondary)
    async def first_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = 0
        self._update_items()
        await interaction.response.edit_message(embed=self.build_card_embed(), view=self)

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = max(0, self.index - 1)
        self._update_items()
        await interaction.response.edit_message(embed=self.build_card_embed(), view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = min(len(self.events) - 1, self.index + 1)
        self._update_items()
        await interaction.response.edit_message(embed=self.build_card_embed(), view=self)

    @discord.ui.button(label="Last ⏭️", style=discord.ButtonStyle.secondary)
    async def last_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = len(self.events) - 1
        self._update_items()
        await interaction.response.edit_message(embed=self.build_card_embed(), view=self)

    @discord.ui.button(label="📋 Summary", style=discord.ButtonStyle.primary)
    async def summary_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.mode = "summary"
        self._update_items()
        await interaction.response.edit_message(embed=self.build_summary_embed(), view=self)

    @discord.ui.button(label="📸 Photos", style=discord.ButtonStyle.success)
    async def photos_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        e = self.events[self.index]

        # Need location and coords to search DAT
        location = e.get("location")
        coords = e.get("coords")
        if not location or not coords:
            await interaction.response.send_message("No location data available for this event.", ephemeral=True)
            return

        await interaction.response.defer()
        from utils.events_db import fetch_dat_photos
        magnitude = e.get("magnitude", "")
        urls = await fetch_dat_photos(
            location=location,
            magnitude=magnitude,
            coords=coords,
        )
        if not urls:
            await interaction.followup.send("No damage photos found in the DAT for this event.", ephemeral=True)
            return

        photo_view = TornadoPhotoView(urls, self, location)
        await interaction.edit_original_response(embed=photo_view.build_embed(), view=photo_view)

    def _get_ef_emoji(self, mag: str) -> str:
        mag = (mag or "").upper()
        if "EF5" in mag: return "🟣"
        if "EF4" in mag: return "🔴"
        if "EF3" in mag: return "🟠"
        if "EF2" in mag: return "🟡"
        if "EF1" in mag: return "🟢"
        if "EF0" in mag: return "🔵"
        return "⚪"

    def build_summary_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"{self.title} (Summary)",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc)
        )
        
        for date_str in self.dates[:25]: 
            day_events = self.grouped[date_str]
            counts = {"EF5": 0, "EF4": 0, "EF3": 0, "EF2": 0, "EF1": 0, "EF0": 0, "EFU": 0}
            for e in day_events:
                mag = (e.get("magnitude") or "").upper()
                matched = False
                for scale in ["EF5", "EF4", "EF3", "EF2", "EF1", "EF0"]:
                    if scale in mag:
                        counts[scale] += 1
                        matched = True
                        break
                if not matched:
                    counts["EFU"] += 1
            
            parts = []
            if counts["EF5"]: parts.append(f"🟣 {counts['EF5']}")
            if counts["EF4"]: parts.append(f"🔴 {counts['EF4']}")
            if counts["EF3"]: parts.append(f"🟠 {counts['EF3']}")
            if counts["EF2"]: parts.append(f"🟡 {counts['EF2']}")
            if counts["EF1"]: parts.append(f"🟢 {counts['EF1']}")
            if counts["EF0"]: parts.append(f"🔵 {counts['EF0']}")
            if counts["EFU"]: parts.append(f"⚪ {counts['EFU']}")
            
            val = " ".join(parts) if parts else "Confirmed"
            embed.add_field(name=f"📅 {date_str} ({len(day_events)})", value=val, inline=True)
            
        embed.set_footer(
            text=f"Showing last {min(25, len(self.dates))} active days. Use dropdown to pick a day."
        )
        return embed
        
    def build_card_embed(self) -> discord.Embed:
        e = self.events[self.index]
        dt = datetime.fromtimestamp(e['timestamp'], timezone.utc)
        date_str = dt.strftime("%Y-%m-%d %H:%MZ")
        rel_time = f"<t:{int(e['timestamp'])}:R>"
        
        mag = e.get("magnitude", "Confirmed")
        emoji = self._get_ef_emoji(mag)
        
        embed = discord.Embed(
            title=f"{emoji} Tornado: {e['location']}",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc)
        )
        
        embed.add_field(name="Rating", value=mag, inline=True)
        embed.add_field(name="Time", value=f"{date_str}\n({rel_time})", inline=True)
        embed.add_field(name="Office", value=e['source'], inline=True)
        
        if e.get("lead_time") is not None:
             embed.add_field(name="Lead Time", value=f"{e['lead_time']:.1f} min", inline=True)
        
        if e.get("vtec_id"):
            parts = e["vtec_id"].split(".")
            if len(parts) == 4:
                office, phenom, sig, etn = parts
                url = _vtec_url({
                    "action": "NEW",
                    "office": office,
                    "phenom": phenom,
                    "sig": sig,
                    "etn": etn,
                    "start": dt.strftime("%y%m%dT%H%MZ"),
                })
                embed.add_field(name="VTEC ID", value=f"[{e['vtec_id']}]({url})", inline=True)

        if e.get("dat_guid"):
            dat_url = f"https://apps.dat.noaa.gov/stormdamage/damageviewer/?datglobalid={e['dat_guid']}"
            embed.add_field(name="NWS DAT", value=f"[View Track]({dat_url})", inline=True)

        if e.get("raw_text"):
             text = e["raw_text"]
             if len(text) > 500:
                  text = text[:497] + "..."
             embed.add_field(name="Remarks", value=f"```\n{text}\n```", inline=False)
            
        embed.set_footer(text=f"Event {self.index + 1} of {len(self.events)} | {e['coords']}")
        return embed


class WarningsCog(commands.Cog):
    MANAGED_TASK_NAMES = [("auto_poll_warnings", "auto_poll_warnings")]

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._backoff = TaskBackoff("auto_poll_warnings")
        self._validators = {"etag": "", "last_modified": ""}
        self._cancelled_warnings: set[str] = set()

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

        # Claim the dedup key BEFORE the (possibly slow) Discord send so
        # a concurrent NWS API poll can't double-post.
        if not is_update:
            self.bot.state.posted_warnings[vtec_id] = {} # placeholder
        
        self.bot.state.active_warnings[vtec_id] = vtec

        # Log significant events (tornadoes, hail, wind) to DB
        await self._check_and_log_significant_event(event, raw_text, vtec)

        emoji, display_event, color, footer_id = get_warning_style(event, raw_text)
        
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

        # Download IEM Autoplot image (only if we have a real ETN, or it's an SPS)
        files = []
        if (vtec.get("etn") and vtec["etn"] != "0") or vtec.get("phenom") == "SPS":
            image_url = iem_autoplot_url(vtec)
            filename = f"warning_{vtec_id.replace('.', '_')}.png"
            f = await _download_warning_image(image_url, filename)
            if f:
                files.append(f)
                embed.set_image(url=f"attachment://{filename}")

        try:
            msg = await channel.send(embed=embed, files=files)
            logger.info(f"[WARN] Posted (iembot) {event} {vtec_id} ({'Update' if is_update else 'Issuance'})")
            
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
            if not is_update and vtec_id in self.bot.state.posted_warnings:
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
            self._cancelled_warnings.add(vtec_id)
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

            # If it's in the poll at all, it hasn't disappeared.
            if vtec_dict["action"] in ("NEW", "CON", "EXT", "UPG"):
                current_vtec_ids.add(issuance_id)

            # 1. Skip if we already processed this as cancelled in this session.
            # This prevents mass-cancellation spam when the NWS index lags.
            if issuance_id in self._cancelled_warnings:
                continue

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

            # 2. If NOT in posted_warnings, we should post it!
            # Allow NEW, CON, EXT, and UPG to trigger initial discovery posts.
            # This ensures we catch warnings issued while the bot was down/starting.
            if vtec_dict["action"] not in ("NEW", "CON", "EXT", "UPG"):
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
            f = await _download_warning_image(image_url, filename)
            if f:
                files.append(f)
                embed.set_image(url=f"attachment://{filename}")

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
