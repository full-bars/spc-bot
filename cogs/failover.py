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
        logger.info("--- [Failover System Initialized | Direct-Access Mode] ---")

    async def cog_load(self):
        if not self.sync_loop.is_running():
            self.sync_loop.start()

    @tasks.loop(minutes=5)
    async def sync_loop(self):
        await self.bot.wait_until_ready()
        try:
            # Rank check is the only thing we trust the bot object for
            is_primary = getattr(self.bot.state, 'is_primary', False)
            
            if is_primary:
                await self.perform_push()
            else:
                await self.perform_hydration()
        except Exception as e:
            logger.error(f"[SYNC] Loop Error: {e}")

    async def perform_push(self):
        """Fetches from local DB (disk or bot) and pushes to Upstash."""
        db_path = "/opt/spc-bot/cache/bot_state.db"
        url = os.getenv("UPSTASH_REDIS_REST_URL")
        token = os.getenv("UPSTASH_REDIS_REST_TOKEN")

        try:
            # Direct SQLite lookup
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cursor = conn.cursor()
            mds = [row[0] for row in cursor.execute("SELECT md_number FROM posted_mds ORDER BY id DESC LIMIT 25").fetchall()]
            watches = [row[0] for row in cursor.execute("SELECT watch_number FROM posted_watches ORDER BY id DESC LIMIT 25").fetchall()]
            conn.close()

            if not mds and not watches:
                return

            data = json.dumps({"mds": mds, "watches": watches})
            headers = {"Authorization": f"Bearer {token}"}
            
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{url}/set/spcbot:state:posted_records", headers=headers, data=data) as resp:
                    if resp.status == 200:
                        logger.info(f"[SYNC] [DIRECT] Pushed {len(mds)} MDs and {len(watches)} Watches to Upstash.")
                    else:
                        logger.error(f"[SYNC] Push failed: {resp.status}")
        except Exception as e:
            logger.error(f"[SYNC] Push Error: {e}")

    async def perform_hydration(self):
        """Pulls from Upstash and writes directly to disk."""
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
                        if 'mds' in data:
                            for md in data['mds']:
                                cursor.execute("INSERT OR IGNORE INTO posted_mds (md_number) VALUES (?)", (md,))
                        if 'watches' in data:
                            for w in data['watches']:
                                cursor.execute("INSERT OR IGNORE INTO posted_watches (watch_number) VALUES (?)", (w,))
                        conn.commit()
                        conn.close()
                        logger.info("[SYNC] [DIRECT] Hydrated local DB from Upstash.")
        except Exception as e:
            logger.error(f"[SYNC] Hydration Error: {e}")

async def setup(bot):
    await bot.add_cog(Failover(bot))
