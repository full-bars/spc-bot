"""Tests for `utils.http` — retry/backoff, rate-limit handling, and
the conditional-GET helper.

We patch the session's `get` method rather than spinning up a real HTTP
server — the objective here is to verify *our* logic around aiohttp,
not aiohttp itself.
"""

import asyncio
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
                self.request_info, self.history, status=self.status, message="Mocked HTTP Error"
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

    with patch("utils.http.ensure_session", AsyncMock(return_value=session)), patch(
        "utils.http.asyncio.sleep", _fake_sleep
    ):
        await http.http_get_bytes("https://x/a", retries=2)
    assert slept and max(slept) <= 60


async def test_http_get_bytes_gives_up_after_retries():
    """Every attempt raises — function returns (None, None)."""
    session = MagicMock()
    session.get = MagicMock(side_effect=aiohttp.ClientError("boom"))
    session.closed = False

    async def _fake_sleep(_):
        pass

    with patch("utils.http.ensure_session", AsyncMock(return_value=session)), patch(
        "utils.http.asyncio.sleep", _fake_sleep
    ):
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
    (attached to the root logger) sees nothing. Temporarily enable
    propagation for the duration of the test so caplog's root handler picks
    up records — safer than adding the handler manually, since `at_level`
    on newer pytest also adds the handler and we'd get duplicate records."""
    import logging

    logger = logging.getLogger("spc_bot")
    prior_propagate = logger.propagate
    logger.propagate = True
    yield caplog
    logger.propagate = prior_propagate


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

        host = "test_open_does_not_relog.example"
        with caplog.at_level(logging.WARNING, logger="spc_bot"):
            for _ in range(3):
                cb.record_failure(host)
            warning_count_after_trip = sum(1 for r in caplog.records if "Circuit OPEN" in r.message)
            assert warning_count_after_trip == 1, "should log once on threshold edge"
            # Further failures while already OPEN must NOT re-log the threshold.
            for _ in range(10):
                cb.record_failure(host)
            assert (
                sum(1 for r in caplog.records if "Circuit OPEN" in r.message)
                == warning_count_after_trip
            )

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
        assert not any("Circuit OPEN" in r.getMessage() for r in caplog.records)
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


# ── looks_like_error_page ────────────────────────────────────────────────────

# Verbatim shape of what mesonet.agron.iastate.edu serves when it has
# blocked our IP: a 302 to iowamesonet.github.io/sorry/ that still lands on
# HTTP 200 after the client follows the redirect.
IEM_BLOCK_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Service Notice</title>
  <link rel="stylesheet" href="index.css">
</head>
<body>
  <main class="wrap">
    <section class="card" role="status" aria-live="polite">
      <div class="status" id="status">Temporary Notice</div>
      <h1 id="headline">This service is currently unavailable.</h1>
      <p id="message1">The site you requested is temporarily down for maintenance.</p>
    </section>
  </main>
  <script src="index.js"></script>
</body>
</html>
"""


def test_looks_like_error_page_detects_iem_block_page():
    assert http.looks_like_error_page(IEM_BLOCK_PAGE) is True


def test_looks_like_error_page_detects_generic_html_doc():
    assert http.looks_like_error_page("<!DOCTYPE HTML><html><body>oops</body></html>") is True


def test_looks_like_error_page_detects_html_after_leading_whitespace():
    assert http.looks_like_error_page("\n\n   <html><body>oops</body></html>") is True


def test_looks_like_error_page_matches_marker_anywhere_in_body():
    # A page that does not start with a doctype but still announces itself.
    assert http.looks_like_error_page("<div>This service is currently unavailable</div>") is True


def test_looks_like_error_page_accepts_plain_text_product():
    product = "WTPZ31 KNHC 220900\nTCPEP1\n\nBULLETIN\nHURRICANE FAUSTO ADVISORY NUMBER 14\n"
    assert http.looks_like_error_page(product) is False


def test_looks_like_error_page_accepts_json_payload():
    assert http.looks_like_error_page('{"messages": [], "seqnum": 12}') is False


def test_looks_like_error_page_falsy_inputs():
    assert http.looks_like_error_page(None) is False
    assert http.looks_like_error_page("") is False


# ── Egress proxy pool ─────────────────────────────────────────────────────────


@pytest.fixture
def pool_state():
    """Snapshot/restore the module-level router so tests don't leak config."""
    saved_urls = http._proxy_pool.urls
    saved_hosts = set(http._proxy_hosts)
    saved_retry = http._direct_retry_seconds
    saved_unhealthy = dict(http._direct_unhealthy_until)
    yield
    http._direct_unhealthy_until.clear()
    http._direct_unhealthy_until.update(saved_unhealthy)
    http.configure_proxy_pool(urls=saved_urls, hosts=saved_hosts, direct_retry_seconds=saved_retry)


class TestProxyPool:
    def test_disabled_pool_returns_none(self):
        pool = http.ProxyPool([])
        assert pool.enabled is False
        assert pool.acquire() is None

    def test_round_robin_rotates_across_every_proxy(self):
        pool = http.ProxyPool(["http://a:1", "http://b:2", "http://c:3"])
        seen = [pool.acquire() for _ in range(6)]
        assert set(seen) == {"http://a:1", "http://b:2", "http://c:3"}
        # Strict rotation: proxy 1 follows proxy 3, wraps back to 1, etc.
        assert seen == [
            "http://a:1",
            "http://b:2",
            "http://c:3",
            "http://a:1",
            "http://b:2",
            "http://c:3",
        ]

    def test_failure_cools_down_that_proxy_only(self):
        clock = [0.0]
        pool = http.ProxyPool(["http://a:1", "http://b:2"], cooldown=60, clock=lambda: clock[0])
        pool.acquire()
        pool.report_failure("http://b:2")
        # b is out of rotation, so a is handed out every time.
        assert [pool.acquire() for _ in range(4)] == ["http://a:1"] * 4

    def test_cooldown_expires_and_proxy_rejoins_rotation(self):
        clock = [0.0]
        pool = http.ProxyPool(["http://a:1", "http://b:2"], cooldown=60, clock=lambda: clock[0])
        pool.report_failure("http://b:2")
        assert pool.acquire() == "http://a:1"
        clock[0] = 61.0
        assert set(pool.acquire() for _ in range(4)) == {"http://a:1", "http://b:2"}

    def test_success_clears_cooldown_early(self):
        clock = [0.0]
        pool = http.ProxyPool(["http://a:1", "http://b:2"], cooldown=60, clock=lambda: clock[0])
        pool.report_failure("http://b:2")
        pool.report_success("http://b:2")
        assert set(pool.acquire() for _ in range(2)) == {"http://a:1", "http://b:2"}

    def test_all_cooling_down_still_returns_a_proxy(self):
        pool = http.ProxyPool(["http://a:1", "http://b:2"], cooldown=60)
        pool.report_failure("http://a:1")
        pool.report_failure("http://b:2")
        assert pool.acquire() is not None

    def test_warns_once_per_incident_not_per_failure(self, _spc_caplog):
        caplog = _spc_caplog
        pool = http.ProxyPool(["http://a:1"], cooldown=60)
        with caplog.at_level("WARNING"):
            pool.report_failure("http://a:1")
            pool.report_failure("http://a:1")
            pool.report_failure("http://a:1")
        assert sum("cooling down" in r.getMessage() for r in caplog.records) == 1

    def test_proxy_label_never_includes_credentials(self):
        assert http._proxy_label("http://user:secret@192.0.2.1:3129") == "192.0.2.1:3129"
        assert http._proxy_label(None) == "direct"


class TestProxyForUrl:
    """Routing rules: direct always wins until the direct path proves it is
    being refused, and only for hosts inside the configured scope."""

    def _enable(self, hosts=None):
        http.configure_proxy_pool(
            urls=["http://p1:3129", "http://p2:3129"],
            hosts=hosts if hosts is not None else {"mesonet.agron.iastate.edu"},
        )

    def test_default_is_direct_even_with_a_pool_configured(self, pool_state):
        self._enable()
        assert http.proxy_for_url("https://mesonet.agron.iastate.edu/x") is None
        assert http.proxy_for_url("https://api.weather.gov/") is None
        assert http.proxy_for_url("https://nomad.openstreetmap.org/search?q=x") is None

    def test_marked_host_routes_to_the_pool(self, pool_state):
        self._enable()
        http.mark_direct_unhealthy("mesonet.agron.iastate.edu")
        assert http.proxy_for_url("https://mesonet.agron.iastate.edu/x") is not None

    def test_unlisted_host_never_uses_a_proxy(self, pool_state):
        self._enable()
        # Even a host that is actively being blocked stays direct if it isn't
        # in the allowlist — we only proxy the products we opted in for.
        http.mark_direct_unhealthy("api.weather.gov")
        assert http.proxy_for_url("https://api.weather.gov/") is None

    def test_subdomain_of_configured_host_is_not_routed(self, pool_state):
        self._enable()
        http.mark_direct_unhealthy("mesonet.agron.iastate.edu")
        assert http.proxy_for_url("https://sub.mesonet.agron.iastate.edu/x") is None

    def test_no_urls_means_direct(self, pool_state):
        http.configure_proxy_pool(urls=[], hosts={"mesonet.agron.iastate.edu"})
        http.mark_direct_unhealthy("mesonet.agron.iastate.edu")
        assert http.proxy_for_url("https://mesonet.agron.iastate.edu/") is None

    def test_window_expires_and_traffic_returns_to_direct(self, pool_state, monkeypatch):
        self._enable()
        http.configure_proxy_pool(direct_retry_seconds=60)
        clock = [1000.0]
        monkeypatch.setattr(http, "_clock", lambda: clock[0])

        assert http.proxy_for_url("https://mesonet.agron.iastate.edu/x") is None
        http.mark_direct_unhealthy("mesonet.agron.iastate.edu")
        assert http.proxy_for_url("https://mesonet.agron.iastate.edu/x") is not None

        clock[0] = 1061.0  # window lapsed → probe our own IP again
        assert http.proxy_for_url("https://mesonet.agron.iastate.edu/x") is None
        assert "mesonet.agron.iastate.edu" not in http._direct_unhealthy_until

    def test_successful_direct_clears_the_mark_early(self, pool_state):
        self._enable()
        http.mark_direct_unhealthy("mesonet.agron.iastate.edu")
        http.mark_direct_healthy("mesonet.agron.iastate.edu")
        assert http.proxy_for_url("https://mesonet.agron.iastate.edu/x") is None

    def test_netloc_and_hostname_map_to_the_same_record(self, pool_state):
        self._enable()
        # mark() may be handed a host:port from a call site, proxy_for_url()
        # hands us a bare hostname — they must agree.
        http.mark_direct_unhealthy("mesonet.agron.iastate.edu:443")
        assert http.proxy_for_url("https://mesonet.agron.iastate.edu/x") is not None

    def test_failure_evidence_separates_blocking_from_bad_ids(self, pool_state):
        assert http._direct_failure_evidence(403, None) is True
        assert http._direct_failure_evidence(503, None) is True
        # A wrong product id is the caller's fault, not our egress.
        assert http._direct_failure_evidence(404, None) is False
        # A slow upstream must not send us into proxy mode for ten minutes.
        assert http._direct_failure_evidence(None, asyncio.TimeoutError()) is False
        assert (
            http._direct_failure_evidence(
                None, aiohttp.ClientConnectorError(MagicMock(), OSError(111, "Connection refused"))
            )
            is True
        )

    def test_note_proxy_failure_ignores_target_side_errors(self, pool_state):
        self._enable()
        http.mark_direct_unhealthy("mesonet.agron.iastate.edu")
        proxy = http.proxy_for_url("https://mesonet.agron.iastate.edu/")
        # A 404 from IEM is the target's problem, not the proxy's.
        http._note_proxy_failure(proxy, aiohttp.ClientResponseError(MagicMock(), (), status=404))
        assert "http://p1:3129" not in http._proxy_pool._failures

    def test_note_proxy_failure_marks_proxy_on_407(self, pool_state):
        self._enable()
        http.mark_direct_unhealthy("mesonet.agron.iastate.edu")
        proxy = http.proxy_for_url("https://mesonet.agron.iastate.edu/")
        http._note_proxy_failure(proxy, aiohttp.ClientResponseError(MagicMock(), (), status=407))
        assert "http://p1:3129" in http._proxy_pool._failures

    def test_scrub_strips_credentials_from_log_text(self):
        secret = "http://user:hunter2@192.0.2.1:3129"
        scrubbed = http._scrub(f"Cannot connect via {secret}")
        assert "hunter2" not in scrubbed
        assert "user" not in scrubbed
        assert "192.0.2.1:3129" in scrubbed


# ── Direct-first fetch with proxy fallback ───────────────────────────────────

_IEM = "https://mesonet.agron.iastate.edu/api/1/nwstext/1"


def _session_capturing(responses, seen):
    """Mock session recording the `proxy` kwarg of every `get(...)`."""
    it = iter(responses)
    session = MagicMock()

    def _get(url, **kwargs):
        seen.append(kwargs.get("proxy"))
        return next(it)

    session.get = MagicMock(side_effect=_get)
    session.closed = False
    return session


class TestDirectFirstFallback:
    def _enable(self):
        http.configure_proxy_pool(
            urls=["http://p1:3129", "http://p2:3129"],
            hosts={"mesonet.agron.iastate.edu"},
        )

    async def test_healthy_endpoint_stays_on_our_own_ip(self, pool_state):
        self._enable()
        seen = []
        session = _session_capturing([_MockResponse(200, b"WTPZ31 KNHC 220900")], seen)
        with patch("utils.http.ensure_session", AsyncMock(return_value=session)):
            content, status, _ = await http.http_get_bytes_conditional(_IEM, retries=1)
        assert (content, status) == (b"WTPZ31 KNHC 220900", 200)
        assert seen == [None], "healthy fetch must not touch the proxy pool"
        assert http.proxy_for_url(_IEM) is None

    async def test_block_page_triggers_fallback_and_serves_the_real_product(self, pool_state):
        self._enable()
        seen = []
        session = _session_capturing(
            [_MockResponse(200, IEM_BLOCK_PAGE.encode()), _MockResponse(200, b"real product")],
            seen,
        )
        with patch("utils.http.ensure_session", AsyncMock(return_value=session)):
            content, status, _ = await http.http_get_bytes_conditional(_IEM, retries=1)
        assert (content, status) == (b"real product", 200)
        assert seen == [None, "http://p1:3129"], "direct first, then proxy"
        # Host is now sticky: the next request skips direct entirely.
        assert http.proxy_for_url(_IEM) is not None

    async def test_unlisted_host_is_never_rerouted(self, pool_state):
        """SPC/NWS endpoints serve HTML too — we must not read that as a block."""
        self._enable()
        seen = []
        session = _session_capturing([_MockResponse(200, IEM_BLOCK_PAGE.encode())], seen)
        with patch("utils.http.ensure_session", AsyncMock(return_value=session)):
            content, status, _ = await http.http_get_bytes_conditional(
                "https://www.spc.noaa.gov/products/md/", retries=1
            )
        assert content and status == 200
        assert seen == [None]

    async def test_transient_failure_falls_back_without_sticking(self, pool_state):
        """A slow upstream still gets one proxied retry, but a timeout alone is
        not evidence our egress is refused — so the host does not go into
        proxy mode for ten minutes."""
        self._enable()
        seen = []
        session = MagicMock()

        def _get(url, **kwargs):
            seen.append(kwargs.get("proxy"))
            if len(seen) == 1:
                raise asyncio.TimeoutError()
            return _MockResponse(200, b"recovered")

        session.get = MagicMock(side_effect=_get)
        session.closed = False
        with patch("utils.http.ensure_session", AsyncMock(return_value=session)), patch(
            "utils.http.asyncio.sleep", AsyncMock()
        ):
            content, status, _ = await http.http_get_bytes_conditional(_IEM, retries=1)
        assert (content, status) == (b"recovered", 200)
        assert seen == [None, "http://p1:3129"]
        # Not sticky → the next request tries our own IP again.
        assert http.proxy_for_url(_IEM) is None

    async def test_marked_host_skips_the_direct_attempt(self, pool_state):
        self._enable()
        http.mark_direct_unhealthy("mesonet.agron.iastate.edu")
        seen = []
        session = _session_capturing([_MockResponse(200, b"real product")], seen)
        with patch("utils.http.ensure_session", AsyncMock(return_value=session)):
            content, _, _ = await http.http_get_bytes_conditional(_IEM, retries=1)
        assert content == b"real product"
        assert seen == ["http://p1:3129"], "must not waste a direct round trip"
