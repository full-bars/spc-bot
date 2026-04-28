# cogs/reports.py
import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ext import commands

from config import WARNINGS_CHANNEL_ID
from utils.http import http_get_bytes
from utils.state_store import (
    add_posted_survey, 
    get_posted_surveys, 
    prune_posted_surveys
)

logger = logging.getLogger("spc_bot")

class ReportsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.posted_reports = set() # Simple dedup
        self.posted_surveys: set[str] = set()
        self._surveys_loaded = False

    async def _ensure_surveys_loaded(self):
        if not self._surveys_loaded:
            try:
                self.posted_surveys = await get_posted_surveys()
                self._surveys_loaded = True
            except Exception as e:
                logger.warning(f"[REPORTS] Could not load posted surveys: {e}")

    async def post_report_now(self, product_id: str, raw_text: str, pil: str):
        """Handle LSR or PNS damage survey posts."""
        if product_id in self.posted_reports:
            return
        
        if pil == "LSR":
            await self._handle_lsr(product_id, raw_text)
        elif pil == "PNS":
            await self._handle_pns(product_id, raw_text)

    async def _handle_lsr(self, product_id: str, raw_text: str):
        # Local Storm Report parsing
        # Usually multiple reports can be in one LSR product, but IEMbot usually 
        # sends them individually or as a small block.
        # Format: TIME...EVENT...CITY...LAT/LON etc.
        
        # Split by the standard LSR separator if multiple
        reports = re.split(r"\n\n(?=\d{4}\s+[A-Z])", raw_text)
        
        channel = self.bot.get_channel(WARNINGS_CHANNEL_ID)
        if not channel: return

        for r in reports:
            if "LOCAL STORM REPORT" not in r and "EVENT" not in r:
                continue
            
            # Simple extraction
            m_event = re.search(r"(\d{4}\s+[AP]M)\s+([A-Z\s]+)\s+([\d\.\-]+)\s+MILES?\s+([A-Z\s]+)", r, re.I)
            # This is hard to parse generic LSRs without a state machine, 
            # so let's use a more robust regex for the header lines.
            
            # Match: 0131 AM     TSTM WND DMG     DICKSON                 36.03N 87.39W
            m_header = re.search(r"^(\d{4}\s+[AP]M)\s+(.{16})\s+(.{24})\s+(\d+\.\d+N\s+\d+\.\d+W)", r, re.M)
            if not m_header: continue

            time_str, event_type, city, coords = m_header.groups()
            event_type = event_type.strip()
            city = city.strip()
            
            # Remarks
            m_remarks = re.search(r"REMARKS\s*\.\.\.\s*(.*?)(?=\n\s*\n|\$\$|$)", r, re.I | re.DOTALL)
            remarks = m_remarks.group(1).replace("\n", " ").strip() if m_remarks else ""

            office = product_id.split("-")[1] if "-" in product_id else "NWS"
            
            emoji = "⚠️"
            color = discord.Color.blue()
            if "TORNADO" in event_type.upper():
                emoji = "🌪️"
                color = discord.Color.red()
            elif "WND" in event_type.upper() or "WIND" in event_type.upper():
                emoji = "🌬️"
                color = discord.Color.gold()
            elif "HAIL" in event_type.upper():
                emoji = "🧊"
                color = discord.Color.light_grey()
            elif "FLOOD" in event_type.upper():
                emoji = "🌊"
                color = discord.Color.dark_blue()

            embed = discord.Embed(
                title=f"{emoji} {event_type} - {city}",
                description=remarks if remarks else "No additional remarks.",
                color=color,
                timestamp=datetime.now(timezone.utc)
            )
            embed.set_footer(text=f"{office} LSR | {time_str} | {coords}")
            
            await channel.send(embed=embed)
            self.posted_reports.add(product_id)

    async def _handle_pns(self, product_id: str, raw_text: str):
        # Public Information Statement - Damage Survey
        if "DAMAGE SURVEY" not in raw_text.upper():
            return
        
        channel = self.bot.get_channel(WARNINGS_CHANNEL_ID)
        if not channel: return

        # Extract EF Rating
        rating = "N/A"
        m_rating = re.search(r"RATING:\s*(EF\-?\d+)", raw_text, re.I)
        if m_rating:
            rating = m_rating.group(1).upper()
        
        # Max Wind
        winds = "N/A"
        m_winds = re.search(r"ESTIMATED PEAK WIND:\s*([\d\-]+\s*MPH)", raw_text, re.I)
        if m_winds:
            winds = m_winds.group(1)

        # Location/Event
        m_event = re.search(r"\.\.\.(.*?)\.\.\.", raw_text)
        event_name = m_event.group(1).strip() if m_event else "NWS Damage Survey"

        office = product_id.split("-")[1] if "-" in product_id else "NWS"

        embed = discord.Embed(
            title=f"📐 {event_name}",
            description=f"**Max Rating:** {rating}\n**Peak Winds:** {winds}",
            color=discord.Color.teal(),
            timestamp=datetime.now(timezone.utc)
        )
        
        # Snippet of the summary if available
        m_summary = re.search(r"SUMMARY:\s*(.*?)(?=\n\s*\n|\$\$|$)", raw_text, re.I | re.DOTALL)
        if m_summary:
            summary = m_summary.group(1).replace("\n", " ").strip()
            if len(summary) > 500:
                summary = summary[:497] + "..."
            embed.add_field(name="Summary", value=summary, inline=False)

        embed.set_footer(text=f"{office} PNS | {product_id}")
        
        await channel.send(embed=embed)
        self.posted_reports.add(product_id)

        # Trigger Autoplot 253 check if this was a damage survey
        await self._ensure_surveys_loaded()
        
        # Try to find a date in the text to poll for Autoplot 253 tracks.
        # Patterns: MM/DD/YYYY or Month DD, YYYY
        event_date = None
        # Numerical: 05/21/2024
        m_num = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", raw_text)
        if m_num:
            event_date = f"{m_num.group(3)}-{m_num.group(1).zfill(2)}-{m_num.group(2).zfill(2)}"
        else:
            # Narrative: MAY 21 2024
            months = "JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC"
            m_narr = re.search(rf"({months})\w*\s+(\d{{1,2}})\s*,\s*(\d{{4}})", raw_text, re.I)
            if m_narr:
                m_str = m_narr.group(1).upper()[:3]
                m_idx = (months.split("|").index(m_str) + 1)
                event_date = f"{m_narr.group(3)}-{str(m_idx).zfill(2)}-{m_narr.group(2).zfill(2)}"

        if event_date:
            logger.info(f"[REPORTS] Detected event date {event_date} in PNS, checking for tracks")
            asyncio.create_task(self._check_for_surveys(event_date))

    async def _check_for_surveys(self, event_date: str):
        """Poll IEM metadata API for Autoplot 253 tracks on a specific date."""
        # IEM metadata endpoint: /plotting/auto/meta.py?p=253&date=YYYY-MM-DD
        url = f"https://mesonet.agron.iastate.edu/plotting/auto/meta.py?p=253&date={event_date}"
        try:
            content, status = await http_get_bytes(url, retries=2, timeout=10)
            if not content or status != 200:
                return

            import json as _json
            data = _json.loads(content)
            # Find datglobalid options
            options = {}
            args = data.get("arguments", [])
            for arg in args:
                if arg.get("id") == "datglobalid":
                    options = arg.get("options", {})
                    break
            
            if not options:
                return

            channel = self.bot.get_channel(WARNINGS_CHANNEL_ID)
            if not channel: return

            for guid, label in options.items():
                if guid in self.posted_surveys:
                    continue
                
                # Format: datglobalid:{GUID}::dat:YYYY-MM-DD::cmap:gist_rainbow::_r:t::dpi:100.png
                img_url = (
                    f"https://mesonet.agron.iastate.edu/plotting/auto/plot/253/"
                    f"datglobalid:{guid}::dat:{event_date}::cmap:gist_rainbow::"
                    f"_r:t::dpi:100.png"
                )
                
                embed = discord.Embed(
                    title=f"🌪️ Tornado Track + Lead Time",
                    description=f"**Event:** {label}\n**Date:** {event_date}",
                    color=discord.Color.red(),
                    url=f"https://mesonet.agron.iastate.edu/plotting/auto/?q=253&dat={event_date.replace('-', '/')}&datglobalid={guid}"
                )
                embed.set_image(url=img_url)
                embed.set_footer(text=f"IEM Autoplot 253 | {guid}")
                
                await channel.send(embed=embed)
                self.posted_surveys.add(guid)
                await add_posted_survey(guid)
                await prune_posted_surveys()
                logger.info(f"[REPORTS] Posted Autoplot 253 for {guid} ({label})")

        except Exception as e:
            logger.warning(f"[REPORTS] Survey check failed for {event_date}: {e}")

async def setup(bot: commands.Bot):
    await bot.add_cog(ReportsCog(bot))
