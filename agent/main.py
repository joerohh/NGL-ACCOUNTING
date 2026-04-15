"""NGL Agent Server — FastAPI entry point."""

import asyncio
import logging
import os
import socket
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import JSONResponse

from config import (
    HOST, PORT, BASE_DIR, BUNDLE_DIR, APPDATA_DIR, ALLOWED_ORIGINS, CLAUDE_API_KEY,
    DAILY_API_CALL_LIMIT, GMAIL_ADDRESS, GMAIL_APP_PASSWORD,
    TRANZACT_USERNAME, TRANZACT_PASSWORD,
    DEBUG_DIR, DATA_DIR, BACKUP_DIR, BACKUP_RETAIN_DAYS,
    TMS_SELECTORS_FILE,
    WEB_UPDATE_URL, WEBAPP_CACHE_DIR,
)
from routers import auth, jobs, files, qbo, customers, audit, tms, settings
from services.shared_browser import SharedBrowser
from services.qbo_api import QBOApiClient
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

# Bundle version — baked in at build time, visible via /health for diagnosing stale bundles.
AGENT_VERSION = "2.25.0"


# ── Global exception handler — prevents silent crashes ──────────────
def _handle_unhandled_exception(loop, context):
    """Catch unhandled async exceptions so they don't crash the server."""
    exc = context.get("exception")
    msg = context.get("message", "")
    if exc:
        logger.error("Unhandled async exception: %s — %s", msg, exc, exc_info=exc)
    else:
        logger.error("Unhandled async error: %s", msg)

# ── Shared instances ─────────────────────────────────────────────────
shared_browser = SharedBrowser()
qbo_api = QBOApiClient()
tms_browser = TMSBrowser()
classifier = None
email_sender = None
portal_uploader = None
job_manager = None

# Session alerts — set by keep-alive loop when auto-reconnect fails
_session_alerts = {"tms_needs_login": False}


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
            # TMS keep-alive — skip if TMS context hasn't been created yet (lazy init)
            if not tms_browser.is_initialized:
                pass  # TMS not active yet — nothing to keep alive
            elif not (tms_alive := await tms_browser.keep_alive()):
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


def _db_is_empty(db_path):
    """Check if a DB file is missing, tiny, or has no customers."""
    if not db_path.exists():
        return True
    if db_path.stat().st_size < 1024:  # less than 1KB = basically empty
        return True
    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        count = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        conn.close()
        return count == 0
    except Exception:
        return True


def _migrate_data_to_appdata():
    """One-time migration: seed DB from bundled data or copy from old install dir."""
    import shutil
    new_data = APPDATA_DIR / "data"
    new_data.mkdir(parents=True, exist_ok=True)

    existing_db = new_data / "ngl.db"

    if _db_is_empty(existing_db):
        # Try 1: copy from old install dir (upgrade from pre-1.2)
        old_data = BASE_DIR / "data"
        if old_data.is_dir() and (old_data / "ngl.db").exists() and not _db_is_empty(old_data / "ngl.db"):
            logger.info("Migrating data from old install dir: %s", old_data)
            for f in old_data.iterdir():
                dest = new_data / f.name
                if f.is_file():
                    shutil.copy2(f, dest)
                elif f.is_dir() and not dest.exists():
                    shutil.copytree(f, dest)
                logger.info("  Copied: %s", f.name)
        # Try 2: seed from bundled seed-data (fresh install)
        else:
            seed_db = BUNDLE_DIR / "seed-data" / "ngl.db"
            if seed_db.exists():
                logger.info("Seeding database from bundled data: %s", seed_db)
                shutil.copy2(seed_db, new_data / "ngl.db")
            else:
                logger.warning("No seed database found at %s", seed_db)

    # Migrate .env credentials (prevents silent credential loss on upgrade)
    old_env = BASE_DIR / ".env"
    new_env = APPDATA_DIR / ".env"
    if old_env.exists() and not new_env.exists():
        try:
            shutil.copy2(old_env, new_env)
            old_env.rename(old_env.with_suffix(".env.bak"))
            logger.info("Migrated .env credentials to AppData: %s", new_env)
            # Reload so this process picks up the migrated values
            from dotenv import load_dotenv
            load_dotenv(new_env, override=True)
        except Exception as e:
            logger.error("Failed to migrate .env to AppData: %s — credentials may be missing!", e)

    # Migrate browser profiles
    for name in [".tms_browser_profile"]:
        old = BASE_DIR / name
        new = APPDATA_DIR / name
        if old.is_dir() and not new.exists():
            logger.info("Migrating %s to AppData...", name)
            shutil.copytree(old, new)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    global classifier, email_sender, portal_uploader, job_manager

    # Install global exception handler to catch unhandled async errors
    # (e.g., Playwright internal failures) instead of crashing the server
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(_handle_unhandled_exception)

    logger.info("=" * 50)
    logger.info("  NGL Agent Server starting on %s:%d", HOST, PORT)
    logger.info("=" * 50)

    # ── Startup validation — surface missing files immediately ──
    import sys as _sys
    _is_frozen = getattr(_sys, "frozen", False)
    logger.info("Packaged mode: %s", _is_frozen)
    logger.info("BASE_DIR (install):    %s", BASE_DIR)
    logger.info("APPDATA_DIR (persist): %s", APPDATA_DIR)
    logger.info("BUNDLE_DIR (bundled):  %s", BUNDLE_DIR)

    # ── One-time migration: copy data from old install dir to AppData ──
    if _is_frozen and APPDATA_DIR != BASE_DIR:
        _migrate_data_to_appdata()
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

    # Init shared Playwright browser (single Chrome process for TMS + portals)
    from config import TMS_DOWNLOADS_DIR, TMS_VIEWPORT
    from utils import cleanup_old_profiles

    await shared_browser.start(headless=False)
    logger.info("Shared browser started (1 Chrome process for all automation)")

    # Clean dead Chrome profile cache from old launch_persistent_context() usage
    cleanup_old_profiles()

    # TMS: lazy initialization — context created on first use
    tms_browser.set_shared_browser(shared_browser)
    logger.info("TMS browser ready (lazy — context created on first use)")

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
        portal_ctx = await shared_browser.create_context("portal",
            viewport={"width": 1920, "height": 960},
            accept_downloads=True,
        )
        portal_uploader = PortalUploader(
            browser_context=portal_ctx,
            username=TRANZACT_USERNAME,
            password=TRANZACT_PASSWORD,
        )
        logger.info("Portal uploader ready (TranzAct)")
    else:
        logger.info("TranzAct credentials not configured — portal upload flow disabled")

    # Init job manager
    job_manager = JobManager(
        qbo_api, classifier,
        email_sender=email_sender,
        portal_uploader=portal_uploader,
        tms_browser=tms_browser,
    )
    jobs.set_job_manager(job_manager)
    qbo.set_qbo_api(qbo_api)
    tms.set_tms_browser(tms_browser)
    settings.set_tms_browser(tms_browser)
    settings.set_job_manager(job_manager)

    # Log QBO API status
    if qbo_api.is_connected:
        logger.info("QBO API connected (realm: %s)", qbo_api.token_manager.realm_id)
        if qbo_api.token_manager.needs_reauth_warning:
            days = qbo_api.token_manager.refresh_token_days_remaining
            logger.warning("QBO API refresh token expires in %d days — re-authorize soon!", days)
    else:
        from config import QBO_CLIENT_ID
        if QBO_CLIENT_ID:
            logger.info("QBO API configured but not connected — visit /qbo/oauth/connect to authorize")
        else:
            logger.info("QBO API not configured (no QBO_CLIENT_ID in .env)")

    logger.info("Job manager ready")

    # TMS session check skipped — TMS uses lazy initialization.
    # Context will be created on first TMS operation (login checked then).
    logger.info("TMS session check deferred (lazy init — will check on first use)")

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
    await tms_browser.close()     # saves TMS cookies (if initialized)
    await shared_browser.close()  # kills the ONE Chrome process + Playwright
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
# Uses raw ASGI middleware instead of BaseHTTPMiddleware (which can deadlock under load)
class NoCacheStaticMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        needs_nocache = path.endswith(('.js', '.css', '.html'))

        async def send_with_headers(message):
            if needs_nocache and message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"cache-control", b"no-cache, no-store, must-revalidate"))
                headers.append((b"pragma", b"no-cache"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


# Auth middleware — validates JWT tokens on API routes
# Exempts: health check, public auth endpoints, static file serving, and OPTIONS (CORS preflight)
_AUTH_EXEMPT_PATHS = ("/health", "/auth/token", "/auth/login", "/auth/google", "/auth/setup", "/qbo/oauth/")


class AuthTokenMiddleware:
    """Raw ASGI auth middleware — validates JWT on API routes.
    Uses pure ASGI instead of BaseHTTPMiddleware to avoid event loop deadlocks.
    """
    _API_PREFIXES = ("/jobs", "/files", "/qbo", "/tms", "/customers", "/audit", "/settings", "/auth/")

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        method = scope.get("method", "GET")

        # Skip auth for: OPTIONS preflight, exempt paths, non-API routes
        if method == "OPTIONS":
            return await self.app(scope, receive, send)
        if any(path.startswith(p) for p in _AUTH_EXEMPT_PATHS):
            return await self.app(scope, receive, send)
        if not any(path.startswith(p) for p in self._API_PREFIXES):
            return await self.app(scope, receive, send)

        # Extract token from Authorization header or query param (for SSE)
        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode()
        token = auth_header[7:] if auth_header.startswith("Bearer ") else ""
        if not token:
            qs = scope.get("query_string", b"").decode()
            from urllib.parse import parse_qs
            token = parse_qs(qs).get("token", [""])[0]

        if not token:
            resp = JSONResponse(status_code=401, content={"detail": "Missing auth token"})
            return await resp(scope, receive, send)

        from routers.auth import decode_jwt
        payload = decode_jwt(token)
        if not payload:
            resp = JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})
            return await resp(scope, receive, send)

        # Inject user into scope state so route handlers can access it
        if "state" not in scope:
            scope["state"] = {}
        scope["state"]["user"] = payload
        return await self.app(scope, receive, send)


# Auth middleware — only active when NGL_AUTH_ENABLED=true in .env
from config import AUTH_ENABLED
if AUTH_ENABLED:
    app.add_middleware(AuthTokenMiddleware)
    logger.info("Auth middleware ENABLED — login required for API routes")
else:
    logger.info("Auth middleware DISABLED — all API routes are open (local dev mode)")

# CORS — allow the HTML app to call us (localhost only)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(NoCacheStaticMiddleware)

# Mount routers
app.include_router(auth.router)
app.include_router(jobs.router)
app.include_router(files.router)
app.include_router(qbo.router)
app.include_router(customers.router)
app.include_router(audit.router)
app.include_router(tms.router)
app.include_router(settings.router)


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
    _session_alerts["tms_needs_login"] = False

    return {
        "status": "ok",
        "service": "ngl-agent",
        "version": AGENT_VERSION,
        "qbo_api": "connected" if qbo_api.is_connected else "not_connected",
        "tms_browser": "initialized" if tms_browser.is_initialized else "lazy_pending",
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
def _port_in_use(port: int) -> bool:
    """Check if a port is already bound (another agent instance running)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True


if __name__ == "__main__":
    if _port_in_use(PORT):
        logger.error(
            "Port %d is already in use — another agent instance may be running. "
            "Close it first or check Task Manager for orphaned python/ngl-agent processes.",
            PORT,
        )
        # Wait briefly so the log message is visible in Electron's captured stderr
        import time; time.sleep(2)
        sys.exit(2)  # exit code 2 = port conflict (distinct from crash code 1)

    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
        reload=False,
        log_level="info",
    )
