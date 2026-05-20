"""Tests for `utils.http` — retry/backoff, rate-limit handling, and
the conditional-GET helper.

We patch the session's `get` method rather than spinning up a real HTTP
server — the objective here is to verify *our* logic around aiohttp,
not aiohttp itself.
"""

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
        self.request_info = MagicMock()
        self.history = ()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def read(self):
        return self._body

    def raise_for_status(self):
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                self.request_info,
                self.history,
                status=self.status,
                message="Mocked HTTP Error"
            )


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
    assert status == 0
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


# ── CircuitBreaker state machine ────────────────────────────────────────────

@pytest.fixture
def _spc_caplog(caplog):
    """The `spc_bot` logger has propagate=False, so pytest's default caplog
    (attached to the root logger) sees nothing. Attach caplog's handler
    directly to the named logger for the duration of the test."""
    import logging
    logger = logging.getLogger("spc_bot")
    logger.addHandler(caplog.handler)
    yield caplog
    logger.removeHandler(caplog.handler)


class TestCircuitBreakerStateMachine:
    """Pins the three-state semantics added when the old `==` threshold log
    was producing duplicate OPEN warnings on every half-open flap."""

    def _make_breaker(self):
        return http.CircuitBreaker(failure_threshold=3, recovery_timeout=0.05)

    def test_closed_until_threshold(self):
        cb = self._make_breaker()
        for _ in range(2):
            cb.record_failure("h")
            assert not cb.is_open("h")
        cb.record_failure("h")
        assert cb.is_open("h")

    def test_open_does_not_relog_on_further_failures(self, _spc_caplog):
        caplog = _spc_caplog
        cb = self._make_breaker()
        import logging
        # Disambiguate this test's host name so leftover records from any
        # other test that happens to use "h" can't bleed into the count.
        host = "test_open_does_not_relog.example"
        with caplog.at_level(logging.WARNING, logger="spc_bot"):
            for _ in range(3):
                cb.record_failure(host)
            warning_count_after_trip = sum(
                1 for r in caplog.records
                if "Circuit OPEN" in r.message and host in r.message
            )
            assert warning_count_after_trip == 1, "should log once on threshold edge"
            # Further failures while already OPEN must NOT re-log the threshold.
            for _ in range(10):
                cb.record_failure(host)
            assert sum(
                1 for r in caplog.records
                if "Circuit OPEN" in r.message and host in r.message
            ) == warning_count_after_trip

    def test_half_open_blocks_other_callers(self):
        """The first caller after recovery_timeout transitions OPEN→HALF_OPEN
        and returns False; concurrent callers must still see is_open=True
        until the trial resolves."""
        cb = self._make_breaker()
        for _ in range(3):
            cb.record_failure("h")
        time.sleep(0.06)  # past recovery_timeout
        assert cb.is_open("h") is False  # first caller — trial slot
        # State must now be HALF_OPEN; subsequent callers see OPEN.
        assert cb.is_open("h") is True
        assert cb.is_open("h") is True

    def test_half_open_trial_success_closes_circuit(self):
        cb = self._make_breaker()
        for _ in range(3):
            cb.record_failure("h")
        time.sleep(0.06)
        cb.is_open("h")  # trip into HALF_OPEN
        cb.record_success("h")
        assert cb._get_state("h") == cb._STATE_CLOSED
        assert "h" not in cb.failures

    def test_half_open_trial_failure_returns_to_open_without_relog(self, _spc_caplog):
        caplog = _spc_caplog
        cb = self._make_breaker()
        import logging
        for _ in range(3):
            cb.record_failure("h")
        time.sleep(0.06)
        cb.is_open("h")  # → HALF_OPEN
        caplog.clear()  # discard records from setup so we test only the trial
        with caplog.at_level(logging.WARNING, logger="spc_bot"):
            cb.record_failure("h")  # trial failed
        # Must NOT log a fresh "Circuit OPEN" warning — that was already
        # surfaced on the CLOSED→OPEN edge.
        assert not any("Circuit OPEN" in r.message for r in caplog.records)
        assert cb._get_state("h") == cb._STATE_OPEN


# Need `time` for the breaker tests' sleeps — import here to keep the
# rest of the file untouched.
import time  # noqa: E402


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
