"""
Pytest configuration.

Philosophy
----------
- Required environment variables are set before `config` is imported so
  module-level validation in `config.py` doesn't abort collection.
- Side-effecting fixtures are *opt-in*, not autouse. The previous
  conftest globally patched `asyncio.create_task` and `TaskBackoff`,
  which silently hid real failures (e.g. a background task that never
  started would look healthy to every test in the suite).
- A real `BotState`-backed bot fixture is provided for integration-style
  tests so attribute access against `bot.state` behaves like production
  rather than silently returning a `MagicMock`.
- An in-memory SQLite fixture is provided so `utils/db.py` can be
  exercised for real without touching the dev database.
"""

import asyncio
import os
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Env vars must be set before any `config` import ──────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_TEST_CACHE = tempfile.mkdtemp(prefix="spc_bot_test_")
os.environ.setdefault("DISCORD_TOKEN", "test-token-not-real")
os.environ.setdefault("SPC_CHANNEL_ID", "123456789")
os.environ.setdefault("MODELS_CHANNEL_ID", "987654321")
os.environ.setdefault("GUILD_ID", "111222333")
os.environ.setdefault("CACHE_DIR", _TEST_CACHE)
os.environ.setdefault("LOG_FILE", os.path.join(_TEST_CACHE, "spc_bot_test.log"))
os.environ.setdefault("FAILOVER_TOKEN", "test-failover-token-not-real")


# ── Cleanup: close global DB / HTTP handles after every test ────────────────
@pytest.fixture(autouse=True)
async def _cleanup_global_resources():
    """Close the module-level DB connection and aiohttp session between tests.

    This is the only autouse fixture. It only releases resources — it does
    not mutate behavior of the system under test.
    """
    yield
    try:
        from utils.db import close_db
        from utils.http import close_session

        await asyncio.sleep(0)  # let any just-scheduled tasks settle
        await close_db()
        await close_session()
    except Exception:
        # Cleanup must never fail the test that succeeded.
        pass


# ── Opt-in fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def suppress_create_task():
    """Silence `asyncio.create_task` for one test.

    Use ONLY when a test triggers a fire-and-forget background task you
    cannot otherwise await. Overusing this masks real bugs, which is why
    it is no longer autouse.
    """
    with patch("asyncio.create_task", return_value=MagicMock()):
        yield


@pytest.fixture
def stub_task_backoff(monkeypatch):
    """Stub `utils.backoff.TaskBackoff` for one test.

    Replaces the real class with a mock whose `should_skip` is always
    False and whose `failure`/`success` are no-ops. Use when a test
    exercises a loop task body and the backoff dance is irrelevant.
    """
    mock_backoff = MagicMock()
    mock_backoff.failure = AsyncMock()
    mock_backoff.success = MagicMock()
    mock_backoff.should_skip = MagicMock(return_value=False)
    monkeypatch.setattr(
        "utils.backoff.TaskBackoff", MagicMock(return_value=mock_backoff)
    )
    return mock_backoff


@pytest.fixture
def bot_state():
    """Fresh, real `BotState` (not a MagicMock)."""
    from utils.state import BotState

    return BotState()


@pytest.fixture
def fake_bot(bot_state):
    """A test-grade `commands.Bot` stand-in.

    Key properties vs. `MagicMock()`:
    - `bot.state` is a real `BotState` so attribute typos raise
      AttributeError instead of silently returning another Mock.
    - `bot.cogs` is a real dict.
    - Discord-I/O methods (`get_channel`, `wait_until_ready`, `tree`)
      are still mocked because tests cannot speak to Discord.
    """
    bot = MagicMock()
    # Real attributes — these must NOT be MagicMocks.
    bot.state = bot_state
    bot.cogs = {}
    bot.guilds = []
    bot.latency = 0.05
    # Async/coroutine stubs.
    bot.wait_until_ready = AsyncMock()
    bot.get_channel = MagicMock(return_value=AsyncMock())
    return bot


@pytest.fixture
def fake_interaction(fake_bot):
    """A mock Discord interaction bound to `fake_bot`."""
    interaction = MagicMock()
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.client = fake_bot
    return interaction


@pytest.fixture
async def isolated_db(tmp_path, monkeypatch):
    """Point `utils.db` at a fresh SQLite file for one test.

    The global `_db` connection is reset before and after the test so
    the production code path is exercised (WAL, pragmas, schema
    creation, retry logic) without touching the real cache DB.
    """
    from utils import db as db_mod

    # Reset any connection state left over from earlier tests.
    if db_mod._db is not None:
        await db_mod.close_db()

    db_file = tmp_path / "test_bot_state.db"
    monkeypatch.setattr(db_mod, "DB_PATH", str(db_file))

    conn = await db_mod.get_db()
    yield conn

    await db_mod.close_db()
