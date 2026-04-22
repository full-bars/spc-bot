# utils/http.py
import asyncio
import logging
import random
from typing import Dict, Optional, Tuple

import aiohttp

logger = logging.getLogger("spc_bot")

http_session: Optional[aiohttp.ClientSession] = None
_session_lock = asyncio.Lock()


def _default_user_agent() -> str:
    # NWS/SPC require an identifying UA with contact info. Pulling the
    # version here keeps the string aligned with the release tag.
    # Local import with try/except: falls back to "dev" if config
    # import fails (e.g. during test collection without env vars).
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
            # ttl_dns_cache avoids re-resolving the same handful of NWS/SPC
            # hosts on every request; keepalive_timeout holds TCP/TLS
            # connections open across the 30s–2min poll cadence.
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


def _backoff_delay(attempt: int) -> float:
    """Exponential backoff with jitter.

    Parallel fetches that all 429 at once would otherwise retry in lockstep
    and re-trigger the rate limit. Full jitter spreads them out.
    """
    return random.uniform(0, 2 ** attempt)


def _get_retry_after(response: aiohttp.ClientResponse) -> Optional[float]:
    """Extract Retry-After from response headers, if present."""
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
    timeout: int = 10,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[Optional[bytes], Optional[int]]:
    """Unconditional GET. Thin wrapper over the conditional variant with
    no validators passed in, so the server cannot 304."""
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
    timeout: int = 10,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Tuple[Optional[bytes], Optional[int], Optional[Dict[str, str]]]:
    """Conditional GET. Returns (content, status, validators).

    - If the server returns 304, content is None and validators carries the
      prior values so callers can keep them.
    - On 200, validators carries the fresh ETag / Last-Modified for storage.
    """
    headers: Dict[str, str] = dict(extra_headers) if extra_headers else {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    for attempt in range(retries):
        try:
            session = await ensure_session()
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=timeout),
                headers=headers or None,
            ) as response:
                if response.status in (429, 503):
                    retry_after = _get_retry_after(response) or _backoff_delay(attempt)
                    retry_after = min(retry_after, 60)
                    logger.warning(
                        f"Rate limited on {url} (status={response.status}), "
                        f"waiting {retry_after:.1f}s (attempt {attempt + 1}/{retries})"
                    )
                    if attempt == retries - 1:
                        return None, response.status, None
                    await asyncio.sleep(retry_after)
                    continue
                if response.status == 304:
                    return None, 304, {"etag": etag or "", "last_modified": last_modified or ""}
                content = await response.read()
                validators = {
                    "etag": response.headers.get("ETag", ""),
                    "last_modified": response.headers.get("Last-Modified", ""),
                }
                return content, response.status, validators
        except Exception as e:
            logger.warning(
                f"Error fetching {url} (attempt {attempt + 1}/{retries}): "
                f"{type(e).__name__}: {e}"
            )
            if attempt == retries - 1:
                break
            await asyncio.sleep(_backoff_delay(attempt))
    return None, None, None


async def http_get_text(
    url: str, retries: int = 3, timeout: int = 10
) -> Optional[str]:
    content, status = await http_get_bytes(url, retries=retries, timeout=timeout)
    if content and status == 200:
        return content.decode("utf-8", errors="ignore")
    return None


async def http_head_ok(url: str, timeout: int = 5) -> bool:
    """Cheap liveness check. HEAD only; no full-GET fallback (that defeats the point)."""
    try:
        session = await ensure_session()
        async with session.head(
            url, timeout=aiohttp.ClientTimeout(total=timeout)
        ) as r:
            return r.status == 200
    except Exception as e:
        logger.warning(f"HEAD check failed for {url}: {type(e).__name__}: {e}")
        return False


async def http_head_meta(url: str, timeout: int = 5) -> Optional[Dict[str, str]]:
    try:
        session = await ensure_session()
        async with session.head(
            url, timeout=aiohttp.ClientTimeout(total=timeout)
        ) as r:
            if r.status != 200:
                return None
            return {
                "etag": r.headers.get("ETag", ""),
                "last_modified": r.headers.get("Last-Modified", ""),
                "content_length": r.headers.get("Content-Length", ""),
            }
    except Exception as e:
        logger.warning(f"HEAD meta failed for {url}: {type(e).__name__}: {e}")
        return None
