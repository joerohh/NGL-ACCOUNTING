# NGL Accounting Service

A local-first logistics accounting utility for NGL Transportation. Three tools in one app:

1. **Merge Tool** — Combine invoices and POD documents into organized PDFs per container
2. **Invoice Sender** — Send invoices to customers via QuickBooks Online with email automation
3. **Customer Manager** — Maintain customer profiles, email addresses, and document rules

All PDF and Excel processing happens 100% in the browser. No files ever leave your machine.

## Quick Start

### First-Time Setup

1. Run `agent/setup.bat` to install the Python environment and dependencies
2. Copy `agent/.env.template` to `agent/.env` and fill in your credentials:
   - `ANTHROPIC_API_KEY` — for AI document classification
   - `QBO_EMAIL` / `QBO_PASSWORD` — for QBO auto-login
   - `TMS_EMAIL` / `TMS_PASSWORD` — for TMS auto-login
   - `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` — for OEC email flow
   - `TRANZACT_USERNAME` / `TRANZACT_PASSWORD` — for portal upload flow

### Running the App

**Option A:** Double-click `NGL Accounting.bat` (starts the agent and opens the app)

**Option B:** Manual start
1. Run `Start Agent.bat` (or `cd agent && python main.py`)
2. Open `app/index.html` in your browser

The agent server runs on `http://localhost:8787`. The web app connects to it automatically.

## Project Structure

```
app/                          Web frontend (vanilla HTML/CSS/JS)
  index.html                  Main HTML structure
  assets/css/styles.css       All styling
  assets/js/                  ES modules
    shared/                   Shared utilities, state, constants
    tools/                    Tool-specific code (merge, invoice-sender, customers, settings)
    app.js                    Entry point (navigation, init)
    agent-ui.js               Agent panel UI

agent/                        Python backend (FastAPI)
  main.py                     Server entry point (localhost:8787)
  config.py                   All configuration
  .env                        Credentials (not committed)
  services/                   Core services
    qbo_api/                  QBO REST API (OAuth, invoices, attachments)
    tms_browser/              TMS portal automation (Playwright)
    job_manager/              Background job orchestration + SSE
    claude_classifier.py      AI document classification
    email_sender.py           Gmail SMTP for invoice delivery
    portal_uploader.py        TranzAct portal uploads
  routers/                    API endpoints
  data/                       Customer data, audit logs
  debug/                      Debug screenshots (auto-cleaned after 7 days)

.context/                     Architecture and convention docs
```

## Tech Stack

- **Frontend:** Vanilla HTML/CSS/JS, Tailwind CSS (CDN), pdf-lib, SheetJS, SortableJS, JSZip
- **Backend:** Python 3.12+, FastAPI, Playwright, Anthropic SDK
- **Automation:** QBO and TMS via persistent Chrome browser profiles
