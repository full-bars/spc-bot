# utils/change_detection.py
"""Content-change detection via HEAD headers and content hashing."""

import hashlib
import logging
import os
from typing import Dict

from config import CACHE_DIR

logger = logging.getLogger("spc_bot")

# Per-URL HEAD snapshot from last poll cycle
_head_cache: Dict[str, Dict[str, str]] = {}


def calculate_hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def get_cache_path_for_url(url: str) -> str:
    md5 = hashlib.md5(url.encode()).hexdigest()
    _, ext = os.path.splitext(url)
    ext = ext if ext else ".img"
    filename = f"cached_{md5}{ext}"
    return os.path.join(CACHE_DIR, filename)


# Known SPC placeholder image hashes (add more as discovered)
_KNOWN_PLACEHOLDER_HASHES = set()


def is_placeholder_image(content: bytes) -> bool:
    """
    Detect placeholder / stub images from SPC.

    Checks file size (< 2048 bytes is almost certainly a placeholder) and
    optionally compares against known placeholder hashes.
    """
    if not content:
        return True
    if len(content) < 2048:
        return True
    if _KNOWN_PLACEHOLDER_HASHES:
        h = calculate_hash_bytes(content)
        if h in _KNOWN_PLACEHOLDER_HASHES:
            return True
    return False


async def head_changed(url: str, http_head_meta_fn) -> bool:
    """
    Check if a URL has changed since the last poll using HEAD headers.

    Returns True if the content appears to have changed (or if we can't tell).
    """
    meta = await http_head_meta_fn(url)
    if meta is None:
        return True

    prev = _head_cache.get(url, {})
    if not prev:
        _head_cache[url] = meta
        return True

    changed = (
        (meta["etag"] and meta["etag"] != prev.get("etag"))
        or (meta["last_modified"] and meta["last_modified"] != prev.get("last_modified"))
        or (meta["content_length"] and meta["content_length"] != prev.get("content_length"))
    )
    if changed:
        _head_cache[url] = meta

    # If no useful headers at all, assume changed
    if not any(meta.values()):
        return True

    return changed


def clear_head_cache_for_url(url: str):
    """Remove a URL from the HEAD cache (e.g. after migration)."""
    _head_cache.pop(url, None)
