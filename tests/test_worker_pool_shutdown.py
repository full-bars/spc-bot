"""Tests for utils/worker_pool.shutdown_executors fast/non-blocking shutdown.

Regression guard: a graceful ``shutdown(wait=True)`` used to block until the
worker currently rendering a plot finished, overrunning systemd's
TimeoutStopSec and getting the process SIGKILLed with orphaned workers left
behind. shutdown_executors() must now return quickly and terminate in-flight
workers.
"""

import time

import pytest

import utils.worker_pool as wp


def _busy(seconds: float) -> str:
    """Worker task that blocks far longer than the shutdown should wait."""
    time.sleep(seconds)
    return "done"


@pytest.fixture(autouse=True)
def _reset_pools():
    """Ensure module globals start and end clean so tests don't leak pools."""
    wp._HODO_EXECUTOR = None
    wp._SOUNDING_EXECUTOR = None
    yield
    wp.shutdown_executors()


def test_shutdown_returns_fast_with_inflight_worker():
    """A worker mid-render must not hold shutdown_executors() open."""
    executor = wp.get_hodo_executor()
    # Kick off a task that would block for 30s if we waited for it.
    future = executor.submit(_busy, 30.0)
    # Let the worker actually pick the task up.
    time.sleep(0.5)

    start = time.monotonic()
    wp.shutdown_executors()
    elapsed = time.monotonic() - start

    # Force-terminate path should be near-instant, never the 30s render.
    assert elapsed < 5.0, f"shutdown blocked for {elapsed:.1f}s on in-flight worker"
    # Globals reset so the next get_*_executor() rebuilds a fresh pool.
    assert wp._HODO_EXECUTOR is None
    assert wp._SOUNDING_EXECUTOR is None
    # The in-flight future never completes successfully (worker was killed).
    assert not (future.done() and future.exception() is None and future.result() == "done")


def test_shutdown_noop_when_no_pools():
    """Safe to call when nothing was ever forked (idempotent)."""
    assert wp._HODO_EXECUTOR is None
    assert wp._SOUNDING_EXECUTOR is None
    wp.shutdown_executors()  # must not raise
    wp.shutdown_executors()  # idempotent


def test_terminates_worker_processes():
    """Worker processes are actually killed, not left orphaned."""
    executor = wp.get_sounding_executor()
    executor.submit(_busy, 30.0)
    time.sleep(0.5)
    procs = [p for p in executor._processes.values()]
    assert any(p.is_alive() for p in procs)

    wp.shutdown_executors()

    # Give terminate()+join() a beat to reap.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and any(p.is_alive() for p in procs):
        time.sleep(0.1)
    assert all(not p.is_alive() for p in procs), "worker processes survived shutdown"
