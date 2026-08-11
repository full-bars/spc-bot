"""Coverage round 5: mesoscale MD text extraction, formatting, and fetch paths.

Pure-logic tests for extract/clean/chunk/embed building plus HTTP-mocked and
race-deterministic fetch tests for the MD index and details.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch


from cogs import mesoscale

# ── Task-exception logger ────────────────────────────────────────────────────


@pytest.mark.real_create_task
async def test_log_task_exception_handles_failed_task():
    async def _boom():
        raise RuntimeError("background failure")

    task = asyncio.create_task(_boom())
    await asyncio.sleep(0)
    # Task has a stored exception; the logger must not raise.
    mesoscale._log_task_exception(task)
    await asyncio.sleep(0)


# ── MD body extraction ───────────────────────────────────────────────────────


def test_extract_md_body_none_or_empty():
    assert mesoscale.extract_md_body(None) is None
    assert mesoscale.extract_md_body("") is None


def test_extract_md_body_plain_text():
    raw = "  MESOSCALE DISCUSSION 1234\nsome body text\n "
    assert mesoscale.extract_md_body(raw) == "MESOSCALE DISCUSSION 1234\nsome body text"


def test_extract_md_body_html_pre():
    raw = "<html><pre>MESOSCALE DISCUSSION 1234\nAreas affected...\nTEXT &amp; MORE</pre></html>"
    body = mesoscale.extract_md_body(raw)
    assert body is not None
    assert "Areas affected" in body
    assert "TEXT & MORE" in body  # HTML entities unescaped


def test_extract_md_body_html_without_marker_returns_none():
    raw = "<html><pre>NO MARKER HERE</pre></html>"
    assert mesoscale.extract_md_body(raw) is None


def test_extract_md_body_strips_attn_footer():
    raw = "MESOSCALE DISCUSSION 1234\nbody\nATTN...WFO OUN\nmore footer"
    body = mesoscale.extract_md_body(raw)
    assert body is not None
    assert "ATTN" not in body
    assert "footer" not in body


def test_extract_md_body_strips_top_header():
    raw = "some preamble\n\nAreas affected...\nbody text"
    body = mesoscale.extract_md_body(raw)
    assert body is not None
    assert body.startswith("Areas affected")


# ── Discord text cleaning ────────────────────────────────────────────────────


def test_clean_md_text_collects_summary_paragraph():
    text = "SUMMARY...Threat continues.\nMore summary text.\n\nDISCUSSION...\nAnalysis here."
    cleaned = mesoscale.clean_md_text_for_discord(text)
    assert "**SUMMARY...** Threat continues. More summary text." in cleaned
    assert "**DISCUSSION...** Analysis here." in cleaned


def test_clean_md_text_areas_affected_merges_location_lines():
    text = "Areas affected...\n...Central Oklahoma\n...North Texas\n\nValid 2000Z."
    cleaned = mesoscale.clean_md_text_for_discord(text)
    assert "**Areas affected... ...Central Oklahoma ...North Texas**" in cleaned
    assert "**Valid 2000Z.**" in cleaned


def test_clean_md_text_removes_double_asterisks():
    text = "**bold-ish** regular"
    cleaned = mesoscale.clean_md_text_for_discord(text)
    assert "**" not in cleaned


def test_clean_md_text_passthrough_lines():
    text = "The severe threat continues.\n\nConcerning tornado watch issuance"
    cleaned = mesoscale.clean_md_text_for_discord(text)
    assert "The severe threat continues." in cleaned
    assert "**Concerning tornado watch issuance**" in cleaned


# ── Chunking ─────────────────────────────────────────────────────────────────


def test_chunk_md_text_empty_and_short():
    assert mesoscale.chunk_md_text("") == []
    text = "short"
    assert mesoscale.chunk_md_text(text) == [text]


def test_chunk_md_text_splits_on_paragraphs():
    text = ("a" * 3000) + "\n\n" + ("b" * 3000)
    chunks = mesoscale.chunk_md_text(text, max_chars=4000)
    assert len(chunks) == 2
    assert chunks[0].strip() == "a" * 3000
    assert chunks[1].strip() == "b" * 3000


def test_chunk_md_text_splits_overlong_paragraph_by_lines():
    text = "\n".join(["x" * 100] * 50)  # 5000 chars, no blank lines
    chunks = mesoscale.chunk_md_text(text, max_chars=1000)
    assert len(chunks) > 1
    assert all(len(c) <= 1000 for c in chunks)


def test_chunk_md_text_hard_truncates_overlong_line():
    text = "y" * 5000
    chunks = mesoscale.chunk_md_text(text, max_chars=100)
    assert chunks[0].endswith("...")
    assert len(chunks[0]) == 100


# ── Embed building ───────────────────────────────────────────────────────────


def test_build_md_embeds_single_with_image():
    embeds = mesoscale.build_md_embeds("1234", "short body", image_filename="mcd1234.png")
    assert len(embeds) == 1
    assert embeds[0].title == "🌩️ SPC Mesoscale Discussion #1234"
    assert embeds[0].description == "```\nshort body\n```"
    assert embeds[0].image.url == "attachment://mcd1234.png"


def test_build_md_embeds_multiple_image_only_first():
    text = ("a" * 3000) + "\n\n" + ("b" * 3000)
    embeds = mesoscale.build_md_embeds("1234", text, image_filename="mcd1234.png")
    assert len(embeds) == 2
    assert "(1/2)" in embeds[0].title
    assert "(2/2)" in embeds[1].title
    assert embeds[0].image.url == "attachment://mcd1234.png"
    assert embeds[1].image.url is None


# ── MD index fetch ───────────────────────────────────────────────────────────


def _reset_md_index():
    mesoscale._md_index_head = None
    mesoscale._md_index_unreachable = None


async def test_fetch_latest_md_numbers_head_unchanged_returns_none(isolated_db):
    _reset_md_index()
    mesoscale._md_index_head = {"etag": "e1", "last_modified": "lm"}
    with patch("cogs.mesoscale.http_head_meta", new_callable=AsyncMock) as mock_head, patch(
        "cogs.mesoscale.http_get_text", new_callable=AsyncMock
    ):
        mock_head.return_value = {"etag": "e1", "last_modified": "lm"}
        numbers, fallback = await mesoscale.fetch_latest_md_numbers()

    assert numbers is None
    assert fallback is False


async def test_fetch_latest_md_numbers_scrapes_spc(isolated_db):
    _reset_md_index()
    mesoscale._md_index_head = {"etag": "old"}
    html = '<a href="md1234.html">1</a><a href="/products/md/md1234.html">1</a><a href="md0567.html">2</a>'
    with patch("cogs.mesoscale.http_head_meta", new_callable=AsyncMock) as mock_head, patch(
        "cogs.mesoscale.http_get_text", new_callable=AsyncMock
    ) as mock_text:
        mock_head.return_value = {"etag": "new"}
        mock_text.return_value = html
        numbers, fallback = await mesoscale.fetch_latest_md_numbers()

    assert numbers == ["1234", "0567"]  # appearance order in the index HTML
    assert fallback is False
    assert mesoscale._md_index_head["etag"] == "new"


async def test_fetch_latest_md_numbers_iem_fallback(isolated_db):
    _reset_md_index()
    iem_text = "ACUS11 KWNS 101200\nMESOSCALE DISCUSSION 1234\nMESOSCALE DISCUSSION 567"
    with patch("cogs.mesoscale.http_head_meta", new_callable=AsyncMock) as mock_head, patch(
        "cogs.mesoscale.http_get_text", new_callable=AsyncMock
    ) as mock_text:
        mock_head.return_value = {"etag": "x"}
        mock_text.side_effect = [None, iem_text]
        numbers, fallback = await mesoscale.fetch_latest_md_numbers()

    assert numbers == ["1234", "0567"]
    assert fallback is True
    assert mesoscale._md_index_unreachable is True


async def test_fetch_latest_md_numbers_spc_reachable_again(isolated_db):
    _reset_md_index()
    mesoscale._md_index_unreachable = True
    with patch("cogs.mesoscale.http_head_meta", new_callable=AsyncMock) as mock_head, patch(
        "cogs.mesoscale.http_get_text", new_callable=AsyncMock
    ) as mock_text:
        mock_head.return_value = None
        mock_text.return_value = '<a href="md1234.html">1</a>'
        numbers, fallback = await mesoscale.fetch_latest_md_numbers()

    assert numbers == ["1234"]
    assert fallback is False
    assert mesoscale._md_index_unreachable is False


# ── MD details: IEM fallback ─────────────────────────────────────────────────


@pytest.mark.real_create_task
async def test_fetch_md_details_iem_returns_image_and_summary():
    iem_text = (
        "ACUS11 KWNS 101200\n"
        "MESOSCALE DISCUSSION 1234\n"
        "CONCERNING TORNADO WATCH ISSUANCE PROBABILITIES"
    )
    with patch("cogs.mesoscale.http_get_bytes", new_callable=AsyncMock) as mock_bytes, patch(
        "cogs.mesoscale.http_get_text", new_callable=AsyncMock
    ) as mock_text:
        mock_bytes.return_value = (b"x" * 3000, 200)
        mock_text.return_value = iem_text

        image_url, summary, raw = await mesoscale.fetch_md_details_iem("1234")

    assert image_url == "https://mesonet.agron.iastate.edu/pickup/mcd/mcd1234.png"
    assert summary is not None
    assert "CONCERNING" in summary
    assert "MESOSCALE DISCUSSION 1234" in raw


@pytest.mark.real_create_task
async def test_fetch_md_details_iem_skips_small_image():
    with patch("cogs.mesoscale.http_get_bytes", new_callable=AsyncMock) as mock_bytes, patch(
        "cogs.mesoscale.http_get_text", new_callable=AsyncMock
    ) as mock_text:
        mock_bytes.return_value = (b"x" * 100, 200)
        mock_text.return_value = "no matching product"
        image_url, summary, raw = await mesoscale.fetch_md_details_iem("1234")

    assert image_url is None
    assert summary is None
    assert raw is None


# ── MD details: race ─────────────────────────────────────────────────────────


def _md_html(num):
    return (
        f'<html><img src="mcd{num}.png"><pre>'
        f"MESOSCALE DISCUSSION {num}\nCONCERNING SEVERE THUNDERSTORM DEVELOPMENT\n"
        "body text</pre></html>"
    ).encode()


@pytest.mark.real_create_task
async def test_fetch_md_details_spc_wins():
    with patch("utils.cache.fetch_with_validators", new_callable=AsyncMock) as mock_fv, patch(
        "cogs.mesoscale.fetch_md_details_iem", new_callable=AsyncMock
    ) as mock_iem, patch(
        "cogs.mesoscale.get_cached_md_text", new_callable=AsyncMock
    ) as mock_cache, patch("os.path.exists", return_value=False):
        mock_fv.return_value = (_md_html("1234"), 200)
        mock_iem.return_value = (None, None, None)
        mock_cache.return_value = None

        image_url, summary, from_cache, raw = await mesoscale.fetch_md_details("1234")

    assert image_url == "https://www.spc.noaa.gov/products/md/mcd1234.png"
    assert summary == "CONCERNING SEVERE THUNDERSTORM DEVELOPMENT"
    assert from_cache is False
    assert raw is not None


async def test_fetch_md_details_summary_from_pre_block():
    # No CONCERNING line -> summary falls back to the first <pre> block lines.
    html = (
        b'<html><img src="mcd1234.png"><pre>'
        b"MESOSCALE DISCUSSION 1234\nline one\nline two\nline three</pre></html>"
    )
    with patch("utils.cache.fetch_with_validators", new_callable=AsyncMock) as mock_fv, patch(
        "cogs.mesoscale.fetch_md_details_iem", new_callable=AsyncMock
    ) as mock_iem, patch(
        "cogs.mesoscale.get_cached_md_text", new_callable=AsyncMock
    ) as mock_cache, patch("os.path.exists", return_value=False):
        mock_fv.return_value = (html, 200)
        mock_iem.return_value = (None, None, None)
        mock_cache.return_value = None

        image_url, summary, _from_cache, raw = await mesoscale.fetch_md_details("1234")

    assert summary == "MESOSCALE DISCUSSION 1234 line one line two"
    assert raw is not None


@pytest.mark.real_create_task
async def test_fetch_md_details_iem_fallback_when_spc_down():
    async def _slow_iem(*args, **kwargs):
        await asyncio.sleep(0.05)  # keep IEM pending so SPC's failure path runs
        return ("https://iem/mcd1234.png", "IEM SUMMARY", "IEM RAW")

    with patch("utils.cache.fetch_with_validators", new_callable=AsyncMock) as mock_fv, patch(
        "cogs.mesoscale.fetch_md_details_iem", side_effect=_slow_iem
    ), patch("cogs.mesoscale.get_cached_md_text", new_callable=AsyncMock) as mock_cache, patch(
        "os.path.exists", return_value=False
    ):
        mock_fv.return_value = (None, 500)
        mock_cache.return_value = None

        image_url, summary, from_cache, raw = await mesoscale.fetch_md_details("1234")

    assert image_url == "https://iem/mcd1234.png"
    assert summary == "IEM SUMMARY"
    assert from_cache is True
    assert raw == "IEM RAW"


@pytest.mark.real_create_task
async def test_fetch_md_details_cached_text_only():
    with patch("utils.cache.fetch_with_validators", new_callable=AsyncMock) as mock_fv, patch(
        "cogs.mesoscale.fetch_md_details_iem", new_callable=AsyncMock
    ) as mock_iem, patch(
        "cogs.mesoscale.get_cached_md_text", new_callable=AsyncMock
    ) as mock_cache, patch("os.path.exists", return_value=False):
        mock_fv.return_value = (None, 500)
        mock_iem.return_value = (None, None, None)
        mock_cache.return_value = "CACHED TEXT"

        image_url, summary, from_cache, raw = await mesoscale.fetch_md_details("1234")

    assert image_url is None
    assert summary is None
    assert from_cache is False
    assert raw == "CACHED TEXT"


@pytest.mark.real_create_task
async def test_fetch_md_details_nothing_available():
    with patch("utils.cache.fetch_with_validators", new_callable=AsyncMock) as mock_fv, patch(
        "cogs.mesoscale.fetch_md_details_iem", new_callable=AsyncMock
    ) as mock_iem, patch(
        "cogs.mesoscale.get_cached_md_text", new_callable=AsyncMock
    ) as mock_cache, patch("os.path.exists", return_value=False):
        mock_fv.return_value = (None, 500)
        mock_iem.return_value = (None, None, None)
        mock_cache.return_value = None

        image_url, summary, from_cache, raw = await mesoscale.fetch_md_details("1234")

    assert (image_url, summary, from_cache, raw) == (None, None, False, None)
