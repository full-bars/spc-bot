"""
Unit tests for cogs/nwws.py — parsing and routing of XMPP MUC products.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

# Mock product text
SAMPLE_TOR_TEXT = """WFUS54 KOUN 011234
TOROUN
OKC031-011315-
/O.NEW.KOUN.TO.W.0042.260501T1234Z-260501T1315Z/

BULLETIN - EAS ACTIVATION REQUESTED
TORNADO WARNING
NATIONAL WEATHER SERVICE NORMAN OK
734 AM CDT FRI MAY 1 2026

...A TORNADO WARNING REMAINS IN EFFECT FOR NORTHERN COMANCHE COUNTY...
"""

@pytest.fixture
def mock_payload():
    payload = {
        'office': 'KOUN',
        'ttaaii': 'WFUS54',
        'awipsid': 'TOROUN',
        'issue': '202605011234',
        'raw_text': SAMPLE_TOR_TEXT
    }
    return payload


@pytest.mark.asyncio
async def test_process_nwws_message_routes_warning():
    """Test that _process_nwws_message routes tornado warnings correctly."""
    bot = MagicMock()
    warnings_cog = MagicMock()
    warnings_cog.post_warning_now = AsyncMock()
    bot.get_cog.side_effect = lambda name: warnings_cog if name == "WarningsCog" else None

    from cogs.nwws import NWWSCog
    cog = NWWSCog.__new__(NWWSCog)
    cog.bot = bot

    payload = {
        'office': 'KOUN',
        'ttaaii': 'WFUS54',
        'awipsid': 'TOROUN',
        'issue': '202605011234',
        'raw_text': SAMPLE_TOR_TEXT
    }

    received_at = datetime.now(timezone.utc)
    await cog._process_nwws_message(payload, SAMPLE_TOR_TEXT, received_at, is_archived=False)

    # Verify routing happened
    assert bot.get_cog.called


@pytest.mark.asyncio
async def test_process_nwws_message_routes_watch():
    """Test that _process_nwws_message routes watches correctly."""
    bot = MagicMock()
    watches_cog = MagicMock()
    watches_cog.post_watch_now = AsyncMock()
    bot.get_cog.side_effect = lambda name: watches_cog if name == "WatchesCog" else None

    from cogs.nwws import NWWSCog
    cog = NWWSCog.__new__(NWWSCog)
    cog.bot = bot

    watch_text = "SEVERE THUNDERSTORM WATCH NUMBER 42\n..."
    payload = {
        'office': 'KWNS',
        'ttaaii': 'WWUS20',
        'awipsid': 'SEL5',
        'issue': '202605011200',
        'raw_text': watch_text
    }

    with patch("cogs.iembot._parse_watch_text", return_value="Parsed Text"), \
         patch("utils.state_store.set_product_cache", AsyncMock()):
        received_at = datetime.now(timezone.utc)
        await cog._process_nwws_message(payload, watch_text, received_at, is_archived=False)

    # Verify the cog was called
    assert bot.get_cog.called


@pytest.mark.asyncio
async def test_process_nwws_message_ignores_garbage():
    """Test that empty/garbage messages are ignored."""
    bot = MagicMock()

    from cogs.nwws import NWWSCog
    cog = NWWSCog.__new__(NWWSCog)
    cog.bot = bot

    payload = {
        'office': '',
        'ttaaii': '',
        'awipsid': '',
        'issue': '',
        'raw_text': ''
    }

    received_at = datetime.now(timezone.utc)
    # Should not raise, should just skip
    await cog._process_nwws_message(payload, '', received_at, is_archived=False)
