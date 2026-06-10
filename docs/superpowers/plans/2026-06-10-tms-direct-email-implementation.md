# TMS-Direct Email — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `_send_qbo_api` standard-email cascade (download from TMS → upload to QBO → re-read → email) with a direct flow (download from TMS → email). Eliminates the silent upload failure that dropped a POD on PROMAR01 LM26060242F (2026-06-08).

**Architecture:** TMS is the source of truth for supporting docs. QBO supplies only the invoice PDF. The cascade upload step is preserved as dead code (for future revival once the classifier and upload-retry issues are fixed) but no longer invoked. New statuses (`tms_unreachable`, `pod_missing`) let the user catch held sends in the results UI.

**Tech Stack:** Python 3.11 (FastAPI agent), SQLite + Supabase (PostgREST), vanilla JS frontend, pytest + pytest-asyncio, httpx.

**Spec:** `docs/superpowers/specs/2026-06-10-tms-direct-email-design.md`

---

## File Map

**Modified:**
- `agent/services/job_manager/__init__.py` — rename `SendResult.attachments_found` → `attachments_emailed`, update `to_dict()`, extend status comment
- `agent/services/job_manager/send_qbo_api.py` — rewrite `_send_qbo_api` body; add DISABLED header to `_tms_fetch_and_upload_missing_docs`
- `agent/services/job_manager/send_warehouse.py` — update `result.attachments_found` → `attachments_emailed`
- `agent/services/job_manager/send_job.py` — update `result.attachments_found` → `attachments_emailed`
- `agent/services/qbo_api/attachments.py` — add DISABLED header above `classify_attachment` + `DOC_PATTERNS`
- `agent/services/qbo_api/dedup.py` — add DISABLED header above `dedupe_attachments`
- `agent/services/tms_api.py` — wrap `get_work_order` in the same retry policy used by `download_document`
- `agent/services/database.py` — rename column, update `_insert_audit_entry` + `_row_to_audit_entry`
- `agent/services/supabase_client.py` — update audit log column mappings (3 sites)
- `app/assets/js/tools/invoice-sender/invoice-sender.js` — read `attachmentsEmailed` instead of `attachmentsFound`
- `app/assets/js/tools/invoice-sender/invoice-sender-results.js` — add "Needs retry" group for `tms_unreachable` / `pod_missing` with one-click retry

**Created:**
- `agent/tests/test_send_qbo_api/test_tms_direct_flow.py` — new tests for the new flow
- `agent/tests/test_tms_api_retry.py` — retry tests for `get_work_order`
- `agent/scripts/migrate_attachments_emailed.sql` — one-time column rename SQL (local + Supabase)

**Preserved (dead code with DISABLED header):**
- `_tms_fetch_and_upload_missing_docs` in `send_qbo_api.py`
- `classify_attachment` + `DOC_PATTERNS` in `attachments.py`
- `dedupe_attachments` in `dedup.py`

---

## Task 1: Rename `SendResult.attachments_found` → `attachments_emailed`

**Files:**
- Modify: `agent/services/job_manager/__init__.py:100, 105, 131`
- Modify: `agent/services/job_manager/send_warehouse.py:122`
- Modify: `agent/services/job_manager/send_job.py:399, 404`
- Test: `agent/tests/test_send_result_rename.py` (new)

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_send_result_rename.py`:

```python
"""Verify SendResult exposes attachments_emailed (the renamed field)."""
from services.job_manager import SendResult


def test_send_result_has_attachments_emailed_field():
    r = SendResult("INV-1", "ABCU000", "CUST")
    assert hasattr(r, "attachments_emailed")
    assert r.attachments_emailed == []


def test_to_dict_emits_attachments_emailed_key():
    r = SendResult("INV-1", "ABCU000", "CUST")
    r.attachments_emailed = ["pod", "do"]
    d = r.to_dict()
    assert "attachmentsEmailed" in d
    assert d["attachmentsEmailed"] == ["pod", "do"]
    assert "attachmentsFound" not in d


def test_attachments_missing_still_exists_for_warehouse_oec():
    r = SendResult("INV-1", "ABCU000", "CUST")
    assert hasattr(r, "attachments_missing")
    assert r.to_dict()["attachmentsMissing"] == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd "C:/Users/Joseph/Desktop/NGL ACCOUNTING SERVICE/agent"
python -m pytest tests/test_send_result_rename.py -v
```

Expected: FAIL with `AttributeError: 'SendResult' object has no attribute 'attachments_emailed'` and KeyError on `attachmentsEmailed`.

- [ ] **Step 3: Apply the rename**

In `agent/services/job_manager/__init__.py`:

Line 100, replace:
```python
        self.status: str = "pending"  # sent, skipped, skipped_no_attachments, error, mismatch, missing_docs
```
with:
```python
        self.status: str = "pending"  # sent, skipped, skipped_no_attachments, error, mismatch, missing_docs, tms_unreachable, pod_missing
```

Line 105, replace:
```python
        self.attachments_found: list[str] = []
```
with:
```python
        self.attachments_emailed: list[str] = []
```

Line 131, replace:
```python
            "attachmentsFound": self.attachments_found,
```
with:
```python
            "attachmentsEmailed": self.attachments_emailed,
```

In `agent/services/job_manager/send_warehouse.py:122`, replace:
```python
            result.attachments_found = attachments_display
```
with:
```python
            result.attachments_emailed = attachments_display
```

In `agent/services/job_manager/send_job.py`, replace both occurrences of `result.attachments_found` (around lines 399 and 404) with `result.attachments_emailed`.

In `agent/services/job_manager/send_qbo_api.py`, replace all occurrences of `result.attachments_found` with `result.attachments_emailed` (will be lines 202, 215, 232, 269, 311 — they'll mostly disappear in Task 7's rewrite, but rename them in place now to keep the codebase consistent).

- [ ] **Step 4: Run the rename test**

```bash
python -m pytest tests/test_send_result_rename.py -v
```

Expected: PASS.

- [ ] **Step 5: Run the full existing test suite to confirm nothing broke**

```bash
python -m pytest tests/ -x --timeout=30
```

Expected: PASS (or same pre-existing failures as before the change — note any new failures and address before commit).

- [ ] **Step 6: Commit**

```bash
git add agent/services/job_manager/__init__.py agent/services/job_manager/send_qbo_api.py agent/services/job_manager/send_warehouse.py agent/services/job_manager/send_job.py agent/tests/test_send_result_rename.py
git commit -m "refactor(send-result): rename attachments_found to attachments_emailed"
```

---

## Task 2: Migrate audit_log column (SQLite + Supabase)

**Files:**
- Create: `agent/scripts/migrate_attachments_emailed.sql`
- Modify: `agent/services/database.py:525-552, 564-583`
- Modify: `agent/services/supabase_client.py:262, 291, 416`

- [ ] **Step 1: Write the migration SQL**

Create `agent/scripts/migrate_attachments_emailed.sql`:

```sql
-- One-time migration: rename audit_log.attachments_found → attachments_emailed
-- Run once against local SQLite (~/AppData/Local/NGL Accounting/data/ngl.db)
-- AND once against Supabase (xkiunwaobjhpjhtzpvvs.supabase.co) via SQL editor.
--
-- Both SQLite and Postgres support ALTER TABLE RENAME COLUMN syntax.
ALTER TABLE audit_log RENAME COLUMN attachments_found TO attachments_emailed;
```

- [ ] **Step 2: Run the SQLite migration**

```bash
sqlite3 "$LOCALAPPDATA/NGL Accounting/data/ngl.db" < agent/scripts/migrate_attachments_emailed.sql
sqlite3 "$LOCALAPPDATA/NGL Accounting/data/ngl.db" "PRAGMA table_info(audit_log);" | grep -E "attachments_emailed|attachments_found"
```

Expected: one row containing `attachments_emailed`, zero rows containing `attachments_found`.

- [ ] **Step 3: Run the Supabase migration**

Open `https://supabase.com/dashboard/project/xkiunwaobjhpjhtzpvvs/sql/new` and paste the same SQL. Click Run. Confirm "Success. No rows returned."

Verify via REST:
```bash
python -c "
import httpx
url = 'https://xkiunwaobjhpjhtzpvvs.supabase.co'
key = open('agent/.env').read().split('SUPABASE_SERVICE_KEY=')[1].split()[0]
r = httpx.get(f'{url}/rest/v1/audit_log?select=attachments_emailed&limit=1', headers={'apikey': key, 'Authorization': f'Bearer {key}'}, timeout=10.0)
print(r.status_code, r.text[:200])
"
```

Expected: HTTP 200 with a JSON list (column accessible).

- [ ] **Step 4: Update `_insert_audit_entry` in database.py**

In `agent/services/database.py:525-552`, replace:

```python
def _insert_audit_entry(conn: sqlite3.Connection, entry: dict, commit: bool = True) -> None:
    """Insert a single audit log entry (camelCase keys from SendResult.to_dict())."""
    conn.execute("""
        INSERT INTO audit_log
        (timestamp, invoice_number, container_number, customer_code,
         status, to_emails, cc_emails, bcc_emails, subject,
         attachments_found, attachments_missing, error,
         do_sender_email, do_sender_source, username)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        entry.get("timestamp", datetime.now(timezone.utc).isoformat()),
        entry.get("invoiceNumber", ""),
        entry.get("containerNumber", ""),
        entry.get("customerCode", ""),
        entry.get("status", ""),
        json.dumps(entry.get("toEmails", [])),
        json.dumps(entry.get("ccEmails", [])),
        json.dumps(entry.get("bccEmails", [])),
        entry.get("subject", ""),
        json.dumps(entry.get("attachmentsFound", [])),
        json.dumps(entry.get("attachmentsMissing", [])),
        entry.get("error"),
        entry.get("doSenderEmail", ""),
        entry.get("doSenderSource", ""),
        entry.get("username", ""),
    ))
    if commit:
        conn.commit()
```

with:

```python
def _insert_audit_entry(conn: sqlite3.Connection, entry: dict, commit: bool = True) -> None:
    """Insert a single audit log entry (camelCase keys from SendResult.to_dict())."""
    conn.execute("""
        INSERT INTO audit_log
        (timestamp, invoice_number, container_number, customer_code,
         status, to_emails, cc_emails, bcc_emails, subject,
         attachments_emailed, attachments_missing, error,
         do_sender_email, do_sender_source, username)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        entry.get("timestamp", datetime.now(timezone.utc).isoformat()),
        entry.get("invoiceNumber", ""),
        entry.get("containerNumber", ""),
        entry.get("customerCode", ""),
        entry.get("status", ""),
        json.dumps(entry.get("toEmails", [])),
        json.dumps(entry.get("ccEmails", [])),
        json.dumps(entry.get("bccEmails", [])),
        entry.get("subject", ""),
        json.dumps(entry.get("attachmentsEmailed", [])),
        json.dumps(entry.get("attachmentsMissing", [])),
        entry.get("error"),
        entry.get("doSenderEmail", ""),
        entry.get("doSenderSource", ""),
        entry.get("username", ""),
    ))
    if commit:
        conn.commit()
```

- [ ] **Step 5: Update `_row_to_audit_entry` in database.py**

In `agent/services/database.py:564-583`, replace:

```python
        "attachmentsFound": json.loads(row["attachments_found"]),
```

with:

```python
        "attachmentsEmailed": json.loads(row["attachments_emailed"]),
```

- [ ] **Step 6: Update Supabase client at three sites**

In `agent/services/supabase_client.py`, replace at line 262 (inside `write_audit_entry`):

```python
        "attachments_found": entry.get("attachmentsFound", []),
```

with:

```python
        "attachments_emailed": entry.get("attachmentsEmailed", []),
```

At line 291 (inside `_sb_row_to_audit`):

```python
        "attachmentsFound": row.get("attachments_found", []),
```

with:

```python
        "attachmentsEmailed": row.get("attachments_emailed", []),
```

At line 416 (inside `migrate_audit_to_supabase`):

```python
            "attachments_found": e.get("attachmentsFound", []),
```

with:

```python
            "attachments_emailed": e.get("attachmentsEmailed", []),
```

- [ ] **Step 7: Smoke-test the round-trip**

```bash
python -c "
import sys, os
sys.path.insert(0, 'agent')
os.environ.setdefault('SUPABASE_URL','')  # force SQLite path
from services.database import write_audit_entry, query_audit_log
write_audit_entry({
    'invoiceNumber': 'TEST-RENAME-1', 'containerNumber': 'TEST0000001',
    'customerCode': 'TEST', 'status': 'sent',
    'attachmentsEmailed': ['pod', 'do'], 'attachmentsMissing': [],
})
res = query_audit_log('', '', '', 'TEST-RENAME-1', 10, 0)
print(res['entries'][0])
"
```

Expected: dict containing `"attachmentsEmailed": ["pod", "do"]`.

- [ ] **Step 8: Commit**

```bash
git add agent/scripts/migrate_attachments_emailed.sql agent/services/database.py agent/services/supabase_client.py
git commit -m "refactor(audit-log): rename attachments_found column to attachments_emailed"
```

---

## Task 3: Update frontend to read `attachmentsEmailed`

**Files:**
- Modify: `app/assets/js/tools/invoice-sender/invoice-sender.js:1606`

- [ ] **Step 1: Grep for any other frontend references**

```bash
cd "C:/Users/Joseph/Desktop/NGL ACCOUNTING SERVICE"
grep -rn "attachmentsFound\|attachments_found" app/assets/js/ --include="*.js"
```

Expected: only one hit at `invoice-sender.js:1606`. If more appear, update all of them.

- [ ] **Step 2: Apply the rename**

In `app/assets/js/tools/invoice-sender/invoice-sender.js:1606`, replace:

```javascript
      <div><strong>Attachments:</strong> ${(event.attachmentsFound || []).length ? escHtml(event.attachmentsFound.join(', ')) : '<span style="color:#d97706;">None detected</span>'}${event.podSource ? ' ' + invSourceBadge(event.podSource, 'Found in') : ''}</div>
```

with:

```javascript
      <div><strong>Attachments:</strong> ${(event.attachmentsEmailed || []).length ? escHtml(event.attachmentsEmailed.join(', ')) : '<span style="color:#d97706;">None detected</span>'}${event.podSource ? ' ' + invSourceBadge(event.podSource, 'Found in') : ''}</div>
```

- [ ] **Step 3: Run the JS syntax gate**

```bash
node desktop/check-js.js
```

Expected: "All JS files passed syntax check" (or equivalent success line).

- [ ] **Step 4: Commit**

```bash
git add app/assets/js/tools/invoice-sender/invoice-sender.js
git commit -m "refactor(ui): read attachmentsEmailed instead of attachmentsFound"
```

---

## Task 4: Add retry to `tms_api.get_work_order`

**Files:**
- Modify: `agent/services/tms_api.py:69-94`
- Test: `agent/tests/test_tms_api_retry.py` (new)

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_tms_api_retry.py`:

```python
"""Verify get_work_order retries on transient network errors (3 attempts, 1s/3s backoff)."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


@pytest.mark.asyncio
async def test_get_work_order_retries_three_times_on_connect_error():
    from services.tms_api import TMSApiClient

    client = TMSApiClient(client_id="x", client_secret="y", base_url="https://tms.test")
    client._token = "fake"
    client._token_expires_at = 9_999_999_999

    call_count = {"n": 0}

    async def mock_get(*args, **kwargs):
        call_count["n"] += 1
        raise httpx.ConnectError("simulated DNS blip")

    with patch.object(httpx.AsyncClient, "get", new=mock_get):
        with patch("asyncio.sleep", new=AsyncMock()):
            result = await client.get_work_order("LM2605280007")

    assert result is None
    assert call_count["n"] == 3, f"expected 3 attempts, got {call_count['n']}"


@pytest.mark.asyncio
async def test_get_work_order_returns_after_first_success():
    from services.tms_api import TMSApiClient

    client = TMSApiClient(client_id="x", client_secret="y", base_url="https://tms.test")
    client._token = "fake"
    client._token_expires_at = 9_999_999_999

    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = {"wo_no": "LM2605280007", "documents": []}
    mock_response.raise_for_status = MagicMock()

    call_count = {"n": 0}

    async def mock_get(*args, **kwargs):
        call_count["n"] += 1
        return mock_response

    with patch.object(httpx.AsyncClient, "get", new=mock_get):
        result = await client.get_work_order("LM2605280007")

    assert result == {"wo_no": "LM2605280007", "documents": []}
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_get_work_order_no_retry_on_404():
    from services.tms_api import TMSApiClient

    client = TMSApiClient(client_id="x", client_secret="y", base_url="https://tms.test")
    client._token = "fake"
    client._token_expires_at = 9_999_999_999

    mock_response = MagicMock(status_code=404)
    mock_response.raise_for_status = MagicMock()

    call_count = {"n": 0}

    async def mock_get(*args, **kwargs):
        call_count["n"] += 1
        return mock_response

    with patch.object(httpx.AsyncClient, "get", new=mock_get):
        result = await client.get_work_order("MISSING-WO")

    assert result is None
    assert call_count["n"] == 1, "404 must not retry"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_tms_api_retry.py -v
```

Expected: FAIL — `test_get_work_order_retries_three_times_on_connect_error` shows `call_count == 1` (no retry yet).

- [ ] **Step 3: Apply the retry implementation**

In `agent/services/tms_api.py`, replace the `get_work_order` method (lines 69-94):

```python
    async def get_work_order(self, wo_no: str) -> Optional[dict]:
        """Fetch a TMS WO record.

        Retries up to 3 times with 1s/3s backoff on transient network errors
        (DNS blip, brief connection refusal, read timeout). 4xx (incl. 404) and
        5xx are treated as permanent — no retry. Mirrors download_document's
        retry policy so the standard-email send path (which depends on this
        call) has the same resilience floor as direct file downloads.
        """
        if not wo_no:
            return None
        if not self.is_configured():
            logger.warning("[TMS_API] not configured — missing TMS_CLIENT_ID/SECRET")
            return None
        try:
            token = await self._get_token()
        except Exception as e:
            logger.warning("[TMS_API] token fetch failed: %s", e)
            return None

        url = f"{self._base_url}/api/v1/work-orders/{wo_no}"
        last_error: Optional[Exception] = None
        for attempt in range(_DOWNLOAD_RETRY_ATTEMPTS):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    r = await client.get(url, headers={"Authorization": f"Bearer {token}"})
                if r.status_code == 404:
                    logger.info("[TMS_API] WO %s not found (404)", wo_no)
                    return None
                if r.status_code >= 500:
                    logger.warning("[TMS_API] WO %s server error %s", wo_no, r.status_code)
                    return None
                r.raise_for_status()
                if attempt > 0:
                    logger.info("[TMS_API] get_work_order recovered on attempt %d for %s",
                                attempt + 1, wo_no)
                return r.json()
            except _TRANSIENT_NETWORK_ERRORS as e:
                last_error = e
                if attempt < _DOWNLOAD_RETRY_ATTEMPTS - 1:
                    backoff = _DOWNLOAD_RETRY_BACKOFF_SECONDS[attempt]
                    logger.warning(
                        "[TMS_API] get_work_order attempt %d/%d failed for %s (%s) — retrying in %.1fs",
                        attempt + 1, _DOWNLOAD_RETRY_ATTEMPTS,
                        wo_no, type(e).__name__, backoff,
                    )
                    await asyncio.sleep(backoff)
                    continue
            except Exception as e:
                logger.warning("[TMS_API] get_work_order(%s) failed: %s", wo_no, e)
                return None

        logger.error(
            "[TMS_API] get_work_order FAILED for %s after %d attempts — supporting docs will be missing from the email. Last error: %s",
            wo_no, _DOWNLOAD_RETRY_ATTEMPTS, last_error,
        )
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_tms_api_retry.py -v
```

Expected: PASS (all 3 tests).

- [ ] **Step 5: Run the wider TMS test suite**

```bash
python -m pytest tests/test_tms_data/ tests/test_tms_api_retry.py -v
```

Expected: PASS (no regressions in pre-existing TMS tests).

- [ ] **Step 6: Commit**

```bash
git add agent/services/tms_api.py agent/tests/test_tms_api_retry.py
git commit -m "feat(tms-api): add retry-with-backoff to get_work_order"
```

---

## Task 5: Add a `get_work_order_required` accessor on the TMS data layer

**Why:** The new `_send_qbo_api` needs to know "did `get_work_order` return None because of unreachable TMS vs. WO genuinely missing?" so it can distinguish `tms_unreachable` from `pod_missing`. Currently `get_all_documents` swallows both into an empty dict. Add a thin signal-passing helper.

**Files:**
- Modify: `agent/services/tms_data/__init__.py` (add method)
- Modify: `agent/services/tms_data/cascade.py:159-235` (return discriminated reason)

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_tms_data/test_get_all_documents_reason.py`:

```python
"""Verify get_all_documents distinguishes 'no WO#', 'WO 404', and 'TMS unreachable'."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.tms_data import TMSDataLayer


@pytest.mark.asyncio
async def test_returns_unreachable_when_get_work_order_raises(tmp_path):
    tms_api = MagicMock()
    tms_api.get_work_order = AsyncMock(side_effect=RuntimeError("ConnectError"))
    layer = TMSDataLayer(tms_api=tms_api, tms_browser=None)

    paths, reason = await layer.get_all_documents_with_reason(
        job_id="j1", invoice_data={"CustomField": [{"Name": "NGL REF#", "StringValue": "LM2605280007/X"}]},
        dest_dir=tmp_path,
    )
    assert paths == {}
    assert reason == "tms_unreachable"


@pytest.mark.asyncio
async def test_returns_wo_not_found_when_get_work_order_returns_none(tmp_path):
    tms_api = MagicMock()
    tms_api.get_work_order = AsyncMock(return_value=None)
    layer = TMSDataLayer(tms_api=tms_api, tms_browser=None)

    paths, reason = await layer.get_all_documents_with_reason(
        job_id="j1", invoice_data={"CustomField": [{"Name": "NGL REF#", "StringValue": "LM2605280007/X"}]},
        dest_dir=tmp_path,
    )
    assert paths == {}
    assert reason == "wo_not_found"


@pytest.mark.asyncio
async def test_returns_ok_when_documents_returned(tmp_path):
    tms_api = MagicMock()
    tms_api.get_work_order = AsyncMock(return_value={
        "wo_no": "LM2605280007",
        "documents": [
            {"type_": "POD", "file_url": "https://x/pod.pdf"},
            {"type_": "DO", "file_url": "https://x/do.pdf"},
        ],
    })
    tms_api.download_document = AsyncMock(return_value=b"%PDF-content")
    layer = TMSDataLayer(tms_api=tms_api, tms_browser=None)

    paths, reason = await layer.get_all_documents_with_reason(
        job_id="j1", invoice_data={"CustomField": [{"Name": "NGL REF#", "StringValue": "LM2605280007/X"}]},
        dest_dir=tmp_path,
    )
    assert reason == "ok"
    assert set(paths.keys()) == {"pod", "do"}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_tms_data/test_get_all_documents_reason.py -v
```

Expected: FAIL with `AttributeError: 'TMSDataLayer' object has no attribute 'get_all_documents_with_reason'`.

- [ ] **Step 3: Extend the cascade to return a reason**

In `agent/services/tms_data/cascade.py`, add a new function below `run_all_documents` (around line 245):

```python
async def run_all_documents_with_reason(
    invoice_data: dict,
    dest_dir: Path,
    tms_api,
    *,
    skip_types: Optional[set[str]] = None,
) -> Tuple[dict[str, Path], dict[str, str], str]:
    """Like run_all_documents but returns a discriminated reason string instead of top_error.

    Reasons:
      - 'ok'              — wo was fetched (may still have zero downloadable docs)
      - 'no_wo_number'    — invoice_data has no WO# field
      - 'wo_not_found'    — get_work_order returned None (404 / not in TMS)
      - 'tms_unreachable' — get_work_order raised

    Used by send_qbo_api to distinguish "TMS is down, hold the send" from
    "WO simply has no POD yet, decide based on customer config".
    """
    import asyncio

    wo_no = extract_wo_from_qbo(invoice_data)
    if not wo_no:
        return {}, {}, "no_wo_number"

    try:
        wo = await tms_api.get_work_order(wo_no)
    except Exception as e:
        logger.warning("TMS API get_work_order failed for %s: %s", wo_no, e)
        return {}, {}, "tms_unreachable"

    if not wo:
        return {}, {}, "wo_not_found"

    paths: dict[str, Path] = {}
    per_doc_errors: dict[str, str] = {}

    work: list[Tuple[str, str]] = []
    skip = skip_types or set()
    for doc in wo.get("documents") or []:
        if not isinstance(doc, dict):
            continue
        type_raw = doc.get("type_") or ""
        url = doc.get("file_url") or ""
        if not type_raw or not url:
            continue
        doc_type = type_raw.lower()
        if doc_type in skip:
            continue
        work.append((doc_type, url))

    if not work:
        return paths, per_doc_errors, "ok"

    dest_dir.mkdir(parents=True, exist_ok=True)

    async def _download_one(doc_type: str, url: str) -> Tuple[str, Optional[Path], Optional[str]]:
        try:
            data = await tms_api.download_document(url)
        except Exception as e:
            logger.warning("TMS API download_document(%s) raised: %s", url, e)
            return doc_type, None, str(e)
        if not data:
            return doc_type, None, f"Document download returned no data for {doc_type}"
        path = dest_dir / f"{wo_no}_{doc_type}.pdf"
        path.write_bytes(data)
        return doc_type, path, None

    results = await asyncio.gather(*(_download_one(d, u) for d, u in work))
    for doc_type, path, err in results:
        if path:
            paths[doc_type] = path
        elif err:
            per_doc_errors[doc_type] = err

    return paths, per_doc_errors, "ok"
```

- [ ] **Step 4: Wire it up in `TMSDataLayer`**

In `agent/services/tms_data/__init__.py`, after the existing `get_all_documents` method (around line 261), add:

```python
    async def get_all_documents_with_reason(
        self,
        job_id: str,
        invoice_data: dict,
        dest_dir: Path,
        source: Source = "api",
        *,
        skip_types: Optional[set[str]] = None,
    ) -> tuple[dict[str, Path], str]:
        """Like get_all_documents but returns (paths, reason) instead of just paths.

        Reasons (see cascade.run_all_documents_with_reason):
          - 'ok' — work order fetched, any present file_urls downloaded
          - 'no_wo_number' — invoice has no WO# field on QBO
          - 'wo_not_found' — TMS doesn't know this WO (404)
          - 'tms_unreachable' — TMS API call raised after retries exhausted
        """
        if source == "browser":
            raise NotImplementedError(
                "get_all_documents_with_reason only supports source='api'."
            )

        from services.tms_data.cascade import run_all_documents_with_reason

        cached = self._CachedTmsApi(self._tms_api, self._wo_cache, self._in_flight, job_id)
        paths, per_doc_errors, reason = await run_all_documents_with_reason(
            invoice_data, dest_dir, cached, skip_types=skip_types,
        )

        for doc_type, err in per_doc_errors.items():
            row_id = self._failed.record_failure(
                job_id=job_id,
                invoice_number=_invoice_label(invoice_data),
                container_number=None,
                operation="get_document",
                doc_type=doc_type,
                error_message=err,
                source="tms_api",
            )
            self._retry_ctx[row_id] = {
                "operation": "get_document",
                "invoice_data": invoice_data,
                "doc_type": doc_type,
                "dest_dir": dest_dir,
            }

        return paths, reason
```

- [ ] **Step 5: Run the new tests**

```bash
python -m pytest tests/test_tms_data/test_get_all_documents_reason.py -v
```

Expected: PASS (all 3 tests).

- [ ] **Step 6: Run existing tms_data tests for regressions**

```bash
python -m pytest tests/test_tms_data/ -v
```

Expected: PASS (no regressions).

- [ ] **Step 7: Commit**

```bash
git add agent/services/tms_data/cascade.py agent/services/tms_data/__init__.py agent/tests/test_tms_data/test_get_all_documents_reason.py
git commit -m "feat(tms-data): add get_all_documents_with_reason for tms_unreachable distinction"
```

---

## Task 6: Write failing tests for the new `_send_qbo_api` flow

**Files:**
- Create: `agent/tests/test_send_qbo_api/test_tms_direct_flow.py`

- [ ] **Step 1: Write the failing tests**

Create `agent/tests/test_send_qbo_api/test_tms_direct_flow.py`:

```python
"""End-to-end tests for the TMS-direct standard email send flow (2026-06-10).

The new flow:
  1. Fetch every TMS doc with a file_url
  2. If customer requires POD and TMS returned none → status=pod_missing, no email
  3. If TMS unreachable → status=tms_unreachable, no email
  4. Otherwise: email = QBO invoice PDF + every TMS doc
  5. NO uploads to QBO

These tests exercise SendJobMixin._send_qbo_api as it stands after Task 7.
"""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_jm(tms_return_paths=None, tms_reason="ok", upload_should_not_be_called=True):
    from services.job_manager import JobManager
    from services.qbo_api import QBOApiClient

    jm = JobManager(QBOApiClient(), classifier=MagicMock())
    jm._emit_send = AsyncMock()
    jm._emit_failed_rows_changed = AsyncMock()
    jm._write_audit_log = MagicMock()

    layer = MagicMock()
    layer.get_all_documents_with_reason = AsyncMock(
        return_value=(tms_return_paths or {}, tms_reason),
    )
    layer.get_failed_rows = MagicMock(return_value=[])
    jm.set_tms_data(layer)

    email_sender = MagicMock()
    email_sender.send_invoice_email = AsyncMock(return_value={"sent": True})
    jm._email_sender = email_sender

    return jm, layer, email_sender


def _make_invoice():
    inv = MagicMock(
        invoice_number="LM26060242F",
        container_number="TEMU7671557",
        customer_code="PROMAR01",
        amount=None,
        subject=None,
        do_sender_email=None,
        is_resend=False,
    )
    return inv


def _make_api(invoice_id="400638", invoice_pdf_bytes=b"%PDF-invoice"):
    api = MagicMock()
    api.search_invoice = AsyncMock(return_value={
        "Id": invoice_id, "DocNumber": "LM26060242F",
        "CustomField": [{"Name": "NGL REF#", "StringValue": "LM2605280007/ASN616574"}],
        "CustomerRef": {"name": "[PROMAR01] PRO-MART"},
        "TotalAmt": "100.00", "DueDate": "2026-07-05",
    })
    api.verify_invoice_details = AsyncMock(return_value={
        "verified": True, "found_container": "TEMU7671557",
    })
    api.download_invoice_pdf = AsyncMock(return_value=invoice_pdf_bytes)
    api.get_invoice_link = AsyncMock(return_value="https://qbo.example/pay")
    api.upload_attachment = AsyncMock(side_effect=AssertionError("upload_attachment must NOT be called"))
    return api


@pytest.mark.asyncio
async def test_email_includes_all_tms_docs_plus_invoice_pdf(tmp_path):
    """Happy path: TMS returns 4 docs, email payload has 5 items (4 TMS + invoice PDF)."""
    from services.job_manager import SendResult

    tms_paths = {}
    for dt in ("pod", "do", "it", "ite"):
        p = tmp_path / f"LM2605280007_{dt}.pdf"
        p.write_bytes(f"%PDF-{dt}".encode())
        tms_paths[dt] = p

    jm, layer, email_sender = _make_jm(tms_return_paths=tms_paths)
    jm._qbo_api = _make_api()
    customer = {
        "code": "PROMAR01", "name": "PRO-MART",
        "emails": ["accountspayable@promartinc.com"],
        "sendMethod": "email", "requiredDocs": [],
    }
    result = SendResult("LM26060242F", "TEMU7671557", "PROMAR01")
    job = MagicMock(id="job-1", test_mode=False)

    await jm._send_qbo_api(job, _make_invoice(), customer, result, 0)

    assert result.status == "sent"
    assert email_sender.send_invoice_email.await_count == 1
    call_kwargs = email_sender.send_invoice_email.await_args.kwargs
    attached_names = [a["filename"] for a in call_kwargs["attachments"]]
    assert "LM26060242F.pdf" in attached_names
    for dt in ("pod", "do", "it", "ite"):
        assert any(f"LM2605280007_{dt}.pdf" == n for n in attached_names), \
            f"missing {dt} in {attached_names}"
    assert sorted(result.attachments_emailed) == ["do", "ite", "it", "pod"]


@pytest.mark.asyncio
async def test_pod_missing_when_required_and_tms_has_no_pod(tmp_path):
    """Customer.required_docs has 'pod', TMS returns no POD → status=pod_missing, no email."""
    from services.job_manager import SendResult

    tms_paths = {}
    for dt in ("do", "it"):
        p = tmp_path / f"LM2605280007_{dt}.pdf"
        p.write_bytes(b"%PDF-x")
        tms_paths[dt] = p

    jm, layer, email_sender = _make_jm(tms_return_paths=tms_paths)
    jm._qbo_api = _make_api()
    customer = {
        "code": "PROMAR01", "name": "PRO-MART",
        "emails": ["accountspayable@promartinc.com"],
        "sendMethod": "email", "requiredDocs": ["pod"],
    }
    result = SendResult("LM26060242F", "TEMU7671557", "PROMAR01")
    job = MagicMock(id="job-1", test_mode=False)

    await jm._send_qbo_api(job, _make_invoice(), customer, result, 0)

    assert result.status == "pod_missing"
    assert email_sender.send_invoice_email.await_count == 0
    assert "POD" in (result.error or "")


@pytest.mark.asyncio
async def test_tms_unreachable_holds_send(tmp_path):
    """TMS API failure → status=tms_unreachable, no email."""
    from services.job_manager import SendResult

    jm, layer, email_sender = _make_jm(tms_return_paths={}, tms_reason="tms_unreachable")
    jm._qbo_api = _make_api()
    customer = {
        "code": "PROMAR01", "name": "PRO-MART",
        "emails": ["accountspayable@promartinc.com"],
        "sendMethod": "email", "requiredDocs": [],
    }
    result = SendResult("LM26060242F", "TEMU7671557", "PROMAR01")
    job = MagicMock(id="job-1", test_mode=False)

    await jm._send_qbo_api(job, _make_invoice(), customer, result, 0)

    assert result.status == "tms_unreachable"
    assert email_sender.send_invoice_email.await_count == 0
    assert "TMS" in (result.error or "")


@pytest.mark.asyncio
async def test_no_qbo_upload_attempted(tmp_path):
    """The new flow must never call upload_attachment. api.upload_attachment side_effect=AssertionError."""
    from services.job_manager import SendResult

    tms_paths = {}
    for dt in ("pod", "do"):
        p = tmp_path / f"LM2605280007_{dt}.pdf"
        p.write_bytes(b"%PDF-x")
        tms_paths[dt] = p

    jm, layer, email_sender = _make_jm(tms_return_paths=tms_paths)
    jm._qbo_api = _make_api()  # api.upload_attachment will raise AssertionError if called
    customer = {
        "code": "PROMAR01", "name": "PRO-MART",
        "emails": ["accountspayable@promartinc.com"],
        "sendMethod": "email", "requiredDocs": [],
    }
    result = SendResult("LM26060242F", "TEMU7671557", "PROMAR01")
    job = MagicMock(id="job-1", test_mode=False)

    await jm._send_qbo_api(job, _make_invoice(), customer, result, 0)

    # If upload_attachment were called, the AssertionError would propagate.
    assert result.status == "sent"


@pytest.mark.asyncio
async def test_wo_not_found_proceeds_when_pod_not_required(tmp_path):
    """TMS returns wo_not_found, customer doesn't require POD → send the invoice PDF alone."""
    from services.job_manager import SendResult

    jm, layer, email_sender = _make_jm(tms_return_paths={}, tms_reason="wo_not_found")
    jm._qbo_api = _make_api()
    customer = {
        "code": "PROMAR01", "name": "PRO-MART",
        "emails": ["accountspayable@promartinc.com"],
        "sendMethod": "email", "requiredDocs": [],
    }
    result = SendResult("LM26060242F", "TEMU7671557", "PROMAR01")
    job = MagicMock(id="job-1", test_mode=False)

    await jm._send_qbo_api(job, _make_invoice(), customer, result, 0)

    assert result.status == "sent"
    assert email_sender.send_invoice_email.await_count == 1
    attached = email_sender.send_invoice_email.await_args.kwargs["attachments"]
    assert len(attached) == 1  # invoice PDF only
    assert result.attachments_emailed == []
```

- [ ] **Step 2: Run tests to verify they all fail**

```bash
python -m pytest tests/test_send_qbo_api/test_tms_direct_flow.py -v
```

Expected: FAIL on all 5 tests (the new flow doesn't exist yet — current `_send_qbo_api` still runs the cascade).

- [ ] **Step 3: Commit tests-only**

```bash
git add agent/tests/test_send_qbo_api/test_tms_direct_flow.py
git commit -m "test(send-qbo-api): failing tests for TMS-direct flow"
```

---

## Task 7: Rewrite `_send_qbo_api` to use the TMS-direct flow

**Files:**
- Modify: `agent/services/job_manager/send_qbo_api.py:129-491` (the `_send_qbo_api` method body)

- [ ] **Step 1: Replace the `_send_qbo_api` method**

In `agent/services/job_manager/send_qbo_api.py`, replace the entire `_send_qbo_api` method (starting at line 129) with:

```python
    async def _send_qbo_api(self, job, invoice, customer: dict,
                             result, index: int) -> None:
        """Standard email send: QBO API for the invoice PDF + TMS API for supporting docs + Gmail SMTP for delivery.

        Flow (see docs/superpowers/specs/2026-06-10-tms-direct-email-design.md):
          1. Search QBO for the invoice → invoice_id, customer fields, ref fields
          2. Verify invoice details (container#, amount)
          3. Fetch every TMS doc with a file_url for the WO
          4. If customer requires POD and TMS returned none → status=pod_missing, hold
          5. If TMS unreachable after retries → status=tms_unreachable, hold
          6. Download invoice PDF from QBO
          7. Email = invoice PDF + every TMS doc
          8. Audit row records attachments_emailed = [tms doc types]

        OEC flow is dispatched separately by send_job.py — this method only handles
        the non-OEC standard email path. Warehouse and portal use their own mixins.
        """
        customer_emails = normalize_email_list(customer.get("emails", []))
        if not customer_emails:
            result.status = "skipped"
            result.error = f"No emails configured for customer: {invoice.customer_code}"
            await self._emit_send(job, "invoice_skipped", {
                "invoiceNumber": invoice.invoice_number,
                "reason": "no_emails",
                "customerCode": invoice.customer_code,
            })
            return

        api = self._qbo_api

        # Step 1: Search QBO for the invoice.
        await self._emit_send(job, "searching_invoice", {
            "invoiceNumber": invoice.invoice_number,
        })

        invoice_data = await api.search_invoice(invoice.invoice_number)
        if not invoice_data:
            result.status = "error"
            result.error = f"Invoice {invoice.invoice_number} not found in QBO"
            await self._emit_send(job, "invoice_not_found", {
                "invoiceNumber": invoice.invoice_number,
            })
            return

        invoice_id = invoice_data["Id"]

        # Step 2: Verify invoice details.
        await self._emit_send(job, "verifying_invoice", {
            "invoiceNumber": invoice.invoice_number,
            "containerNumber": invoice.container_number,
        })

        verification = await api.verify_invoice_details(
            invoice_data, invoice.container_number, invoice.amount or None
        )
        if not verification.get("verified"):
            result.status = "mismatch"
            result.error = verification.get("reason", "Verification failed")
            await self._emit_send(job, "invoice_mismatch", {
                "invoiceNumber": invoice.invoice_number,
                "containerNumber": invoice.container_number,
                "reason": result.error,
            })
            return

        if verification.get("amount_note"):
            await self._emit_send(job, "invoice_amount_warning", {
                "invoiceNumber": invoice.invoice_number,
                "note": verification["amount_note"],
            })

        # Step 3: Fetch every TMS doc with a file_url. TMS is the source of truth
        # for supporting docs in the new flow — we never upload to QBO from here.
        await self._emit_send(job, "tms_fetching_docs", {
            "invoiceNumber": invoice.invoice_number,
            "containerNumber": verification.get("found_container") or invoice.container_number or "",
            "docTypes": [],
        })

        if not self._tms_data:
            logger.warning("TMSDataLayer not configured — skipping TMS doc fetch for %s",
                           invoice.invoice_number)
            tms_paths: dict = {}
            tms_reason = "tms_unreachable"
        else:
            temp_dir = Path(tempfile.mkdtemp(prefix="ngl_docs_"))
            try:
                rows_before = len(self._tms_data.get_failed_rows(job.id))
                tms_paths, tms_reason = await asyncio.wait_for(
                    self._tms_data.get_all_documents_with_reason(
                        job.id, invoice_data, temp_dir, source="api",
                    ),
                    timeout=TMS_FETCH_TIMEOUT_S,
                )
                if len(self._tms_data.get_failed_rows(job.id)) > rows_before:
                    await self._emit_failed_rows_changed(job, "added")
            except asyncio.TimeoutError:
                logger.warning("TMS doc fetch timed out after %ds for %s",
                               TMS_FETCH_TIMEOUT_S, invoice.invoice_number)
                tms_paths, tms_reason = {}, "tms_unreachable"

        # Step 4: TMS unreachable → hold send.
        if tms_reason == "tms_unreachable":
            result.status = "tms_unreachable"
            result.error = "TMS unreachable after retries — supporting docs could not be fetched"
            await self._emit_send(job, "tms_fetch_error", {
                "invoiceNumber": invoice.invoice_number,
                "error": result.error,
            })
            self._cleanup_temp(locals().get("temp_dir"))
            return

        # Step 5: POD required but missing → hold send.
        required_docs = [d.lower() for d in customer.get("requiredDocs", [])]
        pod_required = "pod" in required_docs
        if pod_required and "pod" not in tms_paths:
            result.status = "pod_missing"
            wo_no = ""
            for f in invoice_data.get("CustomField", []) or []:
                if "REF" in (f.get("Name") or "").upper():
                    val = (f.get("StringValue") or "").split("/", 1)[0].strip()
                    if val:
                        wo_no = val
                        break
            result.error = f"POD not yet available on TMS for WO {wo_no or '<unknown>'}"
            await self._emit_send(job, "invoice_pod_missing", {
                "invoiceNumber": invoice.invoice_number,
                "woNo": wo_no,
            })
            self._cleanup_temp(locals().get("temp_dir"))
            return

        # Step 6: Build email fields.
        container = verification.get("found_container") or invoice.container_number or ""
        subject = invoice.subject or f"[NGL_INV] {invoice.invoice_number} - Container#{container}"
        to_emails = customer_emails
        cc_emails = ["ar@ngltrans.net"] + normalize_email_list(customer.get("ccEmails", []))
        bcc_emails = normalize_email_list(customer.get("bccEmails", []))

        result.to_emails = to_emails
        result.cc_emails = cc_emails
        result.bcc_emails = bcc_emails
        result.subject = subject
        result.attachments_emailed = sorted(tms_paths.keys())

        await self._emit_send(job, "filling_send_form", {
            "invoiceNumber": invoice.invoice_number,
            "toEmails": to_emails,
            "subject": subject,
        })

        # Test mode approval gate (unchanged from old flow).
        if job.test_mode:
            approved = await self._wait_for_approval(
                job, invoice, result, index,
                to_emails, cc_emails, bcc_emails, subject,
                attachments_display=result.attachments_emailed,
            )
            if not approved:
                self._cleanup_temp(locals().get("temp_dir"))
                return

        # Step 7: Download invoice PDF + build attachments list.
        await self._emit_send(job, "downloading_attachments", {
            "invoiceNumber": invoice.invoice_number,
            "count": len(tms_paths) + 1,  # +1 for invoice PDF
        })

        email_attachments: list[dict] = []

        invoice_pdf = await api.download_invoice_pdf(invoice_id)
        if invoice_pdf:
            email_attachments.append({
                "filename": f"{invoice.invoice_number}.pdf",
                "data": invoice_pdf,
            })

        for doc_type, path in tms_paths.items():
            try:
                email_attachments.append({
                    "filename": path.name,
                    "data": path.read_bytes(),
                })
            except Exception as e:
                logger.warning("Failed to read TMS doc %s for email: %s", path, e)

        if not email_attachments:
            result.status = "error"
            result.error = "Failed to download invoice PDF and TMS attachments"
            await self._emit_send(job, "invoice_error", {
                "invoiceNumber": invoice.invoice_number,
                "error": result.error,
            })
            self._cleanup_temp(locals().get("temp_dir"))
            return

        # Step 8: Send via Gmail SMTP.
        await self._emit_send(job, "sending_invoice", {
            "invoiceNumber": invoice.invoice_number,
            "method": "gmail",
        })

        if not self._email_sender:
            result.status = "error"
            result.error = "Gmail email sender not configured"
            await self._emit_send(job, "invoice_error", {
                "invoiceNumber": invoice.invoice_number,
                "error": result.error,
            })
            self._cleanup_temp(locals().get("temp_dir"))
            return

        # Build email body (unchanged from old flow).
        customer_name = invoice_data.get("CustomerRef", {}).get("name", "")
        if "] " in customer_name:
            customer_name = customer_name.split("] ", 1)[1]

        ngl_ref = ""
        customer_ref = ""
        for field in invoice_data.get("CustomField", []):
            name = field.get("Name", "").upper()
            val = field.get("StringValue", "")
            if "REF" in name and "/" in val:
                parts = val.split("/", 1)
                if not ngl_ref:
                    ngl_ref = parts[0].strip()
                customer_ref = parts[1].strip() if len(parts) > 1 else ""
                break

        due_date = invoice_data.get("DueDate", "")
        amount = str(invoice_data.get("TotalAmt", ""))
        invoice_link = await api.get_invoice_link(invoice_id)

        body = build_invoice_email_html(
            invoice_number=invoice.invoice_number,
            container=container,
            customer_name=customer_name,
            amount=amount,
            due_date=due_date,
            ngl_ref=ngl_ref,
            customer_ref=customer_ref,
            invoice_link=invoice_link,
            resend_notice=RESEND_NOTICE,
        )

        send_result = await self._email_sender.send_invoice_email(
            to=to_emails,
            cc=cc_emails,
            bcc=bcc_emails,
            subject=subject,
            body=body,
            attachments=email_attachments,
        )

        if send_result.get("sent"):
            result.status = "sent"
            result.error = None
            await self._emit_send(job, "invoice_sent", {
                "invoiceNumber": invoice.invoice_number,
                "containerNumber": container,
                "toEmails": to_emails,
                "subject": subject,
                "method": "gmail",
                "attachmentCount": len(email_attachments),
            })
        else:
            result.status = "error"
            result.error = send_result.get("error", "Gmail send failed")
            await self._emit_send(job, "invoice_error", {
                "invoiceNumber": invoice.invoice_number,
                "error": result.error,
            })

        self._cleanup_temp(locals().get("temp_dir"))
```

- [ ] **Step 2: Run the new-flow tests**

```bash
python -m pytest tests/test_send_qbo_api/test_tms_direct_flow.py -v
```

Expected: PASS (all 5 tests).

- [ ] **Step 3: Run the full send-related test suite for regressions**

```bash
python -m pytest tests/test_send_qbo_api/ tests/test_job_manager/ -v --timeout=30
```

Expected: PASS for new tests; pre-existing `test_tms_fetch_and_upload.py` and `test_send_qbo_api_tms_data.py` still pass (they test the preserved dead-code function `_tms_fetch_and_upload_missing_docs`).

- [ ] **Step 4: Commit**

```bash
git add agent/services/job_manager/send_qbo_api.py
git commit -m "feat(send-qbo-api): TMS-direct email flow (skip QBO upload)"
```

---

## Task 8: Mark cascade and classifier as DISABLED dead code

**Files:**
- Modify: `agent/services/job_manager/send_qbo_api.py:51-127` (the `_tms_fetch_and_upload_missing_docs` method)
- Modify: `agent/services/qbo_api/attachments.py:26-43` (the `DOC_PATTERNS` dict and `classify_attachment` function)
- Modify: `agent/services/qbo_api/dedup.py:14-53` (the `dedupe_attachments` function)

- [ ] **Step 1: Add DISABLED header to `_tms_fetch_and_upload_missing_docs`**

In `agent/services/job_manager/send_qbo_api.py:51`, the method's existing docstring currently starts with "Fetch every TMS document for the WO; upload to QBO any not already attached." Prepend the following block to that docstring (insert it after the opening `"""` and before the existing text — do NOT delete the existing text):

```
DISABLED from standard email send (2026-06-10). Preserved as dead code.

See docs/superpowers/specs/2026-06-10-tms-direct-email-design.md for context.

To re-enable: (1) fix DOC_PATTERNS in attachments.py so DO/IT/ITE recognize
TMS-style `_<type>_<ms-timestamp>.pdf` filenames; (2) add retry to
upload_attachment in attachments.py mirroring download_document's policy;
(3) persist cascade failures to the audit_log row (new column or status).

Replaced by direct TMS→email attachment in _send_qbo_api after a silent
upload failure on PROMAR01 LM26060242F emailed the invoice without a POD
on 2026-06-08.

---

```

(The `---` separator is part of the literal docstring — it visually divides the new header from the preserved original text. The method body itself stays untouched — still referenced by `test_tms_fetch_and_upload.py`.)

- [ ] **Step 2: Add DISABLED header to `classify_attachment` / `DOC_PATTERNS`**

In `agent/services/qbo_api/attachments.py:26`, prepend above the `DOC_PATTERNS` dict:

```python
# DISABLED from standard email send (2026-06-10). Preserved for warehouse path
# and the dead-code cascade in send_qbo_api._tms_fetch_and_upload_missing_docs.
# See docs/superpowers/specs/2026-06-10-tms-direct-email-design.md.
#
# Known inconsistency: "pod" patterns are forgiving (match anywhere `_pod`
# appears, including TMS-style `_pod_<ms-timestamp>.pdf`), but "do" / "invoice"
# patterns require a literal period (`_do\.`, `_it\.`). TMS-generated filenames
# never match the period rule, so DO and IT classify as "other". This is why
# the cascade dedup skip-list misses TMS-style files and would create duplicate
# uploads — masked on 2026-06-08 only because the upload was also failing
# silently. Fix BEFORE re-enabling the cascade.
DOC_PATTERNS = {
```

- [ ] **Step 3: Add DISABLED header to `dedupe_attachments`**

In `agent/services/qbo_api/dedup.py:14`, replace the existing docstring of `dedupe_attachments` with:

```python
def dedupe_attachments(attachments: list[dict]) -> tuple[list[dict], list[dict]]:
    """DISABLED from standard email send (2026-06-10). Still used by warehouse path.

    See docs/superpowers/specs/2026-06-10-tms-direct-email-design.md.

    Returns (kept, skipped). Two attachments are duplicates if (filename.lower().strip(),
    size) match. Tie-breaker: keep the attachment with the highest int(id) — QBO IDs
    are monotonic, so highest = most recent upload.

    Originally used by send_qbo_api._send_qbo_api to drop TMS-008 duplicate
    Attachable records before email. Standard email no longer reads QBO
    attachments at all (TMS-direct), so the workaround is moot for that path.
    """
```

- [ ] **Step 4: Run the existing tests for the disabled functions to confirm they still pass**

```bash
python -m pytest tests/test_send_qbo_api/test_tms_fetch_and_upload.py tests/test_job_manager/test_send_qbo_api_tms_data.py -v
```

Expected: PASS — disabled functions still work, they're just no longer called from the live send path.

- [ ] **Step 5: Commit**

```bash
git add agent/services/job_manager/send_qbo_api.py agent/services/qbo_api/attachments.py agent/services/qbo_api/dedup.py
git commit -m "docs(cascade): mark TMS→QBO cascade + classifier as DISABLED with revival notes"
```

---

## Task 9: Frontend "Needs retry" group + plain-English status copy

**Files:**
- Modify: `app/assets/js/tools/invoice-sender/invoice-sender-results.js`

- [ ] **Step 1: Locate the status-rendering switch**

```bash
grep -n "case 'sent'\|case 'error'\|case 'missing_docs'\|statusBadge\|renderResultRow" "C:/Users/Joseph/Desktop/NGL ACCOUNTING SERVICE/app/assets/js/tools/invoice-sender/invoice-sender-results.js" | head -20
```

Note the line numbers — Step 2 needs them.

- [ ] **Step 2: Add new status pill renderers**

Find the switch that maps status → badge label/color (around the lines from Step 1) and add cases for the new statuses, e.g.:

```javascript
    case 'tms_unreachable':
      return { label: 'TMS unreachable — retry when connection returns', cls: 'badge-warn' };
    case 'pod_missing':
      return { label: 'POD not yet on TMS — retry after checkpoint clears', cls: 'badge-warn' };
```

(Use whatever existing badge class matches "yellow/warning" in the project's CSS. If unsure, grep `app/assets/css/styles.css` for `badge-warn` to confirm it exists; if not, use the same class already used by `missing_docs`.)

- [ ] **Step 3: Add a "Needs retry" group in the results UI**

Find where result rows are grouped (typically a section per status). Add a new group above the "Failed" / "Error" section for rows whose status is `tms_unreachable` or `pod_missing`. Each row in that group renders a one-click "Retry" button that POSTs to `/jobs/{job_id}/retry-row` (or whatever endpoint the existing failed-rows retry uses — grep `retry-row\|retryRow\|retry_row` to find it).

Example markup pattern (adapt to match existing UI conventions):

```javascript
function renderNeedsRetryGroup(rows, jobId) {
  if (!rows.length) return '';
  return `
    <details open class="results-group results-group-warn">
      <summary>Needs retry (${rows.length})</summary>
      ${rows.map(r => `
        <div class="result-row" data-status="${r.status}">
          <div class="result-row-main">
            <strong>${escHtml(r.invoiceNumber)}</strong> · ${escHtml(r.containerNumber)}
            <span class="badge badge-warn">${escHtml(statusLabel(r.status))}</span>
          </div>
          <div class="result-row-detail">${escHtml(r.error || '')}</div>
          <button class="btn btn-sm" onclick="retryInvoice('${jobId}', '${r.invoiceNumber}')">Retry</button>
        </div>
      `).join('')}
    </details>
  `;
}
```

(Hook this into the existing renderResults / renderJob function so the group appears alongside Sent / Skipped / Errored.)

- [ ] **Step 4: Wire the Retry button**

If `retryInvoice(jobId, invoiceNumber)` doesn't already exist, add it. It should POST to the existing single-row retry endpoint. To find the endpoint:

```bash
grep -rn "retry.*single\|single.*retry\|/jobs/.*/retry" "C:/Users/Joseph/Desktop/NGL ACCOUNTING SERVICE/app/assets/js/" --include="*.js" | head -10
grep -rn "@router\.post.*retry" "C:/Users/Joseph/Desktop/NGL ACCOUNTING SERVICE/agent/routers/" | head -10
```

Reuse the existing pattern. If there isn't one yet (the project may only have a batch resend today), the simplest path is to add a thin wrapper that resends just one invoice via the existing send job, scoped to a single-element invoice list.

- [ ] **Step 5: Run the JS syntax gate**

```bash
node desktop/check-js.js
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/assets/js/tools/invoice-sender/invoice-sender-results.js
git commit -m "feat(invoice-sender): Needs retry group for tms_unreachable and pod_missing"
```

---

## Task 10: Full test suite + manual integration verification

**Files:**
- None modified — verification only.

- [ ] **Step 1: Run the full Python test suite**

```bash
cd "C:/Users/Joseph/Desktop/NGL ACCOUNTING SERVICE/agent"
python -m pytest tests/ -v --timeout=60
```

Expected: All tests pass (or the same pre-existing failures as before this branch — note any deltas).

- [ ] **Step 2: Run the JS syntax gate**

```bash
cd "C:/Users/Joseph/Desktop/NGL ACCOUNTING SERVICE"
node desktop/check-js.js
```

Expected: PASS.

- [ ] **Step 3: Restart the agent and reload the app**

Close the running NGL Accounting Electron window, then re-launch it (or run `agent/main.py` directly if testing the source tree before packaging).

Wait for the agent panel to show "Connected" and QBO/TMS both green.

- [ ] **Step 4: Re-send PROMAR01 `LM26060242F` to a test address**

In the Invoice Sender:
1. Build a one-row CSV with `LM26060242F, TEMU7671557, PROMAR01` (or whatever the project's send CSV format is).
2. Replace PROMAR01's email temporarily (in Customer Manager) with your own test address — or use the test-mode approval gate if it lets you redirect.
3. Run the send.

Expected timeline in the live results UI:
- `searching_invoice` → `verifying_invoice` → `tms_fetching_docs` → `filling_send_form` → `downloading_attachments` → `sending_invoice` → `invoice_sent` with `attachmentCount: 5`.

- [ ] **Step 5: Verify the email contents**

Open the test inbox. The email should contain 5 attachments:
- `LM26060242F.pdf` (invoice PDF from QBO)
- `LM2605280007_pod.pdf`
- `LM2605280007_do.pdf`
- `LM2605280007_it.pdf`
- `LM2605280007_ite.pdf`

If POD is missing, the fix didn't land — STOP and review.

- [ ] **Step 6: Verify QBO is untouched**

```bash
python -c "
import httpx, json, base64
t = json.loads(open(r'C:/Users/Joseph/AppData/Local/NGL Accounting/.qbo_tokens.json').read())
cid = 'ABUW1eHFKjPYvPDYxTu13nonqVSPR7HRE3yTjAbK0lBZmvNNsS'
csec = 'ekVbSYoo3yW1OSbBaPdM069AHTGzpCbRRYFKB0uA'
auth = base64.b64encode(f'{cid}:{csec}'.encode()).decode()
new = httpx.post('https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer',
    headers={'Authorization': f'Basic {auth}', 'Content-Type': 'application/x-www-form-urlencoded'},
    data={'grant_type': 'refresh_token', 'refresh_token': t['refresh_token']}, timeout=30.0).json()
access = new['access_token']
realm = t['realm_id']
q = \"SELECT * FROM Attachable WHERE AttachableRef.EntityRef.value = '400638'\"
r = httpx.get(f'https://quickbooks.api.intuit.com/v3/company/{realm}/query',
    headers={'Authorization': f'Bearer {access}', 'Accept': 'application/json'},
    params={'query': q, 'minorversion': '65'}, timeout=30.0)
atts = r.json().get('QueryResponse', {}).get('Attachable', [])
print('attachment count:', len(atts))
for a in atts:
    print(' ', a.get('FileName'), '·', a.get('MetaData', {}).get('CreateTime'))
"
```

Expected: still exactly 2 attachments (the original DO from 6/5 and ITE from 6/8). NO new Attachable created today.

- [ ] **Step 7: Verify the audit row**

```bash
python -c "
import httpx
url = 'https://xkiunwaobjhpjhtzpvvs.supabase.co'
key = open(r'C:/Users/Joseph/Desktop/NGL ACCOUNTING SERVICE/agent/.env').read().split('SUPABASE_SERVICE_KEY=')[1].split()[0]
r = httpx.get(f'{url}/rest/v1/audit_log?invoice_number=eq.LM26060242F&order=timestamp.desc&limit=1',
    headers={'apikey': key, 'Authorization': f'Bearer {key}'}, timeout=10.0)
row = r.json()[0]
print('status:', row['status'])
print('attachments_emailed:', row.get('attachments_emailed'))
print('attachments_missing:', row.get('attachments_missing'))
"
```

Expected: `status: sent`, `attachments_emailed: ['do', 'it', 'ite', 'pod']` (some order), `attachments_missing: []`.

- [ ] **Step 8: Restore PROMAR01's real email address**

If you swapped the customer record's email for Step 4, switch it back now. Do NOT leave a real customer pointed at your test inbox.

---

## Task 11: Ship (deferred — wait for explicit "build now")

**This task is intentionally not auto-run.** Per the user's standing rule (`feedback_no_autobuild_during_testing`): commit fixes only during a test cycle; wait for explicit "build now" before kicking off runbuild + push + release.

When the user says "build now":

- [ ] **Step 1: Bump version**

Open `desktop/VERSION` and bump the patch number (current is 2.79.1 → 2.79.2 or 2.80.0 depending on the convention the user prefers for this change size). This is a behavior-changing feature so 2.80.0 is the right call.

- [ ] **Step 2: Run the build pipeline**

```bash
cd "C:/Users/Joseph/Desktop/NGL ACCOUNTING SERVICE"
desktop/runbuild.bat
```

(`runbuild.bat` is the non-interactive sibling of `build-all.bat` — see `reference_build_js_check.md` for the rationale. It runs `check-js.js`, builds the agent, builds the installer.)

- [ ] **Step 3: Push and release**

```bash
git push origin main
gh release create v2.80.0 desktop/dist/*.exe desktop/dist/latest.yml --title "v2.80.0 — TMS-direct email" --notes "Skip QBO upload of TMS supporting docs. Pull POD/DO/BL/IT/ITE directly from TMS at email time. Fixes silent cascade failure that dropped a POD on PROMAR01 LM26060242F on 2026-06-08. See docs/superpowers/specs/2026-06-10-tms-direct-email-design.md."
```

- [ ] **Step 4: Verify the auto-updater picks it up**

Restart the user's installed Electron app. Watch for the "Update available" toast and "Restart to apply" prompt. Confirm the post-update version reads 2.80.0.
