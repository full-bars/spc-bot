"""
Tests for the batched posted_* insert helpers added to `utils.db`.

These helpers back `mirror_to_sqlite()` on promotion — replacing N+1
single-row INSERTs with one transaction per kind. Each test covers:
  - empty input is a no-op (no rows, no exception)
  - duplicate keys don't error (INSERT OR IGNORE semantics)
  - the rows actually land and are readable
"""

from utils import db


# ── posted_mds_batch ───────────────────────────────────────────────────────

async def test_add_posted_mds_batch_empty_is_noop(isolated_db):
    await db.add_posted_mds_batch([])
    assert await db.get_posted_mds() == set()


async def test_add_posted_mds_batch_inserts_rows(isolated_db):
    await db.add_posted_mds_batch(["0001", "0002", "0003"])
    assert await db.get_posted_mds() == {"0001", "0002", "0003"}


async def test_add_posted_mds_batch_ignores_duplicates(isolated_db):
    await db.add_posted_md("0001")
    await db.add_posted_mds_batch(["0001", "0002", "0001"])
    assert await db.get_posted_mds() == {"0001", "0002"}


# ── posted_watches_batch ───────────────────────────────────────────────────

async def test_add_posted_watches_batch_empty_is_noop(isolated_db):
    await db.add_posted_watches_batch([])
    assert await db.get_posted_watches() == set()


async def test_add_posted_watches_batch_inserts_rows(isolated_db):
    await db.add_posted_watches_batch(["0100", "0101"])
    assert await db.get_posted_watches() == {"0100", "0101"}


async def test_add_posted_watches_batch_ignores_duplicates(isolated_db):
    await db.add_posted_watch("0100")
    await db.add_posted_watches_batch(["0100", "0101"])
    assert await db.get_posted_watches() == {"0100", "0101"}


# ── posted_reports_batch ───────────────────────────────────────────────────

async def test_add_posted_reports_batch_empty_is_noop(isolated_db):
    await db.add_posted_reports_batch([])
    assert await db.get_posted_reports() == set()


async def test_add_posted_reports_batch_inserts_rows(isolated_db):
    await db.add_posted_reports_batch(["KOUN:LSR:1", "KOUN:LSR:2"])
    assert await db.get_posted_reports() == {"KOUN:LSR:1", "KOUN:LSR:2"}


async def test_add_posted_reports_batch_ignores_duplicates(isolated_db):
    await db.add_posted_report("KOUN:LSR:1")
    await db.add_posted_reports_batch(["KOUN:LSR:1", "KOUN:LSR:3"])
    assert await db.get_posted_reports() == {"KOUN:LSR:1", "KOUN:LSR:3"}


# ── posted_product_ids_batch ───────────────────────────────────────────────

async def test_add_posted_product_ids_batch_empty_is_noop(isolated_db):
    await db.add_posted_product_ids_batch([])
    assert await db.get_posted_product_ids() == set()


async def test_add_posted_product_ids_batch_inserts_rows(isolated_db):
    await db.add_posted_product_ids_batch(["P:1", "P:2", "P:3"])
    assert await db.get_posted_product_ids() == {"P:1", "P:2", "P:3"}


async def test_add_posted_product_ids_batch_ignores_duplicates(isolated_db):
    await db.add_posted_product_id("P:1")
    await db.add_posted_product_ids_batch(["P:1", "P:2"])
    assert await db.get_posted_product_ids() == {"P:1", "P:2"}
