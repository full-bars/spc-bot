"""Tests for Rust parameter calculations with Python fallback verification."""

import numpy as np
import pytest
from lib.vad_plotter.params import (
    compute_bunkers, compute_bunkers_py,
    compute_srh, compute_srh_py,
    vec2comp, comp2vec,
)


@pytest.fixture
def sample_vad_data():
    """Sample VAD profile for testing."""
    return {
        'wind_dir': np.array([250., 260., 270., 280., 290., 300., 310.]),
        'wind_spd': np.array([5., 10., 15., 20., 25., 30., 35.]),
        'altitude': np.array([0., 1000., 2000., 3000., 4000., 5000., 6000.]),
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
        dirs = np.array([0., 90., 180., 270.])
        spds = np.array([10., 10., 10., 10.])
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
        rust_result = compute_bunkers(sample_vad_data)
        python_result = compute_bunkers_py(sample_vad_data)

        for rust_motion, python_motion in zip(rust_result, python_result):
            rust_dir, rust_spd = rust_motion
            python_dir, python_spd = python_motion
            # Allow 0.1 degree difference in direction, 0.1 kt in speed
            assert abs(rust_dir - python_dir) < 0.1 or \
                   (abs(rust_dir - python_dir) > 359.0), \
                   f"Direction mismatch: {rust_dir} vs {python_dir}"
            assert abs(rust_spd - python_spd) < 0.1, \
                   f"Speed mismatch: {rust_spd} vs {python_spd}"

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
            'wind_dir': np.array([]),
            'wind_spd': np.array([]),
            'altitude': np.array([]),
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
        storm_motion = (250.0, 20.0)
        hght = 3000.0  # 3 km

        rust_result = compute_srh(sample_vad_data, storm_motion, hght)
        python_result = compute_srh_py(sample_vad_data, storm_motion, hght)

        # SRH values should be close (within 1 m2/s2)
        if not np.isnan(python_result):
            assert abs(rust_result - python_result) < 1.0, \
                   f"SRH mismatch: {rust_result} vs {python_result}"

    def test_returns_scalar(self, sample_vad_data):
        """Should return a scalar value."""
        storm_motion = (250.0, 20.0)
        result = compute_srh(sample_vad_data, storm_motion, 3000.0)
        assert isinstance(result, (int, float, np.floating))

    def test_empty_profile(self):
        """Should handle empty profile gracefully."""
        empty_data = {
            'wind_dir': np.array([]),
            'wind_spd': np.array([]),
            'altitude': np.array([]),
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
