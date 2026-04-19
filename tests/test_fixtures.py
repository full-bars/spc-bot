"""Smoke tests for the shared conftest fixtures.

If these regress, every other test becomes suspect — keep them simple
and direct.
"""

import pytest


def test_fake_bot_state_is_real(fake_bot):
    """`fake_bot.state` must be a real BotState so attribute typos raise."""
    from utils.state import BotState

    assert isinstance(fake_bot.state, BotState)
    # Attribute that exists — should work.
    assert isinstance(fake_bot.state.posted_mds, set)
    # Attribute that does not exist — must raise, not return a Mock.
    with pytest.raises(AttributeError):
        _ = fake_bot.state.this_attr_does_not_exist


def test_fake_bot_cogs_is_real_dict(fake_bot):
    assert fake_bot.cogs == {}
    fake_bot.cogs["marker"] = "x"
    assert fake_bot.cogs["marker"] == "x"


async def test_isolated_db_roundtrip(isolated_db):
    """The fixture should hand back a fresh, writable sqlite connection
    with the schema already created."""
    from utils import db

    await db.set_state("smoke", "ok")
    assert await db.get_state("smoke") == "ok"
    await db.delete_state("smoke")
    assert await db.get_state("smoke") is None


async def test_isolated_db_per_test_isolation(isolated_db):
    """Writes from the previous test must not leak into this one."""
    from utils import db

    assert await db.get_state("smoke") is None
