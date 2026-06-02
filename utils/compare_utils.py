# utils/compare_utils.py
"""Versioning and comparison logic for outlook products."""

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple

from config import CACHE_DIR
from utils.change_detection import calculate_hash_bytes

logger = logging.getLogger("spc_bot.compare_utils")

OUTLOOK_VERSIONS_DIR = os.path.join(CACHE_DIR, "outlook_versions")


def _ensure_versions_dir() -> None:
    """Create outlook versions directory if it doesn't exist."""
    os.makedirs(OUTLOOK_VERSIONS_DIR, exist_ok=True)


def _get_product_key(day: int, product: str) -> str:
    """Construct a unique key for a product (e.g., 'day1_categorical')."""
    return f"day{day}_{product}"


def _get_version_filename(product_key: str, timestamp: datetime) -> str:
    """Generate a timestamped version filename with microsecond precision."""
    ts = timestamp.strftime("%Y%m%d_%H%M%S")
    us = timestamp.microsecond
    return f"{product_key}_{ts}_{us:06d}.png"


async def _write_file_async(path: str, data: bytes) -> None:
    """Off-loop file write."""
    loop = asyncio.get_running_loop()

    def _do():
        with open(path, "wb") as f:
            f.write(data)

    await loop.run_in_executor(None, _do)


async def archive_outlook_version(
    day: int, product: str, image_data: bytes, current_time: Optional[datetime] = None
) -> Optional[str]:
    """
    Archive an outlook image if it differs from the previous version.
    Returns the archive path on success, None if no archive needed (same as previous).
    """
    if not image_data:
        return None

    _ensure_versions_dir()
    current_time = current_time or datetime.now(timezone.utc)
    product_key = _get_product_key(day, product)
    new_hash = calculate_hash_bytes(image_data)

    # Check if this differs from the previous version
    existing = _get_all_versions_for_product(product_key)
    if existing:
        latest_path = existing[0]  # Most recent is first
        try:
            with open(latest_path, "rb") as f:
                latest_data = f.read()
            latest_hash = calculate_hash_bytes(latest_data)
            if latest_hash == new_hash:
                logger.debug(f"Outlook {product_key} unchanged (hash match) — not archiving")
                return None
        except Exception as e:
            logger.warning(f"Failed to compare hashes for {product_key}: {e}")

    # New version — archive it
    filename = _get_version_filename(product_key, current_time)
    filepath = os.path.join(OUTLOOK_VERSIONS_DIR, filename)
    try:
        await _write_file_async(filepath, image_data)
        logger.info(f"Archived {product_key} version: {filename}")
        return filepath
    except Exception as e:
        logger.warning(f"Failed to archive {product_key}: {e}")
        return None


def _get_all_versions_for_product(product_key: str) -> list[str]:
    """Get all versions of a product, sorted newest-first."""
    _ensure_versions_dir()
    pattern = f"{product_key}_*.png"
    files = sorted(
        Path(OUTLOOK_VERSIONS_DIR).glob(pattern),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    return [str(f) for f in files]


async def get_comparison_pair(day: int, product: str) -> Tuple[Optional[str], Optional[str], str]:
    """
    Get the latest and previous versions of an outlook product.
    Returns (latest_path, previous_path, status_msg).
    - If only one version exists: (latest, None, "Only latest available")
    - If no versions: (None, None, "No archived versions found")
    """
    versions = _get_all_versions_for_product(_get_product_key(day, product))

    if not versions:
        return None, None, "No archived versions found for this product"

    latest = versions[0]
    if len(versions) < 2:
        return latest, None, "Only latest version available (no prior to compare)"

    previous = versions[1]
    return latest, previous, "Comparison ready"


async def cleanup_old_versions(max_age_hours: int = 24) -> int:
    """
    Delete versions older than max_age_hours. Returns count deleted.
    Should be called periodically (e.g., with periodic_cleanup task).
    """
    _ensure_versions_dir()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    deleted_count = 0

    try:
        for fpath in Path(OUTLOOK_VERSIONS_DIR).glob("*.png"):
            mtime = datetime.fromtimestamp(fpath.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                fpath.unlink()
                deleted_count += 1
                logger.debug(f"Deleted old version: {fpath.name}")
    except Exception as e:
        logger.warning(f"Error cleaning up old versions: {e}")

    if deleted_count > 0:
        logger.info(f"Cleaned up {deleted_count} old outlook versions")
    return deleted_count
