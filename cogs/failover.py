import os
import base64
import asyncio
import logging
import aiohttp
from discord.ext import commands, tasks

logger = logging.getLogger("spc_bot")

class Failover(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        logger.info("--- [Failover System Initialized | Full Binary Sync] ---")

    async def cog_load(self):
        if not self.sync_loop.is_running():
            self.sync_loop.start()

    @tasks.loop(minutes=5)
    async def sync_loop(self):
        await self.bot.wait_until_ready()
        try:
            is_primary = getattr(self.bot.state, 'is_primary', False)
            if is_primary:
                await self.update_heartbeat()
                await self.push_binary_db()
            else:
                await self.pull_binary_db()
        except Exception as e:
            logger.error(f"[SYNC] Binary Loop Error: {e}")

    async def update_heartbeat(self):
        url = os.getenv("UPSTASH_REDIS_REST_URL")
        token = os.getenv("UPSTASH_REDIS_REST_TOKEN")
        headers = {"Authorization": f"Bearer {token}"}
        async with aiohttp.ClientSession() as session:
            await session.post(f"{url}/set/spcbot:state:sync/ALIVE/EX/420", headers=headers)

    async def push_binary_db(self):
        """Reads the DB file and pushes it as a Base64 string."""
        db_path = "/opt/spc-bot/cache/bot_state.db"
        url = os.getenv("UPSTASH_REDIS_REST_URL")
        token = os.getenv("UPSTASH_REDIS_REST_TOKEN")

        if not os.path.exists(db_path):
            return

        try:
            with open(db_path, "rb") as f:
                db_bytes = f.read()
            
            # Encode to base64 string for JSON transport
            encoded = base64.b64encode(db_bytes).decode('utf-8')
            headers = {"Authorization": f"Bearer {token}"}
            
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{url}/set/spcbot:state:raw_db", headers=headers, data=encoded) as resp:
                    if resp.status == 200:
                        size_kb = len(db_bytes) / 1024
                        logger.info(f"[SYNC] [BINARY] Pushed full DB ({size_kb:.1f} KB) to Upstash.")
        except Exception as e:
            logger.error(f"[SYNC] Binary Push Error: {e}")

    async def pull_binary_db(self):
        """Pulls the Base64 string and overwrites the local DB file."""
        db_path = "/opt/spc-bot/cache/bot_state.db"
        url = os.getenv("UPSTASH_REDIS_REST_URL")
        token = os.getenv("UPSTASH_REDIS_REST_TOKEN")

        try:
            async with aiohttp.ClientSession() as session:
                headers = {"Authorization": f"Bearer {token}"}
                async with session.get(f"{url}/get/spcbot:state:raw_db", headers=headers) as resp:
                    if resp.status == 200:
                        res_json = await resp.json()
                        encoded_data = res_json.get('result')
                        if not encoded_data:
                            return

                        db_bytes = base64.b64decode(encoded_data)
                        
                        # Atomic write: write to temp file then rename
                        with open(db_path + ".tmp", "wb") as f:
                            f.write(db_bytes)
                        os.replace(db_path + ".tmp", db_path)
                        
                        logger.info(f"[SYNC] [BINARY] Overwrote local DB with cloud state.")
        except Exception as e:
            logger.error(f"[SYNC] Binary Hydration Error: {e}")

async def setup(bot):
    await bot.add_cog(Failover(bot))
