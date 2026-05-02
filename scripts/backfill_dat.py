"""
One-off script to backfill DAT GUIDs for recent tornadoes using geographic proximity.
Run this to link missing tracks to your database immediately.
"""

import asyncio
import os
import sys
from dotenv import load_dotenv

# Add project root to path
sys.path.append(os.getcwd())

from utils.events_db import backfill_dat_guids

async def main():
    load_dotenv()
    print("Starting geographic backfill of DAT GUIDs...")
    print("This will search the last 7 days of official NOAA survey tracks.")
    
    await backfill_dat_guids(days=7)
    
    print("\nBackfill complete. Check bot logs for results.")

if __name__ == "__main__":
    asyncio.run(main())
