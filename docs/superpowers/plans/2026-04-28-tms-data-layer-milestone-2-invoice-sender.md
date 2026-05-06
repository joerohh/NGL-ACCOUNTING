# TMS Data Layer Milestone 2 — Invoice Sender Migration

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate Invoice Sender (`send_qbo_api.py` + `send_oec.py`) from direct TMS browser calls to `TMSDataLayer.get_documents` / `enrich_invoice`, ship the Failed Rows box UI, and cut a GitHub release. First user-visible change of the data layer rebuild.

**Architecture:** `TMSDataLayer` becomes the single TMS gateway for the send flow. QBO-first cascade unchanged for non-OEC invoices; OEC opts into `force=True` enrichment to guarantee `do_sender_email` is fetched. Failed Rows box (new) lets the user retry per-row with explicit Source choice. TMS browser remains opt-in only — never auto-invoked.

**Tech Stack:** Python 3.13 (FastAPI / Playwright / asyncio / pytest), vanilla JS + HTML/CSS in `app/`, Electron desktop wrapper, GitHub Releases for auto-update.

---

## Hard Invariants (must verify in Phase D)

These are the 12 hard invariants from the spec ([2026-04-28-tms-data-layer-design.md](../specs/2026-04-28-tms-data-layer-design.md) §"Hard Invariants the Redesign MUST Preserve"). Phase D rewrites the OEC and QBO send paths — every one of these must still hold afterwards.

1. **Two emails per OEC invoice, in order:** POD email FIRST, invoice email SECOND.
2. **POD email** — TO: `customer.podEmailTo`; CC: `customer.podEmailCc` + D/O sender email; attachment: POD PDF only.
3. **Invoice email** — TO: `customer.emails`; CC: `ar@ngltrans.net` + `customer.ccEmails` + D/O sender email; attachment: invoice PDF only (hard-guarded).
4. **D/O sender CC'd on BOTH emails.**
5. **`result.pod_status` tracked separately from `result.status`.** Final status reconciles to `sent` vs. `sent_no_pod`.
6. **Invoice email goes out even if POD email failed.**
7. **`ar@ngltrans.net` always CC'd** on invoice email.
8. **Customer-required-docs gate** (`att_check.allPresent`) preserved.
9. **Test mode approval gate** still fires per row.
10. **QBO is the source of truth** for invoice data — never overridden by TMS.
11. **D/O sender cache** ([send_oec.py:124-146](../../../agent/services/job_manager/send_oec.py#L124-L146)) preserved as final fallback.
12. **Audit log** schema unchanged.

---

## Phase A — Data layer fixes & enhancements

Eight backend tasks. Pure backend work, no UI, no migration. Locks in the API surface before the Invoice Sender rewrite.

### Task 1: Consolidate `TMSApiClient` into `app.state.tms_api`

Three independent `TMSApiClient` instances exist today (each with its own token cache) — see [main.py:60](../../../agent/main.py#L60), [routers/tms.py:18](../../../agent/routers/tms.py#L18), [services/job_manager/__init__.py:303](../../../agent/services/job_manager/__init__.py#L303). Consolidate via the existing setter pattern (matches `set_qbo_api`, `set_tms_browser`, etc.).

**Files:**
- Modify: `agent/services/job_manager/__init__.py:294-305` (constructor + add `set_tms_api`)
- Modify: `agent/routers/tms.py:18` (replace module-level instantiation with setter)
- Modify: `agent/main.py:292-303` (add `tms.set_tms_api(tms_api)` and `job_manager.set_tms_api(tms_api)`)
- Test: `agent/tests/test_tms_data/test_tms_api_singleton.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# agent/tests/test_tms_data/test_tms_api_singleton.py
"""Verify there is one TMSApiClient instance shared across main, routers, and JobManager."""

from unittest.mock import MagicMock

from routers import tms as tms_router
from services.job_manager import JobManager
from services.qbo_api.client import QBOApiClient


def test_set_tms_api_propagates_to_router():
    fake = MagicMock(name="tms_api_singleton")
    tms_router.set_tms_api(fake)
    assert tms_router._tms_api is fake


def test_set_tms_api_propagates_to_job_manager():
    fake = MagicMock(name="tms_api_singleton")
    jm = JobManager(QBOApiClient(), classifier=MagicMock())
    jm.set_tms_api(fake)
    assert jm._tms_api is fake
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && python -m pytest tests/test_tms_data/test_tms_api_singleton.py -v`
Expected: FAIL — `set_tms_api` doesn't exist on either target.

- [ ] **Step 3: Add the setters and remove duplicate instantiations**

In `agent/routers/tms.py`, replace line 18 with:

```python
_tms_api = None  # Set by main.py via set_tms_api(tms_api) at startup.


def set_tms_api(client) -> None:
    """Inject the shared TMSApiClient instance (called from main.py lifespan)."""
    global _tms_api
    _tms_api = client
```

In `agent/services/job_manager/__init__.py`, change the constructor and add the setter:

```python
def __init__(self, qbo_api: QBOApiClient, classifier: ClaudeClassifier,
             email_sender: Optional["EmailSender"] = None,
             portal_uploader: Optional["PortalUploader"] = None,
             tms_browser: Optional["TMSBrowser"] = None) -> None:
    self._qbo_api = qbo_api
    self._classifier = classifier
    self._email_sender = email_sender
    self._portal_uploader = portal_uploader
    self._tms = tms_browser
    self._tms_api = None  # Set via set_tms_api() at startup.
    self._tms_data = None  # Set via set_tms_data() at startup (Task 11).
    self._jobs: dict[str, Job] = {}

def set_tms_api(self, client) -> None:
    """Inject the shared TMSApiClient instance (called from main.py lifespan)."""
    self._tms_api = client
```

Drop the `from services.tms_api import TMSApiClient` import if unused after this change.

In `agent/main.py`, after line 303 (`chassis.set_job_manager(job_manager)`), add:

```python
tms.set_tms_api(tms_api)
job_manager.set_tms_api(tms_api)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent && python -m pytest tests/test_tms_data/test_tms_api_singleton.py -v`
Expected: PASS — both setters wire the injected instance.

- [ ] **Step 5: Run the existing TMS data layer test suite to confirm nothing else broke**

Run: `cd agent && python -m pytest tests/test_tms_data/ -v`
Expected: 65 existing tests + 2 new tests all PASS.

- [ ] **Step 6: Commit**

```bash
git add agent/main.py agent/routers/tms.py agent/services/job_manager/__init__.py agent/tests/test_tms_data/test_tms_api_singleton.py
git commit -m "refactor(tms_data): consolidate TMSApiClient via app.state singleton"
```

---

### Task 2: Add `force=True` flag to `enrich_invoice`

OEC needs `do_sender_email` even when QBO has chassis+CNEE. Add a `force` kwarg that bypasses the cascade's QBO-complete short-circuit. Default `force=False` preserves the optimization for non-OEC callers.

**Files:**
- Modify: `agent/services/tms_data/cascade.py:26-58` (add `force` param to `run_enrich`)
- Modify: `agent/services/tms_data/__init__.py:39-73` (forward `force` to cascade)
- Test: `agent/tests/test_tms_data/test_cascade.py` (add 2 tests)
- Test: `agent/tests/test_tms_data/test_data_layer.py` (add 1 test)

- [ ] **Step 1: Write the failing tests**

Append to `agent/tests/test_tms_data/test_cascade.py`:

```python
@pytest.mark.asyncio
async def test_run_enrich_force_calls_tms_even_when_qbo_complete():
    """force=True bypasses the QBO-complete short-circuit so do_sender is fetched."""
    invoice_data = {
        "DocNumber": "INV-001",
        "CustomField": [{"Name": "NGL REF#", "StringValue": "WO12345/CUST"}],
        # Pretend QBO has both chassis and CNEE — without force, TMS wouldn't be called.
        "CustomerMemo": {"value": "→ ROUTE → ABC TERMINAL"},
    }
    # Force chassis to be present in QBO via a custom field the helper recognizes.
    invoice_data["CustomField"].append(
        {"Name": "Container/Chassis", "StringValue": "TGBU6571759/CHX9999"}
    )

    mock_tms_api = AsyncMock()
    mock_tms_api.get_work_order.return_value = {
        "container_no": "TGBU6571759",
        "do_sender": ["sender@example.com"],
    }

    enriched, err = await run_enrich(invoice_data, mock_tms_api, force=True)

    assert err is None
    assert mock_tms_api.get_work_order.await_count == 1, "force=True must hit TMS API"
    assert enriched.do_sender_email == "sender@example.com"
    assert enriched.sources["do_sender_email"] == "tms_api"


@pytest.mark.asyncio
async def test_run_enrich_default_skips_tms_when_qbo_complete():
    """Without force, QBO chassis+CNEE means no TMS call (optimization preserved)."""
    invoice_data = {
        "DocNumber": "INV-002",
        "CustomField": [
            {"Name": "NGL REF#", "StringValue": "WO12345/CUST"},
            {"Name": "Container/Chassis", "StringValue": "TGBU6571759/CHX9999"},
        ],
        "CustomerMemo": {"value": "→ ROUTE → ABC TERMINAL"},
    }
    mock_tms_api = AsyncMock()

    enriched, err = await run_enrich(invoice_data, mock_tms_api)

    assert err is None
    assert mock_tms_api.get_work_order.await_count == 0, "default path must skip TMS"
    assert enriched.do_sender_email is None
```

Append to `agent/tests/test_tms_data/test_data_layer.py`:

```python
@pytest.mark.asyncio
async def test_enrich_invoice_force_kwarg_forwarded():
    """TMSDataLayer.enrich_invoice forwards force=True to run_enrich."""
    qbo, tms_api, browser = MagicMock(), AsyncMock(), AsyncMock()
    tms_api.get_work_order.return_value = {"do_sender": ["x@y.com"]}
    layer = TMSDataLayer(qbo, tms_api, browser)

    invoice_data = {
        "DocNumber": "INV-100",
        "CustomField": [
            {"Name": "NGL REF#", "StringValue": "WO9/X"},
            {"Name": "Container/Chassis", "StringValue": "TGBU6571759/CHX1"},
        ],
        "CustomerMemo": {"value": "→ ROUTE → CNEE"},
    }
    enriched = await layer.enrich_invoice("job-1", invoice_data, force=True)
    assert tms_api.get_work_order.await_count == 1
    assert enriched.do_sender_email == "x@y.com"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent && python -m pytest tests/test_tms_data/test_cascade.py::test_run_enrich_force_calls_tms_even_when_qbo_complete tests/test_tms_data/test_data_layer.py::test_enrich_invoice_force_kwarg_forwarded -v`
Expected: FAIL — `run_enrich` rejects `force` kwarg; layer doesn't forward it.

- [ ] **Step 3: Add the `force` parameter to `run_enrich`**

In `agent/services/tms_data/cascade.py`, change the signature and predicate:

```python
async def run_enrich(
    invoice_data: dict,
    tms_api,
    force: bool = False,
) -> Tuple[EnrichedInvoice, Optional[str]]:
    """Build an EnrichedInvoice from QBO data, filling blanks via TMS API.

    When force=True, TMS is queried even if QBO already has chassis+CNEE.
    Used by the OEC flow to guarantee do_sender_email is populated, since
    that field lives only in TMS but the QBO-complete short-circuit would
    otherwise skip the call.
    """
    # ... [Step 1 unchanged] ...

    # Step 2: short-circuit if QBO already has chassis+CNEE AND caller didn't force.
    # do_sender_email is TMS-only, so non-force callers accept it stays None.
    # OEC callers pass force=True to guarantee do_sender_email is fetched.
    needs_tms = bool(wo_no) and (force or not chassis or not cnee)
    if not needs_tms:
        return EnrichedInvoice(...)
    # ... [rest unchanged] ...
```

- [ ] **Step 4: Forward `force` from the data layer**

In `agent/services/tms_data/__init__.py`, change `enrich_invoice`:

```python
async def enrich_invoice(
    self,
    job_id: str,
    invoice_data: dict,
    source: Source = "api",
    force: bool = False,
) -> EnrichedInvoice:
    """Fill in missing chassis / CNEE / D/O sender from TMS.

    force=True bypasses the QBO-complete short-circuit (used by OEC for do_sender_email).
    """
    if source == "browser":
        enriched, err = await run_enrich_browser(invoice_data, self._tms_browser)
        failed_at = "tms_browser"
    else:
        enriched, err = await run_enrich(invoice_data, self._tms_api, force=force)
        failed_at = "tms_api"
    # ... [rest unchanged] ...
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd agent && python -m pytest tests/test_tms_data/ -v`
Expected: All tests PASS (65 original + 3 new = 68).

- [ ] **Step 6: Commit**

```bash
git add agent/services/tms_data/cascade.py agent/services/tms_data/__init__.py agent/tests/test_tms_data/test_cascade.py agent/tests/test_tms_data/test_data_layer.py
git commit -m "feat(tms_data): force=True kwarg on enrich_invoice for OEC do_sender"
```

---

### Task 3: Per-job WO cache inside `TMSDataLayer`

OEC calls `enrich_invoice` (D/O sender) and `get_document("POD")` for the same WO# — both internally call `tms_api.get_work_order(wo_no)`. Add a per-`(job_id, wo_no)` in-memory cache. Cleared by `reset_for_new_job`.

**Files:**
- Modify: `agent/services/tms_data/__init__.py` (add `_wo_cache` + helper, hook into both ops, clear in `reset_for_new_job`)
- Modify: `agent/services/tms_data/cascade.py` (accept `wo_getter` callable injected by data layer; falls back to `tms_api.get_work_order`)
- Test: `agent/tests/test_tms_data/test_wo_cache.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# agent/tests/test_tms_data/test_wo_cache.py
"""Verify the per-job WO cache prevents redundant TMS API calls within a job."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.tms_data import TMSDataLayer


@pytest.fixture
def invoice_data():
    return {
        "DocNumber": "INV-WO-CACHE",
        "Id": "1",
        "CustomField": [{"Name": "NGL REF#", "StringValue": "WO77/CUST"}],
    }


@pytest.mark.asyncio
async def test_enrich_then_get_document_uses_cached_wo(tmp_path, invoice_data):
    qbo, tms_api, browser = MagicMock(), AsyncMock(), AsyncMock()
    tms_api.get_work_order.return_value = {
        "container_no": "TGBU0000001",
        "do_sender": ["s@x.com"],
        "documents": [{"type_": "POD", "file_url": "https://example/pod.pdf"}],
    }
    tms_api.download_document.return_value = b"%PDF-fake"

    layer = TMSDataLayer(qbo, tms_api, browser)

    await layer.enrich_invoice("job-cache", invoice_data, force=True)
    await layer.get_document("job-cache", invoice_data, "POD", tmp_path)

    assert tms_api.get_work_order.await_count == 1, \
        "Second call to same WO# in same job must reuse cached fetch"


@pytest.mark.asyncio
async def test_reset_for_new_job_clears_wo_cache(tmp_path, invoice_data):
    qbo, tms_api, browser = MagicMock(), AsyncMock(), AsyncMock()
    tms_api.get_work_order.return_value = {"container_no": "X"}

    layer = TMSDataLayer(qbo, tms_api, browser)

    await layer.enrich_invoice("job-A", invoice_data, force=True)
    layer.reset_for_new_job("job-A")
    await layer.enrich_invoice("job-A", invoice_data, force=True)

    assert tms_api.get_work_order.await_count == 2


@pytest.mark.asyncio
async def test_different_jobs_do_not_share_cache(tmp_path, invoice_data):
    qbo, tms_api, browser = MagicMock(), AsyncMock(), AsyncMock()
    tms_api.get_work_order.return_value = {"container_no": "X"}
    layer = TMSDataLayer(qbo, tms_api, browser)

    await layer.enrich_invoice("job-A", invoice_data, force=True)
    await layer.enrich_invoice("job-B", invoice_data, force=True)

    assert tms_api.get_work_order.await_count == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && python -m pytest tests/test_tms_data/test_wo_cache.py -v`
Expected: FAIL — first test fails because `get_work_order` is called twice.

- [ ] **Step 3: Add a cached `get_work_order` wrapper in the data layer**

In `agent/services/tms_data/__init__.py`, add a cache dict to `__init__` and a helper:

```python
def __init__(self, qbo_api, tms_api, tms_browser) -> None:
    self._qbo_api = qbo_api
    self._tms_api = tms_api
    self._tms_browser = tms_browser
    self._failed = FailedRowsTracker()
    self._retry_ctx: dict[str, dict] = {}
    # Per-job WO cache: (job_id, wo_no) -> wo_record. Avoids redundant TMS API
    # calls when the same job needs both enrich and document-fetch on one WO.
    self._wo_cache: dict[tuple[str, str], dict] = {}

class _CachedTmsApi:
    """Adapter that proxies tms_api but memoizes get_work_order per (job_id, wo_no)."""

    def __init__(self, real_api, cache: dict, job_id: str) -> None:
        self._real = real_api
        self._cache = cache
        self._job_id = job_id

    async def get_work_order(self, wo_no):
        key = (self._job_id, wo_no)
        if key in self._cache:
            return self._cache[key]
        wo = await self._real.get_work_order(wo_no)
        if wo is not None:
            self._cache[key] = wo
        return wo

    async def download_document(self, url):
        return await self._real.download_document(url)

    def is_configured(self):
        return self._real.is_configured()
```

Use the adapter in `enrich_invoice` and `get_document`:

```python
async def enrich_invoice(self, job_id, invoice_data, source="api", force=False):
    if source == "browser":
        enriched, err = await run_enrich_browser(invoice_data, self._tms_browser)
        failed_at = "tms_browser"
    else:
        cached = self._CachedTmsApi(self._tms_api, self._wo_cache, job_id)
        enriched, err = await run_enrich(invoice_data, cached, force=force)
        failed_at = "tms_api"
    # ... rest unchanged ...
```

```python
async def get_document(self, job_id, invoice_data, doc_type, dest_dir, source="api"):
    if source == "browser":
        # ... unchanged ...
    else:
        cached = self._CachedTmsApi(self._tms_api, self._wo_cache, job_id)
        path, err = await run_document(invoice_data, doc_type, dest_dir, cached)
        failed_at = "tms_api"
    # ... rest unchanged ...
```

Update `reset_for_new_job` to clear the cache:

```python
def reset_for_new_job(self, job_id: str) -> None:
    rows = self._failed.get_rows(job_id)
    for r in rows:
        self._retry_ctx.pop(r.row_id, None)
    self._failed.reset(job_id)
    # Clear cached WO records for this job.
    for key in list(self._wo_cache.keys()):
        if key[0] == job_id:
            del self._wo_cache[key]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && python -m pytest tests/test_tms_data/test_wo_cache.py tests/test_tms_data/ -v`
Expected: All tests PASS (3 new + 68 existing = 71).

- [ ] **Step 5: Commit**

```bash
git add agent/services/tms_data/__init__.py agent/tests/test_tms_data/test_wo_cache.py
git commit -m "feat(tms_data): per-job WO cache to halve OEC TMS API calls"
```

---

### Task 4: Stub `tms_browser.fetch_detail_info` with `NotImplementedError`

[browser_path.py:50](../../../agent/services/tms_data/browser_path.py#L50) calls `tms_browser.fetch_detail_info(wo_no)` — this method doesn't exist. Tests pass only because `AsyncMock` auto-creates attributes. Add a stub on the real class so production "Retry (Browser)" on enrich raises a clear error instead of an `AttributeError`. The UI (Task 17) will hide the "Retry (Browser)" button for `operation == "enrich_invoice"` failures.

**Files:**
- Modify: `agent/services/tms_browser/__init__.py` (add stub method)
- Test: `agent/tests/test_tms_data/test_browser_path.py` (add 1 test)

- [ ] **Step 1: Locate the TMSBrowser class file and identify where mixins live**

Run: `cd agent && python -c "from services.tms_browser import TMSBrowser; import inspect; print(inspect.getfile(TMSBrowser))"`

Note the path output — that's the file to edit. (Per memory, it's the package's `__init__.py`.)

- [ ] **Step 2: Write the failing test**

In `agent/tests/test_tms_data/test_browser_path.py`, append:

```python
@pytest.mark.asyncio
async def test_run_enrich_browser_propagates_not_implemented_error(monkeypatch):
    """fetch_detail_info is stubbed — error must surface as a recordable failure."""
    from services.tms_browser import TMSBrowser

    real_browser = TMSBrowser()
    invoice_data = {
        "DocNumber": "INV-X",
        "CustomField": [{"Name": "NGL REF#", "StringValue": "WO11/CUST"}],
    }

    enriched, err = await run_enrich_browser(invoice_data, real_browser)

    assert err is not None
    assert "not implemented" in err.lower()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd agent && python -m pytest tests/test_tms_data/test_browser_path.py::test_run_enrich_browser_propagates_not_implemented_error -v`
Expected: FAIL — currently `AttributeError: TMSBrowser has no fetch_detail_info` (raw, untranslated).

- [ ] **Step 4: Add the stub to TMSBrowser**

In the TMSBrowser class file, add the method (place near other public fetch methods):

```python
async def fetch_detail_info(self, wo_no: str) -> dict:
    """Scrape the Detail Info tab of a WO via Playwright. NOT YET IMPLEMENTED.

    Used only by TMSDataLayer's "Retry (Browser)" path on enrich_invoice failures.
    Until implemented, the UI hides the Retry (Browser) button for enrich
    failures (see invoice-sender.js failed-rows renderer).
    """
    raise NotImplementedError(
        "TMSBrowser.fetch_detail_info is not implemented yet — "
        "use the TMS API path or implement Detail Info tab scraping."
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd agent && python -m pytest tests/test_tms_data/test_browser_path.py -v`
Expected: All browser_path tests PASS.

- [ ] **Step 6: Commit**

```bash
git add agent/services/tms_browser/__init__.py agent/tests/test_tms_data/test_browser_path.py
git commit -m "feat(tms_browser): stub fetch_detail_info with NotImplementedError"
```

---

### Task 5: Bugfix — `record_failure` race + retry success-detection

Two bugs in [__init__.py:67](../../../agent/services/tms_data/__init__.py#L67) and [retry_failed_row](../../../agent/services/tms_data/__init__.py#L156-L162):

1. After `record_failure`, code reads `self._failed.get_rows(job_id)[-1].row_id` — racy and wasteful. `record_failure` already returns `row_id`.
2. `retry_failed_row` for `enrich_invoice` rescans the tracker by `invoice_number`. Two invoices sharing an empty `DocNumber` cross-contaminate.

Fix both: use `record_failure`'s return value directly; track success by comparing tracker length before vs. after.

**Files:**
- Modify: `agent/services/tms_data/__init__.py:39-113, 156-162`
- Test: `agent/tests/test_tms_data/test_data_layer.py` (add 2 tests)

- [ ] **Step 1: Write the failing test for retry cross-contamination**

```python
# Append to agent/tests/test_tms_data/test_data_layer.py

@pytest.mark.asyncio
async def test_retry_enrich_does_not_cross_contaminate_on_empty_docnumber():
    """Two invoices with empty DocNumber must not collide on retry."""
    qbo, tms_api, browser = MagicMock(), AsyncMock(), AsyncMock()
    tms_api.get_work_order.side_effect = RuntimeError("network down")

    layer = TMSDataLayer(qbo, tms_api, browser)
    inv_a = {"DocNumber": "", "Id": "10", "CustomField": [{"Name": "NGL REF#", "StringValue": "WO-A/X"}]}
    inv_b = {"DocNumber": "", "Id": "11", "CustomField": [{"Name": "NGL REF#", "StringValue": "WO-B/Y"}]}

    await layer.enrich_invoice("j", inv_a, force=True)
    await layer.enrich_invoice("j", inv_b, force=True)

    rows = layer.get_failed_rows("j")
    assert len(rows) == 2
    row_a = rows[0]

    # Retry only row_a; row_b's failure must remain.
    tms_api.get_work_order.side_effect = None
    tms_api.get_work_order.return_value = {"container_no": "X"}
    ok = await layer.retry_failed_row("j", row_a.row_id, source="api")
    assert ok is True

    remaining = layer.get_failed_rows("j")
    assert len(remaining) == 1, "row_b's failure must not have been removed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && python -m pytest tests/test_tms_data/test_data_layer.py::test_retry_enrich_does_not_cross_contaminate_on_empty_docnumber -v`
Expected: FAIL — current `retry_failed_row` removes both rows because both share `invoice_number=""`.

- [ ] **Step 3: Fix `record_failure` use site (capture return value)**

In `agent/services/tms_data/__init__.py`, replace the `enrich_invoice` failure-recording block:

```python
if err:
    row_id = self._failed.record_failure(
        job_id=job_id,
        invoice_number=str(invoice_data.get("DocNumber") or ""),
        container_number=enriched.container_no,
        operation="enrich_invoice",
        doc_type=None,
        error_message=err,
        source=failed_at,
    )
    self._retry_ctx[row_id] = {
        "operation": "enrich_invoice",
        "invoice_data": invoice_data,
        "force": force,  # remember for retry
    }
```

Same change for `get_document`:

```python
if err:
    row_id = self._failed.record_failure(
        job_id=job_id,
        invoice_number=str(invoice_data.get("DocNumber") or ""),
        container_number=None,
        operation="get_document",
        doc_type=doc_type,
        error_message=err,
        source=failed_at,
    )
    self._retry_ctx[row_id] = {
        "operation": "get_document",
        "invoice_data": invoice_data,
        "doc_type": doc_type,
        "dest_dir": dest_dir,
    }
```

- [ ] **Step 4: Fix `retry_failed_row` success detection**

Replace the body with row-id-based length comparison:

```python
async def retry_failed_row(self, job_id, row_id, source):
    row = self._failed.find_row(job_id, row_id)
    ctx = self._retry_ctx.get(row_id)
    if row is None or ctx is None:
        return False

    self._failed.remove_row(job_id, row_id)
    self._retry_ctx.pop(row_id, None)

    rows_before = len(self._failed.get_rows(job_id))

    if ctx["operation"] == "enrich_invoice":
        await self.enrich_invoice(
            job_id, ctx["invoice_data"],
            source=source, force=ctx.get("force", False),
        )
    elif ctx["operation"] == "get_document":
        await self.get_document(
            job_id, ctx["invoice_data"], ctx["doc_type"],
            ctx["dest_dir"], source=source,
        )
    else:
        return False

    rows_after = len(self._failed.get_rows(job_id))
    # Success = the just-run op did not record a new failure row for this job.
    return rows_after == rows_before
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd agent && python -m pytest tests/test_tms_data/ -v`
Expected: All tests PASS (existing 71 + 1 new = 72).

- [ ] **Step 6: Commit**

```bash
git add agent/services/tms_data/__init__.py agent/tests/test_tms_data/test_data_layer.py
git commit -m "fix(tms_data): retry success uses row-id length diff (no DocNumber collisions)"
```

---

### Task 6: Bugfix — empty `DocNumber` falls back to `Id`

QBO invoices occasionally have empty `DocNumber`. The Failed Rows box currently shows blank invoice numbers, which the user can't act on. Fall back to `Id` so every row shows *something* identifying.

**Files:**
- Modify: `agent/services/tms_data/__init__.py` (replace 2 occurrences of `str(invoice_data.get("DocNumber") or "")`)
- Test: `agent/tests/test_tms_data/test_data_layer.py` (add 1 test)

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_failed_row_uses_id_when_docnumber_empty():
    """Empty DocNumber should fall back to QBO Id so the UI shows something."""
    qbo, tms_api, browser = MagicMock(), AsyncMock(), AsyncMock()
    tms_api.get_work_order.side_effect = RuntimeError("boom")

    layer = TMSDataLayer(qbo, tms_api, browser)
    invoice_data = {
        "DocNumber": "",
        "Id": "QBO-42",
        "CustomField": [{"Name": "NGL REF#", "StringValue": "WO/X"}],
    }

    await layer.enrich_invoice("j", invoice_data, force=True)
    rows = layer.get_failed_rows("j")
    assert rows[0].invoice_number == "QBO-42"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && python -m pytest tests/test_tms_data/test_data_layer.py::test_failed_row_uses_id_when_docnumber_empty -v`
Expected: FAIL — currently records `invoice_number=""`.

- [ ] **Step 3: Add a helper and replace use sites**

Add at the top of `agent/services/tms_data/__init__.py` (under the `Source` alias):

```python
def _invoice_label(invoice_data: dict) -> str:
    """Return DocNumber, falling back to Id, falling back to '<unknown>'."""
    doc = invoice_data.get("DocNumber")
    if isinstance(doc, str) and doc.strip():
        return doc.strip()
    inv_id = invoice_data.get("Id")
    if inv_id:
        return str(inv_id)
    return "<unknown>"
```

Replace both `str(invoice_data.get("DocNumber") or "")` occurrences with `_invoice_label(invoice_data)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent && python -m pytest tests/test_tms_data/ -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/services/tms_data/__init__.py agent/tests/test_tms_data/test_data_layer.py
git commit -m "fix(tms_data): fall back to Id when DocNumber is empty in failed rows"
```

---

### Task 7: Bugfix — `get_document` retry must not collapse "no doc on WO" with success

Today, `retry_failed_row` for `get_document` returns `path is not None`. If the WO truly has no POD attached, `run_document` returns `(None, None)` (not an error) and the retry returns False, but the row was already removed → user thinks Retry succeeded but the doc is still missing. Fix: re-record a row with a clear "doc not present" message in this specific case.

**Files:**
- Modify: `agent/services/tms_data/__init__.py` (`retry_failed_row` get_document branch)
- Test: `agent/tests/test_tms_data/test_data_layer.py` (add 1 test)

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_retry_get_document_records_when_doc_absent_on_wo(tmp_path):
    """If the WO exists but has no doc of the requested type, record it as still-failed."""
    qbo, tms_api, browser = MagicMock(), AsyncMock(), AsyncMock()
    # First call: error (creates failed row)
    tms_api.get_work_order.side_effect = RuntimeError("fail")
    layer = TMSDataLayer(qbo, tms_api, browser)
    invoice_data = {
        "DocNumber": "INV-Z",
        "Id": "9",
        "CustomField": [{"Name": "NGL REF#", "StringValue": "WO99/X"}],
    }
    await layer.get_document("j", invoice_data, "POD", tmp_path)
    rows_before = layer.get_failed_rows("j")
    assert len(rows_before) == 1
    row_id = rows_before[0].row_id

    # Retry: WO exists but has no POD on it. run_document returns (None, None) — not an error.
    tms_api.get_work_order.side_effect = None
    tms_api.get_work_order.return_value = {"documents": []}

    ok = await layer.retry_failed_row("j", row_id, source="api")
    assert ok is False, "POD genuinely absent on WO — retry must report failure"
    rows_after = layer.get_failed_rows("j")
    assert len(rows_after) == 1
    assert "not present" in rows_after[0].error_message.lower() \
        or "no doc" in rows_after[0].error_message.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && python -m pytest tests/test_tms_data/test_data_layer.py::test_retry_get_document_records_when_doc_absent_on_wo -v`
Expected: FAIL — current code returns False but doesn't re-record, leaving 0 rows after.

- [ ] **Step 3: Fix the retry get_document branch**

In `retry_failed_row`, after the get_document retry call, distinguish "doc not present" from "success":

```python
elif ctx["operation"] == "get_document":
    rows_before = len(self._failed.get_rows(job_id))
    path = await self.get_document(
        job_id, ctx["invoice_data"], ctx["doc_type"],
        ctx["dest_dir"], source=source,
    )
    rows_after = len(self._failed.get_rows(job_id))

    if path is not None:
        return True
    if rows_after > rows_before:
        # get_document already recorded a new failure (network error, etc.).
        return False
    # No path returned and no new failure recorded — WO has no such doc.
    self._failed.record_failure(
        job_id=job_id,
        invoice_number=_invoice_label(ctx["invoice_data"]),
        container_number=None,
        operation="get_document",
        doc_type=ctx["doc_type"],
        error_message=f"Document {ctx['doc_type']} not present on WO",
        source="tms_api" if source == "api" else "tms_browser",
    )
    self._retry_ctx[self._failed.get_rows(job_id)[-1].row_id] = ctx
    return False
```

(Yes, this still uses `[-1].row_id` — but only for the just-recorded entry where it's race-safe inside the synchronous path. If you'd rather, capture the return value from `record_failure` and use that.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && python -m pytest tests/test_tms_data/ -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/services/tms_data/__init__.py agent/tests/test_tms_data/test_data_layer.py
git commit -m "fix(tms_data): retry records 'doc not present' rather than masking as failure"
```

---

### Task 8: Live TMS API field-name validation (deferred from milestone 1 Task 14)

The cascade extractors try `chassis_no | chassis | chassis_number` and `billto | bill_to | consignee | cnee`. Verify against the live TMS API. This is a one-time check — write a smoke test that hits `/tms/api-test/{wo_no}` and asserts which keys are actually present.

**Files:**
- Create: `agent/tests/test_tms_data/test_field_names_live.py` (manual / opt-in)

- [ ] **Step 1: Write a manual smoke test that prints the key set**

```python
# agent/tests/test_tms_data/test_field_names_live.py
"""LIVE TMS API field-name probe.

Skipped by default. Run manually with:
  cd agent && TMS_API_LIVE=1 python -m pytest tests/test_tms_data/test_field_names_live.py -v -s

Requires a working TMS API token in .env and a valid WO# in TEST_WO_NO env var.
"""

import os
import pytest


@pytest.mark.skipif(
    not os.getenv("TMS_API_LIVE"),
    reason="Set TMS_API_LIVE=1 to run live TMS API probes",
)
@pytest.mark.asyncio
async def test_print_wo_field_names():
    from services.tms_api import TMSApiClient

    api = TMSApiClient()
    assert api.is_configured(), "TMS API not configured (.env missing TMS_API_TOKEN)"

    wo_no = os.getenv("TEST_WO_NO", "LM2602170009")  # known-good per memory
    wo = await api.get_work_order(wo_no)
    assert wo, f"No WO returned for {wo_no}"

    print("\n=== TMS WO keys for", wo_no, "===")
    for k in sorted(wo.keys()):
        v = wo[k]
        sample = (str(v)[:60] + "...") if isinstance(v, str) and len(str(v)) > 60 else v
        print(f"  {k!r}: {sample!r}")

    # Assert at least one of each expected field-name family is present.
    chassis_keys = {"chassis_no", "chassis", "chassis_number"}
    cnee_keys = {"billto", "bill_to", "consignee", "cnee"}
    assert chassis_keys & set(wo.keys()) or cnee_keys & set(wo.keys()), (
        "Neither chassis nor CNEE candidate keys present — extractor list needs update"
    )
```

- [ ] **Step 2: User runs the live probe and reports the actual key names**

The user (Joseph) runs:
```bash
cd agent
TMS_API_LIVE=1 TEST_WO_NO=LM2602170009 python -m pytest tests/test_tms_data/test_field_names_live.py -v -s
```

If the printed key list reveals an unexpected field name (e.g., `chassis_number_str` instead of `chassis_no`), update `agent/services/tms_data/extractors.py` accordingly and add the new key to the candidate tuple.

- [ ] **Step 3: Commit the probe + any extractor updates**

```bash
git add agent/tests/test_tms_data/test_field_names_live.py
# add agent/services/tms_data/extractors.py if updated
git commit -m "test(tms_data): live field-name probe + extractor candidate updates"
```

---

## Phase B — Failed Rows HTTP API + SSE events

Three tasks. Endpoints + SSE wiring so the UI can read failed rows and trigger retries.

### Task 9: Add `/jobs/{job_id}/failed-rows` GET endpoint

Read the failed-rows list for a job. Returns a JSON array of `FailedRow` dicts.

**Files:**
- Modify: `agent/routers/jobs.py` (add endpoint)
- Test: `agent/tests/test_endpoints.py` (add 2 tests)

- [ ] **Step 1: Write the failing test**

```python
# Append to agent/tests/test_endpoints.py

def test_get_failed_rows_unknown_job_returns_empty_list(client):
    r = client.get("/jobs/does-not-exist/failed-rows")
    assert r.status_code == 200
    assert r.json() == {"rows": []}


def test_get_failed_rows_returns_recorded_failures(client, app):
    # Seed a failure directly into the data layer.
    from services.tms_data.failed_rows import FailedRow
    layer = app.state.tms_data
    layer._failed._rows.setdefault("job-x", []).append(
        FailedRow(
            row_id="row-test1",
            invoice_number="INV-1",
            container_number="ABCU0000001",
            operation="get_document",
            doc_type="POD",
            error_message="boom",
            failed_at_source="tms_api",
            timestamp=1.0,
        )
    )
    r = client.get("/jobs/job-x/failed-rows")
    assert r.status_code == 200
    body = r.json()
    assert len(body["rows"]) == 1
    assert body["rows"][0]["invoice_number"] == "INV-1"
    assert body["rows"][0]["doc_type"] == "POD"
    assert body["rows"][0]["failed_at_source"] == "tms_api"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && python -m pytest tests/test_endpoints.py::test_get_failed_rows_unknown_job_returns_empty_list -v`
Expected: FAIL — 404 or route not found.

- [ ] **Step 3: Add the endpoint**

In `agent/routers/jobs.py`, append:

```python
from dataclasses import asdict
from fastapi import Request


@router.get("/{job_id}/failed-rows")
async def get_failed_rows(job_id: str, request: Request):
    """List failed rows for a job. UI polls this on SSE event or page load."""
    layer = request.app.state.tms_data
    rows = layer.get_failed_rows(job_id)
    return {"rows": [asdict(r) for r in rows]}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && python -m pytest tests/test_endpoints.py -v`
Expected: All endpoint tests PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/routers/jobs.py agent/tests/test_endpoints.py
git commit -m "feat(jobs): GET /jobs/{job_id}/failed-rows endpoint"
```

---

### Task 10: Add per-row + batch retry endpoints

Two POST endpoints: per-row retry, and retry-all batch.

**Files:**
- Modify: `agent/routers/jobs.py` (add 2 endpoints)
- Test: `agent/tests/test_endpoints.py` (add 3 tests)

- [ ] **Step 1: Write the failing tests**

```python
# Append to agent/tests/test_endpoints.py

def test_retry_failed_row_invalid_source_rejected(client):
    r = client.post("/jobs/j/failed-rows/row-1/retry?source=carrier-pigeon")
    assert r.status_code == 422 or r.status_code == 400


def test_retry_failed_row_unknown_returns_404(client, app):
    r = client.post("/jobs/no-such/failed-rows/row-x/retry?source=api")
    assert r.status_code == 200
    assert r.json()["succeeded"] is False


def test_retry_all_failed_returns_counts(client, app):
    # Seed two failures.
    from services.tms_data.failed_rows import FailedRow
    layer = app.state.tms_data
    layer._failed._rows["job-batch"] = [
        FailedRow("r1", "INV-1", None, "enrich_invoice", None, "e", "tms_api", 1.0),
        FailedRow("r2", "INV-2", None, "enrich_invoice", None, "e", "tms_api", 2.0),
    ]
    layer._retry_ctx["r1"] = {"operation": "enrich_invoice", "invoice_data": {}, "force": False}
    layer._retry_ctx["r2"] = {"operation": "enrich_invoice", "invoice_data": {}, "force": False}

    r = client.post("/jobs/job-batch/failed-rows/retry-all?source=api")
    assert r.status_code == 200
    body = r.json()
    assert "succeeded" in body and "still_failed" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent && python -m pytest tests/test_endpoints.py -k retry -v`
Expected: FAIL — endpoints don't exist.

- [ ] **Step 3: Add the endpoints**

```python
from typing import Literal
from fastapi import HTTPException, Query


@router.post("/{job_id}/failed-rows/{row_id}/retry")
async def retry_failed_row(
    job_id: str,
    row_id: str,
    request: Request,
    source: Literal["api", "browser"] = Query(..., description="api or browser"),
):
    """Retry one failed row using the chosen source."""
    layer = request.app.state.tms_data
    ok = await layer.retry_failed_row(job_id, row_id, source)
    return {"succeeded": ok}


@router.post("/{job_id}/failed-rows/retry-all")
async def retry_all_failed(
    job_id: str,
    request: Request,
    source: Literal["api", "browser"] = Query(...),
):
    """Retry every currently-failed row in the job. Returns counts."""
    layer = request.app.state.tms_data
    return await layer.retry_all_failed(job_id, source)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && python -m pytest tests/test_endpoints.py -v`
Expected: All endpoint tests PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/routers/jobs.py agent/tests/test_endpoints.py
git commit -m "feat(jobs): retry-failed-row + retry-all endpoints"
```

---

### Task 11: Wire `tms_data` into JobManager + emit failed-row SSE events

Add `set_tms_data` setter to JobManager. Wrap data-layer calls so each new failure / removal emits an SSE event the UI can listen for.

**Files:**
- Modify: `agent/services/job_manager/__init__.py` (add `_tms_data` + setter, helper to emit events)
- Modify: `agent/main.py` (call `job_manager.set_tms_data(tms_data)`)
- Modify: `agent/services/job_manager/send_job.py` (call `tms_data.reset_for_new_job(job.id)` at job start)
- Test: `agent/tests/test_tms_data/test_data_layer.py` (no new tests needed beyond what Phase D covers — JobManager wiring is exercised by integration)

- [ ] **Step 1: Add `_tms_data` + `set_tms_data` to JobManager**

In `agent/services/job_manager/__init__.py:294-305`, the constructor (already updated in Task 1) has `self._tms_data = None`. Add the setter method below `set_tms_api`:

```python
def set_tms_data(self, layer) -> None:
    """Inject the shared TMSDataLayer instance (called from main.py lifespan)."""
    self._tms_data = layer
```

- [ ] **Step 2: Wire it from `main.py`**

Add after `job_manager.set_tms_api(tms_api)` (Task 1):

```python
job_manager.set_tms_data(tms_data)
```

- [ ] **Step 3: Reset failed rows + WO cache when each send job starts**

In `agent/services/job_manager/send_job.py`, at the top of `_run_send_job_inner`, after `job.status = "running"`:

```python
if self._tms_data:
    self._tms_data.reset_for_new_job(job.id)
```

- [ ] **Step 4: Add SSE-emitting wrappers around the failure tracker**

The cleanest way to emit events without polluting cascade.py is to emit them from the JobManager wrappers that *call* the data layer (Tasks 12-13). To enable that, add a small helper in `send_job.py`:

```python
async def _emit_failed_rows_changed(self, job, reason: str = "added"):
    """Push a 'failed_rows_changed' SSE event so the UI re-fetches the list."""
    await self._emit_send(job, "failed_rows_changed", {
        "jobId": job.id,
        "reason": reason,  # "added" | "removed" | "cleared"
    })
```

Phase D will call `await self._emit_failed_rows_changed(job, "added")` after each data-layer call that may have recorded a failure.

- [ ] **Step 5: Run the existing test suite to confirm nothing broke**

Run: `cd agent && python -m pytest tests/ -v`
Expected: All existing tests PASS.

- [ ] **Step 6: Commit**

```bash
git add agent/main.py agent/services/job_manager/__init__.py agent/services/job_manager/send_job.py
git commit -m "feat(job_manager): wire TMSDataLayer + failed_rows_changed SSE events"
```

---

## Phase C — Migrate Invoice Sender

Two large refactors. After this phase, `send_qbo_api.py` and `send_oec.py` no longer call `self._tms.fetch_*` directly — all TMS access goes through `self._tms_data`. Browser is opt-in only.

### Task 12: Migrate `send_qbo_api._tms_fetch_and_upload_missing_docs`

Today's flow (lines 56-168) tries direct-URL TMS browser fetch per missing doc, falls back to grid POD-only fetch. After this task, it calls `self._tms_data.get_documents(...)` once and uploads each returned doc to QBO.

**Files:**
- Modify: `agent/services/job_manager/send_qbo_api.py:56-168` (replace `_tms_fetch_and_upload_missing_docs`)
- Modify: `agent/services/job_manager/send_qbo_api.py:263-291` (call site — drop wo_no/detail_type args)
- Test: `agent/tests/test_job_manager/test_send_qbo_api_tms_data.py` (new file)

- [ ] **Step 1: Create the test directory and write the failing test**

```python
# agent/tests/test_job_manager/__init__.py (empty file if not present)

# agent/tests/test_job_manager/test_send_qbo_api_tms_data.py
"""Verify send_qbo_api uses TMSDataLayer.get_documents instead of browser calls."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_layer():
    layer = MagicMock()
    layer.get_documents = AsyncMock(return_value={})
    layer.get_failed_rows = MagicMock(return_value=[])
    return layer


@pytest.mark.asyncio
async def test_fetch_and_upload_uses_tms_data_get_documents(mock_layer, tmp_path):
    """The send mixin calls TMSDataLayer.get_documents — never tms_browser.fetch_*."""
    from services.job_manager import JobManager
    from services.qbo_api.client import QBOApiClient

    jm = JobManager(QBOApiClient(), classifier=MagicMock())
    jm.set_tms_data(mock_layer)
    jm._tms = MagicMock(name="tms_browser_should_not_be_called")
    jm._emit_send = AsyncMock()

    job = MagicMock(id="job-1", test_mode=False)
    invoice = MagicMock(invoice_number="INV-1", container_number="ABCU0000001",
                        do_sender_email=None, customer_code="CUST")
    api = MagicMock()
    api.upload_attachment = AsyncMock(return_value=True)
    invoice_data = {"DocNumber": "INV-1", "Id": "1",
                    "CustomField": [{"Name": "NGL REF#", "StringValue": "WO/X"}]}
    verification = {"found_container": "ABCU0000001"}

    # Pretend the data layer returns 2 of the 3 missing docs.
    mock_layer.get_documents.return_value = {
        "pod": tmp_path / "pod.pdf",
        "bl": tmp_path / "bl.pdf",
    }
    (tmp_path / "pod.pdf").write_bytes(b"%PDF-pod")
    (tmp_path / "bl.pdf").write_bytes(b"%PDF-bl")

    uploaded = await jm._tms_fetch_and_upload_missing_docs(
        job, invoice, api, "1", verification, tmp_path,
        ["pod", "bl", "do"], invoice_data=invoice_data,
    )

    assert mock_layer.get_documents.await_count == 1
    args, kwargs = mock_layer.get_documents.call_args
    assert kwargs.get("doc_types") == ["pod", "bl", "do"] or args[2] == ["pod", "bl", "do"]
    assert sorted(uploaded) == ["bl", "pod"]
    # Browser must not be touched on the API path.
    assert not jm._tms.fetch_doc_by_wo.called
    assert not jm._tms.fetch_pod_and_do_sender.called
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && python -m pytest tests/test_job_manager/test_send_qbo_api_tms_data.py -v`
Expected: FAIL — current `_tms_fetch_and_upload_missing_docs` doesn't accept `invoice_data` and calls `self._tms` directly.

- [ ] **Step 3: Replace `_tms_fetch_and_upload_missing_docs` with the data-layer version**

In `agent/services/job_manager/send_qbo_api.py`, replace lines 56-168 with:

```python
async def _tms_fetch_and_upload_missing_docs(
    self, job, invoice, api, invoice_id, verification, temp_dir,
    missing_docs, invoice_data: dict,
) -> list[str]:
    """Fetch each missing required doc via the TMS Data Layer, upload to QBO.

    Failures are accumulated in the data layer's per-job FailedRowsTracker; the
    UI shows them in the Failed Rows box with explicit Retry buttons. We never
    auto-fall-back to the browser — the user must opt in.
    """
    if not self._tms_data:
        logger.warning("TMSDataLayer not configured — skipping doc fetch for %s",
                       invoice.invoice_number)
        return []

    # Filter out non-fetchable types.
    types_to_fetch = [
        (m or "").lower() for m in missing_docs
        if (m or "").lower() and (m or "").lower() != "invoice"
    ]
    if not types_to_fetch:
        return []

    container = verification.get("found_container") or invoice.container_number or ""
    await self._emit_send(job, "tms_fetching_docs", {
        "invoiceNumber": invoice.invoice_number,
        "containerNumber": container,
        "docTypes": types_to_fetch,
    })

    fetched = await self._tms_data.get_documents(
        job.id, invoice_data, types_to_fetch, temp_dir, source="api",
    )

    # Surface any failures the layer just recorded.
    await self._emit_failed_rows_changed(job, "added")

    uploaded: list[str] = []
    for dt in types_to_fetch:
        path = fetched.get(dt)
        if not (path and path.exists()):
            await self._emit_send(job, "tms_doc_not_found", {
                "invoiceNumber": invoice.invoice_number,
                "docType": dt,
            })
            continue

        await self._emit_send(job, "uploading_doc_to_qbo", {
            "invoiceNumber": invoice.invoice_number,
            "docType": dt,
            "fileName": path.name,
        })
        if await api.upload_attachment(invoice_id, path):
            uploaded.append(dt)
            await self._emit_send(job, "doc_uploaded_to_qbo", {
                "invoiceNumber": invoice.invoice_number,
                "docType": dt,
                "fileName": path.name,
            })
        else:
            await self._emit_send(job, "doc_upload_failed", {
                "invoiceNumber": invoice.invoice_number,
                "docType": dt,
                "error": "QBO upload API returned no result",
            })

    return uploaded
```

Also delete the `_ensure_tms_login` helper and `SUPPORTED_DIRECT_URL_DOC_TYPES` constant — neither is needed when the API path drives doc fetching.

- [ ] **Step 4: Update the call site to pass `invoice_data`**

In `agent/services/job_manager/send_qbo_api.py:263-291`, replace the `if missing_docs and self._tms and not is_oec:` block:

```python
if missing_docs and self._tms_data and not is_oec:
    temp_dir = Path(tempfile.mkdtemp(prefix="ngl_docs_"))
    try:
        uploaded = await asyncio.wait_for(
            self._tms_fetch_and_upload_missing_docs(
                job, invoice, api, invoice_id, verification, temp_dir,
                missing_docs, invoice_data=invoice_data,
            ),
            timeout=TMS_FETCH_TIMEOUT_S,
        )
        if uploaded:
            att_check = await api.check_attachments(invoice_id, required_docs)
            result.attachments_found = att_check.get("found", [])
            result.attachments_missing = att_check.get("missing", [])
            all_attachments = att_check.get("attachments", [])
    except asyncio.TimeoutError:
        logger.warning("TMS doc fetch timed out after %ds for %s — skipping",
                       TMS_FETCH_TIMEOUT_S, invoice.invoice_number)
        await self._emit_send(job, "tms_fetch_timeout", {
            "invoiceNumber": invoice.invoice_number,
            "message": f"TMS doc fetch timed out after {TMS_FETCH_TIMEOUT_S}s",
        })
    except Exception as e:
        logger.warning("TMS doc fetch failed for %s: %s",
                       invoice.invoice_number, e)
        await self._emit_send(job, "tms_fetch_error", {
            "invoiceNumber": invoice.invoice_number,
            "error": str(e),
        })
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd agent && python -m pytest tests/ -v`
Expected: All tests PASS, including the new send_qbo_api test.

- [ ] **Step 6: Commit**

```bash
git add agent/services/job_manager/send_qbo_api.py agent/tests/test_job_manager/
git commit -m "refactor(send_qbo_api): use TMSDataLayer.get_documents instead of browser"
```

---

### Task 13: Migrate `send_oec._oec_tms_lookup`

OEC currently has 200+ lines of TMS API + browser fallback logic in `_oec_tms_lookup` ([send_oec.py:348-559](../../../agent/services/job_manager/send_oec.py#L348-L559)). Replace with two data-layer calls: `enrich_invoice(force=True)` for D/O sender, `get_document("POD")` for POD download. Preserve the local D/O sender cache as a final fallback (hard invariant #11).

**Files:**
- Modify: `agent/services/job_manager/send_oec.py` (replace `_oec_tms_lookup`, simplify `_send_oec_pod_email`)
- Test: `agent/tests/test_job_manager/test_send_oec_tms_data.py` (new)

- [ ] **Step 1: Write the failing tests covering hard invariants**

```python
# agent/tests/test_job_manager/test_send_oec_tms_data.py
"""Verify OEC POD-email step uses TMSDataLayer and preserves hard invariants."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_jm(layer):
    from services.job_manager import JobManager, SendResult
    from services.qbo_api.client import QBOApiClient

    jm = JobManager(QBOApiClient(), classifier=MagicMock())
    jm.set_tms_data(layer)
    jm._tms = MagicMock(name="tms_browser_should_not_be_called")
    jm._tms_api = MagicMock()
    jm._email_sender = AsyncMock()
    jm._email_sender.send_pod_email = AsyncMock(return_value={"sent": True})
    jm._emit_send = AsyncMock()
    jm._get_cached_do_sender = MagicMock(return_value=None)
    jm._save_do_sender_cache = MagicMock()
    jm._qbo_api = AsyncMock()
    return jm


@pytest.mark.asyncio
async def test_oec_calls_enrich_with_force_true(tmp_path):
    """OEC must pass force=True so do_sender is fetched even if QBO has chassis+CNEE."""
    from services.tms_data.enriched_invoice import EnrichedInvoice
    layer = MagicMock()
    layer.enrich_invoice = AsyncMock(return_value=EnrichedInvoice(
        wo_no="WO1", container_no="ABCU1", chassis="C/X", cnee="CN",
        do_sender_email="dosender@example.com",
        sources={"do_sender_email": "tms_api"},
    ))
    layer.get_document = AsyncMock(return_value=tmp_path / "pod.pdf")
    (tmp_path / "pod.pdf").write_bytes(b"%PDF")

    jm = _make_jm(layer)
    jm._qbo_api.search_invoice = AsyncMock(return_value={"Id": "1", "DocNumber": "INV-1"})
    jm._qbo_api.verify_invoice_details = AsyncMock(return_value={"found_container": "ABCU1"})
    jm._qbo_api.check_attachments = AsyncMock(return_value={"attachments": []})

    job = MagicMock(id="oec-1", test_mode=False)
    invoice = MagicMock(invoice_number="INV-1", container_number="ABCU1",
                        do_sender_email=None, customer_code="OEC")
    customer = {
        "podEmailTo": ["pod@cust.com"], "podEmailCc": [],
        "podEmailSubject": "POD", "podEmailBody": "POD body",
    }
    from services.job_manager import SendResult
    result = SendResult(invoice_number="INV-1")

    await jm._send_oec_pod_email(job, invoice, customer, result, 0)

    # enrich_invoice was called with force=True
    args, kwargs = layer.enrich_invoice.call_args
    assert kwargs.get("force") is True or (len(args) > 3 and args[3] is True)


@pytest.mark.asyncio
async def test_oec_invoice_do_sender_email_set_from_layer(tmp_path):
    """Hard invariant #4: D/O sender from data layer must populate invoice.do_sender_email."""
    from services.tms_data.enriched_invoice import EnrichedInvoice
    layer = MagicMock()
    layer.enrich_invoice = AsyncMock(return_value=EnrichedInvoice(
        wo_no="WO1", container_no="ABCU1", chassis=None, cnee=None,
        do_sender_email="dosender@example.com",
        sources={"do_sender_email": "tms_api"},
    ))
    layer.get_document = AsyncMock(return_value=tmp_path / "pod.pdf")
    (tmp_path / "pod.pdf").write_bytes(b"%PDF")

    jm = _make_jm(layer)
    jm._qbo_api.search_invoice = AsyncMock(return_value={"Id": "1", "DocNumber": "INV-1"})
    jm._qbo_api.verify_invoice_details = AsyncMock(return_value={"found_container": "ABCU1"})
    jm._qbo_api.check_attachments = AsyncMock(return_value={"attachments": []})

    job = MagicMock(id="oec-2", test_mode=False)
    invoice = MagicMock(invoice_number="INV-1", container_number="ABCU1",
                        do_sender_email=None, customer_code="OEC")
    customer = {
        "podEmailTo": ["pod@cust.com"], "podEmailCc": [],
        "podEmailSubject": "POD", "podEmailBody": "POD body",
    }
    from services.job_manager import SendResult
    result = SendResult(invoice_number="INV-1")

    await jm._send_oec_pod_email(job, invoice, customer, result, 0)

    assert invoice.do_sender_email == "dosender@example.com"


@pytest.mark.asyncio
async def test_oec_falls_back_to_cache_when_data_layer_returns_no_do_sender(tmp_path):
    """Hard invariant #11: D/O sender cache is the final fallback."""
    from services.tms_data.enriched_invoice import EnrichedInvoice
    layer = MagicMock()
    layer.enrich_invoice = AsyncMock(return_value=EnrichedInvoice(
        wo_no="WO1", container_no="ABCU1", chassis=None, cnee=None,
        do_sender_email=None, sources={"do_sender_email": "missing"},
    ))
    layer.get_document = AsyncMock(return_value=tmp_path / "pod.pdf")
    (tmp_path / "pod.pdf").write_bytes(b"%PDF")

    jm = _make_jm(layer)
    jm._get_cached_do_sender = MagicMock(return_value="cached@example.com")
    jm._qbo_api.search_invoice = AsyncMock(return_value={"Id": "1", "DocNumber": "INV-1"})
    jm._qbo_api.verify_invoice_details = AsyncMock(return_value={"found_container": "ABCU1"})
    jm._qbo_api.check_attachments = AsyncMock(return_value={"attachments": []})

    job = MagicMock(id="oec-3", test_mode=False)
    invoice = MagicMock(invoice_number="INV-1", container_number="ABCU1",
                        do_sender_email=None, customer_code="OEC")
    customer = {
        "podEmailTo": ["pod@cust.com"], "podEmailCc": [],
        "podEmailSubject": "POD", "podEmailBody": "POD body",
    }
    from services.job_manager import SendResult
    result = SendResult(invoice_number="INV-1")

    await jm._send_oec_pod_email(job, invoice, customer, result, 0)

    assert invoice.do_sender_email == "cached@example.com"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent && python -m pytest tests/test_job_manager/test_send_oec_tms_data.py -v`
Expected: FAIL — `_send_oec_pod_email` still uses `_oec_tms_lookup` browser path.

- [ ] **Step 3: Rewrite `_send_oec_pod_email` to use the data layer**

In `agent/services/job_manager/send_oec.py`, replace the entire `_send_oec_pod_email` body and delete `_oec_tms_lookup`:

```python
async def _send_oec_pod_email(self, job, invoice, customer: dict,
                               result, index: int) -> None:
    """Send the POD/D-O email FIRST in the OEC flow.

    Runs BEFORE _send_qbo_api. Uses TMSDataLayer for both the POD download
    and the D/O sender lookup. Falls back to the local cache when the layer
    can't find a D/O sender (hard invariant #11). Sets result.pod_status
    only — does NOT set result.status (owned by the invoice email step).
    """
    api = self._qbo_api

    invoice_data = await api.search_invoice(invoice.invoice_number)
    if not invoice_data:
        logger.warning("[OEC_POD] Invoice %s not found in QBO — skipping POD email",
                       invoice.invoice_number)
        result.pod_status = "skipped"
        return

    invoice_id = invoice_data["Id"]
    verification = await api.verify_invoice_details(
        invoice_data, invoice.container_number, invoice.amount or None
    )
    container = (verification.get("found_container")
                 or invoice.container_number or "")

    # Look for POD already attached to QBO before going to TMS.
    att_check = await api.check_attachments(invoice_id, ["invoice", "pod"])
    all_attachments = att_check.get("attachments", [])
    temp_dir = Path(tempfile.mkdtemp(prefix="ngl_pod_"))
    pod_path = None
    pod_source = None

    for att in all_attachments:
        if att.get("docType") == "pod" and att.get("id"):
            await self._emit_send(job, "oec_downloading_pod", {
                "invoiceNumber": invoice.invoice_number,
            })
            pod_path = await api.download_attachment(
                att["id"], att.get("fileName", "pod.pdf"), temp_dir
            )
            if pod_path:
                pod_source = "QBO"
            break

    # ── TMS Data Layer: D/O sender + POD (if not already from QBO) ──
    csv_do_sender = invoice.do_sender_email or ""
    layer_do_sender_source = ""

    if self._tms_data:
        try:
            enriched = await asyncio.wait_for(
                self._tms_data.enrich_invoice(
                    job.id, invoice_data, source="api", force=True,
                ),
                timeout=TMS_FETCH_TIMEOUT_S,
            )
            if enriched.do_sender_email and not invoice.do_sender_email:
                invoice.do_sender_email = enriched.do_sender_email
                layer_do_sender_source = "TMS API"

            if not pod_path:
                tms_pod = await asyncio.wait_for(
                    self._tms_data.get_document(
                        job.id, invoice_data, "POD", temp_dir, source="api",
                    ),
                    timeout=TMS_FETCH_TIMEOUT_S,
                )
                if tms_pod and tms_pod.exists():
                    pod_path = tms_pod
                    pod_source = "TMS API"
                    await self._emit_send(job, "tms_pod_downloaded", {
                        "invoiceNumber": invoice.invoice_number,
                        "fileName": pod_path.name,
                        "strategy": "api",
                    })

            await self._emit_failed_rows_changed(job, "added")
        except asyncio.TimeoutError:
            logger.warning("[OEC] TMS Data Layer timed out for %s",
                           invoice.invoice_number)
            await self._emit_send(job, "tms_fetch_timeout", {
                "invoiceNumber": invoice.invoice_number,
                "message": f"TMS lookup timed out after {TMS_FETCH_TIMEOUT_S}s",
            })

    # ── Local D/O sender cache fallback (hard invariant #11) ──
    if not invoice.do_sender_email and not csv_do_sender:
        cached = self._get_cached_do_sender(invoice.container_number)
        if cached:
            invoice.do_sender_email = cached
            layer_do_sender_source = "Cache"
            await self._emit_send(job, "do_sender_from_cache", {
                "invoiceNumber": invoice.invoice_number,
                "containerNumber": invoice.container_number,
                "doSenderEmail": cached,
            })

    # Save TMS-derived D/O senders to cache for future fallback.
    if invoice.do_sender_email and layer_do_sender_source == "TMS API":
        self._save_do_sender_cache(
            invoice.container_number, invoice.do_sender_email,
            source="TMS API", strategy="data_layer",
        )

    # Determine D/O sender source label for events.
    do_sender_source = ""
    if invoice.do_sender_email:
        if csv_do_sender:
            do_sender_source = "CSV"
        else:
            do_sender_source = layer_do_sender_source or "TMS"
        await self._emit_send(job, "oec_do_sender_resolved", {
            "invoiceNumber": invoice.invoice_number,
            "doSenderEmail": invoice.do_sender_email,
            "doSenderSource": do_sender_source,
        })
    else:
        await self._emit_send(job, "oec_do_sender_missing", {
            "invoiceNumber": invoice.invoice_number,
            "containerNumber": invoice.container_number,
            "message": "D/O Sender email not found in TMS, cache, or CSV",
        })

    result.do_sender_email = invoice.do_sender_email or ""
    result.do_sender_source = do_sender_source

    # No POD found anywhere — skip the POD email but let invoice email run.
    if not pod_path:
        source = "QBO or TMS"
        result.pod_status = "skipped"
        result.error = f"No POD found ({source}) — D/O email skipped"
        await self._emit_send(job, "oec_pod_email_failed", {
            "invoiceNumber": invoice.invoice_number,
            "error": f"No POD found in {source} — send POD manually",
        })
        return

    # ── Build POD email recipients ──
    pod_to = normalize_email_list(customer.get("podEmailTo", []))
    pod_cc = normalize_email_list(customer.get("podEmailCc", []))
    validate_and_append_email(pod_cc, invoice.do_sender_email, label="D/O SENDER")

    pod_subject = customer.get("podEmailSubject", "") or f"POD — {invoice.container_number}"
    pod_body = customer.get("podEmailBody", "") or \
        f"Please find attached the Proof of Delivery for container {invoice.container_number}."

    token_map = {
        "{invoice_number}": invoice.invoice_number,
        "{container_number}": invoice.container_number,
        "{customer_name}": customer.get("name", ""),
        "{customer_code}": invoice.customer_code,
    }
    for token, value in token_map.items():
        pod_subject = pod_subject.replace(token, value)
        pod_body = pod_body.replace(token, value)

    # Pre-send verification.
    if not pod_to:
        result.pod_status = "skipped"
        result.error = "No podEmailTo recipients configured — D/O email skipped"
        await self._emit_send(job, "oec_pod_email_failed", {
            "invoiceNumber": invoice.invoice_number,
            "error": result.error,
        })
        return

    if not pod_path or not pod_path.exists():
        result.pod_status = "skipped"
        result.error = f"POD file missing or deleted: {pod_path}"
        await self._emit_send(job, "oec_pod_email_failed", {
            "invoiceNumber": invoice.invoice_number,
            "error": result.error,
        })
        return

    # Test mode approval (hard invariant #9).
    if job.test_mode:
        # ... [keep the existing test-mode approval block from send_oec.py:245-299
        # unchanged — it's not affected by the data layer migration] ...
        pass  # see existing implementation; copy it here verbatim

    # ── Send POD email — POD-only attachment (hard invariant #2) ──
    await self._emit_send(job, "oec_sending_pod_email", {
        "invoiceNumber": invoice.invoice_number,
        "to": pod_to, "cc": pod_cc,
    })

    email_result = await self._email_sender.send_pod_email(
        to=pod_to, cc=pod_cc,
        subject=pod_subject, body=pod_body,
        pod_path=pod_path,
    )

    if email_result.get("sent"):
        result.pod_status = "sent"
        await self._emit_send(job, "oec_pod_email_sent", {
            "invoiceNumber": invoice.invoice_number,
            "to": pod_to, "cc": pod_cc,
            "doSenderEmail": invoice.do_sender_email or "",
            "doSenderIncluded": bool(invoice.do_sender_email),
        })
    else:
        result.pod_status = "failed"
        result.error = f"POD email failed: {email_result.get('error', 'Unknown')}"
        await self._emit_send(job, "oec_pod_email_failed", {
            "invoiceNumber": invoice.invoice_number,
            "error": email_result.get("error", "Unknown error"),
        })

    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        pass
```

Then **delete the entire `_oec_tms_lookup` method** (lines 348-559) — no longer needed.

- [ ] **Step 4: Carefully copy the test-mode approval block back in**

In Step 3 above, the test-mode block has a `pass` placeholder. Copy lines 245-299 from the original `send_oec.py` verbatim into that location (the approval-event setup, the `_approval_event` wait, the cc_override application). This is invariant #9 and must not be subtly altered.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd agent && python -m pytest tests/ -v`
Expected: All tests PASS, including the 3 OEC migration tests.

- [ ] **Step 6: Commit**

```bash
git add agent/services/job_manager/send_oec.py agent/tests/test_job_manager/test_send_oec_tms_data.py
git commit -m "refactor(send_oec): use TMSDataLayer.enrich_invoice + get_document"
```

---

### Task 14: Remove obsolete TMS browser callers and prune dead code

After Tasks 12-13, several browser-driven helpers are no longer called from the send path. Remove unused imports and confirm the only TMS browser usage left in `send_qbo_api.py` and `send_oec.py` is the test-mode approval modal (none). Note: the TMS browser is *kept* alive for future "Retry (Browser)" calls — only the *send-path* usage is removed.

**Files:**
- Modify: `agent/services/job_manager/send_qbo_api.py` (drop `_ensure_tms_login`, `SUPPORTED_DIRECT_URL_DOC_TYPES`, `extract_wo_from_invoice` if unused)
- Modify: `agent/services/job_manager/send_oec.py` (drop `extract_wo_from_invoice`, `TMS_FETCH_TIMEOUT_S` only if unused — likely still needed)

- [ ] **Step 1: Run grep for unused imports/symbols in the two files**

Run: `cd agent && python -c "import ast, sys; [print(f) for f in ['services/job_manager/send_qbo_api.py', 'services/job_manager/send_oec.py']]"`

Then visually inspect both files: anything imported but not referenced is dead.

- [ ] **Step 2: Delete dead code**

Remove unused imports and constants. Be conservative — when in doubt, leave it.

- [ ] **Step 3: Run the full test suite to verify nothing broke**

Run: `cd agent && python -m pytest tests/ -v`
Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
git add agent/services/job_manager/send_qbo_api.py agent/services/job_manager/send_oec.py
git commit -m "chore(send): remove dead TMS browser helpers from send path"
```

---

## Phase D — Failed Rows UI

Three tasks. Adds the Failed Rows box to Invoice Sender's UI: HTML structure → CSS → JS render + button handlers + SSE handlers.

### Task 15: HTML structure for Failed Rows box

The spec says "top of the right-hand status panel, above the Status Log." Invoice Sender's layout doesn't have a separate right panel — it's a vertical layout with the table above the Status Log ([index.html:1340-1388](../../../app/index.html#L1340-L1388)). Insert the box between the Invoice Table and the Status Log so it's prominent but doesn't shove the table down when empty.

**Files:**
- Modify: `app/index.html:1367-1369` (insert after `</div><!-- /invTableContainer -->`)

- [ ] **Step 1: Insert the Failed Rows box markup**

Between the `</table></div>` that closes `invTableContainer` (line ~1367) and the `<!-- ── Status Log (Collapsible) ── -->` comment (line ~1369), insert:

```html
<!-- ── Failed Rows Box (TMS Data Layer failures) ── -->
<div id="invFailedRowsBox" class="panel-card" style="display:none; padding:14px 20px; margin-top:20px; border-left:3px solid #ea580c;">
  <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:10px;">
    <div style="display:flex; align-items:center; gap:8px;">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#ea580c" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
      </svg>
      <span class="section-label" style="margin-bottom:0;">Failed Rows</span>
      <span id="invFailedRowsCount" style="background:#fee2e2; color:#991b1b; font-size:0.72rem; font-weight:700; padding:2px 8px; border-radius:10px;">0</span>
    </div>
    <div style="display:flex; gap:6px;">
      <button id="invFailedRetryAllApi" class="btn btn-secondary" style="padding:5px 11px; font-size:0.78rem;" onclick="invFailedRetryAll('api')">
        Retry all (API)
      </button>
      <button id="invFailedRetryAllBrowser" class="btn btn-secondary" style="padding:5px 11px; font-size:0.78rem; border-color:#f59e0b; color:#92400e; background:#fffbeb;" onclick="invFailedRetryAll('browser')">
        Retry all (Browser)
      </button>
    </div>
  </div>
  <div id="invFailedRowsList" style="display:flex; flex-direction:column; gap:6px;"></div>
</div>
```

- [ ] **Step 2: Verify the page still loads**

Open `app/index.html` in the browser (or in the Electron desktop app via `npm start` from `desktop/`). Confirm the Invoice Sender view renders and the Failed Rows box is hidden by default.

- [ ] **Step 3: Commit**

```bash
git add app/index.html
git commit -m "feat(ui): Failed Rows box markup in Invoice Sender"
```

---

### Task 16: CSS styling for Failed Rows box

Add styles for the per-row layout (the `<div>` rows rendered into `#invFailedRowsList`).

**Files:**
- Modify: `app/assets/css/styles.css` (append a new section)

- [ ] **Step 1: Append the CSS block**

```css
/* ─── Failed Rows Box ─── */
.failed-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  background: #fef9f5;
  border: 1px solid #fde4cf;
  border-radius: 6px;
  font-size: 0.82rem;
}

.failed-row__label {
  flex: 1;
  min-width: 0;
}

.failed-row__invoice {
  font-weight: 600;
  color: #1e293b;
}

.failed-row__container {
  color: #64748b;
  font-size: 0.78rem;
  margin-left: 8px;
}

.failed-row__error {
  color: #991b1b;
  font-size: 0.76rem;
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.failed-row__op {
  font-size: 0.74rem;
  color: #64748b;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.failed-row__buttons {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}

.failed-row__retry-btn {
  padding: 4px 10px;
  font-size: 0.74rem;
  font-weight: 600;
  border-radius: 5px;
  cursor: pointer;
  border: 1px solid #cbd5e1;
  background: #fff;
  color: #334155;
}

.failed-row__retry-btn:hover { background: #f1f5f9; }

.failed-row__retry-btn--browser {
  border-color: #f59e0b;
  color: #92400e;
  background: #fffbeb;
}

.failed-row__retry-btn--disabled {
  opacity: 0.5;
  cursor: not-allowed;
  pointer-events: none;
}
```

- [ ] **Step 2: Reload the app and verify the styles render**

(Visual check — no automated test for CSS.)

- [ ] **Step 3: Commit**

```bash
git add app/assets/css/styles.css
git commit -m "feat(ui): CSS for Failed Rows box per-row layout"
```

---

### Task 17: JS — render rows, wire buttons, SSE handlers

Wire the Failed Rows box to the agent's HTTP API and SSE stream. On `failed_rows_changed` events, re-fetch the list and re-render. Per-row buttons hit the retry endpoint. Hide "Retry (Browser)" specifically for `operation == "enrich_invoice"` rows (see Task 4).

**Files:**
- Modify: `app/assets/js/tools/invoice-sender/invoice-sender.js` (add functions, wire SSE)

- [ ] **Step 1: Add render + retry functions**

Append to `invoice-sender.js`:

```javascript
// ────────────────────────────────────────────────────────────────────
// Failed Rows box — populated by SSE failed_rows_changed events.
// ────────────────────────────────────────────────────────────────────

let _invCurrentJobId = null;  // set when a send job starts

async function invFetchFailedRows(jobId) {
  if (!jobId) return [];
  try {
    const r = await fetch(`http://localhost:8787/jobs/${encodeURIComponent(jobId)}/failed-rows`);
    if (!r.ok) return [];
    const body = await r.json();
    return body.rows || [];
  } catch (e) {
    console.warn("Failed to fetch failed rows:", e);
    return [];
  }
}

function invRenderFailedRows(rows) {
  const box = document.getElementById("invFailedRowsBox");
  const list = document.getElementById("invFailedRowsList");
  const count = document.getElementById("invFailedRowsCount");
  if (!box || !list || !count) return;

  if (!rows || rows.length === 0) {
    box.style.display = "none";
    list.innerHTML = "";
    count.textContent = "0";
    return;
  }

  box.style.display = "block";
  count.textContent = String(rows.length);

  list.innerHTML = rows.map(row => {
    // Browser retry is unavailable for enrich_invoice (Task 4 stub).
    const browserDisabled = row.operation === "enrich_invoice";
    const opLabel = row.operation === "get_document"
      ? `${(row.doc_type || "doc").toUpperCase()}`
      : "ENRICH";
    const containerCell = row.container_number
      ? `<span class="failed-row__container">${escHtml(row.container_number)}</span>`
      : "";

    return `
      <div class="failed-row" data-row-id="${escHtml(row.row_id)}">
        <div class="failed-row__label">
          <div>
            <span class="failed-row__op">${escHtml(opLabel)}</span>
            <span class="failed-row__invoice">${escHtml(row.invoice_number || "(no #)")}</span>
            ${containerCell}
          </div>
          <div class="failed-row__error" title="${escHtml(row.error_message)}">
            ${escHtml(row.error_message)}
          </div>
        </div>
        <div class="failed-row__buttons">
          <button class="failed-row__retry-btn"
                  onclick="invFailedRetryRow('${escHtml(row.row_id)}', 'api')">
            Retry (API)
          </button>
          <button class="failed-row__retry-btn failed-row__retry-btn--browser ${browserDisabled ? 'failed-row__retry-btn--disabled' : ''}"
                  ${browserDisabled ? 'disabled title="Browser retry not yet supported for enrichment"' : ''}
                  onclick="invFailedRetryRow('${escHtml(row.row_id)}', 'browser')">
            Retry (Browser)
          </button>
        </div>
      </div>
    `;
  }).join("");
}

async function invFailedRetryRow(rowId, source) {
  if (!_invCurrentJobId) return;
  try {
    const r = await fetch(
      `http://localhost:8787/jobs/${encodeURIComponent(_invCurrentJobId)}/failed-rows/${encodeURIComponent(rowId)}/retry?source=${source}`,
      { method: "POST" },
    );
    const body = await r.json();
    // Re-fetch and re-render — the layer has updated by now.
    const rows = await invFetchFailedRows(_invCurrentJobId);
    invRenderFailedRows(rows);
    if (typeof invLog === "function") {
      invLog(body.succeeded
        ? `Retry succeeded for row ${rowId} (${source})`
        : `Retry failed for row ${rowId} (${source}) — see error in box`);
    }
  } catch (e) {
    console.error("Retry failed:", e);
  }
}

async function invFailedRetryAll(source) {
  if (!_invCurrentJobId) return;
  try {
    const r = await fetch(
      `http://localhost:8787/jobs/${encodeURIComponent(_invCurrentJobId)}/failed-rows/retry-all?source=${source}`,
      { method: "POST" },
    );
    const body = await r.json();
    const rows = await invFetchFailedRows(_invCurrentJobId);
    invRenderFailedRows(rows);
    if (typeof invLog === "function") {
      invLog(`Retry all (${source}): ${body.succeeded || 0} succeeded, ${body.still_failed || 0} still failed`);
    }
  } catch (e) {
    console.error("Retry all failed:", e);
  }
}
```

- [ ] **Step 2: Wire the SSE handler**

Find the existing SSE event-dispatch block in `invoice-sender.js` (search for the `switch` or `if/else` that matches `event.type` against `invoice_sent`, `tms_login_required`, etc.) and add a case:

```javascript
} else if (event.type === "failed_rows_changed") {
  invFetchFailedRows(_invCurrentJobId).then(invRenderFailedRows);
}
```

Also: when a send job starts (search for where the SSE `EventSource` is opened in response to `invSendViaQBO()`), set `_invCurrentJobId = job_id` from the `send_job_started` event payload.

- [ ] **Step 3: Reset the box when a new job starts**

When the send button is clicked (search for `invSendViaQBO`), reset the UI before opening the SSE:

```javascript
_invCurrentJobId = null;
invRenderFailedRows([]);
```

- [ ] **Step 4: Manual smoke test**

Restart the agent, open the Electron app (dev mode: `cd desktop && npm start`), upload a small CSV (3-5 invoices), and click Send.

Test cases:
- ✅ Box stays hidden when there are zero failures.
- ✅ Box appears the moment one row fails (artificially induce by editing `agent/.env` to set `TMS_API_BASE_URL=http://localhost:9` to break the API).
- ✅ Per-row "Retry (API)" button works — succeeds when API is restored.
- ✅ "Retry (Browser)" button is greyed out for enrich rows.
- ✅ "Retry all (API)" footer button retries every row in one batch.

If anything fails, fix and commit before moving on.

- [ ] **Step 5: Commit**

```bash
git add app/assets/js/tools/invoice-sender/invoice-sender.js
git commit -m "feat(ui): Failed Rows box render + retry buttons + SSE handler"
```

---

## Phase E — Verification, version bump, ship

Three tasks. Final verification, rebuild, GitHub release.

### Task 18: End-to-end smoke test in test mode

Manual integration test on a real QBO + real TMS API. Required because unit tests can't catch e.g. invariant #1 (POD email goes out before invoice email — they're separate `_email_sender` calls that have to run in the right order).

**Files:**
- None (this is a manual checklist — record the result in the commit message)

- [ ] **Step 1: Prepare a 5-row test CSV**

Pick 5 invoices: at least 1 OEC customer, at least 1 non-OEC customer, at least 1 invoice that you know has a POD missing on QBO but present on TMS (force the data layer to do real fetch work).

- [ ] **Step 2: Run in test mode with the QBO + TMS test environments**

In Invoice Sender, toggle Test Mode on, set limit to 5, upload the CSV, click Send Invoices. Approve each row when prompted.

- [ ] **Step 3: Verify each invariant 1–12 manually**

Walk down the "Hard Invariants" list at the top of this plan. For each, confirm in the SSE log + audit log that the behavior is preserved. Note especially:

- Hard invariant #1: For OEC rows, the `oec_pod_email_sent` event timestamp is *before* `invoice_sent`.
- Hard invariant #4: D/O sender appears in both POD email CC list and invoice email CC list.
- Hard invariant #11: If you disconnect the agent's network briefly during D/O sender lookup, the cache fallback still produces a result.

- [ ] **Step 4: Record the result**

If everything passed, proceed to Task 19. If anything failed, fix it (likely back in Phase C/D) and re-test. Don't ship a regression.

---

### Task 19: Bump VERSION + rebuild

Per CLAUDE.md "Rebuild Pipeline — MANDATORY", every rebuild bumps the version, builds agent + Electron installer, commits, pushes, and publishes a GH release. This is the first user-visible release of the data-layer cycle.

**Files:**
- Modify: `desktop/VERSION`
- Modify: `agent/main.py:44` (`AGENT_VERSION = "..."`) — must match `desktop/VERSION`

- [ ] **Step 1: Bump the version**

Read `desktop/VERSION`, increment the patch (or minor — Joseph's call) component. Update `agent/main.py` line 44 to match.

- [ ] **Step 2: Build agent + Electron installer**

Run: `cd desktop && build-all.bat`
Expected: `desktop/dist/NGL Accounting Setup X.Y.Z.exe` and `latest.yml` produced. If the agent build step (PyInstaller) fails, check `desktop/build-log-*.txt`.

- [ ] **Step 3: Commit the version bump + any build artifacts not under .gitignore**

```bash
git add desktop/VERSION agent/main.py
git commit -m "chore: bump version to X.Y.Z for milestone 2 release"
```

---

### Task 20: Push + GitHub release

- [ ] **Step 1: Push all milestone 2 commits**

```bash
git push origin main
```

- [ ] **Step 2: Create the GitHub release**

```bash
gh release create vX.Y.Z \
  desktop/dist/"NGL Accounting Setup X.Y.Z.exe" \
  desktop/dist/latest.yml \
  --title "vX.Y.Z — TMS Data Layer Milestone 2 (Invoice Sender)" \
  --notes "$(cat <<'EOF'
## TMS Data Layer Milestone 2 — Invoice Sender migration

**What's new:**
- Invoice Sender now uses the TMS Data Layer (QBO API → TMS REST API cascade) for all TMS lookups. Faster sends — most doc fetches are sub-second instead of 3-8 seconds.
- TMS browser is no longer auto-invoked during sends. It remains available as an opt-in fallback.
- New **Failed Rows box** above the Status Log: rows where TMS lookup failed appear here with explicit **Retry (API)** and **Retry (Browser)** buttons. Footer has batch retry buttons.
- OEC two-email flow unchanged: POD email first, invoice email second, D/O sender CC'd on both.

**Under the hood:**
- Single `TMSApiClient` instance shared across `main`, routers, and JobManager (was: 3 independent token caches).
- Per-job WO cache halves TMS API calls for OEC invoices.
- `enrich_invoice(force=True)` guarantees D/O sender is fetched for OEC even when QBO has chassis+CNEE.

**Hard invariants verified:** all 12 from the design spec (POD email order, attachment guards, D/O sender CC, status reconciliation, cache fallback, audit log shape, etc.).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Verify the release page shows the installer + latest.yml**

Open the URL `gh release create` printed. Confirm both attachments are listed and the release is marked Latest.

- [ ] **Step 4: Verify auto-update reaches the desktop app**

Wait ~5-10 minutes, restart the desktop app on a co-worker's machine (or your own), confirm the Electron auto-updater pulls the new version.

---

## Self-review checklist

Before declaring milestone 2 complete:

- [ ] All 20 tasks above committed
- [ ] All `pytest tests/` pass in the agent dir
- [ ] All 12 hard invariants verified in Task 18
- [ ] GH release published with installer + latest.yml
- [ ] `MEMORY.md` updated to reflect "Milestone 2: COMPLETE"

---

## Known limitations carried into milestone 3+

These are deliberate gaps, not regressions — note them when planning the next milestone:

1. **`fetch_detail_info` not implemented.** "Retry (Browser)" on an enrich failure shows a disabled button. Implementing the Detail Info scraper is its own future task; if telemetry shows API enrichment failures are rare in production, it may not be worth doing.
2. **Container Fetch and Chassis Finder still bypass the data layer.** They get their TMS API fallback in milestones 3 and 4.
3. **No telemetry yet** for "Retry (Browser)" click counts. Milestone 5 adds that — it's the signal for the eventual browser-removal decision.
