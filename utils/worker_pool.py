"""
utils/worker_pool.py — Shared ProcessPoolExecutor for CPU-heavy tasks.
Ensures matplotlib and SounderPy rendering don't block the Discord event loop.
"""

import concurrent.futures
import os
from typing import Optional

_EXECUTOR: Optional[concurrent.futures.ProcessPoolExecutor] = None
# Cap at 3 workers to protect memory/CPU on low-tier VPS
_MAX_WORKERS = min(3, (os.cpu_count() or 2))

def _worker_init():
    """Initialize each worker process by pre-importing heavy libraries."""
    import logging as _logging
    import sys as _sys
    import io as _io

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
        pass
    finally:
        _sys.stdout = _stdout

def get_executor() -> concurrent.futures.ProcessPoolExecutor:
    """Get or create the global shared ProcessPoolExecutor."""
    global _EXECUTOR
    if _EXECUTOR is None:
        _EXECUTOR = concurrent.futures.ProcessPoolExecutor(
            max_workers=_MAX_WORKERS,
            initializer=_worker_init
        )
    return _EXECUTOR

def shutdown_executor():
    """Cleanly shut down the worker pool."""
    global _EXECUTOR
    if _EXECUTOR is not None:
        _EXECUTOR.shutdown(wait=False, cancel_futures=True)
        _EXECUTOR = None
