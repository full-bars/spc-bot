#!/usr/bin/env python3
"""
Diagnostic: Compare watch data availability across sources.
Usage: python diagnose_watch_sources.py <watch_number>
Example: python diagnose_watch_sources.py 308
"""

import asyncio
import sys
import json
from datetime import datetime, timezone
from utils.http import http_get_text, http_get_bytes


async def check_spc_main(watch_num):
    """Check main SPC watch page"""
    start = datetime.now(timezone.utc)
    url = f"https://www.spc.noaa.gov/products/watch/ww{watch_num}.html"
    text = await http_get_text(url)
    elapsed = (datetime.now(timezone.utc) - start).total_seconds()

    has_probs = "Probability" in text if text else False
    return {
        "source": "SPC Main Page",
        "url": url,
        "elapsed_sec": elapsed,
        "status": "OK" if text else "FAILED",
        "has_probabilities": has_probs,
    }


async def check_spc_prob(watch_num):
    """Check SPC probabilities page"""
    start = datetime.now(timezone.utc)
    url = f"https://www.spc.noaa.gov/products/watch/ww{watch_num}_prob.html"
    text = await http_get_text(url)
    elapsed = (datetime.now(timezone.utc) - start).total_seconds()

    has_probs = "Probability" in text if text else False
    return {
        "source": "SPC Prob Page",
        "url": url,
        "elapsed_sec": elapsed,
        "status": "OK" if text else "FAILED",
        "has_probabilities": has_probs,
    }


async def check_iem_api(watch_num):
    """Check IEM watches API"""
    start = datetime.now(timezone.utc)
    year = datetime.now(timezone.utc).year
    url = f"https://mesonet.agron.iastate.edu/json/watches.py?year={year}"
    content, status = await http_get_bytes(url, retries=1, timeout=10)
    elapsed = (datetime.now(timezone.utc) - start).total_seconds()

    has_probs = False
    if content and status == 200:
        try:
            data = json.loads(content)
            for event in data.get("events", []):
                if str(event.get("sel")) == watch_num:
                    has_probs = "prob_tornado" in event or event.get("prob_tornado_txt")
                    break
        except Exception:
            pass

    return {
        "source": "IEM API",
        "url": url,
        "elapsed_sec": elapsed,
        "status": "OK" if content else "FAILED",
        "has_probabilities": has_probs,
    }


async def check_nwws_text(watch_num):
    """Check if NWWS alert contains probabilities"""
    from cogs.iembot import get_cached_watch_text

    start = datetime.now(timezone.utc)
    text = await get_cached_watch_text(watch_num)
    elapsed = (datetime.now(timezone.utc) - start).total_seconds()

    has_probs = "Probability" in text if text else False
    return {
        "source": "NWWS (Cached)",
        "url": "NWS Alerts API (cached)",
        "elapsed_sec": elapsed,
        "status": "OK" if text else "NOT CACHED",
        "has_probabilities": has_probs,
    }


async def main():
    if len(sys.argv) < 2:
        print("Usage: python diagnose_watch_sources.py <watch_number>")
        sys.exit(1)

    watch_num = sys.argv[1].zfill(4)
    print(f"\n📊 Checking watch #{watch_num} across sources...\n")

    results = await asyncio.gather(
        check_spc_main(watch_num),
        check_spc_prob(watch_num),
        check_iem_api(watch_num),
        check_nwws_text(watch_num),
    )

    print(f"{'Source':<20} {'Response (s)':<15} {'Has Probs?':<12} {'Status':<10}")
    print("-" * 60)
    for r in sorted(results, key=lambda x: x["elapsed_sec"]):
        probs = "✓ Yes" if r["has_probabilities"] else "✗ No"
        print(f"{r['source']:<20} {r['elapsed_sec']:<15.2f} {probs:<12} {r['status']:<10}")

    print("\n🎯 Fastest source with probabilities:")
    with_probs = [r for r in results if r["has_probabilities"]]
    if with_probs:
        fastest = min(with_probs, key=lambda x: x["elapsed_sec"])
        print(f"   {fastest['source']} ({fastest['elapsed_sec']:.2f}s)")
    else:
        print("   None have probabilities yet (may not be released)")

    print()


if __name__ == "__main__":
    asyncio.run(main())
