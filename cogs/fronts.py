# cogs/fronts.py
import hashlib
import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from utils.http import http_get_bytes
from utils.state_store import set_state

logger = logging.getLogger("spc_bot.fronts")

FRONTS_IMAGE_URL = "https://www.wpc.ncep.noaa.gov/sfc/usfntsfc21wbg.gif"
FRONTS_PAGE_URL = "https://www.wpc.ncep.noaa.gov/#page=frt"
WEATHER_CHAT_CHANNEL_ID = 1016454846326513876


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

        embed = discord.Embed(
            title="WPC Surface Fronts",
            description="Latest surface fronts analysis from the Weather Prediction Center",
            url=FRONTS_PAGE_URL,
            color=discord.Color.blue(),
        )
        embed.set_image(url=FRONTS_IMAGE_URL)
        embed.set_footer(text="Source: WPC (wpc.ncep.noaa.gov)")

        await interaction.followup.send(embed=embed)

    @tasks.loop(hours=3)
    async def fronts_loop(self):
        try:
            await self.bot.wait_until_ready()
            if not self.bot.state.is_primary:
                return

            channel = self.bot.get_channel(WEATHER_CHAT_CHANNEL_ID)
            if not channel:
                logger.warning("Weather Chat channel not found for fronts_loop")
                return

            try:
                image_data = await http_get_bytes(FRONTS_IMAGE_URL, timeout=15)
            except Exception as e:
                logger.warning(f"Failed to fetch fronts image: {e}")
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
            embed.set_image(url=FRONTS_IMAGE_URL)
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
