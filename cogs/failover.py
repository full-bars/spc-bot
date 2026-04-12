# cogs/failover.py
"""
Failover cog — manages primary/standby coordination via Upstash Redis
and a local aiohttp HTTP state server.

Primary (Portland):
  - Runs aiohttp server on localhost:8765 serving GET /state and POST /sync
  - Starts cloudflared tunnel, writes tunnel URL to Upstash with 7min TTL
  - Refreshes heartbeat every 5 min

Standby (3CAPE):
  - Polls Upstash every 5 min for primary URL
  - If URL found, fetches /state to stay hydrated
  - If URL missing (TTL expired) → promotes to primary, loads all cogs
  - On demotion, pushes accumulated state to primary via POST /sync
"""

import asyncio
import base64
import json
import logging
import os
import subprocess

import aiohttp
from aiohttp import web
from discord.ext import commands, tasks

logger = logging.getLogger("spc_bot")

UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL", "")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")
FAILOVER_TOKEN = os.getenv("FAILOVER_TOKEN", "changeme")
STATE_PORT = int(os.getenv("STATE_PORT", "8765"))
HEARTBEAT_TTL = 420  # 7 minutes
SYNC_INTERVAL = 30   # seconds

UPSTASH_HEADERS = {"Authorization": f"Bearer {UPSTASH_TOKEN}"}

ALL_EXTENSIONS = [
    "cogs.scp", "cogs.outlooks", "cogs.mesoscale", "cogs.watches",
    "cogs.status", "cogs.radar", "cogs.csu_mlp", "cogs.ncar",
    "cogs.sounding", "cogs.hodograph",
]


class FailoverCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._http_runner = None
        self._tunnel_proc = None
        self._tunnel_url = None
        self._primary_failures = 0
        self._max_failures = 3

    async def cog_load(self):
        if self.bot.state.is_primary:
            await self._start_http_server()
            await self._start_tunnel()
        self.sync_loop.start()

    async def cog_unload(self):
        self.sync_loop.cancel()
        if self._http_runner:
            await self._http_runner.cleanup()
        if self._tunnel_proc:
            self._tunnel_proc.terminate()

    # ── HTTP server (primary only) ────────────────────────────────────────

    async def _start_http_server(self):
        app = web.Application()
        app.router.add_get("/state", self._handle_get_state)
        app.router.add_post("/sync", self._handle_post_sync)
        self._http_runner = web.AppRunner(app)
        await self._http_runner.setup()
        site = web.TCPSite(self._http_runner, "127.0.0.1", STATE_PORT)
        await site.start()
        logger.info(f"[FAILOVER] HTTP state server started on port {STATE_PORT}")

    def _check_token(self, request):
        return request.headers.get("Authorization") == f"Bearer {FAILOVER_TOKEN}"

    async def _handle_get_state(self, request):
        if not self._check_token(request):
            return web.Response(status=401, text="Unauthorized")
        state_dict = self.bot.state.to_dict()
        return web.json_response(state_dict)

    async def _handle_post_sync(self, request):
        if not self._check_token(request):
            return web.Response(status=401, text="Unauthorized")
        try:
            data = await request.json()
            # Merge standby state into primary
            self.bot.state.posted_mds.update(data.get("posted_mds", []))
            self.bot.state.posted_watches.update(data.get("posted_watches", []))
            self.bot.state.auto_cache.update(data.get("auto_cache", {}))
            for day_key, urls in data.get("last_posted_urls", {}).items():
                if day_key not in self.bot.state.last_posted_urls:
                    self.bot.state.last_posted_urls[day_key] = urls
            logger.info("[FAILOVER] Merged standby state from POST /sync")
            return web.json_response({"ok": True})
        except Exception as e:
            logger.error(f"[FAILOVER] /sync error: {e}")
            return web.Response(status=500, text=str(e))

    # ── Cloudflare tunnel ─────────────────────────────────────────────────

    async def _start_tunnel(self):
        loop = asyncio.get_event_loop()
        try:
            self._tunnel_proc = await asyncio.create_subprocess_exec(
                "cloudflared", "tunnel", "--url", f"http://localhost:{STATE_PORT}",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            # Read stderr to find the tunnel URL (cloudflared logs to stderr)
            async def _read_url():
                while True:
                    line = await self._tunnel_proc.stderr.readline()
                    if not line:
                        break
                    text = line.decode()
                    if "https://" in text and "trycloudflare.com" in text:
                        for word in text.split():
                            if word.startswith("https://") and "trycloudflare.com" in word:
                                url = word.strip().rstrip("|").strip()
                                self._tunnel_url = url
                                logger.info(f"[FAILOVER] Tunnel URL: {self._tunnel_url}")
                                await self._write_url_to_upstash(self._tunnel_url)
                                return
            asyncio.create_task(_read_url())
        except FileNotFoundError:
            logger.error("[FAILOVER] cloudflared not found — tunnel disabled")

    async def _write_url_to_upstash(self, url: str):
        try:
            headers = {**UPSTASH_HEADERS, "Content-Type": "application/json"}
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    UPSTASH_URL,
                    headers=headers,
                    json=["SET", "spcbot:primary_url", url, "EX", str(HEARTBEAT_TTL)],
                ) as resp:
                    if resp.status == 200:
                        logger.info(f"[FAILOVER] Wrote primary URL to Upstash (TTL {HEARTBEAT_TTL}s)")
                    else:
                        logger.error(f"[FAILOVER] Upstash write failed: {resp.status}")
        except Exception as e:
            logger.error(f"[FAILOVER] Failed to write URL to Upstash: {e}")

    # ── Sync loop ─────────────────────────────────────────────────────────

    @tasks.loop(seconds=SYNC_INTERVAL)
    async def sync_loop(self):
        await self.bot.wait_until_ready()
        try:
            if self.bot.state.is_primary:
                await self._write_url_to_upstash(self._tunnel_url or "unknown")
                await self._check_for_demotion()
            else:
                await self._standby_cycle()
        except Exception as e:
            logger.error(f"[FAILOVER] Sync loop error: {e}")

    async def _standby_cycle(self):
        primary_url = await self._get_primary_url()
        if not primary_url:
            self._primary_failures += 1
            logger.warning(f"[FAILOVER] Primary URL missing from Upstash (failure {self._primary_failures}/{self._max_failures})")
            if self._primary_failures >= self._max_failures:
                await self._promote()
            return

        # Try to reach primary
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{primary_url}/state",
                    headers={"Authorization": f"Bearer {FAILOVER_TOKEN}"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        self._hydrate(data)
                        self._primary_failures = 0
                        logger.debug("[FAILOVER] Hydrated state from primary")
                    else:
                        self._primary_failures += 1
                        logger.warning(f"[FAILOVER] Primary /state returned {resp.status} (failure {self._primary_failures}/{self._max_failures})")
                        if self._primary_failures >= self._max_failures:
                            await self._promote()
        except Exception as e:
            self._primary_failures += 1
            logger.warning(f"[FAILOVER] Cannot reach primary: {e} (failure {self._primary_failures}/{self._max_failures})")
            if self._primary_failures >= self._max_failures:
                await self._promote()

    async def _get_primary_url(self) -> str | None:
        try:
            headers = {**UPSTASH_HEADERS, "Content-Type": "application/json"}
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    UPSTASH_URL,
                    headers=headers,
                    json=["GET", "spcbot:primary_url"],
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("result")
        except Exception as e:
            logger.error(f"[FAILOVER] Upstash error: {e}")
        return None

    def _hydrate(self, data: dict):
        from datetime import datetime, timezone
        self.bot.state.posted_mds.update(data.get("posted_mds", []))
        self.bot.state.posted_watches.update(data.get("posted_watches", []))
        self.bot.state.auto_cache.update(data.get("auto_cache", {}))
        self.bot.state.last_posted_urls.update(data.get("last_posted_urls", {}))
        for k, v in data.get("last_post_times", {}).items():
            if v and k in self.bot.state.last_post_times:
                try:
                    self.bot.state.last_post_times[k] = datetime.fromisoformat(v)
                except Exception:
                    pass

    async def _promote(self):
        logger.warning("[FAILOVER] !!! PROMOTING TO PRIMARY !!!")
        self.bot.state.is_primary = True
        await self._start_http_server()
        await self._start_tunnel()
        for ext in ALL_EXTENSIONS:
            try:
                await self.bot.load_extension(ext)
                logger.info(f"[FAILOVER] Loaded {ext}")
            except Exception as e:
                logger.error(f"[FAILOVER] Failed to load {ext}: {e}")
        try:
            synced = await self.bot.tree.sync()
            logger.info(f"[FAILOVER] Synced {len(synced)} slash commands")
        except Exception as e:
            logger.error(f"[FAILOVER] Failed to sync commands: {e}")

    async def _check_for_demotion(self):
        """If we are acting primary but another server wrote a newer URL, demote."""
        stored_url = await self._get_primary_url()
        if stored_url and stored_url != self._tunnel_url:
            logger.info(f"[FAILOVER] Detected new primary at {stored_url} — demoting")
            await self._demote(stored_url)

    async def _demote(self, primary_url: str):
        """Push accumulated state to primary then demote."""
        logger.info("[FAILOVER] Primary back online — syncing state and demoting")
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(
                    f"{primary_url}/sync",
                    headers={"Authorization": f"Bearer {FAILOVER_TOKEN}"},
                    json=self.bot.state.to_dict(),
                    timeout=aiohttp.ClientTimeout(total=10),
                )
            logger.info("[FAILOVER] Pushed state to primary successfully")
        except Exception as e:
            logger.error(f"[FAILOVER] Failed to push state to primary: {e}")

        self.bot.state.is_primary = False
        for ext in ALL_EXTENSIONS:
            try:
                await self.bot.unload_extension(ext)
            except Exception:
                pass
        if self._http_runner:
            await self._http_runner.cleanup()
            self._http_runner = None
        if self._tunnel_proc:
            self._tunnel_proc.terminate()
            self._tunnel_proc = None
        logger.info("[FAILOVER] Demoted to STANDBY")


async def setup(bot):
    await bot.add_cog(FailoverCog(bot))
