# utils/persistence.py
"""Atomic JSON persistence helpers."""

import json
import logging
import os
import tempfile
from typing import Dict, Set

logger = logging.getLogger("spc_bot")


def atomic_json_dump(data, filepath: str):
    """Write JSON atomically via temp-file + rename."""
    dirname = os.path.dirname(filepath) or "."
    os.makedirs(dirname, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, dir=dirname, suffix=".tmp"
        ) as tmp_file:
            tmp_path = tmp_file.name
            json.dump(data, tmp_file, indent=2)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, filepath)
    except Exception as e:
        logger.warning(f"atomic_json_dump replace failed, trying fallback: {e}")
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)


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
