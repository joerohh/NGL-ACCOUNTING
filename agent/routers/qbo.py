"""QBO connection endpoints — login status and manual login trigger."""

import os
import signal

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/qbo", tags=["qbo"])

# Injected by main.py on startup
_qbo_browser = None


def set_qbo_browser(qbo):
    global _qbo_browser
    _qbo_browser = qbo


@router.get("/status")
async def qbo_status():
    """Passive check — just reads the current URL without navigating the browser."""
    if not _qbo_browser:
        return {"status": "not_initialized", "loggedIn": False}

    try:
        url = _qbo_browser.current_url
        # If we're on a QBO app page (not sign-in), consider logged in
        logged_in = bool(url) and "qbo.intuit.com" in url and "sign-in" not in url
        return {
            "status": "connected" if logged_in else "login_required",
            "loggedIn": logged_in,
            "currentUrl": url,
        }
    except Exception as e:
        return {
            "status": "error",
            "loggedIn": False,
            "error": str(e),
        }


@router.post("/open-login")
async def open_qbo_login():
    """Open the QBO login page in the agent's browser for manual authentication."""
    if not _qbo_browser:
        raise HTTPException(503, "QBO browser not initialized")

    try:
        url = await _qbo_browser.open_login_page()
        return {
            "status": "login_page_opened",
            "url": url,
            "message": "Please log into QuickBooks in the Chrome window that opened.",
        }
    except Exception as e:
        raise HTTPException(500, f"Failed to open login page: {e}")


@router.post("/wait-for-login")
async def wait_for_qbo_login():
    """Wait for the user to complete manual QBO login (up to 2 minutes)."""
    if not _qbo_browser:
        raise HTTPException(503, "QBO browser not initialized")

    try:
        success = await _qbo_browser.wait_for_login(timeout_s=120)
        if success:
            return {"status": "logged_in", "message": "QBO login successful!"}
        else:
            return {"status": "timeout", "message": "Login timed out. Please try again."}
    except Exception as e:
        raise HTTPException(500, f"Error waiting for login: {e}")


@router.get("/selector-health")
async def qbo_selector_health():
    """Check if critical QBO DOM selectors are present on the current page."""
    from services.health_check import check_qbo_selectors
    return await check_qbo_selectors(_qbo_browser)


@router.post("/shutdown")
async def shutdown_server():
    """Gracefully shut down the agent server so Chrome saves cookies/session properly."""
    # Send SIGINT to ourselves — this triggers FastAPI's lifespan shutdown
    # which calls qbo_browser.close() → Chrome exits cleanly → cookies saved
    os.kill(os.getpid(), signal.SIGINT)
    return {"status": "shutting_down", "message": "Server shutting down gracefully..."}
