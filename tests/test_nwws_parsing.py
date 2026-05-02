"""
Unit tests for cogs/nwws.py — parsing and routing of XMPP MUC products.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from cogs.nwws import NWWSClient

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
    payload = MagicMock()
    # Mock the __getitem__ access for attributes
    data = {
        'cccc': 'KOUN',
        'ttaaii': 'WFUS54',
        'awipsid': 'TOROUN',
        'issue': '2026-05-01T12:34:00Z',
        'id': '1.1'
    }
    payload.__getitem__.side_effect = data.get
    payload.xml.text = SAMPLE_TOR_TEXT
    return payload

@pytest.fixture
def mock_msg(mock_payload):
    msg = MagicMock()
    msg['type'] = 'groupchat'
    msg['from'] = 'nwws@conference.nwws-oi.weather.gov/nwws-oi'
    msg['nwws'] = mock_payload
    return msg

@pytest.mark.asyncio
async def test_process_nwws_message_routes_warning(mock_payload):
    bot = MagicMock()
    warnings_cog = MagicMock()
    warnings_cog.post_warning_now = AsyncMock()
    bot.get_cog.side_effect = lambda name: warnings_cog if name == "WarningsCog" else None
    
    # Mock slixmpp client setup
    with patch('slixmpp.ClientXMPP.register_plugin'), \
         patch('slixmpp.xmlstream.register_stanza_plugin'):
        client = NWWSClient("test@jid", "pass", bot)
        await client._process_nwws_message(mock_payload, SAMPLE_TOR_TEXT)
    
    assert warnings_cog.post_warning_now.called
    args = warnings_cog.post_warning_now.call_args[0]
    assert "TOR" in args[0] # product_id
    assert "Tornado Warning" == args[2] # event type

@pytest.mark.asyncio
async def test_process_nwws_message_routes_watch(mock_payload):
    bot = MagicMock()
    watches_cog = MagicMock()
    watches_cog.post_watch_now = AsyncMock()
    bot.get_cog.side_effect = lambda name: watches_cog if name == "WatchesCog" else None
    
    # Update payload for watch
    data = {
        'cccc': 'KWNS',
        'ttaaii': 'WWUS20',
        'awipsid': 'SEL5'
    }
    mock_payload.__getitem__.side_effect = data.get
    watch_text = "SEVERE THUNDERSTORM WATCH NUMBER 42\n..."
    mock_payload.xml.text = watch_text
    
    with patch('slixmpp.ClientXMPP.register_plugin'), \
         patch('slixmpp.xmlstream.register_stanza_plugin'):
        client = NWWSClient("test@jid", "pass", bot)
        with patch("cogs.iembot._parse_watch_text", return_value="Parsed Text"), \
             patch("utils.state_store.set_product_cache", AsyncMock()):
            await client._process_nwws_message(mock_payload, watch_text)
    
    assert watches_cog.post_watch_now.called
    args = watches_cog.post_watch_now.call_args[0]
    assert args[0] == "0042" # watch_num
    assert args[1]["type"] == "SVR"

@pytest.mark.asyncio
async def test_process_nwws_message_ignores_garbage():
    bot = MagicMock()
    with patch('slixmpp.ClientXMPP.register_plugin'), \
         patch('slixmpp.xmlstream.register_stanza_plugin'):
        client = NWWSClient("test@jid", "pass", bot)
        
    payload = MagicMock()
    data = {'awipsid': ''}
    payload.__getitem__.side_effect = data.get
    payload.xml.text = ''
    
    msg = MagicMock()
    msg['nwws'] = payload
    msg['type'] = 'groupchat'
    
    client.message(msg)
    assert not bot.get_cog.called
