# utils/change_detection.py
"""Content-change detection via HEAD headers and content hashing."""

import hashlib
import logging
import os

from config import CACHE_DIR

logger = logging.getLogger("spc_bot")


def calculate_hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


# Whitelist of extensions we actually serve. Anything else collapses
# to ".img" — protects against query-string junk or path separators
# sneaking into the filename via os.path.splitext on a raw URL.
_ALLOWED_EXTS = {".gif", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".bmp"}


def get_cache_path_for_url(url: str) -> str:
    # Strip query / fragment before extracting an extension; splitext on
    # "x.gif?param=.." would otherwise return ".gif?param=..".
    clean = url.split("?", 1)[0].split("#", 1)[0]
    md5 = hashlib.md5(url.encode()).hexdigest()
    _, ext = os.path.splitext(clean)
    ext = ext.lower() if ext else ""
    if ext not in _ALLOWED_EXTS:
        ext = ".img"
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


