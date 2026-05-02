"""
Unit tests for utils/dat_api.py — ArcGIS geometry fetching.
"""

import pytest
from unittest.mock import AsyncMock, patch
from utils.dat_api import fetch_dat_track_geometry

@pytest.mark.asyncio
async def test_fetch_dat_track_geometry_success():
    guid = "test-guid-123"
    mock_response = {
        "features": [
            {
                "geometry": {
                    "paths": [
                        [[-90.0, 35.0], [-90.1, 35.1]]
                    ]
                }
            }
        ]
    }
    
    with patch("utils.dat_api.http_get_json", AsyncMock(return_value=mock_response)):
        res = await fetch_dat_track_geometry(guid)
        
    assert res is not None
    assert len(res) == 1
    # Check [lon, lat] -> (lat, lon) conversion
    assert res[0][0] == (35.0, -90.0)
    assert res[0][1] == (35.1, -90.1)

@pytest.mark.asyncio
async def test_fetch_dat_track_geometry_no_features():
    guid = "empty-guid"
    mock_response = {"features": []}
    
    with patch("utils.dat_api.http_get_json", AsyncMock(return_value=mock_response)):
        res = await fetch_dat_track_geometry(guid)
        
    assert res is None

@pytest.mark.asyncio
async def test_fetch_dat_track_geometry_error():
    guid = "fail-guid"
    
    with patch("utils.dat_api.http_get_json", AsyncMock(side_effect=Exception("API Down"))):
        res = await fetch_dat_track_geometry(guid)
        
    assert res is None
