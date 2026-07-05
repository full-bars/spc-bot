# Testing Guide

This guide covers the common local checks for SPCBot development.

## Environment

Use the project virtual environment when available:

```bash
./venv/bin/python -m pytest
./venv/bin/python -m ruff check .
```

This repository currently collects **559 tests** with the project virtual environment:

```bash
./venv/bin/python -m pytest --collect-only -q
```

If the virtual environment does not exist yet:

```bash
python3 -m venv venv
./venv/bin/python -m pip install -r requirements-dev.txt
```

The test suite sets required configuration values in `tests/conftest.py` before importing `config.py`, so local tests should not need real Discord, NWWS, or Upstash credentials.

Prefer `./venv/bin/python` for local checks. Some hosts have a system `python3` without pytest or the scientific stack installed.

## Focused Test Runs

Run tests for a specific subsystem while iterating:

```bash
./venv/bin/python -m pytest tests/test_main_lifecycle.py
./venv/bin/python -m pytest tests/test_watches.py tests/test_mesoscale.py
./venv/bin/python -m pytest tests/test_state_store.py tests/test_db.py
./venv/bin/python -m pytest tests/test_hodograph.py tests/test_sounding_qc.py
```

For syntax-only checks across selected files:

```bash
./venv/bin/python -m py_compile config.py main.py utils/http.py utils/cache.py
```

## Background Task Tests

Most tests patch `asyncio.create_task` to prevent fire-and-forget background work from leaking across test boundaries. Tests that need real task scheduling should opt out with:

```python
@pytest.mark.real_create_task
```

Use that marker only when task creation is part of the behavior under test.

## Pre-PR Checklist

1. Run the focused tests for the files you changed.
2. Run `./venv/bin/python -m ruff check .`.
3. For startup or config changes, run a `py_compile` pass over `config.py` and `main.py`.
4. For docs-only changes, verify links and environment variable names against `config.py` and `.env.example`.
