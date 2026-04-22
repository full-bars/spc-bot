"""
Failover cog (simplified — Upstash-backed state edition).

With shared state in Upstash (see utils.state_store) the primary and
standby no longer need to ship in-memory state between themselves.
The HTTP `/state` and `/sync` endpoints, the cloudflared tunnel, and
all the hydration machinery are gone.

What this cog still does
------------------------
Leader election via a short-lived Upstash key:

    spcbot:primary_url   EX HEARTBEAT_TTL

The "primary" is whichever node currently holds the key. The value is
a per-process identifier so we can detect whether we still own the
lease or someone else has taken it. The key name `primary_url` is
retained for migration compatibility — the old code reads it too and
interprets its presence correctly.

Promotion semantics
-------------------
- Primary: writes the lease every SYNC_INTERVAL with EX HEARTBEAT_TTL.
- Standby: reads the lease every SYNC_INTERVAL. If the key is missing
  for `MAX_FAILURES` consecutive cycles (primary has been silent for
  at least HEARTBEAT_TTL), the standby promotes: invalidates the
  process cache so fresh reads come from Upstash, loads all cogs, and
  begins holding the lease itself.
- If a second holder appears the current holder steps back down.

The v4.13.2 "liveness vs. reachability" split is no longer needed:
liveness is Upstash-mediated directly, and there's nothing to hydrate
from.
"""

from __future__ import annotations

import logging
import os
import socket
import time
import uuid

import aiohttp
from discord.ext import commands, tasks

from cogs import ALL_EXTENSIONS
from utils import state_store

logger = logging.getLogger("spc_bot")

UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL", "")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")
FAILOVER_TOKEN = os.getenv("FAILOVER_TOKEN", "")

HEARTBEAT_TTL = 420  # seconds
SYNC_INTERVAL = 30   # seconds

STARTUP_GRACE_SECONDS = 120
MAX_FAILURES = max(5, HEARTBEAT_TTL // (2 * SYNC_INTERVAL))

UPSTASH_HEADERS = {"Authorization": f"Bearer {UPSTASH_TOKEN}"}

LEASE_KEY = "spcbot:primary_url"


def _require_failover_token() -> str:
    if not FAILOVER_TOKEN or FAILOVER_TOKEN == "changeme":
        raise RuntimeError(
            "FAILOVER_TOKEN environment variable must be set to a strong, "
            "non-default value. Refusing to participate in leader election "
            "with a known/missing token."
        )
    return FAILOVER_TOKEN


def _node_identity() -> str:
    """Stable per-process identifier used as the lease value so we can
    detect whether we still hold the lease vs. some other holder."""
    host = socket.gethostname()
    return f"{host}:{uuid.uuid4().hex[:8]}"


class FailoverCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._primary_failures = 0
        self._identity = _node_identity()
        self._cog_load_monotonic: float | None = None
        # Dedicated session for leader-election traffic, kept separate from
        # utils.http.http_session so lease operations don't interfere (and
        # aren't interfered with) by the shared pool if it misbehaves.
        self._session: aiohttp.ClientSession | None = None

    async def cog_load(self):
        _require_failover_token()
        self._cog_load_monotonic = time.monotonic()
        self._session = aiohttp.ClientSession()
        self.sync_loop.start()

    async def cog_unload(self):
        self.sync_loop.cancel()
        if self.bot.state.is_primary:
            await self._release_lease()
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    # ── Upstash ──────────────────────────────────────────────────────────

    async def _upstash(self, *args) -> object | None:
        """Single REST command. Uses a dedicated session (see cog_load)
        so leader election keeps working even if utils.http is
        misbehaving (the two paths are independent)."""
        if not UPSTASH_URL or not UPSTASH_TOKEN:
            return None
        if self._session is None or self._session.closed:
            # Can happen during cog reload or an unexpected unload; recreate.
            self._session = aiohttp.ClientSession()
        try:
            headers = {**UPSTASH_HEADERS, "Content-Type": "application/json"}
            async with self._session.post(
                UPSTASH_URL,
                headers=headers,
                json=[str(a) for a in args],
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return data.get("result")
        except Exception as e:
            logger.warning(f"[FAILOVER] Upstash error: {e!r}")
            return None

    async def _write_lease(self) -> None:
        await self._upstash(
            "SET", LEASE_KEY, self._identity, "EX", str(HEARTBEAT_TTL)
        )

    async def _read_lease_holder(self) -> str | None:
        return await self._upstash("GET", LEASE_KEY)

    async def _release_lease(self) -> None:
        # Only release if it's still ours — otherwise we'd clobber a
        # brand-new primary that just took over.
        holder = await self._read_lease_holder()
        if holder == self._identity:
            await self._upstash("DEL", LEASE_KEY)
            logger.info("[FAILOVER] Released primary lease on shutdown")

    # ── Sync loop ────────────────────────────────────────────────────────

    @tasks.loop(seconds=SYNC_INTERVAL)
    async def sync_loop(self):
        await self.bot.wait_until_ready()
        try:
            if self.bot.state.is_primary:
                await self._primary_cycle()
            else:
                await self._standby_cycle()
        except Exception as e:
            logger.exception(f"[FAILOVER] Sync loop error: {e}")

    async def _primary_cycle(self) -> None:
        """Hold the lease; step down if someone else grabbed it."""
        holder = await self._read_lease_holder()
        if holder and holder != self._identity:
            logger.warning(
                f"[FAILOVER] Another node ({holder}) holds the lease — demoting"
            )
            await self._demote()
            return
        await self._write_lease()

    def _in_startup_grace(self) -> bool:
        if self._cog_load_monotonic is None:
            return False
        return (time.monotonic() - self._cog_load_monotonic) < STARTUP_GRACE_SECONDS

    def _register_failure(self, reason: str) -> int:
        if self._in_startup_grace():
            logger.info(
                f"[FAILOVER] {reason} — in startup grace "
                f"({STARTUP_GRACE_SECONDS}s), not counting toward promotion"
            )
            return 0
        self._primary_failures += 1
        logger.warning(
            f"[FAILOVER] {reason} "
            f"(failure {self._primary_failures}/{MAX_FAILURES})"
        )
        return self._primary_failures

    async def _standby_cycle(self) -> None:
        holder = await self._read_lease_holder()
        if holder:
            if self._primary_failures > 0:
                logger.info(
                    f"[FAILOVER] Primary lease held by {holder}; clearing "
                    f"{self._primary_failures} prior failures"
                )
            self._primary_failures = 0
            return

        # Key missing = primary silent for ≥ HEARTBEAT_TTL (Upstash expired it).
        count = self._register_failure("Primary lease expired in Upstash")
        if count >= MAX_FAILURES:
            await self._promote()

    # ── Promotion / demotion ─────────────────────────────────────────────

    async def _promote(self) -> None:
        logger.warning("[FAILOVER] !!! PROMOTING TO PRIMARY !!!")
        self.bot.state.is_primary = True

        # Drop stale cache so fresh reads re-hit Upstash.
        state_store.invalidate_all_caches()

        # Claim the lease immediately (before loading cogs so there's no
        # window where another watcher sees the key missing).
        await self._write_lease()

        # Refresh the in-process BotState mirrors. Cogs still read
        # `bot.state.posted_mds`, `bot.state.auto_cache`, etc. as local
        # collections; those were populated from SQLite at boot and are
        # now stale relative to what the old primary wrote to Upstash
        # while we were in standby.
        try:
            await self._rehydrate_bot_state()
        except Exception as e:
            logger.exception(f"[FAILOVER] Rehydrate on promotion failed: {e}")

        # Push anything SQLite has that Upstash is missing. Handles the
        # edge case where this node's prior writes during an Upstash
        # outage are queued only on this machine.
        try:
            await state_store.resync_to_upstash()
        except Exception as e:
            logger.exception(f"[FAILOVER] Resync on promotion failed: {e}")

        for ext in ALL_EXTENSIONS:
            try:
                await self.bot.load_extension(ext)
                logger.info(f"[FAILOVER] Loaded {ext}")
            except Exception as e:
                logger.exception(f"[FAILOVER] Failed to load {ext}: {e}")

        try:
            synced = await self.bot.tree.sync()
            logger.info(f"[FAILOVER] Synced {len(synced)} slash commands")
        except Exception as e:
            logger.exception(f"[FAILOVER] Failed to sync commands: {e}")

    async def _rehydrate_bot_state(self) -> None:
        """Pull authoritative state from Upstash into BotState mirrors.

        Cogs read the BotState collections synchronously; on promotion
        we need them to reflect what the outgoing primary wrote to
        Upstash, not what we snapshotted at boot.
        """
        st = self.bot.state
        st.auto_cache = await state_store.get_all_hashes("auto")
        st.manual_cache = await state_store.get_all_hashes("manual")
        st.posted_mds = await state_store.get_posted_mds()
        st.posted_watches = await state_store.get_posted_watches()

        last_seq = await state_store.get_state("iembot_last_seqnum")
        if isinstance(last_seq, str) and last_seq.isdigit():
            st.iembot_last_seqnum = max(st.iembot_last_seqnum, int(last_seq))

        for day_key in ("day1", "day2", "day3"):
            urls = await state_store.get_posted_urls(day_key)
            if urls:
                st.last_posted_urls[day_key] = urls
        logger.info("[FAILOVER] Rehydrated BotState from Upstash")

    async def _demote(self) -> None:
        logger.info("[FAILOVER] Demoting to STANDBY")
        self.bot.state.is_primary = False
        for ext in ALL_EXTENSIONS:
            try:
                await self.bot.unload_extension(ext)
            except Exception:
                pass
        self._primary_failures = 0


async def setup(bot):
    await bot.add_cog(FailoverCog(bot))
