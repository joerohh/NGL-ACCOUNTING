"""QBO connection endpoints — browser login + OAuth API integration."""

import os
import signal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, HTMLResponse

from config import QBO_MODE, QBO_CLIENT_ID

router = APIRouter(prefix="/qbo", tags=["qbo"])

# Injected by main.py on startup
_qbo_browser = None
_qbo_api = None


def set_qbo_browser(qbo):
    global _qbo_browser
    _qbo_browser = qbo


def set_qbo_api(api):
    global _qbo_api
    _qbo_api = api


@router.get("/status")
async def qbo_status():
    """Check QBO connection status — works for both browser and API modes."""
    from config import QBO_MODE

    result = {"mode": QBO_MODE}

    # API status (always report if configured)
    if _qbo_api:
        api_status = _qbo_api.get_status()
        result["api"] = api_status

    # Browser status (always report if initialized)
    if _qbo_browser:
        try:
            url = _qbo_browser.current_url
            logged_in = bool(url) and "qbo.intuit.com" in url and "sign-in" not in url
            result["browser"] = {
                "status": "connected" if logged_in else "login_required",
                "loggedIn": logged_in,
                "currentUrl": url,
            }
        except Exception as e:
            result["browser"] = {"status": "error", "loggedIn": False, "error": str(e)}

    # Top-level convenience fields based on active mode
    if QBO_MODE == "api" and _qbo_api:
        result["loggedIn"] = _qbo_api.is_connected
        result["status"] = "connected" if _qbo_api.is_connected else "api_not_connected"
    elif _qbo_browser:
        browser = result.get("browser", {})
        result["loggedIn"] = browser.get("loggedIn", False)
        result["status"] = browser.get("status", "not_initialized")
    else:
        result["loggedIn"] = False
        result["status"] = "not_initialized"

    return result


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


# ── OAuth 2.0 endpoints (QBO API) ────────────────────────────────────

@router.get("/oauth/connect")
async def oauth_connect():
    """Redirect user to Intuit authorization page."""
    if not _qbo_api:
        raise HTTPException(503, "QBO API client not initialized")
    if not QBO_CLIENT_ID:
        raise HTTPException(400, "QBO_CLIENT_ID not configured in .env")

    auth_url = _qbo_api.token_manager.get_authorization_url()
    return RedirectResponse(auth_url)


@router.get("/oauth/callback")
async def oauth_callback(code: str = "", state: str = "", realmId: str = ""):
    """Handle Intuit OAuth redirect — exchange code for tokens."""
    if not _qbo_api:
        raise HTTPException(503, "QBO API client not initialized")

    if not code:
        return HTMLResponse(
            "<h2>Authorization Failed</h2><p>No authorization code received.</p>",
            status_code=400,
        )

    success = await _qbo_api.token_manager.exchange_code(code, state, realmId)
    if success:
        return HTMLResponse("""
            <html><body style="font-family: sans-serif; text-align: center; padding: 60px;">
                <h2 style="color: #22c55e;">QBO API Connected!</h2>
                <p>Authorization successful. You can close this tab and return to the app.</p>
                <p style="color: #666; font-size: 14px;">Company ID: {realm}</p>
                <script>setTimeout(() => window.close(), 3000);</script>
            </body></html>
        """.format(realm=realmId))
    else:
        return HTMLResponse(
            "<h2>Authorization Failed</h2>"
            "<p>Could not exchange the authorization code for tokens. Check the agent logs.</p>",
            status_code=400,
        )


@router.get("/oauth/authorize")
async def oauth_authorize_page():
    """Show a page with instructions to paste the redirect URL after authorizing."""
    if not _qbo_api:
        raise HTTPException(503, "QBO API client not initialized")
    if not QBO_CLIENT_ID:
        raise HTTPException(400, "QBO_CLIENT_ID not configured in .env")

    auth_url = _qbo_api.token_manager.get_authorization_url()
    return HTMLResponse(f"""
    <html>
    <head><title>Connect QBO API</title></head>
    <body style="font-family: -apple-system, sans-serif; max-width: 600px; margin: 60px auto; padding: 0 20px;">
        <h2 style="color: #0f172a;">Connect QBO API</h2>

        <div style="background: #f0f9ff; border: 1px solid #bae6fd; border-radius: 10px; padding: 20px; margin-bottom: 24px;">
            <p style="margin: 0 0 12px; font-weight: 600; color: #0369a1;">Step 1: Authorize</p>
            <p style="margin: 0 0 12px; color: #334155; font-size: 0.9rem;">
                Click the button below to open Intuit's authorization page. Sign in and click <strong>Connect</strong>.
            </p>
            <a href="{auth_url}" target="_blank"
               style="display: inline-block; background: #16a34a; color: #fff; padding: 10px 24px;
                      border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 0.9rem;">
                Open Intuit Authorization
            </a>
        </div>

        <div style="background: #fff7ed; border: 1px solid #fed7aa; border-radius: 10px; padding: 20px; margin-bottom: 24px;">
            <p style="margin: 0 0 12px; font-weight: 600; color: #c2410c;">Step 2: Paste the redirect URL</p>
            <p style="margin: 0 0 12px; color: #334155; font-size: 0.9rem;">
                After you click Connect, you'll be redirected to a page. <strong>Copy the entire URL</strong>
                from your browser's address bar and paste it below.
            </p>
            <input type="text" id="redirectUrl" placeholder="Paste the full URL here..."
                   style="width: 100%; padding: 10px 14px; border: 1px solid #d1d5db; border-radius: 8px;
                          font-size: 0.88rem; box-sizing: border-box; margin-bottom: 12px;" />
            <button onclick="submitUrl()"
                    style="background: #ea580c; color: #fff; padding: 10px 24px; border: none;
                           border-radius: 8px; font-weight: 600; font-size: 0.9rem; cursor: pointer;"
                    id="submitBtn">
                Complete Connection
            </button>
            <p id="resultMsg" style="display: none; margin: 12px 0 0; padding: 10px; border-radius: 8px; font-size: 0.85rem;"></p>
        </div>

        <script>
        async function submitUrl() {{
            const url = document.getElementById('redirectUrl').value.trim();
            const btn = document.getElementById('submitBtn');
            const msg = document.getElementById('resultMsg');
            if (!url) {{ msg.textContent = 'Please paste the URL first.'; msg.style.display=''; msg.style.color='#dc2626'; return; }}

            // Parse the URL params
            let params;
            try {{ params = new URL(url).searchParams; }} catch(e) {{
                msg.textContent = 'Invalid URL. Copy the full URL from the address bar.';
                msg.style.display = ''; msg.style.color = '#dc2626'; return;
            }}
            const code = params.get('code');
            const state = params.get('state');
            const realmId = params.get('realmId');
            if (!code) {{
                msg.textContent = 'No authorization code found in the URL. Make sure you clicked Connect on Intuit\\'s page.';
                msg.style.display = ''; msg.style.color = '#dc2626'; return;
            }}

            btn.disabled = true; btn.textContent = 'Connecting...';
            try {{
                const resp = await fetch('/qbo/oauth/callback?code=' + encodeURIComponent(code) +
                    '&state=' + encodeURIComponent(state || '') +
                    '&realmId=' + encodeURIComponent(realmId || ''));
                const text = await resp.text();
                if (resp.ok && text.includes('Connected')) {{
                    msg.innerHTML = '<strong style="color:#16a34a;">QBO API Connected!</strong> You can close this tab.';
                    msg.style.display = ''; msg.style.background = '#f0fdf4'; msg.style.border = '1px solid #bbf7d0';
                }} else {{
                    msg.textContent = 'Connection failed. Check the agent logs for details.';
                    msg.style.display = ''; msg.style.color = '#dc2626';
                }}
            }} catch(e) {{
                msg.textContent = 'Error: ' + e.message;
                msg.style.display = ''; msg.style.color = '#dc2626';
            }}
            btn.disabled = false; btn.textContent = 'Complete Connection';
        }}
        </script>
    </body>
    </html>
    """)


@router.get("/oauth/status")
async def oauth_status():
    """Check if QBO API tokens are valid."""
    if not _qbo_api:
        return {"connected": False, "reason": "QBO API client not initialized"}
    return _qbo_api.get_status()


@router.post("/oauth/disconnect")
async def oauth_disconnect():
    """Revoke QBO API tokens and disconnect."""
    if not _qbo_api:
        raise HTTPException(503, "QBO API client not initialized")

    await _qbo_api.token_manager.revoke()
    return {"status": "disconnected", "message": "QBO API tokens revoked."}
