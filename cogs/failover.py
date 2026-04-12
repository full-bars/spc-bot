import os
import json
import asyncio
import logging
import aiohttp
import sqlite3
from discord.ext import commands, tasks

logger = logging.getLogger("spc_bot")

class Failover(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        logger.info("--- [Failover System Initialized | Direct + Heartbeat] ---")

    async def cog_load(self):
        if not self.sync_loop.is_running():
            self.sync_loop.start()

    @tasks.loop(minutes=5)
    async def sync_loop(self):
        await self.bot.wait_until_ready()
        try:
            is_primary = getattr(self.bot.state, 'is_primary', False)
            
            # 1. Update Heartbeat/Lock if Primary
            if is_primary:
                await self.update_heartbeat()
                await self.perform_push()
            else:
                await self.perform_hydration()
        except Exception as e:
            logger.error(f"[SYNC] Loop Error: {e}")

    async def update_heartbeat(self):
        """Primary writes a timestamp to Redis to show it is alive."""
        url = os.getenv("UPSTASH_REDIS_REST_URL")
        token = os.getenv("UPSTASH_REDIS_REST_TOKEN")
        headers = {"Authorization": f"Bearer {token}"}
        
        async with aiohttp.ClientSession() as session:
            # We set a 7-minute TTL so it expires if Portland is dead for >1 cycle
            url_set = f"{url}/set/spcbot:state:sync/ALIVE/EX/420"
            async with session.post(url_set, headers=headers) as resp:
                if resp.status == 200:
                    logger.info("[SYNC] Heartbeat updated.")

    async def perform_push(self):
        db_path = "/opt/spc-bot/cache/bot_state.db"
        url = os.getenv("UPSTASH_REDIS_REST_URL")
        token = os.getenv("UPSTASH_REDIS_REST_TOKEN")
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cursor = conn.cursor()
            mds = [row[0] for row in cursor.execute("SELECT md_number FROM posted_mds LIMIT 50").fetchall()]
            watches = [row[0] for row in cursor.execute("SELECT watch_number FROM posted_watches LIMIT 50").fetchall()]
            conn.close()

            data = json.dumps({"mds": mds, "watches": watches})
            headers = {"Authorization": f"Bearer {token}"}
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{url}/set/spcbot:state:posted_records", headers=headers, data=data) as resp:
                    if resp.status == 200:
                        logger.info(f"[SYNC] [DIRECT] Pushed state to Upstash.")
        except Exception as e:
            logger.error(f"[SYNC] Push Error: {e}")

    async def perform_hydration(self):
        db_path = "/opt/spc-bot/cache/bot_state.db"
        url = os.getenv("UPSTASH_REDIS_REST_URL")
        token = os.getenv("UPSTASH_REDIS_REST_TOKEN")
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"Authorization": f"Bearer {token}"}
                async with session.get(f"{url}/get/spcbot:state:posted_records", headers=headers) as resp:
                    if resp.status == 200:
                        res_json = await resp.json()
                        if not res_json.get('result'): return
                        data = json.loads(res_json['result'])
                        conn = sqlite3.connect(db_path)
                        cursor = conn.cursor()
                        for md in data.get('mds', []):
                            cursor.execute("INSERT OR IGNORE INTO posted_mds (md_number) VALUES (?)", (md,))
                        for w in data.get('watches', []):
                            cursor.execute("INSERT OR IGNORE INTO posted_watches (watch_number) VALUES (?)", (w,))
                        conn.commit()
                        conn.close()
                        logger.info("[SYNC] [DIRECT] Hydrated local DB.")
        except Exception as e:
            logger.error(f"[SYNC] Hydration Error: {e}")

async def setup(bot):
    await bot.add_cog(Failover(bot))
