"""Tests for Rust VTEC parser with Python fallback verification."""

import pytest
from lib.vtec_parser import parse_vtec, parse_vtec_py


class TestParseVTEC:
    """Test VTEC string parsing."""

    @pytest.fixture
    def sample_vtec_string(self):
        """Sample VTEC string."""
        return (
            "URGENT - IMMEDIATE BROADCAST REQUESTED\n"
            "TORNADO WARNING\n"
            "/O.NEW.KOUN.TO.W.0042.260427T2018Z-260427T2100Z/\n"
            "THE TORNADO WARNING FOR PARTS OF GRADY COUNTY..."
        )

    def test_parses_valid_vtec(self, sample_vtec_string):
        """Should parse valid VTEC string."""
        result = parse_vtec(sample_vtec_string)
        assert result is not None
        assert result["action"] == "NEW"
        assert result["office"] == "KOUN"
        assert result["phenom"] == "TO"
        assert result["sig"] == "W"
        assert result["etn"] == "0042"
        assert result["start"] == "260427T2018Z"
        assert result["end"] == "260427T2100Z"
        assert result["vtec_id"] == "KOUN.TO.W.0042"

    def test_matches_python(self, sample_vtec_string):
        """Rust version should match Python."""
        rust_result = parse_vtec(sample_vtec_string)
        python_result = parse_vtec_py(sample_vtec_string)
        assert rust_result == python_result

    def test_returns_none_for_empty(self):
        """Should return None for empty string."""
        assert parse_vtec("") is None
        assert parse_vtec_py("") is None

    def test_returns_none_for_no_vtec(self):
        """Should return None when no VTEC is present."""
        text_no_vtec = "URGENT - IMMEDIATE BROADCAST REQUESTED\nTORNADO WARNING"
        assert parse_vtec(text_no_vtec) is None
        assert parse_vtec_py(text_no_vtec) is None

    def test_normalizes_3_char_office(self):
        """Should prepend K to 3-char office IDs."""
        text_3char = (
            "/O.NEW.OUN.TO.W.0042.260427T2018Z-260427T2100Z/\n"
        )
        result = parse_vtec(text_3char)
        assert result is not None
        assert result["office"] == "KOUN"
        python_result = parse_vtec_py(text_3char)
        assert python_result is not None
        assert python_result["office"] == "KOUN"

    def test_handles_various_actions(self):
        """Should parse all valid VTEC actions."""
        actions = ["NEW", "CON", "EXP", "CAN", "UPG", "EXA", "EXT", "ROU"]
        for action in actions:
            text = f"/O.{action}.KOUN.TO.W.0042.260427T2018Z-260427T2100Z/"
            result = parse_vtec(text)
            assert result is not None, f"Failed to parse action {action}"
            assert result["action"] == action

    def test_extracts_vtec_id(self):
        """Should construct correct VTEC ID for deduplication."""
        text = "/O.NEW.KOUN.SV.A.0123.260427T2018Z-260427T2100Z/"
        result = parse_vtec(text)
        assert result is not None
        assert result["vtec_id"] == "KOUN.SV.A.0123"

    def test_finds_first_vtec_only(self):
        """Should find first VTEC in multi-VTEC text."""
        text = (
            "/O.NEW.KOUN.TO.W.0042.260427T2018Z-260427T2100Z/\n"
            "/O.NEW.KMOB.SV.A.0001.260427T2030Z-260427T2200Z/"
        )
        result = parse_vtec(text)
        assert result is not None
        assert result["office"] == "KOUN"
        assert result["etn"] == "0042"
