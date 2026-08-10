"""Coverage round 3: watch_fetch parsing and HTTP-mocked fetch paths (no network)."""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from cogs import watch_fetch

SPC_INDEX_HTML = """
<html><body>
<a href="/products/watch/ww1234.html">Watch 1234</a>
<a href="/products/watch/ww0567.html">Watch 567</a>
</body></html>
"""

NWS_ALERTS_JSON = json.dumps(
    {
        "features": [
            {
                "properties": {
                    "parameters": {"VTEC": ["/O.NEW.KOUN.SV.A.1234.260810T2000Z-260810T2100Z/"]},
                    "expires": "2026-08-10T21:00:00Z",
                    "affectedZones": ["OKC005"],
                }
            },
            {
                "properties": {
                    "parameters": {"VTEC": ["/O.NEW.KOUN.TO.A.0567.260810T2000Z-260810T2100Z/"]},
                    "ends": "2026-08-10T22:00:00Z",
                    "affectedZones": ["OKC006"],
                }
            },
            {
                # No VTEC — must be skipped.
                "properties": {"parameters": {}},
            },
        ]
    }
).encode()


def _reset_last_parsed():
    watch_fetch._nws_last_parsed = None


# ── SPC watch index ──────────────────────────────────────────────────────────


async def test_get_spc_active_watch_numbers_parses_etns():
    with patch("cogs.watch_fetch.http_get_text", new_callable=AsyncMock) as mock_text:
        mock_text.return_value = SPC_INDEX_HTML
        assert await watch_fetch.get_spc_active_watch_numbers() == {"1234", "0567"}


async def test_get_spc_active_watch_numbers_empty_returns_none():
    with patch("cogs.watch_fetch.http_get_text", new_callable=AsyncMock) as mock_text:
        mock_text.return_value = ""
        assert await watch_fetch.get_spc_active_watch_numbers() is None


# ── NWS active-watch fetch ───────────────────────────────────────────────────


async def test_fetch_active_watches_nws_parses_and_persists(isolated_db):
    _reset_last_parsed()
    with patch(
        "cogs.watch_fetch.http_get_bytes_conditional", new_callable=AsyncMock
    ) as mock_cond, patch("cogs.watch_fetch.http_get_text", new_callable=AsyncMock) as mock_text:
        mock_cond.return_value = (NWS_ALERTS_JSON, 200, {"etag": "e1", "last_modified": "lm1"})
        mock_text.return_value = SPC_INDEX_HTML

        result = await watch_fetch.fetch_active_watches_nws()

    assert result is not None
    assert set(result) == {"1234", "0567"}
    assert result["1234"]["type"] == "SVR"
    assert result["0567"]["type"] == "TORNADO"
    assert result["0567"]["expires"] == datetime.fromisoformat("2026-08-10T22:00:00Z").astimezone(
        timezone.utc
    )
    assert result["1234"]["affected_zones"] == ["OKC005"]
    # Persisted to state + validators.
    from config import NWS_ALERTS_URL
    from utils import db as sqlite_backend

    assert sqlite_backend.get_state("watch_last_parsed") is not None
    assert sqlite_backend.get_validators(NWS_ALERTS_URL) is not None


async def test_fetch_active_watches_nws_304_returns_cached(isolated_db):
    watch_fetch._nws_last_parsed = {"0001": {"type": "SVR", "expires": None}}
    with patch(
        "cogs.watch_fetch.http_get_bytes_conditional", new_callable=AsyncMock
    ) as mock_cond, patch("cogs.watch_fetch.http_get_text", new_callable=AsyncMock) as mock_text:
        mock_cond.return_value = (None, 304, None)
        result = await watch_fetch.fetch_active_watches_nws()

    assert result == {"0001": {"type": "SVR", "expires": None}}
    mock_text.assert_not_awaited()


async def test_fetch_active_watches_nws_resumes_from_state(isolated_db):
    _reset_last_parsed()
    from utils import db as sqlite_backend
    from utils import state_store

    state_store._cache.clear()
    await sqlite_backend.set_state(
        "watch_last_parsed",
        json.dumps({"0001": {"type": "SVR", "expires": "2026-08-10T21:00:00+00:00"}}),
    )
    with patch("cogs.watch_fetch.http_get_bytes_conditional", new_callable=AsyncMock) as mock_cond:
        mock_cond.return_value = (None, 304, None)
        result = await watch_fetch.fetch_active_watches_nws()

    assert result is not None
    assert result["0001"]["expires"] == datetime.fromisoformat("2026-08-10T21:00:00+00:00")


async def test_fetch_active_watches_nws_non_200_returns_none(isolated_db):
    _reset_last_parsed()
    with patch("cogs.watch_fetch.http_get_bytes_conditional", new_callable=AsyncMock) as mock_cond:
        mock_cond.return_value = (None, 500, None)
        assert await watch_fetch.fetch_active_watches_nws() is None


async def test_fetch_active_watches_nws_bad_json_returns_none(isolated_db):
    _reset_last_parsed()
    with patch("cogs.watch_fetch.http_get_bytes_conditional", new_callable=AsyncMock) as mock_cond:
        mock_cond.return_value = (b"not-json", 200, None)
        assert await watch_fetch.fetch_active_watches_nws() is None


async def test_fetch_active_watches_nws_skips_unknown_etn(isolated_db):
    _reset_last_parsed()
    # SPC index only lists 0567 — 1234 from the NWS API must be filtered out.
    with patch(
        "cogs.watch_fetch.http_get_bytes_conditional", new_callable=AsyncMock
    ) as mock_cond, patch("cogs.watch_fetch.http_get_text", new_callable=AsyncMock) as mock_text:
        mock_cond.return_value = (NWS_ALERTS_JSON, 200, None)
        mock_text.return_value = '<a href="ww0567.html">Watch 567</a>'
        result = await watch_fetch.fetch_active_watches_nws()

    assert set(result) == {"0567"}


# ── Latest watch numbers ─────────────────────────────────────────────────────


async def test_fetch_latest_watch_numbers_uses_nws():
    with patch("cogs.watch_fetch.fetch_active_watches_nws", new_callable=AsyncMock) as mock_nws:
        mock_nws.return_value = {"1234": {"type": "SVR"}, "0567": {"type": "TORNADO"}}
        result = await watch_fetch.fetch_latest_watch_numbers()

    assert result == [("1234", "SVR"), ("0567", "TORNADO")]


async def test_fetch_latest_watch_numbers_api_failure_returns_empty():
    with patch("cogs.watch_fetch.fetch_active_watches_nws", new_callable=AsyncMock) as mock_nws:
        mock_nws.return_value = None
        assert await watch_fetch.fetch_latest_watch_numbers() == []


async def test_fetch_latest_watch_numbers_spc_fallback():
    tornado_page = "<html>Tornado Watch Number 1234</html>"
    with patch(
        "cogs.watch_fetch.fetch_active_watches_nws", new_callable=AsyncMock
    ) as mock_nws, patch("cogs.watch_fetch.http_get_text", new_callable=AsyncMock) as mock_text:
        mock_nws.return_value = {}
        mock_text.side_effect = [
            SPC_INDEX_HTML,
            tornado_page,
            "<html>Severe Thunderstorm Watch 567</html>",
        ]
        result = await watch_fetch.fetch_latest_watch_numbers()

    assert result == [("1234", "TORNADO"), ("0567", "SVR")]


# ── IEM fallback details ─────────────────────────────────────────────────────


async def test_fetch_watch_details_iem_formats_pds():
    payload = json.dumps(
        {
            "events": [
                {
                    "num": 1234,
                    "states": "OK,TX",
                    "is_pds": True,
                    "tornadoes_1m_strong": 10,
                    "hail_1m_2inch": 5,
                    "max_hail_size": 2.5,
                    "max_wind_gust_knots": 70,
                }
            ]
        }
    ).encode()
    with patch("cogs.watch_fetch.http_get_bytes", new_callable=AsyncMock) as mock_bytes:
        mock_bytes.return_value = (payload, 200)
        text, img, probs, is_pds = await watch_fetch.fetch_watch_details_iem("1234")

    assert text is not None
    assert "**Areas:** OK, TX" in text
    assert "PDS" in text
    assert probs is not None
    assert "Sig. tornado (EF2+): **10%**" in probs
    assert "Max gusts: **81 mph (70 kt)**" in probs
    assert is_pds is True
    assert img is None


async def test_fetch_watch_details_iem_no_match():
    payload = json.dumps({"events": [{"num": 9999, "states": "OK"}]}).encode()
    with patch("cogs.watch_fetch.http_get_bytes", new_callable=AsyncMock) as mock_bytes:
        mock_bytes.return_value = (payload, 200)
        text, img, probs, is_pds = await watch_fetch.fetch_watch_details_iem("1234")

    assert text is None
    assert probs is None
    assert is_pds is False


async def test_fetch_watch_details_iem_error_returns_nones():
    async def _boom(*args, **kwargs):
        raise RuntimeError("network down")

    with patch("cogs.watch_fetch.http_get_bytes", side_effect=_boom):
        text, img, probs, is_pds = await watch_fetch.fetch_watch_details_iem("1234")

    assert text is None and img is None and probs is None and is_pds is False


# ── Full watch details (SPC page parse) ──────────────────────────────────────

WATCH_PAGE_HTML = b"""
<html><body>
<img src="ww1234_overview.gif">
<pre>
SEL1
URGENT - IMMEDIATE BROADCAST REQUESTED
Severe Thunderstorm Watch Number 1234
NWS Storm Prediction Center Norman OK
800 PM CDT Mon Aug 10 2026

The NWS Storm Prediction Center has issued a

Severe Thunderstorm Watch for portions of
Central Oklahoma
North Texas

Effective this Monday evening and Tuesday morning from 800 PM until 400 AM CDT.

Primary threats include...
A few tornadoes possible
Scattered large hail up to 2 inches in diameter

SUMMARY...
</pre>
</body></html>
"""

WATCH_PROB_HTML = b"""
<html><body><table>
<tr><td>Probability of Tornado (with 30 MI)</td><td>Low (5%)</td></tr>
<tr><td>Probability of Wind Gusts 65 mph (with 30 MI)</td><td>High (30%)</td></tr>
<tr><td>Probability of Hail 2 inches (with 30 MI)</td><td>Mod (15%)</td></tr>
<tr><td>Probability of Combined Severe Threat</td><td>Mod (10%)</td></tr>
</table></body></html>
"""


async def test_fetch_watch_details_parses_spc_page():
    with patch("utils.cache.fetch_with_validators", new_callable=AsyncMock) as mock_fv, patch(
        "cogs.watch_fetch.get_cached_watch_text", new_callable=AsyncMock
    ) as mock_cache:
        mock_fv.side_effect = [(WATCH_PAGE_HTML, 200), (WATCH_PROB_HTML, 200)]
        mock_cache.return_value = None

        image_url, text_summary, probs, is_pds = await watch_fetch.fetch_watch_details("1234")

    assert image_url == "https://www.spc.noaa.gov/products/watch/ww1234_overview.gif"
    assert text_summary is not None
    assert "**Areas:** Central Oklahoma, North Texas" in text_summary
    assert (
        "**Time:** Effective this Monday evening and Tuesday morning from 800 PM until 400 AM CDT."
        in text_summary
    )
    assert "• A few tornadoes possible" in text_summary
    assert probs is not None
    assert "**Tornado**" in probs
    assert "🔴 Tornado (with 30 MI): **Low (5%)**" in probs
    assert "**Wind**" in probs
    assert "**Hail**" in probs
    assert "**Combined**" in probs
    assert is_pds is False


async def test_fetch_watch_details_detects_pds():
    pds_page = WATCH_PAGE_HTML.replace(
        b"Severe Thunderstorm Watch", b"PARTICULARLY DANGEROUS SITUATION Tornado Watch"
    )
    with patch("utils.cache.fetch_with_validators", new_callable=AsyncMock) as mock_fv, patch(
        "cogs.watch_fetch.get_cached_watch_text", new_callable=AsyncMock
    ) as mock_cache:
        mock_fv.side_effect = [(pds_page, 200), (None, 404)]
        mock_cache.return_value = None

        _image, _text, _probs, is_pds = await watch_fetch.fetch_watch_details("1234")

    assert is_pds is True


async def test_fetch_watch_details_iem_fallback_when_spc_down():
    with patch("utils.cache.fetch_with_validators", new_callable=AsyncMock) as mock_fv, patch(
        "cogs.watch_fetch.get_cached_watch_text", new_callable=AsyncMock
    ) as mock_cache, patch(
        "cogs.watch_fetch.fetch_watch_details_iem", new_callable=AsyncMock
    ) as mock_iem:
        mock_fv.side_effect = [(None, 500), (None, 500)]
        mock_cache.return_value = None
        mock_iem.return_value = ("IEM summary text", None, None, False)

        image_url, text_summary, probs, is_pds = await watch_fetch.fetch_watch_details("1234")

    assert image_url is None
    assert text_summary == "IEM summary text"
    assert probs is None
    assert is_pds is False
