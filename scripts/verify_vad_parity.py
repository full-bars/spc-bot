
import asyncio
import aiohttp
import aioboto3
from botocore.config import Config
import botocore
import logging
import numpy as np

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("parity_check")

async def fetch_tgftp_raw(rid, filename):
    url = f"https://tgftp.nws.noaa.gov/SL.us008001/DF.of/DC.radar/DS.48vwp/SI.{rid.lower()}/{filename}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                return await resp.read()
    return None

async def fetch_s3_raw(bucket, key):
    session = aioboto3.Session()
    async with session.client("s3", config=Config(signature_version=botocore.UNSIGNED), region_name="us-east-1") as s3:
        resp = await s3.get_object(Bucket=bucket, Key=key)
        return await resp["Body"].read()

async def main():
    # Site and Product
    rid = "KTLX"
    tgftp_file = "sn.last" # This is the most recent
    
    print(f"Comparing data for {rid}...")
    
    # 1. Fetch from TGFTP
    tgftp_content = await fetch_tgftp_raw(rid, tgftp_file)
    if not tgftp_content:
        print("FAILED to fetch from TGFTP")
        return
    print(f"TGFTP content: {len(tgftp_content)} bytes")
    
    # 2. Parse TGFTP to get the timestamp (for S3 lookup)
    from lib.vad_plotter.vad_reader import VADFile
    try:
        vad_tgftp = VADFile(tgftp_content)
        ts = vad_tgftp['time']
        print(f"TGFTP Time: {ts}")
    except Exception as e:
        print(f"FAILED to parse TGFTP content: {e}")
        return
        
    # 3. Fetch from S3 using the timestamp
    bucket = "unidata-nexrad-level3"
    # S3 Key format: TLX_NVW_YYYY_MM_DD_HH_MM_SS
    key = f"{rid[1:]}_NVW_{ts.strftime('%Y_%m_%d_%H_%M_%S')}"
    print(f"Looking for S3 key: {key}")
    
    s3_content = None
    try:
        s3_content = await fetch_s3_raw(bucket, key)
        print(f"S3 content: {len(s3_content)} bytes")
    except Exception as e:
        print(f"FAILED to fetch from S3: {e}")
        # Try a small window if seconds don't match exactly?
        # Sometimes S3 and TGFTP timestamps differ by 1-2 seconds if they use different headers
        print("Searching S3 for nearby keys...")
        prefix = f"{rid[1:]}_NVW_{ts.strftime('%Y_%m_%d_%H_%M')}"
        session = aioboto3.Session()
        async with session.client("s3", config=Config(signature_version=botocore.UNSIGNED), region_name="us-east-1") as s3:
            response = await s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
            if "Contents" in response:
                for obj in response["Contents"]:
                    print(f"  Found near match: {obj['Key']}")
                    s3_content = await fetch_s3_raw(bucket, obj['Key'])
                    break

    if not s3_content:
        print("FAILED to find matching S3 content")
        return

    # 4. Compare parsed data
    vad_s3 = VADFile(s3_content)
    print(f"S3 Time: {vad_s3['time']}")
    
    fields = ['altitude', 'wind_dir', 'wind_spd']
    all_match = True
    for field in fields:
        data_tgftp = vad_tgftp[field]
        data_s3 = vad_s3[field]
        
        if len(data_tgftp) != len(data_s3):
            print(f"MISMATCH in {field} length: TGFTP={len(data_tgftp)}, S3={len(data_s3)}")
            all_match = False
            continue
            
        if not np.allclose(data_tgftp, data_s3, equal_nan=True):
            print(f"MISMATCH in {field} values!")
            print(f"  TGFTP: {data_tgftp[:5]}")
            print(f"  S3:    {data_s3[:5]}")
            all_match = False
        else:
            print(f"SUCCESS: {field} values match perfectly.")

    if all_match:
        print("\nDATA PARITY VERIFIED SUCCESSFULLY!")
    else:
        print("\nDATA PARITY FAILED!")

if __name__ == "__main__":
    import sys
    import os
    # Add current dir to path for imports
    sys.path.append(os.getcwd())
    asyncio.run(main())
