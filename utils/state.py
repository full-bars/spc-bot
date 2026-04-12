# utils/state.py
"""
BotState — single source of truth for all in-memory bot state.
Attached to the bot instance as bot.state at startup.
"""

import os
from datetime import datetime
from typing import Dict, List, Optional, Set

from config import CACHE_DIR


class BotState:
    """Encapsulates all mutable in-memory state for the bot."""

    def __init__(self):
        self.is_primary: bool = True  # overridden by IS_PRIMARY env var in main.py
        # ── Image hash caches ─────────────────────────────────────────────
        self.auto_cache: Dict[str, str] = {}
        self.manual_cache: Dict[str, str] = {}
        self.partial_update_state: Dict[str, Dict] = {}

        # ── SPC watch/MD tracking ─────────────────────────────────────────
        self.posted_mds: Set[str] = set()
        self.posted_watches: Set[str] = set()
        self.active_mds: Set[str] = set()
        self.active_watches: Dict[str, dict] = {}

        # ── Post timing ───────────────────────────────────────────────────
        self.last_post_times: Dict[str, Optional[datetime]] = {
            "day1": None, "day2": None, "day3": None,
            "day48": None, "scp": None, "md": None, "watch": None,
            "csu_day1": None, "csu_day2": None, "csu_day3": None,
            "csu_day4": None, "csu_day5": None, "csu_day6": None,
            "csu_day7": None, "csu_day8": None,
            "csu_panel12": None, "csu_panel38": None,
            "wxnext": None, "sounding": None,
        }
        self.last_posted_urls: Dict[str, List[str]] = {}

    def to_dict(self) -> dict:
        """Serialize state to a JSON-safe dict (for failover endpoint)."""
        return {
            "auto_cache": self.auto_cache,
            "manual_cache": self.manual_cache,
            "posted_mds": list(self.posted_mds),
            "posted_watches": list(self.posted_watches),
            "active_watches": {
                k: {
                    "type": v.get("type"),
                    "expires": v["expires"].isoformat() if v.get("expires") else None,
                    "affected_zones": v.get("affected_zones", []),
                }
                for k, v in self.active_watches.items()
                if isinstance(v, dict)
            },
            "last_posted_urls": self.last_posted_urls,
            "last_post_times": {
                k: v.isoformat() if v else None
                for k, v in self.last_post_times.items()
            },
        }
