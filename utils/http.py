# utils/http.py
import asyncio
import logging
import time
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

import aiohttp
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger("spc_bot")

# Named timeout presets (seconds) — use these at call sites instead of bare integers
TIMEOUT_FAST = 10       # Quick HEAD checks, small API calls
TIMEOUT_STANDARD = 15  # Most JSON endpoints
TIMEOUT_SLOW = 30       # Larger content, general GET

# Circuit breaker tuning — adjust these to change trip sensitivity globally
_CB_FAILURE_THRESHOLD = 5
_CB_RECOVERY_TIMEOUT = 60.0

http_session: Optional[aiohttp.ClientSession] = None
_session_lock = asyncio.Lock()


class CircuitOpenError(Exception):
    """Raised when the circuit breaker is open for a host."""
    pass


class CircuitBreaker:
    def __init__(self, failure_threshold: int = _CB_FAILURE_THRESHOLD, recovery_timeout: float = _CB_RECOVERY_TIMEOUT):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failures: Dict[str, int] = {}
        self.last_failure_time: Dict[str, float] = {}

    def record_success(self, host: str):
        if host in self.failures:
            logger.info(f"[CIRCUIT] {host} recovered. Closing circuit.")
            self.failures.pop(host, None)
            self.last_failure_time.pop(host, None)

    def record_failure(self, host: str):
        self.failures[host] = self.failures.get(host, 0) + 1
        self.last_failure_time[host] = time.time()
        if self.failures[host] == self.failure_threshold:
            logger.warning(f"[CIRCUIT] {host} reached {self.failure_threshold} failures. Circuit OPEN.")

    def is_open(self, host: str) -> bool:
        failures = self.failures.get(host, 0)
        if failures >= self.failure_threshold:
            # Check if recovery timeout has passed
            if time.time() - self.last_failure_time.get(host, 0) > self.recovery_timeout:
                logger.info(f"[CIRCUIT] {host} recovery timeout elapsed. Half-open circuit.")
                # Half-open: allow one request through to test
                self.failures[host] = self.failure_threshold - 1
                return False
            return True
        return False

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
                limit=20,
                limit_per_host=10,
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
) -> Tuple[Optional[bytes], Optional[int]]:
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
) -> Tuple[Optional[bytes], Optional[int], Optional[Dict[str, str]]]:
    parsed = urlparse(url)
    host = parsed.netloc

    if circuit_breaker.is_open(host):
        logger.warning(f"[HTTP] Circuit open for {host}, failing fast: {url}")
        raise CircuitOpenError(f"Circuit breaker is open for {host}")

    headers: Dict[str, str] = dict(extra_headers) if extra_headers else {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    # Use tenacity for retries
    retry_decorator = retry(
        stop=stop_after_attempt(retries),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
        reraise=True
    )

    async def _do_request():
        session = await ensure_session()
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=timeout),
            headers=headers or None,
        ) as response:
            if response.status in (429, 503, 502, 504):
                # Tenacity handles the backoff/retry; we just signal the failure
                raise aiohttp.ClientResponseError(
                    response.request_info,
                    response.history,
                    status=response.status,
                    message="Server returned retryable error"
                )
            
            if response.status == 304:
                return None, 304, {"etag": etag or "", "last_modified": last_modified or ""}
                
            response.raise_for_status() # Raise for 4xx/5xx
            
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
        status = getattr(e, 'status', None)
        if status is None or status >= 500 or status == 429:
            circuit_breaker.record_failure(host)
            
        logger.warning(f"[HTTP] Request failed for {url} after {retries} retries: {e}")
        return None, status, None


async def http_get_text(
    url: str, retries: int = 3, timeout: int = 30
) -> Optional[str]:
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
        async with session.head(
            url, timeout=aiohttp.ClientTimeout(total=timeout)
        ) as r:
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
        async with session.head(
            url, timeout=aiohttp.ClientTimeout(total=timeout)
        ) as r:
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


async def http_get_json(url: str, retries: int = 1, timeout: int = TIMEOUT_STANDARD) -> Optional[dict]:
    """Fetch JSON from a URL with retries and circuit breaker."""
    parsed = urlparse(url)
    host = parsed.netloc
    if circuit_breaker.is_open(host):
        return None

    retry_decorator = retry(
        stop=stop_after_attempt(retries + 1),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
        reraise=True,
    )

    async def _do_request():
        session = await ensure_session()
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            if r.status in (429, 502, 503, 504):
                raise aiohttp.ClientResponseError(
                    r.request_info, r.history, status=r.status, message="Server returned retryable error"
                )
            if r.status != 200:
                logger.warning(f"[HTTP] JSON fetch failed for {url}: {r.status}")
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
        logger.warning(f"[HTTP] JSON fetch error for {url}: {type(e).__name__}: {e}")
        return None
