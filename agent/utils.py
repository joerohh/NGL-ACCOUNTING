"""Shared utility functions for the NGL agent."""

import json
import logging
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("ngl.utils")


def strip_motw(file_path: Path) -> None:
    """Remove the Zone.Identifier alternate data stream (Mark of the Web) from a file.

    Windows adds this ADS to files downloaded from the internet, which triggers
    'this file is potentially harmful' warnings. Since our agent downloads
    legitimate invoices from QBO, we strip it automatically.
    """
    try:
        os.remove(str(file_path) + ":Zone.Identifier")
    except OSError:
        pass  # No Zone.Identifier present — nothing to do


def kill_chrome_with_profile(profile_dir: Path) -> None:
    """Kill any Chrome processes using a specific user-data-dir.

    When Chrome is left running (e.g. after a crash or agent restart),
    it locks the profile directory and prevents Playwright from reusing it.
    This finds those orphaned processes via PowerShell and terminates them.
    """
    try:
        needle = str(profile_dir).replace("'", "''")
        cmd = (
            "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
            "Where-Object { $_.CommandLine -and $_.CommandLine.Contains('"
            + needle
            + "') } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True, timeout=15,
        )
        if result.returncode == 0:
            logger.info("Cleaned up orphaned Chrome processes for %s", profile_dir.name)
    except Exception as e:
        logger.debug("Chrome cleanup skipped: %s", e)


def kill_orphaned_playwright_chrome() -> None:
    """Kill orphaned Chrome processes spawned by Playwright.

    Since SharedBrowser uses launch() (not launch_persistent_context()),
    Chrome doesn't have --user-data-dir in its command line. Instead,
    we identify Playwright-spawned Chrome by the --disable-blink-features
    flag (which normal user Chrome doesn't have).

    Only kills Chrome processes whose parent PID no longer exists
    (truly orphaned — not Chrome actively used by a running agent).
    """
    try:
        cmd = (
            "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
            "Where-Object { $_.CommandLine -and "
            "$_.CommandLine.Contains('--disable-blink-features=AutomationControlled') -and "
            "(-not (Get-Process -Id $_.ParentProcessId -ErrorAction SilentlyContinue)) } | "
            "ForEach-Object { "
            "Write-Output \"Killing orphaned Playwright Chrome PID $($_.ProcessId)\"; "
            "Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True, timeout=15, text=True,
        )
        if result.stdout.strip():
            logger.info("Cleaned up orphaned Playwright Chrome: %s", result.stdout.strip())
        else:
            logger.debug("No orphaned Playwright Chrome processes found")
    except Exception as e:
        logger.debug("Playwright Chrome cleanup skipped: %s", e)


def save_cookies(context, cookie_file: Path) -> None:
    """Save browser cookies to a JSON file for session persistence."""
    try:
        import asyncio
        cookies = asyncio.get_event_loop().run_until_complete(context.cookies())
        cookie_file.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
        logger.info("Saved %d cookies to %s", len(cookies), cookie_file.name)
    except Exception as e:
        logger.warning("Could not save cookies: %s", e)


async def save_cookies_async(context, cookie_file: Path) -> None:
    """Save browser cookies to a JSON file (async version)."""
    try:
        cookies = await context.cookies()
        cookie_file.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
        logger.info("Saved %d cookies to %s", len(cookies), cookie_file.name)
    except Exception as e:
        logger.warning("Could not save cookies: %s", e)


def update_env_file(key: str, value: str, env_path: Path = None) -> None:
    """Update or add a key=value pair in the .env file."""
    if env_path is None:
        from config import APPDATA_DIR
        env_path = APPDATA_DIR / ".env"

    lines = []
    found = False
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} ="):
                lines[i] = f"{key}={value}\n"
                found = True
                break

    if not found:
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.append(f"{key}={value}\n")

    env_path.write_text("".join(lines), encoding="utf-8")
    logger.info("Updated .env: %s=%s", key, "***" if "PASSWORD" in key.upper() else value)


def reload_env_credentials():
    """Re-read .env and update config module globals for credential fields."""
    import config as _cfg
    from dotenv import load_dotenv as _load
    _load(_cfg.APPDATA_DIR / ".env", override=True)
    _cfg.TMS_EMAIL = os.getenv("TMS_EMAIL", "")
    _cfg.TMS_PASSWORD = os.getenv("TMS_PASSWORD", "")
    _cfg.GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "")
    _cfg.GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")


async def restore_cookies(context, cookie_file: Path) -> int:
    """Load cookies from a JSON file back into the browser context.

    Returns the number of cookies restored, or 0 if file missing / error.
    """
    if not cookie_file.exists():
        return 0
    try:
        cookies = json.loads(cookie_file.read_text(encoding="utf-8"))
        if cookies:
            await context.add_cookies(cookies)
            logger.info("Restored %d cookies from %s", len(cookies), cookie_file.name)
            return len(cookies)
    except Exception as e:
        logger.warning("Could not restore cookies: %s", e)
    return 0


def cleanup_old_profiles() -> None:
    """Remove dead Chrome profile cache from old launch_persistent_context() usage.

    Since we switched to SharedBrowser (browser.new_context()), the profile
    directories only need the _session_cookies.json file. Everything else
    (Chrome cache, GPU cache, Local Storage, etc.) is dead weight.
    Runs once on startup to reclaim disk space (can be 100-300 MB).
    """
    from config import TMS_PROFILE_DIR
    for profile_dir in [TMS_PROFILE_DIR]:
        if not profile_dir.exists():
            continue
        cleaned = 0
        for item in list(profile_dir.iterdir()):
            if item.name == "_session_cookies.json":
                continue  # keep the cookie file
            try:
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink()
                cleaned += 1
            except OSError:
                pass
        if cleaned:
            logger.info("Cleaned %d old profile items from %s", cleaned, profile_dir.name)


def cleanup_old_debug_files(debug_dir: Path, max_age_days: int = 7) -> int:
    """Delete debug screenshots/HTML older than max_age_days.

    Returns the number of files deleted.
    """
    if not debug_dir.exists():
        return 0
    cutoff = time.time() - (max_age_days * 86400)
    deleted = 0
    for f in debug_dir.rglob("*"):
        if f.is_file() and f.stat().st_mtime < cutoff:
            try:
                f.unlink()
                deleted += 1
            except OSError:
                pass
    if deleted:
        logger.info("Cleaned up %d debug files older than %d days", deleted, max_age_days)
    return deleted


def rotate_audit_log(audit_file: Path, archive_dir: Path = None) -> None:
    """Rotate audit_log.jsonl if it exceeds 1 MB or has entries from a previous month.

    Moves the current file to archive_dir/audit_log_YYYY-MM.jsonl.
    """
    if not audit_file.exists():
        return
    if archive_dir is None:
        archive_dir = audit_file.parent / "archive"

    needs_rotation = False

    # Check size
    if audit_file.stat().st_size > 1_000_000:
        needs_rotation = True
    else:
        # Check if first entry is from a previous month
        try:
            with open(audit_file, "r", encoding="utf-8") as f:
                first_line = f.readline().strip()
            if first_line:
                entry = json.loads(first_line)
                ts = entry.get("timestamp", "")
                if ts:
                    entry_date = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    now = datetime.now(entry_date.tzinfo) if entry_date.tzinfo else datetime.now()
                    if (entry_date.year, entry_date.month) != (now.year, now.month):
                        needs_rotation = True
        except (json.JSONDecodeError, ValueError, KeyError):
            pass

    if not needs_rotation:
        return

    archive_dir.mkdir(exist_ok=True)
    now = datetime.now()
    # Use previous month for the archive filename
    archive_name = f"audit_log_{now.year}-{now.month - 1:02d}.jsonl" if now.month > 1 \
        else f"audit_log_{now.year - 1}-12.jsonl"
    dest = archive_dir / archive_name

    # If archive already exists, append instead of overwrite
    if dest.exists():
        with open(dest, "a", encoding="utf-8") as out, \
             open(audit_file, "r", encoding="utf-8") as inp:
            out.write(inp.read())
        audit_file.unlink()
    else:
        shutil.move(str(audit_file), str(dest))

    logger.info("Rotated audit log → %s", dest.name)


def backup_data_files(
    data_dir: Path,
    backup_dir: Path,
    retain_days: int = 30,
) -> bool:
    """Create a daily backup of all data files.

    Copies customers.json, audit_log.jsonl, and do_sender_cache.json into
    a timestamped subfolder under backup_dir. Skips if today's backup already
    exists. Deletes backups older than retain_days.

    Returns True if a new backup was created.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    today_dir = backup_dir / today

    # Skip if today's backup already exists
    if today_dir.exists():
        logger.debug("Backup already exists for %s — skipping", today)
        return False

    # Collect files to back up
    files_to_backup = [
        f for f in data_dir.iterdir()
        if f.is_file() and f.suffix in (".json", ".jsonl", ".db")
    ]
    if not files_to_backup:
        logger.debug("No data files to back up")
        return False

    # Create today's backup
    today_dir.mkdir(parents=True, exist_ok=True)
    for f in files_to_backup:
        shutil.copy2(str(f), str(today_dir / f.name))

    logger.info(
        "Backed up %d data files to %s",
        len(files_to_backup), today_dir.name,
    )

    # Prune old backups
    cutoff = time.time() - (retain_days * 86400)
    for d in backup_dir.iterdir():
        if d.is_dir() and d != today_dir and d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            logger.info("Deleted old backup: %s", d.name)

    return True
