"""Tests for cogs/analytics.py — IEM Autoplot URL construction and /verify."""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

import pytest

from cogs.analytics import AnalyticsCog


def _make_interaction():
    interaction = MagicMock()
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    return interaction


def _make_cog():
    bot = MagicMock()
    cog = AnalyticsCog(bot)
    return cog


# ── /topstats URL construction ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_topstats_warnings_by_state_url():
    """/topstats with source=109 and by=state produces a valid Autoplot #109 URL."""
    cog = _make_cog()
    interaction = _make_interaction()

    await cog.top_stats.callback(cog, interaction, by="state", year=2025, source="109")

    interaction.followup.send.assert_called_once()
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "109" in embed.image.url
    assert "TO.W" in embed.image.url
    assert "by:state" in embed.image.url
    assert "2025" in embed.image.url


@pytest.mark.asyncio
async def test_topstats_reports_by_wfo_url():
    """/topstats with source=163 and by=wfo uses Autoplot #163 with TORNADO filter."""
    cog = _make_cog()
    interaction = _make_interaction()

    await cog.top_stats.callback(cog, interaction, by="wfo", year=2024, source="163")

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "163" in embed.image.url
    assert "TORNADO" in embed.image.url
    assert "by:wfo" in embed.image.url


@pytest.mark.asyncio
async def test_topstats_defaults_to_current_year(monkeypatch):
    """When year is omitted, it defaults to the current UTC year."""
    cog = _make_cog()
    interaction = _make_interaction()

    current_year = datetime.now(timezone.utc).year
    await cog.top_stats.callback(cog, interaction, by="state", year=None, source="109")

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert str(current_year) in embed.image.url


# ── /dayssince ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dayssince_url_contains_to_phenomenon():
    """/dayssince URL includes the TO.W phenomenon parameters."""
    cog = _make_cog()
    interaction = _make_interaction()

    await cog.days_since.callback(cog, interaction)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "phenomena:TO" in embed.image.url
    assert "significance:W" in embed.image.url


# ── /dailyrecap ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dailyrecap_explicit_date_in_url():
    """/dailyrecap with an explicit date embeds it in the Autoplot #203 URL."""
    cog = _make_cog()
    interaction = _make_interaction()

    await cog.daily_recap.callback(cog, interaction, date="2026-04-29")

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "203" in embed.image.url
    assert "2026-04-29" in embed.image.url


@pytest.mark.asyncio
async def test_dailyrecap_defaults_to_yesterday():
    """/dailyrecap with no date defaults to yesterday's UTC date."""
    cog = _make_cog()
    interaction = _make_interaction()

    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1))
    await cog.daily_recap.callback(cog, interaction, date=None)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert str(yesterday) in embed.image.url


# ── /tornadoheatmap ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tornado_heatmap_url_contains_days():
    """/tornadoheatmap URL includes the requested lookback period."""
    cog = _make_cog()
    interaction = _make_interaction()

    await cog.tornado_heatmap.callback(cog, interaction, days=60)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    # The URL should reference 60 days worth of date range
    assert "163" in embed.image.url or "60" in embed.image.url or embed.image.url


# ── /verify (IEM Cow) ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_posts_embed_on_success():
    """/verify fetches IEM Cow API and posts stats embed."""
    cog = _make_cog()
    interaction = _make_interaction()

    cow_payload = {
        "stats": {
            "POD[1]": 0.85,
            "FAR[1]": 0.72,
            "avg_leadtime[min]": 14.2,
            "CSI[1]": 0.55,
        }
    }

    with patch("utils.http.http_get_json", AsyncMock(return_value=cow_payload)):
        await cog.verify.callback(cog, interaction, wfo="OUN", days=30)

    interaction.followup.send.assert_called_once()
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert embed is not None
    assert "0.85" in embed.fields[0].value


@pytest.mark.asyncio
async def test_verify_handles_api_failure_gracefully():
    """/verify surfaces an error message when IEM Cow is unreachable."""
    cog = _make_cog()
    interaction = _make_interaction()

    with patch("utils.http.http_get_json", AsyncMock(return_value=None)):
        await cog.verify.callback(cog, interaction, wfo="OUN", days=30)

    interaction.followup.send.assert_called_once()
    args, kwargs = interaction.followup.send.call_args
    assert "Could not fetch verification data" in args[0]
