# cogs/reports.py
import asyncio
import logging
import os
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

class PNSView(discord.ui.View):
    def __init__(self, raw_text: str):
        super().__init__(timeout=86400) # Long timeout for persistent posts
        self.raw_text = raw_text

    @discord.ui.button(label="📜 View Full Text", style=discord.ButtonStyle.secondary)
    async def view_text(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Split text if it exceeds 2000 chars (unlikely for most, but just in case)
        if len(self.raw_text) > 1950:
            parts = [self.raw_text[i:i+1950] for i in range(0, len(self.raw_text), 1950)]
            for i, p in enumerate(parts):
                await interaction.response.send_message(f"```\n{p}\n```", ephemeral=True) if i == 0 else await interaction.followup.send(f"```\n{p}\n```", ephemeral=True)
        else:
            await interaction.response.send_message(f"```\n{self.raw_text}\n```", ephemeral=True)

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
            
            # Match Line 1: 0131 AM     TSTM WND DMG     DICKSON                 36.03N 87.39W
            m_l1 = re.search(r"^(\d{4}\s+[AP]M)\s+(.{16})\s+(.{24})\s+(\d+\.\d+N\s+\d+\.\d+W)", r, re.M)
            if not m_l1:
                continue

            time_str, event_type, location, coords = m_l1.groups()
            event_type = event_type.strip()
            location = location.strip()

            # Match Line 2: 04/29/2026                   DICKSON            TN   Public
            # We need to find the line starting with a date after the header
            m_l2 = re.search(r"^(\d{2}/\d{2}/\d{4})\s+(.{24})\s+([A-Z]{2})\s+(.*)$", r, re.M)
            county = ""
            state = ""
            source = "NWS"
            if m_l2:
                _, county, state, source = m_l2.groups()
                county = county.strip()
                state = state.strip()
                source = source.strip()

            # Remarks
            m_remarks = re.search(r"REMARKS\s*\.\.\.\s*(.*?)(?=\n\s*\n|\$\$|$)", r, re.I | re.DOTALL)
            remarks = m_remarks.group(1).replace("\n", " ").strip() if m_remarks else ""

            # Specialized logic for automated stations (ASOS, AWOS, MTR)
            source_upper = source.upper()
            is_automated = any(x in source_upper for x in ("ASOS", "AWOS", "MTR"))
            
            peak_wind_summary = ""
            if is_automated:
                # Extract Peak Wind: PK WND 27045/2220 -> 45kt
                m_pk = re.search(r"PK WND\s+\d{3}(\d{2,3})/?(\d{4})?", remarks, re.I)
                if m_pk:
                    gust_kt = m_pk.group(1).lstrip("0")
                    time_pk = m_pk.group(2)
                    peak_wind_summary = f"Peak wind {gust_kt}kt"
                    if time_pk:
                        peak_wind_summary += f" at {time_pk[:2]}:{time_pk[2:]}Z"
                    peak_wind_summary += ". "

            office = product_id.split("-")[1] if "-" in product_id else "NWS"
            lsr_url = f"https://mesonet.agron.iastate.edu/p.php?pid={product_id}"
            
            # Approx timestamp for LSR
            lsr_ts = datetime.now(timezone.utc).timestamp()
            if product_id and len(product_id) >= 12:
                try:
                    lsr_ts = datetime.strptime(product_id[:12], "%Y%m%d%H%M").replace(tzinfo=timezone.utc).timestamp()
                except Exception:
                    logger.debug("LSR timestamp parse failed for product_id %r, falling back to now()", product_id)
            
            # Dedup check for Tornadoes (Discord side)
            if "TORNADO" in event_type.upper():
                from utils.state_store import find_matching_tornado
                from utils.db import get_posted_warning_timestamp
                match = await find_matching_tornado(office, lsr_ts, location, window_hours=1.0)
                if match:
                    event_id, vtec_id = match
                    logger.info(f"[REPORTS] Skipping Discord post for Tornado LSR {product_id}, matches {event_id}")
                    
                    # Calculate Lead Time if we have a vtec_id
                    if vtec_id:
                        warn_ts = await get_posted_warning_timestamp(vtec_id)
                        if warn_ts:
                            lead_time = (lsr_ts - warn_ts) / 60.0
                            logger.info(f"[REPORTS] Calculated lead time for {event_id}: {lead_time:.1f} min")
                            # Update existing event with lead time
                            await add_significant_event(
                                event_id=event_id,
                                event_type="Tornado",
                                location=location,
                                lead_time=lead_time
                            )
                    continue

            color = discord.Color.blue()
            if "TORNADO" in event_type.upper():
                color = discord.Color.red()
            elif "WND" in event_type.upper() or "WIND" in event_type.upper():
                color = discord.Color.gold()
            elif "HAIL" in event_type.upper():
                color = discord.Color.light_grey()
            elif "FLOOD" in event_type.upper():
                color = discord.Color.dark_blue()

            # Format: {location} [{County, STATE}] {source} [reports {event}](url) at {time} -- {remarks}
            source_display = f"({source})" if is_automated else source
            area_frag = f"{location} [{county}, {state}]" if state else f"{location}"
            desc = (
                f"{area_frag} {source_display} [reports {event_type}]({lsr_url}) at {time_str} -- "
                f"{peak_wind_summary}{remarks if remarks else 'No additional remarks.'}\n"
                f"[<t:{int(lsr_ts)}:R>]"
            )

            embed = discord.Embed(
                description=desc,
                color=color,
                timestamp=datetime.now(timezone.utc)
            )
            embed.set_footer(text=f"{office} LSR | {coords}")
            
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
                    location=location,
                    magnitude="Confirmed",
                    coords=coords,
                    timestamp=lsr_ts,
                    source=office,
                    raw_text=remarks,
                )

            await channel.send(embed=embed)

    async def _handle_pns(self, product_id: str, raw_text: str):
        # Public Information Statement - Damage Survey
        if "DAMAGE SURVEY" not in raw_text.upper():
            return
        
        channel = self.bot.get_channel(WARNINGS_CHANNEL_ID)
        if not channel:
            return

        # 1. Strip boilerplate (EF Scale key) to avoid false EF5 matches
        search_text = raw_text
        for stop_marker in ("&&", "EF SCALE:", "THE ENHANCED FUJITA SCALE"):
            m_stop = re.search(re.escape(stop_marker), raw_text, re.I)
            if m_stop:
                search_text = raw_text[:m_stop.start()]
                break

        # 2. Extract All Ratings and find the Max
        # Strictly match "Rating: EF{N}" to avoid legend matches
        ratings = re.findall(r"Rating:\s*EF(\d|U)", search_text, re.I)
        ef_nums = []
        for r in ratings:
            if r.upper() == "U":
                ef_nums.append(-1) # Unknown
            else:
                ef_nums.append(int(r))
        
        max_ef = max(ef_nums) if ef_nums else None
        rating_str = f"EF{max_ef}" if max_ef is not None and max_ef >= 0 else ("EFU" if max_ef == -1 else "N/A")
        
        # 3. Location/Event Name
        m_event = re.search(r"\.\.\.(.*?)\.\.\.", raw_text)
        event_name = m_event.group(1).strip() if m_event else "NWS Damage Survey"

        office = product_id.split("-")[1] if "-" in product_id else "NWS"
        pns_url = f"https://mesonet.agron.iastate.edu/p.php?pid={product_id}"
        
        # 4. Count total events in this product
        total_tors = len(ef_nums)
        tor_count_msg = f" ({total_tors} tornadoes)" if total_tors > 1 else ""

        # Snippet of the summary if available
        m_summary = re.search(r"SUMMARY:\s*(.*?)(?=\n\s*\n|\$\$|$)", search_text, re.I | re.DOTALL)
        summary_snippet = ""
        if m_summary:
            summary_snippet = m_summary.group(1).replace("\n", " ").strip()
            if len(summary_snippet) > 150:
                summary_snippet = summary_snippet[:147] + "..."

        # Time extraction for PNS
        pns_ts = datetime.now(timezone.utc).timestamp()
        pns_time_str = "Unknown Time"
        m_time = re.search(r"(\d{1,2}:\d{2}\s+[AP]M\s+[A-Z]{3}.*?202\d)", raw_text, re.I)
        if m_time:
            pns_time_str = m_time.group(1).strip()
        elif product_id and len(product_id) >= 12:
            try:
                dt = datetime.strptime(product_id[:12], "%Y%m%d%H%M")
                pns_ts = dt.replace(tzinfo=timezone.utc).timestamp()
                pns_time_str = dt.strftime("%b %d, %I:%M %p")
            except Exception:
                logger.debug("PNS timestamp parse failed for product_id %r", product_id)

        desc = (
            f"{office} issues [Damage Survey PNS]({pns_url}) (Max: {rating_str}){tor_count_msg} at {pns_time_str}\n"
            f"> {summary_snippet if summary_snippet else 'Multi-event damage survey summary.'}\n"
            f"[<t:{int(pns_ts)}:R>]"
        )

        view = PNSView(raw_text)
        embed = discord.Embed(
            description=desc,
            color=discord.Color.teal(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text=f"{office} PNS | {product_id}")
        
        await channel.send(embed=embed, view=view)
        
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
        match = await find_matching_tornado(office, event_ts, event_name)
        
        # Only log to significant events if it's a Tornado survey
        # (check event_name and rating)
        is_tornado = "TORNADO" in event_name.upper() or rating_str.startswith("EF")
        
        if is_tornado:
            if match:
                event_id, vtec_id = match
                logger.info(f"[REPORTS] Found matching tornado {event_id} for survey, updating rating to {rating_str}")
                # Update existing row by using the same event_id
                await add_significant_event(
                    event_id=event_id,
                    event_type="Tornado",
                    location=event_name,
                    magnitude=rating_str,
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
                    magnitude=rating_str,
                    coords=coords,
                    timestamp=event_ts,
                    source=office,
                    raw_text=raw_text
                )
        else:
            logger.info(f"[REPORTS] Skipping SignificantEvent log for non-tornado PNS: {event_name}")

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

                # --- Fallback Logic: Try IEM first, fall back to local render ---
                file_to_send = None
                source_text = "IEM Autoplot 253"
                
                # Check if IEM image is available (timeout quickly to avoid lag)
                _, status = await http_get_bytes(img_url, retries=0, timeout=5)
                if status != 200:
                    logger.info(f"[REPORTS] IEM map not ready for {guid}, attempting local render")
                    from utils.dat_api import fetch_dat_track_geometry  # noqa: PLC0415
                    from utils.map_utils import render_tornado_track  # noqa: PLC0415
                    
                    paths = await fetch_dat_track_geometry(guid)
                    if paths:
                        out_path = os.path.join("cache", f"track_{guid}.png")
                        render_tornado_track(paths, out_path)
                        if os.path.exists(out_path):
                            file_to_send = discord.File(out_path, filename=f"track_{guid}.png")
                            source_text = "Local DAT Render"

                embed = discord.Embed(
                    title="🌪️ Tornado Track + Lead Time",
                    description=f"**Event:** {label}\n**Date:** {event_date}",
                    color=discord.Color.red(),
                    url=f"https://mesonet.agron.iastate.edu/plotting/auto/?q=253&dat={event_date.replace('-', '/')}&datglobalid={guid}"
                )
                
                if file_to_send:
                    embed.set_image(url=f"attachment://{file_to_send.filename}")
                else:
                    embed.set_image(url=img_url)
                    
                embed.set_footer(text=f"{source_text} | {guid}")
                
                await channel.send(embed=embed, file=file_to_send)
                self.posted_surveys.add(guid)
                await add_posted_survey(guid)
                await prune_posted_surveys()
                logger.info(f"[REPORTS] Posted survey map for {guid} ({label}) via {source_text}")
                
                from utils.events_db import link_dat_guid_to_tornado
                await link_dat_guid_to_tornado(event_date, guid, label)

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
                
                if typetext == "TORNADO":
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

                        # Dedup for tornadoes: if the iembot fast-path already logged
                        # this event, update that entry with the cleaner GeoJSON
                        # location ("City, ST") rather than skipping entirely.
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
