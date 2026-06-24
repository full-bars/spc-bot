# utils/change_detection.py
"""Content-change detection via HEAD headers and content hashing."""

import hashlib
import logging
import os

from config import CACHE_DIR

logger = logging.getLogger("spc_bot.change_detection")

# Rust core fallback
try:
    import spc_rust_core

    RUST_AVAILABLE = True
    _validate_batch_rust = spc_rust_core.validate_image_cache_batch
    logger.info("Hashing engine initialized: using Rust hybrid core (XXH3)")
except (ImportError, AttributeError):
    RUST_AVAILABLE = False
    _validate_batch_rust = None
    logger.debug("Rust core not available, using pure-python fallback for hashing")


def calculate_hash_bytes(content: bytes) -> str:
    """Calculate hash for change detection. Prefers Rust XXH3, falls back to Python SHA256."""
    if RUST_AVAILABLE:
        try:
            return spc_rust_core.calculate_fast_hash(content)
        except Exception as e:
            logger.debug(f"Rust fast hash failed: {e}. Falling back to SHA256.")

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
_KNOWN_PLACEHOLDER_HASHES: set[str] = set()


def is_placeholder_image(content: bytes) -> bool:
    """
    Detect placeholder / stub images from SPC, or invalid content (e.g. HTML 404s).

    Checks file size (< 2048 bytes is almost certainly a placeholder),
    GIF truncation (missing 0x3B trailer byte), and optionally compares
    against known placeholder hashes.
    Also verifies magic bytes to ensure the content is actually an image.
    """
    if not content:
        return True
    if len(content) < 2048:
        return True

    # Magic bytes check for common image formats
    is_valid_magic = False
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        is_valid_magic = True
    elif content.startswith(b"GIF87a") or content.startswith(b"GIF89a"):
        is_valid_magic = True
    elif content.startswith(b"\xff\xd8\xff"):
        is_valid_magic = True
    elif content.startswith(b"RIFF") and len(content) > 12 and content[8:12] == b"WEBP":
        is_valid_magic = True
    elif content.startswith(b"BM"):
        is_valid_magic = True
    elif content.lstrip().startswith(b"<svg") or content.lstrip().startswith(b"<?xml"):
        is_valid_magic = True

    if not is_valid_magic:
        logger.warning(
            f"Invalid image magic bytes detected (size={len(content)}). "
            f"First 10 bytes: {content[:10]!r}. Treating as placeholder/invalid."
        )
        return True

    if content[:3] == b"GIF" and len(content) >= 6 and content[-1] != 0x3B:
        logger.warning(
            f"Truncated GIF detected: {len(content)} bytes, missing trailer byte (0x3B), "
            f"treating as placeholder"
        )
        return True
    if _KNOWN_PLACEHOLDER_HASHES:
        h = calculate_hash_bytes(content)
        if h in _KNOWN_PLACEHOLDER_HASHES:
            return True
    return False


def validate_image_cache_batch_py(items: list[tuple[str, bytes]]) -> list[tuple[str, str, bool]]:
    """Batch validate images: compute hash and check for placeholder.

    Returns list of (url, hash_hex, is_placeholder) tuples.
    """
    results = []
    for url, content in items:
        h = calculate_hash_bytes(content)
        is_placeholder = is_placeholder_image(content)
        results.append((url, h, is_placeholder))
    return results


def validate_image_cache_batch(items: list[tuple[str, bytes]]) -> list[tuple[str, str, bool]]:
    """Batch validate images; try Rust first, fall back to Python.

    Returns list of (url, hash_hex, is_placeholder) tuples.
    """
    if _validate_batch_rust:
        try:
            return _validate_batch_rust(items)
        except Exception as e:
            logger.debug(f"Rust batch validation failed: {e}. Falling back to Python.")
    return validate_image_cache_batch_py(items)
