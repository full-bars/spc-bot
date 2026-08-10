"""Tests for the /status 'Warning Labels (6h)' severity-line formatting.

Regression coverage for the mixed-dimension double-count (severity buckets
and tornado-confidence buckets are both tallied per warning, so displaying
them on one line made the sub-counts not sum to the total).
"""

from cogs.status import format_severity_line


def test_format_severity_line_full():
    line = format_severity_line(
        "🌪️",
        "tor",
        16,
        [("TOR", 15), ("TORP", 1), ("TORE", 0)],
    )
    assert line == "🌪️ **16** tor · 15 TOR · 1 TORP"


def test_format_severity_line_buckets_sum_to_total():
    """Severity-only buckets are mutually exclusive, so they sum to total."""
    total = 16
    buckets = [("TOR", 15), ("TORP", 1), ("TORE", 0)]
    assert sum(count for _, count in buckets) == total
    line = format_severity_line("🌪️", "tor", total, buckets)
    assert "**16**" in line
    assert "15 TOR" in line
    assert "1 TORP" in line


def test_format_severity_line_suppresses_zero_buckets():
    line = format_severity_line(
        "⛈️",
        "svr",
        61,
        [("SVR", 54), ("SVRC", 7), ("SVRD", 0)],
    )
    assert line == "⛈️ **61** svr · 54 SVR · 7 SVRC"
    assert "0" not in line


def test_format_severity_line_all_zero_buckets():
    line = format_severity_line("🌊", "ffw", 0, [("FFW", 0), ("FFWC", 0), ("FFE", 0)])
    assert line == "🌊 **0** ffw"


def test_format_severity_line_omits_trailing_separator_when_no_buckets():
    line = format_severity_line("🌊", "ffw", 13, [("FFW", 0), ("FFWC", 0), ("FFE", 0)])
    assert line == "🌊 **13** ffw"
