"""Storage sweep + info functions."""

import os
import time
from pathlib import Path

import pytest

from services.storage import sweep_old_outputs, sweep_old_downloads, get_storage_info


def _mtime_age_days(path: Path, days: int) -> None:
    """Backdate a path's mtime by N days."""
    ts = time.time() - (days * 86400)
    os.utime(path, (ts, ts))


def _make_dated_dir(root: Path, mode: str, year_month: str, day: str, content: bytes = b"x") -> Path:
    """Make root/Merge Outputs/<mode>/<year_month>/<day>/file.pdf with one file."""
    d = root / "Merge Outputs" / mode / year_month / day
    d.mkdir(parents=True, exist_ok=True)
    (d / "file.pdf").write_bytes(content)
    return d


def test_sweep_outputs_deletes_old_date_folders(tmp_path: Path):
    root = tmp_path
    fresh = _make_dated_dir(root, "Per Invoice", "2026-05", "2026-05-19")
    stale = _make_dated_dir(root, "Per Invoice", "2026-05", "2026-05-01")
    _mtime_age_days(stale, 14)
    _mtime_age_days(stale / "file.pdf", 14)

    result = sweep_old_outputs(root, retain_days=7)

    assert fresh.exists()
    assert not stale.exists()
    assert result.files_removed == 1
    assert result.bytes_freed > 0


def test_sweep_outputs_handles_multiple_modes(tmp_path: Path):
    root = tmp_path
    fresh = _make_dated_dir(root, "Per Invoice", "2026-05", "2026-05-19")
    stale1 = _make_dated_dir(root, "Per Container", "2026-04", "2026-04-10")
    stale2 = _make_dated_dir(root, "Combined PDF", "2026-04", "2026-04-15")
    for s in (stale1, stale2):
        _mtime_age_days(s, 14)
        _mtime_age_days(s / "file.pdf", 14)

    result = sweep_old_outputs(root, retain_days=7)

    assert fresh.exists()
    assert not stale1.exists()
    assert not stale2.exists()
    assert result.files_removed == 2


def test_sweep_outputs_does_not_touch_files_outside_merge_outputs(tmp_path: Path):
    root = tmp_path
    untouched = root / "some-other-file.pdf"
    untouched.write_bytes(b"hello")
    _mtime_age_days(untouched, 30)

    sweep_old_outputs(root, retain_days=7)

    assert untouched.exists()


def test_sweep_outputs_missing_root_is_noop(tmp_path: Path):
    """Sweeping a path that doesn't have Merge Outputs/ is fine."""
    root = tmp_path / "nope"
    result = sweep_old_outputs(root, retain_days=7)
    assert result.files_removed == 0


def test_sweep_downloads_deletes_old_job_dirs(tmp_path: Path):
    fresh = tmp_path / "abc123"
    fresh.mkdir()
    (fresh / "a.pdf").write_bytes(b"new")

    stale = tmp_path / "def456"
    stale.mkdir()
    (stale / "a.pdf").write_bytes(b"old")
    _mtime_age_days(stale, 14)
    _mtime_age_days(stale / "a.pdf", 14)

    result = sweep_old_downloads(tmp_path, retain_days=7)

    assert fresh.exists()
    assert not stale.exists()
    assert result.files_removed == 1


def test_get_storage_info_reports_totals(tmp_path: Path):
    output_root = tmp_path / "out"
    downloads_root = tmp_path / "dl"
    output_root.mkdir(); downloads_root.mkdir()

    _make_dated_dir(output_root, "Per Invoice", "2026-05", "2026-05-19", content=b"a" * 100)
    _make_dated_dir(output_root, "Per Invoice", "2026-05", "2026-05-18", content=b"a" * 50)
    (downloads_root / "job1").mkdir()
    (downloads_root / "job1" / "f.pdf").write_bytes(b"b" * 200)

    info = get_storage_info(output_root, downloads_root)

    assert info.output_size_bytes == 150
    assert info.output_file_count == 2
    assert info.downloads_size_bytes == 200
    assert info.downloads_batch_count == 1
