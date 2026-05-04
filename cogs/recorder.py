import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Set, Optional
import os
import concurrent.futures
from PIL import Image

import discord
from discord import app_commands
from discord.ext import commands, tasks

from lib.vad_plotter.vad_reader import download_vad, find_file_times
from config import CACHE_DIR

logger = logging.getLogger("spc_bot")

RECORDING_DIR = os.path.join(CACHE_DIR, "vad_recordings")
ARCHIVE_DIR = os.path.join(CACHE_DIR, "event_archive")

class VADRecordingMission:
    def __init__(self, site_id: str, trigger_ts: float, event_id: str = None):
        self.site_id = site_id
        self.trigger_ts = trigger_ts
        self.event_id = event_id # Links to significant_events
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
        self.executor = concurrent.futures.ProcessPoolExecutor(max_workers=3)
        
        # Ensure directories exist
        os.makedirs(RECORDING_DIR, exist_ok=True)
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        
        self.recorder_loop.start()

    def cog_unload(self):
        self.recorder_loop.cancel()
        self.executor.shutdown(wait=False)

    def start_mission(self, site_id: str, trigger_ts: float, event_id: str = None):
        if site_id in self.active_missions:
            self.active_missions[site_id].extend(trigger_ts)
        else:
            self.active_missions[site_id] = VADRecordingMission(site_id, trigger_ts, event_id)
            logger.info(f"[RECORDER] Started NEW mission for {site_id} (Event: {event_id})")

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
                        # We use the raw fetcher to get bytes
                        # download_vad in its latest version returns a VADFile object
                        # but also handles caching if path is provided.
                        await download_vad(mission.site_id, time=dt_utc, cache_path=mission.dir)
                        mission.processed_timestamps.add(ts)
                        logger.debug(f"[RECORDER] Saved scan for {mission.site_id} @ {dt_utc}")
                    except Exception as e:
                        logger.warning(f"[RECORDER] Failed to fetch {mission.site_id} @ {dt_utc}: {e}")
                        
        except Exception as e:
            logger.error(f"[RECORDER] Step failed for {mission.site_id}: {e}")

    async def _finalize_mission(self, mission: VADRecordingMission):
        """Build the evolution GIF and cleanup."""
        logger.info(f"[RECORDER] Finalizing mission for {mission.site_id}...")
        
        # 1. Get all saved .has files in the mission directory
        files = [f for f in os.listdir(mission.dir) if f.endswith(".has")]
        if not files:
            logger.warning(f"[RECORDER] No data saved for mission {mission.site_id}. Skipping GIF.")
            return

        # Sort by timestamp (build_has_name format ensures sortability usually)
        files.sort()
        
        # 2. Render each frame using the existing matplotlib logic
        # We run this in our process pool to avoid blocking the bot
        frame_paths = []
        try:
            loop = asyncio.get_running_loop()
            for filename in files:
                input_path = os.path.join(mission.dir, filename)
                output_path = os.path.join(mission.dir, f"{filename}.png")
                
                # Execute existing vad.py as a subprocess or function call in pool
                # For now, we'll use a wrapper that calls the stable plotting logic
                await loop.run_in_executor(self.executor, self._render_frame_worker, input_path, output_path, mission.site_id)
                frame_paths.append(output_path)
            
            # 3. Stitch into GIF
            if frame_paths:
                gif_name = f"{mission.site_id}_{int(mission.trigger_ts)}_evolution.gif"
                gif_path = os.path.join(ARCHIVE_DIR, gif_name)
                
                frames = [Image.open(f) for f in frame_paths]
                frames[0].save(
                    gif_path,
                    format="GIF",
                    append_images=frames[1:],
                    save_all=True,
                    duration=200, # 200ms per frame
                    loop=0
                )
                
                logger.info(f"[RECORDER] Created evolution GIF: {gif_path}")
                
                # 4. Calculate Peak SRH for the mission
                peak_srh = 0.0
                try:
                    import numpy as np
                    from lib.vad_plotter.vad_reader import VADFile
                    from lib.vad_plotter.met_engine import vec2comp, storm_motion_bunkers, storm_relative_helicity
                    for filename in files:
                        with open(os.path.join(mission.dir, filename), 'rb') as f:
                            vad = VADFile(f)
                        u, v = vec2comp(vad['wind_dir'], vad['wind_spd'])
                        sm = storm_motion_bunkers(u, v, vad['altitude'])
                        srh = storm_relative_helicity(u, v, vad['altitude'], 0, 1.0, *sm['right'])
                        if not np.isnan(srh):
                            peak_srh = max(peak_srh, srh)
                except Exception as e:
                    logger.warning(f"[RECORDER] Could not calculate peak SRH: {e}")

                # 5. Update DB
                if mission.event_id:
                    from utils.events_db import update_event_environment
                    await update_event_environment(mission.event_id, gif_path, peak_srh)
                    
                    # 6. Post summary to forensics channel
                    await self._post_forensic_summary(mission, gif_path, peak_srh)

                # 7. Cleanup raw data
                import shutil
                shutil.rmtree(mission.dir)
                logger.info(f"[RECORDER] Cleaned up temporary data for {mission.site_id}")
        except Exception as e:
            logger.error(f"[RECORDER] Finalization failed for {mission.site_id}: {e}")

    async def _post_forensic_summary(self, mission: VADRecordingMission, gif_path: str, peak_srh: float):
        """Post a detailed summary of the recorded mission to Discord."""
        try:
            from config import DEV_CHANNEL_ID
            # Use DEV_CHANNEL_ID as default forensics channel for now
            channel = self.bot.get_channel(DEV_CHANNEL_ID)
            if not channel: return
            
            # Fetch event details for location
            from utils.events_db import get_events_db
            db = await get_events_db()
            async with db.execute("SELECT location FROM significant_events WHERE event_id = ?", (mission.event_id,)) as cur:
                row = await cur.fetchone()
            
            loc = row["location"] if row else "Unknown Location"
            
            embed = discord.Embed(
                title=f"🌪️ Forensic Archive: {mission.site_id}",
                description=(
                    f"**Event**: {loc}\n"
                    f"**Peak 0-1km SRH**: {peak_srh:.0f} m2/s2\n"
                    f"**Mission Window**: 150 minutes"
                ),
                color=discord.Color.dark_blue(),
                timestamp=datetime.now(timezone.utc)
            )
            embed.set_footer(text=f"Event ID: {mission.event_id}")
            
            file = discord.File(gif_path, filename="evolution.gif")
            embed.set_image(url="attachment://evolution.gif")
            
            await channel.send(embed=embed, file=file)
            logger.info(f"[RECORDER] Posted forensic summary for {mission.site_id}")
        except Exception as e:
            logger.error(f"[RECORDER] Failed to post summary: {e}")

    @app_commands.command(name="archive", description="Search the environmental forensics archive")
    @app_commands.describe(radar="4-letter radar ID (e.g. KTLX)", date="Date in YYYY-MM-DD format (optional)")
    async def archive_search(self, interaction: discord.Interaction, radar: Optional[str] = None, date: Optional[str] = None):
        await interaction.response.defer(ephemeral=True)
        
        query = "SELECT * FROM significant_events WHERE gif_path IS NOT NULL"
        params = []
        
        if radar:
            query += " AND event_id LIKE ?"
            params.append(f"%{radar.upper()}%")
        
        if date:
            try:
                # Simple string match on vtec_id or timestamp range
                dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                ts_start = dt.timestamp()
                ts_end = ts_start + 86400
                query += " AND timestamp BETWEEN ? AND ?"
                params.extend([ts_start, ts_end])
            except:
                await interaction.followup.send("Invalid date format. Use YYYY-MM-DD.", ephemeral=True)
                return
                
        query += " ORDER BY timestamp DESC LIMIT 10"
        
        from utils.events_db import get_events_db
        db = await get_events_db()
        async with db.execute(query, tuple(params)) as cur:
            rows = await cur.fetchall()
            
        if not rows:
            await interaction.followup.send("No archived forensics found for those criteria.", ephemeral=True)
            return
            
        embed = discord.Embed(title="📂 Forensic Archive Search", color=discord.Color.blue())
        for r in rows:
            time_str = datetime.fromtimestamp(r['timestamp'], timezone.utc).strftime('%Y-%m-%d %H:%MZ')
            val = f"**{r['location']}**\nSRH: {r['srh_0_1']:.0f} | {time_str}"
            embed.add_field(name=f"{r['event_id'].split(':')[-1]}", value=val, inline=False)
            
        await interaction.followup.send(embed=embed, ephemeral=True)

    @staticmethod
    def _render_frame_worker(input_path, output_path, rid):
        """Worker function to render a single hodograph frame using matplotlib."""
        from lib.vad_plotter.vad_reader import VADFile
        from lib.vad_plotter.plot import plot_vad
        try:
            with open(input_path, 'rb') as f:
                vad = VADFile(f)
            
            # Use the existing stable plot function
            plot_vad(vad, rid, output_path, web=False, fixed=True)
            return True
        except Exception as e:
            print(f"Frame render failed: {e}")
            return False

async def setup(bot: commands.Bot):
    await bot.add_cog(RecorderCog(bot))
