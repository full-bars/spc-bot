import pytest
from cogs.sounding_utils import (
    parse_sounding_time,
    get_recent_sounding_times,
    _iem_level_is_valid,
    get_md_area_centroid,
)


def test_parse_sounding_time():
    assert parse_sounding_time("04-10-2026 00z") == ("2026", "04", "10", "00")
    assert parse_sounding_time("04-10-2026 12Z") == ("2026", "04", "10", "12")
    assert parse_sounding_time(None) is None

    with pytest.raises(ValueError):
        parse_sounding_time("invalid time")

    with pytest.raises(ValueError):
        parse_sounding_time("04-10-2026 25z")


def test_get_recent_sounding_times():
    times = get_recent_sounding_times(4)
    assert len(times) == 4
    for t in times:
        assert len(t) == 4
        assert t[3] in ("00", "12")


def test_iem_level_is_valid():
    valid_lv = {"pres": "1000", "tmpc": "20", "dwpc": "15", "drct": "180", "sknt": "10"}
    assert _iem_level_is_valid(valid_lv) is True

    invalid_pres = {"pres": "2000", "tmpc": "20", "dwpc": "15", "drct": "180", "sknt": "10"}
    assert _iem_level_is_valid(invalid_pres) is False

    missing_key = {"pres": "1000", "tmpc": "20", "drct": "180", "sknt": "10"}
    assert _iem_level_is_valid(missing_key) is False


@pytest.mark.asyncio
async def test_get_md_area_centroid():
    raw_text = """
    ...
    LAT...LON   35009845 36009845 36009745 35009745 35009845
    ...
    """
    centroid = await get_md_area_centroid(raw_text)
    assert centroid is not None
    lat, lon = centroid
    assert lat == pytest.approx(35.4)
    assert lon == pytest.approx(-98.05)
