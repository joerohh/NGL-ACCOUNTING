"""Desktop notification service — Windows toast notifications via PowerShell.

No external dependencies required. Uses PowerShell's .NET WinForms API
which is available on all Windows 10/11 systems.

Usage:
    from services.notifier import notify
    notify("Session Expired", "QBO session needs manual re-login")
"""

import logging
import subprocess
import threading

logger = logging.getLogger("ngl.notifier")

# Global toggle — set via /settings/notifications endpoint
_enabled = False


def set_enabled(enabled: bool) -> None:
    """Enable or disable desktop notifications."""
    global _enabled
    _enabled = enabled
    logger.info("Desktop notifications %s", "enabled" if enabled else "disabled")


def is_enabled() -> bool:
    return _enabled


def notify(title: str, message: str, *, force: bool = False) -> None:
    """Show a Windows toast notification.

    Args:
        title: Notification title (short).
        message: Notification body text.
        force: If True, show even when notifications are disabled (for critical errors).
    """
    if not force and not _enabled:
        return

    # Run in a thread to avoid blocking the async event loop
    threading.Thread(
        target=_show_toast,
        args=(title, message),
        daemon=True,
    ).start()


def _show_toast(title: str, message: str) -> None:
    """Actually show the toast via PowerShell (runs in background thread)."""
    try:
        # Escape for PowerShell single-quoted strings
        safe_title = title.replace("'", "''")
        safe_msg = message.replace("'", "''")

        # Use BalloonTip via NotifyIcon — works on Windows 10/11
        ps_script = f"""
Add-Type -AssemblyName System.Windows.Forms
$icon = New-Object System.Windows.Forms.NotifyIcon
$icon.Icon = [System.Drawing.SystemIcons]::Information
$icon.Visible = $true
$icon.BalloonTipTitle = '{safe_title}'
$icon.BalloonTipText = '{safe_msg}'
$icon.BalloonTipIcon = 'Info'
$icon.ShowBalloonTip(5000)
Start-Sleep -Seconds 6
$icon.Dispose()
"""
        subprocess.run(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script],
            capture_output=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception as e:
        logger.debug("Toast notification failed: %s", e)
