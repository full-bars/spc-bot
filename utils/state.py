import os

class BotState:
    def __init__(self):
        # Fields required by existing integration tests
        self.posted_mds = set()
        self.posted_watches = set()
        self.auto_cache = {}
        self.manual_cache = {}
        self.active_mds = set()
        self.active_watches = {}
        self.last_posted_urls = {}
        self.partial_update_state = {}
        
        # Integration tests expect default keys for outlook tracking
        self.last_post_times = {"day1": None, "day2": None, "day3": None}
        
        # Failover hierarchy configuration
        self.rank = int(os.getenv("FAILOVER_RANK", "1"))
        # Rank 1 (Portland) starts as Primary; others start as Standby
        self.is_primary = (self.rank == 1)

    def to_dict(self):
        """Serialize critical state for Redis synchronization."""
        return {
            "posted_mds": list(self.posted_mds),
            "posted_watches": list(self.posted_watches),
            "auto_cache": self.auto_cache,
            "manual_cache": self.manual_cache,
            "partial_update_state": self.partial_update_state,
            "last_post_times": self.last_post_times
        }

    def from_dict(self, data):
        """Hydrate state from Redis JSON payload."""
        if "posted_mds" in data:
            self.posted_mds = set(data["posted_mds"])
        if "posted_watches" in data:
            self.posted_watches = set(data["posted_watches"])
        if "auto_cache" in data:
            self.auto_cache = data["auto_cache"]
        if "manual_cache" in data:
            self.manual_cache = data["manual_cache"]
        if "partial_update_state" in data:
            self.partial_update_state = data["partial_update_state"]
        if "last_post_times" in data:
            self.last_post_times = data["last_post_times"]
