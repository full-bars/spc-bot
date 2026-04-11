# cogs/radar/views.py
"""Discord UI views and modals for the NEXRAD radar downloader."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import ButtonStyle, SelectOption
from discord.ui import Button, Modal, Select, TextInput, View

from cogs.radar.downloads import run_download, send_error
from cogs.radar.s3 import get_radar_sites, parse_z_time, resolve_z_range

logger = logging.getLogger("spc_bot")


# ── Modals ────────────────────────────────────────────────────────────────────


class ZRangeModal(Modal, title="Z-to-Z Time Range"):
    time_range = TextInput(
        label="Time range (e.g. 22Z-04Z or 22:30-04:15)",
        placeholder="22Z-04Z  or  1800Z-0600Z  or  22:30-04:15",
        required=True,
    )

    def __init__(
        self, radar_sites, date, messages_to_delete, original_user=None
    ):
        super().__init__()
        self.radar_sites = radar_sites
        self.date = date
        self.messages_to_delete = messages_to_delete
        self.original_user = original_user

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            raw = self.time_range.value.replace(" ", "")
            parts = raw.split("-")
            if len(parts) == 2:
                start_str, end_str = parts
            else:
                start_str = parts[0]
                end_str = "-".join(parts[1:])
            start_dt, end_dt, dates_to_query = resolve_z_range(
                start_str, end_str, self.date
            )
            logger.info(f"[RADAR] Z-range: {start_dt} to {end_dt}")
            await run_download(
                interaction,
                self.radar_sites,
                self.messages_to_delete,
                start_dt,
                end_dt,
                dates_to_query,
            )
        except ValueError as e:
            await send_error(
                interaction,
                "Invalid Time Range",
                str(e)
                or (
                    f"Could not parse `{self.time_range.value}`.\n"
                    f"Use format: `22Z-04Z` or `22:30-04:15`"
                ),
            )
        except Exception as e:
            await send_error(
                interaction, "Error", f"Something went wrong: {e}"
            )


class StartPlusDurationModal(Modal, title="Start Time + Duration"):
    start_time = TextInput(
        label="Start time in Z (e.g. 22Z or 18:30Z)",
        placeholder="22Z  or  1800Z  or  18:30",
        required=True,
    )
    duration = TextInput(
        label="Duration in hours (e.g. 6 or 2.5)",
        placeholder="6",
        required=True,
    )

    def __init__(
        self, radar_sites, date, messages_to_delete, original_user=None
    ):
        super().__init__()
        self.radar_sites = radar_sites
        self.date = date
        self.messages_to_delete = messages_to_delete
        self.original_user = original_user

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            start_dt = parse_z_time(self.start_time.value, self.date)
            hours = float(self.duration.value)
            if hours <= 0:
                await send_error(
                    interaction,
                    "Invalid Duration",
                    "Duration must be greater than 0 hours.",
                )
                return
            end_dt = start_dt + timedelta(hours=hours)
            dates_to_query = [self.date]
            if end_dt.date() > self.date.date():
                dates_to_query.append(self.date + timedelta(days=1))
            logger.info(
                f"[RADAR] Start+duration: {start_dt} + {hours}h = {end_dt}"
            )
            await run_download(
                interaction,
                self.radar_sites,
                self.messages_to_delete,
                start_dt,
                end_dt,
                dates_to_query,
            )
        except ValueError:
            await send_error(
                interaction,
                "Invalid Input",
                "Start time should be like `22Z` or `18:30`. "
                "Duration should be a number like `6` or `2.5`.",
            )
        except Exception as e:
            await send_error(
                interaction, "Error", f"Something went wrong: {e}"
            )


class ExplicitRangeModal(Modal, title="Explicit Date/Time Range"):
    start = TextInput(
        label="Start (YYYY-MM-DD HH:MM or HH:MMZ)",
        placeholder="2026-04-02 22:00  or  22:00Z",
        required=True,
    )
    end = TextInput(
        label="End (YYYY-MM-DD HH:MM or HH:MMZ)",
        placeholder="2026-04-03 04:00  or  04:00Z",
        required=True,
    )

    def __init__(
        self, radar_sites, date, messages_to_delete, original_user=None
    ):
        super().__init__()
        self.radar_sites = radar_sites
        self.date = date
        self.messages_to_delete = messages_to_delete
        self.original_user = original_user

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:

            def parse_field(val, reference_date):
                val = val.strip().upper().replace("Z", "")
                for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H", "%H:%M", "%H"):
                    try:
                        dt = datetime.strptime(val, fmt)
                        if fmt in ("%H:%M", "%H"):
                            dt = reference_date.replace(
                                hour=dt.hour,
                                minute=dt.minute,
                                second=0,
                                microsecond=0,
                            )
                        else:
                            dt = dt.replace(tzinfo=timezone.utc)
                        return dt.replace(tzinfo=timezone.utc)
                    except ValueError:
                        continue
                raise ValueError(f"Could not parse: `{val}`")

            start_dt = parse_field(self.start.value, self.date)
            end_dt = parse_field(self.end.value, self.date)

            if start_dt == end_dt:
                await send_error(
                    interaction,
                    "Invalid Range",
                    "Start and end times are the same.",
                )
                return

            if end_dt <= start_dt:
                end_dt += timedelta(days=1)

            dates_to_query = []
            d = start_dt.replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            while d.date() <= end_dt.date():
                dates_to_query.append(d)
                d += timedelta(days=1)

            logger.info(
                f"[RADAR] Explicit range: {start_dt} to {end_dt}"
            )
            await run_download(
                interaction,
                self.radar_sites,
                self.messages_to_delete,
                start_dt,
                end_dt,
                dates_to_query,
            )
        except ValueError as e:
            await send_error(
                interaction,
                "Invalid Input",
                f"{e}\n\nTry:\n- `2026-04-02 22:00` for full datetime\n"
                f"- `22:00Z` for time only (uses selected date)",
            )
        except Exception as e:
            await send_error(
                interaction, "Error", f"Something went wrong: {e}"
            )


class NumFilesModal(Modal, title="Number of Recent Files"):
    num = TextInput(
        label="How many recent files?",
        placeholder="10",
        required=True,
    )

    def __init__(
        self, radar_sites, date, messages_to_delete, original_user=None
    ):
        super().__init__()
        self.radar_sites = radar_sites
        self.date = date
        self.messages_to_delete = messages_to_delete
        self.original_user = original_user

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            n = int(self.num.value)
            if n < 1 or n > 200:
                await send_error(
                    interaction,
                    "Invalid Number",
                    "Please enter a number between 1 and 200.",
                )
                return
            now = datetime.now(timezone.utc)
            await run_download(
                interaction,
                self.radar_sites,
                self.messages_to_delete,
                start_dt=now,
                end_dt=now,
                dates_to_query=[self.date],
                max_files=n,
            )
        except ValueError:
            await send_error(
                interaction,
                "Invalid Number",
                "Enter a whole number between 1 and 200.",
            )
        except Exception as e:
            await send_error(
                interaction, "Error", f"Something went wrong: {e}"
            )


class DateModal(Modal, title="Enter Custom Date"):
    date_input = TextInput(
        label="Date (YYYY-MM-DD)",
        placeholder="e.g., 2025-05-13",
        required=True,
    )

    def __init__(
        self, radar_sites, messages_to_delete, original_user=None
    ):
        super().__init__()
        self.radar_sites = radar_sites
        self.messages_to_delete = messages_to_delete
        self.original_user = original_user

    async def on_submit(self, interaction: discord.Interaction):
        try:
            date = datetime.strptime(
                self.date_input.value, "%Y-%m-%d"
            ).replace(tzinfo=timezone.utc)
            view = TimeRangeView(
                self.radar_sites,
                date,
                self.messages_to_delete,
                original_user=self.original_user,
            )
            embed = discord.Embed(
                title=f"Selected: {', '.join(self.radar_sites)}",
                description=(
                    f"Date: {date.strftime('%Y-%m-%d')}\n"
                    f"Choose a time range option:"
                ),
                color=discord.Color.blue(),
            )
            await interaction.response.send_message(
                embed=embed, view=view
            )
            msg = await interaction.original_response()
            self.messages_to_delete.append(msg)
        except ValueError:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Invalid Date",
                    description="Please use format: YYYY-MM-DD",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )


class MultiRadarModal(Modal, title="Select Multiple Sites"):
    radar_input = TextInput(
        label="Radar Sites (e.g., TOKC TJUA)",
        placeholder="e.g., TOKC TJUA KTLX",
        required=True,
    )

    def __init__(
        self, available_sites, messages_to_delete, original_user=None
    ):
        super().__init__()
        self.available_sites = available_sites
        self.messages_to_delete = messages_to_delete
        self.original_user = original_user

    async def on_submit(self, interaction: discord.Interaction):
        entered_sites = self.radar_input.value.upper().split()
        valid_sites = [
            site
            for site in entered_sites
            if site in self.available_sites
        ]
        invalid_sites = [
            site
            for site in entered_sites
            if site not in self.available_sites
        ]
        if not valid_sites:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ No Valid Radar Sites",
                    description=(
                        "None of the entered radar sites were found.\n"
                        "Check the site codes and try again."
                    ),
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return
        view = DateSelectionView(
            valid_sites,
            self.messages_to_delete,
            original_user=self.original_user,
        )
        description = "Select a date for the data:"
        if invalid_sites:
            description += (
                f"\n\n⚠️ Skipped unknown sites: "
                f"`{'`, `'.join(invalid_sites)}`"
            )
        embed = discord.Embed(
            title=f"Selected: {', '.join(valid_sites)}",
            description=description,
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()
        self.messages_to_delete.append(msg)


class SearchModal(Modal, title="Search Radar Sites"):
    search_input = TextInput(
        label="Enter Radar Site Code (e.g., KT)",
        placeholder="e.g., KT for KTLX",
        required=True,
    )

    def __init__(
        self, radar_sites, messages_to_delete, original_user=None
    ):
        super().__init__()
        self.original_user = original_user
        self.radar_sites = radar_sites
        self.messages_to_delete = messages_to_delete

    async def on_submit(self, interaction: discord.Interaction):
        if (
            self.original_user
            and interaction.user != self.original_user
        ):
            await interaction.response.send_message(
                "This interaction is not yours.", ephemeral=True
            )
            return
        search_term = self.search_input.value.upper()
        filtered_sites = [
            site for site in self.radar_sites if search_term in site
        ]
        if not filtered_sites:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ No Matches Found",
                    description=(
                        f"No radar sites found matching `{search_term}`.\n"
                        f"Try a shorter or different search term."
                    ),
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return
        if search_term in self.radar_sites:
            view = DateSelectionView(
                [search_term],
                self.messages_to_delete,
                original_user=self.original_user,
            )
            embed = discord.Embed(
                title=f"Selected: {search_term}",
                description="Select a date for the data:",
                color=discord.Color.blue(),
            )
            await interaction.response.send_message(
                embed=embed, view=view
            )
            msg = await interaction.original_response()
            self.messages_to_delete.append(msg)
            return
        options = [
            SelectOption(label=site, value=site)
            for site in filtered_sites[:25]
        ]
        select = RadarSiteSelect(
            placeholder=f"Select a radar site ({len(options)} matches)...",
            options=options,
            messages_to_delete=self.messages_to_delete,
            original_user=self.original_user,
        )
        view = View()
        view.add_item(select)
        desc = f"Found {len(filtered_sites)} matches for `{search_term}`."
        if len(filtered_sites) > 25:
            desc += " Showing first 25 — refine your search for more."
        embed = discord.Embed(
            title="Select a Radar Site",
            description=desc,
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()
        self.messages_to_delete.append(msg)


# ── Views ─────────────────────────────────────────────────────────────────────


class TimeRangeView(View):
    def __init__(
        self, radar_sites, date, messages_to_delete, original_user=None
    ):
        super().__init__(timeout=300)
        self.original_user = original_user
        self.radar_sites = radar_sites
        self.date = date
        self.messages_to_delete = messages_to_delete

    async def interaction_check(
        self, interaction: discord.Interaction
    ) -> bool:
        if (
            self.original_user
            and interaction.user != self.original_user
        ):
            await interaction.response.send_message(
                "This interaction is not yours.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Last 1h", style=ButtonStyle.green, row=0)
    async def last_1h(
        self, interaction: discord.Interaction, button: Button
    ):
        await interaction.response.defer()
        now = datetime.now(timezone.utc)
        await run_download(
            interaction,
            self.radar_sites,
            self.messages_to_delete,
            start_dt=now - timedelta(hours=1),
            end_dt=now,
            dates_to_query=self._dates_for_hours(1),
        )

    @discord.ui.button(label="Last 2h", style=ButtonStyle.green, row=0)
    async def last_2h(
        self, interaction: discord.Interaction, button: Button
    ):
        await interaction.response.defer()
        now = datetime.now(timezone.utc)
        await run_download(
            interaction,
            self.radar_sites,
            self.messages_to_delete,
            start_dt=now - timedelta(hours=2),
            end_dt=now,
            dates_to_query=self._dates_for_hours(2),
        )

    @discord.ui.button(label="Last 3h", style=ButtonStyle.green, row=0)
    async def last_3h(
        self, interaction: discord.Interaction, button: Button
    ):
        await interaction.response.defer()
        now = datetime.now(timezone.utc)
        await run_download(
            interaction,
            self.radar_sites,
            self.messages_to_delete,
            start_dt=now - timedelta(hours=3),
            end_dt=now,
            dates_to_query=self._dates_for_hours(3),
        )

    @discord.ui.button(label="Last 4h", style=ButtonStyle.green, row=0)
    async def last_4h(
        self, interaction: discord.Interaction, button: Button
    ):
        await interaction.response.defer()
        now = datetime.now(timezone.utc)
        await run_download(
            interaction,
            self.radar_sites,
            self.messages_to_delete,
            start_dt=now - timedelta(hours=4),
            end_dt=now,
            dates_to_query=self._dates_for_hours(4),
        )

    @discord.ui.button(
        label="Z-to-Z Range", style=ButtonStyle.blurple, row=1
    )
    async def z_range(
        self, interaction: discord.Interaction, button: Button
    ):
        await interaction.response.send_modal(
            ZRangeModal(
                self.radar_sites,
                self.date,
                self.messages_to_delete,
                self.original_user,
            )
        )

    @discord.ui.button(
        label="Start + Duration", style=ButtonStyle.blurple, row=1
    )
    async def start_duration(
        self, interaction: discord.Interaction, button: Button
    ):
        await interaction.response.send_modal(
            StartPlusDurationModal(
                self.radar_sites,
                self.date,
                self.messages_to_delete,
                self.original_user,
            )
        )

    @discord.ui.button(
        label="Explicit Range", style=ButtonStyle.blurple, row=1
    )
    async def explicit_range(
        self, interaction: discord.Interaction, button: Button
    ):
        await interaction.response.send_modal(
            ExplicitRangeModal(
                self.radar_sites,
                self.date,
                self.messages_to_delete,
                self.original_user,
            )
        )

    @discord.ui.button(
        label="N Most Recent", style=ButtonStyle.grey, row=1
    )
    async def n_most_recent(
        self, interaction: discord.Interaction, button: Button
    ):
        await interaction.response.send_modal(
            NumFilesModal(
                self.radar_sites,
                self.date,
                self.messages_to_delete,
                self.original_user,
            )
        )

    def _dates_for_hours(self, hours):
        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=hours)
        dates = [self.date]
        if start.date() < self.date.date():
            dates.insert(0, self.date - timedelta(days=1))
        return dates


class DateSelectionView(View):
    def __init__(
        self, radar_sites, messages_to_delete, original_user=None
    ):
        super().__init__(timeout=300)
        self.original_user = original_user
        self.radar_sites = radar_sites
        self.messages_to_delete = messages_to_delete

    async def interaction_check(
        self, interaction: discord.Interaction
    ) -> bool:
        if (
            self.original_user
            and interaction.user != self.original_user
        ):
            await interaction.response.send_message(
                "This interaction is not yours.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Today", style=ButtonStyle.green)
    async def today(
        self, interaction: discord.Interaction, button: Button
    ):
        date = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        view = TimeRangeView(
            self.radar_sites,
            date,
            self.messages_to_delete,
            original_user=self.original_user,
        )
        embed = discord.Embed(
            title=f"Selected: {', '.join(self.radar_sites)}",
            description=(
                f"Date: {date.strftime('%Y-%m-%d')}\n"
                f"Choose a time range option:"
            ),
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()
        self.messages_to_delete.append(msg)

    @discord.ui.button(label="Yesterday", style=ButtonStyle.green)
    async def yesterday(
        self, interaction: discord.Interaction, button: Button
    ):
        date = (datetime.now(timezone.utc) - timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        view = TimeRangeView(
            self.radar_sites,
            date,
            self.messages_to_delete,
            original_user=self.original_user,
        )
        embed = discord.Embed(
            title=f"Selected: {', '.join(self.radar_sites)}",
            description=(
                f"Date: {date.strftime('%Y-%m-%d')}\n"
                f"Choose a time range option:"
            ),
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()
        self.messages_to_delete.append(msg)

    @discord.ui.button(label="Custom Date", style=ButtonStyle.grey)
    async def custom_date(
        self, interaction: discord.Interaction, button: Button
    ):
        await interaction.response.send_modal(
            DateModal(
                self.radar_sites,
                self.messages_to_delete,
                original_user=self.original_user,
            )
        )


class RadarSiteSelect(Select):
    def __init__(
        self,
        placeholder,
        options,
        messages_to_delete,
        original_user=None,
    ):
        super().__init__(placeholder=placeholder, options=options)
        self.messages_to_delete = messages_to_delete
        self.original_user = original_user

    async def callback(self, interaction: discord.Interaction):
        radar_site = self.values[0]
        view = DateSelectionView(
            [radar_site],
            self.messages_to_delete,
            original_user=self.original_user or interaction.user,
        )
        embed = discord.Embed(
            title=f"Selected Radar Site: {radar_site}",
            description="Select a date for the data:",
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()
        self.messages_to_delete.append(msg)


class RadarSiteView(View):
    def __init__(
        self, radar_sites, messages_to_delete, original_user
    ):
        super().__init__(timeout=300)
        self.original_user = original_user
        self.radar_sites = radar_sites
        self.messages_to_delete = messages_to_delete
        self.current_page = 0
        self.page_size = 25
        self._rebuild_items()

    def _rebuild_items(self):
        self.clear_items()
        start_idx = self.current_page * self.page_size
        end_idx = min(
            start_idx + self.page_size, len(self.radar_sites)
        )
        options = [
            SelectOption(label=site, value=site)
            for site in self.radar_sites[start_idx:end_idx]
        ]
        self.add_item(
            RadarSiteSelect(
                placeholder="Choose a radar site...",
                options=options,
                messages_to_delete=self.messages_to_delete,
                original_user=self.original_user,
            )
        )
        search_btn = Button(
            label="Search Radar Sites", style=ButtonStyle.grey
        )
        search_btn.callback = self._search_callback
        self.add_item(search_btn)
        multi_btn = Button(
            label="Select Multiple Sites", style=ButtonStyle.grey
        )
        multi_btn.callback = self._multi_callback
        self.add_item(multi_btn)
        if self.current_page > 0:
            prev_btn = Button(label="Previous", style=ButtonStyle.grey)
            prev_btn.callback = self._prev_callback
            self.add_item(prev_btn)
        if end_idx < len(self.radar_sites):
            next_btn = Button(label="Next", style=ButtonStyle.grey)
            next_btn.callback = self._next_callback
            self.add_item(next_btn)

    async def interaction_check(
        self, interaction: discord.Interaction
    ) -> bool:
        if interaction.user != self.original_user:
            await interaction.response.send_message(
                "This interaction is not yours.", ephemeral=True
            )
            return False
        return True

    async def _search_callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(
            SearchModal(
                self.radar_sites,
                self.messages_to_delete,
                original_user=self.original_user,
            )
        )

    async def _multi_callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(
            MultiRadarModal(
                self.radar_sites,
                self.messages_to_delete,
                original_user=self.original_user,
            )
        )

    async def _prev_callback(self, interaction: discord.Interaction):
        self.current_page -= 1
        self._rebuild_items()
        embed = discord.Embed(
            title="AWS NEXRAD Data Downloader",
            description=(
                f"Select a radar site "
                f"({len(self.radar_sites)} available):"
            ),
            color=discord.Color.blue(),
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def _next_callback(self, interaction: discord.Interaction):
        self.current_page += 1
        self._rebuild_items()
        embed = discord.Embed(
            title="AWS NEXRAD Data Downloader",
            description=(
                f"Select a radar site "
                f"({len(self.radar_sites)} available):"
            ),
            color=discord.Color.blue(),
        )
        await interaction.response.edit_message(embed=embed, view=self)


class StartView(View):
    def __init__(self, original_user):
        super().__init__(timeout=300)
        self.original_user = original_user
        self.messages_to_delete = []

    async def interaction_check(
        self, interaction: discord.Interaction
    ) -> bool:
        if interaction.user != self.original_user:
            await interaction.response.send_message(
                "This interaction is not yours.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(
        label="Start Download", style=ButtonStyle.green
    )
    async def start_download(
        self, interaction: discord.Interaction, button: Button
    ):
        today = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).replace(hour=0, minute=0, second=0, microsecond=0)
        radar_sites = await get_radar_sites(today)
        if not radar_sites:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ No Radar Sites Found",
                    description=(
                        "Could not retrieve radar sites from S3.\n"
                        "S3 may be unreachable or there is no data "
                        "for yesterday."
                    ),
                    color=discord.Color.red(),
                ),
                delete_after=15,
            )
            return
        view = RadarSiteView(
            radar_sites,
            self.messages_to_delete,
            original_user=self.original_user,
        )
        embed = discord.Embed(
            title="AWS NEXRAD Data Downloader",
            description=(
                f"Select a radar site "
                f"({len(radar_sites)} available):"
            ),
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()
        self.messages_to_delete.append(msg)

class TimeRangeView(View):
    """Standalone time range picker for quick-start /download with known sites."""
    def __init__(self, radar_sites, messages_to_delete, original_user):
        super().__init__(timeout=300)
        self.radar_sites = radar_sites
        self.messages_to_delete = messages_to_delete
        self.original_user = original_user
        self.date = datetime.now(timezone.utc)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.original_user:
            await interaction.response.send_message(
                "This interaction is not yours.", ephemeral=True
            )
            return False
        return True

    def _dates_for_hours(self, hours):
        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=hours)
        dates = [self.date]
        if start.date() < self.date.date():
            dates.insert(0, self.date - timedelta(days=1))
        return dates

    @discord.ui.button(label="Last 1h", style=ButtonStyle.green, row=0)
    async def last_1h(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()
        now = datetime.now(timezone.utc)
        await run_download(
            interaction, self.radar_sites, self.messages_to_delete,
            start_dt=now - timedelta(hours=1), end_dt=now,
            dates_to_query=self._dates_for_hours(1),
        )

    @discord.ui.button(label="Last 2h", style=ButtonStyle.green, row=0)
    async def last_2h(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()
        now = datetime.now(timezone.utc)
        await run_download(
            interaction, self.radar_sites, self.messages_to_delete,
            start_dt=now - timedelta(hours=2), end_dt=now,
            dates_to_query=self._dates_for_hours(2),
        )

    @discord.ui.button(label="Last 3h", style=ButtonStyle.green, row=0)
    async def last_3h(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()
        now = datetime.now(timezone.utc)
        await run_download(
            interaction, self.radar_sites, self.messages_to_delete,
            start_dt=now - timedelta(hours=3), end_dt=now,
            dates_to_query=self._dates_for_hours(3),
        )

    @discord.ui.button(label="Last 4h", style=ButtonStyle.green, row=0)
    async def last_4h(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()
        now = datetime.now(timezone.utc)
        await run_download(
            interaction, self.radar_sites, self.messages_to_delete,
            start_dt=now - timedelta(hours=4), end_dt=now,
            dates_to_query=self._dates_for_hours(4),
        )

    @discord.ui.button(label="10 Most Recent", style=ButtonStyle.grey, row=1)
    async def most_recent(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()
        now = datetime.now(timezone.utc)
        await run_download(
            interaction, self.radar_sites, self.messages_to_delete,
            start_dt=None, end_dt=None,
            dates_to_query=[now],
            max_files=10,
        )

