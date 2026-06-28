from lib.vtec_parser import (
    _parse_vtec_py,
    _parse_warning_polygon_py,
    get_polygon_centroid,
    parse_warning_polygon,
)


def test_parse_warning_polygon_basic():
    text = "LAT...LON 3401 9802 3410 9815 3420 9810"
    coords = parse_warning_polygon(text)
    assert coords == [(34.01, -98.02), (34.1, -98.15), (34.2, -98.1)]


def test_parse_warning_polygon_multiline():
    text = "LAT...LON 3401 9802 3410 9815\n3420 9810\n$$"
    coords = parse_warning_polygon(text)
    assert coords == [(34.01, -98.02), (34.1, -98.15), (34.2, -98.1)]


def test_parse_warning_polygon_sanity_clip():
    # Outside US box (lat 10 is too low)
    text = "LAT...LON 1000 9800 3410 9815"
    coords = parse_warning_polygon(text)
    assert coords == [(34.1, -98.15)]


def test_parse_warning_polygon_none():
    assert parse_warning_polygon("") is None
    assert parse_warning_polygon("NO POLYGON HERE") is None


def test_get_polygon_centroid():
    coords = [(10.0, 10.0), (20.0, 20.0)]
    assert get_polygon_centroid(coords) == (15.0, 15.0)
    assert get_polygon_centroid([]) is None


# ── Python fallback tests ──────────────────────────────────────────────────


def test_parse_vtec_py_basic():
    text = "/O.NEW.KOUN.TO.W.0042.260427T2018Z-260427T2100Z/"
    result = _parse_vtec_py(text)
    assert result is not None
    assert result["action"] == "NEW"
    assert result["office"] == "KOUN"
    assert result["phenom"] == "TO"
    assert result["sig"] == "W"
    assert result["etn"] == "0042"
    assert result["start"] == "260427T2018Z"
    assert result["end"] == "260427T2100Z"
    assert result["vtec_id"] == "KOUN.TO.W.0042"


def test_parse_vtec_py_no_match():
    assert _parse_vtec_py("") is None
    assert _parse_vtec_py("No VTEC here") is None


def test_parse_vtec_py_finds_first_in_multiline():
    text = """some header
/O.NEW.KOUN.TO.W.0042.260427T2018Z-260427T2100Z/
/O.CON.KOUN.SV.W.0099.260429T2018Z-260429T2115Z/
footer"""
    result = _parse_vtec_py(text)
    assert result is not None
    assert result["action"] == "NEW"
    assert result["vtec_id"] == "KOUN.TO.W.0042"


def test_parse_warning_polygon_py_basic():
    text = "LAT...LON 3401 9802 3410 9815 3420 9810"
    coords = _parse_warning_polygon_py(text)
    assert coords == [(34.01, -98.02), (34.1, -98.15), (34.2, -98.1)]


def test_parse_warning_polygon_py_none():
    assert _parse_warning_polygon_py("") is None
    assert _parse_warning_polygon_py("NO POLYGON HERE") is None
