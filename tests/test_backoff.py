"""Tests for `utils.backoff.TaskBackoff`.

Previously this class was stubbed by an autouse conftest fixture across
the whole suite, so no test ever exercised the real delay/skip logic.
"""

from unittest.mock import AsyncMock, patch

import pytest

from utils.backoff import TaskBackoff, _BACKOFF_DELAYS


def test_fresh_backoff_does_not_skip():
    b = TaskBackoff("t1")
    assert b.should_skip() is False


def test_success_resets_failure_count():
    b = TaskBackoff("t1")
    b._failures = 4
    b.success()
    assert b._failures == 0
    assert b.should_skip() is False


async def test_failure_increments_and_sleeps(monkeypatch):
    """`failure()` must increment counter and sleep for the delay at
    that level. We patch `asyncio.sleep` so the test stays fast."""
    b = TaskBackoff("t1")
    slept = []

    async def _fake_sleep(d):
        slept.append(d)

    # backoff module imports `asyncio` at top — patch the module-level
    # reference so our fake sleep is used.
    import utils.backoff as bm

    monkeypatch.setattr(bm.asyncio, "sleep", _fake_sleep)

    # First failure: index 1 → 0s, no sleep.
    await b.failure()
    assert b._failures == 1
    assert slept == []

    # Second failure: index 2 → 30s.
    await b.failure()
    assert b._failures == 2
    assert slept == [_BACKOFF_DELAYS[2]]


async def test_failure_caps_delay_at_max_entry(monkeypatch):
    """Beyond the end of the delay table, the last entry is used."""
    b = TaskBackoff("t1")
    slept = []

    async def _fake_sleep(d):
        slept.append(d)

    import utils.backoff as bm

    monkeypatch.setattr(bm.asyncio, "sleep", _fake_sleep)

    # Drive failures well past the table length.
    for _ in range(len(_BACKOFF_DELAYS) + 3):
        await b.failure()

    # Last recorded sleep must equal the maximum entry in the table.
    assert slept[-1] == _BACKOFF_DELAYS[-1]


async def test_failure_alerts_at_fifth_consecutive_failure(monkeypatch):
    """The 5th consecutive failure should fire the Discord alert hook."""
    b = TaskBackoff("t1")
    import utils.backoff as bm

    async def _noop_sleep(_):
        pass

    monkeypatch.setattr(bm.asyncio, "sleep", _noop_sleep)

    alert = AsyncMock()
    # The alert is imported from `main` lazily inside the method.
    with patch("main.send_bot_alert", new=alert):
        for _ in range(4):
            await b.failure(bot="stub")
        alert.assert_not_called()
        await b.failure(bot="stub")  # 5th
        alert.assert_called_once()
