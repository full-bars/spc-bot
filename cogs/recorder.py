import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, Optional, Set

import discord
from discord import app_commands
from discord.ext import commands, tasks
from PIL import Image

from config import ARCHIVE_DIR, RECORDING_DIR
from lib.vad_plotter.vad_reader import download_vad, find_file_times
from utils.discord_send import safe_send
from utils.state_store import get_state, set_state
from utils.worker_pool import get_executor

logger = logging.getLogger("spc_bot.recorder")


def _log_task_exception(task: asyncio.Task) -> None:
    try:
        exc = task.exception()
    except (asyncio.CancelledError, RuntimeError):
        return
    if exc:
        logger.error("Background task failed", exc_info=exc)


STATE_KEY = "vad_active_missions"


class VADRecordingMission:
    def __init__(self, site_id: str, trigger_ts: float, event_ids: Set[str] = None):
        self.site_id = site_id
        self.trigger_ts = trigger_ts
        self.event_ids = event_ids or set()
        self.start_ts = trigger_ts - (60 * 60)  # 1h lookback
        self.end_ts = trigger_ts + (90 * 60)  # 90m follow-up
        self.processed_timestamps: Set[float] = set()

        # Create storage directory
        self.dir = os.path.join(RECORDING_DIR, f"{site_id}_{int(trigger_ts)}")
        os.makedirs(self.dir, exist_ok=True)

    def extend(self, new_trigger_ts: float, event_id: str = None):
        """Extend the follow-up window and link a new event ID."""
        self.end_ts = max(self.end_ts, new_trigger_ts + (90 * 60))
        if event_id:
            self.event_ids.add(event_id)
        logger.info(
            f"Extended mission for {self.site_id} until {datetime.fromtimestamp(self.end_ts, timezone.utc)} (Events: {len(self.event_ids)})"
        )

    def to_dict(self) -> dict:
        return {
            "site_id": self.site_id,
            "trigger_ts": self.trigger_ts,
            "event_ids": list(self.event_ids),
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "processed_timestamps": list(self.processed_timestamps),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "VADRecordingMission":
        mission = cls(d["site_id"], d["trigger_ts"], set(d["event_ids"]))
        mission.start_ts = d["start_ts"]
        mission.end_ts = d["end_ts"]
        mission.processed_timestamps = set(d["processed_timestamps"])
        return mission


class RecorderCog(commands.Cog):
    MANAGED_TASK_NAMES = [("recorder_loop", "recorder_loop")]

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_missions: Dict[str, VADRecordingMission] = {}
        self.executor = get_executor()

        # Ensure directories exist
        os.makedirs(RECORDING_DIR, exist_ok=True)
        os.makedirs(ARCHIVE_DIR, exist_ok=True)

        self.recorder_loop.start()

    def cog_unload(self):
        self.recorder_loop.cancel()

    async def _persist_missions(self):
        """Save active missions to shared state store for resumption after restart/failover."""
        try:
            data = {sid: m.to_dict() for sid, m in self.active_missions.items()}
            await set_state(STATE_KEY, json.dumps(data))
        except Exception as e:
            logger.error(f"Failed to persist missions: {e}")

    async def _load_missions(self):
        """Reload active missions from shared state store."""
        try:
            raw = await get_state(STATE_KEY)
            if raw:
                data = json.loads(raw)
                for sid, m_dict in data.items():
                    self.active_missions[sid] = VADRecordingMission.from_dict(m_dict)
                if self.active_missions:
                    logger.info(f"Resumed {len(self.active_missions)} missions from state store")
        except Exception as e:
            logger.error(f"Failed to load missions: {e}")

    async def start_mission(self, site_id: str, trigger_ts: float, event_id: str = None):
        if site_id in self.active_missions:
            self.active_missions[site_id].extend(trigger_ts, event_id=event_id)
        else:
            self.active_missions[site_id] = VADRecordingMission(
                site_id, trigger_ts, {event_id} if event_id else None
            )
            logger.info(f"Started NEW mission for {site_id} (Initial Event: {event_id})")

        await self._persist_missions()

    @tasks.loop(minutes=5)
    async def recorder_loop(self):
        if not self.active_missions:
            # Check for missions to resume on first run or if empty
            await self._load_missions()
            if not self.active_missions:
                return

        now = datetime.now(timezone.utc).timestamp()

        # 1. Fetch data for all active missions
        tasks_list = []
        for _site_id, mission in self.active_missions.items():
            tasks_list.append(self._record_step(mission))

        if tasks_list:
            await asyncio.gather(*tasks_list, return_exceptions=True)
            await self._persist_missions()  # Save progress (processed_timestamps)

        # 2. Identify missions to finalize
        to_finalize = []
        for site_id, mission in list(self.active_missions.items()):
            if now > mission.end_ts:
                to_finalize.append(site_id)

        # 3. Handle finalization
        if to_finalize:
            for site_id in to_finalize:
                mission = self.active_missions.pop(site_id)
                t = asyncio.create_task(self._finalize_mission(mission))
                t.add_done_callback(_log_task_exception)
            await self._persist_missions()

    async def _record_step(self, mission: VADRecordingMission):
        """Fetch available VAD scans for the mission window."""
        try:
            available_times = await find_file_times(mission.site_id)

            mission_start = datetime.fromtimestamp(mission.start_ts, timezone.utc)
            mission_end = datetime.fromtimestamp(mission.end_ts, timezone.utc)

            for _fn, dt in available_times:
                dt_utc = dt.replace(tzinfo=timezone.utc) if not dt.tzinfo else dt
                ts = dt_utc.timestamp()

                if (
                    mission_start <= dt_utc <= mission_end
                    and ts not in mission.processed_timestamps
                ):
                    try:
                        await download_vad(mission.site_id, time=dt_utc, cache_path=mission.dir)
                        mission.processed_timestamps.add(ts)
                        logger.debug(f"Saved scan for {mission.site_id} @ {dt_utc}")
                    except Exception as e:
                        logger.warning(f"Failed to fetch {mission.site_id} @ {dt_utc}: {e}")

        except Exception as e:
            logger.error(f"Step failed for {mission.site_id}: {e}")

    async def _finalize_mission(self, mission: VADRecordingMission):
        """Build the evolution GIF and cleanup."""
        logger.info(f"Finalizing mission for {mission.site_id}...")

        loop = asyncio.get_running_loop()
        try:
            files = await loop.run_in_executor(None, os.listdir, mission.dir)
            files = [f for f in files if f.endswith(".has")]
        except FileNotFoundError:
            logger.warning(f"Mission dir {mission.dir} not found. Skipping.")
            return

        if not files:
            logger.warning(f"No data saved for mission {mission.site_id}. Skipping GIF.")
            return
        files.sort()

        frame_paths = []
        try:
            frame_tasks = []
            for filename in files:
                input_path = os.path.join(mission.dir, filename)
                output_path = os.path.join(mission.dir, f"{filename}.png")
                frame_tasks.append(
                    loop.run_in_executor(
                        self.executor,
                        self._render_frame_worker,
                        input_path,
                        output_path,
                        mission.site_id,
                    )
                )
                frame_paths.append(output_path)
            await asyncio.gather(*frame_tasks)

            if frame_paths:
                gif_name = f"{mission.site_id}_{int(mission.trigger_ts)}_evolution.gif"
                gif_path = os.path.join(ARCHIVE_DIR, gif_name)

                await loop.run_in_executor(
                    self.executor, self._make_gif_worker, frame_paths, gif_path
                )

                logger.info(f"Created evolution GIF: {gif_path}")

                # 4. Calculate Peak SRH
                peak_srh = 0.0
                try:
                    peak_srh = await loop.run_in_executor(
                        self.executor, self._calc_srh_worker, mission.dir, files
                    )
                except Exception as e:
                    logger.warning(f"Peak SRH calc failed: {e}")

                # 5. Update DB for all events
                if mission.event_ids:
                    from utils.events_db import update_event_environment

                    for eid in mission.event_ids:
                        await update_event_environment(eid, gif_path, peak_srh)
                    await self._post_forensic_summary(mission, gif_path, peak_srh)

                await loop.run_in_executor(None, self._cleanup_worker, mission.dir)
                logger.info(f"Cleaned up temporary data for {mission.site_id}")
        except Exception as e:
            logger.error(f"Finalization failed for {mission.site_id}: {e}")
            # Clean up temp files even on failure so the mission doesn't leak
            # disk space indefinitely.
            try:
                await loop.run_in_executor(None, self._cleanup_worker, mission.dir)
            except Exception:
                pass

    async def _post_forensic_summary(
        self, mission: VADRecordingMission, gif_path: str, peak_srh: float
    ):
        try:
            from config import DEV_CHANNEL_ID

            channel = self.bot.get_channel(DEV_CHANNEL_ID)
            if not channel:
                return
            first_eid = list(mission.event_ids)[0]
            from utils.events_db import get_events_db

            db = await get_events_db()
            async with db.execute(
                "SELECT location FROM significant_events WHERE event_id = ?", (first_eid,)
            ) as cur:
                row = await cur.fetchone()
            loc = row["location"] if row else "Multi-event outbreak"
            embed = discord.Embed(
                title=f"🌪️ Forensic Archive: {mission.site_id}",
                description=f"**Primary Event**: {loc}\n**Peak 0-1km SRH**: {peak_srh:.0f} m2/s2\n**Linked Warnings**: {len(mission.event_ids)}",
                color=discord.Color.dark_blue(),
                timestamp=datetime.now(timezone.utc),
            )
            file = discord.File(gif_path, filename="evolution.gif")
            embed.set_image(url="attachment://evolution.gif")
            await safe_send(channel, context="VAD forensic archive summary", embed=embed, file=file)
        except Exception as e:
            logger.error(f"Summary post failed: {e}")

    @app_commands.command(name="archive", description="Search the environmental forensics archive")
    @app_commands.describe(radar="4-letter radar ID", date="YYYY-MM-DD")
    async def archive_search(
        self,
        interaction: discord.Interaction,
        radar: Optional[str] = None,
        date: Optional[str] = None,
    ):
        await interaction.response.defer(ephemeral=True)
        query = "SELECT * FROM significant_events WHERE gif_path IS NOT NULL"
        params = []
        if radar:
            query += " AND event_id LIKE ?"
            params.append(f"%{radar.upper()}%")
        if date:
            try:
                dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                ts_start = dt.timestamp()
                query += " AND timestamp BETWEEN ? AND ?"
                params.extend([ts_start, ts_start + 86400])
            except ValueError:
                await interaction.followup.send("Invalid date format.", ephemeral=True)
                return
        query += " ORDER BY timestamp DESC LIMIT 10"
        from utils.events_db import get_events_db

        db = await get_events_db()
        async with db.execute(query, tuple(params)) as cur:
            rows = await cur.fetchall()
        if not rows:
            await interaction.followup.send("No archived forensics found.", ephemeral=True)
            return
        embed = discord.Embed(title="📂 Forensic Archive Search", color=discord.Color.blue())
        for r in rows:
            time_str = datetime.fromtimestamp(r["timestamp"], timezone.utc).strftime(
                "%Y-%m-%d %H:%MZ"
            )
            embed.add_field(
                name=f"{r['event_id'].split(':')[-1]}",
                value=f"**{r['location']}**\nSRH: {r['srh_0_1']:.0f} | {time_str}",
                inline=False,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @staticmethod
    def _render_frame_worker(input_path, output_path, rid):
        from lib.vad_plotter.params import compute_parameters
        from lib.vad_plotter.plot import plot_hodograph
        from lib.vad_plotter.vad_reader import VADFile

        try:
            with open(input_path, "rb") as f:
                vad = VADFile(f)
            vad.rid = rid
            params = compute_parameters(vad, "right-mover")
            plot_hodograph(vad, params, output_path, web=False, fixed=True)
            return True
        except Exception as e:
            print(f"Frame render failed: {e}")
            return False

    @staticmethod
    def _make_gif_worker(frame_paths, gif_path):
        frames = [Image.open(f) for f in frame_paths]
        try:
            frames[0].save(
                gif_path,
                format="GIF",
                append_images=frames[1:],
                save_all=True,
                duration=200,
                loop=0,
            )
        finally:
            for frame in frames:
                frame.close()

    @staticmethod
    def _calc_srh_worker(mission_dir, files):
        import numpy as np

        from lib.vad_plotter.params import compute_bunkers, compute_srh
        from lib.vad_plotter.vad_reader import VADFile

        srh_max = 0.0
        for filename in files:
            p = os.path.join(mission_dir, filename)
            if not os.path.exists(p):
                continue
            try:
                with open(p, "rb") as f:
                    vad = VADFile(f)
                if len(vad["wind_dir"]) < 2:
                    continue

                # compute_bunkers returns (right, left, mean) vectors in (dir, spd)
                brm, _, _ = compute_bunkers(vad)
                srh = compute_srh(vad, brm, 1.0)  # 1km SRH

                if not np.isnan(srh):
                    srh_max = max(srh_max, srh)
            except Exception:
                continue
        return srh_max

    @staticmethod
    def _cleanup_worker(mission_dir):
        import shutil

        if os.path.exists(mission_dir):
            shutil.rmtree(mission_dir)


async def setup(bot: commands.Bot):
    await bot.add_cog(RecorderCog(bot))
