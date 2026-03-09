"""Unit tests for agent/utils.py functions."""

import json
import shutil
import time
from datetime import datetime
from pathlib import Path

import pytest

# Add agent/ to path so imports work
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils import (
    cleanup_old_debug_files,
    rotate_audit_log,
    backup_data_files,
)


@pytest.fixture
def tmp_dir(tmp_path):
    """Provide a temp directory with common subdirs."""
    (tmp_path / "debug").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "backups").mkdir()
    return tmp_path


# ── cleanup_old_debug_files ─────────────────────────────────────────


class TestCleanupOldDebugFiles:
    def test_deletes_old_files(self, tmp_dir):
        debug_dir = tmp_dir / "debug"
        old_file = debug_dir / "old_screenshot.png"
        old_file.write_text("old")
        # Set mtime to 10 days ago
        old_time = time.time() - (10 * 86400)
        import os
        os.utime(old_file, (old_time, old_time))

        deleted = cleanup_old_debug_files(debug_dir, max_age_days=7)
        assert deleted == 1
        assert not old_file.exists()

    def test_keeps_recent_files(self, tmp_dir):
        debug_dir = tmp_dir / "debug"
        recent_file = debug_dir / "recent.png"
        recent_file.write_text("recent")

        deleted = cleanup_old_debug_files(debug_dir, max_age_days=7)
        assert deleted == 0
        assert recent_file.exists()

    def test_handles_missing_dir(self):
        result = cleanup_old_debug_files(Path("/nonexistent/dir"))
        assert result == 0

    def test_handles_empty_dir(self, tmp_dir):
        result = cleanup_old_debug_files(tmp_dir / "debug")
        assert result == 0


# ── rotate_audit_log ────────────────────────────────────────────────


class TestRotateAuditLog:
    def test_no_rotation_when_file_missing(self, tmp_dir):
        rotate_audit_log(tmp_dir / "data" / "audit_log.jsonl")
        # Should not raise

    def test_rotates_large_file(self, tmp_dir):
        audit_file = tmp_dir / "data" / "audit_log.jsonl"
        # Write >1MB of data
        entry = json.dumps({"timestamp": datetime.now().isoformat(), "data": "x" * 500})
        with open(audit_file, "w") as f:
            for _ in range(2500):  # ~1.25 MB
                f.write(entry + "\n")

        archive_dir = tmp_dir / "data" / "archive"
        rotate_audit_log(audit_file, archive_dir)

        assert not audit_file.exists()
        assert archive_dir.exists()
        archives = list(archive_dir.glob("*.jsonl"))
        assert len(archives) == 1

    def test_no_rotation_for_small_current_month_file(self, tmp_dir):
        audit_file = tmp_dir / "data" / "audit_log.jsonl"
        entry = json.dumps({"timestamp": datetime.now().isoformat(), "data": "small"})
        audit_file.write_text(entry + "\n")

        rotate_audit_log(audit_file)
        assert audit_file.exists()  # Should NOT be rotated


# ── backup_data_files ───────────────────────────────────────────────


class TestBackupDataFiles:
    def test_creates_daily_backup(self, tmp_dir):
        data_dir = tmp_dir / "data"
        backup_dir = tmp_dir / "backups"

        # Create some data files
        (data_dir / "customers.json").write_text('{"a": 1}')
        (data_dir / "do_sender_cache.json").write_text('{}')

        result = backup_data_files(data_dir, backup_dir)
        assert result is True

        today = datetime.now().strftime("%Y-%m-%d")
        today_dir = backup_dir / today
        assert today_dir.exists()
        assert (today_dir / "customers.json").exists()
        assert (today_dir / "do_sender_cache.json").exists()

    def test_skips_if_already_backed_up_today(self, tmp_dir):
        data_dir = tmp_dir / "data"
        backup_dir = tmp_dir / "backups"
        (data_dir / "customers.json").write_text('{}')

        # First backup
        assert backup_data_files(data_dir, backup_dir) is True
        # Second backup same day
        assert backup_data_files(data_dir, backup_dir) is False

    def test_skips_non_json_files(self, tmp_dir):
        data_dir = tmp_dir / "data"
        backup_dir = tmp_dir / "backups"
        (data_dir / "customers.json").write_text('{}')
        (data_dir / "readme.txt").write_text('ignore me')

        backup_data_files(data_dir, backup_dir)

        today = datetime.now().strftime("%Y-%m-%d")
        today_dir = backup_dir / today
        assert (today_dir / "customers.json").exists()
        assert not (today_dir / "readme.txt").exists()

    def test_prunes_old_backups(self, tmp_dir):
        data_dir = tmp_dir / "data"
        backup_dir = tmp_dir / "backups"
        (data_dir / "customers.json").write_text('{}')

        # Create a fake old backup
        old_dir = backup_dir / "2020-01-01"
        old_dir.mkdir()
        (old_dir / "customers.json").write_text('{}')
        old_time = time.time() - (60 * 86400)
        import os
        os.utime(old_dir, (old_time, old_time))

        backup_data_files(data_dir, backup_dir, retain_days=30)

        assert not old_dir.exists()

    def test_no_backup_if_no_data_files(self, tmp_dir):
        data_dir = tmp_dir / "data"
        backup_dir = tmp_dir / "backups"
        # data_dir is empty
        result = backup_data_files(data_dir, backup_dir)
        assert result is False
