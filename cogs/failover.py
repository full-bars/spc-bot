import os
import json
import asyncio
import logging
import aiohttp
from discord.ext import commands

logger = logging.getLogger("cogs.failover")

class FailoverCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.rank = bot.state.rank
        self.url = os.getenv("UPSTASH_REDIS_REST_URL")
        self.token = os.getenv("UPSTASH_REDIS_REST_TOKEN")
        self.headers = {"Authorization": f"Bearer {self.token}"}
        self.lock_key = "spcbot:leader:lock"
        self.state_key = "spcbot:state:sync"
        self.heartbeat_task = self.bot.loop.create_task(self.heartbeat_loop())

    @commands.Cog.listener()
    async def on_ready(self):
        status = "PRIMARY" if self.bot.state.is_primary else "STANDBY"
        logger.info("--- [Failover System Online] ---")
        logger.info(f"Rank: {self.rank} | Mode: {status}")
        logger.info(f"Target: {self.url.split('//')[-1]}")
        logger.info("-------------------------------")

    async def heartbeat_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                async with aiohttp.ClientSession(headers=self.headers) as session:
                    if self.bot.state.is_primary:
                        await session.get(f"{self.url}/set/{self.lock_key}/{self.rank}/EX/60")
                        state_data = json.dumps(self.bot.state.to_dict())
                        async with session.post(f"{self.url}/set/{self.state_key}", data=state_data) as resp:
                            if resp.status != 200:
                                logger.error(f"[Failover] State sync failed: {resp.status}")
                    else:
                        async with session.get(f"{self.url}/get/{self.lock_key}") as resp:
                            data = await resp.json()
                            if data.get("result") is None:
                                logger.warning("[Failover] Primary lost. Promoting standby.")
                                self.bot.state.is_primary = True
                                async with session.get(f"{self.url}/get/{self.state_key}") as s_resp:
                                    s_data = await s_resp.json()
                                    if s_data.get("result"):
                                        self.bot.state.from_dict(json.loads(s_data["result"]))
                            else:
                                async with session.get(f"{self.url}/get/{self.state_key}") as s_resp:
                                    s_data = await s_resp.json()
                                    if s_data.get("result"):
                                        self.bot.state.from_dict(json.loads(s_data["result"]))
            except Exception as e:
                logger.error(f"[Failover] Loop error: {e}")
            await asyncio.sleep(20)

async def setup(bot):
    # This fires immediately during bot.load_extension()
    print("--- [Failover Extension Loading] ---") 
    await bot.add_cog(FailoverCog(bot))
    print("--- [Failover Extension Loaded] ---")
