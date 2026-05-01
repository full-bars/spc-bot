# cogs/radar/s3.py
"""Async S3 interaction for NEXRAD Level 2 radar data via aioboto3."""

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import aioboto3
import botocore

logger = logging.getLogger("spc_bot")

BUCKET = "unidata-nexrad-level2"
_session = aioboto3.Session()


@asynccontextmanager
async def _s3():
    """Async context manager for an unsigned S3 client."""
    async with _session.client(
        "s3",
        config=botocore.config.Config(signature_version=botocore.UNSIGNED),
    ) as client:
        yield client


async def get_radar_sites(date: datetime) -> list[str]:
    """List all radar sites available for a given date."""
    prefix = f"{date.year}/{date.month:02d}/{date.day:02d}/"
    try:
        async with _s3() as s3:
            response = await s3.list_objects_v2(
                Bucket=BUCKET, Prefix=prefix, Delimiter="/"
            )
        if "CommonPrefixes" not in response:
            logger.warning(
                f"[RADAR] No radar sites found for {date.strftime('%Y-%m-%d')}"
            )
            return []
        sites = sorted(
            [p["Prefix"].split("/")[-2] for p in response["CommonPrefixes"]]
        )
        logger.info(
            f"[RADAR] Found {len(sites)} radar sites "
            f"for {date.strftime('%Y-%m-%d')}"
        )
        return sites
    except Exception as e:
        logger.exception(f"[RADAR] Error listing radar sites: {e}")
        return []


async def list_files(radar_site: str, dates: list) -> list[dict]:
    """List all NEXRAD files for a radar site across given dates."""
    all_files = []
    async with _s3() as s3:
        for date in dates:
            prefix = f"{date.year}/{date.month:02d}/{date.day:02d}/{radar_site}/"
            try:
                # Paginate: a busy NEXRAD site can exceed the 1,000-object page limit.
                page_kwargs: dict = {"Bucket": BUCKET, "Prefix": prefix}
                files = []
                while True:
                    response = await s3.list_objects_v2(**page_kwargs)
                    for obj in response.get("Contents", []):
                        if obj["Key"].lower().endswith(".tar") or "_mdm" in obj["Key"].lower():
                            continue
                        files.append({
                            "Key": obj["Key"],
                            "LastModified": obj["LastModified"].replace(tzinfo=timezone.utc),
                            "Size": obj["Size"],
                            "RadarSite": radar_site,
                        })
                    if not response.get("IsTruncated"):
                        break
                    page_kwargs["ContinuationToken"] = response["NextContinuationToken"]
                for f in files:
                    filename = os.path.basename(f["Key"])
                    try:
                        if (
                            len(filename) >= 19
                            and filename[4:12].isdigit()
                            and filename[13:19].isdigit()
                        ):
                            f["FileTimestamp"] = datetime.strptime(
                                filename[4:12] + filename[13:19], "%Y%m%d%H%M%S"
                            ).replace(tzinfo=timezone.utc)
                        else:
                            f["FileTimestamp"] = f["LastModified"]
                    except ValueError:
                        f["FileTimestamp"] = f["LastModified"]
                all_files.extend(files)
            except Exception as e:
                logger.exception(
                    f"[RADAR] Error listing files for {radar_site} "
                    f"on {date.strftime('%Y-%m-%d')}: {e}"
                )
                raise RuntimeError(
                    f"Could not reach S3 to list files for {radar_site} "
                    f"on {date.strftime('%Y-%m-%d')}. Check your connection."
                ) from e
    return sorted(all_files, key=lambda x: x["FileTimestamp"], reverse=True)


async def s3_download_file(file_key: str, output_path: str, progress_callback=None) -> None:
    """Download a single file from S3 with optional progress callback."""
    async with _s3() as s3:
        await s3.download_file(
            BUCKET,
            file_key,
            output_path,
            Callback=progress_callback,
        )


def parse_z_time(time_str: str, reference_date: datetime) -> datetime:
    time_str = time_str.strip().upper().replace("Z", "")
    if ":" in time_str:
        parts = time_str.split(":")
        hour = int(parts[0])
        minute = int(parts[1])
    elif len(time_str) == 4 and time_str.isdigit():
        hour = int(time_str[:2])
        minute = int(time_str[2:])
    else:
        hour = int(time_str)
        minute = 0
    return reference_date.replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )


def resolve_z_range(start_str: str, end_str: str, reference_date: datetime):
    start_dt = parse_z_time(start_str, reference_date)
    end_dt = parse_z_time(end_str, reference_date)

    if start_dt == end_dt:
        raise ValueError(
            "Start and end times are the same — please enter a valid range."
        )

    if end_dt <= start_dt:
        end_dt += timedelta(days=1)

    dates_to_query = [reference_date]
    if end_dt.date() > reference_date.date():
        dates_to_query.append(reference_date + timedelta(days=1))
    if start_dt.date() < reference_date.date():
        dates_to_query.insert(0, reference_date - timedelta(days=1))

    return start_dt, end_dt, dates_to_query
