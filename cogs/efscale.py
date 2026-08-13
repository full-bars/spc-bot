"""EF scale damage-indicator lookup (/damageindicators).

Presents the Enhanced Fujita scale's damage indicators (DI) and degrees of
damage (DoD) with lower-bound / expected / upper-bound 3-second-gust wind
speed estimates and the EF rating per bound, color coded with Wikipedia's
EF palette (see utils/ef_scale.py).

Usage:
    /damageindicators indicator:FR12              -> full DoD breakdown
    /damageindicators indicator:2 dod:10          -> single-DoD card + color swatch
"""

from __future__ import annotations

import io
import logging
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

_MAX_DOD = max(len(di["dods"]) for di in DAMAGE_INDICATORS)

_SOURCE_FOOTER = (
    "3-second-gust estimates for typical construction (LB/EXP/UB) · "
    "Wikipedia: Enhanced Fujita scale — https://en.wikipedia.org/wiki/Enhanced_Fujita_scale"
)

_KMH_PER_MPH = 1.60934


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


def build_full_embed(di: DamageIndicator) -> discord.Embed:
    """One field per DoD — the whole breakdown for a damage indicator."""
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
    return embed


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


def render_swatch(dod: DegreeOfDamage) -> io.BytesIO:
    """Render the LB/EXP/UB strip as three Wikipedia-colored cells (PNG).

    Each cell shows the bound label, wind estimate (mph + km/h), and EF
    rating, colored with the Wikipedia EF palette so the Discord embed
    reproduces the article's table look (Discord text cannot be colored).
    """
    import matplotlib

    matplotlib.use("Agg")

    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    fig, ax = plt.subplots(figsize=(7.2, 1.7), dpi=110)
    ax.set_xlim(0, 3)
    ax.set_ylim(0, 1)
    ax.axis("off")

    for i, (label, est) in enumerate((("LB", dod["lb"]), ("EXP", dod["exp"]), ("UB", dod["ub"]))):
        ef = est["ef"] or "EFU"
        color = "#" + EF_COLORS.get(ef, "CCCCCC")
        ax.add_patch(
            Rectangle(
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
    plt.close(fig)
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
        indicator="Damage indicator: number (2), abbreviation (FR12, MHSW), or name",
        dod="Degree of damage 1..max for that indicator — omit for the full breakdown",
    )
    async def damageindicators(
        self,
        interaction: discord.Interaction,
        indicator: str,
        dod: app_commands.Range[int, 1, _MAX_DOD] | None = None,
    ) -> None:
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
            embed, buf = build_dod_embed(di, dod_obj)
            file = discord.File(buf, filename="ef_swatch.png")
            await interaction.response.send_message(embed=embed, file=file)
            return

        embed = build_full_embed(di)
        await interaction.response.send_message(embed=embed)

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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EfScaleCog(bot))
