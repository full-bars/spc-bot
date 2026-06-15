import pytest
from cogs.nwws import (
    normalize_product_id_py,
    parse_md_number_py,
    parse_watch_number_py,
)

def test_normalize_product_id_py():
    assert normalize_product_id_py("OUN", "WUUS54", "TOROUN", "2026-05-03T06:50:00Z") == "202605030650-OUN-WUUS54-TOROUN"
    assert normalize_product_id_py("OUN", "WUUS54", "TOROUN", "202605030650") == "202605030650-OUN-WUUS54-TOROUN"

def test_parse_md_number_py():
    assert parse_md_number_py("Mesoscale Discussion 0123") == "0123"
    assert parse_md_number_py("MESOSCALE DISCUSSION 45") == "0045"
    assert parse_md_number_py("Some other text") is None

def test_parse_watch_number_py():
    assert parse_watch_number_py("Tornado Watch Number 12") == ("0012", "TORNADO")
    assert parse_watch_number_py("Severe Thunderstorm Watch Number 34") == ("0034", "SVR")
    assert parse_watch_number_py("Invalid watch") is None
