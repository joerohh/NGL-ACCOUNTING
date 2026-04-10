"""Settings endpoints — credential management for auto-login."""

import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from config import APPDATA_DIR
from utils import update_env_file, reload_env_credentials

logger = logging.getLogger("ngl.settings")

router = APIRouter(prefix="/settings", tags=["settings"])

# Injected by main.py at startup
_tms_browser = None


def set_tms_browser(tms):
    global _tms_browser
    _tms_browser = tms


class CredentialUpdate(BaseModel):
    tms_email: Optional[str] = None
    tms_password: Optional[str] = None


@router.get("/credentials")
async def get_credentials():
    """Return which credentials are configured (never returns actual passwords)."""
    from config import TMS_EMAIL, TMS_PASSWORD
    return {
        "tms_configured": bool(TMS_EMAIL and TMS_PASSWORD),
        "tms_email": TMS_EMAIL if TMS_EMAIL else "",
    }


@router.post("/credentials")
async def save_credentials(data: CredentialUpdate):
    """Save credentials to .env without triggering login."""
    env_path = APPDATA_DIR / ".env"

    if data.tms_email is not None:
        update_env_file("TMS_EMAIL", data.tms_email, env_path)
    if data.tms_password is not None:
        update_env_file("TMS_PASSWORD", data.tms_password, env_path)

    reload_env_credentials()
    return {"status": "saved", "message": "Credentials saved to .env"}


@router.post("/credentials/connect")
async def save_and_connect(data: CredentialUpdate):
    """Save credentials AND immediately attempt auto-login for TMS."""
    env_path = APPDATA_DIR / ".env"

    if data.tms_email is not None:
        update_env_file("TMS_EMAIL", data.tms_email, env_path)
    if data.tms_password is not None:
        update_env_file("TMS_PASSWORD", data.tms_password, env_path)

    reload_env_credentials()
    results = {}

    # TMS auto-login
    if data.tms_email and data.tms_password and _tms_browser:
        try:
            tms_ok = await _tms_browser.auto_login(data.tms_email, data.tms_password)
            results["tms"] = "logged_in" if tms_ok else "needs_manual_login"
        except Exception as e:
            logger.error("TMS auto-login via settings failed: %s", e)
            results["tms"] = f"error: {e}"

    return {"status": "saved_and_connecting", "results": results}


# ── Notification Settings ──

class NotificationUpdate(BaseModel):
    enabled: bool


@router.get("/notifications")
async def get_notification_settings():
    """Return current notification state."""
    from services.notifier import is_enabled
    return {"enabled": is_enabled()}


@router.post("/notifications")
async def update_notification_settings(data: NotificationUpdate):
    """Enable or disable desktop notifications."""
    from services.notifier import set_enabled
    set_enabled(data.enabled)
    return {"status": "ok", "enabled": data.enabled}
