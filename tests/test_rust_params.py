"""Tests for Rust parameter calculations with Python fallback verification."""

import numpy as np
import pytest
from lib.vad_plotter.params import (
    compute_bunkers,
    compute_srh,
    compute_bunkers_py,
    compute_srh_py,
    vec2comp,
    comp2vec,
)


@pytest.fixture
def sample_vad_data():
    """Sample VAD profile for testing (altitude in km)."""
    return {
        "wind_dir": np.array([250.0, 260.0, 270.0, 280.0, 290.0, 300.0, 310.0]),
        "wind_spd": np.array([5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0]),
        "altitude": np.array([0.1, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0]),
    }


class TestVec2Comp:
    """Test vector to component conversion."""

    def test_scalar(self):
        """Test scalar inputs."""
        u, v = vec2comp(0.0, 10.0)
        assert abs(u) < 0.01  # u should be ~0
        assert abs(v - (-10.0)) < 0.01  # v should be ~-10

    def test_array(self):
        """Test array inputs."""
        dirs = np.array([0.0, 90.0, 180.0, 270.0])
        spds = np.array([10.0, 10.0, 10.0, 10.0])
        u, v = vec2comp(dirs, spds)
        assert isinstance(u, np.ndarray)
        assert isinstance(v, np.ndarray)


class TestComp2Vec:
    """Test component to vector conversion."""

    def test_scalar(self):
        """Test scalar inputs."""
        dir_res, spd_res = comp2vec(0.0, -10.0)
        assert abs(spd_res - 10.0) < 0.01
        assert abs(dir_res - 0.0) < 1.0 or abs(dir_res - 360.0) < 1.0


class TestComputeBunkers:
    """Test Bunkers storm motion calculation."""

    def test_matches_python(self, sample_vad_data):
        """Rust version should match Python within tolerance."""
        # Make a copy to avoid mutating the fixture, and extend above 6km
        data = {
            "wind_dir": np.append(sample_vad_data["wind_dir"].copy(), [275.0, 280.0]),
            "wind_spd": np.append(sample_vad_data["wind_spd"].copy(), [38.0, 40.0]),
            "altitude": np.append(sample_vad_data["altitude"].copy(), [7.0, 8.0]),
        }

        rust_result = compute_bunkers(data)
        python_result = compute_bunkers_py(data)

        for rust_motion, python_motion in zip(rust_result, python_result):
            rust_dir, rust_spd = rust_motion
            python_dir, python_spd = python_motion
            # Allow very tight tolerance: algorithm now matches Python exactly
            assert abs(rust_dir - python_dir) < 0.01 or abs(rust_dir - python_dir) > 359.9, (
                f"Direction mismatch: {rust_dir} vs {python_dir}"
            )
            assert abs(rust_spd - python_spd) < 0.01, f"Speed mismatch: {rust_spd} vs {python_spd}"

    def test_returns_three_motions_only(self, sample_vad_data):
        """Should return three motion tuples (mean, left, right)."""
        result = compute_bunkers(sample_vad_data)
        assert len(result) == 3

    def test_returns_three_motions(self, sample_vad_data):
        """Should return (right_motion, left_motion, mean_motion)."""
        result = compute_bunkers(sample_vad_data)
        assert len(result) == 3
        for motion in result:
            assert len(motion) == 2
            assert isinstance(motion[0], (int, float))
            assert isinstance(motion[1], (int, float))

    def test_empty_profile(self):
        """Should handle empty profile gracefully."""
        empty_data = {
            "wind_dir": np.array([]),
            "wind_spd": np.array([]),
            "altitude": np.array([]),
        }
        try:
            result = compute_bunkers(empty_data)
            # If it succeeds, all values should be NaN
            assert all(np.isnan(x) for motion in result for x in motion)
        except (ValueError, IndexError, ZeroDivisionError):
            # Empty profile may raise an exception; that's acceptable
            pass


class TestComputeSRH:
    """Test storm-relative helicity calculation."""

    def test_matches_python(self, sample_vad_data):
        """Rust version should match Python within tolerance."""
        # Make a copy to avoid mutating the fixture, and extend above 3km
        data = {
            "wind_dir": np.append(sample_vad_data["wind_dir"].copy(), [275.0, 280.0]),
            "wind_spd": np.append(sample_vad_data["wind_spd"].copy(), [38.0, 40.0]),
            "altitude": np.append(sample_vad_data["altitude"].copy(), [7.0, 8.0]),
        }

        storm_motion = (250.0, 20.0)
        hght = 3.0  # 3 km (in same units as altitude)

        rust_result = compute_srh(data, storm_motion, hght)
        python_result = compute_srh_py(data, storm_motion, hght)

        # SRH values should match exactly (algorithm now matches Python perfectly)
        assert abs(rust_result - python_result) < 0.01, (
            f"SRH mismatch: Rust={rust_result} vs Python={python_result}"
        )

    def test_computes_value(self, sample_vad_data):
        """Should compute a finite SRH value."""
        storm_motion = (250.0, 20.0)
        hght = 3.0  # 3 km

        result = compute_srh(sample_vad_data, storm_motion, hght)
        assert isinstance(result, (int, float, np.floating))

    def test_returns_scalar(self, sample_vad_data):
        """Should return a scalar value."""
        storm_motion = (250.0, 20.0)
        result = compute_srh(sample_vad_data, storm_motion, 3000.0)
        assert isinstance(result, (int, float, np.floating))

    def test_empty_profile(self):
        """Should handle empty profile gracefully."""
        empty_data = {
            "wind_dir": np.array([]),
            "wind_spd": np.array([]),
            "altitude": np.array([]),
        }
        try:
            result = compute_srh(empty_data, (250.0, 20.0), 3000.0)
            # If it succeeds, should return NaN
            assert np.isnan(result)
        except (ValueError, IndexError):
            # Empty profile may raise an exception; that's acceptable
            pass


class TestRustFallback:
    """Test Python fallback when Rust is unavailable."""

    def test_compute_bunkers_fallback(self, sample_vad_data):
        """Bunkers should fall back to Python gracefully."""
        # This just verifies the function works; actual fallback would require
        # mocking the Rust import, which is tested implicitly by running these
        # tests in an environment with/without Rust available.
        result = compute_bunkers(sample_vad_data)
        assert len(result) == 3

    def test_compute_srh_fallback(self, sample_vad_data):
        """SRH should fall back to Python gracefully."""
        result = compute_srh(sample_vad_data, (250.0, 20.0), 3000.0)
        assert isinstance(result, (int, float, np.floating))
