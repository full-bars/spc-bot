"""
utils/worker_pool.py — Shared ProcessPoolExecutors for CPU-heavy tasks.
Separates lightweight hodograph rendering from heavyweight sounding plots
to prevent long-running soundings from blocking rapid-fire radar updates.
"""

import asyncio
import concurrent.futures
import multiprocessing
from typing import Optional

_HODO_EXECUTOR: Optional[concurrent.futures.ProcessPoolExecutor] = None
_SOUNDING_EXECUTOR: Optional[concurrent.futures.ProcessPoolExecutor] = None

# Hodographs are fast and memory-light.
_MAX_HODO_WORKERS = 4
# Soundings are slow and memory-heavy (SounderPy/MetPy).
_MAX_SOUNDING_WORKERS = 4

# Semaphore for sounding queue management (for UI feedback)
_sounding_semaphore: Optional[asyncio.Semaphore] = None


def _hodo_worker_init():
    """Hodo-only init — skips SounderPy (hodo renders never touch it)."""
    import logging as _logging

    for name in ("spc_bot", None):
        log_obj = _logging.getLogger(name)
        for h in log_obj.handlers[:]:
            log_obj.removeHandler(h)
    _logging.getLogger().setLevel(_logging.WARNING)

    import matplotlib as _mpl

    _mpl.use("Agg")


def _worker_init():
    """Initialize each worker process by pre-importing heavy libraries."""
    import io as _io
    import logging as _logging
    import sys as _sys

    for name in ("spc_bot", None):
        log_obj = _logging.getLogger(name)
        for h in log_obj.handlers[:]:
            log_obj.removeHandler(h)
    _logging.getLogger().setLevel(_logging.WARNING)

    import matplotlib as _mpl

    _mpl.use("Agg")

    # Silence SounderPy banner
    _stdout = _sys.stdout
    _sys.stdout = _io.StringIO()
    try:
        import sounderpy as _spy  # noqa: F401
    except ImportError:
        pass
    finally:
        _sys.stdout = _stdout


def get_hodo_executor() -> concurrent.futures.ProcessPoolExecutor:
    """Get or create the executor dedicated to fast radar/hodograph plots."""
    global _HODO_EXECUTOR
    if _HODO_EXECUTOR is None:
        import logging as _logging

        _logging.getLogger("spc_bot").info(f"Initializing Hodo Pool ({_MAX_HODO_WORKERS} workers)")
        _HODO_EXECUTOR = concurrent.futures.ProcessPoolExecutor(
            max_workers=_MAX_HODO_WORKERS,
            initializer=_hodo_worker_init,
            mp_context=multiprocessing.get_context("forkserver"),
        )
    return _HODO_EXECUTOR


def prefork_sounding_executor() -> None:
    """Pre-fork all sounding workers at bot startup. Call from main.py before bot.run()."""
    global _SOUNDING_EXECUTOR
    if _SOUNDING_EXECUTOR is None:
        import logging as _logging

        _logging.getLogger("spc_bot").info(
            f"Pre-forking Sounding Pool ({_MAX_SOUNDING_WORKERS} workers)"
        )
        _SOUNDING_EXECUTOR = concurrent.futures.ProcessPoolExecutor(
            max_workers=_MAX_SOUNDING_WORKERS,
            initializer=_worker_init,
            max_tasks_per_child=5,
            mp_context=multiprocessing.get_context("forkserver"),
        )


def get_sounding_executor() -> concurrent.futures.ProcessPoolExecutor:
    """Get or create the executor dedicated to heavy sounding plots."""
    global _SOUNDING_EXECUTOR
    if _SOUNDING_EXECUTOR is None:
        import logging as _logging

        _logging.getLogger("spc_bot").info(
            f"Initializing Sounding Pool ({_MAX_SOUNDING_WORKERS} workers)"
        )
        _SOUNDING_EXECUTOR = concurrent.futures.ProcessPoolExecutor(
            max_workers=_MAX_SOUNDING_WORKERS,
            initializer=_worker_init,
            max_tasks_per_child=5,
            mp_context=multiprocessing.get_context("forkserver"),
        )
    return _SOUNDING_EXECUTOR


def get_sounding_semaphore() -> asyncio.Semaphore:
    """Get the semaphore used to track the sounding queue."""
    global _sounding_semaphore
    if _sounding_semaphore is None:
        _sounding_semaphore = asyncio.Semaphore(_MAX_SOUNDING_WORKERS)
    return _sounding_semaphore


def sounding_queue_depth() -> int:
    """Return current number of tasks waiting for a sounding worker slot."""
    sem = get_sounding_semaphore()
    if hasattr(sem, "_waiters") and sem._waiters is not None:
        return len(sem._waiters)
    return 0


def get_executor() -> concurrent.futures.ProcessPoolExecutor:
    """Legacy alias for the hodo executor."""
    return get_hodo_executor()


def shutdown_executors(join_timeout: float = 1.0):
    """Stop all worker pools quickly, without blocking on in-flight renders.

    A graceful ``shutdown(wait=True)`` blocks until the worker currently
    rendering a plot finishes — a sounding can take tens of seconds, which
    overruns systemd's ``TimeoutStopSec`` and gets the whole process SIGKILLed
    (leaving orphaned worker processes behind). Instead we drop queued work
    (``cancel_futures=True``) without waiting (``wait=False``) and then forcibly
    terminate any worker still alive, so shutdown returns near-instantly. A
    half-drawn plot on shutdown is throwaway, so losing it is harmless.
    """
    global _HODO_EXECUTOR, _SOUNDING_EXECUTOR
    for executor in (_HODO_EXECUTOR, _SOUNDING_EXECUTOR):
        if executor is None:
            continue
        # Capture worker handles BEFORE shutdown() — shutdown(wait=False)
        # clears the executor's internal ``_processes`` map, so grabbing it
        # afterwards would leave in-flight workers un-terminated. ``_processes``
        # is None/empty if no worker forked yet.
        procs = list((getattr(executor, "_processes", None) or {}).values())
        # Drop queued (not-yet-started) work and don't block on a worker
        # mid-render.
        executor.shutdown(wait=False, cancel_futures=True)
        # Hard-stop any worker still running so an in-flight plot can't hold
        # us past TimeoutStopSec. Join all live workers concurrently so the
        # total shutdown wall-clock stays bounded by a single join_timeout
        # (previously serial joins summed up to N*1.0s, exceeding the 3s
        # backstop in main.py).
        alive = [p for p in procs if p.is_alive()]
        for p in alive:
            p.terminate()
        if alive:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(alive)) as joiner:
                futures = [joiner.submit(p.join, join_timeout) for p in alive]
                concurrent.futures.wait(futures, timeout=join_timeout + 0.5)
    _HODO_EXECUTOR = None
    _SOUNDING_EXECUTOR = None


def shutdown_executor():
    """Legacy alias."""
    shutdown_executors()
