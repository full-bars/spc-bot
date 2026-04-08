# utils/http.py
import asyncio
import logging
from typing import Dict, Optional, Tuple

import aiohttp

logger = logging.getLogger("spc_bot")

http_session: Optional[aiohttp.ClientSession] = None
_session_lock = asyncio.Lock()


async def ensure_session() -> aiohttp.ClientSession:
    global http_session
    async with _session_lock:
        if http_session is None or http_session.closed:
            connector = aiohttp.TCPConnector(limit=20, limit_per_host=10)
            http_session = aiohttp.ClientSession(connector=connector)
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
    """Extract Retry-After from response headers, if present."""
    val = response.headers.get("Retry-After")
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


async def http_get_bytes(
    url: str, retries: int = 3, timeout: int = 10
) -> Tuple[Optional[bytes], Optional[int]]:
    for attempt in range(retries):
        try:
            session = await ensure_session()
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as response:
                # Respect Retry-After on 429 or 503
                if response.status in (429, 503):
                    retry_after = _get_retry_after(response) or (2**attempt)
                    retry_after = min(retry_after, 60)  # cap at 60s
                    logger.warning(
                        f"Rate limited on {url} (status={response.status}), "
                        f"waiting {retry_after:.1f}s (attempt {attempt + 1}/{retries})"
                    )
                    await asyncio.sleep(retry_after)
                    continue
                content = await response.read()
                return content, response.status
        except Exception as e:
            logger.warning(
                f"Error fetching {url} (attempt {attempt + 1}/{retries}): "
                f"{type(e).__name__}: {e}"
            )
            await asyncio.sleep(2**attempt)
    return None, None


async def http_get_text(
    url: str, retries: int = 3, timeout: int = 10
) -> Optional[str]:
    content, status = await http_get_bytes(url, retries=retries, timeout=timeout)
    if content and status == 200:
        return content.decode("utf-8", errors="ignore")
    return None


async def http_head_ok(url: str, timeout: int = 5) -> bool:
    try:
        session = await ensure_session()
        async with session.head(
            url, timeout=aiohttp.ClientTimeout(total=timeout)
        ) as r:
            return r.status == 200
    except Exception as e:
        logger.warning(f"HEAD check failed for {url}: {type(e).__name__}: {e}")
        try:
            content, status = await http_get_bytes(url, retries=1, timeout=timeout)
            return status == 200
        except Exception:
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
