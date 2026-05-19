"""Storage management — auto-cleanup of old merge outputs + agent temp downloads.

Two trees are tracked:
  1. <user-chosen output location>/Merge Outputs/<mode>/<YYYY-MM>/<YYYY-MM-DD>/
  2. agent/downloads/<job_id>/

Date subdirs older than STORAGE_RETAIN_DAYS are deleted entirely; files inside
the current week are never touched. The sweeps only descend into the two named
roots — anything else inside the user's chosen folder is left alone.
"""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("ngl.storage")


@dataclass
class SweepResult:
    files_removed: int = 0
    bytes_freed: int = 0
    folders_removed: int = 0


@dataclass
class StorageInfo:
    output_size_bytes: int = 0
    output_file_count: int = 0
    output_folder_count: int = 0
    downloads_size_bytes: int = 0
    downloads_batch_count: int = 0
    last_cleanup_ts: float = 0.0
    last_cleanup_freed_bytes: int = 0
    last_cleanup_files_removed: int = 0


def _dir_size(path: Path) -> tuple[int, int]:
    """Returns (total_bytes, file_count)."""
    total = 0
    count = 0
    if not path.exists():
        return 0, 0
    for entry in path.rglob("*"):
        if entry.is_file():
            try:
                total += entry.stat().st_size
                count += 1
            except OSError:
                pass
    return total, count


def _is_older_than(path: Path, threshold_ts: float) -> bool:
    try:
        return path.stat().st_mtime < threshold_ts
    except OSError:
        return False


def sweep_old_outputs(output_root: Path, retain_days: int) -> SweepResult:
    """Walk <output_root>/Merge Outputs/<mode>/<YYYY-MM>/<YYYY-MM-DD> and delete
    any date subdir older than retain_days.

    Skips entirely if Merge Outputs/ doesn't exist. Errors deleting individual
    folders are logged but don't abort the sweep.
    """
    result = SweepResult()
    base = output_root / "Merge Outputs"
    if not base.exists():
        return result

    threshold_ts = time.time() - (retain_days * 86400)

    for mode_dir in base.iterdir():
        if not mode_dir.is_dir():
            continue
        for month_dir in mode_dir.iterdir():
            if not month_dir.is_dir():
                continue
            for date_dir in month_dir.iterdir():
                if not date_dir.is_dir():
                    continue
                if _is_older_than(date_dir, threshold_ts):
                    size, count = _dir_size(date_dir)
                    try:
                        shutil.rmtree(date_dir)
                        result.bytes_freed += size
                        result.files_removed += count
                        result.folders_removed += 1
                        logger.info(
                            "Sweep removed %s (%d files, %d bytes)",
                            date_dir, count, size,
                        )
                    except OSError as e:
                        logger.warning("Failed to remove %s: %s", date_dir, e)
    return result


def sweep_old_downloads(downloads_root: Path, retain_days: int) -> SweepResult:
    """Walk <downloads_root>/<job_id>/ and delete any job dir older than retain_days."""
    result = SweepResult()
    if not downloads_root.exists():
        return result

    threshold_ts = time.time() - (retain_days * 86400)

    for job_dir in downloads_root.iterdir():
        if not job_dir.is_dir():
            continue
        if _is_older_than(job_dir, threshold_ts):
            size, count = _dir_size(job_dir)
            try:
                shutil.rmtree(job_dir)
                result.bytes_freed += size
                result.files_removed += count
                result.folders_removed += 1
                logger.info(
                    "Sweep removed download dir %s (%d files, %d bytes)",
                    job_dir, count, size,
                )
            except OSError as e:
                logger.warning("Failed to remove %s: %s", job_dir, e)
    return result


def get_storage_info(output_root: Path, downloads_root: Path,
                     last_cleanup_ts: float = 0.0,
                     last_cleanup_freed_bytes: int = 0,
                     last_cleanup_files_removed: int = 0) -> StorageInfo:
    """Compute current storage totals. Walks both trees, no caching."""
    info = StorageInfo()

    base = output_root / "Merge Outputs"
    if base.exists():
        for mode_dir in base.iterdir():
            if not mode_dir.is_dir():
                continue
            for month_dir in mode_dir.iterdir():
                if not month_dir.is_dir():
                    continue
                for date_dir in month_dir.iterdir():
                    if date_dir.is_dir():
                        info.output_folder_count += 1
        size, count = _dir_size(base)
        info.output_size_bytes = size
        info.output_file_count = count

    if downloads_root.exists():
        info.downloads_batch_count = sum(
            1 for p in downloads_root.iterdir() if p.is_dir()
        )
        size, _ = _dir_size(downloads_root)
        info.downloads_size_bytes = size

    info.last_cleanup_ts = last_cleanup_ts
    info.last_cleanup_freed_bytes = last_cleanup_freed_bytes
    info.last_cleanup_files_removed = last_cleanup_files_removed

    return info
