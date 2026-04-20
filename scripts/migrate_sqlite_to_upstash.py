#!/usr/bin/env python3
"""
One-shot migration from local SQLite into Upstash Redis.

Reads the current bot state from `cache/bot_state.db` and writes every
row into Upstash under the key schema defined in utils.state_store.

Safe to run repeatedly:
  - Redis SADD / HSET are idempotent; re-running won't duplicate data.
  - --dry-run prints counts without sending any commands.
  - --force overwrites existing Upstash values (useful if the schema
    changes and you want to re-seed from SQLite).

Prerequisites:
  - `.env` in the repo root (or environment variables) containing
    UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN.
  - The current SQLite cache file is at its normal location.

Usage:
  python -m scripts.migrate_sqlite_to_upstash --dry-run
  python -m scripts.migrate_sqlite_to_upstash
  python -m scripts.migrate_sqlite_to_upstash --force
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any, List

# Allow running from the repo root without install.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("migrate")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be migrated without hitting Upstash.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing Upstash keys (default: additive merge).",
    )
    args = parser.parse_args()

    # Lazy-import after sys.path is set.
    from utils import db as sqlite_backend
    from utils.state_store import (
        _k_hash_url_lookup,
        _k_posted_mds,
        _k_posted_watches,
        _k_state,
        _k_posted_urls,
        _upstash_cmd,
    )

    if not os.getenv("UPSTASH_REDIS_REST_URL") or not os.getenv(
        "UPSTASH_REDIS_REST_TOKEN"
    ):
        logger.error(
            "UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN must be set."
        )
        return 2

    # ── Read everything from SQLite ──────────────────────────────────────
    logger.info("Reading SQLite…")
    hashes_auto = await sqlite_backend.get_all_hashes("auto")
    hashes_manual = await sqlite_backend.get_all_hashes("manual")
    posted_mds = await sqlite_backend.get_posted_mds()
    posted_watches = await sqlite_backend.get_posted_watches()

    iembot_seq = await sqlite_backend.get_state("iembot_last_seqnum")
    csu_posted = await sqlite_backend.get_state("csu_mlp_posted")

    day_urls = {}
    for day in ("day1", "day2", "day3"):
        urls = await sqlite_backend.get_posted_urls(day)
        if urls:
            day_urls[day] = urls

    counts = {
        "hashes_auto": len(hashes_auto),
        "hashes_manual": len(hashes_manual),
        "posted_mds": len(posted_mds),
        "posted_watches": len(posted_watches),
        "iembot_last_seqnum": 1 if iembot_seq else 0,
        "csu_mlp_posted": 1 if csu_posted else 0,
        "posted_urls_days": len(day_urls),
    }
    logger.info(f"SQLite snapshot: {counts}")

    if args.dry_run:
        logger.info("--dry-run: skipping Upstash writes.")
        return 0

    # ── Write everything to Upstash ──────────────────────────────────────
    commands_sent = 0

    def count() -> None:
        nonlocal commands_sent
        commands_sent += 1

    # Hashes (one HSET per cache_type, many field/value pairs).
    for cache_type, hashes in (("auto", hashes_auto), ("manual", hashes_manual)):
        if not hashes:
            continue
        if args.force:
            await _upstash_cmd("DEL", _k_hash_url_lookup(cache_type, ""))
            count()
        args_list: List[Any] = ["HSET", _k_hash_url_lookup(cache_type, "")]
        for url, h in hashes.items():
            args_list.append(url)
            args_list.append(h)
        await _upstash_cmd(*args_list)
        count()
        logger.info(f"  → {len(hashes)} {cache_type} hashes")

    # Posted MDs.
    if posted_mds:
        if args.force:
            await _upstash_cmd("DEL", _k_posted_mds())
            count()
        await _upstash_cmd("SADD", _k_posted_mds(), *sorted(posted_mds))
        count()
        logger.info(f"  → {len(posted_mds)} posted MDs")

    # Posted watches.
    if posted_watches:
        if args.force:
            await _upstash_cmd("DEL", _k_posted_watches())
            count()
        await _upstash_cmd(
            "SADD", _k_posted_watches(), *sorted(posted_watches)
        )
        count()
        logger.info(f"  → {len(posted_watches)} posted watches")

    # Scalars.
    if iembot_seq:
        await _upstash_cmd("SET", _k_state("iembot_last_seqnum"), iembot_seq)
        count()
        logger.info(f"  → iembot_last_seqnum = {iembot_seq}")

    if csu_posted:
        await _upstash_cmd("SET", _k_state("csu_mlp_posted"), csu_posted)
        count()
        logger.info("  → csu_mlp_posted")

    # Posted URLs per day.
    for day, urls in day_urls.items():
        await _upstash_cmd(
            "SET", _k_posted_urls(day), json.dumps(urls)
        )
        count()
        logger.info(f"  → posted_urls[{day}] ({len(urls)} urls)")

    logger.info(f"Migration complete. Upstash commands sent: {commands_sent}")

    # Close the SQLite connection so the script exits cleanly.
    await sqlite_backend.close_db()
    from utils.http import close_session
    await close_session()
    return 0


if __name__ == "__main__":
    try:
        rc = asyncio.run(main())
    except KeyboardInterrupt:
        rc = 130
    sys.exit(rc)
