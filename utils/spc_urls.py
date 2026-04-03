# utils/spc_urls.py
import os
import re
import logging
from typing import List

from config import (
    CACHE_DIR,
    SPC_OUTLOOK_BASE,
    SPC_URLS_FALLBACK,
    AUTO_CACHE_FILE,
    MANUAL_CACHE_FILE,
)
from utils.http import http_get_bytes, http_get_text
from utils.cache import (
    auto_cache,
    manual_cache,
    get_cache_path_for_url,
    atomic_json_dump,
)

logger = logging.getLogger("scp_bot")

CIG_MIGRATION_FLAG = os.path.join(CACHE_DIR, ".cig_migration_done")


async def get_spc_urls(day: int) -> List[str]:
    """
    Scrape the SPC outlook HTML page to resolve the current issuance-time PNG URLs
    introduced with the CIG format on March 3, 2026.
    Returns empty list if scraping fails — caller will skip posting rather than
    using stale fallback GIFs which may be days old.
    """
    fallback = []
    page_url = f"{SPC_OUTLOOK_BASE}/day{day}otlk.html"

    try:
        content, status = await http_get_bytes(page_url)
        if not content or status != 200:
            logger.warning(f"[DYN_URL] Failed to fetch day{day} page (status={status})")
            return fallback

        html = content.decode("utf-8", errors="ignore")
        base = f"{SPC_OUTLOOK_BASE}/"

        if day in (1, 2):
            otlk_match = re.search(r"show_tab\('(otlk_\d+)'\)", html)
            hazard_tabs = re.findall(r"show_tab\('(probotlk_\d+_(?:torn|wind|hail))'\)", html)

            if not otlk_match:
                snippet = html[:500].replace("\n", " ").strip()
                logger.warning(
                    f"[DYN_URL] day{day} page fetched OK (status=200) but otlk tab pattern "
                    f"not found — page structure may have changed. "
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
                logger.warning(f"[DYN_URL] No hazard tabs found for day{day}, using fallback")
                return fallback

            logger.debug(f"[DYN_URL] Resolved day{day} URLs: {urls}")
            return urls

        elif day == 3:
            otlk_match = re.search(r"show_tab\('(otlk_\d+)'\)", html)
            prob_match = re.search(r"show_tab\('(prob_\d+)'\)", html)

            if not otlk_match or not prob_match:
                logger.warning(f"[DYN_URL] Could not find tabs for day3, using fallback")
                return fallback

            urls = [
                f"{base}day3{otlk_match.group(1)}.png",
                f"{base}day3{prob_match.group(1)}.png",
            ]
            logger.debug(f"[DYN_URL] Resolved day3 URLs: {urls}")
            return urls

    except Exception as e:
        logger.warning(f"[DYN_URL] Exception resolving day{day} URLs: {e}, using fallback")

    return fallback


async def cig_migration():
    """
    One-time cache bust for the CIG PNG format rollout on March 3, 2026.
    Removes stale GIF caches and hash entries so the bot re-downloads fresh PNGs.
    """
    if os.path.exists(CIG_MIGRATION_FLAG):
        return

    logger.info("[CIG MIGRATION] Running one-time cache bust for CIG PNG format rollout")

    all_spc_urls = []
    for day in (1, 2, 3):
        all_spc_urls.extend(SPC_URLS_FALLBACK[day])

    removed_files = 0
    for url in all_spc_urls:
        path = get_cache_path_for_url(url)
        if os.path.exists(path):
            try:
                os.remove(path)
                removed_files += 1
                logger.info(f"[CIG MIGRATION] Removed stale cache file: {path}")
            except Exception as e:
                logger.warning(f"[CIG MIGRATION] Could not remove {path}: {e}")
        auto_cache.pop(url, None)
        manual_cache.pop(url, None)

    spc_keys = [k for k in list(auto_cache.keys()) if "spc.noaa.gov/products/outlook" in k]
    spc_keys += [k for k in list(manual_cache.keys()) if "spc.noaa.gov/products/outlook" in k]
    for k in spc_keys:
        auto_cache.pop(k, None)
        manual_cache.pop(k, None)

    atomic_json_dump(auto_cache, AUTO_CACHE_FILE)
    atomic_json_dump(manual_cache, MANUAL_CACHE_FILE)

    open(CIG_MIGRATION_FLAG, "w").close()
    logger.info(f"[CIG MIGRATION] Done. Removed {removed_files} cached files and cleared hash entries.")
