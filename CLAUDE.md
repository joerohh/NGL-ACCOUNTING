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
index.html                        (1,045 lines — HTML structure only)
assets/
  css/
    styles.css                    (608 lines — all visual styling)
  js/
    utils.js                      (71 lines — pure helpers: uid, fmtSize, escHtml, findColumnKey)
    state.js                      (53 lines — global state objects)
    agent-bridge.js               (~540 lines — REST client, agent panel, QBO login)
    merge.js                      (~640 lines — Excel/PDF handling, merge modes, logging)
    invoice-sender.js             (~780 lines — CSV, table, send flow, SSE events)
    customers.js                  (~500 lines — CRUD, modals, tag inputs, import/export)
    app.js                        (~150 lines — navigation, init, responsive, drop zones)
  images/
    (logo + hero images)
```

**Script load order matters** (all share global scope, no ES modules):
```
utils.js → state.js → agent-bridge.js → merge.js → invoice-sender.js → customers.js → app.js
```

### Agent Server — `agent/`
```
main.py                           (FastAPI entry point, localhost:8787)
config.py                         (paths, environment, constants)
utils.py                          (shared utilities — strip_motw)
services/
  qbo_browser.py                  (Playwright QBO automation)
  claude_classifier.py            (Claude Haiku document classification)
  job_manager.py                  (background job orchestration, SSE streaming)
routers/
  jobs.py                         (job endpoints)
  files.py                        (file serving + saving)
  qbo.py                          (QBO status + login endpoints)
```

## Core Workflows
1. **Auto Merge (Data-Driven):**
   - Parse .xlsx → Extract "Container Number" + "Invoice Number" columns
   - Match local PDFs to containers via fuzzy name matching
   - Merge matches into organized PDFs (per-container, all-in-one, by type)
2. **Manual Merge (On-the-fly):**
   - Upload 2+ PDFs → reorder via drag-and-drop → merge and download
3. **Invoice Sending:**
   - Upload CSV export + PDF attachments → match to customers → send via QBO agent
4. **Customer Management:**
   - CRUD customer profiles → set email addresses, required docs, send method

## Key Patterns
- `state` object in state.js tracks all merge tool state
- `invoiceState` / `sendState` track invoice sender state
- `agentBridge` object handles all agent communication (REST + SSE)
- Agent health check runs every 15 seconds
- Fuzzy Excel column matching via `normalizeHeader()` + `findColumnKey()` with alias arrays
- All modals use `.open` CSS class toggle pattern

## Error Handling
- If an Excel row has no matching PDF, log it to a "Failure Report" UI
- If a PDF is corrupted, skip it and notify the user via the Status Log
- Agent connection failures show inline warnings, don't block client-side features

## Running the Project
- **Web App:** Open `app/index.html` directly in a browser (file:// protocol, or double-click)
- **Agent Server:** Run `Start Agent.bat` or `cd agent && python main.py`
- **Agent Setup:** Run `agent/setup.bat` for first-time Python environment setup

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
