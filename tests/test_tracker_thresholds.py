"""Threshold auto-tracking and post-tropical stop for the tropical tracker."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from cogs import tropical_tracker as tracker


# ── intensity ranking ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("storm", "expected"),
    [
        ({"type": "Hurricane", "winds_mph": 125.0}, 3),
        ({"type": "Major Hurricane", "winds_mph": 100.0}, 3),
        ({"type": "Hurricane", "winds_mph": 90.0}, 2),
        ({"type": "Hurricane", "winds_mph": None}, 2),
        ({"type": "Tropical Storm", "winds_mph": 40.0}, 1),
        ({"type": "Subtropical Storm", "winds_mph": 50.0}, 1),
        ({"type": "Tropical Depression", "winds_mph": 30.0}, 0),
        ({"type": "", "winds_mph": 80.0}, 2),
        ({"type": "", "winds_mph": 45.0}, 1),
        ({"type": "", "winds_mph": 115.0}, 3),
        ({"type": "", "winds_mph": 25.0}, 0),
        ({"type": "Post-Tropical Cyclone", "winds_mph": 45.0}, None),
        ({"type": "Extratropical Cyclone", "winds_mph": 60.0}, None),
        ({}, None),
    ],
)
def test_storm_intensity_rank(storm, expected):
    assert tracker.storm_intensity_rank(storm) == expected


def test_is_post_tropical():
    assert tracker.is_post_tropical({"type": "Post-Tropical Cyclone"}) is True
    assert tracker.is_post_tropical({"type": "Extratropical Cyclone"}) is True
    assert tracker.is_post_tropical({"type": "Hurricane"}) is False
    assert tracker.is_post_tropical({"type": ""}) is False
    assert tracker.is_post_tropical({}) is False


# ── threshold state helpers ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_threshold_round_trip_defaults_to_geocolor():
    stored = {}

    async def _set(key, value):
        stored[key] = value

    async def _get(key):
        return stored.get(key)

    with patch.object(tracker, "set_state", side_effect=_set), patch.object(
        tracker, "get_state", side_effect=_get
    ):
        await tracker.set_channel_threshold(111, "H")
        cfg = await tracker.get_channel_threshold(111)

    assert cfg == {"threshold": "H", "sat_product": "GEOCOLOR"}
    assert json.loads(stored["tracker_thresholds:channel:111"]) == {
        "threshold": "H",
        "sat_product": "GEOCOLOR",
    }


@pytest.mark.asyncio
async def test_get_channel_threshold_missing_or_invalid():
    with patch.object(tracker, "get_state", AsyncMock(return_value=None)):
        assert await tracker.get_channel_threshold(111) is None
    with patch.object(tracker, "get_state", AsyncMock(return_value="{not json")):
        assert await tracker.get_channel_threshold(111) is None
    with patch.object(
        tracker, "get_state", AsyncMock(return_value=json.dumps({"threshold": "XX"}))
    ):
        assert await tracker.get_channel_threshold(111) is None


@pytest.mark.asyncio
async def test_clear_channel_threshold_reports_whether_one_existed():
    with patch.object(tracker, "get_state", AsyncMock(return_value=None)), patch.object(
        tracker, "delete_state", AsyncMock()
    ) as mock_delete:
        assert await tracker.clear_channel_threshold(111) is False
        mock_delete.assert_not_called()

    with patch.object(tracker, "get_state", AsyncMock(return_value="{}")), patch.object(
        tracker, "delete_state", AsyncMock()
    ) as mock_delete:
        assert await tracker.clear_channel_threshold(111) is True
        mock_delete.assert_called_once_with("tracker_thresholds:channel:111")


@pytest.mark.asyncio
async def test_get_all_channel_thresholds_maps_keys_to_configs():
    async def _list_keys(prefix):
        assert prefix == "tracker_thresholds:channel:"
        return ["tracker_thresholds:channel:100", "tracker_thresholds:channel:200", "junk"]

    async def _get(key):
        values = {
            "tracker_thresholds:channel:100": json.dumps(
                {"threshold": "MH", "sat_product": "AirMass"}
            ),
            "tracker_thresholds:channel:200": json.dumps({"threshold": "TS"}),
        }
        return values.get(key)

    with patch.object(tracker, "list_state_keys", side_effect=_list_keys), patch.object(
        tracker, "get_state", side_effect=_get
    ):
        thresholds = await tracker.get_all_channel_thresholds()

    assert thresholds == {
        100: {"threshold": "MH", "sat_product": "AirMass"},
        200: {"threshold": "TS", "sat_product": "GEOCOLOR"},
    }


# ── enrollment ────────────────────────────────────────────────────────────────


def _make_cog():
    bot = MagicMock()
    bot.state.is_primary = True
    bot.wait_until_ready = AsyncMock()
    cog = tracker.TropicalTrackerCog(bot)
    return cog, bot


HURRICANE = {"name": "Polo", "type": "Hurricane", "winds_mph": 125.0}
TS = {"name": "Rachel", "type": "Tropical Storm", "winds_mph": 40.0}
POST = {"name": "Odalys", "type": "Post-Tropical Cyclone", "winds_mph": 45.0}


@pytest.mark.asyncio
async def test_enroll_threshold_channel_enrolls_meets_and_skips_the_rest():
    cog, _ = _make_cog()
    cog._post_immediate_update = AsyncMock()
    channel = MagicMock(spec=discord.TextChannel)
    active = {"EP172026": HURRICANE, "EP182026": TS, "EP162026": POST, "AL012026": {}}

    with patch.object(tracker, "add_tracked_storm", AsyncMock(return_value=True)) as mock_add:
        enrolled = await cog._enroll_threshold_channel(
            channel, 555, {"threshold": "H", "sat_product": "GEOCOLOR"}, active, set()
        )

    assert enrolled == ["EP172026"]
    mock_add.assert_called_once_with(555, "EP172026", "GEOCOLOR")
    cog._post_immediate_update.assert_awaited_once_with(channel, "EP172026")


@pytest.mark.asyncio
async def test_enroll_threshold_channel_keeps_requested_product_and_skips_tracked():
    cog, _ = _make_cog()
    cog._post_immediate_update = AsyncMock()
    channel = MagicMock(spec=discord.TextChannel)

    with patch.object(tracker, "add_tracked_storm", AsyncMock(return_value=True)) as mock_add:
        enrolled = await cog._enroll_threshold_channel(
            channel,
            555,
            {"threshold": "TS", "sat_product": "AirMass"},
            {"EP172026": HURRICANE, "EP182026": TS},
            {"EP182026"},
        )

    assert enrolled == ["EP172026"]
    mock_add.assert_called_once_with(555, "EP172026", "AirMass")


@pytest.mark.asyncio
async def test_enroll_threshold_channel_ignores_unknown_threshold():
    cog, _ = _make_cog()
    cog._post_immediate_update = AsyncMock()

    with patch.object(tracker, "add_tracked_storm", AsyncMock()) as mock_add:
        enrolled = await cog._enroll_threshold_channel(
            MagicMock(), 555, {"threshold": "??"}, {"EP172026": HURRICANE}, set()
        )

    assert enrolled == []
    mock_add.assert_not_called()


# ── update loop ───────────────────────────────────────────────────────────────


def _loop_patches(
    tracked_channels,
    thresholds,
    active,
    records=None,
):
    """Patch the module-level state helpers the update loop reads."""
    records = records or {}

    async def _get_records(channel_id):
        return records.get(channel_id, [])

    return [
        patch.object(tracker, "get_all_tracked_channels", AsyncMock(return_value=tracked_channels)),
        patch.object(tracker, "get_all_channel_thresholds", AsyncMock(return_value=thresholds)),
        patch.object(tracker, "get_active_storms", AsyncMock(return_value=active)),
        patch.object(tracker, "get_tracked_storms", side_effect=_get_records),
    ]


@pytest.mark.asyncio
async def test_loop_enrolls_threshold_only_channel():
    """A channel with a threshold but no tracked storms still runs (no early return)."""
    cog, bot = _make_cog()
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 999
    bot.get_channel.return_value = channel
    cog._post_immediate_update = AsyncMock()
    cog._post_storm_update = AsyncMock()

    patches = _loop_patches(
        tracked_channels={},
        thresholds={999: {"threshold": "H", "sat_product": "GEOCOLOR"}},
        active={"EP172026": HURRICANE, "EP182026": TS},
    )
    with patches[0], patches[1], patches[2], patches[3], patch.object(
        tracker, "add_tracked_storm", AsyncMock(return_value=True)
    ) as mock_add, patch.object(tracker, "remove_tracked_storm", AsyncMock()):
        await cog.update_loop()

    mock_add.assert_awaited_once_with(999, "EP172026", "GEOCOLOR")
    # First update posts immediately for the newly enrolled storm.
    cog._post_immediate_update.assert_awaited_once_with(channel, "EP172026")


@pytest.mark.asyncio
async def test_loop_keeps_storm_that_weakened_below_threshold():
    """Enrollment is one-way: a tracked storm below the floor keeps updating."""
    cog, bot = _make_cog()
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 999
    bot.get_channel.return_value = channel
    cog._post_storm_update = AsyncMock()
    cog._post_immediate_update = AsyncMock()
    cog._post_posttropical_notice = AsyncMock()

    patches = _loop_patches(
        tracked_channels={999: ["EP182026"]},
        thresholds={999: {"threshold": "H", "sat_product": "GEOCOLOR"}},
        active={"EP182026": TS, "EP172026": HURRICANE},
        records={999: [{"storm_id": "EP182026", "last_etn": "3", "sat_product": "GEOCOLOR"}]},
    )
    with patches[0], patches[1], patches[2], patches[3], patch.object(
        tracker, "add_tracked_storm", AsyncMock(return_value=True)
    ) as mock_add, patch.object(tracker, "remove_tracked_storm", AsyncMock()) as mock_remove:
        await cog.update_loop()

    # The below-threshold storm is untouched and still gets its update;
    # the qualifying one is enrolled with an immediate first post.
    mock_remove.assert_not_called()
    assert [c.args for c in mock_add.call_args_list] == [(999, "EP172026", "GEOCOLOR")]
    cog._post_immediate_update.assert_awaited_once_with(channel, "EP172026")
    posted = [c.args[1] for c in cog._post_storm_update.call_args_list]
    assert "EP182026" in posted


@pytest.mark.asyncio
async def test_loop_stops_tracking_when_storm_goes_post_tropical():
    cog, bot = _make_cog()
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 999
    bot.get_channel.return_value = channel
    cog._post_storm_update = AsyncMock()
    cog._post_posttropical_notice = AsyncMock()

    patches = _loop_patches(
        tracked_channels={999: ["EP162026"]},
        thresholds={},
        active={"EP162026": POST},
        records={999: [{"storm_id": "EP162026", "last_etn": "31", "sat_product": "GEOCOLOR"}]},
    )
    with patches[0], patches[1], patches[2], patches[3], patch.object(
        tracker, "remove_tracked_storm", AsyncMock(return_value=True)
    ) as mock_remove:
        await cog.update_loop()

    cog._post_posttropical_notice.assert_awaited_once_with(channel, "EP162026", POST)
    cog._post_storm_update.assert_not_awaited()
    mock_remove.assert_awaited_once_with(999, "EP162026")


# ── /nhc storm threshold ──────────────────────────────────────────────────────


def _interaction():
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 999
    channel.name = "storms"
    interaction.channel = channel
    return interaction, channel


@pytest.mark.asyncio
async def test_storm_command_sets_threshold_and_enrolls_immediately():
    cog, _ = _make_cog()
    interaction, channel = _interaction()
    cog._enroll_threshold_channel = AsyncMock(return_value=["EP172026"])

    with patch.object(tracker, "set_channel_threshold", AsyncMock()) as mock_set, patch.object(
        tracker, "get_active_storms", AsyncMock(return_value={"EP172026": HURRICANE})
    ), patch.object(tracker, "get_tracked_storms", AsyncMock(return_value=[])):
        await cog.track_storm.callback(
            cog,
            interaction,
            None,
            None,
            discord.app_commands.Choice(name="Hurricane or stronger (H+)", value="H"),
        )

    mock_set.assert_awaited_once_with(999, "H", "GEOCOLOR")
    interaction.response.send_message.assert_awaited_once()
    interaction.followup.send.assert_awaited_once()
    assert "EP172026" in interaction.followup.send.call_args[0][0]
    cog._enroll_threshold_channel.assert_awaited_once()
    assert cog._enroll_threshold_channel.call_args[0][2] == {
        "threshold": "H",
        "sat_product": "GEOCOLOR",
    }


@pytest.mark.asyncio
async def test_storm_command_threshold_off_clears_rule():
    cog, _ = _make_cog()
    interaction, _ = _interaction()

    with patch.object(
        tracker, "clear_channel_threshold", AsyncMock(return_value=True)
    ) as mock_clear:
        await cog.track_storm.callback(
            cog,
            interaction,
            None,
            None,
            discord.app_commands.Choice(name="Off — manual tracking only", value="Off"),
        )

    mock_clear.assert_awaited_once_with(999)
    interaction.response.send_message.assert_awaited_once()
    assert "removed" in interaction.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_storm_command_rejects_storm_plus_threshold():
    cog, _ = _make_cog()
    interaction, _ = _interaction()

    with patch.object(tracker, "set_channel_threshold", AsyncMock()) as mock_set:
        await cog.track_storm.callback(
            cog,
            interaction,
            "EP172026",
            None,
            discord.app_commands.Choice(name="Hurricane or stronger (H+)", value="H"),
        )

    mock_set.assert_not_called()
    assert "not both" in interaction.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_tracked_command_shows_threshold_banner():
    cog, _ = _make_cog()
    interaction, channel = _interaction()

    with patch.object(
        tracker,
        "get_tracked_storms",
        AsyncMock(
            return_value=[{"storm_id": "EP172026", "last_etn": "29", "sat_product": "GEOCOLOR"}]
        ),
    ), patch.object(
        tracker,
        "get_channel_threshold",
        AsyncMock(return_value={"threshold": "H", "sat_product": "GEOCOLOR"}),
    ), patch.object(tracker, "get_active_storms", AsyncMock(return_value={"EP172026": HURRICANE})):
        await cog.list_tracked.callback(cog, interaction)

    embed = interaction.response.send_message.call_args[1]["embed"]
    assert "Auto-track" in embed.description
    assert "Hurricane or stronger" in embed.description
