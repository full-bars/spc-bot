"""
Failover cog (simplified — Redis-backed state edition).

With shared state in Redis (either self-hosted or Upstash, see utils.state_store),
the primary and standby no longer need to ship in-memory state between themselves.
The HTTP `/state` and `/sync` endpoints, the cloudflared tunnel, and all the
hydration machinery are gone.

What this cog still does
------------------------
Leader election via a short-lived Redis key:

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
  process cache so fresh reads hit Redis, loads all cogs, and
  begins holding the lease itself.
- If a second holder appears the current holder steps back down.

The v4.13.2 "liveness vs. reachability" split is no longer needed:
liveness is Redis-mediated directly, and there's nothing to hydrate from.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
import uuid

import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ext.commands import ExtensionNotLoaded

from cogs import ALL_EXTENSIONS
from utils import state_store

logger = logging.getLogger("spc_bot")

FAILOVER_TOKEN = os.getenv("FAILOVER_TOKEN", "")

HEARTBEAT_TTL = 420  # seconds
SYNC_INTERVAL = 30   # seconds

STARTUP_GRACE_SECONDS = 120
MAX_FAILURES = max(5, HEARTBEAT_TTL // (2 * SYNC_INTERVAL))

LEASE_KEY = "spcbot:primary_url"


def _require_failover_token() -> str:
    if not FAILOVER_TOKEN or FAILOVER_TOKEN == "changeme":
        raise RuntimeError(
            "FAILOVER_TOKEN environment variable must be set to a strong, "
            "non-default value. Refusing to participate in leader election "
            "with a known/missing token."
        )
    return FAILOVER_TOKEN


_PROCESS_UUID = uuid.uuid4().hex[:8]


def _node_identity(is_primary: bool) -> str:
    """Per-process identifier used as the lease value.

    Includes a role prefix ('P' for configured primary, 'S' for standby)
    so that a rebooting primary can identify if the current lease holder
    is just a promoted standby and pre-empt it.
    """
    role = "P" if is_primary else "S"
    return f"{role}:{socket.gethostname()}:{_PROCESS_UUID}"


class FailoverCog(commands.Cog):
    MANAGED_TASK_NAMES = [("sync_loop", "sync_loop")]

    def __init__(self, bot):
        self.bot = bot
        self._primary_failures = 0
        # Store the INITIAL configured identity. This doesn't change
        # when promoted — it represents the node's hard-coded intent.
        self._identity = _node_identity(bot.state.is_primary)
        self._cog_load_monotonic: float | None = None

    async def cog_load(self):
        _require_failover_token()
        self._cog_load_monotonic = time.monotonic()
        self.sync_loop.start()

    def _is_our_node(self, target: str) -> bool:
        """True if *target* designates this process.

        Accepts either the full per-process identity (``hostname:uuid``) or a
        bare hostname so that the /failover Discord command (which stores just
        the hostname) still works correctly.
        """
        return target == self._identity or target == socket.gethostname()

    async def startup_lease_check(self) -> bool:
        """Synchronous lease probe run during setup_hook, before other cogs
        are loaded. Decides whether this node should boot as primary.

        Returns True if this node should load cogs as primary, False if it
        should stay standby. Updates `bot.state.is_primary` accordingly.

        Closes the 30-second window where a rebooting primary would load
        cogs and post duplicates before the first sync_loop tick detected
        another node already held the lease.
        """
        redis = state_store._redis_pool
        # Manual override wins over env var and over the lease.
        try:
            manual_bytes = await redis.get("spcbot:manual_primary") if redis else None
            manual = manual_bytes.decode("utf-8") if isinstance(manual_bytes, bytes) else (str(manual_bytes) if manual_bytes else None)
        except Exception as e:
            logger.debug(f"[FAILOVER] Failed to read manual override at startup: {e}")
            manual = None

        if manual:
            if self._is_our_node(manual):
                logger.info(
                    f"[FAILOVER] Startup: manual override names us "
                    f"('{self._identity}') as Primary — claiming lease"
                )
                await self._write_lease()
                self.bot.state.is_primary = True
                try:
                    await state_store.resync_to_upstash()
                except Exception as e:
                    logger.warning(f"[FAILOVER] Startup resync (manual) failed: {e}")
                return True
            logger.info(
                f"[FAILOVER] Startup: manual override names '{manual}' as "
                f"Primary — booting as STANDBY"
            )
            self.bot.state.is_primary = False
            return False

        holder = await self._read_lease_holder()
        if holder and holder != self._identity:
            # Pre-emption logic: if WE are a configured primary, and the current
            # holder is a standby (prefixed with 'S:'), we take the lease.
            if self._identity.startswith("P:") and holder.startswith("S:"):
                logger.warning(
                    f"[FAILOVER] Startup: lease held by Standby node '{holder}'. "
                    f"We are configured Primary — pre-empting."
                )
                # Proceed to claim lease below
            else:
                logger.warning(
                    f"[FAILOVER] Startup: lease held by '{holder}' — booting as "
                    f"STANDBY regardless of IS_PRIMARY env"
                )
                self.bot.state.is_primary = False
                return False

        # Lease is free, already ours, or we are pre-empting a standby.
        # Safe to boot as primary if that's what we were configured as.
        if not self.bot.state.is_primary:
            logger.info(
                "[FAILOVER] Startup: lease is free but node configured as "
                "STANDBY — not self-promoting"
            )
            return False

        logger.info(
            f"[FAILOVER] Startup: lease free/ours — claiming as Primary "
            f"('{self._identity}')"
        )
        await self._write_lease()

        # Push anything SQLite has to Redis before loading other cogs.
        # This handles the case where the primary rebooted after a Redis
        # outage and the local SQLite mirror is more recent than Redis.
        try:
            await state_store.resync_to_upstash()
        except Exception as e:
            logger.warning(f"[FAILOVER] Startup resync failed: {e}")

        return True

    async def cog_unload(self):
        self.sync_loop.cancel()
        if self.bot.state.is_primary:
            await self._release_lease()

    # ── Redis Operations ────────────────────────────────────────────────

    async def _write_lease(self) -> None:
        """Renew the primary lease in Redis."""
        try:
            redis = state_store._redis_pool
            if redis is None:
                logger.warning("[FAILOVER] Redis pool unavailable, cannot write lease")
                return
            await redis.set(LEASE_KEY, self._identity, ex=HEARTBEAT_TTL)
        except Exception as e:
            logger.warning(f"[FAILOVER] Failed to write lease: {e}")

    async def _read_lease_holder(self) -> str | None:
        """Read who currently holds the primary lease."""
        try:
            redis = state_store._redis_pool
            if redis is None:
                return None
            result = await redis.get(LEASE_KEY)
            return result.decode("utf-8") if isinstance(result, bytes) else (str(result) if result else None)
        except Exception as e:
            logger.warning(f"[FAILOVER] Failed to read lease: {e}")
            return None

    async def _release_lease(self) -> None:
        """Release the primary lease (shutdown cleanup)."""
        try:
            holder = await self._read_lease_holder()
            if holder == self._identity:
                redis = state_store._redis_pool
                if redis is not None:
                    await redis.delete(LEASE_KEY)
                    logger.info("[FAILOVER] Released primary lease on shutdown")
        except Exception as e:
            logger.warning(f"[FAILOVER] Failed to release lease: {e}")

    # ── Sync loop ────────────────────────────────────────────────────────

    @tasks.loop(seconds=SYNC_INTERVAL)
    async def sync_loop(self):
        await self.bot.wait_until_ready()
        try:
            redis = state_store._redis_pool
            if redis is None:
                logger.debug("[FAILOVER] Redis pool unavailable, skipping sync cycle")
                return

            # 1. Update heartbeat registry
            try:
                await redis.hset("spcbot:nodes", self._identity, str(int(time.time())))
            except Exception as e:
                logger.debug(f"[FAILOVER] Failed to update heartbeat: {e}")

            # 2. Check for manual override
            try:
                manual_primary = await redis.get("spcbot:manual_primary")
                manual_primary = manual_primary.decode("utf-8") if isinstance(manual_primary, bytes) else (str(manual_primary) if manual_primary else None)
            except Exception as e:
                logger.debug(f"[FAILOVER] Failed to read manual override: {e}")
                manual_primary = None

            if manual_primary:
                if self._is_our_node(manual_primary):
                    # We are the designated primary
                    if not self.bot.state.is_primary:
                        logger.warning(f"[FAILOVER] Manual override: Promoting node '{self._identity}' to Primary")
                        await self._promote()
                else:
                    # Someone else is the designated primary
                    if self.bot.state.is_primary:
                        logger.warning(f"[FAILOVER] Manual override: Demoting node '{self._identity}' to Standby (Target is '{manual_primary}')")
                        await self._demote()
                    return

            # 3. Proceed with normal cycle
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
        if holder is not None:
            # We hold the lease (or it was empty and readable) — renew normally.
            await self._write_lease()
            return

        # holder is None: either the key expired or a Redis read failed.  A
        # plain SET here would silently overwrite a standby that promoted during
        # a connectivity gap.  Use SET NX so we only claim the key if it is
        # genuinely absent; if another node holds it, the NX write returns null.
        try:
            redis = state_store._redis_pool
            if redis is None:
                return
            # redis.set returns True if NX succeeded, None/False if key already exists
            result = await redis.set(LEASE_KEY, self._identity, nx=True, ex=HEARTBEAT_TTL)
            if result is None or result is False:
                # NX write failed: key already held by another node.
                # Re-read to check and demote if necessary.
                holder = await self._read_lease_holder()
                if holder and holder != self._identity:
                    logger.warning(
                        f"[FAILOVER] Another node ({holder}) holds the lease after NX — demoting"
                    )
                    await self._demote()
                return
            logger.info("[FAILOVER] Reclaimed expired lease via NX write")
        except Exception as e:
            logger.warning(f"[FAILOVER] Failed to reclaim lease via NX: {e}")

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
            # Pre-emption logic: if WE are a configured primary, and the current
            # holder is a standby (prefixed with 'S:'), we take the lease.
            if self._identity.startswith("P:") and holder.startswith("S:"):
                 logger.warning(
                     f"[FAILOVER] Standby Primary detected promoted Standby node "
                     f"'{holder}' holding lease. Reclaiming."
                 )
                 await self._promote()
                 return

            if self._primary_failures > 0:
                logger.info(
                    f"[FAILOVER] Primary lease held by {holder}; clearing "
                    f"{self._primary_failures} prior failures"
                )
            self._primary_failures = 0
            return

        # Key missing = primary silent for ≥ HEARTBEAT_TTL (lease expired).
        count = self._register_failure("Primary lease expired")
        if count >= MAX_FAILURES:
            await self._promote()

    # ── Promotion / demotion ─────────────────────────────────────────────

    async def _promote(self) -> None:
        logger.warning("[FAILOVER] !!! PROMOTING TO PRIMARY !!!")
        self.bot.state.is_primary = True

        # Restore events.db from Syncthing snapshot before cogs load.
        from utils.events_db import restore_from_sync, set_syncthing_folder_mode  # noqa: PLC0415
        restore_from_sync()
        await set_syncthing_folder_mode("sendonly")

        # Drop stale cache so fresh reads hit Redis.
        state_store.invalidate_all_caches()

        # Claim the lease immediately (before loading cogs so there's no
        # window where another watcher sees the key missing).
        await self._write_lease()

        # Update local SQLite mirror from Redis so this node's durable
        # store is fresh before it starts taking new writes.
        try:
            await state_store.mirror_to_sqlite()
        except Exception as e:
            logger.exception(f"[FAILOVER] Mirroring on promotion failed: {e}")

        # Brief pause so the outgoing Primary's next sync cycle can detect
        # the new lease holder and demote before we start posting. Keeps the
        # dual-primary window under one sync interval (~30 s) in normal cases
        # and under a few seconds in the common pre-emption path.
        await asyncio.sleep(2.0)

        # Refresh the in-process BotState mirrors. Cogs still read
        # `bot.state.posted_mds`, `bot.state.auto_cache`, etc. as local
        # collections; those were populated from SQLite at boot and are
        # now stale relative to what the old primary wrote to Redis
        # while we were in standby.
        try:
            await self._rehydrate_bot_state()
        except Exception as e:
            logger.exception(f"[FAILOVER] Rehydrate on promotion failed: {e}")

        # Push anything SQLite has that Redis is missing. Handles the
        # edge case where this node's prior writes during a Redis
        # outage are queued only on this machine.
        try:
            await state_store.resync_to_upstash()
        except Exception as e:
            logger.exception(f"[FAILOVER] Resync on promotion failed: {e}")

        # Load cogs and start posting.
        for ext in ALL_EXTENSIONS:
            try:
                await self.bot.load_extension(ext)
            except Exception as e:
                logger.exception(f"[FAILOVER] Failed to load {ext} during promotion: {e}")

        # Sync commands to Discord.
        try:
            synced = await self.bot.tree.sync()
            logger.info(f"[FAILOVER] Synced {len(synced)} slash commands")
        except Exception as e:
            logger.exception(f"[FAILOVER] Failed to sync commands: {e}")

    async def _rehydrate_bot_state(self) -> None:
        """Pull authoritative state from Redis into BotState mirrors.

        Cogs read the BotState collections synchronously; on promotion
        we need them to reflect what the outgoing primary wrote to
        Redis, not what we snapshotted at boot.
        """
        st = self.bot.state
        st.auto_cache = await state_store.get_all_hashes("auto")
        st.manual_cache = await state_store.get_all_hashes("manual")
        st.posted_mds = await state_store.get_posted_mds()
        st.posted_watches = await state_store.get_posted_watches()
        st.posted_reports = await state_store.get_posted_reports()

        last_seq = await state_store.get_state("iembot_last_seqnum")
        if isinstance(last_seq, str) and last_seq.isdigit():
            st.iembot_last_seqnum = max(st.iembot_last_seqnum, int(last_seq))

        for day_key in ("day1", "day2", "day3"):
            urls = await state_store.get_posted_urls(day_key)
            if urls:
                st.last_posted_urls[day_key] = urls

        # Pull today's CSU-MLP posted-days set so we don't re-post panels
        # the outgoing primary already handled this UTC day.
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
        logger.info("[FAILOVER] Demoting to STANDBY")
        self.bot.state.is_primary = False
        from utils.events_db import set_syncthing_folder_mode  # noqa: PLC0415
        await set_syncthing_folder_mode("receiveonly")
        failed = []
        for ext in ALL_EXTENSIONS:
            try:
                await self.bot.unload_extension(ext)
            except ExtensionNotLoaded:
                pass  # expected when demoting a node that was never promoted
            except Exception as e:
                logger.warning(f"[FAILOVER] Failed to unload {ext} during demote: {e}")
                failed.append(ext)
        if failed:
            logger.error(
                f"[FAILOVER] {len(failed)} cog(s) failed to unload — "
                f"bot may still be posting as primary: {failed}"
            )
        self._primary_failures = 0

    # ── Slash Command ───────────────────────────────────────────────────

    @app_commands.command(
        name="failover",
        description="Manually designate the Primary node (Admin only)"
    )
    async def failover_slash(self, interaction: discord.Interaction):
        # 1. Authorization check
        raw_admin_id = os.getenv("ADMIN_USER_ID", "0")
        try:
            authorized_id = int(raw_admin_id)
        except ValueError:
            authorized_id = 0

        if interaction.user.id != authorized_id:
            await interaction.response.send_message(
                "❌ You do not have permission to use this command.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        redis = state_store._redis_pool
        if redis is None:
            await interaction.followup.send(
                "❌ Redis unavailable.",
                ephemeral=True
            )
            return

        # 2. Fetch active nodes from registry
        try:
            nodes_raw = await redis.hgetall("spcbot:nodes")
            if not nodes_raw:
                await interaction.followup.send(
                    "❌ No active nodes found in the registry.",
                    ephemeral=True
                )
                return
            active_nodes = list(nodes_raw.keys())
        except Exception as e:
            logger.exception(f"[FAILOVER] Failed to fetch nodes: {e}")
            await interaction.followup.send(
                f"❌ Failed to fetch nodes: {e}",
                ephemeral=True
            )
            return

        # Filter by age (5 minutes)
        now = int(time.time())
        recent_nodes = []
        for node_id, timestamp_bytes in nodes_raw.items():
            try:
                timestamp = int(timestamp_bytes) if isinstance(timestamp_bytes, int) else int(timestamp_bytes.decode("utf-8"))
                if (now - timestamp) < 300:
                    recent_nodes.append(node_id.decode("utf-8") if isinstance(node_id, bytes) else node_id)
            except (ValueError, AttributeError):
                pass

        if not recent_nodes:
            await interaction.followup.send(
                "❌ No nodes have sent a heartbeat in the last 5 minutes.",
                ephemeral=True
            )
            return

        # 3. Fetch current manual override
        try:
            current_manual_bytes = await redis.get("spcbot:manual_primary")
            current_manual = current_manual_bytes.decode("utf-8") if isinstance(current_manual_bytes, bytes) else (str(current_manual_bytes) if current_manual_bytes else None)
        except Exception as e:
            logger.debug(f"[FAILOVER] Failed to fetch manual override: {e}")
            current_manual = None

        current_lease = await self._read_lease_holder()

        # 4. Present UI
        view = FailoverView(self, recent_nodes, current_manual, current_lease)
        await interaction.followup.send(
            content=(
                f"**Failover Management**\n"
                f"Current Lease Holder: `{current_lease or 'None'}`\n"
                f"Manual Override: `{current_manual or 'None (Automatic)'}`\n\n"
                f"Select a node to force it to be Primary, or clear the override "
                f"to return to automatic failover."
            ),
            view=view,
            ephemeral=True
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

            options.append(discord.SelectOption(
                label=label,
                value=node,
                description=f"Force {node} to be Primary",
                default=(node == current_manual)
            ))

        options.append(discord.SelectOption(
            label="❌ Clear Manual Override",
            value="CLEAR",
            description="Return to standard automatic failover",
            emoji="🔄"
        ))

        self.add_item(FailoverSelect(cog, options))


class FailoverSelect(discord.ui.Select):
    def __init__(self, cog: FailoverCog, options: list[discord.SelectOption]):
        super().__init__(
            placeholder="Choose a target Primary node...",
            min_values=1,
            max_values=1,
            options=options
        )
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        target = self.values[0]
        redis = state_store._redis_pool

        if redis is None:
            await interaction.response.edit_message(content="❌ Redis unavailable.", view=None)
            return

        try:
            if target == "CLEAR":
                await redis.delete("spcbot:manual_primary")
                msg = "✅ Manual override cleared. Returning to automatic failover."
            else:
                # Store just the hostname (strip role prefix and per-process UUID)
                # so the override survives process restarts on the same host.
                # Identity format is "role:hostname:uuid", so index 1 is hostname.
                hostname = target.split(":")[1]
                await redis.set("spcbot:manual_primary", hostname)
                msg = f"✅ Manual override set: `{hostname}` is now the designated Primary."

            await interaction.response.edit_message(content=msg, view=None)
        except Exception as e:
            logger.exception(f"[FAILOVER] Failed to update override: {e}")
            await interaction.response.edit_message(content=f"❌ Failed: {e}", view=None)


async def setup(bot):
    await bot.add_cog(FailoverCog(bot))
