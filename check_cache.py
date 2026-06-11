import asyncio
import os
import sys

sys.path.append(os.getcwd())
from utils.state_store import _get_redis_client, get_product_cache


async def check():
    redis = _get_redis_client()
    keys = await redis.keys("ai_summary_outlook_day3_*")
    print(f"Keys found: {keys}")
    for k in keys:
        val = await get_product_cache(k.decode("utf-8"))
        print(f"Value for {k.decode('utf-8')}: {val}")


if __name__ == "__main__":
    asyncio.run(check())
