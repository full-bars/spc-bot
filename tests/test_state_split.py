# tests/test_state_split.py
from utils.state import BotState, PostingLog, TimingTracker, HashStore


def test_substores_are_present():
    s = BotState()
    assert isinstance(s.hashes, HashStore)
    assert isinstance(s.posting, PostingLog)
    assert isinstance(s.timing, TimingTracker)


def test_legacy_attribute_is_same_object_as_substore_field():
    s = BotState()
    # Reading via legacy property returns the container by reference
    assert s.posted_mds is s.posting.posted_mds
    assert s.auto_cache is s.hashes.auto_cache
    assert s.last_post_times is s.timing.last_post_times


def test_legacy_assignment_replaces_substore_field():
    s = BotState()
    new_set = {"0001"}
    s.posted_mds = new_set
    assert s.posting.posted_mds is new_set


def test_substore_mutation_visible_via_legacy_attr():
    s = BotState()
    s.posting.posted_mds.add("9999")
    assert "9999" in s.posted_mds


def test_to_dict_output_shape_unchanged():
    """Failover protocol depends on the exact keys to_dict emits."""
    s = BotState()
    s.iembot_last_seqnum = 7
    s.posted_mds.add("0100")
    s.auto_cache["u"] = "h"

    d = s.to_dict()
    expected_keys = {
        "iembot_last_seqnum",
        "auto_cache",
        "manual_cache",
        "posted_mds",
        "posted_watches",
        "posted_warnings",
        "posted_reports",
        "csu_posted",
        "active_watches",
        "active_warnings",
        "active_mds",
        "last_posted_urls",
        "last_post_times",
        "posted_product_ids",
    }
    assert set(d.keys()) == expected_keys
    assert d["iembot_last_seqnum"] == 7
    assert "0100" in d["posted_mds"]
    assert d["auto_cache"] == {"u": "h"}


def test_substores_are_independent():
    """Separate BotState instances must not share sub-store state."""
    a = BotState()
    b = BotState()
    a.posted_mds.add("X")
    assert "X" not in b.posted_mds
    a.auto_cache["k"] = "v"
    assert "k" not in b.auto_cache
