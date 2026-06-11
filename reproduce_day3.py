import asyncio
import os
import sys

sys.path.append(os.getcwd())

from config import GEMINI_API_KEY

if not GEMINI_API_KEY:
    print("Warning: GEMINI_API_KEY not set. Test will fail if it needs to call AI.")


async def test_day3_summary():
    from cogs.ai_summaries import ensure_outlook_summary
    from utils.http import close_session, http_get_text
    import re

    print("Testing Day 3 outlook fetching and summary generation...")
    try:
        url = "https://www.spc.noaa.gov/products/outlook/day3otlk.txt"
        raw_text = await http_get_text(url)
        if raw_text:
            raw_text = re.sub(r"<[^>]*>", "", raw_text).strip()
            print(f"Fetched raw_text (length {len(raw_text)})")
        else:
            print("Failed to fetch raw_text from URL")

        summary = await ensure_outlook_summary("3")
        if summary:
            print("Successfully generated summary!")
        else:
            print("Failed to generate summary (returned None)")
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        await close_session()


if __name__ == "__main__":
    asyncio.run(test_day3_summary())
