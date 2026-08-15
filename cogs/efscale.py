"""EF scale damage-indicator lookup (/damageindicators).

Presents the Enhanced Fujita scale's damage indicators (DI) and degrees of
damage (DoD) with lower-bound / expected / upper-bound 3-second-gust wind
speed estimates and the EF rating per bound, color coded with Wikipedia's
EF palette (see utils/ef_scale.py).

Usage:
    /damageindicators                              -> numbered list of all 28 DIs
    /damageindicators indicator:FR12              -> full DoD breakdown
                                                     (text fields + color-coded
                                                     table image)
    /damageindicators indicator:2 dod:10          -> single-DoD card + color swatch
"""

from __future__ import annotations

import asyncio
import io
import logging
import textwrap
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from utils.ef_scale import (
    DAMAGE_INDICATORS,
    EF_COLORS,
    DegreeOfDamage,
    DamageIndicator,
    ef_color,
    get_di,
    get_dod,
    search_damage_indicators,
)

logger = logging.getLogger("spc_bot.efscale")

_SOURCE_FOOTER = (
    "3-second-gust estimates for typical construction (LB/EXP/UB) · "
    "Wikipedia: Enhanced Fujita scale — https://en.wikipedia.org/wiki/Enhanced_Fujita_scale"
)

_KMH_PER_MPH = 1.60934

_CHOICE_NAME_MAX = 100


def _bound_text(bound: Any) -> str:
    """Render one LB/EXP/UB estimate: '165 mph (266 km/h) EF3'."""
    if bound["mph"] is None:
        return "N/A (EFU)"
    mph = int(bound["mph"])
    kmh = round(mph * _KMH_PER_MPH)
    return f"{mph} mph ({kmh} km/h) {bound['ef'] or 'EFU'}"


def _max_exp_ef(di: DamageIndicator) -> str:
    """Highest EXP rating across a DI's DoDs ('EF0'..'EF5', or 'EFU')."""
    best = -1
    for dod in di["dods"]:
        ef = dod["exp"]["ef"]
        if ef and ef.startswith("EF") and ef[2:].isdigit():
            best = max(best, int(ef[2:]))
    return f"EF{best}" if best >= 0 else "EFU"


def _resolve_indicator(token: str) -> DamageIndicator | None:
    """Resolve the free-text indicator option to a DI (number or search)."""
    t = token.strip()
    if t.isdigit():
        return get_di(int(t))
    matches = search_damage_indicators(t)
    return matches[0] if matches else None


def build_index_embed() -> discord.Embed:
    """No-argument view: every damage indicator by number with its full name.

    Discord's constraints shape this: a code block can't fit four full-name
    columns (monospace width), and embeds cap at 25 fields, so a numbered
    list in the description is the layout that fits all 28 names on one
    line each at the embed's real width.
    """
    lines = [f"{d['di']}. {d['name']}" for d in DAMAGE_INDICATORS]
    embed = discord.Embed(
        title="🌪️ EF Scale — Damage Indicators",
        description="\n".join(lines),
        color=ef_color("EFU"),
    )
    embed.set_footer(text=_SOURCE_FOOTER)
    return embed


def build_full_embed(di: DamageIndicator) -> tuple[discord.Embed, io.BytesIO]:
    """Full breakdown: one field per DoD, plus a color-coded table image.

    The embed text is the copyable reference; the attached table renders the
    same data with Wikipedia's per-bound EF colors so the color escalation
    across DoDs is visible at a glance (Discord embeds only carry one color).
    """
    embed = discord.Embed(
        title=f"🌪️ EF Scale — DI {di['di']}: {di['name']}",
        color=ef_color(_max_exp_ef(di)),
    )
    embed.set_footer(text=_SOURCE_FOOTER)
    total = len(di["dods"])
    for dod in di["dods"]:
        value = (
            f"{dod['desc']}\n"
            f"LB {_bound_text(dod['lb'])} · EXP {_bound_text(dod['exp'])} · "
            f"UB {_bound_text(dod['ub'])}"
        )
        embed.add_field(name=f"DoD {dod['dod']}/{total}", value=value, inline=False)
    embed.set_image(url="attachment://ef_di_table.png")
    return embed, render_breakdown_table(di)


async def build_full_embed_async(di: DamageIndicator) -> tuple[discord.Embed, io.BytesIO]:
    """Full breakdown with the table image rendered off the event loop.

    Same content as build_full_embed, but the CPU-bound matplotlib render
    runs in a worker thread so the asyncio loop stays responsive and the
    interaction acknowledgement stays inside Discord's 3-second window.
    """
    embed = discord.Embed(
        title=f"🌪️ EF Scale — DI {di['di']}: {di['name']}",
        color=ef_color(_max_exp_ef(di)),
    )
    embed.set_footer(text=_SOURCE_FOOTER)
    total = len(di["dods"])
    for dod in di["dods"]:
        value = (
            f"{dod['desc']}\n"
            f"LB {_bound_text(dod['lb'])} · EXP {_bound_text(dod['exp'])} · "
            f"UB {_bound_text(dod['ub'])}"
        )
        embed.add_field(name=f"DoD {dod['dod']}/{total}", value=value, inline=False)
    embed.set_image(url="attachment://ef_di_table.png")
    buf = await asyncio.to_thread(render_breakdown_table, di)
    return embed, buf


def render_breakdown_table(di: DamageIndicator) -> io.BytesIO:
    """Render a DI's DoDs as a Wikipedia-style color-coded table (PNG).

    Columns: DoD | Damage description | LB | EXP | UB. Each wind-speed cell
    is filled with the Wikipedia EF color of that bound's rating so the
    cyan -> yellow -> orange -> salmon -> purple escalation matches the
    article's table exactly.
    """
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.patches import Rectangle as MplRectangle

    dods = di["dods"]
    n = len(dods)

    row_h = 0.85
    top = 0.9 + (n + 1) * row_h + 0.2  # title band + header + n rows + bottom pad
    fig = Figure(figsize=(9.6, 0.3 + top), dpi=110)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, top)
    ax.axis("off")

    # title band
    ax.add_patch(MplRectangle((0, top - 0.9), 1, 0.9, facecolor="#1c1e26"))
    ax.text(
        0.01,
        top - 0.45,
        f"DI {di['di']} — {di['name']}",
        color="white",
        fontsize=14,
        fontweight="bold",
        va="center",
    )

    # column headers
    hdr_y = top - 0.9 - row_h
    for name, w, x0 in (
        ("DoD", 0.055, 0.0),
        ("Damage description", 0.535, 0.055),
        ("LB", 0.137, 0.59),
        ("EXP", 0.137, 0.727),
        ("UB", 0.137, 0.864),
    ):
        ax.add_patch(
            MplRectangle(
                (x0, hdr_y), w, row_h, facecolor="#e4e5ea", edgecolor="black", linewidth=0.6
            )
        )
        ax.text(
            x0 + w / 2,
            hdr_y + row_h / 2,
            name,
            ha="center",
            va="center",
            fontsize=9,
            fontweight="bold",
        )

    for i, dod in enumerate(dods):
        y = hdr_y - (i + 1) * row_h
        # DoD number
        ax.add_patch(
            MplRectangle(
                (0.0, y), 0.055, row_h, facecolor="#ffffff", edgecolor="black", linewidth=0.6
            )
        )
        ax.text(
            0.0275,
            y + row_h / 2,
            str(dod["dod"]),
            ha="center",
            va="center",
            fontsize=9,
            fontweight="bold",
        )
        # damage description
        ax.add_patch(
            MplRectangle(
                (0.055, y), 0.535, row_h, facecolor="#ffffff", edgecolor="black", linewidth=0.6
            )
        )
        wrapped = "\n".join(textwrap.wrap(dod["desc"], 64))
        ax.text(
            0.063, y + row_h / 2, wrapped, ha="left", va="center", fontsize=8.2, color="#1c1e26"
        )
        # LB / EXP / UB colored cells
        for j, est in enumerate((dod["lb"], dod["exp"], dod["ub"])):
            x0 = 0.59 + j * 0.137
            ef = est["ef"] or "EFU"
            ax.add_patch(
                MplRectangle(
                    (x0, y),
                    0.137,
                    row_h,
                    facecolor="#" + EF_COLORS.get(ef, "CCCCCC"),
                    edgecolor="black",
                    linewidth=0.6,
                )
            )
            mph = "N/A" if est["mph"] is None else f"{est['mph']} mph"
            ax.text(
                x0 + 0.0685,
                y + 0.62 * row_h,
                mph,
                ha="center",
                va="center",
                fontsize=8,
                fontweight="bold",
            )
            ax.text(
                x0 + 0.0685,
                y + 0.28 * row_h,
                ef,
                ha="center",
                va="center",
                fontsize=7.5,
                fontweight="bold",
            )

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    buf.seek(0)
    return buf


def build_dod_embed(di: DamageIndicator, dod: DegreeOfDamage) -> tuple[discord.Embed, io.BytesIO]:
    """Single-DoD card: description + LB/EXP/UB estimates + color swatch."""
    embed = discord.Embed(
        title=f"🌪️ EF Scale — DI {di['di']}: {di['name']}",
        color=ef_color(dod["exp"]["ef"] or "EFU"),
    )
    embed.add_field(
        name=f"DoD {dod['dod']}/{len(di['dods'])}",
        value=dod["desc"],
        inline=False,
    )
    for label, est in (
        ("Lower bound (LB)", dod["lb"]),
        ("Expected (EXP)", dod["exp"]),
        ("Upper bound (UB)", dod["ub"]),
    ):
        embed.add_field(name=label, value=_bound_text(est), inline=True)
    embed.set_footer(text=_SOURCE_FOOTER)
    embed.set_image(url="attachment://ef_swatch.png")
    return embed, render_swatch(dod)


async def build_dod_embed_async(
    di: DamageIndicator, dod: DegreeOfDamage
) -> tuple[discord.Embed, io.BytesIO]:
    """Build the single-DoD card with the swatch rendered off the event loop.

    matplotlib rendering is CPU-bound and the first call pays the import
    cost; running it in a worker thread keeps the asyncio loop responsive
    and stays inside Discord's interaction acknowledgement window.
    """
    embed = discord.Embed(
        title=f"🌪️ EF Scale — DI {di['di']}: {di['name']}",
        color=ef_color(dod["exp"]["ef"] or "EFU"),
    )
    embed.add_field(
        name=f"DoD {dod['dod']}/{len(di['dods'])}",
        value=dod["desc"],
        inline=False,
    )
    for label, est in (
        ("Lower bound (LB)", dod["lb"]),
        ("Expected (EXP)", dod["exp"]),
        ("Upper bound (UB)", dod["ub"]),
    ):
        embed.add_field(name=label, value=_bound_text(est), inline=True)
    embed.set_footer(text=_SOURCE_FOOTER)
    embed.set_image(url="attachment://ef_swatch.png")
    buf = await asyncio.to_thread(render_swatch, dod)
    return embed, buf


def render_swatch(dod: DegreeOfDamage) -> io.BytesIO:
    """Render the LB/EXP/UB strip as three Wikipedia-colored cells (PNG).

    Each cell shows the bound label, wind estimate (mph + km/h), and EF
    rating, colored with the Wikipedia EF palette so the Discord embed
    reproduces the article's table look (Discord text cannot be colored).
    """
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.patches import Rectangle as MplRectangle

    fig = Figure(figsize=(7.2, 1.7), dpi=110)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    ax.set_xlim(0, 3)
    ax.set_ylim(0, 1)
    ax.axis("off")

    for i, (label, est) in enumerate((("LB", dod["lb"]), ("EXP", dod["exp"]), ("UB", dod["ub"]))):
        ef = est["ef"] or "EFU"
        color = "#" + EF_COLORS.get(ef, "CCCCCC")
        ax.add_patch(
            MplRectangle(
                (i, 0.08),
                1,
                0.72,
                facecolor=color,
                edgecolor="black",
                linewidth=1.2,
            )
        )
        ax.text(i + 0.5, 0.62, label, ha="center", va="center", fontsize=13, fontweight="bold")
        if est["mph"] is None:
            ax.text(i + 0.5, 0.40, "N/A", ha="center", va="center", fontsize=11)
        else:
            mph = int(est["mph"])
            kmh = round(mph * _KMH_PER_MPH)
            ax.text(i + 0.5, 0.40, f"{mph} mph ({kmh} km/h)", ha="center", va="center", fontsize=11)
        ax.text(i + 0.5, 0.20, ef, ha="center", va="center", fontsize=10, fontweight="bold")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    buf.seek(0)
    return buf


class EfScaleCog(commands.Cog):
    """EF scale damage-indicator reference commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="damageindicators",
        description=(
            "EF scale damage indicators: LB/EXP/UB wind estimates by damage "
            "type (DI) and degree of damage (DoD)"
        ),
    )
    @app_commands.describe(
        indicator=(
            "Damage indicator: number, abbreviation (FR12, MHSW), or name — "
            "omit for a list of all 28"
        ),
        dod=(
            "Degree of damage for the selected indicator — pick from the "
            "suggestions, or omit for the full breakdown"
        ),
    )
    async def damageindicators(
        self,
        interaction: discord.Interaction,
        indicator: str = "",
        dod: int | None = None,
    ) -> None:
        if not indicator.strip():
            await interaction.response.send_message(embed=build_index_embed())
            return

        di = _resolve_indicator(indicator)
        if di is None:
            await interaction.response.send_message(
                f"Couldn't find damage indicator `{indicator}` — try picking "
                "one from the suggestions.",
                ephemeral=True,
            )
            return

        if dod is not None:
            dod_obj = get_dod(di["di"], dod)
            if dod_obj is None:
                await interaction.response.send_message(
                    f"DI {di['di']} ({di['name']}) only has DoDs 1–{len(di['dods'])}.",
                    ephemeral=True,
                )
                return
            # Defer first: the matplotlib render can exceed Discord's 3-second
            # acknowledgement window (first call pays the import + font-cache
            # cost). Render off the event loop, then reply via followup.
            await interaction.response.defer()
            embed, buf = await build_dod_embed_async(di, dod_obj)
            file = discord.File(buf, filename="ef_swatch.png")
            await interaction.followup.send(embed=embed, file=file)
            return

        await interaction.response.defer()
        embed, buf = await build_full_embed_async(di)
        file = discord.File(buf, filename="ef_di_table.png")
        await interaction.followup.send(embed=embed, file=file)

    @damageindicators.autocomplete("indicator")
    async def _indicator_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        results = search_damage_indicators(current)[:25]
        return [
            app_commands.Choice(name=f"{d['di']}. {d['name']}", value=str(d["di"])) for d in results
        ]

    @damageindicators.autocomplete("dod")
    async def _dod_autocomplete(
        self,
        interaction: discord.Interaction,
        current: int,
    ) -> list[app_commands.Choice[int]]:
        """List the selected indicator's DoDs with their damage descriptions."""
        indicator = (getattr(interaction.namespace, "indicator", "") or "").strip()
        di = _resolve_indicator(indicator) if indicator else None
        if di is None:
            return [app_commands.Choice(name="↖️ Pick an indicator first", value=0)]
        total = len(di["dods"])
        choices: list[app_commands.Choice[int]] = []
        for dod in di["dods"]:
            name = f"DoD {dod['dod']}/{total} — {dod['desc']}"
            if len(name) > _CHOICE_NAME_MAX:
                name = name[: _CHOICE_NAME_MAX - 3] + "..."
            choices.append(app_commands.Choice(name=name, value=dod["dod"]))
        return choices


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EfScaleCog(bot))
