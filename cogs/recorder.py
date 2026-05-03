import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Set
import os

import discord
from discord.ext import commands, tasks

from lib.vad_plotter.vad_reader import download_vad, find_file_times
from lib.vad_plotter.wsr88d import build_has_name
from config import CACHE_DIR

logger = logging.getLogger("spc_bot")

RECORDING_DIR = os.path.join(CACHE_DIR, "vad_recordings")

class VADRecordingMission:
    def __init__(self, site_id: str, trigger_ts: float):
        self.site_id = site_id
        self.trigger_ts = trigger_ts
        self.start_ts = trigger_ts - (60 * 60) # 1h lookback
        self.end_ts = trigger_ts + (90 * 60)   # 90m follow-up
        self.processed_timestamps: Set[float] = set()
        
        # Create storage directory
        self.dir = os.path.join(RECORDING_DIR, f"{site_id}_{int(trigger_ts)}")
        os.makedirs(self.dir, exist_ok=True)

    def extend(self, new_trigger_ts: float):
        """Extend the follow-up window if a new warning is issued."""
        self.end_ts = max(self.end_ts, new_trigger_ts + (90 * 60))
        logger.info(f"[RECORDER] Extended mission for {self.site_id} until {datetime.fromtimestamp(self.end_ts, timezone.utc)}")

class RecorderCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_missions: Dict[str, VADRecordingMission] = {}
        
        # Ensure base recording dir exists
        os.makedirs(RECORDING_DIR, exist_ok=True)
        
        self.recorder_loop.start()

    def cog_unload(self):
        self.recorder_loop.cancel()

    def start_mission(self, site_id: str, trigger_ts: float):
        if site_id in self.active_missions:
            self.active_missions[site_id].extend(trigger_ts)
        else:
            self.active_missions[site_id] = VADRecordingMission(site_id, trigger_ts)
            logger.info(f"[RECORDER] Started NEW mission for {site_id}")

    @tasks.loop(minutes=5)
    async def recorder_loop(self):
        await self.bot.wait_until_ready()
        
        now = datetime.now(timezone.utc).timestamp()
        
        # 1. Fetch data for all active missions
        tasks_list = []
        for site_id, mission in self.active_missions.items():
            tasks_list.append(self._record_step(mission))
            
        if tasks_list:
            await asyncio.gather(*tasks_list, return_exceptions=True)

        # 2. Identify missions to finalize
        to_finalize = []
        for site_id, mission in list(self.active_missions.items()):
            if now > mission.end_ts:
                to_finalize.append(site_id)

        # 3. Handle finalization
        for site_id in to_finalize:
            mission = self.active_missions.pop(site_id)
            asyncio.create_task(self._finalize_mission(mission))

    async def _record_step(self, mission: VADRecordingMission):
        """Fetch available VAD scans for the mission window."""
        try:
            available_times = await find_file_times(mission.site_id)
            
            mission_start = datetime.fromtimestamp(mission.start_ts, timezone.utc)
            mission_end = datetime.fromtimestamp(mission.end_ts, timezone.utc)
            
            for fn, dt in available_times:
                dt_utc = dt.replace(tzinfo=timezone.utc) if not dt.tzinfo else dt
                ts = dt_utc.timestamp()
                
                if mission_start <= dt_utc <= mission_end and ts not in mission.processed_timestamps:
                    # Download and save to mission dir
                    try:
                        vad = await download_vad(mission.site_id, time=dt_utc)
                        if vad:
                            # The vad_reader saves to cache_path if provided, but we want 
                            # specific control here for the recording mission.
                            # We'll re-save the content to our mission dir.
                            # (Actually, download_vad returns a VADFile object)
                            
                            # For simplicity in v1, we'll just re-fetch the raw bytes 
                            # or modify download_vad to return them.
                            # Let's just use the build_has_name to save it.
                            filename = build_has_name(mission.site_id, dt_utc)
                            # ... (fetch and save logic)
                            mission.processed_timestamps.add(ts)
                    except Exception as e:
                        logger.warning(f"[RECORDER] Failed to fetch {mission.site_id} @ {dt_utc}: {e}")
                        
        except Exception as e:
            logger.error(f"[RECORDER] Step failed for {mission.site_id}: {e}")

    async def _finalize_mission(self, mission: VADRecordingMission):
        """Build the GIF and archive the metadata."""
        logger.info(f"[RECORDER] Finalizing mission for {mission.site_id}...")
        # GIF logic will go here in Phase 3
        pass

async def setup(bot: commands.Bot):
    await bot.add_cog(RecorderCog(bot))
