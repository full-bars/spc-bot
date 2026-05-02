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
from logging.handlers import RotatingFileHandler
from typing import Optional

from discord.ext import commands, tasks
from slixmpp import ClientXMPP, Message
from slixmpp.exceptions import IqError, IqTimeout
from slixmpp.xmlstream import ElementBase, register_stanza_plugin

from config import NWWS_USER, NWWS_PASSWORD, NWWS_SERVER, NWWS_FIREHOSE_LOG

logger = logging.getLogger("spc_bot")

# --- Secondary Firehose Logger ---
# This logger writes EVERYTHING from NWWS to a separate file (capped at 10MB)
# so the main log stays quiet.
firehose_logger = logging.getLogger("nwws_firehose")
firehose_logger.setLevel(logging.INFO)
# Disable propagation so it doesn't leak into spc_bot.log
firehose_logger.propagate = False

# Only add the handler once
if not firehose_logger.handlers:
    fh = RotatingFileHandler(
        NWWS_FIREHOSE_LOG, 
        maxBytes=10*1024*1024, # 10MB
        backupCount=1
    )
    fh.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
    firehose_logger.addHandler(fh)

# --- NWWS-OI Custom XML Payload ---
# Specification: <x xmlns='nwws-oi' cccc='...' ttaaii='...' issue='...' awipsid='...' id='...' />
class NWWSPayload(ElementBase):
    name = 'x'
    namespace = 'nwws-oi'
    plugin_attrib = 'nwws'
    interfaces = {'cccc', 'ttaaii', 'issue', 'awipsid', 'id'}

class NWWSClient(ClientXMPP):
    def __init__(self, jid, password, bot):
        # The documentation says Resource should be 'nwws'
        full_jid = f"{jid}/nwws"
        super().__init__(full_jid, password)
        self.bot = bot
        self.is_connected = False
        # Room is 'nwws' on the conference server
        self.room = f"nwws@conference.{NWWS_SERVER}"
        self.nick = jid.split('@')[0]
        
        # Register the MUC plugin and our custom payload
        self.register_plugin('xep_0045') # Multi-User Chat
        register_stanza_plugin(Message, NWWSPayload)

        self.add_event_handler("session_start", self.session_start)
        self.add_event_handler("message", self.message)
        self.add_event_handler("disconnected", self.on_disconnect)
        
        # Enable auto-reconnect at the slixmpp level
        self.reconnect = True
        self.use_ipv6 = False

    async def session_start(self, event):
        self.is_connected = True
        self.send_presence()
        try:
            await self.get_roster()
        except (IqError, IqTimeout):
            logger.error("[NWWS] Error fetching roster")
        
        # Join the NWWS-OI Multi-User Chat
        logger.info(f"[NWWS] Joining room {self.room} as {self.nick}...")
        self.plugin['xep_0045'].join_muc(self.room, self.nick)
        logger.info(f"[NWWS] XMPP Session Started as {self.boundjid}")

    def on_disconnect(self, event):
        self.is_connected = False
        logger.warning("[NWWS] XMPP Disconnected")

    def message(self, msg):
        # The specification says weather products arrive as 'groupchat' messages
        # from the room.
        msg_type = msg['type']
        
        # Extract the custom NWWS-OI payload
        payload = msg['nwws']
        raw_text = payload.xml.text.strip() if payload.xml is not None and payload.xml.text else ""
        
        # If no custom payload, check body (for MOTD or other messages)
        body = msg['body']

        # --- VERBOSE LOGGING: Directed to nwws_firehose.log ---
        if payload['awipsid'] or raw_text:
            # Metadata log
            firehose_logger.info(
                f"[{msg_type.upper()}] from {msg['from']} | "
                f"cccc: {payload['cccc']}, ttaaii: {payload['ttaaii']}, awipsid: {payload['awipsid']}"
            )
            # Full text log (no truncation, rotating file handles size)
            text_clean = raw_text.replace('\r', '')
            firehose_logger.info(f"RAW TEXT:\n{text_clean}\n" + "-"*40)
        elif body and "**WARNING**" in body:
            firehose_logger.info(f"MOTD: {body[:100]}...")
        # ---------------------

        if not raw_text or not payload['awipsid']:
            return

        # Route to processing
        asyncio.create_task(self._process_nwws_message(payload, raw_text))

    async def _process_nwws_message(self, payload: NWWSPayload, raw_text: str):
        """Parse raw text product and route to appropriate cogs."""
        try:
            afos_pil = payload['awipsid']
            office = payload['cccc']
            ttaaii = payload['ttaaii']
            
            # Construct a product_id matching the iembot format where possible
            ts_str = time.strftime("%Y%m%d%H%M", time.gmtime())
            product_id = f"{ts_str}-{office}-{ttaaii}-{afos_pil}"

            # 2. Routing Logic
            
            # WATCHES (SEL products)
            if "SEL" in afos_pil:
                m = re.search(r"(?:Tornado|Severe Thunderstorm)\s+Watch\s+Number\s+(\d+)", raw_text, re.IGNORECASE)
                if m:
                    watch_num = m.group(1).zfill(4)
                    watches_cog = self.bot.get_cog("WatchesCog")
                    if watches_cog:
                        from cogs.iembot import _parse_watch_text
                        text = _parse_watch_text(raw_text)
                        if text:
                            from utils.state_store import set_product_cache
                            await set_product_cache(f"watch_{watch_num}", text, ttl=600)
                        
                        wtype = "TORNADO" if "Tornado Watch" in raw_text else "SVR"
                        await watches_cog.post_watch_now(watch_num, {"type": wtype, "expires": None, "affected_zones": []})
                        logger.info(f"[NWWS] Triggered Watch {watch_num} via XMPP")

            # MDs (SWOMCD)
            elif "SWOMCD" in afos_pil:
                m = re.search(r"Mesoscale Discussion\s+(\d+)", raw_text, re.IGNORECASE)
                if m:
                    md_num = m.group(1).zfill(4)
                    mesoscale_cog = self.bot.get_cog("MesoscaleCog")
                    if mesoscale_cog:
                        from utils.state_store import set_product_cache
                        await set_product_cache(f"md_{md_num}", raw_text, ttl=600)
                        await mesoscale_cog.post_md_now(md_num)
                        logger.info(f"[NWWS] Triggered MD {md_num} via XMPP")

            # WARNINGS (TOR, SVR, FFW, etc)
            elif any(afos_pil.startswith(x) for x in ("TOR", "SVR", "FFW", "SVS", "FFS", "SPS")):
                warnings_cog = self.bot.get_cog("WarningsCog")
                if warnings_cog:
                    # Clean the raw text for WarningsCog (strip the leading sequence number if present)
                    # Heuristic: find the line starting with the WMO header (ttaaii)
                    cleaned_text = raw_text
                    lines = raw_text.splitlines()
                    for i, line in enumerate(lines):
                        if ttaaii in line:
                            cleaned_text = "\n".join(lines[i:])
                            break

                    event_map = {
                        "TOR": "Tornado Warning",
                        "SVR": "Severe Thunderstorm Warning",
                        "FFW": "Flash Flood Warning",
                        "SVS": "Severe Weather Statement",
                        "FFS": "Flash Flood Statement",
                        "SPS": "Special Weather Statement"
                    }
                    pil_prefix = next((p for p in event_map if afos_pil.startswith(p)), None)
                    if pil_prefix:
                        await warnings_cog.post_warning_now(product_id, cleaned_text, event_map[pil_prefix])
                        logger.info(f"[NWWS] Triggered {pil_prefix} Warning via XMPP")

            # REPORTS (LSR, PNS)
            elif any(afos_pil.startswith(x) for x in ("LSR", "PNS")):
                reports_cog = self.bot.get_cog("ReportsCog")
                if reports_cog:
                    pil_prefix = "LSR" if afos_pil.startswith("LSR") else "PNS"
                    await reports_cog.post_report_now(product_id, raw_text, pil_prefix)
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

    async def trigger_connection(self):
        """Immediately attempt to connect to NWWS-OI (called by FailoverCog)."""
        if not self._should_be_connected:
            self._should_be_connected = True
            if not self.monitor_connection.is_running():
                self.monitor_connection.start()
        
        await self.monitor_connection()

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
            
            if self.xmpp_client.transport is not None:
                # Still in flight
                return

            # Clean up before retrying.
            self.xmpp_client.disconnect()
            self.xmpp_client = None

        logger.info(f"[NWWS] Connecting to {NWWS_SERVER}...")
        jid = f"{NWWS_USER}@{NWWS_SERVER}"
        self.xmpp_client = NWWSClient(jid, NWWS_PASSWORD, self.bot)
        
        try:
            self.xmpp_client.connect(address=(NWWS_SERVER, 5222))
        except Exception as e:
            logger.error(f"[NWWS] Connection attempt failed: {e}")
            self.xmpp_client = None

    @monitor_connection.before_loop
    async def before_monitor(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(NWWSCog(bot))
