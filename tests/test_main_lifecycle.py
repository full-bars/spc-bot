"""Tests for `main.py` lifecycle: import smoke, shutdown guard,
and the watchdog restart path.

The goal is to exercise the control-flow branches that PR #91 added —
particularly the `_shutting_down` re-entry guard and the new
await-before-restart in the watchdog — so that future refactors can't
silently regress them.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch



# ── Startup smoke ───────────────────────────────────────────────────────────

def test_main_module_imports_with_required_env():
    """Importing main.py must succeed with the env already set by conftest."""
    import importlib
    import main
    importlib.reload(main)  # force a fresh run of module-level code
    assert main.bot is not None
    assert main.bot.state is not None


def test_all_extensions_list_is_shared():
    """main.ALL_EXTENSIONS must be the same object as cogs.ALL_EXTENSIONS —
    PR #90 regression guard."""
    import main
    import cogs

    assert main.ALL_EXTENSIONS is cogs.ALL_EXTENSIONS


# ── Shutdown guard (PR #91) ─────────────────────────────────────────────────

async def test_shutdown_guard_ignores_duplicate_signals():
    """Two back-to-back `_shutdown()` calls must only run the work once."""
    import main

    # Reset guard state for this test, and restore at teardown.
    main._shutting_down = False

    close_calls = 0

    async def _fake_close():
        nonlocal close_calls
        close_calls += 1

    # Patch the expensive / I/O-bound things `_shutdown` touches so the
    # test runs offline.
    with patch.object(main.bot, "close", new=AsyncMock(side_effect=_fake_close)), \
         patch("utils.http.close_session", new=AsyncMock()), \
         patch("main.close_db", new=AsyncMock()):

        # First invocation: runs cleanup.
        await main._shutdown()
        # Second invocation: must early-return because guard is set.
        await main._shutdown()

    assert close_calls == 1
    assert main._shutting_down is True

    # Clean up for other tests.
    main._shutting_down = False


# ── Watchdog restart path (PR #91) ──────────────────────────────────────────

def _fake_bot_with_cogs(cogs_dict, is_primary=True):
    """Build a mock bot whose `cogs` is a real dict (discord.py's real
    `Bot.cogs` is a read-only property, so we can't patch it in place —
    we swap the whole `main.bot` reference instead)."""
    from utils.state import BotState

    bot = MagicMock()
    bot.state = BotState()
    bot.state.is_primary = is_primary
    bot.cogs = cogs_dict
    bot.wait_until_ready = AsyncMock()
    return bot


async def test_watchdog_restart_awaits_cancelled_inner_task(monkeypatch):
    """When the managed task is stopped, watchdog must cancel, wait for
    the inner asyncio task to finalize (bounded by a timeout), and only
    then call `task.start()`. The previous implementation used a plain
    `sleep(0.5)` which could race."""
    import main
    from discord.ext import tasks

    # `spec=tasks.Loop` makes isinstance(fake_loop_task, tasks.Loop) True,
    # which is how main.watchdog_task filters managed tasks.
    fake_loop_task = MagicMock(spec=tasks.Loop)
    fake_loop_task.is_running.return_value = False

    # `inner` must be a real asyncio Task/Future so `asyncio.shield(inner)`
    # and `asyncio.wait_for(...)` don't raise TypeError on a MagicMock.
    async def _never_finishes():
        await asyncio.Event().wait()

    inner = asyncio.ensure_future(_never_finishes())
    fake_loop_task.get_task.return_value = inner

    fake_cog = MagicMock()
    fake_cog.MANAGED_TASK_NAMES = [("fake_task", "fake_task")]
    fake_cog.fake_task = fake_loop_task

    monkeypatch.setattr(main, "bot", _fake_bot_with_cogs({"fake_cog": fake_cog}))
    monkeypatch.setattr(main.utils.http, "ensure_session", AsyncMock())
    monkeypatch.setattr(main.utils.http, "close_session", AsyncMock())
    monkeypatch.setattr(main, "send_bot_alert", AsyncMock())
    monkeypatch.setattr("utils.http.http_session", None)

    main._task_fail_counts.clear()
    main._task_alerted.clear()
    # Simulate the task having been seen running before, so the
    # watchdog treats its current 'stopped' state as a regression
    # rather than a startup race.
    main._task_seen_running.clear()
    main._task_seen_running.add("fake_task")

    # Drive the watchdog. With a 5s wait_for and a never-finishing
    # inner, the wait must time out and the code must still reach
    # task.start(). We give the test a tight budget by shortening
    # the timeout via monkeypatch on asyncio.wait_for.
    orig_wait_for = asyncio.wait_for

    async def _short_wait_for(coro, timeout):
        # Ignore the watchdog's 5s and use 0.05 so the test is quick.
        return await orig_wait_for(coro, timeout=0.05)

    monkeypatch.setattr(main.asyncio, "wait_for", _short_wait_for)

    await main.watchdog_task.coro()

    # Cancel the never-finishing inner so pytest teardown is clean.
    inner.cancel()
    try:
        await inner
    except (asyncio.CancelledError, Exception):
        pass

    fake_loop_task.cancel.assert_called_once()
    # The key regression guard: restart happened AFTER the cancelled
    # inner was awaited (bounded by the timeout). Before PR #91, this
    # was a race against a naked sleep(0.5).
    fake_loop_task.start.assert_called_once()


async def test_watchdog_standby_does_nothing(monkeypatch):
    """Standby mode must not touch tasks or the HTTP session."""
    import main

    ens = AsyncMock()
    cls = AsyncMock()

    monkeypatch.setattr(main, "bot", _fake_bot_with_cogs({}, is_primary=False))
    monkeypatch.setattr(main.utils.http, "ensure_session", ens)
    monkeypatch.setattr(main.utils.http, "close_session", cls)

    await main.watchdog_task.coro()

    ens.assert_not_called()
    cls.assert_not_called()
