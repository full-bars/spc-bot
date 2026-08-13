"""Tests for the EF scale damage-indicator data + /damageindicators command."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs.efscale import (
    _resolve_indicator,
    build_dod_embed,
    build_full_embed,
    render_swatch,
    EfScaleCog,
)
from utils.ef_scale import (
    DAMAGE_INDICATORS,
    ef_color,
    get_di,
    get_dod,
    search_damage_indicators,
)

VALID_EF = {"EFU", "EF0", "EF1", "EF2", "EF3", "EF4", "EF5"}


def test_data_shape():
    assert len(DAMAGE_INDICATORS) == 28
    assert sum(len(d["dods"]) for d in DAMAGE_INDICATORS) == 224
    for i, di in enumerate(DAMAGE_INDICATORS, start=1):
        assert di["di"] == i
        assert di["name"]
        assert [d["dod"] for d in di["dods"]] == list(range(1, len(di["dods"]) + 1))


def test_bounds_valid_and_monotonic():
    for di in DAMAGE_INDICATORS:
        exps = [d["exp"]["mph"] for d in di["dods"] if d["exp"]["mph"] is not None]
        assert exps == sorted(exps), di["di"]
        for d in di["dods"]:
            for key in ("lb", "exp", "ub"):
                est = d[key]
                if est["mph"] is not None:
                    assert est["ef"] in VALID_EF
                    assert est["mph"] > 0
                else:
                    assert est["ef"] is None  # EFU rows carry no estimate


def test_efu_rows_are_the_known_pair():
    efu = [
        (d["di"], dd["dod"])
        for d in DAMAGE_INDICATORS
        for dd in d["dods"]
        if dd["exp"]["mph"] is None
    ]
    assert efu == [(5, 7), (16, 12)]


def test_accessors():
    assert get_di(2) is not None
    assert get_di(2)["name"] == "One- or two-family residences (FR12)"
    assert get_di(99) is None
    d = get_dod(2, 10)
    assert d is not None
    assert d["desc"] == (
        "Destruction of engineered and/or well constructed residence; slab swept clean"
    )
    assert d["exp"] == {"mph": 200, "ef": "EF4"}
    assert d["ub"] == {"mph": 220, "ef": "EF5"}
    assert get_dod(2, 11) is None
    assert get_dod(99, 1) is None


def test_search():
    assert search_damage_indicators("")[0]["di"] == 1
    assert search_damage_indicators("2")[0]["di"] == 2
    assert search_damage_indicators("FR12")[0]["di"] == 2
    assert search_damage_indicators("fr12")[0]["di"] == 2
    assert search_damage_indicators("residence")[0]["di"] == 2
    assert search_damage_indicators("school")[0]["di"] == 15
    assert search_damage_indicators("zzzz-no-match") == []


def test_ef_color():
    assert ef_color("EF0") == 0x4DFFFF
    assert ef_color("EF5") == 0xA188FC
    assert ef_color(None) == 0xCCCCCC
    assert ef_color("EF99") == 0xCCCCCC  # unknown falls back to grey


def test_full_embed_shape():
    di = get_di(2)
    assert di is not None
    embed = build_full_embed(di)
    assert embed.title == "🌪️ EF Scale — DI 2: One- or two-family residences (FR12)"
    assert int(embed.color) == 0xFF738A  # max EXP = EF4
    assert len(embed.fields) == 10
    assert embed.fields[0].name == "DoD 1/10"
    assert "53 mph (85 km/h) EF0" in embed.fields[0].value
    assert "200 mph (322 km/h) EF4" in embed.fields[-1].value


def test_dod_embed_shape():
    di = get_di(2)
    dod = get_dod(2, 10)
    assert di is not None and dod is not None
    embed, buf = build_dod_embed(di, dod)
    assert int(embed.color) == 0xFF738A  # EXP EF4
    assert [f.name for f in embed.fields] == [
        "DoD 10/10",
        "Lower bound (LB)",
        "Expected (EXP)",
        "Upper bound (UB)",
    ]
    assert embed.fields[1].value == "165 mph (266 km/h) EF3"
    assert embed.fields[2].value == "200 mph (322 km/h) EF4"
    assert embed.fields[3].value == "220 mph (354 km/h) EF5"
    assert embed.image.url == "attachment://ef_swatch.png"
    assert buf is not None


def test_efu_embed_render():
    di = get_di(5)
    dod = get_dod(5, 7)
    assert di is not None and dod is not None
    embed, _ = build_dod_embed(di, dod)
    assert embed.fields[1].value == "N/A (EFU)"
    assert int(embed.color) == 0xCCCCCC


def test_render_swatch_is_png():
    dod = get_dod(2, 10)
    assert dod is not None
    buf = render_swatch(dod)
    data = buf.getvalue()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(data) > 1000


def test_resolve_indicator():
    assert _resolve_indicator("2") is not None
    assert _resolve_indicator("2")["di"] == 2
    assert _resolve_indicator("FR12") is not None
    assert _resolve_indicator("FR12")["di"] == 2
    assert _resolve_indicator(" 2 ") is not None
    assert _resolve_indicator(" 2 ")["di"] == 2
    assert _resolve_indicator("nope-zz") is None


@pytest.mark.asyncio
async def test_command_full_breakdown():
    cog = EfScaleCog.__new__(EfScaleCog)
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    await cog.damageindicators.callback(cog, interaction, "2")
    kwargs = interaction.response.send_message.await_args.kwargs
    assert "file" not in kwargs
    assert kwargs["embed"].title == ("🌪️ EF Scale — DI 2: One- or two-family residences (FR12)")


@pytest.mark.asyncio
async def test_command_single_dod_with_swatch():
    cog = EfScaleCog.__new__(EfScaleCog)
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    await cog.damageindicators.callback(cog, interaction, "2", 10)
    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs["file"] is not None
    assert kwargs["file"].filename == "ef_swatch.png"
    assert kwargs["embed"].color.value == 0xFF738A


@pytest.mark.asyncio
async def test_command_unknown_indicator_is_ephemeral():
    cog = EfScaleCog.__new__(EfScaleCog)
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    await cog.damageindicators.callback(cog, interaction, "zzz-nope")
    call = interaction.response.send_message.await_args
    assert call.kwargs["ephemeral"] is True
    assert "Couldn't find" in call.args[0]


@pytest.mark.asyncio
async def test_command_dod_out_of_range():
    cog = EfScaleCog.__new__(EfScaleCog)
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    await cog.damageindicators.callback(cog, interaction, "2", 99)
    call = interaction.response.send_message.await_args
    assert call.kwargs["ephemeral"] is True
    assert "only has DoDs 1–10" in call.args[0]


@pytest.mark.asyncio
async def test_autocomplete():
    cog = EfScaleCog.__new__(EfScaleCog)
    interaction = MagicMock()
    choices = await cog._indicator_autocomplete(interaction, "FR")
    assert choices[0].name.startswith("2.")
    assert choices[0].value == "2"


@pytest.mark.asyncio
async def test_dod_autocomplete_lists_selected_indicators_dods():
    cog = EfScaleCog.__new__(EfScaleCog)
    interaction = MagicMock()
    interaction.namespace.indicator = "2"
    choices = await cog._dod_autocomplete(interaction, 0)
    assert len(choices) == 10
    assert choices[0].name == "DoD 1/10 — Threshold of visible damage"
    assert choices[0].value == 1
    assert "slab swept clean" in choices[-1].name
    assert choices[-1].value == 10
    # every name is inside Discord's choice-name limit
    assert all(len(c.name) <= 100 for c in choices)


@pytest.mark.asyncio
async def test_dod_autocomplete_hint_when_no_indicator():
    cog = EfScaleCog.__new__(EfScaleCog)
    interaction = MagicMock()
    interaction.namespace.indicator = ""
    choices = await cog._dod_autocomplete(interaction, 0)
    assert len(choices) == 1
    assert choices[0].value == 0
    assert "Pick an indicator" in choices[0].name


@pytest.mark.asyncio
async def test_dod_autocomplete_respects_di_specific_scale():
    cog = EfScaleCog.__new__(EfScaleCog)
    interaction = MagicMock()
    # DI 25 (free-standing towers) only has 3 DoDs
    interaction.namespace.indicator = "25"
    choices = await cog._dod_autocomplete(interaction, 0)
    assert len(choices) == 3
    assert choices[0].name == "DoD 1/3 — Threshold of visible damage"
    assert choices[-1].name == "DoD 3/3 — Collapsed micro-wave tower"
