"""Agent configuration — all settings in one place."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Paths — NGL_AGENT_DIR is set by PyInstaller runtime hook when running as bundled exe
BASE_DIR = Path(os.environ["NGL_AGENT_DIR"]) if "NGL_AGENT_DIR" in os.environ else Path(__file__).resolve().parent
# BUNDLE_DIR = where PyInstaller extracts read-only data files (_internal/ in packaged mode)
# In dev mode, this is the same as BASE_DIR (agent/ folder).
BUNDLE_DIR = Path(os.environ["NGL_BUNDLE_DIR"]) if "NGL_BUNDLE_DIR" in os.environ else BASE_DIR

# Persistent user data — survives app updates/reinstalls
# In packaged mode: %LOCALAPPDATA%/NGL Accounting/  (e.g. C:\Users\Joe\AppData\Local\NGL Accounting)
# In dev mode: same as BASE_DIR (agent/ folder)
_is_packaged = "NGL_AGENT_DIR" in os.environ
APPDATA_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "NGL Accounting" if _is_packaged else BASE_DIR

DOWNLOADS_DIR = BASE_DIR / "downloads"
BROWSER_DOWNLOADS_DIR = BASE_DIR / ".browser_downloads"  # temp dir for Playwright auto-downloads
BROWSER_PROFILE_DIR = APPDATA_DIR / ".browser_profile"
SELECTORS_FILE = BUNDLE_DIR / "selectors.json"
JOB_STATE_DIR = BASE_DIR / ".job_state"
DEBUG_DIR = BASE_DIR / "debug"
OUTPUT_DIR = BASE_DIR / "output"  # Final merged PDFs saved here (no MOTW)
DATA_DIR = APPDATA_DIR / "data"
CUSTOMERS_FILE = DATA_DIR / "customers.json"
AUDIT_LOG_FILE = DATA_DIR / "audit_log.jsonl"
DO_SENDER_CACHE_FILE = DATA_DIR / "do_sender_cache.json"
BACKUP_DIR = APPDATA_DIR / "backups"
BACKUP_RETAIN_DAYS = 30  # keep last 30 daily backups

# Ensure directories exist
APPDATA_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOADS_DIR.mkdir(exist_ok=True)
BROWSER_DOWNLOADS_DIR.mkdir(exist_ok=True)
BROWSER_PROFILE_DIR.mkdir(exist_ok=True)
JOB_STATE_DIR.mkdir(exist_ok=True)
DEBUG_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)
BACKUP_DIR.mkdir(exist_ok=True)

# Server
HOST = "127.0.0.1"
PORT = 8787
ALLOWED_ORIGINS = [
    "null",                    # file:// origin
    "http://localhost",
    "http://localhost:8080",
    "http://localhost:8787",
    "http://127.0.0.1",
    "http://127.0.0.1:8080",
    "http://127.0.0.1:8787",
]

# Claude API
CLAUDE_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-haiku-4-5-20251001"

# Browser (shared Playwright instance)
BROWSER_HEADLESS = False  # set True to run Chrome headless (no visible window)

# QBO
QBO_BASE_URL = "https://app.qbo.intuit.com"
QBO_LOGIN_URL = "https://qbo.intuit.com/app/homepage"

# Timing — guards against rate limiting
QBO_ACTION_DELAY_S = 1.0        # seconds between QBO actions (was 2.5)
QBO_RETRY_COUNT = 3             # retries per failed download
QBO_RETRY_BACKOFF_S = 5.0       # initial backoff (doubles each retry)

# Classification
CLASSIFICATION_CONFIDENCE_THRESHOLD = 0.80  # below this → flag for manual review
CLASSIFICATION_DPI = 100                    # lower DPI = smaller image = cheaper API call
MAX_BATCH_SIZE = 200                        # max containers per job
CONTAINER_TIMEOUT_S = 120                   # max seconds per container in fetch jobs
FETCH_CONCURRENCY = 1                       # max parallel container fetches (1 = sequential)
SEND_TIMEOUT_S = 180                        # max seconds per invoice in send jobs (OEC/portal flows are slower)
RESEND_NOTICE = True                        # prepend transmission error notice to outgoing emails (turn off when done)
DAILY_API_CALL_LIMIT = 200                  # max Claude API calls per day (safety cap)
API_USAGE_FILE = APPDATA_DIR / ".api_usage.json"  # tracks daily usage

# Gmail SMTP (OEC flow — POD emails)
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")

# TranzAct portal (portal upload flow)
TRANZACT_USERNAME = os.getenv("TRANZACT_USERNAME", "")
TRANZACT_PASSWORD = os.getenv("TRANZACT_PASSWORD", "")

# QBO auto-login credentials (browser automation — legacy)
QBO_EMAIL = os.getenv("QBO_EMAIL", "")
QBO_PASSWORD = os.getenv("QBO_PASSWORD", "")

# QBO API (OAuth 2.0) — replaces browser automation
QBO_CLIENT_ID = os.getenv("QBO_CLIENT_ID", "")
QBO_CLIENT_SECRET = os.getenv("QBO_CLIENT_SECRET", "")
QBO_REALM_ID = os.getenv("QBO_REALM_ID", "")
QBO_REDIRECT_URI = os.getenv("QBO_REDIRECT_URI", f"http://localhost:{PORT}/qbo/oauth/callback")
QBO_API_BASE_URL = "https://quickbooks.api.intuit.com"  # Production
QBO_API_BASE_URL_SANDBOX = "https://sandbox-quickbooks.api.intuit.com"  # Sandbox
QBO_USE_SANDBOX = os.getenv("QBO_USE_SANDBOX", "false").lower() in ("true", "1", "yes")
QBO_TOKENS_FILE = APPDATA_DIR / ".qbo_tokens.json"

# QBO mode: "browser" (Playwright, legacy) or "api" (official QBO API)
QBO_MODE = os.getenv("QBO_MODE", "browser")

# TMS auto-login credentials (Google SSO)
TMS_EMAIL = os.getenv("TMS_EMAIL", "")
TMS_PASSWORD = os.getenv("TMS_PASSWORD", "")

# TMS portal (POD fetching for OEC flow)
TMS_URL = "https://tms.ngltrans.net"
TMS_LOGIN_URL = "https://tms.ngltrans.net/sign-in"
TMS_PROFILE_DIR = APPDATA_DIR / ".tms_browser_profile"
TMS_DOWNLOADS_DIR = BASE_DIR / ".tms_downloads"
TMS_DEBUG_DIR = DEBUG_DIR / "tms"
TMS_SELECTORS_FILE = BUNDLE_DIR / "tms_selectors.json"
TMS_ACTION_DELAY_S = 2.0  # seconds between TMS actions
TMS_VIEWPORT = {"width": 1600, "height": 1000}  # fixed viewport to prevent layout shifts

TMS_PROFILE_DIR.mkdir(exist_ok=True)
TMS_DOWNLOADS_DIR.mkdir(exist_ok=True)
TMS_DEBUG_DIR.mkdir(exist_ok=True)

# Supabase (shared cloud database)
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

# Web app auto-update — set this to a URL that hosts version.json + webapp.zip
# Example: "https://yourserver.com/ngl-updates" or a GitHub Pages URL
# The agent will check {WEB_UPDATE_URL}/version.json on startup and download
# {WEB_UPDATE_URL}/webapp.zip if a newer version is available.
WEB_UPDATE_URL = os.getenv("WEB_UPDATE_URL", "")
WEBAPP_CACHE_DIR = APPDATA_DIR / "webapp-cache"

# Auth token for local server (simple security)
AUTH_TOKEN = os.getenv("NGL_AGENT_TOKEN", "ngl-local-dev-token")
# Set to True to enforce JWT login (disabled until auth UI is fully tested)
AUTH_ENABLED = os.getenv("NGL_AUTH_ENABLED", "false").lower() in ("true", "1", "yes")
