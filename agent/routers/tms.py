"""TMS connection endpoints — login status and manual login trigger."""

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/tms", tags=["tms"])

# Injected by main.py on startup
_tms_browser = None


def set_tms_browser(tms):
    global _tms_browser
    _tms_browser = tms


@router.get("/status")
async def tms_status():
    """Passive check — just reads the current URL without navigating."""
    if not _tms_browser:
        return {"status": "not_configured", "loggedIn": False}

    try:
        url = _tms_browser.current_url
        logged_in = _tms_browser.is_logged_in()
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
async def open_tms_login():
    """Open the TMS login page for manual Google SSO authentication."""
    if not _tms_browser:
        raise HTTPException(503, "TMS browser not initialized")

    try:
        url = await _tms_browser.open_login_page()
        return {
            "status": "login_page_opened",
            "url": url,
            "message": "Please log into TMS via Google SSO in the Chrome window.",
        }
    except Exception as e:
        raise HTTPException(500, f"Failed to open TMS login page: {e}")


@router.post("/wait-for-login")
async def wait_for_tms_login():
    """Wait for the user to complete Google SSO login (up to 2 minutes)."""
    if not _tms_browser:
        raise HTTPException(503, "TMS browser not initialized")

    try:
        success = await _tms_browser.wait_for_login(timeout_s=120)
        if success:
            return {"status": "logged_in", "message": "TMS login successful!"}
        else:
            return {"status": "timeout", "message": "Login timed out. Please try again."}
    except Exception as e:
        raise HTTPException(500, f"Error waiting for TMS login: {e}")


@router.post("/test-search/{container}")
async def test_search(container: str):
    """Test endpoint: search TMS for a container and return the work order URL."""
    if not _tms_browser:
        raise HTTPException(503, "TMS browser not initialized")
    if not _tms_browser.is_logged_in():
        raise HTTPException(400, "TMS not logged in — log in first via /tms/open-login")

    try:
        url = await _tms_browser.search_container(container)
        return {"container": container, "work_order_url": url, "found": url is not None}
    except Exception as e:
        raise HTTPException(500, f"TMS search failed: {e}")


@router.post("/test-do-sender/{container}")
async def test_do_sender(container: str):
    """Test endpoint: fetch D/O sender email for a container."""
    if not _tms_browser:
        raise HTTPException(503, "TMS browser not initialized")
    if not _tms_browser.is_logged_in():
        raise HTTPException(400, "TMS not logged in")

    try:
        email = await _tms_browser.fetch_do_sender_email(container)
        return {"container": container, "do_sender_email": email, "found": email is not None}
    except Exception as e:
        raise HTTPException(500, f"D/O sender lookup failed: {e}")
