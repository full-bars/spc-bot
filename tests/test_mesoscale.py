"""Unit tests for cogs.mesoscale — SPC MD monitoring and IEM fallbacks."""

import json as _json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from cogs.mesoscale import MesoscaleCog, fetch_latest_md_numbers


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_bot(posted_mds=None, active_mds=None, is_primary=True):
    bot = MagicMock()
    bot.state.is_primary = is_primary
    bot.state.posted_mds = set(posted_mds or [])
    bot.state.active_mds = set(active_mds or [])
    bot.state.last_post_times = {}
    bot.wait_until_ready = AsyncMock()
    channel = AsyncMock()
    bot.get_channel.return_value = channel
    return bot, channel


# ── MD Cancellations ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cancellation_fires_when_index_empty():
    """Active MD is cancelled when the SPC index returns empty (#171 regression)."""
    bot, channel = _make_bot(active_mds={"0100"})

    with patch("cogs.mesoscale.fetch_latest_md_numbers", AsyncMock(return_value=([], False))):
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

    with patch("cogs.mesoscale.fetch_latest_md_numbers", AsyncMock(return_value=(["0100"], False))), \
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

    with patch("cogs.mesoscale.fetch_latest_md_numbers", AsyncMock(return_value=([], False))):
        cog = MesoscaleCog(bot)
        await cog.auto_post_md()

    assert channel.send.call_count == 3

    assert len(bot.state.active_mds) == 0


@pytest.mark.asyncio
async def test_partial_cancellation_when_some_mds_expire():
    """Only expired MDs get cancelled; active ones are spared."""
    bot, channel = _make_bot(active_mds={"0100", "0101"})

    with patch("cogs.mesoscale.fetch_latest_md_numbers", AsyncMock(return_value=(["0101"], False))), \
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


@pytest.mark.asyncio
async def test_cancellation_skipped_in_fallback():
    """Approach 2: Cancellations are skipped entirely when in fallback mode."""
    bot, channel = _make_bot(active_mds={"0100"})

    # Even though index is empty, if is_fallback=True, we don't cancel.
    with patch("cogs.mesoscale.fetch_latest_md_numbers", AsyncMock(return_value=([], True))):
        cog = MesoscaleCog(bot)
        await cog.auto_post_md()

    channel.send.assert_not_called()
    assert "0100" in bot.state.active_mds


# ── Lag protection ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_lag_protection_spares_newer_md():
    """An MD newer than anything on the index is spared (index lag guard)."""
    bot, channel = _make_bot(active_mds={"0101"})

    # Index has 0100; 0101 is newer so it should be spared
    with patch("cogs.mesoscale.fetch_latest_md_numbers", AsyncMock(return_value=(["0100"], False))), \
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

    with patch("cogs.mesoscale.fetch_latest_md_numbers", AsyncMock(return_value=(["0100"], False))), \
         patch("cogs.mesoscale.fetch_md_details", AsyncMock(return_value=(None, None, False, None))):
        cog = MesoscaleCog(bot)
        await cog.auto_post_md()

    assert "0099" not in bot.state.active_mds


@pytest.mark.asyncio
async def test_year_wraparound_spares_early_year_md():
    """MD 0001 is treated as newer than 9999 (year wraparound guard)."""
    bot, channel = _make_bot(active_mds={"0001"})

    with patch("cogs.mesoscale.fetch_latest_md_numbers", AsyncMock(return_value=(["9999"], False))), \
         patch("cogs.mesoscale.fetch_md_details", AsyncMock(return_value=(None, None, False, None))):
        cog = MesoscaleCog(bot)
        await cog.auto_post_md()

    assert "0001" in bot.state.active_mds


# ── Standby guard ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_standby_does_not_post_cancellations():
    """Standby node skips the entire loop body."""
    bot, channel = _make_bot(active_mds={"0100"}, is_primary=False)

    with patch("cogs.mesoscale.fetch_latest_md_numbers", AsyncMock(return_value=([], False))):
        cog = MesoscaleCog(bot)
        await cog.auto_post_md()

    channel.send.assert_not_called()


# ── Error handling ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cancellation_send_failure_re_adds_md():
    """If Discord send fails, the MD remains in active_mds for retry."""
    bot, channel = _make_bot(active_mds={"0100"})
    channel.send.side_effect = discord.HTTPException(MagicMock(), "failed")

    with patch("cogs.mesoscale.fetch_latest_md_numbers", AsyncMock(return_value=([], False))):
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
    """post_md_now returns immediately if MD is in posted_mds."""
    bot, channel = _make_bot_for_post(posted_mds={"0100"})
    cog = MesoscaleCog.__new__(MesoscaleCog)
    cog.bot = bot

    await cog.post_md_now("0100")

    channel.send.assert_not_called()


@pytest.mark.asyncio
async def test_post_md_now_sends_and_marks_posted():
    """Successful post adds MD to posted_mds and active_mds."""
    bot, channel = _make_bot_for_post()
    cog = MesoscaleCog.__new__(MesoscaleCog)
    cog.bot = bot
    cog._pending_tasks = set()

    # Use a dummy text to trigger upgrade task creation
    dummy_text = "SPC MD 0100"
    
    with patch("cogs.mesoscale.fetch_md_details", AsyncMock(return_value=("img", dummy_text, True, "/path"))), \
         patch("cogs.mesoscale.download_single_image", AsyncMock(return_value=(None, False, None))), \
         patch("cogs.mesoscale.extract_md_body", return_value="Discussion body"), \
         patch("cogs.mesoscale.add_posted_md", AsyncMock()), \
         patch("cogs.mesoscale.clean_md_text_for_discord", return_value="Discussion body"):
        await cog.post_md_now("100")

    channel.send.assert_called_once()
    assert "0100" in bot.state.posted_mds
    assert "0100" in bot.state.active_mds


@pytest.mark.asyncio
async def test_post_md_now_no_channel_returns_early():
    """Method returns gracefully if channel is missing."""
    bot, _ = _make_bot_for_post()
    bot.get_channel.return_value = None
    cog = MesoscaleCog.__new__(MesoscaleCog)
    cog.bot = bot

    await cog.post_md_now("0100")  # should not raise


@pytest.mark.asyncio
async def test_post_md_now_send_failure_does_not_mark_posted():
    """If send fails, MD is not added to posted sets."""
    bot, channel = _make_bot_for_post()
    channel.send.side_effect = Exception("failed")
    cog = MesoscaleCog.__new__(MesoscaleCog)
    cog.bot = bot

    with patch("cogs.mesoscale.fetch_md_details", AsyncMock(return_value=("img", "sum", True, "/path"))):
        await cog.post_md_now("0398")  # must not raise

    assert "0398" not in bot.state.posted_mds


# ── fetch_latest_md_numbers — IEM fallback parse path ────────────────────────

@pytest.mark.asyncio
async def test_fetch_md_numbers_parses_iem_fallback_text():
    """When SPC index is empty, IEM retrieve.py text is parsed for MD numbers."""
    iem_text = (
        "MESOSCALE DISCUSSION 0412\n"
        "Some content...\n\n"
        "MESOSCALE DISCUSSION 0413\n"
        "More content...\n"
    )

    with patch("cogs.mesoscale.http_get_text", AsyncMock(side_effect=[
        # First call: SPC index (empty → triggers fallback)
        None,
        # Second call: IEM retrieve.py
        iem_text,
    ])), \
    patch("cogs.mesoscale.http_head_meta", AsyncMock(return_value=None)):
        result, is_fallback = await fetch_latest_md_numbers(fresh=True)

    assert "0412" in result
    assert "0413" in result
    assert is_fallback is True


@pytest.mark.asyncio
async def test_fetch_md_numbers_returns_none_when_both_sources_fail():
    """If SPC index and IEM fallback both fail, None is returned (not empty list)."""
    with patch("cogs.mesoscale.http_get_text", AsyncMock(return_value=None)), \
         patch("cogs.mesoscale.http_head_meta", AsyncMock(return_value=None)):
        result, is_fallback = await fetch_latest_md_numbers(fresh=True)

    assert result is None
    assert is_fallback is True


@pytest.mark.asyncio
async def test_fetch_md_numbers_zero_pads_md_numbers():
    """MD numbers from IEM are zero-padded to 4 digits."""
    iem_text = "MESOSCALE DISCUSSION 42\nContent."

    with patch("cogs.mesoscale.http_get_text", AsyncMock(side_effect=[None, iem_text])), \
         patch("cogs.mesoscale.http_head_meta", AsyncMock(return_value=None)):
        result, is_fallback = await fetch_latest_md_numbers(fresh=True)

    assert "0042" in result
    assert is_fallback is True


@pytest.mark.asyncio
async def test_fetch_md_numbers_deduplicates_repeated_numbers():
    """Duplicate MD numbers in IEM text are returned once each."""
    iem_text = (
        "MESOSCALE DISCUSSION 0100\n"
        "MESOSCALE DISCUSSION 0100\n"  # duplicate
        "MESOSCALE DISCUSSION 0101\n"
    )

    with patch("cogs.mesoscale.http_get_text", AsyncMock(side_effect=[None, iem_text])), \
         patch("cogs.mesoscale.http_head_meta", AsyncMock(return_value=None)):
        result, is_fallback = await fetch_latest_md_numbers(fresh=True)

    assert result.count("0100") == 1
    assert "0101" in result
    assert is_fallback is True


@pytest.mark.asyncio
async def test_fetch_md_numbers_spc_path_uses_head_cache():
    """Unchanged SPC index (HEAD match) returns empty list without full fetch."""
    import cogs.mesoscale as md_mod

    # Seed a cached HEAD so the comparison can match
    md_mod._md_index_head = {"etag": '"abc123"', "last_modified": "Thu, 01 Jan 2026 00:00:00 GMT",
                              "content_length": "12345"}

    with patch("cogs.mesoscale.http_head_meta", AsyncMock(return_value={
        "etag": '"abc123"',
        "last_modified": "Thu, 01 Jan 2026 00:00:00 GMT",
        "content_length": "12345",
    })), patch("cogs.mesoscale.http_get_text") as mock_get:
        result, is_fallback = await fetch_latest_md_numbers(fresh=False)

    assert result == []
    assert is_fallback is False
    mock_get.assert_not_called()

    # Reset module state for other tests
    md_mod._md_index_head = {}
