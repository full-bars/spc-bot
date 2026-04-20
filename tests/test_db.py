"""Roundtrip tests for `utils.db` against a real sqlite file.

`isolated_db` in conftest redirects `DB_PATH` at a tmp_path, so these
tests exercise the production code path — WAL, pragmas, schema
creation, lock serialization, conflict-update — without touching the
dev database.
"""

import asyncio


from utils import db


# ── Image hash ops ───────────────────────────────────────────────────────────

async def test_set_and_get_hash(isolated_db):
    await db.set_hash("https://example.com/a.png", "hash_a", "auto")
    assert await db.get_hash("https://example.com/a.png") == "hash_a"


async def test_get_hash_missing_returns_none(isolated_db):
    assert await db.get_hash("https://not-seen.example/x.png") is None


async def test_set_hash_conflict_update(isolated_db):
    """Second insert with same URL should update the hash in place."""
    await db.set_hash("https://example.com/a.png", "first", "auto")
    await db.set_hash("https://example.com/a.png", "second", "auto")
    assert await db.get_hash("https://example.com/a.png") == "second"


async def test_get_all_hashes_filters_by_cache_type(isolated_db):
    await db.set_hash("https://a/1", "h1", "auto")
    await db.set_hash("https://a/2", "h2", "auto")
    await db.set_hash("https://m/1", "h3", "manual")

    auto = await db.get_all_hashes("auto")
    manual = await db.get_all_hashes("manual")
    all_ = await db.get_all_hashes()

    assert auto == {"https://a/1": "h1", "https://a/2": "h2"}
    assert manual == {"https://m/1": "h3"}
    assert len(all_) == 3


async def test_set_hashes_batch(isolated_db):
    payload = {f"https://x/{i}": f"h{i}" for i in range(10)}
    await db.set_hashes_batch(payload, "auto")
    stored = await db.get_all_hashes("auto")
    assert stored == payload


async def test_set_hashes_batch_empty_is_noop(isolated_db):
    await db.set_hashes_batch({}, "auto")
    assert await db.get_all_hashes("auto") == {}


# ── Posted MDs / watches ────────────────────────────────────────────────────

async def test_add_and_get_posted_mds(isolated_db):
    await db.add_posted_md("0100")
    await db.add_posted_md("0101")
    assert await db.get_posted_mds() == {"0100", "0101"}


async def test_add_posted_md_is_idempotent(isolated_db):
    await db.add_posted_md("0100")
    await db.add_posted_md("0100")
    assert await db.get_posted_mds() == {"0100"}


async def test_prune_posted_mds_keeps_most_recent(isolated_db):
    for i in range(10):
        await db.add_posted_md(f"{i:04d}")
    await db.prune_posted_mds(max_size=3)
    remaining = await db.get_posted_mds()
    # Ordered by CAST(md_number AS INTEGER) DESC — newest 3
    assert remaining == {"0007", "0008", "0009"}


async def test_add_and_get_posted_watches(isolated_db):
    await db.add_posted_watch("0042")
    await db.add_posted_watch("0043")
    assert await db.get_posted_watches() == {"0042", "0043"}


async def test_prune_posted_watches_keeps_most_recent(isolated_db):
    for i in range(5):
        await db.add_posted_watch(f"{i:04d}")
    await db.prune_posted_watches(max_size=2)
    assert await db.get_posted_watches() == {"0003", "0004"}


# ── Key/value state ─────────────────────────────────────────────────────────

async def test_set_get_delete_state(isolated_db):
    assert await db.get_state("nonexistent") is None
    await db.set_state("key1", "value1")
    assert await db.get_state("key1") == "value1"
    await db.set_state("key1", "value2")
    assert await db.get_state("key1") == "value2"
    await db.delete_state("key1")
    assert await db.get_state("key1") is None


# ── Posted URLs ─────────────────────────────────────────────────────────────

async def test_posted_urls_roundtrip(isolated_db):
    urls = ["https://a/1.png", "https://a/2.png"]
    await db.set_posted_urls("day1", urls)
    assert await db.get_posted_urls("day1") == urls


async def test_posted_urls_overwrite(isolated_db):
    await db.set_posted_urls("day1", ["a"])
    await db.set_posted_urls("day1", ["b", "c"])
    assert await db.get_posted_urls("day1") == ["b", "c"]


async def test_posted_urls_missing_returns_empty(isolated_db):
    assert await db.get_posted_urls("day9999") == []


# ── Product text cache (TTL) ────────────────────────────────────────────────

async def test_product_cache_returns_fresh_entry(isolated_db):
    await db.set_product_cache("prod_1", "hello", ttl=600)
    assert await db.get_product_cache("prod_1") == "hello"


async def test_product_cache_respects_ttl(isolated_db):
    """An entry whose expires_at is already in the past must not be returned,
    even if the once-per-hour prune has not yet run."""
    await db.set_product_cache("prod_2", "stale", ttl=-1)  # already expired
    assert await db.get_product_cache("prod_2") is None


async def test_product_cache_prune_timer_does_not_block_read(isolated_db):
    """Repeated reads in quick succession must not each trigger a DELETE.

    We can't inspect prune frequency directly, but we can verify that
    many back-to-back reads complete fast and return correct data.
    """
    await db.set_product_cache("prod_3", "ok", ttl=600)
    for _ in range(50):
        assert await db.get_product_cache("prod_3") == "ok"


# ── Integrity check ─────────────────────────────────────────────────────────

async def test_check_integrity_on_fresh_db(isolated_db):
    assert await db.check_integrity() is True


# ── Concurrent writes must not lose data ────────────────────────────────────

async def test_concurrent_writes_serialized(isolated_db):
    """Fire 50 concurrent writes; all must land."""
    await asyncio.gather(*[db.add_posted_md(f"{i:04d}") for i in range(50)])
    assert len(await db.get_posted_mds()) == 50
