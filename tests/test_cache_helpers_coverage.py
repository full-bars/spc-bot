"""Coverage round 10: cache helpers — validators LRU, timedelta, SPC windows, conditional GET."""

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from utils import cache as cache_mod
from utils.cache import (
    _stat_mtimes,
    _validators_get,
    _validators_set,
    fetch_with_validators,
    format_timedelta,
    hydrate_validators_from_store,
    is_near_spc_update,
    should_use_cache_for_manual,
)


@pytest.fixture(autouse=True)
def _clear_validators():
    cache_mod._validators_cache.clear()
    cache_mod._validators_hydrated = False
    yield
    cache_mod._validators_cache.clear()
    cache_mod._validators_hydrated = False


# ── Validator LRU cache ──────────────────────────────────────────────────────


def test_validators_set_get_roundtrip():
    _validators_set("http://a", {"etag": "e1"})
    assert _validators_get("http://a") == {"etag": "e1"}
    assert _validators_get("http://missing") == {}


def test_validators_cache_evicts_oldest():
    max_n = cache_mod._VALIDATORS_CACHE_MAX
    for i in range(max_n + 1):
        _validators_set(f"http://u{i}", {"etag": f"e{i}"})
    # The oldest (u0) was evicted; the newest is still present.
    assert _validators_get("http://u0") == {}
    assert _validators_get(f"http://u{max_n}") == {"etag": f"e{max_n}"}


# ── Validator hydration ──────────────────────────────────────────────────────


async def test_hydrate_validators_from_store():
    stored = {"http://a": {"etag": "e1"}, "http://b": {"etag": "e2"}}
    with patch(
        "utils.cache.get_all_validators", new_callable=AsyncMock, return_value=stored
    ) as mock_get:
        n = await hydrate_validators_from_store()
        n2 = await hydrate_validators_from_store()

    assert n == 2
    assert n2 == 2
    assert _validators_get("http://a") == {"etag": "e1"}
    mock_get.assert_awaited_once()  # idempotent — only the first call hits the store


# ── SPC update window ────────────────────────────────────────────────────────


class _FakeDatetime:
    """Stand-in for utils.cache.datetime with a fixed Central 'now'."""

    fixed = None

    @classmethod
    def now(cls, tz=None):
        return cls.fixed


@pytest.fixture
def fake_now(monkeypatch):
    monkeypatch.setattr(cache_mod, "datetime", _FakeDatetime)
    return _FakeDatetime


def test_is_near_spc_update_unknown_day(fake_now):
    fake_now.fixed = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    assert is_near_spc_update(9) is False


def test_is_near_spc_update_within_window(fake_now):
    # Day 1 schedule includes 20:00 Central; 20:30 is within the ±60m window.
    fake_now.fixed = datetime(2026, 8, 11, 20, 30, tzinfo=timezone.utc)
    assert is_near_spc_update(1) is True


def test_is_near_spc_update_outside_window(fake_now):
    # 22:30 is 2.5h from the 20:00 update and 2.5h from the next-day 01:00.
    fake_now.fixed = datetime(2026, 8, 11, 22, 30, tzinfo=timezone.utc)
    assert is_near_spc_update(1) is False


# ── Timedelta formatting ─────────────────────────────────────────────────────


def test_format_timedelta():
    assert format_timedelta(timedelta(seconds=90)) == "1m"
    assert format_timedelta(timedelta(hours=1)) == "1h 0m"
    assert format_timedelta(timedelta(days=2, hours=3)) == "2d 3h 0m"
    assert format_timedelta(timedelta(seconds=-5)) == "just now"
    assert format_timedelta(timedelta(0)) == "0m"


# ── Stat mtimes ──────────────────────────────────────────────────────────────


def test_stat_mtimes_all_exist(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.write_bytes(b"x")
    b.write_bytes(b"y")
    mtimes = _stat_mtimes([str(a), str(b)])
    assert mtimes is not None
    assert len(mtimes) == 2
    assert all(m > 0 for m in mtimes)


def test_stat_mtimes_missing_returns_none(tmp_path):
    a = tmp_path / "a"
    a.write_bytes(b"x")
    assert _stat_mtimes([str(a), str(tmp_path / "missing")]) is None


# ── Manual cache decision ────────────────────────────────────────────────────


async def test_should_use_cache_no_day():
    assert await should_use_cache_for_manual(["http://x/other.png"]) is False


async def test_should_use_cache_near_update_forces_fresh():
    with patch("utils.cache.is_near_spc_update", return_value=True):
        assert await should_use_cache_for_manual(["http://x/day1otlk.gif"]) is False


async def test_should_use_cache_missing_files():
    with patch("utils.cache.is_near_spc_update", return_value=False), patch(
        "utils.cache._stat_mtimes", return_value=None
    ):
        assert await should_use_cache_for_manual(["http://x/day1otlk.gif"]) is False


async def test_should_use_cache_fresh_files():
    now = time.time()
    with patch("utils.cache.is_near_spc_update", return_value=False), patch(
        "utils.cache._stat_mtimes", return_value=[now - 60]
    ):
        assert await should_use_cache_for_manual(["http://x/day2otlk.gif"]) is True


async def test_should_use_cache_old_files():
    now = time.time()
    with patch("utils.cache.is_near_spc_update", return_value=False), patch(
        "utils.cache._stat_mtimes", return_value=[now - 4 * 3600]
    ):
        assert await should_use_cache_for_manual(["http://x/day1otlk.gif"]) is False


async def test_should_use_cache_three_hour_boundary():
    now = time.time()
    # Just under 3h is NOT > 3h -> cache still used; just past 3h -> refresh.
    # (Use ±1s margins — time.time() and datetime.now() have sub-second skew.)
    with patch("utils.cache.is_near_spc_update", return_value=False), patch(
        "utils.cache._stat_mtimes", return_value=[now - (3 * 3600 - 1)]
    ):
        assert await should_use_cache_for_manual(["http://x/day1otlk.gif"]) is True
    with patch("utils.cache.is_near_spc_update", return_value=False), patch(
        "utils.cache._stat_mtimes", return_value=[now - (3 * 3600 + 1)]
    ):
        assert await should_use_cache_for_manual(["http://x/day1otlk.gif"]) is False


# ── Conditional GET ──────────────────────────────────────────────────────────


async def test_fetch_with_validators_200_stores():
    with patch(
        "utils.cache.http_get_bytes_conditional",
        new_callable=AsyncMock,
        return_value=(b"data", 200, {"etag": "e1", "last_modified": "lm"}),
    ), patch("utils.cache.set_validators", new_callable=AsyncMock) as mock_set:
        content, status = await fetch_with_validators("http://a")

    assert content == b"data"
    assert status == 200
    assert _validators_get("http://a") == {"etag": "e1", "last_modified": "lm"}
    mock_set.assert_awaited_once()


async def test_fetch_with_validators_304():
    with patch(
        "utils.cache.http_get_bytes_conditional",
        new_callable=AsyncMock,
        return_value=(None, 304, None),
    ):
        content, status = await fetch_with_validators("http://a")

    assert (content, status) == (None, 304)


async def test_fetch_with_validators_retries_retry_statuses():
    responses = [
        (None, 404, None),
        (None, 404, None),
        (b"ok", 200, {"etag": "e1"}),
    ]
    with patch(
        "utils.cache.http_get_bytes_conditional",
        new_callable=AsyncMock,
        side_effect=responses,
    ) as mock_cond, patch("utils.cache.set_validators", new_callable=AsyncMock), patch(
        "utils.cache.asyncio.sleep", new_callable=AsyncMock
    ):
        content, status = await fetch_with_validators("http://a", retries=2, retry_statuses=[404])

    assert content == b"ok"
    assert status == 200
    assert mock_cond.await_count == 3
