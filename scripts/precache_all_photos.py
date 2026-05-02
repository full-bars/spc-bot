"""
One-time migration script to pre-cache DAT photos for all historical tornadoes.
Ensures instant browsing in the /recenttornadoes dashboard.
"""

import asyncio
import os
import sys
import logging
from dotenv import load_dotenv

# Add project root to path
sys.path.append(os.getcwd())

from utils.events_db import get_recent_significant_events, cache_dat_photos

# Set up logging to stdout
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("precache_migration")

async def main():
    load_dotenv()
    
    # 1. Fetch all tornado records from the last 30 days
    logger.info("Fetching tornado records from last 30 days...")
    events = await get_recent_significant_events(event_type="Tornado", since_hours=720)
    
    if not events:
        logger.info("No tornado records found to pre-cache.")
        return

    logger.info(f"Found {len(events)} events. Starting parallel pre-cache...")

    # 2. Process in small batches to avoid hitting API rate limits too hard
    batch_size = 5
    for i in range(0, len(events), batch_size):
        batch = events[i : i + batch_size]
        tasks = []
        for e in batch:
            event_id = e["event_id"]
            location = e["location"]
            magnitude = e.get("magnitude", "")
            coords = e.get("coords", "")
            
            logger.info(f"  > Queueing: {location} ({event_id})")
            tasks.append(cache_dat_photos(event_id, location, magnitude, coords))
            
        results = await asyncio.gather(*tasks)
        cached_this_batch = sum(results)
        logger.info(f"Batch complete. Cached {cached_this_batch} new photos.")
        
        # Small delay between batches to be nice to NOAA's server
        await asyncio.sleep(1)

    logger.info("\n✅ Pre-caching migration complete.")

if __name__ == "__main__":
    asyncio.run(main())
