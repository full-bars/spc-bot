import pytest
import hashlib
import json
from unittest.mock import MagicMock, AsyncMock, patch
import discord


@pytest.mark.asyncio
async def test_handle_outlook_summary_caching():
    bot = MagicMock()

    with patch("utils.http.http_get_text", new_callable=AsyncMock) as mock_get_text, patch(
        "utils.ai.summarize_outlook", new_callable=AsyncMock
    ) as mock_summarize, patch("utils.ai.generate_morning_briefing", new_callable=AsyncMock), patch(
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
