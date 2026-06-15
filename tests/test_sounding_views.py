import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import discord
import os

from cogs.sounding_views import (
    _plot_path,
    post_sounding,
    send_sounding_embed,
    TimeSelectionView,
    StationSelectionView,
    CombinedSoundingView,
    IEMTimeSelectionView,
    SoundingPlotView,
)


@pytest.fixture
def mock_interaction():
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.channel = AsyncMock()
    interaction.user = MagicMock()
    interaction.user.id = 123
    return interaction


def test_plot_path():
    path = _plot_path("KTLX", "2026", "05", "10", "12", dark_mode=True)
    assert path.endswith("sounding_KTLX_20260510_12z_dark")


@pytest.mark.asyncio
async def test_post_sounding_cache_hit(mock_interaction):
    station = {"icao": "KTLX", "name": "Oklahoma City"}

    with patch("cogs.sounding_views.os.path.exists", return_value=True):
        with patch("cogs.sounding_views.send_sounding_embed", new=AsyncMock()) as mock_send:
            await post_sounding(mock_interaction, station, "2026", "05", "10", "12", False)
            mock_send.assert_called_once()
            args, kwargs = mock_send.call_args
            assert args[1] == "KTLX"
            # It should post immediately when image exists without fetching data


@pytest.mark.asyncio
async def test_time_selection_view(mock_interaction):
    station = {"icao": "KTLX", "name": "Oklahoma City"}
    view = TimeSelectionView(station, False, mock_interaction.user)

    # Just verify buttons got added
    assert len(view.children) > 0
    assert view.children[0].label is not None


@pytest.mark.asyncio
async def test_station_selection_view(mock_interaction):
    stations = [{"icao": "KTLX", "name": "OKC", "dist_km": 10}]
    view = StationSelectionView(stations, ("2026", "05", "10", "12"), False, mock_interaction.user)

    assert len(view.children) == 1
    assert "OKC" in view.children[0].label


@pytest.mark.asyncio
async def test_combined_sounding_view(mock_interaction):
    raob = [{"icao": "KTLX", "name": "OKC", "lat": 35.0, "lon": -97.0}]
    acars = [
        {
            "airport": "DFW",
            "profile_id": 1,
            "time_label": "12z",
            "year": "2026",
            "month": "05",
            "day": "10",
            "acars_hour": "12",
        }
    ]
    view = CombinedSoundingView(raob, acars, None, False, mock_interaction.user)

    # 1 raob + 1 acars + 1 mode toggle
    assert len(view.children) == 3

    # Test mode toggle
    with patch("cogs.sounding_views.set_user_dark_mode", new=AsyncMock()):
        toggle_btn = view.children[-1]
        await toggle_btn.callback(mock_interaction)
        assert view.dark_mode == True
        mock_interaction.response.edit_message.assert_called_once()


@pytest.mark.asyncio
async def test_sounding_plot_view():
    view = SoundingPlotView("cache_key_123")
    assert len(view.children) == 1
    assert view.children[0].custom_id == "ai_snd:cache_key_123"
