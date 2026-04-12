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

    # Initialize BotState if not already done
    if not hasattr(bot, "state"):
        bot.state = BotState()

    status_cog.BOT_START_TIME = datetime.now(timezone.utc)

    logger.info(f"Logged in as {bot.user} (id={bot.user.id})")

    await ensure_session()

    # Initialize database
    db_ok = await check_integrity()
    if not db_ok:
        logger.warning("[DB] Database integrity check failed — recreating")
        import os
        from config import CACHE_DIR
        db_path = os.path.join(CACHE_DIR, "bot_state.db")
        if os.path.exists(db_path):
            os.rename(db_path, db_path + ".corrupted")
        await get_db()
    await migrate_from_json()

    # Restore in-memory caches from DB
    from utils.db import get_all_hashes, get_posted_urls, get_posted_mds, get_posted_watches
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
    for day_key in ["day1", "day2", "day3"]:
        urls = await get_posted_urls(day_key)
        if urls:
            bot.state.last_posted_urls[day_key] = urls
            logger.info(f"[DB] Restored posted URLs for {day_key}")
    logger.info("[DB] Database ready")





    # Slash command sync
    try:
        logger.info(
            f"Checking for old guild-specific commands "
            f"in guild {GUILD_ID}..."
        )
        try:
            guild_obj = discord.Object(id=GUILD_ID)
            guild_commands = await bot.tree.fetch_commands(guild=guild_obj)
            if guild_commands:
                logger.info(
                    f"Found {len(guild_commands)} old guild command(s), "
                    f"removing them..."
                )
                for cmd in guild_commands:
                    try:
                        await bot.http.delete_guild_command(
                            bot.application_id, GUILD_ID, cmd.id
                        )
                        logger.info(
                            f"Deleted old guild command: /{cmd.name}"
                        )
                    except Exception as e:
                        logger.warning(
                            f"Could not delete guild command "
                            f"/{cmd.name}: {e}"
                        )
                await bot.tree.sync(guild=guild_obj)
            else:
                logger.info("No old guild commands found")
        except Exception as e:
            logger.warning(
                f"Could not remove guild commands cleanly: {e}"
            )

        logger.info("Syncing command tree globally...")
        synced = await bot.tree.sync()
        logger.info(
            f"Successfully synced {len(synced)} global slash command(s)"
        )
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
import aiohttp


@tasks.loop(minutes=2)
async def watchdog_task():
    await bot.wait_until_ready()

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

        await bot.load_extension("cogs.scp")
        await bot.load_extension("cogs.outlooks")
        await bot.load_extension("cogs.mesoscale")
        await bot.load_extension("cogs.watches")
        await bot.load_extension("cogs.status")
        await bot.load_extension("cogs.radar")
        await bot.load_extension("cogs.csu_mlp")
        await bot.load_extension("cogs.ncar")
        await bot.load_extension("cogs.sounding")
        await bot.load_extension("cogs.hodograph")

        # Register cog tasks with watchdog after loading
        outlooks_cog = bot.cogs.get("OutlooksCog")
        mesoscale_cog = bot.cogs.get("MesoscaleCog")
        watches_cog = bot.cogs.get("WatchesCog")
        scp_cog = bot.cogs.get("SCPCog")
        csu_mlp_cog = bot.cogs.get("CSUMLPCog")
        ncar_cog = bot.cogs.get("NCARCog")

        if outlooks_cog:
            MANAGED_TASKS.extend(
                [
                    (outlooks_cog.auto_post_spc, "auto_post_spc"),
                    (
                        outlooks_cog.aggressive_check_spc,
                        "aggressive_check_spc",
                    ),
                    (outlooks_cog.auto_post_spc48, "auto_post_spc48"),
                ]
            )
        if mesoscale_cog:
            MANAGED_TASKS.append(
                (mesoscale_cog.auto_post_md, "auto_post_md")
            )
        if watches_cog:
            MANAGED_TASKS.append(
                (watches_cog.auto_post_watches, "auto_post_watches")
            )
        if scp_cog:
            MANAGED_TASKS.append(
                (scp_cog.auto_post_scp, "auto_post_scp_daily")
            )
        if csu_mlp_cog:
            MANAGED_TASKS.append(
                (csu_mlp_cog.csu_mlp_daily_poll, "csu_mlp_daily_poll")
            )
        if ncar_cog:
            MANAGED_TASKS.append(
                (ncar_cog.wxnext_daily_poll, "wxnext_daily_poll")
            )

        watchdog_task.start()

        await bot.start(TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.exception(f"Unhandled exception in bot run: {e}")
    finally:
        logger.info("Bot exited.")
