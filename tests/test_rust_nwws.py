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
            assert rust_result == python_result, (
                f"Mismatch for {office}/{ttaaii}/{afos_pil}: {rust_result} != {python_result}"
            )

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

        result = normalize_product_id("KOUN", "ACUS52", "SVDMX", "202605030650")
        assert "ACUS52" in result
        assert "SVDMX" in result


class TestRustNwwsIngestion:
    """Test NWWS ingestion using the Rust backend integration."""

    def test_drain_rust_nwws_mapping(self):
        from unittest.mock import MagicMock, AsyncMock, patch
        import asyncio
        from cogs.nwws import NWWSCog

        # Mock msg_dict returned by Rust nwws_try_recv
        msg_dict = {
            "office": "KOUN",
            "cccc": "KOUN",
            "ttaaii": "ACUS42",
            "awipsid": "SVDMX",
            "issue": "2026-05-03T06:50:00Z",
            "raw_text": "This is raw text product",
            "text": "This is raw text product",
            "delay_stamp": "2026-05-21T11:00:00Z",
        }
        mock_rust = MagicMock()
        mock_rust.nwws_try_recv.side_effect = [msg_dict, None]

        with patch.dict("sys.modules", {"spc_rust_core": mock_rust}):
            # Instantiate NWWSCog
            bot = MagicMock()
            cog = NWWSCog(bot)
            cog._use_rust = True
            cog._process_nwws_message = AsyncMock()

            # Run drain loop using asyncio
            asyncio.run(cog._drain_rust_nwws())

        # Assertions
        cog._process_nwws_message.assert_called_once()
        args, kwargs = cog._process_nwws_message.call_args
        payload, raw_text, received_at, is_archived = args

        # Check payload dictionary mappings
        assert payload["cccc"] == "KOUN"
        assert payload["office"] == "KOUN"
        assert payload["awipsid"] == "SVDMX"
        assert payload["ttaaii"] == "ACUS42"
        assert payload["issue"] == "2026-05-03T06:50:00Z"
        assert raw_text == "This is raw text product"
        assert is_archived is True  # since delay_stamp is older than 10 seconds
