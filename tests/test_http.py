"""Tests for `utils.http` — retry/backoff, rate-limit handling, and
the conditional-GET helper.

We patch the session's `get` method rather than spinning up a real HTTP
server — the objective here is to verify *our* logic around aiohttp,
not aiohttp itself.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from utils import http


class _MockResponse:
    """Minimal stand-in for `aiohttp.ClientResponse`."""

    def __init__(self, status: int, body: bytes = b"", headers: dict = None):
        self.status = status
        self._body = body
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def read(self):
        return self._body


def _session_returning(*responses):
    """Build a mock session whose `.get(...)` yields the given responses
    in order. Each call consumes one."""
    it = iter(responses)
    session = MagicMock()

    def _get(url, **kwargs):
        return next(it)

    session.get = MagicMock(side_effect=_get)
    session.closed = False
    return session


@pytest.fixture(autouse=True)
async def _reset_http_module():
    """Ensure the module-level session is reset between tests."""
    yield
    await http.close_session()


# ── http_get_bytes ──────────────────────────────────────────────────────────

async def test_http_get_bytes_success():
    session = _session_returning(_MockResponse(200, b"payload"))
    with patch("utils.http.ensure_session", AsyncMock(return_value=session)):
        content, status = await http.http_get_bytes("https://x/a", retries=1)
    assert status == 200
    assert content == b"payload"


async def test_http_get_bytes_retries_on_429_then_succeeds():
    session = _session_returning(
        _MockResponse(429, headers={"Retry-After": "0"}),
        _MockResponse(200, b"ok"),
    )
    with patch("utils.http.ensure_session", AsyncMock(return_value=session)):
        content, status = await http.http_get_bytes("https://x/a", retries=3)
    assert status == 200
    assert content == b"ok"


async def test_http_get_bytes_retry_after_is_capped():
    """Retry-After values greater than 60 must be clamped to avoid
    hanging the event loop for minutes on a cooperative server."""
    session = _session_returning(
        _MockResponse(503, headers={"Retry-After": "99999"}),
        _MockResponse(200, b"ok"),
    )
    slept = []

    async def _fake_sleep(d):
        slept.append(d)

    with patch("utils.http.ensure_session", AsyncMock(return_value=session)), \
         patch("utils.http.asyncio.sleep", _fake_sleep):
        await http.http_get_bytes("https://x/a", retries=2)
    assert slept and max(slept) <= 60


async def test_http_get_bytes_gives_up_after_retries():
    """Every attempt raises — function returns (None, None)."""
    session = MagicMock()
    session.get = MagicMock(
        side_effect=aiohttp.ClientError("boom")
    )
    session.closed = False

    async def _fake_sleep(_):
        pass

    with patch("utils.http.ensure_session", AsyncMock(return_value=session)), \
         patch("utils.http.asyncio.sleep", _fake_sleep):
        content, status = await http.http_get_bytes("https://x/a", retries=3)
    assert content is None
    assert status is None
    assert session.get.call_count == 3


# ── http_get_bytes_conditional ──────────────────────────────────────────────

async def test_conditional_get_sends_validator_headers():
    """If-None-Match and If-Modified-Since must be sent when provided."""
    seen_headers = {}

    def _get(url, **kwargs):
        seen_headers.update(kwargs.get("headers") or {})
        return _MockResponse(304)

    session = MagicMock()
    session.get = MagicMock(side_effect=_get)
    session.closed = False

    with patch("utils.http.ensure_session", AsyncMock(return_value=session)):
        _, status, _ = await http.http_get_bytes_conditional(
            "https://x/a",
            etag='"abc"',
            last_modified="Wed, 01 Jan 2025 00:00:00 GMT",
            retries=1,
        )
    assert status == 304
    assert seen_headers.get("If-None-Match") == '"abc"'
    assert seen_headers.get("If-Modified-Since") == "Wed, 01 Jan 2025 00:00:00 GMT"


async def test_conditional_get_304_preserves_prior_validators():
    """On 304 the caller's existing validators should be echoed back so
    the caller can keep them for the next cycle."""
    session = _session_returning(_MockResponse(304))
    with patch("utils.http.ensure_session", AsyncMock(return_value=session)):
        content, status, validators = await http.http_get_bytes_conditional(
            "https://x/a", etag='"abc"', retries=1
        )
    assert content is None
    assert status == 304
    assert validators == {"etag": '"abc"', "last_modified": ""}


async def test_conditional_get_200_returns_fresh_validators():
    """On 200 the returned validators reflect the response headers,
    not the request headers."""
    session = _session_returning(
        _MockResponse(
            200,
            b"body",
            headers={
                "ETag": '"new"',
                "Last-Modified": "Wed, 02 Jan 2025 00:00:00 GMT",
            },
        )
    )
    with patch("utils.http.ensure_session", AsyncMock(return_value=session)):
        content, status, validators = await http.http_get_bytes_conditional(
            "https://x/a", etag='"old"', retries=1
        )
    assert status == 200
    assert content == b"body"
    assert validators == {
        "etag": '"new"',
        "last_modified": "Wed, 02 Jan 2025 00:00:00 GMT",
    }


async def test_conditional_get_no_validators_sends_no_headers():
    """If no etag/last_modified given, no conditional headers are sent."""
    seen_headers = {}

    def _get(url, **kwargs):
        seen_headers["captured"] = kwargs.get("headers")
        return _MockResponse(200, b"body")

    session = MagicMock()
    session.get = MagicMock(side_effect=_get)
    session.closed = False

    with patch("utils.http.ensure_session", AsyncMock(return_value=session)):
        await http.http_get_bytes_conditional("https://x/a", retries=1)
    # Either None or an empty dict is acceptable — the goal is no
    # stale validator leaking into the request.
    captured = seen_headers["captured"]
    assert not captured or "If-None-Match" not in captured
