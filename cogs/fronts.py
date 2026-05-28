# cogs/fronts.py
import hashlib
import logging
import re
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from utils.http import http_get_bytes, http_get_text
from utils.state_store import set_state

logger = logging.getLogger("spc_bot.fronts")

FRONTS_PAGE_URL = "https://www.wpc.ncep.noaa.gov/#page=frt"
FRONTS_SFC_DIR = "https://www.wpc.ncep.noaa.gov/sfc/"
WEATHER_CHAT_CHANNEL_ID = 1016454846326513876


async def get_current_fronts_url() -> str:
    """Fetch the latest WPC surface fronts analysis image URL by checking directory."""
    try:
        page = await http_get_text(FRONTS_SFC_DIR, timeout=10)
        if not page:
            logger.warning("Failed to fetch WPC sfc directory")
            return None

        # Look for awcsfc* files with last-modified times, prefer the current/latest
        # Pattern: awcsfcwbg.gif (current) or awcsfc00-21wbg.gif (specific times)
        files = re.findall(r'awcsfc\d*wbg\.gif', page)
        if not files:
            logger.warning("No awcsfc files found in WPC directory")
            return None

        # Prefer awcsfcwbg.gif (current) if available, otherwise use the last numbered one
        if 'awcsfcwbg.gif' in files:
            return f"{FRONTS_SFC_DIR}awcsfcwbg.gif"

        # Fall back to the latest numbered file
        files = sorted(set(files))
        return f"{FRONTS_SFC_DIR}{files[-1]}" if files else None
    except Exception as e:
        logger.warning(f"Error discovering fronts URL: {e}")
        return None


class FrontsCog(commands.Cog):
    MANAGED_TASK_NAMES = [("fronts_loop", "fronts_loop")]

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.fronts_loop.start()

    @discord.app_commands.command(
        name="fronts",
        description="Fetch the latest surface fronts map from WPC",
    )
    async def fronts(self, interaction: discord.Interaction):
        await interaction.response.defer()

        image_url = await get_current_fronts_url()
        if not image_url:
            await interaction.followup.send("Failed to fetch WPC surface fronts. Try again later.")
            return

        embed = discord.Embed(
            title="WPC Surface Fronts",
            description="Latest surface fronts analysis from the Weather Prediction Center",
            url=FRONTS_PAGE_URL,
            color=discord.Color.blue(),
        )
        embed.set_image(url=image_url)
        embed.set_footer(text="Source: WPC (wpc.ncep.noaa.gov)")

        await interaction.followup.send(embed=embed)

    @tasks.loop(minutes=15)
    async def fronts_loop(self):
        try:
            await self.bot.wait_until_ready()
            if not self.bot.state.is_primary:
                return

            channel = self.bot.get_channel(WEATHER_CHAT_CHANNEL_ID)
            if not channel:
                logger.warning("Weather Chat channel not found for fronts_loop")
                return

            image_url = await get_current_fronts_url()
            if not image_url:
                logger.warning("Failed to discover current fronts product URL")
                return

            try:
                image_data, status = await http_get_bytes(image_url, timeout=15)
            except Exception as e:
                logger.warning(f"Failed to fetch fronts image: {e}")
                return

            if not image_data:
                logger.warning(f"Failed to fetch fronts image: HTTP {status}")
                return

            current_hash = hashlib.sha256(image_data).hexdigest()
            last_hash = self.bot.state.last_fronts_hash

            if current_hash == last_hash:
                logger.debug("Fronts image unchanged — skipping post")
                return

            embed = discord.Embed(
                title="WPC Surface Fronts",
                description="Latest surface fronts analysis from the Weather Prediction Center",
                url=FRONTS_PAGE_URL,
                color=discord.Color.blue(),
                timestamp=datetime.now(timezone.utc),
            )
            embed.set_image(url=image_url)
            embed.set_footer(text="Source: WPC (wpc.ncep.noaa.gov)")

            await channel.send(embed=embed)
            self.bot.state.last_fronts_hash = current_hash
            await set_state("last_fronts_hash", current_hash)
            logger.info("Posted updated WPC surface fronts")

        except Exception as e:
            logger.exception(f"Fronts loop error: {e}")

    @fronts_loop.before_loop
    async def before_fronts_loop(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(FrontsCog(bot))
