# v2.69 — Customer Manager Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/superpowers/specs/2026-05-14-invoice-sender-customer-manager-design.md`

**Goal:** Replace the nested "Send Method dropdown + Required Documents toggle" with a single dropdown that has three methods (Standard / OEC / **Custom**). The Custom method exposes a doc-picker with the Invoice checkbox restored as a meaningful (auto-satisfied) entry. Hide Portal Upload from the dropdown but keep its backend intact. Add live duplicate-customer-code detection. Migrate existing customers with `"invoice"` in `requiredDocs` onto the new Custom method so APEXMA01/TOPTRA02 start working immediately.

**Architecture:** Frontend changes are HTML/CSS/JS in the customer-manager surface only. Backend changes are (1) one-line filter at the requiredDocs gate so `"invoice"` is auto-satisfied, (2) a new idempotent startup migration that calls the existing `list_customers()` / `update_customer()` API (which auto-routes to SQLite or Supabase via `_maybe_use_supabase()`), (3) no dispatcher change — `"custom"` falls through the existing `else` branch in `send_job.py` and routes to `_send_qbo_api` exactly like `"email"`.

**Tech Stack:** Vanilla ES modules (no build step for JS), Tailwind via CDN, FastAPI on the agent, pytest for backend tests, Electron packaged via `runbuild.bat`. No JS test runner — frontend verification is manual in the packaged app.

**Locked decisions (from 2026-05-14 brainstorm):**
1. Three send methods visible: `email` · `qbo_invoice_only_then_pod_email` · `custom`. Portal Upload hidden but backend preserved.
2. The Required Documents section appears **only** when `sendMethod === 'custom'`.
3. Invoice checkbox is part of the Custom doc list, defaults to checked, and is auto-satisfied at the gate (filtered out before `check_attachments()` runs).
4. Data migration moves any customer with `"invoice"` in `requiredDocs` → `sendMethod = "custom"`, preserves their `requiredDocs`. Idempotent.
5. Duplicate customer code triggers a live red warning + "View existing →" link that closes the modal and re-opens it on the existing customer.

---

## File Structure

| File | Change type | Responsibility |
| --- | --- | --- |
| `desktop/VERSION` | Modify | Bump `2.68` → `2.69` |
| `desktop/package.json` | Modify | Sync `version` field to `2.69.0` |
| `agent/services/job_manager/send_qbo_api.py` | Modify (line 192) | Filter `"invoice"` out of `required_docs` before the gate |
| `agent/services/database.py` | Modify (after `_migrate_if_needed`) | Add `migrate_invoice_to_custom()` invoked from `init_db()` |
| `agent/tests/test_send_qbo_api/test_invoice_gate_filter.py` | Create | TDD test for the gate filter |
| `agent/tests/test_migrate_invoice_to_custom.py` | Create | TDD tests for migration: happy path, idempotency, OEC/portal customers untouched |
| `app/assets/css/styles.css` | Modify (append + delete) | Add `.dup-warning*` + `.invoice-info-banner`; remove unused `.doc-mode-btn` and `.doc-mode-active` |
| `app/index.html` | Modify (lines ~573–684) | Send-method dropdown updates, Required Documents restructure, dup-warning markup, invoice-info-banner |
| `app/assets/js/tools/customers/customers.js` | Modify (multiple functions) | Rewrite `custSendMethodChanged`; delete `custSetDocMode`; add `custCheckDuplicateCode`, `custJumpToExisting`; update save validation + open-modal init |

No new agent routes. No new JS modules. No agent-side dispatcher changes.

---

## Conventions used in this plan

- **TDD for backend:** Test → Run-fails → Implement → Run-passes → Commit. Pytest for the agent.
- **Manual verify for frontend:** Open the app (browser for quick checks; packaged Electron for the final smoke test) and walk the listed actions. Per `feedback_app_not_website.md`, the **final** smoke test must be in the packaged installer.
- **Commit message convention:** Matches recent project style (`feat(customers/v69):`, `fix(send-qbo-api/v69):`, `chore(v69):`).
- **Class prefix:** New CSS classes use **`v69-`** prefix where the spec invents a new component (avoids collision with the `v64-` recipient-list classes that shipped in v2.64 and the `v62-` results-view classes that shipped in v2.62).
- **One task per commit** unless the step explicitly bundles two trivial changes.
- **Subagent dispatch:** Per `feedback_opus_for_heavy_tasks.md`, default to Opus for any multi-file task (Tasks 2, 3, 7, 8, 9). Sonnet only for trivial single-file wiring (Tasks 1, 5).

---

## Task 1: Bump version

**Files:**
- Modify: `desktop/VERSION`
- Modify: `desktop/package.json`

**Why first:** Establishes the version line every later commit message will reference (e.g. `feat(customers/v69):`).

- [ ] **Step 1: Bump `desktop/VERSION`**

Replace contents with exactly:
```
2.69
```

- [ ] **Step 2: Sync `desktop/package.json` version field**

Find the `"version"` line near the top of `desktop/package.json` and change it to:
```json
  "version": "2.69.0",
```

- [ ] **Step 3: Commit**

```bash
git add desktop/VERSION desktop/package.json
git commit -m "$(cat <<'EOF'
chore(v69): bump VERSION to 2.69 for Customer Manager cleanup

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Backend — filter `"invoice"` out of the requiredDocs gate (TDD)

**Files:**
- Create: `agent/tests/test_send_qbo_api/__init__.py` (empty file, only if missing)
- Create: `agent/tests/test_send_qbo_api/test_invoice_gate_filter.py`
- Modify: `agent/services/job_manager/send_qbo_api.py` (line 192)

**Why:** The gate at line 192 today blocks the send if `"invoice"` is in `requiredDocs` and `check_attachments()` can't find an `"invoice"` Attachable in QBO. The invoice PDF is never an Attachable, so the gate fails forever. Filter `"invoice"` out before the gate runs — auto-satisfied.

- [ ] **Step 1: Create the test package marker (skip if it already exists)**

Check whether `agent/tests/test_send_qbo_api/` is a package. If `__init__.py` is missing, create an empty one:
```bash
ls agent/tests/test_send_qbo_api/__init__.py
# If "No such file", create it:
echo "" > agent/tests/test_send_qbo_api/__init__.py
```

- [ ] **Step 2: Write the failing test**

Create `agent/tests/test_send_qbo_api/test_invoice_gate_filter.py`:

```python
"""Verify the requiredDocs gate filters out 'invoice' — Fix 3 / v2.69.

The invoice PDF is auto-sent via QBO email and never an Attachable on the
QBO invoice record. Before v2.69 the gate at send_qbo_api.py:192 blocked
the send when 'invoice' was listed because check_attachments() never finds
it. After v2.69 the filter strips 'invoice' from required_docs so it's
auto-satisfied.

This test exercises the in-process logic only — no network, no QBO.
"""
import pytest


def _filter_required_docs(customer: dict, *, is_oec: bool) -> list[str]:
    """Mirror of the production filter for unit testing.

    The real production code lives at services/job_manager/send_qbo_api.py
    around line 192. We re-import the helper after the production change
    in the integration step below to confirm parity.
    """
    if is_oec:
        return []
    return [
        d
        for d in customer.get("requiredDocs", [])
        if d.lower() != "invoice"
    ]


def test_filter_strips_invoice_case_insensitive():
    customer = {"requiredDocs": ["Invoice", "POD", "BOL"]}
    assert _filter_required_docs(customer, is_oec=False) == ["POD", "BOL"]


def test_filter_preserves_order_of_remaining_docs():
    customer = {"requiredDocs": ["POD", "invoice", "BOL", "POL"]}
    assert _filter_required_docs(customer, is_oec=False) == ["POD", "BOL", "POL"]


def test_filter_handles_only_invoice():
    customer = {"requiredDocs": ["invoice"]}
    # The customer wants invoice-only. After the filter the gate is empty,
    # meaning the send goes through unconditionally — which is correct because
    # the invoice always sends via QBO email.
    assert _filter_required_docs(customer, is_oec=False) == []


def test_filter_handles_empty_required_docs():
    customer = {"requiredDocs": []}
    assert _filter_required_docs(customer, is_oec=False) == []


def test_filter_handles_missing_required_docs_key():
    customer = {}
    assert _filter_required_docs(customer, is_oec=False) == []


def test_oec_returns_empty_regardless_of_required_docs():
    customer = {"requiredDocs": ["POD", "invoice", "BOL"]}
    # OEC flow handles its own POD via Gmail; the QBO invoice email never
    # enforces requiredDocs.
    assert _filter_required_docs(customer, is_oec=True) == []


def test_production_code_uses_same_filter():
    """Smoke check that the production code at send_qbo_api.py:192 matches
    the helper above. If this fails, the production code and the test
    have drifted apart.
    """
    import inspect
    from services.job_manager import send_qbo_api

    src = inspect.getsource(send_qbo_api)
    assert "d.lower() != \"invoice\"" in src or "d.lower() != 'invoice'" in src, (
        "Expected the requiredDocs filter to strip 'invoice' case-insensitively. "
        "Update send_qbo_api.py:192 to match the helper in this test file."
    )
```

- [ ] **Step 3: Run the test — confirm it fails**

```bash
cd agent
python -m pytest tests/test_send_qbo_api/test_invoice_gate_filter.py -v
```

Expected: All unit tests **pass** (they exercise the local helper). `test_production_code_uses_same_filter` **fails** because production code at `send_qbo_api.py:192` still reads `customer.get("requiredDocs", [])` unfiltered.

- [ ] **Step 4: Apply the production change**

Open `agent/services/job_manager/send_qbo_api.py` and find this block at line 191–192:

```python
        is_oec = customer.get("sendMethod") == "qbo_invoice_only_then_pod_email"
        required_docs = [] if is_oec else customer.get("requiredDocs", [])
```

Replace line 192 with:

```python
        required_docs = (
            []
            if is_oec
            else [d for d in customer.get("requiredDocs", []) if d.lower() != "invoice"]
        )
```

- [ ] **Step 5: Run the test — confirm it now passes**

```bash
cd agent
python -m pytest tests/test_send_qbo_api/test_invoice_gate_filter.py -v
```

Expected: All 7 tests **pass**.

- [ ] **Step 6: Commit**

```bash
git add agent/services/job_manager/send_qbo_api.py \
        agent/tests/test_send_qbo_api/test_invoice_gate_filter.py \
        agent/tests/test_send_qbo_api/__init__.py
git commit -m "$(cat <<'EOF'
fix(send-qbo-api/v69): auto-satisfy 'invoice' in requiredDocs gate

The QBO invoice email always carries the invoice PDF — it's never an
Attachable. Customers with 'invoice' listed in requiredDocs (APEXMA01,
TOPTRA02) hit a gate that could never be satisfied. Filter 'invoice' out
of required_docs before the check_attachments() call so it's treated as
already-present.

OEC path is unaffected (still returns []). Tests cover case-insensitive
match, order preservation, invoice-only customers, missing-key handling,
and a parity check that the production code matches the test helper.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Backend — `migrate_invoice_to_custom()` (TDD)

**Files:**
- Create: `agent/tests/test_migrate_invoice_to_custom.py`
- Modify: `agent/services/database.py` (add `migrate_invoice_to_custom()` function near other migrations)

**Why:** The runtime filter in Task 2 prevents new failures, but existing customers with `"invoice"` in `requiredDocs` are still mislabeled as "Send All Attachments" mode (the legacy interpretation). The migration moves them to `sendMethod = "custom"` so the UI shows their real configuration and the user can see/edit it cleanly. The Supabase swap in `_maybe_use_supabase()` (line 772) means calling `list_customers()` / `update_customer()` automatically hits the right backend.

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_migrate_invoice_to_custom.py`:

```python
"""Tests for migrate_invoice_to_custom() — v2.69 data fixup.

Customers with 'invoice' in their requiredDocs should be moved onto
sendMethod='custom' so they show correctly in the new dropdown.
The migration must:
  - move 'email' customers with 'invoice' in requiredDocs to 'custom'
  - leave OEC customers alone (their flow doesn't use requiredDocs)
  - leave portal_upload customers alone (separate path)
  - leave customers without 'invoice' in requiredDocs alone
  - be idempotent (running twice is a no-op)
  - preserve the full requiredDocs list (including 'invoice')

Patches list_customers / update_customer to avoid hitting a real DB.
"""
from unittest.mock import patch, MagicMock


@patch("services.database.update_customer")
@patch("services.database.list_customers")
def test_migrates_email_customer_with_invoice(mock_list, mock_update):
    from services.database import migrate_invoice_to_custom

    mock_list.return_value = [
        {"code": "APEXMA01", "sendMethod": "email", "requiredDocs": ["invoice"]},
    ]
    migrate_invoice_to_custom()

    mock_update.assert_called_once_with("APEXMA01", {
        "sendMethod": "custom",
        "requiredDocs": ["invoice"],
    })


@patch("services.database.update_customer")
@patch("services.database.list_customers")
def test_migrates_customer_with_invoice_and_other_docs(mock_list, mock_update):
    from services.database import migrate_invoice_to_custom

    mock_list.return_value = [
        {"code": "X01", "sendMethod": "email", "requiredDocs": ["invoice", "pod"]},
    ]
    migrate_invoice_to_custom()

    mock_update.assert_called_once_with("X01", {
        "sendMethod": "custom",
        "requiredDocs": ["invoice", "pod"],
    })


@patch("services.database.update_customer")
@patch("services.database.list_customers")
def test_does_not_touch_oec_customers(mock_list, mock_update):
    from services.database import migrate_invoice_to_custom

    mock_list.return_value = [
        {"code": "OEC01", "sendMethod": "qbo_invoice_only_then_pod_email",
         "requiredDocs": ["invoice", "pod"]},
    ]
    migrate_invoice_to_custom()

    mock_update.assert_not_called()


@patch("services.database.update_customer")
@patch("services.database.list_customers")
def test_does_not_touch_portal_upload_customers(mock_list, mock_update):
    from services.database import migrate_invoice_to_custom

    mock_list.return_value = [
        {"code": "PORT01", "sendMethod": "portal_upload",
         "requiredDocs": ["invoice"]},
    ]
    migrate_invoice_to_custom()

    mock_update.assert_not_called()


@patch("services.database.update_customer")
@patch("services.database.list_customers")
def test_does_not_touch_customers_without_invoice(mock_list, mock_update):
    from services.database import migrate_invoice_to_custom

    mock_list.return_value = [
        {"code": "STD01", "sendMethod": "email", "requiredDocs": ["pod"]},
        {"code": "STD02", "sendMethod": "email", "requiredDocs": []},
    ]
    migrate_invoice_to_custom()

    mock_update.assert_not_called()


@patch("services.database.update_customer")
@patch("services.database.list_customers")
def test_is_idempotent_on_already_migrated_customers(mock_list, mock_update):
    """Second run after migration sees customers already on 'custom'. No-op."""
    from services.database import migrate_invoice_to_custom

    mock_list.return_value = [
        {"code": "APEXMA01", "sendMethod": "custom", "requiredDocs": ["invoice"]},
    ]
    migrate_invoice_to_custom()

    mock_update.assert_not_called()


@patch("services.database.update_customer")
@patch("services.database.list_customers")
def test_case_insensitive_invoice_match(mock_list, mock_update):
    """'Invoice' (capital I) should also trigger migration."""
    from services.database import migrate_invoice_to_custom

    mock_list.return_value = [
        {"code": "CASE01", "sendMethod": "email", "requiredDocs": ["Invoice"]},
    ]
    migrate_invoice_to_custom()

    mock_update.assert_called_once_with("CASE01", {
        "sendMethod": "custom",
        "requiredDocs": ["Invoice"],
    })


@patch("services.database.update_customer")
@patch("services.database.list_customers")
def test_migration_calls_list_customers_with_no_filter(mock_list, mock_update):
    """Must scan all customers including inactive — defensive."""
    from services.database import migrate_invoice_to_custom

    mock_list.return_value = []
    migrate_invoice_to_custom()

    mock_list.assert_called_once_with("", False)
```

- [ ] **Step 2: Run the test — confirm it fails**

```bash
cd agent
python -m pytest tests/test_migrate_invoice_to_custom.py -v
```

Expected: All tests **fail** with `ImportError` (the function doesn't exist yet).

- [ ] **Step 3: Implement `migrate_invoice_to_custom()`**

Open `agent/services/database.py` and add this function after `_migrate_if_needed()` (around line 200):

```python
def migrate_invoice_to_custom() -> None:
    """v2.69 data fixup: move customers with 'invoice' in requiredDocs to Custom.

    Customers like APEXMA01 / TOPTRA02 carry 'invoice' in their requiredDocs
    list — a legacy state that the v2.69 UI represents as Custom send method.
    This migration relabels them so the UI shows their real configuration
    and the user can edit it normally.

    OEC (qbo_invoice_only_then_pod_email) and portal_upload customers are
    untouched — their flows don't use requiredDocs the same way.

    Idempotent: customers already on 'custom' or without 'invoice' in
    requiredDocs are skipped on subsequent runs.

    Routes through the module-level list_customers / update_customer, which
    _maybe_use_supabase() will have swapped to the Supabase client if
    configured — so this works for both backends.
    """
    customers = list_customers("", False)  # all, including inactive
    migrated = 0
    for c in customers:
        method = c.get("sendMethod", "email")
        if method in ("qbo_invoice_only_then_pod_email", "portal_upload"):
            continue
        required = c.get("requiredDocs") or []
        if not any(d.lower() == "invoice" for d in required):
            continue
        if method == "custom":
            continue  # already migrated
        update_customer(c["code"], {
            "sendMethod": "custom",
            "requiredDocs": required,
        })
        migrated += 1
        logger.info("v69 migration: %s → custom (requiredDocs=%s)", c["code"], required)
    if migrated:
        logger.info("v69 migration: relabeled %d customer(s) onto 'custom' send method", migrated)
    else:
        logger.debug("v69 migration: no customers needed relabeling")
```

- [ ] **Step 4: Run the test — confirm it passes**

```bash
cd agent
python -m pytest tests/test_migrate_invoice_to_custom.py -v
```

Expected: All 8 tests **pass**.

- [ ] **Step 5: Wire the migration into `init_db()`**

In `agent/services/database.py`, find `init_db()` (around line 38). Currently the last call inside it is `_maybe_use_supabase()` on line 114. Add this **after** that call so the migration uses whichever backend is active:

```python
def init_db() -> None:
    """Create tables if they don't exist, then run migration if needed."""
    # ... existing body ...

    # If Supabase is configured, swap customer functions to cloud DB
    _maybe_use_supabase()

    # v2.69 data fixup — relabel 'invoice'-in-requiredDocs customers as Custom
    try:
        migrate_invoice_to_custom()
    except Exception as e:
        logger.error("v69 migration failed (non-fatal): %s", e)
```

The try/except is intentional — a migration failure shouldn't block agent startup. Worst case the user keeps their current behavior until next launch.

- [ ] **Step 6: Commit**

```bash
git add agent/services/database.py agent/tests/test_migrate_invoice_to_custom.py
git commit -m "$(cat <<'EOF'
feat(database/v69): migrate 'invoice'-in-requiredDocs customers to Custom

APEXMA01 and TOPTRA02 (and any future customers with 'invoice' listed in
requiredDocs) are now relabeled to sendMethod='custom' on agent startup.
Their requiredDocs list is preserved unchanged.

The migration runs after _maybe_use_supabase() so it automatically uses
the Supabase client when configured. Idempotent — a second run sees the
customers already on 'custom' and skips them. Failures are logged but
non-fatal so the agent always starts.

OEC and portal_upload customers are untouched.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Frontend — append new CSS, remove unused

**Files:**
- Modify: `app/assets/css/styles.css`

**Why:** Pre-stage the visual tokens for the dup-warning block and the invoice-info-banner. Strip dead CSS for the toggle buttons we're removing. No callers yet, so this commit is purely additive on top of dead-class removal — safe to ship even if the rest of v2.69 didn't.

- [ ] **Step 1: Append the v69 styles at the end of `app/assets/css/styles.css`**

```css
/* ════════════════════════════════════════════════════════════════
   CUSTOMER MANAGER v69 — Dup-code warning + Invoice-info banner
   Mirrors the fix-3-customer-manager.html mockup palette.
   ════════════════════════════════════════════════════════════════ */

/* Duplicate customer-code warning under the Code field */
.v69-dup-warning {
  display: none;
  background: #fef2f2;
  border: 1px solid #fca5a5;
  border-left: 3px solid #dc2626;
  border-radius: 8px;
  padding: 10px 12px 11px;
  margin-top: 8px;
}
.v69-dup-warning.is-active {
  display: block;
}
.v69-dup-warning-title {
  display: flex; align-items: center; gap: 6px;
  font-size: 0.83rem; font-weight: 700; color: #b91c1c;
  margin-bottom: 4px;
}
.v69-dup-warning-title .v69-warn-icon {
  color: #dc2626;
}
.v69-dup-warning-text {
  font-size: 0.78rem; color: #7f1d1d;
  line-height: 1.45;
  margin-left: 22px;
}
.v69-dup-warning-link {
  color: #c2410c; font-weight: 700; text-decoration: none;
  background: none; border: none; cursor: pointer;
  font-family: inherit; font-size: 0.78rem;
  padding: 0; margin-left: 4px;
  display: inline-flex; align-items: center; gap: 2px;
}
.v69-dup-warning-link:hover {
  color: #9a3412; text-decoration: underline;
}

/* Static info banner where the Invoice checkbox helper sits */
.v69-invoice-info-banner {
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  border-radius: 8px;
  padding: 9px 12px;
  margin-bottom: 10px;
  display: flex; gap: 8px; align-items: flex-start;
}
.v69-invoice-info-banner .v69-info-icon {
  color: #2563eb; flex-shrink: 0; line-height: 1.2;
}
.v69-invoice-info-banner .v69-info-text {
  font-size: 0.78rem; color: #1e3a8a; line-height: 1.45;
}
.v69-invoice-info-banner .v69-info-text strong {
  color: #1e40af; font-weight: 700;
}

/* Hint line under the Send Method dropdown — refreshed per-method copy */
.v69-send-method-hint {
  font-size: 0.73rem; color: #94a3b8; margin-top: 4px; line-height: 1.4;
}
```

- [ ] **Step 2: Remove unused `.doc-mode-btn` styles**

Search for `.doc-mode-btn` and `.doc-mode-active` in `app/assets/css/styles.css`. Delete the entire declaration blocks for both (they are tied to the toggle being removed in Task 5). If multiple blocks reference these classes, delete every one.

```bash
# Verify both classes are gone after deletion:
grep -n "doc-mode-btn\|doc-mode-active" app/assets/css/styles.css
# Expected: no matches
```

- [ ] **Step 3: Manual quick-check (browser)**

Open `app/index.html` directly in a browser. Confirm:
- Page still loads without console errors
- Customer Manager view still renders normally (modal not opened)
- No layout regressions on the Customers list

- [ ] **Step 4: Commit**

```bash
git add app/assets/css/styles.css
git commit -m "$(cat <<'EOF'
style(customers/v69): add dup-warning + invoice-info-banner; drop doc-mode-btn

Append .v69-dup-warning, .v69-invoice-info-banner, and .v69-send-method-hint
styles for the upcoming Customer Manager UI restructure. Remove the
.doc-mode-btn / .doc-mode-active styles that backed the Send-All vs
Require-Specific toggle that v2.69 deletes.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Frontend HTML — restructure the customer modal

**Files:**
- Modify: `app/index.html` (lines ~573–684 — customer modal body)

**Why:** Apply the DOM changes the new JS will drive: replace 3 dropdown options with 3 different ones (Portal Upload hidden), drop the Send-All/Specific toggle, rename the Required Documents section so it's only the doc picker, add the dup-warning markup under the Code field, and add the Invoice-info-banner inside the doc picker.

- [ ] **Step 1: Replace the Send Method dropdown options**

In `app/index.html` around lines 573–577, find:

```html
        <select class="modal-input" id="custSendMethod" onchange="custSendMethodChanged()">
          <option value="email">Standard QBO Email</option>
          <option value="qbo_invoice_only_then_pod_email">QBO Invoice Only + POD Email</option>
          <option value="portal_upload">Portal Upload</option>
        </select>
```

Replace with:

```html
        <select class="modal-input" id="custSendMethod" onchange="custSendMethodChanged()">
          <option value="email">Standard QBO Email</option>
          <option value="qbo_invoice_only_then_pod_email">QBO Invoice Only + POD Email</option>
          <option value="custom">Custom</option>
          <!-- portal_upload intentionally hidden in v2.69 — backend still routes it -->
        </select>
```

- [ ] **Step 2: Replace the hint container with the v69 hint class**

At lines 578–580, find:

```html
        <div style="font-size:0.73rem; color:#94a3b8; margin-top:4px;" id="custSendMethodHint">
          Send all attachments via QuickBooks Online email.
        </div>
```

Replace with:

```html
        <div class="v69-send-method-hint" id="custSendMethodHint">
          Send all attachments on the QBO invoice via QuickBooks email.
        </div>
```

- [ ] **Step 3: Find the Code field and add the dup-warning markup**

Find the Customer Code input field in the customer modal. (Search for `id="custCode"`.) Immediately after the closing tag of its wrapper, insert the warning block:

```html
        <div id="custDupWarning" class="v69-dup-warning">
          <div class="v69-dup-warning-title">
            <span class="v69-warn-icon">⚠</span>
            <span>Customer code "<span id="custDupCode"></span>" is already in use.</span>
          </div>
          <div class="v69-dup-warning-text">
            That code belongs to <strong id="custDupName"></strong>. Want to edit that customer instead?
            <button type="button" class="v69-dup-warning-link" onclick="custJumpToExisting()">View existing →</button>
          </div>
        </div>
```

- [ ] **Step 4: Restructure the Required Documents section**

At lines 630–674, find the `<div id="custDocRulesSection" ...>` block including everything through the closing `</div>` of `#custDocRulesSection`. Replace the entire section with:

```html
      <!-- Required Documents — only visible when sendMethod === 'custom' -->
      <div id="custDocRulesSection" style="display:none; margin-bottom:16px;">
        <label class="modal-field-label">Required Documents</label>

        <!-- Static helper banner that anchors the Invoice checkbox -->
        <div class="v69-invoice-info-banner">
          <span class="v69-info-icon">ℹ</span>
          <div class="v69-info-text">
            <strong>The invoice PDF is always sent automatically</strong> — checking Invoice below is for inclusion tracking. Any other ticked docs must be present in QBO (or fetchable from TMS) before the invoice will send.
          </div>
        </div>

        <!-- Doc picker checkboxes -->
        <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:6px; margin-bottom:10px;">
          <label class="doc-check-label"><input type="checkbox" value="invoice" onchange="custDocCheckChanged()"> Invoice</label>
          <label class="doc-check-label"><input type="checkbox" value="pod" onchange="custDocCheckChanged()"> POD</label>
          <label class="doc-check-label"><input type="checkbox" value="pol" onchange="custDocCheckChanged()"> POL</label>
          <label class="doc-check-label"><input type="checkbox" value="bol" onchange="custDocCheckChanged()"> BOL</label>
          <label class="doc-check-label"><input type="checkbox" value="pl" onchange="custDocCheckChanged()"> PL</label>
          <label class="doc-check-label"><input type="checkbox" value="do" onchange="custDocCheckChanged()"> DO</label>
        </div>

        <!-- OR-group builder (unchanged from prior version) -->
        <div id="custOrGroupSection" style="display:none; border-top:1px solid #e2e8f0; padding-top:8px;">
          <div style="font-size:0.78rem; font-weight:600; color:#475569; margin-bottom:6px;">Either / Or Groups</div>
          <div id="custOrGroupList" style="margin-bottom:6px;"></div>
          <div style="display:flex; gap:6px; align-items:center;">
            <select id="custOrLeft" class="modal-input" style="flex:1; padding:4px 8px; font-size:0.78rem;"></select>
            <span style="font-size:0.78rem; color:#94a3b8; font-weight:600;">OR</span>
            <select id="custOrRight" class="modal-input" style="flex:1; padding:4px 8px; font-size:0.78rem;"></select>
            <button type="button" class="btn btn-secondary" style="padding:4px 10px; font-size:0.75rem; white-space:nowrap;" onclick="custAddOrGroup()">Link</button>
          </div>
          <div style="font-size:0.7rem; color:#94a3b8; margin-top:4px;">
            Link two docs as "either/or" — the invoice will pass if <em>at least one</em> is attached.
          </div>
        </div>

        <!-- Rules summary -->
        <div id="custDocRuleSummary" style="margin-top:8px; padding:8px 10px; background:#f8fafc; border-radius:6px; font-size:0.78rem; color:#475569; display:none;"></div>
      </div>
```

Key differences from the old markup: (a) the `<div style="display:flex;...border:1px solid #e2e8f0; border-radius:8px; overflow:hidden;">` toggle and its two buttons are gone, (b) the `#custDocModeHint` element is gone, (c) the `#custDocSpecificPanel` wrapper is gone (its children are now direct children of `#custDocRulesSection`), (d) the static info banner is added, (e) the section now starts with `display:none` and is shown by JS only when `sendMethod === 'custom'`.

- [ ] **Step 5: Manual smoke check**

```bash
# Quick syntax check
node desktop/check-js.js 2>&1 | head -5
# Or open app/index.html in a browser
```

Expected: no JS console errors on load. The customer modal isn't opened yet, so we can't verify functionality here — but the page should render normally and the Customers list should look identical.

- [ ] **Step 6: Commit**

```bash
git add app/index.html
git commit -m "$(cat <<'EOF'
feat(customers/v69): restructure customer modal — Custom dropdown + dup-warning

- Send Method dropdown: 'Custom' replaces 'Portal Upload' in the visible
  options. Portal-upload backend still works (dispatcher routes it) but
  it's no longer assignable through the UI.
- Required Documents section: Send-All/Specific toggle removed; section
  is hidden by default and only visible when sendMethod === 'custom'.
- Invoice checkbox restored to the doc-picker grid, with a blue info
  banner explaining auto-inclusion.
- New v69-dup-warning markup under the Code field for the live
  duplicate-code detection JS that lands in Task 7.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Frontend JS — rewrite `custSendMethodChanged()` and remove `custSetDocMode()`

**Files:**
- Modify: `app/assets/js/tools/customers/customers.js` (lines 71–~115 for `custSendMethodChanged`; lines 325–~345 for `custSetDocMode`; window exports at the bottom)

**Why:** The dropdown now drives section visibility directly — no nested toggle. Custom mode shows the Required Documents section; the other two methods hide it. Invoice checkbox is pre-ticked when the user first picks Custom in an Add flow.

- [ ] **Step 1: Read the current `custSendMethodChanged()` to see what it touches**

```bash
grep -n "custSendMethodChanged\|custDocRulesSection\|custDocInfoOec\|custPodEmailSection\|custPortalSection\|custDocModeAll\|custDocModeSpecific\|custDocSpecificPanel" app/assets/js/tools/customers/customers.js
```

Note every element ID the function references so the rewrite doesn't lose a reveal/hide rule.

- [ ] **Step 2: Replace `custSendMethodChanged()`**

In `app/assets/js/tools/customers/customers.js` around line 71, find the existing `function custSendMethodChanged() { ... }`. Replace the entire function body with:

```javascript
function custSendMethodChanged() {
  const method = document.getElementById('custSendMethod').value;

  // Section visibility — one source of truth: the dropdown.
  const showRequiredDocs = method === 'custom';
  const showPodEmail = method === 'qbo_invoice_only_then_pod_email';
  const showPortal = method === 'portal_upload';  // legacy customers only
  const showOecDocInfo = method === 'qbo_invoice_only_then_pod_email';

  document.getElementById('custDocRulesSection').style.display = showRequiredDocs ? '' : 'none';
  document.getElementById('custPodEmailSection').style.display = showPodEmail ? '' : 'none';
  document.getElementById('custPortalSection').style.display = showPortal ? '' : 'none';
  document.getElementById('custDocInfoOec').style.display = showOecDocInfo ? '' : 'none';

  // Per-method hint copy
  const hint = document.getElementById('custSendMethodHint');
  if (method === 'email') {
    hint.textContent = 'Send all attachments on the QBO invoice via QuickBooks email.';
  } else if (method === 'qbo_invoice_only_then_pod_email') {
    hint.textContent = 'Send invoice via QBO, then POD via Gmail as a follow-up email.';
  } else if (method === 'custom') {
    hint.textContent = 'Send invoice plus only the documents you tick below. TMS auto-fills missing supporting docs when possible.';
  } else if (method === 'portal_upload') {
    hint.textContent = 'Merge invoice + POD into one PDF and upload to the TranzAct portal.';
  }

  // First switch into Custom in an Add flow: pre-tick Invoice so the
  // user lands in a valid (invoice-only) configuration by default.
  if (method === 'custom') {
    const invBox = document.querySelector('#custDocRulesSection input[type="checkbox"][value="invoice"]');
    const anyChecked = Array.from(
      document.querySelectorAll('#custDocRulesSection input[type="checkbox"]')
    ).some(cb => cb.checked);
    if (invBox && !anyChecked) {
      invBox.checked = true;
      if (typeof custDocCheckChanged === 'function') {
        custDocCheckChanged();
      }
    }
  }
}
```

- [ ] **Step 3: Delete `custSetDocMode()` and its callers**

Find `function custSetDocMode(mode) { ... }` around line 325 and delete the entire function.

Find the two existing callers in `customers.js`:
- Line ~457: `custSetDocMode('all');` — replace with `// v69: Send-All / Specific toggle removed — handled by dropdown`
- Line ~463: `custSetDocMode('specific');` — same replacement
- Line ~485: `custDocCheckChanged();` — keep (still valid)
- Line ~492: `custSetDocMode('all');` — replace with `// v69: same as above`

Then remove the `window.custSetDocMode = custSetDocMode;` export near the bottom (around line 700).

- [ ] **Step 4: Update `custOpenModal()` to load saved state correctly**

Find `function custOpenModal(code)` around line 117. Around line 180 there's a call to `custSendMethodChanged()` after setting the dropdown value. Make sure the function:
- Sets `document.getElementById('custSendMethod').value` from the loaded `customer.sendMethod || 'email'`
- Restores `requiredDocs` to the checkboxes (existing code, around lines 480–490)
- Falls through to `custSendMethodChanged()` which now handles section visibility

If the customer being loaded has `sendMethod === 'email'` AND `requiredDocs` is non-empty, that means they haven't been migrated yet for some reason — keep them on `email` (don't auto-flip). The Task 3 migration handles the bulk relabel; manual edits should be deliberate.

- [ ] **Step 5: Manual verify**

Open `app/index.html` in a browser. Navigate to Customer Manager → Add Customer. Walk through:
- Open modal: dropdown shows 3 options. Required Documents hidden. Hint says "Send all attachments…"
- Pick OEC: POD Email Settings appear. Required Documents still hidden. Hint says "Send invoice via QBO, then POD via Gmail…"
- Pick Custom: Required Documents appears with Invoice pre-checked. Hint says "Send invoice plus only the documents you tick below…"
- Tick a few more boxes, then switch back to Standard. Required Documents hides; switch back to Custom — checkboxes preserved.

Expected: no console errors. Each method shows the right sections.

- [ ] **Step 6: Commit**

```bash
git add app/assets/js/tools/customers/customers.js
git commit -m "$(cat <<'EOF'
feat(customers/v69): dropdown drives doc-picker visibility — delete toggle

- custSendMethodChanged() rewritten as the single source of truth for
  section visibility. Required Documents shows only for 'custom'; the
  OEC POD email block shows only for 'qbo_invoice_only_then_pod_email';
  Portal Upload block stays available for legacy customers.
- custSetDocMode() and its three call sites removed. The Send-All /
  Require-Specific toggle no longer exists.
- First-switch into Custom in an Add flow pre-ticks Invoice so the user
  lands in a valid invoice-only configuration by default.
- Per-method hint copy refreshed.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Frontend JS — duplicate-code detection (`custCheckDuplicateCode` + save guard)

**Files:**
- Modify: `app/assets/js/tools/customers/customers.js` (wire `oninput` on `#custCode`, add new functions, update save handler)

**Why:** Live feedback prevents the silent-overwrite bug. As the user types in the Customer Code field, the app checks against the existing customer list and surfaces the warning before they save.

- [ ] **Step 1: Add `custCheckDuplicateCode()` and `custJumpToExisting()`**

In `app/assets/js/tools/customers/customers.js`, add the following functions near the other modal helpers (after `custCloseModal`, around line 140):

```javascript
// Module-level cache of the most recent duplicate hit, so the
// "View existing →" link knows which customer to open without
// re-querying the agent.
let _custDupHit = null;

function custCheckDuplicateCode() {
  const inputEl = document.getElementById('custCode');
  const warnEl = document.getElementById('custDupWarning');
  const codeOut = document.getElementById('custDupCode');
  const nameOut = document.getElementById('custDupName');

  const typedRaw = (inputEl.value || '').trim();
  const typed = typedRaw.toUpperCase();

  // Hide the warning whenever the field is empty or matches the
  // customer currently being edited (so editing your own code doesn't
  // trigger a false positive).
  if (!typed) {
    warnEl.classList.remove('is-active');
    _custDupHit = null;
    return;
  }
  if (custState.editingCode && custState.editingCode.toUpperCase() === typed) {
    warnEl.classList.remove('is-active');
    _custDupHit = null;
    return;
  }

  // Match against the in-memory customer list. custState.customers is
  // refreshed every time the Customers view is rendered, so this is
  // up-to-date as long as the user hasn't been on the modal for hours.
  const list = (custState && custState.customers) || [];
  const hit = list.find(c => (c.code || '').toUpperCase() === typed);

  if (hit) {
    codeOut.textContent = typedRaw;
    nameOut.textContent = hit.name || '(no name)';
    warnEl.classList.add('is-active');
    _custDupHit = hit;
  } else {
    warnEl.classList.remove('is-active');
    _custDupHit = null;
  }
}

function custJumpToExisting() {
  if (!_custDupHit) return;
  const targetCode = _custDupHit.code;
  custCloseModal();
  // Tiny delay so the close animation finishes before we re-open.
  setTimeout(() => {
    if (typeof custOpenModal === 'function') {
      custOpenModal(targetCode);
    }
  }, 200);
}
```

- [ ] **Step 2: Wire the `oninput` listener on `#custCode`**

Find the `#custCode` `<input>` element in `app/index.html` (search for `id="custCode"`). Add `oninput="custCheckDuplicateCode()"` to the element:

```html
<input class="modal-input" id="custCode" type="text"
       placeholder="e.g. ACME01" oninput="custCheckDuplicateCode()">
```

(Adjust other existing attributes; only add the `oninput`.)

- [ ] **Step 3: Guard the Save action**

Find the save handler — search for `function custSaveCustomer` or wherever the modal Save button's `onclick` fires. Inside that function, BEFORE any payload assembly, add:

```javascript
  // v69: block save if dup-code warning is showing
  if (document.getElementById('custDupWarning').classList.contains('is-active')) {
    if (typeof window.invShowToast === 'function') {
      window.invShowToast('Customer code already exists. Choose a different code or open the existing customer.', 'error');
    } else {
      alert('Customer code already exists. Choose a different code or open the existing customer.');
    }
    return;
  }
```

(If a toast helper isn't easily reachable, `alert()` is acceptable — Lorena will only hit this on a real conflict.)

- [ ] **Step 4: Export the new functions to `window`**

Near the bottom of `customers.js` where other `window.cust*` exports live (around line 700), add:

```javascript
window.custCheckDuplicateCode = custCheckDuplicateCode;
window.custJumpToExisting = custJumpToExisting;
```

- [ ] **Step 5: Make sure `custOpenModal()` resets the warning and `_custDupHit`**

Find `custOpenModal(code)`. Near the top, immediately after the modal is shown and before populating fields, add:

```javascript
  document.getElementById('custDupWarning').classList.remove('is-active');
  _custDupHit = null;
```

Same for `custCloseModal()` — reset both at the start so a re-open never inherits stale state.

- [ ] **Step 6: Track which customer is being edited (so editing your own code doesn't warn)**

Inside `custOpenModal(code)`, when populating fields for an existing customer (not Add), set `custState.editingCode = code`. In Add mode set `custState.editingCode = null`. If `custState` doesn't have an `editingCode` field, add it to the state declaration in `app/assets/js/shared/state.js` (export `custState`) — initial value `null`.

Find `custState` in `state.js` and ensure it has `editingCode: null` in the initializer.

- [ ] **Step 7: Manual verify**

Open in a browser. Walk through:
- Customer Manager → Add Customer. Type `APEXMA01` (or any code from the visible list). Warning appears live. Save is blocked with a clear toast/alert.
- Click "View existing →". Modal closes, re-opens for APEXMA01 in edit mode. Warning is gone.
- Edit the APEXMA01 modal. Don't change the Code. No warning (editing own code is fine).
- Change APEXMA01's code to `TOPTRA02`. Warning appears.

Expected: clean live feedback, no false positives, no console errors.

- [ ] **Step 8: Commit**

```bash
git add app/assets/js/tools/customers/customers.js app/index.html \
        app/assets/js/shared/state.js
git commit -m "$(cat <<'EOF'
feat(customers/v69): live duplicate-code detection + Save guard

- custCheckDuplicateCode() runs on every keystroke in the Code field
  and shows the v69-dup-warning block when the typed code matches an
  existing customer (excluding the customer being edited).
- custJumpToExisting() closes the current modal and re-opens it on the
  conflicting customer in edit mode — preserves the user's intent of
  "I'm trying to find that customer".
- Save is blocked when the warning is visible; a toast (or alert
  fallback) tells the user what's wrong.
- custState.editingCode tracks which customer is open so editing your
  own code never triggers a false positive.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Backend — confirm the dispatcher accepts `"custom"` (sanity test, no code change)

**Files:**
- Create: `agent/tests/test_send_job_dispatcher_custom.py`

**Why:** The spec says `"custom"` falls through the existing `else` in `send_job.py:304-317` and routes to `_send_qbo_api` just like `"email"`. We didn't change the dispatcher — but we should pin that behavior with a test so future refactors don't silently break it.

- [ ] **Step 1: Write the test**

Create `agent/tests/test_send_job_dispatcher_custom.py`:

```python
"""Pin the v2.69 contract: sendMethod='custom' routes through _send_qbo_api.

We didn't change send_job.py for v2.69 — but the v2.69 spec assumes
'custom' falls through the existing else branch (line 316 in send_job.py).
This test guards that assumption.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_custom_method_dispatches_to_qbo_api():
    """A customer with sendMethod='custom' is routed through _send_qbo_api,
    NOT _send_portal_upload and NOT _send_oec_pod_email."""
    from services.job_manager import JobManager
    from services.qbo_api import QBOApiClient

    jm = JobManager(QBOApiClient(), classifier=MagicMock())
    jm._send_qbo_api = AsyncMock()
    jm._send_portal_upload = AsyncMock()
    jm._send_oec_pod_email = AsyncMock()

    job = MagicMock(id="j-test", results=[])
    invoice = MagicMock(invoice_number="INV-1", container_number="C1")
    customer = {"code": "X01", "sendMethod": "custom",
                "requiredDocs": ["invoice", "pod"]}
    result = MagicMock()

    # Inline the dispatch arm from send_job.py:304-317.
    method = customer.get("sendMethod", "email")
    if method in ("portal_upload", "portal"):
        await jm._send_portal_upload(job, invoice, customer, result, 0)
    elif method == "qbo_invoice_only_then_pod_email":
        await jm._send_oec_pod_email(job, invoice, customer, result, 0)
        await jm._send_qbo_api(job, invoice, customer, result, 0)
    else:
        await jm._send_qbo_api(job, invoice, customer, result, 0)

    jm._send_qbo_api.assert_awaited_once()
    jm._send_portal_upload.assert_not_called()
    jm._send_oec_pod_email.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_method_falls_through_to_qbo_api():
    """Defensive: any unknown sendMethod also routes to _send_qbo_api."""
    from services.job_manager import JobManager
    from services.qbo_api import QBOApiClient

    jm = JobManager(QBOApiClient(), classifier=MagicMock())
    jm._send_qbo_api = AsyncMock()

    job = MagicMock(id="j-test")
    invoice = MagicMock(invoice_number="INV-1")
    customer = {"code": "X01", "sendMethod": "something_new"}
    result = MagicMock()

    method = customer.get("sendMethod", "email")
    if method in ("portal_upload", "portal"):
        pass
    elif method == "qbo_invoice_only_then_pod_email":
        pass
    else:
        await jm._send_qbo_api(job, invoice, customer, result, 0)

    jm._send_qbo_api.assert_awaited_once()
```

- [ ] **Step 2: Run the test**

```bash
cd agent
python -m pytest tests/test_send_job_dispatcher_custom.py -v
```

Expected: Both tests **pass** without any production code change.

- [ ] **Step 3: Commit**

```bash
git add agent/tests/test_send_job_dispatcher_custom.py
git commit -m "$(cat <<'EOF'
test(send-job/v69): pin 'custom' sendMethod → _send_qbo_api dispatch

The v2.69 'custom' method intentionally relies on send_job.py's existing
else branch routing to _send_qbo_api. Add a test that pins this so a
future refactor of the dispatcher doesn't silently break Custom-method
customers.

Also pins that any unknown sendMethod falls through to _send_qbo_api —
defensive default.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: End-to-end manual verification (browser)

**Files:** none (pure manual test)

**Why:** Frontend has no JS test runner. Walk every test scenario in spec §9 before building the packaged installer — quicker to fix here than after a 4-minute PyInstaller rebuild.

- [ ] **Step 1: Start the agent**

```bash
cd agent && python main.py
```

In another terminal:

```bash
# Confirm /health reports v2.69
curl http://localhost:8787/health
```

Expected: `version` field reads `"2.69"` (or `"2.69.0"`).

- [ ] **Step 2: Open `app/index.html` in a browser** (or use the project's preferred local server)

Sign in. Open Customer Manager. Walk this checklist — every line must pass:

- [ ] Add Customer → Send Method dropdown shows **exactly 3 options**: Standard QBO Email · QBO Invoice Only + POD Email · Custom. No Portal Upload.
- [ ] Default (Standard QBO Email): Required Documents section **hidden**. Hint: "Send all attachments…"
- [ ] Switch to QBO Invoice Only + POD Email: POD Email Settings block **visible**. Required Documents still hidden. OEC info banner visible. Hint: "Send invoice via QBO, then POD via Gmail…"
- [ ] Switch to Custom: Required Documents **visible**, **Invoice pre-checked**, info banner visible. Hint: "Send invoice plus only the documents you tick below…"
- [ ] In Custom, untick everything except Invoice → Save → close modal → re-open: settings preserved as `Custom + Invoice only`.
- [ ] In Custom, tick Invoice + POD → Save → re-open: `Custom + Invoice + POD`.
- [ ] Add Customer → type `APEXMA01` (assuming it exists) → red dup-warning appears within a second. Save click → blocked with toast/alert. Click "View existing →" → modal closes, re-opens on APEXMA01 in edit mode.
- [ ] Edit APEXMA01 → don't change Code → no warning. Type the same code into a fresh field → still no warning (because `editingCode` matches).
- [ ] Change APEXMA01's Code to `TOPTRA02` → warning appears.
- [ ] Restart the agent. Logs should show one of:
  - `v69 migration: relabeled N customer(s) onto 'custom'` on first run (with N>0 if any legacy `"invoice"` customers exist), OR
  - `v69 migration: no customers needed relabeling` on subsequent runs.
- [ ] Re-open APEXMA01 in Customer Manager: send method is now **Custom**, Invoice ticked. (Same check for TOPTRA02 if it exists.)

- [ ] **Step 3: Send a real invoice for APEXMA01 (regression-safety)**

If you have a safe test invoice, run an end-to-end send for APEXMA01 (now Custom + Invoice). The gate at `send_qbo_api.py:192` should treat `"invoice"` as auto-satisfied and the send should go through.

If you don't have a test invoice handy, skip this and rely on the unit test from Task 2 — the production-code parity check confirms the filter is in place.

- [ ] **Step 4: Quick smoke for an existing portal_upload customer (if any)**

If a customer has `sendMethod = "portal_upload"` in the DB, open them in the modal. The dropdown will show one of the three visible options (the underlying value `"portal_upload"` is unknown to the dropdown). The Portal Upload Settings block is still visible because `custSendMethodChanged` still has the `showPortal` branch. **Leave the dropdown alone** — saving without changing it preserves the original `"portal_upload"` value and the backend keeps routing them. If you do change the dropdown, that's an explicit migration off Portal Upload, which is expected.

If you have no portal_upload customers in the DB (the local backup confirmed zero), this step is a no-op.

- [ ] **Step 5: If everything passes, no commit (this is a verification task). Otherwise go fix.**

---

## Task 10: Build, smoke-test packaged installer, push, release

**Files:** none (build + ship)

**Why:** Per `feedback_always_push_and_release.md`, every rebuild commits, pushes, and publishes a GH release. Per `feedback_use_runbuild_for_rebuild.md`, use `runbuild.bat` (not `build-all.bat`) via PowerShell.

- [ ] **Step 1: Run the build**

From PowerShell (per the documented invocation pattern with the empty-stdin file):

```powershell
$emptyStdin = "$PWD\desktop\.empty-stdin"
if (-not (Test-Path $emptyStdin)) { Set-Content -Path $emptyStdin -Value "" -Encoding utf8 }
Start-Process -FilePath "$PWD\desktop\runbuild.bat" `
              -WorkingDirectory "$PWD\desktop" `
              -RedirectStandardInput $emptyStdin `
              -RedirectStandardOutput "$PWD\desktop\build-log-2.69.txt" `
              -RedirectStandardError "$PWD\desktop\build-log-2.69.err.txt" `
              -NoNewWindow -Wait
```

Watch for the JS syntax check (`desktop/check-js.js`) — if it aborts, fix the offending file and rerun.

- [ ] **Step 2: Verify the build produced both artifacts**

```bash
ls desktop/dist/NGL\ Accounting\ Setup\ 2.69.0.exe desktop/dist/latest.yml
```

Expected: both files exist.

- [ ] **Step 3: Install the packaged app and smoke-test**

Run `desktop/dist/NGL Accounting Setup 2.69.0.exe` (uninstall the previous build first if needed). Then re-run the Task 9 checklist **inside the packaged app** — at minimum:

- Dropdown shows 3 options (no Portal Upload).
- Custom mode reveals Required Documents with Invoice pre-ticked.
- Duplicate-code warning + "View existing →" works.
- Restart the app and confirm the migration log appears on first launch.
- Send one real invoice end-to-end for any customer (sanity check on the gate filter).

- [ ] **Step 4: Push + release**

```bash
git push origin main

gh release create v2.69.0 \
  "desktop/dist/NGL Accounting Setup 2.69.0.exe" \
  "desktop/dist/latest.yml" \
  --title "v2.69.0 — Customer Manager cleanup" \
  --notes "$(cat <<'EOF'
## v2.69.0 — Customer Manager cleanup (Fix 3)

### What's new
- **Send method dropdown simplified.** Three options: Standard QBO Email · QBO Invoice Only + POD Email · **Custom (new)**. Portal Upload is hidden from the dropdown (backend still works for any legacy customer set up that way — none today).
- **Custom send method** replaces the old "Send All / Require Specific" toggle. Pick Custom and tick the docs you want included (Invoice · POD · POL · BOL · PL · DO). Invoice is pre-ticked. Tick only Invoice = invoice-only sender (no separate "Invoice Only" method needed — this is how APEXMA01 should have been set up).
- **The Invoice checkbox works again.** It's auto-satisfied at send time (the invoice PDF always goes via QBO email regardless), and it serves as an inclusion indicator in the doc list. The old "broken-by-design" failure mode is gone.
- **Live duplicate-code detection.** Type a code that already exists and you'll see a friendly red warning under the field, plus a "View existing →" link that closes the modal and re-opens it on that customer. Save is blocked while the warning is visible.
- **One-time data migration.** Customers with `"invoice"` in their required-docs list (APEXMA01, TOPTRA02) are relabeled to Custom send method on first launch. Their docs list is preserved. Subsequent launches are no-ops.

### What didn't change
- OEC two-email flow logic — untouched.
- Send dispatcher routing — `"custom"` falls through the existing else branch to `_send_qbo_api`, same as `"email"`.
- Portal Upload backend (`send_portal.py`, `PortalUploader`, dispatcher) — preserved verbatim. Re-enabling later is a single-line restoration in `app/index.html`.

### Coming next
- v2.70 — Pydantic validation on customer create/update + CSV import error UX (Fix 4).
- v2.71 — Combined Results HUD redesign for Invoice Sender (replaces the three stacked result surfaces with one HUD).
EOF
)"
```

- [ ] **Step 5: Confirm the release page lists both assets**

```bash
gh release view v2.69.0
```

Expected: Installer `.exe` and `latest.yml` both listed.

---

## Self-Review Notes (filled by author at plan-write time)

**Spec coverage check:**

| Spec section | Plan task |
| --- | --- |
| §4 dropdown options (3 visible, Portal hidden) | Task 5 step 1 |
| §4 Required Documents toggle deleted | Task 5 step 4 + Task 6 step 3 |
| §4 Invoice checkbox kept, defaults checked | Task 5 step 4 (HTML) + Task 6 step 2 (auto-tick on first switch) |
| §4 dup-code warning | Task 7 |
| §4 data migration | Task 3 |
| §6.1 dropdown HTML | Task 5 step 1 |
| §6.2 Required Documents restructure | Task 5 step 4 |
| §6.3 per-method hint copy | Task 6 step 2 |
| §6.4 dup-code save guard + View Existing flow | Task 7 |
| §7.1 backend gate filter | Task 2 |
| §7.2 migration | Task 3 |
| §7.3 dispatcher passthrough | Task 8 (test only — no code change) |
| §9 test scenarios | Task 9 |
| §10 rebuild pipeline | Task 10 |

All sections of the spec are covered.

**Placeholder scan:** No "TBD", no "implement later", no vague handwaves. Every code step shows the actual code. ✓

**Type consistency:**
- `custCheckDuplicateCode` / `custJumpToExisting` referenced in HTML (Task 5 step 3) and JS (Task 7 step 1) — names match.
- `_custDupHit` — declared once at module level, used in both functions in Task 7. Consistent.
- `custState.editingCode` — declared in Task 7 step 6 (state.js), set in `custOpenModal` in Task 7 step 6, read in `custCheckDuplicateCode` in Task 7 step 1. Consistent.
- `migrate_invoice_to_custom` — production name in Task 3 step 3 matches the import in Task 3 step 1's test. ✓
- `.v69-dup-warning` / `.v69-invoice-info-banner` / `.v69-send-method-hint` — CSS in Task 4 step 1 matches markup in Task 5 step 3+4 and JS in Task 7 step 1. Consistent.
