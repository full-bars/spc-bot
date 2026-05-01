# utils/spc_urls.py
import logging
import re
from typing import List, Dict, Tuple, Optional

from config import (
    SPC_OUTLOOK_BASE,
)
from utils.http import http_get_bytes_conditional

logger = logging.getLogger("spc_bot")

# Cache to store (etag, last_modified, urls) per day to avoid redundant parsing
_OUTLOOK_CACHE: Dict[int, Tuple[Optional[str], Optional[str], List[str]]] = {}

async def get_spc_urls(day: int) -> List[str]:
    """
    Scrape the SPC outlook HTML page to resolve the current issuance-time PNG URLs
    introduced with the CIG format on March 3, 2026.

    Uses conditional HTTP requests to avoid re-downloading/parsing unchanged pages.
    Returns empty list if scraping fails — caller will skip posting rather than
    using stale fallback GIFs which may be days old.
    """
    fallback: List[str] = []
    page_url = f"{SPC_OUTLOOK_BASE}/day{day}otlk.html"
    
    cached_etag, cached_lm, cached_urls = _OUTLOOK_CACHE.get(day, (None, None, []))

    try:
        content, status, new_headers = await http_get_bytes_conditional(
            page_url, etag=cached_etag, last_modified=cached_lm
        )
        
        if status == 304:
            logger.debug(f"[DYN_URL] day{day} page unchanged (304 Not Modified)")
            return cached_urls
            
        if not content or status != 200:
            logger.warning(
                f"[DYN_URL] Failed to fetch day{day} page (status={status})"
            )
            return fallback

        html = content.decode("utf-8", errors="ignore")
        base = f"{SPC_OUTLOOK_BASE}/"

        if day in (1, 2):
            otlk_match = re.search(r"show_tab\('(otlk_\d+)'\)", html)
            hazard_tabs = re.findall(
                r"show_tab\('(probotlk_\d+_(?:torn|wind|hail))'\)", html
            )

            if not otlk_match:
                snippet = html[:500].replace("\n", " ").strip()
                logger.warning(
                    f"[DYN_URL] day{day} page fetched OK (status=200) but otlk tab "
                    f"pattern not found — page structure may have changed. "
                    f"First 500 chars: {snippet!r}"
                )
                return fallback

            otlk_tab = otlk_match.group(1)
            urls = [f"{base}day{day}{otlk_tab}.png"]

            if hazard_tabs:
                seen = set()
                for tab in hazard_tabs:
                    if tab not in seen:
                        seen.add(tab)
                        urls.append(f"{base}day{day}{tab}.png")
            else:
                logger.warning(
                    f"[DYN_URL] No hazard tabs found for day{day}, using fallback"
                )
                return fallback

            logger.debug(f"[DYN_URL] Resolved day{day} URLs: {urls}")
            if new_headers:
                _OUTLOOK_CACHE[day] = (new_headers.get("etag"), new_headers.get("last_modified"), urls)
            return urls

        elif day == 3:
            otlk_match = re.search(r"show_tab\('(otlk_\d+)'\)", html)
            prob_match = re.search(r"show_tab\('(prob_\d+)'\)", html)

            if not otlk_match or not prob_match:
                logger.warning(
                    "[DYN_URL] Could not find tabs for day3, using fallback"
                )
                return fallback

            urls = [
                f"{base}day3{otlk_match.group(1)}.png",
                f"{base}day3{prob_match.group(1)}.png",
            ]
            logger.debug(f"[DYN_URL] Resolved day3 URLs: {urls}")
            if new_headers:
                _OUTLOOK_CACHE[day] = (new_headers.get("etag"), new_headers.get("last_modified"), urls)
            return urls

    except Exception as e:
        logger.warning(
            f"[DYN_URL] Exception resolving day{day} URLs: {e}, using fallback"
        )

    return fallback
