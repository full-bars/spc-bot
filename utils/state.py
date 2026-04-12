import os

class BotState:
    def __init__(self):
        # Local cache for already posted items
        self.posted_mds = set()
        self.posted_watches = {}
        
        # Failover hierarchy
        self.rank = int(os.getenv("FAILOVER_RANK", "1"))
        # Rank 1 starts as Primary, others start as Standby
        self.is_primary = (self.rank == 1)

    def to_dict(self):
        """Serialize state for Redis synchronization."""
        return {
            "posted_mds": list(self.posted_mds),
            "posted_watches": self.posted_watches
        }

    def from_dict(self, data):
        """Hydrate state from Redis JSON payload."""
        if "posted_mds" in data:
            # Convert list back to set for membership testing
            self.posted_mds = set(data["posted_mds"])
        if "posted_watches" in data:
            self.posted_watches = data["posted_watches"]
