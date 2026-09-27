"""
Tests for utils/storm_corroboration.py and the tracker's dissipation gate.

The gate exists because a single transient miss on the NHC cyclones page once
produced a public "Storm Dissipated" post for a storm that was still active.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.storm_corroboration import (
    SourceSets,
    fetch_sources,
    ids_from_nesdis,
    ids_from_nhc_rss,
    ids_from_tidbits,
)

# ── source parsers ───────────────────────────────────────────────────────────


def test_ids_from_nhc_rss_reads_storm_ids_from_titles():
    xml = """
    <rss><channel>
      <title>NHC Eastern North Pacific</title>
      <item><title>Summary for Hurricane Polo (EP2/EP172026)</title></item>
      <item><title>Hurricane Polo Public Advisory Number 29</title></item>
      <item><title>Summary for Tropical Storm Rachel (EP3/EP182026)</title></item>
    </channel></rss>
    """
    assert ids_from_nhc_rss(xml) == {"EP172026", "EP182026"}


def test_ids_from_nhc_rss_empty_when_no_storms():
    assert ids_from_nhc_rss("<rss><channel><title>quiet</title></channel></rss>") == set()


def test_ids_from_nesdis_reads_floater_nav_links():
    html = "<li><a title='Hurricane Polo' href='floater.php?stormid=EP172026#navLink'>Polo</a></li>"
    assert ids_from_nesdis(html) == {"EP172026"}


def test_ids_from_tidbits_maps_basin_keys_and_skips_invests():
    payload = json.dumps(
        {
            "06L": "<div/>",
            "17E": "<div/>",
            "01C": "<div/>",
            "91L": "<div/>",  # invest, not a cyclone
            "25W": "<div/>",  # west Pacific, untracked basin
            "not-a-storm": "<div/>",
        }
    )
    assert ids_from_tidbits(payload, year=2026) == {"AL062026", "EP172026", "CP012026"}


# ── verdicts ─────────────────────────────────────────────────────────────────


def test_gone_when_complete_rss_omits_storm_and_nobody_else_lists_it():
    sources = SourceSets(
        rss={"AL": {"AL062026"}, "EP": {"EP162026"}, "CP": set()},
        nesdis=set(),
        tidbits=set(),
    )
    verdict = sources.evaluate("EP172026")
    assert verdict.active is False
    assert verdict.seen_in == ()


def test_active_when_third_party_still_lists_storm_despite_complete_rss():
    sources = SourceSets(
        rss={"AL": set(), "EP": set(), "CP": set()},
        nesdis={"EP172026"},
        tidbits=set(),
    )
    verdict = sources.evaluate("EP172026")
    assert verdict.active is True
    assert verdict.seen_in == ("nesdis",)


def test_active_when_nhc_rss_lists_storm_even_if_third_parties_down():
    sources = SourceSets(rss={"AL": set(), "EP": {"EP172026"}, "CP": set()})
    assert sources.evaluate("EP172026").active is True


def test_inconclusive_when_rss_incomplete_and_no_third_party_lists_it():
    sources = SourceSets(
        rss={"AL": set(), "EP": None, "CP": set()},
        nesdis=set(),
        tidbits=set(),
    )
    verdict = sources.evaluate("EP172026")
    assert verdict.active is None
    assert "EP" in verdict.detail


def test_third_party_rescue_when_rss_incomplete():
    sources = SourceSets(rss={"AL": set(), "EP": None, "CP": set()}, tidbits={"EP172026"})
    verdict = sources.evaluate("EP172026")
    assert verdict.active is True
    assert verdict.seen_in == ("tidbits",)


def test_inconclusive_when_nothing_was_reachable():
    verdict = SourceSets().evaluate("EP172026")
    assert verdict.active is None
    assert verdict.seen_in == ()


# ── fetching ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_sources_marks_a_failed_feed_as_incomplete():
    rss_at = (
        "<rss><item><title>Summary for Tropical Depression Fay (AT1/AL062026)</title></item></rss>"
    )
    rss_cp = "<rss><item><title>Summary for Hurricane Nolo (CP2/EP152026)</title></item></rss>"
    nesdis = "<a href='floater.php?stormid=EP172026'>Polo</a>"
    tidbits = json.dumps({"17E": "<div/>"})

    async def _fake_get_text(url, retries=2, timeout=12):
        if url.endswith("index-at.xml"):
            return rss_at
        if url.endswith("index-ep.xml"):
            return None  # this feed is down
        if url.endswith("index-cp.xml"):
            return rss_cp
        if "star.nesdis" in url:
            return nesdis
        return tidbits

    with patch("utils.storm_corroboration.http_get_text", side_effect=_fake_get_text):
        sources = await fetch_sources(year=2026)

    assert sources.rss == {"AL": {"AL062026"}, "EP": None, "CP": {"EP152026"}}
    assert sources.nhc_rss_complete is False
    # The down feed must not be able to declare a storm gone...
    assert sources.evaluate("EP172026").active is True  # ...but NESDIS still rescues it
    assert sources.evaluate("EP182026").active is None  # nothing else lists it -> inconclusive


@pytest.mark.asyncio
async def test_fetch_sources_agrees_storm_is_gone_when_every_source_misses_it():
    empty_rss = "<rss><channel></channel></rss>"

    async def _fake_get_text(url, retries=2, timeout=12):
        if "star.nesdis" in url or "tropicaltidbits" in url:
            return "{}" if "tropicaltidbits" in url else "<html></html>"
        return empty_rss

    with patch("utils.storm_corroboration.http_get_text", side_effect=_fake_get_text):
        sources = await fetch_sources(year=2026)

    assert sources.nhc_rss_complete is True
    assert sources.evaluate("EP172026").active is False


# ── tracker dissipation gate ─────────────────────────────────────────────────


def _cog_with_misses(misses: dict[str, int] | None = None):
    from cogs import tropical_tracker as tracker

    cog = tracker.TropicalTrackerCog(MagicMock())
    cog._dissipation_misses = dict(misses or {})
    return cog, tracker


@pytest.mark.asyncio
async def test_missing_storm_confirmed_active_by_backup_is_never_announced():
    cog, tracker = _cog_with_misses()
    active: dict = {}
    tracked = {"EP172026"}

    # NHC's cyclones page dropped it, but NESDIS still lists it.
    sources = SourceSets(
        rss={"AL": set(), "EP": set(), "CP": set()},
        nesdis={"EP172026"},
        tidbits=set(),
    )
    with patch.object(tracker, "get_active_storms", AsyncMock(return_value={})), patch.object(
        tracker, "active_storms_authoritative", return_value=True
    ), patch.object(tracker, "fetch_sources", AsyncMock(return_value=sources)):
        for _ in range(3):
            active, verdicts = await cog._assess_missing_storms(active, tracked)
            assert verdicts["EP172026"].active is True
            assert cog._should_announce_dissipation("EP172026", verdicts) is False

    # A corroborated rescue never counts as a miss.
    assert "EP172026" not in cog._dissipation_misses


@pytest.mark.asyncio
async def test_dissipation_needs_corroboration_and_consecutive_misses():
    cog, tracker = _cog_with_misses()
    active: dict = {}
    tracked = {"EP172026"}

    sources = SourceSets(rss={"AL": set(), "EP": set(), "CP": set()}, nesdis=set(), tidbits=set())
    with patch.object(tracker, "get_active_storms", AsyncMock(return_value={})), patch.object(
        tracker, "active_storms_authoritative", return_value=True
    ), patch.object(tracker, "fetch_sources", AsyncMock(return_value=sources)):
        _, verdicts = await cog._assess_missing_storms(active, tracked)
        # First confirmed miss: not enough to announce.
        assert verdicts["EP172026"].active is False
        assert cog._dissipation_misses["EP172026"] == 1
        assert cog._should_announce_dissipation("EP172026", verdicts) is False

        _, verdicts = await cog._assess_missing_storms(active, tracked)
        assert cog._dissipation_misses["EP172026"] == 2
        assert cog._should_announce_dissipation("EP172026", verdicts) is True


@pytest.mark.asyncio
async def test_dissipation_held_when_corroboration_is_inconclusive():
    cog, tracker = _cog_with_misses()
    active: dict = {}
    tracked = {"EP172026"}

    # Every source is down: an unexplained absence must never untrack a storm.
    sources = SourceSets(rss={"AL": None, "EP": None, "CP": None})
    with patch.object(tracker, "get_active_storms", AsyncMock(return_value={})), patch.object(
        tracker, "active_storms_authoritative", return_value=True
    ), patch.object(tracker, "fetch_sources", AsyncMock(return_value=sources)):
        for _ in range(4):
            _, verdicts = await cog._assess_missing_storms(active, tracked)
            assert verdicts["EP172026"].active is None
            assert cog._should_announce_dissipation("EP172026", verdicts) is False

    assert cog._dissipation_misses == {}


@pytest.mark.asyncio
async def test_failed_primary_fetch_holds_tracking_without_corroboration():
    cog, tracker = _cog_with_misses()
    with patch.object(tracker, "get_active_storms", AsyncMock(return_value={})), patch.object(
        tracker, "active_storms_authoritative", return_value=False
    ), patch.object(tracker, "fetch_sources", AsyncMock()) as mock_fetch:
        _, verdicts = await cog._assess_missing_storms({}, {"EP172026"})

    assert verdicts == {}
    mock_fetch.assert_not_awaited()
    assert cog._dissipation_misses == {}
