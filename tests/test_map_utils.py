"""
Unit tests for utils/map_utils.py — Local map rendering.
"""

from utils.map_utils import render_tornado_track

def test_render_tornado_track_creates_file(tmp_path):
    output_file = tmp_path / "track_test.png"
    # Simple L-shaped path in Oklahoma
    path = [(35.1, -97.4), (35.2, -97.4), (35.2, -97.3)]
    
    render_tornado_track([path], str(output_file))
    
    assert output_file.exists()
    assert output_file.stat().st_size > 1000 # Should be a non-empty image

def test_render_tornado_track_handles_empty(tmp_path):
    output_file = tmp_path / "should_not_exist.png"
    # Should not crash
    render_tornado_track([], str(output_file))
    assert not output_file.exists()
