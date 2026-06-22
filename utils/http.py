# utils/http.py
import asyncio
import logging
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


# Named timeout presets (seconds) — use these at call sites instead of bare integers
TIMEOUT_FAST = 10  # Quick HEAD checks, small API calls
TIMEOUT_STANDARD = 15  # Most JSON endpoints
TIMEOUT_SLOW = 30  # Larger content, general GET

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
            # Trial already in flight from another caller — keep the gate shut
            # for everyone else until that request resolves.
            return True
        # OPEN: check if recovery timeout has elapsed.
        if time.time() - self.last_failure_time.get(host, 0) > self.recovery_timeout:
            host_hash = hash(host) % 10000
            logger.info(f"Host #{host_hash} recovery timeout elapsed. Half-open circuit.")
            self._state[host] = self._STATE_HALF_OPEN
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

    async def _do_request():
        session = await ensure_session()
        start = time.perf_counter()
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=timeout),
            headers=headers or None,
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
                return None, 304, {"etag": etag or "", "last_modified": last_modified or ""}

            response.raise_for_status()  # Raise for 4xx/5xx

            content = await response.read()
            validators = {
                "etag": response.headers.get("ETag", ""),
                "last_modified": response.headers.get("Last-Modified", ""),
            }
            return content, response.status, validators

    try:
        result = await retry_decorator(_do_request)()
        circuit_breaker.record_success(host)
        return result
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        # Only record failure in the circuit breaker if it's a "hard" failure
        # (connection/timeout) or a server-side/rate-limit error (5xx, 429).
        # We DON'T trip the circuit on 404s or other user-side 4xx errors.
        status: int = getattr(e, "status", None) or 0
        if status == 0 or status >= 500 or status == 429:
            circuit_breaker.record_failure(host)

        logger.warning(f"Request failed for {url} after {retries} retries: {e}")
        return None, status, None


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

    try:
        session = await ensure_session()
        async with session.head(url, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            success = r.status == 200
            if success:
                circuit_breaker.record_success(host)
            elif r.status >= 500 or r.status == 429:
                circuit_breaker.record_failure(host)
            return success
    except Exception as e:
        # Standard exceptions (timeout, conn error) always count as failure
        circuit_breaker.record_failure(host)
        logger.warning(f"HEAD check failed for {url}: {type(e).__name__}: {e}")
        return False


async def http_head_meta(url: str, timeout: int = 20) -> Optional[Dict[str, str]]:
    parsed = urlparse(url)
    host = parsed.netloc
    if circuit_breaker.is_open(host):
        return None

    try:
        session = await ensure_session()
        async with session.head(url, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            if r.status != 200:
                if r.status >= 500 or r.status == 429:
                    circuit_breaker.record_failure(host)
                return None
            circuit_breaker.record_success(host)
            return {
                "etag": r.headers.get("ETag", ""),
                "last_modified": r.headers.get("Last-Modified", ""),
                "content_length": r.headers.get("Content-Length", ""),
            }
    except Exception as e:
        circuit_breaker.record_failure(host)
        logger.warning(f"HEAD meta failed for {url}: {type(e).__name__}: {e}")
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
                logger.warning(f"JSON fetch failed for {url}: {r.status}")
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
        logger.warning(f"JSON fetch error for {url}: {type(e).__name__}: {e}")
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
