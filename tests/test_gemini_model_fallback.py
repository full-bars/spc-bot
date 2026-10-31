"""Gemini model selection and in-provider fallback.

The bot's third AI leg is Gemini. The free tier is asymmetric: the lite model has a
generous daily allowance but is often refused with 503 under load, while the full
model answers but allows only a handful of calls per day. So the model name must be
configurable, and a refusal on the lite model should fall through to the other model
rather than ending the chain.
"""

import json
from unittest.mock import AsyncMock

import pytest

import utils.ai as ai


def _gemini_reply(text: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


@pytest.fixture(autouse=True)
def _gemini_key(monkeypatch):
    monkeypatch.setattr(ai, "GEMINI_API_KEY", "test-key", raising=False)


@pytest.mark.asyncio
async def test_uses_configured_model_in_url(monkeypatch):
    monkeypatch.setattr(ai, "GEMINI_MODEL", "gemini-test-lite", raising=False)
    monkeypatch.setattr(ai, "GEMINI_FALLBACK_MODEL", "", raising=False)
    post = AsyncMock(return_value=_gemini_reply("hello"))
    monkeypatch.setattr(ai, "http_post_json", post)

    assert await ai.call_gemini("prompt") == "hello"
    url = post.await_args_list[0].args[0]
    assert "gemini-test-lite" in url
    assert "generateContent" in url


@pytest.mark.asyncio
async def test_falls_through_to_fallback_model_on_empty_reply(monkeypatch):
    monkeypatch.setattr(ai, "GEMINI_MODEL", "gemini-lite", raising=False)
    monkeypatch.setattr(ai, "GEMINI_FALLBACK_MODEL", "gemini-full", raising=False)
    post = AsyncMock(side_effect=[None, _gemini_reply("from full")])
    monkeypatch.setattr(ai, "http_post_json", post)

    assert await ai.call_gemini("prompt") == "from full"
    urls = [c.args[0] for c in post.await_args_list]
    assert "gemini-lite" in urls[0]
    assert "gemini-full" in urls[1]


@pytest.mark.asyncio
async def test_does_not_repeat_a_model_configured_twice(monkeypatch):
    monkeypatch.setattr(ai, "GEMINI_MODEL", "gemini-lite", raising=False)
    monkeypatch.setattr(ai, "GEMINI_FALLBACK_MODEL", "gemini-lite", raising=False)
    post = AsyncMock(return_value=None)
    monkeypatch.setattr(ai, "http_post_json", post)

    assert await ai.call_gemini("prompt") is None
    assert post.await_count == 1


@pytest.mark.asyncio
async def test_no_fallback_configured_returns_none_after_one_attempt(monkeypatch):
    monkeypatch.setattr(ai, "GEMINI_MODEL", "gemini-lite", raising=False)
    monkeypatch.setattr(ai, "GEMINI_FALLBACK_MODEL", "", raising=False)
    post = AsyncMock(return_value=None)
    monkeypatch.setattr(ai, "http_post_json", post)

    assert await ai.call_gemini("prompt") is None
    assert post.await_count == 1
    assert "gemini-lite" in post.await_args_list[0].args[0]
    assert post.await_args_list[0].kwargs["retries"] == 2


@pytest.mark.asyncio
async def test_json_mode_parses_fallback_model_reply(monkeypatch):
    monkeypatch.setattr(ai, "GEMINI_MODEL", "gemini-lite", raising=False)
    monkeypatch.setattr(ai, "GEMINI_FALLBACK_MODEL", "gemini-full", raising=False)
    payload = [{"region": "Southern Plains", "confidence": "moderate"}]
    post = AsyncMock(side_effect=[None, _gemini_reply(json.dumps(payload))])
    monkeypatch.setattr(ai, "http_post_json", post)

    assert await ai.call_gemini("prompt", is_json=True) == payload
    assert (
        post.await_args_list[1].kwargs["json_data"]["generationConfig"]["response_mime_type"]
        == "application/json"
    )


@pytest.mark.asyncio
async def test_missing_key_returns_none_without_calling(monkeypatch):
    monkeypatch.setattr(ai, "GEMINI_API_KEY", "", raising=False)
    post = AsyncMock()
    monkeypatch.setattr(ai, "http_post_json", post)

    assert await ai.call_gemini("prompt") is None
    post.assert_not_awaited()
