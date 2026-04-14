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
_job_manager = None


def set_tms_browser(tms):
    global _tms_browser
    _tms_browser = tms


def set_job_manager(jm):
    global _job_manager
    _job_manager = jm


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


# ── Email (Gmail) Settings ──

class EmailConfigUpdate(BaseModel):
    gmail_address: Optional[str] = None
    gmail_app_password: Optional[str] = None


class EmailTestRequest(BaseModel):
    gmail_address: Optional[str] = None      # if provided, test these creds (without saving)
    gmail_app_password: Optional[str] = None
    to: Optional[str] = None                 # defaults to gmail_address (send to self)


@router.get("/email")
async def get_email_config():
    """Return whether Gmail is configured (never returns the password)."""
    from config import GMAIL_ADDRESS, GMAIL_APP_PASSWORD
    return {
        "configured": bool(GMAIL_ADDRESS and GMAIL_APP_PASSWORD),
        "gmail_address": GMAIL_ADDRESS or "",
    }


@router.post("/email")
async def save_email_config(data: EmailConfigUpdate):
    """Save Gmail credentials and rebuild the EmailSender immediately."""
    env_path = APPDATA_DIR / ".env"

    if data.gmail_address is not None:
        update_env_file("GMAIL_ADDRESS", data.gmail_address.strip(), env_path)
    if data.gmail_app_password is not None:
        # Strip spaces — Google displays App Passwords with spaces, but they don't belong
        cleaned = data.gmail_app_password.replace(" ", "")
        update_env_file("GMAIL_APP_PASSWORD", cleaned, env_path)

    reload_env_credentials()

    # Rebuild the EmailSender on the live job_manager
    from config import GMAIL_ADDRESS, GMAIL_APP_PASSWORD
    if _job_manager and GMAIL_ADDRESS and GMAIL_APP_PASSWORD:
        from services.email_sender import EmailSender
        _job_manager.set_email_sender(EmailSender(GMAIL_ADDRESS, GMAIL_APP_PASSWORD))
        logger.info("EmailSender rebuilt for %s", GMAIL_ADDRESS)
        return {"status": "saved", "configured": True, "gmail_address": GMAIL_ADDRESS}
    return {"status": "saved", "configured": False, "gmail_address": GMAIL_ADDRESS}


@router.post("/email/test")
async def test_email_config(data: EmailTestRequest):
    """Send a test email to verify Gmail credentials work.

    If gmail_address/gmail_app_password are provided in the body, those are tested
    (without persisting). Otherwise the saved credentials are used.
    """
    from config import GMAIL_ADDRESS, GMAIL_APP_PASSWORD
    address = (data.gmail_address or GMAIL_ADDRESS or "").strip()
    password = (data.gmail_app_password or GMAIL_APP_PASSWORD or "").replace(" ", "")
    to_address = (data.to or address).strip()

    if not address or not password:
        return {"sent": False, "error": "Gmail address and App Password are required."}
    if not to_address:
        return {"sent": False, "error": "No recipient address."}

    from services.email_sender import EmailSender
    sender = EmailSender(address, password)
    result = await sender.send_invoice_email(
        to=[to_address],
        subject="NGL Accounting — Test Email",
        body=(
            f"This is a test email from your NGL Accounting app.\n\n"
            f"If you received this, your Gmail sending is configured correctly.\n\n"
            f"Sent from: {address}\n"
        ),
        attachments=[],
    )
    return result
