"""Shared utility functions for the NGL agent."""

import json
import logging
import os
import subprocess
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
