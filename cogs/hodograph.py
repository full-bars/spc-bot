# cogs/hodograph.py
import asyncio
import difflib
import logging
import os

import discord
from discord.ext import commands

from lib.vad_plotter.wsr88d import _radar_info

logger = logging.getLogger("spc_bot")

VALID_RADARS = list(_radar_info.keys())
HODO_OUTPUT_DIR = os.path.join("cache", "hodographs")
VAD_SCRIPT = os.path.join("lib", "vad_plotter", "vad.py")


async def generate_hodograph(interaction: discord.Interaction, site: str):
    """Run vad.py in a ProcessPoolExecutor and send the resulting image."""
    os.makedirs(HODO_OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(HODO_OUTPUT_DIR, f"{site.lower()}_hodograph.png")

    logger.info(f"[HODO] Generating hodograph for {site} in executor pool")

    from cogs.sounding_utils import _get_plot_executor
    from lib.vad_plotter.vad import vad_plotter
    
    loop = asyncio.get_running_loop()
    try:
        await asyncio.wait_for(
            loop.run_in_executor(
                _get_plot_executor(),
                vad_plotter,
                site,           # radar_id
                'right-mover',  # storm_motion
                None,           # sfc_wind
                None,           # time
                output_path,    # fname
                None,           # local_path
                None,           # cache_path
                False,          # web
                False           # fixed
            ),
            timeout=60
        )
    except asyncio.TimeoutError:
        logger.exception(f"[HODO] vad_plotter timed out for {site}")
        await interaction.followup.send(
            f"⏱️ Timed out fetching data for `{site}`. The radar may be offline or have no recent VWP data.",
            ephemeral=True,
        )
        return
    except Exception as e:
        logger.error(f"[HODO] vad_plotter failed for {site}: {e}")
        await interaction.followup.send(
            f"⚠️ Could not generate hodograph for `{site}`. The radar may not have recent data.",
            ephemeral=True,
        )
        return

    if not os.path.exists(output_path):
        logger.error(f"[HODO] Output file not found after successful run for {site}")
        await interaction.followup.send(
            f"⚠️ Hodograph image not generated for `{site}`.",
            ephemeral=True,
        )
        return

    logger.info(f"[HODO] Hodograph generated at {output_path}")
    await interaction.followup.send(
        content=f"**{site}** VWP Hodograph",
        file=discord.File(output_path),
    )


class RadarSuggestionView(discord.ui.View):
    def __init__(self, suggestions: list[str]):
        super().__init__(timeout=60)
        for site in suggestions:
            button = discord.ui.Button(
                label=site,
                style=discord.ButtonStyle.primary,
                custom_id=f"hodo_{site}",
            )
            button.callback = self._make_callback(site)
            self.add_item(button)

    def _make_callback(self, site: str):
        async def callback(interaction: discord.Interaction):
            await interaction.response.defer(thinking=True)
            for item in self.children:
                item.disabled = True
            await interaction.message.edit(view=self)
            await generate_hodograph(interaction, site)
        return callback


class HodographCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @discord.app_commands.command(
        name="hodograph",
        description="Generate a VWP hodograph for a NEXRAD or TDWR site",
    )
    @discord.app_commands.describe(site="4-letter radar site ID (e.g. KTLX, KHOU, KNKX)")
    async def hodograph_slash(self, interaction: discord.Interaction, site: str):
        await interaction.response.defer(thinking=True)

        site = site.upper().strip()

        if site not in VALID_RADARS:
            suggestions = difflib.get_close_matches(site, VALID_RADARS, n=3, cutoff=0.5)
            if suggestions:
                view = RadarSuggestionView(suggestions)
                await interaction.followup.send(
                    f"❌ `{site}` is not a recognized radar ID. Did you mean one of these?",
                    view=view,
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    f"❌ `{site}` is not a recognized radar ID. Try a 4-letter NEXRAD code like `KTLX` or `KHOU`.",
                    ephemeral=True,
                )
            return

        try:
            await generate_hodograph(interaction, site)
        except Exception as e:
            logger.exception(f"[HODO] Unhandled error in /hodograph for {site}: {e}")
            try:
                await interaction.followup.send(
                    f"⚠️ Unexpected error for `{site}`. Please try again.",
                    ephemeral=True,
                )
            except discord.HTTPException as send_err:
                logger.debug(f"[HODO] Could not send error message: {send_err}")


async def setup(bot: commands.Bot):
    await bot.add_cog(HodographCog(bot))
