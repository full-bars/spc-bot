# utils/cache.py
"""
In-memory state and download/change-detection orchestration.
Persistence is handled exclusively by utils.db (SQLite).
Content-change detection is handled by utils.change_detection.
"""

import asyncio
import logging
import os
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from config import (
    CENTRAL,
    SPC_SCHEDULE,
)
from utils.state_store import (
    set_hash,
    set_hashes_batch,
    get_all_validators,
    set_validators,
)
from utils.change_detection import (
    calculate_hash_bytes,
    get_cache_path_for_url,
    is_placeholder_image,
)
from utils.http import (
    ensure_session,
    http_get_bytes_conditional,
    http_head_ok,
)

# Per-URL HTTP validators (ETag, Last-Modified) from the most recent 200
# response. Hydrated from the durable store at startup (see
# hydrate_validators_from_store) so the first poll after a restart no
# longer redownloads every URL. LRU-bounded so long-running processes
# don't grow the dict without bound as URLs rotate (e.g. dated filenames).
_VALIDATORS_CACHE_MAX = 2048
_validators_cache: "OrderedDict[str, Dict[str, str]]" = OrderedDict()
_validators_hydrated: bool = False


def _validators_set(url: str, validators: Dict[str, str]) -> None:
    _validators_cache[url] = validators
    _validators_cache.move_to_end(url)
    while len(_validators_cache) > _VALIDATORS_CACHE_MAX:
        _validators_cache.popitem(last=False)


def _validators_get(url: str) -> Dict[str, str]:
    v = _validators_cache.get(url)
    if v is not None:
        _validators_cache.move_to_end(url)
        return v
    return {}


async def hydrate_validators_from_store() -> int:
    """Load persisted validators into the in-process cache. Safe to call
    more than once; only the first call hits the DB."""
    global _validators_hydrated
    if _validators_hydrated:
        return len(_validators_cache)
    stored = await get_all_validators()
    if stored:
        _validators_cache.update(stored)
    _validators_hydrated = True
    logger.info(f"[CACHE] Hydrated {len(_validators_cache)} HTTP validators from store")
    return len(_validators_cache)

logger = logging.getLogger("spc_bot")

__all__ = [
    "get_cache_path_for_url",
    "is_placeholder_image",
    "calculate_hash_bytes",
    "download_single_image",
    "download_images_parallel",
    "check_partial_updates_parallel",
    "save_downloaded_images",
    "check_all_urls_exist_parallel",
    "format_timedelta",
    "MAX_TRACKED_MDS",
    "MAX_TRACKED_WATCHES",
]

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


def _stat_mtimes(paths: List[str]) -> Optional[List[float]]:
    """Stat a batch of files. Returns None if any file is missing or
    unreadable; otherwise a list of mtimes aligned with paths. Runs in
    a worker thread via run_in_executor to keep the event loop free
    on slow storage."""
    out = []
    for p in paths:
        try:
            out.append(os.stat(p).st_mtime)
        except FileNotFoundError:
            return None
        except Exception:
            return None
    return out


async def should_use_cache_for_manual(urls: List[str]) -> bool:
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

    paths = [get_cache_path_for_url(u) for u in urls]
    loop = asyncio.get_running_loop()
    mtimes = await loop.run_in_executor(None, _stat_mtimes, paths)
    if mtimes is None:
        return False
    now = datetime.now()
    ages = [now - datetime.fromtimestamp(m) for m in mtimes]
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


async def _write_file(path: str, data: bytes) -> None:
    """Off-loop file write. Images are small but during bursts (several
    outlook images saved in one tick) synchronous writes stack up."""
    loop = asyncio.get_running_loop()

    def _do():
        with open(path, "wb") as f:
            f.write(data)

    await loop.run_in_executor(None, _do)


# ── Download helpers ─────────────────────────────────────────────────────────
async def download_single_image(
    url: str,
    cache_file_path: str,
    cache: Dict[str, str],
    retries: int = 3,
) -> Tuple[Optional[str], Optional[bytes], Optional[str]]:
    prev = _validators_get(url)
    content, status, validators = await http_get_bytes_conditional(
        url,
        etag=prev.get("etag") or None,
        last_modified=prev.get("last_modified") or None,
        retries=retries,
    )
    cache_path = get_cache_path_for_url(url)

    # 304: body unchanged. If we already have the file on disk we can
    # return it; if not (rare — validator without disk file), fall back
    # to an unconditional fetch next tick by dropping the validator.
    if status == 304:
        if os.path.exists(cache_path):
            return cache_path, None, cache.get(url)
        _validators_cache.pop(url, None)
        return None, None, None

    if content is None or status != 200:
        logger.warning(f"Failed to download {url} (status={status})")
        return None, None, None

    if validators and (validators.get("etag") or validators.get("last_modified")):
        _validators_set(url, validators)
        try:
            await set_validators(
                url,
                validators.get("etag", ""),
                validators.get("last_modified", ""),
            )
        except Exception as e:
            logger.debug(f"[CACHE] set_validators failed for {url}: {e}")

    if is_placeholder_image(content):
        logger.info(
            f"Downloaded placeholder/tiny image for {url} (size={len(content)}), "
            f"treating as no-update"
        )
        return None, content, None

    h = calculate_hash_bytes(content)

    try:
        await _write_file(cache_path, content)
        if url not in cache or cache.get(url) != h:
            cache[url] = h
            cache_type = "manual" if "manual" in cache_file_path else "auto"
            await set_hash(url, h, cache_type)
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
    if use_cached and await should_use_cache_for_manual(urls):
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
) -> Tuple[int, int, Dict[str, Tuple[bytes, Optional[str]]]]:
    """
    For each URL, do a conditional GET (If-None-Match / If-Modified-Since).
    Returns (updated_count, total_count, downloaded_data).
    """
    await ensure_session()
    total_count = len(urls)
    downloaded_data: Dict[str, Tuple[bytes, Optional[str]]] = {}

    async def _check_one(url: str) -> Tuple[str, int]:
        """Return (url, outcome) where outcome is 1=updated, 2=304, 0=other."""
        prev = _validators_get(url)
        content, status, validators = await http_get_bytes_conditional(
            url,
            etag=prev.get("etag") or None,
            last_modified=prev.get("last_modified") or None,
        )
        if status == 304:
            return url, 2
        if content is None or status != 200:
            return url, 0

        if validators and (validators.get("etag") or validators.get("last_modified")):
            _validators_set(url, validators)
            try:
                await set_validators(
                    url,
                    validators.get("etag", ""),
                    validators.get("last_modified", ""),
                )
            except Exception as e:
                logger.debug(f"[CACHE] set_validators failed for {url}: {e}")

        if is_placeholder_image(content):
            return url, 0

        h = calculate_hash_bytes(content)
        if url not in cache or cache.get(url) != h:
            downloaded_data[url] = (content, h)
            return url, 1
        return url, 0

    outcomes = await asyncio.gather(*[_check_one(u) for u in urls])
    updated_count = sum(1 for _, o in outcomes if o == 1)
    not_modified_count = sum(1 for _, o in outcomes if o == 2)

    log = logger.info if updated_count > 0 else logger.debug
    log(
        f"[CACHE] Partial check complete: {updated_count}/{total_count} updated "
        f"({not_modified_count}/{total_count} returned 304 Not Modified)"
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
    batch_updates = {}
    for url in urls:
        if url in downloaded_data:
            content, h = downloaded_data[url]
            if content is None:
                continue
            cache_path = get_cache_path_for_url(url)
            try:
                await _write_file(cache_path, content)
                if h:
                    cache[url] = h
                    batch_updates[url] = h
                files.append(cache_path)
                logger.debug(f"Saved cached file for {url} -> {cache_path}")
            except Exception as e:
                logger.warning(f"Error writing {cache_path}: {e}")

    if batch_updates:
        cache_type = "manual" if "manual" in cache_file_path else "auto"
        # Standardized await for DB integrity
        await set_hashes_batch(batch_updates, cache_type)

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
