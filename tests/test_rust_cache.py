"""Tests for Rust image cache batch validator with Python fallback verification."""

import pytest
from utils.change_detection import (
    validate_image_cache_batch,
    validate_image_cache_batch_py,
    calculate_hash_bytes,
)


class TestValidateImageCacheBatch:
    """Test image cache batch validation."""

    @pytest.fixture
    def sample_images(self):
        """Sample image data for testing."""
        # Valid image: > 2048 bytes
        valid_image = b"PNG\x89\x50" + b"\x00" * 3000

        # Placeholder: < 2048 bytes
        placeholder = b"<html>Not Found</html>"

        return [
            ("https://example.com/valid.png", valid_image),
            ("https://example.com/placeholder.png", placeholder),
        ]

    def test_batch_returns_correct_structure(self, sample_images):
        """Should return list of (url, hash, is_placeholder) tuples."""
        results = validate_image_cache_batch(sample_images)
        assert len(results) == 2
        assert isinstance(results[0], tuple)
        assert len(results[0]) == 3
        assert isinstance(results[0][0], str)  # url
        assert isinstance(results[0][1], str)  # hash
        assert isinstance(results[0][2], bool)  # is_placeholder

    def test_matches_python(self, sample_images):
        """Rust version should match Python version."""
        rust_results = validate_image_cache_batch(sample_images)
        python_results = validate_image_cache_batch_py(sample_images)

        assert len(rust_results) == len(python_results)
        for (rust_url, rust_hash, rust_placeholder), (
            py_url,
            py_hash,
            py_placeholder,
        ) in zip(rust_results, python_results):
            assert rust_url == py_url
            assert rust_hash == py_hash
            assert rust_placeholder == py_placeholder

    def test_detects_valid_image(self, sample_images):
        """Should mark non-placeholder images correctly."""
        results = validate_image_cache_batch(sample_images)
        # First item (valid image) should not be placeholder
        _, _, is_placeholder = results[0]
        assert is_placeholder is False

    def test_detects_placeholder(self, sample_images):
        """Should detect placeholder images < 2048 bytes."""
        results = validate_image_cache_batch(sample_images)
        # Second item (placeholder) should be marked as placeholder
        _, _, is_placeholder = results[1]
        assert is_placeholder is True

    def test_hash_consistency(self):
        """Hash should match calculate_hash_bytes for single item."""
        content = b"test image data" * 100
        url = "https://example.com/image.png"

        batch_results = validate_image_cache_batch([(url, content)])
        _, batch_hash, _ = batch_results[0]

        single_hash = calculate_hash_bytes(content)
        assert batch_hash == single_hash

    def test_empty_batch(self):
        """Should handle empty batch gracefully."""
        results = validate_image_cache_batch([])
        assert results == []

    def test_large_batch(self):
        """Should handle large batches efficiently."""
        items = [(f"https://example.com/image{i}.png", b"data" * 1000) for i in range(100)]
        results = validate_image_cache_batch(items)
        assert len(results) == 100

    def test_placeholder_threshold_boundary(self):
        """Should correctly apply 2048 byte threshold."""
        exact_threshold = b"x" * 2048
        below_threshold = b"x" * 2047
        above_threshold = b"x" * 2049

        batch = [
            ("exact", exact_threshold),
            ("below", below_threshold),
            ("above", above_threshold),
        ]

        results = validate_image_cache_batch(batch)
        _, _, exact_is_placeholder = results[0]
        _, _, below_is_placeholder = results[1]
        _, _, above_is_placeholder = results[2]

        assert exact_is_placeholder is False
        assert below_is_placeholder is True
        assert above_is_placeholder is False
