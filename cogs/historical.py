# cogs/historical.py
"""Historical SPC outlook retrieval (archive: 2004-present)."""

import asyncio
import io
import logging
from datetime import date, datetime
from typing import Optional

import discord
from discord.ext import commands

from utils.http import http_get_bytes_conditional, http_head_ok

logger = logging.getLogger("spc_bot.historical")

ARCHIVE_BASE = "https://www.spc.noaa.gov/products/outlook/archive"
ARCHIVE_FLOOR = date(2004, 1, 1)
DAY3_PROB_FLOOR = date(2009, 1, 1)

# Issuance times ordered latest-first (for defaults)
ISSUANCE_TIMES = {1: ["2000", "1630", "1300"], 2: ["1730", "0600"], 3: ["0730"]}

# Possible bonus issuances on active days (discovered via HEAD sweep)
BONUS_TIMES = {"0600", "1800"}


def _build_url(year: int, yyyymmdd: str, day: int, product: str, hhmm: str) -> str:
    """Construct archive image URL.

    SPC changed archive format on March 3, 2026 from .gif to .html/.geojson.
    For dates before that, use legacy .gif URLs.
    For dates on/after that, archive no longer has image files — return None to trigger fallback.
    """
    # March 3, 2026 is when SPC switched formats
    cutoff = 20260303
    yyyymmdd_int = int(yyyymmdd)

    if yyyymmdd_int >= cutoff:
        # Archive no longer has image files for post-March-3-2026 dates
        return None

    # Legacy .gif URLs for pre-March-3-2026
    if day in (1, 2) and product == "categorical":
        return f"{ARCHIVE_BASE}/{year}/day{day}otlk_{yyyymmdd}_{hhmm}.gif"
    elif day in (1, 2) and product in ("tornado", "wind", "hail"):
        suffix = f"_{product[0] if product == 'tornado' else product[0]}"
        if product == "tornado":
            suffix = "_torn"
        return f"{ARCHIVE_BASE}/{year}/day{day}probotlk_{yyyymmdd}_{hhmm}{suffix}.gif"
    elif day == 3 and product == "categorical":
        return f"{ARCHIVE_BASE}/{year}/day3otlk_{yyyymmdd}_{hhmm}.gif"
    raise ValueError(f"Unsupported day/product combo: day{day}/{product}")


async def _discover_available_times(yyyymmdd: str, day: int, product: str) -> list[str]:
    """HEAD-sweep to find which issuance times have archived images."""
    year = yyyymmdd[:4]
    candidate_times = list(ISSUANCE_TIMES[day]) + list(BONUS_TIMES)

    tasks = []
    for hhmm in candidate_times:
        try:
            url = _build_url(int(year), yyyymmdd, day, product, hhmm)
            tasks.append(http_head_ok(url))
        except ValueError:
            continue

    results = await asyncio.gather(*tasks, return_exceptions=True)
    available = [t for t, ok in zip(candidate_times, results) if ok is True]
    # Re-order latest-first
    return sorted(available, reverse=True)


class TimeSelectView(discord.ui.View):
    """Select menu for choosing an available issuance time."""

    def __init__(self, date_str: str, day: int, product: str, available_times: list[str]):
        super().__init__(timeout=30)
        self.date_str = date_str
        self.day = day
        self.product = product
        self.selected_time: Optional[str] = None

        # Build select options
        options = []
        for hhmm in available_times:
            label = f"{hhmm[0:2]}:{hhmm[2:]}z"
            options.append(discord.SelectOption(label=label, value=hhmm))

        self.select.options = options

    @discord.ui.select(placeholder="Choose an issuance time:")
    async def select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.selected_time = select.values[0]
        await interaction.response.defer()
        self.stop()


async def _fetch_and_send(
    interaction: discord.Interaction, date_str: str, day: int, product: str, hhmm: str
):
    """Fetch historical outlook and send to Discord."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        await interaction.followup.send(
            "Invalid date format. Use YYYY-MM-DD (e.g., 2024-06-01).",
            ephemeral=True,
        )
        return

    # Validation
    if dt.date() < ARCHIVE_FLOOR:
        await interaction.followup.send(
            "SPC archive is accessible from 2004-01-01 onward.", ephemeral=True
        )
        return
    if dt.date() >= date.today():
        await interaction.followup.send(
            "Use `/spc1`, `/spc2`, or `/spc3` for current outlooks.", ephemeral=True
        )
        return

    # Day/product validation
    if product == "all" and day not in (1, 2):
        await interaction.followup.send(
            "'All' only available for Day 1 and Day 2 (Day 3 has no hazard breakdown).",
            ephemeral=True,
        )
        return
    if product in ("tornado", "wind", "hail") and day not in (1, 2):
        await interaction.followup.send(
            "Tornado/wind/hail products only available for Day 1 and Day 2.",
            ephemeral=True,
        )
        return

    yyyymmdd = dt.strftime("%Y%m%d")
    year = dt.year

    # Handle "all" product type
    if product == "all":
        products_to_fetch = ["categorical", "tornado", "wind", "hail"]
    else:
        products_to_fetch = [product]

    # Collect all products
    found = []
    for prod in products_to_fetch:
        # Try the requested time
        urls_to_try = []
        if hhmm:
            try:
                url = _build_url(year, yyyymmdd, day, prod, hhmm)
                if url:
                    urls_to_try = [url]
            except ValueError:
                continue  # Skip invalid product combos in "all" mode
        else:
            # Default: try canonical times
            for t in ISSUANCE_TIMES[day]:
                try:
                    url = _build_url(year, yyyymmdd, day, prod, t)
                    if url:
                        urls_to_try.append(url)
                except ValueError:
                    continue

        # Attempt fetches
        for url in urls_to_try:
            content, status, _ = await http_get_bytes_conditional(url, retries=1)
            if status == 200 and content:
                found.append((url, content))
                if hhmm:
                    break  # specific time requested — stop at first hit

    if not found:
        if product == "all":
            await interaction.followup.send(
                f"No products found for **{date_str}** Day {day}.",
                ephemeral=True,
            )
        elif hhmm:
            # Requested time not found — discover available and prompt
            logger.info(f"Requested time {hhmm} not found for {date_str} Day {day} {product}")
            available = await _discover_available_times(yyyymmdd, day, product)

            if available:
                # Prompt user with available times
                view = TimeSelectView(date_str, day, product, available)
                await interaction.followup.send(
                    f"**{hhmm}z not available for {date_str} Day {day} {product}.**\n"
                    f"Available issuance times:",
                    view=view,
                    ephemeral=True,
                    wait=True,
                )

                # Wait for user selection
                await view.wait()
                if view.selected_time:
                    # Re-fetch with selected time
                    await _fetch_and_send(interaction, date_str, day, product, view.selected_time)
                return
            else:
                await interaction.followup.send(
                    f"No {product} product found for **{date_str}** Day {day}. "
                    f"That date may have no issuance.",
                    ephemeral=True,
                )
                return
        else:
            # No canonical time found (very rare)
            await interaction.followup.send(
                f"No Day {day} {product} outlook found for **{date_str}**.",
                ephemeral=True,
            )
            return

    # Send response
    files = []
    for url, content in found[:10]:  # Discord 10-file cap
        fname = url.split("/")[-1]
        try:
            files.append(discord.File(io.BytesIO(content), filename=fname))
        except Exception as e:
            logger.warning(f"Failed to create File from {fname}: {e}")

    if files:
        label = f"{hhmm[0:2]}:{hhmm[2:]}z" if hhmm else "latest"
        if product == "all":
            title = f"**SPC Day {day} All Products — {date_str} {label}**"
        else:
            title = f"**SPC Day {day} {product.capitalize()} Outlook — {date_str} {label}**"
        await interaction.followup.send(
            title,
            files=files,
        )
    else:
        await interaction.followup.send(
            "Failed to load images.",
            ephemeral=True,
        )


class HistoricalCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @discord.app_commands.command(
        name="historical",
        description="Fetch historical SPC convective outlooks (archive: 2004-present)",
    )
    @discord.app_commands.describe(
        date="Date in YYYY-MM-DD format (e.g., 2024-06-01)",
        day="Outlook day (1, 2, or 3)",
        product="Product type",
        time="Issuance time in UTC (optional; defaults to latest available)",
    )
    @discord.app_commands.choices(
        day=[
            discord.app_commands.Choice(name="Day 1", value=1),
            discord.app_commands.Choice(name="Day 2", value=2),
            discord.app_commands.Choice(name="Day 3", value=3),
        ],
        product=[
            discord.app_commands.Choice(name="All (Cat + Hazards)", value="all"),
            discord.app_commands.Choice(name="Categorical", value="categorical"),
            discord.app_commands.Choice(name="Tornado", value="tornado"),
            discord.app_commands.Choice(name="Wind", value="wind"),
            discord.app_commands.Choice(name="Hail", value="hail"),
        ],
        time=[
            discord.app_commands.Choice(name="0600z", value="0600"),
            discord.app_commands.Choice(name="0730z", value="0730"),
            discord.app_commands.Choice(name="1300z", value="1300"),
            discord.app_commands.Choice(name="1630z", value="1630"),
            discord.app_commands.Choice(name="1730z", value="1730"),
            discord.app_commands.Choice(name="1800z (rare)", value="1800"),
            discord.app_commands.Choice(name="2000z", value="2000"),
        ],
    )
    async def historical_slash(
        self,
        interaction: discord.Interaction,
        date: str,
        day: int,
        product: str,
        time: Optional[str] = None,
    ):
        await interaction.response.defer(thinking=True)
        await _fetch_and_send(interaction, date, day, product, time)


async def setup(bot: commands.Bot):
    await bot.add_cog(HistoricalCog(bot))
