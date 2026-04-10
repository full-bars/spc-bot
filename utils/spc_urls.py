# utils/spc_urls.py
import logging
import re
from typing import List

from config import (
    SPC_OUTLOOK_BASE,
)
from utils.http import http_get_bytes

logger = logging.getLogger("spc_bot")


async def get_spc_urls(day: int) -> List[str]:
    """
    Scrape the SPC outlook HTML page to resolve the current issuance-time PNG URLs
    introduced with the CIG format on March 3, 2026.

    Returns empty list if scraping fails — caller will skip posting rather than
    using stale fallback GIFs which may be days old.
    """
    fallback: List[str] = []
    page_url = f"{SPC_OUTLOOK_BASE}/day{day}otlk.html"

    try:
        content, status = await http_get_bytes(page_url)
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
            return urls

        elif day == 3:
            otlk_match = re.search(r"show_tab\('(otlk_\d+)'\)", html)
            prob_match = re.search(r"show_tab\('(prob_\d+)'\)", html)

            if not otlk_match or not prob_match:
                logger.warning(
                    f"[DYN_URL] Could not find tabs for day3, using fallback"
                )
                return fallback

            urls = [
                f"{base}day3{otlk_match.group(1)}.png",
                f"{base}day3{prob_match.group(1)}.png",
            ]
            logger.debug(f"[DYN_URL] Resolved day3 URLs: {urls}")
            return urls

    except Exception as e:
        logger.warning(
            f"[DYN_URL] Exception resolving day{day} URLs: {e}, using fallback"
        )

    return fallback
