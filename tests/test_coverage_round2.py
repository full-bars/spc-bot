"""Coverage round 2: pure-logic tests for the worst-covered files.

Targets per-level IEM QC, the IEM->clean_data conversion, the recent-
sounding-times helper, and the status owner check — no network.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from cogs import sounding_utils
from cogs.status import is_owner


# ── IEM per-level QC ─────────────────────────────────────────────────────────


def test_iem_level_is_valid_accepts_plausible_level():
    lv = {"pres": 850.0, "tmpc": 10.0, "dwpc": 5.0, "drct": 180.0, "sknt": 20.0}
    assert sounding_utils._iem_level_is_valid(lv) is True


def test_iem_level_is_valid_rejects_missing_fields():
    assert sounding_utils._iem_level_is_valid({"pres": 850.0}) is False
    assert sounding_utils._iem_level_is_valid({}) is False


@pytest.mark.parametrize(
    "mutate",
    [
        {"pres": 0.5},
        {"pres": 1200.0},
        {"tmpc": -200.0},
        {"tmpc": 100.0},
        {"dwpc": 200.0},
        {"dwpc": 20.0, "tmpc": 10.0},  # dewpoint > temp + 0.5
        {"drct": 400.0},
        {"sknt": 400.0},
        {"pres": "not-a-number"},
    ],
)
def test_iem_level_is_valid_rejects_implausible(mutate):
    lv = {"pres": 850.0, "tmpc": 10.0, "dwpc": 5.0, "drct": 180.0, "sknt": 20.0}
    lv.update(mutate)
    assert sounding_utils._iem_level_is_valid(lv) is False


# ── IEM profile -> clean_data conversion ─────────────────────────────────────


def _sample_profile():
    return [
        {"pres": 1000.0, "hght": 100, "tmpc": 25.0, "dwpc": 15.0, "drct": 200.0, "sknt": 10.0},
        {"pres": 850.0, "hght": 1500, "tmpc": 12.0, "dwpc": 5.0, "drct": 210.0, "sknt": 25.0},
        {"pres": 500.0, "hght": 5800, "tmpc": -15.0, "dwpc": -25.0, "drct": 240.0, "sknt": 40.0},
    ]


def test_iem_to_clean_data_basic_conversion():
    data = sounding_utils._iem_to_clean_data(
        _sample_profile(), "KOUN", "NORMAN", 35.2, -97.4, 357.0, "2026-08-10T12:00:00Z"
    )
    assert data is not None
    assert len(data["p"]) == 3
    assert data["site_info"]["site-id"] == "KOUN"
    assert data["site_info"]["site-latlon"] == [35.2, -97.4]
    assert data["site_info"]["run-time"] == ["2026", "08", "10", "12:00"]
    # u/v components use the meteorological "from" direction:
    # u = -speed*sin(dir), v = -speed*cos(dir)
    u0 = -10.0 * np.sin(np.deg2rad(200.0))
    v0 = -10.0 * np.cos(np.deg2rad(200.0))
    assert data["u"][0].magnitude == pytest.approx(u0)
    assert data["v"][0].magnitude == pytest.approx(v0)


def test_iem_to_clean_data_dedupes_near_pressures():
    profile = _sample_profile()
    profile.insert(
        1, {"pres": 850.01, "hght": 1501, "tmpc": 12.1, "dwpc": 5.1, "drct": 211.0, "sknt": 26.0}
    )
    data = sounding_utils._iem_to_clean_data(
        profile, "KOUN", "NORMAN", 35.2, -97.4, 357.0, "2026-08-10T12:00:00Z"
    )
    assert data is not None
    assert len(data["p"]) == 3  # 850.01 deduped against 850.0


def test_iem_to_clean_data_keeps_distinct_pressures():
    # 0.2 hPa apart is above the 0.1 dedupe threshold — must be kept.
    profile = _sample_profile()
    profile.insert(
        1, {"pres": 850.2, "hght": 1502, "tmpc": 12.2, "dwpc": 5.2, "drct": 212.0, "sknt": 27.0}
    )
    data = sounding_utils._iem_to_clean_data(
        profile, "KOUN", "NORMAN", 35.2, -97.4, 357.0, "2026-08-10T12:00:00Z"
    )
    assert data is not None
    assert len(data["p"]) == 4


def test_iem_to_clean_data_rejects_all_invalid():
    data = sounding_utils._iem_to_clean_data(
        [{"pres": 0.0, "tmpc": None, "dwpc": None, "drct": None, "sknt": None}],
        "KOUN",
        "NORMAN",
        35.2,
        -97.4,
        357.0,
        "2026-08-10T12:00:00Z",
    )
    assert data is None


def test_iem_to_clean_data_bad_valid_time_falls_back():
    data = sounding_utils._iem_to_clean_data(
        _sample_profile(), "KOUN", "NORMAN", 35.2, -97.4, 357.0, "not-a-date"
    )
    assert data is not None
    assert data["site_info"]["run-time"] == ["none", "none", "none", "none"]


# ── recent sounding times ────────────────────────────────────────────────────


def test_get_recent_sounding_times_returns_standard_hours_desc():
    times = sounding_utils.get_recent_sounding_times(n=4)
    assert len(times) == 4
    for _y, _mo, _d, h in times:
        assert int(h) in (0, 6, 12, 18)
    # newest first
    dt0 = datetime(
        int(times[0][0]), int(times[0][1]), int(times[0][2]), int(times[0][3]), tzinfo=timezone.utc
    )
    dt1 = datetime(
        int(times[1][0]), int(times[1][1]), int(times[1][2]), int(times[1][3]), tzinfo=timezone.utc
    )
    assert dt0 >= dt1


# ── status owner check ───────────────────────────────────────────────────────


class _FakeOwner:
    def __init__(self, oid):
        self.id = oid


async def test_is_owner_matches_owner_id():
    interaction = MagicMock()
    interaction.user.id = 123
    interaction.client.owner_id = 123
    assert await is_owner(interaction) is True


async def test_is_owner_fetches_application_when_missing():
    interaction = MagicMock()
    interaction.user.id = 123
    interaction.client.owner_id = 999
    interaction.client.application = None
    app = MagicMock()
    app.owner = _FakeOwner(123)
    interaction.client.application_info = AsyncMock(return_value=app)
    assert await is_owner(interaction) is True


async def test_is_owner_team_membership():
    interaction = MagicMock()
    interaction.user.id = 123
    interaction.client.owner_id = 999
    app = MagicMock()
    import discord

    team = MagicMock(spec=discord.Team)
    team.members = [_FakeOwner(123), _FakeOwner(456)]
    app.owner = team
    interaction.client.application = app
    assert await is_owner(interaction) is True


async def test_is_owner_rejects_when_not_owner():
    interaction = MagicMock()
    interaction.user.id = 123
    interaction.client.owner_id = 999
    app = MagicMock()
    app.owner = _FakeOwner(999)
    interaction.client.application = app
    assert await is_owner(interaction) is False


async def test_is_owner_handles_none_owner():
    interaction = MagicMock()
    interaction.user.id = 123
    interaction.client.owner_id = 999
    app = MagicMock()
    app.owner = None
    interaction.client.application = app
    assert await is_owner(interaction) is False


async def test_is_owner_propagates_application_info_error():
    interaction = MagicMock()
    interaction.user.id = 123
    interaction.client.owner_id = 999
    interaction.client.application = None
    interaction.client.application_info = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        await is_owner(interaction)


# ── parse_sounding_time edge cases ───────────────────────────────────────────


def test_parse_sounding_time_none():
    assert sounding_utils.parse_sounding_time(None) is None


@pytest.mark.parametrize(
    "bad",
    ["2026-13-10 12z", "04-32-2026 12z", "04-10-2026 24z", "garbage"],
)
def test_parse_sounding_time_invalid(bad):
    with pytest.raises(ValueError):
        sounding_utils.parse_sounding_time(bad)


def test_parse_sounding_time_empty_returns_none():
    assert sounding_utils.parse_sounding_time("") is None


def test_parse_sounding_time_valid():
    assert sounding_utils.parse_sounding_time("04-10-2026 12z") == ("2026", "04", "10", "12")


def test_parse_sounding_time_valid_leap_day():
    assert sounding_utils.parse_sounding_time("02-29-2024 12z") == ("2024", "02", "29", "12")


# ── db warning counts for date ───────────────────────────────────────────────


async def test_warning_counts_for_date_groups_by_phenom(isolated_db):
    from utils import db as dbmod

    await dbmod.add_posted_warning(
        "KOUN.TO.W.0001", 1, 2, posted_at=100.0, tornado_confidence="observed"
    )
    await dbmod.add_posted_warning(
        "KOUN.TO.W.0002", 2, 2, posted_at=150.0, tornado_confidence="radar_indicated"
    )
    await dbmod.add_posted_warning("KOUN.SV.W.0001", 3, 2, posted_at=200.0)

    counts = await dbmod.get_warning_counts_for_date(since=0.0, until=1000.0)

    assert counts["tor"] == 2
    assert counts["svr"] == 1
    assert counts["ffw"] == 0
    assert counts["tor_observed"] == 1


async def test_warning_counts_for_date_window(isolated_db):
    from utils import db as dbmod

    await dbmod.add_posted_warning("KOUN.TO.W.0001", 1, 2, posted_at=100.0)
    await dbmod.add_posted_warning("KOUN.TO.W.0002", 2, 2, posted_at=2000.0)

    counts = await dbmod.get_warning_counts_for_date(since=500.0, until=1000.0)

    assert counts["tor"] == 0
