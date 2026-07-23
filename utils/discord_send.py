"""Send/thread helpers for background auto-posters.

Wraps discord.abc.Messageable.send() and Message.create_thread() so that a
missing-permissions failure is always logged clearly (channel/thread name and
ID, what operation failed) instead of surfacing only as a generic exception
buried in a traceback. Intended for cogs that post to a resolved/configured
channel on a background task, where no user is present to see a failure.
"""

import logging

import discord

logger = logging.getLogger("spc_bot")


async def safe_send(channel, *, context: str, **send_kwargs):
    """Send a message to `channel`, logging clearly on missing permissions.

    Returns the sent discord.Message, or None if the send failed.
    `context` is a short human-readable description of what was being posted
    (e.g. "tropical TCP for Bertha"), used in the log message.
    """
    try:
        return await channel.send(**send_kwargs)
    except discord.Forbidden as e:
        logger.error(f"Missing permissions to post {context} in #{channel.id} ({channel}): {e}")
        return None
    except Exception as e:
        logger.exception(f"Failed to post {context} in #{channel.id} ({channel}): {e}")
        return None


async def safe_create_thread(message, *, context: str, **thread_kwargs):
    """Create a thread on `message`, logging clearly on missing permissions.

    Returns the created discord.Thread, or None if creation failed.
    """
    try:
        return await message.create_thread(**thread_kwargs)
    except discord.Forbidden as e:
        logger.error(
            f"Missing permissions to create thread for {context} "
            f"in #{message.channel.id} ({message.channel}): {e}"
        )
        return None
    except Exception as e:
        logger.warning(f"Failed to create thread for {context}: {e}")
        return None
