"""Landfall detection, product-driven tracker updates, and announcements."""

import contextlib
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from cogs import tropical_tracker as tracker
from cogs.tropical import TropicalCog
from utils.nhc_landfall import detect_landfall
from utils.nhc_storms import (
    extract_storm_id,
    parse_header_advisory,
    parse_header_issuance,
)

# Real NHC product: Hurricane Isaias 830 PM CDT Position Update (0130 UTC),
# the product that announced the actual landfall.
LANDFALL_TCU = """158
WTNT64 KNHC 100130
TCUAT4

Hurricane Isaias Tropical Cyclone Update
NWS National Hurricane Center Miami FL       AL092026
830 PM CDT Fri Oct 09 2026

...ISAIAS MAKES LANDFALL NEAR DESTIN FLORIDA...
...LIFE-THREATENING STORM SURGE IMPACTING THE GULF COAST AS
DANGEROUS HURRICANE CONDITIONS AND FLASH FLOODING SPREAD INLAND...

NWS Doppler Radar data indicate that Hurricane Isaias made landfall
near Destin, Florida, around 830 PM CDT (0130 UTC) with maximum
sustained winds of 105 mph (165 km/h). The estimated minimum central
pressure is 971 mb (28.67 inches).

A wind gust to 116 mph (187 km/h) was recently reported at a
WeatherFlow station near Santa Rosa Sound, Florida.


SUMMARY OF 830 PM CDT...0130 UTC...INFORMATION
----------------------------------------------
LOCATION...30.4N 86.5W
ABOUT 40 MI...65 KM E OF PENSACOLA FLORIDA
MAXIMUM SUSTAINED WINDS...105 MPH...165 KM/H
PRESENT MOVEMENT...N OR 10 DEGREES AT 19 MPH...31 KM/H
MINIMUM CENTRAL PRESSURE...971 MB...28.67 INCHES

$$
Forecaster Mahoney/Camposano/Reinhart/Cangialosi
"""

# Real advisory text written hours *before* landfall.
PRE_LANDFALL_TCP = """WTNT34 KNHC 100245
TCPAT4

BULLETIN
Hurricane Isaias Advisory Number  14
NWS National Hurricane Center Miami FL       AL092026
1000 PM CDT Fri Oct 09 2026

...ISAIAS NEARING LANDFALL IN THE WESTERN FLORIDA PANHANDLE...

DISCUSSION AND OUTLOOK
----------------------
On the forecast track, Isaias is expected to move across Alabama and
into the Tennessee Valley. Interests along the coast should monitor
later products for updates on whether Isaias will make landfall
tonight.

STORM SURGE: The combination of a dangerous storm surge and the
tide will cause normally dry areas near the coast to be flooded by
rising waters moving inland from the shoreline.

A Storm Surge Warning means there is a danger of life-threatening
inundation, from rising water moving inland from the coastline.
"""


@contextlib.contextmanager
def _stack(*contexts):
    """Enter several patchers/context managers with py38-legal syntax."""
    with contextlib.ExitStack() as stack:
        for ctx in contexts:
            stack.enter_context(ctx)
        yield


# ── detect_landfall ───────────────────────────────────────────────────────────


def test_detect_landfall_finds_headline_sentence_and_place():
    match = detect_landfall(LANDFALL_TCU)

    assert match is not None
    assert match.headline == "ISAIAS MAKES LANDFALL NEAR DESTIN FLORIDA"
    assert "made landfall" in match.sentence
    assert "105 mph" in match.sentence
    assert match.place == "Destin, Florida"


@pytest.mark.parametrize(
    "text",
    [
        "",
        PRE_LANDFALL_TCP,
        "...ISAIAS NEARING LANDFALL IN THE WESTERN FLORIDA PANHANDLE...",
        "Isaias is expected to make landfall tonight.",
        "The center is forecast to cross the coastline early Saturday.",
        "Isaias could come ashore near Tampa.",
        "Isaias is expected to have made landfall by 10 PM CDT.",
        "Residents should move inland from the coastline before the storm.",
        "Water will cause normally dry areas near the coast to be flooded by\n"
        "rising waters moving inland from the shoreline.",
    ],
)
def test_detect_landfall_rejects_non_landfall_text(text):
    assert detect_landfall(text) is None


# Real Hurricane Simon TCD (202610102100-KNHC-WTPZ45-TCDEP5) that produced a
# false landfall announcement: model guidance "making landfall ... in 24-36 h".
SIMON_FORECAST_DISCUSSION = (
    "One notable change from this morning, now all the hurricane-regional "
    "model runs (HWRF, HMON, HAFS-A, HAFS-B) all show Simon making landfall "
    "in Jalisco in 24-36 h."
)


@pytest.mark.parametrize(
    "text",
    [
        SIMON_FORECAST_DISCUSSION,
        "Guidance shows Simon making landfall in 12 hr.",
        "The runs bring Simon making landfall within 12 hours.",
        "Models trend Simon making landfall over the next 24 h.",
        "Ensemble mean has Simon making landfall in the next 3 days.",
    ],
)
def test_detect_landfall_rejects_forecast_lead_times(text):
    """A lead time after the phrase marks a forecast, not a completed landfall."""
    assert detect_landfall(text) is None


def test_detect_landfall_still_accepts_clock_time_landfall():
    """A real landfall is dated with a clock time, never a lead time."""
    text = (
        "NWS Doppler Radar data indicate that Hurricane Isaias made landfall "
        "near Destin, Florida, around 830 PM CDT (0130 UTC) with maximum "
        "sustained winds of 105 mph (165 km/h)."
    )
    match = detect_landfall(text)
    assert match is not None
    assert match.place == "Destin, Florida"


def test_detect_landfall_headline_only_product_still_matches():
    """A product carrying only the all-caps banner must still announce."""
    text = """WTNT64 KNHC 100130
TCUAT4

Tropical Cyclone Update
NWS National Hurricane Center Miami FL       AL092026
830 PM CDT Fri Oct 09 2026

...ISAIAS MAKES LANDFALL NEAR DESTIN FLORIDA...
"""
    match = detect_landfall(text)
    assert match is not None
    assert match.sentence == "ISAIAS MAKES LANDFALL NEAR DESTIN FLORIDA"
    assert match.place == "Destin Florida"


def test_detect_landfall_hedged_past_tense_is_rejected():
    assert detect_landfall("Models suggest the center may have crossed the coastline.") is None


@pytest.mark.asyncio
async def test_announce_landfall_builds_fresh_file_per_channel():
    """discord.File is single-use; reusing one uploads 0 bytes after the first.

    Regression guard: the cone BytesIO must be re-wrapped for every channel so
    the second and later sends are not empty attachments.
    """
    cog, bot = _make_cog()
    first, second = _channel(999), _channel(777)
    bot.get_channel = MagicMock(side_effect=lambda cid: {999: first}.get(cid))
    state = _State()
    sent = []
    match = detect_landfall(LANDFALL_TCU)
    assert match is not None
    cone_payload = b"\x89PNG\r\n\x1a\n" + b"cone-data" * 20

    async def _safe_send(channel, *, context, embed=None, files=None, **kwargs):
        # Simulate discord.py draining the stream during the request.
        drained = [f.fp.read() for f in (files or [])]
        sent.append({"channel": channel, "files": files, "drained": drained})
        return MagicMock()

    with _stack(
        patch.object(
            tracker, "get_all_tracked_channels", AsyncMock(return_value={999: ["AL092026"]})
        ),
        patch.object(tracker, "get_active_storms", AsyncMock(return_value={})),
        patch.object(tracker, "_download_cone_image", AsyncMock(return_value=cone_payload)),
        patch.object(tracker, "safe_send", side_effect=_safe_send),
        _state_stack(state),
    ):
        await cog.announce_landfall(
            storm_id="AL092026",
            product_id="202610100130-KNHC-WTNT64-TCUAT4",
            match=match,
            parsed=_parsed(),
            extra_channels=(second,),
        )

    assert len(sent) == 2
    # Distinct File objects wrapping distinct streams, both non-empty.
    assert sent[0]["files"][0] is not sent[1]["files"][0]
    assert sent[0]["files"][0].fp is not sent[1]["files"][0].fp
    assert sent[0]["drained"] == [cone_payload]
    assert sent[1]["drained"] == [cone_payload]


# ── reset_landfall_announcements_once ────────────────────────────────────────


@pytest.mark.asyncio
async def test_reset_landfall_announcements_once_clears_all_keys():
    state = _State(
        {
            tracker._landfall_key(999, "AL092026"): "pid-1",
            tracker._landfall_key(777, "EP202026"): "pid-2",
            "tracked_storms:channel:999": "must-survive",
        }
    )

    with _state_stack(state):
        cleared = await tracker.reset_landfall_announcements_once()

    assert cleared == 2
    assert await state.get(tracker._landfall_key(999, "AL092026")) is None
    assert await state.get(tracker._landfall_key(777, "EP202026")) is None
    # Only the landfall namespace is touched.
    assert await state.get("tracked_storms:channel:999") == "must-survive"
    assert await state.get(tracker._LANDFALL_ANNOUNCED_RESET_KEY) == "1"


@pytest.mark.asyncio
async def test_reset_landfall_announcements_once_is_idempotent():
    state = _State({tracker._landfall_key(999, "AL092026"): "pid-1"})

    with _state_stack(state):
        assert await tracker.reset_landfall_announcements_once() == 1
        # Re-arm the key the sweep just cleared, then run again: the marker
        # short-circuits, so a key written after the sweep survives.
        await state.set(tracker._landfall_key(999, "AL092026"), "pid-2")
        assert await tracker.reset_landfall_announcements_once() == 0
        assert await state.get(tracker._landfall_key(999, "AL092026")) == "pid-2"


# ── product header parsers ───────────────────────────────────────────────────


def test_extract_storm_id_reads_header_line():
    assert extract_storm_id(LANDFALL_TCU) == "AL092026"


def test_parse_header_advisory_and_issuance():
    assert parse_header_advisory(PRE_LANDFALL_TCP) == "14"
    assert parse_header_issuance(PRE_LANDFALL_TCP) == "1000 PM CDT Fri Oct 09 2026"
    # Hourly position updates carry no advisory number of their own.
    assert parse_header_advisory(LANDFALL_TCU) is None
    assert parse_header_issuance(LANDFALL_TCU) == "830 PM CDT Fri Oct 09 2026"


# ── update_fingerprint ───────────────────────────────────────────────────────


def test_fingerprint_treats_page_and_product_issuance_as_equal():
    page = {
        "position": "30.4N 86.5W",
        "winds_mph": 105.0,
        "pressure": 971,
        "issuance": "As of 830 PM CDT Fri Oct 09",
        "advisory": "13A",
    }
    product = {
        "position": "30.4N 86.5W",
        "winds_mph": 105.0,
        "pressure": 971,
        "issuance": "830 PM CDT Fri Oct 09 2026",
        "advisory": "",
    }
    assert tracker.update_fingerprint(page) == tracker.update_fingerprint(product)


def test_fingerprint_changes_with_new_advisory_state():
    base = {"position": "30.4N 86.5W", "winds_mph": 105.0, "pressure": 971, "issuance": "830 PM"}
    moved = dict(base, position="30.5N 86.4W")
    weakened = dict(base, winds_mph=85.0)
    assert len({tracker.update_fingerprint(x) for x in (base, moved, weakened)}) == 3


# ── tracker state helpers ────────────────────────────────────────────────────


class _State:
    def __init__(self, records=None):
        self.data = dict(records or {})

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value):
        self.data[key] = value

    async def delete(self, key):
        self.data.pop(key, None)

    async def keys(self, prefix):
        # Matches state_store.list_state_keys: keys are returned without it.
        return [k[len(prefix) :] for k in self.data if k.startswith(prefix)]


def _state_stack(state):
    return _stack(
        patch.object(tracker, "get_state", side_effect=state.get),
        patch.object(tracker, "set_state", side_effect=state.set),
        patch.object(tracker, "list_state_keys", side_effect=state.keys),
        patch.object(tracker, "delete_state", side_effect=state.delete),
    )


@pytest.mark.asyncio
async def test_record_post_preserves_sat_product_and_previous_fields():
    state = _State({tracker._state_key(999, "AL092026"): json.dumps({"sat_product": "AirMass"})})

    with _state_stack(state):
        await tracker._record_post(
            999, "AL092026", advisory="14", fingerprint="fp1", product_id="pid-1"
        )
        records = await tracker.get_tracked_storms(999)

    (record,) = records
    assert record["sat_product"] == "AirMass"
    assert record["last_etn"] == "14"
    assert record["fingerprint"] == "fp1"
    assert record["last_product_id"] == "pid-1"
    assert record["last_post_at"] is not None


@pytest.mark.asyncio
async def test_get_tracked_storms_reads_legacy_bare_string_record():
    state = _State({tracker._state_key(999, "AL092026"): "13"})
    with _state_stack(state):
        (record,) = await tracker.get_tracked_storms(999)
    assert record["last_etn"] == "13"
    assert record["sat_product"] == tracker.DEFAULT_SATELLITE_PRODUCT


def _make_cog():
    bot = MagicMock()
    bot.state.is_primary = True
    bot.wait_until_ready = AsyncMock()
    return tracker.TropicalTrackerCog(bot), bot


def _channel(channel_id):
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    return channel


@contextlib.contextmanager
def _post_harness(sent):
    async def _send(channel, *, context, embed, cone_bytes, sat_bytes, storm_id, view=None):
        sent.append({"channel": channel, "embed": embed, "context": context})
        return MagicMock()

    with _stack(
        patch.object(tracker, "_send_tracker_update", side_effect=_send),
        patch.object(tracker, "_download_cone_image", AsyncMock(return_value=None)),
        patch.object(tracker, "_download_satellite_image", AsyncMock(return_value=None)),
    ):
        yield


INFO = {
    "name": "Isaias",
    "type": "Hurricane",
    "position": "30.4N 86.5W",
    "winds_mph": 105.0,
    "pressure": 971,
    "issuance": "830 PM CDT Fri Oct 09",
    "graphics_url": "https://example.invalid/cone.png",
}


@pytest.mark.asyncio
async def test_post_storm_update_posts_once_per_advisory_state():
    """The page scrape must not re-post what the product stream already sent."""
    cog, _ = _make_cog()
    state = _State()
    sent = []
    channel = _channel(999)

    with _state_stack(state), _post_harness(sent):
        # Product feed delivers the advisory first…
        await cog._post_storm_update(channel, "AL092026", dict(INFO), 999, product_id="pid-1")
        # …then the slower page scrape describes the very same state.
        await cog._post_storm_update(channel, "AL092026", dict(INFO), 999)
        # …and a duplicate delivery of the same product is ignored.
        await cog._post_storm_update(channel, "AL092026", dict(INFO), 999, product_id="pid-2")

    assert len(sent) == 1


@pytest.mark.asyncio
async def test_post_storm_update_page_feed_posts_after_gate_opens():
    cog, _ = _make_cog()
    state = _State()
    sent = []
    channel = _channel(999)
    stale = datetime.now(timezone.utc) - timedelta(seconds=tracker.PAGE_UPDATE_GATE_SECONDS + 5)

    with _state_stack(state), _post_harness(sent):
        await tracker._record_post(
            999, "AL092026", advisory="14", fingerprint="old", product_id="p"
        )
        raw = json.loads(state.data[tracker._state_key(999, "AL092026")])
        raw["last_post_at"] = stale.isoformat()
        state.data[tracker._state_key(999, "AL092026")] = json.dumps(raw)

        await cog._post_storm_update(channel, "AL092026", dict(INFO), 999)

    assert len(sent) == 1


@pytest.mark.asyncio
async def test_post_storm_update_holds_page_feed_during_gate():
    cog, _ = _make_cog()
    state = _State()
    sent = []
    channel = _channel(999)

    with _state_stack(state), _post_harness(sent):
        await tracker._record_post(
            999, "AL092026", advisory="13", fingerprint="previous", product_id="p-old"
        )
        await cog._post_storm_update(channel, "AL092026", dict(INFO), 999)

    assert sent == []


# ── on_nhc_product fan-out ───────────────────────────────────────────────────


def _parsed(text=LANDFALL_TCU):
    return {"raw_text": text, "summary": None, "storm_type": "HURRICANE", "storm_name": "Isaias"}


@pytest.mark.asyncio
async def test_on_nhc_product_posts_to_tracking_channels_only():
    cog, bot = _make_cog()
    tracking, bystander = _channel(999), _channel(888)
    bot.get_channel = MagicMock(side_effect=lambda cid: {999: tracking, 888: bystander}.get(cid))
    sent = []

    with _stack(
        patch.object(
            tracker,
            "get_all_tracked_channels",
            AsyncMock(return_value={999: ["AL092026"], 888: []}),
        ),
        patch.object(tracker, "get_tracked_storms", AsyncMock(return_value=[])),
        patch.object(tracker, "get_active_storms", AsyncMock(return_value={})),
        patch.object(tracker, "get_state", AsyncMock(return_value=None)),
        patch.object(tracker, "set_state", AsyncMock()),
        _post_harness(sent),
    ):
        await cog.on_nhc_product("AL092026", "202610100130-KNHC-WTNT64-TCUAT4", _parsed())

    assert [s["channel"] for s in sent] == [tracking]
    assert sent[0]["embed"].footer.text == "Advisory 830 PM CDT Fri Oct 09 2026 • NHC"


@pytest.mark.asyncio
async def test_on_nhc_product_rejects_non_primary():
    cog, bot = _make_cog()
    bot.state.is_primary = False
    with patch.object(tracker, "get_all_tracked_channels", AsyncMock(return_value={})) as mock:
        await cog.on_nhc_product("AL092026", "pid", _parsed())
    mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_nhc_product_ignores_malformed_storm_id():
    cog, _ = _make_cog()
    with patch.object(tracker, "get_all_tracked_channels", AsyncMock(return_value={})) as mock:
        await cog.on_nhc_product("junk", "pid", _parsed())
    mock.assert_not_awaited()


# ── announce_landfall ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_announce_landfall_posts_once_per_channel_with_red_embed():
    cog, bot = _make_cog()
    tracking, extra = _channel(999), _channel(777)
    bot.get_channel = MagicMock(side_effect=lambda cid: {999: tracking}.get(cid))
    state = _State()
    sent = []
    match = detect_landfall(LANDFALL_TCU)
    assert match is not None

    async def _safe_send(channel, *, context, embed=None, files=None, **kwargs):
        sent.append({"channel": channel, "embed": embed, "files": files, "context": context})
        return MagicMock()

    with _stack(
        patch.object(
            tracker, "get_all_tracked_channels", AsyncMock(return_value={999: ["AL092026"]})
        ),
        patch.object(tracker, "get_active_storms", AsyncMock(return_value={})),
        patch.object(tracker, "_download_cone_image", AsyncMock(return_value=None)),
        patch.object(tracker, "safe_send", side_effect=_safe_send),
        _state_stack(state),
    ):
        await cog.announce_landfall(
            storm_id="AL092026",
            product_id="202610100130-KNHC-WTNT64-TCUAT4",
            match=match,
            parsed=_parsed(),
            extra_channels=(extra,),
        )
        # A later product repeating the landfall statement must not re-post.
        await cog.announce_landfall(
            storm_id="AL092026",
            product_id="202610100300-KNHC-WTNT34-TCPAT4",
            match=match,
            parsed=_parsed(),
            extra_channels=(extra,),
        )

    assert [s["channel"] for s in sent] == [extra, tracking]
    embed = sent[0]["embed"]
    assert embed.title == "🚨 LANDFALL — Isaias (AL092026) — Destin, Florida"
    assert embed.color.value == 0xE74C3C
    assert "made landfall" in embed.description
    assert embed.footer.text.startswith("NHC official • ")
    assert "💨 **105 MPH**" in embed.description
    assert "🌀 **971 MB**" in embed.description

    # Dedup state is persisted per channel so a restart cannot re-announce.
    assert await state.get(tracker._landfall_key(999, "AL092026"))
    assert await state.get(tracker._landfall_key(777, "AL092026"))


@pytest.mark.asyncio
async def test_announce_landfall_skips_when_nothing_is_tracking():
    cog, _ = _make_cog()
    match = detect_landfall(LANDFALL_TCU)
    assert match is not None
    with patch.object(
        tracker, "get_all_tracked_channels", AsyncMock(return_value={})
    ), patch.object(tracker, "get_state", AsyncMock()) as mock_state:
        await cog.announce_landfall(
            storm_id="AL092026",
            product_id="pid",
            match=match,
            parsed=_parsed(),
        )
    mock_state.assert_not_awaited()


# ── TropicalCog → tracker hand-off ───────────────────────────────────────────


def _tropical_cog(tracker_cog=None):
    bot = MagicMock()
    bot.get_cog = MagicMock(return_value=tracker_cog)
    bot.get_channel = MagicMock(return_value=AsyncMock())
    return TropicalCog(bot), bot


@pytest.mark.asyncio
async def test_notify_tracker_fans_out_advisory_and_announces_landfall():
    tracker_cog = AsyncMock()
    cog, _ = _tropical_cog(tracker_cog)
    parsed = _parsed()

    with patch("cogs.tropical.get_active_storms", AsyncMock(return_value={})):
        await cog._notify_tracker("UPDATE", "202610100130-KNHC-WTNT64-TCUAT4", parsed)

    tracker_cog.announce_landfall.assert_awaited_once()
    kwargs = tracker_cog.announce_landfall.await_args.kwargs
    assert kwargs["storm_id"] == "AL092026"
    assert kwargs["product_id"] == "202610100130-KNHC-WTNT64-TCUAT4"
    assert kwargs["match"].place == "Destin, Florida"

    tracker_cog.on_nhc_product.assert_awaited_once()
    fanout = tracker_cog.on_nhc_product.await_args.args
    assert fanout == ("AL092026", "202610100130-KNHC-WTNT64-TCUAT4", parsed)


@pytest.mark.asyncio
async def test_notify_tracker_scans_discussions_for_landfall_without_fanout():
    """Discussions repeat the landfall statement but are not hourly updates."""
    tracker_cog = AsyncMock()
    cog, _ = _tropical_cog(tracker_cog)

    with patch("cogs.tropical.get_active_storms", AsyncMock(return_value={})):
        await cog._notify_tracker("DISCUSSION", "pid-discussion", _parsed())

    tracker_cog.announce_landfall.assert_awaited_once()
    tracker_cog.on_nhc_product.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_tracker_ignores_products_that_cannot_carry_landfall():
    tracker_cog = AsyncMock()
    cog, _ = _tropical_cog(tracker_cog)

    await cog._notify_tracker("TROPICAL WEATHER OUTLOOK", "pid-two", _parsed())

    tracker_cog.announce_landfall.assert_not_awaited()
    tracker_cog.on_nhc_product.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_tropical_product_hands_off_to_tracker_before_channel_check():
    """Tracked channels must be served even when the raw-product channel is off."""
    tracker_cog = AsyncMock()
    cog, bot = _tropical_cog(tracker_cog)

    with _stack(
        patch("cogs.tropical.fetch_nhc_product", AsyncMock(return_value=_parsed())),
        patch("cogs.tropical.get_state", AsyncMock(return_value="disabled")),
        patch("cogs.tropical.get_active_storms", AsyncMock(return_value={})),
    ):
        await cog.post_tropical_product("202610100130-KNHC-WTNT64-TCUAT4", "", "UPDATE")

    tracker_cog.on_nhc_product.assert_awaited_once()
    bot.get_channel.assert_not_called()
