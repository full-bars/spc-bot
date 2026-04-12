import os
import json
import asyncio
import logging
import aiohttp
from discord.ext import commands

logger = logging.getLogger("spc_bot")

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
        logger.info(f"--- [Failover System Initialized | Rank: {self.rank}] ---")

    @commands.Cog.listener()
    async def on_ready(self):
        await self.hydrate_local_state()
        await asyncio.sleep(1.5)
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

    async def hydrate_local_state(self):
        """Pull remote posted records from Redis to prevent duplicate alerts on boot."""
        try:
            # Fetch the list from Upstash
            remote_data = await self.redis.get("spcbot:state:posted_records")
            if remote_data:
                # Insert into local SQLite
                async with self.bot.db.execute_batch() as batch:
                    await batch.executemany(
                        "INSERT OR IGNORE INTO posted_records (id) VALUES (?)",
                        [(r,) for r in remote_data]
                    )
                self.bot.logger.info(f"Hydrated {len(remote_data)} records from Redis.")
        except Exception as e:
            self.bot.logger.error(f"Failed to hydrate state: {e}")

    async def hydrate_local_state(self):
        """Pull remote posted records from Redis and route to correct SQLite tables."""
        try:
            remote_data = await self.redis.get("spcbot:state:posted_records")
            if not remote_data:
                return

            # remote_data is likely a dict like {'mds': [...], 'watches': [...]} 
            # or a flat list we need to sort. Adjusting for your schema:
            
            async with self.bot.db.cursor() as cursor:
                # Hydrate MDs
                if 'mds' in remote_data:
                    await cursor.executemany(
                        "INSERT OR IGNORE INTO posted_mds (md_number) VALUES (?)",
                        [(m,) for m in remote_data['mds']]
                    )
                # Hydrate Watches
                if 'watches' in remote_data:
                    await cursor.executemany(
                        "INSERT OR IGNORE INTO posted_watches (watch_number) VALUES (?)",
                        [(w,) for w in remote_data['watches']]
                    )
            self.bot.logger.info("Local database tables hydrated from Upstash.")
        except Exception as e:
            self.bot.logger.error(f"Hydration failed: {e}")


    async def hydrate_local_state(self):
        """Pull remote state and force-inject into correct tables."""
        try:
            self.bot.logger.info("[SYNC] Attempting to pull state from Upstash...")
            # 1. Pull the data
            remote_data = await self.bot.state.redis.get("spcbot:state:posted_records")
            
            if not remote_data:
                self.bot.logger.warning("[SYNC] No remote data found in Redis.")
                return

            self.bot.logger.info(f"[SYNC] Data received: {type(remote_data)}")

            # 2. Handle flat list (just MD numbers) vs Dict (MDs and Watches)
            md_list = []
            if isinstance(remote_data, list):
                md_list = remote_data
            elif isinstance(remote_data, dict):
                md_list = remote_data.get('mds', [])

            # 3. Inject into SQLite
            if md_list:
                for md in md_list:
                    await self.bot.db.execute(
                        "INSERT OR IGNORE INTO posted_mds (md_number) VALUES (?)", 
                        (str(md),)
                    )
                self.bot.logger.info(f"[SYNC] Successfully hydrated {len(md_list)} MDs.")
                
        except Exception as e:
            self.bot.logger.error(f"[SYNC] Critical failure during hydration: {e}")
