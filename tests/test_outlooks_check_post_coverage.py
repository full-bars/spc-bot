"""Coverage round 12: outlook day-posting flow (partial-update logic, posting path)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch


from cogs import outlooks
from utils.state import BotState


def _bot_state():
    return BotState()


async def test_check_and_post_day_in_flight_returns():
    state = _bot_state()
    state.hashes._in_flight.add("day1")
    with patch("cogs.outlooks.get_spc_urls", new_callable=AsyncMock) as mock_urls:
        await outlooks.check_and_post_day(MagicMock(), 1, state)

    mock_urls.assert_not_awaited()
    assert "day1" in state.hashes._in_flight


async def test_check_and_post_day_unchanged_fallback_skips():
    state = _bot_state()
    urls = ["http://fallback/day1otlk.gif"]
    with patch("cogs.outlooks.get_spc_urls", new_callable=AsyncMock, return_value=urls), patch(
        "cogs.outlooks.SPC_URLS_FALLBACK", {"1": urls}
    ):
        state.last_posted_urls["day1"] = urls
        await outlooks.check_and_post_day(MagicMock(), 1, state)

    # Nothing posted: last_post_times unchanged.
    assert state.last_post_times.get("day1") is None


async def test_check_and_post_day_no_updates_waits():
    state = _bot_state()
    with patch("cogs.outlooks.get_spc_urls", new_callable=AsyncMock, return_value=["u1"]), patch(
        "cogs.outlooks.check_partial_updates_parallel",
        new_callable=AsyncMock,
        return_value=(0, 2, {}),
    ):
        await outlooks.check_and_post_day(MagicMock(), 1, state)

    assert state.last_post_times.get("day1") is None


async def test_check_and_post_day_partial_timeout_clears():
    state = _bot_state()
    state.partial_update_state["day1"] = {
        "start_time": datetime.now(timezone.utc) - timedelta(minutes=25),
        "downloaded_data": {},
    }
    with patch("cogs.outlooks.get_spc_urls", new_callable=AsyncMock, return_value=["u1"]), patch(
        "cogs.outlooks.check_partial_updates_parallel",
        new_callable=AsyncMock,
        return_value=(0, 2, {}),
    ):
        await outlooks.check_and_post_day(MagicMock(), 1, state)

    assert "day1" not in state.partial_update_state


async def test_check_and_post_day_partial_stores_for_day1():
    state = _bot_state()
    with patch("cogs.outlooks.get_spc_urls", new_callable=AsyncMock, return_value=["u1"]), patch(
        "cogs.outlooks.check_partial_updates_parallel",
        new_callable=AsyncMock,
        return_value=(1, 2, {"u1": (b"x", 200)}),
    ):
        await outlooks.check_and_post_day(MagicMock(), 1, state)

    # Day 1 partial update enters the waiting cycle without posting.
    assert "day1" in state.partial_update_state
    assert state.last_post_times.get("day1") is None


async def test_check_and_post_day_day2_partial_timeout_posts(tmp_path):
    # Non-day-1 days wait up to 20 minutes on a partial update, then post.
    state = _bot_state()
    f1 = tmp_path / "f1.gif"
    f1.write_bytes(b"GIF")
    state.partial_update_state["day2"] = {
        "start_time": datetime.now(timezone.utc) - timedelta(minutes=21),
        "downloaded_data": {},
    }
    with patch("cogs.outlooks.get_spc_urls", new_callable=AsyncMock, return_value=["u1"]), patch(
        "cogs.outlooks.check_partial_updates_parallel",
        new_callable=AsyncMock,
        return_value=(1, 2, {"u1": (b"x", 200)}),
    ), patch(
        "cogs.outlooks.save_downloaded_images", new_callable=AsyncMock, return_value=[str(f1)]
    ), patch("cogs.outlooks.safe_send", new_callable=AsyncMock, return_value=MagicMock()), patch(
        "cogs.outlooks.safe_create_thread", new_callable=AsyncMock, return_value=None
    ), patch("cogs.outlooks.archive_outlook_version", new_callable=AsyncMock), patch(
        "cogs.ai_summaries.autopost_outlook_summary", new_callable=AsyncMock
    ), patch("cogs.ai_summaries._fetch_outlook_text", new_callable=AsyncMock), patch(
        "cogs.outlooks.set_posted_urls", new_callable=AsyncMock
    ):
        await outlooks.check_and_post_day(MagicMock(), 2, state)

    assert state.last_post_times.get("day2") is not None
    assert "day2" not in state.partial_update_state


async def test_check_and_post_day_send_error_cleans_inflight(tmp_path):
    state = _bot_state()
    f1 = tmp_path / "f1.gif"
    f1.write_bytes(b"GIF")
    with patch("cogs.outlooks.get_spc_urls", new_callable=AsyncMock, return_value=["u1"]), patch(
        "cogs.outlooks.check_partial_updates_parallel",
        new_callable=AsyncMock,
        return_value=(1, 1, {"u1": (b"x", 200)}),
    ), patch(
        "cogs.outlooks.save_downloaded_images", new_callable=AsyncMock, return_value=[str(f1)]
    ), patch("cogs.outlooks.safe_send", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
        await outlooks.check_and_post_day(MagicMock(), 1, state)

    # The finally block must still release the in-flight guard.
    assert "day1" not in state.hashes._in_flight


async def test_check_and_post_day_day1_posts_after_five_minutes(tmp_path):
    state = _bot_state()
    f1 = tmp_path / "f1.gif"
    f1.write_bytes(b"GIF")
    state.partial_update_state["day1"] = {
        "start_time": datetime.now(timezone.utc) - timedelta(minutes=6),
        "downloaded_data": {},
    }
    with patch("cogs.outlooks.get_spc_urls", new_callable=AsyncMock, return_value=["u1"]), patch(
        "cogs.outlooks.check_partial_updates_parallel",
        new_callable=AsyncMock,
        return_value=(1, 2, {"u1": (b"x", 200)}),
    ), patch(
        "cogs.outlooks.save_downloaded_images", new_callable=AsyncMock, return_value=[str(f1)]
    ), patch("cogs.outlooks.safe_send", new_callable=AsyncMock, return_value=MagicMock()), patch(
        "cogs.outlooks.safe_create_thread", new_callable=AsyncMock, return_value=None
    ), patch("cogs.outlooks.archive_outlook_version", new_callable=AsyncMock), patch(
        "cogs.ai_summaries.autopost_outlook_summary", new_callable=AsyncMock
    ), patch("cogs.ai_summaries._fetch_outlook_text", new_callable=AsyncMock), patch(
        "cogs.outlooks.set_posted_urls", new_callable=AsyncMock
    ):
        await outlooks.check_and_post_day(MagicMock(), 1, state)

    assert state.last_post_times.get("day1") is not None
    assert "day1" not in state.partial_update_state


async def test_check_and_post_day_all_ready_posts_full(tmp_path):
    state = _bot_state()
    f1 = tmp_path / "f1.gif"
    f2 = tmp_path / "f2.gif"
    f1.write_bytes(b"GIF1")
    f2.write_bytes(b"GIF2")
    with patch(
        "cogs.outlooks.get_spc_urls", new_callable=AsyncMock, return_value=["u1", "u2"]
    ), patch(
        "cogs.outlooks.check_partial_updates_parallel",
        new_callable=AsyncMock,
        return_value=(2, 2, {"u1": (b"x", 200), "u2": (b"y", 200)}),
    ), patch(
        "cogs.outlooks.save_downloaded_images",
        new_callable=AsyncMock,
        return_value=[str(f1), str(f2)],
    ), patch("cogs.outlooks.safe_send", new_callable=AsyncMock, return_value=MagicMock()), patch(
        "cogs.outlooks.safe_create_thread", new_callable=AsyncMock, return_value=None
    ), patch("cogs.outlooks.archive_outlook_version", new_callable=AsyncMock), patch(
        "cogs.ai_summaries.autopost_outlook_summary", new_callable=AsyncMock
    ), patch("cogs.ai_summaries._fetch_outlook_text", new_callable=AsyncMock), patch(
        "cogs.outlooks.set_posted_urls", new_callable=AsyncMock
    ) as mock_set_urls:
        await outlooks.check_and_post_day(MagicMock(), 1, state)

    assert state.last_post_times.get("day1") is not None
    assert state.last_posted_urls.get("day1") == ["u1", "u2"]
    assert "day1" not in state.hashes._in_flight  # cleaned in finally
    mock_set_urls.assert_awaited_once_with("day1", ["u1", "u2"])
