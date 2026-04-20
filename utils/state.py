# utils/state.py
"""
BotState — in-memory state for the bot, attached to the bot instance
as `bot.state` at startup.

Composition
-----------
BotState is a thin coordinator over three focused sub-stores:

  HashStore     — image-hash caches used for content-change detection.
  PostingLog    — which MDs / watches / CSU days we've already posted,
                  plus the currently-active set (as returned by the NWS
                  feed).
  TimingTracker — when each category was last posted, and the URLs we
                  published for each outlook day.

BotState still exposes the legacy attribute names (`posted_mds`,
`auto_cache`, `last_post_times`, …) as properties so every existing
call site keeps working unchanged. The sub-store references are also
exposed directly (`state.hashes`, `state.posting`, `state.timing`) so
new code can take an explicit dependency on the component it needs
rather than on the whole coordinator.
"""

from datetime import datetime
from typing import Dict, List, Optional, Set


class HashStore:
    """Image-hash caches and partial-update state."""

    __slots__ = ("auto_cache", "manual_cache", "partial_update_state")

    def __init__(self):
        self.auto_cache: Dict[str, str] = {}
        self.manual_cache: Dict[str, str] = {}
        self.partial_update_state: Dict[str, Dict] = {}


class PostingLog:
    """Deduplication log for SPC posts and the currently-active alerts."""

    __slots__ = (
        "posted_mds",
        "posted_watches",
        "csu_posted",
        "active_mds",
        "active_watches",
    )

    def __init__(self):
        self.posted_mds: Set[str] = set()
        self.posted_watches: Set[str] = set()
        self.csu_posted: Set[str] = set()
        self.active_mds: Set[str] = set()
        self.active_watches: Dict[str, dict] = {}


class TimingTracker:
    """Per-category last-posted timestamps and URL payloads."""

    __slots__ = ("last_post_times", "last_posted_urls")

    def __init__(self):
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


def _delegate(store_attr: str, field: str) -> property:
    """Build a read/write property that forwards to a sub-store field.

    Reading returns the underlying container by reference, so existing
    callers doing `state.posted_mds.add(x)` keep mutating the same set
    the sub-store owns. Writing replaces the field on the sub-store.
    """

    def _get(self):
        return getattr(getattr(self, store_attr), field)

    def _set(self, value):
        setattr(getattr(self, store_attr), field, value)

    return property(_get, _set)


class BotState:
    """Top-level mutable state for the bot.

    Scalar flags (`is_primary`, `iembot_last_seqnum`) live here directly
    because they don't belong to any sub-store. Everything else is
    delegated to `hashes`, `posting`, or `timing`.
    """

    def __init__(self):
        self.is_primary: bool = True  # overridden by IS_PRIMARY env var in main.py
        self.iembot_last_seqnum: int = 0

        self.hashes = HashStore()
        self.posting = PostingLog()
        self.timing = TimingTracker()

    # ── Legacy attribute surface (delegated) ────────────────────────────────
    auto_cache = _delegate("hashes", "auto_cache")
    manual_cache = _delegate("hashes", "manual_cache")
    partial_update_state = _delegate("hashes", "partial_update_state")

    posted_mds = _delegate("posting", "posted_mds")
    posted_watches = _delegate("posting", "posted_watches")
    csu_posted = _delegate("posting", "csu_posted")
    active_mds = _delegate("posting", "active_mds")
    active_watches = _delegate("posting", "active_watches")

    last_post_times = _delegate("timing", "last_post_times")
    last_posted_urls = _delegate("timing", "last_posted_urls")

    # ── Serialization ───────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        """Serialize state to a JSON-safe dict (for the failover /state
        endpoint). Shape is stable — the failover protocol depends on it."""
        return {
            "iembot_last_seqnum": self.iembot_last_seqnum,
            "auto_cache": self.auto_cache,
            "manual_cache": self.manual_cache,
            "posted_mds": list(self.posted_mds),
            "posted_watches": list(self.posted_watches),
            "csu_posted": list(self.csu_posted),
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
