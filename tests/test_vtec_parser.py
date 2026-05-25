from lib.vtec_parser import parse_warning_polygon, get_polygon_centroid


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
