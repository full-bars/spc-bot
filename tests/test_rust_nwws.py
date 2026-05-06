"""Tests for Rust NWWS product_id normalization with Python fallback."""

from cogs.nwws import normalize_product_id, normalize_product_id_py


class TestNormalizeProductId:
    """Test NWWS product_id normalization."""

    def test_compact_timestamp_passthrough(self):
        """Should pass through compact timestamps unchanged."""
        result = normalize_product_id("KOUN", "ACUS42", "SVDMX", "202605030650")
        assert result == "202605030650-KOUN-ACUS42-SVDMX"

    def test_iso8601_to_compact(self):
        """Should convert ISO8601 timestamp to compact format."""
        result = normalize_product_id("KOUN", "ACUS42", "SVDMX", "2026-05-03T06:50:00Z")
        assert result == "202605030650-KOUN-ACUS42-SVDMX"

    def test_truncates_to_12_chars(self):
        """Should truncate timestamps longer than 12 chars."""
        # Compact format with seconds
        result = normalize_product_id("KOUN", "ACUS42", "SVDMX", "20260503065030")
        assert result == "202605030650-KOUN-ACUS42-SVDMX"

    def test_matches_python(self):
        """Rust version should match Python."""
        test_cases = [
            ("KOUN", "ACUS42", "SVDMX", "202605030650"),
            ("KMOB", "ACUS42", "WFTMPA", "2026-05-03T06:50:00Z"),
            ("KDMX", "ACUS52", "SVDMX", "20260503065030"),
        ]

        for office, ttaaii, afos_pil, issue_str in test_cases:
            rust_result = normalize_product_id(office, ttaaii, afos_pil, issue_str)
            python_result = normalize_product_id_py(office, ttaaii, afos_pil, issue_str)
            assert rust_result == python_result, \
                f"Mismatch for {office}/{ttaaii}/{afos_pil}: {rust_result} != {python_result}"

    def test_various_offices(self):
        """Should handle various office codes."""
        offices = ["KOUN", "KMOB", "KDMX", "KRSA"]
        for office in offices:
            result = normalize_product_id(office, "ACUS42", "SVDMX", "202605030650")
            assert office in result
            assert result.startswith("202605030650")

    def test_various_products(self):
        """Should handle various product types."""
        products = ["SVDMX", "WFTMPA", "TORDMX", "FFADMX"]
        for product in products:
            result = normalize_product_id("KOUN", "ACUS42", product, "202605030650")
            assert product in result
            assert result.endswith(f"-{product}")

    def test_dedup_consistency(self):
        """Same product should produce same ID regardless of input format."""
        iso8601 = "2026-05-03T06:50:00Z"
        compact = "202605030650"
        compact_long = "20260503065000"

        iso_result = normalize_product_id("KOUN", "ACUS42", "SVDMX", iso8601)
        compact_result = normalize_product_id("KOUN", "ACUS42", "SVDMX", compact)
        long_result = normalize_product_id("KOUN", "ACUS42", "SVDMX", compact_long)

        assert iso_result == compact_result == long_result

    def test_special_characters_preserved(self):
        """Should preserve office/ttaaii/product special characters."""
        # Some products have digits
        result = normalize_product_id("KOUN", "ACUS52", "SVDMX", "202605030650")
        assert "ACUS52" in result
        assert "SVDMX" in result
