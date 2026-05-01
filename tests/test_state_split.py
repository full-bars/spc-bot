"""Tests for the BotState sub-store split.

The split's load-bearing property is that the legacy attribute surface
(`state.posted_mds`, `state.auto_cache`, …) keeps working AND returns
the same underlying container the sub-store owns, so existing
in-place mutations (`state.posted_mds.add(x)`, dict updates) still
reach the intended storage.
"""

from utils.state import BotState, HashStore, PostingLog, TimingTracker


def test_substores_are_present():
    s = BotState()
    assert isinstance(s.hashes, HashStore)
    assert isinstance(s.posting, PostingLog)
    assert isinstance(s.timing, TimingTracker)


def test_legacy_attribute_is_same_object_as_substore_field():
    """Mutating via the legacy attribute must reach the sub-store."""
    s = BotState()
    s.posted_mds.add("0100")
    assert "0100" in s.posting.posted_mds

    s.auto_cache["url"] = "hash"
    assert s.hashes.auto_cache["url"] == "hash"

    s.last_posted_urls["day1"] = ["u"]
    assert s.timing.last_posted_urls["day1"] == ["u"]


def test_legacy_assignment_replaces_substore_field():
    """`state.posted_mds = new_set` must update the sub-store too."""
    s = BotState()
    new_set = {"A", "B"}
    s.posted_mds = new_set
    # The delegated setter replaced the field on the sub-store.
    assert s.posting.posted_mds is new_set


def test_substore_mutation_visible_via_legacy_attr():
    """The reverse direction — mutate sub-store, read via legacy attr."""
    s = BotState()
    s.posting.posted_watches.add("0042")
    assert "0042" in s.posted_watches


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
