# Architecture — NGL Accounting System

## Overview
A local-first logistics accounting utility with a vanilla web frontend and a Python automation backend.

## Core Constraint
**100% client-side PDF/Excel processing.** No files are ever uploaded to a server. All merge and parsing operations happen entirely in the browser.

## System Components

### Web App (`app/`)
- **Protocol:** `file://` (opened directly in browser, no web server needed)
- **Architecture:** Vanilla HTML/CSS/JavaScript with ES modules
- **Styling:** Tailwind CSS (CDN) + custom CSS
- **State:** Global objects (`state`, `invoiceState`, `sendState`) in shared modules

### Agent Server (`agent/`)
- **Framework:** Python FastAPI on `localhost:8787`
- **Browser Automation:** Playwright (persistent Chrome profiles for QBO + TMS)
- **AI Classification:** Claude Haiku via Anthropic API
- **Email:** Gmail SMTP for OEC POD emails
- **Portal:** Playwright-based TranzAct portal uploads

## Data Flow — Merge Tool
```
.xlsx file → SheetJS parser → extract Container # + Invoice #
→ match against local PDF files → pdf-lib merge → download per-container PDFs
```

## Data Flow — Invoice Sender
```
CSV export → parse invoices → match to customers (localStorage)
→ agent sends via QBO browser automation (Playwright)
→ SSE progress streaming back to frontend
```

## Data Flow — Customer Manager
```
CRUD in localStorage → sync to agent server (customers.json)
→ used by Invoice Sender for email routing + doc requirements
```

## Auth
- No user accounts — local-first utility
- Agent server uses a simple bearer token for API calls
- QBO/TMS sessions via persistent Chrome browser profiles

## Key Boundaries
- Frontend handles all PDF/Excel processing — never delegates to server
- Agent server handles browser automation (QBO, TMS) and email sending
- Communication: REST API + SSE for real-time progress
