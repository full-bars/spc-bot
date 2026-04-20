"""Tests for the conditional-GET flow in `utils.cache`.

PR #93 replaced the broken HEAD+GET dance with a single conditional GET
per URL, keyed on a module-level `_validators_cache`. These tests pin
that behavior down so a future refactor can't silently revert to the
old "every poll hits full bytes" pattern.
"""

from unittest.mock import AsyncMock

import pytest


@pytest.fixture(autouse=True)
def _clear_validators_cache():
    """The validator cache is module-level state; clear it between tests."""
    from utils import cache

    cache._validators_cache.clear()
    yield
    cache._validators_cache.clear()


async def test_first_poll_sends_no_conditional_headers(monkeypatch):
    """On first sight of a URL there are no stored validators, so the
    conditional GET degrades to a plain GET."""
    from utils import cache

    called_with = {}

    async def _fake_conditional(url, etag=None, last_modified=None, **kw):
        called_with["etag"] = etag
        called_with["last_modified"] = last_modified
        return (b"x" * 3000, 200, {"etag": '"v1"', "last_modified": ""})

    monkeypatch.setattr(cache, "http_get_bytes_conditional", _fake_conditional)
    monkeypatch.setattr(cache, "ensure_session", AsyncMock())

    updated, total, data = await cache.check_partial_updates_parallel(
        ["https://x/a"], cache={}
    )

    assert called_with == {"etag": None, "last_modified": None}
    assert total == 1
    assert updated == 1
    assert "https://x/a" in data


async def test_second_poll_echoes_stored_validators(monkeypatch):
    """After a 200, the next poll must send the validators we stored."""
    from utils import cache

    calls = []

    async def _fake_conditional(url, etag=None, last_modified=None, **kw):
        calls.append({"etag": etag, "last_modified": last_modified})
        if len(calls) == 1:
            return (
                b"x" * 3000,
                200,
                {"etag": '"v1"', "last_modified": "Wed, 01 Jan 2025 00:00:00 GMT"},
            )
        # Second call: server says not modified.
        return (None, 304, {"etag": '"v1"', "last_modified": ""})

    monkeypatch.setattr(cache, "http_get_bytes_conditional", _fake_conditional)
    monkeypatch.setattr(cache, "ensure_session", AsyncMock())

    await cache.check_partial_updates_parallel(["https://x/a"], cache={})
    updated, total, data = await cache.check_partial_updates_parallel(
        ["https://x/a"], cache={}
    )

    assert total == 1
    assert updated == 0  # 304 → no update
    assert data == {}
    # The second request must have sent the validators from the first.
    assert calls[1] == {
        "etag": '"v1"',
        "last_modified": "Wed, 01 Jan 2025 00:00:00 GMT",
    }


async def test_304_does_not_touch_downloaded_data(monkeypatch):
    """304 responses must not produce a hash or a downloaded-data entry."""
    from utils import cache

    async def _fake_conditional(url, etag=None, last_modified=None, **kw):
        return (None, 304, {"etag": etag or "", "last_modified": ""})

    monkeypatch.setattr(cache, "http_get_bytes_conditional", _fake_conditional)
    monkeypatch.setattr(cache, "ensure_session", AsyncMock())

    # Seed a validator so the 304 path is legitimate.
    cache._validators_cache["https://x/a"] = {
        "etag": '"v1"',
        "last_modified": "",
    }

    updated, total, data = await cache.check_partial_updates_parallel(
        ["https://x/a"], cache={"https://x/a": "oldhash"}
    )

    assert total == 1
    assert updated == 0
    assert data == {}


async def test_placeholder_image_is_not_reported_as_update(monkeypatch):
    """A tiny/placeholder body must not bump updated_count."""
    from utils import cache

    async def _fake_conditional(url, **kw):
        return (b"tiny", 200, {"etag": '"v1"', "last_modified": ""})

    monkeypatch.setattr(cache, "http_get_bytes_conditional", _fake_conditional)
    monkeypatch.setattr(cache, "ensure_session", AsyncMock())

    updated, total, data = await cache.check_partial_updates_parallel(
        ["https://x/a"], cache={}
    )
    assert updated == 0
    assert data == {}
