# cogs/radar/s3.py
"""S3 interaction for NEXRAD Level 2 radar data."""

import logging
import os
import threading
from datetime import datetime, timedelta, timezone

import boto3
import botocore

logger = logging.getLogger("spc_bot")

_thread_local = threading.local()


def get_s3_client():
    if not hasattr(_thread_local, "s3_client"):
        _thread_local.s3_client = boto3.client(
            "s3",
            config=botocore.config.Config(signature_version=botocore.UNSIGNED),
        )
    return _thread_local.s3_client


def get_radar_sites(date):
    bucket = "unidata-nexrad-level2"
    prefix = f"{date.year}/{date.month:02d}/{date.day:02d}/"
    try:
        response = get_s3_client().list_objects_v2(
            Bucket=bucket, Prefix=prefix, Delimiter="/"
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
        logger.error(f"[RADAR] Error listing radar sites: {e}")
        return []


def list_files(radar_site, dates):
    bucket = "unidata-nexrad-level2"
    all_files = []
    for date in dates:
        prefix = f"{date.year}/{date.month:02d}/{date.day:02d}/{radar_site}/"
        try:
            response = get_s3_client().list_objects_v2(
                Bucket=bucket, Prefix=prefix
            )
            if "Contents" not in response:
                continue
            files = [
                {
                    "Key": obj["Key"],
                    "LastModified": obj["LastModified"].replace(
                        tzinfo=timezone.utc
                    ),
                    "Size": obj["Size"],
                    "RadarSite": radar_site,
                }
                for obj in response["Contents"]
                if not (
                    obj["Key"].lower().endswith(".tar")
                    or "_mdm" in obj["Key"].lower()
                )
            ]
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
            logger.error(
                f"[RADAR] Error listing files for {radar_site} "
                f"on {date.strftime('%Y-%m-%d')}: {e}"
            )
            raise RuntimeError(
                f"Could not reach S3 to list files for {radar_site} "
                f"on {date.strftime('%Y-%m-%d')}. Check your connection."
            )
    return sorted(all_files, key=lambda x: x["FileTimestamp"], reverse=True)


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
