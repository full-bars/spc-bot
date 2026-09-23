"""NHC Tropical cyclone product auto-poster."""

import logging
import re
from datetime import datetime, timezone

import discord
from discord.ext import commands

from config import TROPICAL_CHANNEL_ID
from utils.discord_send import safe_create_thread, safe_send
from utils.nhc_storms import (
    SAFFIR_EMOJI,
    SAFFIR_SIMPSON_COLORS,
    classify_product,
    fetch_nhc_product,
    parse_location,
    parse_location_desc,
    parse_max_wind,
    parse_movement,
    parse_pressure,
    winds_to_category,
)
from utils.state_store import get_state

logger = logging.getLogger("spc_bot")

TROPICAL_PILS = {"TCV", "TCD", "TWD", "TCU", "TWO", "TCE", "TCP"}
TROPICAL_OFFICES = {"KNHC", "KTPC", "PHFO"}

_NHC_LABEL_RE = re.compile(
    r"(?:NATIONAL HURRICANE CENTER|NHC|TROPICAL|HURRICANE|TROPICAL STORM)", re.IGNORECASE
)


def _build_compact_summary(
    product_type: str,
    parsed: dict,
    wind_mph: float | None,
    ss_cat: str | None,
    storm_name: str | None,
    storm_type: str | None,
) -> str:
    raw = parsed.get("raw_text", "")
    summary = parsed.get("summary", "")

    if product_type == "ADVISORY":
        parts = []
        loc = parse_location(summary or raw)
        loc_desc = parse_location_desc(summary or raw)
        if loc:
            line = f"📍 {loc}"
            if loc_desc:
                line += f" — {loc_desc}"
            parts.append(line)
        data_bits = []
        if wind_mph:
            data_bits.append(f"💨 {wind_mph:.0f} MPH")
        pressure = parse_pressure(summary or raw)
        if pressure:
            data_bits.append(f"🌀 {pressure} MB")
        movement = parse_movement(summary or raw)
        if movement:
            data_bits.append(f"➡️ {movement}")
        if data_bits:
            parts.append(" | ".join(data_bits))
        return "\n".join(parts)

    elif product_type == "DISCUSSION":
        lines = (summary or raw).splitlines()
        for line in lines:
            stripped = line.strip()
            if (
                stripped
                and not stripped.startswith("SUMMARY")
                and not stripped.startswith("-")
                and not stripped.startswith("$$")
            ):
                return stripped[:200]
        return ""

    elif product_type == "TROPICAL WEATHER OUTLOOK":
        lines = (summary or raw).splitlines()
        # Priority 1: formation chance lines (most informative)
        for line in lines:
            s = line.strip()
            if "formation chance" in s.lower() and ("%" in s or "percent" in s.lower()):
                return s[:200]
        # Priority 2: "not expected" (quiet period)
        for line in lines:
            s = line.strip()
            if "not expected" in s.lower():
                return s[:200]
        # Priority 3: first non-header sentence mentioning active systems
        for line in lines:
            s = line.strip()
            if (
                s
                and len(s) > 30
                and not s.startswith(("NWS", "$$", "Forecaster", "&&", "For the", "Products"))
                and not s.startswith("http")
            ):
                return s[:200]
        return ""

    elif product_type == "TROPICAL WEATHER DISCUSSION":
        lines = (summary or raw).splitlines()
        for line in lines:
            s = line.strip()
            if s and len(s) > 20 and not s.startswith(("$", ".", "*")):
                return s[:200]
        return ""

    elif product_type == "UPDATE":
        loc = parse_location(summary or raw)
        if loc and wind_mph:
            return f"📍 {loc} | 💨 {wind_mph:.0f} MPH"
        if loc:
            return f"📍 {loc}"
        return ""

    return ""


class TropicalCog(commands.Cog, name="Tropical"):
    """Auto-posts NHC tropical cyclone products."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._posted = set()

    async def _resolve_channel(self):
        """Return the channel to post tropical products to, or None if disabled."""
        override = await get_state("warning_channel:tropical")
        if override == "disabled":
            return None
        if override:
            try:
                channel = self.bot.get_channel(int(override))
            except ValueError:
                channel = None
            if channel:
                return channel
            logger.warning(f"Tropical channel override {override} not found, using default")
        channel = self.bot.get_channel(TROPICAL_CHANNEL_ID)
        if not channel:
            logger.warning(f"Tropical channel {TROPICAL_CHANNEL_ID} not found")
        return channel

    async def post_tropical_product(
        self,
        product_id: str,
        raw_text: str,
        pil_prefix: str = None,
        source: str = "IEMBot",
    ):
        dedup_key = f"tropical_{product_id}"
        if dedup_key in self._posted:
            return

        product_type = pil_prefix or classify_product(product_id)
        if not product_type:
            return

        parsed = await fetch_nhc_product(product_id)
        if not parsed:
            return

        channel = await self._resolve_channel()
        if not channel:
            return

        storm_type = parsed["storm_type"]
        storm_name = parsed["storm_name"]
        wind_mph = parse_max_wind(parsed["raw_text"])
        ss_cat = winds_to_category(wind_mph) if wind_mph else None

        emoji = SAFFIR_EMOJI.get(ss_cat or "", "🌀")
        embed_color = SAFFIR_SIMPSON_COLORS.get(ss_cat or "", 0xF39C12)

        if product_type in ("ADVISORY", "UPDATE"):
            title = f"{emoji} {storm_name or product_type}"
        elif product_type == "DISCUSSION":
            title = f"{emoji} {storm_name + ' ' if storm_name else ''}Discussion"
        else:
            title = f"{emoji} NHC {product_type}"

        compact = _build_compact_summary(
            product_type, parsed, wind_mph, ss_cat, storm_name, storm_type
        )
        embed = discord.Embed(
            title=title,
            description=compact or None,
            color=embed_color,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"{source} | {product_id}")

        msg = await safe_send(
            channel, context=f"tropical {product_type} ({product_id})", embed=embed
        )
        if not msg:
            return
        logger.info(f"Posted tropical {product_type} for {storm_name or product_id}")

        self._posted.add(dedup_key)
        if len(self._posted) > 1000:
            self._posted.clear()

        thread_name = f"{storm_name or 'NHC'} {product_type}".strip()[:100]
        thread = await safe_create_thread(
            msg,
            context=f"tropical {product_type} ({product_id})",
            name=thread_name,
            auto_archive_duration=1440,
        )

        # Full summary embed goes in thread
        summary_embed = (
            discord.Embed(
                title=f"{product_type} — {storm_name or ''}",
                description=(parsed["summary"] or parsed["raw_text"])[:4096],
                color=embed_color,
            )
            if parsed.get("summary")
            else None
        )

        target = thread or channel
        if summary_embed:
            await safe_send(target, context=f"tropical summary ({product_id})", embed=summary_embed)

        # Chunk full text across multiple embeds if it exceeds 4096 chars
        full_text = parsed["raw_text"]
        for i in range(0, len(full_text), 4096):
            chunk_embed = discord.Embed(
                title="Full Text" if i == 0 else "Full Text (cont.)",
                description=full_text[i : i + 4096],
                color=discord.Color.dark_gray(),
            )
            await safe_send(target, context=f"tropical full text ({product_id})", embed=chunk_embed)

    async def route_from_product_id(
        self, product_id: str, raw_text: str = None, source: str = "IEMBot"
    ):
        pil_prefix = classify_product(product_id)
        if not pil_prefix:
            return False

        await self.post_tropical_product(product_id, raw_text or "", pil_prefix, source)
        return True


async def setup(bot: commands.Bot):
    await bot.add_cog(TropicalCog(bot))
