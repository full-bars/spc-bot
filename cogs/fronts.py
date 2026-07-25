# cogs/fronts.py
import hashlib
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import discord
from discord.ext import commands, tasks

from config import WEATHER_CHAT_CHANNEL_ID
from utils.http import http_get_bytes, http_head_meta
from utils.state_store import set_state

logger = logging.getLogger("spc_bot.fronts")

FRONTS_PAGE_URL = "https://www.wpc.ncep.noaa.gov/#page=frt"
FRONTS_SFC_DIR = "https://www.wpc.ncep.noaa.gov/sfc/"
FRONTS_CYCLES = ["00", "03", "06", "09", "12", "15", "18", "21"]  # 3-hourly analysis cycles


async def get_current_fronts_url() -> tuple:
    """Get the latest WPC surface fronts analysis by checking Last-Modified of all cycles.

    Returns: (url, last_modified_datetime) or (None, None) if discovery fails.
    """
    try:
        latest_url = None
        latest_modified = None

        for cycle in FRONTS_CYCLES:
            url = f"{FRONTS_SFC_DIR}usfntsfc{cycle}wbg.gif"
            meta = await http_head_meta(url, timeout=15)
            if not meta or not meta.get("last_modified"):
                logger.debug(f"{cycle}Z: No Last-Modified header or failed HEAD")
                continue

            try:
                # Parse HTTP date header: "Thu, 28 May 2026 22:33:11 GMT"
                mod_time = parsedate_to_datetime(meta["last_modified"])
                logger.debug(f"{cycle}Z: Last-Modified={mod_time.isoformat()}")
                if latest_modified is None or mod_time > latest_modified:
                    logger.debug(
                        f"{cycle}Z: New latest (was: {latest_modified.isoformat() if latest_modified else 'None'})"
                    )
                    latest_modified = mod_time
                    latest_url = url
            except Exception as e:
                logger.debug(f"Failed to parse Last-Modified for {cycle}Z: {e}")
                continue

        if latest_url:
            logger.info(
                f"Latest fronts: {latest_url.split('/')[-1]} (modified {latest_modified.isoformat()})"
            )
            return latest_url, latest_modified

        logger.warning("No valid fronts cycles found via HEAD requests")
        return None, None
    except Exception as e:
        logger.warning(f"Error discovering current fronts: {e}")
        return None, None


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

        image_url, last_modified = await get_current_fronts_url()
        logger.info(f"/fronts command: discovered URL={image_url}, modified={last_modified}")
        if not image_url:
            await interaction.followup.send("Failed to fetch WPC surface fronts. Try again later.")
            return

        image_data, status = await http_get_bytes(image_url, timeout=15)
        if not image_data:
            logger.warning(
                f"/fronts command: Failed to download image from {image_url} (HTTP {status})"
            )
            await interaction.followup.send(
                "Failed to download WPC surface fronts image. Try again later."
            )
            return

        embed = discord.Embed(
            title="WPC Surface Fronts",
            description="Latest surface fronts analysis from the Weather Prediction Center",
            url=FRONTS_PAGE_URL,
            color=discord.Color.blue(),
        )
        import io

        file = discord.File(io.BytesIO(image_data), filename="fronts.gif")
        embed.set_image(url="attachment://fronts.gif")
        footer_text = "Source: WPC (wpc.ncep.noaa.gov)"
        if last_modified:
            footer_text += f" • Released {last_modified.strftime('%H:%M UTC')}"
        embed.set_footer(text=footer_text)

        await interaction.followup.send(embed=embed, file=file)

    @tasks.loop(minutes=15)
    async def fronts_loop(self):
        try:
            await self.bot.wait_until_ready()
            if not self.bot.state.is_primary:
                logger.debug("Fronts loop: Not primary, skipping")
                return

            channel = self.bot.get_channel(WEATHER_CHAT_CHANNEL_ID)
            if not channel:
                logger.warning("Weather Chat channel not found for fronts_loop")
                return

            image_url, last_modified = await get_current_fronts_url()
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
                logger.debug(f"Fronts image unchanged ({image_url.split('/')[-1]}) — skipping post")
                return

            embed = discord.Embed(
                title="WPC Surface Fronts",
                description="Latest surface fronts analysis from the Weather Prediction Center",
                url=FRONTS_PAGE_URL,
                color=discord.Color.blue(),
                timestamp=datetime.now(timezone.utc),
            )
            import io

            file = discord.File(io.BytesIO(image_data), filename="fronts.gif")
            embed.set_image(url="attachment://fronts.gif")
            footer_text = "Source: WPC (wpc.ncep.noaa.gov)"
            if last_modified:
                footer_text += f" • Released {last_modified.strftime('%H:%M UTC')}"
            embed.set_footer(text=footer_text)

            await channel.send(embed=embed, file=file)
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
