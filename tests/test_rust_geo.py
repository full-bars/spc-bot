"""Tests for Rust haversine distance calculation with Python fallback."""

from utils.geo import haversine, haversine_py, haversine_batch


class TestHaversine:
    """Test haversine distance calculation."""

    def test_same_point_is_zero(self):
        """Distance from a point to itself should be zero."""
        result = haversine(40.0, -100.0, 40.0, -100.0)
        assert abs(result) < 0.001

    def test_matches_python(self):
        """Rust version should match Python implementation."""
        test_cases = [
            (40.7128, -74.0060, 34.0522, -118.2437),  # NYC to LA
            (35.6762, 139.6503, 51.5074, -0.1278),     # Tokyo to London
            (0.0, 0.0, 0.0, 180.0),                     # Equator, opposite sides
        ]

        for lat1, lon1, lat2, lon2 in test_cases:
            rust_result = haversine(lat1, lon1, lat2, lon2)
            python_result = haversine_py(lat1, lon1, lat2, lon2)
            # Allow 0.01 km tolerance
            assert abs(rust_result - python_result) < 0.01, \
                f"Mismatch: {rust_result} vs {python_result}"

    def test_nyc_to_la_distance(self):
        """NYC to LA distance should be approximately 3944 km."""
        nyc_lat, nyc_lon = 40.7128, -74.0060
        la_lat, la_lon = 34.0522, -118.2437
        distance = haversine(nyc_lat, nyc_lon, la_lat, la_lon)
        # Actual great circle distance is ~3944 km
        assert 3900 < distance < 4000

    def test_symmetry(self):
        """haversine(A, B) should equal haversine(B, A)."""
        lat1, lon1 = 40.0, -100.0
        lat2, lon2 = 35.0, -95.0
        dist1 = haversine(lat1, lon1, lat2, lon2)
        dist2 = haversine(lat2, lon2, lat1, lon1)
        assert abs(dist1 - dist2) < 0.001

    def test_returns_float(self):
        """Should return float type."""
        result = haversine(0.0, 0.0, 1.0, 1.0)
        assert isinstance(result, float)


class TestHaversineBatch:
    """Test batch haversine calculation."""

    def test_matches_individual_calls(self):
        """Batch result should match individual calls."""
        origin_lat, origin_lon = 40.0, -100.0
        targets = [
            (35.0, -95.0),
            (45.0, -105.0),
            (30.0, -90.0),
        ]

        batch_results = haversine_batch(origin_lat, origin_lon, targets)

        assert len(batch_results) == len(targets)
        for i, (lat, lon) in enumerate(targets):
            individual = haversine(origin_lat, origin_lon, lat, lon)
            batch = batch_results[i]
            assert abs(individual - batch) < 0.001, \
                f"Item {i}: {individual} vs {batch}"

    def test_empty_targets(self):
        """Empty targets should return empty list."""
        result = haversine_batch(40.0, -100.0, [])
        assert result == []

    def test_single_target(self):
        """Single target should work correctly."""
        result = haversine_batch(0.0, 0.0, [(1.0, 1.0)])
        assert len(result) == 1
        assert result[0] > 0

    def test_multiple_targets(self):
        """Should handle multiple targets correctly."""
        targets = [(float(i), float(i)) for i in range(10)]
        results = haversine_batch(0.0, 0.0, targets)
        assert len(results) == 10
        # All distances should be increasing (moving away from origin)
        for i in range(1, len(results)):
            assert results[i] > results[i - 1]

    def test_returns_list_of_floats(self):
        """Should return list of floats."""
        results = haversine_batch(40.0, -100.0, [(35.0, -95.0), (45.0, -105.0)])
        assert isinstance(results, list)
        assert all(isinstance(x, float) for x in results)
