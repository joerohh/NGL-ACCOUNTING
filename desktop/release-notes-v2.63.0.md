## Critical hotfix — v2.62 was broken; this fixes it + the underlying causes

### Why v2.62 didn't work

v2.62 shipped a JavaScript SyntaxError (a duplicate function declaration in the new Invoice Sender results module). The error blocked the entire frontend JS from loading. Visible symptoms: clicking **Sign In** did nothing, the **Sign in with Google** button never appeared, the **Connecting to agent…** pill stayed gray forever. The Electron app loaded an HTML page that looked correct but had no working button handlers.

The bug existed in the code — but the bigger failure is that nothing in the build pipeline checked the JS before packaging the installer. The release went out without anyone running the app. This release fixes both the bug and the gap that let it ship.

### What changed

1. **The SyntaxError is gone.** The duplicate `renderPanel` declaration is removed; the panel state dispatch now lives inside the original function. v2.63 loads cleanly and Sign In works.

2. **The build now refuses to package broken JS.** New `desktop/check-js.js` runs `node --check` on every file under `app/assets/js/` before PyInstaller and electron-builder are invoked. Any SyntaxError aborts the build with a clear file + line error. Today's bug would have been caught in <1 second.

3. **The agent stops lying about its version.** `agent/main.py` used to hardcode `AGENT_VERSION = "2.37.0"` — that string has been stale across ~25 releases. Now the agent reads the version from an env var passed by Electron at launch (with a `desktop/VERSION` file fallback for dev runs). `/health` finally returns the real version.

4. **Electron refuses to use a wrong-version agent.** When the desktop app starts, it checks whether something is already running on port 8787 (used to be a "skip the spawn" optimization). Previously it would silently trust whoever was there. Now it asks the running agent its version, compares to the desktop app's own version, and if they don't match, shows a clear dialog instructing the user how to close the leftover process via Task Manager, then quits. Prevents silent corruption from stale dev agents or auto-update edge cases.

5. **Login screen polls for the agent.** The pre-existing one-shot health check at startup is replaced with a polling loop that retries every 1.5 s for up to 30 s while the "Connecting to agent…" pill is visible. Defense in depth for cold-start races on slow machines.

### What's NOT in this release

No Invoice Sender or Merge Tool feature changes since v2.62 (which itself shipped Fix 1 + Fix 2 of the Invoice Sender UX overhaul — those features land properly for the first time in v2.63 because v2.62's JS never loaded).
