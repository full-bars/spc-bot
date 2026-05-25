# cogs/wxsummary.py
import html as _html
import logging
import re

import discord
from discord.ext import commands

import utils.http as _http

logger = logging.getLogger("spc_bot")

BRIEFING_URL = "https://sonde.projectweathereye.org/api/foryou/briefing"

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return _html.unescape(_TAG_RE.sub("", text)).strip()


def _build_embed(data: dict) -> discord.Embed:
    text = data.get("text") or _strip_html(data.get("html", ""))
    signals = data.get("signals", {})
    tags = data.get("tags", [])

    risk_label = signals.get("spc_day1_label") or "—"
    wai = signals.get("wai")
    top_state = signals.get("top_state_name") or signals.get("top_state") or "—"
    chasers = signals.get("chasers", 0)
    live = signals.get("live", 0)
    tone = data.get("tone", "")

    color_map = {
        "active": discord.Color.red(),
        "elevated": discord.Color.orange(),
        "light": discord.Color.yellow(),
        "calm": discord.Color.green(),
    }
    color = color_map.get(tone, discord.Color.blurple())

    embed = discord.Embed(
        title="Weather Briefing",
        description=text,
        color=color,
    )
    embed.add_field(name="SPC Day 1 Risk", value=risk_label, inline=True)
    if wai is not None:
        embed.add_field(name="WAI", value=str(wai), inline=True)
    embed.add_field(name="Top State", value=top_state, inline=True)
    if chasers or live:
        embed.add_field(
            name="Field Activity",
            value=f"{chasers} chasers · {live} live",
            inline=True,
        )
    if tags:
        embed.add_field(name="Tags", value=" · ".join(tags), inline=False)

    embed.set_footer(
        text="Source: Project WxEye · sonde.projectweathereye.org · updates every 20 min"
    )
    return embed


class WxSummaryCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @discord.app_commands.command(
        name="wxsummary",
        description="Current weather briefing from Project WxEye (updates every 20 min)",
    )
    async def wxsummary(self, interaction: discord.Interaction):
        await interaction.response.defer()
        data = await _http.http_get_json(BRIEFING_URL, retries=2, timeout=15)
        if not data:
            await interaction.followup.send(
                "Could not fetch the weather briefing right now. Try again shortly."
            )
            return
        embed = _build_embed(data)
        await interaction.followup.send(embed=embed)
        logger.info(f"[WXSUMMARY] /wxsummary served to {interaction.user}")


async def setup(bot: commands.Bot):
    await bot.add_cog(WxSummaryCog(bot))
