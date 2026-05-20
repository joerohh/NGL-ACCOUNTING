# Users + Storage tabs, Settings cleanup, and login bootstrap fix

**Date:** 2026-05-19
**Status:** Design (awaiting user review)
**Scope:** v2.73.0 (next minor)

## Background

Three concerns surfaced in the same session and share enough files/concepts to be one coordinated change:

1. **User management is buried in Settings** — admins must scroll past credentials, notifications, and selector health to reach the user list. The Add User flow works but is missing a Delete button. Passwords cannot be displayed (bcrypt, by design) and that's not communicated anywhere in the UI.
2. **Storage card is buried in Settings** — operators need to know where merge outputs land, but the info is mixed with admin credentials. Page is visually heavy with too many bordered sections.
3. **Fresh installs cannot use Google sign-in** — `app.js` checks local `get_user_count()` to decide between Setup screen and Login screen. Local count is 0 on fresh install, so Setup shows. Setup screen has no Google button (only the manual create-admin form). New co-workers either create a parallel local admin (invisible to other installs and to Supabase) or get stuck. Saved as a deferred fix in memory (May 14) but never shipped.

All three touch `app/index.html`, `app/assets/js/app.js`, and `agent/routers/auth.py`. Bundled here to avoid two near-simultaneous PRs in the same files.

## Goals

- Move user management out of Settings into a dedicated **Users tab** (admin-only, hidden from operators)
- Move storage info out of Settings into a dedicated **Storage tab** (visible to all)
- Restructure the sidebar so active tools and management/admin items are clearly separated
- Clean up the Settings page into a single Connections card + small Preferences card + collapsible Advanced
- Fix the fresh-install login bootstrap so new users can sign in via Google
- Add a Deactivate button (soft delete) per user row and surface inactive users via a collapsible
- Communicate clearly in the UI why passwords cannot be displayed

## Non-goals

- Hardening the multi-user Supabase model beyond what's needed for this change (e.g., RLS audit, user-scoped row policies — defer)
- Adding search / filter / pagination to the user list (YAGNI — the team is small)
- Replacing bcrypt or adding password-strength rules
- Email-based password reset flow (admin-reset via Edit modal remains the recovery path)

---

## Section 1 — Sidebar restructure

### New structure

**Workspace** section (active tools, in this order):
1. Invoice Sender
2. Merging Tool
3. Chassis Finder *(renamed from "INI Chassis Finder")*

**System** section (management/admin/reference, in this order):
1. Settings
2. Customers *(moved from Workspace)*
3. Storage *(new tab)*
4. Session History *(moved from Workspace)*
5. Users *(new tab — admin-only)*

### Visibility rules

- **Workspace items**: always visible
- **System items**: Settings, Customers, Storage, Session History always visible; **Users** is visible only when `agentBridge.getCurrentUser().role === 'admin'`
- The sidebar must re-evaluate visibility on login/logout and on user-data refresh (`/auth/me`) so a role change without a full page reload still updates the nav

### Renames

- Label "INI Chassis Finder" → "Chassis Finder" in `app/index.html` and in the page header rendered by `chassis-finder.js`. The internal tool key `chassis-finder` stays unchanged — only the display label changes.

---

## Section 2 — Users tab

### Files

New module: `app/assets/js/tools/users/users.js`
New view container: `<div id="usersView">` added to `app/index.html`

The existing `#userManagementSection` block currently inside Settings (around `app/index.html:1376-1488`, plus the Add/Edit User modal at `app/index.html:1490-1532`) is **lifted into the new tab unchanged in markup**, with the wrapper container changed from a Settings sub-card to a full page layout. The Add/Edit User modal stays exactly as it is — same HTML, same handlers (`window.openAddUserModal`, `window.openEditUserModal`, `window.saveUser`).

### Layout

- Page header: `<h1>Users</h1>` + subtitle "Manage who has access to NGL Accounting"
- Top-right action: "+ Add User" button (existing handler)
- User list (card-per-row, existing visual pattern from `settings.js` `loadUserList`):
  - Avatar (initials from displayName)
  - Name (with `(you)` marker for self)
  - `@username · ●Active` / `●Inactive` line
  - Role pill: ADMIN (amber) or OPERATOR (blue)
  - Action icons:
    - ✏️ **Edit** — opens existing modal (password field inside is how admins reset passwords)
    - 🗑️ **Deactivate** — calls `agentBridge.deleteUser(id)`; disabled with tooltip for own row
- "Show inactive users (N)" — `<details>` collapsible at bottom. When expanded, lists deactivated users with a single ↻ Reactivate icon (calls `agentBridge.updateUser(id, { active: true })`)
- Footer info line: "🔒 Passwords are stored scrambled (bcrypt) and can't be displayed. To help a user who forgot theirs, click Edit and set a new one."

### Frontend wiring

```js
// app/assets/js/tools/users/users.js
export async function usersLoad() {
  const user = agentBridge.getCurrentUser();
  if (!user || user.role !== 'admin') {
    switchTool('settings');  // defense in depth — sidebar hides the tab, but a deep link or stale state could land here
    return;
  }
  await renderUserList();
}
```

`switchTool('users')` is added to `app.js`'s view-hide-show block and to the sidebar markup. The existing `loadUserList`, `openAddUserModal`, `openEditUserModal`, `saveUser` functions move from `settings.js` to `users.js` essentially unchanged.

A new function `reactivateUser(id)` wraps `agentBridge.updateUser(id, { active: true })` and re-renders the list on success.

### Delete (deactivate) flow

- Click 🗑️ on a row → confirm dialog: "Deactivate {displayName}? They will lose access immediately. You can reactivate them later from 'Show inactive users'."
- On confirm: `agentBridge.deleteUser(id)` (already exists, hits `DELETE /auth/users/{id}` which soft-deletes). On success, re-render list and show a small toast.
- On failure: surface `result.error` in the same toast slot used by `settingsShowResult`.

### Backend changes

**One small hardening — "can't demote the last admin":**

In `agent/services/database.py` `update_user()`, before applying a `role: 'operator'` change or an `active: false` change to an admin user, count how many active admins remain. If demoting/deactivating this user would leave zero active admins, raise `ValueError("Cannot demote or deactivate the last active admin")`.

`agent/routers/auth.py` `update_existing_user` and `deactivate_user` catch the `ValueError` and return `400` with the message; the frontend already surfaces `result.error`.

### Tests

Add to `agent/tests/test_endpoints.py`:
- `test_list_users_admin_only` — operator JWT gets 403; admin gets 200
- `test_create_user_validation` — short password rejected, invalid role rejected, duplicate username 409
- `test_update_user_password_reset` — admin resets another user's password; that user can then log in with the new password
- `test_deactivate_user_blocks_self` — admin cannot deactivate own ID (existing behavior, regression test)
- `test_reactivate_user` — soft-deleted user can be reactivated via PUT `active: true`
- `test_cannot_demote_last_admin` — single-admin install rejects role change to operator and active=false

---

## Section 3 — Storage tab

### Files

New module: `app/assets/js/tools/storage/storage.js`
New view container: `<div id="storageView">` added to `app/index.html`

The existing storage card markup (`app/index.html:1419-1486`) lifts into the new tab. The existing functions `loadStorageInfo`, `settingsOpenOutputFolder`, `settingsCleanupNow`, `settingsChangeOutputFolder` move from `settings.js` to `storage.js` essentially unchanged. Names that are still referenced via `window.*` (e.g., `window.settingsCleanupNow`) are re-exported under the same global names so existing onclick handlers in the moved HTML keep working without rename.

### Layout

- Page header: `<h1>Storage</h1>` + subtitle "Where your files are saved and how much space they use"
- **Folders card**: two rows
  - Saved merged files — label, path (monospace), size meta, size bar, "📂 Open folder" button aligned right on the same row as the label
  - Temporary app files — same structure, no Open folder button (or a button if `storageDownloadsPath` is a meaningful folder)
- **Auto-cleanup callout** — orange-tinted, identical text + last-cleanup timestamp
- **Actions card** — "🧹 Clean up now" (primary) + "Change output folder…" (ghost) buttons
- Footnote: "Only the Merging Tool saves files to disk. Invoice Sender and Customer Manager don't write to these folders."

### Visibility

Always visible — informational, no admin gating.

### Tests

No new tests — the underlying agent endpoints (`/files/storage-info`, cleanup endpoint) are unchanged and already covered.

---

## Section 4 — Settings cleanup

### What leaves Settings

- `#userManagementSection` → moves to Users tab (Section 2)
- `#storageCard` → moves to Storage tab (Section 3)

### What stays, regrouped

**Connections card** — replaces today's three separately-styled credential blocks (QBO, TMS, Gmail). Single bordered card with three rows:

- **QuickBooks Online** — icon, name, status line ("Connected as NGL Transportation" / "Not connected"), pill (Connected / Not set), action button (Disconnect / Set up)
- **TMS Portal** — icon, name, email shown when configured, pill (Configured / Not set), Edit button (opens a small modal with email + password fields, matching the existing Add/Edit User modal pattern)
- **Gmail (for sending invoices)** — icon, name, "App password not set" / "Configured", pill, Set up button (opens existing Gmail App Password setup with the help text intact)

The existing "Save & Connect" mega-button is removed. Each row owns its own save action when its inline edit is open.

**Preferences card** — small card containing only the Desktop notifications toggle. Subtitle: "Personal settings for this device."

**Advanced** — `<details>` collapsible at the bottom of Settings:
- Summary: "Advanced — Selector Health check"
- Body: "Diagnostic tool. Run Check" button (existing handler)

**Footer line** — replaces the green info banner: "Credentials are saved locally to your agent — never sent online." One small line, muted color.

### CSS

A few new utility classes in `app/assets/css/styles.css`:
- `.connections-card` — wrapper
- `.conn-row` — row inside connections card
- `.conn-icon` — 30px tinted square with service initials
- `.status-pill` (`.pill-ok`, `.pill-warn`) — pill colors
- `.preferences-card` — small toggle card

Existing storage / user-mgmt styles either move to scoped CSS for the new tab modules or get inlined per the project's vanilla-JS-no-build pattern.

---

## Section 5 — Login bootstrap fix

### Problem recap

On fresh installs, `app.js` queries `/auth/setup-required` which calls `get_user_count()` on the **local SQLite** file. Local file is empty on every fresh install, so Setup screen always shows. Setup screen has no Google button (only manual username/password form). New co-workers who should be able to Google sign-in instead:

1. See the Setup screen
2. Get stuck (or create a local-only admin that's invisible to Supabase and to other installs)

### Fix 1 — Add Google button to Setup screen

**`app/index.html`** — Copy the `#googleLoginSection` block from the Login screen (currently around `app/index.html:91`) into the Setup screen (currently around `app/index.html:134-207`). Give it a distinct id `setupGoogleLoginSection` but same internal markup. Same divider, same button label ("Sign in with Google"). The button calls `doGoogleLogin()` — same handler as the Login screen.

**`app/assets/js/app.js`** — In `startup()` before the early `return` on the setup-required branch (around `app.js:289-292`), call `initGoogleSignIn()` and have it un-hide both `#googleLoginSection` and `#setupGoogleLoginSection`. Easiest: change `initGoogleSignIn` to query both ids and un-hide whichever exist.

### Fix 2 — Cloud-aware setup-required check

**`agent/services/database.py`** — Add `sb_get_user_count()` in `supabase_client.py` (returns total count from cloud `users` table). In `_maybe_use_supabase()` override block, add `_self.get_user_count = sb_get_user_count`.

**`agent/routers/auth.py` `setup_required()` endpoint** — Wrap the count check with a fallback:

```python
@router.get("/setup-required")
async def setup_required():
    from services.database import get_user_count
    try:
        count = get_user_count()  # routes to Supabase when configured
    except Exception:
        # Cloud unreachable — fall back to local count so offline users aren't blocked
        from services.database import _get_conn
        count = _get_conn().execute("SELECT COUNT(*) FROM users").fetchone()[0]
    return {"setupRequired": count == 0}
```

The fallback ensures a co-worker without internet still sees Setup (and can create a local admin) rather than getting a 500.

### Fix 3 — First Google user becomes admin

**`agent/routers/auth.py` `google_callback()`** — In the find-or-create branch (around `auth.py:241-256`), when `get_user_by_username(email)` returns None, check whether **this is the very first user in the system**. If yes, create them as `admin`; otherwise create as `operator` (current behavior).

```python
user = get_user_by_username(email)
if not user:
    is_first_user = (get_user_count() == 0)
    role = "admin" if is_first_user else "operator"
    user = create_google_user(email, name, role=role)
    logger.info("Google login: created new %s account for %s", role, email)
```

**`agent/services/database.py` `create_google_user`** — Add an optional `role: str = "operator"` parameter; pass it into the INSERT. Same for `sb_create_google_user` in `supabase_client.py` if that's added (or override `create_google_user` to call `sb_create_user` directly when Supabase is enabled).

> ⚠️ Note: As confirmed in the brainstorm session, this rule will never fire for the current user (Joseph) — Supabase already has admins. It's a safety net for hypothetical clean-slate deploys and removes a latent landmine where no admin could ever be created via Google sign-in.

### Tests

Add to `agent/tests/test_endpoints.py`:
- `test_setup_required_false_when_users_exist` — local count 1 → returns `setupRequired: false`
- `test_setup_required_falls_back_to_local_on_cloud_error` — mock `get_user_count` to raise, assert endpoint still returns 200 with local count

Add to `agent/tests/test_database.py` (or appropriate location):
- `test_create_google_user_defaults_to_operator` — no role arg → role='operator'
- `test_create_google_user_admin_role` — `role='admin'` → role='admin'

Google callback admin-promotion path is harder to test without an actual Google OAuth flow; document the manual test plan in the implementation plan.

---

## Data flow / sequence diagrams

### New co-worker first launch (after this fix ships)

```
1. User installs latest .exe → launches app
2. Frontend startup():
   - /auth/token → loginRequired: true
   - /auth/setup-required → backend calls cloud-aware get_user_count()
     → Supabase has N admins → returns setupRequired: false
   - No saved session → showLogin() + initGoogleSignIn()
3. Login screen with Google button visible
4. Click "Sign in with Google" → @ngltrans.net account
5. /auth/google/callback:
   - get_user_by_username(email) → None (new user)
   - get_user_count() returns N > 0 → not first user
   - create_google_user(email, name, role='operator')
   - JWT issued, frontend polls /auth/google/poll, gets token
6. User is in the app as operator
7. Joseph opens Users tab → promotes them to admin if needed
```

### Offline new install (Supabase unreachable)

```
1. /auth/setup-required → cloud call raises → fallback to local count → 0 → returns setupRequired: true
2. Setup screen with both Google button AND manual form
3. User picks one:
   a. Manual form → create local admin (current behavior) → in
   b. Google button → only works if outbound HTTPS to Google works; if it does, same path as above creates them as admin (first user, get_user_count fell back to local 0)
4. Once online, Joseph can manually clean up duplicate accounts if needed
```

### Admin manages users (after this fix ships)

```
1. Joseph signs in (any method)
2. Sidebar evaluates user.role === 'admin' → shows Users tab
3. Clicks Users tab → /auth/users (GET) → renders list
4. Clicks 🗑️ on Lorena's row → confirm → DELETE /auth/users/{id} → soft delete in Supabase users.active = false
5. Re-render hides Lorena from main list; "Show inactive users (1)" appears
6. Clicks Show inactive → expands → Lorena row with ↻ Reactivate button
7. Clicks ↻ → PUT /auth/users/{id} { active: true } → re-render
```

---

## Error handling & edge cases

| Scenario | Behavior |
|---|---|
| Operator hits `switchTool('users')` via stale state or deep link | `usersLoad()` redirects to `settings`; backend `/auth/users` returns 403 anyway |
| Supabase write fails on Create User | Modal shows `result.error` (existing path), modal stays open for retry |
| Supabase unreachable on Setup check | Fall back to local count; user sees Setup screen and can still proceed |
| Admin tries to demote / deactivate the last admin | Backend returns 400 with message; UI shows error toast |
| Admin tries to deactivate self | Trash icon disabled in UI; backend `auth.py:411` blocks anyway |
| Google button doesn't render despite `GOOGLE_CLIENT_ID` configured | Out of scope here — that's a separate "SDK didn't load" failure mode worth investigating only if reported |
| Existing local admin AND existing Supabase admin (e.g. someone created via Setup before Fix #2 shipped) | Both can log in. Users tab shows whichever set the agent is configured to read. Cleanup is a one-time manual step, not automated by this change. |

---

## Backwards compatibility

- Existing users keep working — no schema migration, no data move
- The Electron auto-updater carries the change to existing installs on next launch
- All `window.*` global function names (`openAddUserModal`, `saveUser`, `settingsCleanupNow`, `settingsOpenOutputFolder`, etc.) are preserved so any leftover onclick HTML keeps working
- The `chassis-finder` internal key is unchanged — only the displayed label changes

---

## File-by-file change summary

**New files:**
- `app/assets/js/tools/users/users.js`
- `app/assets/js/tools/storage/storage.js`

**Modified:**
- `app/index.html` — sidebar items reordered + renamed, two new view containers, Setup-screen Google block added, Settings page restructured (Connections / Preferences / Advanced)
- `app/assets/js/app.js` — `switchTool` wires up `users` and `storage` views; `startup()` calls `initGoogleSignIn()` before showing Setup; `initGoogleSignIn` un-hides both Google sections
- `app/assets/js/tools/settings/settings.js` — user-management functions and storage functions removed (moved to new modules); Connections / Preferences / Advanced rendering added
- `app/assets/css/styles.css` — connections-card / conn-row / status-pill / preferences-card additions; any storage- or user-mgmt-specific CSS that moves with the markup
- `agent/routers/auth.py` — `setup_required` adds cloud-aware count + fallback; `google_callback` checks first-user and passes `role` to `create_google_user`
- `agent/services/database.py` — `create_google_user` adds `role` param; `update_user` adds last-admin guard; `_maybe_use_supabase` overrides `get_user_count`
- `agent/services/supabase_client.py` — add `sb_get_user_count` (or expose existing count) for the override
- `agent/tests/test_endpoints.py` — new test cases listed in Section 2 & 5

**No changes:** Add/Edit User modal HTML (`#userModal`), `agentBridge.{listUsers,createUser,updateUser,deleteUser}`, storage-info endpoints, any merge/invoice-sender/customers code

---

## Release / ship

- Bump `desktop/VERSION` to `2.73.0`
- `runbuild.bat` builds installer + latest.yml
- `git add` + commit + push
- `gh release create v2.73.0` with installer .exe + latest.yml

Once the GitHub release is up, existing installs auto-update on next launch. New co-workers download the latest installer from the release page.
