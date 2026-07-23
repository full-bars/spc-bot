"""NHC Tropical cyclone product auto-poster."""

import logging
import re
from datetime import datetime, timezone

import discord
from discord.ext import commands

from config import IEM_NWSTEXT_URL, TROPICAL_CHANNEL_ID
from utils.discord_send import safe_create_thread, safe_send
from utils.http import http_get_bytes
from utils.state_store import get_state

logger = logging.getLogger("spc_bot")

TROPICAL_PILS = {"TCV", "TCD", "TWD", "TCU", "TWO", "TCE", "TCP"}
TROPICAL_OFFICES = {"KNHC", "KTPC", "PHFO"}

_NHC_LABEL_RE = re.compile(
    r"(?:NATIONAL HURRICANE CENTER|NHC|TROPICAL|HURRICANE|TROPICAL STORM)", re.IGNORECASE
)

SAFFIR_SIMPSON_COLORS = {
    "TD": 0x5DBAFF,
    "TS": 0x00FBF4,
    "CAT1": 0xFFFFCD,
    "CAT2": 0xFEE775,
    "CAT3": 0xFFC140,
    "CAT4": 0xFF8F21,
    "CAT5": 0xFF6060,
}

SAFFIR_EMOJI = {
    "TD": "☁️",
    "TS": "🌧️",
    "CAT1": "🌀",
    "CAT2": "🌀",
    "CAT3": "⚠️🌀⚠️",
    "CAT4": "⚠️🌀⚠️",
    "CAT5": "⚠️🌀⚠️",
}


def _winds_to_category(wind_mph: float) -> str:
    if wind_mph < 39:
        return "TD"
    if wind_mph < 74:
        return "TS"
    if wind_mph < 96:
        return "CAT1"
    if wind_mph < 111:
        return "CAT2"
    if wind_mph < 130:
        return "CAT3"
    if wind_mph < 157:
        return "CAT4"
    return "CAT5"


def _parse_max_wind(text: str) -> float | None:
    """Extract maximum sustained wind speed in MPH from advisory text."""
    m = re.search(r"MAXIMUM\s+SUSTAINED\s+WINDS[\.\s:]+?(\d+)\s*MPH", text, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


STORM_TYPE_ORDER = [
    "REMNANTS",
    "POST-TROPICAL CYCLONE",
    "TROPICAL DEPRESSION",
    "SUBTROPICAL DEPRESSION",
    "SUBTROPICAL STORM",
    "TROPICAL STORM",
    "HURRICANE",
    "MAJOR HURRICANE",
]


def _classify_storm_type(text: str) -> str:
    upper = text.upper()
    for t in STORM_TYPE_ORDER:
        if t in upper:
            return t
    if "TROPICAL CYCLONE" in upper:
        return "TROPICAL CYCLONE"
    return None


def _extract_storm_name(text: str) -> str:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        upper = line.upper()
        for t in STORM_TYPE_ORDER:
            if t in upper:
                parts = upper.split(t, 1)
                if len(parts) > 1:
                    name = parts[1].strip().strip(".").strip()
                    return name.title()
    return None


NHC_PRODUCT_NAMES = {
    "TCP": "ADVISORY",
    "TCD": "DISCUSSION",
    "TWD": "TROPICAL WEATHER DISCUSSION",
    "TWO": "TROPICAL WEATHER OUTLOOK",
    "TCU": "UPDATE",
    "TCE": "POSITION ESTIMATE",
    "TCV": "WATCH/WARNING SUMMARY",
}


def _classify_product(product_id: str) -> str:
    pid = product_id.upper()
    for pil, name in NHC_PRODUCT_NAMES.items():
        if pil in pid:
            return name
    return None


async def _fetch_nhc_product(product_id: str) -> dict:
    """Fetch and parse an NHC product from the IEM archive."""
    url = IEM_NWSTEXT_URL.format(product_id=product_id)
    content, status = await http_get_bytes(url, retries=2, timeout=10)
    if not content or status != 200:
        return None

    text = content.decode("utf-8", errors="ignore")
    if "not found" in text.lower() and len(text) < 100:
        return None

    lines = text.splitlines()
    header_text = []
    body_start = 0
    for i, line in enumerate(lines):
        header_text.append(line)
        if line.strip().startswith("ATTENTION") or line.strip().startswith("000"):
            body_start = i
            break

    # The "Summary" section (location/movement/pressure) is bounded by the next
    # section header — an all-caps line immediately followed by a dashed
    # underline, e.g. "WATCHES AND WARNINGS\n--------------------". The
    # summary header itself has the same dashed-underline shape, so start
    # looking for the *next* one two lines after the summary header to skip
    # over its own underline.
    body_lines = lines[body_start:]
    summary_lines = []
    for idx, line in enumerate(body_lines):
        upper = line.strip().upper()
        if "SUMMARY OF" in upper or "SUMMARY INFORMATION" in upper:
            end = len(body_lines)
            for j in range(idx + 2, len(body_lines) - 1):
                underline = body_lines[j + 1].strip()
                if underline and set(underline) == {"-"} and len(underline) > 3:
                    end = j
                    break
            summary_lines = body_lines[idx:end]
            break

    summary = "\n".join(summary_lines).strip() if summary_lines else None

    storm_type = _classify_storm_type(text)
    storm_name = _extract_storm_name(text)

    return {
        "raw_text": text,
        "summary": summary,
        "storm_type": storm_type,
        "storm_name": storm_name,
    }


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

        product_type = pil_prefix or _classify_product(product_id)
        if not product_type:
            return

        parsed = await _fetch_nhc_product(product_id)
        if not parsed:
            return

        channel = await self._resolve_channel()
        if not channel:
            return

        storm_type = parsed["storm_type"]
        storm_name = parsed["storm_name"]
        wind_mph = _parse_max_wind(parsed["raw_text"])
        ss_cat = _winds_to_category(wind_mph) if wind_mph else None

        emoji = SAFFIR_EMOJI.get(ss_cat or "", "🌀")
        embed_color = SAFFIR_SIMPSON_COLORS.get(ss_cat or "", 0xF39C12)

        title = f"{emoji} NHC {product_type}"
        if storm_name:
            title += f" — {storm_name}"
        category_label = storm_type or ""
        if ss_cat and ss_cat.startswith("CAT"):
            cat_num = ss_cat.replace("CAT", "")
            category_label = f"Cat {cat_num} {storm_type}" if storm_type else f"Cat {cat_num}"
        elif ss_cat == "TS":
            category_label = "Tropical Storm"
        elif ss_cat == "TD":
            category_label = "Tropical Depression"
        if category_label:
            title += f" ({category_label})"
        embed = discord.Embed(
            title=title,
            color=embed_color,
            timestamp=datetime.now(timezone.utc),
        )
        if parsed["summary"]:
            embed.description = parsed["summary"][:2048]
        embed.set_footer(text=f"{source} | {product_id}")

        msg = await safe_send(channel, context=f"tropical {product_type} ({product_id})", embed=embed)
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

        full_text_embed = discord.Embed(
            title=f"Full Text — {product_type}",
            description=parsed["raw_text"][:4096],
            color=discord.Color.dark_gray(),
        )
        target = thread or channel
        await safe_send(
            target, context=f"tropical full text ({product_id})", embed=full_text_embed
        )

    async def route_from_product_id(
        self, product_id: str, raw_text: str = None, source: str = "IEMBot"
    ):
        pil_prefix = _classify_product(product_id)
        if not pil_prefix:
            return False

        await self.post_tropical_product(product_id, raw_text or "", pil_prefix, source)
        return True


async def setup(bot: commands.Bot):
    await bot.add_cog(TropicalCog(bot))
