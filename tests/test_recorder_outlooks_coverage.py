"""Coverage round 9: VAD recording missions (pure data-class logic) + outlook URL/product extraction."""

import os

import pytest

from cogs import recorder
from cogs import outlooks


# ── VADRecordingMission ──────────────────────────────────────────────────────


@pytest.fixture
def tmp_recording_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(recorder, "RECORDING_DIR", str(tmp_path / "recordings"))
    return tmp_path / "recordings"


def test_mission_init_windows(tmp_recording_dir):
    mission = recorder.VADRecordingMission("KTLX", 1000.0)
    assert mission.site_id == "KTLX"
    assert mission.trigger_ts == 1000.0
    assert mission.start_ts == 1000.0 - 3600  # 1h lookback
    assert mission.end_ts == 1000.0 + 5400  # 90m follow-up
    assert mission.event_ids == set()
    assert mission.dir == str(tmp_recording_dir / "KTLX_1000")
    assert os.path.isdir(mission.dir)


def test_mission_extend(tmp_recording_dir):
    mission = recorder.VADRecordingMission("KTLX", 1000.0)
    mission.extend(2000.0, event_id="EVT-1")
    assert mission.end_ts == 2000.0 + 5400
    assert mission.event_ids == {"EVT-1"}
    # Extending with an earlier trigger keeps the max end.
    mission.extend(500.0, event_id="EVT-2")
    assert mission.end_ts == 2000.0 + 5400
    assert mission.event_ids == {"EVT-1", "EVT-2"}
    # Same trigger does not change the window or the event set.
    mission.extend(2000.0)
    assert mission.end_ts == 2000.0 + 5400
    assert mission.event_ids == {"EVT-1", "EVT-2"}


def test_mission_to_from_dict_roundtrip(tmp_recording_dir):
    mission = recorder.VADRecordingMission("KTLX", 1000.0)
    mission.extend(2000.0, event_id="EVT-1")
    mission.processed_timestamps = {1500.0, 1600.0}

    restored = recorder.VADRecordingMission.from_dict(mission.to_dict())

    assert restored.site_id == "KTLX"
    assert restored.trigger_ts == 1000.0
    assert restored.start_ts == 1000.0 - 3600
    assert restored.end_ts == 2000.0 + 5400
    assert restored.event_ids == {"EVT-1"}
    assert restored.processed_timestamps == {1500.0, 1600.0}


# ── Outlook product extraction ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://spc.noaa.gov/products/outlook/day1probotlk_20260811_1630_torn.gif", "tornado"),
        ("https://spc.noaa.gov/products/outlook/day1probotlk_20260811_1630_wind.gif", "wind"),
        ("https://spc.noaa.gov/products/outlook/day1probotlk_20260811_1630_hail.gif", "hail"),
        ("https://spc.noaa.gov/products/outlook/day1otlk_20260811_1630.gif", "categorical"),
        ("https://spc.noaa.gov/products/outlook/day2prob_20260811_1630.gif", "categorical"),
        ("https://example.com/other.png", "other"),
    ],
)
def test_extract_product_from_url(url, expected):
    day = 2 if "day2prob" in url or "day2otlk" in url else 1
    assert outlooks._extract_product_from_url(url, day) == expected


def test_extract_product_from_url_case_insensitive():
    assert (
        outlooks._extract_product_from_url(
            "https://spc.noaa.gov/products/outlook/DAY2PROBOTLK_20260811_1200_TORN.GIF", 2
        )
        == "tornado"
    )
    # Mixed-case day prefix too.
    assert (
        outlooks._extract_product_from_url(
            "https://spc.noaa.gov/products/outlook/Day1Otlk_20260811_1630.gif", 1
        )
        == "categorical"
    )


def test_extract_product_from_url_wrong_day():
    # day1probotlk with day=2 -> "other" (the day prefix must match)
    assert (
        outlooks._extract_product_from_url(
            "https://spc.noaa.gov/products/outlook/day1probotlk_20260811_1630_torn.gif", 2
        )
        == "other"
    )


def test_outlook_summary_view_button():
    view = outlooks.OutlookSummaryView("1")
    assert len(view.children) == 1
    assert view.children[0].label == "🪄 AI Analysis"
    assert view.children[0].custom_id == "ai_outlook:1"
