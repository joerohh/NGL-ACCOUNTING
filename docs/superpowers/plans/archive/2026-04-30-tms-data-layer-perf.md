# TMS Data Layer Performance Optimization Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:test-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking. One implementer subagent executes all 3 tasks with separate commits.

**Goal:** Bring v2.38.0's per-invoice send time back to ≤ v2.37.0's level (~15s) by skipping wasted TMS downloads + parallelizing the remaining ones.

**Architecture:**
1. **Pre-dedupe:** caller computes the set of doc types QBO already has (plus `"invoice"`) and passes it to the cascade as `skip_types`. The cascade never downloads those.
2. **Parallel TMS downloads:** `cascade.run_all_documents` switches the per-doc download loop from serial `await` to `asyncio.gather()`. Each download writes to its own path; no conflict.
3. **Parallel QBO uploads:** `_tms_fetch_and_upload_missing_docs` switches the upload loop to `asyncio.gather()`. QBO API rate limit is 500/min — 4 parallel uploads is safe.

**Tech stack:** Python 3.11+, asyncio, pytest.

---

## Task 1: cascade — accept `skip_types` + parallelize downloads

**Files:**
- Modify: `agent/services/tms_data/cascade.py` — `run_all_documents`
- Test: `agent/tests/test_tms_data/test_cascade.py` — append 2 new tests

- [ ] **Step 1: Write the failing tests**

Append to `agent/tests/test_tms_data/test_cascade.py`:

```python
@pytest.mark.asyncio
async def test_run_all_documents_skip_types_filters_before_download(tmp_path):
    """skip_types in the cascade prevents downloading those types entirely."""
    invoice = _qbo_invoice(wo="LM2604130046")
    wo = {
        "documents": [
            {"type_": "POD", "file_url": "https://tms/pod.pdf"},
            {"type_": "DO", "file_url": "https://tms/do.pdf"},
            {"type_": "IT", "file_url": "https://tms/it.pdf"},
        ],
    }
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value=wo)
    tms_api.download_document = AsyncMock(return_value=b"BYTES")

    paths, per_doc_errors, top_error = await run_all_documents(
        invoice, tmp_path, tms_api, skip_types={"pod", "do"},
    )

    assert top_error is None
    assert per_doc_errors == {}
    assert set(paths.keys()) == {"it"}
    # Only IT should have been downloaded — skipped types never hit the network
    assert tms_api.download_document.await_count == 1
    args, _ = tms_api.download_document.call_args
    assert args[0] == "https://tms/it.pdf"


@pytest.mark.asyncio
async def test_run_all_documents_downloads_in_parallel(tmp_path):
    """Multiple downloads run concurrently via asyncio.gather, not serially."""
    import asyncio

    invoice = _qbo_invoice(wo="LM2604130046")
    wo = {
        "documents": [
            {"type_": "POD", "file_url": "https://tms/pod.pdf"},
            {"type_": "DO", "file_url": "https://tms/do.pdf"},
            {"type_": "IT", "file_url": "https://tms/it.pdf"},
        ],
    }

    # Track concurrency: each download takes 50ms; if serial, total ≥ 150ms.
    # Parallel via gather → total ≤ ~80ms (with overhead).
    concurrent = 0
    max_concurrent = 0

    async def slow_download(url):
        nonlocal concurrent, max_concurrent
        concurrent += 1
        max_concurrent = max(max_concurrent, concurrent)
        await asyncio.sleep(0.05)
        concurrent -= 1
        return b"BYTES"

    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value=wo)
    tms_api.download_document = slow_download

    paths, per_doc_errors, top_error = await run_all_documents(invoice, tmp_path, tms_api)

    assert top_error is None
    assert set(paths.keys()) == {"pod", "do", "it"}
    assert max_concurrent >= 2, f"Expected concurrent downloads, got max={max_concurrent}"
```

- [ ] **Step 2: Run tests — verify both fail**

```bash
cd agent && python -m pytest tests/test_tms_data/test_cascade.py -v -k "skip_types or parallel"
```

Expected: 2 FAIL — `skip_types` is not a parameter, downloads are serial.

- [ ] **Step 3: Modify `run_all_documents`**

In `agent/services/tms_data/cascade.py`, replace the entire `run_all_documents` function with:

```python
async def run_all_documents(
    invoice_data: dict,
    dest_dir: Path,
    tms_api,
    *,
    skip_types: Optional[set[str]] = None,
) -> Tuple[dict[str, Path], dict[str, str], Optional[str]]:
    """Download every documents[].file_url on the WO. One get_work_order call.

    `skip_types` (lowercased doc types) bypasses both download and write for
    types the caller already has — used by send_qbo_api to skip QBO-present
    types and the QBO-owned 'invoice' type without paying the network cost.

    Returns (paths, per_doc_errors, top_error):
      - paths: dict of lowercased doc_type → saved Path (only successful downloads)
      - per_doc_errors: dict of lowercased doc_type → error message for failed downloads
      - top_error: non-None when get_work_order itself raised. None when:
                   * WO record was returned (per-doc errors recorded separately)
                   * WO returned 404 (treated as no-data, paths == {})
                   * No WO# extractable from QBO (returns "no WO#" top_error so the
                     data layer can record one summary failure if it wants to)
    """
    import asyncio

    wo_no = extract_wo_from_qbo(invoice_data)
    if not wo_no:
        return {}, {}, "Cannot fetch from TMS API: no WO# on QBO invoice"

    try:
        wo = await tms_api.get_work_order(wo_no)
    except Exception as e:
        logger.warning("TMS API get_work_order failed for %s: %s", wo_no, e)
        return {}, {}, str(e)

    if not wo:
        return {}, {}, None

    paths: dict[str, Path] = {}
    per_doc_errors: dict[str, str] = {}

    # Build the download work list, applying skip_types up-front.
    work: list[Tuple[str, str]] = []  # (doc_type, url)
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
        return paths, per_doc_errors, None

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

    results = await asyncio.gather(*[_download_one(dt, u) for dt, u in work])
    for doc_type, path, err in results:
        if err:
            per_doc_errors[doc_type] = err
        elif path:
            paths[doc_type] = path

    return paths, per_doc_errors, None
```

- [ ] **Step 4: Run tests — verify all green**

```bash
cd agent && python -m pytest tests/test_tms_data/test_cascade.py -v
```

Expected: PASS — all existing 5 tests still pass + 2 new ones (7 total in the run_all_documents block).

- [ ] **Step 5: Commit**

```bash
git add agent/services/tms_data/cascade.py agent/tests/test_tms_data/test_cascade.py
git commit -m "perf(tms_data): cascade.run_all_documents — parallel downloads + skip_types

Adds skip_types kwarg so callers can avoid downloading docs they already
have (QBO existing attachments + the QBO-owned 'invoice' type). Also
switches the per-doc download loop to asyncio.gather so unskipped docs
run concurrently — sub-second total instead of N×download time.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: data layer — forward `skip_types`

**Files:**
- Modify: `agent/services/tms_data/__init__.py` — `get_all_documents`
- Test: `agent/tests/test_tms_data/test_data_layer.py` — append 1 test

- [ ] **Step 1: Write the failing test**

Append to `agent/tests/test_tms_data/test_data_layer.py`:

```python
@pytest.mark.asyncio
async def test_get_all_documents_forwards_skip_types(tmp_path):
    """skip_types passed to get_all_documents must reach run_all_documents intact."""
    from services.tms_data import TMSDataLayer

    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value={
        "documents": [
            {"type_": "POD", "file_url": "https://tms/pod.pdf"},
            {"type_": "DO", "file_url": "https://tms/do.pdf"},
        ],
    })
    tms_api.download_document = AsyncMock(return_value=b"BYTES")

    layer = TMSDataLayer(qbo_api=MagicMock(), tms_api=tms_api, tms_browser=MagicMock())
    invoice = {
        "DocNumber": "LM26040724F",
        "CustomField": [{"Name": "NGL REF#", "StringValue": "LM2604130046/CUST"}],
    }

    paths = await layer.get_all_documents(
        "job-skip", invoice, tmp_path, skip_types={"pod"},
    )

    # Only DO downloaded (POD skipped before download)
    assert set(paths.keys()) == {"do"}
    assert tms_api.download_document.await_count == 1
```

- [ ] **Step 2: Run test — verify it fails**

```bash
cd agent && python -m pytest tests/test_tms_data/test_data_layer.py -v -k forwards_skip_types
```

Expected: FAIL — `get_all_documents` doesn't accept `skip_types`.

- [ ] **Step 3: Modify `get_all_documents`**

In `agent/services/tms_data/__init__.py`, locate the `get_all_documents` method. Update the signature and the `run_all_documents` call:

```python
    async def get_all_documents(
        self,
        job_id: str,
        invoice_data: dict,
        dest_dir: Path,
        source: Source = "api",
        *,
        skip_types: Optional[set[str]] = None,
    ) -> dict[str, Path]:
        """Fetch EVERY TMS document on the invoice's WO. Returns dict of doc_type → Path.

        Used by the non-OEC send flow: TMS is treated as the source of truth for
        supporting documents. Caller decides which to upload to QBO (e.g. dedupe
        against existing attachments).

        `skip_types` (lowercased) is forwarded to the cascade so docs the caller
        already has are skipped BEFORE the network download — significant perf
        win for repeat invoices.

        Per-doc download failures are recorded in FailedRowsTracker so the user
        can retry per-doc from the Failed Rows box. Top-level WO failures (no WO#,
        network error on get_work_order) are logged but NOT recorded as FailedRows
        — the user can re-trigger by rerunning the batch. 404 (WO not found) is
        treated as no-data and returns {} silently.

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
            invoice_data, dest_dir, cached, skip_types=skip_types,
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

The two changes from current state:
1. Added `*, skip_types: Optional[set[str]] = None` to signature
2. Pass `skip_types=skip_types` into the `run_all_documents` call

The `Optional` import: confirm it's already at the top of `__init__.py` (it is, from `from typing import Literal, Optional`).

- [ ] **Step 4: Run tests — verify all green**

```bash
cd agent && python -m pytest tests/test_tms_data/ -v
```

Expected: PASS — all 92 tests from before + 1 new = 93 passing, 1 skipped.

- [ ] **Step 5: Commit**

```bash
git add agent/services/tms_data/__init__.py agent/tests/test_tms_data/test_data_layer.py
git commit -m "perf(tms_data): TMSDataLayer.get_all_documents — forward skip_types

Adds skip_types kwarg, forwarded into cascade.run_all_documents so callers
that already know which doc types are present locally (or owned elsewhere)
can skip the TMS download for those types entirely.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: send_qbo_api — pre-dedupe + parallel uploads

**Files:**
- Modify: `agent/services/job_manager/send_qbo_api.py` — `_tms_fetch_and_upload_missing_docs`
- Test: `agent/tests/test_send_qbo_api/test_tms_fetch_and_upload.py` — update mocks + add 1 test

- [ ] **Step 1: Update existing tests + add new test**

The existing tests pass `existing_attachments=...` and assume the in-method dedupe filters duplicates. After this change, the cascade does the dedupe via `skip_types`, so the mock for `get_all_documents` needs to either (a) respect skip_types, or (b) we change the assertions to check that skip_types was forwarded correctly.

Use approach (b) — clearer intent.

In `agent/tests/test_send_qbo_api/test_tms_fetch_and_upload.py`, update the existing dedupe test:

```python
@pytest.mark.asyncio
async def test_dedupes_against_existing_qbo_attachments(tmp_path):
    """existing_attachments → skip_types passed to cascade; only missing docs returned/uploaded."""
    do_path = tmp_path / "LM2604130046_do.pdf"; do_path.write_bytes(b"DO")

    tms_data = MagicMock()
    tms_data.get_failed_rows = MagicMock(return_value=[])
    # Cascade already filtered POD via skip_types, returns only DO
    tms_data.get_all_documents = AsyncMock(return_value={"do": do_path})

    api = MagicMock()
    api.upload_attachment = AsyncMock(return_value=True)

    existing = [{"docType": "pod", "fileName": "Existing POD.pdf"}]

    mixin = _make_mixin(tms_data)
    uploaded = await mixin._tms_fetch_and_upload_missing_docs(
        job=_make_job(), invoice=_make_invoice(), api=api, invoice_id="123",
        verification={}, temp_dir=tmp_path,
        invoice_data={"DocNumber": "LM26040724F"}, existing_attachments=existing,
    )

    # Verify skip_types forwarded correctly (POD existing + invoice type)
    assert tms_data.get_all_documents.await_count == 1
    kwargs = tms_data.get_all_documents.await_args.kwargs
    assert kwargs.get("skip_types") == {"pod", "invoice"}

    assert uploaded == ["do"]
    assert api.upload_attachment.await_count == 1
```

Update `test_skips_invoice_doc_type` similarly:

```python
@pytest.mark.asyncio
async def test_invoice_type_added_to_skip_types(tmp_path):
    """Invoice doc type is always added to skip_types — QBO owns the invoice PDF."""
    pod_path = tmp_path / "LM2604130046_pod.pdf"; pod_path.write_bytes(b"POD")

    tms_data = MagicMock()
    tms_data.get_failed_rows = MagicMock(return_value=[])
    tms_data.get_all_documents = AsyncMock(return_value={"pod": pod_path})

    api = MagicMock()
    api.upload_attachment = AsyncMock(return_value=True)

    mixin = _make_mixin(tms_data)
    uploaded = await mixin._tms_fetch_and_upload_missing_docs(
        job=_make_job(), invoice=_make_invoice(), api=api, invoice_id="123",
        verification={}, temp_dir=tmp_path,
        invoice_data={"DocNumber": "LM26040724F"}, existing_attachments=[],
    )

    kwargs = tms_data.get_all_documents.await_args.kwargs
    assert "invoice" in kwargs.get("skip_types", set())

    assert uploaded == ["pod"]
```

Update `test_dedupe_handles_uppercase_qbo_doc_type` similarly:

```python
@pytest.mark.asyncio
async def test_uppercase_qbo_doc_type_lowercased_in_skip_types(tmp_path):
    """QBO docType uppercase is lowercased before being added to skip_types."""
    tms_data = MagicMock()
    tms_data.get_failed_rows = MagicMock(return_value=[])
    tms_data.get_all_documents = AsyncMock(return_value={})

    api = MagicMock()
    api.upload_attachment = AsyncMock(return_value=True)

    existing = [{"docType": "POD", "fileName": "Existing POD.pdf"}]

    mixin = _make_mixin(tms_data)
    await mixin._tms_fetch_and_upload_missing_docs(
        job=_make_job(), invoice=_make_invoice(), api=api, invoice_id="123",
        verification={}, temp_dir=tmp_path,
        invoice_data={"DocNumber": "LM26040724F"}, existing_attachments=existing,
    )

    kwargs = tms_data.get_all_documents.await_args.kwargs
    assert "pod" in kwargs.get("skip_types", set())
    assert "invoice" in kwargs.get("skip_types", set())
```

Add a NEW test for parallel uploads:

```python
@pytest.mark.asyncio
async def test_uploads_run_in_parallel(tmp_path):
    """Multiple uploads use asyncio.gather, not serial await."""
    import asyncio

    pod_path = tmp_path / "LM2604130046_pod.pdf"; pod_path.write_bytes(b"POD")
    do_path = tmp_path / "LM2604130046_do.pdf"; do_path.write_bytes(b"DO")
    it_path = tmp_path / "LM2604130046_it.pdf"; it_path.write_bytes(b"IT")

    tms_data = MagicMock()
    tms_data.get_failed_rows = MagicMock(return_value=[])
    tms_data.get_all_documents = AsyncMock(return_value={
        "pod": pod_path, "do": do_path, "it": it_path,
    })

    concurrent = 0
    max_concurrent = 0

    async def slow_upload(invoice_id, path):
        nonlocal concurrent, max_concurrent
        concurrent += 1
        max_concurrent = max(max_concurrent, concurrent)
        await asyncio.sleep(0.05)
        concurrent -= 1
        return True

    api = MagicMock()
    api.upload_attachment = slow_upload

    mixin = _make_mixin(tms_data)
    uploaded = await mixin._tms_fetch_and_upload_missing_docs(
        job=_make_job(), invoice=_make_invoice(), api=api, invoice_id="123",
        verification={}, temp_dir=tmp_path,
        invoice_data={"DocNumber": "LM26040724F"}, existing_attachments=[],
    )

    assert sorted(uploaded) == ["do", "it", "pod"]
    assert max_concurrent >= 2, f"Expected concurrent uploads, got max={max_concurrent}"
```

Keep existing tests `test_uploads_all_tms_docs_when_qbo_has_none`, `test_existing_attachments_none_does_not_crash`, `test_skips_path_that_does_not_exist` AS-IS — they still apply.

(The renamed tests replace `test_dedupes_against_existing_qbo_attachments`, `test_skips_invoice_doc_type`, `test_dedupe_handles_uppercase_qbo_doc_type`. So total count is 4 unchanged + 3 updated + 1 new = 8 tests in the file.)

- [ ] **Step 2: Run tests — verify failures match plan**

```bash
cd agent && python -m pytest tests/test_send_qbo_api/test_tms_fetch_and_upload.py -v
```

Expected: 4 FAIL or 5 FAIL — the renamed tests fail (their new assertions don't match current code), `test_uploads_run_in_parallel` fails (uploads are serial), and the currently-passing tests should still pass since we haven't changed behavior yet.

- [ ] **Step 3: Rewrite `_tms_fetch_and_upload_missing_docs`**

In `agent/services/job_manager/send_qbo_api.py`, replace the method body:

```python
    async def _tms_fetch_and_upload_missing_docs(
        self, job, invoice, api, invoice_id, verification, temp_dir,
        invoice_data: dict, existing_attachments: list[dict],
    ) -> list[str]:
        """Fetch every TMS document for the WO; upload to QBO any not already attached.

        TMS is the source of truth for supporting documents. QBO holds only the
        invoice PDF. We compute the dedupe set up front (existing QBO docTypes +
        the QBO-owned 'invoice' type) and pass it as `skip_types` to the cascade
        so docs we'd skip aren't downloaded over the network. Remaining downloads
        and the corresponding QBO uploads run in parallel via asyncio.gather.

        Per-doc download failures land in the data layer's FailedRowsTracker;
        the UI exposes them in the Failed Rows box with explicit Retry buttons.
        We never auto-fall-back to the browser — the user must opt in.
        """
        import asyncio

        if not self._tms_data:
            logger.warning("TMSDataLayer not configured — skipping doc fetch for %s",
                           invoice.invoice_number)
            return []

        container = verification.get("found_container") or invoice.container_number or ""

        # Compute skip_types up front: every QBO docType (lowercased) + the
        # QBO-owned 'invoice' type. Cascade skips downloads for these.
        skip_types = {
            (a.get("docType") or "").lower()
            for a in (existing_attachments or [])
            if a.get("docType")
        }
        skip_types.add("invoice")

        await self._emit_send(job, "tms_fetching_docs", {
            "invoiceNumber": invoice.invoice_number,
            "containerNumber": container,
            "docTypes": [],  # populated as docs come back via uploading_doc_to_qbo events
        })

        rows_before = len(self._tms_data.get_failed_rows(job.id))
        fetched = await self._tms_data.get_all_documents(
            job.id, invoice_data, temp_dir, source="api", skip_types=skip_types,
        )
        if len(self._tms_data.get_failed_rows(job.id)) > rows_before:
            await self._emit_failed_rows_changed(job, "added")

        if not fetched:
            return []

        # Filter out paths that don't exist on disk (defensive).
        valid_uploads = [(dt, p) for dt, p in fetched.items() if p and p.exists()]
        if not valid_uploads:
            return []

        async def _upload_one(dt: str, path) -> Optional[str]:
            await self._emit_send(job, "uploading_doc_to_qbo", {
                "invoiceNumber": invoice.invoice_number,
                "docType": dt,
                "fileName": path.name,
            })
            if await api.upload_attachment(invoice_id, path):
                await self._emit_send(job, "doc_uploaded_to_qbo", {
                    "invoiceNumber": invoice.invoice_number,
                    "docType": dt,
                    "fileName": path.name,
                })
                return dt
            await self._emit_send(job, "doc_upload_failed", {
                "invoiceNumber": invoice.invoice_number,
                "docType": dt,
                "error": "QBO upload API returned no result",
            })
            return None

        results = await asyncio.gather(*[_upload_one(dt, p) for dt, p in valid_uploads])
        return [r for r in results if r is not None]
```

The `Optional` import: confirm `from typing import Optional` is at the top of `send_qbo_api.py` — if not, add it.

- [ ] **Step 4: Run tests — verify all green**

```bash
cd agent && python -m pytest tests/test_send_qbo_api/ tests/test_tms_data/ tests/test_job_manager/ -v
```

Expected: PASS — all 8 tests in `test_tms_fetch_and_upload.py` (4 unchanged + 3 updated + 1 new), plus the existing 95+ tests.

- [ ] **Step 5: Commit**

```bash
git add agent/services/job_manager/send_qbo_api.py agent/tests/test_send_qbo_api/test_tms_fetch_and_upload.py
git commit -m "perf(send_qbo_api): pre-dedupe via skip_types + parallel QBO uploads

Computes the skip_types set (existing QBO docTypes + 'invoice') up front
and passes it to TMSDataLayer.get_all_documents, so duplicates are never
downloaded over the network. Remaining QBO upload_attachment calls run
in parallel via asyncio.gather. Together with the cascade-side parallel
download change, brings v2.38.0 send time back under v2.37.0's baseline.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## After all 3 commits

- [ ] **Verify nothing else broke** — run the full agent test suite excluding the live-server suite:

```bash
cd agent && python -m pytest tests/ -v --ignore=tests/test_endpoints.py 2>&1 | tail -10
```

Expected: 173 + 4 new tests = ~177 passing, 1 skipped.

- [ ] **Status to report:** DONE / DONE_WITH_CONCERNS / NEEDS_CONTEXT / BLOCKED.

After all 3 commits land, the controller will run a single combined spec+quality review, then ship as v2.39.0.

## Hard invariants (verify preserved)

1. The send flow's hard invariants (OEC two-email order, ar@ngltrans.net always CC'd, etc.) are unchanged — none of these changes touch the send order or recipient logic.
2. `requiredDocs` post-cascade enforcement at `if required_docs and not att_check.get("allPresent"):` (around line 232 of send_qbo_api.py) is UNCHANGED.
3. `tms_doc_already_on_qbo` SSE event is no longer emitted (it was per-doc-type before; now the dedupe happens before download). The handler in invoice-sender.js can stay — it just won't fire. Don't remove it; it's a cheap leftover.
4. The 'invoice' type is still never uploaded to QBO (now via skip_types up front instead of in-method filter).
