# utils/cache.py
"""
In-memory state and download/change-detection orchestration.

Persistence is handled by utils.persistence.
Content-change detection is handled by utils.change_detection.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

import aiohttp

from config import (
    AUTO_CACHE_FILE,
    CACHE_DIR,
    CENTRAL,
    MANUAL_CACHE_FILE,
    SPC_SCHEDULE,
)
from utils.change_detection import (
    calculate_hash_bytes,
    get_cache_path_for_url,
    head_changed,
    is_placeholder_image,
)
from utils.http import ensure_session, http_get_bytes, http_head_meta, http_head_ok
from utils.persistence import (
    atomic_json_dump,
    load_json_if_exists,
    load_set_if_exists,
    save_set,
)

logger = logging.getLogger("spc_bot")

# Re-export for backward compatibility
__all__ = [
    "auto_cache",
    "manual_cache",
    "partial_update_state",
    "posted_mds",
    "posted_watches",
    "active_mds",
    "active_watches",
    "last_post_times",
    "last_posted_urls",
    "save_set",
    "atomic_json_dump",
    "get_cache_path_for_url",
    "is_placeholder_image",
    "calculate_hash_bytes",
    "download_single_image",
    "download_images_parallel",
    "check_partial_updates_parallel",
    "save_downloaded_images",
    "check_all_urls_exist_parallel",
    "format_timedelta",
    "MD_CACHE_FILE",
    "WATCH_CACHE_FILE",
]

# ── In-memory state ──────────────────────────────────────────────────────────
manual_cache: Dict[str, str] = {}
auto_cache: Dict[str, str] = {}
partial_update_state: Dict[str, Dict] = {}

MD_CACHE_FILE = os.path.join(CACHE_DIR, "posted_mds.json")
WATCH_CACHE_FILE = os.path.join(CACHE_DIR, "posted_watches.json")

posted_mds: Set[str] = set()
posted_watches: Set[str] = set()
active_mds: Set[str] = set()
active_watches: Dict[str, dict] = {}

last_post_times: Dict[str, Optional[datetime]] = {
    "day1": None,
    "day2": None,
    "day3": None,
    "day48": None,
    "scp": None,
    "md": None,
    "watch": None,
    "csu_day1": None, "csu_day2": None, "csu_day3": None,
    "csu_day4": None, "csu_day5": None, "csu_day6": None,
    "csu_day7": None, "csu_day8": None,
    "csu_panel12": None, "csu_panel38": None,
}
last_posted_urls: Dict[str, List[str]] = {}

# Max tracked items before pruning
MAX_TRACKED_MDS = 200
MAX_TRACKED_WATCHES = 200


# ── SPC update window helpers ────────────────────────────────────────────────
def is_near_spc_update(day: int) -> bool:
    now_ct = datetime.now(CENTRAL)
    if day not in SPC_SCHEDULE:
        return False
    for hour in SPC_SCHEDULE[day]:
        update_time = now_ct.replace(hour=hour, minute=0, second=0, microsecond=0)
        time_until = (update_time - now_ct).total_seconds() / 60
        if -60 <= time_until <= 60:
            return True
        next_day_update = update_time + timedelta(days=1)
        time_until_tomorrow = (next_day_update - now_ct).total_seconds() / 60
        if -60 <= time_until_tomorrow <= 60:
            return True
    return False


def should_use_cache_for_manual(urls: List[str]) -> bool:
    day = None
    s = " ".join(urls)
    if "day1" in s:
        day = 1
    elif "day2" in s:
        day = 2
    elif "day3" in s:
        day = 3
    if day is None:
        return False
    if is_near_spc_update(day):
        logger.debug(f"[CACHE] Near Day {day} update window - forcing fresh download")
        return False
    all_exist = all(
        os.path.exists(get_cache_path_for_url(u)) for u in urls
    )
    if not all_exist:
        return False
    ages = []
    for u in urls:
        p = get_cache_path_for_url(u)
        try:
            ages.append(datetime.now() - datetime.fromtimestamp(os.path.getmtime(p)))
        except Exception:
            return False
    if ages and min(ages) > timedelta(days=3):
        logger.info("[CACHE] Cached files are older than 3 days; refreshing")
        return False
    logger.debug(f"[CACHE] Using cached files for Day {day} (min age: {min(ages)})")
    return True


def format_timedelta(td: timedelta) -> str:
    total = int(td.total_seconds())
    if total < 0:
        return "just now"
    days = total // 86400
    hours = (total % 86400) // 3600
    minutes = (total % 3600) // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


# ── Download helpers ─────────────────────────────────────────────────────────
async def download_single_image(
    url: str,
    cache_file_path: str,
    cache: Dict[str, str],
    retries: int = 3,
) -> Tuple[Optional[str], Optional[bytes], Optional[str]]:
    content, status = await http_get_bytes(url, retries=retries)
    if content is None or status != 200:
        logger.warning(f"Failed to download {url} (status={status})")
        return None, None, None

    if is_placeholder_image(content):
        logger.info(
            f"Downloaded placeholder/tiny image for {url} (size={len(content)}), "
            f"treating as no-update"
        )
        return None, content, None

    h = calculate_hash_bytes(content)
    cache_path = get_cache_path_for_url(url)

    try:
        with open(cache_path, "wb") as f:
            f.write(content)
        if url not in cache or cache.get(url) != h:
            cache[url] = h
            atomic_json_dump(cache, cache_file_path)
            logger.debug(f"Updated cache hash for {url}: {h}")
        logger.debug(f"Downloaded and saved: {url} -> {cache_path}")
        return cache_path, content, h
    except Exception as e:
        logger.warning(f"Error saving {cache_path}: {e}")
        return None, None, None


async def download_images_parallel(
    urls: List[str],
    cache_file_path: str,
    cache: Dict[str, str],
    use_cached: bool = False,
) -> List[str]:
    if use_cached and should_use_cache_for_manual(urls):
        files = [
            get_cache_path_for_url(u)
            for u in urls
            if os.path.exists(get_cache_path_for_url(u))
        ]
        logger.info(f"Using cached files for manual request: {files}")
        return files

    await ensure_session()
    tasks_ = [download_single_image(u, cache_file_path, cache) for u in urls]
    results = await asyncio.gather(*tasks_, return_exceptions=False)
    return [r[0] for r in results if r and r[0] is not None]


async def check_partial_updates_parallel(
    urls: List[str], cache: Dict[str, str]
) -> Tuple[int, int, Dict[str, Tuple[bytes, str]]]:
    """
    For each URL, do a HEAD check then conditionally fetch.
    Returns (updated_count, total_count, downloaded_data).
    """
    await ensure_session()

    # HEAD checks in parallel
    head_results = await asyncio.gather(
        *[head_changed(u, http_head_meta) for u in urls]
    )

    async def fetch_if_changed(
        url: str, should_fetch: bool
    ) -> Tuple[bool, Optional[bytes], Optional[str]]:
        if not should_fetch:
            return False, None, None
        try:
            session = await ensure_session()
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    content = await response.read()
                    new_hash = calculate_hash_bytes(content) if content else None
                    if (
                        new_hash
                        and (url not in cache or cache.get(url) != new_hash)
                        and not is_placeholder_image(content)
                    ):
                        return True, content, new_hash
                    else:
                        return False, content, new_hash
        except Exception as e:
            logger.warning(f"Error fetching {url}: {e}")
        return False, None, None

    # Fetch in parallel for URLs whose HEAD says changed
    fetch_results = await asyncio.gather(
        *[fetch_if_changed(u, head_results[i]) for i, u in enumerate(urls)]
    )

    updated_count = sum(1 for updated, _, _ in fetch_results if updated)
    total_count = len(urls)
    downloaded_data = {}
    for i, res in enumerate(fetch_results):
        updated, content, new_hash = res
        if content is not None and new_hash is not None:
            downloaded_data[urls[i]] = (content, new_hash)
        elif content is not None and new_hash is None:
            downloaded_data[urls[i]] = (content, None)

    head_fetched = sum(1 for h in head_results if h)
    logger.debug(
        f"Partial check: {updated_count}/{total_count} images appear updated "
        f"({head_fetched}/{total_count} warranted full fetch)"
    )
    return updated_count, total_count, downloaded_data


async def save_downloaded_images(
    urls: List[str],
    downloaded_data: Dict[str, Tuple[bytes, Optional[str]]],
    cache_file_path: str,
    cache: Dict[str, str],
) -> List[str]:
    """Save downloaded images to disk and batch-update cache JSON once."""
    files = []
    cache_dirty = False
    for url in urls:
        if url in downloaded_data:
            content, h = downloaded_data[url]
            if content is None:
                continue
            cache_path = get_cache_path_for_url(url)
            try:
                with open(cache_path, "wb") as f:
                    f.write(content)
                if h:
                    cache[url] = h
                    cache_dirty = True
                files.append(cache_path)
                logger.debug(f"Saved cached file for {url} -> {cache_path}")
            except Exception as e:
                logger.warning(f"Error writing {cache_path}: {e}")

    # Single write instead of per-URL
    if cache_dirty:
        atomic_json_dump(cache, cache_file_path)

    return files


async def check_all_urls_exist_parallel(urls: List[str]) -> bool:
    tasks_ = [http_head_ok(u) for u in urls]
    results = await asyncio.gather(*tasks_, return_exceptions=False)
    ok = all(results)
    if not ok:
        logger.warning(
            f"Some URLs not reachable: "
            f"{[(u, r) for u, r in zip(urls, results) if not r]}"
        )
    return ok


def prune_tracked_set(s: Set[str], max_size: int, cache_file: str):
    """Prune a tracked set to the most recent entries by numeric value."""
    if len(s) <= max_size:
        return
    sorted_items = sorted(s, key=lambda x: int(x) if x.isdigit() else 0)
    s.clear()
    s.update(sorted_items[-max_size:])
    save_set(s, cache_file)


# ── Load persisted state on import ───────────────────────────────────────────
manual_cache.update(load_json_if_exists(MANUAL_CACHE_FILE))
auto_cache.update(load_json_if_exists(AUTO_CACHE_FILE))
posted_mds.update(load_set_if_exists(MD_CACHE_FILE))
posted_watches.update(load_set_if_exists(WATCH_CACHE_FILE))
