"""
Unit tests for cogs/nwws.py — parsing and routing of XMPP products.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from cogs.nwws import NWWSClient

# Mock body samples
SAMPLE_TOR = """WFUS54 KOUN 011234
TOROUN
OKC031-011315-
/O.NEW.KOUN.TO.W.0042.260501T1234Z-260501T1315Z/

BULLETIN - EAS ACTIVATION REQUESTED
TORNADO WARNING
NATIONAL WEATHER SERVICE NORMAN OK
734 AM CDT FRI MAY 1 2026

...A TORNADO WARNING REMAINS IN EFFECT FOR NORTHERN COMANCHE COUNTY...
"""

SAMPLE_SEL = """WWUS20 KWNS 011545
SEL5

SEVERE THUNDERSTORM WATCH NUMBER 42
NWS STORM PREDICTION CENTER NORMAN OK
1045 AM CDT FRI MAY 1 2026

THE NWS STORM PREDICTION CENTER HAS ISSUED A
...
"""

@pytest.mark.asyncio
async def test_process_nwws_message_routes_warning():
    bot = MagicMock()
    warnings_cog = MagicMock()
    warnings_cog.post_warning_now = AsyncMock()
    bot.get_cog.return_value = warnings_cog
    
    client = NWWSClient("test@jid", "pass", bot)
    await client._process_nwws_message(SAMPLE_TOR)
    
    assert warnings_cog.post_warning_now.called
    args = warnings_cog.post_warning_now.call_args[0]
    assert "TOR" in args[0] # product_id
    assert "Tornado Warning" == args[2] # event type

@pytest.mark.asyncio
async def test_process_nwws_message_routes_watch():
    bot = MagicMock()
    watches_cog = MagicMock()
    watches_cog.post_watch_now = AsyncMock()
    bot.get_cog.return_value = watches_cog
    
    client = NWWSClient("test@jid", "pass", bot)
    with patch("cogs.iembot._parse_watch_text", return_value="Parsed Text"), \
         patch("utils.state_store.set_product_cache", AsyncMock()):
        await client._process_nwws_message(SAMPLE_SEL)
    
    assert watches_cog.post_watch_now.called
    args = watches_cog.post_watch_now.call_args[0]
    assert args[0] == "0042" # watch_num
    assert args[1]["type"] == "SVR"

@pytest.mark.asyncio
async def test_process_nwws_message_routes_md():
    bot = MagicMock()
    mesoscale_cog = MagicMock()
    mesoscale_cog.post_md_now = AsyncMock()
    bot.get_cog.return_value = mesoscale_cog
    
    SAMPLE_MCD = """ACUS11 KWNS 011200
SWOMCD
SPC MCD 011200
Mesoscale Discussion 0590
Concerning tornado activity.
"""
    client = NWWSClient("test@jid", "pass", bot)
    with patch("utils.state_store.set_product_cache", AsyncMock()):
        await client._process_nwws_message(SAMPLE_MCD)
    
    assert mesoscale_cog.post_md_now.called
    args = mesoscale_cog.post_md_now.call_args[0]
    assert args[0] == "0590"

@pytest.mark.asyncio
async def test_process_nwws_message_ignores_garbage():
    bot = MagicMock()
    client = NWWSClient("test@jid", "pass", bot)
    # Should not raise or call anything
    await client._process_nwws_message("short junk")
    assert not bot.get_cog.called
