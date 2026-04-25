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
import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ext.commands import ExtensionNotLoaded

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


_PROCESS_UUID = uuid.uuid4().hex[:8]


def _node_identity() -> str:
    """Per-process identifier used as the lease value.

    Includes a random suffix so two processes on the same host have
    distinct identities and won't silently share a lease.
    """
    return f"{socket.gethostname()}:{_PROCESS_UUID}"


class FailoverCog(commands.Cog):
    MANAGED_TASK_NAMES = [("sync_loop", "sync_loop")]

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
        # Manual override wins over env var and over the lease.
        manual = await self._upstash("GET", "spcbot:manual_primary")
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
            logger.warning(
                f"[FAILOVER] Startup: lease held by '{holder}' — booting as "
                f"STANDBY regardless of IS_PRIMARY env"
            )
            self.bot.state.is_primary = False
            return False

        # Lease is free or already ours. Safe to boot as primary if that's
        # what we were configured as. If IS_PRIMARY=false (dedicated
        # standby), stay standby and let the sync_loop promote us later if
        # the primary actually dies.
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

        # Push anything SQLite has to Upstash before loading other cogs.
        # This handles the case where the primary rebooted after an Upstash
        # outage and the local SQLite mirror is more recent than Upstash.
        try:
            await state_store.resync_to_upstash()
        except Exception as e:
            logger.warning(f"[FAILOVER] Startup resync failed: {e}")

        return True

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
            # 1. Update heartbeat registry
            await self._upstash(
                "HSET", "spcbot:nodes", self._identity, str(int(time.time()))
            )

            # 2. Check for manual override
            manual_primary = await self._upstash("GET", "spcbot:manual_primary")
            
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
                    return # Skip normal cycles if we've been told to be standby

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

        logger.info("[FAILOVER] Rehydrated BotState from Upstash")

    async def _demote(self) -> None:
        logger.info("[FAILOVER] Demoting to STANDBY")
        self.bot.state.is_primary = False
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
                "❌ You are not authorized to use this command.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        # 2. Fetch active nodes from registry
        # HGETALL returns [k1, v1, k2, v2, ...]
        nodes_raw = await self._upstash("HGETALL", "spcbot:nodes")
        if not nodes_raw or not isinstance(nodes_raw, list):
            await interaction.followup.send(
                "❌ No active nodes found in the registry.",
                ephemeral=True
            )
            return

        # Parse nodes and filter by age (5 minutes)
        now = int(time.time())
        active_nodes = []
        for i in range(0, len(nodes_raw), 2):
            node_id = nodes_raw[i]
            timestamp = int(nodes_raw[i+1])
            if (now - timestamp) < 300: # 5 minutes
                active_nodes.append(node_id)

        if not active_nodes:
            await interaction.followup.send(
                "❌ No nodes have sent a heartbeat in the last 5 minutes.",
                ephemeral=True
            )
            return

        # 3. Fetch current manual override
        current_manual = await self._upstash("GET", "spcbot:manual_primary")
        current_lease = await self._read_lease_holder()

        # 4. Present UI
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

        if target == "CLEAR":
            await self.cog._upstash("DEL", "spcbot:manual_primary")
            msg = "✅ Manual override cleared. Returning to automatic failover."
        else:
            # Store just the hostname (strip per-process UUID suffix) so the
            # override survives process restarts on the same host.
            hostname = target.split(":")[0]
            await self.cog._upstash("SET", "spcbot:manual_primary", hostname)
            msg = f"✅ Manual override set: `{hostname}` is now the designated Primary."

        await interaction.response.edit_message(content=msg, view=None)


async def setup(bot):
    await bot.add_cog(FailoverCog(bot))
