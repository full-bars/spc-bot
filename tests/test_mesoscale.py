"""
Tests for cogs/mesoscale.py — focused on the MD cancellation logic (#171 fix).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from cogs.mesoscale import MesoscaleCog


def _make_bot(active_mds=None, posted_mds=None, is_primary=True):
    bot = MagicMock()
    bot.state.is_primary = is_primary
    bot.state.active_mds = set(active_mds or [])
    bot.state.posted_mds = set(posted_mds or [])
    bot.state.last_post_times = {}
    bot.wait_until_ready = AsyncMock()
    channel = AsyncMock()
    bot.get_channel.return_value = channel
    return bot, channel


# ── MD Cancellation: empty index ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cancellation_fires_when_index_empty():
    """Active MD is cancelled when the SPC index returns empty (#171 regression)."""
    bot, channel = _make_bot(active_mds={"0100"})

    with patch("cogs.mesoscale.fetch_latest_md_numbers", AsyncMock(return_value=[])):
        cog = MesoscaleCog(bot)
        await cog.auto_post_md()

    channel.send.assert_called_once()
    embed = channel.send.call_args.kwargs["embed"]
    assert "100" in embed.title
    assert "Cancelled" in embed.title
    assert "0100" not in bot.state.active_mds


@pytest.mark.asyncio
async def test_no_cancellation_when_md_still_active():
    """Active MD is NOT cancelled when it's still in the current index."""
    bot, channel = _make_bot(active_mds={"0100"})

    with patch("cogs.mesoscale.fetch_latest_md_numbers", AsyncMock(return_value=["0100"])), \
         patch("cogs.mesoscale.fetch_md_details", AsyncMock(return_value=(None, None, False, None))):
        cog = MesoscaleCog(bot)
        await cog.auto_post_md()

    # channel.send may be called for new MD posting path, but not for cancellation
    for call in channel.send.call_args_list:
        embed = call.kwargs.get("embed") or (call.args[0] if call.args else None)
        if embed and hasattr(embed, "title"):
            assert "Cancelled" not in (embed.title or "")


@pytest.mark.asyncio
async def test_multiple_cancellations_when_index_empty():
    """All active MDs are cancelled when the index goes empty."""
    bot, channel = _make_bot(active_mds={"0100", "0101", "0102"})

    with patch("cogs.mesoscale.fetch_latest_md_numbers", AsyncMock(return_value=[])):
        cog = MesoscaleCog(bot)
        await cog.auto_post_md()

    assert channel.send.call_count == 3
    assert len(bot.state.active_mds) == 0


@pytest.mark.asyncio
async def test_partial_cancellation_when_some_mds_expire():
    """Only expired MDs get cancelled; active ones are spared."""
    bot, channel = _make_bot(active_mds={"0100", "0101"})

    with patch("cogs.mesoscale.fetch_latest_md_numbers", AsyncMock(return_value=["0101"])), \
         patch("cogs.mesoscale.fetch_md_details", AsyncMock(return_value=(None, None, False, None))):
        cog = MesoscaleCog(bot)
        await cog.auto_post_md()

    cancel_calls = [
        c for c in channel.send.call_args_list
        if "Cancelled" in (c.kwargs.get("embed", MagicMock()).title or "")
    ]
    assert len(cancel_calls) == 1
    assert "0100" not in bot.state.active_mds
    assert "0101" in bot.state.active_mds


# ── Lag protection ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_lag_protection_spares_newer_md():
    """An MD newer than anything on the index is spared (index lag guard)."""
    bot, channel = _make_bot(active_mds={"0101"})

    # Index has 0100; 0101 is newer so it should be spared
    with patch("cogs.mesoscale.fetch_latest_md_numbers", AsyncMock(return_value=["0100"])), \
         patch("cogs.mesoscale.fetch_md_details", AsyncMock(return_value=(None, None, False, None))):
        cog = MesoscaleCog(bot)
        await cog.auto_post_md()

    assert "0101" in bot.state.active_mds
    for call in channel.send.call_args_list:
        embed = call.kwargs.get("embed")
        if embed:
            assert "Cancelled" not in (embed.title or "")


@pytest.mark.asyncio
async def test_lag_protection_does_not_spare_older_md():
    """An MD older than the current max is cancelled even with lag guard active."""
    bot, channel = _make_bot(active_mds={"0099"})

    with patch("cogs.mesoscale.fetch_latest_md_numbers", AsyncMock(return_value=["0100"])), \
         patch("cogs.mesoscale.fetch_md_details", AsyncMock(return_value=(None, None, False, None))):
        cog = MesoscaleCog(bot)
        await cog.auto_post_md()

    assert "0099" not in bot.state.active_mds


@pytest.mark.asyncio
async def test_year_wraparound_spares_early_year_md():
    """MD 0001 is treated as newer than 9999 (year wraparound guard)."""
    bot, channel = _make_bot(active_mds={"0001"})

    with patch("cogs.mesoscale.fetch_latest_md_numbers", AsyncMock(return_value=["9999"])), \
         patch("cogs.mesoscale.fetch_md_details", AsyncMock(return_value=(None, None, False, None))):
        cog = MesoscaleCog(bot)
        await cog.auto_post_md()

    assert "0001" in bot.state.active_mds


# ── Standby guard ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_standby_does_not_post_cancellations():
    """Standby node skips the entire loop body."""
    bot, channel = _make_bot(active_mds={"0100"}, is_primary=False)

    with patch("cogs.mesoscale.fetch_latest_md_numbers", AsyncMock(return_value=[])):
        cog = MesoscaleCog(bot)
        await cog.auto_post_md()

    channel.send.assert_not_called()


# ── Discord error on cancellation send ────────────────────────────────────────

@pytest.mark.asyncio
async def test_cancellation_send_failure_re_adds_md():
    """If the Discord send fails, the MD is put back in active_mds."""
    import discord as _discord
    bot, channel = _make_bot(active_mds={"0100"})
    channel.send.side_effect = _discord.HTTPException(MagicMock(status=500), "server error")

    with patch("cogs.mesoscale.fetch_latest_md_numbers", AsyncMock(return_value=[])):
        cog = MesoscaleCog(bot)
        await cog.auto_post_md()

    assert "0100" in bot.state.active_mds


# ── post_md_now (iembot fast-path) ───────────────────────────────────────────

def _make_bot_for_post(posted_mds=None, active_mds=None):
    bot = MagicMock()
    bot.state.is_primary = True
    bot.state.posted_mds = set(posted_mds or [])
    bot.state.active_mds = set(active_mds or [])
    bot.state.auto_cache = {}
    bot.state.last_post_times = {}
    bot.cogs = {}
    bot.wait_until_ready = AsyncMock()
    channel = AsyncMock()
    bot.get_channel.return_value = channel
    return bot, channel


@pytest.mark.asyncio
async def test_post_md_now_dedup_skips_already_posted():
    """post_md_now returns immediately if the MD is already in posted_mds."""
    bot, channel = _make_bot_for_post(posted_mds={"0398"})
    cog = MesoscaleCog.__new__(MesoscaleCog)
    cog.bot = bot

    await cog.post_md_now("0398")

    channel.send.assert_not_called()


@pytest.mark.asyncio
async def test_post_md_now_sends_and_marks_posted():
    """post_md_now posts an embed and records the MD in state."""
    bot, channel = _make_bot_for_post()
    cog = MesoscaleCog.__new__(MesoscaleCog)
    cog.bot = bot

    with patch("cogs.mesoscale.fetch_md_details", AsyncMock(return_value=("http://img/mcd0398.png", "summary", False, "raw text"))), \
         patch("cogs.mesoscale.download_single_image", AsyncMock(return_value=(None, False, None))), \
         patch("cogs.mesoscale.extract_md_body", return_value="Discussion body"), \
         patch("cogs.mesoscale.add_posted_md", AsyncMock()), \
         patch("cogs.mesoscale.clean_md_text_for_discord", return_value="Discussion body"):
        await cog.post_md_now("398")

    channel.send.assert_called_once()
    assert "0398" in bot.state.posted_mds
    assert "0398" in bot.state.active_mds


@pytest.mark.asyncio
async def test_post_md_now_no_channel_returns_early():
    """post_md_now silently returns if the channel is not found."""
    bot, _ = _make_bot_for_post()
    bot.get_channel.return_value = None
    cog = MesoscaleCog.__new__(MesoscaleCog)
    cog.bot = bot

    await cog.post_md_now("0398")  # should not raise


@pytest.mark.asyncio
async def test_post_md_now_send_failure_does_not_mark_posted():
    """A Discord send failure must not mark the MD as posted — it must be retried next cycle."""
    import discord as _discord

    bot, channel = _make_bot_for_post()
    channel.send.side_effect = _discord.HTTPException(MagicMock(status=500), "server error")

    cog = MesoscaleCog.__new__(MesoscaleCog)
    cog.bot = bot

    with patch("cogs.mesoscale.fetch_md_details", AsyncMock(return_value=(None, None, False, None))), \
         patch("cogs.mesoscale.download_single_image", AsyncMock(return_value=(None, False, None))), \
         patch("cogs.mesoscale.extract_md_body", return_value=None), \
         patch("cogs.mesoscale.clean_md_text_for_discord", return_value=None):
        await cog.post_md_now("0398")  # must not raise

    assert "0398" not in bot.state.posted_mds
