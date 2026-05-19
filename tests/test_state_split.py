# tests/test_state_split.py
from unittest.mock import AsyncMock, patch

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
        "posted_soundings",
        "sounding_handled_watches",
        }

    assert set(d.keys()) == expected_keys
    assert d["iembot_last_seqnum"] == 7
    assert "0100" in d["posted_mds"]
    assert d["auto_cache"] == {"u": "h"}


async def test_prune_posted_warnings_drops_evicted_entries():
    s = BotState()
    s.posted_warnings["A"] = {"message_id": 1, "channel_id": 1, "area": ""}
    s.posted_warnings["B"] = {"message_id": 2, "channel_id": 1, "area": ""}
    s.posted_warnings["C"] = {"message_id": 3, "channel_id": 1, "area": ""}

    with patch("utils.state_store.prune_posted_warnings", new=AsyncMock()) as prune, \
         patch("utils.state_store.get_all_posted_warnings", new=AsyncMock(return_value={"B": {}, "C": {}})):
        removed = await s.prune_posted_warnings(max_size=2)

    prune.assert_awaited_once_with(2)
    assert removed == 1
    assert set(s.posted_warnings.keys()) == {"B", "C"}


async def test_prune_posted_warnings_preserves_placeholders():
    """Unconfirmed placeholders (empty-dict values) must survive prune —
    a concurrent post may be mid-flight before its row hits SQLite."""
    s = BotState()
    s.posted_warnings["OLD"] = {"message_id": 1, "channel_id": 1, "area": ""}
    s.posted_warnings["INFLIGHT"] = {}  # placeholder mid-post

    with patch("utils.state_store.prune_posted_warnings", new=AsyncMock()), \
         patch("utils.state_store.get_all_posted_warnings", new=AsyncMock(return_value={})):
        await s.prune_posted_warnings(max_size=10)

    assert "INFLIGHT" in s.posted_warnings
    assert "OLD" not in s.posted_warnings


async def test_claim_inserts_placeholder_on_enter():
    s = BotState()
    async with s.claim_posted_warning("KOUN.TO.W.0042"):
        assert "KOUN.TO.W.0042" in s.posted_warnings
        assert s.posted_warnings["KOUN.TO.W.0042"] == {}


async def test_claim_rolls_back_when_not_confirmed():
    s = BotState()
    async with s.claim_posted_warning("KOUN.TO.W.0042"):
        pass  # never confirmed
    assert "KOUN.TO.W.0042" not in s.posted_warnings


async def test_claim_rolls_back_on_exception():
    s = BotState()
    with patch("utils.state_store.add_posted_warning", new=AsyncMock()):
        try:
            async with s.claim_posted_warning("KOUN.TO.W.0042"):
                raise RuntimeError("send failed")
        except RuntimeError:
            pass
    assert "KOUN.TO.W.0042" not in s.posted_warnings


async def test_claim_confirm_persists_and_survives_exit():
    s = BotState()
    with patch("utils.state_store.add_posted_warning", new=AsyncMock()) as persist:
        async with s.claim_posted_warning("KOUN.TO.W.0042") as claim:
            await claim.confirm(message_id=1, channel_id=2, area="Cleveland")
        persist.assert_awaited_once()
    assert s.posted_warnings["KOUN.TO.W.0042"]["message_id"] == 1
    assert s.posted_warnings["KOUN.TO.W.0042"]["area"] == "Cleveland"


async def test_claim_no_ops_when_vtec_already_claimed():
    """Concurrent NWWS + IEM trigger on the same vtec: second claim sees
    the first task's entry, leaves it alone on exit (no clobber)."""
    s = BotState()
    s.posted_warnings["KOUN.TO.W.0042"] = {"message_id": 999, "channel_id": 888}

    async with s.claim_posted_warning("KOUN.TO.W.0042"):
        pass  # never confirmed; should NOT roll back the existing entry

    assert s.posted_warnings["KOUN.TO.W.0042"]["message_id"] == 999


async def test_claim_abort_marks_for_rollback():
    s = BotState()
    async with s.claim_posted_warning("KOUN.TO.W.0042") as claim:
        claim.abort()
    assert "KOUN.TO.W.0042" not in s.posted_warnings


async def test_remove_posted_warning_clears_memory_and_persistence():
    s = BotState()
    s.posted_warnings["KOUN.TO.W.0042"] = {"message_id": 1, "channel_id": 2}

    with patch("utils.state_store.remove_posted_warning", new=AsyncMock()) as remove:
        await s.remove_posted_warning("KOUN.TO.W.0042")

    remove.assert_awaited_once_with("KOUN.TO.W.0042")
    assert "KOUN.TO.W.0042" not in s.posted_warnings


async def test_remove_posted_product_id_silent_when_absent():
    """Rollback path must tolerate the deque already lacking the entry —
    e.g. a maxlen=1000 eviction beat the rollback to it."""
    s = BotState()
    with patch("utils.state_store.remove_posted_product_id", new=AsyncMock()) as remove:
        await s.remove_posted_product_id("PROD-not-here")
    remove.assert_awaited_once_with("PROD-not-here")


def test_update_http_latency_without_host_back_compatible():
    """Pre-R3 callers passed only the latency value; that must still work."""
    s = BotState()
    s.update_http_latency(0.250)
    assert s.http_latency == 0.250
    assert s.http_latency_by_host == {}


def test_update_http_latency_with_host_buckets_samples():
    s = BotState()
    s.update_http_latency(0.100, host="api.weather.gov")
    s.update_http_latency(0.200, host="api.weather.gov")
    s.update_http_latency(0.500, host="mesonet.agron.iastate.edu")
    assert list(s.http_latency_by_host["api.weather.gov"]) == [0.100, 0.200]
    assert list(s.http_latency_by_host["mesonet.agron.iastate.edu"]) == [0.500]


def test_http_latency_per_host_rolling_window_caps_growth():
    s = BotState()
    for i in range(s.HTTP_LATENCY_WINDOW + 50):
        s.update_http_latency(float(i) / 1000, host="api.weather.gov")
    samples = s.http_latency_by_host["api.weather.gov"]
    assert len(samples) == s.HTTP_LATENCY_WINDOW
    # Oldest samples evicted — first kept should be sample #50
    assert samples[0] == 50.0 / 1000


def test_http_latency_percentiles_returns_none_for_unknown_host():
    s = BotState()
    assert s.http_latency_percentiles("nowhere.invalid") is None


def test_http_latency_percentiles_computes_p50_p95():
    s = BotState()
    # 100 samples ranging 0.001..0.100s
    for i in range(1, 101):
        s.update_http_latency(i / 1000, host="api.weather.gov")
    p50, p95 = s.http_latency_percentiles("api.weather.gov")
    # P50 of 1..100 ≈ 50ms; P95 ≈ 95ms (nearest-rank)
    assert 0.045 <= p50 <= 0.055
    assert 0.090 <= p95 <= 0.100


def test_substores_are_independent():
    """Separate BotState instances must not share sub-store state."""
    a = BotState()
    b = BotState()
    a.posted_mds.add("X")
    assert "X" not in b.posted_mds
    a.auto_cache["k"] = "v"
    assert "k" not in b.auto_cache
