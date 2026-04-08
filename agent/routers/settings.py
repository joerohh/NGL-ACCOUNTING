"""Settings endpoints — credential management for auto-login."""

import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from config import BASE_DIR
from utils import update_env_file, reload_env_credentials

logger = logging.getLogger("ngl.settings")

router = APIRouter(prefix="/settings", tags=["settings"])

# Injected by main.py at startup
_qbo_browser = None
_tms_browser = None


def set_browsers(qbo, tms):
    global _qbo_browser, _tms_browser
    _qbo_browser = qbo
    _tms_browser = tms


class CredentialUpdate(BaseModel):
    qbo_email: Optional[str] = None
    qbo_password: Optional[str] = None
    tms_email: Optional[str] = None
    tms_password: Optional[str] = None


@router.get("/credentials")
async def get_credentials():
    """Return which credentials are configured (never returns actual passwords)."""
    from config import QBO_EMAIL, QBO_PASSWORD, TMS_EMAIL, TMS_PASSWORD
    return {
        "qbo_configured": bool(QBO_EMAIL and QBO_PASSWORD),
        "qbo_email": QBO_EMAIL if QBO_EMAIL else "",
        "tms_configured": bool(TMS_EMAIL and TMS_PASSWORD),
        "tms_email": TMS_EMAIL if TMS_EMAIL else "",
    }


@router.post("/credentials")
async def save_credentials(data: CredentialUpdate):
    """Save credentials to .env without triggering login."""
    env_path = BASE_DIR / ".env"

    if data.qbo_email is not None:
        update_env_file("QBO_EMAIL", data.qbo_email, env_path)
    if data.qbo_password is not None:
        update_env_file("QBO_PASSWORD", data.qbo_password, env_path)
    if data.tms_email is not None:
        update_env_file("TMS_EMAIL", data.tms_email, env_path)
    if data.tms_password is not None:
        update_env_file("TMS_PASSWORD", data.tms_password, env_path)

    reload_env_credentials()
    return {"status": "saved", "message": "Credentials saved to .env"}


@router.post("/credentials/connect")
async def save_and_connect(data: CredentialUpdate):
    """Save credentials AND immediately attempt auto-login for each service."""
    env_path = BASE_DIR / ".env"

    if data.qbo_email is not None:
        update_env_file("QBO_EMAIL", data.qbo_email, env_path)
    if data.qbo_password is not None:
        update_env_file("QBO_PASSWORD", data.qbo_password, env_path)
    if data.tms_email is not None:
        update_env_file("TMS_EMAIL", data.tms_email, env_path)
    if data.tms_password is not None:
        update_env_file("TMS_PASSWORD", data.tms_password, env_path)

    reload_env_credentials()
    results = {}

    # QBO auto-login
    if data.qbo_email and data.qbo_password and _qbo_browser:
        try:
            qbo_ok = await _qbo_browser.auto_login(data.qbo_email, data.qbo_password)
            results["qbo"] = "logged_in" if qbo_ok else "needs_manual_login"
        except Exception as e:
            logger.error("QBO auto-login via settings failed: %s", e)
            results["qbo"] = f"error: {e}"

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


# ── QBO Mode ──

class QboModeUpdate(BaseModel):
    mode: str  # "browser" or "api"


@router.post("/qbo-mode")
async def set_qbo_mode(data: QboModeUpdate):
    """Switch between QBO browser automation and API mode."""
    if data.mode not in ("browser", "api"):
        return {"error": "Invalid mode. Use 'browser' or 'api'."}

    env_path = BASE_DIR / ".env"
    update_env_file("QBO_MODE", data.mode, env_path)

    # Update the runtime config value
    import config
    config.QBO_MODE = data.mode

    logger.info("QBO mode switched to: %s", data.mode)
    return {"status": "ok", "mode": data.mode}
