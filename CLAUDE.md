# NGL Accounting System - Project Guide

## Project Overview
A specialized logistics accounting utility with three tools:
1. **Merge Tool** — Combine invoices and POD documents into organized PDFs per container
2. **Invoice Sender** — Send invoices to customers via QBO with email automation
3. **Customer Manager** — Maintain customer profiles, email addresses, and document rules

**Key Priority:** 100% Client-side PDF/Excel processing. No files ever leave the user's machine.

## Tech Stack
- **Web App:** Vanilla HTML/CSS/JavaScript (no framework, no build step)
- **Styling:** Tailwind CSS (CDN) + custom CSS
- **PDF Engine:** `pdf-lib` (CDN)
- **Excel Parsing:** `xlsx` / SheetJS (CDN)
- **Drag & Drop:** `SortableJS` (CDN)
- **ZIP Output:** `JSZip` (CDN)
- **Agent Server:** Python FastAPI on localhost:8787
- **QBO Automation:** Playwright browser automation
- **AI Classification:** Claude Haiku via Anthropic API

## File Structure

### Web App — `app/`
```
index.html                        HTML structure only (no inline JS for tool logic)
assets/
  css/
    styles.css                    all visual styling
  js/
    shared/
      utils.js                    pure helpers (uid, fmtSize, escHtml, findColumnKey)
      state.js                    global state objects
      agent-client.js             REST client (saveBatchOutput, pickFolder, openPath, …)
      log.js, dom-helpers.js, constants.js
    agent-ui.js                   persistent agent panel + health check
    app.js                        navigation, init, drop zones (legacy v1 zones removed)
    tools/
      merge/                      merge tool (M4 v2.55+ — single-version, no legacy)
        merge-v2.js               state machine + render (Empty/Loading/Review/Fetching/Ready/Merge)
        merge-v2-engine.js        pdf-lib merge functions for the 6 modes
        merge-v2-output.js        mode metadata + filename + path builders + save flow
      invoice-sender/             invoice sender (CSV, table, send flow, SSE)
      customers/                  CRUD, modals, tag inputs, import/export
      settings/, session-history/, chassis-finder/
  images/
    (logo + hero images)
```

The web app uses native ES modules. `app.js` is the entry point and statically imports the rest. No build step.

### Agent Server — `agent/`
```
main.py                           (FastAPI entry point, localhost:8787)
config.py                         (paths, environment, constants)
utils.py                          (shared utilities — strip_motw)
services/
  qbo_api/                        (QBO REST API — OAuth, invoices, attachments)
  tms_browser/                    (TMS portal automation — Playwright)
  job_manager/                    (background job orchestration, SSE streaming)
  claude_classifier.py            (Claude Haiku document classification)
  email_sender.py                 (Gmail SMTP for invoice delivery)
  portal_uploader.py              (TranzAct portal uploads)
routers/
  jobs.py                         (job endpoints)
  files.py                        (file serving + saving)
  qbo.py                          (QBO OAuth + status endpoints)
```

## Core Workflows
1. **Merge Tool (5-state flow):**
   - Empty → drop Excel manifest
   - Loading → parse + validate (dedup by INV#, soft-flag missing INV#)
   - Review → user fixes any issues, picks rows to fetch
   - Fetching → agent fetches invoices from QBO + docs from TMS (POD → BOL → POL → IT/ITE chain)
   - Ready → user clicks Continue to Merge → Merge screen with 6 mode cards (Per Container × 3, Combined × 3); merges accumulate as completed cards
2. **Invoice Sending:**
   - Upload CSV export + PDF attachments → match to customers → send via QBO agent
3. **Customer Management:**
   - CRUD customer profiles → set email addresses, required docs, send method

## Key Patterns
- `v2State` object inside `merge-v2.js` tracks all merge tool state (rows, jobId, completedModes, outputLocation, etc.)
- `invoiceState` / `sendState` (in `shared/state.js`) track invoice sender state
- `agentBridge` object handles all agent communication (REST + SSE)
- Agent health check runs every 15 seconds
- Fuzzy Excel column matching via `normalizeHeader()` + `findColumnKey()` with alias arrays
- Merge outputs land at `[user-chosen location]/Merge Outputs/[Mode]/YYYY-MM/YYYY-MM-DD/...` with same-day overwrite
- All modals use `.open` CSS class toggle pattern

## Error Handling
- If an Excel row has no matching PDF, log it to a "Failure Report" UI
- If a PDF is corrupted, skip it and notify the user via the Status Log
- Agent connection failures show inline warnings, don't block client-side features

## Running the Project
- **Web App:** Open `app/index.html` directly in a browser (file:// protocol, or double-click)
- **Agent Server:** Run `Start Agent.bat` or `cd agent && python main.py`
- **Agent Setup:** Run `agent/setup.bat` for first-time Python environment setup

## Rebuild Pipeline — MANDATORY
**Every rebuild MUST complete the full pipeline. No exceptions. Never stop at just building.**
1. Bump version in `desktop/VERSION`
2. Build agent (PyInstaller) + Electron installer (`build-all.bat` or manual steps)
3. `git add` + `git commit` all changes
4. `git push` to remote
5. `gh release create` with the installer `.exe` and `latest.yml` attached

The user relies on the Electron auto-updater — without a GitHub release, the update won't reach the app. Never ask "want me to push?" — just do it all automatically.

## Context Management — MANDATORY
**CRITICAL RULE: You MUST run `/compact` the moment context usage reaches 65%. No exceptions.**
- This is a BLOCKING requirement — stop whatever you are doing and compact immediately.
- Do NOT wait until 70%, 80%, or 89%. Compact at 65%.
- After compaction, re-read the plan file and todo list, then resume your work.
- If you are mid-edit when you hit 65%, finish the current atomic edit, then compact before continuing.

## Context Files
- `.context/architecture.md` — Stable architectural decisions
- `.context/conventions.md` — Coding standards reference
- `.context/current.md` — Active session notes
- `.context/tech-stack.md` — Package versions and dependencies
- `.context/tms-integration-plan.md` — TMS integration plan (deferred)
