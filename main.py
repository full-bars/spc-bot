# main.py
import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timedelta, timezone
from typing import List, Tuple

import discord
from discord.ext import commands, tasks

from config import CACHE_DIR, GUILD_ID, TOKEN, CONFIG
from utils.http import close_session, ensure_session
from utils.db import check_integrity, close_db, get_db, migrate_from_json
from utils.state import BotState

# ── Logging setup ────────────────────────────────────────────────────────────
logger = logging.getLogger("spc_bot")
logger.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

ch = logging.StreamHandler()
ch.setFormatter(formatter)
logger.addHandler(ch)

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

async def setup_hook():
    """Hydrate state from DB before any cogs are loaded."""
    from utils.db import (
        check_integrity, get_db, migrate_from_json,
        get_all_hashes, get_posted_urls, get_posted_mds, get_posted_watches,
        get_state
    )
    import json as _json

    # Initialize database
    db_ok = await check_integrity()
    if not db_ok:
        logger.warning("[DB] Database integrity check failed — recreating")
        db_path = os.path.join(CACHE_DIR, "bot_state.db")
        if os.path.exists(db_path):
            os.rename(db_path, db_path + ".corrupted")
        await get_db()
    await migrate_from_json()

    # Restore in-memory caches from DB
    db_auto = await get_all_hashes("auto")
    if db_auto:
        bot.state.auto_cache.update(db_auto)
        logger.info(f"[DB] Loaded {len(db_auto)} auto hashes into cache")

    db_manual = await get_all_hashes("manual")
    if db_manual:
        bot.state.manual_cache.update(db_manual)
        logger.info(f"[DB] Loaded {len(db_manual)} manual hashes into cache")

    db_mds = await get_posted_mds()
    if db_mds:
        bot.state.posted_mds.update(db_mds)
        logger.info(f"[DB] Loaded {len(db_mds)} posted MDs into cache")

    db_watches = await get_posted_watches()
    if db_watches:
        bot.state.posted_watches.update(db_watches)
        logger.info(f"[DB] Loaded {len(db_watches)} posted watches into cache")

    # CSU state
    csu_raw = await get_state("csu_mlp_posted")
    if csu_raw:
        try:
            csu_data = _json.loads(csu_raw)
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if csu_data.get("date") == today:
                bot.state.csu_posted.update(str(d) for d in csu_data.get("days", []))
                logger.info(f"[DB] Restored {len(bot.state.csu_posted)} CSU posted days")
        except Exception:
            pass

    for day_key in ["day1", "day2", "day3"]:
        urls = await get_posted_urls(day_key)
        if urls:
            bot.state.last_posted_urls[day_key] = urls
            logger.info(f"[DB] Restored posted URLs for {day_key}")
    logger.info("[DB] Database ready")

    # Load failover cog first
    await bot.load_extension("cogs.failover")
    if IS_PRIMARY:
        for ext in ALL_EXTENSIONS:
            await bot.load_extension(ext)
    else:
        logger.info("[FAILOVER] Running as STANDBY — cogs suppressed until promoted")

    # Register cog tasks with watchdog after loading
    for cog in bot.cogs.values():
        if hasattr(cog, "MANAGED_TASK_NAMES"):
            for task_attr, display_name in cog.MANAGED_TASK_NAMES:
                task = getattr(cog, task_attr, None)
                if task and isinstance(task, tasks.Loop):
                    MANAGED_TASKS.append((task, display_name))
                    logger.debug(f"[WATCHDOG] Registered task '{display_name}' from {type(cog).__name__}")

    watchdog_task.start()

bot.setup_hook = setup_hook

IS_PRIMARY = os.getenv("IS_PRIMARY", "true").lower() == "true"
bot.state.is_primary = IS_PRIMARY

ALL_EXTENSIONS = [
    "cogs.iembot", "cogs.scp", "cogs.outlooks", "cogs.mesoscale", "cogs.watches",
    "cogs.status", "cogs.radar", "cogs.csu_mlp", "cogs.ncar",
    "cogs.sounding", "cogs.hodograph",
]

# Watchdog state
_task_fail_counts = {}
_task_alerted = set()
MANAGED_TASKS: List[Tuple] = []


async def send_bot_alert(
    title: str, description: str, critical: bool = False
):
    """Post a health alert embed to the SPC channel."""
    from config import SPC_CHANNEL_ID

    try:
        channel = bot.get_channel(SPC_CHANNEL_ID)
        if not channel:
            logger.error(
                f"[ALERT] Could not find SPC channel to send alert: {title}"
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
        logger.error(f"[ALERT] Failed to send Discord alert '{title}': {e}")


# ── Events ───────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    import cogs.status as status_cog

    status_cog.BOT_START_TIME = datetime.now(timezone.utc)

    logger.info(f"Logged in as {bot.user} (id={bot.user.id})")

    await ensure_session()

    # Slash command sync
    try:
        if bot.state.is_primary:
            try:
                logger.info("Syncing command tree globally...")
                synced = await bot.tree.sync()
                logger.info(
                    f"Successfully synced {len(synced)} global slash command(s)"
                )
            except Exception as e:
                logger.error(f"Failed to sync command tree: {e}")
        else:
            logger.info("[FAILOVER] Standby — skipping command sync to preserve primary commands")
        logger.info("All tasks started. Bot is ready.")
        periodic_sync.start()
    except Exception as e:
        logger.error(f"[on_ready] Unhandled error: {e}")

@tasks.loop(hours=24)
async def periodic_sync():
    await bot.wait_until_ready()
    if not IS_PRIMARY:
        return
    try:
        synced = await bot.tree.sync()
        logger.info(f"[SYNC] Periodic command sync: {len(synced)} commands")
    except Exception as e:
        logger.error(f"[SYNC] Periodic command sync failed: {e}")


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    logger.error(f"Command error: {error}")
    raise error


# ── Watchdog ─────────────────────────────────────────────────────────────────
import aiohttp


@tasks.loop(minutes=2)
async def watchdog_task():
    await bot.wait_until_ready()

    # If we are in STANDBY, do not monitor or restart tasks as they
    # are suppressed by the failover mechanism.
    if not bot.state.is_primary:
        return

    from utils.http import http_session

    session_healthy = False
    if http_session is not None and not http_session.closed:
        try:
            async with http_session.get(
                "https://www.google.com",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                session_healthy = r.status < 500
        except Exception as e:
            logger.warning(f"[WATCHDOG] Session probe failed: {e}")

    if not session_healthy:
        logger.warning(
            "[WATCHDOG] Session is dead or unreachable — "
            "tearing down and recreating"
        )
        await close_session()
        await ensure_session()

    for task, name in MANAGED_TASKS:
        if task.is_running():
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
        logger.warning(
            f"[WATCHDOG] Task '{name}' has stopped "
            f"(attempt #{fail_count}) — restarting"
        )

        try:
            task.cancel()
            await asyncio.sleep(0.5)
            task.start()
            logger.info(f"[WATCHDOG] Successfully restarted '{name}'")
        except Exception as e:
            logger.error(
                f"[WATCHDOG] Failed to restart '{name}': {e}"
            )

        is_critical_task = name in ("auto_post_watches", "auto_post_md")
        alert_threshold = 1 if is_critical_task else 2

        if fail_count >= alert_threshold and name not in _task_alerted:
            _task_alerted.add(name)
            critical = is_critical_task
            await send_bot_alert(
                f"{name} is down",
                f"The `{name}` task has stopped and the watchdog is "
                f"attempting to restart it (attempt #{fail_count}).\n\n"
                + (
                    f"**Watch and MD alerts may be delayed — check "
                    f"[SPC directly](https://www.spc.noaa.gov) "
                    f"if severe weather is ongoing.**"
                    if critical
                    else f"Outlook posts may be delayed until the "
                    f"task recovers."
                ),
                critical=critical,
            )


# ── Graceful shutdown ────────────────────────────────────────────────────────
async def _shutdown():
    logger.info("Shutting down bot gracefully...")
    try:
        for task, name in MANAGED_TASKS:
            task.cancel()
        watchdog_task.cancel()
    except Exception:
        pass
    await close_session()
    await close_db()
    try:
        await bot.close()
    except Exception as e:
        logger.warning(f"Error while closing bot: {e}")


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
        # Register signal handlers with the running loop
        try:
            _setup_signal_handlers(asyncio.get_running_loop())
        except Exception as e:
            logger.warning(f"Could not set up signal handlers: {e}")

        await bot.start(TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.exception(f"Unhandled exception in bot run: {e}")
    finally:
        logger.info("Bot exited.")
