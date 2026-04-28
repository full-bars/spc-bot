"""Unit tests for cogs.warnings — VTEC and LAT...LON polygon parsers,
plus the embed-building basics."""

from cogs.warnings import (
    parse_vtec,
    parse_warning_polygon,
)


# ── parse_vtec ───────────────────────────────────────────────────────────────


def test_parse_vtec_new_issuance():
    """The standard NEW VTEC for a Tornado Warning unpacks into action,
    office, phenomenon, significance, ETN, and a stable vtec_id."""
    text = "/O.NEW.KOUN.TO.W.0042.260427T2018Z-260427T2100Z/"
    parsed = parse_vtec(text)
    assert parsed is not None
    assert parsed["action"] == "NEW"
    assert parsed["office"] == "KOUN"
    assert parsed["phenom"] == "TO"
    assert parsed["sig"] == "W"
    assert parsed["etn"] == "0042"
    assert parsed["vtec_id"] == "KOUN.TO.W.0042"


def test_parse_vtec_handles_continue_and_cancel_actions():
    """SVS updates carry CON; cancellations carry CAN. Same dedup key
    as the original issuance because the ETN doesn't change."""
    new = parse_vtec("/O.NEW.KOUN.TO.W.0042.260427T2018Z-260427T2100Z/")
    con = parse_vtec("/O.CON.KOUN.TO.W.0042.260427T2030Z-260427T2100Z/")
    can = parse_vtec("/O.CAN.KOUN.TO.W.0042.260427T2050Z-260427T2100Z/")
    assert new["vtec_id"] == con["vtec_id"] == can["vtec_id"]
    assert con["action"] == "CON"
    assert can["action"] == "CAN"


def test_parse_vtec_finds_first_in_multiline_product():
    """A real product has the VTEC line embedded in plain-text headers;
    the parser must find it without a strict line-anchor."""
    body = (
        "WFUS54 KOUN 272018\n"
        "TORHGX\n"
        "/O.NEW.KOUN.TO.W.0042.260427T2018Z-260427T2100Z/\n"
        "/O.NEW.KOUN.SV.A.0162.000000T0000Z-260428T0500Z/\n"
        "BULLETIN - IMMEDIATE BROADCAST REQUESTED\n"
    )
    parsed = parse_vtec(body)
    # First match wins; the second VTEC line is a watch reference and
    # is irrelevant to which warning we're parsing.
    assert parsed is not None
    assert parsed["action"] == "NEW"
    assert parsed["phenom"] == "TO"


def test_parse_vtec_returns_none_for_garbage():
    assert parse_vtec("") is None
    assert parse_vtec("not a vtec string") is None
    assert parse_vtec(None) is None
    # Almost-but-not-quite — wrong field count:
    assert parse_vtec("/O.NEW.KOUN.TO.W.42.260427T2018Z-260427T2100Z/") is None


# ── parse_warning_polygon ───────────────────────────────────────────────────


def test_parse_polygon_basic():
    """Standard 4-vertex polygon, single line."""
    body = (
        "TIME...MOT...LOC ...\n"
        "LAT...LON 4119 8902 4135 8845 4187 8862 4173 8918\n"
        "$$\n"
    )
    poly = parse_warning_polygon(body)
    assert poly is not None
    assert len(poly) == 4
    # First vertex: 4119 / 100 = 41.19 N, -89.02 W
    assert poly[0] == (41.19, -89.02)
    assert poly[3] == (41.73, -89.18)


def test_parse_polygon_multiline():
    """Real products often wrap the LAT...LON block across multiple
    lines. The parser must keep collecting until a section break."""
    body = (
        "LAT...LON 4119 8902 4135 8845\n"
        "      4187 8862 4173 8918\n"
        "TIME...MOT...LOC 0030Z 200DEG ...\n"
    )
    poly = parse_warning_polygon(body)
    assert poly is not None
    assert len(poly) == 4
    assert poly[2] == (41.87, -88.62)


def test_parse_polygon_clips_implausible_coords():
    """If the LAT...LON block somehow contains nonsensical values
    (corrupt product, parser confusion), out-of-CONUS pairs are
    dropped silently rather than producing absurd geometry."""
    body = "LAT...LON 4119 8902 9999 9999 4135 8845\n$$\n"
    poly = parse_warning_polygon(body)
    assert poly == [(41.19, -89.02), (41.35, -88.45)]


def test_parse_polygon_returns_none_when_absent():
    assert parse_warning_polygon("BULLETIN ONLY, NO COORDS") is None
    assert parse_warning_polygon("") is None
    assert parse_warning_polygon(None) is None


def test_parse_polygon_odd_number_of_values_drops_orphan():
    """If someone hands us 5 numbers (lat lat lat lat lat), the last
    unmatched value is silently dropped — we never invent a coordinate."""
    body = "LAT...LON 4119 8902 4135 8845 4187\n$$\n"
    poly = parse_warning_polygon(body)
    assert poly == [(41.19, -89.02), (41.35, -88.45)]
