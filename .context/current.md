# Current State — NGL Accounting System

> Last updated: 2026-03-05

## Active State
All three tools (Merge, Invoice Sender, Customer Manager) are fully functional.
Agent server handles QBO + TMS browser automation with auto-login and session persistence.

## Recent Work
- Phase 1 audit complete: auth middleware, CORS fix, per-operation timeouts, log factory, dispatch map
- Phase 2 structural improvements: file splits (mixin pattern), constants.js, debug cleanup, audit rotation
- Web Worker merge for parallel PDF processing
- TMS integration working (search, document listing, POD download)
- ES modules migration complete

## Architecture
- Web app: Vanilla HTML/CSS/JS with ES modules (no build step)
- Agent: Python FastAPI on localhost:8787
- Browser automation: Playwright with persistent Chrome profiles
- All PDF/Excel processing is 100% client-side

## CDN Versions Pinned
- pdf-lib@1.17.1
- xlsx@0.20.3 (SheetJS)
- sortablejs@1.15.2
- jszip@3.10.1
- tailwindcss (play CDN)
