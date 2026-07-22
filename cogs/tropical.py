"""NHC Tropical cyclone product auto-poster."""

import logging
import re
from datetime import datetime, timezone

import discord
from discord.ext import commands

from config import IEM_NWSTEXT_URL
from utils.http import http_get_bytes

logger = logging.getLogger("spc_bot")

DEV_CHANNEL_ID = 1336294580743704607
PROD_CHANNEL_ID = 981540312688230420

TROPICAL_PILS = {"TCV", "TCD", "TWD", "TCU", "TWO", "TCE", "TCP"}
TROPICAL_OFFICES = {"KNHC", "KTPC", "PHFO"}

_NHC_LABEL_RE = re.compile(
    r"(?:NATIONAL HURRICANE CENTER|NHC|TROPICAL|HURRICANE|TROPICAL STORM)", re.IGNORECASE
)

STORM_EMOJI = {
    "TROPICAL DEPRESSION": "🌨️",
    "TROPICAL STORM": "🌧️",
    "HURRICANE": "🌀",
    "MAJOR HURRICANE": "🌀",
    "POTENTIAL TROPICAL CYCLONE": "⚠️",
    "POST-TROPICAL CYCLONE": "🌬️",
    "SUBTROPICAL DEPRESSION": "🌨️",
    "SUBTROPICAL STORM": "🌧️",
    "REMNANTS": "💨",
}

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
    "TCV": "ADVISORY",
    "TCD": "DISCUSSION",
    "TWD": "TROPICAL WEATHER DISCUSSION",
    "TWO": "TROPICAL WEATHER OUTLOOK",
    "TCU": "UPDATE",
    "TCE": "POSITION ESTIMATE",
    "TCP": "PROBABILITIES",
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

    summary_lines = []
    in_summary = False
    for line in lines[body_start:]:
        if "SUMMARY OF" in line.upper() or "SUMMARY INFORMATION" in line.upper():
            in_summary = True
        if in_summary:
            summary_lines.append(line)
            if "FORECAST POSITIONS AND MAX WINDS" in line.upper():
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
        self._posted.add(dedup_key)
        if len(self._posted) > 1000:
            self._posted.clear()

        product_type = pil_prefix or _classify_product(product_id)
        if not product_type:
            return

        parsed = await _fetch_nhc_product(product_id)
        if not parsed:
            return

        channel_id = DEV_CHANNEL_ID
        channel = self.bot.get_channel(channel_id)
        if not channel:
            logger.warning(f"Tropical channel {channel_id} not found")
            return

        storm_type = parsed["storm_type"]
        storm_name = parsed["storm_name"]
        emoji = STORM_EMOJI.get(storm_type, "🌀")

        title = f"{emoji} NHC {product_type}"
        if storm_name:
            title += f" — {storm_name}"
        if storm_type:
            title += f" ({storm_type})"

        embed = discord.Embed(
            title=title,
            color=discord.Color.orange(),
            timestamp=datetime.now(timezone.utc),
        )

        for line in parsed.get("summary", "").splitlines()[:5]:
            clean = line.strip()
            if clean:
                embed.add_field(name="Summary", value=clean[:1024], inline=False)
                break

        if parsed["summary"]:
            full_summary = parsed["summary"][:2048]
            embed.description = full_summary

        embed.set_footer(text=f"{source} | {product_id}")

        try:
            await channel.send(embed=embed)
            logger.info(f"Posted tropical {product_type} for {storm_name or product_id}")
        except Exception as e:
            logger.exception(f"Failed to post tropical product: {e}")

    async def route_from_product_id(
        self, product_id: str, raw_text: str = None, source: str = "IEMBot"
    ):
        pil_prefix = _classify_product(product_id)
        if not pil_prefix:
            return False

        await self.post_tropical_product(product_id, raw_text or "", pil_prefix, source)
        return True

    @discord.app_commands.command(
        name="nwws_buffer",
        description="Show last N NWWS messages in ring buffer (debug)",
    )
    @discord.app_commands.describe(count="Number to show (default 5)")
    async def nwws_buffer_slash(self, interaction: discord.Interaction, count: int = 5):
        await interaction.response.defer(ephemeral=True)
        try:
            from cogs.nwws import NWWS_RING_BUFFER

            entries = list(NWWS_RING_BUFFER)[-max(1, min(count, 50)) :]
            if not entries:
                await interaction.followup.send("Buffer is empty.", ephemeral=True)
                return
            lines = []
            for e in entries:
                pil = e.get("afos_pil", "?")
                pid = e.get("product_id", "?")[-40:]
                headline = e.get("headline", "")[:80]
                lines.append("`{}` `{}` {}".format(pil, pid, headline))
            await interaction.followup.send(
                "Last {} NWWS messages:\n{}".format(len(entries), "\n".join(lines)),
                ephemeral=True,
            )
        except Exception as ex:
            await interaction.followup.send("Error: {}".format(ex), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TropicalCog(bot))
