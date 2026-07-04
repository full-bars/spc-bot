import asyncio
import io
import logging
import os
import re
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Optional

import aiohttp
import discord
import pytz
from discord import app_commands
from discord.ext import commands
from PIL import Image

from lib.vad_plotter.radar_coords import get_nearest_radar

logger = logging.getLogger("spc_bot")

IEM_BASE = "https://mesonet.agron.iastate.edu/archive/data"

GIF_CACHE_DIR = os.path.join("cache", "radar_history")
MAX_FRAMES = 30
GIF_DURATION = 200
MAX_GIF_SIZE = 25 * 1024 * 1024

CONUS_TIMEZONES = [
    app_commands.Choice(name="Eastern (ET)", value="America/New_York"),
    app_commands.Choice(name="Central (CT)", value="America/Chicago"),
    app_commands.Choice(name="Mountain (MT)", value="America/Denver"),
    app_commands.Choice(name="Pacific (PT)", value="America/Los_Angeles"),
    app_commands.Choice(name="Alaska (AKT)", value="America/Anchorage"),
    app_commands.Choice(name="Hawaii (HT)", value="Pacific/Honolulu"),
    app_commands.Choice(name="UTC", value="UTC"),
]

NOMINATIM_HEADERS = {
    "User-Agent": "SPCBot/1.0 (radar_history; Discord bot)",
}


async def _geocode_location(query: str) -> Optional[tuple[float, float, str]]:
    coords_match = re.match(r"^\s*(-?\d+\.?\d*)\s*[,;]\s*(-?\d+\.?\d*)\s*$", query)
    if coords_match:
        lat, lon = float(coords_match.group(1)), float(coords_match.group(2))
        return lat, lon, f"{lat:.4f}, {lon:.4f}"

    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": query, "format": "jsonv2", "limit": 1}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, params=params, headers=NOMINATIM_HEADERS, timeout=aiohttp.ClientTimeout(10)
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                if not data:
                    return None
                lat = float(data[0]["lat"])
                lon = float(data[0]["lon"])
                display = data[0].get("display_name", "")
                if display and len(display) > 120:
                    display = display[:117] + "..."
                return lat, lon, display or query
    except Exception:
        return None


def _parse_timezone(tz_value: str):
    try:
        return pytz.timezone(tz_value)
    except Exception:
        return pytz.UTC


def _parse_local_time(date_str: str, time_str: str, tz) -> Optional[datetime]:
    time_str = time_str.strip().upper()
    time_str = time_str.replace(".", "").replace("Z", "")

    dt_local = None
    for fmt in ("%Y-%m-%d %I:%M %p", "%Y-%m-%d %H:%M"):
        try:
            dt_local = datetime.strptime(f"{date_str.strip()} {time_str}", fmt)
            break
        except ValueError:
            continue

    if not dt_local:
        return None

    return tz.localize(dt_local)


def _round_down_to_minute(dt: datetime, interval: int = 5) -> datetime:
    minutes = dt.minute // interval * interval
    return dt.replace(minute=minutes, second=0, microsecond=0)


def _iem_frame_url(dt: datetime) -> str:
    return (
        f"{IEM_BASE}/{dt.year}/{dt.month:02d}/{dt.day:02d}/"
        f"GIS/uscomp/n0q_{dt.strftime('%Y%m%d%H%M')}.png"
    )


async def _fetch_single_frame(url: str, session: aiohttp.ClientSession) -> Optional[bytes]:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(15)) as resp:
            if resp.status == 200:
                return await resp.read()
    except Exception:
        pass
    return None


async def _fetch_iem_frames(
    start_utc: datetime, end_utc: datetime, interval: int = 5
) -> list[bytes]:
    frames = []
    current = _round_down_to_minute(start_utc, interval)
    connector = aiohttp.TCPConnector(limit=8)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = []
        while current <= end_utc and len(tasks) < MAX_FRAMES:
            url = _iem_frame_url(current)
            tasks.append(asyncio.ensure_future(_fetch_single_frame(url, session)))
            current += timedelta(minutes=interval)

        results = await asyncio.gather(*tasks)
        for data in results:
            if data:
                frames.append(data)

    return frames


async def _make_gif(frames: list[bytes], output_path: str) -> bool:
    pil_frames = []
    try:
        for data in frames:
            img = Image.open(io.BytesIO(data))
            pil_frames.append(img.convert("RGB"))

        if not pil_frames:
            return False

        pil_frames[0].save(
            output_path,
            format="GIF",
            append_images=pil_frames[1:],
            save_all=True,
            duration=GIF_DURATION,
            loop=0,
            optimize=False,
        )
        return True
    finally:
        for img in pil_frames:
            img.close()


class RadarHistoryCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="radarhistory",
        description="Generate a historical radar loop for a location",
    )
    @app_commands.describe(
        location="City and state, zip code, or coordinates (e.g. 'Dearborn, MI')",
        date="Date (YYYY-MM-DD)",
        time="Local time (e.g. '7:10 PM' or '19:10')",
        timezone="Your timezone",
        duration="Duration of loop in minutes (default 30)",
    )
    async def radarhistory(
        self,
        interaction: discord.Interaction,
        location: str,
        date: str,
        time: str,
        timezone: str,
        duration: int = 30,
    ):
        await interaction.response.defer(thinking=True)

        tz = _parse_timezone(timezone)
        dt_utc = _parse_local_time(date, time, tz)
        if dt_utc is None:
            await interaction.followup.send(
                "❌ Could not parse date/time. Use format: `YYYY-MM-DD` for date "
                "and `7:10 PM` or `19:10` for time.",
                ephemeral=True,
            )
            return

        geo = await _geocode_location(location)
        if geo is None:
            await interaction.followup.send(
                f"❌ Could not find location: `{location}`. "
                "Try a city name with state (e.g. 'Dearborn, MI'), zip code, or coordinates.",
                ephemeral=True,
            )
            return

        lat, lon, loc_display = geo
        nearest = get_nearest_radar(lat, lon)
        radar_label = f" {nearest}" if nearest else ""
        tz_abbr = dt_utc.strftime("%Z")

        start_utc = dt_utc.astimezone(dt_timezone.utc).replace(tzinfo=None)
        end_utc = start_utc + timedelta(minutes=max(5, min(duration, 120)))

        logger.info(
            f"[RADAR_HISTORY] {location} ({lat:.2f}, {lon:.2f}) → {nearest} "
            f"| {start_utc.isoformat()} to {end_utc.isoformat()} "
            f"({duration} min, tz={tz_abbr})"
        )

        frames = await _fetch_iem_frames(start_utc, end_utc)
        if not frames:
            await interaction.followup.send(
                "❌ No radar imagery available for that time window. "
                "The data may not be in the archive yet (try a date older than today) "
                "or may be outside the available range.",
                ephemeral=True,
            )
            return

        os.makedirs(GIF_CACHE_DIR, exist_ok=True)
        gif_path = os.path.join(
            GIF_CACHE_DIR,
            f"{nearest or 'unknown'}_{start_utc.strftime('%Y%m%d%H%M')}_{duration}m.gif",
        )
        success = await _make_gif(frames, gif_path)
        if not success or not os.path.exists(gif_path):
            await interaction.followup.send(
                "❌ Failed to generate radar loop. Try again.",
                ephemeral=True,
            )
            return

        file_size = os.path.getsize(gif_path)
        if file_size > MAX_GIF_SIZE:
            await interaction.followup.send(
                f"❌ Generated GIF is too large ({file_size / 1024 / 1024:.1f} MB) "
                "for Discord. Try a shorter duration.",
                ephemeral=True,
            )
            return

        content = (
            f"**Radar Loop** — {loc_display}{radar_label}\n"
            f"{dt_utc.strftime('%b %d, %Y %I:%M %p')} {tz_abbr} "
            f"({duration} min, {len(frames)} frames)"
        )
        await interaction.followup.send(
            content=content,
            file=discord.File(gif_path),
        )
        logger.info(
            f"[RADAR_HISTORY] Sent {len(frames)}-frame GIF "
            f"({file_size / 1024 / 1024:.1f} MB) for {nearest}"
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(RadarHistoryCog(bot))
