import discord
from discord.ext import commands, tasks
import os
import logging
import aiohttp
import json

logger = logging.getLogger(__name__)

class FailoverCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.url = os.getenv("UPSTASH_REDIS_REST_URL")
        self.token = os.getenv("UPSTASH_REDIS_REST_TOKEN")
        self.headers = {"Authorization": f"Bearer {self.token}"}
        self.lock_key = "spcbot:leader:lock"
        self.state_key = "spcbot:state:sync"

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.heartbeat_loop.is_running():
            self.heartbeat_loop.start()
        logger.info(f"[Failover] Rank {self.bot.state.rank} online. Primary: {self.bot.state.is_primary}")

    @tasks.loop(seconds=20)
    async def heartbeat_loop(self):
        async with aiohttp.ClientSession() as session:
            if self.bot.state.rank == 1:
                # Primary heartbeat: Set lock with 30s TTL
                url = f"{self.url}/set/{self.lock_key}/1/EX/30"
                async with session.get(url, headers=self.headers) as resp:
                    if resp.status == 200:
                        state_json = json.dumps(self.bot.state.to_dict())
                        await session.post(f"{self.url}/set/{self.state_key}", headers=self.headers, data=state_json)
            else:
                # Standby: Check if leader lock exists
                async with session.get(f"{self.url}/get/{self.lock_key}", headers=self.headers) as resp:
                    data = await resp.json()
                    if data.get("result") is None:
                        # Lock gone! Take over and hydrate state
                        if not self.bot.state.is_primary:
                            await self.hydrate_and_promote(session)
                    else:
                        if self.bot.state.is_primary:
                            self.bot.state.is_primary = False
                            logger.info("[Failover] Higher rank detected. Demoting to STANDBY.")

    async def hydrate_and_promote(self, session):
        async with session.get(f"{self.url}/get/{self.state_key}", headers=self.headers) as resp:
            data = await resp.json()
            if data.get("result"):
                self.bot.state.from_dict(json.loads(data["result"]))
        self.bot.state.is_primary = True
        logger.warning("[Failover] Primary lost. Promoting standby.")

async def setup(bot):
    await bot.add_cog(FailoverCog(bot))
