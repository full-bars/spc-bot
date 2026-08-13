"""Coverage round 4: SPC outlook verification — KML parsing, math, and fetch paths.

Pure-logic + HTTP-mocked tests for cogs/verification.py (the 0%-covered
outlook-verification pipeline): SPC KML parsing, Albers geodesic area,
expected-LSR / verdict math, and the fetch + command paths.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from shapely.geometry import Point, Polygon

from cogs import verification

SAMPLE_KML = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
<Placemark>
<ExtendedData>
<Data name="LABEL"><value>SLGT</value></Data>
<Data name="fill"><value>ff00ff00</value></Data>
</ExtendedData>
<Polygon><outerBoundaryIs><LinearRing><coordinates>
-98,35,0 -97,35,0 -97,36,0 -98,36,0 -98,35,0
</coordinates></LinearRing></outerBoundaryIs></Polygon>
</Placemark>
<Placemark>
<ExtendedData>
<Data name="LABEL"><value>ENH</value></Data>
</ExtendedData>
<Polygon><outerBoundaryIs><LinearRing><coordinates>
-98.5,35.5,0 -97.5,35.5,0 -97.5,36.5,0 -98.5,36.5,0 -98.5,35.5,0
</coordinates></LinearRing></outerBoundaryIs></Polygon>
</Placemark>
</Document>
</kml>
"""


# ── KML parsing ──────────────────────────────────────────────────────────────


def test_parse_spc_kml_extracts_labels_and_areas():
    areas = verification._parse_spc_kml(SAMPLE_KML)

    assert len(areas) == 2
    labels = {a["label"] for a in areas}
    assert labels == {"SLGT", "ENH"}
    for a in areas:
        assert a["area_km2"] > 0
        assert a["polygon"].is_valid


def test_parse_spc_kml_invalid_returns_empty():
    assert verification._parse_spc_kml(b"not-kml") == []


def test_parse_spc_kml_skips_unlabeled_placemarks():
    kml = b"""<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark><ExtendedData><Data name="LABEL"><value></value></Data></ExtendedData>
<Polygon><outerBoundaryIs><LinearRing><coordinates>-98,35,0 -97,35,0 -97,36,0 -98,35,0</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
</Document></kml>"""
    assert verification._parse_spc_kml(kml) == []


def test_parse_spc_kml_skips_missing_coords_and_short_polys():
    kml = b"""<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark><ExtendedData><Data name="LABEL"><value>SLGT</value></Data></ExtendedData></Placemark>
<Placemark><ExtendedData><Data name="LABEL"><value>ENH</value></Data></ExtendedData>
<Polygon><outerBoundaryIs><LinearRing><coordinates>-98,35,0 -97,35,0</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
</Document></kml>"""
    assert verification._parse_spc_kml(kml) == []


def test_parse_spc_kml_skips_bad_coordinate_tokens():
    kml = b"""<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark><ExtendedData><Data name="LABEL"><value>SLGT</value></Data></ExtendedData>
<Polygon><outerBoundaryIs><LinearRing><coordinates>
-98,35,0 -97,35,0 bad -97,36,0 -98,36,0 -98,35,0
</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
</Document></kml>"""
    areas = verification._parse_spc_kml(kml)
    assert len(areas) == 1  # "bad" token skipped, polygon still valid


def test_geodesic_area_sq_km_returns_positive():
    # ~1.5 deg x 1.5 deg box over the southern plains.
    poly = Polygon([(-98, 34), (-96.5, 34), (-96.5, 35.5), (-98, 35.5), (-98, 34)])
    area = verification._geodesic_area_sq_km(poly)
    assert 10000 < area < 50000


# ── Fetch paths (HTTP-mocked) ────────────────────────────────────────────────


def _warnings_json():
    return json.dumps(
        {
            "features": [
                {
                    "properties": {
                        "event": "Tornado Warning",
                        "areaDesc": "CLEVELAND, OK",
                        "severity": "Severe",
                        "certainty": "Observed",
                        "headline": "TORNADO WARNING",
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[-98, 35], [-97, 35], [-97, 36], [-98, 35]]],
                    },
                },
                {"properties": {"event": "X"}, "geometry": None},
            ]
        }
    ).encode()


async def test_fetch_active_warnings_parses_polygons():
    with patch("cogs.verification.http_get_bytes", new_callable=AsyncMock) as mock_bytes:
        mock_bytes.return_value = (_warnings_json(), 200)
        warnings = await verification.fetch_active_warnings()

    assert len(warnings) == 1
    assert warnings[0]["event"] == "Tornado Warning"
    assert warnings[0]["polygon"].is_valid


async def test_fetch_active_warnings_non_200_returns_empty():
    with patch("cogs.verification.http_get_bytes", new_callable=AsyncMock) as mock_bytes:
        mock_bytes.return_value = (None, 500)
        assert await verification.fetch_active_warnings() == []


async def test_fetch_lsr_reports_parses_points():
    payload = json.dumps(
        {
            "features": [
                {
                    "properties": {"type": "T", "typetext": "Tornado", "lat": 35.2, "lon": -97.4},
                    "geometry": {},
                },
                {"properties": {"type": "G", "typetext": "Gust", "lat": 35.3}, "geometry": {}},
            ]
        }
    ).encode()
    with patch("cogs.verification.http_get_bytes", new_callable=AsyncMock) as mock_bytes:
        mock_bytes.return_value = (payload, 200)
        reports = await verification.fetch_lsr_reports(hours=12)

    assert len(reports) == 1  # missing lon skipped
    assert reports[0]["type"] == "T"
    assert reports[0]["point"].x == -97.4


async def test_fetch_spc_outlook_areas_tries_issuances_in_order():
    kml = b"""<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark><ExtendedData><Data name="LABEL"><value>SLGT</value></Data></ExtendedData>
<Polygon><outerBoundaryIs><LinearRing><coordinates>-98,35,0 -97,35,0 -97,36,0 -98,35,0</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
</Document></kml>"""
    requested = []

    async def fake_bytes(url, retries=1, timeout=10):
        requested.append(url)
        if url.endswith("1200_cat.kml"):
            return (kml, 200)
        return (None, 404)

    with patch("cogs.verification.http_get_bytes", side_effect=fake_bytes):
        areas = await verification.fetch_spc_outlook_areas(date_str="2026-08-10")

    assert len(areas) == 1
    assert areas[0]["label"] == "SLGT"
    # Newest issuance first (2000, 1630, 1200) — stops at the first 200.
    assert [u.rsplit("_", 2)[1] for u in requested] == ["2000", "1630", "1200"]


async def test_fetch_spc_outlook_areas_all_missing_returns_empty():
    with patch("cogs.verification.http_get_bytes", new_callable=AsyncMock) as mock_bytes:
        mock_bytes.return_value = (None, 404)
        assert await verification.fetch_spc_outlook_areas(date_str="2026-08-10") == []


# ── Verification math ────────────────────────────────────────────────────────


def _area(label, km2=100000, ring=None):
    ring = ring or [(-98, 35), (-97, 35), (-97, 36), (-98, 36), (-98, 35)]
    return {"label": label, "area_km2": km2, "polygon": Polygon(ring)}


async def test_compute_verification_full_pipeline():
    warning_poly = Polygon([(-98.2, 35.2), (-97.2, 35.2), (-97.2, 36.2), (-98.2, 35.2)])
    with patch("cogs.verification.fetch_active_warnings", new_callable=AsyncMock) as mw, patch(
        "cogs.verification.fetch_active_watches", new_callable=AsyncMock
    ) as mwat, patch("cogs.verification.fetch_lsr_reports", new_callable=AsyncMock) as ml, patch(
        "cogs.verification.fetch_spc_outlook_areas", new_callable=AsyncMock
    ) as ma, patch("utils.geo.points_in_polygon_lookup", return_value=[0, 0, None]) as mlook:
        mw.return_value = [{"event": "Tornado Warning", "polygon": warning_poly}]
        mwat.return_value = [{"event": "Tornado Watch", "polygon": warning_poly}]
        # Rust assignment: [0, 0, None] -> LSRs 0 (T) and 1 (G) land in area 0,
        # LSR 2 (H) is outside -> tor=1, wind=1, hail=0.
        ml.return_value = [
            {"type": "T", "point": Point(-97.4, 35.2)},
            {"type": "G", "point": Point(-97.3, 35.1)},
            {"type": "H", "point": Point(0, 0)},
        ]
        ma.return_value = [_area("SLGT", km2=100000)]

        result = await verification.compute_verification(hours_back=12)

    assert "error" not in result
    assert result["total_warnings"] == 1
    assert result["total_lsrs"] == 3
    r = result["results"][0]
    assert r["label"] == "SLGT"
    # SLGT tornado threshold 0.02: expected = max(1, int(100000*0.02/5077)) = 1
    assert r["tor_expected"] == 1
    assert r["wind_expected"] == 1
    assert r["hail_expected"] == 1
    assert r["tor_lsrs"] == 1
    assert r["wind_lsrs"] == 1
    assert r["hail_lsrs"] == 0
    assert r["tor_verdict"].startswith("✅")
    assert r["hail_verdict"].startswith("⚠️")
    assert r["tor_warnings"] == 1
    assert r["active_watches"] == 1
    mlook.assert_called_once()


async def test_compute_verification_no_areas_returns_error():
    with patch("cogs.verification.fetch_active_warnings", new_callable=AsyncMock), patch(
        "cogs.verification.fetch_active_watches", new_callable=AsyncMock
    ), patch("cogs.verification.fetch_lsr_reports", new_callable=AsyncMock), patch(
        "cogs.verification.fetch_spc_outlook_areas", new_callable=AsyncMock
    ) as ma:
        ma.return_value = []
        result = await verification.compute_verification()

    assert result == {"error": "No SPC outlook data available"}


async def test_compute_verification_tstm_thresholds_zero():
    with patch("cogs.verification.fetch_active_warnings", new_callable=AsyncMock), patch(
        "cogs.verification.fetch_active_watches", new_callable=AsyncMock
    ), patch("cogs.verification.fetch_lsr_reports", new_callable=AsyncMock) as ml, patch(
        "cogs.verification.fetch_spc_outlook_areas", new_callable=AsyncMock
    ) as ma:
        ml.return_value = []
        ma.return_value = [_area("TSTM", km2=100000)]
        result = await verification.compute_verification()

    r = result["results"][0]
    assert r["tor_expected"] == 0
    assert r["tor_verdict"] == ""
    assert r["wind_verdict"] == ""


async def test_compute_verification_mrgl_thresholds():
    # MRGL: tornado 0.02 / wind 0.05 / hail 0.05.
    with patch("cogs.verification.fetch_active_warnings", new_callable=AsyncMock), patch(
        "cogs.verification.fetch_active_watches", new_callable=AsyncMock
    ), patch("cogs.verification.fetch_lsr_reports", new_callable=AsyncMock) as ml, patch(
        "cogs.verification.fetch_spc_outlook_areas", new_callable=AsyncMock
    ) as ma:
        ml.return_value = []
        ma.return_value = [_area("MRGL", km2=500000)]
        result = await verification.compute_verification()

    r = result["results"][0]
    assert r["tor_expected"] == 1  # int(500000 * 0.02 / 5077) = 1
    assert r["wind_expected"] == 4  # int(500000 * 0.05 / 5077) = 4
    assert r["hail_expected"] == 4


async def test_compute_verification_nested_areas_reversal():
    # Rust sees areas REVERSED (nested ENH first); poly_idx 0 maps to the
    # original ENH index via reversal_map.
    enh = _area("ENH", km2=50000, ring=[(-97.8, 35.2), (-97.2, 35.2), (-97.2, 35.8), (-97.8, 35.2)])
    slgt = _area(
        "SLGT", km2=200000, ring=[(-98.5, 34.5), (-96.5, 34.5), (-96.5, 36.5), (-98.5, 34.5)]
    )
    with patch("cogs.verification.fetch_active_warnings", new_callable=AsyncMock), patch(
        "cogs.verification.fetch_active_watches", new_callable=AsyncMock
    ), patch("cogs.verification.fetch_lsr_reports", new_callable=AsyncMock) as ml, patch(
        "cogs.verification.fetch_spc_outlook_areas", new_callable=AsyncMock
    ) as ma, patch("utils.geo.points_in_polygon_lookup", return_value=[0]):
        ml.return_value = [{"type": "T", "point": Point(-97.4, 35.4)}]
        ma.return_value = [slgt, enh]
        result = await verification.compute_verification()

    assert result["results"][0]["tor_lsrs"] == 0  # SLGT
    assert result["results"][1]["tor_lsrs"] == 1  # ENH


async def test_compute_verification_verdict_pct_caps():
    with patch("cogs.verification.fetch_active_warnings", new_callable=AsyncMock), patch(
        "cogs.verification.fetch_active_watches", new_callable=AsyncMock
    ), patch("cogs.verification.fetch_lsr_reports", new_callable=AsyncMock) as ml, patch(
        "cogs.verification.fetch_spc_outlook_areas", new_callable=AsyncMock
    ) as ma, patch("utils.geo.points_in_polygon_lookup", return_value=[0] * 50):
        ml.return_value = [{"type": "T", "point": Point(-97, 35)}] * 50
        # tiny area -> expected = 1, 50 actual -> 5000% capped at 999
        ma.return_value = [_area("SLGT", km2=1000)]
        result = await verification.compute_verification()

    r = result["results"][0]
    assert r["tor_verdict"] == "✅ (999%)"


# ── Discord command ──────────────────────────────────────────────────────────


def _verification_cog():
    cog = verification.VerificationCog.__new__(verification.VerificationCog)
    cog.bot = MagicMock()
    return cog


async def test_verify_outlook_error_path():
    cog = _verification_cog()
    interaction = MagicMock()
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    with patch("cogs.verification.compute_verification", new_callable=AsyncMock) as mock_cv:
        mock_cv.return_value = {"error": "No SPC outlook data available"}

        await cog.verify_outlook.callback(cog, interaction, date="2026-08-10")

    interaction.followup.send.assert_awaited_once()
    assert interaction.followup.send.await_args.args[0] == "No SPC outlook data available"


async def test_verify_outlook_success_embed():
    cog = _verification_cog()
    interaction = MagicMock()
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    result = {
        "results": [
            {
                "label": "SLGT",
                "area_km2": 1500000,
                "tor_warnings": 1,
                "svr_warnings": 0,
                "ffw_warnings": 0,
                "tor_lsrs": 2,
                "wind_lsrs": 0,
                "hail_lsrs": 0,
                "active_watches": 1,
                "tor_expected": 1,
                "wind_expected": 0,
                "hail_expected": 0,
                "tor_verdict": "✅ (200%)",
                "wind_verdict": "",
                "hail_verdict": "",
                "tor_threshold": 0.02,
                "wind_threshold": 0.05,
                "hail_threshold": 0.05,
            }
        ],
        "total_warnings": 1,
        "total_watches": 1,
        "total_lsrs": 2,
        "label": "last 12h",
    }
    with patch("cogs.verification.compute_verification", new_callable=AsyncMock) as mock_cv:
        mock_cv.return_value = result

        await cog.verify_outlook.callback(cog, interaction, hours=12)

    interaction.followup.send.assert_awaited_once()
    embed = interaction.followup.send.await_args.kwargs["embed"]
    assert "SLGT" in embed.title or any("SLGT" in f.name for f in embed.fields)
    assert "1.5M km²" in "".join(f.name for f in embed.fields)


async def test_verify_outlook_exception_path():
    cog = _verification_cog()
    interaction = MagicMock()
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    with patch("cogs.verification.compute_verification", new_callable=AsyncMock) as mock_cv:
        mock_cv.side_effect = RuntimeError("boom")

        await cog.verify_outlook.callback(cog, interaction)

    interaction.followup.send.assert_awaited_once()
    assert "failed" in interaction.followup.send.await_args.args[0]
