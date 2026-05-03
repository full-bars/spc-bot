# main.py
import asyncio
import json as _json
import logging
import os
import signal
from datetime import datetime, timezone

import aiohttp
import discord
import discord.app_commands
from discord.ext import commands, tasks

from config import CACHE_DIR, CONFIG, DEV_CHANNEL_ID, HEALTH_CHANNEL_ID, TOKEN, __version__
import utils.http
from utils.http import CircuitOpenError
from utils.state_store import (
    check_integrity, close_db, get_db,
    get_all_hashes, get_posted_urls, get_posted_mds, get_posted_watches,
    get_posted_reports,
    get_state,
)
from utils.cache import hydrate_validators_from_store
from utils.state import BotState, RecentLogHandler
from cogs import ALL_EXTENSIONS

# ── Logging setup ────────────────────────────────────────────────────────────
logger = logging.getLogger("spc_bot")
logger.setLevel(logging.INFO)
logger.propagate = False
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # In-memory log buffer for /logs command
    log_handler = RecentLogHandler(max_lines=20)
    log_handler.setFormatter(formatter)
    logger.addHandler(log_handler)

    try:
        from logging.handlers import RotatingFileHandler

        fh = RotatingFileHandler(
            CONFIG["log_file"], maxBytes=5 * 1024 * 1024, backupCount=3
        )
        fh.setFormatter(formatter)
        logger.addHandler(fh)
    except Exception as e:
        logger.warning(f"Could not create rotating file handler: {e}")

# ── Bot setup ────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
bot.state = BotState()
bot.log_handler = log_handler

# Initialize HTTP latency tracking
def _update_http_latency(l: float):
    if bot.state.http_latency == 0:
        bot.state.http_latency = l
    else:
        bot.state.http_latency = (bot.state.http_latency * 0.9) + (l * 0.1)

utils.http.set_latency_callback(_update_http_latency)

IS_PRIMARY = os.getenv("IS_PRIMARY", "true").lower() == "true"
bot.state.is_primary = IS_PRIMARY

async def setup_hook():
    """Hydrate state from DB before any cogs are loaded."""
    # Initialize database
    db_ok = await check_integrity()
    if not db_ok:
        logger.warning("[DB] Database integrity check failed — recreating")
        db_path = os.path.join(CACHE_DIR, "bot_state.db")
        if os.path.exists(db_path):
            os.rename(db_path, db_path + ".corrupted")
        await get_db()

    # Restore in-memory caches from DB
    results = await asyncio.gather(
        get_all_hashes("auto"),
        get_all_hashes("manual"),
        get_posted_mds(),
        get_posted_watches(),
        get_posted_reports(),
        get_state("csu_mlp_posted"),
        get_posted_urls("day1"),
        get_posted_urls("day2"),
        get_posted_urls("day3"),
        get_state("iembot_last_seqnum"),
        return_exceptions=True
    )
    
    db_auto, db_manual, db_mds, db_watches, db_reports, csu_raw, d1_urls, d2_urls, d3_urls, last_seq = results

    if isinstance(last_seq, str):
        bot.state.iembot_last_seqnum = int(last_seq)
        logger.info(f"[DB] Restored last seqnum {last_seq}")

    if isinstance(db_auto, dict):
        bot.state.auto_cache.update(db_auto)
        logger.info(f"[DB] Loaded {len(db_auto)} auto hashes into cache")

    if isinstance(db_manual, dict):
        bot.state.manual_cache.update(db_manual)
        logger.info(f"[DB] Loaded {len(db_manual)} manual hashes into cache")

    if isinstance(db_mds, (set, list)):
        bot.state.posted_mds.update(db_mds)
        logger.info(f"[DB] Loaded {len(db_mds)} posted MDs into cache")

    if isinstance(db_watches, (set, list)):
        bot.state.posted_watches.update(db_watches)
        logger.info(f"[DB] Loaded {len(db_watches)} posted watches into cache")

    if isinstance(db_reports, (set, list)):
        bot.state.posted_reports.update(db_reports)
        logger.info(f"[DB] Loaded {len(db_reports)} posted reports into cache")

    # CSU state
    if isinstance(csu_raw, str):
        try:
            csu_data = _json.loads(csu_raw)
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if csu_data.get("date") == today:
                bot.state.csu_posted.update(str(d) for d in csu_data.get("days", []))
                logger.info(f"[DB] Restored {len(bot.state.csu_posted)} CSU posted days")
        except (ValueError, KeyError, TypeError) as e:
            logger.debug(f"[DB] CSU state parse failed (ignored): {e}")

    for day_key, urls in zip(["day1", "day2", "day3"], [d1_urls, d2_urls, d3_urls], strict=True):
        if isinstance(urls, list) and urls:
            bot.state.last_posted_urls[day_key] = urls
            logger.info(f"[DB] Restored posted URLs for {day_key}")
    # Warm the conditional-GET validator cache so the first poll after
    # restart doesn't redownload every URL.
    try:
        await hydrate_validators_from_store()
    except Exception as e:
        logger.warning(f"[DB] validator hydration skipped: {e}")

    logger.info("[DB] Database ready")

    # Register failover cog first so it can probe Upstash for the lease
    # before we decide whether to load the rest. Without this check, a
    # primary reboot races its own sync_loop: cogs start posting using
    # stale in-memory state during the ~30s window before the first
    # lease probe fires, duplicating anything the current lease holder
    # posted while we were down.
    await bot.load_extension("cogs.failover")
    failover_cog = bot.get_cog("FailoverCog")
    should_run_primary = IS_PRIMARY
    if failover_cog is not None:
        try:
            should_run_primary = await failover_cog.startup_lease_check()
        except Exception as e:
            logger.warning(
                f"[FAILOVER] Startup lease check failed ({e!r}) — "
                f"falling back to IS_PRIMARY env value and deferring to "
                f"sync_loop for reconciliation"
            )

    if should_run_primary:
        for ext in ALL_EXTENSIONS:
            await bot.load_extension(ext)
    else:
        logger.info("[FAILOVER] Running as STANDBY — cogs suppressed until promoted")

    watchdog_task.start()

bot.setup_hook = setup_hook

# Watchdog state
_task_fail_counts = {}
_task_alerted = set()
# Only alert when a task goes from running → stopped. Without this,
# the first watchdog iteration can fire before the cog task loops
# have been scheduled by the event loop, producing a spurious
# startup "task is down" alert immediately followed by "recovered".
_task_seen_running = set()
_session_probe_failures = 0


async def send_bot_alert(
    title: str, description: str, critical: bool = False
):
    """Post a health alert embed to the health/SPC channel."""
    try:
        channel = bot.get_channel(HEALTH_CHANNEL_ID)
        if not channel:
            try:
                channel = await bot.fetch_channel(HEALTH_CHANNEL_ID)
            except discord.HTTPException as e:
                logger.error(
                    f"[ALERT] Could not fetch health channel to send alert '{title}': {e}"
                )
                return

        if not channel:
            logger.error(
                f"[ALERT] Health channel not found after fetch for alert: {title}"
            )
            return
        color = discord.Color.red() if critical else discord.Color.orange()
        embed = discord.Embed(
            title=f"{'🚨' if critical else '⚠️'}  Bot Health Alert — {title}",
            description=description,
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text="WXModelBot Health Monitor")
        await channel.send(embed=embed)
        logger.warning(f"[ALERT] Sent Discord alert: {title}")
    except Exception as e:
        logger.exception(f"[ALERT] Failed to send Discord alert '{title}': {e}")


# ── Events ───────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    bot.state.bot_start_time = datetime.now(timezone.utc)

    logger.info(f"Logged in as {bot.user} (id={bot.user.id}) | Version: v{__version__}")

    await utils.http.ensure_session()

    # Slash command sync
    try:
        if bot.state.is_primary:
            # Ensure Syncthing folder is in send-only mode on every Primary boot.
            # _promote() handles this for Standby→Primary transitions; this covers
            # nodes that start directly as Primary (IS_PRIMARY=true).
            from utils.events_db import set_syncthing_folder_mode  # noqa: PLC0415
            await set_syncthing_folder_mode("sendonly")
            try:
                logger.info("Syncing command tree globally...")
                synced = await bot.tree.sync()
                logger.info(
                    f"Successfully synced {len(synced)} global slash command(s)"
                )
            except Exception as e:
                logger.exception(f"Failed to sync command tree: {e}")
        else:
            logger.info("[FAILOVER] Standby — skipping command sync to preserve primary commands")
        logger.info(f"All tasks started. Bot v{__version__} is ready.")
        if not periodic_sync.is_running():
            periodic_sync.start()
        if not snapshot_events_task.is_running():
            snapshot_events_task.start()
    except Exception as e:
        logger.exception(f"[on_ready] Unhandled error: {e}")

@tasks.loop(hours=24)
async def periodic_sync():
    await bot.wait_until_ready()
    if not IS_PRIMARY:
        return
    try:
        synced = await bot.tree.sync()
        logger.info(f"[SYNC] Periodic command sync: {len(synced)} commands")
    except Exception as e:
        logger.exception(f"[SYNC] Periodic command sync failed: {e}")


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    logger.error(f"Command error: {error}")
    raise error

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    # Standby nodes have no cogs loaded, so they receive CommandNotFound for
    # every slash command dispatched to them by Discord. Responding here would
    # pre-acknowledge the interaction and cause the primary's defer() to fail
    # with 40060 "already acknowledged". Swallow silently.
    if isinstance(error, discord.app_commands.CommandNotFound):
        logger.debug(f"AppCommand not found (standby or unknown): {error}")
        return

    original = getattr(error, 'original', error)
    if isinstance(original, CircuitOpenError):
        msg = f"⚠️ The upstream API ({str(original).split('for')[-1].strip()}) is currently degraded or offline. Please try again later."
    else:
        logger.error(f"AppCommand error: {error}")
        msg = "⚠️ An unexpected error occurred while processing this command."

    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except discord.HTTPException as e:
        logger.debug(f"AppCommand error reply failed (interaction expired or already acknowledged): {e}")


# ── Watchdog ─────────────────────────────────────────────────────────────────
@tasks.loop(minutes=2)
async def watchdog_task():
    global _session_probe_failures
    await bot.wait_until_ready()

    # If we are in STANDBY, do not monitor or restart tasks as they
    # are suppressed by the failover mechanism.
    if not bot.state.is_primary:
        return

    # Probe two independent endpoints. We only count a failure if BOTH fail —
    # a single-endpoint outage (e.g. NWS maintenance) shouldn't trigger a
    # session teardown or operator alert.
    _PROBE_PRIMARY = "https://api.weather.gov/"
    _PROBE_SECONDARY = "https://mesonet.agron.iastate.edu/"

    async def _head_ok(url: str) -> bool:
        if utils.http.http_session is None or utils.http.http_session.closed:
            return False
        try:
            async with utils.http.http_session.head(
                url, timeout=aiohttp.ClientTimeout(total=20), allow_redirects=True
            ) as r:
                return r.status < 500
        except Exception as e:
            logger.warning(f"[WATCHDOG] Session probe to {url} failed: {e!r}")
            return False

    primary_ok = await _head_ok(_PROBE_PRIMARY)
    probe_healthy = primary_ok or await _head_ok(_PROBE_SECONDARY)

    if probe_healthy:
        if _session_probe_failures > 0:
            logger.info(f"[WATCHDOG] Session probe recovered after {_session_probe_failures} failure(s)")
        _session_probe_failures = 0
    else:
        _session_probe_failures += 1
        if _session_probe_failures >= 3:
            logger.warning(
                f"[WATCHDOG] Session probe failed {_session_probe_failures} consecutive times — "
                "tearing down and recreating"
            )
            try:
                ch = bot.get_channel(DEV_CHANNEL_ID) or await bot.fetch_channel(DEV_CHANNEL_ID)
                await ch.send(embed=discord.Embed(
                    title="⚠️ Watchdog: session reset",
                    description=(
                        f"Both `{_PROBE_PRIMARY}` and `{_PROBE_SECONDARY}` failed "
                        f"{_session_probe_failures} consecutive cycles. "
                        "Tearing down and recreating the aiohttp session."
                    ),
                    color=discord.Color.red(),
                ))
            except Exception as alert_err:
                logger.warning(f"[WATCHDOG] Could not send session-reset alert: {alert_err}")
            await utils.http.close_session()
            await utils.http.ensure_session()
            _session_probe_failures = 0
        else:
            logger.info(
                f"[WATCHDOG] Session probe failed ({_session_probe_failures}/3) — "
                "waiting for next cycle"
            )
            if _session_probe_failures == 2:
                try:
                    ch = bot.get_channel(DEV_CHANNEL_ID) or await bot.fetch_channel(DEV_CHANNEL_ID)
                    await ch.send(embed=discord.Embed(
                        title="⚠️ Watchdog: probe degraded (2/3)",
                        description=(
                            f"Both `{_PROBE_PRIMARY}` and `{_PROBE_SECONDARY}` unreachable "
                            "for 2 consecutive cycles. Session reset on next failure."
                        ),
                        color=discord.Color.orange(),
                    ))
                except Exception as alert_err:
                    logger.warning(f"[WATCHDOG] Could not send degradation alert: {alert_err}")

    # Grace period for startup race: tasks need a few ticks to schedule
    # their first iteration after wait_until_ready() unblocks.
    if watchdog_task.current_loop == 0:
        await asyncio.sleep(5)

    # Dynamically discover tasks from currently loaded cogs
    current_managed_tasks = []
    for cog in bot.cogs.values():
        if hasattr(cog, "MANAGED_TASK_NAMES"):
            for task_attr, display_name in cog.MANAGED_TASK_NAMES:
                task = getattr(cog, task_attr, None)
                if task and isinstance(task, tasks.Loop):
                    current_managed_tasks.append((task, display_name))

    for task, name in current_managed_tasks:
        if task.is_running():
            _task_seen_running.add(name)
            if name in _task_alerted:
                _task_alerted.discard(name)
                _task_fail_counts[name] = 0
                await send_bot_alert(
                    f"{name} recovered",
                    f"✅ The `{name}` task is running again.",
                    critical=False,
                )
            continue

        _task_fail_counts[name] = _task_fail_counts.get(name, 0) + 1
        fail_count = _task_fail_counts[name]
        
        # Try to extract the error that stopped the task
        error_detail = ""
        inner_task = task.get_task()
        if inner_task and inner_task.done():
            try:
                exc = inner_task.exception()
                if exc:
                    error_detail = f"\n**Last Error:** `{type(exc).__name__}: {exc}`"
            except (asyncio.CancelledError, asyncio.InvalidStateError) as e:
                logger.debug(f"[WATCHDOG] Could not read task exception: {e}")

        # Attempt to (re)start the task quietly
        try:
            task.cancel()
            inner = task.get_task()
            if inner is not None and not inner.done():
                try:
                    await asyncio.wait_for(asyncio.shield(inner), timeout=5.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                except Exception as e:
                    logger.debug(f"[WATCHDOG] Error while awaiting cancelled task: {e}")
            task.start()
            
            log_fn = logger.info if name in _task_seen_running else logger.debug
            log_fn(f"[WATCHDOG] Attempted to {'re' if name in _task_seen_running else ''}start '{name}'")
        except Exception as e:
            logger.exception(
                f"[WATCHDOG] Failed to restart '{name}': {e}"
            )

        # Alerts — only for tasks we've seen running before to avoid startup noise
        if name in _task_seen_running:
            is_critical_task = name in ("auto_post_watches", "auto_post_md")
            alert_threshold = 1 if is_critical_task else 2

            if fail_count >= alert_threshold and name not in _task_alerted:
                _task_alerted.add(name)
                critical = is_critical_task
                await send_bot_alert(
                    f"{name} is down",
                    f"The `{name}` task has stopped and the watchdog is "
                    f"attempting to restart it (attempt #{fail_count})."
                    f"{error_detail or ' Error: None'}\n\n"
                    + (
                        "**Watch and MD alerts may be delayed — check "
                        "[SPC directly](https://www.spc.noaa.gov) "
                        "if severe weather is ongoing.**"
                        if critical
                        else "Outlook posts may be delayed until the "
                        "task recovers."
                    ),
                    critical=critical,
                )


# ── Events DB snapshot (every 5 min, Primary only) ───────────────────────────
@tasks.loop(minutes=5)
async def snapshot_events_task():
    if not bot.state.is_primary:
        return
    from utils.events_db import snapshot_for_sync  # noqa: PLC0415
    await snapshot_for_sync()


# ── Graceful shutdown ────────────────────────────────────────────────────────
_shutting_down = False


async def _shutdown():
    global _shutting_down
    if _shutting_down:
        logger.info("Shutdown already in progress — ignoring duplicate signal")
        return
    _shutting_down = True
    logger.info("Shutting down bot gracefully...")

    # 1. Cancel managed and background tasks
    # We rediscover here too just to be safe
    for cog in bot.cogs.values():
        if hasattr(cog, "MANAGED_TASK_NAMES"):
            for task_attr, _ in cog.MANAGED_TASK_NAMES:
                task = getattr(cog, task_attr, None)
                if task:
                    task.cancel()
    watchdog_task.cancel()
    if periodic_sync.is_running():
        periodic_sync.cancel()
    if snapshot_events_task.is_running():
        snapshot_events_task.cancel()

    # 2. Close DB, HTTP session, and plot worker pool
    try:
        from cogs.sounding_utils import shutdown_plot_executor
        shutdown_plot_executor()
    except Exception:
        pass
    try:
        from utils.events_db import close_events_db  # noqa: PLC0415
        await asyncio.wait_for(
            asyncio.gather(utils.http.close_session(), close_db(), close_events_db(), return_exceptions=True),
            timeout=3.0,
        )
    except asyncio.TimeoutError:
        logger.warning("Shutdown timed out while closing connections")
    except Exception as e:
        logger.warning(f"Error during resource cleanup: {e}")

    # 3. Close the bot — discord.py cancels its internal tasks and closes the
    #    WebSocket, which causes bot.start() in main() to return naturally.
    #    Do NOT wrap in asyncio.wait_for: a timeout leaves _closing_task
    #    dangling in the event loop, which blocks asyncio.run() cleanup for
    #    the full systemd TimeoutStopSec (90 s) before SIGKILL.
    await bot.close()

def _setup_signal_handlers(loop: asyncio.AbstractEventLoop):
    """Register signal handlers using the running event loop."""
    for sig_name in ("SIGINT", "SIGTERM"):
        try:
            sig = getattr(signal, sig_name)
            loop.add_signal_handler(
                sig,
                lambda s=sig_name: asyncio.ensure_future(_shutdown()),
            )
            logger.info(f"Registered signal handler for {sig_name}")
        except (NotImplementedError, OSError) as e:
            # Windows doesn't support add_signal_handler
            logger.warning(f"Could not register signal {sig_name}: {e}")


# ── Entrypoint ───────────────────────────────────────────────────────────────
async def main():
    async with bot:
        # _setup_signal_handlers handles per-signal errors itself.
        _setup_signal_handlers(asyncio.get_running_loop())
        await bot.start(TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.exception(f"Unhandled exception in bot run: {e}")
    finally:
        logger.info("Bot exited.")
