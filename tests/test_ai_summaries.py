import pytest
import hashlib
import json
from unittest.mock import MagicMock, AsyncMock, patch
import discord


@pytest.mark.asyncio
async def test_handle_outlook_summary_caching():
    bot = MagicMock()

    with patch("cogs.ai_summaries.http_get_text", new_callable=AsyncMock) as mock_get_text, patch(
        "utils.ai.summarize_outlook", new_callable=AsyncMock
    ) as mock_summarize, patch(
        "cogs.ai_summaries.generate_morning_briefing", new_callable=AsyncMock
    ), patch(
        "utils.state_store.get_product_cache", new_callable=AsyncMock
    ) as mock_get_cache, patch(
        "utils.state_store.set_product_cache", new_callable=AsyncMock
    ) as mock_set_cache, patch("utils.state_store._get_redis_client", return_value=None), patch(
        "utils.change_detection.calculate_hash_bytes"
    ) as mock_hash:
        from cogs.ai_summaries import AISummariesCog

        cog = AISummariesCog(bot)

        interaction = AsyncMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        interaction.followup = AsyncMock()

        # Setup mocks
        mock_get_text.side_effect = ["Content V1", "Content V1", "Content V2"]

        def simple_hash(data):
            return hashlib.md5(data).hexdigest()

        mock_hash.side_effect = lambda x: simple_hash(x)

        cache = {}

        async def get_cache(key):
            return cache.get(key)

        async def set_cache(key, val, ttl=None):
            cache[key] = val

        mock_get_cache.side_effect = get_cache
        mock_set_cache.side_effect = set_cache

        mock_summarize.side_effect = ["Summary V1", "Summary V2"]

        # 1. First call with Content V1
        await cog._handle_outlook_summary(interaction, "1")
        hash1 = simple_hash(b"Content V1")[:16]
        expected_key1 = f"ai_summary_outlook_day1_{hash1}"
        assert expected_key1 in cache
        assert json.loads(cache[expected_key1]) == "Summary V1"

        # 2. Second call with SAME Content V1
        mock_summarize.reset_mock()
        await cog._handle_outlook_summary(interaction, "1")
        mock_summarize.assert_not_called()

        # 3. Third call with Content V2
        await cog._handle_outlook_summary(interaction, "1")
        hash2 = simple_hash(b"Content V2")[:16]
        expected_key2 = f"ai_summary_outlook_day1_{hash2}"
        assert expected_key2 in cache
        assert json.loads(cache[expected_key2]) == "Summary V2"


@pytest.mark.asyncio
async def test_ensure_outlook_summary_day3_txt():
    """Verify Day 3 uses .txt URL and strips HTML tags."""
    with patch("cogs.ai_summaries.http_get_text", new_callable=AsyncMock) as mock_get_text, patch(
        "utils.ai.summarize_outlook", new_callable=AsyncMock
    ) as mock_summarize, patch("utils.state_store.get_product_cache", return_value=None), patch(
        "utils.state_store.set_product_cache", new_callable=AsyncMock
    ), patch("utils.state_store._get_redis_client", return_value=None):
        from cogs.ai_summaries import ensure_outlook_summary

        # Mock Day 3 .txt content (no HTML)
        txt_content = "ZCZC SPCSWODY3 ALL\n...TEXT...\n$$"
        mock_get_text.return_value = txt_content
        mock_summarize.return_value = [{"region": "Test", "hazards_mode": "None"}]

        summary = await ensure_outlook_summary("3")

        # Verify it called the .txt URL
        mock_get_text.assert_called_with("https://www.spc.noaa.gov/products/outlook/day3otlk.txt")
        # Verify summarize_outlook was called with the raw text
        mock_summarize.assert_called_with("ZCZC SPCSWODY3 ALL\n...TEXT...\n$$")
        assert summary[0]["region"] == "Test"


@pytest.mark.asyncio
async def test_ensure_outlook_summary_html_stripping():
    """Verify HTML stripping for HTML products like Day 4-8."""
    with patch("cogs.ai_summaries.http_get_text", new_callable=AsyncMock) as mock_get_text, patch(
        "utils.ai.summarize_outlook", new_callable=AsyncMock
    ) as mock_summarize, patch("utils.state_store.get_product_cache", return_value=None), patch(
        "utils.state_store.set_product_cache", new_callable=AsyncMock
    ), patch("utils.state_store._get_redis_client", return_value=None):
        from cogs.ai_summaries import ensure_outlook_summary

        # Mock Day 4-8 HTML content
        html_content = "<html><pre>ZCZC ALL\n...TEXT...<a href='link'>ARCHIVE</a></pre></html>"
        mock_get_text.return_value = html_content
        mock_summarize.return_value = [{"region": "Test", "hazards_mode": "None"}]

        await ensure_outlook_summary("48")

        # Verify it called the directory URL
        mock_get_text.assert_called_with("https://www.spc.noaa.gov/products/exper/day4-8/")
        # Verify summarize_outlook was called with STRIPPED text
        # Splitting by <pre> gives "ZCZC ALL\n...TEXT...<a href='link'>ARCHIVE</a>"
        # Stripping tags gives "ZCZC ALL\n...TEXT...ARCHIVE"
        mock_summarize.assert_called_with("ZCZC ALL\n...TEXT...ARCHIVE")
