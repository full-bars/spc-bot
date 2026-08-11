"""Warning styling, formatting, and text-building utilities.

Handles styling decisions (emoji, color, severity tags), URL generation,
and warning description text construction. Async image downloading for
IEM Autoplot maps.
"""

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from io import BytesIO
from typing import List, Optional, Tuple

import discord

from utils.http import http_get_bytes

logger = logging.getLogger("spc_bot.warnings")

try:
    import spc_rust_core as _rust

    _RUST_AVAILABLE = True
except ImportError:
    _rust = None  # type: ignore[assignment]
    _RUST_AVAILABLE = False

_STATE_REGEX = re.compile(
    r"[\s,]+(?:[A-Z]{2}|ALABAMA|ALASKA|ARIZONA|ARKANSAS|CALIFORNIA|COLORADO|CONNECTICUT|DELAWARE|FLORIDA|GEORGIA|HAWAII|IDAHO|ILLINOIS|INDIANA|IOWA|KANSAS|KENTUCKY|LOUISIANA|MAINE|MARYLAND|MASSACHUSETTS|MICHIGAN|MINNESOTA|MISSISSIPPI|MISSOURI|MONTANA|NEBRASKA|NEVADA|NEW\s+HAMPSHIRE|NEW\s+JERSEY|NEW\s+MEXICO|NEW\s+YORK|NORTH\s+CAROLINA|NORTH\s+DAKOTA|OHIO|OKLAHOMA|OREGON|PENNSYLVANIA|RHODE\s+ISLAND|SOUTH\s+CAROLINA|SOUTH\s+DAKOTA|TENNESSEE|TEXAS|UTAH|VERMONT|VIRGINIA|WASHINGTON|WEST\s+VIRGINIA|WISCONSIN|WYOMING)$",
    re.IGNORECASE,
)

_STATES_LIST = [
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
    "ALABAMA",
    "ALASKA",
    "ARIZONA",
    "ARKANSAS",
    "CALIFORNIA",
    "COLORADO",
    "CONNECTICUT",
    "DELAWARE",
    "FLORIDA",
    "GEORGIA",
    "HAWAII",
    "IDAHO",
    "ILLINOIS",
    "INDIANA",
    "IOWA",
    "KANSAS",
    "KENTUCKY",
    "LOUISIANA",
    "MAINE",
    "MARYLAND",
    "MASSACHUSETTS",
    "MICHIGAN",
    "MINNESOTA",
    "MISSISSIPPI",
    "MISSOURI",
    "MONTANA",
    "NEBRASKA",
    "NEVADA",
    "NEW HAMPSHIRE",
    "NEW JERSEY",
    "NEW MEXICO",
    "NEW YORK",
    "NORTH CAROLINA",
    "NORTH DAKOTA",
    "OHIO",
    "OKLAHOMA",
    "OREGON",
    "PENNSYLVANIA",
    "RHODE ISLAND",
    "SOUTH CAROLINA",
    "SOUTH DAKOTA",
    "TENNESSEE",
    "TEXAS",
    "UTAH",
    "VERMONT",
    "VIRGINIA",
    "WASHINGTON",
    "WEST VIRGINIA",
    "WISCONSIN",
    "WYOMING",
]

_GEOGRAPHIC_GARBAGE = [
    "AND",
    "INJURED",
    "EXPECT",
    "DAMAGE",
    "TORNADO",
    "THUNDERSTORM",
    "ROOF",
    "SIDING",
    "VEHICLE",
    "REMAIN",
    "ALERT",
    "ROOM",
    "BASEMENT",
    "PEOPLE",
    "LOCATIONS",
    "PRECAUTIONARY",
    "PREPAREDNESS",
    "ACTIONS",
    "FLYING",
    "DEBRIS",
    "BUILDING",
    "TREES",
    "SHELTER",
    "LIKELY",
    "PROTECT",
    "LIGHTNING",
    "INDOORS",
    "THUNDER",
    "KILLERS",
    "REMEMBER",
    "ENOUGH",
    "STRUCK",
    "STORM",
] + _STATES_LIST

_GEOGRAPHIC_GARBAGE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(kw) for kw in _GEOGRAPHIC_GARBAGE) + r")\b",
    re.IGNORECASE,
)

_WARNING_STYLE = {
    "Tornado Warning": ("🌪️", discord.Color.red()),
    "Severe Thunderstorm Warning": ("⛈️", discord.Color.gold()),
    "Flash Flood Warning": ("🌊", discord.Color.dark_blue()),
    "Severe Weather Statement": ("⛈️", discord.Color.gold()),
    "Flash Flood Statement": ("🌊", discord.Color.dark_blue()),
    "Special Weather Statement": ("☁️", discord.Color.blue()),
}


def _is_null_vtec_time(s: str) -> bool:
    """True when `s` is the NWS null-start sentinel '000000T0000Z'."""
    return bool(s) and s.startswith("000000")


def get_warning_style(
    event: str, text: str, params: dict = None, vtec: dict = None
) -> Tuple[str, str, discord.Color, Optional[str]]:
    """Determine (emoji, display_event_name, color, footer_id) based on event type and severity tags.

    If VTEC is provided, use its phenomenon/significance to override event label mismatch
    (e.g., a CON/UPG to a tornado warning might be labeled "Statement" by NWS API).
    """
    # Correct event label based on VTEC if they disagree
    # This handles CON/UPG actions where NWS labels as "Statement" but VTEC shows tornado/severe/etc
    if vtec:
        phenom = vtec.get("phenom", "")
        sig = vtec.get("sig", "")
        if phenom == "TO" and sig == "W" and event != "Tornado Warning":
            event = "Tornado Warning"
        elif phenom == "SV" and sig == "W" and event != "Severe Thunderstorm Warning":
            event = "Severe Thunderstorm Warning"
        elif phenom == "FF" and sig == "W" and event != "Flash Flood Warning":
            event = "Flash Flood Warning"

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


def get_tornado_attributes(
    event: str, text: str, params: dict = None
) -> Tuple[Optional[str], Optional[str]]:
    """Extract tornado confidence and severity from warning text and parameters.

    Returns (confidence, severity) where:
      - confidence: "observed" or "radar_indicated" (None if not a tornado warning)
      - severity: "emergency", "pds", or "standard" (None if not a tornado warning)
    """
    base_event = event.split(" (")[0].split(" — ")[0].split(" – ")[0]
    if base_event in ("Tornado Warning", "Tornado Emergency"):
        base_event = "Tornado Warning"
    if base_event != "Tornado Warning":
        return None, None

    text_upper = (text or "").upper()

    # Extract confidence: observed vs radar_indicated
    confidence = "radar_indicated"  # default
    if (
        "TORNADO...OBSERVED" in text_upper
        or "CONFIRMED TORNADO" in text_upper
        or "CONFIRMED LARGE" in text_upper
    ):
        confidence = "observed"

    # Extract severity: emergency > pds > standard
    severity = "standard"  # default
    if "TORNADO EMERGENCY" in text_upper:
        severity = "emergency"
    elif "PARTICULARLY DANGEROUS SITUATION" in text_upper:
        severity = "pds"

    # Check params for NWS API path (these override text-based detection)
    if params:
        t_detect = params.get("tornadoDetection") or []
        if "OBSERVED" in t_detect:
            confidence = "observed"
        t_threat = params.get("tornadoDamageThreat") or []
        if "CATASTROPHIC" in t_threat:
            severity = "emergency"
        elif "CONSIDERABLE" in t_threat:
            severity = "pds"

    return confidence, severity


def get_warning_severity(event: str, text: str, params: dict = None) -> Optional[str]:
    """Return the generic severity label for any warning type.

    tornado → 'standard' | 'pds' | 'emergency'
    severe  → 'standard' | 'considerable' | 'destructive'
    flash flood → 'standard' | 'emergency'
    """
    text_upper = (text or "").upper()
    # Strip display suffixes like "(PDS)", "(Emergency)" so base name matches
    base_event = event.split(" (")[0].split(" — ")[0].split(" – ")[0]

    # Map display variants to base event names.
    # Styled SVR/FFW names already encode the severity, so fast-path return them.
    # TOR stays as a normalization (text check below handles emergency/pds/standard).
    if base_event in ("Tornado Warning", "Tornado Emergency"):
        base_event = "Tornado Warning"
    elif base_event == "CONSIDERABLE Severe Tstorm Warning":
        return "considerable"
    elif base_event == "DESTRUCTIVE Severe Tstorm Warning":
        return "destructive"
    elif base_event == "Flash Flood Emergency":
        return "emergency"

    if base_event == "Tornado Warning":
        if "TORNADO EMERGENCY" in text_upper:
            return "emergency"
        if "PARTICULARLY DANGEROUS SITUATION" in text_upper:
            return "pds"
        if params:
            t_threat = params.get("tornadoDamageThreat") or []
            if "CATASTROPHIC" in t_threat:
                return "emergency"
            if "CONSIDERABLE" in t_threat:
                return "pds"
        return "standard"

    if base_event == "Severe Thunderstorm Warning":
        if "THUNDERSTORM DAMAGE THREAT...DESTRUCTIVE" in text_upper:
            return "destructive"
        if "THUNDERSTORM DAMAGE THREAT...CONSIDERABLE" in text_upper:
            return "considerable"
        if params:
            s_threat = params.get("thunderstormDamageThreat") or []
            if "DESTRUCTIVE" in s_threat:
                return "destructive"
            if "CONSIDERABLE" in s_threat:
                return "considerable"
        return "standard"

    if base_event == "Flash Flood Warning":
        if "FLASH FLOOD EMERGENCY" in text_upper:
            return "emergency"
        if params:
            f_threat = params.get("flashFloodDamageThreat") or []
            if "CATASTROPHIC" in f_threat:
                return "emergency"
            if "CONSIDERABLE" in f_threat:
                return "considerable"
        return "standard"

    return None


def iem_autoplot_url(vtec: dict, valid_time: Optional[str] = None) -> str:
    """Return the IEM Autoplot URL (#208 for VTEC, #217 for SPS)."""
    office = vtec.get("office", "")
    phenom = vtec.get("phenom", "")
    sig = vtec.get("sig", "")
    etn = vtec.get("etn", "")

    year = datetime.now(timezone.utc).year
    start = vtec.get("start") or ""

    # Use provided valid_time if available (for cancellations/watermarks)
    # format should be 'YYYY-MM-DD HHMM' (UTC)
    if valid_time:
        valid_param = f"::valid:{valid_time.replace(' ', '%20')}"
    else:
        # Build the valid time from start or end timestamp
        valid_time_str = ""
        if start and not _is_null_vtec_time(start):
            # Parse YYMMDDTHHMMZ format
            try:
                yy, mm, dd, hh, min_ = (
                    int(start[0:2]),
                    int(start[2:4]),
                    int(start[4:6]),
                    int(start[7:9]),
                    int(start[9:11]),
                )
                yyyy = 2000 + yy
                valid_time_str = f"{yyyy:04d}-{mm:02d}-{dd:02d}%20{hh:02d}{min_:02d}"
            except (ValueError, IndexError):
                pass
        valid_param = f"::valid:{valid_time_str}" if valid_time_str else ""

    if start and not _is_null_vtec_time(start):
        try:
            year_code = int(start[:2])
            extracted_year = 2000 + year_code
            now_year = datetime.now(timezone.utc).year
            if now_year - 10 <= extracted_year <= now_year + 10:
                year = extracted_year
        except (ValueError, IndexError):
            pass
    elif vtec.get("end") and not _is_null_vtec_time(vtec["end"]):
        try:
            year_code = int(vtec["end"][:2])
            extracted_year = 2000 + year_code
            now_year = datetime.now(timezone.utc).year
            if now_year - 10 <= extracted_year <= now_year + 10:
                year = extracted_year
        except (ValueError, IndexError):
            pass

    # IEM expectations:
    # 1. 3-letter SID for the WFO (e.g. KOUN -> OUN)
    if office.startswith("K") and len(office) == 4:
        office = office[1:]

    # SPS (Special Weather Statements) use Autoplot 217 which requires the PID
    if phenom == "SPS" and "-" in vtec.get("vtec_id", ""):
        return (
            f"https://mesonet.agron.iastate.edu/plotting/auto/plot/217/"
            f"pid:{vtec.get('vtec_id', '')}::segnum:0.png"
        )

    etn_padded = etn.zfill(4)  # Zero-pad to 4 digits

    return (
        f"https://mesonet.agron.iastate.edu/plotting/auto/plot/208/"
        f"network:WFO::wfo:{office}::year:{year}::phenomenav:{phenom}::significancev:{sig}::"
        f"etn:{etn_padded}{valid_param}.png"
    )


def _vtec_url(vtec: dict) -> str:
    """Build an IEM VTEC event page URL from a parsed vtec dict."""
    start = vtec.get("start", "")
    end = vtec.get("end", "")

    # Use end time when start is missing or is the null-start sentinel (CON/EXT/CAN/EXP products)
    ref = (
        start
        if (start and len(start) >= 11 and not _is_null_vtec_time(start))
        else (end if (end and len(end) >= 11 and not _is_null_vtec_time(end)) else "")
    )

    if ref:
        try:
            year = 2000 + int(ref[:2])
            iso = f"{year}-{ref[2:4]}-{ref[4:6]}T{ref[7:9]}:{ref[9:11]}Z"
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
    if start and len(start) >= 11 and not _is_null_vtec_time(start):
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


def _area_with_state(
    area_desc: str, ugc_codes: List[str], state_fallback: Optional[str] = None
) -> str:  # noqa: C901
    """Append [STATE] abbreviations to the area string, grouping counties by state.

    Uses the NWS API geocode.UGC list (e.g. ['MSC023', 'ARC001']) to determine
    which counties belong to which state, then formats them as:
        'Clarke, Jasper, Jones [MS]'                         (single state)
        'Ashley, Chicot [AR] and Washington [MS]'            (two states)
    County names come from area_desc (already comma/semicolon separated).
    The UGC ordering matches the area_desc ordering in NWS API responses.

    If ugc_codes is empty, uses state_fallback (e.g. "OK") for all counties.
    """
    if _RUST_AVAILABLE and ugc_codes and not state_fallback:
        try:
            return _rust.area_with_state(area_desc, list(ugc_codes))
        except Exception:
            pass

    if not ugc_codes:
        if state_fallback:
            return f"{area_desc} [{state_fallback.upper()}]"
        return area_desc

    # Parse county names from areaDesc - try to preserve "County, ST" pairs
    # Split by semicolon or newline first
    counties = [c.strip() for c in re.split(r"[;\n]\s*", area_desc) if c.strip()]

    # If we only have one item and it has commas, it's likely a comma-separated list.
    # Split by comma but ONLY if not followed by a state abbreviation.
    if len(counties) == 1 and "," in counties[0]:
        counties = [
            c.strip() for c in re.split(r",(?!\s+[A-Z]{2}(?:\s|$|,|;))", counties[0]) if c.strip()
        ]

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
        group = counties[idx : idx + count]
        if group:
            # Clean up county names that already have state info (e.g. "Caddo, OK" -> "Caddo")
            cleaned_group = []
            for c in group:
                c_clean = _STATE_REGEX.sub("", c.strip()).strip()
                # Filter out standalone state abbreviations that got split out
                if len(c_clean) > 2 or not c_clean.isupper():
                    cleaned_group.append(c_clean)
            if cleaned_group:
                parts.append(f"{', '.join(cleaned_group)} [{state}]")
        idx += count

    # Any leftover counties (mismatch in UGC/areaDesc lengths) appended to last group
    if idx < len(counties):
        remainder = counties[idx:]
        cleaned_remainder = []
        for r in remainder:
            r_clean = _STATE_REGEX.sub("", r.strip()).strip()
            if r_clean and (len(r_clean) > 2 or not r_clean.isupper()):
                cleaned_remainder.append(r_clean)

        if cleaned_remainder:
            if parts:
                parts[-1] = parts[-1] + f", {', '.join(cleaned_remainder)}"
            else:
                return ", ".join(cleaned_remainder)

    if len(parts) == 1:
        return parts[0]
    elif len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    else:
        return ", ".join(parts[:-1]) + f" and {parts[-1]}"


def _canonical_county_set(area_str: str) -> set[str]:
    """Extract county names from an area-description string into a canonical
    set for comparison across data sources.

    The iembot fast path stores ``"Caddo, Grady [OK]"`` while the NWS API
    returns ``"Caddo; Grady"`` from ``props.areaDesc``.  Normalising both
    to ``{"Caddo", "Grady"}`` lets us detect real area changes (partial
    cancellations) without being fooled by formatting differences.
    """
    if not area_str:
        return set()
    # Strip state brackets: "Caddo [OK]" → "Caddo"
    cleaned = re.sub(r"\s*\[[A-Z]{2}\]", "", area_str)
    # Split on commas, semicolons, or " and "
    parts = re.split(r"[,;]|\s+and\s+", cleaned)
    return {p.strip() for p in parts if p.strip()}


_DESCRIPTION_LIMIT = 4000


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
    office = vtec.get("office", "")
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
    action_verb = action_map.get(vtec.get("action", "NEW"), "updates")
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

    # SPS products: strip the UGC zone header block that precedes the narrative.
    # The header looks like: zone codes / county list / issuance time / blank line.
    # _extract_narrative can't handle it (it keys off BULLETIN/NWS-in markers).
    if vtec.get("phenom") == "SPS" and text_to_search:
        m_hdr = re.search(
            r"\d{3,4}\s+[AP]M\s+[A-Z]{3,4}\s+(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+\w+\s+\d+\s+\d{4}",
            text_to_search,
        )
        if m_hdr:
            text_to_search = text_to_search[m_hdr.end() :].lstrip()
        # Strip $$-terminated footer so the narrative regex doesn't capture it
        m_end = re.search(r"\$\$", text_to_search)
        if m_end:
            text_to_search = text_to_search[: m_end.start()].rstrip()

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

        # Add "Tornado: POSSIBLE" if present in parameters
        if params.get("tornadoPossible"):
            tags.append("tornado: POSSIBLE")

    # Flash Flood Warning tags: [flash flood: radar indicated] (all lowercase)
    elif "Flash Flood" in display_event:
        if params.get("flashFloodDetection"):
            tags.append(f"flash flood: {params['flashFloodDetection'][0].lower()}")
        if params.get("flashFloodDamageThreat"):
            tags.append(f"flash flood damage threat: {params['flashFloodDamageThreat'][0].lower()}")

    # Fallback to regex if no params (iembot path)
    if not tags and text_to_search:
        # Tornado tag: only catch if it's a specific detection or 'POSSIBLE'
        m_tor = re.search(r"TORNADO\.\.\.(.+?)(?:\n|$)", text_to_search, re.I)
        if m_tor:
            val = m_tor.group(1).strip().upper()
            # Only add if it's high signal and NOT the name of the warning itself
            if val not in ("NONE", "FALSE"):
                tags.append(f"tornado: {val}")

        # Refined to catch specific magnitudes like "2.50 IN" or "GOLF BALL"
        # and ignore "HAIL THREAT...RADAR INDICATED"
        m_hail = re.search(
            r"(?:MAX )?HAIL(?: SIZE)?\.\.\.(?!RADAR|THREAT|NONE)(.+?)(?:\n|$)", text_to_search, re.I
        )
        if m_hail:
            hail_val = m_hail.group(1).strip().upper()
            # Skip zero-size hail — NWS emits "0.00 IN" as a default/null value
            if not re.match(r"^0+\.?0*\s+IN$", hail_val):
                tags.append(f"hail: {hail_val}")

        # Refined for wind
        m_wind = re.search(
            r"(?:MAX )?WIND(?: GUST)?\.\.\.(?!RADAR|THREAT|NONE)(.+?)(?:\n|$)", text_to_search, re.I
        )
        if m_wind:
            tags.append(f"wind: {m_wind.group(1).strip().upper()}")

    tag_str = f" [{', '.join(tags)}]" if tags else ""

    # 3. Area (with [STATE] grouping when UGC codes are available)
    area = "affected area"
    state_fallback = None
    if feature:
        area = feature.get("properties", {}).get("areaDesc", area)
    elif raw_text:
        # Step A: Look for the standard "Warning for..." bullet
        # Refined regex: must NOT cross into common narrative headers or the next bullet
        m_area = re.search(
            r"(?:Warning for|Statement for|IMPACT)[\s.]+(.+?)(?=\n\s*\*|\n\s*At\s+|\n\s*LAT\.\.\.LON|\n\s*HAZARD\.\.\.|\n\s*SOURCE\.\.\.|\n\s*IMPACT\.\.\.|\n\s*PRECAUTIONARY|\n\s*PREPAREDNESS|$)",
            raw_text,
            re.I | re.DOTALL,
        )

        # Step B: Fallback to the technical header line (e.g., "CLEVELAND OK-MCCLAIN OK-")
        if not m_area:
            # Broadened to handle mixed case like "Inland Nassau FL-"
            m_header = re.search(r"(?m)^([A-Z\s]+ ([A-Z]{2})-.*?)-$", raw_text, re.I)
            if m_header:
                raw_list = m_header.group(1).replace("-", ", ")
                state_fallback = m_header.group(2)
            else:
                raw_list = ""
        else:
            raw_list = m_area.group(1)
            # Try to peek ahead for a state if not found in the technical header
            m_st = re.search(r"([A-Z]{2})-", raw_text)
            if m_st:
                state_fallback = m_st.group(1)

        if raw_list:
            # For raw text, we split by dots and 'AND' as well
            parts = re.split(r"\n|;|\.\.\.|\s+AND\s+", raw_list, flags=re.I)

            # If we didn't get multiple parts, try a smart comma split
            if len(parts) <= 1:
                parts = [
                    c.strip()
                    for c in re.split(r",(?!\s+[A-Z]{2}(?:\s|$|,|;))", raw_list)
                    if c.strip()
                ]

            counties = []
            for p in parts:
                c = p.strip().strip(".")
                if not c or len(c) < 2:  # Allow 2-letter state codes to be caught by filter
                    continue

                # Truncate at "THROUGH" instead of skipping the whole part
                if " THROUGH " in c.upper():
                    c = re.split(r"\s+THROUGH\s+", c, flags=re.I)[0]

                # Skip if it contains garbage keywords as whole words
                if _GEOGRAPHIC_GARBAGE_RE.search(c):
                    continue
                # Skip remaining structural text
                if any(
                    x in c.upper()
                    for x in [
                        "UNTIL",
                        "PORTIONS",
                        "AM",
                        "PM",
                        "EDT",
                        "CDT",
                        "MDT",
                        "PDT",
                        "HST",
                        "AKDT",
                        "LOCATED",
                    ]
                ):
                    continue
                # Strip prefixes and "County/Parish"
                c = re.sub(
                    r"^(?:Northeastern|Northwestern|Southeastern|Southwestern|Northern|Southern|Eastern|Western|Central)\s+",
                    "",
                    c,
                    flags=re.I,
                )
                c = re.split(r"\s+in\s+", c, flags=re.I)[0]
                c = re.sub(r"\s+(?:Count[iy]|Parish).*$", "", c, flags=re.I)

                # Suffix removal: strip ", OK" or " OK" or " OKLAHOMA"
                c = _STATE_REGEX.sub("", c.strip()).strip()

                # Final check: skip if standalone state or still contains garbage keywords
                if c and (len(c) > 2 or not c.isupper()):
                    if not _GEOGRAPHIC_GARBAGE_RE.search(c):
                        if c not in counties:
                            counties.append(c)

            if counties:
                area = ", ".join(counties)

    if is_update and prev_area:
        # Normalize prev_area: strip bracketed state info "[OK]" and " and " before diffing
        clean_prev = re.sub(r"\s*\[[A-Z]{2}\]", "", prev_area)
        clean_prev = clean_prev.replace(" and ", ", ")

        prev_parts = [c.strip() for c in re.split(r"[;,]\s*", clean_prev) if c.strip()]

        # Clean current area list: split and run through state stripper/garbage filter
        # This prevents "OK, OK" in the expands to list
        raw_curr = [c.strip() for c in re.split(r"[;,]\s*", area) if c.strip()]
        curr_parts = []
        for c in raw_curr:
            c_clean = _STATE_REGEX.sub("", c).strip()
            if c_clean and (len(c_clean) > 2 or not c_clean.isupper()):
                if not _GEOGRAPHIC_GARBAGE_RE.search(c_clean):
                    if c_clean not in curr_parts:
                        curr_parts.append(c_clean)

        # If previous was just "affected area", don't diff, just show the new area
        if prev_area == "affected area":
            area_formatted = (
                f" for {_area_with_state(area, ugc_codes or [], state_fallback=state_fallback)}"
            )
        else:
            prev_set = set(prev_parts)
            curr_set = set(curr_parts)

            # If curr_parts is empty or just "affected area", default to prev_parts
            if (not curr_parts or area == "affected area") and prev_parts:
                curr_parts = prev_parts
                curr_set = prev_set
                area = clean_prev

            cancelled = sorted([c for c in prev_parts if c in (prev_set - curr_set)])
            continuing = sorted([c for c in curr_parts if c in (curr_set & prev_set)])
            new_added = sorted([c for c in curr_parts if c in (curr_set - prev_set)])

            if cancelled or new_added:
                parts = []
                if cancelled:
                    parts.append(f"**cancels** {', '.join(cancelled)}")
                if continuing:
                    parts.append(f"**continues** {', '.join(continuing)}")
                if new_added:
                    parts.append(f"**expands to** {', '.join(new_added)}")

                area_formatted = f" ({', '.join(parts)})"
            else:
                area_formatted = (
                    f" for {_area_with_state(area, ugc_codes or [], state_fallback=state_fallback)}"
                )
    else:
        area_formatted = (
            f" for {_area_with_state(area, ugc_codes or [], state_fallback=state_fallback)}"
        )

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
        # Narrative extraction: capture the "At..." bullet or the primary impact statement.
        # We stop at the next bullet point (*) or known footer blocks.
        m_nat = re.search(
            r"(?:\*\s*)?At\s+(.+?)(?=\n\s*\*|\n\s*LAT\.\.\.LON|\n\s*PRECAUTIONARY|\n\s*TIME\.\.\.MOT\.\.\.LOC|$)",
            text_to_search,
            re.I | re.DOTALL,
        )

        # Fallback for Special Weather Statements which often lead with "...A STRONG THUNDERSTORM..."
        if not m_nat and vtec.get("phenom") == "SPS":
            # Capture from the first significant impact line starting with dots
            m_nat = re.search(
                r"(?:\n|^)\s*\.\.\.([^.].+?)(?=\n\s*\*|\n\s*LAT\.\.\.LON|$)",
                text_to_search,
                re.I | re.DOTALL,
            )

        if m_nat:
            val = m_nat.group(1).strip()

            # Refined bolding: only bold high-signal weather keywords followed by dots.
            # Avoids bolding structural words like "NEAR...", "LOCATED...", or "TIME..."
            def _bold_repl(m):
                word = m.group(1).upper()
                # Only bold if the word (excluding trailing dots) is a high-signal keyword
                base_word = re.sub(r"\.+$", "", word)
                if base_word in (
                    "TORNADO",
                    "HAIL",
                    "WIND",
                    "GUST",
                    "HAZARD",
                    "WATERSPOUT",
                    "IMPACT",
                    "SOURCE",
                    "MAX",
                    "DAMAGE",
                    "THREAT",
                ):
                    return f"**{m.group(1)}**"
                return m.group(1)

            val = re.sub(r"([A-Z]{4,}\b\.{3,})", _bold_repl, val)
            val = re.sub(r"\s+", " ", val).strip()
            val = val.lstrip(".").strip()

            # Use "At" prefix for standard warnings, or just the text for SPS
            if "At" in m_nat.group(0):
                narrative = f"\nAt {val}"
            else:
                narrative = f"\n{val}"

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

    if _RUST_AVAILABLE:
        try:
            return _rust.extract_narrative(raw)
        except Exception:
            pass

    text = raw
    # Drop the WMO header / AFOS header / VTEC line block at the top so
    # we lead with the substantive narrative rather than transmission
    # metadata. Heuristic: find the line that begins "BULLETIN -" or
    # the first line starting with "The National Weather Service in".
    nws_idx = re.search(r"(?m)^(?:BULLETIN.*|The National Weather Service\b.*)$", text)
    if nws_idx:
        text = text[nws_idx.start() :]

    # Trim known footers — order matters because LAT...LON usually
    # precedes ATTN.
    for footer in ("LAT...LON", "ATTN...WFO", "TIME...MOT...LOC", "$$"):
        m = re.search(re.escape(footer), text, re.IGNORECASE)
        if m:
            text = text[: m.start()]
    text = text.strip()
    return text or None


async def _download_warning_image(image_url: str, filename: str) -> discord.File | None:
    """Fetch an IEM Autoplot image with up to 8 attempts (~60s window).

    Returns a ready-to-send discord.File, or None if all attempts fail.
    Retries on 404 (IEM map not yet generated), 400 (bad request/pending),
    or network errors with exponential backoff.
    """
    logger.debug(f"[IMG_DL_START] Attempting to download: {image_url}")
    for attempt in range(8):
        try:
            content, status = await http_get_bytes(image_url, retries=1, timeout=15)
            if content and status == 200:
                if attempt > 0:
                    logger.info(f"[IMG_DL_RECOVERED] {filename} after {attempt} retries")
                logger.debug(f"[IMG_DL_SUCCESS] {filename}: got {len(content)} bytes")
                return discord.File(BytesIO(content), filename=filename)

            logger.debug(
                f"[IMG_DL_RETRY] Attempt {attempt + 1}/8: status={status}, content_len={len(content) if content else 0}"
            )

            # Map might be pending (404/400). Use exponential backoff: 2s, 4s, 8s, 10s...
            if attempt < 7:
                delay = min(2 ** (attempt + 1), 10)
                await asyncio.sleep(delay)
                continue

            logger.warning(
                f"[IMG_DL_FAIL] {filename}: Failed after 8 attempts (final status={status})"
            )
        except Exception as e:
            logger.debug(f"[IMG_DL_ERROR] Attempt {attempt + 1}/8: {e}")
            if attempt < 7:
                delay = min(2 ** (attempt + 1), 10)
                await asyncio.sleep(delay)
                continue
            logger.warning(f"[IMG_DL_FAIL] {filename}: Exception after 8 attempts: {e}")
        break
    return None


async def _attach_warning_image_async(
    msg,
    vtec: dict,
    vtec_id: str,
) -> None:
    """Post-then-edit pattern: download the IEM autoplot image in the
    background and attach it to an already-sent warning message.

    By sending the text embed first and upgrading with the image later,
    the safety-critical warning text is never delayed by IEM's autoplot
    generation latency (up to 60s of 404 retries).
    """
    has_etn = vtec.get("etn") and vtec["etn"] != "0"
    if not has_etn and vtec.get("phenom") != "SPS":
        return

    image_url = iem_autoplot_url(vtec)
    filename = f"warning_{vtec_id.replace('.', '_')}.png"
    file = await _download_warning_image(image_url, filename)
    if file is None:
        logger.warning(f"[WARN_IMG_FAIL] {vtec_id}: image download returned None")
        return

    new_embed = msg.embeds[0].copy() if msg.embeds else None
    if new_embed:
        new_embed.set_image(url=f"attachment://{filename}")

    try:
        if new_embed:
            await msg.edit(embed=new_embed, attachments=[file])
        else:
            await msg.edit(attachments=[file])
    except Exception:
        logger.debug(f"[WARN_IMG_UPGRADE] Edit failed for {vtec_id}", exc_info=True)
