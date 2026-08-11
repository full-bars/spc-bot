"""Coverage round 11: radar rotation formatting helpers + recorder mission persistence."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cogs.radar import (
    _bearing_to_compass,
    _fmt_z,
    _format_rotation_field,
    _format_site_line,
    _format_tds_timeline,
    _tds_marker,
)
from cogs import recorder


@pytest.fixture
def tmp_recording_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(recorder, "RECORDING_DIR", str(tmp_path / "recordings"))
    return tmp_path / "recordings"


# ── Bearing to compass ───────────────────────────────────────────────────────


def test_bearing_to_compass():
    assert _bearing_to_compass(0) == "N"
    assert _bearing_to_compass(22.5) == "NNE"
    assert _bearing_to_compass(45) == "NE"
    assert _bearing_to_compass(90) == "E"
    assert _bearing_to_compass(180) == "S"
    assert _bearing_to_compass(270) == "W"
    assert _bearing_to_compass(360) == "N"  # wraps


# ── ISO time formatting ──────────────────────────────────────────────────────


def test_fmt_z():
    assert _fmt_z("2026-08-11T20:15:00Z") == "20:15Z"
    assert _fmt_z("garbage") == "garbage"  # passthrough on parse failure


# ── TDS markers ─────────────────────────────────────────────────────────────


def test_tds_marker_thresholds():
    assert _tds_marker(50) == "🔴"
    assert _tds_marker(25) == "🟠"
    assert _tds_marker(10) == "⚪"
    assert _tds_marker(99) == "🔴"


# ── Site line formatting ─────────────────────────────────────────────────────


def test_format_site_line_tvs():
    line = _format_site_line(
        {
            "strength": "tvs",
            "azimuth_deg": 90.0,
            "vrot_mps": 45.2,
            "gtg_dv_mps": 30.1,
            "range_km": 40.5,
            "depth_tilts": 3,
        }
    )
    assert "🌪️ **TVS**" in line
    assert "Vrot 45 m/s" in line
    assert "ΔV 30 m/s" in line
    assert "E @ 40km" in line
    assert "3 tilts deep" in line


# ── Rotation field formatting ────────────────────────────────────────────────


def test_format_rotation_field_missing_sidecar():
    text = _format_rotation_field(Path("/nonexistent/sidecar.json"))
    assert "No rotation data" in text


def test_format_rotation_field_bad_json(tmp_path):
    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text("{not-json")
    text = _format_rotation_field(sidecar)
    assert text == "No rotation data available."


def test_format_rotation_field_full(tmp_path):
    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text(
        json.dumps(
            {
                "volume_time": "2026-08-11T20:15:00Z",
                "sites": [
                    {
                        "strength": "tvs",
                        "azimuth_deg": 90.0,
                        "vrot_mps": 45.2,
                        "gtg_dv_mps": 30.1,
                        "range_km": 40.5,
                        "depth_tilts": 3,
                    },
                    {
                        "strength": "weak_circulation",
                        "azimuth_deg": 180.0,
                        "vrot_mps": 10.0,
                        "gtg_dv_mps": 5.0,
                        "range_km": 80.0,
                        "depth_tilts": 1,
                    },
                ],
                "peak_vrot": {
                    "time": "2026-08-11T20:10:00Z",
                    "strength": "mesocyclone",
                    "vrot_mps": 50.0,
                },
                "peak_dv": {"time": "2026-08-11T20:05:00Z", "gtg_dv_mps": 40.0},
                "tds_timeline": [
                    {"time": "2026-08-11T20:10:00Z", "tds_score": 60.0},
                    {"time": "2026-08-11T20:15:00Z", "tds_score": 30.0},
                ],
            }
        )
    )
    text = _format_rotation_field(sidecar)
    assert "**Current (20:15Z):**" in text
    assert "🌪️ **TVS**" in text
    assert "**Peak this loop:**" in text
    assert "**Peak ΔV:**" in text
    assert "**PTDS (debris signature) by scan:**" in text
    assert "Peak: 🔴 60% @ 20:10Z" in text


# ── TDS timeline ─────────────────────────────────────────────────────────────


def test_format_tds_timeline_empty():
    assert _format_tds_timeline([]) == []


def test_format_tds_timeline_scans():
    scans = [
        {"time": "2026-08-11T20:05:00Z", "tds_score": 10.0},
        {"time": "2026-08-11T20:10:00Z", "tds_score": 55.0},
        {"time": "2026-08-11T20:15:00Z", "tds_score": 30.0},
    ]
    lines = _format_tds_timeline(scans)
    assert lines[0] == "**PTDS (debris signature) by scan:**"
    assert "Peak: 🔴 55% @ 20:10Z" in lines[1]
    assert any("20:15Z — 30%" in ln for ln in lines)
    assert any("experimental and unvalidated" in ln for ln in lines)


# ── Recorder mission persistence ─────────────────────────────────────────────


def _recorder_cog(bot=None):
    return recorder.RecorderCog(bot or MagicMock())


async def test_persist_missions_writes_json(tmp_recording_dir):
    cog = _recorder_cog()
    cog.active_missions = {"KTLX": recorder.VADRecordingMission("KTLX", 1000.0)}
    with patch("cogs.recorder.set_state", new_callable=AsyncMock) as mock_set:
        await cog._persist_missions()

    mock_set.assert_awaited_once()
    data = json.loads(mock_set.await_args.args[1])
    assert data["KTLX"]["site_id"] == "KTLX"
    assert data["KTLX"]["trigger_ts"] == 1000.0


async def test_load_missions_restores(tmp_recording_dir):
    cog = _recorder_cog()
    payload = json.dumps(
        {
            "KTLX": {
                "site_id": "KTLX",
                "trigger_ts": 1000.0,
                "event_ids": ["EVT-1"],
                "start_ts": 1000.0 - 3600,
                "end_ts": 1000.0 + 5400,
                "processed_timestamps": [1500.0],
            }
        }
    )
    with patch("cogs.recorder.get_state", new_callable=AsyncMock, return_value=payload):
        await cog._load_missions()

    assert "KTLX" in cog.active_missions
    assert cog.active_missions["KTLX"].event_ids == {"EVT-1"}
    assert cog.active_missions["KTLX"].processed_timestamps == {1500.0}


async def test_start_mission_new_and_extend(tmp_recording_dir):
    cog = _recorder_cog()
    with patch("cogs.recorder.set_state", new_callable=AsyncMock):
        await cog.start_mission("KTLX", 1000.0, event_id="EVT-1")
        assert "KTLX" in cog.active_missions
        assert cog.active_missions["KTLX"].event_ids == {"EVT-1"}

        await cog.start_mission("KTLX", 2000.0, event_id="EVT-2")
        assert cog.active_missions["KTLX"].event_ids == {"EVT-1", "EVT-2"}
        assert cog.active_missions["KTLX"].end_ts == 2000.0 + 5400
