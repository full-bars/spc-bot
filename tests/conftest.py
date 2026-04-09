# tests/conftest.py
"""
Pytest configuration — set up environment variables before config.py imports.
"""

import os
import sys

# Ensure the project root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Set required env vars before any config imports happen
os.environ.setdefault("DISCORD_TOKEN", "test-token-not-real")
os.environ.setdefault("SPC_CHANNEL_ID", "123456789")
os.environ.setdefault("MODELS_CHANNEL_ID", "987654321")
os.environ.setdefault("GUILD_ID", "111222333")
os.environ.setdefault("CACHE_DIR", "/tmp/spc_bot_test_cache")
os.environ.setdefault("LOG_FILE", "/tmp/spc_bot_test.log")
