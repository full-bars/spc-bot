# cogs/watch_format.py
"""Watch embed building and file helpers — pure formatting, no I/O."""

from datetime import datetime, timezone
from typing import List, Optional, Tuple

import discord


def _build_watch_embed(
    watch_num: str,
    *,
    is_tornado: bool,
    watch_label: str,
    color: discord.Color,
    timestamp: datetime,
    expires=None,
    text_summary: Optional[str] = None,
    probs: Optional[str] = None,
    cache_path: Optional[str] = None,
    footer: str = "SPC Watch Monitor",
    paginator_index: Optional[Tuple[int, int]] = None,
) -> discord.Embed:
    """Canonical watch embed used by paginator, auto-post, iembot fast-path,
    and the upgrade-edit. One place to fix styling drift."""
    embed = discord.Embed(
        title=(
            f"{'🌪️' if is_tornado else '⛈️'}  "
            f"{watch_label} #{int(watch_num)}"
        ),
        color=color,
        timestamp=timestamp,
    )
    if expires:
        embed.add_field(
            name="Expires",
            value=f"<t:{int(expires.timestamp())}:R>",
            inline=True,
        )
    if text_summary:
        embed.add_field(name="Details", value=text_summary[:1024], inline=False)
    if probs:
        embed.add_field(name="Probabilities", value=probs[:1024], inline=False)
    if paginator_index is not None:
        i, n = paginator_index
        embed.set_footer(text=f"Watch {i + 1} of {n} · {footer}")
    else:
        embed.set_footer(text=footer)
    if cache_path:
        embed.set_image(url=f"attachment://watch_{watch_num}.gif")
    return embed


def _watch_files(watch_num: str, cache_path: Optional[str]) -> List[discord.File]:
    if not cache_path:
        return []
    return [discord.File(cache_path, filename=f"watch_{watch_num}.gif")]
