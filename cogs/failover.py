import os
import json
import asyncio
import logging
from discord.ext import commands, tasks

logger = logging.getLogger("spc_bot")

class Failover(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        """Triggered when the cog is loaded."""
        asyncio.create_task(self.initialize_sync())

    async def initialize_sync(self):
        """Wait for the bot and its custom attributes to be ready."""
        await self.bot.wait_until_ready()
        
        # Robustness: Wait for main.py to finish attaching custom attributes
        retries = 0
        while not hasattr(self.bot, 'db') or not hasattr(self.bot, 'config') or not hasattr(self.bot, 'session'):
            if retries > 10:
                logger.error("[SYNC] Critical Failure: Bot attributes never initialized.")
                return
            await asyncio.sleep(2)
            retries += 1
            logger.info(f"[SYNC] Waiting for bot attributes (Attempt {retries})...")

        if not self.sync_loop.is_running():
            self.sync_loop.start()
        
        if self.bot.state.is_primary:
            await self.push_state_to_redis()
        else:
            await self.hydrate_local_state()

    @tasks.loop(minutes=5)
    async def sync_loop(self):
        """Periodic backup for Primary."""
        if self.bot.state.is_primary:
            await self.push_state_to_redis()

    async def push_state_to_redis(self):
        """Primary pushes last 25 records to Upstash."""
        try:
            data = {}
            async with self.bot.db.execute("SELECT md_number FROM posted_mds ORDER BY id DESC LIMIT 25") as cursor:
                data['mds'] = [row[0] for row in await cursor.fetchall()]
            
            async with self.bot.db.execute("SELECT watch_number FROM posted_watches ORDER BY id DESC LIMIT 25") as cursor:
                data['watches'] = [row[0] for row in await cursor.fetchall()]

            url = f"{self.bot.config.UPSTASH_REDIS_REST_URL}/set/spcbot:state:posted_records"
            headers = {"Authorization": f"Bearer {self.bot.config.UPSTASH_REDIS_REST_TOKEN}"}
            
            async with self.bot.session.post(url, headers=headers, data=json.dumps(data)) as resp:
                if resp.status == 200:
                    logger.info(f"[SYNC] Pushed {len(data['mds'])} MDs and {len(data['watches'])} Watches to Upstash.")
                else:
                    logger.error(f"[SYNC] Push failed: {resp.status}")
        except Exception as e:
            logger.error(f"[SYNC] Push Error: {e}")

    async def hydrate_local_state(self):
        """Standby pulls state from Upstash."""
        try:
            url = f"{self.bot.config.UPSTASH_REDIS_REST_URL}/get/spcbot:state:posted_records"
            headers = {"Authorization": f"Bearer {self.bot.config.UPSTASH_REDIS_REST_TOKEN}"}
            
            async with self.bot.session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    res_json = await resp.json()
                    if not res_json.get('result'):
                        return
                    
                    data = json.loads(res_json['result'])
                    async with self.bot.db.cursor() as cursor:
                        if 'mds' in data:
                            for md in data['mds']:
                                await cursor.execute("INSERT OR IGNORE INTO posted_mds (md_number) VALUES (?)", (md,))
                        if 'watches' in data:
                            for w in data['watches']:
                                await cursor.execute("INSERT OR IGNORE INTO posted_watches (watch_number) VALUES (?)", (w,))
                    logger.info("[SYNC] Hydrated local DB from Upstash.")
        except Exception as e:
            logger.error(f"[SYNC] Hydration Error: {e}")

async def setup(bot):
    await bot.add_cog(Failover(bot))
