"""
Unit tests for NWWSCog with Rust tokio XMPP backend.

Note: Async integration tests for cog_load/cog_unload are run in CI
with full dependencies and async framework setup. These are compatibility tests.
"""

def test_nwws_cog_imports():
    """Verify NWWSCog can be imported."""
    from cogs.nwws import NWWSCog
    assert NWWSCog is not None


def test_rust_functions_exist():
    """Verify Rust NWWS functions are available."""
    import spc_rust_core
    assert hasattr(spc_rust_core, 'nwws_start')
    assert hasattr(spc_rust_core, 'nwws_stop')
    assert hasattr(spc_rust_core, 'nwws_try_recv')
    assert hasattr(spc_rust_core, 'nwws_is_connected')
    assert hasattr(spc_rust_core, 'nwws_stats')
