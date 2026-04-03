# cogs/radar.py
import os
import asyncio
import zipfile
import time
import logging
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import botocore
import discord
from discord import ButtonStyle, SelectOption
from discord.ui import Button, View, Modal, TextInput, Select
from discord.ext import commands, tasks

logger = logging.getLogger("scp_bot")

# ── S3 setup ─────────────────────────────────────────────────────────────────
_thread_local = threading.local()

def get_s3_client():
    if not hasattr(_thread_local, 's3_client'):
        _thread_local.s3_client = boto3.client(
            's3',
            config=botocore.config.Config(signature_version=botocore.UNSIGNED)
        )
    return _thread_local.s3_client

# ── Constants ─────────────────────────────────────────────────────────────────
MAX_FILE_SIZE = 100 * 1024 * 1024
MIN_FILE_SIZE = 8 * 1024 * 1024
OUTPUT_DIR = "radar_data"
CLEANUP_AGE_THRESHOLD = 24 * 60 * 60
MAX_CONCURRENT_DOWNLOADS = 50
BATCH_SIZE_THRESHOLD = 5

progress_data = {}
progress_lock = threading.Lock()


# ── Utility functions ─────────────────────────────────────────────────────────
def format_file_size(size_in_bytes):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_in_bytes < 1024.0:
            return f"{size_in_bytes:.2f} {unit}"
        size_in_bytes /= 1024.0
    return f"{size_in_bytes:.2f} TB"

def get_progress_bar(progress_percentage, length=30):
    filled = min(int(progress_percentage / (100 / length)), length)
    bar = "█" * filled + "░" * (length - filled)
    return f"{bar} {min(progress_percentage, 100):.1f}%"

def get_radar_sites(date):
    bucket = 'unidata-nexrad-level2'
    prefix = f"{date.year}/{date.month:02d}/{date.day:02d}/"
    try:
        response = get_s3_client().list_objects_v2(Bucket=bucket, Prefix=prefix, Delimiter='/')
        if 'CommonPrefixes' not in response:
            logger.warning(f"[RADAR] No radar sites found for {date.strftime('%Y-%m-%d')}")
            return []
        sites = sorted([p['Prefix'].split('/')[-2] for p in response['CommonPrefixes']])
        logger.info(f"[RADAR] Found {len(sites)} radar sites for {date.strftime('%Y-%m-%d')}")
        return sites
    except Exception as e:
        logger.error(f"[RADAR] Error listing radar sites: {e}")
        return []

def list_files(radar_site, dates):
    bucket = 'unidata-nexrad-level2'
    all_files = []
    for date in dates:
        prefix = f"{date.year}/{date.month:02d}/{date.day:02d}/{radar_site}/"
        try:
            response = get_s3_client().list_objects_v2(Bucket=bucket, Prefix=prefix)
            if 'Contents' not in response:
                continue
            files = [
                {
                    'Key': obj['Key'],
                    'LastModified': obj['LastModified'].replace(tzinfo=timezone.utc),
                    'Size': obj['Size'],
                    'RadarSite': radar_site
                }
                for obj in response['Contents']
                if not (obj['Key'].lower().endswith('.tar') or '_mdm' in obj['Key'].lower())
            ]
            for f in files:
                filename = os.path.basename(f['Key'])
                try:
                    if len(filename) >= 18 and filename[4:12].isdigit() and filename[12:18].isdigit():
                        f['FileTimestamp'] = datetime.strptime(
                            filename[4:12] + filename[12:18], '%Y%m%d%H%M%S'
                        ).replace(tzinfo=timezone.utc)
                    else:
                        f['FileTimestamp'] = f['LastModified']
                except ValueError:
                    f['FileTimestamp'] = f['LastModified']
            all_files.extend(files)
        except Exception as e:
            logger.error(f"[RADAR] Error listing files for {radar_site}: {e}")
    sorted_files = sorted(all_files, key=lambda x: x['FileTimestamp'], reverse=True)
    logger.debug(f"[RADAR] Listed {len(sorted_files)} files for {radar_site}")
    return sorted_files


async def download_file(file_key, output_dir, start_time, file_size, filename):
    bucket = 'unidata-nexrad-level2'
    output_path = Path(output_dir) / filename
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    def progress_callback(bytes_amount):
        with progress_lock:
            if filename not in progress_data:
                progress_data[filename] = {'bytes_transferred': 0, 'completed': False}
            progress_data[filename]['bytes_transferred'] += bytes_amount
            if progress_data[filename]['bytes_transferred'] >= file_size:
                progress_data[filename]['completed'] = True

    last_err = None
    for attempt in range(3):
        if attempt > 0:
            logger.warning(f"[RADAR] Retry {attempt}/2 for {filename}")
            await asyncio.sleep(2 ** attempt)
            with progress_lock:
                progress_data[filename] = {'bytes_transferred': 0, 'completed': False}
            if hasattr(_thread_local, 's3_client'):
                del _thread_local.s3_client
        try:
            loop = asyncio.get_event_loop()
            fut = loop.run_in_executor(
                None,
                lambda: get_s3_client().download_file(
                    bucket, file_key, str(output_path), Callback=progress_callback
                )
            )
            await asyncio.wait_for(fut, timeout=30)
            download_time = time.time() - start_time
            speed = (file_size / download_time / 1024 / 1024) * 8 if download_time > 0 else 0
            logger.debug(f"[RADAR] Downloaded {filename} in {download_time:.1f}s at {speed:.1f} Mbps")
            return output_path, download_time, speed
        except asyncio.TimeoutError:
            logger.warning(f"[RADAR] Timeout on attempt {attempt+1}/3 for {filename} (30s)")
            if output_path.exists():
                output_path.unlink()
            last_err = RuntimeError(f"Download timed out: {filename}")
        except Exception as e:
            logger.warning(f"[RADAR] Error on attempt {attempt+1}/3 for {filename}: {e}")
            if output_path.exists():
                output_path.unlink()
            last_err = e

    logger.error(f"[RADAR] All 3 attempts failed for {filename}")
    raise last_err


async def cleanup_old_files(directory, age_threshold):
    now = time.time()
    path = Path(directory)
    if not path.exists():
        return
    for item in path.iterdir():
        if item.is_file():
            age = now - item.stat().st_mtime
            if age > age_threshold:
                try:
                    item.unlink()
                    logger.debug(f"[RADAR] Deleted old file: {item}")
                except Exception as e:
                    logger.error(f"[RADAR] Failed to delete old file {item}: {e}")


async def split_and_zip_files(file_paths, radar_sites, split_size, output_dir):
    def _zip():
        chunk_size = 0
        current_zip_files = []
        zip_counter = 1
        zip_paths = []
        for file_path, file_info in file_paths:
            file_size = os.path.getsize(file_path)
            if chunk_size + file_size > split_size and current_zip_files:
                zip_path = Path(output_dir) / f"{radar_sites[0]}_part{zip_counter}.zip"
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for fp, fi in current_zip_files:
                        zipf.write(fp, os.path.join(radar_sites[0], os.path.basename(fp)))
                zip_paths.append(zip_path)
                current_zip_files = []
                chunk_size = 0
                zip_counter += 1
            current_zip_files.append((file_path, file_info))
            chunk_size += file_size
        if current_zip_files:
            zip_path = Path(output_dir) / f"{radar_sites[0]}_part{zip_counter}.zip"
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for fp, fi in current_zip_files:
                    zipf.write(fp, os.path.join(radar_sites[0], os.path.basename(fp)))
            zip_paths.append(zip_path)
        if len(zip_paths) == 1:
            clean_path = Path(output_dir) / f"{radar_sites[0]}.zip"
            zip_paths[0].rename(clean_path)
            zip_paths[0] = clean_path
        return zip_paths
    return await asyncio.get_event_loop().run_in_executor(None, _zip)


async def download_and_zip(interaction, filtered_files, radar_sites, messages_to_delete):
    output_dir = OUTPUT_DIR
    channel = interaction.channel
    total_files = len(filtered_files)
    file_paths = []
    files_remaining = list(filtered_files)
    total_downloaded_size = 0
    total_download_time = 0
    start_time = time.time()
    files_completed = 0

    with progress_lock:
        for f in filtered_files:
            filename = os.path.basename(f['Key'])
            progress_data[filename] = {'bytes_transferred': 0, 'completed': False}

    stats_by_site = {radar_site: {'files': []} for radar_site in radar_sites}
    for f in filtered_files:
        stats_by_site[f['RadarSite']]['files'].append(f)

    logger.info(f"[RADAR] Starting download of {total_files} files for {radar_sites}")

    embed = discord.Embed(
        title="Download Progress",
        description=f"Starting download of {total_files} files...\n",
        color=discord.Color.blue()
    )
    message = await interaction.followup.send(embed=embed)
    messages_to_delete.append(message)

    try:
        current_batch_size = min(MAX_CONCURRENT_DOWNLOADS, total_files)
        avg_speed_history = []
        last_update_time = time.time()

        async def update_progress():
            nonlocal last_update_time
            current_time = time.time()
            if current_time - last_update_time < 1:
                return
            elapsed_time = current_time - start_time
            instantaneous_speed = (total_downloaded_size / elapsed_time / 1024 / 1024) * 8 if elapsed_time > 0 else 0
            progress = (files_completed / total_files) * 100
            progress_bar = get_progress_bar(progress)
            description = f"Downloading {total_files} files...\n{progress_bar} (Speed: {instantaneous_speed:.2f} Mbps)\n\n"
            with progress_lock:
                for fname in progress_data:
                    matched_file = None
                    for site in radar_sites:
                        matched_file = next(
                            (f for f in stats_by_site[site]['files'] if os.path.basename(f['Key']) == fname),
                            None
                        )
                        if matched_file:
                            break
                    if matched_file:
                        percentage = (progress_data[fname]['bytes_transferred'] / matched_file['Size']) * 100
                        file_bar = get_progress_bar(percentage)
                        description += f"**{fname}**: {file_bar}\n"
                    else:
                        description += f"**{fname}**: [Size Unknown]\n"
            embed.description = description
            try:
                await message.edit(embed=embed)
            except (discord.errors.NotFound, discord.errors.HTTPException) as e:
                logger.error(f"[RADAR] Failed to edit progress message: {e}")
            last_update_time = current_time

        async def download_with_progress(file_info, idx):
            nonlocal total_downloaded_size, total_download_time, files_completed
            file_start_time = time.time()
            filename = os.path.basename(file_info['Key'])
            file_path, download_time, speed = await download_file(
                file_info['Key'], output_dir, file_start_time, file_info['Size'], filename
            )
            total_downloaded_size += file_info['Size']
            total_download_time += download_time
            files_completed += 1
            avg_speed_history.append(speed)
            await update_progress()
            return file_path, file_info

        while files_remaining:
            batch_size = min(current_batch_size, len(files_remaining))
            if len(avg_speed_history) >= 5:
                baseline_speed = sum(avg_speed_history[:3]) / 3
                recent_speed = sum(avg_speed_history[-3:]) / 3
                if recent_speed < baseline_speed * 0.7:
                    current_batch_size = max(BATCH_SIZE_THRESHOLD, current_batch_size // 2)
                    logger.info(f"[RADAR] Speed drop detected, reducing batch size to {current_batch_size}")
                if len(avg_speed_history) > 10:
                    avg_speed_history.pop(0)
            batch = files_remaining[:batch_size]
            files_remaining = files_remaining[batch_size:]
            tasks_list = [download_with_progress(fi, i + 1) for i, fi in enumerate(batch)]
            results = await asyncio.gather(*tasks_list, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    embed.title = "Download Error"
                    embed.description = f"Failed to download a file: {result}\n"
                    await message.edit(embed=embed)
                    return
                file_paths.append(result)

        await update_progress()

        elapsed_time = time.time() - start_time
        instantaneous_speed = (total_downloaded_size / elapsed_time / 1024 / 1024) * 8 if elapsed_time > 0 else 0
        progress_bar = get_progress_bar(100)
        embed.title = "Download Complete"
        embed.description = (
            f"Downloaded {len(file_paths)} files totaling {format_file_size(total_downloaded_size)}.\n"
            f"{progress_bar} (Speed: {instantaneous_speed:.2f} Mbps)"
        )
        embed.color = discord.Color.green()
        await message.edit(embed=embed)
        logger.info(f"[RADAR] Download complete: {len(file_paths)} files, {format_file_size(total_downloaded_size)}, {instantaneous_speed:.1f} Mbps avg")

        size_ladder = [MAX_FILE_SIZE, 50 * 1024 * 1024, 25 * 1024 * 1024, MIN_FILE_SIZE]
        all_uploaded = False

        for attempt_size in size_ladder:
            for old_zip in Path(output_dir).glob("*.zip"):
                try:
                    old_zip.unlink()
                except Exception:
                    pass
            zip_paths = await split_and_zip_files(file_paths, radar_sites, attempt_size, output_dir)
            upload_failed = False
            for i, zip_path in enumerate(zip_paths, 1):
                zip_size = os.path.getsize(zip_path)
                part_label = f" - Part {i} of {len(zip_paths)}" if len(zip_paths) > 1 else ""
                embed.title = "Uploading"
                embed.description = (
                    f"Uploading {zip_path.name} ({format_file_size(zip_size)}){part_label}...\n"
                    f"{progress_bar} (Speed: {instantaneous_speed:.2f} Mbps)"
                )
                embed.color = discord.Color.purple()
                await message.edit(embed=embed)
                try:
                    await channel.send(file=discord.File(zip_path))
                    embed.title = "Upload Complete"
                    embed.description = (
                        f"Successfully uploaded {zip_path.name} ({format_file_size(zip_size)}){part_label}.\n"
                        f"{progress_bar} (Speed: {instantaneous_speed:.2f} Mbps)"
                    )
                    embed.color = discord.Color.green()
                    await message.edit(embed=embed)
                    logger.info(f"[RADAR] Uploaded {zip_path.name} ({format_file_size(zip_size)}){part_label}")
                except discord.errors.HTTPException as e:
                    if e.status == 413 and attempt_size > MIN_FILE_SIZE:
                        next_size = size_ladder[size_ladder.index(attempt_size) + 1]
                        embed.title = "Upload Failed — Retrying"
                        embed.description = f"File too large at {format_file_size(attempt_size)}. Retrying with {format_file_size(next_size)} parts..."
                        embed.color = discord.Color.orange()
                        await message.edit(embed=embed)
                        upload_failed = True
                        break
                    else:
                        embed.title = "Upload Failed"
                        embed.description = f"Failed to upload {zip_path.name}: {e}\n{progress_bar}"
                        embed.color = discord.Color.red()
                        await message.edit(embed=embed)
                        logger.error(f"[RADAR] Upload failed for {zip_path.name}: {e}")
                        return
            if not upload_failed:
                all_uploaded = True
                break

        if not all_uploaded:
            embed.title = "Upload Failed"
            embed.description = f"Could not upload files even at minimum size ({format_file_size(MIN_FILE_SIZE)})."
            embed.color = discord.Color.red()
            await message.edit(embed=embed)
            logger.error("[RADAR] Upload failed at all size levels")

    except Exception as e:
        logger.error(f"[RADAR] Unexpected error in download_and_zip: {e}", exc_info=True)
        embed.title = "Unexpected Error"
        embed.description = f"An unexpected error occurred: {e}"
        embed.color = discord.Color.red()
        try:
            await message.edit(embed=embed)
        except Exception:
            pass

    finally:
        for file_path, _ in file_paths:
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception as e:
                    logger.error(f"[RADAR] Failed to delete temp file {file_path}: {e}")
        for zip_path in Path(output_dir).glob("*.zip"):
            try:
                zip_path.unlink()
            except Exception as e:
                logger.error(f"[RADAR] Failed to delete zip {zip_path}: {e}")
        if os.path.exists(output_dir):
            try:
                shutil.rmtree(output_dir)
            except Exception as e:
                logger.error(f"[RADAR] Failed to delete output dir: {e}")
        with progress_lock:
            progress_data.clear()
        for msg in messages_to_delete:
            if hasattr(msg, 'delete'):
                try:
                    await msg.delete()
                except (discord.errors.NotFound, discord.errors.HTTPException):
                    pass


# ── UI Components ─────────────────────────────────────────────────────────────
class RadarSiteSelect(Select):
    def __init__(self, placeholder, options, messages_to_delete, original_user=None):
        super().__init__(placeholder=placeholder, options=options)
        self.messages_to_delete = messages_to_delete
        self.original_user = original_user

    async def callback(self, interaction: discord.Interaction):
        radar_site = self.values[0]
        view = DateSelectionView([radar_site], self.messages_to_delete, original_user=self.original_user or interaction.user)
        embed = discord.Embed(
            title=f"Selected Radar Site: {radar_site}",
            description="Select a date for the data:",
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()
        self.messages_to_delete.append(msg)


class RadarSiteView(View):
    def __init__(self, radar_sites, messages_to_delete, original_user):
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
        end_idx = min(start_idx + self.page_size, len(self.radar_sites))
        options = [SelectOption(label=site, value=site) for site in self.radar_sites[start_idx:end_idx]]
        self.add_item(RadarSiteSelect(
            placeholder="Choose a radar site...",
            options=options,
            messages_to_delete=self.messages_to_delete,
            original_user=self.original_user
        ))
        search_btn = Button(label="Search Radar Sites", style=ButtonStyle.grey)
        search_btn.callback = self._search_callback
        self.add_item(search_btn)
        multi_btn = Button(label="Select Multiple Sites", style=ButtonStyle.grey)
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

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.original_user:
            await interaction.response.send_message("This interaction is not yours.", ephemeral=True)
            return False
        return True

    async def _search_callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SearchModal(self.radar_sites, self.messages_to_delete, original_user=self.original_user))

    async def _multi_callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(MultiRadarModal(self.radar_sites, self.messages_to_delete, original_user=self.original_user))

    async def _prev_callback(self, interaction: discord.Interaction):
        self.current_page -= 1
        self._rebuild_items()
        embed = discord.Embed(title="AWS NEXRAD Data Downloader", description=f"Select a radar site ({len(self.radar_sites)} available):", color=discord.Color.blue())
        await interaction.response.edit_message(embed=embed, view=self)

    async def _next_callback(self, interaction: discord.Interaction):
        self.current_page += 1
        self._rebuild_items()
        embed = discord.Embed(title="AWS NEXRAD Data Downloader", description=f"Select a radar site ({len(self.radar_sites)} available):", color=discord.Color.blue())
        await interaction.response.edit_message(embed=embed, view=self)


class StartView(View):
    def __init__(self, original_user):
        super().__init__(timeout=300)
        self.original_user = original_user
        self.messages_to_delete = []

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.original_user:
            await interaction.response.send_message("This interaction is not yours.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Start Download", style=ButtonStyle.green)
    async def start_download(self, interaction: discord.Interaction, button: Button):
        today = (datetime.now(timezone.utc) - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        radar_sites = await asyncio.get_event_loop().run_in_executor(None, get_radar_sites, today)
        if not radar_sites:
            await interaction.response.send_message(
                embed=discord.Embed(title="No Radar Sites Found", description="No radar sites have data for yesterday.", color=discord.Color.red()),
                delete_after=10
            )
            return
        view = RadarSiteView(radar_sites, self.messages_to_delete, original_user=self.original_user)
        embed = discord.Embed(title="AWS NEXRAD Data Downloader", description=f"Select a radar site ({len(radar_sites)} available):", color=discord.Color.blue())
        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()
        self.messages_to_delete.append(msg)


class DateSelectionView(View):
    def __init__(self, radar_sites, messages_to_delete, original_user=None):
        super().__init__(timeout=300)
        self.original_user = original_user
        self.radar_sites = radar_sites
        self.messages_to_delete = messages_to_delete

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.original_user and interaction.user != self.original_user:
            await interaction.response.send_message("This interaction is not yours.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Today", style=ButtonStyle.green)
    async def today(self, interaction: discord.Interaction, button: Button):
        date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        view = TimeRangeView(self.radar_sites, date, self.messages_to_delete, original_user=self.original_user)
        embed = discord.Embed(title=f"Selected: {', '.join(self.radar_sites)}", description=f"Date: {date.strftime('%Y-%m-%d')}\nChoose a download option:", color=discord.Color.blue())
        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()
        self.messages_to_delete.append(msg)

    @discord.ui.button(label="Yesterday", style=ButtonStyle.green)
    async def yesterday(self, interaction: discord.Interaction, button: Button):
        date = (datetime.now(timezone.utc) - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        view = TimeRangeView(self.radar_sites, date, self.messages_to_delete, original_user=self.original_user)
        embed = discord.Embed(title=f"Selected: {', '.join(self.radar_sites)}", description=f"Date: {date.strftime('%Y-%m-%d')}\nChoose a download option:", color=discord.Color.blue())
        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()
        self.messages_to_delete.append(msg)

    @discord.ui.button(label="Custom Date", style=ButtonStyle.grey)
    async def custom_date(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(DateModal(self.radar_sites, self.messages_to_delete, original_user=self.original_user))


class TimeRangeView(View):
    def __init__(self, radar_sites, date, messages_to_delete, original_user=None):
        super().__init__(timeout=300)
        self.original_user = original_user
        self.radar_sites = radar_sites
        self.date = date
        self.messages_to_delete = messages_to_delete

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.original_user and interaction.user != self.original_user:
            await interaction.response.send_message("This interaction is not yours.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Last 1 Hour", style=ButtonStyle.green)
    async def last_1_hour(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()
        await self.download(interaction, hours_back=1)

    @discord.ui.button(label="Last 2 Hours", style=ButtonStyle.green)
    async def last_2_hours(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()
        await self.download(interaction, hours_back=2)

    @discord.ui.button(label="Last 3 Hours", style=ButtonStyle.green)
    async def last_3_hours(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()
        await self.download(interaction, hours_back=3)

    @discord.ui.button(label="Last 4 Hours", style=ButtonStyle.green)
    async def last_4_hours(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()
        await self.download(interaction, hours_back=4)

    @discord.ui.button(label="Enter Range in Z", style=ButtonStyle.green)
    async def utc_range(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(UTCTimeRangeModal(self.radar_sites, self.date, self.messages_to_delete, original_user=self.original_user))

    @discord.ui.button(label="10 Most Recent", style=ButtonStyle.green)
    async def ten_most_recent(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()
        await self.download(interaction, max_files=10)

    @discord.ui.button(label="Custom", style=ButtonStyle.grey)
    async def custom(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(CustomTimeModal(self.radar_sites, self.date, self.messages_to_delete, original_user=self.original_user))

    async def download(self, interaction, max_files=None, hours_back=None):
        now = datetime.now(timezone.utc)
        all_files = []
        dates_to_query = [self.date]
        if hours_back:
            start_dt = now - timedelta(hours=hours_back)
            if start_dt.date() < self.date.date():
                dates_to_query.append(self.date - timedelta(days=1))
        for radar_site in self.radar_sites:
            files = await asyncio.get_event_loop().run_in_executor(None, list_files, radar_site, dates_to_query)
            all_files.extend(files)
        if not all_files:
            msg = await interaction.followup.send(
                embed=discord.Embed(title="No Files Found", description=f"No radar files found for {', '.join(self.radar_sites)} on {self.date.strftime('%Y-%m-%d')}.", color=discord.Color.red()),
                delete_after=10
            )
            self.messages_to_delete.append(msg)
            return
        if max_files:
            filtered_files = sorted(all_files, key=lambda x: x['FileTimestamp'], reverse=True)[:max_files]
        elif hours_back:
            filtered_files = [f for f in all_files if (now - timedelta(hours=hours_back)) <= f['FileTimestamp'] <= now]
        else:
            filtered_files = all_files
        if not filtered_files:
            msg = await interaction.followup.send(
                embed=discord.Embed(title="No Files Matched", description="No files matched your criteria.", color=discord.Color.red()),
                delete_after=10
            )
            self.messages_to_delete.append(msg)
            return
        await download_and_zip(interaction, filtered_files, self.radar_sites, self.messages_to_delete)


class DateModal(Modal, title="Enter Custom Date"):
    date_input = TextInput(label="Date (YYYY-MM-DD)", placeholder="e.g., 2025-05-13", required=True)

    def __init__(self, radar_sites, messages_to_delete, original_user=None):
        super().__init__()
        self.radar_sites = radar_sites
        self.messages_to_delete = messages_to_delete
        self.original_user = original_user

    async def on_submit(self, interaction: discord.Interaction):
        try:
            date = datetime.strptime(self.date_input.value, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            view = TimeRangeView(self.radar_sites, date, self.messages_to_delete, original_user=self.original_user)
            embed = discord.Embed(title=f"Selected: {', '.join(self.radar_sites)}", description=f"Date: {date.strftime('%Y-%m-%d')}\nChoose a download option:", color=discord.Color.blue())
            await interaction.response.send_message(embed=embed, view=view)
            msg = await interaction.original_response()
            self.messages_to_delete.append(msg)
        except ValueError:
            await interaction.response.send_message(
                embed=discord.Embed(title="Invalid Date", description="Please use format: YYYY-MM-DD", color=discord.Color.red()),
                ephemeral=True
            )


class MultiRadarModal(Modal, title="Select Multiple Sites"):
    radar_input = TextInput(label="Radar Sites (e.g., TOKC TJUA)", placeholder="e.g., TOKC TJUA KTLX", required=True)

    def __init__(self, available_sites, messages_to_delete, original_user=None):
        super().__init__()
        self.available_sites = available_sites
        self.messages_to_delete = messages_to_delete
        self.original_user = original_user

    async def on_submit(self, interaction: discord.Interaction):
        entered_sites = self.radar_input.value.upper().split()
        valid_sites = [site for site in entered_sites if site in self.available_sites]
        if not valid_sites:
            await interaction.response.send_message(
                embed=discord.Embed(title="No Valid Radar Sites", description="None of the entered radar sites were found.", color=discord.Color.red()),
                ephemeral=True
            )
            return
        view = DateSelectionView(valid_sites, self.messages_to_delete, original_user=self.original_user)
        embed = discord.Embed(title=f"Selected: {', '.join(valid_sites)}", description="Select a date for the data:", color=discord.Color.blue())
        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()
        self.messages_to_delete.append(msg)


class CustomTimeModal(Modal, title="Custom Download Options"):
    time_range = TextInput(label="Time Range (YYYY-MM-DD HH:MM to HH:MM)", placeholder="e.g., 2025-05-13 14:00 to 16:00", required=False)
    hours_back = TextInput(label="Hours Back (e.g., 6)", placeholder="e.g., 6", required=False)
    num_files = TextInput(label="Number of Recent Files (e.g., 5)", placeholder="e.g., 5", required=False)

    def __init__(self, radar_sites, date, messages_to_delete, original_user=None):
        super().__init__()
        self.radar_sites = radar_sites
        self.date = date
        self.messages_to_delete = messages_to_delete
        self.original_user = original_user

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        dates_to_query = [self.date]
        all_files = []
        if self.time_range.value:
            try:
                start_str, end_str = self.time_range.value.split(" to ")
                start_dt = datetime.strptime(start_str.strip(), '%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc)
                end_dt = datetime.strptime(end_str.strip(), '%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc)
                if end_dt < start_dt:
                    end_dt += timedelta(days=1)
                if start_dt.date() < self.date.date():
                    dates_to_query.append(self.date - timedelta(days=1))
                for radar_site in self.radar_sites:
                    files = await asyncio.get_event_loop().run_in_executor(None, list_files, radar_site, dates_to_query)
                    all_files.extend(files)
                filtered_files = [f for f in all_files if start_dt <= f['FileTimestamp'] <= end_dt]
            except ValueError:
                await interaction.followup.send(embed=discord.Embed(title="Invalid Time Range", description="Use format: YYYY-MM-DD HH:MM to HH:MM", color=discord.Color.red()), ephemeral=True)
                return
        elif self.hours_back.value:
            try:
                hours = float(self.hours_back.value)
                now = datetime.now(timezone.utc)
                start_dt = now - timedelta(hours=hours)
                if start_dt.date() < self.date.date():
                    dates_to_query.append(self.date - timedelta(days=1))
                for radar_site in self.radar_sites:
                    files = await asyncio.get_event_loop().run_in_executor(None, list_files, radar_site, dates_to_query)
                    all_files.extend(files)
                filtered_files = [f for f in all_files if start_dt <= f['FileTimestamp'] <= now]
            except ValueError:
                await interaction.followup.send(embed=discord.Embed(title="Invalid Hours Back", description="Enter a valid number of hours.", color=discord.Color.red()), ephemeral=True)
                return
        elif self.num_files.value:
            try:
                num = int(self.num_files.value)
                for radar_site in self.radar_sites:
                    files = await asyncio.get_event_loop().run_in_executor(None, list_files, radar_site, dates_to_query)
                    all_files.extend(files)
                filtered_files = sorted(all_files, key=lambda x: x['FileTimestamp'], reverse=True)[:num]
            except ValueError:
                await interaction.followup.send(embed=discord.Embed(title="Invalid Number of Files", description="Enter a valid number.", color=discord.Color.red()), ephemeral=True)
                return
        else:
            await interaction.followup.send(embed=discord.Embed(title="No Criteria Provided", description="Provide a time range, hours back, or number of files.", color=discord.Color.red()), ephemeral=True)
            return
        if not filtered_files:
            await interaction.followup.send(embed=discord.Embed(title="No Files Matched", description="No files matched your criteria.", color=discord.Color.red()), delete_after=10)
            return
        await download_and_zip(interaction, filtered_files, self.radar_sites, self.messages_to_delete)


class UTCTimeRangeModal(Modal, title="Enter UTC Time Range"):
    time_range = TextInput(label="UTC Time Range (e.g., 6-8 or 14:00-16:00)", placeholder="e.g., 6-8 or 14:00-16:00", required=True)

    def __init__(self, radar_sites, date, messages_to_delete, original_user=None):
        super().__init__()
        self.radar_sites = radar_sites
        self.date = date
        self.messages_to_delete = messages_to_delete
        self.original_user = original_user

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            range_str = self.time_range.value.replace("Z", "").strip()
            start_str, end_str = range_str.split("-")
            start_hour = int(start_str.split(":")[0].zfill(2))
            start_min = int(start_str.split(":")[1].zfill(2)) if ":" in start_str else 0
            end_hour = int(end_str.split(":")[0].zfill(2))
            end_min = int(end_str.split(":")[1].zfill(2)) if ":" in end_str else 0
            start_dt = self.date.replace(hour=start_hour, minute=start_min, second=0, microsecond=0)
            end_dt = self.date.replace(hour=end_hour, minute=end_min, second=0, microsecond=0)
            if end_dt < start_dt:
                end_dt += timedelta(days=1)
            all_files = []
            dates_to_query = [self.date]
            if start_dt.date() < self.date.date():
                dates_to_query.append(self.date - timedelta(days=1))
            for radar_site in self.radar_sites:
                files = await asyncio.get_event_loop().run_in_executor(None, list_files, radar_site, dates_to_query)
                all_files.extend(files)
            filtered_files = [f for f in all_files if start_dt <= f['FileTimestamp'] <= end_dt]
            if not filtered_files:
                await interaction.followup.send(
                    embed=discord.Embed(title="No Files Found", description=f"No files found between {start_dt.strftime('%H:%M')}Z and {end_dt.strftime('%H:%M')}Z.", color=discord.Color.red()),
                    delete_after=10
                )
                return
            await download_and_zip(interaction, filtered_files, self.radar_sites, self.messages_to_delete)
        except (ValueError, IndexError):
            await interaction.followup.send(
                embed=discord.Embed(title="Invalid Time Range", description="Use format: HH-HH or HH:MM-HH:MM", color=discord.Color.red()),
                ephemeral=True
            )


class SearchModal(Modal, title="Search Radar Sites"):
    search_input = TextInput(label="Enter Radar Site Code (e.g., KT)", placeholder="e.g., KT for KTLX", required=True)

    def __init__(self, radar_sites, messages_to_delete, original_user=None):
        super().__init__()
        self.original_user = original_user
        self.radar_sites = radar_sites
        self.messages_to_delete = messages_to_delete

    async def on_submit(self, interaction: discord.Interaction):
        if self.original_user and interaction.user != self.original_user:
            await interaction.response.send_message("This interaction is not yours.", ephemeral=True)
            return
        search_term = self.search_input.value.upper()
        filtered_sites = [site for site in self.radar_sites if search_term in site]
        if not filtered_sites:
            await interaction.response.send_message(
                embed=discord.Embed(title="No Matches Found", description=f"No radar sites found for '{search_term}'.", color=discord.Color.red()),
                ephemeral=True
            )
            return
        if search_term in self.radar_sites:
            view = DateSelectionView([search_term], self.messages_to_delete, original_user=self.original_user)
            embed = discord.Embed(title=f"Selected: {search_term}", description="Select a date for the data:", color=discord.Color.blue())
            await interaction.response.send_message(embed=embed, view=view)
            msg = await interaction.original_response()
            self.messages_to_delete.append(msg)
            return
        options = [SelectOption(label=site, value=site) for site in filtered_sites[:25]]
        select = RadarSiteSelect(placeholder=f"Select a radar site ({len(options)} matches)...", options=options, messages_to_delete=self.messages_to_delete, original_user=self.original_user)
        view = View()
        view.add_item(select)
        embed = discord.Embed(title="Select a Radar Site", description=f"Found {len(filtered_sites)} matches for '{search_term}'.", color=discord.Color.blue())
        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()
        self.messages_to_delete.append(msg)


# ── Cog ───────────────────────────────────────────────────────────────────────
class RadarCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.periodic_cleanup.start()

    def cog_unload(self):
        self.periodic_cleanup.cancel()

    async def _start_download_flow(self, ctx_or_interaction, original_user):
        embed = discord.Embed(
            title="AWS NEXRAD Data Downloader",
            description="Click to start downloading radar data.",
            color=0x0000FF
        )
        view = StartView(original_user=original_user)
        if isinstance(ctx_or_interaction, discord.Interaction):
            await ctx_or_interaction.response.send_message(embed=embed, view=view)
            msg = await ctx_or_interaction.original_response()
        else:
            msg = await ctx_or_interaction.send(embed=embed, view=view)
        view.messages_to_delete.append(msg)

    @commands.command(name="download")
    async def download_prefix(self, ctx):
        await self._start_download_flow(ctx, ctx.author)

    @commands.command(name="dl")
    async def dl_prefix(self, ctx):
        await self._start_download_flow(ctx, ctx.author)

    @discord.app_commands.command(name="download", description="Download NEXRAD Level 2 radar data from AWS S3")
    async def download_slash(self, interaction: discord.Interaction):
        await self._start_download_flow(interaction, interaction.user)

    @discord.app_commands.command(name="downloaderstatus", description="Check AWS downloader and S3 latency")
    async def downloaderstatus_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        ws_latency = round(self.bot.latency * 1000)
        ws_icon = "🟢" if ws_latency < 100 else "🟡" if ws_latency < 200 else "🔴"

        try:
            s3_start = time.time()
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: get_s3_client().list_objects_v2(
                    Bucket='unidata-nexrad-level2',
                    Prefix='2026/',
                    Delimiter='/',
                    MaxKeys=1
                )
            )
            s3_latency = round((time.time() - s3_start) * 1000)
            s3_icon = "🟢" if s3_latency < 500 else "🟡" if s3_latency < 1000 else "🔴"
            s3_status = f"{s3_latency}ms"
        except Exception as e:
            s3_status = f"Error: {e}"
            s3_icon = "🔴"

        embed = discord.Embed(title="AWS NEXRAD Downloader Status", color=discord.Color.blue())
        embed.add_field(name=f"{ws_icon} Discord WS Latency", value=f"`{ws_latency}ms`", inline=True)
        embed.add_field(name=f"{s3_icon} S3 Bucket Latency", value=f"`{s3_status}`", inline=True)
        embed.set_footer(text=f"Logged in as {self.bot.user}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @tasks.loop(hours=1)
    async def periodic_cleanup(self):
        await cleanup_old_files(OUTPUT_DIR, CLEANUP_AGE_THRESHOLD)


async def setup(bot: commands.Bot):
    await bot.add_cog(RadarCog(bot))
