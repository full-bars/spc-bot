# cogs/fronts.py
import logging

import discord
from discord.ext import commands

logger = logging.getLogger("spc_bot.fronts")

FRONTS_IMAGE_URL = "https://www.wpc.ncep.noaa.gov/sfc/usfntsfc21wbg.gif"
FRONTS_PAGE_URL = "https://www.wpc.ncep.noaa.gov/#page=frt"


class FrontsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

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


async def setup(bot: commands.Bot):
    await bot.add_cog(FrontsCog(bot))
