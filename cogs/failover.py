import os
import json
import asyncio
import logging
from discord.ext import commands, tasks

logger = logging.getLogger("spc_bot")

class Failover(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        rank = getattr(bot.state, 'rank', 'UNKNOWN')
        logger.info(f"--- [Failover System Initialized | Rank: {rank}] ---")

    async def cog_load(self):
        asyncio.create_task(self.initialize_sync())

    async def initialize_sync(self):
        await self.bot.wait_until_ready()
        
        retries = 0
        while retries < 15:
            # 1. Try standard access
            db = getattr(self.bot, 'db', None)
            config = getattr(self.bot, 'config', None)
            session = getattr(self.bot, 'session', None)
            
            # 2. Try raw dict access
            if not db: db = self.bot.__dict__.get('db')
            if not config: config = self.bot.__dict__.get('config')
            if not session: session = self.bot.__dict__.get('session')

            if db and config and session:
                logger.info(f"[SYNC] All systems ready after {retries*2}s.")
                break
                
            await asyncio.sleep(2)
            retries += 1
            if retries % 2 == 0:
                logger.info(f"[SYNC] Polling attributes (Attempt {retries}/15)...")

        if not db or not config:
            logger.error(f"[SYNC] Attribute detection failed. Forcing manual lookup via state object...")
            # Last ditch effort: Try to pull from the bot's state object if main.py put it there
            db = getattr(self.bot.state, 'db', None)
            config = getattr(self.bot.state, 'config', None)

        if not db or not config:
            # Absolute last resort: just try to access them directly and catch the error
            try:
                db = self.bot.db
                config = self.bot.config
                session = self.bot.session
            except AttributeError:
                logger.error("[SYNC] Absolute Critical Failure. Attributes unreachable via any scope.")
                return

        # Start logic
        if not self.sync_loop.is_running():
            self.sync_loop.start()
        
        if self.bot.state.is_primary:
            await self.push_state_to_redis(db, config, session)
        else:
            await self.hydrate_local_state(db, config, session)

    @tasks.loop(minutes=5)
    async def sync_loop(self):
        if self.bot.state.is_primary:
            # Re-fetch attributes for the periodic loop
            db = getattr(self.bot, 'db', None) or self.bot.__dict__.get('db')
            config = getattr(self.bot, 'config', None) or self.bot.__dict__.get('config')
            session = getattr(self.bot, 'session', None) or self.bot.__dict__.get('session')
            if db and config and session:
                await self.push_state_to_redis(db, config, session)

    async def push_state_to_redis(self, db, config, session):
        try:
            data = {}
            async with db.execute("SELECT md_number FROM posted_mds ORDER BY id DESC LIMIT 25") as cursor:
                data['mds'] = [row[0] for row in await cursor.fetchall()]
            async with db.execute("SELECT watch_number FROM posted_watches ORDER BY id DESC LIMIT 25") as cursor:
                data['watches'] = [row[0] for row in await cursor.fetchall()]

            url = f"{config.UPSTASH_REDIS_REST_URL}/set/spcbot:state:posted_records"
            headers = {"Authorization": f"Bearer {config.UPSTASH_REDIS_REST_TOKEN}"}
            
            async with session.post(url, headers=headers, data=json.dumps(data)) as resp:
                if resp.status == 200:
                    logger.info(f"[SYNC] Pushed {len(data['mds'])} MDs and {len(data['watches'])} Watches.")
                else:
                    logger.error(f"[SYNC] Push failed: {resp.status}")
        except Exception as e:
            logger.error(f"[SYNC] Push Error: {e}")

    async def hydrate_local_state(self, db, config, session):
        try:
            url = f"{config.UPSTASH_REDIS_REST_URL}/get/spcbot:state:posted_records"
            headers = {"Authorization": f"Bearer {config.UPSTASH_REDIS_REST_TOKEN}"}
            
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    res_json = await resp.json()
                    if not res_json.get('result'): return
                    
                    data = json.loads(res_json['result'])
                    async with db.cursor() as cursor:
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
