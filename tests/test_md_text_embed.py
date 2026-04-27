"""Tests for the MD body extractor and embed-builder helpers in
cogs.mesoscale — full text rendering and Discord 4096-char-per-embed
splitting."""

import discord

from cogs.mesoscale import (
    EMBED_BODY_LIMIT,
    build_md_embeds,
    chunk_md_text,
    extract_md_body,
)


SAMPLE_MD = """\
Mesoscale Discussion 0579
   NWS Storm Prediction Center Norman OK
   0618 PM CDT Mon Apr 27 2026

   Areas affected...southern IL...far southeast MO...eastern
   KY...northwest TN

   Concerning...Tornado Watch 162...

   Valid 272318Z - 280045Z

   The severe weather threat for Tornado Watch 162 continues.

   SUMMARY...Severe risk is increasing across portions of WW 162.
   Strong tornadoes, very large hail and damaging gusts remain
   possible.

   DISCUSSION...Deepening cumulus and a few towering cumulus are noted
   in GOES-16 DCP imagery recently."""


# ── extract_md_body ─────────────────────────────────────────────────────────


def test_extract_md_body_from_spc_html():
    """SPC HTML wraps the MD body in <pre>; we should pull just the
    text, strip embedded tags, and decode entities."""
    html = (
        "<html><head><title>x</title></head><body>"
        "<pre>Mesoscale Discussion 0579\n"
        "   <b>Concerning</b>...Tornado Watch 162...\n"
        "   AT&amp;T weather feed offline.\n"
        "</pre>"
        "</body></html>"
    )
    body = extract_md_body(html)
    assert body is not None
    assert "Mesoscale Discussion 0579" in body
    assert "<b>" not in body, "HTML tags must be stripped"
    assert "AT&T" in body, "entities must be decoded"
    assert "Concerning...Tornado Watch 162..." in body


def test_extract_md_body_passes_plain_text_through():
    """IEM gives us already-plain text — no HTML; we shouldn't try to
    parse a <pre> block that doesn't exist."""
    body = extract_md_body(SAMPLE_MD)
    assert body == SAMPLE_MD.strip()


def test_extract_md_body_returns_none_for_empty_input():
    assert extract_md_body(None) is None
    assert extract_md_body("") is None
    assert extract_md_body("   \n  ") is None


def test_extract_md_body_returns_none_when_html_has_no_pre():
    """Defensive: malformed SPC pages without a <pre> shouldn't yield
    a stray HTML blob as if it were an MD body."""
    assert extract_md_body("<html><body><p>oops</p></body></html>") is None


# ── chunk_md_text ───────────────────────────────────────────────────────────


def test_chunk_short_text_single_chunk():
    chunks = chunk_md_text(SAMPLE_MD)
    assert len(chunks) == 1
    assert chunks[0] == SAMPLE_MD.strip()


def test_chunk_empty_text_returns_empty_list():
    assert chunk_md_text("") == []
    assert chunk_md_text(None) == []


def test_chunk_splits_oversized_text_on_paragraph_boundaries():
    """A long MD must split at blank-line paragraph breaks, not mid-line.
    SPC formats areas/threats as fixed-width columns and a mid-line
    break would render unreadably."""
    # Build a text with paragraphs that fit individually but overflow together.
    para = ("Line one of paragraph.\n"
            "Line two extends the paragraph with more words.\n"
            "Line three closes it out.")
    paragraphs = [f"Paragraph {i}: {para}" for i in range(60)]
    text = "\n\n".join(paragraphs)
    assert len(text) > EMBED_BODY_LIMIT

    chunks = chunk_md_text(text, max_chars=EMBED_BODY_LIMIT)
    assert len(chunks) >= 2
    for c in chunks:
        assert len(c) <= EMBED_BODY_LIMIT, f"chunk too big: {len(c)}"
    # Concatenating should preserve every paragraph (modulo the
    # blank-line separator we re-insert when joining).
    rejoined = "\n\n".join(chunks)
    for i in range(60):
        assert f"Paragraph {i}:" in rejoined


def test_chunk_handles_single_oversized_paragraph():
    """When one paragraph alone exceeds the limit we fall through to
    per-line splitting."""
    big_para = "\n".join(f"Line {i:04d} with some narrative content." for i in range(200))
    assert len(big_para) > EMBED_BODY_LIMIT

    chunks = chunk_md_text(big_para, max_chars=EMBED_BODY_LIMIT)
    assert len(chunks) >= 2
    for c in chunks:
        assert len(c) <= EMBED_BODY_LIMIT


def test_chunk_hard_truncates_a_single_giant_line():
    """Sanity: even a pathological 5000-char single line shouldn't
    crash the splitter — we hard-truncate as a last resort."""
    line = "x" * (EMBED_BODY_LIMIT + 1000)
    chunks = chunk_md_text(line, max_chars=EMBED_BODY_LIMIT)
    assert len(chunks) >= 1
    for c in chunks:
        assert len(c) <= EMBED_BODY_LIMIT


# ── build_md_embeds ─────────────────────────────────────────────────────────


def test_build_embed_single_chunk_with_image():
    embeds = build_md_embeds("0579", SAMPLE_MD, image_filename="mcd_0579.png")
    assert len(embeds) == 1
    e = embeds[0]
    assert isinstance(e, discord.Embed)
    assert "0579" in e.title
    assert e.url == "https://www.spc.noaa.gov/products/md/mcd0579.html"
    assert e.description.startswith("```")
    assert e.description.endswith("```")
    assert "Concerning...Tornado Watch 162..." in e.description
    assert e.image.url == "attachment://mcd_0579.png"


def test_build_embed_multi_chunk_marks_pagination_and_image_only_first():
    """A long MD splits across multiple embeds; titles get an N/M suffix
    and only the first embed carries the image so Discord doesn't try
    to attach a graphic to every fragment."""
    big_text = "\n\n".join(
        f"Paragraph {i}: " + ("filler " * 50)
        for i in range(60)
    )
    embeds = build_md_embeds("0599", big_text, image_filename="mcd_0599.png")
    assert len(embeds) >= 2
    # Title pagination
    for i, e in enumerate(embeds):
        assert f"({i + 1}/{len(embeds)})" in e.title
        assert e.url == "https://www.spc.noaa.gov/products/md/mcd0599.html"
    # Image only on first embed
    assert embeds[0].image.url == "attachment://mcd_0599.png"
    for e in embeds[1:]:
        assert e.image.url is None or e.image.url == "" or not e.image.url


def test_build_embed_no_text_still_returns_one_embed():
    """If we can't resolve any body text we still want a clickable
    title + image embed — better than dropping the post."""
    embeds = build_md_embeds("0579", None, image_filename="mcd_0579.png")
    assert len(embeds) == 1
    assert embeds[0].description is None or embeds[0].description == ""
    assert embeds[0].image.url == "attachment://mcd_0579.png"


def test_build_embed_no_image_filename_omits_image():
    """Pre-graphic backfill case: text-only embed, no image attribute."""
    embeds = build_md_embeds("0579", SAMPLE_MD, image_filename=None)
    assert len(embeds) == 1
    img_url = embeds[0].image.url
    assert not img_url, f"expected no image url, got {img_url!r}"


def test_build_embed_description_under_4096_with_codeblock_overhead():
    """Each rendered description (code-block fences included) must
    stay inside Discord's 4096-char embed-description hard limit."""
    big_text = "\n\n".join(
        f"Paragraph {i}: " + ("filler " * 50)
        for i in range(60)
    )
    embeds = build_md_embeds("0599", big_text, image_filename="mcd_0599.png")
    for e in embeds:
        assert len(e.description) <= 4096
