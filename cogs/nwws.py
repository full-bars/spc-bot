# cogs/nwws.py
"""
NWWS-OI XMPP Cog — The bot's primary high-speed "firehose".

Maintains a persistent connection to the NOAA Weather Wire Service (NWWS-OI)
via XMPP. Receives text products directly from NWS/NOAA and triggers
immediate posts in relevant cogs (Warnings, Watches, Mesoscale).

Authority Sequence:
1. NWWS (Primary Fast-Path)
2. IEMBot (Secondary Fast-Path)
3. API Polling (Tertiary Safety Net)
"""

import asyncio
import logging
import re
import time
from typing import Optional

from discord.ext import commands, tasks
from slixmpp import ClientXMPP
from slixmpp.exceptions import IqError, IqTimeout

from config import NWWS_USER, NWWS_PASSWORD, NWWS_SERVER

logger = logging.getLogger("spc_bot")

# --- Product ID Parsing ---
# NWWS message content usually contains the AFOS PIL in the first few lines.
# We also use the XMPP message metadata where possible.

class NWWSClient(ClientXMPP):
    def __init__(self, jid, password, bot):
        super().__init__(jid, password)
        self.bot = bot
        self.is_connected = False
        self.add_event_handler("session_start", self.session_start)
        self.add_event_handler("message", self.message)
        self.add_event_handler("disconnected", self.on_disconnect)
        
        # Auto-reconnect is handled by slixmpp by default, but we'll monitor it.

    async def session_start(self, event):
        self.is_connected = True
        self.send_presence()
        try:
            await self.get_roster()
        except (IqError, IqTimeout):
            logger.error("[NWWS] Error fetching roster")
        logger.info(f"[NWWS] XMPP Session Started as {self.boundjid}")

    def on_disconnect(self, event):
        self.is_connected = False
        logger.warning("[NWWS] XMPP Disconnected")

    def message(self, msg):
        if msg['type'] in ('chat', 'normal'):
            body = msg['body']
            if not body:
                return
            
            # NWWS-OI specific: product info often embedded in custom XML tags
            # or in the first line of the body.
            # Example first line: "TORNADO WARNING...NWS OKLAHOMA CITY OK"
            # Or AFOS PIL: "TORENC"
            
            # We want to run the processing off the XMPP event loop
            asyncio.create_task(self._process_nwws_message(body))

    async def _process_nwws_message(self, body: str):
        """Parse raw text product and route to appropriate cogs."""
        try:
            # 1. Extract WMO Header / AFOS PIL
            # Most NWWS messages start with the WMO header (e.g. WFUS54 KOUN 011234)
            # followed by the AFOS PIL (e.g. TOROUN)
            lines = [line.strip() for line in body.splitlines() if line.strip()]
            if not lines:
                return

            # Basic heuristic for a text product
            if len(lines) < 3:
                return

            wmo_header = lines[0]
            afos_pil = lines[1] if len(lines) > 1 else ""
            
            # Construct a product_id similar to what iembot/reports use
            # YYYYMMDDHHMM-OFFICE-WMO-AFOSPIL
            ts_str = time.strftime("%Y%m%d%H%M", time.gmtime())
            office = wmo_header.split()[1] if len(wmo_header.split()) > 1 else "NWS"
            product_id = f"{ts_str}-{office}-{wmo_header.replace(' ', '')}-{afos_pil}"

            # 2. Routing Logic
            # Matches the patterns in iembot.py
            
            # WATCHES (SEL products)
            if "SEL" in afos_pil:
                m = re.search(r"(?:Tornado|Severe Thunderstorm)\s+Watch\s+Number\s+(\d+)", body, re.IGNORECASE)
                if m:
                    watch_num = m.group(1).zfill(4)
                    watches_cog = self.bot.get_cog("WatchesCog")
                    if watches_cog:
                        # IEMBot handles the parsing/caching; we just trigger the post.
                        # We might need to cache the text here too for authority.
                        from cogs.iembot import _parse_watch_text
                        text = _parse_watch_text(body)
                        if text:
                            from utils.state_store import set_product_cache
                            await set_product_cache(f"watch_{watch_num}", text, ttl=600)
                        
                        wtype = "TORNADO" if "Tornado Watch" in body else "SVR"
                        await watches_cog.post_watch_now(watch_num, {"type": wtype, "expires": None, "affected_zones": []})
                        logger.info(f"[NWWS] Triggered Watch {watch_num} via XMPP")

            # MDs (SWOMCD)
            elif "SWOMCD" in afos_pil:
                m = re.search(r"Mesoscale Discussion\s+(\d+)", body, re.IGNORECASE)
                if m:
                    md_num = m.group(1).zfill(4)
                    mesoscale_cog = self.bot.get_cog("MesoscaleCog")
                    if mesoscale_cog:
                        from utils.state_store import set_product_cache
                        await set_product_cache(f"md_{md_num}", body, ttl=600)
                        await mesoscale_cog.post_md_now(md_num)
                        logger.info(f"[NWWS] Triggered MD {md_num} via XMPP")

            # WARNINGS (TOR, SVR, FFW, etc)
            elif any(x in afos_pil for x in ("TOR", "SVR", "FFW", "SVS", "FFS", "SPS")):
                warnings_cog = self.bot.get_cog("WarningsCog")
                if warnings_cog:
                    # Map PIL to event type
                    event_map = {
                        "TOR": "Tornado Warning",
                        "SVR": "Severe Thunderstorm Warning",
                        "FFW": "Flash Flood Warning",
                        "SVS": "Severe Weather Statement",
                        "FFS": "Flash Flood Statement",
                        "SPS": "Special Weather Statement"
                    }
                    pil_prefix = next((p for p in event_map if p in afos_pil), None)
                    if pil_prefix:
                        await warnings_cog.post_warning_now(product_id, body, event_map[pil_prefix])
                        logger.info(f"[NWWS] Triggered {pil_prefix} Warning via XMPP")

            # REPORTS (LSR, PNS)
            elif any(x in afos_pil for x in ("LSR", "PNS")):
                reports_cog = self.bot.get_cog("ReportsCog")
                if reports_cog:
                    pil_prefix = "LSR" if "LSR" in afos_pil else "PNS"
                    await reports_cog.post_report_now(product_id, body, pil_prefix)
                    logger.info(f"[NWWS] Triggered {pil_prefix} via XMPP")

        except Exception as e:
            logger.exception(f"[NWWS] Error processing XMPP message: {e}")

class NWWSCog(commands.Cog):
    MANAGED_TASK_NAMES = [("monitor_connection", "nwws_connection")]

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.xmpp_client: Optional[NWWSClient] = None
        self._should_be_connected = False

    async def cog_load(self):
        if not all([NWWS_USER, NWWS_PASSWORD]):
            logger.warning("[NWWS] Credentials missing, NWWS cog disabled")
            return
        
        self._should_be_connected = True
        self.monitor_connection.start()

    def cog_unload(self):
        self._should_be_connected = False
        self.monitor_connection.cancel()
        if self.xmpp_client:
            self.xmpp_client.disconnect()

    @tasks.loop(seconds=30)
    async def monitor_connection(self):
        """Maintain persistent connection to NWWS-OI."""
        if not self.bot.state.is_primary or not self._should_be_connected:
            if self.xmpp_client and self.xmpp_client.is_connected:
                logger.info("[NWWS] Node is Standby — disconnecting NWWS")
                self.xmpp_client.disconnect()
            return

        # Check existing client state
        if self.xmpp_client is not None:
            if self.xmpp_client.is_connected:
                return
            
            # If we're here, we aren't connected yet. 
            # slixmpp handles auto-reconnect, but if it takes too long
            # or the transport is dead, we'll recreate.
            if self.xmpp_client.transport is not None:
                # Still in flight
                return

            # Clean up before retrying.
            self.xmpp_client.disconnect()
            self.xmpp_client = None

        logger.info(f"[NWWS] Connecting to {NWWS_SERVER}...")
        # NWWS-OI requires a plain JID: username@nwws-oi.weather.gov
        jid = f"{NWWS_USER}@{NWWS_SERVER}"
        self.xmpp_client = NWWSClient(jid, NWWS_PASSWORD, self.bot)
        
        # NWWS-OI specific connectivity tweaks
        self.xmpp_client.use_ipv6 = False
        # Enable auto-reconnect at the slixmpp level
        self.xmpp_client.reconnect = True

        try:
            # connect() is an async-safe call that registers tasks on the current loop.
            # We do NOT call process() as discord.py is already running the loop.
            self.xmpp_client.connect(address=(NWWS_SERVER, 5222))
        except Exception as e:
            logger.error(f"[NWWS] Connection attempt failed: {e}")
            self.xmpp_client = None

    @monitor_connection.before_loop
    async def before_monitor(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(NWWSCog(bot))
