# tests/conftest.py
"""
Pytest configuration — set up environment variables before config.py imports.
"""

import os
import sys
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock

# Ensure the project root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Set required env vars before any config imports happen
os.environ.setdefault("DISCORD_TOKEN", "test-token-not-real")
os.environ.setdefault("SPC_CHANNEL_ID", "123456789")
os.environ.setdefault("MODELS_CHANNEL_ID", "987654321")
os.environ.setdefault("GUILD_ID", "111222333")
os.environ.setdefault("CACHE_DIR", "/tmp/spc_bot_test_cache")
os.environ.setdefault("LOG_FILE", "/tmp/spc_bot_test.log")

@pytest.fixture(autouse=True)
async def cleanup_resources():
    """Ensure DB and HTTP sessions are closed after every test."""
    yield
    try:
        from utils.db import close_db
        from utils.http import close_session
        
        # Give short time for background tasks to start so they can be handled by loop closure
        await asyncio.sleep(0.01)
        
        await close_db()
        await close_session()
    except Exception:
        pass

@pytest.fixture(autouse=True)
def patch_task_backoff(monkeypatch):
    """Patch TaskBackoff to prevent it from starting background watchdog tasks during tests."""
    mock_backoff = MagicMock()
    mock_backoff.failure = AsyncMock()
    mock_backoff.success = MagicMock()
    mock_backoff.should_skip = MagicMock(return_value=False)
    monkeypatch.setattr("utils.backoff.TaskBackoff", MagicMock(return_value=mock_backoff))
