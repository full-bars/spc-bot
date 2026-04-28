"""
utils/state_store.py — Shared state backed by Upstash Redis with
a local SQLite mirror for durability and outage survival.

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
    │  │ Upstash (REST)   │      │ SQLite (local)   │    │
    │  │ source of truth  │      │ durable mirror   │    │
    │  └──────────────────┘      └──────────────────┘    │
    │          ▲                         ▲               │
    │          └─────────────┬───────────┘               │
    │                        │                           │
    │  ┌─────────────────────┴────────────────────────┐  │
    │  │ Reconciler — retries writes that failed      │  │
    │  │ to Upstash by scanning a dirty-key set.      │  │
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
  Upstash → populate cache → return. If Upstash is unreachable → fall
  back to SQLite. If SQLite also errors → return empty/None and log.

- WRITE: update cache immediately so the local process sees the new
  value on its next read. Then double-write to Upstash and SQLite in
  parallel. SQLite success is the durability guarantee; Upstash is
  best-effort. If Upstash fails, the key is enqueued for reconciliation.

- RECONCILER: when a write to Upstash fails but SQLite succeeded, the
  key is added to a `_dirty` set. A background task (started lazily on
  first failure) periodically retries those writes until Upstash ACKs.

- On process start, a full-resync pass ensures everything Upstash is
  missing gets pushed. This handles "Upstash was down when we wrote
  and the process then restarted" — the dirty set is in-memory only.

Free-tier budget
================

Upstash free is 10,000 commands/day. Hot-read paths are served from
the in-process cache (0 commands). Bulk loads on startup (SMEMBERS /
keys SCAN) cost one command regardless of set size. Writes cost one
command each but happen rarely compared to reads.

Projected daily usage with current settings (heartbeat 30s, refresh
every 5 min, both nodes):
  primary heartbeat writes          2,880
  primary state mutations               ~300
  standby heartbeat reads           2,880
  standby periodic refresh          ~2,000
  bulk loads + startup              ~50
  headroom                          ~2,000
                                   ──────
                                   ~8,200 / 10,000
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import aiohttp

from utils import db as sqlite_backend
from utils.http import ensure_session

logger = logging.getLogger("spc_bot")

# ── Configuration ────────────────────────────────────────────────────────────

UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL", "")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")

CACHE_TTL_SECONDS = 60.0
RECONCILER_INTERVAL_SECONDS = 30.0
UPSTASH_TIMEOUT_SECONDS = 5.0

# Key prefixes — single source of truth. Never construct a key manually;
# always go through one of the _k_* helpers.
_PREFIX = "spcbot"


def _k_hash(cache_type: str, url: str) -> str:
    # URLs are long and contain punctuation Redis treats literally; hash
    # them so keys stay short and well-formed.
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return f"{_PREFIX}:hashes:{cache_type}:{h}"


def _k_hash_url_lookup(cache_type: str, url: str) -> str:
    # We also need to recover the URL from its hash for get_all_hashes().
    # Store url → hash in the main key, and a reverse index in a Redis Hash.
    return f"{_PREFIX}:hashes_index:{cache_type}"


def _k_posted_mds() -> str:
    return f"{_PREFIX}:posted_mds"


def _k_posted_watches() -> str:
    return f"{_PREFIX}:posted_watches"


def _k_posted_surveys() -> str:
    return f"{_PREFIX}:posted_surveys"


def _k_posted_warnings() -> str:
    return f"{_PREFIX}:posted_warnings"


def _k_state(key: str) -> str:
    return f"{_PREFIX}:state:{key}"


def _k_posted_urls(day_key: str) -> str:
    return f"{_PREFIX}:posted_urls:{day_key}"


def _k_product_cache(product_id: str) -> str:
    return f"{_PREFIX}:product_cache:{product_id}"


# ── Upstash REST client ──────────────────────────────────────────────────────

class _UpstashUnavailable(Exception):
    """Raised when Upstash is unreachable or not configured."""


async def _upstash_cmd(*args: Any) -> Any:
    """Execute a single Upstash REST command.

    Each call is one HTTP round-trip and one billed command. The return
    value is Upstash's `result` field (JSON-decoded). Raises
    `_UpstashUnavailable` on any network-level failure or missing
    configuration — callers treat that as "fall back to SQLite".
    """
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        raise _UpstashUnavailable("Upstash not configured")

    # Reject None/bytes explicitly so we don't silently ship the literal
    # "None" or a mojibake'd byte string to Upstash.
    for a in args:
        if a is None:
            raise ValueError("_upstash_cmd: None is not a valid argument")

    session = await ensure_session()
    try:
        async with session.post(
            UPSTASH_URL,
            headers={
                "Authorization": f"Bearer {UPSTASH_TOKEN}",
                "Content-Type": "application/json",
            },
            json=[str(a) for a in args],
            timeout=aiohttp.ClientTimeout(total=UPSTASH_TIMEOUT_SECONDS),
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise _UpstashUnavailable(
                    f"Upstash returned {resp.status}: {text[:200]}"
                )
            body = await resp.json()
            if "error" in body:
                # Hard failure from Redis itself — e.g. syntax error. Do
                # not reconcile these; they're bugs, not transient.
                raise RuntimeError(f"Upstash error: {body['error']}")
            return body.get("result")
    except asyncio.TimeoutError as e:
        raise _UpstashUnavailable(f"Upstash timeout: {e}") from e
    except aiohttp.ClientError as e:
        raise _UpstashUnavailable(f"Upstash transport error: {e}") from e


# ── Local cache ──────────────────────────────────────────────────────────────

class _CacheEntry:
    __slots__ = ("value", "expires_at")

    def __init__(self, value: Any, ttl: float):
        self.value = value
        self.expires_at = time.monotonic() + ttl


_cache: Dict[str, _CacheEntry] = {}
_cache_lock = asyncio.Lock()


def _cache_get(key: str) -> Tuple[bool, Any]:
    entry = _cache.get(key)
    if entry is None:
        return False, None
    if entry.expires_at < time.monotonic():
        _cache.pop(key, None)
        return False, None
    return True, entry.value


def _cache_set(key: str, value: Any, ttl: Optional[float] = None) -> None:
    # Look up the default at call-time, not at def-time, so tests (and
    # anyone who wants to tune live) can monkeypatch CACHE_TTL_SECONDS.
    if ttl is None:
        ttl = CACHE_TTL_SECONDS
    _cache[key] = _CacheEntry(value, ttl)


def _cache_invalidate(key: str) -> None:
    _cache.pop(key, None)


def invalidate_all_caches() -> None:
    """Wipe the process cache — use on failover promotion so the newly-
    active node refetches authoritative state from Upstash."""
    _cache.clear()
    logger.info("[STATE] Process cache invalidated")


# ── Reconciler (dirty-key retry) ─────────────────────────────────────────────

# Each entry describes an Upstash write that needs to be retried. `op`
# is one of: "set_hash", "add_posted_md", "add_posted_watch", "add_posted_warning",
# "set_state", "delete_state", "set_posted_urls", "set_product_cache".
# `args` are the user-facing arguments to the corresponding public
# function (NOT the Upstash command args) so the reconciler replays
# through the normal write path.
_Dirty = Tuple[str, tuple]
_dirty: List[_Dirty] = []
_dirty_lock = asyncio.Lock()
_reconciler_task: Optional[asyncio.Task] = None


async def _enqueue_dirty(op: str, args: tuple) -> None:
    """Remember a write that failed to reach Upstash."""
    async with _dirty_lock:
        _dirty.append((op, args))
    _start_reconciler_if_needed()


def _start_reconciler_if_needed() -> None:
    global _reconciler_task
    if _reconciler_task is None or _reconciler_task.done():
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop (e.g. during import-time); will start on first await
        _reconciler_task = loop.create_task(_reconciler_loop())
        logger.info("[STATE] Reconciler started")


async def _reconciler_loop() -> None:
    """Retry queued dirty writes on a timer until the queue drains."""
    while True:
        try:
            await asyncio.sleep(RECONCILER_INTERVAL_SECONDS)
            async with _dirty_lock:
                if not _dirty:
                    return  # exit; re-start on next _enqueue_dirty
                pending = list(_dirty)
                _dirty.clear()

            still_dirty: List[_Dirty] = []
            for op, args in pending:
                try:
                    await _replay(op, args)
                except _UpstashUnavailable:
                    still_dirty.append((op, args))
                except Exception as e:
                    logger.exception(
                        f"[STATE] Reconciler dropped write {op}{args}: {e}"
                    )

            async with _dirty_lock:
                _dirty[0:0] = still_dirty
                if still_dirty:
                    logger.info(
                        f"[STATE] Reconciler: {len(still_dirty)} writes "
                        f"still pending"
                    )
                else:
                    logger.info("[STATE] Reconciler: all writes caught up")
                    return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"[STATE] Reconciler loop error: {e}")


async def _replay(op: str, args: tuple) -> None:
    """Push a queued write to Upstash only (SQLite already has it)."""
    if op == "set_hash":
        url, hash_val, cache_type = args
        await _upstash_set_hash(url, hash_val, cache_type)
    elif op == "add_posted_md":
        (md_number,) = args
        await _upstash_cmd("SADD", _k_posted_mds(), md_number)
    elif op == "add_posted_watch":
        (watch_number,) = args
        await _upstash_cmd("SADD", _k_posted_watches(), watch_number)
    elif op == "add_posted_survey":
        (dat_guid,) = args
        await _upstash_cmd("SADD", _k_posted_surveys(), dat_guid)
    elif op == "add_posted_warning":
        vtec_id, message_id, channel_id, _ = args
        data = {"message_id": message_id, "channel_id": channel_id}
        await _upstash_cmd("HSET", _k_posted_warnings(), vtec_id, json.dumps(data))
    elif op == "set_state":
        key, value = args
        await _upstash_cmd("SET", _k_state(key), value)
    elif op == "delete_state":
        (key,) = args
        await _upstash_cmd("DEL", _k_state(key))
    elif op == "set_posted_urls":
        day_key, urls = args
        await _upstash_cmd("SET", _k_posted_urls(day_key), json.dumps(urls))
    elif op == "set_product_cache":
        product_id, text, ttl = args
        await _upstash_cmd(
            "SET", _k_product_cache(product_id), text, "EX", int(ttl)
        )
    else:
        raise ValueError(f"unknown replay op: {op}")


# ── Internal Upstash helpers (single-purpose wrappers) ───────────────────────

async def _upstash_set_hash(url: str, hash_val: str, cache_type: str) -> None:
    # We store the hash at a per-url key, and also in a per-type Hash
    # so get_all_hashes() can bulk-load with a single HGETALL.
    await _upstash_cmd("HSET", _k_hash_url_lookup(cache_type, url), url, hash_val)


# ── Public API — drop-in for utils.db ────────────────────────────────────────

async def check_integrity() -> bool:
    """SQLite integrity check (Upstash has no equivalent — it's managed)."""
    return await sqlite_backend.check_integrity()


async def close_db() -> None:
    await sqlite_backend.close_db()


async def get_db():
    # Retained for compatibility with legacy callers that only need the
    # side-effect of ensuring the SQLite connection is open.
    return await sqlite_backend.get_db()


# ── Image hashes ─────────────────────────────────────────────────────────────

async def get_hash(url: str, cache_type: Optional[str] = None) -> Optional[str]:
    """Get the last-seen content hash for a URL. Cache → Upstash → SQLite.

    When the caller knows the cache_type (auto vs manual), pass it — that
    cuts Upstash traffic in half by querying only the matching index
    instead of both. Unscoped callers still work but cost 2 commands.
    """
    cache_key = f"hash::{cache_type or 'ANY'}::{url}"
    hit, val = _cache_get(cache_key)
    if hit:
        return val

    try:
        if cache_type:
            result = await _upstash_cmd(
                "HGET", _k_hash_url_lookup(cache_type, url), url
            )
        else:
            auto_result, manual_result = await asyncio.gather(
                _upstash_cmd("HGET", _k_hash_url_lookup("auto", url), url),
                _upstash_cmd("HGET", _k_hash_url_lookup("manual", url), url),
            )
            result = auto_result or manual_result
        _cache_set(cache_key, result)
        return result
    except _UpstashUnavailable as e:
        logger.debug(f"[STATE] get_hash({url}) falling back to SQLite: {e}")
        val = await sqlite_backend.get_hash(url, cache_type)
        _cache_set(cache_key, val, ttl=CACHE_TTL_SECONDS / 2)
        return val


async def set_hash(url: str, hash_val: str, cache_type: str = "auto") -> None:
    """Store the content hash for a URL. Cache + Upstash + SQLite."""
    _cache_set(f"hash::{cache_type}::{url}", hash_val)
    _cache_set(f"hash::ANY::{url}", hash_val)
    # Invalidate any cached bulk-load of this cache_type.
    _cache_invalidate(f"all_hashes::{cache_type}")

    # SQLite first — it's our durability guarantee.
    await sqlite_backend.set_hash(url, hash_val, cache_type)

    try:
        await _upstash_set_hash(url, hash_val, cache_type)
    except _UpstashUnavailable as e:
        logger.warning(f"[STATE] set_hash queued for reconcile: {e}")
        await _enqueue_dirty("set_hash", (url, hash_val, cache_type))


async def get_all_hashes(cache_type: Optional[str] = None) -> Dict[str, str]:
    """Bulk-load all hashes for a cache_type. One Upstash command."""
    cache_key = f"all_hashes::{cache_type or 'ALL'}"
    hit, val = _cache_get(cache_key)
    if hit:
        return dict(val)

    try:
        if cache_type:
            result = await _upstash_cmd(
                "HGETALL", _k_hash_url_lookup(cache_type, "")
            )
            mapping = _pairs_to_dict(result)
        else:
            a = await _upstash_cmd(
                "HGETALL", _k_hash_url_lookup("auto", "")
            )
            m = await _upstash_cmd(
                "HGETALL", _k_hash_url_lookup("manual", "")
            )
            mapping = {**_pairs_to_dict(a), **_pairs_to_dict(m)}
        _cache_set(cache_key, mapping)
        return dict(mapping)
    except _UpstashUnavailable as e:
        logger.debug(f"[STATE] get_all_hashes falling back to SQLite: {e}")
        return await sqlite_backend.get_all_hashes(cache_type)


def _pairs_to_dict(pairs: Any) -> Dict[str, str]:
    """Upstash HGETALL returns a flat [k1, v1, k2, v2, …] list."""
    if not pairs:
        return {}
    if isinstance(pairs, dict):
        return pairs
    return {pairs[i]: pairs[i + 1] for i in range(0, len(pairs), 2)}


async def set_hashes_batch(hashes: Dict[str, str], cache_type: str = "auto") -> None:
    """Store many hashes in one write. One SQLite batch + one HSET per entry
    (HSET with multiple field/value pairs is a single Upstash command)."""
    if not hashes:
        return

    # Warm local cache for any subsequent reads in this process.
    for url, h in hashes.items():
        _cache_set(f"hash::{cache_type}::{url}", h)
        _cache_set(f"hash::ANY::{url}", h)
    _cache_invalidate(f"all_hashes::{cache_type}")

    await sqlite_backend.set_hashes_batch(hashes, cache_type)

    try:
        args: List[Any] = ["HSET", _k_hash_url_lookup(cache_type, "")]
        for url, h in hashes.items():
            args.append(url)
            args.append(h)
        await _upstash_cmd(*args)
    except _UpstashUnavailable as e:
        logger.warning(
            f"[STATE] set_hashes_batch ({len(hashes)}) queued for reconcile: {e}"
        )
        # Enqueue each individually — the reconciler is per-op.
        for url, h in hashes.items():
            await _enqueue_dirty("set_hash", (url, h, cache_type))


# ── Posted MDs ───────────────────────────────────────────────────────────────

async def get_posted_mds() -> Set[str]:
    cache_key = "posted_mds"
    hit, val = _cache_get(cache_key)
    if hit:
        return set(val)
    try:
        result = await _upstash_cmd("SMEMBERS", _k_posted_mds())
        members = set(result or [])
        _cache_set(cache_key, members)
        return set(members)
    except _UpstashUnavailable as e:
        logger.debug(f"[STATE] get_posted_mds falling back to SQLite: {e}")
        val = await sqlite_backend.get_posted_mds()
        _cache_set(cache_key, val, ttl=CACHE_TTL_SECONDS / 2)
        return val


async def add_posted_md(md_number: str) -> None:
    _cache_invalidate("posted_mds")
    await sqlite_backend.add_posted_md(md_number)
    try:
        await _upstash_cmd("SADD", _k_posted_mds(), md_number)
    except _UpstashUnavailable as e:
        logger.warning(f"[STATE] add_posted_md({md_number}) queued: {e}")
        await _enqueue_dirty("add_posted_md", (md_number,))


async def prune_posted_mds(max_size: int = 200) -> None:
    # SQLite prune first — it's the fallback source of truth.
    await sqlite_backend.prune_posted_mds(max_size)
    _cache_invalidate("posted_mds")
    # For Upstash, we don't bother pruning — SETs are small and Redis
    # memory is free-tier-generous. If needed later, the approach would
    # be: SMEMBERS → sort → SREM the oldest.


# ── Posted watches ───────────────────────────────────────────────────────────

async def get_posted_watches() -> Set[str]:
    cache_key = "posted_watches"
    hit, val = _cache_get(cache_key)
    if hit:
        return set(val)
    try:
        result = await _upstash_cmd("SMEMBERS", _k_posted_watches())
        members = set(result or [])
        _cache_set(cache_key, members)
        return set(members)
    except _UpstashUnavailable as e:
        logger.debug(f"[STATE] get_posted_watches falling back to SQLite: {e}")
        val = await sqlite_backend.get_posted_watches()
        _cache_set(cache_key, val, ttl=CACHE_TTL_SECONDS / 2)
        return val


async def add_posted_watch(watch_number: str) -> None:
    _cache_invalidate("posted_watches")
    await sqlite_backend.add_posted_watch(watch_number)
    try:
        await _upstash_cmd("SADD", _k_posted_watches(), watch_number)
    except _UpstashUnavailable as e:
        logger.warning(f"[STATE] add_posted_watch({watch_number}) queued: {e}")
        await _enqueue_dirty("add_posted_watch", (watch_number,))


async def prune_posted_watches(max_size: int = 200) -> None:
    await sqlite_backend.prune_posted_watches(max_size)
    _cache_invalidate("posted_watches")


# ── Posted surveys ───────────────────────────────────────────────────────────

async def get_posted_surveys() -> Set[str]:
    cache_key = "posted_surveys"
    hit, val = _cache_get(cache_key)
    if hit:
        return set(val)
    try:
        result = await _upstash_cmd("SMEMBERS", _k_posted_surveys())
        members = set(result or [])
        _cache_set(cache_key, members)
        return set(members)
    except _UpstashUnavailable as e:
        logger.debug(f"[STATE] get_posted_surveys falling back to SQLite: {e}")
        val = await sqlite_backend.get_posted_surveys()
        _cache_set(cache_key, val, ttl=CACHE_TTL_SECONDS / 2)
        return val


async def add_posted_survey(dat_guid: str) -> None:
    _cache_invalidate("posted_surveys")
    await sqlite_backend.add_posted_survey(dat_guid)
    try:
        await _upstash_cmd("SADD", _k_posted_surveys(), dat_guid)
    except _UpstashUnavailable as e:
        logger.warning(f"[STATE] add_posted_survey({dat_guid}) queued: {e}")
        await _enqueue_dirty("add_posted_survey", (dat_guid,))


async def prune_posted_surveys(max_size: int = 100) -> None:
    await sqlite_backend.prune_posted_surveys(max_size)
    _cache_invalidate("posted_surveys")


# ── Posted warnings ──────────────────────────────────────────────────────────

async def get_all_posted_warnings() -> Dict[str, dict]:
    cache_key = "posted_warnings"
    hit, val = _cache_get(cache_key)
    if hit:
        return dict(val)
    try:
        # We store as a hash in Upstash: {vtec_id -> JSON string}
        result = await _upstash_cmd("HGETALL", _k_posted_warnings())
        # HGETALL returns [k1, v1, k2, v2, ...]
        mapping = {}
        if result:
            for i in range(0, len(result), 2):
                vtec_id = result[i]
                try:
                    mapping[vtec_id] = json.loads(result[i + 1])
                except json.JSONDecodeError:
                    continue
        _cache_set(cache_key, mapping)
        return dict(mapping)
    except _UpstashUnavailable as e:
        logger.debug(f"[STATE] get_all_posted_warnings falling back to SQLite: {e}")
        val = await sqlite_backend.get_all_posted_warnings()
        _cache_set(cache_key, val, ttl=CACHE_TTL_SECONDS / 2)
        return val


async def add_posted_warning(
    vtec_id: str, message_id: int, channel_id: int, posted_at: float = 0.0
) -> None:
    _cache_invalidate("posted_warnings")
    await sqlite_backend.add_posted_warning(vtec_id, message_id, channel_id, posted_at)
    data = {"message_id": message_id, "channel_id": channel_id}
    try:
        await _upstash_cmd("HSET", _k_posted_warnings(), vtec_id, json.dumps(data))
    except _UpstashUnavailable as e:
        logger.warning(f"[STATE] add_posted_warning({vtec_id}) queued: {e}")
        await _enqueue_dirty("add_posted_warning", (vtec_id, message_id, channel_id, posted_at))


async def prune_posted_warnings(max_size: int = 500) -> None:
    await sqlite_backend.prune_posted_warnings(max_size)
    _cache_invalidate("posted_warnings")


# ── Key/value state ──────────────────────────────────────────────────────────

async def get_state(key: str) -> Optional[str]:
    cache_key = f"state::{key}"
    hit, val = _cache_get(cache_key)
    if hit:
        return val
    try:
        result = await _upstash_cmd("GET", _k_state(key))
        _cache_set(cache_key, result)
        return result
    except _UpstashUnavailable as e:
        logger.debug(f"[STATE] get_state({key}) falling back to SQLite: {e}")
        val = await sqlite_backend.get_state(key)
        _cache_set(cache_key, val, ttl=CACHE_TTL_SECONDS / 2)
        return val


async def set_state(key: str, value: str) -> None:
    _cache_set(f"state::{key}", value)
    await sqlite_backend.set_state(key, value)
    try:
        await _upstash_cmd("SET", _k_state(key), value)
    except _UpstashUnavailable as e:
        logger.warning(f"[STATE] set_state({key}) queued: {e}")
        await _enqueue_dirty("set_state", (key, value))


async def delete_state(key: str) -> None:
    _cache_invalidate(f"state::{key}")
    await sqlite_backend.delete_state(key)
    try:
        await _upstash_cmd("DEL", _k_state(key))
    except _UpstashUnavailable as e:
        logger.warning(f"[STATE] delete_state({key}) queued: {e}")
        await _enqueue_dirty("delete_state", (key,))


# ── Posted URLs (per day) ────────────────────────────────────────────────────

async def get_posted_urls(day_key: str) -> List[str]:
    cache_key = f"posted_urls::{day_key}"
    hit, val = _cache_get(cache_key)
    if hit:
        return list(val)
    try:
        result = await _upstash_cmd("GET", _k_posted_urls(day_key))
        urls = json.loads(result) if result else []
        _cache_set(cache_key, urls)
        return list(urls)
    except _UpstashUnavailable as e:
        logger.debug(f"[STATE] get_posted_urls({day_key}) falling back: {e}")
        val = await sqlite_backend.get_posted_urls(day_key)
        _cache_set(cache_key, val, ttl=CACHE_TTL_SECONDS / 2)
        return val


async def set_posted_urls(day_key: str, urls: List[str]) -> None:
    _cache_set(f"posted_urls::{day_key}", list(urls))
    await sqlite_backend.set_posted_urls(day_key, urls)
    try:
        await _upstash_cmd("SET", _k_posted_urls(day_key), json.dumps(urls))
    except _UpstashUnavailable as e:
        logger.warning(f"[STATE] set_posted_urls({day_key}) queued: {e}")
        await _enqueue_dirty("set_posted_urls", (day_key, urls))


# ── Product text cache (TTL) ─────────────────────────────────────────────────

async def get_product_cache(product_id: str) -> Optional[str]:
    cache_key = f"product_cache::{product_id}"
    hit, val = _cache_get(cache_key)
    if hit:
        return val
    try:
        # Upstash respects the EX we set on write; expired keys return None.
        result = await _upstash_cmd("GET", _k_product_cache(product_id))
        _cache_set(cache_key, result, ttl=min(CACHE_TTL_SECONDS, 30.0))
        return result
    except _UpstashUnavailable as e:
        logger.debug(f"[STATE] get_product_cache({product_id}) fallback: {e}")
        val = await sqlite_backend.get_product_cache(product_id)
        _cache_set(cache_key, val, ttl=CACHE_TTL_SECONDS / 2)
        return val


async def set_product_cache(product_id: str, text: str, ttl: int = 600) -> None:
    _cache_set(f"product_cache::{product_id}", text, ttl=min(CACHE_TTL_SECONDS, ttl))
    await sqlite_backend.set_product_cache(product_id, text, ttl)
    try:
        await _upstash_cmd(
            "SET", _k_product_cache(product_id), text, "EX", int(ttl)
        )
    except _UpstashUnavailable as e:
        logger.warning(f"[STATE] set_product_cache({product_id}) queued: {e}")
        await _enqueue_dirty("set_product_cache", (product_id, text, ttl))


# ── HTTP validators (ETag / Last-Modified) ─────────────────────────────────
# SQLite-only by design — the conditional-GET flow runs every 60s per URL,
# and pushing every validator update through Upstash would blow the free-
# tier budget. SQLite is the authoritative source here.


async def get_validators(url: str) -> Optional[Dict[str, str]]:
    return await sqlite_backend.get_validators(url)


async def get_all_validators() -> Dict[str, Dict[str, str]]:
    return await sqlite_backend.get_all_validators()


async def set_validators(url: str, etag: str, last_modified: str) -> None:
    await sqlite_backend.set_validators(url, etag, last_modified)


# ── Startup resync ───────────────────────────────────────────────────────────

async def resync_to_upstash() -> Dict[str, int]:
    """Push everything SQLite has to Upstash.

    Intended for process-startup so that any writes that happened during
    an Upstash outage (and were queued only in the now-lost in-memory
    dirty list) still make it up. Returns counts of items pushed per
    category for logging.
    """
    counts = {"hashes": 0, "posted_mds": 0, "posted_watches": 0, "posted_surveys": 0, "state": 0, "urls": 0}
    try:
        for cache_type in ("auto", "manual"):
            hashes = await sqlite_backend.get_all_hashes(cache_type)
            if hashes:
                args: List[Any] = ["HSET", _k_hash_url_lookup(cache_type, "")]
                for url, h in hashes.items():
                    args.append(url)
                    args.append(h)
                await _upstash_cmd(*args)
                counts["hashes"] += len(hashes)

        mds = await sqlite_backend.get_posted_mds()
        if mds:
            await _upstash_cmd("SADD", _k_posted_mds(), *mds)
            counts["posted_mds"] = len(mds)

        watches = await sqlite_backend.get_posted_watches()
        if watches:
            await _upstash_cmd("SADD", _k_posted_watches(), *watches)
            counts["posted_watches"] = len(watches)

        surveys = await sqlite_backend.get_posted_surveys()
        if surveys:
            await _upstash_cmd("SADD", _k_posted_surveys(), *surveys)
            counts["posted_surveys"] = len(surveys)

        states = await sqlite_backend.get_all_state()
        if states:
            args = ["MSET"]
            for key, value in states.items():
                args.append(_k_state(key))
                args.append(value)
            await _upstash_cmd(*args)
            counts["state"] = len(states)

        urls_map = await sqlite_backend.get_all_posted_urls()
        if urls_map:
            args = ["MSET"]
            for day_key, urls in urls_map.items():
                args.append(_k_posted_urls(day_key))
                args.append(json.dumps(urls))
            await _upstash_cmd(*args)
            counts["urls"] = len(urls_map)

        logger.info(f"[STATE] Resync → Upstash: {counts}")
        return counts
    except _UpstashUnavailable as e:
        logger.warning(f"[STATE] Resync skipped — Upstash unavailable: {e}")
        return counts
