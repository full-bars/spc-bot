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

logger = logging.getLogger("spc_bot")

class ReportsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.posted_reports = set() # Simple dedup

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

async def setup(bot: commands.Bot):
    await bot.add_cog(ReportsCog(bot))
