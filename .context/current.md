# Current Session — NGL Accounting System

> Update this file at the start of every work session.

## Date
2026-02-19

## Active Goal
v2.0 feature upgrade — single-file HTML app with Auto Mode, Manual Mode, drag-and-drop, Status Log, ZIP download, and Agent API stub.

## Task Checklist
- [x] Created CLAUDE.md with project rules
- [x] Added TypeScript, component, and API conventions
- [x] Created .context/ folder system
- [x] Built full hybrid merger (app/index.html)
- [x] Auto Mode — Excel-driven container matching + bulk merge
- [x] Manual Mode — drag-and-drop reorder + quick merge
- [x] Status Log (developer console aesthetic)
- [x] Download All as ZIP (bulk mode)
- [x] Failure Report UI (lists unmatched containers)
- [x] Progress bar
- [x] Agent-ready API stub (window.__nglAgent)
- [ ] Node.js not installed — cannot run Next.js yet (install from nodejs.org)
- [ ] Phase 2: Python automation agent (watchdog + Claude API)
- [ ] Phase 2: Connect __nglAgent.processPayload to fetch PDFs from URLs

## Deployment
- File: app/index.html
- All deps via CDN — no build step required
- Deploy: copy index.html to GitHub Pages repo root

## CDN Versions Pinned
- pdf-lib@1.17.1
- xlsx@0.20.3 (SheetJS)
- sortablejs@1.15.2
- jszip@3.10.1
- tailwindcss (play CDN)

## Known Issues / Blockers
- Node.js not installed — cannot use npm/Next.js. Install from nodejs.org if Next.js migration is desired.
- For now, single-file HTML is the deployment target (GitHub Pages).

## Notes for AI Assistant
- The app is 100% client-side. Files never leave the browser.
- window.__nglAgent is the hook point for Phase 2 automation agent.
- Excel "Container Number" column matching is flexible (case-insensitive, ignores spaces/dashes).
