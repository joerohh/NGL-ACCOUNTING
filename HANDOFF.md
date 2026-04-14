# NGL Accounting — Handoff Guide

**Prepared by:** Joseph Roh
**Last updated:** 2026-04-14

This document is everything the next person needs to keep NGL Accounting running after I'm gone. Read the whole thing once before you touch anything.

---

## What this app is

A Windows desktop app used by NGL accounting staff to:
1. **Merge** invoices + POD documents into single PDFs per container
2. **Send** invoices to customers via QuickBooks
3. **Manage** customer contact info + document rules

It runs as an installed `.exe` on each staff member's PC. The program automatically updates itself when I (or my successor) publish a new release on GitHub.

---

## The 5 critical accounts — DO NOT LOSE ACCESS

If any of these accounts are lost, a piece of the app breaks for everyone. Transfer ownership to company-controlled accounts BEFORE my departure.

| # | Service | Current owner | What it controls | Transfer priority |
|---|---------|--------------|------------------|-------------------|
| 1 | **GitHub** (repo: joerohh/NGL-ACCOUNTING) | joerohh (personal) | Source code + auto-updater | 🔴 Critical |
| 2 | **Intuit Developer** | Joseph's Intuit account | QBO connection for the whole company | 🔴 Critical |
| 3 | **Supabase** | Joseph's Supabase account | Database: customers, users, audit logs, QBO tokens | 🔴 Critical |
| 4 | **Google Cloud** | Joseph's Google account | Sign-in-with-Google, Google Drive upload | 🟡 Medium |
| 5 | **Anthropic API** | Joseph's Anthropic account | AI document classification (Claude) | 🟡 Medium |

### How to transfer each

**GitHub:**
- Create a GitHub organization named `ngltrans` (or similar, company-owned email)
- Repo Settings → Transfer ownership → type `ngltrans/NGL-ACCOUNTING`
- OR at minimum: add a trusted colleague with "Admin" role under Repo Settings → Collaborators

**Intuit Developer:**
- developer.intuit.com → My Hub → NGL ACCOUNTING app
- Team Members → invite a company colleague with Admin role
- After they accept, they can manage the app even if my account is deleted

**Supabase:**
- supabase.com dashboard → your project → Organization Settings
- Members → Invite → add a company email with **Owner** role

**Google Cloud:**
- console.cloud.google.com → IAM & Admin → IAM
- Add a company email as a **Owner** on the NGL project
- Also add them to the OAuth consent screen + credentials if needed

**Anthropic:**
- Best option: have the company create its own Anthropic account with company credit card
- Generate a new API key there
- Update `agent/.env`: replace `ANTHROPIC_API_KEY=...`
- Rebuild + release (see below)

---

## Monthly health check (15 minutes)

Do this on the first Monday of each month.

### 1. Check Supabase usage
- supabase.com → your project → Home
- Look for: Database size, API requests, Monthly active users
- Free tier limits: 500 MB database, 5 GB bandwidth
- If you're at 80%+ of any limit, upgrade to Pro ($25/mo) before hitting the cap

### 2. Check QBO connection
- Open NGL Accounting → Settings → QBO API Connection
- Should say "Connected" with a green indicator
- If the refresh-token warning appears (under 7 days remaining), tell the QBO admin to reconnect ASAP (see "Reconnecting QuickBooks" below)

### 3. Check for failed jobs
- Open NGL Accounting → any recent sending job
- If errors are spiking, something broke. See "Troubleshooting" below.

### 4. Check that auto-update is working
- Compare the version shown in Settings with the latest on github.com/joerohh/NGL-ACCOUNTING/releases
- They should match

---

## How to rebuild and release (when you make a change)

You need these installed on your PC first:
- **Node.js** (nodejs.org)
- **Python 3.9 or newer** (python.org)
- **Git** (git-scm.com)
- **GitHub CLI** (`gh`) — install via `winget install GitHub.cli`, then run `gh auth login`

### First-time setup on a new machine

```
cd "C:\path\to\NGL ACCOUNTING SERVICE\agent"
setup.bat
```

This installs Python dependencies into a virtual environment.

### Making a change and shipping it

1. Edit the code (use Claude Code or VS Code)
2. Test it locally: `cd agent && python main.py` then open `app/index.html` in a browser
3. When satisfied, ship it:
   - Open File Explorer → go to `desktop` folder
   - Double-click `publish-release.bat`
   - It bumps the version, builds the installer, pushes to GitHub, creates a release
   - Co-workers' apps will auto-update within a few minutes of opening them

**That's it.** No manual steps.

### If `publish-release.bat` fails

Common causes + fixes:
- **"Node.js is not installed"** → install Node.js from nodejs.org, reboot, try again
- **"gh: command not found"** → install GitHub CLI, then `gh auth login`
- **"PyInstaller build failed"** → run `agent/setup.bat` first
- **"Google Drive upload failed"** → not critical, the release still works; skip it

---

## Common problems and fixes

### "Co-worker's app says 'Not connected' to QBO"
The QBO admin needs to reconnect. In any installed app:
1. Open app → Settings → QBO API Connection
2. Click **Connect QBO API**
3. Sign in with a QBO admin account
4. Authorize
5. All co-workers now share the new connection

### "The auto-updater is failing with a 404"
Likely the GitHub repo was made private or moved. Make it public again:
```
gh repo edit <owner>/NGL-ACCOUNTING --visibility public --accept-visibility-change-consequences
```

### "Supabase project is paused"
Free tier pauses after 7 days of zero activity. Open Supabase dashboard → your project → click "Restore project." Takes 2 minutes.

### "QuickBooks suddenly disconnected everyone"
The refresh token expired (>101 days unused). Just have the QBO admin reconnect. No data lost.

### "Someone left the company and I want to revoke their access"
1. Open NGL Accounting → Settings → Users → find them → set to "Inactive"
2. If you're paranoid: Supabase dashboard → Project Settings → API → Generate new service_role key → update `agent/.env` → rebuild + release
3. Their installer stops working within 1 hour

### "The TMS integration broke"
TMS is maintained separately — the selectors may have changed. Look at `agent/services/tms_browser/` and the debug logs in `%LOCALAPPDATA%\NGL Accounting\debug\tms\`. This is the hardest part of the app; consider hiring a developer for TMS fixes.

---

## Security rotation procedure (do if you suspect a breach)

**Scenario:** Someone stole a laptop with the installer, or a disgruntled ex-employee kept a copy.

1. **Rotate Supabase service_role key**
   - Supabase dashboard → Project Settings → API → "Generate new service_role key"
   - Copy the new key
   - Update `agent/.env` → `SUPABASE_SERVICE_KEY=<new key>`

2. **Rotate Anthropic key**
   - console.anthropic.com → API Keys → revoke the old key → create new one
   - Update `agent/.env` → `ANTHROPIC_API_KEY=<new key>`

3. **Rotate Gmail app passwords** (each user does their own in Settings page)

4. **Rebuild and release**
   - Double-click `desktop/publish-release.bat`

5. Tell all co-workers to restart their apps to pick up the new version.

Old installer copies stop working within an hour.

---

## What you should NOT touch

- `agent/.qbo_tokens.json` — shared via Supabase, regenerated automatically
- `desktop/build-env/.env` — auto-generated from `agent/.env` by the build script
- `desktop/dist/` — build output, gets regenerated each build
- `desktop/agent-dist/` — build output
- `desktop/build-temp/` — build output

---

## Who to call when it's truly broken

1. **Me (Joseph)** — until my departure date
2. **Claude Code** (claude.com/code) — an AI coding assistant; paste this HANDOFF.md into a session and ask for help
3. **A freelance developer** on Upwork/Fiverr who knows Python + Electron + Supabase (approx $50-100/hr for non-urgent fixes)

---

## Code layout cheat sheet

If a developer needs to find something:
- `app/` — the user interface (HTML/JS)
- `agent/` — the backend Python server
- `agent/services/qbo_api/` — QuickBooks integration
- `agent/services/tms_browser/` — TMS browser automation
- `agent/services/supabase_client.py` — database layer
- `desktop/` — Windows packaging scripts
- `docs/qbo-callback.html` — OAuth HTTPS redirect page (hosted on GitHub Pages)

Full architecture notes: `.context/architecture.md`

---

## Final note

This app does real work and saves real hours every week. Please don't let it die. Even if you can't make changes, just **keep the accounts alive** and **keep the installer working** — that alone is enough to keep the team productive.

Good luck. — Joseph
