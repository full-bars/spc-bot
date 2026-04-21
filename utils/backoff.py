# utils/backoff.py
"""
Exponential backoff tracker for background task loops.

Usage:
    backoff = TaskBackoff("auto_post_spc")

    # In your task loop:
    try:
        await do_work()
        backoff.success()
    except SomeNetworkError:
        await backoff.failure(bot)
"""

import asyncio
import logging

logger = logging.getLogger("spc_bot")

# Backoff delays in seconds for consecutive failure counts
# Index = failure count (capped at last value)
_BACKOFF_DELAYS = [0, 0, 30, 60, 120, 300, 300]


class TaskBackoff:
    """
    Tracks consecutive failures for a named task and sleeps inline
    on `failure()` to back off during network issues.
    """

    def __init__(self, name: str):
        self.name = name
        self._failures = 0

    def success(self):
        """Call after a successful cycle to reset backoff."""
        if self._failures > 0:
            logger.info(
                f"[BACKOFF] {self.name}: recovered after "
                f"{self._failures} consecutive failure(s)"
            )
        self._failures = 0

    async def failure(self, bot=None):
        """
        Call after a failed cycle. Calculates backoff and optionally
        sends a Discord alert if failures are severe.
        """
        self._failures += 1
        delay = _BACKOFF_DELAYS[min(self._failures, len(_BACKOFF_DELAYS) - 1)]

        if delay > 0:
            # Convert delay to loop cycles to skip (approximate)
            # We just sleep inline instead of counting cycles for simplicity
            logger.warning(
                f"[BACKOFF] {self.name}: failure #{self._failures}, "
                f"sleeping {delay}s before next attempt"
            )
            await asyncio.sleep(delay)
        else:
            logger.debug(
                f"[BACKOFF] {self.name}: failure #{self._failures}, "
                f"no delay yet"
            )

        # Send alert at threshold
        if self._failures == 5 and bot:
            try:
                from main import send_bot_alert
                await send_bot_alert(
                    f"{self.name} degraded",
                    f"Task `{self.name}` has failed **{self._failures}** times "
                    f"in a row. Backoff is active — polling slowed to avoid "
                    f"hammering unreachable servers.",
                    critical=False,
                )
            except Exception as e:
                logger.warning(f"[BACKOFF] Could not send alert: {e}")
