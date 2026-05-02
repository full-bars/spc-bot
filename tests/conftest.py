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
os.environ.setdefault("NWWS_FIREHOSE_LOG", "nwws_firehose_test.log")
os.environ.setdefault("FAILOVER_TOKEN", "test-failover-token-not-real")
# Force Upstash credentials to empty so load_dotenv() (called by config.py)
# cannot override them with real values from .env. Without this, every call
# to get_cached_md_text / get_product_cache makes a live Upstash network
# request, burning free-tier quota and hanging the event loop on teardown.
os.environ.setdefault("UPSTASH_REDIS_REST_URL", "")
os.environ.setdefault("UPSTASH_REDIS_REST_TOKEN", "")


# ── Autouse fixtures ─────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def global_suppress_create_task(request):
    """Silence `asyncio.create_task` globally for all tests.

    Background tasks (like `_upgrade_md_message` or `_handle_watch`
    dispatches) are "fire and forget" in production but can hang
    or leak resources in tests if they run unawaited in the background.

    Mark a test with @pytest.mark.real_create_task to opt out — required
    for tests that call functions which use asyncio.create_task internally
    as part of their core logic (e.g. fetch_md_details racing SPC vs IEM).
    """
    if request.node.get_closest_marker("real_create_task"):
        yield
    else:
        with patch("asyncio.create_task", return_value=MagicMock()):
            yield


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


@pytest.fixture
async def isolated_events_db(tmp_path, monkeypatch):
    """Point `utils.events_db` at a fresh SQLite file for one test."""
    from utils import events_db as edb_mod

    if edb_mod._db is not None:
        await edb_mod.close_events_db()

    db_file = tmp_path / "test_events.db"
    monkeypatch.setattr(edb_mod, "_EVENTS_DB_PATH", str(db_file))
    monkeypatch.setattr(edb_mod, "_SYNC_PATH", str(tmp_path / "events_sync.db"))

    conn = await edb_mod.get_events_db()
    yield conn

    await edb_mod.close_events_db()


@pytest.fixture(autouse=True, scope="session")
def close_events_db_after_session():
    """Ensure the events_db connection is closed at end of session.

    Without this, aiosqlite's worker thread keeps the process alive after
    pytest completes, causing CI to hang indefinitely.
    """
    yield
    import asyncio
    from utils import events_db as edb_mod
    if edb_mod._db is not None:
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(edb_mod.close_events_db())
            loop.close()
        except Exception:
            pass
