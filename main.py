# main.py
import os
import sys
import asyncio
import logging
import signal
from datetime import datetime, timedelta, timezone
from typing import List, Tuple

import discord
from discord.ext import commands

from config import TOKEN, GUILD_ID, CACHE_DIR
from utils.http import ensure_session, close_session
from utils.spc_urls import cig_migration
from utils.cache import (
    auto_cache,
    manual_cache,
    last_post_times,
    partial_update_state,
    posted_mds,
    posted_watches,
)

# ── Logging setup ────────────────────────────────────────────────────────────
from config import CONFIG
logger = logging.getLogger("scp_bot")
logger.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

ch = logging.StreamHandler()
ch.setFormatter(formatter)
logger.addHandler(ch)

try:
    from logging.handlers import RotatingFileHandler
    fh = RotatingFileHandler(CONFIG["log_file"], maxBytes=5 * 1024 * 1024, backupCount=3)
    fh.setFormatter(formatter)
    logger.addHandler(fh)
except Exception as e:
    logger.warning(f"Could not create rotating file handler: {e}")

# ── Bot setup ────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Watchdog state
_task_fail_counts = {}
_task_alerted = set()
MANAGED_TASKS: List[Tuple] = []


async def send_bot_alert(title: str, description: str, critical: bool = False):
    """Post a health alert embed to the SPC channel."""
    from config import SPC_CHANNEL_ID
    try:
        channel = bot.get_channel(SPC_CHANNEL_ID)
        if not channel:
            logger.error(f"[ALERT] Could not find SPC channel to send alert: {title}")
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
    await cig_migration()

    # Startup cleanup: remove cached files older than 7 days
    cache_age_limit = timedelta(days=7)
    now = datetime.now()
    for filename in os.listdir(CACHE_DIR):
        if filename.startswith("cached_"):
            file_path = os.path.join(CACHE_DIR, filename)
            try:
                file_time = datetime.fromtimestamp(os.path.getmtime(file_path))
                if now - file_time > cache_age_limit:
                    os.remove(file_path)
                    logger.info(f"Deleted old cached file on startup: {filename}")
            except Exception as e:
                logger.warning(f"Error deleting old cached file {filename}: {e}")

    # Slash command sync
    try:
        logger.info(f"Checking for old guild-specific commands in guild {GUILD_ID}...")
        try:
            guild_obj = discord.Object(id=GUILD_ID)
            guild_commands = await bot.tree.fetch_commands(guild=guild_obj)
            if guild_commands:
                logger.info(f"Found {len(guild_commands)} old guild command(s), removing them...")
                for cmd in guild_commands:
                    try:
                        await bot.http.delete_guild_command(bot.application_id, GUILD_ID, cmd.id)
                        logger.info(f"Deleted old guild command: /{cmd.name}")
                    except Exception as e:
                        logger.warning(f"Could not delete guild command /{cmd.name}: {e}")
                await bot.tree.sync(guild=guild_obj)
            else:
                logger.info("No old guild commands found")
        except Exception as e:
            logger.warning(f"Could not remove guild commands cleanly: {e}")

        logger.info("Syncing command tree globally...")
        synced = await bot.tree.sync()
        logger.info(f"Successfully synced {len(synced)} global slash command(s)")
    except Exception as e:
        logger.error(f"Failed to sync command tree: {e}")

    logger.info("All tasks started. Bot is ready.")


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    logger.error(f"Command error: {error}")
    raise error


# ── Watchdog ─────────────────────────────────────────────────────────────────
from discord.ext import tasks

@tasks.loop(minutes=10)
async def watchdog_task():
    await bot.wait_until_ready()

    from utils.http import http_session
    import aiohttp
    session_healthy = False
    if http_session is not None and not http_session.closed:
        try:
            async with http_session.get(
                "https://www.google.com",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as r:
                session_healthy = r.status < 500
        except Exception as e:
            logger.warning(f"[WATCHDOG] Session probe failed: {e}")

    if not session_healthy:
        logger.warning("[WATCHDOG] Session is dead or unreachable — tearing down and recreating")
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
        logger.warning(f"[WATCHDOG] Task '{name}' has stopped (attempt #{fail_count}) — restarting")

        try:
            task.cancel()
            await asyncio.sleep(0.5)
            task.start()
            logger.info(f"[WATCHDOG] Successfully restarted '{name}'")
        except Exception as e:
            logger.error(f"[WATCHDOG] Failed to restart '{name}': {e}")

        is_critical_task = name in ("auto_post_watches", "auto_post_md")
        alert_threshold = 1 if is_critical_task else 2

        if fail_count >= alert_threshold and name not in _task_alerted:
            _task_alerted.add(name)
            critical = is_critical_task
            await send_bot_alert(
                f"{name} is down",
                f"The `{name}` task has stopped and the watchdog is attempting to restart it "
                f"(attempt #{fail_count}).\n\n"
                + (
                    f"**Watch and MD alerts may be delayed — check "
                    f"[SPC directly](https://www.spc.noaa.gov) if severe weather is ongoing.**"
                    if critical else
                    f"Outlook posts may be delayed until the task recovers."
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
    try:
        await bot.close()
    except Exception as e:
        logger.warning(f"Error while closing bot: {e}")


def _signal_handler(signame):
    logger.info(f"Received signal {signame}. Scheduling shutdown...")
    try:
        asyncio.get_event_loop().create_task(_shutdown())
    except Exception as e:
        logger.error(f"Failed to schedule shutdown task: {e}")


for s in ("SIGINT", "SIGTERM"):
    try:
        signum = getattr(signal, s)
        signal.signal(signum, lambda _signum, _frame, s=s: _signal_handler(s))
    except Exception as e:
        logger.warning(f"Could not register signal {s}: {e}")


# ── Entrypoint ───────────────────────────────────────────────────────────────
async def main():
    async with bot:
        await bot.load_extension("cogs.scp")
        await bot.load_extension("cogs.outlooks")
        await bot.load_extension("cogs.mesoscale")
        await bot.load_extension("cogs.watches")
        await bot.load_extension("cogs.status")
        await bot.load_extension("cogs.radar")

        # Register cog tasks with watchdog after loading
        outlooks_cog = bot.cogs.get("OutlooksCog")
        mesoscale_cog = bot.cogs.get("MesoscaleCog")
        watches_cog = bot.cogs.get("WatchesCog")

        if outlooks_cog:
            MANAGED_TASKS.extend([
                (outlooks_cog.auto_post_spc,        "auto_post_spc"),
                (outlooks_cog.aggressive_check_spc, "aggressive_check_spc"),
                (outlooks_cog.auto_post_spc48,      "auto_post_spc48"),
            ])
        if mesoscale_cog:
            MANAGED_TASKS.append((mesoscale_cog.auto_post_md, "auto_post_md"))
        if watches_cog:
            MANAGED_TASKS.append((watches_cog.auto_post_watches, "auto_post_watches"))

        watchdog_task.start()

        await bot.start(TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.exception(f"Unhandled exception in bot run: {e}")
    finally:
        logger.info("Bot exited.")
