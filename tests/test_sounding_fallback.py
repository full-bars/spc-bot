"""
Integration tests for sounding fallback logic and circuit-awareness.
"""

import pytest
from unittest.mock import AsyncMock, patch

from cogs.sounding_utils import fetch_sounding

@pytest.mark.asyncio
async def test_fetch_sounding_falls_back_to_gsl_on_iem_failure():
    station_id = "KILX"
    # Mock Wyoming (skipped for 18z)
    # Mock IEM to fail
    with patch("cogs.sounding_utils.fetch_iem_sounding", AsyncMock(return_value=None)), \
         patch("cogs.sounding_utils.fetch_gsl_sounding", AsyncMock()) as mock_gsl:
        
        mock_gsl.return_value = {"p": [1000], "z": [100], "T": [20], "Td": [15], "u": [5], "v": [5]}
        
        with patch("cogs.sounding_utils.validate_sounding_data", side_effect=[False, True]):
            res = await fetch_sounding(station_id, "2026", "05", "01", "18")
            
        assert res is not None
        assert mock_gsl.called

@pytest.mark.asyncio
async def test_fetch_sounding_skips_iem_when_circuit_open():
    station_id = "KILX"
    # Mock circuit breaker to be open for IEM
    with patch("utils.http.circuit_breaker.is_open", return_value=True), \
         patch("cogs.sounding_utils.fetch_iem_sounding", AsyncMock()) as mock_iem, \
         patch("cogs.sounding_utils.fetch_gsl_sounding", AsyncMock()) as mock_gsl:
        
        mock_gsl.return_value = {"p": [1000]}
        with patch("cogs.sounding_utils.validate_sounding_data", return_value=True):
            await fetch_sounding(station_id, "2026", "05", "01", "18")
        
        assert not mock_iem.called
        assert mock_gsl.called

@pytest.mark.asyncio
async def test_fetch_sounding_standard_hour_wyoming_first():
    station_id = "KILX"
    # 12z sounding
    with patch("sounderpy.get_obs_data") as mock_wyo, \
         patch("cogs.sounding_utils.validate_sounding_data", return_value=True):
        
        mock_wyo.return_value = {"p": [1000]}
        res = await fetch_sounding(station_id, "2026", "05", "01", "12")
        
        assert res is not None
        assert mock_wyo.called
