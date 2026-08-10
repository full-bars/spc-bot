"""Coverage round 3: ai_summaries pure-logic + HTTP-mocked paths (no network)."""

import asyncio
import json
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import discord

from cogs.ai_summaries import (
    AISummariesCog,
    RegionalAnalysisView,
    _get_regions_from_redis,
    _resolve_message_thread,
    autopost_md_summary,
    autopost_outlook_summary,
    autopost_sounding_summary,
    ensure_md_summary,
    ensure_outlook_summary,
    ensure_sounding_summary,
)

# ── RegionalAnalysisView ─────────────────────────────────────────────────────


def _region(name):
    return {
        "region": name,
        "favorable_factors": "FF",
        "fail_modes": "FM",
        "hazards_mode": "HM",
        "timing": "T",
        "confidence": "C",
    }


def test_region_view_embed_first_page():
    view = RegionalAnalysisView("1", [_region("NORTH"), _region("SOUTH")], current_page=0)
    embed = view._create_embed()

    assert "Day 1" in embed.title
    assert "Region 1 of 2: NORTH" in embed.description
    # Prev disabled at page 0, next enabled.
    assert view.children[0].disabled is True
    assert view.children[1].disabled is False


def test_region_view_embed_last_page_truncates_long_fields():
    long_region = _region("LONG")
    long_region["timing"] = "x" * 1100
    view = RegionalAnalysisView("2", [_region("A"), long_region], current_page=1)
    embed = view._create_embed()

    assert "Region 2 of 2: LONG" in embed.description
    assert view.children[0].disabled is False
    assert view.children[1].disabled is True
    # Long values are truncated to 1021 chars + "...".
    timing_field = [f for f in embed.fields if f.name == "Timing"][0]
    assert len(timing_field.value) == 1024
    assert timing_field.value.endswith("...")


# ── Region cache ─────────────────────────────────────────────────────────────


async def test_get_regions_from_redis_returns_parsed():
    with patch("utils.state_store.get_product_cache", new_callable=AsyncMock) as mock_cache:
        mock_cache.return_value = json.dumps([{"region": "A"}])
        assert await _get_regions_from_redis("1") == [{"region": "A"}]


async def test_get_regions_from_redis_corrupt_returns_none():
    with patch("utils.state_store.get_product_cache", new_callable=AsyncMock) as mock_cache:
        mock_cache.return_value = "not-json"
        assert await _get_regions_from_redis("1") is None


# ── MD summaries ─────────────────────────────────────────────────────────────


async def test_ensure_md_summary_cache_hit():
    redis = AsyncMock()
    with patch("utils.state_store.get_product_cache", new_callable=AsyncMock) as mock_cache, patch(
        "utils.state_store._get_redis_client", return_value=redis
    ), patch("utils.ai.summarize_md", new_callable=AsyncMock) as mock_summarize:
        mock_cache.return_value = "cached summary"

        result = await ensure_md_summary("1234")

    assert result == "cached summary"
    mock_summarize.assert_not_awaited()
    redis.incr.assert_awaited_once()


async def test_ensure_md_summary_generates_and_caches():
    redis = AsyncMock()
    with patch("utils.state_store.get_product_cache", new_callable=AsyncMock) as mock_cache, patch(
        "utils.state_store.set_product_cache", new_callable=AsyncMock
    ) as mock_set, patch("utils.state_store._get_redis_client", return_value=redis), patch(
        "utils.ai.summarize_md", new_callable=AsyncMock
    ) as mock_summarize:
        mock_cache.side_effect = [None, "RAW MD TEXT"]
        mock_summarize.return_value = "MD summary"

        result = await ensure_md_summary("1234")

    assert result == "MD summary"
    mock_summarize.assert_awaited_once_with("RAW MD TEXT")
    mock_set.assert_awaited_once_with("ai_summary_md_1234", "MD summary", ttl=86400 * 3)


async def test_ensure_md_summary_fetches_html_when_no_raw():
    with patch("utils.state_store.get_product_cache", new_callable=AsyncMock) as mock_cache, patch(
        "utils.state_store._get_redis_client", return_value=None
    ), patch("cogs.ai_summaries.http_get_text", new_callable=AsyncMock) as mock_text, patch(
        "utils.ai.summarize_md", new_callable=AsyncMock
    ) as mock_summarize:
        mock_cache.side_effect = [None, None]
        mock_text.return_value = "<html><pre>RAW <b>MD</b> TEXT</pre></html>"
        mock_summarize.return_value = "MD summary"

        result = await ensure_md_summary("1234")

    assert result == "MD summary"
    mock_summarize.assert_awaited_once_with("RAW MD TEXT")


async def test_ensure_md_summary_no_raw_returns_none():
    with patch("utils.state_store.get_product_cache", new_callable=AsyncMock) as mock_cache, patch(
        "utils.state_store._get_redis_client", return_value=None
    ), patch("cogs.ai_summaries.http_get_text", new_callable=AsyncMock) as mock_text:
        mock_cache.side_effect = [None, None]
        mock_text.return_value = None

        assert await ensure_md_summary("1234") is None


# ── Outlook text / summary ───────────────────────────────────────────────────


async def test_fetch_outlook_text_txt():
    with patch("cogs.ai_summaries.http_get_text", new_callable=AsyncMock) as mock_text:
        mock_text.return_value = "ZCZC SPCSWODY1 ALL\n...TEXT..."
        from cogs.ai_summaries import _fetch_outlook_text

        raw = await _fetch_outlook_text("1")

    assert raw == "ZCZC SPCSWODY1 ALL\n...TEXT..."


async def test_fetch_outlook_text_html_pre():
    with patch("cogs.ai_summaries.http_get_text", new_callable=AsyncMock) as mock_text:
        mock_text.return_value = "<html><pre>RAW <a href='x'>HTML</a></pre></html>"
        from cogs.ai_summaries import _fetch_outlook_text

        raw = await _fetch_outlook_text("48")

    assert raw == "RAW HTML"


async def test_fetch_outlook_text_unknown_day():
    with patch("cogs.ai_summaries.http_get_text", new_callable=AsyncMock) as mock_text:
        from cogs.ai_summaries import _fetch_outlook_text

        assert await _fetch_outlook_text("9") is None
    mock_text.assert_not_awaited()


async def test_ensure_outlook_summary_cached():
    regions = [{"region": "A"}]
    with patch("utils.state_store.get_product_cache", new_callable=AsyncMock) as mock_cache, patch(
        "utils.state_store._get_redis_client", return_value=None
    ), patch(
        "utils.change_detection.calculate_hash_bytes", return_value="a" * 32
    ) as mock_hash, patch("utils.ai.summarize_outlook", new_callable=AsyncMock) as mock_sum:
        mock_cache.return_value = json.dumps(regions)

        result = await ensure_outlook_summary("1", raw_text="RAW")

    assert result == regions
    mock_sum.assert_not_awaited()
    mock_hash.assert_called_once_with(b"RAW")


async def test_ensure_outlook_summary_generates():
    regions = [{"region": "A"}]
    with patch("utils.state_store.get_product_cache", new_callable=AsyncMock) as mock_cache, patch(
        "utils.state_store.set_product_cache", new_callable=AsyncMock
    ) as mock_set, patch("utils.state_store._get_redis_client", return_value=None), patch(
        "utils.change_detection.calculate_hash_bytes", return_value="a" * 32
    ), patch("utils.ai.summarize_outlook", new_callable=AsyncMock) as mock_sum:
        mock_cache.return_value = None
        mock_sum.return_value = regions

        result = await ensure_outlook_summary("1", raw_text="RAW")

    assert result == regions
    assert mock_set.await_count == 2


async def test_ensure_outlook_summary_no_raw_returns_none():
    with patch(
        "cogs.ai_summaries._fetch_outlook_text", new_callable=AsyncMock
    ) as mock_fetch, patch("utils.change_detection.calculate_hash_bytes", return_value="a" * 32):
        mock_fetch.return_value = None
        assert await ensure_outlook_summary("1") is None


# ── Thread resolution ────────────────────────────────────────────────────────


async def test_resolve_message_thread_none_message():
    assert await _resolve_message_thread(None) is None


async def test_resolve_message_thread_uses_existing():
    message = MagicMock()
    message.thread = "THREAD"
    assert await _resolve_message_thread(message) == "THREAD"


async def test_resolve_message_thread_fetch_fallback():
    message = MagicMock()
    message.thread = None
    message.fetch_thread = AsyncMock(return_value="FETCHED")
    assert await _resolve_message_thread(message) == "FETCHED"

    message2 = MagicMock()
    message2.thread = None
    message2.fetch_thread = AsyncMock(side_effect=discord.NotFound(MagicMock(), "gone"))
    assert await _resolve_message_thread(message2) is None


# ── Sounding summaries ───────────────────────────────────────────────────────


async def test_ensure_sounding_summary_cache_hit():
    redis = AsyncMock()
    with patch("utils.state_store.get_product_cache", new_callable=AsyncMock) as mock_cache, patch(
        "utils.state_store._get_redis_client", return_value=redis
    ), patch("utils.ai.summarize_sounding", new_callable=AsyncMock) as mock_sum:
        mock_cache.return_value = "cached"

        result = await ensure_sounding_summary("KOUN_20260810_00z")

    assert result == "cached"
    mock_sum.assert_not_awaited()


async def test_ensure_sounding_summary_missing_raw_returns_none():
    with patch("utils.state_store.get_product_cache", new_callable=AsyncMock) as mock_cache, patch(
        "utils.state_store._get_redis_client", return_value=None
    ), patch("utils.ai.summarize_sounding", new_callable=AsyncMock) as mock_sum:
        mock_cache.side_effect = [None, None]

        result = await ensure_sounding_summary("KOUN_20260810_00z")

    assert result is None
    mock_sum.assert_not_awaited()


async def test_ensure_sounding_summary_generates_plain(fake_bot):
    with patch("utils.state_store.get_product_cache", new_callable=AsyncMock) as mock_cache, patch(
        "utils.state_store.set_product_cache", new_callable=AsyncMock
    ) as mock_set, patch("utils.state_store._get_redis_client", return_value=None), patch(
        "utils.ai.summarize_sounding", new_callable=AsyncMock
    ) as mock_sum:
        mock_cache.return_value = None
        mock_sum.return_value = "sounding summary"

        result = await ensure_sounding_summary(
            "KOUN_20260810_00z",
            raw_text="RAW SOUNDING",
            lat=35.2,
            lon=-97.4,
            location_name="NORMAN",
            bot=fake_bot,
        )

    assert result == "sounding summary"
    mock_sum.assert_awaited_once_with("RAW SOUNDING")
    mock_set.assert_awaited_once()


async def test_ensure_sounding_summary_inflight_dedup():
    event = asyncio.Event()
    import cogs.ai_summaries as ai_mod

    ai_mod._sounding_inflight["KOUN_20260810_00z"] = event
    asyncio.get_running_loop().create_task(_set_event(event))

    with patch("utils.state_store.get_product_cache", new_callable=AsyncMock) as mock_cache, patch(
        "utils.state_store._get_redis_client", return_value=None
    ), patch("utils.ai.summarize_sounding", new_callable=AsyncMock) as mock_sum:
        mock_cache.side_effect = [None, "generated-elsewhere"]

        result = await ensure_sounding_summary("KOUN_20260810_00z")

    assert result == "generated-elsewhere"
    mock_sum.assert_not_awaited()


async def _set_event(event):
    await asyncio.sleep(0.05)
    event.set()


async def test_ensure_sounding_summary_hodo_resolves_location():
    with patch("utils.state_store.get_product_cache", new_callable=AsyncMock) as mock_cache, patch(
        "utils.state_store._get_redis_client", return_value=None
    ), patch("cogs.sounding_utils.resolve_location", new_callable=AsyncMock) as mock_resolve, patch(
        "utils.ai.summarize_sounding", new_callable=AsyncMock
    ) as mock_sum:
        mock_cache.return_value = None
        mock_resolve.return_value = (37.65, -97.43, "WICHITA")
        mock_sum.return_value = "hodo summary"

        result = await ensure_sounding_summary("hodo_KICT_20260810", raw_text="RAW")

    assert result == "hodo summary"
    mock_resolve.assert_awaited_once_with("KICT")


# ── Autopost helpers ─────────────────────────────────────────────────────────


async def test_autopost_outlook_correction():
    channel = AsyncMock()
    with patch(
        "cogs.ai_summaries._fetch_outlook_text", new_callable=AsyncMock
    ) as mock_fetch, patch(
        "utils.state_store.get_previous_outlook_text", new_callable=AsyncMock
    ) as mock_prev, patch(
        "utils.state_store.set_previous_outlook_text", new_callable=AsyncMock
    ) as mock_set_prev, patch(
        "utils.ai.summarize_outlook_revision", new_callable=AsyncMock
    ) as mock_rev:
        mock_fetch.return_value = "RAW OUTLOOK CORR 2\nmore text"
        mock_prev.return_value = "OLD TEXT"
        mock_rev.return_value = "REVISION SUMMARY"

        await autopost_outlook_summary(channel, "1", delay=0.0)

    channel.send.assert_awaited_once()
    embed = channel.send.await_args.kwargs["embed"]
    assert "CORR 2" in embed.title
    mock_set_prev.assert_awaited_once()


async def test_autopost_outlook_summary_posts_view():
    channel = AsyncMock()
    regions = [_region("NORTH")]
    with patch(
        "cogs.ai_summaries._fetch_outlook_text", new_callable=AsyncMock
    ) as mock_fetch, patch(
        "utils.state_store.get_previous_outlook_text", new_callable=AsyncMock
    ) as mock_prev, patch(
        "utils.state_store.set_previous_outlook_text", new_callable=AsyncMock
    ), patch("cogs.ai_summaries.ensure_outlook_summary", new_callable=AsyncMock) as mock_ensure:
        mock_fetch.return_value = "RAW OUTLOOK"
        mock_prev.return_value = None
        mock_ensure.return_value = regions

        await autopost_outlook_summary(channel, "1", delay=0.0)

    channel.send.assert_awaited_once()
    assert channel.send.await_args.kwargs["view"] is not None


async def test_autopost_md_summary_posts_to_thread():
    md_msg = MagicMock()
    thread = AsyncMock()
    md_msg.create_thread = AsyncMock(return_value=thread)
    with patch("cogs.ai_summaries.ensure_md_summary", new_callable=AsyncMock) as mock_ensure:
        mock_ensure.return_value = "MD SUMMARY"

        await autopost_md_summary(md_msg, "1234", delay=0.0)

    thread.send.assert_awaited_once()


async def test_autopost_md_summary_channel_fallback():
    md_msg = MagicMock()
    md_msg.create_thread = AsyncMock(side_effect=RuntimeError("no threads"))
    md_msg.channel = AsyncMock()
    with patch("cogs.ai_summaries.ensure_md_summary", new_callable=AsyncMock) as mock_ensure:
        mock_ensure.return_value = "MD SUMMARY"

        await autopost_md_summary(md_msg, "1234", delay=0.0)

    md_msg.channel.send.assert_awaited_once()


async def test_autopost_sounding_summary_reply_fallback():
    msg = MagicMock()
    msg.create_thread = AsyncMock(side_effect=RuntimeError("no threads"))
    msg.reply = AsyncMock(return_value=MagicMock(jump_url="https://discord/1"))
    with patch(
        "cogs.ai_summaries.ensure_sounding_summary", new_callable=AsyncMock
    ) as mock_ensure, patch(
        "utils.state_store.set_product_cache", new_callable=AsyncMock
    ) as mock_set:
        mock_ensure.return_value = "SOUNDING SUMMARY"

        await autopost_sounding_summary(msg, "KOUN_20260810_00z", delay=0.0)

    msg.reply.assert_awaited_once()
    mock_set.assert_awaited_once_with(
        "sounding_summary_message_KOUN_20260810_00z", "https://discord/1"
    )


# ── Cog interaction handlers ─────────────────────────────────────────────────


def _cog_with_mocks():
    bot = MagicMock()
    cog = AISummariesCog(bot)
    cog._handle_md_summary = AsyncMock()
    cog._handle_outlook_summary = AsyncMock()
    cog._handle_sounding_summary = AsyncMock()
    cog._handle_region_pagination = AsyncMock()
    return cog


async def test_on_interaction_routes_custom_ids():
    cog = _cog_with_mocks()

    def interaction(custom_id):
        i = MagicMock()
        i.type = discord.InteractionType.component
        i.data = {"custom_id": custom_id}
        return i

    await cog.on_interaction(interaction("ai_md:1234"))
    cog._handle_md_summary.assert_awaited_once()
    await cog.on_interaction(interaction("ai_outlook:1"))
    cog._handle_outlook_summary.assert_awaited_once()
    await cog.on_interaction(interaction("ai_snd:KOUN_20260810_00z"))
    cog._handle_sounding_summary.assert_awaited_once()
    await cog.on_interaction(interaction("ai_region_prev:1:1"))
    cog._handle_region_pagination.assert_awaited_once_with(ANY, "1", 0)
    await cog.on_interaction(interaction("ai_region_next:1:0"))
    cog._handle_region_pagination.assert_awaited_with(ANY, "1", 1)


async def test_on_interaction_ignores_non_component():
    cog = _cog_with_mocks()
    i = MagicMock()
    i.type = discord.InteractionType.application_command
    await cog.on_interaction(i)
    cog._handle_md_summary.assert_not_awaited()


async def test_handle_md_summary_posts_to_thread():
    interaction = MagicMock()
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    thread = AsyncMock()
    thread.jump_url = "https://discord/thread"
    interaction.message = MagicMock(thread=thread)
    with patch("cogs.ai_summaries.ensure_md_summary", new_callable=AsyncMock) as mock_ensure:
        mock_ensure.return_value = "MD SUMMARY"
        cog = AISummariesCog(MagicMock())
        await cog._handle_md_summary(interaction, "1234")

    thread.send.assert_awaited_once()
    interaction.followup.send.assert_awaited_once()


async def test_handle_outlook_summary_str_fallback():
    interaction = MagicMock()
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.message = MagicMock(thread=None)
    interaction.message.fetch_thread = AsyncMock(return_value=None)
    with patch("cogs.ai_summaries.ensure_outlook_summary", new_callable=AsyncMock) as mock_ensure:
        mock_ensure.return_value = "plain string summary"
        cog = AISummariesCog(MagicMock())
        await cog._handle_outlook_summary(interaction, "1")

    assert interaction.followup.send.await_args.kwargs["view"] is None


async def test_handle_region_pagination_valid():
    interaction = MagicMock()
    interaction.response = AsyncMock()
    with patch("cogs.ai_summaries._get_regions_from_redis", new_callable=AsyncMock) as mock_regions:
        mock_regions.return_value = [_region("A"), _region("B")]
        cog = AISummariesCog(MagicMock())
        await cog._handle_region_pagination(interaction, "1", 1)

    interaction.response.edit_message.assert_awaited_once()


async def test_handle_region_pagination_expired():
    interaction = MagicMock()
    interaction.response = AsyncMock()
    with patch("cogs.ai_summaries._get_regions_from_redis", new_callable=AsyncMock) as mock_regions:
        mock_regions.return_value = None
        cog = AISummariesCog(MagicMock())
        await cog._handle_region_pagination(interaction, "1", 0)

    interaction.response.send_message.assert_awaited_once()


async def test_daily_briefing_not_configured():
    interaction = MagicMock()
    interaction.response = AsyncMock()
    with patch("cogs.ai_summaries.GEMINI_API_KEY", None), patch(
        "cogs.ai_summaries.OPENCODE_API_KEY", None
    ):
        cog = AISummariesCog(MagicMock())
        await cog.daily_briefing.callback(cog, interaction)

    interaction.response.send_message.assert_awaited_once()


async def test_daily_briefing_generates():
    interaction = MagicMock()
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    with patch("cogs.ai_summaries.GEMINI_API_KEY", "key"), patch(
        "cogs.ai_summaries.OPENCODE_API_KEY", "key"
    ), patch("cogs.ai_summaries.http_get_text", new_callable=AsyncMock) as mock_text, patch(
        "utils.ai.generate_morning_briefing", new_callable=AsyncMock
    ) as mock_brief:
        mock_text.return_value = "<html>DAY1</html>"
        mock_brief.return_value = "BRIEFING"
        cog = AISummariesCog(MagicMock())
        await cog.daily_briefing.callback(cog, interaction)

    interaction.followup.send.assert_awaited_once()
