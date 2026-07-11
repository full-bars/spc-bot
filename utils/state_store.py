"""
utils/state_store.py — Shared state backed by a local Redis instance with
a SQLite mirror for durability and outage survival.

Architecture
============

    ┌────────────────────────────────────────────────────┐
    │ Application code (cogs, main.setup_hook, …)        │
    └─────────────────────────┬──────────────────────────┘
                              │
                              ▼
    ┌────────────────────────────────────────────────────┐
    │ StateStore facade  — utils.state_store             │
    │                                                    │
    │  ┌───────────────────────────────────────────────┐ │
    │  │ In-process cache (dict w/ per-entry expiry)   │ │
    │  └───────┬─────────────────────────┬─────────────┘ │
    │          ▼                         ▼               │
    │  ┌──────────────────┐      ┌──────────────────┐    │
    │  │ Redis (TCP)      │      │ SQLite (local)   │    │
    │  │ source of truth  │      │ durable mirror   │    │
    │  └──────────────────┘      └──────────────────┘    │
    │          ▲                         ▲               │
    │          └─────────────┬───────────┘               │
    │                        │                           │
    │  ┌─────────────────────┴────────────────────────┐  │
    │  │ Reconciler — retries writes that failed      │  │
    │  │ to Redis by scanning a dirty-key set.        │  │
    │  └──────────────────────────────────────────────┘  │
    └────────────────────────────────────────────────────┘

Public API
==========

Call-compatible drop-in for utils.db. Every function that existed in
utils.db (get_hash, set_hash, add_posted_md, get_posted_mds, set_state,
get_state, set_posted_urls, get_posted_urls, get_product_cache,
set_product_cache, …) exists here with the same signature and the same
return contract. Cogs import from here instead of utils.db.

Semantics
=========

- READ: cache hit & fresh → return from cache. Miss/stale → query
  Redis → populate cache → return. If Redis is unreachable → fall
  back to SQLite. If SQLite also errors → return empty/None and log.

- WRITE: update cache immediately so the local process sees the new
  value on its next read. Then double-write to Redis and SQLite in
  parallel. SQLite success is the durability guarantee; Redis is
  best-effort. If Redis fails, the key is enqueued for reconciliation.

- RECONCILER: when a write to Redis fails but SQLite succeeded, the
  op is appended to a durable dirty-write queue in SQLite. The failover
  sync loop drains this queue (via resync_to_redis()) on every cycle
  while this node is Primary, retrying each op until Redis ACKs; ops
  that keep failing non-transiently are moved to a dead-letter table
  after _MAX_REPLAY_RETRIES.

- On process start and on promotion to Primary, a resync pass also
  drains the queue, so a node never takes over with writes still
  stranded locally (which would let the new Primary re-post items the
  old Primary already handled).

Connection
==========

Configure via REDIS_URL (e.g. redis://localhost:6379/0) or the
individual REDIS_HOST / REDIS_PORT / REDIS_DB env vars. A single
long-lived redis.asyncio.Redis client is shared across all calls;
the connection pool is never closed per-command.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import redis.asyncio as aioredis
import redis.exceptions as redis_exc

from utils import db as sqlite_backend

logger = logging.getLogger("spc_bot")

# ── Configuration ────────────────────────────────────────────────────────────

_redis_host = os.getenv("REDIS_HOST") or "localhost"
_redis_port = int(os.getenv("REDIS_PORT") or 6379)
_redis_db = int(os.getenv("REDIS_DB") or 0)
REDIS_URL = os.getenv("REDIS_URL") or f"redis://{_redis_host}:{_redis_port}/{_redis_db}"

CACHE_TTL_SECONDS = 60.0
REDIS_TIMEOUT_SECONDS = 5.0

# Key prefixes — single source of truth. Never construct a key manually.
_PREFIX = "spcbot"


def _k_hash_url_lookup(cache_type: str) -> str:
    return f"{_PREFIX}:hashes_index:{cache_type}"


def _k_posted_mds() -> str:
    return f"{_PREFIX}:posted_mds"


def _k_posted_watches() -> str:
    return f"{_PREFIX}:posted_watches"


def _k_posted_surveys() -> str:
    return f"{_PREFIX}:posted_surveys"


def _k_posted_reports() -> str:
    return f"{_PREFIX}:posted_reports"


def _k_posted_product_ids() -> str:
    return f"{_PREFIX}:posted_product_ids"


def _k_posted_warnings() -> str:
    return f"{_PREFIX}:posted_warnings"


def _k_state(key: str) -> str:
    return f"{_PREFIX}:state:{key}"


def _k_posted_urls(day_key: str) -> str:
    return f"{_PREFIX}:posted_urls:{day_key}"


def _k_product_cache(product_id: str) -> str:
    return f"{_PREFIX}:product_cache:{product_id}"


# ── Redis client ─────────────────────────────────────────────────────────────

# Max replay attempts before a dirty_write is moved to the dead-letter
# table. Five gives transient bugs a few startups to clear themselves
# while still preventing a permanently-malformed row from blocking the
# queue forever.
_MAX_REPLAY_RETRIES = 5


class _RedisUnavailable(Exception):
    """Raised when Redis is unreachable. Callers fall back to SQLite."""


# Module-level client — created once, never closed per-command.
_redis_client: Optional[aioredis.Redis] = None


def _build_redis_client() -> aioredis.Redis:
    pool = aioredis.ConnectionPool.from_url(
        REDIS_URL,
        socket_timeout=REDIS_TIMEOUT_SECONDS,
        socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
        decode_responses=True,
        max_connections=10,
    )
    return aioredis.Redis(connection_pool=pool)


def _get_redis_client() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = _build_redis_client()
    return _redis_client


async def _redis_cmd(*args: Any) -> Any:
    """Execute a Redis command via the shared client.

    Raises `_RedisUnavailable` on any connection/timeout failure so
    callers can fall back to SQLite. Re-raises Redis syntax/logic errors
    (e.g. wrong number of args) as-is — those are bugs, not transients.
    """
    for a in args:
        if a is None:
            raise ValueError("_redis_cmd: None is not a valid argument")

    client = _get_redis_client()
    cmd_name = str(args[0]).upper()
    try:
        return await client.execute_command(cmd_name, *args[1:])
    except (
        redis_exc.ConnectionError,
        redis_exc.TimeoutError,
        redis_exc.BusyLoadingError,
        redis_exc.ReadOnlyError,
        OSError,
        asyncio.TimeoutError,
    ) as e:
        raise _RedisUnavailable(f"Redis unavailable: {e}") from e


async def _scan_all_keys(pattern: str) -> List[str]:
    """Paginate SCAN until the cursor returns to 0. Returns all matching keys."""
    client = _get_redis_client()
    keys: List[str] = []
    cursor = 0
    try:
        while True:
            cursor, batch = await client.scan(cursor=cursor, match=pattern, count=100)
            keys.extend(k.decode() if isinstance(k, bytes) else k for k in batch)
            if cursor == 0:
                break
    except (
        redis_exc.ConnectionError,
        redis_exc.TimeoutError,
        redis_exc.ReadOnlyError,
        OSError,
    ) as e:
        raise _RedisUnavailable(f"Redis SCAN unavailable: {e}") from e
    return keys


# ── Local cache ──────────────────────────────────────────────────────────────


class _CacheEntry:
    __slots__ = ("value", "expires_at")

    def __init__(self, value: Any, ttl: float):
        self.value = value
        self.expires_at = time.monotonic() + ttl


_cache: Dict[str, _CacheEntry] = {}


def _cache_get(key: str) -> Tuple[bool, Any]:
    entry = _cache.get(key)
    if entry is None:
        return False, None
    if entry.expires_at < time.monotonic():
        _cache.pop(key, None)
        return False, None
    return True, entry.value


def _cache_set(key: str, value: Any, ttl: Optional[float] = None) -> None:
    if ttl is None:
        ttl = CACHE_TTL_SECONDS
    _cache[key] = _CacheEntry(value, ttl)


def _cache_invalidate(key: str) -> None:
    _cache.pop(key, None)


def invalidate_all_caches() -> None:
    """Wipe the process cache — use on failover promotion."""
    _cache.clear()
    logger.info("[STATE] Process cache invalidated")


# ── Dirty queue (promotion/startup sync) ───────────────────────────────────

# Rate-limited summary logging — during a Redis outage with high write volume,
# every individual queue-on-fail warning produces dozens of log lines per
# minute. Each failing write still increments a counter, but we emit at most
# one WARNING summary every _DIRTY_LOG_INTERVAL seconds. The individual call
# sites log at DEBUG so per-op detail is available when needed.
_DIRTY_LOG_INTERVAL = 30.0
_dirty_since_log: int = 0
_dirty_last_log: float = 0.0


def _note_dirty_enqueue(op: str) -> None:
    """Bump the rate-limited counter and emit one summary WARNING per window."""
    global _dirty_since_log, _dirty_last_log
    _dirty_since_log += 1
    now = time.monotonic()
    if now - _dirty_last_log >= _DIRTY_LOG_INTERVAL:
        logger.warning(
            f"[STATE] Redis reconciler: {_dirty_since_log} write(s) queued "
            f"in the last {_DIRTY_LOG_INTERVAL:.0f}s (latest op: {op})"
        )
        _dirty_since_log = 0
        _dirty_last_log = now


async def _enqueue_dirty(op: str, args: tuple) -> None:
    await sqlite_backend.add_dirty_write(op, args)
    _note_dirty_enqueue(op)


async def _replay(op: str, args: tuple) -> None:
    """Push a queued write to Redis only (SQLite already has it)."""
    if op == "set_hash":
        url, hash_val, cache_type = args
        await _set_hash_in_redis(url, hash_val, cache_type)
    elif op == "add_posted_md":
        (md_number,) = args
        await _redis_cmd("SADD", _k_posted_mds(), md_number)
    elif op == "add_posted_watch":
        (watch_number,) = args
        await _redis_cmd("SADD", _k_posted_watches(), watch_number)
    elif op == "add_posted_survey":
        (dat_guid,) = args
        await _redis_cmd("SADD", _k_posted_surveys(), dat_guid)
    elif op == "add_posted_report":
        (product_id,) = args
        await _redis_cmd("SADD", _k_posted_reports(), product_id)
    elif op == "add_posted_product_id":
        (product_id,) = args
        await _redis_cmd("SADD", _k_posted_product_ids(), product_id)
    elif op == "remove_posted_product_id":
        (product_id,) = args
        await _redis_cmd("SREM", _k_posted_product_ids(), product_id)
    elif op == "remove_posted_warning":
        (vtec_id,) = args
        await _redis_cmd("HDEL", _k_posted_warnings(), vtec_id)
    elif op == "add_posted_warning":
        # Handle legacy (5-7 element) and current (9-element) formats
        vtec_id = args[0]
        message_id = args[1]
        channel_id = args[2]
        posted_at = args[3] if len(args) > 3 else 0.0
        area = args[4] if len(args) > 4 else ""
        tornado_confidence = args[5] if len(args) > 5 else None
        tornado_severity = args[6] if len(args) > 6 else None
        severity = args[7] if len(args) > 7 else None
        raw_text = args[8] if len(args) > 8 else None
        data = {
            "message_id": message_id,
            "channel_id": channel_id,
            "posted_at": posted_at,
            "area": area,
            "tornado_confidence": tornado_confidence,
            "tornado_severity": tornado_severity,
            "severity": severity,
            "raw_text": raw_text,
        }
        await _redis_cmd("HSET", _k_posted_warnings(), vtec_id, json.dumps(data))
    elif op == "set_state":
        key, value = args
        await _redis_cmd("SET", _k_state(key), value)
    elif op == "delete_state":
        (key,) = args
        await _redis_cmd("DEL", _k_state(key))
    elif op == "set_posted_urls":
        day_key, urls = args
        await _redis_cmd("SET", _k_posted_urls(day_key), json.dumps(urls))
    elif op == "set_product_cache":
        product_id, text, ttl = args
        await _redis_cmd("SET", _k_product_cache(product_id), text, "EX", int(ttl))
    elif op == "add_posted_sounding":
        (pkey,) = args
        await _redis_cmd("SADD", "spcbot:posted_soundings", pkey)
    elif op == "add_sounding_handled_watch":
        (watch_number,) = args
        await _redis_cmd("SADD", "spcbot:sounding_handled_watches", watch_number)
    else:
        raise ValueError(f"unknown replay op: {op}")


# ── Internal Redis helpers ────────────────────────────────────────────────────


async def _set_hash_in_redis(url: str, hash_val: str, cache_type: str) -> None:
    await _redis_cmd("HSET", _k_hash_url_lookup(cache_type), url, hash_val)


# ── Public API — drop-in for utils.db ────────────────────────────────────────


async def check_integrity() -> bool:
    return await sqlite_backend.check_integrity()


async def close_db() -> None:
    global _redis_client
    if _redis_client is not None:
        try:
            await _redis_client.aclose()
        except Exception:
            pass
        _redis_client = None
    await sqlite_backend.close_db()


async def get_db():
    return await sqlite_backend.get_db()


# ── Image hashes ─────────────────────────────────────────────────────────────


async def get_hash(url: str, cache_type: Optional[str] = None) -> Optional[str]:
    cache_key = f"hash::{cache_type or 'ANY'}::{url}"
    hit, val = _cache_get(cache_key)
    if hit:
        return val

    try:
        if cache_type:
            result = await _redis_cmd("HGET", _k_hash_url_lookup(cache_type), url)
        else:
            auto_result, manual_result = await asyncio.gather(
                _redis_cmd("HGET", _k_hash_url_lookup("auto"), url),
                _redis_cmd("HGET", _k_hash_url_lookup("manual"), url),
            )
            result = auto_result or manual_result
        _cache_set(cache_key, result)
        return result
    except _RedisUnavailable as e:
        logger.debug(f"[STATE] get_hash({url}) falling back to SQLite: {e}")
        val = await sqlite_backend.get_hash(url, cache_type)
        _cache_set(cache_key, val, ttl=CACHE_TTL_SECONDS / 2)
        return val


async def set_hash(url: str, hash_val: str, cache_type: str = "auto") -> None:
    _cache_set(f"hash::{cache_type}::{url}", hash_val)
    _cache_set(f"hash::ANY::{url}", hash_val)
    _cache_invalidate(f"all_hashes::{cache_type}")
    await sqlite_backend.set_hash(url, hash_val, cache_type)
    try:
        await _set_hash_in_redis(url, hash_val, cache_type)
    except _RedisUnavailable as e:
        logger.debug(f"[STATE] set_hash queued for reconcile: {e}")
        await _enqueue_dirty("set_hash", (url, hash_val, cache_type))


async def get_all_hashes(cache_type: Optional[str] = None) -> Dict[str, str]:
    cache_key = f"all_hashes::{cache_type or 'ALL'}"
    hit, val = _cache_get(cache_key)
    if hit:
        return dict(val)

    try:
        if cache_type:
            result = await _redis_cmd("HGETALL", _k_hash_url_lookup(cache_type))
            mapping = result if isinstance(result, dict) else {}
        else:
            a = await _redis_cmd("HGETALL", _k_hash_url_lookup("auto"))
            m = await _redis_cmd("HGETALL", _k_hash_url_lookup("manual"))
            mapping = {**(a or {}), **(m or {})}
        _cache_set(cache_key, mapping)
        return dict(mapping)
    except _RedisUnavailable as e:
        logger.debug(f"[STATE] get_all_hashes falling back to SQLite: {e}")
        return await sqlite_backend.get_all_hashes(cache_type)


async def set_hashes_batch(hashes: Dict[str, str], cache_type: str = "auto") -> None:
    if not hashes:
        return
    for url, h in hashes.items():
        _cache_set(f"hash::{cache_type}::{url}", h)
        _cache_set(f"hash::ANY::{url}", h)
    _cache_invalidate(f"all_hashes::{cache_type}")
    await sqlite_backend.set_hashes_batch(hashes, cache_type)
    try:
        args: List[Any] = ["HSET", _k_hash_url_lookup(cache_type)]
        for url, h in hashes.items():
            args.append(url)
            args.append(h)
        await _redis_cmd(*args)
    except _RedisUnavailable as e:
        logger.debug(f"[STATE] set_hashes_batch ({len(hashes)}) queued: {e}")
        for url, h in hashes.items():
            await _enqueue_dirty("set_hash", (url, h, cache_type))


# ── Posted MDs ───────────────────────────────────────────────────────────────


async def get_posted_mds() -> Set[str]:
    cache_key = "posted_mds"
    hit, val = _cache_get(cache_key)
    if hit:
        return set(val)
    try:
        result = await _redis_cmd("SMEMBERS", _k_posted_mds())
        members = set(result or [])
        _cache_set(cache_key, members)
        return set(members)
    except _RedisUnavailable as e:
        logger.debug(f"[STATE] get_posted_mds falling back to SQLite: {e}")
        val = await sqlite_backend.get_posted_mds()
        _cache_set(cache_key, val, ttl=CACHE_TTL_SECONDS / 2)
        return val


async def add_posted_md(md_number: str) -> None:
    _cache_invalidate("posted_mds")
    await sqlite_backend.add_posted_md(md_number)
    try:
        await _redis_cmd("SADD", _k_posted_mds(), md_number)
    except _RedisUnavailable as e:
        logger.debug(f"[STATE] add_posted_md({md_number}) queued: {e}")
        await _enqueue_dirty("add_posted_md", (md_number,))


async def prune_posted_mds(max_size: int = 200) -> None:
    await sqlite_backend.prune_posted_mds(max_size)
    _cache_invalidate("posted_mds")
    # Prune the Redis set to match — avoids unbounded growth.
    try:
        members = await _redis_cmd("SMEMBERS", _k_posted_mds())
        if members and len(members) > max_size:
            sqlite_members = await sqlite_backend.get_posted_mds()
            to_remove = [m for m in members if m not in sqlite_members]
            if to_remove:
                await _redis_cmd("SREM", _k_posted_mds(), *to_remove)
    except _RedisUnavailable:
        pass


# ── Posted watches ───────────────────────────────────────────────────────────


async def get_posted_watches() -> Set[str]:
    cache_key = "posted_watches"
    hit, val = _cache_get(cache_key)
    if hit:
        return set(val)
    try:
        result = await _redis_cmd("SMEMBERS", _k_posted_watches())
        members = set(result or [])
        _cache_set(cache_key, members)
        return set(members)
    except _RedisUnavailable as e:
        logger.debug(f"[STATE] get_posted_watches falling back to SQLite: {e}")
        val = await sqlite_backend.get_posted_watches()
        _cache_set(cache_key, val, ttl=CACHE_TTL_SECONDS / 2)
        return val


async def add_posted_watch(watch_number: str) -> None:
    _cache_invalidate("posted_watches")
    await sqlite_backend.add_posted_watch(watch_number)
    try:
        await _redis_cmd("SADD", _k_posted_watches(), watch_number)
    except _RedisUnavailable as e:
        logger.debug(f"[STATE] add_posted_watch({watch_number}) queued: {e}")
        await _enqueue_dirty("add_posted_watch", (watch_number,))


async def prune_posted_watches(max_size: int = 100) -> None:
    await sqlite_backend.prune_posted_watches(max_size)
    _cache_invalidate("posted_watches")
    try:
        members = await _redis_cmd("SMEMBERS", _k_posted_watches())
        if members and len(members) > max_size:
            sqlite_members = await sqlite_backend.get_posted_watches()
            to_remove = [m for m in members if m not in sqlite_members]
            if to_remove:
                await _redis_cmd("SREM", _k_posted_watches(), *to_remove)
    except _RedisUnavailable:
        pass


# ── Posted surveys ───────────────────────────────────────────────────────────


async def get_posted_surveys() -> Set[str]:
    cache_key = "posted_surveys"
    hit, val = _cache_get(cache_key)
    if hit:
        return set(val)
    try:
        result = await _redis_cmd("SMEMBERS", _k_posted_surveys())
        members = set(result or [])
        _cache_set(cache_key, members)
        return set(members)
    except _RedisUnavailable as e:
        logger.debug(f"[STATE] get_posted_surveys falling back to SQLite: {e}")
        val = await sqlite_backend.get_posted_surveys()
        _cache_set(cache_key, val, ttl=CACHE_TTL_SECONDS / 2)
        return val


async def add_posted_survey(dat_guid: str) -> None:
    _cache_invalidate("posted_surveys")
    await sqlite_backend.add_posted_survey(dat_guid)
    try:
        await _redis_cmd("SADD", _k_posted_surveys(), dat_guid)
    except _RedisUnavailable as e:
        logger.debug(f"[STATE] add_posted_survey({dat_guid}) queued: {e}")
        await _enqueue_dirty("add_posted_survey", (dat_guid,))


async def prune_posted_surveys(max_size: int = 100) -> None:
    await sqlite_backend.prune_posted_surveys(max_size)
    _cache_invalidate("posted_surveys")
    try:
        members = await _redis_cmd("SMEMBERS", _k_posted_surveys())
        if members and len(members) > max_size:
            sqlite_members = await sqlite_backend.get_posted_surveys()
            to_remove = [m for m in members if m not in sqlite_members]
            if to_remove:
                await _redis_cmd("SREM", _k_posted_surveys(), *to_remove)
    except _RedisUnavailable:
        pass


# ── Posted reports (LSRs) ────────────────────────────────────────────────────


async def get_posted_reports() -> Set[str]:
    cache_key = "posted_reports"
    hit, val = _cache_get(cache_key)
    if hit:
        return set(val)
    try:
        result = await _redis_cmd("SMEMBERS", _k_posted_reports())
        members = set(result or [])
        _cache_set(cache_key, members)
        return set(members)
    except _RedisUnavailable as e:
        logger.debug(f"[STATE] get_posted_reports falling back to SQLite: {e}")
        val = await sqlite_backend.get_posted_reports()
        _cache_set(cache_key, val, ttl=CACHE_TTL_SECONDS / 2)
        return val


async def add_posted_report(product_id: str) -> None:
    _cache_invalidate("posted_reports")
    await sqlite_backend.add_posted_report(product_id)
    try:
        await _redis_cmd("SADD", _k_posted_reports(), product_id)
    except _RedisUnavailable as e:
        logger.debug(f"[STATE] add_posted_report({product_id}) queued: {e}")
        await _enqueue_dirty("add_posted_report", (product_id,))


async def prune_posted_reports(max_size: int = 500) -> None:
    await sqlite_backend.prune_posted_reports(max_size)
    _cache_invalidate("posted_reports")
    try:
        members = await _redis_cmd("SMEMBERS", _k_posted_reports())
        if members and len(members) > max_size:
            sqlite_members = await sqlite_backend.get_posted_reports()
            to_remove = [m for m in members if m not in sqlite_members]
            if to_remove:
                await _redis_cmd("SREM", _k_posted_reports(), *to_remove)
    except _RedisUnavailable:
        pass


# ── Posted product IDs (cross-feed dedup) ───────────────────────────────────


async def get_posted_product_ids() -> Set[str]:
    cache_key = "posted_product_ids"
    hit, val = _cache_get(cache_key)
    if hit:
        return set(val)
    try:
        result = await _redis_cmd("SMEMBERS", _k_posted_product_ids())
        members = set(result or [])
        _cache_set(cache_key, members)
        return set(members)
    except _RedisUnavailable as e:
        logger.debug(f"[STATE] get_posted_product_ids falling back to SQLite: {e}")
        val = await sqlite_backend.get_posted_product_ids()
        _cache_set(cache_key, val, ttl=CACHE_TTL_SECONDS / 2)
        return val


async def add_posted_product_id(product_id: str) -> None:
    _cache_invalidate("posted_product_ids")
    await sqlite_backend.add_posted_product_id(product_id)
    try:
        await _redis_cmd("SADD", _k_posted_product_ids(), product_id)
    except _RedisUnavailable as e:
        logger.debug(f"[STATE] add_posted_product_id({product_id}) queued: {e}")
        await _enqueue_dirty("add_posted_product_id", (product_id,))


async def remove_posted_product_id(product_id: str) -> None:
    """Roll back an `add_posted_product_id` write across SQLite + Redis."""
    _cache_invalidate("posted_product_ids")
    await sqlite_backend.remove_posted_product_id(product_id)
    try:
        await _redis_cmd("SREM", _k_posted_product_ids(), product_id)
    except _RedisUnavailable as e:
        logger.debug(f"[STATE] remove_posted_product_id({product_id}) queued: {e}")
        await _enqueue_dirty("remove_posted_product_id", (product_id,))


async def prune_posted_product_ids(max_size: int = 1000) -> None:
    await sqlite_backend.prune_posted_product_ids(max_size)
    _cache_invalidate("posted_product_ids")
    # Prune Redis set as well
    try:
        members = await _redis_cmd("SMEMBERS", _k_posted_product_ids())
        if members and len(members) > max_size:
            # This is a bit expensive for a set, but sets don't have natural ordering in Redis.
            # SQLite handles the ordering. We just want to keep them roughly in sync.
            # Better way: get authoritative list from SQLite after pruning.
            sqlite_members = await sqlite_backend.get_posted_product_ids()
            to_remove = [m for m in members if m not in sqlite_members]
            if to_remove:
                await _redis_cmd("SREM", _k_posted_product_ids(), *to_remove)
    except _RedisUnavailable:
        pass


# ── Significant events — routed to events_db, not Redis ─────────────────────


async def add_significant_event(
    event_id: str,
    event_type: str,
    location: str,
    magnitude: str = "",
    vtec_id: str = "",
    coords: str = "",
    timestamp: float = 0.0,
    source: str = "",
    raw_text: str = "",
) -> None:
    from utils.events_db import add_significant_event as _add  # noqa: PLC0415

    await _add(
        event_id, event_type, location, magnitude, vtec_id, coords, timestamp, source, raw_text
    )


async def get_recent_significant_events(
    event_type: Optional[str] = None,
    since_hours: int = 24,
    limit: int = 50,
) -> List[dict]:
    from utils.events_db import get_recent_significant_events as _get  # noqa: PLC0415

    return await _get(event_type, since_hours, limit)


async def find_matching_tornado(
    source: str,
    timestamp: float,
    location_query: str,
    window_hours: float = 12.0,
) -> Optional[Tuple[str, Optional[str]]]:
    from utils.events_db import find_matching_tornado as _find  # noqa: PLC0415

    return await _find(source, timestamp, location_query, window_hours)


# ── Posted warnings ──────────────────────────────────────────────────────────


async def get_all_posted_warnings() -> Dict[str, dict]:
    cache_key = "posted_warnings"
    hit, val = _cache_get(cache_key)
    if hit:
        return dict(val)
    try:
        result = await _redis_cmd("HGETALL", _k_posted_warnings())
        # redis-py with decode_responses=True returns a dict directly
        mapping: Dict[str, dict] = {}
        if result and isinstance(result, dict):
            for vtec_id, json_str in result.items():
                try:
                    mapping[vtec_id] = json.loads(json_str)
                except (json.JSONDecodeError, TypeError) as e:
                    logger.warning(f"[STATE] Corrupt posted_warning entry dropped {vtec_id!r}: {e}")
                    continue
        _cache_set(cache_key, mapping)
        return dict(mapping)
    except _RedisUnavailable as e:
        logger.debug(f"[STATE] get_all_posted_warnings falling back to SQLite: {e}")
        val = await sqlite_backend.get_all_posted_warnings()
        _cache_set(cache_key, val, ttl=CACHE_TTL_SECONDS / 2)
        return val


async def add_posted_warning(
    vtec_id: str,
    message_id: int,
    channel_id: int,
    posted_at: float = 0.0,
    area: str = "",
    tornado_confidence: Optional[str] = None,
    tornado_severity: Optional[str] = None,
    severity: Optional[str] = None,
    raw_text: Optional[str] = None,
) -> bool:
    _cache_invalidate("posted_warnings")
    ok = await sqlite_backend.add_posted_warning(
        vtec_id,
        message_id,
        channel_id,
        posted_at,
        area,
        tornado_confidence,
        tornado_severity,
        severity,
        raw_text,
    )
    data = {
        "message_id": message_id,
        "channel_id": channel_id,
        "area": area,
        "tornado_confidence": tornado_confidence,
        "tornado_severity": tornado_severity,
        "severity": severity,
    }
    try:
        await _redis_cmd("HSET", _k_posted_warnings(), vtec_id, json.dumps(data))
    except _RedisUnavailable as e:
        logger.debug(f"[STATE] add_posted_warning({vtec_id}) queued: {e}")
        await _enqueue_dirty(
            "add_posted_warning",
            (
                vtec_id,
                message_id,
                channel_id,
                posted_at,
                area,
                tornado_confidence,
                tornado_severity,
                severity,
                raw_text,
            ),
        )
    return ok


async def remove_posted_warning(vtec_id: str) -> None:
    """Roll back an `add_posted_warning` write across SQLite + Redis."""
    _cache_invalidate("posted_warnings")
    await sqlite_backend.remove_posted_warning(vtec_id)
    try:
        await _redis_cmd("HDEL", _k_posted_warnings(), vtec_id)
    except _RedisUnavailable as e:
        logger.debug(f"[STATE] remove_posted_warning({vtec_id}) queued: {e}")
        await _enqueue_dirty("remove_posted_warning", (vtec_id,))


async def prune_posted_warnings(max_size: int = 500) -> None:
    await sqlite_backend.prune_posted_warnings(max_size)
    _cache_invalidate("posted_warnings")
    try:
        # Prune Redis HSET to match SQLite
        members = await _redis_cmd("HKEYS", _k_posted_warnings())
        if members and len(members) > max_size:
            sqlite_data = await sqlite_backend.get_all_posted_warnings()
            sqlite_keys = set(sqlite_data.keys())
            to_remove = [m for m in members if m not in sqlite_keys]
            if to_remove:
                await _redis_cmd("HDEL", _k_posted_warnings(), *to_remove)
    except _RedisUnavailable:
        pass


# ── Soundings ─────────────────────────────────────────────────────────────────


async def get_posted_soundings() -> Set[str]:
    hit, val = _cache_get("posted_soundings")
    if hit:
        return set(val)
    try:
        result = await _redis_cmd("SMEMBERS", "spcbot:posted_soundings")
        s = set(result or [])
        _cache_set("posted_soundings", s)
        return s
    except _RedisUnavailable:
        return await sqlite_backend.get_posted_soundings()


async def add_posted_sounding(pkey: str) -> None:
    _cache_invalidate("posted_soundings")
    await sqlite_backend.add_posted_sounding(pkey)
    try:
        await _redis_cmd("SADD", "spcbot:posted_soundings", pkey)
    except _RedisUnavailable as e:
        logger.debug(f"[STATE] add_posted_sounding queued: {e}")
        await _enqueue_dirty("add_posted_sounding", (pkey,))


async def prune_posted_soundings(max_days: int = 2) -> None:
    await sqlite_backend.prune_posted_soundings(max_days)
    _cache_invalidate("posted_soundings")


async def get_sounding_handled_watches() -> Set[str]:
    hit, val = _cache_get("sounding_handled_watches")
    if hit:
        return set(val)
    try:
        result = await _redis_cmd("SMEMBERS", "spcbot:sounding_handled_watches")
        s = set(result or [])
        _cache_set("sounding_handled_watches", s)
        return s
    except _RedisUnavailable:
        return await sqlite_backend.get_sounding_handled_watches()


async def add_sounding_handled_watch(watch_number: str) -> None:
    _cache_invalidate("sounding_handled_watches")
    await sqlite_backend.add_sounding_handled_watch(watch_number)
    try:
        await _redis_cmd("SADD", "spcbot:sounding_handled_watches", watch_number)
    except _RedisUnavailable as e:
        logger.debug(f"[STATE] add_sounding_handled_watch queued: {e}")
        await _enqueue_dirty("add_sounding_handled_watch", (watch_number,))


async def clear_sounding_handled_watches() -> None:
    _cache_invalidate("sounding_handled_watches")
    await sqlite_backend.clear_sounding_handled_watches()
    try:
        await _redis_cmd("DEL", "spcbot:sounding_handled_watches")
    except _RedisUnavailable:
        pass


# ── Key/value state ───────────────────────────────────────────────────────────


async def get_state(key: str) -> Optional[str]:
    cache_key = f"state::{key}"
    hit, val = _cache_get(cache_key)
    if hit:
        return val
    try:
        result = await _redis_cmd("GET", _k_state(key))
        _cache_set(cache_key, result)
        return result
    except _RedisUnavailable as e:
        logger.debug(f"[STATE] get_state({key}) falling back to SQLite: {e}")
        val = await sqlite_backend.get_state(key)
        _cache_set(cache_key, val, ttl=CACHE_TTL_SECONDS / 2)
        return val


async def set_state(key: str, value: str) -> None:
    _cache_set(f"state::{key}", value)
    await sqlite_backend.set_state(key, value)
    try:
        await _redis_cmd("SET", _k_state(key), value)
    except _RedisUnavailable as e:
        logger.debug(f"[STATE] set_state({key}) queued: {e}")
        await _enqueue_dirty("set_state", (key, value))


async def delete_state(key: str) -> None:
    _cache_invalidate(f"state::{key}")
    await sqlite_backend.delete_state(key)
    try:
        await _redis_cmd("DEL", _k_state(key))
    except _RedisUnavailable as e:
        logger.debug(f"[STATE] delete_state({key}) queued: {e}")
        await _enqueue_dirty("delete_state", (key,))


# ── Posted URLs (per day) ────────────────────────────────────────────────────


async def get_posted_urls(day_key: str) -> List[str]:
    cache_key = f"posted_urls::{day_key}"
    hit, val = _cache_get(cache_key)
    if hit:
        return list(val)
    try:
        result = await _redis_cmd("GET", _k_posted_urls(day_key))
        try:
            urls = json.loads(result) if result else []
        except json.JSONDecodeError:
            logger.warning(
                f"[STATE] get_posted_urls({day_key}): malformed data, falling back to SQLite"
            )
            urls = await sqlite_backend.get_posted_urls(day_key)
        _cache_set(cache_key, urls)
        return list(urls)
    except _RedisUnavailable as e:
        logger.debug(f"[STATE] get_posted_urls({day_key}) falling back: {e}")
        val = await sqlite_backend.get_posted_urls(day_key)
        _cache_set(cache_key, val, ttl=CACHE_TTL_SECONDS / 2)
        return val


async def set_posted_urls(day_key: str, urls: List[str]) -> None:
    _cache_set(f"posted_urls::{day_key}", list(urls))
    await sqlite_backend.set_posted_urls(day_key, urls)
    try:
        await _redis_cmd("SET", _k_posted_urls(day_key), json.dumps(urls))
    except _RedisUnavailable as e:
        logger.debug(f"[STATE] set_posted_urls({day_key}) queued: {e}")
        await _enqueue_dirty("set_posted_urls", (day_key, urls))


# ── Product text cache (TTL) ─────────────────────────────────────────────────


async def get_product_cache(product_id: str) -> Optional[str]:
    cache_key = f"product_cache::{product_id}"
    hit, val = _cache_get(cache_key)
    if hit:
        return val
    try:
        result = await _redis_cmd("GET", _k_product_cache(product_id))
        _cache_set(cache_key, result, ttl=min(CACHE_TTL_SECONDS, 30.0))
        return result
    except _RedisUnavailable as e:
        logger.debug(f"[STATE] get_product_cache({product_id}) fallback: {e}")
        val = await sqlite_backend.get_product_cache(product_id)
        _cache_set(cache_key, val, ttl=CACHE_TTL_SECONDS / 2)
        return val


async def set_product_cache(product_id: str, text: str, ttl: int = 600) -> None:
    _cache_set(f"product_cache::{product_id}", text, ttl=min(CACHE_TTL_SECONDS, ttl))
    await sqlite_backend.set_product_cache(product_id, text, ttl)
    try:
        await _redis_cmd("SET", _k_product_cache(product_id), text, "EX", int(ttl))
    except _RedisUnavailable as e:
        logger.debug(f"[STATE] set_product_cache({product_id}) queued: {e}")
        await _enqueue_dirty("set_product_cache", (product_id, text, ttl))


# ── Outlook revision tracking ────────────────────────────────────────────────


async def get_previous_outlook_text(day: str) -> Optional[str]:
    """Return the previous outlook text for revision comparison."""
    return await get_product_cache(f"previous_outlook_text_day{day}")


async def set_previous_outlook_text(day: str, text: str, ttl: int = 3600) -> None:
    """Store the outlook text for revision comparison (1 hour TTL)."""
    await set_product_cache(f"previous_outlook_text_day{day}", text, ttl=ttl)


# ── HTTP validators (ETag / Last-Modified) ─────────────────────────────────
# SQLite-only — conditional-GET runs every 60s per URL; pushing every
# validator update through Redis would be wasteful on the hot path.


async def get_validators(url: str) -> Optional[Dict[str, str]]:
    return await sqlite_backend.get_validators(url)


async def get_all_validators() -> Dict[str, Dict[str, str]]:
    return await sqlite_backend.get_all_validators()


async def set_validators(url: str, etag: str, last_modified: str) -> None:
    await sqlite_backend.set_validators(url, etag, last_modified)


# ── Startup resync ───────────────────────────────────────────────────────────


async def resync_to_redis(force_full: bool = False) -> Dict[str, int]:
    """Push pending writes from SQLite to Redis.

    By default, only pushes items explicitly marked dirty (failed writes).
    force_full=True pushes every SQLite record — use only for initial
    migration or disaster recovery.
    """
    if force_full:
        return await _resync_full()

    pending = await sqlite_backend.get_dirty_writes()
    if not pending:
        logger.debug("[STATE] Startup resync: no dirty writes found")
        return {"dirty": 0}

    ids_to_delete: list[int] = []
    ids_to_bump: list[int] = []
    ids_to_quarantine: list[int] = []
    redis_down = False
    for item in pending:
        if redis_down:
            break
        try:
            await _replay(item["op"], tuple(item["args"]))
            ids_to_delete.append(item["id"])
        except _RedisUnavailable:
            remaining = (
                len(pending) - len(ids_to_delete) - len(ids_to_bump) - len(ids_to_quarantine)
            )
            logger.warning(
                f"[STATE] Resync paused — Redis unavailable with {remaining} write(s) still pending"
            )
            redis_down = True
            break
        except Exception as e:
            # Don't silently drop — bump retry, and after _MAX_REPLAY_RETRIES
            # move the row to the dead-letter table for operator review.
            retry_count = int(item.get("retry_count") or 0)
            if retry_count + 1 >= _MAX_REPLAY_RETRIES:
                ids_to_quarantine.append(item["id"])
                logger.error(
                    f"[STATE] Resync quarantining {item['op']} (id={item['id']}) "
                    f"after {retry_count + 1} failed retries: {e}"
                )
            else:
                ids_to_bump.append(item["id"])
                logger.warning(
                    f"[STATE] Resync retry {retry_count + 1}/{_MAX_REPLAY_RETRIES} "
                    f"for {item['op']} (id={item['id']}): {e}"
                )

    if ids_to_delete:
        await sqlite_backend.delete_dirty_writes_batch(ids_to_delete)
    if ids_to_bump:
        await sqlite_backend.bump_dirty_retry(ids_to_bump)
    if ids_to_quarantine:
        await sqlite_backend.quarantine_dirty_writes(ids_to_quarantine)
    if ids_to_delete or ids_to_bump or ids_to_quarantine:
        logger.info(
            f"[STATE] Startup resync: caught up {len(ids_to_delete)} writes, "
            f"retrying {len(ids_to_bump)}, quarantined {len(ids_to_quarantine)}"
        )

    return {
        "dirty": len(ids_to_delete),
        "retried": len(ids_to_bump),
        "quarantined": len(ids_to_quarantine),
    }


async def mirror_to_sqlite() -> None:
    """Pull authoritative state from Redis and update the local SQLite mirror.
    Used on promotion so the standby's local DB is fresh before it takes writes.
    """
    try:
        logger.info("[STATE] Mirroring Redis → SQLite...")

        # 1. Hashes
        for ct in ("auto", "manual"):
            h = await get_all_hashes(ct)
            if h:
                await sqlite_backend.set_hashes_batch(h, ct)

        # 2. Posted collections — one transaction per kind, not one per row.
        # With thousands of cached entries the old N+1 loop added seconds
        # to promotion latency.
        await sqlite_backend.add_posted_mds_batch(await get_posted_mds())
        await sqlite_backend.add_posted_watches_batch(await get_posted_watches())
        await sqlite_backend.add_posted_reports_batch(await get_posted_reports())
        await sqlite_backend.add_posted_product_ids_batch(await get_posted_product_ids())

        # 3. State — paginate SCAN fully (C3 fix)
        state_keys = await _scan_all_keys(f"{_k_state('*')}")
        if state_keys:
            client = _get_redis_client()
            values = await client.mget(*state_keys)
            prefix = _k_state("")
            for k, val in zip(state_keys, values):
                if val:
                    base_key = k.removeprefix(prefix)
                    str_val = val.decode() if isinstance(val, bytes) else val
                    await sqlite_backend.set_state(base_key, str_val)

        # 4. Posted URLs
        for day in ("day1", "day2", "day3"):
            urls = await get_posted_urls(day)
            if urls:
                await sqlite_backend.set_posted_urls(day, urls)

        logger.info("[STATE] Mirroring complete")
    except Exception as e:
        logger.warning(f"[STATE] Mirroring failed: {e}")


async def _resync_full() -> Dict[str, int]:
    counts: Dict[str, int] = {
        "hashes": 0,
        "posted_mds": 0,
        "posted_watches": 0,
        "posted_surveys": 0,
        "posted_reports": 0,
        "posted_product_ids": 0,
        "state": 0,
        "urls": 0,
    }
    try:
        for cache_type in ("auto", "manual"):
            hashes = await sqlite_backend.get_all_hashes(cache_type)
            if hashes:
                args: List[Any] = ["HSET", _k_hash_url_lookup(cache_type)]
                for url, h in hashes.items():
                    args.append(url)
                    args.append(h)
                await _redis_cmd(*args)
                counts["hashes"] += len(hashes)

        mds = await sqlite_backend.get_posted_mds()
        if mds:
            await _redis_cmd("SADD", _k_posted_mds(), *mds)
            counts["posted_mds"] = len(mds)

        watches = await sqlite_backend.get_posted_watches()
        if watches:
            await _redis_cmd("SADD", _k_posted_watches(), *watches)
            counts["posted_watches"] = len(watches)

        surveys = await sqlite_backend.get_posted_surveys()
        if surveys:
            await _redis_cmd("SADD", _k_posted_surveys(), *surveys)
            counts["posted_surveys"] = len(surveys)

        reports = await sqlite_backend.get_posted_reports()
        if reports:
            await _redis_cmd("SADD", _k_posted_reports(), *reports)
            counts["posted_reports"] = len(reports)

        product_ids = await sqlite_backend.get_posted_product_ids()
        if product_ids:
            await _redis_cmd("SADD", _k_posted_product_ids(), *product_ids)
            counts["posted_product_ids"] = len(product_ids)

        states = await sqlite_backend.get_all_state()
        if states:
            args = ["MSET"]
            for key, value in states.items():
                args.append(_k_state(key))
                args.append(value)
            await _redis_cmd(*args)
            counts["state"] = len(states)

        urls_map = await sqlite_backend.get_all_posted_urls()
        if urls_map:
            args = ["MSET"]
            for day_key, urls in urls_map.items():
                args.append(_k_posted_urls(day_key))
                args.append(json.dumps(urls))
            await _redis_cmd(*args)
            counts["urls"] = len(urls_map)

        logger.info(f"[STATE] Full resync → Redis: {counts}")
        return counts
    except _RedisUnavailable as e:
        logger.warning(f"[STATE] Full resync skipped — Redis unavailable: {e}")
        return counts
    except Exception as e:
        logger.error(f"[STATE] Full resync partial failure: {e}")
        return counts
