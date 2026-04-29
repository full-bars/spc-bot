# cogs/reports.py
import asyncio
import logging
import re
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from config import WARNINGS_CHANNEL_ID
from utils.http import http_get_bytes
from utils.state_store import (
    add_posted_report,
    add_posted_survey, 
    add_significant_event,
    get_posted_surveys, 
    prune_posted_reports,
    prune_posted_surveys
)

logger = logging.getLogger("spc_bot")

class ReportsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.posted_surveys: set[str] = set()
        self._surveys_loaded = False
        self.poll_lsrs.start()

    def cog_unload(self):
        self.poll_lsrs.cancel()

    async def _ensure_surveys_loaded(self):
        if not self._surveys_loaded:
            try:
                self.posted_surveys = await get_posted_surveys()
                self._surveys_loaded = True
            except Exception as e:
                logger.warning(f"[REPORTS] Could not load posted surveys: {e}")

    async def post_report_now(self, product_id: str, raw_text: str, pil: str):
        """Handle LSR or PNS damage survey posts."""
        if product_id in self.bot.state.posted_reports:
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
        if not channel:
            return

        for r in reports:
            if "LOCAL STORM REPORT" not in r and "EVENT" not in r:
                continue
            
            # Match: 0131 AM     TSTM WND DMG     DICKSON                 36.03N 87.39W
            m_header = re.search(r"^(\d{4}\s+[AP]M)\s+(.{16})\s+(.{24})\s+(\d+\.\d+N\s+\d+\.\d+W)", r, re.M)
            if not m_header:
                continue

            time_str, event_type, city, coords = m_header.groups()
            event_type = event_type.strip()
            city = city.strip()

            # Append state from the county/date line that follows the header:
            # "04/29/2026                   Madison           MO   Public"
            m_state = re.search(r"^\d{2}/\d{2}/\d{4}\s+.{10,}\s([A-Z]{2})\s", r, re.M)
            if m_state:
                city = f"{city}, {m_state.group(1)}"

            # Remarks
            m_remarks = re.search(r"REMARKS\s*\.\.\.\s*(.*?)(?=\n\s*\n|\$\$|$)", r, re.I | re.DOTALL)
            remarks = m_remarks.group(1).replace("\n", " ").strip() if m_remarks else ""

            office = product_id.split("-")[1] if "-" in product_id else "NWS"
            
            # Dedup check for Tornadoes (Discord side)
            if "TORNADO" in event_type.upper():
                # Approx timestamp for LSR
                lsr_ts = datetime.now(timezone.utc).timestamp()
                if product_id and len(product_id) >= 12:
                    try:
                        lsr_ts = datetime.strptime(product_id[:12], "%Y%m%d%H%M").replace(tzinfo=timezone.utc).timestamp()
                    except Exception as e:
                        logger.warning(f"Failed to parse LSR timestamp '{product_id}': {e}")
                
                from utils.state_store import find_matching_tornado
                match_id = await find_matching_tornado(office, lsr_ts, city, window_hours=1.0)
                if match_id:
                    logger.info(f"[REPORTS] Skipping Discord post for Tornado LSR {product_id}, matches {match_id}")
                    continue

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
            
            # --- Persist State Before Sending ---
            self.bot.state.posted_reports.add(product_id)
            await add_posted_report(product_id)
            await prune_posted_reports()

            # Log significant events to DB
            event_upper = event_type.upper()
            if "TORNADO" in event_upper:
                await add_significant_event(
                    event_id=f"IEM:LSR:{product_id}",
                    event_type="Tornado",
                    location=city,
                    magnitude="Confirmed",
                    coords=coords,
                    timestamp=lsr_ts,
                    source=office,
                    raw_text=remarks,
                )
            elif "HAIL" in event_upper:
                m_mag = re.search(r"[ME]([\d.]+)", r)
                if m_mag:
                    try:
                        size = float(m_mag.group(1))
                        if size >= 3.0:
                            await add_significant_event(
                                event_id=f"IEM:LSR:{product_id}:hail",
                                event_type="Hail",
                                location=city,
                                magnitude=f"{size:.2f} Inch",
                                coords=coords,
                                timestamp=lsr_ts,
                                source=office,
                                raw_text=remarks,
                            )
                    except ValueError:
                        pass
            elif "WND" in event_upper or "WIND" in event_upper:
                m_mag = re.search(r"[ME]([\d.]+)", r)
                if m_mag:
                    try:
                        speed = float(m_mag.group(1))
                        if speed >= 80:
                            await add_significant_event(
                                event_id=f"IEM:LSR:{product_id}:wind",
                                event_type="Wind",
                                location=city,
                                magnitude=f"{speed} MPH",
                                coords=coords,
                                timestamp=lsr_ts,
                                source=office,
                                raw_text=remarks,
                            )
                    except ValueError:
                        pass

            await channel.send(embed=embed)

    async def _handle_pns(self, product_id: str, raw_text: str):
        # Public Information Statement - Damage Survey
        if "DAMAGE SURVEY" not in raw_text.upper():
            return
        
        channel = self.bot.get_channel(WARNINGS_CHANNEL_ID)
        if not channel:
            return

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
        summary = ""
        if m_summary:
            summary = m_summary.group(1).replace("\n", " ").strip()
            if len(summary) > 500:
                display_summary = summary[:497] + "..."
            else:
                display_summary = summary
            embed.add_field(name="Summary", value=display_summary, inline=False)

        embed.set_footer(text=f"{office} PNS | {product_id}")
        
        await channel.send(embed=embed)
        self.bot.state.posted_reports.add(product_id)
        await add_posted_report(product_id)
        await prune_posted_reports()

        # --- DB Logging & Matching ---
        # Try to find a date in the text to poll for Autoplot 253 tracks.
        # Patterns: MM/DD/YYYY or Month DD, YYYY
        event_date = None
        event_ts = datetime.now(timezone.utc).timestamp()
        
        # Numerical: 05/21/2024
        m_num = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", raw_text)
        if m_num:
            event_date = f"{m_num.group(3)}-{m_num.group(1).zfill(2)}-{m_num.group(2).zfill(2)}"
            try:
                event_ts = datetime.strptime(event_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
            except Exception as e:
                logger.warning(f"Failed to parse event date '{event_date}': {e}")
        else:
            # Narrative: MAY 21 2024
            months = "JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC"
            m_narr = re.search(rf"({months})\w*\s+(\d{{1,2}})\s*,\s*(\d{{4}})", raw_text, re.I)
            if m_narr:
                m_str = m_narr.group(1).upper()[:3]
                m_idx = (months.split("|").index(m_str) + 1)
                event_date = f"{m_narr.group(3)}-{str(m_idx).zfill(2)}-{m_narr.group(2).zfill(2)}"
                try:
                    event_ts = datetime.strptime(event_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
                except Exception as e:
                    logger.warning(f"Failed to parse event date '{event_date}': {e}")

        # Coords
        coords = ""
        m_latlon = re.search(r"START LAT/LON:\s*([\d\.\-]+)\s*/\s*([\d\.\-]+)", raw_text, re.I)
        if m_latlon:
            coords = f"{m_latlon.group(1)}N {abs(float(m_latlon.group(2)))}W"

        from utils.state_store import find_matching_tornado
        match_id = await find_matching_tornado(office, event_ts, event_name)
        
        if match_id:
            logger.info(f"[REPORTS] Found matching tornado {match_id} for survey, updating rating to {rating}")
            # Update existing row by using the same event_id
            await add_significant_event(
                event_id=match_id,
                event_type="Tornado",
                location=event_name,
                magnitude=rating,
                vtec_id=None, # Keep existing? add_significant_event in db.py doesn't update vtec_id on conflict yet
                coords=coords,
                timestamp=event_ts,
                source=office,
                raw_text=raw_text
            )
        else:
            # Log as a new survey event
            await add_significant_event(
                event_id=f"IEM:PNS:{product_id}",
                event_type="Tornado", # Log as Tornado so it shows in /recenttornadoes
                location=event_name,
                magnitude=rating,
                coords=coords,
                timestamp=event_ts,
                source=office,
                raw_text=raw_text
            )

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
            if not channel:
                return

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
                    title="🌪️ Tornado Track + Lead Time",
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

    @tasks.loop(minutes=5)
    async def poll_lsrs(self):
        """Poll IEM LSR GeoJSON for recent significant reports."""
        # Poll last 1 hour of reports
        url = "https://mesonet.agron.iastate.edu/geojson/lsr.geojson?hours=1"
        try:
            content, status = await http_get_bytes(url, retries=1, timeout=15)
            if not content or status != 200:
                return

            import json as _json
            data = _json.loads(content)
            for feature in data.get("features", []):
                props = feature.get("properties", {})
                pid = props.get("product_id")
                if not pid or pid in self.bot.state.posted_reports:
                    continue
                
                # Check significance
                is_sig = False
                typetext = props.get("typetext", "").upper()
                mag = props.get("magf", 0)
                
                if typetext == "TORNADO":
                    is_sig = True
                elif typetext == "HAIL" and mag >= 3.0:
                    is_sig = True
                elif "WIND" in typetext and mag >= 80:
                    is_sig = True
                
                if is_sig:
                    # We found a significant report not seen via iembot fast-path
                    # Log it
                    valid_str = props.get("valid") # 2026-04-28T11:08:00Z
                    ts = 0.0
                    if valid_str:
                        try:
                            dt = datetime.strptime(valid_str, "%Y-%m-%dT%H:%M:%SZ")
                            ts = dt.replace(tzinfo=timezone.utc).timestamp()
                        except Exception as e:
                            logger.warning(f"Failed to parse poll LSR timestamp '{valid_str}': {e}")

                    if ts == 0.0:
                        ts = datetime.now(timezone.utc).timestamp()

                    office = props.get("wfo")
                    location = f"{props.get('city')}, {props.get('state')}"
                    
                    if typetext == "TORNADO":
                        event_type = "Tornado"
                        magnitude = "Confirmed"
                        event_id = f"IEM:LSR:{pid}"
                    elif typetext == "HAIL":
                        event_type = "Hail"
                        magnitude = f"{mag:.2f} Inch"
                        event_id = f"IEM:LSR:{pid}:hail"
                    else:
                        event_type = "Wind"
                        magnitude = f"{int(mag)} MPH"
                        event_id = f"IEM:LSR:{pid}:wind"

                    # Dedup for tornadoes: if the iembot fast-path already logged
                    # this event, update that entry with the cleaner GeoJSON
                    # location ("City, ST") rather than skipping entirely.
                    if event_type == "Tornado":
                        from utils.state_store import find_matching_tornado  # noqa: PLC0415
                        match_id = await find_matching_tornado(office, ts, location, window_hours=1.0)
                        if match_id:
                            await add_significant_event(
                                event_id=match_id,
                                event_type="Tornado",
                                location=location,
                                magnitude=magnitude,
                                coords=f"{props.get('lat')}N {abs(props.get('lon'))}W",
                                timestamp=ts,
                                source=office,
                                raw_text=props.get("remark"),
                            )
                            logger.debug(f"[REPORTS] Updated location for {match_id} → {location!r}")
                            self.bot.state.posted_reports.add(pid)
                            await add_posted_report(pid)
                            continue

                    await add_significant_event(
                        event_id=event_id,
                        event_type=event_type,
                        location=location,
                        magnitude=magnitude,
                        coords=f"{props.get('lat')}N {abs(props.get('lon'))}W",
                        timestamp=ts,
                        source=office,
                        raw_text=props.get("remark"),
                    )
                    # Persist dedup
                    self.bot.state.posted_reports.add(pid)
                    await add_posted_report(pid)
                    await prune_posted_reports()

        except Exception as e:
            logger.warning(f"[REPORTS] LSR poll failed: {e}")

    @poll_lsrs.before_loop
    async def before_poll_lsrs(self):
        await self.bot.wait_until_ready()

async def setup(bot: commands.Bot):
    await bot.add_cog(ReportsCog(bot))
