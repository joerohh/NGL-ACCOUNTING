# NGL Accounting — Desktop Release Checklist

## Pre-Build Checks

- [ ] `agent/.env` exists and has all required keys (ANTHROPIC_API_KEY, GMAIL_*, TRANZACT_*, SUPABASE_*)
- [ ] QBO_EMAIL, QBO_PASSWORD, TMS_EMAIL, TMS_PASSWORD are in `agent/.env` but will be excluded from build
- [ ] Playwright Chromium is installed: `cd agent && venv\Scripts\python.exe -m playwright install chromium`
- [ ] No stale Chrome processes locking browser profiles (close all Chrome windows)
- [ ] `desktop/node_modules/` exists (run `cd desktop && npm install` if not)

## Build Steps (run from desktop/ folder)

### Option A: Build + Publish (recommended)
```
cd desktop
publish-release.bat
```
This builds the app AND uploads it to GitHub Releases so users get the update automatically.

### Option B: Build only (no publish)
```
cd desktop
build-all.bat
```
This builds locally without uploading. Use this for testing.

### Option C: Step by step
```
cd desktop

:: Step 1 — Build the Python agent (includes prepare-env.py)
build-agent.bat

:: Step 2 — Verify agent bundle has correct files
dir agent-dist\ngl-agent\_internal\.env
dir agent-dist\ngl-agent\_internal\ms-playwright\

:: Step 3 — Build the Electron installer
npm run build
```

## Post-Build Verification

### Check the bundle structure
```
desktop\dist\win-unpacked\resources\
  ├── app\           ← Electron internal (main.js, preload.js, package.json)
  ├── webapp\        ← Web UI (index.html, assets/)
  └── agent\
       └── ngl-agent\
            ├── ngl-agent.exe
            └── _internal\
                 ├── .env              ← Shared secrets (no QBO/TMS passwords)
                 ├── tms_selectors.json
                 ├── playwright\       ← Playwright driver
                 └── ms-playwright\    ← Chromium browser
                      └── chromium-*\
```

### Launch test (from win-unpacked)
1. Run `desktop\dist\win-unpacked\NGL Accounting.exe`
2. Check `%APPDATA%\ngl-accounting\ngl-debug.log` for startup errors
3. Verify: no "MISSING" errors in the log
4. Verify: agent health check shows "ok" (green status in app)

## Functional Validation Checklist

- [ ] **App launches** — Electron window opens, no error dialogs
- [ ] **Agent starts** — Green "Agent Connected" indicator in app
- [ ] **AI classifier available** — Health check shows `classifier: "ready"` (not `no_api_key`)
- [ ] **QBO login** — Can log into QBO via the app (Settings page or login modal)
- [ ] **TMS login** — Can log into TMS via the app
- [ ] **Merge tool** — Upload Excel + PDFs, merge works, download output
- [ ] **Invoice sender** — Upload CSV + PDFs, send flow works (with QBO logged in)
- [ ] **Customer manager** — Can view, edit, add customers (Supabase sync working)
- [ ] **Single instance** — Second launch focuses existing window instead of opening new one
- [ ] **Tray icon** — Minimize to tray works, restore from tray works
- [ ] **Settings page** — Can save QBO/TMS credentials, credentials persist after restart
- [ ] **Installer** — Install `NGL Accounting Setup *.exe` on a clean machine, verify all above

## Clean Machine Test

Install on a PC that has NEVER had:
- Python installed
- Node.js installed
- The agent venv or .env
- Any Playwright browsers

The installer must work with zero prerequisites. The user should only need to:
1. Install the .exe
2. Open the app
3. Enter their QBO/TMS credentials in Settings
4. Start using it

## Bug Prevention Regression Checks

| # | Previous Bug | What to verify |
|---|---|---|
| 1 | Wrong exe path | `main.js` line ~54: `path.join(agentDir, "ngl-agent", "ngl-agent.exe")` |
| 2 | uvicorn string import | `main.py` last line: `uvicorn.run(app, ...)` — passes object, not string |
| 3 | resources/app conflict | `package.json` extraResources uses `"to": "webapp"` |
| 4 | logFile typo | `main.js` uses `_logFile` consistently (search for `logFile` — should not appear without underscore) |
| 5 | Stale closing brace | Single instance lock is self-contained block, syntax verified with `node --check` |
| 6 | Missing build-env | `build-agent.bat` calls `prepare-env.py` before PyInstaller |
| 7 | _internal path (NEW) | Runtime hook uses `sys._MEIPASS`, config.py uses `BUNDLE_DIR` |
