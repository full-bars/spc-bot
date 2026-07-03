"""
Failover cog — Redis-backed leader election.

With shared state in Redis (see utils.state_store) the primary and
standby no longer need to ship in-memory state between themselves.

What this cog does
------------------
Leader election via a short-lived Redis key:

    spcbot:primary_url   EX HEARTBEAT_TTL

The "primary" is whichever node currently holds the key. The value is
a per-process identifier so we can detect whether we still own the
lease or someone else has taken it.

Promotion semantics
-------------------
- Primary: renews the lease every SYNC_INTERVAL with a Lua conditional
  SET (only renews if the key still equals our identity, preventing a
  demoted node from accidentally reclaiming after a split-brain).
- Standby: reads the lease every SYNC_INTERVAL. If the key is missing
  for MAX_FAILURES consecutive cycles the standby promotes.
- If a second holder appears the current holder steps back down.

Lease safety
------------
All lease operations use atomic Lua scripts to avoid TOCTOU races:
- Release: check-and-delete in a single script (C5 fix).
- Renewal: conditional SET only if we still hold the key (C6 fix).
- Reclaim: SET NX (already correct).
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
import uuid

import discord
import redis.asyncio as aioredis
import redis.exceptions
from discord import app_commands
from discord.ext import commands, tasks
from discord.ext.commands import ExtensionNotLoaded

from cogs import ALL_EXTENSIONS
from utils import state_store

logger = logging.getLogger("spc_bot")

FAILOVER_TOKEN = os.getenv("FAILOVER_TOKEN", "")

# On standby nodes, point this at the PRIMARY node's Redis (via Tailscale).
# The failover cog uses this URL exclusively for lease/election traffic so that
# connection failures to the primary's Redis are the promotion trigger — not the
# stale TTL on a local replica, which would delay failover by up to HEARTBEAT_TTL.
# If unset, falls back to state_store.REDIS_URL (correct for the primary node).
ELECTION_REDIS_URL = os.getenv("ELECTION_REDIS_URL", "")

HEARTBEAT_TTL = 420  # seconds — lease expiry
SYNC_INTERVAL = 30  # seconds — heartbeat / check cadence

STARTUP_GRACE_SECONDS = 120
MAX_FAILURES = max(5, HEARTBEAT_TTL // (2 * SYNC_INTERVAL))

LEASE_KEY = "spcbot:primary_url"
NODES_KEY = "spcbot:nodes"
MANUAL_KEY = "spcbot:manual_primary"

# Lua: atomic check-and-delete (C5 fix).
# Returns 1 if deleted, 0 if key belonged to someone else.
_LUA_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""

# Lua: conditional renewal — only renew if we still hold the key (C6 fix).
# Returns "OK" on renewal, nil if key belongs to someone else or is gone.
_LUA_RENEW = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('set', KEYS[1], ARGV[1], 'EX', tonumber(ARGV[2]))
else
    return nil
end
"""


def _require_failover_token() -> str:
    if not FAILOVER_TOKEN or FAILOVER_TOKEN == "changeme":
        raise RuntimeError(
            "FAILOVER_TOKEN must be set to a strong non-default value. "
            "Refusing to participate in leader election."
        )
    return FAILOVER_TOKEN


class FailoverCog(commands.Cog):
    MANAGED_TASK_NAMES = [("sync_loop", "sync_loop")]

    def __init__(self, bot):
        self.bot = bot
        self._primary_failures = 0
        # Fresh per-instance UUID so importlib.reload() during tests yields
        # a new identity rather than reusing a stale module-level constant.
        self._process_uuid = uuid.uuid4().hex[:8]
        self._identity = self._node_identity(bot.state.is_primary)
        self._cog_load_monotonic: float | None = None
        # Serializes _promote/_demote so a manual override and a lease-expiry
        # firing in the same sync_loop tick cannot race each other.
        self._role_lock: asyncio.Lock = asyncio.Lock()
        # Dedicated Redis client for leader-election traffic — kept separate
        # from utils.state_store so lease operations stay independent of the
        # shared pool's health.
        self._redis: aioredis.Redis | None = None

    def _node_identity(self, is_primary: bool) -> str:
        """Per-instance lease value: role:hostname:uuid."""
        role = "P" if is_primary else "S"
        return f"{role}:{socket.gethostname()}:{self._process_uuid}"

    def _build_redis(self) -> aioredis.Redis:
        # Use ELECTION_REDIS_URL if set (standby nodes point this at the primary's
        # Redis so connection errors are the promotion signal), else local Redis.
        url = ELECTION_REDIS_URL or state_store.REDIS_URL
        pool = aioredis.ConnectionPool.from_url(
            url,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
            decode_responses=True,
            max_connections=5,
        )
        return aioredis.Redis(connection_pool=pool)

    def _build_local_redis(self) -> aioredis.Redis:
        """Always connects to the local Redis instance.
        Used for REPLICAOF NO ONE on promotion — the election client may be
        pointing to the (now-dead) primary, so we need a separate local handle."""
        pool = aioredis.ConnectionPool.from_url(
            state_store.REDIS_URL,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
            decode_responses=True,
            max_connections=2,
        )
        return aioredis.Redis(connection_pool=pool)

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = self._build_redis()
        return self._redis

    async def _cleanup_own_stale_entries(self) -> None:
        """Remove nodes hash entries for this hostname from prior process instances."""
        try:
            client = await self._get_redis()
            nodes: dict = await client.hgetall(NODES_KEY) or {}
            my_hostname = socket.gethostname()
            to_delete = [
                field
                for field in nodes
                if field != self._identity and field.split(":")[1:2] == [my_hostname]
            ]
            if to_delete:
                await client.hdel(NODES_KEY, *to_delete)
                logger.debug(
                    f"[FAILOVER] Removed {len(to_delete)} stale self-entries from nodes hash"
                )
        except Exception as e:
            logger.debug(f"[FAILOVER] Could not clean own stale entries: {e}")

    async def cog_load(self):
        _require_failover_token()
        self._cog_load_monotonic = time.monotonic()
        self._redis = self._build_redis()
        await self._cleanup_own_stale_entries()
        self.sync_loop.start()

    async def cog_unload(self):
        self.sync_loop.cancel()
        if self.bot.state.is_primary:
            await self._release_lease()
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                logger.warning("Failed to close Redis connection", exc_info=True)
            self._redis = None

    def _is_our_node(self, target: str) -> bool:
        return target == self._identity or target == socket.gethostname()

    # ── Low-level Redis helpers ───────────────────────────────────────────

    async def _exec(self, *args) -> object | None:
        """Execute a Redis command via the dedicated client. Returns None on
        any connection error so lease logic degrades gracefully."""
        try:
            client = await self._get_redis()
            cmd = str(args[0]).upper()
            cmd_args = [str(a) for a in args[1:]]
            return await client.execute_command(cmd, *cmd_args)
        except redis.exceptions.ReadOnlyError as e:
            # Expected on a replica standby node — not a problem until promotion.
            logger.debug(f"[FAILOVER] Redis read-only (replica standby): {e!r}")
            return None
        except (
            redis.exceptions.ConnectionError,
            redis.exceptions.TimeoutError,
            OSError,
        ) as e:
            logger.warning(f"[FAILOVER] Redis error: {e!r}")
            return None

    async def _write_lease(self) -> bool:
        """Unconditional lease write — used only on startup/promotion where
        we are claiming the key for the first time. Returns True if Redis
        confirmed the write; False means Redis was unreachable."""
        result = await self._exec("SET", LEASE_KEY, self._identity, "EX", str(HEARTBEAT_TTL))
        return result is not None

    async def _renew_lease(self) -> bool:
        """Conditionally renew lease only if we still hold it (C6 fix).
        Returns True if renewed, False if someone else holds the key."""
        try:
            client = await self._get_redis()
            result = await client.eval(_LUA_RENEW, 1, LEASE_KEY, self._identity, str(HEARTBEAT_TTL))
            return result is not None
        except (
            redis.exceptions.ConnectionError,
            redis.exceptions.TimeoutError,
            redis.exceptions.ReadOnlyError,
            OSError,
        ) as e:
            logger.warning(f"[FAILOVER] Renew lease error: {e!r}")
            return False

    async def _read_lease_holder(self) -> str | None:
        result = await self._exec("GET", LEASE_KEY)
        return str(result) if result is not None else None

    async def _release_lease(self) -> None:
        """Atomically release the lease only if we still hold it (C5 fix)."""
        try:
            client = await self._get_redis()
            result = await client.eval(_LUA_RELEASE, 1, LEASE_KEY, self._identity)
            if result:
                logger.info("[FAILOVER] Released primary lease on shutdown")
        except (
            redis.exceptions.ConnectionError,
            redis.exceptions.TimeoutError,
            redis.exceptions.ReadOnlyError,
            OSError,
        ) as e:
            logger.warning(f"[FAILOVER] Release lease error: {e!r}")

    # ── Startup lease check ───────────────────────────────────────────────

    async def startup_lease_check(self) -> bool:
        """Probe the lease before other cogs load. Returns True if this node
        should boot as primary."""
        manual = await self._exec("GET", MANUAL_KEY)
        if manual:
            if self._is_our_node(manual):
                logger.info("[FAILOVER] Startup: manual override names us as Primary")
                if not await self._write_lease():
                    logger.warning(
                        "[FAILOVER] Startup (manual): Redis unreachable — cannot write lease; booting as STANDBY"
                    )
                    self.bot.state.is_primary = False
                    return False
                self.bot.state.is_primary = True
                try:
                    await state_store.resync_to_redis()
                except Exception as e:
                    logger.warning(f"[FAILOVER] Startup resync (manual) failed: {e}")
                return True
            logger.info(
                f"[FAILOVER] Startup: manual override names '{manual}' — booting as STANDBY"
            )
            self.bot.state.is_primary = False
            return False

        holder = await self._read_lease_holder()
        if holder and holder != self._identity:
            if self._identity.startswith("P:") and holder.startswith("S:"):
                logger.warning(
                    f"[FAILOVER] Startup: lease held by Standby '{holder}' — pre-empting"
                )
            else:
                logger.warning(f"[FAILOVER] Startup: lease held by '{holder}' — booting as STANDBY")
                self.bot.state.is_primary = False
                return False

        if not self.bot.state.is_primary:
            logger.info("[FAILOVER] Startup: lease free but configured as STANDBY")
            return False

        logger.info(f"[FAILOVER] Startup: claiming lease as Primary ('{self._identity}')")
        if not await self._write_lease():
            logger.warning(
                "[FAILOVER] Startup: Redis unreachable — could not write lease; "
                "booting as STANDBY to avoid split-brain"
            )
            self.bot.state.is_primary = False
            return False
        try:
            await state_store.resync_to_redis()
        except Exception as e:
            logger.warning(f"[FAILOVER] Startup resync failed: {e}")
        return True

    # ── Sync loop ─────────────────────────────────────────────────────────

    @tasks.loop(seconds=SYNC_INTERVAL)
    async def sync_loop(self):
        await self.bot.wait_until_ready()
        try:
            await self._exec("HSET", NODES_KEY, self._identity, str(int(time.time())))

            # Periodically clean up stale entries (every 5 sync cycles = ~2.5 min).
            # Use the deterministic loop counter rather than wall-clock modulo —
            # clock drift / NTP slews could otherwise fire this 0 or 2 times
            # in a window.
            if self.sync_loop.current_loop % 5 == 0:
                await self._cleanup_stale_nodes()

            manual_primary = await self._exec("GET", MANUAL_KEY)

            if manual_primary:
                if self._is_our_node(manual_primary):
                    if not self.bot.state.is_primary:
                        logger.warning(f"[FAILOVER] Manual override: promoting '{self._identity}'")
                        # force=True so the conditional SET NX in _do_promote
                        # is bypassed — an operator's explicit override must
                        # be able to take the lease from a currently-held node.
                        await self._promote(force=True)
                else:
                    if self.bot.state.is_primary:
                        logger.warning(
                            f"[FAILOVER] Manual override: demoting to standby (target: '{manual_primary}')"
                        )
                        await self._demote()
                    return

            if self.bot.state.is_primary:
                await self._primary_cycle()
                # Drain any writes that failed to reach Redis since the last
                # cycle. Without this, a transient Redis blip on a long-lived
                # Primary strands dirty writes in SQLite until the next
                # restart/promotion — the Standby's Redis silently misses them
                # and could re-post already-handled items after a failover.
                # Re-check is_primary: _primary_cycle may have just demoted us.
                if self.bot.state.is_primary:
                    await self._drain_dirty_writes()
            else:
                await self._standby_cycle()
        except Exception as e:
            logger.exception(f"[FAILOVER] Sync loop error: {e}")

    async def _drain_dirty_writes(self) -> None:
        """Replay writes that failed to reach Redis while we hold the lease.

        resync_to_redis() is a cheap no-op (a single SQLite SELECT) when the
        dirty queue is empty, and short-circuits on the first _RedisUnavailable
        during an outage, so it is safe to call every sync cycle. Errors are
        swallowed here — sync_loop's outer handler is the backstop.
        """
        try:
            result = await state_store.resync_to_redis()
            drained = result.get("dirty", 0)
            if drained:
                logger.info(f"[FAILOVER] Reconciler drained {drained} pending write(s) to Redis")
        except Exception as e:
            logger.warning(f"[FAILOVER] Dirty-write drain failed: {e}")

    async def _primary_cycle(self) -> None:
        holder = await self._read_lease_holder()
        if holder and holder != self._identity:
            logger.warning(f"[FAILOVER] Another node ({holder}) holds lease — demoting")
            await self._demote()
            return
        if holder is not None:
            # We hold the lease — renew conditionally.
            renewed = await self._renew_lease()
            if renewed:
                self.bot.state.lease_renewals += 1
            else:
                # Lost the lease between read and renew — re-check and demote.
                new_holder = await self._read_lease_holder()
                if new_holder and new_holder != self._identity:
                    logger.warning(
                        f"[FAILOVER] Lost lease to {new_holder} during renewal — demoting"
                    )
                    await self._demote()
            return
        # Lease expired — reclaim with NX so we don't stomp a legitimate holder.
        result = await self._exec("SET", LEASE_KEY, self._identity, "NX", "EX", str(HEARTBEAT_TTL))
        if result is None:
            new_holder = await self._read_lease_holder()
            if new_holder and new_holder != self._identity:
                logger.warning(f"[FAILOVER] NX failed — lease held by {new_holder} — demoting")
                await self._demote()
            return
        logger.info("[FAILOVER] Reclaimed expired lease via NX")

    def _in_startup_grace(self) -> bool:
        if self._cog_load_monotonic is None:
            return False
        return (time.monotonic() - self._cog_load_monotonic) < STARTUP_GRACE_SECONDS

    def _register_failure(self, reason: str) -> int:
        if self._in_startup_grace():
            logger.info(f"[FAILOVER] {reason} — in startup grace, not counting")
            return 0
        self._primary_failures += 1
        logger.warning(f"[FAILOVER] {reason} (failure {self._primary_failures}/{MAX_FAILURES})")
        return self._primary_failures

    async def _cleanup_stale_nodes(self) -> None:
        """Remove nodes that haven't heartbeated in over 90 seconds (3 sync cycles + grace)."""
        try:
            client = await self._get_redis()
            nodes: dict = await client.hgetall(NODES_KEY) or {}
            now = int(time.time())
            stale_threshold = 90

            stale_count = 0
            for node_id, ts_str in nodes.items():
                try:
                    timestamp = int(float(ts_str))
                    if now - timestamp > stale_threshold:
                        await self._exec("HDEL", NODES_KEY, node_id)
                        stale_count += 1
                except (ValueError, TypeError):
                    pass

            if stale_count > 0:
                logger.debug(f"[FAILOVER] Cleaned up {stale_count} stale node entries")
        except Exception as e:
            logger.debug(f"[FAILOVER] Cleanup failed: {e}")

    async def _standby_cycle(self) -> None:
        holder = await self._read_lease_holder()
        if holder:
            if self._identity.startswith("P:") and holder.startswith("S:"):
                logger.warning(
                    f"[FAILOVER] Configured Primary reclaiming lease from Standby '{holder}'"
                )
                await self._promote()
                return
            if self._primary_failures > 0:
                logger.info(
                    f"[FAILOVER] Primary lease held by {holder}; clearing {self._primary_failures} failures"
                )
            self._primary_failures = 0
            return
        count = self._register_failure("Primary lease expired")
        if count >= MAX_FAILURES:
            await self._promote()

    # ── Promotion / demotion ──────────────────────────────────────────────

    async def _promote(self, force: bool = False) -> None:
        async with self._role_lock:
            if self.bot.state.is_primary:
                return  # already primary — idempotent
            await self._do_promote(force=force)

    async def _do_promote(self, force: bool = False) -> None:
        # ── Split-brain prevention ────────────────────────────────────────
        # Claim the lease atomically BEFORE mutating any state. If two
        # standbys race to promote after the lease expires, the SET NX loser
        # aborts here — no `is_primary=True`, no cache invalidation, no cog
        # load — and stays a clean standby. `force=True` (manual override)
        # bypasses NX so an operator can forcibly take the lease from a
        # currently-held primary.
        promoting_identity = self._node_identity(True)
        if force:
            result = await self._exec(
                "SET", LEASE_KEY, promoting_identity, "EX", str(HEARTBEAT_TTL)
            )
            if result is None:
                logger.error(
                    "[FAILOVER] Forced promotion failed — Redis unreachable, cannot claim "
                    "lease. Aborting promotion to avoid split-brain."
                )
                return
            logger.warning("[FAILOVER] !!! PROMOTING TO PRIMARY (forced) !!!")
        else:
            claim_result = await self._exec(
                "SET", LEASE_KEY, promoting_identity, "NX", "EX", str(HEARTBEAT_TTL)
            )
            if claim_result != "OK":
                # NX failed — either another node holds the lease, or Redis
                # is unavailable. In both cases, the safe action is to
                # remain a standby. Try to identify the holder for the log.
                holder = await self._read_lease_holder()
                if holder:
                    logger.warning(
                        f"[FAILOVER] Promotion aborted — lease held by {holder!r}. "
                        f"Remaining standby."
                    )
                else:
                    logger.warning(
                        "[FAILOVER] Promotion aborted — could not claim lease "
                        "(Redis unavailable or contended). Remaining standby."
                    )
                return
            logger.warning("[FAILOVER] !!! PROMOTING TO PRIMARY !!!")

        self.bot.state.is_primary = True
        self.bot.state.failover_count += 1
        # Update identity so the next heartbeat HSET announces us as Primary ("P:").
        self._identity = promoting_identity
        await self._cleanup_own_stale_entries()

        from utils.events_db import restore_from_sync, set_syncthing_folder_mode  # noqa: PLC0415

        restore_from_sync()
        await set_syncthing_folder_mode("sendonly")

        # If our local Redis is a replica, detach it so writes succeed.
        # Must use a local Redis client here — the election client may point to
        # the (now-dead) primary node's Redis, which would be unreachable.
        try:
            local = self._build_local_redis()
            info = await local.info("replication")
            if info.get("role") == "slave":
                logger.warning(
                    "[FAILOVER] Promoting Redis replica to standalone master (REPLICAOF NO ONE)"
                )
                await local.execute_command("REPLICAOF", "NO", "ONE")
                await asyncio.sleep(0.5)
            await local.aclose()
        except Exception as e:
            logger.warning(f"[FAILOVER] Could not promote Redis replica: {e}")

        state_store.invalidate_all_caches()
        # Lease was already claimed atomically at the top of this method —
        # no second _write_lease() call needed (and writing it again here
        # would overwrite our own value pointlessly).

        try:
            await state_store.mirror_to_sqlite()
        except Exception as e:
            logger.exception(f"[FAILOVER] Mirroring on promotion failed: {e}")

        await asyncio.sleep(2.0)

        try:
            await self._rehydrate_bot_state()
        except Exception as e:
            logger.exception(f"[FAILOVER] Rehydrate on promotion failed: {e}")

        try:
            await state_store.resync_to_redis()
        except Exception as e:
            logger.exception(f"[FAILOVER] Resync on promotion failed: {e}")
            try:
                from main import send_bot_alert  # noqa: PLC0415

                await send_bot_alert(
                    "Failover: promoted with stale Redis state",
                    f"This node was promoted to Primary but `resync_to_redis()` failed: `{e}`.\n"
                    "Dirty writes from standby period may not have been replayed. "
                    "Monitor for duplicate posts or missing dedup entries.",
                    critical=True,
                )
            except Exception as alert_err:
                logger.warning(f"[FAILOVER] Could not send resync-failure alert: {alert_err}")

        # ── Extension Loading (Transactional) ───────────────────────────────
        loaded_exts = []
        for ext in ALL_EXTENSIONS:
            try:
                await self.bot.load_extension(ext)
                loaded_exts.append(ext)
                logger.info(f"[FAILOVER] Loaded {ext}")
            except Exception as e:
                logger.exception(f"[FAILOVER] Failed to load {ext}: {e} - Rolling back promotion.")
                # Rollback: unload what we just loaded
                for loaded in reversed(loaded_exts):
                    try:
                        await self.bot.unload_extension(loaded)
                    except Exception as rollback_err:
                        logger.error(f"[FAILOVER] Rollback failure on {loaded}: {rollback_err}")

                # Signal demotion and exit. Call _do_demote() directly —
                # _demote() would re-acquire _role_lock which is already held
                # by the enclosing _promote(), and asyncio.Lock is not
                # reentrant (would deadlock the task indefinitely).
                await self._do_demote()
                return

        nwws = self.bot.get_cog("NWWSCog")
        if nwws:
            asyncio.create_task(nwws.trigger_connection())

        try:
            synced = await self.bot.tree.sync()
            logger.info(f"[FAILOVER] Synced {len(synced)} slash commands")
        except Exception as e:
            logger.exception(f"[FAILOVER] Failed to sync commands: {e}")

    async def _rehydrate_bot_state(self) -> None:
        st = self.bot.state
        st.auto_cache = await state_store.get_all_hashes("auto")
        st.manual_cache = await state_store.get_all_hashes("manual")
        st.posted_mds = await state_store.get_posted_mds()
        st.posted_watches = await state_store.get_posted_watches()
        st.posted_reports = await state_store.get_posted_reports()
        st.posted_product_ids.extend(await state_store.get_posted_product_ids())
        st.posted_soundings = await state_store.get_posted_soundings()
        st.sounding_handled_watches = await state_store.get_sounding_handled_watches()

        last_seq = await state_store.get_state("iembot_last_seqnum")
        if isinstance(last_seq, str) and last_seq.isdigit():
            st.iembot_last_seqnum = max(st.iembot_last_seqnum, int(last_seq))

        for day_key in ("day1", "day2", "day3"):
            urls = await state_store.get_posted_urls(day_key)
            if urls:
                st.last_posted_urls[day_key] = urls

        csu_raw = await state_store.get_state("csu_mlp_posted")
        if isinstance(csu_raw, str):
            try:
                import json as _json
                from datetime import datetime, timezone

                csu_data = _json.loads(csu_raw)
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if csu_data.get("date") == today:
                    st.csu_posted.update(str(d) for d in csu_data.get("days", []))
            except (ValueError, KeyError, TypeError) as e:
                logger.debug(f"[FAILOVER] CSU state parse failed (ignored): {e}")

        logger.info("[FAILOVER] Rehydrated BotState from Redis")

    async def _demote(self) -> None:
        async with self._role_lock:
            if not self.bot.state.is_primary:
                return  # already standby — idempotent
            await self._do_demote()

    async def _do_demote(self) -> None:
        logger.info("[FAILOVER] Demoting to STANDBY")
        self.bot.state.is_primary = False
        self._identity = self._node_identity(False)
        from utils.events_db import set_syncthing_folder_mode  # noqa: PLC0415

        await set_syncthing_folder_mode("receiveonly")
        failed = []
        for ext in ALL_EXTENSIONS:
            try:
                await self.bot.unload_extension(ext)
            except ExtensionNotLoaded:
                pass
            except Exception as e:
                logger.warning(f"[FAILOVER] Failed to unload {ext} during demote: {e}")
                failed.append(ext)
        if failed:
            logger.error(
                f"[FAILOVER] {len(failed)} cog(s) failed to unload during demotion: {failed}. "
                "A stuck cog could continue posting as primary — forcing process exit."
            )
            try:
                from main import send_bot_alert  # noqa: PLC0415

                await send_bot_alert(
                    "Failover: forced exit — cog unload failed during demotion",
                    f"Could not unload {failed} during `_demote()`. "
                    "Forcing `os._exit(1)` to prevent split-brain duplicate posts. "
                    "systemd will restart the process in standby mode.",
                    critical=True,
                )
            except Exception as alert_err:
                logger.warning(f"[FAILOVER] Could not send demote-failure alert: {alert_err}")
            import os as _os  # noqa: PLC0415

            _os._exit(1)
        self._primary_failures = 0

    # ── Slash command ─────────────────────────────────────────────────────

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        """Use followup.send() when the interaction was already deferred."""
        logger.error(f"AppCommand error: Command 'failover' raised an exception: {error}")
        try:
            if interaction.response.is_done():
                await interaction.followup.send(f"❌ An error occurred: `{error}`", ephemeral=True)
            else:
                await interaction.response.send_message(
                    f"❌ An error occurred: `{error}`", ephemeral=True
                )
        except Exception as reply_err:
            logger.warning(f"[FAILOVER] Could not send error reply: {reply_err}")

    @app_commands.command(
        name="failover", description="Manually designate the Primary node (Admin only)"
    )
    async def failover_slash(self, interaction: discord.Interaction):
        raw_admin_id = os.getenv("ADMIN_USER_ID", "0")
        try:
            authorized_id = int(raw_admin_id)
        except ValueError:
            authorized_id = 0

        if interaction.user.id != authorized_id:
            await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # HGETALL with decode_responses=True returns a dict[str, str].
        try:
            client = await self._get_redis()
            nodes_raw: dict = await client.hgetall(NODES_KEY) or {}
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError, OSError):
            await interaction.followup.send("❌ Redis unavailable.", ephemeral=True)
            return

        now = int(time.time())
        active_nodes = []
        for node_id, ts_str in nodes_raw.items():
            try:
                if (now - int(ts_str)) < 300:
                    active_nodes.append(node_id)
            except (ValueError, TypeError):
                pass

        if not active_nodes:
            await interaction.followup.send(
                "❌ No nodes have sent a heartbeat in the last 5 minutes.", ephemeral=True
            )
            return

        current_manual = await self._exec("GET", MANUAL_KEY)
        current_lease = await self._read_lease_holder()

        view = FailoverView(self, active_nodes, current_manual, current_lease)
        await interaction.followup.send(
            content=(
                f"**Failover Management**\n"
                f"Current Lease Holder: `{current_lease or 'None'}`\n"
                f"Manual Override: `{current_manual or 'None (Automatic)'}`\n\n"
                f"Select a node to force it to be Primary, or clear the override "
                f"to return to automatic failover."
            ),
            view=view,
            ephemeral=True,
        )


class FailoverView(discord.ui.View):
    def __init__(
        self,
        cog: FailoverCog,
        nodes: list[str],
        current_manual: str | None,
        current_lease: str | None,
    ):
        super().__init__(timeout=60)
        self.cog = cog

        options = []
        for node in nodes:
            label = node
            if node == current_lease:
                label += " (Active Primary)"
            if node == cog._identity:
                label += " (This Node)"
            options.append(
                discord.SelectOption(
                    label=label,
                    value=node,
                    description=f"Force {node} to be Primary",
                    default=(node == current_manual),
                )
            )

        options.append(
            discord.SelectOption(
                label="❌ Clear Manual Override",
                value="CLEAR",
                description="Return to standard automatic failover",
                emoji="🔄",
            )
        )

        self.add_item(FailoverSelect(cog, options))


class FailoverSelect(discord.ui.Select):
    def __init__(self, cog: FailoverCog, options: list[discord.SelectOption]):
        super().__init__(
            placeholder="Choose a target Primary node...",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        # Defer immediately — Redis calls below can exceed the 3-second
        # component-interaction window, causing 40060 on the final edit.
        await interaction.response.defer(ephemeral=True)

        target = self.values[0]

        if target == "CLEAR":
            await self.cog._exec("DEL", MANUAL_KEY)
            logger.warning(
                f"[FAILOVER] Manual override cleared by {interaction.user} ({interaction.user.id})"
            )
            msg = "✅ Manual override cleared. Returning to automatic failover."
        else:
            # Store just the hostname so the override survives process restarts.
            # Identity format: "role:hostname:uuid" → index 1 is hostname.
            parts = target.split(":")
            hostname = parts[1] if len(parts) >= 2 else target
            await self.cog._exec("SET", MANUAL_KEY, hostname)
            logger.warning(
                f"[FAILOVER] Manual override set to '{hostname}' "
                f"by {interaction.user} ({interaction.user.id})"
            )
            msg = f"✅ Manual override set: `{hostname}` is now the designated Primary.\nPromotion will take effect within {SYNC_INTERVAL}s."

        await interaction.edit_original_response(content=msg, view=None)


async def setup(bot):
    await bot.add_cog(FailoverCog(bot))
