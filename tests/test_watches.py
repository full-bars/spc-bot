# tests/test_watches.py
"""
Unit tests for watches VTEC parsing and API failure handling.

Run with: python -m pytest tests/ -v
"""

import json
from unittest.mock import AsyncMock, patch

import pytest


class TestFetchActiveWatchesNWS:
    """Tests for fetch_active_watches_nws() parsing and failure handling."""

    def _make_response(self, features):
        """Build a minimal NWS API response payload."""
        return json.dumps({"features": features}).encode()

    def _make_feature(self, vtec, expires=None):
        """Build a minimal NWS alert feature with a VTEC string."""
        return {
            "properties": {
                "parameters": {"VTEC": [vtec]},
                "expires": expires,
                "ends": None,
            }
        }

    @pytest.mark.asyncio
    async def test_valid_tornado_watch(self):
        """Valid TO.A VTEC string is parsed as TORNADO watch."""
        from cogs.watches import fetch_active_watches_nws

        feature = self._make_feature(
            "/O.NEW.KWNS.TO.A.0042.260409T1800Z-260410T0000Z/",
            expires="2026-04-09T18:00:00+00:00",
        )
        payload = self._make_response([feature])

        with patch(
            "cogs.watches.http_get_bytes_conditional",
            new_callable=AsyncMock,
            return_value=(payload, 200, None),
        ):
            result = await fetch_active_watches_nws()

        assert result is not None
        assert "0042" in result
        assert result["0042"]["type"] == "TORNADO"
        assert result["0042"]["expires"] is not None

    @pytest.mark.asyncio
    async def test_valid_severe_watch(self):
        """Valid SV.A VTEC string is parsed as SVR watch."""
        from cogs.watches import fetch_active_watches_nws

        feature = self._make_feature(
            "/O.NEW.KWNS.SV.A.0101.260409T1800Z-260410T0000Z/",
        )
        payload = self._make_response([feature])

        with patch(
            "cogs.watches.http_get_bytes_conditional",
            new_callable=AsyncMock,
            return_value=(payload, 200, None),
        ):
            result = await fetch_active_watches_nws()

        assert result is not None
        assert "0101" in result
        assert result["0101"]["type"] == "SVR"

    @pytest.mark.asyncio
    async def test_watch_number_zero_padded(self):
        """Watch numbers are zero-padded to 4 digits."""
        from cogs.watches import fetch_active_watches_nws

        feature = self._make_feature(
            "/O.NEW.KWNS.SV.A.0007.260409T1800Z-260410T0000Z/",
        )
        payload = self._make_response([feature])

        with patch(
            "cogs.watches.http_get_bytes_conditional",
            new_callable=AsyncMock,
            return_value=(payload, 200, None),
        ):
            result = await fetch_active_watches_nws()

        assert "0007" in result

    @pytest.mark.asyncio
    async def test_duplicate_watch_number_deduplicated(self):
        """Duplicate watch numbers from multiple features are deduplicated."""
        from cogs.watches import fetch_active_watches_nws

        vtec = "/O.NEW.KWNS.TO.A.0042.260409T1800Z-260410T0000Z/"
        payload = self._make_response([
            self._make_feature(vtec),
            self._make_feature(vtec),
        ])

        with patch(
            "cogs.watches.http_get_bytes_conditional",
            new_callable=AsyncMock,
            return_value=(payload, 200, None),
        ):
            result = await fetch_active_watches_nws()

        assert result is not None
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_malformed_vtec_skipped(self):
        """Features with unparseable VTEC strings are skipped gracefully."""
        from cogs.watches import fetch_active_watches_nws

        feature = self._make_feature("not-a-vtec-string")
        payload = self._make_response([feature])

        with patch(
            "cogs.watches.http_get_bytes_conditional",
            new_callable=AsyncMock,
            return_value=(payload, 200, None),
        ):
            result = await fetch_active_watches_nws()

        assert result == {}

    @pytest.mark.asyncio
    async def test_missing_expires_still_returns_entry(self):
        """Watch with no expires field is still returned with expires=None."""
        from cogs.watches import fetch_active_watches_nws

        feature = self._make_feature(
            "/O.NEW.KWNS.SV.A.0055.260409T1800Z-260410T0000Z/",
            expires=None,
        )
        payload = self._make_response([feature])

        with patch(
            "cogs.watches.http_get_bytes_conditional",
            new_callable=AsyncMock,
            return_value=(payload, 200, None),
        ):
            result = await fetch_active_watches_nws()

        assert "0055" in result
        assert result["0055"]["expires"] is None

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self):
        """HTTP non-200 response returns None, not empty dict."""
        from cogs.watches import fetch_active_watches_nws

        with patch(
            "cogs.watches.http_get_bytes_conditional",
            new_callable=AsyncMock,
            return_value=(None, 500, None),
        ):
            result = await fetch_active_watches_nws()

        assert result is None

    @pytest.mark.asyncio
    async def test_json_parse_error_returns_none(self):
        """Unparseable JSON response returns None, not empty dict."""
        from cogs.watches import fetch_active_watches_nws

        with patch(
            "cogs.watches.http_get_bytes_conditional",
            new_callable=AsyncMock,
            return_value=(b"not json {{{", 200, None),
        ):
            result = await fetch_active_watches_nws()

        assert result is None

    @pytest.mark.asyncio
    async def test_empty_features_returns_empty_dict(self):
        """API success with zero features returns {} not None."""
        from cogs.watches import fetch_active_watches_nws

        payload = self._make_response([])

        with patch(
            "cogs.watches.http_get_bytes_conditional",
            new_callable=AsyncMock,
            return_value=(payload, 200, None),
        ):
            result = await fetch_active_watches_nws()

        assert result == {}
        assert result is not None
