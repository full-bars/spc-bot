import pytest
import numpy as np
from lib.vad_plotter.vad_reader import VADFile
from lib.vad_plotter.params import (
    _to_list,
)
import spc_rust_core


@pytest.fixture
def vad_data():
    """Load test.vwp fixture."""
    with open("tests/fixtures/test.vwp", "rb") as f:
        vad = VADFile(f.read())
    # Add a dummy surface wind for more interesting calculations
    vad.add_surface_wind((200, 10))
    return vad


def test_compute_shear_mag_parity(vad_data):
    # Test heights
    for hght in [0.5, 1.0, 3.0, 6.0]:

        def compute_shear_mag_py(data, hght):
            from lib.vad_plotter.params import vec2comp, interp

            u, v = vec2comp(data["wind_dir"], data["wind_spd"])
            u_hght, v_hght = interp(u, v, data["altitude"], hght)
            return float(np.hypot(u_hght - u[0], v_hght - v[0]))

        py_val = compute_shear_mag_py(vad_data, hght)

        # Rust version
        rust_val = spc_rust_core.compute_shear_mag(
            _to_list(vad_data["wind_dir"]),
            _to_list(vad_data["wind_spd"]),
            _to_list(vad_data["altitude"]),
            hght,
        )

        if np.isnan(py_val):
            assert np.isnan(rust_val)
        else:
            assert rust_val == pytest.approx(py_val, abs=1e-9)


def test_compute_sr_flow_parity(vad_data):
    storm_motion = (240, 30)

    for bot, top in [(0, 0.5), (0, 1.0), (0, 3.0)]:

        def compute_sr_flow_py(data, storm_motion, hght_bot, hght_top):
            from lib.vad_plotter.params import vec2comp

            u, v = vec2comp(data["wind_dir"], data["wind_spd"])
            storm_u, storm_v = vec2comp(*storm_motion)
            alt = data["altitude"]
            layer_alts = np.linspace(hght_bot, hght_top, 50)
            u_layer = np.interp(layer_alts, alt, u, left=np.nan, right=np.nan)
            v_layer = np.interp(layer_alts, alt, v, left=np.nan, right=np.nan)
            sr_u = u_layer - storm_u
            sr_v = v_layer - storm_v
            sr_mag = np.hypot(sr_u, sr_v)
            if np.all(np.isnan(sr_mag)):
                return np.nan
            return float(np.nanmean(sr_mag))

        py_val = compute_sr_flow_py(vad_data, storm_motion, bot, top)

        # Rust version
        rust_val = spc_rust_core.compute_sr_flow(
            _to_list(vad_data["wind_dir"]),
            _to_list(vad_data["wind_spd"]),
            _to_list(vad_data["altitude"]),
            storm_motion[0],
            storm_motion[1],
            bot,
            top,
        )

        if np.isnan(py_val):
            assert np.isnan(rust_val)
        else:
            assert rust_val == pytest.approx(py_val, abs=1e-7)  # linspace/interp precision


def test_clip_profile_parity(vad_data):
    clip_alt = 1.0
    intrp_prof = 15.0

    prof = vad_data["wind_spd"]
    alt = vad_data["altitude"]

    def clip_profile_py(prof, alt, clip_alt, intrp_prof):
        try:
            idx_clip = np.where((alt[:-1] <= clip_alt) & (alt[1:] > clip_alt))[0][0]
        except IndexError:
            return np.nan * np.ones(prof.size)
        prof_clip = prof[: (idx_clip + 1)]
        prof_clip = np.append(prof_clip, intrp_prof)
        return np.array(prof_clip)

    py_res = clip_profile_py(prof, alt, clip_alt, intrp_prof)

    # Rust version
    rust_res = spc_rust_core.clip_profile(_to_list(prof), _to_list(alt), clip_alt, intrp_prof)

    np.testing.assert_allclose(rust_res, py_res, atol=1e-9)


def test_interp_nan_behavior():
    """Verify that Rust _interp_linear matches numpy.interp(left=nan, right=nan)"""
    x = [1.0, 2.0, 3.0]
    y = [10.0, 20.0, 30.0]

    # In-bounds
    val = spc_rust_core.compute_shear_mag(
        [0.0, 0.0, 0.0],  # dirs
        y,  # spds
        x,  # alts
        2.5,
    )
    assert val == pytest.approx(15.0)

    # Out of bounds high
    val_high = spc_rust_core.compute_shear_mag([0.0, 0.0, 0.0], y, x, 4.0)
    assert np.isnan(val_high)

    # Out of bounds low
    val_low = spc_rust_core.compute_shear_mag([0.0, 0.0, 0.0], y, x, 0.5)
    assert np.isnan(val_low)
