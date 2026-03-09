"""NGL Agent Server — FastAPI entry point."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from config import (
    HOST, PORT, BASE_DIR, BUNDLE_DIR, ALLOWED_ORIGINS, AUTH_TOKEN, CLAUDE_API_KEY,
    DAILY_API_CALL_LIMIT, GMAIL_ADDRESS, GMAIL_APP_PASSWORD,
    TRANZACT_USERNAME, TRANZACT_PASSWORD,
    DEBUG_DIR, DATA_DIR, BACKUP_DIR, BACKUP_RETAIN_DAYS,
    SELECTORS_FILE, TMS_SELECTORS_FILE,
    WEB_UPDATE_URL, WEBAPP_CACHE_DIR,
)
from routers import jobs, files, qbo, customers, audit, tms, settings
from services.qbo_browser import QBOBrowser
from services.tms_browser import TMSBrowser
from services.claude_classifier import ClaudeClassifier
from services.email_sender import EmailSender
from services.portal_uploader import PortalUploader
from services.job_manager import JobManager

# ── Logging ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ngl.main")

# ── Shared instances ─────────────────────────────────────────────────
qbo_browser = QBOBrowser()
tms_browser = TMSBrowser()
classifier = None
email_sender = None
portal_uploader = None
job_manager = None

# Session alerts — set by keep-alive loop when auto-reconnect fails
_session_alerts = {"qbo_needs_login": False, "tms_needs_login": False}


KEEPALIVE_INTERVAL_S = 300  # 5 minutes between keep-alive pings


def _notify(title: str, message: str) -> None:
    """Send a desktop notification (if enabled)."""
    try:
        from services.notifier import notify
        notify(title, message)
    except Exception:
        pass


async def _session_keepalive_loop():
    """Background task: ping QBO and TMS browsers every 5 minutes to prevent session timeout.

    If a session has expired, automatically navigate to the login page so the
    user sees it immediately and can re-authenticate.
    Also runs a daily backup check (skips if today's backup already exists).
    """
    from datetime import date as _date
    from utils import backup_data_files
    _last_backup_date = _date.today()

    await asyncio.sleep(60)  # Wait 1 min after startup before first check
    while True:
        try:
            # QBO keep-alive
            qbo_alive = await qbo_browser.keep_alive()
            if not qbo_alive:
                logger.warning("QBO session lost — attempting auto-reconnect...")
                try:
                    reconnected = await qbo_browser.auto_login()
                    if reconnected:
                        logger.info("QBO auto-reconnect successful!")
                        _session_alerts["qbo_needs_login"] = False
                    else:
                        logger.warning("QBO auto-reconnect failed — opening login page")
                        _session_alerts["qbo_needs_login"] = True
                        await qbo_browser.open_login_page()
                        _notify("QBO Session Expired", "Auto-reconnect failed. Please log in manually.")
                except Exception as e:
                    logger.error("QBO auto-reconnect error: %s", e)
                    _session_alerts["qbo_needs_login"] = True
                    _notify("QBO Error", f"Session reconnect failed: {e}")

            # TMS keep-alive
            tms_alive = await tms_browser.keep_alive()
            if not tms_alive:
                logger.warning("TMS session lost — attempting auto-reconnect...")
                try:
                    reconnected = await tms_browser.auto_login()
                    if reconnected:
                        logger.info("TMS auto-reconnect successful!")
                        _session_alerts["tms_needs_login"] = False
                    else:
                        logger.warning("TMS auto-reconnect failed — manual login needed")
                        _session_alerts["tms_needs_login"] = True
                        await tms_browser.open_login_page()
                        _notify("TMS Session Expired", "Auto-reconnect failed. Please log in manually.")
                except Exception as e:
                    logger.error("TMS auto-reconnect error: %s", e)
                    _session_alerts["tms_needs_login"] = True
                    _notify("TMS Error", f"Session reconnect failed: {e}")

            # Daily backup check — runs once per new day
            today = _date.today()
            if today != _last_backup_date:
                backup_data_files(DATA_DIR, BACKUP_DIR, BACKUP_RETAIN_DAYS)
                _last_backup_date = today

        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error("Keep-alive loop error: %s", e)

        try:
            await asyncio.sleep(KEEPALIVE_INTERVAL_S)
        except asyncio.CancelledError:
            return


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    global classifier, email_sender, portal_uploader, job_manager

    logger.info("=" * 50)
    logger.info("  NGL Agent Server starting on %s:%d", HOST, PORT)
    logger.info("=" * 50)

    # ── Startup validation — surface missing files immediately ──
    import sys as _sys
    _is_frozen = getattr(_sys, "frozen", False)
    logger.info("Packaged mode: %s", _is_frozen)
    logger.info("BASE_DIR (writable):   %s", BASE_DIR)
    logger.info("BUNDLE_DIR (bundled):  %s", BUNDLE_DIR)
    if not SELECTORS_FILE.exists():
        logger.error("MISSING: %s — QBO automation will fail!", SELECTORS_FILE)
    if not TMS_SELECTORS_FILE.exists():
        logger.error("MISSING: %s — TMS automation will fail!", TMS_SELECTORS_FILE)
    if not CLAUDE_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set — AI classification disabled")
    _pw_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    if _is_frozen and _pw_path and not os.path.isdir(_pw_path):
        logger.error("MISSING: Playwright browsers dir %s — browser automation will fail!", _pw_path)
    elif _is_frozen and _pw_path:
        logger.info("Playwright browsers:   %s", _pw_path)

    # Initialize SQLite database (creates tables + migrates JSON/JSONL on first run)
    from services.database import init_db
    init_db()

    # Housekeeping — clean old debug files, daily backup
    from utils import cleanup_old_debug_files, backup_data_files
    cleanup_old_debug_files(DEBUG_DIR)
    backup_data_files(DATA_DIR, BACKUP_DIR, BACKUP_RETAIN_DAYS)

    # Init Playwright browsers
    await qbo_browser.init()
    logger.info("QBO browser ready")

    try:
        await tms_browser.init()
        logger.info("TMS browser ready")
    except Exception as e:
        logger.error("TMS browser failed to start: %s", e)
        logger.error("Close any Chrome windows using the TMS profile and restart the agent")

    # Init Claude classifier
    if CLAUDE_API_KEY:
        classifier = ClaudeClassifier()
        logger.info("Claude classifier ready (model: configured)")
    else:
        logger.warning("ANTHROPIC_API_KEY not set — document classification disabled")
        logger.warning("Set it in agent/.env to enable AI classification")

    # Init Gmail email sender (OEC flow)
    if GMAIL_ADDRESS and GMAIL_APP_PASSWORD:
        email_sender = EmailSender(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        logger.info("Gmail email sender ready (%s)", GMAIL_ADDRESS)
    else:
        logger.info("Gmail not configured — OEC POD email flow disabled")

    # Init portal uploader (TrueVal flow)
    if TRANZACT_USERNAME and TRANZACT_PASSWORD:
        portal_uploader = PortalUploader(
            browser_context=qbo_browser._context,
            username=TRANZACT_USERNAME,
            password=TRANZACT_PASSWORD,
        )
        logger.info("Portal uploader ready (TranzAct)")
    else:
        logger.info("TranzAct credentials not configured — portal upload flow disabled")

    # Init job manager
    job_manager = JobManager(
        qbo_browser, classifier,
        email_sender=email_sender,
        portal_uploader=portal_uploader,
        tms_browser=tms_browser,
    )
    jobs.set_job_manager(job_manager)
    qbo.set_qbo_browser(qbo_browser)
    tms.set_tms_browser(tms_browser)
    settings.set_browsers(qbo_browser, tms_browser)
    logger.info("Job manager ready")

    # Auto-check sessions — try auto-login if cookies didn't restore
    try:
        qbo_logged_in = await qbo_browser.is_logged_in()
        if qbo_logged_in:
            logger.info("QBO session restored — already logged in!")
        else:
            logger.info("QBO session not active — attempting auto-login...")
            auto_ok = await qbo_browser.auto_login()
            if auto_ok:
                logger.info("QBO auto-login successful!")
            else:
                logger.info("QBO auto-login needs manual step — opening login page")
                await qbo_browser.open_login_page()
    except Exception as e:
        logger.warning("QBO session check failed: %s", e)

    try:
        tms_logged_in = tms_browser.is_logged_in()
        if tms_logged_in:
            logger.info("TMS session restored — already logged in!")
        else:
            logger.info("TMS session not active — attempting auto-login...")
            auto_ok = await tms_browser.auto_login()
            if auto_ok:
                logger.info("TMS auto-login successful!")
            else:
                logger.info("TMS auto-login needs manual step — manual login required")
    except Exception as e:
        logger.warning("TMS session check failed: %s", e)

    logger.info("=" * 50)
    logger.info("  Agent is live! Open http://localhost:%d in your browser.", PORT)
    logger.info("=" * 50)

    # Start background keep-alive task
    keepalive_task = asyncio.create_task(_session_keepalive_loop())

    yield  # App is running

    # Shutdown
    keepalive_task.cancel()
    logger.info("Shutting down agent server...")
    if portal_uploader:
        await portal_uploader.close()
    await tms_browser.close()
    await qbo_browser.close()
    from services.database import close_db
    close_db()
    logger.info("Goodbye!")


# ── FastAPI app ──────────────────────────────────────────────────────
app = FastAPI(
    title="NGL Agent Server",
    version="1.0.0",
    lifespan=lifespan,
)

# No-cache for JS/CSS — prevents stale browser caching during development
class NoCacheStaticMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        path = request.url.path
        if path.endswith(('.js', '.css', '.html')):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        return response


# Auth middleware — validates Bearer token on API routes
# Exempts: health check, auth/token bootstrap, static file serving, and OPTIONS (CORS preflight)
_AUTH_EXEMPT_PREFIXES = ("/health", "/auth/")
_AUTH_EXEMPT_EXACT = frozenset()


class AuthTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Skip auth for: OPTIONS preflight, exempt paths, static assets (served by mount)
        if request.method == "OPTIONS":
            return await call_next(request)
        if any(path.startswith(p) for p in _AUTH_EXEMPT_PREFIXES):
            return await call_next(request)
        # Static files (HTML/JS/CSS/images) are served by the mounted StaticFiles app.
        # API routes all start with known prefixes — check if this is an API route.
        api_prefixes = ("/jobs", "/files", "/qbo", "/tms", "/customers", "/audit", "/settings")
        if not any(path.startswith(p) for p in api_prefixes):
            return await call_next(request)

        # Check Authorization header: "Bearer <token>"
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            if token == AUTH_TOKEN:
                return await call_next(request)

        # Check query parameter (needed for EventSource/SSE which can't set headers)
        token_param = request.query_params.get("token", "")
        if token_param == AUTH_TOKEN:
            return await call_next(request)

        return JSONResponse(status_code=401, content={"detail": "Invalid or missing auth token"})


app.add_middleware(NoCacheStaticMiddleware)
app.add_middleware(AuthTokenMiddleware)

# CORS — allow the HTML app to call us (localhost only)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
app.include_router(jobs.router)
app.include_router(files.router)
app.include_router(qbo.router)
app.include_router(customers.router)
app.include_router(audit.router)
app.include_router(tms.router)
app.include_router(settings.router)


@app.get("/auth/token")
async def get_auth_token():
    """Return the auth token for the web UI to use.

    Protected by CORS — only same-origin requests can reach this.
    The token is then sent as a Bearer header on all subsequent API calls.
    """
    return {"token": AUTH_TOKEN}


@app.get("/health")
async def health():
    """Health check — the web app pings this to see if the agent is running."""
    usage_info = {}
    if classifier:
        usage_info = {
            "api_calls_today": classifier.usage.calls_today,
            "api_limit": DAILY_API_CALL_LIMIT,
            "estimated_cost_today": f"${classifier.usage.cost_today:.4f}",
        }
    # Read and clear session alerts (one-time notifications)
    alerts = dict(_session_alerts)
    _session_alerts["qbo_needs_login"] = False
    _session_alerts["tms_needs_login"] = False

    return {
        "status": "ok",
        "service": "ngl-agent",
        "qbo_browser": "initialized",
        "tms_browser": "initialized",
        "classifier": "ready" if classifier else "no_api_key",
        "session_alerts": alerts,
        **usage_info,
    }


# Serve the web app — MUST be last so API routes (/health, /jobs, etc.) match first
import os as _os
from pathlib import Path as _Path
from services.web_updater import check_for_updates

_bundled_app_dir = _Path(_os.environ.get("NGL_APP_DIR", str(BASE_DIR.parent / "app")))
_app_dir = check_for_updates(_bundled_app_dir, WEBAPP_CACHE_DIR, WEB_UPDATE_URL)

if _app_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_app_dir), html=True), name="webapp")
    logger.info("Serving web app from: %s", _app_dir)
else:
    logger.warning("Web app directory not found: %s — static file serving disabled", _app_dir)


# ── Run ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
        reload=False,
        log_level="info",
    )
