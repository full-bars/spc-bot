"""Cache management utilities.

Handles TTL-based eviction of cached files to prevent disk space exhaustion.
"""
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("spc_bot")

DEFAULT_CACHE_DIR = "cache"
DEFAULT_TTL_SECONDS = 7 * 24 * 3600  # 7 days


async def cleanup_old_cache_files(
    cache_dir: str = DEFAULT_CACHE_DIR,
    max_age_seconds: int = DEFAULT_TTL_SECONDS,
) -> tuple[int, int]:
    """Remove cached files older than max_age_seconds.

    Returns (files_deleted, bytes_freed).
    """
    if not os.path.isdir(cache_dir):
        logger.debug(f"[CACHE] Cache directory not found: {cache_dir}")
        return 0, 0

    now = time.time()
    deleted = 0
    freed_bytes = 0

    try:
        for entry in os.scandir(cache_dir):
            if not entry.is_file(follow_symlinks=False):
                continue

            age_seconds = now - entry.stat().st_mtime
            if age_seconds > max_age_seconds:
                try:
                    size = entry.stat().st_size
                    os.remove(entry.path)
                    deleted += 1
                    freed_bytes += size
                    logger.debug(f"[CACHE] Evicted {entry.name} ({size} bytes, age: {age_seconds / 3600:.1f}h)")
                except Exception as e:
                    logger.warning(f"[CACHE] Failed to delete {entry.path}: {e}")

    except Exception as e:
        logger.warning(f"[CACHE] Error scanning cache directory: {e}")

    if deleted > 0:
        logger.info(f"[CACHE] Evicted {deleted} file(s), freed {freed_bytes / (1024*1024):.1f} MB")

    return deleted, freed_bytes


def get_cache_size(cache_dir: str = DEFAULT_CACHE_DIR) -> int:
    """Calculate total size of cache directory in bytes."""
    if not os.path.isdir(cache_dir):
        return 0

    total_size = 0
    try:
        for entry in os.scandir(cache_dir):
            if entry.is_file(follow_symlinks=False):
                total_size += entry.stat().st_size
    except Exception as e:
        logger.warning(f"[CACHE] Error calculating cache size: {e}")

    return total_size
