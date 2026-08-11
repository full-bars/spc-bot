"""Coverage round 7: warnings cog — channel resolution, perms, phenom mapping.

Pure-logic + mocked-discord tests for cogs/warnings.py (58% covered): the
event-to-phenom mapping, channel permission checks, alert-response parsing,
channel-error notification, and warning-channel resolution.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cogs import warnings


# ── Event -> phenom mapping ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "event,expected",
    [
        ("Tornado Warning", "tor"),
        ("tornado warning", "tor"),
        ("Severe Thunderstorm Warning", "svr"),
        ("Severe Weather Statement", "svr"),
        ("Flash Flood Warning", "ffw"),
        ("Special Weather Statement", "sps"),
        ("Blizzard Warning", "default"),
        ("", "default"),
    ],
)
def test_event_to_phenom(event, expected):
    assert warnings.WarningsCog._event_to_phenom(event) == expected


# ── Channel permission checks ────────────────────────────────────────────────


def _channel_with_perms(**perm_values):
    me = MagicMock()
    perms = MagicMock()
    for name in ("send_messages", "embed_links", "attach_files"):
        setattr(perms, name, perm_values.get(name, True))
    channel = MagicMock()
    channel.guild.me = me
    channel.permissions_for.return_value = perms
    return channel


def test_check_channel_perms_all_present():
    cog = warnings.WarningsCog(MagicMock())
    assert cog._check_channel_perms(_channel_with_perms()) == []


def test_check_channel_perms_missing_some():
    cog = warnings.WarningsCog(MagicMock())
    channel = _channel_with_perms(send_messages=False, attach_files=False)
    missing = cog._check_channel_perms(channel)
    assert "send_messages" in missing
    assert "attach_files" in missing
    assert "embed_links" not in missing


def test_check_channel_perms_no_guild_or_me():
    cog = warnings.WarningsCog(MagicMock())
    channel = MagicMock()
    channel.guild = None
    assert cog._check_channel_perms(channel) == []

    channel2 = MagicMock()
    channel2.guild.me = None
    assert cog._check_channel_perms(channel2) == []


# ── Alert response parsing ───────────────────────────────────────────────────


def test_parse_alert_response_valid():
    cog = warnings.WarningsCog(MagicMock())
    payload = (
        '{"type": "FeatureCollection", "features": ['
        '{"id": "x", "properties": {'
        '"id": "x", "areaDesc": "NORMAN OK", "event": "Tornado Warning", '
        '"description": "TORNADO WARNING", "status": "Actual", '
        '"messageType": "Alert", "category": "Met", "severity": "Severe", '
        '"certainty": "Observed", "urgency": "Immediate", "senderName": "NWS"}}]}'
    ).encode()
    parsed = cog._parse_alert_response(payload)
    assert parsed is not None
    assert parsed.features[0].properties.event == "Tornado Warning"


def test_parse_alert_response_invalid_json():
    cog = warnings.WarningsCog(MagicMock())
    assert cog._parse_alert_response(b"not-json") is None


def test_parse_alert_response_wrong_shape():
    cog = warnings.WarningsCog(MagicMock())
    # A feature with a non-string id fails pydantic validation -> None.
    assert cog._parse_alert_response(b'{"features": [{"id": 5}]}') is None


# ── Channel-error notification ───────────────────────────────────────────────


async def test_notify_channel_error_already_warned():
    cog = warnings.WarningsCog(MagicMock())
    cog._perm_warned.add(42)
    channel = MagicMock()
    channel.id = 42

    with patch("main.send_bot_alert", new_callable=AsyncMock) as mock_alert:
        await cog._notify_channel_error(channel, ["send_messages"])

    mock_alert.assert_not_awaited()


async def test_notify_channel_error_sends_alert():
    cog = warnings.WarningsCog(MagicMock())
    channel = MagicMock()
    channel.id = 7
    channel.name = "warnings"

    with patch("main.send_bot_alert", new_callable=AsyncMock) as mock_alert:
        await cog._notify_channel_error(channel, ["send_messages", "embed_links"])

    mock_alert.assert_awaited_once()
    assert 7 in cog._perm_warned
    args = mock_alert.await_args.args
    assert "Missing Permissions" in args[0]
    assert "send_messages, embed_links" in args[1]


# ── Warning channel resolution ───────────────────────────────────────────────


def _cog_with_state():
    from utils.state import BotState

    bot = MagicMock()
    bot.state = BotState()
    cog = warnings.WarningsCog(bot)
    cog.bot.state = bot.state
    return cog


async def test_resolve_warning_channel_disabled_override():
    cog = _cog_with_state()
    with patch("cogs.warnings.get_state", new_callable=AsyncMock) as mock_state:
        mock_state.return_value = "disabled"
        result = await cog._resolve_warning_channel("Tornado Warning")

    assert result is None


async def test_resolve_warning_channel_uses_override():
    cog = _cog_with_state()
    channel = MagicMock()
    channel.guild.me = MagicMock()
    cog.bot.get_channel.return_value = channel
    with patch("cogs.warnings.get_state", new_callable=AsyncMock) as mock_state:
        mock_state.return_value = "555"
        result = await cog._resolve_warning_channel("Tornado Warning")

    assert result is channel
    cog.bot.get_channel.assert_called_once_with(555)


async def test_resolve_warning_channel_override_missing_perms():
    cog = _cog_with_state()
    channel = MagicMock()
    perms = MagicMock()
    perms.send_messages = False
    perms.embed_links = False
    perms.attach_files = False
    channel.guild.me = MagicMock()
    channel.permissions_for.return_value = perms
    cog.bot.get_channel.return_value = channel
    with patch("cogs.warnings.get_state", new_callable=AsyncMock) as mock_state, patch(
        "cogs.warnings.logger"
    ):
        mock_state.return_value = "555"
        result = await cog._resolve_warning_channel("Tornado Warning")

    assert result is None


async def test_resolve_warning_channel_static_fallback():
    cog = _cog_with_state()
    channel = MagicMock()
    cog.bot.get_channel.return_value = channel
    with patch("cogs.warnings.get_state", new_callable=AsyncMock) as mock_state:
        mock_state.return_value = None
        result = await cog._resolve_warning_channel("Blizzard Warning")  # default phenom

    assert result is channel


async def test_resolve_warning_channel_vtec_override():
    cog = _cog_with_state()
    channel = MagicMock()
    channel.guild.me = MagicMock()
    cog.bot.get_channel.return_value = channel
    with patch("cogs.warnings.get_state", new_callable=AsyncMock) as mock_state:
        mock_state.return_value = "666"
        # Event string would map to svr, but VTEC TO overrides to tor.
        result = await cog._resolve_warning_channel("Severe Thunderstorm Warning", vtec_phenom="TO")

    assert result is channel
    cog.bot.get_channel.assert_called_once_with(666)
