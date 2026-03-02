"""Agent configuration — all settings in one place."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Paths
BASE_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
BROWSER_DOWNLOADS_DIR = BASE_DIR / ".browser_downloads"  # temp dir for Playwright auto-downloads
BROWSER_PROFILE_DIR = BASE_DIR / ".browser_profile"
SELECTORS_FILE = BASE_DIR / "selectors.json"
JOB_STATE_DIR = BASE_DIR / ".job_state"
DEBUG_DIR = BASE_DIR / "debug"
OUTPUT_DIR = BASE_DIR / "output"  # Final merged PDFs saved here (no MOTW)
DATA_DIR = BASE_DIR / "data"
CUSTOMERS_FILE = DATA_DIR / "customers.json"
AUDIT_LOG_FILE = DATA_DIR / "audit_log.jsonl"

# Ensure directories exist
DOWNLOADS_DIR.mkdir(exist_ok=True)
BROWSER_DOWNLOADS_DIR.mkdir(exist_ok=True)
BROWSER_PROFILE_DIR.mkdir(exist_ok=True)
JOB_STATE_DIR.mkdir(exist_ok=True)
DEBUG_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

# Server
HOST = "127.0.0.1"
PORT = 8787
ALLOWED_ORIGINS = [
    "null",                    # file:// origin
    "http://localhost",
    "http://localhost:8080",
    "http://127.0.0.1",
    "http://127.0.0.1:8080",
]

# Claude API
CLAUDE_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-haiku-4-5-20251001"

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
DAILY_API_CALL_LIMIT = 200                  # max Claude API calls per day (safety cap)
API_USAGE_FILE = BASE_DIR / ".api_usage.json"  # tracks daily usage

# Gmail SMTP (OEC flow — POD emails)
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")

# TranzAct portal (portal upload flow)
TRANZACT_USERNAME = os.getenv("TRANZACT_USERNAME", "")
TRANZACT_PASSWORD = os.getenv("TRANZACT_PASSWORD", "")

# TMS portal (POD fetching for OEC flow)
TMS_URL = "https://tms.ngltrans.net"
TMS_LOGIN_URL = "https://tms.ngltrans.net/sign-in"
TMS_PROFILE_DIR = BASE_DIR / ".tms_browser_profile"
TMS_DOWNLOADS_DIR = BASE_DIR / ".tms_downloads"
TMS_DEBUG_DIR = DEBUG_DIR / "tms"
TMS_SELECTORS_FILE = BASE_DIR / "tms_selectors.json"
TMS_ACTION_DELAY_S = 2.0  # seconds between TMS actions

TMS_PROFILE_DIR.mkdir(exist_ok=True)
TMS_DOWNLOADS_DIR.mkdir(exist_ok=True)
TMS_DEBUG_DIR.mkdir(exist_ok=True)

# Auth token for local server (simple security)
AUTH_TOKEN = os.getenv("NGL_AGENT_TOKEN", "ngl-local-dev-token")
