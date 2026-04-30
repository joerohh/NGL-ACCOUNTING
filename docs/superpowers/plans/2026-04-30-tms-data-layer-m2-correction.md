# TMS Data Layer M2 Correction — Unconditional TMS Cascade

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the inverted gate in `send_qbo_api.py` so the TMS cascade runs unconditionally for every non-OEC invoice with an extractable WO#. Switch from `requiredDocs`-driven `types_to_fetch` to TMS-WO-driven (download every doc with a `file_url`). Ship as v2.37.1.

**Architecture:** Add one new public method on the data layer — `get_all_documents` — that pulls every document on a TMS WO and returns `dict[doc_type, Path]`. Backed by a new cascade function `run_all_documents`. Rewrite `_tms_fetch_and_upload_missing_docs` to use it and dedupe against existing QBO attachments. The `if missing_docs and ...` gate is removed; `requiredDocs` becomes pure enforcement at the existing post-cascade check.

**Tech Stack:** Python 3.13 (FastAPI / asyncio / pytest), PyInstaller-bundled agent, Electron desktop wrapper, GitHub Releases for auto-update.

---

## Bug summary (recap)

Real-world miss 2026-04-30: `[NGL_INV] LM26040724F - Container#HASU4865550` (customer APEXMA01, WO `LM2604130046`) was sent without a POD. TMS WO had POD + DO + IT + ITE all with valid `file_url`s.

Root cause — `agent/services/job_manager/send_qbo_api.py:174-183`:

```python
missing_docs = [m for m in (result.attachments_missing or []) if (m or "").lower() != "invoice"]
...
if missing_docs and self._tms_data and not is_oec:
    ... cascade runs ...
```

`missing_docs` is derived from `customer.requiredDocs - what_QBO_has`. APEXMA01 is configured "Send all attachments" → `requiredDocs: []` (the "ALL" pill in the Customer Manager). With empty `requiredDocs`, `check_attachments` returns `missing=[]`, so `missing_docs=[]`, so the gate fails and the cascade never fires.

The cascade should run for every non-OEC invoice with an extractable WO#. `requiredDocs` should only enforce a block at the existing post-cascade check (line 225).

---

## File map

**New code:**
- `agent/services/tms_data/cascade.py` — append `run_all_documents`
- `agent/services/tms_data/__init__.py` — append `get_all_documents` method on `TMSDataLayer`

**Modified:**
- `agent/services/job_manager/send_qbo_api.py:28-95, 174-211` — rewrite `_tms_fetch_and_upload_missing_docs` body and call site

**Tests added:**
- `agent/tests/test_tms_data/test_cascade.py` — 3 tests for `run_all_documents`
- `agent/tests/test_tms_data/test_data_layer.py` — 2 tests for `get_all_documents`
- `agent/tests/test_send_qbo_api/test_tms_fetch_and_upload.py` (new file) — 3 tests for the rewritten method

**Docs:**
- `docs/superpowers/specs/2026-04-28-tms-data-layer-design.md` — append correction note

**Untouched (verify only):**
- `agent/services/job_manager/send_oec.py` — already pulls from TMS unconditionally
- `agent/services/tms_data/browser_path.py` — opt-in retry path; no `run_all_documents_browser` needed for v2.37.1 (browser-retry of all-docs can be a follow-up)

---

## Hard invariants the rewrite MUST preserve

1. OEC code path is untouched (`is_oec == True` → cascade does NOT run from `send_qbo_api.py`; OEC handles its own POD via `send_oec.py`).
2. `requiredDocs` enforcement at `send_qbo_api.py:225` still blocks the send when a required doc is missing post-cascade.
3. `ar@ngltrans.net` always CC'd on the invoice email.
4. Test mode approval gate still fires per row.
5. QBO is the source of truth for the invoice PDF.
6. TMS WO call coalesces via `_CachedTmsApi` so concurrent calls hit the API once.
7. The "invoice" doc type is never uploaded back to QBO (QBO has its own invoice PDF).

---

## Phase A — Data layer additions (TDD)

### Task 1: `cascade.run_all_documents`

Add a cascade function that calls `tms_api.get_work_order(wo_no)` once and downloads every documents entry that has a `file_url`. Returns the per-doc paths plus per-doc errors so the data layer can record each failure as its own FailedRow.

**Files:**
- Modify: `agent/services/tms_data/cascade.py` (append at end)
- Test: `agent/tests/test_tms_data/test_cascade.py` (append 3 tests)

- [ ] **Step 1: Write the failing tests**

Append to `agent/tests/test_tms_data/test_cascade.py`:

```python
from services.tms_data.cascade import run_all_documents


@pytest.mark.asyncio
async def test_run_all_documents_downloads_every_file_url(tmp_path):
    """All docs with a file_url are downloaded; the dict is keyed by lowercased type_."""
    invoice = _qbo_invoice(wo="LM2604130046")
    wo = {
        "documents": [
            {"type_": "POD", "file_url": "https://tms/pod.pdf"},
            {"type_": "DO", "file_url": "https://tms/do.pdf"},
            {"type_": "IT", "file_url": "https://tms/it.pdf"},
            {"type_": "ITE", "file_url": "https://tms/ite.pdf"},
            {"type_": "MEMO", "file_url": ""},  # no file_url — must be skipped
        ],
    }
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value=wo)
    tms_api.download_document = AsyncMock(side_effect=[b"PODBYTES", b"DOBYTES", b"ITBYTES", b"ITEBYTES"])

    paths, per_doc_errors, top_error = await run_all_documents(invoice, tmp_path, tms_api)

    assert top_error is None
    assert per_doc_errors == {}
    assert set(paths.keys()) == {"pod", "do", "it", "ite"}
    assert paths["pod"].read_bytes() == b"PODBYTES"
    assert paths["pod"].name.endswith("_pod.pdf")
    tms_api.get_work_order.assert_awaited_once_with("LM2604130046")
    assert tms_api.download_document.await_count == 4


@pytest.mark.asyncio
async def test_run_all_documents_no_wo_returns_top_error(tmp_path):
    """If the QBO invoice has no WO#, no API calls happen and top_error is set."""
    invoice = _qbo_invoice()  # no WO
    tms_api = AsyncMock()

    paths, per_doc_errors, top_error = await run_all_documents(invoice, tmp_path, tms_api)

    assert paths == {}
    assert per_doc_errors == {}
    assert top_error is not None
    assert "WO" in top_error
    tms_api.get_work_order.assert_not_called()


@pytest.mark.asyncio
async def test_run_all_documents_partial_download_failure_records_per_doc(tmp_path):
    """One doc downloads fine, another fails — each tracked separately."""
    invoice = _qbo_invoice(wo="LM2604130046")
    wo = {
        "documents": [
            {"type_": "POD", "file_url": "https://tms/pod.pdf"},
            {"type_": "DO", "file_url": "https://tms/do.pdf"},
        ],
    }
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value=wo)
    tms_api.download_document = AsyncMock(side_effect=[b"PODBYTES", None])

    paths, per_doc_errors, top_error = await run_all_documents(invoice, tmp_path, tms_api)

    assert top_error is None
    assert set(paths.keys()) == {"pod"}
    assert "do" in per_doc_errors
    assert "no data" in per_doc_errors["do"].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd agent && python -m pytest tests/test_tms_data/test_cascade.py -v -k run_all_documents
```

Expected: 3 FAIL with `ImportError: cannot import name 'run_all_documents'`.

- [ ] **Step 3: Implement `run_all_documents`**

Append to `agent/services/tms_data/cascade.py`:

```python
async def run_all_documents(
    invoice_data: dict,
    dest_dir: Path,
    tms_api,
) -> Tuple[dict[str, Path], dict[str, str], Optional[str]]:
    """Download every documents[].file_url on the WO. One get_work_order call.

    Returns (paths, per_doc_errors, top_error):
      - paths: dict of lowercased doc_type → saved Path (only successful downloads)
      - per_doc_errors: dict of lowercased doc_type → error message for failed downloads
      - top_error: non-None when get_work_order itself raised. None when:
                   * WO record was returned (per-doc errors recorded separately)
                   * WO returned 404 (treated as no-data, paths == {})
                   * No WO# extractable from QBO (returns "no WO#" top_error so the
                     data layer can record one summary failure if it wants to)
    """
    wo_no = extract_wo_from_qbo(invoice_data)
    if not wo_no:
        return {}, {}, "Cannot fetch from TMS API: no WO# on QBO invoice"

    try:
        wo = await tms_api.get_work_order(wo_no)
    except Exception as e:
        logger.warning("TMS API get_work_order failed for %s: %s", wo_no, e)
        return {}, {}, str(e)

    if not wo:
        # 404 or API not configured — not a hard failure.
        return {}, {}, None

    paths: dict[str, Path] = {}
    per_doc_errors: dict[str, str] = {}

    dest_dir.mkdir(parents=True, exist_ok=True)

    for doc in wo.get("documents") or []:
        if not isinstance(doc, dict):
            continue
        type_raw = doc.get("type_") or ""
        url = doc.get("file_url") or ""
        if not type_raw or not url:
            continue
        doc_type = type_raw.lower()

        try:
            data = await tms_api.download_document(url)
        except Exception as e:
            logger.warning("TMS API download_document(%s) raised: %s", url, e)
            per_doc_errors[doc_type] = str(e)
            continue

        if not data:
            per_doc_errors[doc_type] = f"Document download returned no data for {doc_type}"
            continue

        path = dest_dir / f"{wo_no}_{doc_type}.pdf"
        path.write_bytes(data)
        paths[doc_type] = path

    return paths, per_doc_errors, None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd agent && python -m pytest tests/test_tms_data/test_cascade.py -v
```

Expected: PASS — all existing cascade tests + 3 new ones.

- [ ] **Step 5: Commit**

```bash
git add agent/services/tms_data/cascade.py agent/tests/test_tms_data/test_cascade.py
git commit -m "feat(tms_data): add cascade.run_all_documents — downloads every WO doc with a file_url"
```

---

### Task 2: `TMSDataLayer.get_all_documents`

Public method on the data layer. Uses `run_all_documents` via the existing `_CachedTmsApi` adapter so concurrent same-WO calls coalesce. Records each per-doc download failure as its own `get_document` FailedRow (so the existing per-doc Retry buttons work as-is). Top-level WO failures are NOT recorded — they're logged but not user-retryable from the Failed Rows box (the user can re-trigger by re-running the batch).

**Files:**
- Modify: `agent/services/tms_data/__init__.py` (append after `get_documents`)
- Test: `agent/tests/test_tms_data/test_data_layer.py` (append 2 tests)

- [ ] **Step 1: Write the failing tests**

Append to `agent/tests/test_tms_data/test_data_layer.py`:

```python
@pytest.mark.asyncio
async def test_get_all_documents_returns_paths_and_records_per_doc_failures(tmp_path):
    """All TMS docs are returned; per-doc download failures land in FailedRowsTracker."""
    from services.tms_data import TMSDataLayer

    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value={
        "documents": [
            {"type_": "POD", "file_url": "https://tms/pod.pdf"},
            {"type_": "DO", "file_url": "https://tms/do.pdf"},
        ],
    })
    tms_api.download_document = AsyncMock(side_effect=[b"PODBYTES", None])

    layer = TMSDataLayer(qbo_api=MagicMock(), tms_api=tms_api, tms_browser=MagicMock())
    invoice = {
        "DocNumber": "LM26040724F",
        "CustomField": [{"Name": "NGL REF#", "StringValue": "LM2604130046/CUST"}],
    }

    paths = await layer.get_all_documents("job-1", invoice, tmp_path)

    assert set(paths.keys()) == {"pod"}
    rows = layer.get_failed_rows("job-1")
    assert len(rows) == 1
    assert rows[0].operation == "get_document"
    assert rows[0].doc_type == "do"


@pytest.mark.asyncio
async def test_get_all_documents_no_wo_returns_empty_no_failed_row(tmp_path):
    """No WO# on QBO invoice → empty dict, no FailedRow recorded (logged only)."""
    from services.tms_data import TMSDataLayer

    tms_api = AsyncMock()
    layer = TMSDataLayer(qbo_api=MagicMock(), tms_api=tms_api, tms_browser=MagicMock())

    invoice = {"DocNumber": "INV-NOWO", "CustomField": []}
    paths = await layer.get_all_documents("job-2", invoice, tmp_path)

    assert paths == {}
    assert layer.get_failed_rows("job-2") == []
    tms_api.get_work_order.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd agent && python -m pytest tests/test_tms_data/test_data_layer.py -v -k get_all_documents
```

Expected: 2 FAIL with `AttributeError: 'TMSDataLayer' object has no attribute 'get_all_documents'`.

- [ ] **Step 3: Implement `get_all_documents`**

In `agent/services/tms_data/__init__.py`, add the import (top of file with the other cascade imports) — change:

```python
from services.tms_data.cascade import run_document, run_enrich
```

to:

```python
from services.tms_data.cascade import run_all_documents, run_document, run_enrich
```

Then append this method to the `TMSDataLayer` class, immediately after `get_documents` (~line 196):

```python
async def get_all_documents(
    self,
    job_id: str,
    invoice_data: dict,
    dest_dir: Path,
    source: Source = "api",
) -> dict[str, Path]:
    """Fetch EVERY TMS document on the invoice's WO. Returns dict of doc_type → Path.

    Used by the non-OEC send flow: TMS is treated as the source of truth for
    supporting documents. Caller decides which to upload to QBO (e.g. dedupe
    against existing attachments).

    Per-doc download failures are recorded in FailedRowsTracker so the user
    can retry per-doc from the Failed Rows box. Top-level WO failures (no WO#,
    network error on get_work_order, 404) are logged but NOT recorded as
    FailedRows — the user can re-trigger by rerunning the batch.

    source='browser' is currently not supported for this method (raises). The
    browser path remains opt-in via per-doc retry through get_document.
    """
    if source == "browser":
        raise NotImplementedError(
            "get_all_documents only supports source='api'. "
            "Use get_document with source='browser' for explicit retries."
        )

    cached = self._CachedTmsApi(self._tms_api, self._wo_cache, self._in_flight, job_id)
    paths, per_doc_errors, top_error = await run_all_documents(
        invoice_data, dest_dir, cached,
    )

    if top_error:
        logger.info(
            "get_all_documents top-level skip for job=%s invoice=%s: %s",
            job_id, _invoice_label(invoice_data), top_error,
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

    return paths
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd agent && python -m pytest tests/test_tms_data/ -v
```

Expected: PASS — all existing tests + 2 new ones. (One pre-existing skip in `test_field_names_live.py` is fine.)

- [ ] **Step 5: Commit**

```bash
git add agent/services/tms_data/__init__.py agent/tests/test_tms_data/test_data_layer.py
git commit -m "feat(tms_data): add TMSDataLayer.get_all_documents for unconditional WO doc pull"
```

---

## Phase B — Rewrite send_qbo_api.py

### Task 3: Rewrite `_tms_fetch_and_upload_missing_docs`

Change behavior from "fetch types listed in `missing_docs`" to "fetch every TMS doc, dedupe against existing QBO attachments." New signature: takes `existing_attachments` (the `att_check` `attachments` list) instead of `missing_docs`.

**Files:**
- Modify: `agent/services/job_manager/send_qbo_api.py:28-95`
- Test: `agent/tests/test_send_qbo_api/test_tms_fetch_and_upload.py` (new)
- Create: `agent/tests/test_send_qbo_api/__init__.py` (empty)

- [ ] **Step 1: Create test directory**

```bash
mkdir -p agent/tests/test_send_qbo_api
touch agent/tests/test_send_qbo_api/__init__.py
```

- [ ] **Step 2: Write the failing tests**

Create `agent/tests/test_send_qbo_api/test_tms_fetch_and_upload.py`:

```python
"""Tests for SendQBOApiMixin._tms_fetch_and_upload_missing_docs after the M2 correction.

The cascade is now unconditional for non-OEC (no requiredDocs gate at the call site).
This module verifies the rewritten method body:
  - calls TMSDataLayer.get_all_documents (not get_documents)
  - dedupes downloaded docs against existing QBO attachments by docType
  - skips the "invoice" doc type
  - uploads only the missing doc types to QBO
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from services.job_manager.send_qbo_api import SendQBOApiMixin


def _make_mixin(tms_data, send_event=None):
    """Build a minimal mixin instance with the dependencies the method touches."""
    m = SendQBOApiMixin()
    m._tms_data = tms_data
    m._emit_send = AsyncMock() if send_event is None else send_event
    m._emit_failed_rows_changed = AsyncMock()
    return m


def _make_invoice(invoice_number="LM26040724F", container="HASU4865550"):
    inv = MagicMock()
    inv.invoice_number = invoice_number
    inv.container_number = container
    return inv


def _make_job():
    job = MagicMock()
    job.id = "job-correction"
    return job


@pytest.mark.asyncio
async def test_uploads_all_tms_docs_when_qbo_has_none(tmp_path):
    """No existing QBO attachments → every TMS doc is uploaded (POD + DO + IT + ITE)."""
    pod_path = tmp_path / "LM2604130046_pod.pdf"; pod_path.write_bytes(b"POD")
    do_path = tmp_path / "LM2604130046_do.pdf"; do_path.write_bytes(b"DO")
    it_path = tmp_path / "LM2604130046_it.pdf"; it_path.write_bytes(b"IT")
    ite_path = tmp_path / "LM2604130046_ite.pdf"; ite_path.write_bytes(b"ITE")

    tms_data = MagicMock()
    tms_data.get_failed_rows = MagicMock(return_value=[])
    tms_data.get_all_documents = AsyncMock(return_value={
        "pod": pod_path, "do": do_path, "it": it_path, "ite": ite_path,
    })

    api = MagicMock()
    api.upload_attachment = AsyncMock(return_value=True)

    mixin = _make_mixin(tms_data)
    uploaded = await mixin._tms_fetch_and_upload_missing_docs(
        job=_make_job(), invoice=_make_invoice(), api=api, invoice_id="123",
        verification={"found_container": "HASU4865550"}, temp_dir=tmp_path,
        invoice_data={"DocNumber": "LM26040724F"}, existing_attachments=[],
    )

    assert sorted(uploaded) == ["do", "it", "ite", "pod"]
    assert api.upload_attachment.await_count == 4


@pytest.mark.asyncio
async def test_dedupes_against_existing_qbo_attachments(tmp_path):
    """If QBO already has POD, only the other TMS docs are uploaded."""
    pod_path = tmp_path / "LM2604130046_pod.pdf"; pod_path.write_bytes(b"POD")
    do_path = tmp_path / "LM2604130046_do.pdf"; do_path.write_bytes(b"DO")

    tms_data = MagicMock()
    tms_data.get_failed_rows = MagicMock(return_value=[])
    tms_data.get_all_documents = AsyncMock(return_value={
        "pod": pod_path, "do": do_path,
    })

    api = MagicMock()
    api.upload_attachment = AsyncMock(return_value=True)

    existing = [{"docType": "pod", "fileName": "Existing POD.pdf"}]

    mixin = _make_mixin(tms_data)
    uploaded = await mixin._tms_fetch_and_upload_missing_docs(
        job=_make_job(), invoice=_make_invoice(), api=api, invoice_id="123",
        verification={}, temp_dir=tmp_path,
        invoice_data={"DocNumber": "LM26040724F"}, existing_attachments=existing,
    )

    assert uploaded == ["do"]
    assert api.upload_attachment.await_count == 1
    # The path actually uploaded was the DO, not the POD
    args, _ = api.upload_attachment.call_args
    assert args[1] == do_path


@pytest.mark.asyncio
async def test_skips_invoice_doc_type(tmp_path):
    """Even if TMS returns an 'invoice' type, it is NEVER uploaded back to QBO."""
    inv_path = tmp_path / "LM2604130046_invoice.pdf"; inv_path.write_bytes(b"INV")
    pod_path = tmp_path / "LM2604130046_pod.pdf"; pod_path.write_bytes(b"POD")

    tms_data = MagicMock()
    tms_data.get_failed_rows = MagicMock(return_value=[])
    tms_data.get_all_documents = AsyncMock(return_value={
        "invoice": inv_path, "pod": pod_path,
    })

    api = MagicMock()
    api.upload_attachment = AsyncMock(return_value=True)

    mixin = _make_mixin(tms_data)
    uploaded = await mixin._tms_fetch_and_upload_missing_docs(
        job=_make_job(), invoice=_make_invoice(), api=api, invoice_id="123",
        verification={}, temp_dir=tmp_path,
        invoice_data={"DocNumber": "LM26040724F"}, existing_attachments=[],
    )

    assert uploaded == ["pod"]
    assert api.upload_attachment.await_count == 1
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd agent && python -m pytest tests/test_send_qbo_api/test_tms_fetch_and_upload.py -v
```

Expected: 3 FAIL — the method's current signature requires `missing_docs`, and it calls `get_documents` not `get_all_documents`.

- [ ] **Step 4: Rewrite the method**

In `agent/services/job_manager/send_qbo_api.py`, replace the entire `_tms_fetch_and_upload_missing_docs` method (lines 28-95) with:

```python
    async def _tms_fetch_and_upload_missing_docs(
        self, job, invoice, api, invoice_id, verification, temp_dir,
        invoice_data: dict, existing_attachments: list[dict],
    ) -> list[str]:
        """Fetch every TMS document for the WO; upload to QBO any not already attached.

        TMS is the source of truth for supporting documents. QBO holds only the
        invoice PDF. We pull the full document list from TMS, skip the 'invoice'
        type (QBO owns it), dedupe against existing QBO attachments by docType,
        and upload the remainder.

        Per-doc download failures land in the data layer's FailedRowsTracker;
        the UI exposes them in the Failed Rows box with explicit Retry buttons.
        We never auto-fall-back to the browser — the user must opt in.
        """
        if not self._tms_data:
            logger.warning("TMSDataLayer not configured — skipping doc fetch for %s",
                           invoice.invoice_number)
            return []

        container = verification.get("found_container") or invoice.container_number or ""

        rows_before = len(self._tms_data.get_failed_rows(job.id))
        fetched = await self._tms_data.get_all_documents(
            job.id, invoice_data, temp_dir, source="api",
        )
        if len(self._tms_data.get_failed_rows(job.id)) > rows_before:
            await self._emit_failed_rows_changed(job, "added")

        # QBO owns the invoice PDF — never upload a TMS-side 'invoice' back.
        fetched = {dt: p for dt, p in fetched.items() if dt != "invoice"}
        if not fetched:
            return []

        await self._emit_send(job, "tms_fetching_docs", {
            "invoiceNumber": invoice.invoice_number,
            "containerNumber": container,
            "docTypes": list(fetched.keys()),
        })

        # Dedupe against existing QBO attachments by docType.
        existing_types = {
            (a.get("docType") or "").lower()
            for a in (existing_attachments or [])
        }

        uploaded: list[str] = []
        for dt, path in fetched.items():
            if not (path and path.exists()):
                continue
            if dt in existing_types:
                await self._emit_send(job, "tms_doc_already_on_qbo", {
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

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd agent && python -m pytest tests/test_send_qbo_api/ -v
```

Expected: PASS — 3 new tests.

- [ ] **Step 6: Commit**

```bash
git add agent/services/job_manager/send_qbo_api.py agent/tests/test_send_qbo_api/
git commit -m "refactor(send_qbo_api): rewrite TMS fetch — pull all docs, dedupe by docType"
```

---

### Task 4: Update the call site in `_send_qbo_api`

Remove the `missing_docs` derivation and the `if missing_docs and ...` gate. Replace with a simple "non-OEC + tms_data configured" gate. Pass `existing_attachments=all_attachments` to the rewritten method.

**Files:**
- Modify: `agent/services/job_manager/send_qbo_api.py:170-211`

- [ ] **Step 1: Apply the edit**

In `agent/services/job_manager/send_qbo_api.py`, replace lines 170-211 (the entire "Step 3b: Auto-fetch missing docs" block) with:

```python
        # Step 3b: TMS cascade — TMS is source of truth for supporting docs.
        # Run unconditionally for non-OEC (OEC does its own POD pull in send_oec.py).
        # `requiredDocs` is enforced AFTER this block as a pure block gate.
        temp_dir = None

        logger.info("Attachment check for %s: found=%s, missing=%s, tms_available=%s",
                     invoice.invoice_number, result.attachments_found,
                     result.attachments_missing, bool(self._tms_data))
        for a in all_attachments:
            logger.info("  -> '%s' classified as '%s'", a.get("fileName"), a.get("docType"))

        if self._tms_data and not is_oec:
            temp_dir = Path(tempfile.mkdtemp(prefix="ngl_docs_"))
            try:
                uploaded = await asyncio.wait_for(
                    self._tms_fetch_and_upload_missing_docs(
                        job, invoice, api, invoice_id, verification, temp_dir,
                        invoice_data=invoice_data, existing_attachments=all_attachments,
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

The line that previously read `missing_docs = [m for m in (result.attachments_missing or []) if (m or "").lower() != "invoice"]` is deleted entirely. The post-cascade enforcement gate at line 225 (`if required_docs and not att_check.get("allPresent")`) is **unchanged**.

- [ ] **Step 2: Run the full agent test suite to confirm nothing else broke**

```bash
cd agent && python -m pytest tests/ -v --ignore=tests/test_endpoints.py
```

Expected: PASS for all `tests/test_tms_data/`, `tests/test_failed_rows_endpoint.py`, `tests/test_send_qbo_api/`, `tests/test_job_manager/`, `tests/test_utils.py`. (`tests/test_endpoints.py` is the live-server suite that 401s without a running agent — pre-existing, ignore.)

- [ ] **Step 3: Commit**

```bash
git add agent/services/job_manager/send_qbo_api.py
git commit -m "fix(send_qbo_api): TMS cascade is unconditional for non-OEC; requiredDocs is enforcement only

The previous gate `if missing_docs and ...` short-circuited the cascade for
customers configured 'Send all attachments' (requiredDocs:[]). Empty
requiredDocs → empty missing_docs → cascade never fired. Real-world miss:
LM26040724F (APEXMA01) emailed without POD though TMS WO LM2604130046 had it.

The cascade now runs whenever the data layer is configured and the customer
isn't OEC. requiredDocs continues to enforce the post-cascade block at the
existing att_check.allPresent gate."
```

---

## Phase C — Spec doc update

### Task 5: Append correction note to spec

**Files:**
- Modify: `docs/superpowers/specs/2026-04-28-tms-data-layer-design.md` (append at end)

- [ ] **Step 1: Append the correction section**

At the bottom of the spec file, append:

```markdown
---

## 2026-04-30 Correction (shipped as v2.37.1)

**Bug:** The non-OEC cascade in `send_qbo_api.py` was gated on
`customer.requiredDocs` being non-empty. Customers configured "Send all
attachments" (saved as `requiredDocs: []`) had the cascade silently skipped.
Real-world miss: invoice `LM26040724F` (customer `APEXMA01`, WO
`LM2604130046`) was emailed without a POD though TMS had POD + DO + IT + ITE
ready.

**Architectural correction:** TMS is the source of truth for supporting
documents. QBO holds only the invoice PDF. The non-OEC send flow now:

1. Pulls invoice metadata + invoice PDF from QBO (unchanged).
2. Calls `TMSDataLayer.get_all_documents(...)` to download every
   `documents[].file_url` on the WO.
3. Skips the `invoice` doc type (QBO owns the invoice PDF).
4. Dedupes the rest against existing QBO attachments by `docType`.
5. Uploads any not yet on QBO so the QBO record is complete.
6. Emails the QBO invoice PDF + every QBO attachment (now including the
   newly-uploaded TMS docs).

**`requiredDocs` is enforcement only.** It blocks the send at the existing
post-cascade `att_check.allPresent` gate when a required doc is still missing
after TMS pull. It no longer gates *whether* the cascade runs.

**OEC path unchanged.** OEC already pulled POD from TMS unconditionally via
`send_oec.py`; this correction only makes non-OEC consistent.

**New public API:** `TMSDataLayer.get_all_documents(job_id, invoice_data,
dest_dir, source="api") -> dict[str, Path]`. Backed by `cascade.run_all_documents`.
Per-doc download failures are recorded in `FailedRowsTracker` so the existing
per-doc Retry buttons work without further changes.
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-04-28-tms-data-layer-design.md
git commit -m "docs(spec): note 2026-04-30 correction — cascade is unconditional for non-OEC"
```

---

## Phase D — Smoke test + ship (hands-on)

### Task 6: Smoke test against `LM26040724F`

This is hands-on and requires Joseph's machine (Electron app + real QBO/TMS auth).

- [ ] **Step 1: Restart the agent in dev mode against the freshly-built code**

```bash
cd agent && python main.py
```

- [ ] **Step 2: In the web app (or Electron dev mode), open Invoice Sender**

- [ ] **Step 3: Send a test-mode batch of one invoice: `LM26040724F`**

Test mode is the per-row approval gate. Watch the log for:
- `searching_invoice` → invoice found in QBO
- `verifying_invoice` → verification passes
- `checking_attachments` → see what QBO has (probably nothing pre-fix)
- `tms_fetching_docs` with docTypes including `pod`, `do`, `it`, `ite`
- `uploading_doc_to_qbo` for each missing doc type
- `doc_uploaded_to_qbo` for each
- Approval prompt shows attachments now including POD + DO + IT + ITE
- Send proceeds, email goes out

- [ ] **Step 4: Verify the email**

Check the recipient's inbox (or your own if you addressed it to yourself):
- Subject: `[NGL_INV] LM26040724F - Container#HASU4865550`
- Attachments: invoice PDF + POD + DO + IT + ITE (5 PDFs)

- [ ] **Step 5: Verify QBO record updated**

Open invoice `LM26040724F` in QuickBooks Online → Attachments tab. POD + DO + IT + ITE should now be linked to the invoice (in addition to whatever was there before).

If anything fails, do NOT ship — fix and re-test.

---

### Task 7: Ship pipeline (mandatory per CLAUDE.md)

- [ ] **Step 1: Bump version**

Edit `desktop/VERSION`:

```
2.37.1
```

(was `2.37.0`)

- [ ] **Step 2: Build agent + Electron installer**

```bash
cd desktop && build-all.bat
```

Expected: PyInstaller agent build succeeds, then `electron-builder` produces `desktop/dist/NGL Accounting Setup 2.37.1.exe` and `desktop/dist/latest.yml`.

- [ ] **Step 3: Commit version bump**

```bash
git add desktop/VERSION
git commit -m "chore: bump version to 2.37.1 for M2 correction release"
```

- [ ] **Step 4: Push to remote**

```bash
git push origin main
```

- [ ] **Step 5: Create GitHub release**

```bash
gh release create v2.37.1 \
  "desktop/dist/NGL Accounting Setup 2.37.1.exe" \
  "desktop/dist/latest.yml" \
  --title "v2.37.1 — TMS cascade unconditional for non-OEC" \
  --notes "Fixes invoice send flow so TMS documents (POD, DO, IT, ITE, etc.) are always pulled and attached for non-OEC customers — including customers configured 'Send all attachments' (previously skipped due to an inverted requiredDocs gate). Restart the app to auto-update."
```

- [ ] **Step 6: Verify release published**

```bash
gh release view v2.37.1
```

Confirm the installer `.exe` and `latest.yml` are listed under Assets.

- [ ] **Step 7: Update memory**

Edit `C:\Users\Joseph\.claude\projects\C--Users-Joseph-Desktop-NGL-ACCOUNTING-SERVICE\memory\project_tms_data_layer.md`:
- Update header status to note v2.37.1 shipped 2026-04-30 with the M2 correction.
- Add a "2026-04-30 Correction" subsection describing: bug (inverted requiredDocs gate), fix (unconditional cascade for non-OEC + new `get_all_documents`), files touched, and that M3 (Container Fetch) status is unchanged and still pending.

---

## Test summary at end of plan

After all tasks:
- Existing 95 + 1-skipped tests still pass
- 3 new tests in `test_cascade.py` (`run_all_documents`)
- 2 new tests in `test_data_layer.py` (`get_all_documents`)
- 3 new tests in `test_send_qbo_api/test_tms_fetch_and_upload.py`
- Total: 103 passing + 1 skipped

## Subagent model selection (per `feedback_opus_for_heavy_tasks.md`)

- **Opus** for Tasks 1, 2, 3 — invariant-heavy, multi-file, touches the cascade & send flow
- **Sonnet** for Tasks 4, 5 — mechanical edits (call-site swap + spec append)
- Tasks 6, 7 are hands-on with the user
