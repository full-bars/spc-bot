# cogs/radar/downloads.py
"""Download orchestration, progress tracking, zipping, and cleanup."""

import asyncio
import logging
import os
import shutil
import threading
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord

from cogs.radar.s3 import s3_download_file, list_files

logger = logging.getLogger("spc_bot")

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
    for unit in ["B", "KB", "MB", "GB"]:
        if size_in_bytes < 1024.0:
            return f"{size_in_bytes:.2f} {unit}"
        size_in_bytes /= 1024.0
    return f"{size_in_bytes:.2f} TB"


def get_progress_bar(progress_percentage, length=30):
    filled = min(int(progress_percentage / (100 / length)), length)
    bar = "█" * filled + "░" * (length - filled)
    return f"{bar} {min(progress_percentage, 100):.1f}%"


async def download_file(file_key, output_dir, start_time, file_size, filename):
    output_path = Path(output_dir) / filename
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    def progress_callback(bytes_amount):
        with progress_lock:
            if filename not in progress_data:
                progress_data[filename] = {
                    "bytes_transferred": 0,
                    "completed": False,
                }
            progress_data[filename]["bytes_transferred"] += bytes_amount
            if progress_data[filename]["bytes_transferred"] >= file_size:
                progress_data[filename]["completed"] = True

    last_err = None
    for attempt in range(3):
        if attempt > 0:
            logger.warning(f"[RADAR] Retry {attempt}/2 for {filename}")
            await asyncio.sleep(2**attempt)
            with progress_lock:
                progress_data[filename] = {
                    "bytes_transferred": 0,
                    "completed": False,
                }
        try:
            await asyncio.wait_for(
                s3_download_file(file_key, str(output_path), progress_callback),
                timeout=30,
            )
            download_time = time.time() - start_time
            speed = (
                (file_size / download_time / 1024 / 1024) * 8
                if download_time > 0
                else 0
            )
            logger.debug(
                f"[RADAR] Downloaded {filename} in {download_time:.1f}s "
                f"at {speed:.1f} Mbps"
            )
            return output_path, download_time, speed
        except asyncio.TimeoutError:
            logger.warning(
                f"[RADAR] Timeout on attempt {attempt + 1}/3 "
                f"for {filename} (30s)"
            )
            if output_path.exists():
                output_path.unlink()
            last_err = RuntimeError(f"Download timed out: {filename}")
        except Exception as e:
            logger.warning(
                f"[RADAR] Error on attempt {attempt + 1}/3 "
                f"for {filename}: {e}"
            )
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
                    logger.exception(
                        f"[RADAR] Failed to delete old file {item}: {e}"
                    )


async def split_and_zip_files(
    file_paths, radar_sites, split_size, output_dir
):
    """
    Zip files grouped by radar site. Each site gets its own zip(s).
    Returns a list of zip paths.
    """

    def _zip():
        by_site = {site: [] for site in radar_sites}
        for file_path, file_info in file_paths:
            site = file_info.get("RadarSite", radar_sites[0])
            by_site[site].append((file_path, file_info))

        all_zip_paths = []
        for site, site_files in by_site.items():
            if not site_files:
                continue
            chunk_size = 0
            current_zip_files = []
            zip_counter = 1
            site_zip_paths = []
            for file_path, file_info in site_files:
                file_size = os.path.getsize(file_path)
                if (
                    chunk_size + file_size > split_size
                    and current_zip_files
                ):
                    zip_path = (
                        Path(output_dir) / f"{site}_part{zip_counter}.zip"
                    )
                    with zipfile.ZipFile(
                        zip_path, "w", zipfile.ZIP_DEFLATED
                    ) as zipf:
                        for fp, fi in current_zip_files:
                            zipf.write(
                                fp,
                                os.path.join(
                                    site, os.path.basename(fp)
                                ),
                            )
                    site_zip_paths.append(zip_path)
                    current_zip_files = []
                    chunk_size = 0
                    zip_counter += 1
                current_zip_files.append((file_path, file_info))
                chunk_size += file_size
            if current_zip_files:
                zip_path = (
                    Path(output_dir) / f"{site}_part{zip_counter}.zip"
                )
                with zipfile.ZipFile(
                    zip_path, "w", zipfile.ZIP_DEFLATED
                ) as zipf:
                    for fp, fi in current_zip_files:
                        zipf.write(
                            fp,
                            os.path.join(site, os.path.basename(fp)),
                        )
                site_zip_paths.append(zip_path)
            if len(site_zip_paths) == 1:
                clean_path = Path(output_dir) / f"{site}.zip"
                site_zip_paths[0].rename(clean_path)
                site_zip_paths[0] = clean_path
            all_zip_paths.extend(site_zip_paths)
        return all_zip_paths

    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _zip)
    except Exception as e:
        logger.exception(f"[RADAR] ZIP creation failed: {e}")
        raise RuntimeError(f"Failed to create ZIP file: {e}") from e


async def send_error(interaction, title, description):
    """Send a clean error embed to the user."""
    embed = discord.Embed(
        title=f"❌ {title}",
        description=description,
        color=discord.Color.red(),
    )
    try:
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception:
        try:
            await interaction.channel.send(embed=embed)
        except Exception as e:
            logger.exception(f"[RADAR] Could not send error message: {e}")


async def run_download(
    interaction,
    radar_sites,
    messages_to_delete,
    start_dt,
    end_dt,
    dates_to_query=None,
    max_files=None,
):
    now = datetime.now(timezone.utc)

    if not max_files and start_dt and start_dt > now:
        await send_error(
            interaction,
            "Future Time Range",
            f"The start time `{start_dt.strftime('%Y-%m-%d %H:%MZ')}` "
            f"is in the future.\nNo radar files exist yet for that time. "
            f"Please select a past time range.",
        )
        return

    if dates_to_query is None:
        dates_to_query = [
            start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        ]
        if end_dt and end_dt.date() > start_dt.date():
            dates_to_query.append(
                (start_dt + timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
            )

    all_files = []
    try:
        for radar_site in radar_sites:
            files = await list_files(radar_site, dates_to_query)
            all_files.extend(files)
    except RuntimeError as e:
        await send_error(interaction, "S3 Unreachable", str(e))
        return
    except Exception as e:
        await send_error(
            interaction, "S3 Error", f"Could not list files from S3: {e}"
        )
        return

    if not all_files:
        await send_error(
            interaction,
            "No Files Found",
            f"No radar files found for "
            f"`{'`, `'.join(radar_sites)}` on the selected date(s).\n"
            f"The site may not have data for this period.",
        )
        return

    if max_files:
        filtered_files = sorted(
            all_files, key=lambda x: x["FileTimestamp"], reverse=True
        )[:max_files]
    else:
        filtered_files = [
            f
            for f in all_files
            if start_dt <= f["FileTimestamp"] <= end_dt
        ]

    if not filtered_files:
        if start_dt and start_dt > now:
            await send_error(
                interaction,
                "Future Time Range",
                f"No files exist for `{start_dt.strftime('%H:%MZ')}` to "
                f"`{end_dt.strftime('%H:%MZ')}` — "
                f"that time hasn't happened yet.",
            )
        else:
            range_str = (
                f"`{start_dt.strftime('%H:%MZ')}` to "
                f"`{end_dt.strftime('%H:%MZ')}`"
                if start_dt and end_dt
                else ""
            )
            await send_error(
                interaction,
                "No Files Matched",
                f"No files were found for the time range {range_str}.\n"
                f"Try widening your range or checking a different date.",
            )
        return

    await download_and_zip(
        interaction, filtered_files, radar_sites, messages_to_delete
    )


async def download_and_zip(
    interaction, filtered_files, radar_sites, messages_to_delete
):
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
            filename = os.path.basename(f["Key"])
            progress_data[filename] = {
                "bytes_transferred": 0,
                "completed": False,
            }

    stats_by_site = {
        radar_site: {"files": []} for radar_site in radar_sites
    }
    for f in filtered_files:
        stats_by_site[f["RadarSite"]]["files"].append(f)

    logger.info(
        f"[RADAR] Starting download of {total_files} files "
        f"for {radar_sites}"
    )

    embed = discord.Embed(
        title="Download Progress",
        description=f"Starting download of {total_files} files...\n",
        color=discord.Color.blue(),
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
            instantaneous_speed = (
                (total_downloaded_size / elapsed_time / 1024 / 1024) * 8
                if elapsed_time > 0
                else 0
            )
            progress = (files_completed / total_files) * 100
            progress_bar = get_progress_bar(progress)
            header = (
                f"Downloading {total_files} files...\n"
                f"{progress_bar} (Speed: {instantaneous_speed:.2f} Mbps) "
                f"· {files_completed}/{total_files} complete\n\n"
            )
            with progress_lock:
                file_lines = []
                for fname in progress_data:
                    matched_file = None
                    for site in radar_sites:
                        matched_file = next(
                            (
                                f
                                for f in stats_by_site[site]["files"]
                                if os.path.basename(f["Key"]) == fname
                            ),
                            None,
                        )
                        if matched_file:
                            break
                    if matched_file:
                        percentage = (
                            progress_data[fname]["bytes_transferred"]
                            / matched_file["Size"]
                        ) * 100
                        if 0 < percentage < 100:
                            file_bar = get_progress_bar(percentage)
                            file_lines.append(f"**{fname}**: {file_bar}")
                per_file_text = "\n".join(file_lines)
                description = header + per_file_text
                if len(description) > 3900:
                    description = header
            embed.description = description
            try:
                await message.edit(embed=embed)
            except (
                discord.errors.NotFound,
                discord.errors.HTTPException,
            ) as e:
                logger.exception(
                    f"[RADAR] Failed to edit progress message: {e}"
                )
            last_update_time = current_time

        async def download_with_progress(file_info, idx):
            nonlocal total_downloaded_size, total_download_time
            nonlocal files_completed
            file_start_time = time.time()
            filename = os.path.basename(file_info["Key"])
            file_path, download_time, speed = await download_file(
                file_info["Key"],
                output_dir,
                file_start_time,
                file_info["Size"],
                filename,
            )
            total_downloaded_size += file_info["Size"]
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
                    current_batch_size = max(
                        BATCH_SIZE_THRESHOLD, current_batch_size // 2
                    )
                    logger.info(
                        f"[RADAR] Speed drop detected, reducing batch "
                        f"size to {current_batch_size}"
                    )
                if len(avg_speed_history) > 10:
                    avg_speed_history.pop(0)
            batch = files_remaining[:batch_size]
            files_remaining = files_remaining[batch_size:]
            tasks_list = [
                download_with_progress(fi, i + 1)
                for i, fi in enumerate(batch)
            ]
            results = await asyncio.gather(
                *tasks_list, return_exceptions=True
            )
            failed = []
            for result in results:
                if isinstance(result, Exception):
                    logger.error(
                        f"[RADAR] File failed all retries: {result}"
                    )
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
                        f"This is usually a connection issue — "
                        f"please try again.\n\n"
                        f"First error: `{failed[0]}`"
                    )
                    embed.color = discord.Color.red()
                    await message.edit(embed=embed)
                    return
                else:
                    logger.warning(
                        f"[RADAR] {fail_count}/{total_count} files "
                        f"failed, continuing with "
                        f"{len(file_paths)} successful"
                    )
                    embed.description += (
                        f"\n⚠️ {fail_count} file(s) failed to download "
                        f"and will be skipped."
                    )
                    try:
                        await message.edit(embed=embed)
                    except Exception:
                        pass

        if not file_paths:
            embed.title = "Download Failed"
            embed.description = (
                "No files were downloaded successfully. Please try again."
            )
            embed.color = discord.Color.red()
            await message.edit(embed=embed)
            return

        await update_progress()

        elapsed_time = time.time() - start_time
        instantaneous_speed = (
            (total_downloaded_size / elapsed_time / 1024 / 1024) * 8
            if elapsed_time > 0
            else 0
        )
        progress_bar = get_progress_bar(100)
        embed.title = "Download Complete"
        embed.description = (
            f"Downloaded {len(file_paths)} files totaling "
            f"{format_file_size(total_downloaded_size)}.\n"
            f"{progress_bar} (Speed: {instantaneous_speed:.2f} Mbps)"
        )
        embed.color = discord.Color.green()
        await message.edit(embed=embed)
        logger.info(
            f"[RADAR] Download complete: {len(file_paths)} files, "
            f"{format_file_size(total_downloaded_size)}, "
            f"{instantaneous_speed:.1f} Mbps avg"
        )

        size_ladder = [
            MAX_FILE_SIZE,
            50 * 1024 * 1024,
            25 * 1024 * 1024,
            MIN_FILE_SIZE,
        ]
        all_uploaded = False

        try:
            for attempt_size in size_ladder:
                for old_zip in Path(output_dir).glob("*.zip"):
                    try:
                        old_zip.unlink()
                    except Exception:
                        pass
                zip_paths = await split_and_zip_files(
                    file_paths, radar_sites, attempt_size, output_dir
                )
                upload_failed = False
                for i, zip_path in enumerate(zip_paths, 1):
                    zip_size = os.path.getsize(zip_path)
                    part_label = (
                        f" - Part {i} of {len(zip_paths)}"
                        if len(zip_paths) > 1
                        else ""
                    )
                    embed.title = "Uploading"
                    embed.description = (
                        f"Uploading {zip_path.name} "
                        f"({format_file_size(zip_size)}){part_label}...\n"
                        f"{progress_bar} "
                        f"(Speed: {instantaneous_speed:.2f} Mbps)"
                    )
                    embed.color = discord.Color.purple()
                    await message.edit(embed=embed)
                    try:
                        await channel.send(
                            file=discord.File(zip_path)
                        )
                        embed.title = "Upload Complete"
                        embed.description = (
                            f"Successfully uploaded {zip_path.name} "
                            f"({format_file_size(zip_size)})"
                            f"{part_label}.\n"
                            f"{progress_bar} "
                            f"(Speed: {instantaneous_speed:.2f} Mbps)"
                        )
                        embed.color = discord.Color.green()
                        await message.edit(embed=embed)
                        logger.info(
                            f"[RADAR] Uploaded {zip_path.name} "
                            f"({format_file_size(zip_size)}){part_label}"
                        )
                    except discord.errors.HTTPException as e:
                        if (
                            e.status == 413
                            and attempt_size > MIN_FILE_SIZE
                        ):
                            next_size = size_ladder[
                                size_ladder.index(attempt_size) + 1
                            ]
                            embed.title = "Upload Failed — Retrying"
                            embed.description = (
                                f"File too large at "
                                f"{format_file_size(attempt_size)}. "
                                f"Retrying with "
                                f"{format_file_size(next_size)} parts..."
                            )
                            embed.color = discord.Color.orange()
                            await message.edit(embed=embed)
                            upload_failed = True
                            break
                        else:
                            embed.title = "Upload Failed"
                            embed.description = (
                                f"Failed to upload {zip_path.name}: "
                                f"{e}\n{progress_bar}"
                            )
                            embed.color = discord.Color.red()
                            await message.edit(embed=embed)
                            logger.exception(
                                f"[RADAR] Upload failed for "
                                f"{zip_path.name}: {e}"
                            )
                            return
                if not upload_failed:
                    all_uploaded = True
                    break
        except RuntimeError as e:
            embed.title = "ZIP Failed"
            embed.description = str(e)
            embed.color = discord.Color.red()
            await message.edit(embed=embed)
            logger.exception(f"[RADAR] ZIP creation error: {e}")
            return

        if not all_uploaded:
            embed.title = "Upload Failed"
            embed.description = (
                f"Could not upload files even at minimum size "
                f"({format_file_size(MIN_FILE_SIZE)}).\n"
                f"Your server may not be boosted enough for files "
                f"this large."
            )
            embed.color = discord.Color.red()
            await message.edit(embed=embed)
            logger.error("[RADAR] Upload failed at all size levels")

    except Exception as e:
        logger.exception(
            f"[RADAR] Unexpected error in download_and_zip: {e}",
        )
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
                    logger.exception(
                        f"[RADAR] Failed to delete temp file "
                        f"{file_path}: {e}"
                    )
        for zip_path in Path(output_dir).glob("*.zip"):
            try:
                zip_path.unlink()
            except Exception as e:
                logger.exception(
                    f"[RADAR] Failed to delete zip {zip_path}: {e}"
                )
        if os.path.exists(output_dir):
            try:
                shutil.rmtree(output_dir)
            except Exception as e:
                logger.exception(
                    f"[RADAR] Failed to delete output dir: {e}"
                )
        with progress_lock:
            progress_data.clear()
        for msg in messages_to_delete:
            if hasattr(msg, "delete"):
                try:
                    await msg.delete()
                except (
                    discord.errors.NotFound,
                    discord.errors.HTTPException,
                ):
                    pass
