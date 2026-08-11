"""Coverage round 10: iembot SEL/SWOMCD text parsing + failover identity helpers."""

import time
from unittest.mock import MagicMock, patch


from cogs import iembot
from cogs.failover import FailoverCog


# ── iembot: SEL watch text parsing ───────────────────────────────────────────


def test_parse_watch_text_full_product():
    raw = (
        "WWUS40 KWNS 112000\n"
        "URGENT - IMMEDIATE BROADCAST REQUESTED\n"
        "Severe Thunderstorm Watch Number 45\n"
        "Watch for portions of\n"
        "Central Oklahoma\n"
        "North Texas\n"
        "Effective this Monday evening from 800 PM until 400 AM CDT\n"
        "Primary threats include\n"
        "A few tornadoes possible\n"
        "Large hail\n"
        "SUMMARY...this is a test\n"
    )
    text = iembot._parse_watch_text(raw)
    assert text is not None
    assert "**Areas:** Central Oklahoma, North Texas" in text
    assert "**Time:**" in text
    assert "**Threats:**" in text
    assert "• A few tornadoes possible" in text
    assert "**Summary:**" in text


def test_parse_watch_text_no_matches():
    assert iembot._parse_watch_text("nothing relevant here") is None
    assert iembot._parse_watch_text("") is None


# ── iembot: SWOMCD MD text parsing ───────────────────────────────────────────


def test_parse_md_text_concerning():
    raw = (
        "ACUS11 KWNS 101200\nMESOSCALE DISCUSSION 1234\nCONCERNING SEVERE THUNDERSTORM DEVELOPMENT"
    )
    text = iembot._parse_md_text(raw)
    assert text == "CONCERNING SEVERE THUNDERSTORM DEVELOPMENT"


def test_parse_md_text_fallback_lines():
    raw = "line one\nline two\nline three\nline four"
    text = iembot._parse_md_text(raw)
    assert text == "line one line two line three"


def test_parse_md_text_empty():
    assert iembot._parse_md_text("") is None


# ── failover: node identity ──────────────────────────────────────────────────


def _failover_cog():
    bot = MagicMock()
    cog = FailoverCog(bot)
    return cog


def test_node_identity_primary_and_standby():
    cog = _failover_cog()
    with patch("socket.gethostname", return_value="box-a"):
        p = cog._node_identity(True)
        s = cog._node_identity(False)

    assert p == f"P:box-a:{cog._process_uuid}"
    assert s == f"S:box-a:{cog._process_uuid}"


def test_is_our_node_matches():
    cog = _failover_cog()
    with patch("socket.gethostname", return_value="box-a"):
        cog._identity = "P:box-a:uuid1"
        assert cog._is_our_node("P:box-a:uuid1") is True
        assert cog._is_our_node("box-a") is True
        assert cog._is_our_node("S:box-a:uuid2") is True  # hostname embedded
        assert cog._is_our_node("P:box-b:uuid1") is False
        assert cog._is_our_node("") is False


def test_in_startup_grace():
    cog = _failover_cog()
    assert cog._in_startup_grace() is False  # never loaded

    cog._cog_load_monotonic = time.monotonic()
    assert cog._in_startup_grace() is True

    cog._cog_load_monotonic = time.monotonic() - 500  # past the 120s grace
    assert cog._in_startup_grace() is False


def test_register_failure_counts_outside_grace():
    cog = _failover_cog()
    cog._cog_load_monotonic = time.monotonic() - 500
    cog._primary_failures = 0

    n = cog._register_failure("redis down")

    assert n == 1
    assert cog._primary_failures == 1


def test_register_failure_grace_does_not_count():
    cog = _failover_cog()
    cog._cog_load_monotonic = time.monotonic()
    cog._primary_failures = 0

    n = cog._register_failure("redis down")

    assert n == 0
    assert cog._primary_failures == 0
