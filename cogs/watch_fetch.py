# cogs/watch_fetch.py
"""Watch data fetching — NWS API, SPC pages, and IEM fallback."""

import asyncio
import json as _json
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from cogs.iembot import get_cached_watch_text
from config import NWS_ALERTS_URL, SPC_WATCH_INDEX_URL
from utils.http import http_get_bytes, http_get_bytes_conditional, http_get_text
from utils.state_store import (
    get_state,
    get_validators,
    set_state,
    set_validators,
)

logger = logging.getLogger("spc_bot")

# Hoisted patterns — VTEC is scanned in a loop over every active feature,
# so caching the compiled form measurably helps on large NWS payloads.
_VTEC_RE = re.compile(
    r"/O\.[^.]+\.[A-Z]{4}\.(SV|TO)\.A\.(\d{4})\.",
    re.IGNORECASE,
)
_WW_HREF_RE = re.compile(r'href="[^"]*ww(\d+)\.html"', re.IGNORECASE)
_TORNADO_WATCH_RE = re.compile(r"Tornado Watch Number", re.IGNORECASE)

# Module-level conditional-GET state for the NWS active-alerts feed.
_nws_last_parsed: Optional[Dict[str, dict]] = None


async def get_spc_active_watch_numbers() -> Optional[set]:
    """Scrapes the SPC watch index to get a set of authoritative active watch numbers."""
    spc_html = await http_get_text(SPC_WATCH_INDEX_URL)
    if not spc_html:
        return None
    valid_etns = set()
    for wm in _WW_HREF_RE.finditer(spc_html):
        valid_etns.add(wm.group(1).zfill(4))
    return valid_etns


async def fetch_active_watches_nws() -> Optional[Dict[str, dict]]:
    """
    Fetch active SPC watches from the NWS Alerts API.
    Returns dict: watch_num -> {"type": "SVR"|"TORNADO", "expires": datetime}
    Deduplicates by watch number from the VTEC string.
    """
    global _nws_last_parsed

    # 1. Load persistent state on first run
    state_validators = await get_validators(NWS_ALERTS_URL) or {}
    if _nws_last_parsed is None:
        raw_last = await get_state("watch_last_parsed")
        if raw_last:
            try:
                _nws_last_parsed = _json.loads(raw_last)
                # Re-hydrate ISO strings back to datetime objects
                for w in _nws_last_parsed.values():
                    if w.get("expires"):
                        w["expires"] = datetime.fromisoformat(w["expires"])
                logger.info(f"Resumed {len(_nws_last_parsed)} watches from state store")
            except Exception as e:
                logger.warning(f"Failed to load last_parsed state: {e}")
                _nws_last_parsed = {}
        else:
            _nws_last_parsed = {}

    # 2. Conditional GET
    content, status, validators = await http_get_bytes_conditional(
        NWS_ALERTS_URL,
        etag=state_validators.get("etag"),
        last_modified=state_validators.get("last_modified"),
        retries=2,
        timeout=15,
    )

    if status == 304:
        return _nws_last_parsed

    if not content or status != 200:
        logger.warning(f"NWS API returned status {status} — will retry next cycle")
        return None

    # 3. Update validators
    if validators and (validators.get("etag") or validators.get("last_modified")):
        await set_validators(
            NWS_ALERTS_URL, validators.get("etag", ""), validators.get("last_modified", "")
        )

    try:
        data = _json.loads(content)
    except Exception as e:
        logger.warning(f"NWS API JSON parse error: {e}")
        return None

    # Fetch SPC watch index to build authoritative set of active watch numbers.
    # The NWS API occasionally carries stale/bogus WCN continuations from local
    # WFOs for watches that SPC issued months ago (e.g. KILN still sending CON
    # for watch #0001 from January). The SPC index page is the ground truth.
    spc_html = await http_get_text(SPC_WATCH_INDEX_URL)
    valid_etns: set = set()
    if spc_html:
        for wm in _WW_HREF_RE.finditer(spc_html):
            valid_etns.add(wm.group(1).zfill(4))
    else:
        logger.warning(
            "Could not fetch SPC watch index for ETN validation — accepting all NWS API results"
        )

    result = {}
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        vtec_list = props.get("parameters", {}).get("VTEC", [])
        expires_str = props.get("expires") or props.get("ends")
        for vtec in vtec_list:
            m = _VTEC_RE.search(vtec)
            if not m:
                continue
            watch_num = m.group(2).zfill(4)
            if valid_etns and watch_num not in valid_etns:
                logger.debug(f"Skipping watch #{watch_num} — not listed on SPC watch index")
                continue
            wtype = "TORNADO" if m.group(1).upper() == "TO" else "SVR"
            if watch_num in result:
                break
            expires_dt = None
            if expires_str:
                try:
                    expires_dt = datetime.fromisoformat(expires_str).astimezone(timezone.utc)
                except (ValueError, TypeError) as e:
                    logger.debug(f"Could not parse expires {expires_str!r}: {e}")

            affected_zones = props.get("affectedZones", [])
            result[watch_num] = {
                "type": wtype,
                "expires": expires_dt,
                "affected_zones": affected_zones,
            }

    # 4. Save to persistent state
    _nws_last_parsed = result

    # Serialize for storage (converting datetimes to strings)
    persist_data = {}
    for num, info in result.items():
        copy = dict(info)
        if copy.get("expires"):
            copy["expires"] = copy["expires"].isoformat()
        persist_data[num] = copy

    await set_state("watch_last_parsed", _json.dumps(persist_data))
    return result


async def fetch_latest_watch_numbers() -> List[Tuple[str, str]]:
    """
    Returns (watch_num, watch_type) list. Uses NWS API as primary source,
    falls back to SPC HTML scrape if API fails.
    """
    nws = await fetch_active_watches_nws()
    if nws is None:
        logger.warning("NWS API fetch failed — skipping, no fallback for auto loop")
        return []
    if nws:
        return [(num, info["type"]) for num, info in nws.items()]

    logger.warning("NWS API empty, falling back to SPC HTML scrape")
    html = await http_get_text(SPC_WATCH_INDEX_URL)
    if not html:
        return []

    seen = []
    seen_set = set()
    for m in _WW_HREF_RE.finditer(html):
        num = m.group(1).zfill(4)
        if num in seen_set:
            continue
        seen_set.add(num)
        seen.append(num)

    async def _classify(num: str) -> Tuple[str, str]:
        watch_html = await http_get_text(f"https://www.spc.noaa.gov/products/watch/ww{num}.html")
        wtype = "SVR"
        if watch_html and _TORNADO_WATCH_RE.search(watch_html):
            wtype = "TORNADO"
        return num, wtype

    return list(await asyncio.gather(*[_classify(n) for n in seen]))


async def fetch_watch_details_iem(
    watch_number: str,
) -> Tuple[Optional[str], Optional[str], Optional[str], bool]:
    """
    Fallback: fetch watch details from IEM watches API when SPC is unreachable.
    Uses mesonet.agron.iastate.edu/json/watches.py which has structured data
    including states, probabilities, hail size, and wind gusts.
    Returns (text_summary, image_url, probs, is_pds).
    """
    num_int = int(watch_number)
    year = datetime.now(timezone.utc).year

    text_summary = None
    probs = None
    is_pds = False
    try:
        url = f"https://mesonet.agron.iastate.edu/json/watches.py?year={year}"
        content, status = await http_get_bytes(url, retries=2, timeout=15)
        if content and status == 200:
            data = _json.loads(content)
            for event in data.get("events", []):
                if event.get("num") == num_int:
                    states = event.get("states", "")
                    state_list = ", ".join(states.split(",")) if states else "Unknown"
                    is_pds = event.get("is_pds", False)

                    tor_pct = event.get("tornadoes_1m_strong", 0)
                    hail_pct = event.get("hail_1m_2inch", 0)
                    max_hail = event.get("max_hail_size", 0)
                    max_wind = event.get("max_wind_gust_knots", 0)
                    max_wind_mph = round(max_wind * 1.15078) if max_wind else 0

                    parts = [f"**Areas:** {state_list}"]
                    if is_pds:
                        parts.append("⚠️ **Particularly Dangerous Situation (PDS)**")

                    text_summary = "\n".join(parts)

                    prelim_lines = ["**Probabilities (preliminary — will update)**"]
                    if tor_pct:
                        prelim_lines.append(f"🔴 Sig. tornado (EF2+): **{tor_pct}%**")
                    if hail_pct:
                        prelim_lines.append(f'🟢 2"+ hail: **{hail_pct}%** | Max: **{max_hail}"**')
                    if max_wind_mph:
                        prelim_lines.append(
                            f"🔵 Max gusts: **{max_wind_mph} mph ({int(max_wind)} kt)**"
                        )
                    if len(prelim_lines) > 1:
                        probs = "\n".join(prelim_lines)

                    logger.info(f"Got details from IEM watches API for #{watch_number}")
                    break
    except Exception as e:
        logger.warning(f"IEM watches API failed for #{watch_number}: {e}")

    # No image available from IEM — SPC image will be retried separately
    return text_summary, None, probs, is_pds


async def log_watch_source_timing(watch_number: str):
    """Diagnostic: log timing and data availability from all watch sources."""
    try:
        import time
        from cogs.iembot import get_cached_watch_text

        watch_num = str(watch_number).zfill(4)
        logger.info(f"[WATCH-DIAG] Testing all sources for #{watch_num}...")

        # Test SPC Main
        page_url = f"https://www.spc.noaa.gov/products/watch/ww{watch_num}.html"
        start = time.time()
        from utils.cache import fetch_with_validators

        c, s = await fetch_with_validators(page_url)
        spc_main_html = c.decode("utf-8", errors="ignore") if c and s == 200 else None
        spc_main_elapsed = time.time() - start
        spc_main_has_probs = "Probability" in spc_main_html if spc_main_html else False
        logger.info(f"[WATCH-DIAG] SPC Main: {spc_main_elapsed:.2f}s, probs={spc_main_has_probs}")

        # Test SPC Prob
        prob_url = f"https://www.spc.noaa.gov/products/watch/ww{watch_num}_prob.html"
        start = time.time()
        c, s = await fetch_with_validators(prob_url)
        spc_prob_html = c.decode("utf-8", errors="ignore") if c and s == 200 else None
        spc_prob_elapsed = time.time() - start
        spc_prob_has_probs = "Probability" in spc_prob_html if spc_prob_html else False
        logger.info(f"[WATCH-DIAG] SPC Prob: {spc_prob_elapsed:.2f}s, probs={spc_prob_has_probs}")

        # Test NWWS (cached)
        start = time.time()
        nwws_text = await get_cached_watch_text(watch_num)
        nwws_elapsed = time.time() - start
        nwws_has_probs = "Probability" in nwws_text if nwws_text else False
        logger.info(f"[WATCH-DIAG] NWWS: {nwws_elapsed:.2f}s, probs={nwws_has_probs}")

        # Test IEM
        year = datetime.now(timezone.utc).year
        start = time.time()
        url = f"https://mesonet.agron.iastate.edu/json/watches.py?year={year}"
        content, status = await http_get_bytes(url, retries=1, timeout=10)
        iem_elapsed = time.time() - start
        iem_has_probs = False
        if content and status == 200:
            try:
                data = _json.loads(content)
                for event in data.get("events", []):
                    if str(event.get("num")) == watch_num:
                        iem_has_probs = any(
                            event.get(k, 0) > 0
                            for k in ["tornadoes_1m_strong", "hail_1m_2inch", "max_wind_gust_knots"]
                        )
                        break
            except Exception:
                pass
        logger.info(f"[WATCH-DIAG] IEM API: {iem_elapsed:.2f}s, probs={iem_has_probs}")

    except Exception as e:
        logger.exception(f"[WATCH-DIAG] Error during source timing test: {e}")


async def fetch_watch_details(
    watch_number: str,
) -> Tuple[Optional[str], Optional[str], Optional[str], bool]:
    """Fetch an individual watch page and return (image_url, text_summary, probs, is_pds).
    Races SPC and IEM page fetches simultaneously — whichever returns first wins.
    """
    page_url = f"https://www.spc.noaa.gov/products/watch/ww{watch_number}.html"
    prob_url = f"https://www.spc.noaa.gov/products/watch/ww{watch_number}_prob.html"

    from utils.cache import fetch_with_validators
    import time

    async def _get_text_with_val(u, label=None, retry_on_404=False):
        start = time.time()
        retries = 15 if retry_on_404 else 1
        retry_statuses = [404] if retry_on_404 else None
        c, s = await fetch_with_validators(u, retries=retries, retry_statuses=retry_statuses)
        elapsed = time.time() - start
        result = c.decode("utf-8", errors="ignore") if c and s == 200 else None
        if label and result:
            has_prob = "Probability" in result
            logger.debug(f"[WATCH-SOURCE] {label}: {elapsed:.2f}s, has_probs={has_prob}")
        return result

    html, prob_html = await asyncio.gather(
        _get_text_with_val(page_url, "SPC Main"),
        _get_text_with_val(prob_url, "SPC Prob Page", retry_on_404=True),
    )

    image_url = None
    if html:
        for pattern in [
            rf"ww{watch_number}_overview\.gif",
            rf"ww{watch_number}_radar\.gif",
            rf'ww{watch_number}[^"<\s]*\.gif',
        ]:
            m = re.search(pattern, html, re.IGNORECASE)
            if m:
                fname = m.group(0)
                image_url = f"https://www.spc.noaa.gov/products/watch/{fname}"
                break
        if not image_url:
            image_url = f"https://www.spc.noaa.gov/products/watch/ww{watch_number}_overview.gif"

    text_summary = None
    is_pds = False
    if html:
        if "PARTICULARLY DANGEROUS SITUATION" in html.upper():
            is_pds = True

        text_blocks = re.findall(r"<pre[^>]*>(.*?)</pre>", html, re.DOTALL | re.IGNORECASE)
        for block in text_blocks:
            clean = re.sub(r"<[^>]+>", "", block).strip()
            if len(clean) < 100:
                continue
            if not re.search(r"SEL\d|Watch Number", clean, re.IGNORECASE):
                continue

            lines = [line.strip() for line in clean.splitlines() if line.strip()]

            states = []
            in_states = False
            for line in lines:
                if re.search(r"Watch for portions of", line, re.IGNORECASE):
                    in_states = True
                    continue
                if in_states:
                    if re.search(r"Effective|until|Primary", line, re.IGNORECASE):
                        break
                    if line and not re.search(r"\*", line):
                        states.append(line)

            time_line = None
            for line in lines:
                if re.search(r"Effective this", line, re.IGNORECASE):
                    idx = lines.index(line)
                    combined = " ".join(lines[idx : idx + 3])
                    combined = re.sub(r"\s+", " ", combined).strip()
                    time_line = combined
                    break

            threats = []
            in_threats = False
            for line in lines:
                if re.search(r"Primary threats", line, re.IGNORECASE):
                    in_threats = True
                    continue
                if in_threats:
                    if re.search(r"SUMMARY|PRECAUTIONARY|ATTN", line, re.IGNORECASE):
                        break
                    if line and not line.startswith("*"):
                        if threats and not re.search(
                            r"possible$|mph$|diameter$",
                            threats[-1],
                            re.IGNORECASE,
                        ):
                            threats[-1] += " " + line
                        else:
                            threats.append(line)

            parts = []
            if states:
                parts.append("**Areas:** " + ", ".join(states))
            if time_line:
                parts.append("**Time:** " + time_line)
            if threats:
                parts.append("**Threats:**\n" + "\n".join(f"• {t}" for t in threats[:5]))
            if parts:
                text_summary = "\n".join(parts)
                break

    probs = None
    if prob_html:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", prob_html, re.DOTALL | re.IGNORECASE)
        pairs = []
        for cell in cells:
            clean = re.sub(r"<[^>]+>", " ", cell).strip()
            clean = re.sub(r"\s+", " ", clean)
            clean = clean.replace("&gt;", ">").replace("&lt;", "<").replace("&amp;", "&")
            label_m = re.search(r"Probability of (.{5,80})", clean, re.IGNORECASE)
            value_m = re.search(r"(Low|Mod|High)\s*\(([^)]+)\)", clean, re.IGNORECASE)
            if label_m and not value_m:
                pairs.append([label_m.group(1).strip(), None, None])
            elif value_m and pairs and pairs[-1][1] is None:
                pairs[-1][1] = value_m.group(1)
                pairs[-1][2] = value_m.group(2)

        pairs = [p for p in pairs if p[1] is not None]

        if pairs:
            sections = {
                "Tornado": [],
                "Wind": [],
                "Hail": [],
                "Combined": [],
            }
            for label, level, pct in pairs:
                ll = label.lower()
                if "tornado" in ll:
                    sections["Tornado"].append((label, level, pct))
                elif "wind" in ll:
                    sections["Wind"].append((label, level, pct))
                elif "hail" in ll and "combined" not in ll:
                    sections["Hail"].append((label, level, pct))
                else:
                    sections["Combined"].append((label, level, pct))

            section_emoji = {
                "Tornado": "🔴",
                "Wind": "🔵",
                "Hail": "🟢",
                "Combined": "🟣",
            }
            prob_lines = []
            for section, entries in sections.items():
                if not entries:
                    continue
                prob_lines.append(f"**{section}**")
                for label, level, pct in entries:
                    emoji = section_emoji.get(section, "⚪")
                    prob_lines.append(f"{emoji} {label}: **{level} ({pct})**")
            if prob_lines:
                probs = "\n".join(prob_lines)
            logger.info(f"Parsed {len(pairs)} prob entries for #{watch_number}")
        else:
            logger.warning(f"No prob pairs parsed for #{watch_number}")

    # Check iembot real-time cache first (populated within seconds of issuance)
    cached_text = await get_cached_watch_text(watch_number)
    if cached_text and not text_summary:
        text_summary = cached_text
        logger.info(f"Got text from iembot cache for #{watch_number}")
        if "PARTICULARLY DANGEROUS SITUATION" in cached_text.upper():
            is_pds = True

    # IEM fallback: if SPC page was unreachable, try IEM watches API
    if not html:
        logger.warning(f"SPC unreachable for #{watch_number} — using IEM data")
        iem_summary, iem_img, iem_probs, iem_pds = await fetch_watch_details_iem(watch_number)
        if iem_summary and not text_summary:
            text_summary = iem_summary
            logger.info(f"Got text from IEM for #{watch_number}")
        if iem_img and not image_url:
            image_url = iem_img
            logger.info(f"Got image from IEM for #{watch_number}")
        if iem_probs and not probs:
            probs = iem_probs
            logger.info(f"Got preliminary probs from IEM for #{watch_number}")
        if iem_pds:
            is_pds = True

    return image_url, text_summary, probs, is_pds
