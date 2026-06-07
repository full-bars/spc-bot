# cogs/hodograph.py
import asyncio
import difflib
import logging
import os

import discord
from discord.ext import commands

from lib.vad_plotter.vad import vad_plotter
from lib.vad_plotter.wsr88d import _radar_info
from utils.worker_pool import get_hodo_executor

logger = logging.getLogger("spc_bot")

VALID_RADARS = list(_radar_info.keys())
HODO_OUTPUT_DIR = os.path.join("cache", "hodographs")
VAD_SCRIPT = os.path.join("lib", "vad_plotter", "vad.py")


class HodographPlotView(discord.ui.View):
    def __init__(self, cache_key: str):
        super().__init__(timeout=None)
        button = discord.ui.Button(
            label="🪄 AI Analysis",
            style=discord.ButtonStyle.secondary,
            custom_id=f"ai_snd:{cache_key}",
        )
        self.add_item(button)


async def generate_hodograph(interaction: discord.Interaction, site: str):
    """Run vad.py in a ProcessPoolExecutor and send the resulting image."""
    os.makedirs(HODO_OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(HODO_OUTPUT_DIR, f"{site.lower()}_hodograph.png")

    logger.info(f"[HODO] Generating hodograph for {site} in executor pool")

    try:
        params = await asyncio.wait_for(
            vad_plotter(
                site,  # radar_id
                "right-mover",  # storm_motion
                None,  # sfc_wind
                None,  # time
                output_path,  # fname
                None,  # local_path
                None,  # cache_path
                False,  # web
                False,  # fixed
                executor=get_hodo_executor(),
            ),
            timeout=60,
        )
    except asyncio.TimeoutError:
        logger.warning(f"[HODO] vad_plotter timed out for {site}")
        try:
            await interaction.followup.send(
                f"⏱️ Timed out fetching data for `{site}`. The radar may be offline or have no recent VWP data.",
                ephemeral=True,
            )
        except discord.NotFound:
            logger.debug(f"[HODO] Could not send timeout message for {site}: Interaction expired")
        return
    except Exception as e:
        logger.error(f"[HODO] vad_plotter failed for {site}: {e}")
        try:
            await interaction.followup.send(
                f"⚠️ Could not generate hodograph for `{site}`. The radar may not have recent data.",
                ephemeral=True,
            )
        except discord.NotFound:
            logger.debug(f"[HODO] Could not send error message for {site}: Interaction expired")
        return

    if not os.path.exists(output_path):
        logger.error(f"[HODO] Output file not found after successful run for {site}")
        try:
            await interaction.followup.send(
                f"⚠️ Hodograph image not generated for `{site}`.",
                ephemeral=True,
            )
        except discord.NotFound:
            logger.debug(
                f"[HODO] Could not send file-not-found message for {site}: Interaction expired"
            )
        return

    logger.info(f"[HODO] Hodograph generated at {output_path}")

    view = None
    if params:
        summary = "HODOGRAPH KINEMATIC PARAMETERS:\n\n"
        summary += f"Storm Motion (Right-Mover): {params.get('storm_motion')}\n"
        summary += f"Bulk Shear: 0-1km {params.get('shear_mag_1000m')} kts | 0-3km {params.get('shear_mag_3000m')} kts | 0-6km {params.get('shear_mag_6000m')} kts\n"
        summary += f"SRH: 0-500m {params.get('srh_500m')} | 0-1km {params.get('srh_1000m')} | 0-3km {params.get('srh_3000m')}\n"
        summary += f"SR Flow: 0-500m {params.get('sr_flow_500m')} kts | 0-1km {params.get('sr_flow_1000m')} kts | 0-3km {params.get('sr_flow_3000m')} kts\n"
        summary += f"Critical Angle: {params.get('critical')} degrees\n"

        import datetime

        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M")
        cache_key = f"hodo_{site}_{now_str}"
        view = HodographPlotView(cache_key)

        from cogs.ai_summaries import ensure_sounding_summary

        t = asyncio.create_task(ensure_sounding_summary(cache_key, raw_text=summary))
        t.add_done_callback(
            lambda t: logger.debug(
                f"[HODO] Proactive AI summary generation finished for {cache_key}"
            )
        )

    try:
        if view:
            await interaction.followup.send(
                content=f"**{site}** VWP Hodograph", file=discord.File(output_path), view=view
            )
        else:
            await interaction.followup.send(
                content=f"**{site}** VWP Hodograph",
                file=discord.File(output_path),
            )
    except discord.NotFound:
        logger.warning(f"[HODO] Failed to send final hodograph for {site}: Interaction expired")


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
            try:
                await interaction.response.defer(thinking=True)
                for item in self.children:
                    item.disabled = True
                # Use edit_original_response instead of message.edit to properly handle deferred interaction
                await interaction.edit_original_response(view=self)
                await generate_hodograph(interaction, site)
            except discord.NotFound:
                logger.debug(f"[HODO] Suggestion callback failed for {site}: Interaction expired")
            except Exception as e:
                logger.exception(f"[HODO] Error in suggestion callback for {site}: {e}")

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
