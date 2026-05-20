# Users + Storage tabs, Settings cleanup, and login bootstrap fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship v2.73.0 with: a new admin-only Users tab, a Storage tab visible to all, restructured Settings page (Connections + Preferences + Advanced collapsible), a sidebar reorder, and the fresh-install login fix so new users can sign in via Google.

**Bundled with this release** (per explicit user instruction):
- Four pre-existing modified files (test debt + storage single-walk + Excel exception narrowing + merge-v2 render purity) — committed first so the working tree is clean.
- The merge-errors XLSX cleanup — executed via its own plan, then folded into this release.

**Architecture:**
- Frontend stays vanilla ES modules (no build step). New tool modules `tools/users/users.js` and `tools/storage/storage.js` follow the existing `tools/<name>/<name>.js` pattern. Per-tool view containers added to `index.html`. `switchTool()` routes to them.
- Backend changes are limited to `agent/routers/auth.py`, `agent/services/database.py`, and `agent/services/supabase_client.py`. Add `sb_get_user_count` and override it via `_maybe_use_supabase`. `setup_required` becomes cloud-aware with a local fallback for offline use. `create_google_user` accepts a `role` arg, set to `admin` when the system has zero users.
- Soft-delete and reactivate already exist on the backend (`DELETE /auth/users/{id}` and `PUT /auth/users/{id} {active: true}`); the UI just gains the buttons that call them. Add one new backend guard: last-admin protection in `update_user`.

**Tech Stack:** Vanilla HTML / CSS / ES module JS (no framework, no build). Python 3 / FastAPI agent. SQLite (local) and Supabase (cloud) via the existing `_maybe_use_supabase` override pattern. bcrypt for passwords. JWT (HS256) for sessions. pytest for agent-side tests.

**Spec:** `docs/superpowers/specs/2026-05-19-users-storage-tabs-and-login-bootstrap-design.md`

**Rollout:** Bundle into v2.73.0. Standard ship pipeline applies: bump VERSION → `runbuild.bat` → commit + push → `gh release create` with installer + `latest.yml`.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `app/assets/js/tools/users/users.js` | Users-tab state, list rendering, deactivate/reactivate handlers, gate check | **Create** |
| `app/assets/js/tools/storage/storage.js` | Storage-tab rendering, lifts existing storage functions out of settings.js | **Create** |
| `app/index.html` | Sidebar items, view containers, Setup screen Google block, Settings page restructure | Modify |
| `app/assets/js/app.js` | `switchTool` adds `users` and `storage` cases; `startup()` calls `initGoogleSignIn()` before `showSetup()`; `initGoogleSignIn` un-hides both Google sections | Modify |
| `app/assets/js/tools/settings/settings.js` | Remove user-mgmt + storage code (moved); render new Connections + Preferences + Advanced layout | Modify |
| `app/assets/css/styles.css` | Add `.connections-card`, `.conn-row`, `.conn-icon`, `.status-pill`, `.preferences-card`, `.users-tab-*`, `.storage-tab-*` utility classes | Modify |
| `agent/routers/auth.py` | Cloud-aware `setup_required` with fallback; `google_callback` sets admin role for first user | Modify |
| `agent/services/database.py` | `create_google_user` accepts `role`; `update_user` adds last-admin guard; `_maybe_use_supabase` overrides `get_user_count` | Modify |
| `agent/services/supabase_client.py` | Add `sb_get_user_count`; optionally `sb_create_google_user` if needed | Modify |
| `agent/tests/test_endpoints.py` | Tests for setup-required fallback, user CRUD admin gating, last-admin guard, reactivation | Modify |
| `desktop/VERSION` | Bump to `2.73.0` | Modify (final task) |

---

## Phase A — Pre-flight (clear the working tree)

### Task 1: Commit the 4 pre-existing cleanup-bundle files

**Files:**
- Stage: `agent/tests/test_job_manager/test_fetch_job_tms_fallback.py` (BOL → BL test fix)
- Stage: `agent/services/storage.py` (single-walk optimization)
- Stage: `agent/services/excel_converter.py` (narrower COM exception)
- Stage: `app/assets/js/tools/merge/merge-v2.js` (manual-upload moved out of render)

- [ ] **Step 1.1: Verify the bundle still applies cleanly**

Run: `cd "C:/Users/Joseph/Desktop/NGL ACCOUNTING SERVICE" && git status --short | grep -E '^\s*M\s'`

Expected output (exactly these 4 lines, no other modified files):
```
 M agent/services/excel_converter.py
 M agent/services/storage.py
 M agent/tests/test_job_manager/test_fetch_job_tms_fallback.py
 M app/assets/js/tools/merge/merge-v2.js
```

If any other tracked file is modified, **stop and ask the user** — there may be uncommitted work from a different track. Do not commit unknown changes.

- [ ] **Step 1.2: Run the full agent test suite to confirm green**

Run: `cd "C:/Users/Joseph/Desktop/NGL ACCOUNTING SERVICE/agent" && python -m pytest -q 2>&1 | tail -20`

Expected: `309 passed` (or whatever the current count is — must be all-green; no failures).

- [ ] **Step 1.3: Commit the cleanup bundle**

```bash
cd "C:/Users/Joseph/Desktop/NGL ACCOUNTING SERVICE"
git add agent/tests/test_job_manager/test_fetch_job_tms_fallback.py \
  agent/services/storage.py \
  agent/services/excel_converter.py \
  app/assets/js/tools/merge/merge-v2.js
git commit -m "$(cat <<'EOF'
chore(cleanup): test debt + storage walk + COM exception + render purity

- Update test_fetch_job_tms_fallback.py BOL→BL strings (TMS labels the
  doc as "BL" not "BOL"; tests were stuck on the old string after v2.59
  rename).
- storage.get_storage_info() walks Merge Outputs/ once instead of twice.
- excel_converter narrows except Exception around PrintTitleRows to
  pywintypes.com_error with a debug log; real bugs won't be hidden.
- merge-v2 moves manual-upload documents.push out of renderResolvedBody
  into v2HandleSidebarUpload — render functions don't mutate data.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Expected: commit succeeds, `git status --short` shows no modified tracked files (untracked noise like `scratch/` and `app/AR_AGING_assets/` is fine).

---

## Phase B — Merge-errors XLSX cleanup

### Task 2: Execute the existing merge-errors XLSX cleanup plan

The merge-errors XLSX cleanup is already brainstormed, spec'd, and planned in:

- Spec: `docs/superpowers/specs/2026-05-19-merge-errors-xlsx-cleanup-design.md`
- Plan: `docs/superpowers/plans/2026-05-19-merge-errors-xlsx-cleanup.md`

That plan also touches `app/assets/js/tools/merge/merge-v2.js`, but the area is around line 2628 (`buildErrorExportRows`) — independent from the cleanup-bundle change at the manual-upload site. Running it after Phase A means it sees a clean tree.

- [ ] **Step 2.1: Read the merge-errors plan in full**

Run: `cat "docs/superpowers/plans/2026-05-19-merge-errors-xlsx-cleanup.md"`

Confirm you understand the single-function rewrite scope. Don't start implementing yet.

- [ ] **Step 2.2: Execute the merge-errors plan task-by-task**

Follow that document's tasks exactly. The plan's instructions say "commit, don't ship" — that's correct for this bundle too. When the plan's final commit is in, return here for Phase C.

- [ ] **Step 2.3: Verify state before continuing**

Run: `git log --oneline -5`

Expected: top commit is the cleanup-bundle (Task 1), then below it the merge-errors XLSX cleanup commit(s) from the merge-errors plan. Working tree should be clean.

(If you executed Phase B first instead of Phase A, that's fine too — both orders work; the assertion is "both are committed and tree is clean.")

---

## Phase C — Backend: cloud-aware user count + Google admin promotion

### Task 3: Add `sb_get_user_count` to supabase_client.py

**Files:**
- Modify: `agent/services/supabase_client.py`

- [ ] **Step 3.1: Read the existing user query patterns**

Run: `grep -n 'def sb_' "agent/services/supabase_client.py" | head -20`

Note the function signature style for the other `sb_*` user functions (`sb_authenticate_user`, `sb_list_users`, etc.) — match that.

- [ ] **Step 3.2: Add the new helper near the existing user functions**

In `agent/services/supabase_client.py`, add immediately above `sb_create_user` (which is around line 485):

```python
def sb_get_user_count() -> int:
    """Return the total number of users in Supabase (active + inactive)."""
    params = "select=id&limit=1"
    resp = httpx.head(
        f"{_BASE}/users?{params}",
        headers={**_HEADERS, "Prefer": "count=exact"},
        timeout=_TIMEOUT,
    )
    _check_response(resp, "get_user_count")
    # Supabase returns the count in the content-range header: "0-0/N"
    content_range = resp.headers.get("content-range", "0-0/0")
    try:
        return int(content_range.split("/")[-1])
    except (ValueError, IndexError):
        return 0
```

- [ ] **Step 3.3: Wire into the `_maybe_use_supabase` override block**

In `agent/services/database.py` around line 916 (the user-functions override block), add to the import list at line 850-857:

```python
            sb_get_user_count,
```

And add to the override block at line 916-922:

```python
    _self.get_user_count = sb_get_user_count
```

- [ ] **Step 3.4: Smoke-check the import wiring**

Run: `cd agent && python -c "from services.database import get_user_count; print(type(get_user_count))"`

Expected: prints `<class 'function'>` without ImportError.

- [ ] **Step 3.5: Commit**

```bash
git add agent/services/supabase_client.py agent/services/database.py
git commit -m "feat(agent): cloud-aware get_user_count via Supabase

Adds sb_get_user_count() that queries the Supabase users table count
via the content-range header (HEAD + count=exact). Wired into the
_maybe_use_supabase override block so the existing get_user_count()
calls route to Supabase when configured, local SQLite otherwise.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Make `/auth/setup-required` fall back to local count on cloud error

**Files:**
- Modify: `agent/routers/auth.py` (around line 111-115)
- Test: `agent/tests/test_endpoints.py` (add cases)

- [ ] **Step 4.1: Write the failing tests first**

Append to `agent/tests/test_endpoints.py`:

```python
def test_setup_required_false_when_users_exist(client):
    """When at least one user exists, setupRequired is False."""
    # Fixture client already creates a default admin (see conftest.py)
    res = client.get("/auth/setup-required")
    assert res.status_code == 200
    assert res.json() == {"setupRequired": False}


def test_setup_required_falls_back_to_local_on_cloud_error(client, monkeypatch):
    """If get_user_count() raises (e.g. Supabase unreachable), we fall back to the local count."""
    import services.database as db
    original = db.get_user_count

    def boom():
        raise RuntimeError("simulated cloud outage")

    monkeypatch.setattr(db, "get_user_count", boom)
    res = client.get("/auth/setup-required")
    assert res.status_code == 200
    body = res.json()
    assert "setupRequired" in body
    assert isinstance(body["setupRequired"], bool)
    # Restore not strictly needed (monkeypatch auto-unpatches)
    db.get_user_count = original
```

- [ ] **Step 4.2: Run the tests to verify they fail (or pass for the first one)**

Run: `cd agent && python -m pytest tests/test_endpoints.py::test_setup_required_falls_back_to_local_on_cloud_error -v`

Expected: FAIL — currently `setup_required` doesn't catch the exception, so the request returns 500. The fallback isn't implemented yet.

- [ ] **Step 4.3: Implement the cloud-aware-with-fallback logic**

In `agent/routers/auth.py`, replace the existing `setup_required` (around line 111-115):

```python
@router.get("/setup-required")
async def setup_required():
    """Check if first-run setup is needed (no users exist yet).

    Uses the cloud-aware get_user_count() when Supabase is configured.
    Falls back to a direct local SQLite count if the cloud call raises
    so offline users aren't blocked at startup.
    """
    from services.database import get_user_count
    try:
        count = get_user_count()
    except Exception as e:
        logger.warning("get_user_count failed, falling back to local count: %s", e)
        try:
            from services.database import _get_conn
            count = _get_conn().execute("SELECT COUNT(*) FROM users").fetchone()[0]
        except Exception as inner:
            logger.error("Local fallback count also failed: %s", inner)
            count = 0  # Safest default — show Setup so user can at least create local admin
    return {"setupRequired": count == 0}
```

- [ ] **Step 4.4: Run the tests to verify they pass**

Run: `cd agent && python -m pytest tests/test_endpoints.py::test_setup_required_falls_back_to_local_on_cloud_error tests/test_endpoints.py::test_setup_required_false_when_users_exist -v`

Expected: both PASS.

- [ ] **Step 4.5: Run the full test suite to confirm no regressions**

Run: `cd agent && python -m pytest -q 2>&1 | tail -5`

Expected: all tests pass (309+2 new = 311 or whatever the baseline is plus 2).

- [ ] **Step 4.6: Commit**

```bash
git add agent/routers/auth.py agent/tests/test_endpoints.py
git commit -m "feat(agent): setup-required is cloud-aware with local fallback

Now queries the cloud user count when Supabase is configured (via the
override added in the prior commit). Falls back to a direct local
SQLite count if the cloud call raises, so offline installs aren't
blocked. Adds two endpoint tests covering both paths.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Add `role` parameter to `create_google_user`

**Files:**
- Modify: `agent/services/database.py` (around line 732)
- Test: `agent/tests/test_database.py` (or `test_endpoints.py` if no separate file exists)

- [ ] **Step 5.1: Check which test file is appropriate**

Run: `ls agent/tests/test_database* 2>/dev/null; ls agent/tests/test_utils.py`

If `test_database.py` exists, use it. Otherwise add the tests to `test_endpoints.py` under a "Google user creation" section.

- [ ] **Step 5.2: Write the failing tests**

Append (to the chosen file):

```python
def test_create_google_user_defaults_to_operator(monkeypatch, tmp_path):
    """Without an explicit role, create_google_user creates an operator."""
    from services import database as db
    # Test on a clean local DB
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "test.db")
    db._conn = None  # reset cached connection
    db.init_db()
    user = db.create_google_user("test1@ngltrans.net", "Test One")
    assert user["role"] == "operator"


def test_create_google_user_admin_role(monkeypatch, tmp_path):
    """When role='admin' is passed, the user is created as admin."""
    from services import database as db
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "test.db")
    db._conn = None
    db.init_db()
    user = db.create_google_user("test2@ngltrans.net", "Test Two", role="admin")
    assert user["role"] == "admin"
```

- [ ] **Step 5.3: Run the tests to verify they fail**

Run: `cd agent && python -m pytest tests/test_endpoints.py::test_create_google_user_admin_role -v`

Expected: FAIL with `TypeError: create_google_user() got an unexpected keyword argument 'role'`.

- [ ] **Step 5.4: Implement the role parameter**

In `agent/services/database.py` around line 732, replace the existing `create_google_user`:

```python
def create_google_user(email: str, display_name: str, role: str = "operator") -> dict:
    """Create an account for a Google-authenticated user (no password).

    Defaults to 'operator'. Callers can pass role='admin' when the system
    has zero users (the first Google sign-in bootstraps the admin).
    """
    if role not in ("admin", "operator"):
        raise ValueError(f"Invalid role: {role!r}")
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    pw_hash = bcrypt.hashpw(secrets.token_bytes(32), bcrypt.gensalt()).decode("utf-8")
    conn.execute("""
        INSERT INTO users (username, display_name, password_hash, role, active, created_at, updated_at)
        VALUES (?, ?, ?, ?, 1, ?, ?)
    """, (email, display_name, pw_hash, role, now, now))
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (email,)).fetchone()
    return _row_to_user(row)
```

- [ ] **Step 5.5: Run the tests to verify they pass**

Run: `cd agent && python -m pytest tests/test_endpoints.py::test_create_google_user_admin_role tests/test_endpoints.py::test_create_google_user_defaults_to_operator -v`

Expected: both PASS.

- [ ] **Step 5.6: Commit**

```bash
git add agent/services/database.py agent/tests/test_endpoints.py
git commit -m "feat(agent): create_google_user accepts role parameter

Defaults to 'operator' (preserves existing behavior). Allows callers to
pass role='admin' for first-user bootstrap. Validates role is one of
admin|operator. Two unit tests cover default and explicit-admin paths.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: First Google sign-in becomes admin

**Files:**
- Modify: `agent/routers/auth.py` (`google_callback`, around line 241-256)

- [ ] **Step 6.1: Edit the find-or-create block in google_callback**

In `agent/routers/auth.py` around line 241-246, replace:

```python
    # Find or create user
    from services.database import get_user_by_username, create_google_user
    user = get_user_by_username(email)
    if not user:
        user = create_google_user(email, name)
        logger.info("Google login: created new operator account for %s", email)
```

With:

```python
    # Find or create user
    from services.database import get_user_by_username, create_google_user, get_user_count
    user = get_user_by_username(email)
    if not user:
        # First user in the system gets admin role; everyone else is operator.
        is_first_user = (get_user_count() == 0)
        new_role = "admin" if is_first_user else "operator"
        user = create_google_user(email, name, role=new_role)
        logger.info("Google login: created new %s account for %s", new_role, email)
```

- [ ] **Step 6.2: Verify the file parses (Python import check)**

Run: `cd agent && python -c "from routers import auth"`

Expected: no output, no error.

- [ ] **Step 6.3: Document the manual test plan (no automated test for OAuth flow)**

Add a comment block at the top of `agent/routers/auth.py` near the existing Google routes or in a new `MANUAL_TESTING.md` if you prefer, documenting:

```
Manual test plan for first-user admin promotion:
1. Wipe local DB: delete agent/data/ngl.db (or move it aside)
2. Ensure SUPABASE_URL is unset OR Supabase users table is empty
3. Start agent + open the app
4. Click "Sign in with Google" (on Setup screen, after Phase E lands)
5. Sign in with an @ngltrans.net account
6. Verify the new user has role='admin':
   sqlite3 agent/data/ngl.db "SELECT username, role FROM users"
```

(If you don't want to introduce a new docs file, skip the doc and just rely on the spec's manual-test plan. Either is fine — it's an unautomatable scenario.)

- [ ] **Step 6.4: Commit**

```bash
git add agent/routers/auth.py
git commit -m "feat(agent): first Google sign-in bootstraps admin role

When the Google callback creates a brand-new user (get_user_by_username
returns None) AND the system has zero users (get_user_count() == 0),
the new account is created as admin instead of operator. After that
first user, all subsequent Google sign-ins are operators as before.

This closes the latent bootstrap landmine where a clean-slate deploy
could never produce an admin via Google sign-in.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Last-admin guard in `update_user` (deactivate + demote protection)

**Files:**
- Modify: `agent/services/database.py` (`update_user`, around line 780-810)
- Test: `agent/tests/test_endpoints.py`

- [ ] **Step 7.1: Read the existing `update_user` to find the right insertion point**

Run: `grep -n 'def update_user' agent/services/database.py`

Note the function start. You'll add the guard immediately after the existing input validation and before any SQL execution.

- [ ] **Step 7.2: Write the failing test**

Append to `agent/tests/test_endpoints.py`:

```python
def test_cannot_demote_last_admin(client, admin_token):
    """The only active admin cannot be demoted to operator."""
    # Fixture admin_token is for the only admin user (id=1)
    res = client.put(
        "/auth/users/1",
        json={"role": "operator"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res.status_code == 400
    assert "last active admin" in res.json()["detail"].lower()


def test_cannot_deactivate_last_admin(client, admin_token):
    """The only active admin cannot be deactivated (separate from the self-deactivation block)."""
    res = client.put(
        "/auth/users/1",
        json={"active": False},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res.status_code == 400
    assert "last active admin" in res.json()["detail"].lower()
```

If `admin_token` fixture doesn't exist yet in `conftest.py`, add it now:

```python
@pytest.fixture
def admin_token(client):
    """JWT for the default admin fixture user."""
    res = client.post("/auth/login", json={"username": "admin", "password": "admin"})
    assert res.status_code == 200, res.text
    return res.json()["token"]
```

(Adjust username/password to whatever the existing test fixture creates — search `conftest.py` first.)

- [ ] **Step 7.3: Run the tests to verify they fail**

Run: `cd agent && python -m pytest tests/test_endpoints.py::test_cannot_demote_last_admin tests/test_endpoints.py::test_cannot_deactivate_last_admin -v`

Expected: FAIL — backend currently allows demoting/deactivating the last admin.

- [ ] **Step 7.4: Add the guard in `update_user`**

In `agent/services/database.py` `update_user`, after the input validation and before the SQL UPDATE executes, add:

```python
    # Last-admin guard: prevent demoting or deactivating the only active admin.
    if "role" in data and data["role"] == "operator":
        current = conn.execute(
            "SELECT role FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if current and current["role"] == "admin":
            active_admin_count = conn.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1"
            ).fetchone()[0]
            if active_admin_count <= 1:
                raise ValueError("Cannot demote the last active admin")

    if "active" in data and data["active"] is False:
        current = conn.execute(
            "SELECT role, active FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if current and current["role"] == "admin" and current["active"]:
            active_admin_count = conn.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1"
            ).fetchone()[0]
            if active_admin_count <= 1:
                raise ValueError("Cannot deactivate the last active admin")
```

- [ ] **Step 7.5: Surface the ValueError in the API**

In `agent/routers/auth.py` `update_existing_user` (around line 376-401), wrap the `update_user(user_id, update_data)` call in a try/except:

```python
    from services.database import update_user
    try:
        user = update_user(user_id, update_data)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"detail": str(e)})

    if not user:
        return JSONResponse(status_code=404, content={"detail": "User not found"})
```

- [ ] **Step 7.6: Mirror the guard for Supabase (`sb_update_user`)**

In `agent/services/supabase_client.py` `sb_update_user`, add the same guard at the top of the function (since the cloud path bypasses the local guard when Supabase is configured):

```python
def sb_update_user(user_id: int, data: dict) -> Optional[dict]:
    """Update user fields in Supabase."""
    import bcrypt

    # Last-admin guard (same rule as local update_user)
    if (data.get("role") == "operator") or (data.get("active") is False):
        current = sb_get_user_by_id(user_id)
        if current and current.get("role") == "admin" and current.get("active"):
            all_users = sb_list_users(active_only=True)
            active_admins = [u for u in all_users if u.get("role") == "admin"]
            if len(active_admins) <= 1:
                if data.get("role") == "operator":
                    raise ValueError("Cannot demote the last active admin")
                if data.get("active") is False:
                    raise ValueError("Cannot deactivate the last active admin")

    # ... existing function body unchanged ...
```

- [ ] **Step 7.7: Run the tests to verify they pass**

Run: `cd agent && python -m pytest tests/test_endpoints.py::test_cannot_demote_last_admin tests/test_endpoints.py::test_cannot_deactivate_last_admin -v`

Expected: both PASS.

- [ ] **Step 7.8: Run the full suite to confirm no regressions**

Run: `cd agent && python -m pytest -q 2>&1 | tail -5`

Expected: all pass.

- [ ] **Step 7.9: Commit**

```bash
git add agent/services/database.py agent/services/supabase_client.py agent/routers/auth.py agent/tests/test_endpoints.py agent/tests/conftest.py
git commit -m "feat(agent): last-admin guard on demote and deactivate

Prevents demoting or deactivating the only active admin in the system.
Implemented in both the SQLite update_user and the Supabase
sb_update_user paths. The router surfaces the ValueError as a 400 with
a plain-English message. Two endpoint tests cover both attempts.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Smoke tests for the existing user-CRUD admin gating

**Files:**
- Modify: `agent/tests/test_endpoints.py`

These tests verify existing behavior — they aren't fixing a bug, just preventing regressions when the new Users tab starts hitting these endpoints harder.

- [ ] **Step 8.1: Add tests**

Append to `agent/tests/test_endpoints.py`:

```python
def test_list_users_admin_only(client, operator_token, admin_token):
    """Operator JWT gets 403 on GET /auth/users; admin gets 200."""
    res = client.get("/auth/users", headers={"Authorization": f"Bearer {operator_token}"})
    assert res.status_code == 403

    res = client.get("/auth/users", headers={"Authorization": f"Bearer {admin_token}"})
    assert res.status_code == 200
    assert "users" in res.json()


def test_create_user_validation(client, admin_token):
    """Short password rejected, invalid role rejected, duplicate username 409."""
    h = {"Authorization": f"Bearer {admin_token}"}

    # Short password
    res = client.post("/auth/users", json={"username": "shortpw", "password": "abc", "role": "operator"}, headers=h)
    assert res.status_code == 400

    # Invalid role
    res = client.post("/auth/users", json={"username": "badrole", "password": "abcd", "role": "superuser"}, headers=h)
    assert res.status_code == 400

    # Duplicate username
    client.post("/auth/users", json={"username": "dupe", "password": "abcd", "role": "operator"}, headers=h)
    res = client.post("/auth/users", json={"username": "dupe", "password": "abcd", "role": "operator"}, headers=h)
    assert res.status_code == 409


def test_update_user_password_reset_allows_login(client, admin_token):
    """Admin resets another user's password; that user can then log in with the new password."""
    h = {"Authorization": f"Bearer {admin_token}"}
    # Create a user
    create_res = client.post("/auth/users", json={"username": "resetme", "password": "old1234", "role": "operator"}, headers=h)
    user_id = create_res.json()["id"]

    # Reset their password
    res = client.put(f"/auth/users/{user_id}", json={"password": "new5678"}, headers=h)
    assert res.status_code == 200

    # Old password should fail
    res = client.post("/auth/login", json={"username": "resetme", "password": "old1234"})
    assert res.status_code == 401

    # New password should work
    res = client.post("/auth/login", json={"username": "resetme", "password": "new5678"})
    assert res.status_code == 200


def test_reactivate_user(client, admin_token):
    """A deactivated user can be reactivated via PUT active=True."""
    h = {"Authorization": f"Bearer {admin_token}"}
    create_res = client.post("/auth/users", json={"username": "reactme", "password": "abcd", "role": "operator"}, headers=h)
    user_id = create_res.json()["id"]

    client.delete(f"/auth/users/{user_id}", headers=h)  # soft-delete
    res = client.put(f"/auth/users/{user_id}", json={"active": True}, headers=h)
    assert res.status_code == 200
    assert res.json()["active"] is True


def test_deactivate_user_blocks_self(client, admin_token):
    """Admin cannot deactivate their own account (regression test for existing behavior)."""
    # Admin token is for user id=1 (the fixture admin)
    res = client.delete("/auth/users/1", headers={"Authorization": f"Bearer {admin_token}"})
    assert res.status_code == 400
```

If `operator_token` fixture doesn't exist in `conftest.py`, add it:

```python
@pytest.fixture
def operator_token(client, admin_token):
    """Create a fresh operator account and return its JWT."""
    h = {"Authorization": f"Bearer {admin_token}"}
    client.post("/auth/users", json={"username": "opfixture", "password": "abcd", "role": "operator"}, headers=h)
    res = client.post("/auth/login", json={"username": "opfixture", "password": "abcd"})
    return res.json()["token"]
```

- [ ] **Step 8.2: Run them**

Run: `cd agent && python -m pytest tests/test_endpoints.py -k "test_list_users_admin_only or test_create_user_validation or test_update_user_password_reset_allows_login or test_reactivate_user or test_deactivate_user_blocks_self" -v`

Expected: all PASS (these test existing backend behavior — if any fail, you've uncovered a real regression to fix).

- [ ] **Step 8.3: Commit**

```bash
git add agent/tests/test_endpoints.py agent/tests/conftest.py
git commit -m "test(agent): smoke tests for user-CRUD admin gating

Covers the endpoints the new Users tab will hit: list (admin-only),
create (validation + dupe), update (password reset → login), DELETE
(self-block), PUT active=True (reactivate). Five endpoint tests +
operator_token fixture.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase D — Frontend: new tabs (Users + Storage)

### Task 9: Create the Users tab module skeleton

**Files:**
- Create: `app/assets/js/tools/users/users.js`

- [ ] **Step 9.1: Create the directory and file**

```bash
mkdir -p "app/assets/js/tools/users"
```

Then create `app/assets/js/tools/users/users.js` with:

```javascript
// ══════════════════════════════════════════════════════════
//  USERS TAB — admin-only user management
// ══════════════════════════════════════════════════════════
import { state } from '../../shared/state.js';
import { agentBridge } from '../../shared/agent-client.js';

export async function usersLoad() {
  const user = agentBridge.getCurrentUser();
  if (!user || user.role !== 'admin') {
    // Defense in depth — sidebar hides the tab, but a deep-link or stale state could land here
    if (typeof window.switchTool === 'function') window.switchTool('settings');
    return;
  }

  if (!state.agentConnected) {
    const c = document.getElementById('userListV2');
    if (c) c.innerHTML = '<div style="color:#94a3b8; font-size:0.85rem;">Agent offline.</div>';
    return;
  }

  await renderUserList();
}

async function renderUserList() {
  const container = document.getElementById('userListV2');
  if (!container) return;

  const result = await agentBridge.listUsers();
  if (result.error) {
    container.innerHTML = `<div style="color:#dc2626; font-size:0.82rem;">Failed to load users: ${result.error}</div>`;
    return;
  }

  const users = result.users || [];
  const current = agentBridge.getCurrentUser();
  const active = users.filter(u => u.active);
  const inactive = users.filter(u => !u.active);

  const rows = active.map(u => renderUserRow(u, current, false)).join('');
  const inactiveBlock = inactive.length
    ? `<details style="margin-top:14px; font-size:0.82rem; color:#64748b;">
         <summary style="cursor:pointer; padding:8px 0;">Show inactive users (${inactive.length})</summary>
         <div style="margin-top:6px;">${inactive.map(u => renderUserRow(u, current, true)).join('')}</div>
       </details>` : '';

  container.innerHTML = `
    ${rows || '<div style="color:#94a3b8; font-size:0.85rem; padding:14px 0;">No active users.</div>'}
    ${inactiveBlock}
    <div style="font-size:0.7rem; color:#94a3b8; margin-top:14px; padding:10px 12px; background:#fff; border-radius:8px; border:1px dashed #e2e8f0;">
      🔒 Passwords are stored scrambled (bcrypt) and can't be displayed. To help a user who forgot theirs, click <strong>Edit</strong> and set a new one.
    </div>`;
}

function renderUserRow(u, currentUser, isInactive) {
  const isYou = currentUser && currentUser.id === u.id;
  const initials = (u.displayName || u.username || '?')
    .split(/\s+/).map(w => w[0]).slice(0, 2).join('').toUpperCase();
  const roleBadge = u.role === 'admin'
    ? '<span style="background:#fef3c7; color:#92400e; padding:2px 8px; border-radius:4px; font-size:0.7rem; font-weight:600;">ADMIN</span>'
    : '<span style="background:#e0e7ff; color:#3730a3; padding:2px 8px; border-radius:4px; font-size:0.7rem; font-weight:600;">OPERATOR</span>';

  const editBtn = `<button onclick="window.openEditUserModal(${u.id}, '${escAttr(u.username)}', '${escAttr(u.displayName || '')}', '${u.role}', ${u.active})"
        style="background:none; border:1px solid transparent; cursor:pointer; padding:6px; border-radius:6px; color:#64748b;"
        title="Edit user (also resets password)">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
        </svg></button>`;

  let actionBtn;
  if (isInactive) {
    actionBtn = `<button onclick="window.reactivateUser(${u.id})"
        style="background:none; border:1px solid transparent; cursor:pointer; padding:6px; border-radius:6px; color:#16a34a;"
        title="Reactivate user">↻</button>`;
  } else if (isYou) {
    actionBtn = `<button disabled style="background:none; border:1px solid transparent; padding:6px; border-radius:6px; color:#cbd5e1; cursor:not-allowed;"
        title="You can't deactivate yourself">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/>
        </svg></button>`;
  } else {
    actionBtn = `<button onclick="window.confirmDeactivateUser(${u.id}, '${escAttr(u.displayName || u.username)}')"
        style="background:none; border:1px solid transparent; cursor:pointer; padding:6px; border-radius:6px; color:#64748b;"
        title="Deactivate user"
        onmouseover="this.style.color='#dc2626'; this.style.background='#fef2f2'; this.style.borderColor='#fecaca';"
        onmouseout="this.style.color='#64748b'; this.style.background='none'; this.style.borderColor='transparent';">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/>
        </svg></button>`;
  }

  const opacity = isInactive ? '0.6' : '1';
  const statusColor = u.active ? '#16a34a' : '#94a3b8';
  const statusText = u.active ? 'Active' : 'Inactive';

  return `
    <div style="opacity:${opacity}; background:#fff; border:1px solid #e2e8f0; border-radius:10px; padding:12px 14px; display:flex; align-items:center; gap:12px; margin-bottom:8px;">
      <div style="width:36px; height:36px; background:${u.active ? '#f0f9ff' : '#f1f5f9'}; border-radius:9px; display:flex; align-items:center; justify-content:center; font-weight:700; color:${u.active ? '#2563eb' : '#94a3b8'}; font-size:0.85rem;">${initials}</div>
      <div style="flex:1; min-width:0;">
        <div style="font-size:0.88rem; font-weight:600; color:#0f172a;">
          ${escHtml(u.displayName || u.username)}${isYou ? ' <span style="color:#94a3b8; font-weight:400;">(you)</span>' : ''}
        </div>
        <div style="font-size:0.72rem; color:#64748b; margin-top:2px;">@${escHtml(u.username)} · <span style="color:${statusColor};">●</span> ${statusText}</div>
      </div>
      ${roleBadge}
      <div style="display:flex; gap:4px; margin-left:8px;">${editBtn}${actionBtn}</div>
    </div>`;
}

// ── Action handlers exposed on window ──

async function confirmDeactivateUser(id, displayName) {
  const ok = confirm(`Deactivate ${displayName}? They will lose access immediately. You can reactivate them later from "Show inactive users".`);
  if (!ok) return;
  const res = await agentBridge.deleteUser(id);
  if (res.error) {
    alert(`Failed to deactivate user: ${res.error}`);
    return;
  }
  await renderUserList();
}

async function reactivateUser(id) {
  const res = await agentBridge.updateUser(id, { active: true });
  if (res.error) {
    alert(`Failed to reactivate user: ${res.error}`);
    return;
  }
  await renderUserList();
}

// Simple escape helpers — `escHtml` exists in shared/utils.js; re-declared locally
// to keep this module self-contained for any consumers who load it standalone.
function escHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
}
function escAttr(s) {
  return String(s ?? '').replace(/'/g, "\\'").replace(/"/g, '&quot;');
}

// Expose action handlers on window so inline onclick="" can call them
window.confirmDeactivateUser = confirmDeactivateUser;
window.reactivateUser = reactivateUser;
```

- [ ] **Step 9.2: JS syntax check**

Run: `node -e "require('fs').readFileSync('app/assets/js/tools/users/users.js', 'utf8'); console.log('syntax OK')"`

Then a real parse check:

Run: `node --check app/assets/js/tools/users/users.js`

Expected: no output, exit 0. (If the project has `desktop/check-js.js`, run that too — see memory `reference_build_js_check.md`.)

- [ ] **Step 9.3: Commit**

```bash
git add app/assets/js/tools/users/users.js
git commit -m "feat(app): create Users tab module skeleton

New ES module at tools/users/users.js. Renders user list (active in
main view, inactive in a collapsible), edit + deactivate icons per row,
reactivate icon for inactive rows. Admin gate redirects non-admins to
Settings. Exposes confirmDeactivateUser + reactivateUser on window for
inline onclick handlers. Markup pattern matches existing settings.js
loadUserList. No HTML view container yet — that's the next task.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Add the Users view container and wire `switchTool('users')`

**Files:**
- Modify: `app/index.html` (add view container)
- Modify: `app/assets/js/app.js` (switchTool + import)

- [ ] **Step 10.1: Add the view container to index.html**

In `app/index.html`, immediately after the existing view containers (e.g. after `<div id="settingsView">`'s closing tag — search for the existing settingsView block to find the right spot), insert:

```html
<!-- Users tab (admin-only) -->
<div id="usersView" style="display:none; padding:24px 32px;">
  <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:18px;">
    <div>
      <h1 style="font-size:1.2rem; font-weight:800; color:#0f172a; margin:0;">Users</h1>
      <p style="font-size:0.78rem; color:#94a3b8; margin:4px 0 0;">Manage who has access to NGL Accounting</p>
    </div>
    <button class="btn btn-primary" onclick="window.openAddUserModal&&window.openAddUserModal()" style="padding:8px 14px; font-size:0.8rem;">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
      </svg>
      Add User
    </button>
  </div>
  <div id="userListV2"></div>
</div>
```

- [ ] **Step 10.2: Wire switchTool in app.js**

Open `app/assets/js/app.js`. Find the `switchTool(tool)` function (around line 341). Add to the hide-all-views block:

```javascript
  document.getElementById('usersView').style.display = 'none';
```

Find the switch/if-chain that activates each view based on `tool` and add:

```javascript
  } else if (tool === 'users') {
    document.getElementById('usersView').style.display = '';
    document.getElementById('navUsers').classList.add('active');
    import('./tools/users/users.js').then(m => m.usersLoad());
```

(Pattern matches how Settings and other tools are already loaded. Look at the existing `tool === 'settings'` branch for the exact style.)

- [ ] **Step 10.3: Smoke-check JS syntax**

Run: `node --check app/assets/js/app.js`

Expected: exit 0.

- [ ] **Step 10.4: Commit**

```bash
git add app/index.html app/assets/js/app.js
git commit -m "feat(app): add Users view container and switchTool wiring

New #usersView container in index.html with page header + Add User
button + #userListV2 list mount. switchTool('users') hides other views,
shows usersView, calls usersLoad() from the dynamically-imported users
module. No sidebar item yet — that lands in the sidebar restructure
task.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Move user-management functions from settings.js into users.js

**Files:**
- Modify: `app/assets/js/tools/settings/settings.js` (remove user-mgmt code, lines ~12-19 and 267-376 and 711-713)
- Modify: `app/assets/js/tools/users/users.js` (absorb the modal handlers)

- [ ] **Step 11.1: Move the modal handlers**

Cut from `settings.js`:
- `openAddUserModal()` (lines ~315-327)
- `openEditUserModal(...)` (lines ~329-341)
- `saveUser()` (lines ~343-376)
- The `window.openAddUserModal = ...`, `window.openEditUserModal = ...`, `window.saveUser = ...` lines (~711-713)

Paste them into `app/assets/js/tools/users/users.js` near the bottom (above the existing `window.confirmDeactivateUser = ...` line). Convert any references to `loadUserList()` so they call `renderUserList()` instead (the renamed function in users.js).

- [ ] **Step 11.2: Remove the old user-mgmt code from settings.js**

From `app/assets/js/tools/settings/settings.js`:
- Remove the `// Show/hide admin-only sections` block at lines ~12-19 (the Users tab is now its own page; Settings no longer needs the per-load gate)
- Remove the `loadUserList()` function (lines ~268-313) — replaced by `renderUserList()` in users.js
- Remove the three `window.*` exports for user modal handlers

- [ ] **Step 11.3: Verify Settings still loads without the removed code**

Run: `node --check app/assets/js/tools/settings/settings.js`
Run: `node --check app/assets/js/tools/users/users.js`

Expected: both exit 0.

- [ ] **Step 11.4: Commit**

```bash
git add app/assets/js/tools/settings/settings.js app/assets/js/tools/users/users.js
git commit -m "refactor(app): move user-mgmt functions from settings.js to users.js

Cuts openAddUserModal / openEditUserModal / saveUser and their window
exports out of settings.js, pastes them into users.js where the Users
tab actually consumes them. Settings no longer needs its admin-only
gate since user mgmt isn't in Settings anymore. Modal HTML (#userModal)
in index.html is untouched and still referenced by these handlers.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Create the Storage tab module

**Files:**
- Create: `app/assets/js/tools/storage/storage.js`

- [ ] **Step 12.1: Create the directory and file**

```bash
mkdir -p "app/assets/js/tools/storage"
```

Create `app/assets/js/tools/storage/storage.js`. Cut the storage-related functions out of `settings.js` (search for `loadStorageInfo`, `settingsOpenOutputFolder`, `settingsCleanupNow`, `settingsChangeOutputFolder` — typically around lines ~50-260 in settings.js). Paste them into `storage.js` and wrap into an exported `storageLoad()` that calls `loadStorageInfo()` on entry.

Template:

```javascript
// ══════════════════════════════════════════════════════════
//  STORAGE TAB — file location, sizes, cleanup
// ══════════════════════════════════════════════════════════
import { state } from '../../shared/state.js';
import { agentBridge } from '../../shared/agent-client.js';
import { fmtSize } from '../../shared/utils.js';

export async function storageLoad() {
  if (!state.agentConnected) {
    const c = document.getElementById('storageView');
    // Optional: render an "agent offline" notice. For now just no-op since the cards have their own '...' placeholders.
    return;
  }
  await loadStorageInfo();
}

// (cut + paste loadStorageInfo, settingsOpenOutputFolder, settingsCleanupNow, settingsChangeOutputFolder from settings.js here)
// Rename the window exports below to keep onclick attrs in the lifted HTML working.

// Existing window exports preserved (the lifted HTML calls these by these exact names):
window.settingsOpenOutputFolder = settingsOpenOutputFolder;
window.settingsCleanupNow = settingsCleanupNow;
window.settingsChangeOutputFolder = settingsChangeOutputFolder;
```

> **Note:** the `window.*` names stay as `window.settingsCleanupNow` etc. because the lifted HTML in the next task uses those exact strings in its `onclick=""` attributes. Renaming them would require a parallel rename in HTML, which adds risk for no gain. Keep the names.

- [ ] **Step 12.2: Remove the storage functions from settings.js**

In `app/assets/js/tools/settings/settings.js`, delete the four storage functions and their `window.*` exports. Also delete the `loadStorageInfo();` call inside `settingsLoad()` (around line ~50).

- [ ] **Step 12.3: JS syntax check both files**

Run: `node --check app/assets/js/tools/storage/storage.js && node --check app/assets/js/tools/settings/settings.js`

Expected: exit 0.

- [ ] **Step 12.4: Commit**

```bash
git add app/assets/js/tools/storage/storage.js app/assets/js/tools/settings/settings.js
git commit -m "refactor(app): move storage functions from settings.js to storage.js

New tool module at tools/storage/storage.js wraps loadStorageInfo +
the three settings* storage handlers (open folder, cleanup now, change
output folder). Window exports preserved by name so the inline onclick
attributes in the lifted HTML still resolve. settingsLoad no longer
calls loadStorageInfo — the Storage tab does that itself.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: Lift the Storage card markup into a new view container

**Files:**
- Modify: `app/index.html`

- [ ] **Step 13.1: Cut the storage card from Settings**

In `app/index.html` find the existing storage card block (search for `id="storageCard"` — currently at line ~1419-1486). **Cut** the entire block.

- [ ] **Step 13.2: Paste it into a new `#storageView` container**

Add the following block at the same place where you added `#usersView` in Task 10 (sibling to other view divs):

```html
<!-- Storage tab (visible to all) -->
<div id="storageView" style="display:none; padding:24px 32px;">
  <div style="margin-bottom:18px;">
    <h1 style="font-size:1.2rem; font-weight:800; color:#0f172a; margin:0;">Storage</h1>
    <p style="font-size:0.78rem; color:#94a3b8; margin:4px 0 0;">Where your files are saved and how much space they use</p>
  </div>

  <!-- Paste the cut storage card block here.
       The existing id="storageCard" stays; it's inside the new tab now.
       The "Save & Connect" / "Reload" buttons that used to live near this card stay in Settings. -->
</div>
```

Paste the cut storage card markup (with `id="storageCard"` and all its inner structure) inside the new `#storageView` div, where the comment is.

- [ ] **Step 13.3: Wire switchTool('storage') in app.js**

Same pattern as Users. Add hide line + a new branch:

```javascript
  document.getElementById('storageView').style.display = 'none';
```

and

```javascript
  } else if (tool === 'storage') {
    document.getElementById('storageView').style.display = '';
    document.getElementById('navStorage').classList.add('active');
    import('./tools/storage/storage.js').then(m => m.storageLoad());
```

- [ ] **Step 13.4: Smoke-check HTML and JS**

Open `app/index.html` and scan for `id="storageCard"` — should appear exactly once, inside `#storageView`, not inside `#settingsView`.

Run: `node --check app/assets/js/app.js`
Expected: exit 0.

- [ ] **Step 13.5: Commit**

```bash
git add app/index.html app/assets/js/app.js
git commit -m "feat(app): lift Storage card into its own #storageView tab

Cuts the storageCard block out of the Settings view, pastes it into a
new #storageView container with its own page header. switchTool('storage')
shows the tab and calls storageLoad(). No sidebar item yet — that
arrives with the sidebar restructure.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase E — Sidebar restructure + Chassis Finder rename

### Task 14: Reorder sidebar items and add the two new entries

**Files:**
- Modify: `app/index.html` (sidebar nav around line ~213-273)

- [ ] **Step 14.1: Replace the sidebar nav block**

In `app/index.html`, replace the entire `<div class="sidebar-nav">` block (around lines ~228-273) with:

```html
<div class="sidebar-nav">
  <div class="sidebar-section-label">Workspace</div>

  <div class="sidebar-nav-item" id="navInvoiceSender" onclick="switchTool('invoice-sender')">
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/>
    </svg>
    Invoice Sender
  </div>

  <div class="sidebar-nav-item" id="navMerge" onclick="switchTool('merge')">
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M6 21V9a9 9 0 0 0 9 9"/>
    </svg>
    Merging Tool
  </div>

  <div class="sidebar-nav-item" id="navChassisFinder" onclick="switchTool('chassis-finder')">
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
    </svg>
    Chassis Finder
  </div>

  <div class="sidebar-section-label" style="margin-top:16px;">System</div>

  <div class="sidebar-nav-item" id="navSettings" onclick="switchTool('settings')">
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>
    </svg>
    Settings
  </div>

  <div class="sidebar-nav-item" id="navCustomers" onclick="switchTool('customers')">
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>
    </svg>
    Customers
  </div>

  <div class="sidebar-nav-item" id="navStorage" onclick="switchTool('storage')">
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>
    </svg>
    Storage
  </div>

  <div class="sidebar-nav-item" id="navSessionHistory" onclick="switchTool('session-history')">
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
    </svg>
    Session History
  </div>

  <div class="sidebar-nav-item" id="navUsers" onclick="switchTool('users')" style="display:none;">
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>
    </svg>
    Users
  </div>
</div>
```

- [ ] **Step 14.2: Add admin-gating logic in app.js**

In `app/assets/js/app.js`, find `showApp(user)` (or whichever function gets called after login that has access to the user role). Add at the start of that function:

```javascript
  // Admin-only sidebar items
  const navUsers = document.getElementById('navUsers');
  if (navUsers) {
    navUsers.style.display = (user && user.role === 'admin') ? '' : 'none';
  }
```

Also call the same logic when `/auth/me` refreshes the user (search `agentBridge.validateSession` or wherever `getCurrentUser` is updated post-login).

- [ ] **Step 14.3: Smoke-check by opening the app**

Open `app/index.html` in a browser (or run the packaged app). Confirm the sidebar shows:

- Workspace: Invoice Sender, Merging Tool, Chassis Finder (label dropped "INI")
- System: Settings, Customers, Storage, Session History, Users (admin only)

Log in as operator → Users should be hidden. Log in as admin → Users visible.

- [ ] **Step 14.4: Commit**

```bash
git add app/index.html app/assets/js/app.js
git commit -m "feat(app): sidebar restructure + Chassis Finder rename

Workspace section: Invoice Sender, Merging Tool, Chassis Finder
(dropped 'INI' label prefix; internal tool key chassis-finder
unchanged). System section in order: Settings, Customers, Storage,
Session History, Users. #navUsers is hidden by default and shown only
when the logged-in user's role === 'admin' (re-evaluated on login and
on user refresh). #navStorage is always visible.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase F — Settings page cleanup

### Task 15: Replace QBO / TMS / Gmail blocks with single Connections card

**Files:**
- Modify: `app/index.html` (Settings view region, currently sprawls ~1200-1500)
- Modify: `app/assets/css/styles.css` (add new utility classes)

- [ ] **Step 15.1: Add the CSS utility classes**

Append to `app/assets/css/styles.css`:

```css
/* ── Connections card (Settings page) ── */
.connections-card { background: #fff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 4px 18px; margin-bottom: 18px; }
.conn-row { display: flex; align-items: center; gap: 12px; padding: 14px 0; border-top: 1px solid #f1f5f9; }
.conn-row:first-child { border-top: none; }
.conn-icon { width: 32px; height: 32px; border-radius: 8px; display: flex; align-items: center; justify-content: center; font-size: 0.78rem; font-weight: 700; color: #fff; flex-shrink: 0; }
.conn-icon.qbo { background: #2ca01c; }
.conn-icon.tms { background: #1e3a8a; }
.conn-icon.gmail { background: #ea4335; }
.conn-meta { flex: 1; min-width: 0; }
.conn-name { font-size: 0.88rem; font-weight: 600; color: #0f172a; }
.conn-status { font-size: 0.74rem; color: #94a3b8; margin-top: 2px; }
.status-pill { padding: 3px 10px; border-radius: 999px; font-size: 0.7rem; font-weight: 600; }
.pill-ok { background: #dcfce7; color: #166534; }
.pill-warn { background: #fef3c7; color: #92400e; }
.pill-off { background: #f1f5f9; color: #64748b; }
.conn-action { padding: 6px 12px; background: #fff; border: 1px solid #e2e8f0; border-radius: 7px; font-size: 0.76rem; color: #475569; cursor: pointer; }
.conn-action:hover { background: #f8fafc; }

/* ── Preferences card ── */
.preferences-card { background: #fff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 14px 18px; margin-bottom: 18px; }
.pref-toggle { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.pref-label { font-size: 0.85rem; font-weight: 600; color: #0f172a; }
.pref-desc { font-size: 0.72rem; color: #94a3b8; margin-top: 2px; }
```

- [ ] **Step 15.2: Rewrite the Settings page body**

In `app/index.html`, find the `<div id="settingsView">` block (or whatever holds the Settings page). Replace its inner content with:

```html
<div style="padding:24px 32px;">
  <div style="margin-bottom:18px;">
    <h1 style="font-size:1.2rem; font-weight:800; color:#0f172a; margin:0;">Settings</h1>
    <p style="font-size:0.78rem; color:#94a3b8; margin:4px 0 0;">Connections, preferences, and diagnostics</p>
  </div>

  <h3 style="font-size:0.95rem; font-weight:700; color:#0f172a; margin:0 0 4px;">Connections</h3>
  <p style="font-size:0.74rem; color:#94a3b8; margin:0 0 10px;">Sign in to the three services this app talks to</p>

  <div class="connections-card">
    <!-- QBO row -->
    <div class="conn-row">
      <div class="conn-icon qbo">Qb</div>
      <div class="conn-meta">
        <div class="conn-name">QuickBooks Online</div>
        <div class="conn-status" id="qboConnStatus">Checking…</div>
      </div>
      <span id="qboConnPill" class="status-pill pill-off">—</span>
      <button class="conn-action" id="qboConnAction" onclick="window.qboConnAction&&window.qboConnAction()">…</button>
    </div>

    <!-- TMS row -->
    <div class="conn-row">
      <div class="conn-icon tms">Tm</div>
      <div class="conn-meta">
        <div class="conn-name">TMS Portal</div>
        <div class="conn-status" id="tmsConnStatus">Checking…</div>
      </div>
      <span id="tmsConnPill" class="status-pill pill-off">—</span>
      <button class="conn-action" onclick="window.openTmsEditModal&&window.openTmsEditModal()">Edit</button>
    </div>

    <!-- Gmail row -->
    <div class="conn-row">
      <div class="conn-icon gmail">Gm</div>
      <div class="conn-meta">
        <div class="conn-name">Gmail (for sending invoices)</div>
        <div class="conn-status" id="gmailConnStatus">Checking…</div>
      </div>
      <span id="gmailConnPill" class="status-pill pill-off">—</span>
      <button class="conn-action" onclick="window.openGmailEditModal&&window.openGmailEditModal()">Set up</button>
    </div>
  </div>

  <h3 style="font-size:0.95rem; font-weight:700; color:#0f172a; margin:18px 0 4px;">Preferences</h3>
  <p style="font-size:0.74rem; color:#94a3b8; margin:0 0 10px;">Personal settings for this device</p>

  <div class="preferences-card">
    <div class="pref-toggle">
      <div>
        <div class="pref-label">Desktop notifications</div>
        <div class="pref-desc">Alerts when something needs attention</div>
      </div>
      <input type="checkbox" id="settingsNotifyEnabled" onchange="toggleNotifications(this.checked)"
        style="width:36px; height:20px; accent-color:#ea580c;" />
    </div>
  </div>

  <details style="margin-top:14px; font-size:0.8rem; color:#64748b;">
    <summary style="cursor:pointer; padding:8px 0;">Advanced — Selector Health check</summary>
    <div style="padding:10px 0 0;">
      <p style="margin:0 0 8px; font-size:0.74rem; color:#94a3b8;">Diagnostic tool — verifies QBO/TMS page elements are still working.</p>
      <button class="btn btn-secondary" onclick="runSelectorHealthCheck()" id="healthCheckBtn" style="padding:6px 12px; font-size:0.74rem;">Run Check</button>
      <div id="healthCheckResults" style="display:none; margin-top:10px;">
        <div id="healthCheckTms" style="background:#fff; border:1px solid #e2e8f0; border-radius:8px; padding:10px; font-size:0.78rem;"></div>
      </div>
    </div>
  </details>

  <div id="settingsResultMsg" style="margin-top:14px; font-size:0.82rem; display:none;"></div>

  <div style="margin-top:24px; padding-top:12px; border-top:1px solid #f1f5f9; font-size:0.68rem; color:#94a3b8; line-height:1.5;">
    Credentials are saved locally to your agent — never sent online.
  </div>
</div>
```

The old big credential blocks, the green info banner, the "Save & Connect" button row, the in-settings notifications section, the user-mgmt section, and the storage card are all gone from this block.

- [ ] **Step 15.3: Add three small per-row edit modals**

The Connections card has three rows; each Edit button opens a small modal. The simplest path: reuse the existing input markup that used to live inline in Settings, just wrap it into three separate `.modal-overlay` divs.

Add at the bottom of `index.html` (near the existing `#userModal`):

```html
<!-- TMS Edit Modal -->
<div id="tmsEditModal" class="modal-overlay">
  <div class="modal-box" style="max-width:420px;">
    <div class="modal-header">
      <h3 style="margin:0; font-size:1rem; font-weight:700;">TMS Portal</h3>
      <button class="modal-close" onclick="document.getElementById('tmsEditModal').classList.remove('open')">&times;</button>
    </div>
    <div class="modal-body" style="padding:20px;">
      <div style="display:flex; flex-direction:column; gap:12px;">
        <div>
          <label style="font-size:0.75rem; color:#64748b; display:block; margin-bottom:4px;">Email</label>
          <input type="email" id="settingsTmsEmail" style="width:100%; padding:8px 12px; border:1px solid #e2e8f0; border-radius:8px; font-size:0.85rem;" />
        </div>
        <div>
          <label style="font-size:0.75rem; color:#64748b; display:block; margin-bottom:4px;">Password</label>
          <input type="password" id="settingsTmsPassword" style="width:100%; padding:8px 12px; border:1px solid #e2e8f0; border-radius:8px; font-size:0.85rem;" />
        </div>
      </div>
      <div id="tmsEditError" style="margin-top:8px; font-size:0.78rem; color:#dc2626; display:none;"></div>
    </div>
    <div class="modal-footer" style="padding:14px 20px; display:flex; gap:10px; justify-content:flex-end;">
      <button class="btn btn-secondary" onclick="document.getElementById('tmsEditModal').classList.remove('open')">Cancel</button>
      <button class="btn btn-primary" onclick="window.saveTmsCredentials&&window.saveTmsCredentials()">Save</button>
    </div>
  </div>
</div>

<!-- Gmail Edit Modal -->
<div id="gmailEditModal" class="modal-overlay">
  <div class="modal-box" style="max-width:420px;">
    <div class="modal-header">
      <h3 style="margin:0; font-size:1rem; font-weight:700;">Gmail App Password</h3>
      <button class="modal-close" onclick="document.getElementById('gmailEditModal').classList.remove('open')">&times;</button>
    </div>
    <div class="modal-body" style="padding:20px;">
      <div>
        <label style="font-size:0.75rem; color:#64748b; display:block; margin-bottom:4px;">App Password (16 letters)</label>
        <input type="password" id="settingsGmailAppPassword" placeholder="abcd efgh ijkl mnop" style="width:100%; padding:8px 12px; border:1px solid #e2e8f0; border-radius:8px; font-size:0.85rem;" />
      </div>
      <details style="margin-top:10px; font-size:0.76rem; color:#64748b;">
        <summary style="cursor:pointer; color:#2563eb;">How do I get one?</summary>
        <ol style="margin:8px 0 0 0; padding-left:20px; line-height:1.6;">
          <li>Make sure 2-Step Verification is on for your Google account.</li>
          <li>Go to <a href="https://myaccount.google.com/apppasswords" target="_blank" style="color:#2563eb;">myaccount.google.com/apppasswords</a>.</li>
          <li>Create a new app password (name it "NGL Accounting"). Google gives you 16 letters.</li>
          <li>Paste them above (spaces are fine) and Save.</li>
        </ol>
      </details>
      <div id="gmailEditError" style="margin-top:8px; font-size:0.78rem; color:#dc2626; display:none;"></div>
    </div>
    <div class="modal-footer" style="padding:14px 20px; display:flex; gap:10px; justify-content:flex-end;">
      <button class="btn btn-secondary" onclick="document.getElementById('gmailEditModal').classList.remove('open')">Cancel</button>
      <button class="btn btn-primary" onclick="window.saveGmailCredentials&&window.saveGmailCredentials()">Save</button>
    </div>
  </div>
</div>
```

(QBO doesn't need a modal — its action is a one-click "Connect to QuickBooks" OAuth redirect, which is handled by the existing `qboConnAction` function.)

- [ ] **Step 15.4: Rework settings.js to drive the new Connections card**

In `app/assets/js/tools/settings/settings.js`, rewrite `settingsLoad()` to:

1. Query the three connection statuses via existing endpoints (`/qbo/status`, `agentBridge.getCredentials()`, email config endpoint)
2. Populate the three `#xxxConnStatus`, `#xxxConnPill`, `#xxxConnAction` slots based on status
3. Wire `window.openTmsEditModal`, `window.openGmailEditModal`, `window.saveTmsCredentials`, `window.saveGmailCredentials`, `window.qboConnAction`

The bulk of the helper code (querying `getCredentials()`, populating fields, the email config handlers, etc.) already exists in settings.js — keep that code, just have it render into the new DOM ids instead of the old inline ones. Specific functions to keep working: `toggleNotifications`, `runSelectorHealthCheck`, `loadNotificationState`, `loadQboApiStatus`, `loadEmailConfig`.

Pseudocode for new `settingsLoad()`:

```javascript
export async function settingsLoad() {
  loadNotificationState();
  if (!state.agentConnected) {
    document.getElementById('qboConnStatus').textContent = 'Agent offline';
    document.getElementById('tmsConnStatus').textContent = 'Agent offline';
    document.getElementById('gmailConnStatus').textContent = 'Agent offline';
    return;
  }
  await loadQboConnRow();
  await loadTmsConnRow();
  await loadGmailConnRow();
}

async function loadQboConnRow() {
  const status = await agentBridge.checkQBOStatus();
  const statusEl = document.getElementById('qboConnStatus');
  const pillEl = document.getElementById('qboConnPill');
  const actionEl = document.getElementById('qboConnAction');
  if (status.connected) {
    statusEl.textContent = `Connected as ${status.companyName || '—'}`;
    pillEl.textContent = 'Connected'; pillEl.className = 'status-pill pill-ok';
    actionEl.textContent = 'Disconnect';
    window.qboConnAction = () => agentBridge.disconnectQbo().then(settingsLoad);
  } else {
    statusEl.textContent = 'Not connected';
    pillEl.textContent = 'Not connected'; pillEl.className = 'status-pill pill-off';
    actionEl.textContent = 'Connect';
    window.qboConnAction = () => window.open(agentBridge.baseUrl + '/qbo/connect', '_blank');
  }
}

async function loadTmsConnRow() { /* analogous, populates tms* ids, sets window.openTmsEditModal + window.saveTmsCredentials */ }
async function loadGmailConnRow() { /* analogous, populates gmail* ids */ }
```

> ⚠️ **Implementer note:** the exact backend endpoint names (`agentBridge.checkQBOStatus`, `agentBridge.getCredentials`, etc.) and their response shapes must come from inspecting the existing `agent-client.js` and the old `settingsLoad` code you're rewriting. Don't guess the property names — read them from the existing implementation.

- [ ] **Step 15.5: Syntax + visual check**

Run: `node --check app/assets/js/tools/settings/settings.js`
Expected: exit 0.

Open the app, navigate to Settings. Expected: three connection rows render with statuses, Notifications toggle works, Advanced collapsible reveals Selector Health.

- [ ] **Step 15.6: Commit**

```bash
git add app/index.html app/assets/css/styles.css app/assets/js/tools/settings/settings.js
git commit -m "feat(app): Settings page restructure — Connections + Preferences + Advanced

Replaces the three large credential blocks (QBO, TMS, Gmail) with a
single Connections card containing one row per service: icon, name,
status pill, contextual action button. Notifications moved to a small
Preferences card. Selector Health collapsed into an Advanced <details>
section. Green info banner replaced by a single muted footer line.
TMS and Gmail editing happens through new compact modals; QBO uses
the existing OAuth redirect.

User Management and Storage sections were removed in earlier commits
(now their own tabs). Save & Connect / Reload mega-buttons are gone —
each connection owns its own save action.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase G — Login bootstrap (frontend)

### Task 16: Add Google sign-in section to the Setup screen

**Files:**
- Modify: `app/index.html` (Setup screen, around line ~134-207)

- [ ] **Step 16.1: Locate the existing Google block on the Login screen**

Run: `grep -n 'googleLoginSection' app/index.html`

You'll find the block defined for the Login screen (currently around line ~91). Read the full block so you can replicate its structure.

- [ ] **Step 16.2: Copy the block into the Setup screen with a new id**

Inside the Setup screen markup (find `id="setupScreen"` or whichever wrapper), immediately above the manual create-admin form, add:

```html
<div id="setupGoogleLoginSection" style="display:none; margin-top:20px;">
  <button id="setupGoogleLoginBtn" type="button" onclick="doGoogleLogin()"
    style="width:100%; padding:11px 16px; background:#fff; color:#3c4043; border:1px solid #dadce0; border-radius:8px; font-size:0.88rem; font-weight:500; cursor:pointer; display:flex; align-items:center; justify-content:center; gap:10px;">
    <svg width="18" height="18" viewBox="0 0 48 48">
      <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3c-1.6 4.6-6 8-11.3 8-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z"/>
      <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/>
      <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2c-2.1 1.6-4.7 2.4-7.2 2.4-5.2 0-9.6-3.3-11.2-8L6.3 33C9.7 39.7 16.3 44 24 44z"/>
      <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.1 4.1-3.9 5.4l6.2 5.2C40.8 35.3 44 30 44 24c0-1.3-.1-2.4-.4-3.5z"/>
    </svg>
    <span>Sign in with Google</span>
  </button>
  <p style="font-size:0.72rem; color:#94a3b8; margin:8px 0 0; text-align:center;">
    @ngltrans.net accounts only · You'll become an admin if you're the first user
  </p>
  <div style="display:flex; align-items:center; gap:10px; margin:18px 0; color:#cbd5e1; font-size:0.74rem;">
    <div style="flex:1; height:1px; background:#e2e8f0;"></div>
    <span>or create manually</span>
    <div style="flex:1; height:1px; background:#e2e8f0;"></div>
  </div>
</div>
```

(Adjust the surrounding margin/divider to match whatever the Setup screen already does between sections.)

- [ ] **Step 16.3: Smoke-check by visually inspecting in browser**

Open `app/index.html` in a browser. Manually trigger the Setup screen — easiest path: in the browser console run `showSetup()` (if exposed) or temporarily change CSS to display the Setup screen.

Expected: the Setup screen now shows a Google button section above the manual form, with a divider that reads "or create manually."

- [ ] **Step 16.4: Commit**

```bash
git add app/index.html
git commit -m "feat(app): Google sign-in block on the Setup screen

Adds #setupGoogleLoginSection above the manual create-admin form on
the first-run Setup screen. Same Google button markup as the Login
screen. Subcopy clarifies that the first user becomes admin. Section
is display:none until initGoogleSignIn unhides it (next task).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 17: Update `initGoogleSignIn` to handle both sections, call it before Setup

**Files:**
- Modify: `app/assets/js/app.js`

- [ ] **Step 17.1: Update initGoogleSignIn**

In `app/assets/js/app.js` around line 155-165, replace:

```javascript
async function initGoogleSignIn() {
  try {
    const res = await fetch(agentBridge.baseUrl + '/auth/google/available');
    if (!res.ok) return;
    const data = await res.json();
    if (!data.available) return;

    const section = document.getElementById('googleLoginSection');
    if (section) section.style.display = '';
  } catch { /* Google Sign-In not available */ }
}
```

With:

```javascript
async function initGoogleSignIn() {
  try {
    const res = await fetch(agentBridge.baseUrl + '/auth/google/available');
    if (!res.ok) return;
    const data = await res.json();
    if (!data.available) return;

    // Un-hide whichever Google sections exist — Login screen, Setup screen, or both.
    ['googleLoginSection', 'setupGoogleLoginSection'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.style.display = '';
    });
  } catch { /* Google Sign-In not available */ }
}
```

- [ ] **Step 17.2: Call initGoogleSignIn before the Setup early return**

Find the setup-required branch in `startup()` (around line 284-295). Before the early `return;` at line 292, add:

```javascript
          showSetup();
          await initGoogleSignIn();  // Also un-hides the Setup-screen Google section
          document.getElementById('setupUsername').focus();
          return;
```

- [ ] **Step 17.3: Syntax check**

Run: `node --check app/assets/js/app.js`
Expected: exit 0.

- [ ] **Step 17.4: Manual smoke test**

Wipe the local DB (move `agent/data/ngl.db` aside) and unset SUPABASE so the local count returns 0. Start agent + app. Setup screen should appear with the Google button visible above the manual form.

(Skip if testing on Joseph's actual install — your Supabase has users so Setup never shows. The behavior is provable from code review + the existing /auth/google/available endpoint test.)

- [ ] **Step 17.5: Commit**

```bash
git add app/assets/js/app.js
git commit -m "feat(app): initGoogleSignIn handles both Login + Setup sections

initGoogleSignIn now un-hides both #googleLoginSection (Login screen)
and #setupGoogleLoginSection (Setup screen) when the agent reports
Google as available. startup() calls initGoogleSignIn() before the
Setup-screen early return so fresh installs see the Google option
immediately. Closes the fresh-install bootstrap gap saved as a deferred
project note on 2026-05-14.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase H — Manual verification

### Task 18: End-to-end manual test pass

There's no JS test harness for the frontend — manual verification in the packaged app is the gate before shipping. Run through each flow with the running agent + browser/Electron app.

- [ ] **Step 18.1: Run JS pre-build syntax gate**

Run: `node desktop/check-js.js` (per memory `reference_build_js_check.md` — runbuild.bat refuses to package broken JS, so do this first)

Expected: exit 0, no errors.

- [ ] **Step 18.2: Smoke-test the sidebar**

Open the app. Confirm:

- Sidebar shows Workspace (Invoice Sender, Merging Tool, Chassis Finder) then System (Settings, Customers, Storage, Session History, Users)
- "Users" only appears if you're admin
- "Chassis Finder" label no longer says "INI"
- Each item navigates correctly

- [ ] **Step 18.3: Users tab full flow (admin login)**

- Click Users tab → list renders with you + any existing users
- Click "+ Add User" → modal opens, type test username + 4-char password + "Operator" role → Save → new row appears
- Click pencil on test user → modal opens populated, type a new password → Save → confirm by signing in as that user with the new password
- Click trash on test user → confirm dialog → user disappears from main list, "Show inactive users (1)" collapsible appears
- Expand collapsible → see test user with ↻ Reactivate → click → user moves back to main list
- Try to deactivate yourself → trash icon is disabled (tooltip on hover)

- [ ] **Step 18.4: Storage tab**

- Click Storage tab → header + folder rows + size bars + auto-cleanup callout + Clean up / Change folder buttons all render
- Click Open folder → opens the merge outputs folder in OS file explorer
- Click Clean up now → runs cleanup, "Last cleanup" timestamp updates

- [ ] **Step 18.5: Settings page**

- Click Settings tab → see Connections card (QBO/TMS/Gmail rows with statuses), Preferences card (notifications toggle), Advanced collapsible (Selector Health), footer line
- Click Edit on TMS row → modal opens, email + password fields prefilled (email) → can edit + Save
- Click Set up on Gmail row → modal opens, App Password input + help collapsible → can save
- Click Connect/Disconnect on QBO row → kicks off OAuth or disconnects
- Toggle notifications → setting persists
- Expand Advanced → Run Check button works

- [ ] **Step 18.6: Login bootstrap (only testable on a fresh install or local dev)**

If you have a separate test machine or can safely wipe local DB on a non-prod install:

- Wipe local DB → start app → see Setup screen with Google button above manual form
- Click Sign in with Google → @ngltrans.net account → app lets you in as admin
- Or: use manual form → creates local admin → log out → log back in with Google → you're now operator OR admin depending on Supabase state (see spec section 5 table)

If no test machine: confirm by code review only; the behavior is provable.

- [ ] **Step 18.7: Backend test suite**

Run: `cd agent && python -m pytest -q 2>&1 | tail -5`
Expected: all pass.

- [ ] **Step 18.8: If everything passes, mark task complete and proceed. If any flow fails, fix the bug in a new task and re-test.**

(No commit here — this is a verification task.)

---

## Phase I — Ship (v2.73.0)

### Task 19: Bump VERSION and rebuild

**Files:**
- Modify: `desktop/VERSION`

- [ ] **Step 19.1: Bump the version**

Read current `desktop/VERSION` (likely `2.72.1`). Update to `2.73.0`.

- [ ] **Step 19.2: Run the build pipeline**

Per memory `feedback_use_runbuild_for_rebuild.md`: use `runbuild.bat` (not `build-all.bat`).

From PowerShell:

```powershell
# Empty stdin file pattern per memory
$emptyStdin = New-TemporaryFile
Start-Process -FilePath "runbuild.bat" -WorkingDirectory "C:\Users\Joseph\Desktop\NGL ACCOUNTING SERVICE\desktop" -NoNewWindow -Wait -RedirectStandardInput $emptyStdin.FullName
Remove-Item $emptyStdin
```

Wait for the build to finish. Verify the installer exists:

```bash
ls "C:/Users/Joseph/Desktop/NGL ACCOUNTING SERVICE/desktop/dist/NGL_ACCOUNTING_INSTALLER_v2.73.0.exe"
```

Expected: file exists, ~250MB.

- [ ] **Step 19.3: Commit the version bump**

```bash
git add desktop/VERSION desktop/package.json
git commit -m "chore: bump VERSION to 2.73.0

- Users + Storage tabs + Settings cleanup + login bootstrap fix
- Bundled with cleanup-bundle (test debt + storage walk + COM exception
  + render purity) and merge-errors XLSX cleanup

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 19.4: Push to origin**

```bash
git push origin main
```

- [ ] **Step 19.5: Create the GitHub release**

```bash
cd "C:/Users/Joseph/Desktop/NGL ACCOUNTING SERVICE/desktop/dist"
gh release create v2.73.0 \
  NGL_ACCOUNTING_INSTALLER_v2.73.0.exe \
  latest.yml \
  --title "v2.73.0 — Users tab, Storage tab, Settings cleanup, login bootstrap fix" \
  --notes "$(cat <<'EOF'
## What's new

### Admin: dedicated Users tab
- Add / edit / deactivate / reactivate users from one place (admin-only)
- Inactive users tucked into a collapsible so you can reactivate them
- Clear note that passwords can't be displayed (bcrypt scrambled) and explains how to reset them
- Last-admin guard — you can't accidentally demote or deactivate the only remaining admin

### Storage tab (visible to everyone)
- Same info as the old Storage card in Settings, just blown out to its own tab
- Folder paths, size bars, auto-cleanup status, "Clean up now" + "Change folder" actions

### Settings page cleaned up
- One Connections card with three rows (QBO / TMS / Gmail), each with its own status and edit
- Preferences card for notifications
- Advanced collapsible for Selector Health diagnostic
- Removed the giant Save & Connect / Reload mega-buttons

### Login fix for new co-workers
- Fresh installs no longer get stuck on a Setup screen with no Google button
- First Google sign-in on a brand-new system becomes admin automatically
- Cloud-aware setup check skips the Setup screen when admins already exist in Supabase
- Falls back to local check if Supabase is unreachable (offline-friendly)

### Sidebar restructure
- Workspace: Invoice Sender, Merging Tool, Chassis Finder (dropped "INI" label)
- System: Settings, Customers, Storage, Session History, Users

### Other bundled fixes
- Test debt: TMS BOL→BL string rename in fetch-fallback tests (now passing)
- Storage info walks the Merge Outputs folder once instead of twice
- Excel converter narrows broad exception swallowing to actual COM errors
- Merge tool no longer mutates data inside its render function
- Merge errors XLSX export: simpler column layout + fixed [object Object] bug
EOF
)"
```

Expected: GitHub release page shows the new release with both assets attached.

- [ ] **Step 19.6: Verify auto-update path**

Wait ~30 seconds after the release is published. On any existing install, restart the app — the Electron auto-updater should detect the new version and update on next launch.

- [ ] **Step 19.7: Done.**

Mark Phase I complete. The release is live and existing users will auto-update.

---

## Self-Review (run by the planner, not the executor)

### Spec coverage check

| Spec section | Plan task(s) |
|---|---|
| 1 — Sidebar restructure | Task 14 |
| 2 — Users tab | Tasks 9, 10, 11 + backend Tasks 7, 8 |
| 3 — Storage tab | Tasks 12, 13 |
| 4 — Settings cleanup | Task 15 |
| 5 — Login bootstrap | Tasks 3, 4, 5, 6 (backend), 16, 17 (frontend) |
| Bundled cleanup | Task 1 |
| Bundled merge-errors XLSX | Task 2 |
| Ship | Tasks 18, 19 |

All spec sections have at least one task. ✓

### Placeholders

Scanned for "TBD", "implement later", "add appropriate error handling" — none found except in pseudo-code comments where the implementer is explicitly told the variable names come from existing code (Step 15.4). That's intentional — full pseudocode there would duplicate ~150 lines of agent-bridge plumbing; reading the existing code is the right path.

### Type consistency

- `usersLoad()` and `storageLoad()` — both exported from their respective modules and called from `switchTool()`. ✓
- `sb_get_user_count` — defined in Task 3, consumed via `_maybe_use_supabase` override in same task. ✓
- `create_google_user(role=...)` — defined in Task 5, called with `role=new_role` in Task 6. ✓
- `confirmDeactivateUser`, `reactivateUser` — defined in Task 9, called from HTML onclick in same task. ✓
- DOM ids `qboConnStatus`, `tmsConnStatus`, `gmailConnStatus`, `qboConnPill` etc. — defined in Task 15.2 (HTML) and consumed in Task 15.4 (JS). ✓

Plan is internally consistent.
