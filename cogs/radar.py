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
            logger.error(f"[RADAR] Error listing files for {radar_site} on {date.strftime('%Y-%m-%d')}: {e}")
            raise RuntimeError(f"Could not reach S3 to list files for {radar_site} on {date.strftime('%Y-%m-%d')}. Check your connection.")
    return sorted(all_files, key=lambda x: x['FileTimestamp'], reverse=True)


def parse_z_time(time_str: str, reference_date: datetime) -> datetime:
    time_str = time_str.strip().upper().replace('Z', '')
    if ':' in time_str:
        parts = time_str.split(':')
        hour = int(parts[0])
        minute = int(parts[1])
    elif len(time_str) == 4 and time_str.isdigit():
        hour = int(time_str[:2])
        minute = int(time_str[2:])
    else:
        hour = int(time_str)
        minute = 0
    return reference_date.replace(hour=hour, minute=minute, second=0, microsecond=0)


def resolve_z_range(start_str: str, end_str: str, reference_date: datetime):
    start_dt = parse_z_time(start_str, reference_date)
    end_dt = parse_z_time(end_str, reference_date)

    if start_dt == end_dt:
        raise ValueError("Start and end times are the same — please enter a valid range.")

    if end_dt <= start_dt:
        end_dt += timedelta(days=1)

    dates_to_query = [reference_date]
    if end_dt.date() > reference_date.date():
        dates_to_query.append(reference_date + timedelta(days=1))
    if start_dt.date() < reference_date.date():
        dates_to_query.insert(0, reference_date - timedelta(days=1))

    return start_dt, end_dt, dates_to_query


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
    try:
        return await asyncio.get_event_loop().run_in_executor(None, _zip)
    except Exception as e:
        logger.error(f"[RADAR] ZIP creation failed: {e}")
        raise RuntimeError(f"Failed to create ZIP file: {e}")


async def send_error(interaction, title, description):
    """Send a clean error embed to the user."""
    embed = discord.Embed(title=f"❌ {title}", description=description, color=discord.Color.red())
    try:
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception:
        try:
            await interaction.channel.send(embed=embed)
        except Exception as e:
            logger.error(f"[RADAR] Could not send error message: {e}")


async def run_download(interaction, radar_sites, messages_to_delete, start_dt, end_dt, dates_to_query=None, max_files=None):
    now = datetime.now(timezone.utc)

    # Catch future time ranges before hitting S3
    if not max_files and start_dt and start_dt > now:
        await send_error(
            interaction,
            "Future Time Range",
            f"The start time `{start_dt.strftime('%Y-%m-%d %H:%MZ')}` is in the future.\n"
            f"No radar files exist yet for that time. Please select a past time range."
        )
        return

    if dates_to_query is None:
        dates_to_query = [start_dt.replace(hour=0, minute=0, second=0, microsecond=0)]
        if end_dt and end_dt.date() > start_dt.date():
            dates_to_query.append((start_dt + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0))

    all_files = []
    try:
        for radar_site in radar_sites:
            files = await asyncio.get_event_loop().run_in_executor(
                None, list_files, radar_site, dates_to_query
            )
            all_files.extend(files)
    except RuntimeError as e:
        await send_error(interaction, "S3 Unreachable", str(e))
        return
    except Exception as e:
        await send_error(interaction, "S3 Error", f"Could not list files from S3: {e}")
        return

    if not all_files:
        await send_error(
            interaction,
            "No Files Found",
            f"No radar files found for `{'`, `'.join(radar_sites)}` on the selected date(s).\n"
            f"The site may not have data for this period."
        )
        return

    if max_files:
        filtered_files = sorted(all_files, key=lambda x: x['FileTimestamp'], reverse=True)[:max_files]
    else:
        filtered_files = [f for f in all_files if start_dt <= f['FileTimestamp'] <= end_dt]

    if not filtered_files:
        # Check if it's because the range is entirely in the future
        if start_dt and start_dt > now:
            await send_error(
                interaction,
                "Future Time Range",
                f"No files exist for `{start_dt.strftime('%H:%MZ')}` to `{end_dt.strftime('%H:%MZ')}` — that time hasn't happened yet."
            )
        else:
            range_str = f"`{start_dt.strftime('%H:%MZ')}` to `{end_dt.strftime('%H:%MZ')}`" if start_dt and end_dt else ""
            await send_error(
                interaction,
                "No Files Matched",
                f"No files were found for the time range {range_str}.\n"
                f"Try widening your range or checking a different date."
            )
        return

    await download_and_zip(interaction, filtered_files, radar_sites, messages_to_delete)


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
            failed = []
            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"[RADAR] File failed all retries: {result}")
                    failed.append(str(result))
                else:
                    file_paths.append(result)

            if failed:
                fail_count = len(failed)
                total_count = len(filtered_files)
                if fail_count == total_count:
                    embed.title = "Download Failed"
                    embed.description = (
                        f"All {fail_count} files failed to download.\n"
                        f"This is usually a connection issue — please try again.\n\n"
                        f"First error: `{failed[0]}`"
                    )
                    embed.color = discord.Color.red()
                    await message.edit(embed=embed)
                    return
                else:
                    logger.warning(f"[RADAR] {fail_count}/{total_count} files failed, continuing with {len(file_paths)} successful")
                    embed.description += f"\n⚠️ {fail_count} file(s) failed to download and will be skipped."
                    try:
                        await message.edit(embed=embed)
                    except Exception:
                        pass

        if not file_paths:
            embed.title = "Download Failed"
            embed.description = "No files were downloaded successfully. Please try again."
            embed.color = discord.Color.red()
            await message.edit(embed=embed)
            return

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

        try:
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
        except RuntimeError as e:
            embed.title = "ZIP Failed"
            embed.description = str(e)
            embed.color = discord.Color.red()
            await message.edit(embed=embed)
            logger.error(f"[RADAR] ZIP creation error: {e}")
            return

        if not all_uploaded:
            embed.title = "Upload Failed"
            embed.description = (
                f"Could not upload files even at minimum size ({format_file_size(MIN_FILE_SIZE)}).\n"
                f"Your server may not be boosted enough for files this large."
            )
            embed.color = discord.Color.red()
            await message.edit(embed=embed)
            logger.error("[RADAR] Upload failed at all size levels")

    except Exception as e:
        logger.error(f"[RADAR] Unexpected error in download_and_zip: {e}", exc_info=True)
        embed.title = "Unexpected Error"
        embed.description = (
            f"Something went wrong during the download.\n"
            f"Error: `{type(e).__name__}: {e}`\n\n"
            f"Please try again. If this keeps happening check the bot logs."
        )
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


# ── Modals ────────────────────────────────────────────────────────────────────

class ZRangeModal(Modal, title="Z-to-Z Time Range"):
    time_range = TextInput(
        label="Time range (e.g. 22Z-04Z or 22:30-04:15)",
        placeholder="22Z-04Z  or  1800Z-0600Z  or  22:30-04:15",
        required=True
    )

    def __init__(self, radar_sites, date, messages_to_delete, original_user=None):
        super().__init__()
        self.radar_sites = radar_sites
        self.date = date
        self.messages_to_delete = messages_to_delete
        self.original_user = original_user

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            raw = self.time_range.value.replace(' ', '')
            parts = raw.split('-')
            if len(parts) == 2:
                start_str, end_str = parts
            else:
                start_str = parts[0]
                end_str = '-'.join(parts[1:])
            start_dt, end_dt, dates_to_query = resolve_z_range(start_str, end_str, self.date)
            logger.info(f"[RADAR] Z-range: {start_dt} to {end_dt}")
            await run_download(interaction, self.radar_sites, self.messages_to_delete, start_dt, end_dt, dates_to_query)
        except ValueError as e:
            await send_error(interaction, "Invalid Time Range", str(e) or f"Could not parse `{self.time_range.value}`.\nUse format: `22Z-04Z` or `22:30-04:15`")
        except Exception as e:
            await send_error(interaction, "Error", f"Something went wrong: {e}")


class StartPlusDurationModal(Modal, title="Start Time + Duration"):
    start_time = TextInput(
        label="Start time in Z (e.g. 22Z or 18:30Z)",
        placeholder="22Z  or  1800Z  or  18:30",
        required=True
    )
    duration = TextInput(
        label="Duration in hours (e.g. 6 or 2.5)",
        placeholder="6",
        required=True
    )

    def __init__(self, radar_sites, date, messages_to_delete, original_user=None):
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
                await send_error(interaction, "Invalid Duration", "Duration must be greater than 0 hours.")
                return
            end_dt = start_dt + timedelta(hours=hours)
            dates_to_query = [self.date]
            if end_dt.date() > self.date.date():
                dates_to_query.append(self.date + timedelta(days=1))
            logger.info(f"[RADAR] Start+duration: {start_dt} + {hours}h = {end_dt}")
            await run_download(interaction, self.radar_sites, self.messages_to_delete, start_dt, end_dt, dates_to_query)
        except ValueError:
            await send_error(
                interaction,
                "Invalid Input",
                "Start time should be like `22Z` or `18:30`. Duration should be a number like `6` or `2.5`."
            )
        except Exception as e:
            await send_error(interaction, "Error", f"Something went wrong: {e}")


class ExplicitRangeModal(Modal, title="Explicit Date/Time Range"):
    start = TextInput(
        label="Start (YYYY-MM-DD HH:MM or HH:MMZ)",
        placeholder="2026-04-02 22:00  or  22:00Z",
        required=True
    )
    end = TextInput(
        label="End (YYYY-MM-DD HH:MM or HH:MMZ)",
        placeholder="2026-04-03 04:00  or  04:00Z",
        required=True
    )

    def __init__(self, radar_sites, date, messages_to_delete, original_user=None):
        super().__init__()
        self.radar_sites = radar_sites
        self.date = date
        self.messages_to_delete = messages_to_delete
        self.original_user = original_user

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            def parse_field(val, reference_date):
                val = val.strip().upper().replace('Z', '')
                for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%d %H', '%H:%M', '%H'):
                    try:
                        dt = datetime.strptime(val, fmt)
                        if fmt in ('%H:%M', '%H'):
                            dt = reference_date.replace(hour=dt.hour, minute=dt.minute, second=0, microsecond=0)
                        else:
                            dt = dt.replace(tzinfo=timezone.utc)
                        return dt.replace(tzinfo=timezone.utc)
                    except ValueError:
                        continue
                raise ValueError(f"Could not parse: `{val}`")

            start_dt = parse_field(self.start.value, self.date)
            end_dt = parse_field(self.end.value, self.date)

            if start_dt == end_dt:
                await send_error(interaction, "Invalid Range", "Start and end times are the same.")
                return

            if end_dt <= start_dt:
                end_dt += timedelta(days=1)

            dates_to_query = []
            d = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
            while d.date() <= end_dt.date():
                dates_to_query.append(d)
                d += timedelta(days=1)

            logger.info(f"[RADAR] Explicit range: {start_dt} to {end_dt}")
            await run_download(interaction, self.radar_sites, self.messages_to_delete, start_dt, end_dt, dates_to_query)
        except ValueError as e:
            await send_error(
                interaction,
                "Invalid Input",
                f"{e}\n\nTry:\n- `2026-04-02 22:00` for full datetime\n- `22:00Z` for time only (uses selected date)"
            )
        except Exception as e:
            await send_error(interaction, "Error", f"Something went wrong: {e}")


class NumFilesModal(Modal, title="Number of Recent Files"):
    num = TextInput(
        label="How many recent files?",
        placeholder="10",
        required=True
    )

    def __init__(self, radar_sites, date, messages_to_delete, original_user=None):
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
                await send_error(interaction, "Invalid Number", "Please enter a number between 1 and 200.")
                return
            now = datetime.now(timezone.utc)
            await run_download(
                interaction, self.radar_sites, self.messages_to_delete,
                start_dt=now, end_dt=now,
                dates_to_query=[self.date],
                max_files=n
            )
        except ValueError:
            await send_error(interaction, "Invalid Number", "Enter a whole number between 1 and 200.")
        except Exception as e:
            await send_error(interaction, "Error", f"Something went wrong: {e}")


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
            embed = discord.Embed(
                title=f"Selected: {', '.join(self.radar_sites)}",
                description=f"Date: {date.strftime('%Y-%m-%d')}\nChoose a time range option:",
                color=discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed, view=view)
            msg = await interaction.original_response()
            self.messages_to_delete.append(msg)
        except ValueError:
            await interaction.response.send_message(
                embed=discord.Embed(title="❌ Invalid Date", description="Please use format: YYYY-MM-DD", color=discord.Color.red()),
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
        invalid_sites = [site for site in entered_sites if site not in self.available_sites]
        if not valid_sites:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ No Valid Radar Sites",
                    description="None of the entered radar sites were found.\nCheck the site codes and try again.",
                    color=discord.Color.red()
                ),
                ephemeral=True
            )
            return
        view = DateSelectionView(valid_sites, self.messages_to_delete, original_user=self.original_user)
        description = "Select a date for the data:"
        if invalid_sites:
            description += f"\n\n⚠️ Skipped unknown sites: `{'`, `'.join(invalid_sites)}`"
        embed = discord.Embed(
            title=f"Selected: {', '.join(valid_sites)}",
            description=description,
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()
        self.messages_to_delete.append(msg)


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
                embed=discord.Embed(
                    title="❌ No Matches Found",
                    description=f"No radar sites found matching `{search_term}`.\nTry a shorter or different search term.",
                    color=discord.Color.red()
                ),
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
        select = RadarSiteSelect(
            placeholder=f"Select a radar site ({len(options)} matches)...",
            options=options,
            messages_to_delete=self.messages_to_delete,
            original_user=self.original_user
        )
        view = View()
        view.add_item(select)
        desc = f"Found {len(filtered_sites)} matches for `{search_term}`."
        if len(filtered_sites) > 25:
            desc += " Showing first 25 — refine your search for more."
        embed = discord.Embed(title="Select a Radar Site", description=desc, color=discord.Color.blue())
        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()
        self.messages_to_delete.append(msg)


# ── Views ─────────────────────────────────────────────────────────────────────

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

    @discord.ui.button(label="Last 1h", style=ButtonStyle.green, row=0)
    async def last_1h(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()
        now = datetime.now(timezone.utc)
        await run_download(interaction, self.radar_sites, self.messages_to_delete,
                           start_dt=now - timedelta(hours=1), end_dt=now,
                           dates_to_query=self._dates_for_hours(1))

    @discord.ui.button(label="Last 2h", style=ButtonStyle.green, row=0)
    async def last_2h(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()
        now = datetime.now(timezone.utc)
        await run_download(interaction, self.radar_sites, self.messages_to_delete,
                           start_dt=now - timedelta(hours=2), end_dt=now,
                           dates_to_query=self._dates_for_hours(2))

    @discord.ui.button(label="Last 3h", style=ButtonStyle.green, row=0)
    async def last_3h(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()
        now = datetime.now(timezone.utc)
        await run_download(interaction, self.radar_sites, self.messages_to_delete,
                           start_dt=now - timedelta(hours=3), end_dt=now,
                           dates_to_query=self._dates_for_hours(3))

    @discord.ui.button(label="Last 4h", style=ButtonStyle.green, row=0)
    async def last_4h(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()
        now = datetime.now(timezone.utc)
        await run_download(interaction, self.radar_sites, self.messages_to_delete,
                           start_dt=now - timedelta(hours=4), end_dt=now,
                           dates_to_query=self._dates_for_hours(4))

    @discord.ui.button(label="Z-to-Z Range", style=ButtonStyle.blurple, row=1)
    async def z_range(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(
            ZRangeModal(self.radar_sites, self.date, self.messages_to_delete, self.original_user)
        )

    @discord.ui.button(label="Start + Duration", style=ButtonStyle.blurple, row=1)
    async def start_duration(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(
            StartPlusDurationModal(self.radar_sites, self.date, self.messages_to_delete, self.original_user)
        )

    @discord.ui.button(label="Explicit Range", style=ButtonStyle.blurple, row=1)
    async def explicit_range(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(
            ExplicitRangeModal(self.radar_sites, self.date, self.messages_to_delete, self.original_user)
        )

    @discord.ui.button(label="N Most Recent", style=ButtonStyle.grey, row=1)
    async def n_most_recent(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(
            NumFilesModal(self.radar_sites, self.date, self.messages_to_delete, self.original_user)
        )

    def _dates_for_hours(self, hours):
        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=hours)
        dates = [self.date]
        if start.date() < self.date.date():
            dates.insert(0, self.date - timedelta(days=1))
        return dates


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
        embed = discord.Embed(
            title=f"Selected: {', '.join(self.radar_sites)}",
            description=f"Date: {date.strftime('%Y-%m-%d')}\nChoose a time range option:",
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()
        self.messages_to_delete.append(msg)

    @discord.ui.button(label="Yesterday", style=ButtonStyle.green)
    async def yesterday(self, interaction: discord.Interaction, button: Button):
        date = (datetime.now(timezone.utc) - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        view = TimeRangeView(self.radar_sites, date, self.messages_to_delete, original_user=self.original_user)
        embed = discord.Embed(
            title=f"Selected: {', '.join(self.radar_sites)}",
            description=f"Date: {date.strftime('%Y-%m-%d')}\nChoose a time range option:",
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()
        self.messages_to_delete.append(msg)

    @discord.ui.button(label="Custom Date", style=ButtonStyle.grey)
    async def custom_date(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(
            DateModal(self.radar_sites, self.messages_to_delete, original_user=self.original_user)
        )


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
                embed=discord.Embed(
                    title="❌ No Radar Sites Found",
                    description="Could not retrieve radar sites from S3.\nS3 may be unreachable or there is no data for yesterday.",
                    color=discord.Color.red()
                ),
                delete_after=15
            )
            return
        view = RadarSiteView(radar_sites, self.messages_to_delete, original_user=self.original_user)
        embed = discord.Embed(
            title="AWS NEXRAD Data Downloader",
            description=f"Select a radar site ({len(radar_sites)} available):",
            color=discord.Color.blue()
        )
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
