"""Coverage round 9: high-risk sounding sweep logic (mocked fetch/plot paths)."""

from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd

from cogs import sounding


def _cog(bot=None):
    return sounding.SoundingCog(bot or MagicMock())


def _station_df():
    return pd.DataFrame(
        [
            {"ICAO": "KOUN", "WMO": 72553, "NAME": "Norman", "lat": 35.2, "lon": -97.4},
            {"ICAO": "KICT", "WMO": None, "NAME": "Wichita", "lat": 37.7, "lon": -97.4},
            # Inside the polygon but has no usable identifier — _clean must skip it.
            {"ICAO": None, "WMO": "----", "NAME": "InPolyBad", "lat": 36.0, "lon": -97.0},
            {"ICAO": None, "WMO": np.nan, "NAME": "Missing", "lat": 41.0, "lon": -96.0},
        ]
    )


def _bot_primary():
    from utils.state import BotState

    bot = MagicMock()
    bot.state = BotState()
    bot.state.is_primary = True
    bot.wait_until_ready = AsyncMock()
    bot.get_channel.return_value = AsyncMock()
    return bot


async def test_tick_high_risk_not_primary():
    from utils.state import BotState

    bot = MagicMock()
    bot.state = BotState()
    bot.state.is_primary = False
    bot.wait_until_ready = AsyncMock()
    cog = _cog(bot)

    with patch("cogs.sounding.get_high_risk_polygon", new_callable=AsyncMock) as mock_poly:
        await cog._tick_high_risk_soundings()

    mock_poly.assert_not_awaited()


async def test_tick_high_risk_no_polygon():
    bot = _bot_primary()
    cog = _cog(bot)
    with patch(
        "cogs.sounding.get_high_risk_polygon", new_callable=AsyncMock, return_value=(None, [])
    ):
        await cog._tick_high_risk_soundings()  # no raise


async def test_tick_high_risk_no_channel():
    bot = _bot_primary()
    bot.get_channel.return_value = None
    cog = _cog(bot)
    with patch(
        "cogs.sounding.get_high_risk_polygon",
        new_callable=AsyncMock,
        return_value=(object(), ["MDT"]),
    ):
        await cog._tick_high_risk_soundings()  # no raise


async def test_tick_high_risk_no_stations_in_area():
    bot = _bot_primary()
    cog = _cog(bot)
    with patch(
        "cogs.sounding.get_high_risk_polygon",
        new_callable=AsyncMock,
        return_value=(object(), ["MDT"]),
    ), patch(
        "cogs.sounding.get_raob_stations", new_callable=AsyncMock, return_value=_station_df()
    ), patch("cogs.sounding.is_inside_polygon", return_value=False):
        await cog._tick_high_risk_soundings()  # no raise


async def test_tick_high_risk_posts_mdt():
    bot = _bot_primary()
    cog = _cog(bot)
    station = _station_df()

    with patch(
        "cogs.sounding.get_high_risk_polygon",
        new_callable=AsyncMock,
        return_value=(object(), ["MDT"]),
    ), patch(
        "cogs.sounding.get_raob_stations", new_callable=AsyncMock, return_value=station
    ), patch("cogs.sounding.is_inside_polygon", side_effect=lambda lat, lon, poly: lat < 38), patch(
        "cogs.sounding.get_available_sounding_times_iem",
        new_callable=AsyncMock,
        return_value=[(2026, 8, 11, 12)],
    ), patch(
        "cogs.sounding.fetch_sounding", new_callable=AsyncMock, return_value={"dummy": True}
    ), patch("cogs.sounding.generate_plot", new_callable=AsyncMock, return_value=True), patch(
        "cogs.sounding.send_sounding_embed", new_callable=AsyncMock
    ) as mock_send, patch(
        "cogs.sounding.get_acars_profiles_in_polygon", new_callable=AsyncMock, return_value=[]
    ), patch("os.path.exists", return_value=True):
        await cog._tick_high_risk_soundings()

    mock_send.assert_awaited()
    assert "MDT-Risk Sounding" in mock_send.await_args.kwargs["type_label"]
    assert "Inside SPC Day 1 MDT risk area" in mock_send.await_args.kwargs["fallback_note"]
    # Only KOUN (35.2) and KICT (37.7) are inside the polygon; the '----' and
    # NaN rows are filtered by _clean.
    assert mock_send.await_count == 2


async def test_tick_high_risk_generate_plot_failure_skips_send():
    bot = _bot_primary()
    cog = _cog(bot)
    cog._restore_attempted = True
    station = _station_df()

    with patch(
        "cogs.sounding.get_high_risk_polygon",
        new_callable=AsyncMock,
        return_value=(object(), ["MDT"]),
    ), patch(
        "cogs.sounding.get_raob_stations", new_callable=AsyncMock, return_value=station
    ), patch("cogs.sounding.is_inside_polygon", side_effect=lambda lat, lon, poly: lat < 38), patch(
        "cogs.sounding.get_available_sounding_times_iem",
        new_callable=AsyncMock,
        return_value=[(2026, 8, 11, 12)],
    ), patch(
        "cogs.sounding.fetch_sounding", new_callable=AsyncMock, return_value={"dummy": True}
    ), patch("cogs.sounding.generate_plot", new_callable=AsyncMock, return_value=False), patch(
        "cogs.sounding.send_sounding_embed", new_callable=AsyncMock
    ) as mock_send, patch(
        "cogs.sounding.get_acars_profiles_in_polygon", new_callable=AsyncMock, return_value=[]
    ), patch("os.path.exists", return_value=True):
        await cog._tick_high_risk_soundings()

    mock_send.assert_not_awaited()


async def test_tick_high_risk_padded_pkey_does_not_dedup():
    # Production pkeys use UNPADDED ints; a padded key in the posted set does
    # not match and the station is posted again (documents current behavior).
    bot = _bot_primary()
    cog = _cog(bot)
    cog._restore_attempted = True
    bot.state.posted_soundings = {"raob:KOUN:2026-08-11_12z"}
    station = _station_df()

    with patch(
        "cogs.sounding.get_high_risk_polygon",
        new_callable=AsyncMock,
        return_value=(object(), ["MDT"]),
    ), patch(
        "cogs.sounding.get_raob_stations", new_callable=AsyncMock, return_value=station
    ), patch("cogs.sounding.is_inside_polygon", side_effect=lambda lat, lon, poly: lat < 38), patch(
        "cogs.sounding.get_available_sounding_times_iem",
        new_callable=AsyncMock,
        return_value=[(2026, 8, 11, 12)],
    ), patch(
        "cogs.sounding.fetch_sounding", new_callable=AsyncMock, return_value={"dummy": True}
    ), patch("cogs.sounding.generate_plot", new_callable=AsyncMock, return_value=True), patch(
        "cogs.sounding.send_sounding_embed", new_callable=AsyncMock
    ) as mock_send, patch(
        "cogs.sounding.get_acars_profiles_in_polygon", new_callable=AsyncMock, return_value=[]
    ), patch("os.path.exists", return_value=True):
        await cog._tick_high_risk_soundings()

    assert mock_send.await_count == 2  # both KOUN (padded key missed) and KICT


async def test_tick_high_risk_posts_high_prefix():
    bot = _bot_primary()
    cog = _cog(bot)
    station = _station_df()

    with patch(
        "cogs.sounding.get_high_risk_polygon",
        new_callable=AsyncMock,
        return_value=(object(), ["MDT", "HIGH"]),
    ), patch(
        "cogs.sounding.get_raob_stations", new_callable=AsyncMock, return_value=station
    ), patch("cogs.sounding.is_inside_polygon", side_effect=lambda lat, lon, poly: lat < 38), patch(
        "cogs.sounding.get_available_sounding_times_iem",
        new_callable=AsyncMock,
        return_value=[(2026, 8, 11, 12)],
    ), patch(
        "cogs.sounding.fetch_sounding", new_callable=AsyncMock, return_value={"dummy": True}
    ), patch("cogs.sounding.generate_plot", new_callable=AsyncMock, return_value=True), patch(
        "cogs.sounding.send_sounding_embed", new_callable=AsyncMock
    ) as mock_send, patch(
        "cogs.sounding.get_acars_profiles_in_polygon", new_callable=AsyncMock, return_value=[]
    ), patch("os.path.exists", return_value=True):
        await cog._tick_high_risk_soundings()

    assert "High-Risk Sounding" in mock_send.await_args.kwargs["type_label"]


async def test_tick_high_risk_claims_only_new_soundings():
    bot = _bot_primary()
    cog = _cog(bot)
    cog._restore_attempted = True  # skip cog_load DB hydration
    # KOUN already posted — only KICT is new. (pkey uses unpadded ints.)
    bot.state.posted_soundings = {"raob:KOUN:2026-8-11_12z"}
    station = _station_df()

    with patch(
        "cogs.sounding.get_high_risk_polygon",
        new_callable=AsyncMock,
        return_value=(object(), ["MDT"]),
    ), patch(
        "cogs.sounding.get_raob_stations", new_callable=AsyncMock, return_value=station
    ), patch("cogs.sounding.is_inside_polygon", side_effect=lambda lat, lon, poly: lat < 38), patch(
        "cogs.sounding.get_available_sounding_times_iem",
        new_callable=AsyncMock,
        return_value=[(2026, 8, 11, 12)],
    ), patch(
        "cogs.sounding.fetch_sounding", new_callable=AsyncMock, return_value={"dummy": True}
    ), patch("cogs.sounding.generate_plot", new_callable=AsyncMock, return_value=True), patch(
        "cogs.sounding.send_sounding_embed", new_callable=AsyncMock
    ) as mock_send, patch(
        "cogs.sounding.get_acars_profiles_in_polygon", new_callable=AsyncMock, return_value=[]
    ), patch("os.path.exists", return_value=True):
        await cog._tick_high_risk_soundings()

    assert mock_send.await_count == 1
