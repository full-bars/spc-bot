"""Discord UI Views for warning and tornado data displays.

Includes environmental evolution viewer, tornado photo carousel, and
tornado dashboard (summary + card navigation modes).
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import discord

from cogs.warning_format import _vtec_url

logger = logging.getLogger("spc_bot")


class EnvironmentalView(discord.ui.View):
    def __init__(self, event_id: str):
        super().__init__(timeout=None)  # Persistent
        self.event_id = event_id

    @discord.ui.button(label="View Environmental Evolution", style=discord.ButtonStyle.secondary, emoji="📊", custom_id="view_env_evo")
    async def view_env(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        from utils.events_db import get_events_db
        db = await get_events_db()
        async with db.execute(
            "SELECT gif_path, srh_0_1, location FROM significant_events WHERE event_id = ?",
            (self.event_id,)
        ) as cur:
            row = await cur.fetchone()

        if not row or not row["gif_path"]:
            # Check if there is an active mission
            recorder = interaction.client.get_cog("RecorderCog")
            if recorder:
                # We'd need a way to check active missions by event_id
                # For now, a generic message
                await interaction.followup.send(
                    "Environmental data is still being recorded or was not captured for this event. "
                    "Try again in 90 minutes.",
                    ephemeral=True
                )
                return

        # Post the GIF
        gif_path = row["gif_path"]
        if not os.path.exists(gif_path):
            await interaction.followup.send("Archive file no longer exists on the server.", ephemeral=True)
            return

        file = discord.File(gif_path, filename="evolution.gif")
        embed = discord.Embed(
            title=f"🌪️ Environmental Evolution - {row['location']}",
            description=f"**Peak 0-1km SRH**: {row['srh_0_1']:.0f} m2/s2",
            color=discord.Color.blue()
        )
        embed.set_image(url="attachment://evolution.gif")
        await interaction.followup.send(embed=embed, file=file, ephemeral=True)


class TornadoPhotoView(discord.ui.View):
    def __init__(self, urls: list, parent_view: discord.ui.View, location: str):
        super().__init__(timeout=300)
        self.urls = urls
        self.parent_view = parent_view
        self.location = location
        self.page = 0
        self.per_page = 4
        self._update_buttons()

    def _update_buttons(self):
        max_pages = (len(self.urls) + self.per_page - 1) // self.per_page
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= max_pages - 1

    def build_embeds(self) -> list[discord.Embed]:
        start = self.page * self.per_page
        end = start + self.per_page
        page_urls = self.urls[start:end]

        embeds = []
        for i, url in enumerate(page_urls):
            embed = discord.Embed(
                title=f"📸 Damage Photos: {self.location}" if i == 0 else None,
                color=discord.Color.teal(),
                timestamp=datetime.now(timezone.utc) if i == 0 else None
            )
            # Use proxy URL or original URL
            embed.set_image(url=url)

            if i == 0:
                max_pages = (len(self.urls) + self.per_page - 1) // self.per_page
                embed.set_footer(text=f"Page {self.page + 1} of {max_pages} ({len(self.urls)} total photos)")

            embeds.append(embed)

        return embeds

    @discord.ui.button(label="◀ Prev Page", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self._update_buttons()
        await interaction.response.edit_message(embeds=self.build_embeds(), view=self)

    @discord.ui.button(label="Next Page ▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        max_pages = (len(self.urls) + self.per_page - 1) // self.per_page
        self.page = min(max_pages - 1, self.page + 1)
        self._update_buttons()
        await interaction.response.edit_message(embeds=self.build_embeds(), view=self)

    @discord.ui.button(label="🔙 Back to Card", style=discord.ButtonStyle.primary)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=self.parent_view.build_card_embed(), embeds=[], view=self.parent_view)


class TornadoDashboardView(discord.ui.View):
    def __init__(self, events: list, title: str, mode: str = "card"):
        super().__init__(timeout=300)
        self.events = events
        self.title = title
        self.mode = mode  # "summary" or "card"
        self.index = 0    # Index for card mode

        # Group by date (UTC) for summary mode
        self.grouped = {}
        for e in events:
            dt = datetime.fromtimestamp(e['timestamp'], timezone.utc)
            date_str = dt.strftime("%Y-%m-%d")
            if date_str not in self.grouped:
                self.grouped[date_str] = []
            self.grouped[date_str].append(e)

        self.dates = sorted(list(self.grouped.keys()), reverse=True)
        self._update_items()

    def _update_items(self):
        self.clear_items()

        if self.mode == "summary":
            # Build select options for summary
            options = [discord.SelectOption(label="Summary Dashboard", value="summary", default=True)]
            for d in self.dates[:24]:
                options.append(discord.SelectOption(label=f"Events for {d}", value=d))

            select = discord.ui.Select(options=options, custom_id="date_select")
            select.callback = self.on_select
            self.add_item(select)
        else:
            # Card mode navigation
            self.add_item(self.first_btn)
            self.add_item(self.prev_btn)
            self.add_item(self.next_btn)
            self.add_item(self.last_btn)
            self.add_item(self.summary_btn)

            # Add Photos button if event has location and coords for DAT search
            e = self.events[self.index]
            if e.get("location") and e.get("coords"):
                self.add_item(self.photos_btn)

            self.first_btn.disabled = self.index <= 0
            self.prev_btn.disabled = self.index <= 0
            self.next_btn.disabled = self.index >= len(self.events) - 1
            self.last_btn.disabled = self.index >= len(self.events) - 1

        # Global Archive Button
        if self.events:
            min_ts = min(e['timestamp'] for e in self.events)
            max_ts = max(e['timestamp'] for e in self.events)
            min_dt = datetime.fromtimestamp(min_ts, timezone.utc).strftime("%Y-%m-%d")
            max_dt = (datetime.fromtimestamp(max_ts, timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")

            # Stable URL format using query parameters
            url = f"https://tornadoarchive.com/explorer/?start={min_dt}&end={max_dt}&domain=north_america"
            self.add_item(discord.ui.Button(label="Tornado Archive", url=url, style=discord.ButtonStyle.link))

    async def _render_map_if_needed(self, e: dict) -> Tuple[Optional[str], Optional[discord.File]]:
        """Helper to fetch geometry and render a local map if possible."""
        guid = e.get("dat_guid")
        if not guid:
            return None, None

        png_path = os.path.join("cache", f"track_{guid}.png")
        if os.path.exists(png_path):
            return f"attachment://track_{guid}.png", discord.File(png_path, filename=f"track_{guid}.png")

        # Not cached, try to render now
        from utils.dat_api import fetch_dat_track_geometry
        from utils.map_utils import render_tornado_track

        try:
            paths = await fetch_dat_track_geometry(guid)
            if paths:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, render_tornado_track, paths, png_path)
                if os.path.exists(png_path):
                    return f"attachment://track_{guid}.png", discord.File(png_path, filename=f"track_{guid}.png")
        except Exception as err:
            logger.warning(f"[DASHBOARD] Failed to render map for {guid}: {err}")

        return None, None

    async def on_select(self, interaction: discord.Interaction):
        val = interaction.data["values"][0]
        if val == "summary":
            await interaction.response.edit_message(embed=self.build_summary_embed(), embeds=[], view=self)
        else:
            # Switch to card mode at the first event of that day
            day_events = self.grouped[val]
            first_event = sorted(day_events, key=lambda x: x["timestamp"], reverse=True)[0]
            self.index = self.events.index(first_event)
            self.mode = "card"
            self._update_items()

            # Check for map
            img_url, file = await self._render_map_if_needed(first_event)
            embed = self.build_card_embed()
            if img_url:
                embed.set_image(url=img_url)
                embed.set_footer(text=f"Local DAT Render | {embed.footer.text}")

            await interaction.response.edit_message(embed=embed, view=self, attachments=[file] if file else [])

    @discord.ui.button(label="⏮️ First", style=discord.ButtonStyle.secondary)
    async def first_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = 0
        self._update_items()
        e = self.events[self.index]
        img_url, file = await self._render_map_if_needed(e)
        embed = self.build_card_embed()
        if img_url:
            embed.set_image(url=img_url)
            embed.set_footer(text=f"Local DAT Render | {embed.footer.text}")
        await interaction.response.edit_message(embed=embed, view=self, attachments=[file] if file else [])

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = max(0, self.index - 1)
        self._update_items()
        e = self.events[self.index]
        img_url, file = await self._render_map_if_needed(e)
        embed = self.build_card_embed()
        if img_url:
            embed.set_image(url=img_url)
            embed.set_footer(text=f"Local DAT Render | {embed.footer.text}")
        await interaction.response.edit_message(embed=embed, view=self, attachments=[file] if file else [])

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = min(len(self.events) - 1, self.index + 1)
        self._update_items()
        e = self.events[self.index]
        img_url, file = await self._render_map_if_needed(e)
        embed = self.build_card_embed()
        if img_url:
            embed.set_image(url=img_url)
            embed.set_footer(text=f"Local DAT Render | {embed.footer.text}")
        await interaction.response.edit_message(embed=embed, view=self, attachments=[file] if file else [])

    @discord.ui.button(label="Last ⏭️", style=discord.ButtonStyle.secondary)
    async def last_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = len(self.events) - 1
        self._update_items()
        e = self.events[self.index]
        img_url, file = await self._render_map_if_needed(e)
        embed = self.build_card_embed()
        if img_url:
            embed.set_image(url=img_url)
            embed.set_footer(text=f"Local DAT Render | {embed.footer.text}")
        await interaction.response.edit_message(embed=embed, view=self, attachments=[file] if file else [])

    @discord.ui.button(label="📋 Summary", style=discord.ButtonStyle.primary)
    async def summary_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.mode = "summary"
        self._update_items()
        await interaction.response.edit_message(embed=self.build_summary_embed(), view=self)

    @discord.ui.button(label="📸 Photos", style=discord.ButtonStyle.success)
    async def photos_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        e = self.events[self.index]

        # Need location and coords to search DAT
        location = e.get("location")
        coords = e.get("coords")
        event_id = e.get("event_id")
        if not location or not coords:
            await interaction.response.send_message("No location data available for this event.", ephemeral=True)
            return

        await interaction.response.defer()
        from utils.events_db import fetch_dat_photos, get_cached_dat_photos

        # Check cache first (instant load)
        photos = []
        if event_id:
            photos = get_cached_dat_photos(event_id)

        # If not cached, fetch from DAT
        if not photos:
            magnitude = e.get("magnitude", "")
            photo_urls = await fetch_dat_photos(
                location=location,
                magnitude=magnitude,
                coords=coords,
            )
            if photo_urls:
                # Convert URLs to file paths if possible, otherwise use URLs
                photos = photo_urls
        else:
            logger.info(f"[WARNINGS] Using {len(photos)} cached photo(s) for {event_id}")

        if not photos:
            await interaction.followup.send("No damage photos found in the DAT for this event.", ephemeral=True)
            return

        photo_view = TornadoPhotoView(photos, self, location)
        await interaction.edit_original_response(embeds=photo_view.build_embeds(), view=photo_view)

    def _get_ef_emoji(self, mag: str) -> str:
        mag = (mag or "").upper()
        if "EF5" in mag:
            return "🟣"
        if "EF4" in mag:
            return "🔴"
        if "EF3" in mag:
            return "🟠"
        if "EF2" in mag:
            return "🟡"
        if "EF1" in mag:
            return "🟢"
        if "EF0" in mag:
            return "🔵"
        return "⚪"

    def build_summary_embed(self) -> discord.Embed:
        # Calculate Grand Totals
        totals = {"EF5": 0, "EF4": 0, "EF3": 0, "EF2": 0, "EF1": 0, "EF0": 0, "EFU": 0}
        for e in self.events:
            mag = (e.get("magnitude") or "").upper()
            matched = False
            for scale in ["EF5", "EF4", "EF3", "EF2", "EF1", "EF0"]:
                if scale in mag:
                    totals[scale] += 1
                    matched = True
                    break
            if not matched:
                totals["EFU"] += 1

        total_count = len(self.events)

        # Build total line: 🟣0 🔴0 🟠2 🟡5 🟢14 🔵21 ⚪6
        t_parts = []
        if totals["EF5"]:
            t_parts.append(f"🟣{totals['EF5']}")
        if totals["EF4"]:
            t_parts.append(f"🔴{totals['EF4']}")
        if totals["EF3"]:
            t_parts.append(f"🟠{totals['EF3']}")
        if totals["EF2"]:
            t_parts.append(f"🟡{totals['EF2']}")
        if totals["EF1"]:
            t_parts.append(f"🟢{totals['EF1']}")
        if totals["EF0"]:
            t_parts.append(f"🔵{totals['EF0']}")
        if totals["EFU"]:
            t_parts.append(f"⚪{totals['EFU']}")

        total_line = " ".join(t_parts) if t_parts else "None"

        lines = [
            f"**Total: {total_line} ({total_count} tornadoes)**",
            "───────────────────────────"
        ]

        # Add daily breakdown
        for date_str in self.dates[:20]:  # Limit to 20 days for description length
            day_events = self.grouped[date_str]
            d_counts = {"EF5": 0, "EF4": 0, "EF3": 0, "EF2": 0, "EF1": 0, "EF0": 0, "EFU": 0}
            for e in day_events:
                mag = (e.get("magnitude") or "").upper()
                matched = False
                for scale in ["EF5", "EF4", "EF3", "EF2", "EF1", "EF0"]:
                    if scale in mag:
                        d_counts[scale] += 1
                        matched = True
                        break
                if not matched:
                    d_counts["EFU"] += 1

            # Shorten date: 2026-05-01 -> May 01
            try:
                dt_obj = datetime.strptime(date_str, "%Y-%m-%d")
                short_date = dt_obj.strftime("%b %d")
            except ValueError:
                short_date = date_str

            # Build daily parts
            d_parts = []
            if d_counts["EF5"]:
                d_parts.append(f"🟣{d_counts['EF5']}")
            if d_counts["EF4"]:
                d_parts.append(f"🔴{d_counts['EF4']}")
            if d_counts["EF3"]:
                d_parts.append(f"🟠{d_counts['EF3']}")
            if d_counts["EF2"]:
                d_parts.append(f"🟡{d_counts['EF2']}")
            if d_counts["EF1"]:
                d_parts.append(f"🟢{d_counts['EF1']}")
            if d_counts["EF0"]:
                d_parts.append(f"🔵{d_counts['EF0']}")
            if d_counts["EFU"]:
                d_parts.append(f"⚪{d_counts['EFU']}")

            day_icons = " ".join(d_parts) if d_parts else "Confirmed"
            # Use fixed-width date for alignment in monospace blocks
            lines.append(f"`{short_date} ({len(day_events):>2})` .... {day_icons}")

        embed = discord.Embed(
            title=f"{self.title}",
            description="\n".join(lines),
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc)
        )

        embed.set_footer(
            text=f"Showing last {min(20, len(self.dates))} active days. Use dropdown to pick a day."
        )
        return embed

    def build_card_embed(self) -> discord.Embed:
        e = self.events[self.index]
        dt = datetime.fromtimestamp(e['timestamp'], timezone.utc)
        date_str = dt.strftime("%Y-%m-%d %H:%MZ")
        rel_time = f"<t:{int(e['timestamp'])}:R>"

        mag = e.get("magnitude", "Confirmed")
        emoji = self._get_ef_emoji(mag)

        embed = discord.Embed(
            title=f"{emoji} Tornado: {e['location']}",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc)
        )

        embed.add_field(name="Rating", value=mag, inline=True)
        embed.add_field(name="Time", value=f"{date_str}\n({rel_time})", inline=True)
        embed.add_field(name="Office", value=e['source'], inline=True)

        if e.get("lead_time") is not None:
            if e["lead_time"] == -1:
                # Sentinel value: explicitly marked as unwarned
                embed.add_field(name="⚠️ Warning Status", value="UNWARNED", inline=True)
            else:
                embed.add_field(name="Lead Time", value=f"{e['lead_time']:.1f} min", inline=True)

        if e.get("vtec_id"):
            parts = e["vtec_id"].split(".")
            if len(parts) == 4:
                office, phenom, sig, etn = parts
                url = _vtec_url({
                    "action": "NEW",
                    "office": office,
                    "phenom": phenom,
                    "sig": sig,
                    "etn": etn,
                    "start": dt.strftime("%y%m%dT%H%MZ"),
                })
                embed.add_field(name="VTEC ID", value=f"[{e['vtec_id']}]({url})", inline=True)

        if e.get("dat_guid"):
            dat_url = f"https://apps.dat.noaa.gov/stormdamage/damageviewer/?datglobalid={e['dat_guid']}"
            embed.add_field(name="NWS DAT", value=f"[View Track]({dat_url})", inline=True)

            # Show track map in the card
            # We use IEM Autoplot for the dashboard view as it handles historical caching better
            event_date = dt.strftime("%Y-%m-%d")
            img_url = (
                f"https://mesonet.agron.iastate.edu/plotting/auto/plot/253/"
                f"datglobalid:{e['dat_guid']}::dat:{event_date}::cmap:gist_rainbow::"
                f"_r:t::dpi:100.png"
            )
            embed.set_image(url=img_url)

        if e.get("raw_text"):
            text = e["raw_text"]
            if len(text) > 500:
                text = text[:497] + "..."
            embed.add_field(name="Remarks", value=f"```\n{text}\n```", inline=False)

        embed.set_footer(text=f"Event {self.index + 1} of {len(self.events)} | {e['coords']}")
        return embed
