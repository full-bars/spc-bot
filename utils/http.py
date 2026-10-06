# utils/http.py
import asyncio
import logging
import re
import time
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

import aiohttp
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger("spc_bot")

# ---------------------------------------------------------------------------
# Module-level retry decorator cache.
# Building a tenacity retry(...) object is non-trivial (it compiles several
# strategy objects and wraps the callable).  Constructing one on every HTTP
# call adds measurable overhead on high-frequency polling paths.  We cache
# decorators keyed by attempt-count so the common cases (retries=1, 2, 3)
# pay the construction cost exactly once at import time.
# ---------------------------------------------------------------------------
_RETRY_EXCEPTIONS = (aiohttp.ClientError, asyncio.TimeoutError)
_RETRY_WAIT = wait_exponential(multiplier=1, min=1, max=10)


def _make_retry_decorator(attempts: int):
    return retry(
        stop=stop_after_attempt(attempts),
        wait=_RETRY_WAIT,
        retry=retry_if_exception_type(_RETRY_EXCEPTIONS),
        reraise=True,
    )


# Pre-build for the default attempt counts used across this module.
_RETRY_CACHE: dict = {n: _make_retry_decorator(n) for n in (1, 2, 3, 4)}

_RETRY_CACHE_MAX = 16


def _get_retry_decorator(attempts: int):
    """Return a cached retry decorator for *attempts*, building one if needed."""
    if attempts not in _RETRY_CACHE:
        if len(_RETRY_CACHE) >= _RETRY_CACHE_MAX:
            _RETRY_CACHE.pop(next(iter(_RETRY_CACHE)))
        _RETRY_CACHE[attempts] = _make_retry_decorator(attempts)
    return _RETRY_CACHE[attempts]


# Upstreams under maintenance or rate-limiting serve a static placeholder page
# on a 200/302 instead of failing loudly, so `status == 200` alone does not
# prove we got the payload we asked for. Markers are matched
# case-insensitively anywhere in the body.
_ERROR_PAGE_MARKERS = (
    "iowamesonet.github.io/sorry",
    "<title>service notice</title>",
    "this service is currently unavailable",
)


def looks_like_error_page(text: Optional[str]) -> bool:
    """True when *text* is an HTML placeholder/error page rather than the
    plain-text payload the caller expected.

    A bare ``status == 200`` check is not enough: a blocked or down endpoint
    can redirect (HTTP 302, followed by the client) to a static page and still
    come back as 200. Feeding that page to a text parser produces garbage —
    or worse, posts it verbatim to Discord.
    """
    if not text:
        return False
    lowered = text.lower()
    if any(marker in lowered for marker in _ERROR_PAGE_MARKERS):
        return True
    head = lowered.lstrip()[:256]
    return head.startswith("<!doctype html") or head.startswith("<html")


# Named timeout presets (seconds) — use these at call sites instead of bare integers
TIMEOUT_FAST = 10  # Quick HEAD checks, small API calls
TIMEOUT_STANDARD = 15  # Most JSON endpoints
TIMEOUT_SLOW = 30  # Larger content, general GET


# ── Optional egress proxy pool ───────────────────────────────────────────────
# Some upstreams (IEM) block our own IP. When a pool is configured, requests
# for those hosts are round-robined across it so no single egress address
# absorbs all the traffic — and a proxy that starts failing is pulled out of
# rotation instead of failing the request outright.

_PROXY_FAILURE_TYPES = (aiohttp.ClientConnectionError, asyncio.TimeoutError)


def _proxy_label(proxy: Optional[str]) -> str:
    """host:port for a proxy URL — never the credentials."""
    if not proxy:
        return "direct"
    return urlparse(proxy).netloc.rsplit("@")[-1]


_CREDENTIAL_RE = re.compile(r"//[^/\s@]+:[^/\s@]+@")


def _scrub(text: str) -> str:
    """Strip ``user:pass@`` credentials before a string reaches the log."""
    return _CREDENTIAL_RE.sub("//***:***@", text)


class ProxyPool:
    """Round-robin pool of HTTP proxies with a per-proxy cooldown."""

    def __init__(self, urls=(), cooldown: float = 60.0, clock=None):
        self._urls = [u for u in urls if u]
        self._cooldown = cooldown
        self._clock = clock or time.monotonic
        self._failures: Dict[str, float] = {}
        self._warned: set = set()
        self._cursor = 0

    @property
    def enabled(self) -> bool:
        return bool(self._urls)

    @property
    def size(self) -> int:
        return len(self._urls)

    @property
    def urls(self) -> list:
        return list(self._urls)

    @property
    def cooldown(self) -> float:
        return self._cooldown

    def acquire(self) -> Optional[str]:
        if not self._urls:
            return None
        now = self._clock()
        for url in [u for u, ts in self._failures.items() if now - ts >= self._cooldown]:
            del self._failures[url]

        n = len(self._urls)
        for _ in range(n):
            candidate = self._urls[self._cursor % n]
            self._cursor += 1
            if candidate not in self._failures:
                return candidate
        # Everything is cooling down — keep using the least-recently failed
        # one rather than hard-failing the request.
        return min(self._urls, key=lambda u: self._failures.get(u, float("-inf")))

    def report_failure(self, proxy: str) -> None:
        first = proxy not in self._warned
        self._failures[proxy] = self._clock()
        if first:
            self._warned.add(proxy)
            logger.warning(
                f"Proxy {_proxy_label(proxy)} failed — cooling down "
                f"for {self._cooldown:.0f}s ({self.size} in pool)"
            )
        else:
            logger.debug(f"Proxy {_proxy_label(proxy)} failed again — still cooling down")

    def report_success(self, proxy: str) -> None:
        self._failures.pop(proxy, None)
        self._warned.discard(proxy)


_proxy_pool: ProxyPool = ProxyPool()
_proxy_hosts: set = set()
_direct_retry_seconds: float = 600.0

# Hosts whose DIRECT path is currently known-bad → we skip the direct attempt
# and go straight to the proxy until the window lapses, then probe direct
# again. This keeps normal traffic on our own IP and only routes through the
# pool while a host is actually refusing us.
_direct_unhealthy_until: Dict[str, float] = {}
_clock = time.monotonic

# Statuses that mean "our egress is being refused", not "bad product id".
_BLOCK_STATUS = {403, 406, 429, 503}


def configure_proxy_pool(
    urls=None,
    hosts=None,
    cooldown: Optional[float] = None,
    direct_retry_seconds: Optional[float] = None,
) -> None:
    """(Re)build the module-level router. Used by config at import time and by
    tests; passing ``urls=[]`` disables proxying entirely."""
    global _proxy_pool, _proxy_hosts, _direct_retry_seconds
    if hosts is not None:
        _proxy_hosts = set(hosts)
    if direct_retry_seconds is not None:
        _direct_retry_seconds = direct_retry_seconds
    if urls is not None or cooldown is not None:
        current = _proxy_pool
        _proxy_pool = ProxyPool(
            urls if urls is not None else current.urls,
            cooldown=cooldown if cooldown is not None else current.cooldown,
        )


def _eligible_for_proxy(url: str) -> bool:
    """Is this host in the configured proxy scope at all?"""
    return _proxy_pool.enabled and urlparse(url).hostname in _proxy_hosts


def _normalize_host(host: str) -> str:
    """Callers hand us either a netloc (`host:port`) or a bare hostname —
    normalise so both map to the same direct-health record."""
    return host.partition(":")[0]


def _direct_is_unhealthy(host: str) -> bool:
    host = _normalize_host(host)
    until = _direct_unhealthy_until.get(host)
    if until is None:
        return False
    if _clock() >= until:
        # Window lapsed — probe our own IP again so we recover automatically
        # once the upstream stops blocking us.
        _direct_unhealthy_until.pop(host, None)
        return False
    return True


def mark_direct_unhealthy(host: str) -> None:
    host = _normalize_host(host)
    _direct_unhealthy_until[host] = _clock() + _direct_retry_seconds
    logger.warning(
        f"Direct path to {host} rejected — using proxy pool for {_direct_retry_seconds:.0f}s"
    )


def mark_direct_healthy(host: str) -> None:
    host = _normalize_host(host)
    if _direct_unhealthy_until.pop(host, None) is not None:
        logger.info(f"Direct path to {host} recovered — proxy fallback cleared")


def proxy_for_url(url: str) -> Optional[str]:
    """Proxy to use for *url*, or ``None`` for a direct connection.

    Direct is always preferred: a proxy is only handed out once the direct
    path for this host has actually failed (and only for hosts listed in
    ``IEM_PROXY_HOSTS``).
    """
    if not _eligible_for_proxy(url):
        return None
    if not _direct_is_unhealthy(urlparse(url).hostname or ""):
        return None
    return _proxy_pool.acquire()


def _fallback_proxy(url: str) -> Optional[str]:
    """A proxy for one failed attempt, sticky state notwithstanding.

    Used when a single direct request fails: we don't yet have proof the host
    is blocking us, so we don't put it into proxy mode — we just don't let the
    user-visible fetch fail either.
    """
    if not _eligible_for_proxy(url):
        return None
    return proxy_for_url(url) or _proxy_pool.acquire()


def _direct_failure_evidence(status: Optional[int], exc: Optional[BaseException]) -> bool:
    """Does this failure suggest our *egress* is refused, rather than the
    product id being wrong?  Timeouts on their own don't count — a slow
    upstream shouldn't put a host into proxy mode for ten minutes."""
    if status is not None and status in _BLOCK_STATUS:
        return True
    if exc is None:
        return False
    return isinstance(exc, (aiohttp.ClientConnectorError, aiohttp.ServerDisconnectedError))


def _note_proxy_failure(proxy: Optional[str], exc: BaseException) -> None:
    if not proxy:
        return
    if isinstance(exc, aiohttp.ClientResponseError):
        # 407 = the proxy rejected our credentials; any other response came
        # from the target, so the proxy did its job.
        if exc.status == 407:
            _proxy_pool.report_failure(proxy)
        return
    if isinstance(exc, _PROXY_FAILURE_TYPES):
        _proxy_pool.report_failure(proxy)


# Circuit breaker tuning — adjust these to change trip sensitivity globally
_CB_FAILURE_THRESHOLD = 10  # Require more proof of unavailability before tripping
_CB_RECOVERY_TIMEOUT = 90.0  # Give servers more time to recover before retry

_latency_callback = None


def set_latency_callback(cb):
    global _latency_callback
    _latency_callback = cb


http_session: Optional[aiohttp.ClientSession] = None
_session_lock = asyncio.Lock()


class CircuitOpenError(Exception):
    """Raised when the circuit breaker is open for a host."""

    pass


class CircuitBreaker:
    """Three-state breaker (CLOSED → OPEN → HALF_OPEN → CLOSED/OPEN).

    States are tracked per-host alongside the failure counter so:
      - "Circuit OPEN" only logs on the CLOSED→OPEN edge, not on every
        subsequent failure of an already-open host (was: re-logged after
        every half-open trial).
      - Only one request slips through during HALF_OPEN — concurrent
        callers see the host as still OPEN until the trial finishes
        and decides CLOSED or back to OPEN.
    """

    _STATE_CLOSED = "closed"
    _STATE_OPEN = "open"
    _STATE_HALF_OPEN = "half_open"

    def __init__(
        self,
        failure_threshold: int = _CB_FAILURE_THRESHOLD,
        recovery_timeout: float = _CB_RECOVERY_TIMEOUT,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failures: Dict[str, int] = {}
        self.last_failure_time: Dict[str, float] = {}
        self._state: Dict[str, str] = {}
        self._half_open_at: Dict[str, float] = {}

    def _get_state(self, host: str) -> str:
        return self._state.get(host, self._STATE_CLOSED)

    def record_success(self, host: str):
        prev_state = self._get_state(host)
        if prev_state != self._STATE_CLOSED:
            host_hash = hash(host) % 10000
            logger.info(f"Host recovered (#{host_hash}). Closing circuit (was {prev_state}).")
        self.failures.pop(host, None)
        self.last_failure_time.pop(host, None)
        self._state.pop(host, None)
        self._half_open_at.pop(host, None)

    def record_failure(self, host: str):
        self.failures[host] = self.failures.get(host, 0) + 1
        self.last_failure_time[host] = time.time()
        prev_state = self._get_state(host)
        host_hash = hash(host) % 10000

        if self.failures[host] >= self.failure_threshold:
            if prev_state == self._STATE_CLOSED:
                logger.warning(
                    f"Host #{host_hash} reached {self.failure_threshold} failures. Circuit OPEN. "
                    f"Will retry in {self.recovery_timeout}s."
                )
            elif prev_state == self._STATE_HALF_OPEN:
                # Trial request failed — back to OPEN without re-logging the
                # original threshold warning (already noisy enough).
                logger.info(f"Host #{host_hash} half-open trial failed. Circuit returning to OPEN.")
            self._state[host] = self._STATE_OPEN
        else:
            # Log progress toward circuit opening so we can see problems building
            remaining = self.failure_threshold - self.failures[host]
            logger.debug(
                f"Host #{host_hash} failure #{self.failures[host]}/{self.failure_threshold}, "
                f"{remaining} remaining before circuit opens"
            )

    def is_open(self, host: str) -> bool:
        state = self._get_state(host)
        if state == self._STATE_CLOSED:
            return False
        if state == self._STATE_HALF_OPEN:
            # A trial request is in flight — keep the gate shut for new
            # callers.  But if the trial was cancelled or errored without
            # ever calling record_success/record_failure, the host would
            # be locked out permanently.  A dead-man's switch reverts to
            # OPEN after 60s so a new trial can eventually proceed.
            half_open_age = time.time() - self._half_open_at.get(host, 0)
            if half_open_age > 60:
                host_hash = hash(host) % 10000
                logger.warning(
                    f"Host #{host_hash} half-open trial timed out "
                    f"({half_open_age:.0f}s).  Reverting to OPEN."
                )
                self._state[host] = self._STATE_OPEN
                # Fall through to the OPEN recovery-timeout check below.
            else:
                return True
        # OPEN: check if recovery timeout has elapsed.
        if time.time() - self.last_failure_time.get(host, 0) > self.recovery_timeout:
            host_hash = hash(host) % 10000
            logger.info(f"Host #{host_hash} recovery timeout elapsed. Half-open circuit.")
            self._state[host] = self._STATE_HALF_OPEN
            self._half_open_at[host] = time.time()
            return False
        return True


# Global circuit breaker
circuit_breaker = CircuitBreaker()


def _default_user_agent() -> str:
    try:
        from config import __version__  # noqa: PLC0415
    except Exception:
        __version__ = "dev"
    contact = "https://github.com/full-bars/spc-bot"
    return f"WxAlertSPCBot/{__version__} (+{contact})"


async def ensure_session() -> aiohttp.ClientSession:
    global http_session
    async with _session_lock:
        if http_session is None or http_session.closed:
            connector = aiohttp.TCPConnector(
                # Pool sizes raised from 20/10 to 100/25 so radar-frame
                # bursts, concurrent slash commands, and outbreak-time
                # warning images don't throttle on the connector before
                # they even hit the server. 25 per host is well below
                # what NWS API / IEM Autoplot will tolerate.
                limit=100,
                limit_per_host=25,
                ttl_dns_cache=300,
                keepalive_timeout=75,
            )
            http_session = aiohttp.ClientSession(
                connector=connector,
                headers={"User-Agent": _default_user_agent()},
            )
            logger.info("Created new aiohttp ClientSession")
    return http_session


async def close_session():
    global http_session
    async with _session_lock:
        if http_session and not http_session.closed:
            try:
                await http_session.close()
                logger.info("Closed aiohttp ClientSession")
            except Exception as e:
                logger.warning(f"Error closing session: {e}")
            http_session = None


def _get_retry_after(response: aiohttp.ClientResponse) -> Optional[float]:
    val = response.headers.get("Retry-After")
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


async def http_get_bytes(
    url: str,
    retries: int = 3,
    timeout: int = 30,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[Optional[bytes], int]:
    content, status, _ = await http_get_bytes_conditional(
        url,
        etag=None,
        last_modified=None,
        retries=retries,
        timeout=timeout,
        extra_headers=headers,
    )
    return content, status


async def http_get_bytes_conditional(
    url: str,
    etag: Optional[str] = None,
    last_modified: Optional[str] = None,
    retries: int = 3,
    timeout: int = 30,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Tuple[Optional[bytes], int, Optional[Dict[str, str]]]:
    parsed = urlparse(url)
    host = parsed.netloc

    if circuit_breaker.is_open(host):
        logger.debug(f"Circuit open for {host}, failing fast: {url}")
        raise CircuitOpenError(f"Circuit breaker is open for {host}")

    headers: Dict[str, str] = dict(extra_headers) if extra_headers else {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    # Use tenacity for retries — decorator is cached at module level.
    retry_decorator = _get_retry_decorator(retries)

    async def _do_request(proxy: Optional[str]):
        session = await ensure_session()
        start = time.perf_counter()
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=timeout),
                headers=headers or None,
                proxy=proxy,
            ) as response:
                latency = time.perf_counter() - start
                if _latency_callback:
                    try:
                        _latency_callback(latency, host=urlparse(url).hostname)
                    except TypeError:
                        # Legacy callback signature (latency-only) — preserve to
                        # avoid breaking external consumers that haven't migrated.
                        _latency_callback(latency)

                if response.status in (429, 503, 502, 504):
                    # Tenacity handles the backoff/retry; we just signal the failure
                    raise aiohttp.ClientResponseError(
                        response.request_info,
                        response.history,
                        status=response.status,
                        message="Server returned retryable error",
                    )

                if response.status == 304:
                    if proxy:
                        _proxy_pool.report_success(proxy)
                    return None, 304, {"etag": etag or "", "last_modified": last_modified or ""}

                response.raise_for_status()  # Raise for 4xx/5xx

                content = await response.read()
                validators = {
                    "etag": response.headers.get("ETag", ""),
                    "last_modified": response.headers.get("Last-Modified", ""),
                }
                if proxy:
                    _proxy_pool.report_success(proxy)
                return content, response.status, validators
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            _note_proxy_failure(proxy, exc)
            raise

    async def _attempt(proxy: Optional[str]):
        # Must stay an `async def`: a sync lambda would make tenacity wrap it
        # as a plain function and silently drop the retry loop.
        async def _run():
            return await _do_request(proxy)

        return await retry_decorator(_run)()

    def _is_placeholder(result) -> bool:
        """A 200 that isn't the product we asked for — the IEM block answers
        with a redirect to a static HTML "Service Notice". Anything eligible
        for proxy fallback should treat that as a hard failure."""
        content, status, _ = result
        if status == 304 or not content:
            return False
        return looks_like_error_page(content.decode("utf-8", "ignore"))

    def _note_failure(e) -> int:
        # Only record failure in the circuit breaker if it's a "hard" failure
        # (connection/timeout) or a server-side/rate-limit error (5xx, 429).
        # We DON'T trip the circuit on 404s or other user-side 4xx errors.
        status = getattr(e, "status", None) or 0
        if status == 0 or status >= 500 or status == 429:
            circuit_breaker.record_failure(host)
        return status

    def _give_up(status: int, e=None):
        if e is None:
            logger.warning(f"Request failed for {url} after {retries} retries: status {status}")
        else:
            logger.warning(f"Request failed for {url} after {retries} retries: {e}")
        return None, status, None

    # ── 1) Our own IP first ───────────────────────────────────────────────────
    # `proxy` is non-None only when the direct path is already known-bad for
    # this host; otherwise we always try direct and only bail out if it does
    # not actually return the product.
    proxy = proxy_for_url(url)
    if proxy is None:
        try:
            direct = await _attempt(None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            status = _note_failure(e)
            if _eligible_for_proxy(url):
                if _direct_failure_evidence(status, e):
                    mark_direct_unhealthy(host)
                proxy = _fallback_proxy(url)
            if proxy is None:
                return _give_up(status, e)
        else:
            if not _is_placeholder(direct):
                circuit_breaker.record_success(host)
                mark_direct_healthy(host)
                return direct
            # The body is a placeholder/block page: our egress is being refused,
            # not a bad product id. Only proxy-capable hosts can recover.
            if _eligible_for_proxy(url):
                mark_direct_unhealthy(host)
                proxy = _fallback_proxy(url)
            if proxy is None:
                return direct

    # ── 2) Same product, egressed through the pool ────────────────────────────
    try:
        result = await _attempt(proxy)
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        return _give_up(_note_failure(e), e)
    circuit_breaker.record_success(host)
    return result


async def http_get_text(url: str, retries: int = 3, timeout: int = 30) -> Optional[str]:
    try:
        content, status = await http_get_bytes(url, retries=retries, timeout=timeout)
        if content and status == 200:
            return content.decode("utf-8", errors="ignore")
    except CircuitOpenError:
        # Pass exception up so commands can catch it
        raise
    return None


async def http_head_ok(url: str, timeout: int = 20) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc
    if circuit_breaker.is_open(host):
        return False

    proxy = proxy_for_url(url)
    try:
        session = await ensure_session()
        async with session.head(
            url, timeout=aiohttp.ClientTimeout(total=timeout), proxy=proxy
        ) as r:
            success = r.status == 200
            if success:
                circuit_breaker.record_success(host)
                if proxy:
                    _proxy_pool.report_success(proxy)
                else:
                    mark_direct_healthy(host)
            else:
                if r.status >= 500 or r.status == 429:
                    circuit_breaker.record_failure(host)
                if proxy is None and _direct_failure_evidence(r.status, None):
                    mark_direct_unhealthy(host)
            return success
    except Exception as e:
        # Standard exceptions (timeout, conn error) always count as failure
        _note_proxy_failure(proxy, e)
        circuit_breaker.record_failure(host)
        if proxy is None and _eligible_for_proxy(url) and _direct_failure_evidence(None, e):
            mark_direct_unhealthy(host)
        logger.warning(
            f"HEAD check failed for {url.split('?')[0]}: {type(e).__name__}: {_scrub(str(e))}"
        )
        return False


async def http_head_meta(url: str, timeout: int = 20) -> Optional[Dict[str, str]]:
    parsed = urlparse(url)
    host = parsed.netloc
    if circuit_breaker.is_open(host):
        return None

    proxy = proxy_for_url(url)
    try:
        session = await ensure_session()
        async with session.head(
            url, timeout=aiohttp.ClientTimeout(total=timeout), proxy=proxy
        ) as r:
            if r.status != 200:
                if r.status >= 500 or r.status == 429:
                    circuit_breaker.record_failure(host)
                if proxy is None and _direct_failure_evidence(r.status, None):
                    mark_direct_unhealthy(host)
                return None
            circuit_breaker.record_success(host)
            if proxy:
                _proxy_pool.report_success(proxy)
            else:
                mark_direct_healthy(host)
            return {
                "etag": r.headers.get("ETag", ""),
                "last_modified": r.headers.get("Last-Modified", ""),
                "content_length": r.headers.get("Content-Length", ""),
            }
    except Exception as e:
        _note_proxy_failure(proxy, e)
        circuit_breaker.record_failure(host)
        if proxy is None and _eligible_for_proxy(url) and _direct_failure_evidence(None, e):
            mark_direct_unhealthy(host)
        logger.warning(
            f"HEAD meta failed for {url.split('?')[0]}: {type(e).__name__}: {_scrub(str(e))}"
        )
        return None


async def http_get_json(
    url: str, retries: int = 1, timeout: int = TIMEOUT_STANDARD
) -> Optional[dict]:
    """Fetch JSON from a URL with retries and circuit breaker."""
    parsed = urlparse(url)
    host = parsed.netloc
    if circuit_breaker.is_open(host):
        return None

    # Decorator is cached at module level; avoid rebuilding on every call.
    retry_decorator = _get_retry_decorator(retries + 1)

    async def _do_request():
        session = await ensure_session()
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            if r.status in (429, 500, 502, 503, 504):
                raise aiohttp.ClientResponseError(
                    r.request_info,
                    r.history,
                    status=r.status,
                    message="Server returned retryable error",
                )
            if r.status != 200:
                logger.warning(f"JSON fetch failed for {url.split('?')[0]}: {r.status}")
                # Only trip the circuit on server-side / rate-limit errors.
                # 4xx responses (including the 404s IEM emits during
                # availability probing) are expected and should not open
                # the circuit for the entire host.
                if r.status >= 500 or r.status == 429:
                    circuit_breaker.record_failure(host)
                return None
            circuit_breaker.record_success(host)
            return await r.json()

    try:
        return await retry_decorator(_do_request)()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        status = getattr(e, "status", None)
        if status is None or status >= 500 or status == 429:
            circuit_breaker.record_failure(host)
        logger.warning(f"JSON fetch error for {url.split('?')[0]}: {type(e).__name__}: {e}")
        return None


async def http_post_json(
    url: str,
    json_data: dict,
    retries: int = 1,
    timeout: int = TIMEOUT_STANDARD,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Optional[dict]:
    """POST JSON to a URL with retries and circuit breaker."""
    parsed = urlparse(url)
    host = parsed.netloc
    if circuit_breaker.is_open(host):
        return None

    retry_decorator = _get_retry_decorator(retries + 1)

    async def _do_request():
        session = await ensure_session()
        async with session.post(
            url,
            json=json_data,
            timeout=aiohttp.ClientTimeout(total=timeout),
            headers=extra_headers or None,
        ) as r:
            if r.status in (429, 500, 502, 503, 504):
                raise aiohttp.ClientResponseError(
                    r.request_info,
                    r.history,
                    status=r.status,
                    message="Server returned retryable error",
                )
            if r.status != 200:
                text = await r.text()
                logger.warning(f"JSON POST failed for {url}: {r.status} {text}")
                if r.status >= 500 or r.status == 429:
                    circuit_breaker.record_failure(host)
                return None
            circuit_breaker.record_success(host)
            return await r.json()

    try:
        return await retry_decorator(_do_request)()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        status = getattr(e, "status", None)
        if status is None or status >= 500 or status == 429:
            circuit_breaker.record_failure(host)
        logger.warning(f"JSON POST error for {url}: {type(e).__name__}: {e}")
        return None


# Config lives at the bottom so this module stays importable on its own (tests,
# scripts) without dragging in the whole config module.
from config import IEM_DIRECT_RETRY_SECONDS as _DEFAULT_DIRECT_RETRY  # noqa: E402
from config import IEM_PROXY_COOLDOWN as _DEFAULT_PROXY_COOLDOWN  # noqa: E402
from config import IEM_PROXY_HOSTS as _DEFAULT_PROXY_HOSTS  # noqa: E402
from config import IEM_PROXY_URLS as _DEFAULT_PROXY_URLS  # noqa: E402

configure_proxy_pool(
    urls=_DEFAULT_PROXY_URLS,
    hosts=_DEFAULT_PROXY_HOSTS,
    cooldown=_DEFAULT_PROXY_COOLDOWN,
    direct_retry_seconds=_DEFAULT_DIRECT_RETRY,
)
