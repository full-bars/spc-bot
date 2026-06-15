import pytest
from cogs.outlooks import _extract_product_from_url

def test_extract_product_from_url():
    assert _extract_product_from_url("day1probotlk_torn", 1) == "tornado"
    assert _extract_product_from_url("day2probotlk_wind", 2) == "wind"
    assert _extract_product_from_url("day1probotlk_hail", 1) == "hail"
    assert _extract_product_from_url("day1otlk", 1) == "categorical"
    assert _extract_product_from_url("day3prob", 3) == "categorical"
    assert _extract_product_from_url("random_url", 1) == "other"
