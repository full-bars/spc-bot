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
        logger.info(f"--- [Failover System | Rank {getattr(bot.state, 'rank', 'U')}] ---")

    async def cog_load(self):
        if not self.sync_loop.is_running():
            self.sync_loop.start()

    @tasks.loop(minutes=5)
    async def sync_loop(self):
        await self.bot.wait_until_ready()
        try:
            # Check if we should promote based on the Heartbeat TTL
            if not self.bot.state.is_primary:
                await self.check_for_promotion()

            if self.bot.state.is_primary:
                await self.update_heartbeat()
                await self.push_binary_db()
            else:
                await self.pull_binary_db()
        except Exception as e:
            logger.error(f"[SYNC] Loop Error: {e}")

    async def check_for_promotion(self):
        """Phoenix checks if Portland's heartbeat has expired."""
        url = os.getenv("UPSTASH_REDIS_REST_URL")
        token = os.getenv("UPSTASH_REDIS_REST_TOKEN")
        headers = {"Authorization": f"Bearer {token}"}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{url}/get/spcbot:state:sync", headers=headers) as resp:
                if resp.status == 200:
                    res_json = await resp.json()
                    # If result is None, the TTL expired! Portland is dead.
                    if res_json.get('result') is None:
                        logger.warning("!!! [FAILOVER] Portland heartbeat missing. Promoting to PRIMARY. !!!")
                        self.bot.state.is_primary = True

    async def update_heartbeat(self):
        url = os.getenv("UPSTASH_REDIS_REST_URL")
        token = os.getenv("UPSTASH_REDIS_REST_TOKEN")
        headers = {"Authorization": f"Bearer {token}"}
        async with aiohttp.ClientSession() as session:
            # Set ALIVE with 420s (7 min) TTL
            await session.post(f"{url}/set/spcbot:state:sync/ALIVE/EX/420", headers=headers)

    async def push_binary_db(self):
        db_path = "/opt/spc-bot/cache/bot_state.db"
        url = os.getenv("UPSTASH_REDIS_REST_URL")
        token = os.getenv("UPSTASH_REDIS_REST_TOKEN")
        if not os.path.exists(db_path): return
        try:
            with open(db_path, "rb") as f:
                db_bytes = f.read()
            encoded = base64.b64encode(db_bytes).decode('utf-8')
            headers = {"Authorization": f"Bearer {token}"}
            async with aiohttp.ClientSession() as session:
                await session.post(f"{url}/set/spcbot:state:raw_db", headers=headers, data=encoded)
                logger.info(f"[SYNC] [BINARY] Pushed full DB ({len(db_bytes)/1024:.1f} KB).")
        except Exception as e:
            logger.error(f"[SYNC] Push Error: {e}")

    async def pull_binary_db(self):
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
                        if encoded_data:
                            db_bytes = base64.b64decode(encoded_data)
                            with open(db_path + ".tmp", "wb") as f:
                                f.write(db_bytes)
                            os.replace(db_path + ".tmp", db_path)
                            logger.info("[SYNC] [BINARY] Overwrote local DB with cloud state.")
        except Exception as e:
            logger.error(f"[SYNC] Hydration Error: {e}")

async def setup(bot):
    await bot.add_cog(Failover(bot))
