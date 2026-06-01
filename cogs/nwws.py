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

from config import NWWS_FIREHOSE_LOG, NWWS_PASSWORD, NWWS_SERVER, NWWS_USER

logger = logging.getLogger("spc_bot")

# Rust core fallback
try:
    import spc_rust_core

    _normalize_product_id_rust = spc_rust_core.normalize_product_id
    _parse_md_number_rust = spc_rust_core.parse_md_number
    _parse_watch_number_rust = spc_rust_core.parse_watch_number
except (ImportError, AttributeError):
    _normalize_product_id_rust = None
    _parse_md_number_rust = None
    _parse_watch_number_rust = None

# Feature flag: use Rust NWWS backend if available
_USE_RUST_NWWS = False
try:
    import spc_rust_core

    if hasattr(spc_rust_core, "nwws_start"):
        _USE_RUST_NWWS = True
        logger.info("NWWS Rust backend enabled")
except (ImportError, AttributeError):
    logger.debug("NWWS Rust backend not available, will use slixmpp")


def normalize_product_id_py(office: str, ttaaii: str, afos_pil: str, issue_str: str) -> str:
    """Python fallback: normalize product ID for deduplication."""
    ts_str = issue_str
    # Normalize ISO8601 format to compact format for dedup consistency
    if "T" in ts_str and "Z" in ts_str:
        # Convert "2026-05-03T06:50:00Z" → "202605030650"
        ts_str = ts_str.replace("-", "").replace("T", "").replace(":", "").split("Z")[0][:12]
    else:
        # Truncate to 12 characters (YYYYMMDDHHMM format)
        ts_str = ts_str[:12]
    return f"{ts_str}-{office}-{ttaaii}-{afos_pil}"


def normalize_product_id(office: str, ttaaii: str, afos_pil: str, issue_str: str) -> str:
    """Normalize product ID; try Rust first, fall back to Python."""
    if _normalize_product_id_rust:
        try:
            return _normalize_product_id_rust(office, ttaaii, afos_pil, issue_str)
        except Exception:
            pass
    return normalize_product_id_py(office, ttaaii, afos_pil, issue_str)


def parse_md_number_py(text: str) -> Optional[str]:
    """Python fallback: extract Mesoscale Discussion number."""
    m = re.search(r"Mesoscale Discussion\s+(\d+)", text, re.IGNORECASE)
    if m:
        return m.group(1).zfill(4)
    return None


def parse_md_number(text: str) -> Optional[str]:
    """Extract Mesoscale Discussion number; try Rust first, fall back to Python."""
    if _parse_md_number_rust:
        try:
            return _parse_md_number_rust(text)
        except Exception:
            pass
    return parse_md_number_py(text)


def parse_watch_number_py(text: str) -> Optional[tuple]:
    """Python fallback: extract watch number and type (TORNADO or SVR)."""
    m = re.search(r"(?:Tornado|Severe Thunderstorm)\s+Watch\s+Number\s+(\d+)", text, re.IGNORECASE)
    if m:
        watch_num = m.group(1).zfill(4)
        wtype = "TORNADO" if "Tornado Watch" in text else "SVR"
        return (watch_num, wtype)
    return None


def parse_watch_number(text: str) -> Optional[tuple]:
    """Extract watch number and type; try Rust first, fall back to Python."""
    if _parse_watch_number_rust:
        try:
            result = _parse_watch_number_rust(text)
            if result:
                return tuple(result)
        except Exception:
            pass
    return parse_watch_number_py(text)


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
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=1,
    )
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    firehose_logger.addHandler(fh)


# --- NWWS-OI Custom XML Payload ---
# Specification: <x xmlns='nwws-oi' cccc='...' ttaaii='...' issue='...' awipsid='...' id='...' />
class NWWSPayload(ElementBase):
    name = "x"
    namespace = "nwws-oi"
    plugin_attrib = "nwws"
    interfaces = {"cccc", "ttaaii", "issue", "awipsid", "id"}


class NWWSClient(ClientXMPP):
    def __init__(self, jid, password, bot):
        # The documentation says Resource should be 'nwws'
        full_jid = f"{jid}/nwws"
        super().__init__(full_jid, password)
        self.bot = bot
        self.is_connected = False
        # Room is 'nwws' on the conference server
        self.room = f"nwws@conference.{NWWS_SERVER}"
        self.nick = jid.split("@")[0]

        # Register the MUC plugin and our custom payload
        self.register_plugin("xep_0045")  # Multi-User Chat
        self.register_plugin("xep_0199")  # XMPP Ping
        self.register_plugin("xep_0203")  # Delayed Delivery — needed to detect archived backlog
        register_stanza_plugin(Message, NWWSPayload)

        self.add_event_handler("session_start", self.session_start)
        self.add_event_handler("message", self.message)
        self.add_event_handler("disconnected", self.on_disconnect)

        # Start ping task on session start
        self.add_event_handler("session_start", self._start_ping_task)
        self._ping_task: Optional[asyncio.Task] = None

    def _start_ping_task(self, _):
        if self._ping_task is not None and not self._ping_task.done():
            self._ping_task.cancel()
        self._ping_task = self.bot.loop.create_task(self._ping_loop())

    async def _ping_loop(self):
        try:
            while self.is_connected:
                try:
                    start = time.perf_counter()
                    await self["xep_0199"].ping(self.boundjid.host, timeout=10)
                    latency_ms = (time.perf_counter() - start) * 1000
                    self.bot.state.nwws_ping = latency_ms
                except Exception:
                    self.bot.state.nwws_ping = None
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            logger.debug("NWWS ping loop cancelled")
            self.reconnect = True
            self.use_ipv6 = False

    def disconnect(self, reconnect=False, wait=False):
        if self._ping_task is not None:
            self._ping_task.cancel()
            self._ping_task = None
        super().disconnect(reconnect, wait)

    async def session_start(self, event):
        self.is_connected = True
        self.send_presence()
        try:
            await self.get_roster()
        except (IqError, IqTimeout):
            logger.error("Error fetching roster")

        # Join the NWWS-OI Multi-User Chat
        logger.info(f"Joining room {self.room} as {self.nick}...")
        self.plugin["xep_0045"].join_muc(self.room, self.nick)
        logger.info(f"XMPP Session Started as {self.boundjid}")

    def on_disconnect(self, event):
        self.is_connected = False
        logger.warning("XMPP Disconnected")

    def message(self, msg):
        # The specification says weather products arrive as 'groupchat' messages
        # from the room.
        msg_type = msg["type"]

        # Extract the custom NWWS-OI payload
        payload = msg["nwws"]
        raw_text = payload.xml.text.strip() if payload.xml is not None and payload.xml.text else ""

        # If no custom payload, check body (for MOTD or other messages)
        body = msg["body"]

        # --- VERBOSE LOGGING: Directed to nwws_firehose.log ---
        if payload["awipsid"] or raw_text:
            # Metadata log
            firehose_logger.info(
                f"[{msg_type.upper()}] from {msg['from']} | "
                f"cccc: {payload['cccc']}, ttaaii: {payload['ttaaii']}, awipsid: {payload['awipsid']}, issue: {payload['issue']}"
            )
            # Full text log (no truncation, rotating file handles size)
            text_clean = raw_text.replace("\r", "")
            firehose_logger.info(f"RAW TEXT:\n{text_clean}\n" + "-" * 40)
        elif body and "**WARNING**" in body:
            firehose_logger.info(f"MOTD: {body[:100]}...")
        # ---------------------

        if not raw_text or not payload["awipsid"]:
            return

        # Check for archived message (XEP-0203 delay element).
        # xep_0203 parses the stamp into a datetime; backlog messages are > 10s old.
        from datetime import datetime as dt_class
        from datetime import timezone as tz_class

        is_archived = False
        try:
            stamp = msg["delay"]["stamp"]
            if isinstance(stamp, dt_class):
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=tz_class.utc)
                now = dt_class.now(tz_class.utc)
                is_archived = (now - stamp).total_seconds() > 10
        except (AttributeError, ValueError, TypeError):
            is_archived = False

        # Track NWWS message throughput for real-time messages
        if not is_archived:
            from datetime import datetime as dt_class
            from datetime import timezone as tz_class

            now = dt_class.now(tz_class.utc)
            self.bot.state.nwws_msg_count += 1

            # Calculate throughput in 5-second windows (faster population)
            last_time = self.bot.state.nwws_last_window_time
            if last_time is None or not isinstance(last_time, dt_class):
                self.bot.state.nwws_last_window_time = now
                # Set initial throughput estimate after first message
                self.bot.state.nwws_throughput = 0.2  # Conservative initial estimate
                logger.info("First realtime message received, throughput tracking started")
            else:
                elapsed = (now - last_time).total_seconds()
                if elapsed >= 5:
                    # Calculate throughput from this window
                    throughput = self.bot.state.nwws_msg_count / elapsed if elapsed > 0 else 0
                    if self.bot.state.nwws_throughput is None:
                        self.bot.state.nwws_throughput = throughput
                    else:
                        # 70/30 rolling average: favor recent data
                        self.bot.state.nwws_throughput = (self.bot.state.nwws_throughput * 0.7) + (
                            throughput * 0.3
                        )
                    logger.debug(
                        f"Throughput update: {self.bot.state.nwws_throughput:.2f} msg/s ({self.bot.state.nwws_msg_count} in {elapsed:.1f}s)"
                    )
                    # Reset window
                    self.bot.state.nwws_msg_count = 0
                    self.bot.state.nwws_last_window_time = now
        else:
            # Log archived messages separately to understand reconnection behavior
            logger.debug(f"Skipping archived message ({payload['awipsid']})")

        # Capture receive timestamp for accurate wire latency measurement
        from datetime import datetime as dt_class
        from datetime import timezone as tz_class

        received_at = dt_class.now(tz_class.utc)

        # Route to processing
        nwws_cog = self.bot.get_cog("NWWSCog")
        if nwws_cog:
            self.bot.loop.create_task(
                nwws_cog._process_nwws_message(payload, raw_text, received_at, is_archived)
            )


class NWWSCog(commands.Cog):
    MANAGED_TASK_NAMES = [("monitor_connection", "nwws_connection")]

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.xmpp_client: Optional[NWWSClient] = None
        self._should_be_connected = False
        self._use_rust = _USE_RUST_NWWS  # Instance flag, can change at runtime
        # Self-healing: track reconnect_count to detect zombie reconnect loops.
        self._reconnect_baseline: int = 0
        self._reconnect_check_ts: float = 0.0

    async def cog_load(self):
        if not all([NWWS_USER, NWWS_PASSWORD]):
            logger.warning("Credentials missing, NWWS cog disabled")
            return

        self._should_be_connected = True

        # Try Rust backend first
        if self._use_rust:
            try:
                import spc_rust_core

                spc_rust_core.nwws_start(NWWS_USER, NWWS_PASSWORD, NWWS_SERVER)
                logger.info("Rust NWWS backend started successfully")
                self._drain_rust_nwws.start()
                return
            except Exception as e:
                logger.warning(f"Rust NWWS startup failed, falling back to slixmpp: {e}")
                self._use_rust = False

        # Fall back to legacy slixmpp
        logger.info("Starting slixmpp NWWS client (fallback)")
        self.monitor_connection.start()

    def cog_unload(self):
        self._should_be_connected = False

        if self._use_rust:
            try:
                import spc_rust_core

                spc_rust_core.nwws_stop()
                logger.info("Rust NWWS backend stopped")
            except Exception as e:
                logger.exception(f"Error stopping Rust backend: {e}")

            if self._drain_rust_nwws.is_running():
                self._drain_rust_nwws.cancel()
        else:
            self.monitor_connection.cancel()
            if self.xmpp_client:
                self.xmpp_client.disconnect()

    async def trigger_connection(self):
        """Immediately attempt to connect to NWWS-OI (called by FailoverCog)."""
        if not self._should_be_connected:
            self._should_be_connected = True

        if self._use_rust:
            if not self._drain_rust_nwws.is_running():
                self._drain_rust_nwws.start()
            # If Rust is enabled, monitor_connection (legacy) should NOT run.
            if self.monitor_connection.is_running():
                self.monitor_connection.cancel()
            return

        # Legacy fallback path
        if not self.monitor_connection.is_running():
            self.monitor_connection.start()

        await self.monitor_connection()

    @tasks.loop(seconds=0.5)
    async def _drain_rust_nwws(self):
        """
        Drain messages from Rust NWWS channel and route to processing.
        Non-blocking; called every 0.5s.
        """
        if not self._use_rust:
            return

        try:
            import spc_rust_core

            # Drain all available messages from channel (non-blocking)
            messages_drained = 0
            while True:
                msg_dict = spc_rust_core.nwws_try_recv()
                if msg_dict is None:
                    break

                messages_drained += 1

                # Reconstruct payload object (adapts Rust dict to existing _process_nwws_message)
                class RustPayload:
                    def __init__(self, d):
                        self._dict = d

                    def __getitem__(self, key):
                        return self._dict.get(key)

                payload = RustPayload(msg_dict)
                raw_text = msg_dict.get("text") or msg_dict.get("raw_text", "")
                if not raw_text or not msg_dict.get("awipsid"):
                    continue

                # Check for archived message (XEP-0203 delay stamp)
                is_archived = False
                delay_stamp = msg_dict.get("delay_stamp")
                if delay_stamp and isinstance(delay_stamp, str):
                    from datetime import datetime as dt_class
                    from datetime import timezone as tz_class

                    try:
                        if delay_stamp.endswith("Z"):
                            delay_time = dt_class.fromisoformat(delay_stamp.replace("Z", "+00:00"))
                        else:
                            delay_time = dt_class.fromisoformat(delay_stamp)
                        now = dt_class.now(tz_class.utc)
                        is_archived = (now - delay_time).total_seconds() > 10
                    except (ValueError, TypeError):
                        is_archived = False

                # Use current time; Rust already timestamped it
                from datetime import datetime, timezone

                received_at = datetime.now(timezone.utc)

                asyncio.create_task(
                    self._process_nwws_message(payload, raw_text, received_at, is_archived),
                    name=f"nwws-{msg_dict.get('product_id', 'unknown')}",
                )

            if messages_drained > 0:
                logger.debug(f"Drained {messages_drained} messages from Rust NWWS")

            # ── Queue depth & self-healing ─────────────────────────────────
            stats = spc_rust_core.nwws_stats()
            queue_depth = stats.get("queue_depth", 0)
            if queue_depth > 500:
                logger.warning(
                    f"[NWWS] Rust queue depth is {queue_depth} — Python processing "
                    "may be falling behind the XMPP firehose"
                )

            # Check for zombie reconnect loop every 5 minutes.
            now = time.monotonic()
            if now - self._reconnect_check_ts >= 300:
                reconnect_count = stats.get("reconnect_count", 0)
                delta = reconnect_count - self._reconnect_baseline
                if delta >= 5 and self._reconnect_check_ts > 0:
                    logger.critical(
                        f"[NWWS] Rust sidecar made {delta} reconnect attempts in 5 min "
                        "(zombie reconnect loop detected) — tearing down and restarting"
                    )
                    try:
                        spc_rust_core.nwws_stop()
                        spc_rust_core.nwws_start(NWWS_USER, NWWS_PASSWORD, NWWS_SERVER)
                        logger.info("[NWWS] Rust sidecar restarted after self-heal")
                        self._reconnect_baseline = 0
                    except Exception as heal_err:
                        logger.exception(f"[NWWS] Self-heal restart failed: {heal_err}")
                else:
                    self._reconnect_baseline = reconnect_count
                self._reconnect_check_ts = now

        except Exception as e:
            logger.exception(f"Error in Rust NWWS drain loop: {e}")
            self._use_rust = False  # Fall back to slixmpp
            if not self.monitor_connection.is_running():
                self.monitor_connection.start()

    async def _process_nwws_message(
        self, payload, raw_text: str, received_at, is_archived: bool = False
    ):
        """Parse raw text product and route to appropriate cogs."""
        try:
            afos_pil = payload["awipsid"]
            office = payload["cccc"]
            ttaaii = payload["ttaaii"]

            issue_str = payload["issue"] or time.strftime("%Y%m%d%H%M", time.gmtime())
            product_id = normalize_product_id(office, ttaaii, afos_pil, issue_str)

            if not is_archived:
                issue_val = payload["issue"] or issue_str
                try:
                    from datetime import datetime as dt_class
                    from datetime import timezone as tz_class

                    start_time = self.bot.state.bot_start_time

                    if isinstance(start_time, dt_class):
                        uptime_sec = (received_at - start_time).total_seconds()
                        if uptime_sec > 60:
                            if "T" in issue_val:
                                issue_dt = dt_class.fromisoformat(issue_val.replace("Z", "+00:00"))
                            elif len(issue_val) >= 14:
                                issue_dt = dt_class.strptime(
                                    issue_val[:14], "%Y%m%d%H%M%S"
                                ).replace(tzinfo=tz_class.utc)
                            else:
                                issue_dt = dt_class.strptime(issue_val[:12], "%Y%m%d%H%M").replace(
                                    tzinfo=tz_class.utc
                                )

                            latency = max(0.0, (received_at - issue_dt).total_seconds())
                            if self.bot.state.nwws_latency is None:
                                self.bot.state.nwws_latency = latency
                            else:
                                self.bot.state.nwws_latency = (
                                    self.bot.state.nwws_latency * 0.7
                                ) + (latency * 0.3)
                except Exception as e:
                    logger.debug(f"Latency calculation failed ({issue_val}): {e}")

            if "SEL" in afos_pil:
                result = parse_watch_number(raw_text)
                if result:
                    watch_num, wtype = result
                    watches_cog = self.bot.get_cog("WatchesCog")
                    if watches_cog:
                        from cogs.iembot import _parse_watch_text

                        text = _parse_watch_text(raw_text)
                        if text:
                            from utils.state_store import set_product_cache

                            await set_product_cache(f"watch_{watch_num}", text, ttl=600)

                        await watches_cog.post_watch_now(
                            watch_num, {"type": wtype, "expires": None, "affected_zones": []}
                        )
                        logger.info(f"Triggered Watch {watch_num} via XMPP")

            elif "SWOMCD" in afos_pil:
                md_num = parse_md_number(raw_text)
                if md_num:
                    mesoscale_cog = self.bot.get_cog("MesoscaleCog")
                    if mesoscale_cog:
                        from utils.state_store import set_product_cache

                        await set_product_cache(f"md_{md_num}", raw_text, ttl=600)
                        await mesoscale_cog.post_md_now(md_num)
                        logger.info(f"Triggered MD {md_num} via XMPP")

            elif any(afos_pil.startswith(x) for x in ("TOR", "SVR", "FFW", "SVS", "FFS", "SPS")):
                warnings_cog = self.bot.get_cog("WarningsCog")
                if warnings_cog:
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
                        "SPS": "Special Weather Statement",
                    }
                    pil_prefix = next((p for p in event_map if afos_pil.startswith(p)), None)
                    if pil_prefix:
                        await warnings_cog.post_warning_now(
                            product_id, cleaned_text, event_map[pil_prefix]
                        )
                        logger.info(f"Triggered {pil_prefix} Warning via XMPP")

            elif any(afos_pil.startswith(x) for x in ("LSR", "PNS")):
                reports_cog = self.bot.get_cog("ReportsCog")
                if reports_cog:
                    pil_prefix = "LSR" if afos_pil.startswith("LSR") else "PNS"
                    await reports_cog.post_report_now(product_id, raw_text, pil_prefix)
                    logger.info(f"Triggered {pil_prefix} via XMPP")

        except Exception as e:
            logger.exception(f"Error processing XMPP message: {e}")

    @_drain_rust_nwws.before_loop
    async def before_drain(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=30)
    async def monitor_connection(self):
        """Maintain persistent connection to NWWS-OI."""
        if not self.bot.state.is_primary or not self._should_be_connected:
            if self.xmpp_client and self.xmpp_client.is_connected:
                logger.info("Node is Standby — disconnecting NWWS")
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

        logger.info(f"Connecting to {NWWS_SERVER}...")
        jid = f"{NWWS_USER}@{NWWS_SERVER}"
        self.xmpp_client = NWWSClient(jid, NWWS_PASSWORD, self.bot)

        try:
            self.xmpp_client.connect((NWWS_SERVER, 5222))
        except Exception as e:
            logger.error(f"Connection attempt failed: {e}")
            self.xmpp_client = None

    @monitor_connection.before_loop
    async def before_monitor(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(NWWSCog(bot))
