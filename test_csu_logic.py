import asyncio
import aiohttp
from datetime import datetime, timedelta, timezone

BASE = "https://schumacher.atmos.colostate.edu/weather/csu_mlp/archive"
VERSION = "2021"

def _product_slug(day):
    return f"severe_ml_day{day}_all_gefso" if day <= 3 else f"severe_ml_day{day}_gefso"

def _build_url(day, init_date, init_hour):
    date_str = init_date.strftime("%Y%m%d")
    valid_date = init_date + timedelta(days=day)
    valid_str = valid_date.strftime("%m%d")
    product = _product_slug(day)
    folder = f"severe_gefso_{VERSION}_day{day}"
    return f"{BASE}/{folder}/{date_str}{init_hour}/{product}_{valid_str}12.png"

async def _url_is_image(url):
    async with aiohttp.ClientSession() as session:
        try:
            async with session.head(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                ct = resp.headers.get("Content-Type", "")
                print(f"URL: {url}")
                print(f"Status: {resp.status}, CT: {ct}")
                return resp.status == 200 and "image" in ct
        except Exception as e:
            print(f"Error: {e}")
            return False

async def main():
    now_utc = datetime.now(timezone.utc)
    today = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)
    
    print(f"Current UTC: {now_utc}")
    
    # Check Day 3 Fallback (Yesterday 12z)
    url = _build_url(3, yesterday, "12")
    res = await _url_is_image(url)
    print(f"Day 3 Fallback Result: {res}")

if __name__ == "__main__":
    asyncio.run(main())

async def test_with_ua():
    ua = "WxAlertSPCBot/5.18.0 (+https://github.com/full-bars/spc-bot)"
    async with aiohttp.ClientSession(headers={"User-Agent": ua}) as session:
        url = "https://schumacher.atmos.colostate.edu/weather/csu_mlp/archive/severe_gefso_2021_day3/2026050512/severe_ml_day3_all_gefso_050812.png"
        try:
            async with session.head(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                print(f"\nTesting with Bot UA: {ua}")
                print(f"Status: {resp.status}, CT: {resp.headers.get('Content-Type')}")
        except Exception as e:
            print(f"UA Test Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_with_ua())
