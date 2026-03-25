# utils/cache.py
import os
import json
import hashlib
import logging
import tempfile
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Set

from config import CACHE_DIR, AUTO_CACHE_FILE, MANUAL_CACHE_FILE, SPC_SCHEDULE, CENTRAL
from utils.http import ensure_session, http_head_meta, http_head_ok, http_get_bytes

logger = logging.getLogger("scp_bot")

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
}
last_posted_urls: Dict[str, List[str]] = {}

# Per-URL HEAD snapshot from last poll cycle
_head_cache: Dict[str, Dict[str, str]] = {}


# ── JSON persistence ─────────────────────────────────────────────────────────
def load_json_if_exists(path: str) -> Dict:
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load JSON {path}: {e}")
    return {}


def load_set_if_exists(path: str) -> Set[str]:
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                data = json.load(f)
                return set(data) if isinstance(data, list) else set()
    except Exception as e:
        logger.warning(f"Failed to load set JSON {path}: {e}")
    return set()


def save_set(s: Set[str], path: str):
    atomic_json_dump(sorted(list(s)), path)


def atomic_json_dump(data, filepath: str):
    dirname = os.path.dirname(filepath) or "."
    os.makedirs(dirname, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", delete=False, dir=dirname, suffix=".tmp") as tmp_file:
        json.dump(data, tmp_file, indent=2)
        tmp_file.flush()
        os.fsync(tmp_file.fileno())
    try:
        os.replace(tmp_file.name, filepath)
    except Exception as e:
        logger.warning(f"atomic_json_dump replace failed, trying fallback: {e}")
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)


# ── File/hash helpers ────────────────────────────────────────────────────────
def calculate_hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def get_cache_path_for_url(url: str) -> str:
    md5 = hashlib.md5(url.encode()).hexdigest()
    _, ext = os.path.splitext(url)
    ext = ext if ext else ".img"
    filename = f"cached_{md5}{ext}"
    return os.path.join(CACHE_DIR, filename)


def is_placeholder_image(content: bytes) -> bool:
    if not content:
        return True
    if len(content) < 2048:
        return True
    return False


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
        logger.info(f"[CACHE] Near Day {day} update window - forcing fresh download")
        return False
    all_exist = all(os.path.exists(get_cache_path_for_url(u)) for u in urls)
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
    logger.info(f"[CACHE] Using cached files for Day {day} (min age: {min(ages)})")
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
async def download_single_image(url: str, cache_file_path: str, cache: Dict[str, str], retries: int = 3) -> Tuple[Optional[str], Optional[bytes], Optional[str]]:
    content, status = await http_get_bytes(url, retries=retries)
    if content is None or status != 200:
        logger.warning(f"Failed to download {url} (status={status})")
        return None, None, None

    if is_placeholder_image(content):
        logger.info(f"Downloaded placeholder/tiny image for {url} (size={len(content)}), treating as no-update")
        return None, content, None

    h = calculate_hash_bytes(content)
    cache_path = get_cache_path_for_url(url)

    try:
        with open(cache_path, "wb") as f:
            f.write(content)
        if url not in cache or cache.get(url) != h:
            cache[url] = h
            atomic_json_dump(cache, cache_file_path)
            logger.info(f"Updated cache hash for {url}: {h}")
        logger.info(f"Downloaded and saved: {url} -> {cache_path}")
        return cache_path, content, h
    except Exception as e:
        logger.warning(f"Error saving {cache_path}: {e}")
        return None, None, None


async def download_images_parallel(urls: List[str], cache_file_path: str, cache: Dict[str, str], use_cached: bool = False) -> List[str]:
    if use_cached and should_use_cache_for_manual(urls):
        files = [get_cache_path_for_url(u) for u in urls if os.path.exists(get_cache_path_for_url(u))]
        logger.info(f"Using cached files for manual request: {files}")
        return files

    await ensure_session()
    tasks_ = [download_single_image(u, cache_file_path, cache) for u in urls]
    results = await asyncio.gather(*tasks_, return_exceptions=False)
    return [r[0] for r in results if r and r[0] is not None]


async def check_partial_updates_parallel(urls: List[str], cache: Dict[str, str]) -> Tuple[int, int, Dict[str, Tuple[bytes, str]]]:
    from utils.http import http_session
    await ensure_session()

    async def head_changed(url: str) -> bool:
        meta = await http_head_meta(url)
        if meta is None:
            return True
        prev = _head_cache.get(url, {})
        if not prev:
            _head_cache[url] = meta
            return True
        changed = (
            (meta["etag"] and meta["etag"] != prev.get("etag")) or
            (meta["last_modified"] and meta["last_modified"] != prev.get("last_modified")) or
            (meta["content_length"] and meta["content_length"] != prev.get("content_length"))
        )
        if changed:
            _head_cache[url] = meta
        if not any(meta.values()):
            return True
        return changed

    async def fetch_if_changed(url: str, head_says_changed: bool):
        if not head_says_changed:
            return False, None, None
        try:
            from utils.http import http_session as sess
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    content = await response.read()
                    new_hash = calculate_hash_bytes(content) if content else None
                    if new_hash and (url not in cache or cache.get(url) != new_hash) and not is_placeholder_image(content):
                        return True, content, new_hash
                    else:
                        return False, content, new_hash
        except Exception as e:
            logger.warning(f"Error fetching {url}: {e}")
        return False, None, None

    import aiohttp
    head_results = await asyncio.gather(*[head_changed(u) for u in urls])
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
    logger.info(f"Partial check: {updated_count}/{total_count} images appear updated "
                f"({head_fetched}/{total_count} warranted full fetch)")
    return updated_count, total_count, downloaded_data


async def save_downloaded_images(urls: List[str], downloaded_data: Dict[str, Tuple[bytes, Optional[str]]], cache_file_path: str, cache: Dict[str, str]) -> List[str]:
    files = []
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
                    atomic_json_dump(cache, cache_file_path)
                files.append(cache_path)
                logger.info(f"Saved cached file for {url} -> {cache_path}")
            except Exception as e:
                logger.warning(f"Error writing {cache_path}: {e}")
    return files


async def check_all_urls_exist_parallel(urls: List[str]) -> bool:
    tasks_ = [http_head_ok(u) for u in urls]
    results = await asyncio.gather(*tasks_, return_exceptions=False)
    ok = all(results)
    if not ok:
        logger.warning(f"Some URLs not reachable: {[(u, r) for u, r in zip(urls, results) if not r]}")
    return ok


# ── Load persisted state on import ───────────────────────────────────────────
manual_cache.update(load_json_if_exists(MANUAL_CACHE_FILE))
auto_cache.update(load_json_if_exists(AUTO_CACHE_FILE))
posted_mds.update(load_set_if_exists(MD_CACHE_FILE))
posted_watches.update(load_set_if_exists(WATCH_CACHE_FILE))
