"""
Unit tests for cogs/nwws.py — parsing and routing of XMPP MUC products.
Tests updated for Rust tokio XMPP backend.
"""

# Note: Comprehensive async tests for _process_nwws_message are run in CI
# with full dependencies. These are minimal compatibility tests.

def test_parse_md_number():
    """Test MD number parsing."""
    from cogs.nwws import parse_md_number

    # Test valid MD number
    result = parse_md_number("This is Mesoscale Discussion 42 here")
    assert result == "0042", f"Expected '0042', got {result}"

    # Test no MD number
    result = parse_md_number("No MD here")
    assert result is None, f"Expected None, got {result}"


def test_parse_watch_number():
    """Test watch number parsing."""
    from cogs.nwws import parse_watch_number

    # Test tornado watch
    result = parse_watch_number("Tornado Watch Number 42")
    assert result == ("0042", "TORNADO"), f"Expected ('0042', 'TORNADO'), got {result}"

    # Test SVR watch
    result = parse_watch_number("Severe Thunderstorm Watch Number 123")
    assert result == ("0123", "SVR"), f"Expected ('0123', 'SVR'), got {result}"

    # Test no watch
    result = parse_watch_number("No watch here")
    assert result is None, f"Expected None, got {result}"
