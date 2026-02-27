"""NGL Agent Server — FastAPI entry point."""

import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import (
    HOST, PORT, ALLOWED_ORIGINS, CLAUDE_API_KEY, DAILY_API_CALL_LIMIT,
    GMAIL_ADDRESS, GMAIL_APP_PASSWORD,
    TRANZACT_USERNAME, TRANZACT_PASSWORD,
)
from routers import jobs, files, qbo, customers, audit, tms
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


KEEPALIVE_INTERVAL_S = 300  # 5 minutes between keep-alive pings


async def _session_keepalive_loop():
    """Background task: ping QBO and TMS browsers every 5 minutes to prevent session timeout.

    If a session has expired, automatically navigate to the login page so the
    user sees it immediately and can re-authenticate.
    """
    await asyncio.sleep(60)  # Wait 1 min after startup before first check
    while True:
        try:
            # QBO keep-alive
            qbo_alive = await qbo_browser.keep_alive()
            if not qbo_alive:
                logger.warning("QBO session lost — navigating to login page for re-auth")
                try:
                    await qbo_browser.open_login_page()
                except Exception as e:
                    logger.error("QBO auto-reconnect failed: %s", e)

            # TMS keep-alive
            tms_alive = await tms_browser.keep_alive()
            if not tms_alive:
                logger.warning("TMS session lost — navigating to login page for re-auth")
                try:
                    await tms_browser.open_login_page()
                except Exception as e:
                    logger.error("TMS auto-reconnect failed: %s", e)

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
    logger.info("Job manager ready")

    logger.info("=" * 50)
    logger.info("  Agent is live! Open index.html in your browser.")
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
    logger.info("Goodbye!")


# ── FastAPI app ──────────────────────────────────────────────────────
app = FastAPI(
    title="NGL Agent Server",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow the HTML app to call us
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS + ["*"],  # permissive for local dev
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
    return {
        "status": "ok",
        "service": "ngl-agent",
        "qbo_browser": "initialized",
        "tms_browser": "initialized",
        "classifier": "ready" if classifier else "no_api_key",
        **usage_info,
    }


# ── Run ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=HOST,
        port=PORT,
        reload=False,
        log_level="info",
    )
