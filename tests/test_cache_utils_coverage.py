"""Coverage round 4: cache-management utilities (pure filesystem logic)."""

import os
import time

from utils import cache_utils


def _write_file(path, size=10, mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_cleanup_sync_evicts_old_keeps_new(tmp_path):
    old = _write_file(str(tmp_path / "old.txt"), size=100, mtime=time.time() - 10 * 86400)
    new = _write_file(str(tmp_path / "new.txt"), size=50, mtime=time.time())

    deleted, freed = cache_utils._cleanup_sync(str(tmp_path), max_age_seconds=7 * 86400)

    assert deleted == 1
    assert freed == 100
    assert not os.path.exists(old)
    assert os.path.exists(new)


def test_cleanup_sync_skips_directories(tmp_path):
    _write_file(str(tmp_path / "sub" / "inner.txt"))
    subdir = str(tmp_path / "sub")

    deleted, _ = cache_utils._cleanup_sync(str(tmp_path), max_age_seconds=0)

    assert deleted == 0
    assert os.path.isdir(subdir)


def test_cleanup_sync_missing_dir_returns_zero(tmp_path):
    deleted, freed = cache_utils._cleanup_sync(str(tmp_path / "nope"), 100)
    assert (deleted, freed) == (0, 0)


async def test_cleanup_old_cache_files_missing_dir(tmp_path):
    deleted, freed = await cache_utils.cleanup_old_cache_files(
        str(tmp_path / "nope"), max_age_seconds=100
    )
    assert (deleted, freed) == (0, 0)


async def test_cleanup_old_cache_files_evicts(tmp_path):
    _write_file(str(tmp_path / "old.bin"), size=200, mtime=time.time() - 20 * 86400)
    _write_file(str(tmp_path / "fresh.bin"), size=10, mtime=time.time())

    deleted, freed = await cache_utils.cleanup_old_cache_files(
        str(tmp_path), max_age_seconds=7 * 86400
    )

    assert deleted == 1
    assert freed == 200


def test_get_cache_size_sums_files(tmp_path):
    _write_file(str(tmp_path / "a.bin"), size=10)
    _write_file(str(tmp_path / "b.bin"), size=20)

    assert cache_utils.get_cache_size(str(tmp_path)) == 30


def test_get_cache_size_missing_dir_returns_zero(tmp_path):
    assert cache_utils.get_cache_size(str(tmp_path / "nope")) == 0


def test_get_cache_size_skips_directories(tmp_path):
    _write_file(str(tmp_path / "sub" / "inner.bin"), size=5)
    _write_file(str(tmp_path / "top.bin"), size=7)

    assert cache_utils.get_cache_size(str(tmp_path)) == 7
