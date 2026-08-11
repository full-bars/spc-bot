"""Coverage round 6: sounding_utils — dark-mode prefs, parameter formatting.

Pure-logic tests for cogs/sounding_utils.py (46% covered): the dark-mode
preference wrappers, the thermodynamic/kinematic summary formatter (via a
mocked SoundPy fallback — the Rust kernel is not present locally), and the
parameter-text cache wrapper.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np

from cogs import sounding_utils


# ── Dark mode preferences ────────────────────────────────────────────────────


async def test_get_user_dark_mode_true(isolated_db):
    from utils import db as sqlite_backend
    from utils import state_store

    state_store._cache.clear()
    await sqlite_backend.set_state("sounding_dark_123", "1")
    assert await sounding_utils.get_user_dark_mode(123) is True


async def test_get_user_dark_mode_false(isolated_db):
    from utils import db as sqlite_backend
    from utils import state_store

    state_store._cache.clear()
    await sqlite_backend.set_state("sounding_dark_123", "0")
    assert await sounding_utils.get_user_dark_mode(123) is False


async def test_get_user_dark_mode_missing(isolated_db):
    from utils import state_store

    state_store._cache.clear()
    assert await sounding_utils.get_user_dark_mode(999) is False


async def test_set_user_dark_mode(isolated_db):
    from utils import db as sqlite_backend

    await sounding_utils.set_user_dark_mode(123, True)
    assert await sqlite_backend.get_state("sounding_dark_123") == "1"

    await sounding_utils.set_user_dark_mode(123, False)
    assert await sqlite_backend.get_state("sounding_dark_123") == "0"


# ── Parameter summary formatting ─────────────────────────────────────────────


def _fake_sounding_params(thermo, kinematics):
    """Patch sounderpy.sounding_params(...).calc() to return (_, thermo, kin)."""
    calc = MagicMock(return_value=[None, thermo, kinematics])
    sp = MagicMock()
    sp.calc.return_value = calc.return_value
    sp.calc = calc
    patcher = patch("sounderpy.sounding_params", return_value=sp)
    return patcher


def test_compute_params_text_formats_values():
    thermo = {
        "sbcape": 2500.4,
        "mucape": None,
        "mlcape": np.ma.array([1.0], mask=[True])[0],  # masked -> N/A
        "sb3cape": "--",
        "mu3cape": 1800,
        "sbcin": 0.0,
        "mucin": -50.3,
        "dcape": None,
        "mu_ecape": None,
        "sb_lcl_p": 900,
        "sb_lfc_p": None,
        "lr_03km": 7.2,
        "lr_36km": None,
    }
    kinematics = {
        "eil_z": 800,
        "shear_0_to_1000": 35,
        "shear_0_to_6000": None,
        "srh_0_to_1000": np.ma.array([1.0], mask=[True])[0],
        "srh_0_to_3000": 150.0,
        "eil_stp": 2.5,
        "eil_scp": 1.2,
    }
    with _fake_sounding_params(thermo, kinematics):
        summary = sounding_utils._compute_params_text({"dummy": True})

    assert summary is not None
    assert "SBCAPE: 2500 J/kg" in summary
    assert "MUCAPE: N/A" in summary
    assert "MLCAPE: N/A" in summary  # masked
    assert "SB 0-3km CAPE: N/A" in summary  # "--"
    assert "MU 0-3km CAPE: 1800 J/kg" in summary
    assert "SBCIN: 0 J/kg" in summary
    assert "MUCIN: -50 J/kg" in summary
    assert "SB LCL Pressure: 900 hPa" in summary
    assert "Lapse Rate (0-3km): 7 K/km" in summary
    assert "Effective Inflow Layer (EIL): 800 mb" in summary
    assert "Bulk Shear (0-1km): 35 kts" in summary
    assert "SRH (0-3km): 150 m²/s²" in summary
    assert "STP (Effective): 2" in summary
    assert "SCP: 1" in summary


def test_compute_params_text_sounderpy_import_error():
    # Simulate sounderpy being unavailable: the fallback ImportError is
    # swallowed and the function returns None.
    real_import = __import__

    def _fake_import(name, *a, **kw):
        if name == "sounderpy":
            raise ImportError("no sounderpy")
        return real_import(name, *a, **kw)

    with patch("builtins.__import__", side_effect=_fake_import):
        assert sounding_utils._compute_params_text({"dummy": True}) is None


async def test_get_sounding_params_text_cache_hit():
    with patch("utils.state_store.get_product_cache", new_callable=AsyncMock) as mock_cache:
        mock_cache.return_value = "cached params"

        result = await sounding_utils.get_sounding_params_text(
            {"dummy": True}, cache_key="test-key"
        )

    assert result == "cached params"


async def test_get_sounding_params_text_computes_and_caches():
    with patch("utils.state_store.get_product_cache", new_callable=AsyncMock) as mock_cache, patch(
        "utils.state_store.set_product_cache", new_callable=AsyncMock
    ) as mock_set, patch("utils.worker_pool.get_sounding_executor", return_value=None), patch(
        "cogs.sounding_utils._compute_params_text", return_value="params text"
    ):
        mock_cache.return_value = None

        result = await sounding_utils.get_sounding_params_text(
            {"dummy": True}, cache_key="test-key"
        )

    assert result == "params text"
    mock_set.assert_awaited_once_with("raw_text_test-key", "params text", ttl=3600)
