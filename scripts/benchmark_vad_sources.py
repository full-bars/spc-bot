import asyncio
import time
import aiohttp
import aioboto3
from botocore.config import Config
import botocore
from datetime import datetime, timezone
import numpy as np


async def benchmark_tgftp(rid, iterations=5):
    print(f"Benchmarking TGFTP for {rid}...")
    latencies = []

    async with aiohttp.ClientSession() as session:
        for i in range(iterations):
            start = time.perf_counter()
            # 1. Directory listing (required to find filenames)
            url_dir = (
                f"https://tgftp.nws.noaa.gov/SL.us008001/DF.of/DC.radar/DS.48vwp/SI.{rid.lower()}/"
            )
            async with session.get(url_dir) as resp:
                await resp.text()

            # 2. Download sn.last
            url_file = f"{url_dir}sn.last"
            async with session.get(url_file) as resp:
                await resp.read()

            end = time.perf_counter()
            latencies.append(end - start)
            print(f"  Iteration {i + 1}: {(end - start) * 1000:.2f}ms")

    avg = np.mean(latencies) * 1000
    print(f"TGFTP Average: {avg:.2f}ms")
    return avg


async def benchmark_s3(rid, iterations=5):
    print(f"Benchmarking S3 for {rid}...")
    latencies = []
    bucket = "unidata-nexrad-level3"
    prefix = f"{rid[1:]}_NVW_{datetime.now(timezone.utc).strftime('%Y_%m_%d')}"

    session = aioboto3.Session()
    async with session.client(
        "s3", config=Config(signature_version=botocore.UNSIGNED), region_name="us-east-1"
    ) as s3:
        for i in range(iterations):
            start = time.perf_counter()
            # 1. Listing
            response = await s3.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1)
            if "Contents" in response:
                key = response["Contents"][0]["Key"]
                # 2. Download
                resp = await s3.get_object(Bucket=bucket, Key=key)
                await resp["Body"].read()

            end = time.perf_counter()
            latencies.append(end - start)
            print(f"  Iteration {i + 1}: {(end - start) * 1000:.2f}ms")

    avg = np.mean(latencies) * 1000
    print(f"S3 Average: {avg:.2f}ms")
    return avg


async def main():
    rid = "KTLX"
    tgftp_avg = await benchmark_tgftp(rid)
    s3_avg = await benchmark_s3(rid)

    print("\n" + "=" * 30)
    print(f"FINAL COMPARISON ({rid})")
    print(f"TGFTP: {tgftp_avg:.2f}ms")
    print(f"S3:    {s3_avg:.2f}ms")
    diff = abs(tgftp_avg - s3_avg)
    winner = "S3" if s3_avg < tgftp_avg else "TGFTP"
    print(f"Winner: {winner} (faster by {diff:.2f}ms)")
    print("=" * 30)


if __name__ == "__main__":
    asyncio.run(main())
