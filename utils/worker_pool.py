"""
utils/worker_pool.py — Shared ProcessPoolExecutors for CPU-heavy tasks.
Separates lightweight hodograph rendering from heavyweight sounding plots
to prevent long-running soundings from blocking rapid-fire radar updates.
"""

import asyncio
import concurrent.futures
from typing import Optional

_HODO_EXECUTOR: Optional[concurrent.futures.ProcessPoolExecutor] = None
_SOUNDING_EXECUTOR: Optional[concurrent.futures.ProcessPoolExecutor] = None

# Hodographs are fast and memory-light.
_MAX_HODO_WORKERS = 4
# Soundings are slow and memory-heavy (SounderPy/MetPy).
_MAX_SOUNDING_WORKERS = 3

# Semaphore for sounding queue management (for UI feedback)
_sounding_semaphore: Optional[asyncio.Semaphore] = None


def _worker_init():
    """Initialize each worker process by pre-importing heavy libraries."""
    import io as _io
    import logging as _logging
    import sys as _sys

    # Silence inherited loggers to prevent double-logging to stdout
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
            initializer=_worker_init
        )
    return _HODO_EXECUTOR


def prefork_sounding_executor() -> None:
    """Pre-fork all sounding workers at bot startup. Call from main.py before bot.run()."""
    global _SOUNDING_EXECUTOR
    if _SOUNDING_EXECUTOR is None:
        import logging as _logging
        _logging.getLogger("spc_bot").info(f"Pre-forking Sounding Pool ({_MAX_SOUNDING_WORKERS} workers)")
        _SOUNDING_EXECUTOR = concurrent.futures.ProcessPoolExecutor(
            max_workers=_MAX_SOUNDING_WORKERS,
            initializer=_worker_init,
            max_tasks_per_child=5
        )


def get_sounding_executor() -> concurrent.futures.ProcessPoolExecutor:
    """Get or create the executor dedicated to heavy sounding plots."""
    global _SOUNDING_EXECUTOR
    if _SOUNDING_EXECUTOR is None:
        import logging as _logging
        _logging.getLogger("spc_bot").info(f"Initializing Sounding Pool ({_MAX_SOUNDING_WORKERS} workers)")
        _SOUNDING_EXECUTOR = concurrent.futures.ProcessPoolExecutor(
            max_workers=_MAX_SOUNDING_WORKERS,
            initializer=_worker_init,
            max_tasks_per_child=5
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
    if hasattr(sem, '_waiters') and sem._waiters is not None:
        return len(sem._waiters)
    return 0


def get_executor() -> concurrent.futures.ProcessPoolExecutor:
    """Legacy alias for the hodo executor."""
    return get_hodo_executor()


def shutdown_executors():
    """Cleanly shut down all worker pools."""
    global _HODO_EXECUTOR, _SOUNDING_EXECUTOR
    if _HODO_EXECUTOR is not None:
        _HODO_EXECUTOR.shutdown(wait=True, cancel_futures=True)
        _HODO_EXECUTOR = None
    if _SOUNDING_EXECUTOR is not None:
        _SOUNDING_EXECUTOR.shutdown(wait=True, cancel_futures=True)
        _SOUNDING_EXECUTOR = None


def shutdown_executor():
    """Legacy alias."""
    shutdown_executors()
