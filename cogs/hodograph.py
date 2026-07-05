# cogs/hodograph.py
import asyncio
import difflib
import logging
import os
import time

import discord
from discord.ext import commands

from lib.vad_plotter.vad import vad_plotter
from lib.vad_plotter.vad_reader import download_vads_batch
from lib.vad_plotter.params import compute_parameters
from lib.vad_plotter.plot import plot_hodograph_gif
from lib.vad_plotter.wsr88d import _radar_info, RADAR_NAMES
from utils.worker_pool import get_hodo_executor

logger = logging.getLogger("spc_bot")


def _log_task_exception(task: asyncio.Task) -> None:
    try:
        exc = task.exception()
    except (asyncio.CancelError, RuntimeError):
        return
    if exc:
        logger.error("Background task failed", exc_info=exc)


VALID_RADARS = list(_radar_info.keys())
HODO_OUTPUT_DIR = os.path.join("cache", "hodographs")
HODO_GIF_CACHE_DIR = os.path.join("cache", "hodograph_gifs")
VAD_SCRIPT = os.path.join("lib", "vad_plotter", "vad.py")
MAX_GIF_FRAMES = 20
MAX_GIF_SIZE_MB = 25
DEFAULT_LOOP_FRAMES = 6


async def _background_cache_hodo(cache_key: str, raw_text: str, site: str):
    """Fire-and-forget: cache raw_text + prefetch AI analysis using Gemini."""
    try:
        from utils.state_store import get_product_cache, set_product_cache
        from utils.ai import call_gemini

        await set_product_cache(f"raw_text_{cache_key}", raw_text, ttl=3600)

        existing = await get_product_cache(cache_key)
        if existing:
            return

        result = await call_gemini(
            "You are an expert severe weather meteorologist. Analyze this "
            f"radar-derived VAD wind profile (hodograph) at {site}. "
            "Discuss the wind shear profile, low-level shear/SRH, and what "
            "they imply about storm organization and potential for rotation. "
            "Limit to 3 sentences.\n\n"
            f"DATA:\n{raw_text}"
        )
        if result:
            await set_product_cache(cache_key, result, ttl=86400)
            logger.debug(f"[HODO] AI summary cached for {cache_key}")
    except Exception as e:
        logger.debug(f"[HODO] Background cache failed for {cache_key}: {e}")


class HodographPlotView(discord.ui.View):
    def __init__(self, cache_key: str):
        super().__init__(timeout=None)
        button = discord.ui.Button(
            label="AI Analysis",
            style=discord.ButtonStyle.secondary,
            custom_id=f"ai_snd:{cache_key}",
        )
        self.add_item(button)


async def generate_hodograph(interaction: discord.Interaction, site: str):
    """Run vad.py in a ProcessPoolExecutor and send the resulting image."""
    os.makedirs(HODO_OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(HODO_OUTPUT_DIR, f"{site.lower()}_hodograph.png")

    cache_valid = False
    if os.path.exists(output_path):
        file_age = time.time() - os.path.getmtime(output_path)
        if file_age < 300:
            cache_valid = True
            logger.info(f"[HODO] Using cached hodograph for {site} ({file_age:.0f}s old)")

    params = None
    if not cache_valid:
        logger.info(f"[HODO] Generating hodograph for {site} in executor pool")
        try:
            params = await asyncio.wait_for(
                vad_plotter(
                    site,
                    "right-mover",
                    None,
                    None,
                    output_path,
                    None,
                    None,
                    False,
                    False,
                    executor=get_hodo_executor(),
                ),
                timeout=60,
            )
        except asyncio.TimeoutError:
            logger.warning(f"[HODO] vad_plotter timed out for {site}")
            try:
                await interaction.followup.send(
                    f"Timed out fetching data for `{site}`. The radar may be offline or have no recent VWP data.",
                    ephemeral=True,
                )
            except discord.NotFound:
                logger.debug(
                    f"[HODO] Could not send timeout message for {site}: Interaction expired"
                )
            return
        except Exception as e:
            logger.error(f"[HODO] vad_plotter failed for {site}: {e}")
            try:
                await interaction.followup.send(
                    f"Could not generate hodograph for `{site}`. The radar may not have recent data.",
                    ephemeral=True,
                )
            except discord.NotFound:
                logger.debug(f"[HODO] Could not send error message for {site}: Interaction expired")
            return

    if not os.path.exists(output_path):
        logger.error(f"[HODO] Output file not found after successful run for {site}")
        try:
            await interaction.followup.send(
                f"Hodograph image not generated for `{site}`.",
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

    label = f"**{site}** VWP Hodograph"
    location = RADAR_NAMES.get(site)
    if location:
        label = f"**{site} ({location})** VWP Hodograph"

    sent = False
    for attempt in range(2):
        try:
            if view:
                await interaction.followup.send(
                    content=label,
                    file=discord.File(output_path),
                    view=view,
                )
            else:
                await interaction.followup.send(
                    content=label,
                    file=discord.File(output_path),
                )
            sent = True
            break
        except discord.NotFound:
            logger.warning(f"[HODO] Failed to send final hodograph for {site}: Interaction expired")
            break
        except OSError as conn_err:
            if attempt == 0:
                logger.warning(
                    f"[HODO] Connection error sending hodograph for {site}, retrying: {conn_err}"
                )
                await asyncio.sleep(1)
            else:
                logger.error(f"[HODO] Retry also failed for {site}: {conn_err}")

    if sent and params and cache_key:
        t = asyncio.create_task(_background_cache_hodo(cache_key, summary, site))
        t.add_done_callback(_log_task_exception)


async def generate_hodogif(
    interaction: discord.Interaction,
    site: str,
    num_frames: int = DEFAULT_LOOP_FRAMES,
):
    """Generate an animated hodograph GIF loop from recent VAD scans.

    Each frame shows for 1s, last frame lingers 3s on the most recent scan.
    """
    os.makedirs(HODO_GIF_CACHE_DIR, exist_ok=True)
    output_path = os.path.join(HODO_GIF_CACHE_DIR, f"{site.lower()}_hodogif_{num_frames}f.gif")

    cache_valid = False
    if os.path.exists(output_path):
        file_age = time.time() - os.path.getmtime(output_path)
        if file_age < 600:
            cache_valid = True
            logger.info(f"[HODO] Using cached hodogif for {site}")

    if not cache_valid:
        logger.info(f"[HODO] Generating hodogif ({num_frames} frames) for {site}")
        try:
            vads = await asyncio.wait_for(
                download_vads_batch(site, max_frames=num_frames),
                timeout=120,
            )
        except asyncio.TimeoutError:
            logger.warning(f"[HODO] VAD batch download timed out for {site}")
            try:
                await interaction.followup.send(
                    f"Timed out fetching VWP data for `{site}`.",
                    ephemeral=True,
                )
            except discord.NotFound:
                pass
            return
        except Exception as e:
            logger.error(f"[HODO] VAD batch download failed for {site}: {e}")
            try:
                await interaction.followup.send(
                    f"Could not fetch VWP data for `{site}`: {e}",
                    ephemeral=True,
                )
            except discord.NotFound:
                pass
            return

        if not vads:
            try:
                await interaction.followup.send(
                    f"No recent VWP data available for `{site}`.",
                    ephemeral=True,
                )
            except discord.NotFound:
                pass
            return

        params_list = [compute_parameters(v, "right-mover") for v in vads]

        executor = get_hodo_executor()
        loop = asyncio.get_running_loop()
        try:
            success = await asyncio.wait_for(
                loop.run_in_executor(
                    executor,
                    plot_hodograph_gif,
                    vads,
                    params_list,
                    output_path,
                    False,
                    1000,
                    3000,
                ),
                timeout=120,
            )
        except asyncio.TimeoutError:
            logger.warning(f"[HODO] GIF generation timed out for {site}")
            try:
                await interaction.followup.send(
                    f"Timed out generating hodogif for `{site}`.",
                    ephemeral=True,
                )
            except discord.NotFound:
                pass
            return
        except Exception as e:
            logger.error(f"[HODO] GIF generation failed for {site}: {e}")
            try:
                await interaction.followup.send(
                    f"Failed to generate hodogif for `{site}`.",
                    ephemeral=True,
                )
            except discord.NotFound:
                pass
            return

        if not success or not os.path.exists(output_path):
            try:
                await interaction.followup.send(
                    f"Failed to generate hodogif for `{site}`.",
                    ephemeral=True,
                )
            except discord.NotFound:
                pass
            return

        file_size = os.path.getsize(output_path)
        if file_size > MAX_GIF_SIZE_MB * 1024 * 1024:
            logger.info(
                f"[HODO] GIF too large ({file_size / 1024 / 1024:.1f} MB), retrying with fewer frames"
            )
            if num_frames > 4:
                os.remove(output_path)
                await generate_hodogif(interaction, site, num_frames - 2)
                return
            else:
                try:
                    await interaction.followup.send(
                        f"Even a 4-frame GIF is too large ({file_size / 1024 / 1024:.1f} MB). "
                        "Try another radar.",
                        ephemeral=True,
                    )
                except discord.NotFound:
                    pass
                return

    logger.info(f"[HODO] Hodogif generated at {output_path}")

    location = RADAR_NAMES.get(site)
    if location:
        label = f"**{site} ({location})** VWP Hodograph Loop ({num_frames} frames, ~{num_frames * 5} min)"
    else:
        label = f"**{site}** VWP Hodograph Loop ({num_frames} frames, ~{num_frames * 5} min)"

    file_size = os.path.getsize(output_path)
    content = f"{label}\n{file_size / 1024 / 1024:.1f} MB"

    try:
        await interaction.followup.send(content=content, file=discord.File(output_path))
    except discord.NotFound:
        pass


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
                    f"`{site}` is not a recognized radar ID. Did you mean one of these?",
                    view=view,
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    f"`{site}` is not a recognized radar ID. Try a 4-letter NEXRAD code like `KTLX` or `KHOU`.",
                    ephemeral=True,
                )
            return

        try:
            await generate_hodograph(interaction, site)
        except Exception as e:
            logger.exception(f"[HODO] Unhandled error in /hodograph for {site}: {e}")
            try:
                await interaction.followup.send(
                    f"Unexpected error for `{site}`. Please try again.",
                    ephemeral=True,
                )
            except discord.HTTPException as send_err:
                logger.debug(f"[HODO] Could not send error message: {send_err}")

    @discord.app_commands.command(
        name="hodogif",
        description="Generate an animated VWP hodograph loop (default 6 frames, ~30 min)",
    )
    @discord.app_commands.describe(
        site="4-letter radar site ID (e.g. KTLX, KHOU, KNKX)",
        frames="Number of frames (default 6, ~5 min each)",
    )
    async def hodogif_slash(
        self, interaction: discord.Interaction, site: str, frames: int = DEFAULT_LOOP_FRAMES
    ):
        await interaction.response.defer(thinking=True)

        site = site.upper().strip()

        if site not in VALID_RADARS:
            suggestions = difflib.get_close_matches(site, VALID_RADARS, n=3, cutoff=0.5)
            if suggestions:
                view = RadarSuggestionView(suggestions)
                await interaction.followup.send(
                    f"`{site}` is not a recognized radar ID. Did you mean one of these?",
                    view=view,
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    f"`{site}` is not a recognized radar ID. Try a 4-letter NEXRAD code like `KTLX` or `KHOU`.",
                    ephemeral=True,
                )
            return

        frames = max(2, min(frames, MAX_GIF_FRAMES))
        try:
            await generate_hodogif(interaction, site, frames)
        except Exception as e:
            logger.exception(f"[HODO] Unhandled error in /hodogif for {site}: {e}")
            try:
                await interaction.followup.send(
                    f"Unexpected error for `{site}`. Please try again.",
                    ephemeral=True,
                )
            except discord.HTTPException as send_err:
                logger.debug(f"[HODO] Could not send error message: {send_err}")


async def setup(bot: commands.Bot):
    await bot.add_cog(HodographCog(bot))
