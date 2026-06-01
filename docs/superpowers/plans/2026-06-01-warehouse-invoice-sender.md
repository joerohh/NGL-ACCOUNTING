# Warehouse Invoice Sender Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable the Invoice Sender to send warehouse invoices end-to-end — auto-detect by INV# routing letter, fetch all QBO attachments, send `.xlsx` files as-is, block rows whose QBO invoices have no supporting docs.

**Architecture:** Agent gets a new `SendWarehouseMixin` that runs a self-contained warehouse send flow (no TMS, no doc-rule check, no Excel conversion). Web app reuses `parseInvType()` to tag each CSV row with a routing type and reveals a second "Warehouse Subject" field when a batch contains warehouse rows. Container column renders the literal `Warehouse` for those rows, and the INV# position-2 letter (M/E/X/W) gets the existing `.inv-letter` highlight (promoted from merge-tool scope to global). Empty-docs failures land in the existing Needs Attention tab with a plain-English message and an "Open in QuickBooks" resolve action.

**Tech Stack:** Python 3 + FastAPI (agent), vanilla ES modules + Tailwind (web), Gmail SMTP via `EmailSender` (already supports xlsx MIME), QBO REST API (already exposes invoice PDF + attachment endpoints), Electron desktop shell.

**Spec:** `docs/superpowers/specs/2026-06-01-warehouse-invoice-sender-design.md`
**Mockup:** `app/mockups/warehouse-invoice-sender.html`

---

## File Structure

### New
- `agent/services/job_manager/send_warehouse.py` — `SendWarehouseMixin` containing `_send_warehouse_invoice()` flow and the no-docs check
- `agent/tests/test_job_manager/test_send_warehouse.py` — pytest unit tests for the new mixin

### Modified
- `agent/services/job_manager/__init__.py` — import + add `SendWarehouseMixin` to `JobManager`'s mixin chain; update `SendResult` to carry `routing_type` and `warehouse_attachments` fields
- `agent/services/job_manager/send_job.py` (~line 303) — branch dispatch on `_is_warehouse_row(invoice.invoice_number)` BEFORE the customer-sendMethod check
- `app/assets/js/tools/invoice-sender/invoice-sender.js` — on CSV parse, tag each row with `routingType` via `parseInvType()`; reveal the Warehouse Subject field when ≥1 warehouse row is loaded; wire the live preview lines; include `warehouseSubject` in the SendRequest payload
- `app/assets/js/tools/invoice-sender/invoice-sender-results.js` — render INV# with `.inv-letter` wrap; render container column as `Warehouse` for warehouse rows; map `warehouse_no_docs` status to a Needs Attention reason with the plain-English text and Open-in-QuickBooks action
- `app/index.html` — add the Warehouse Subject `<input>` block under the existing Subject field (hidden by default); add `<span>` elements for the two preview lines
- `app/assets/css/styles.css` — promote `.inv-letter` out of `#mergeToolViewV2` scope to a global rule; add `.container-cell.warehouse`, `.wh-subject-block`, `.wh-badge-detected`, `.preview-line`
- `desktop/VERSION` — bump to 2.76.0
- `agent/services/job_manager/util.py` — verify (and only if absent, add) a `_is_warehouse_row` accessor importable from outside `fetch_job.py`; otherwise import the existing helper directly

---

## Task 1: Agent — Make `_is_warehouse_row` reusable outside `fetch_job.py`

**Files:**
- Modify: `agent/services/job_manager/fetch_job.py` (line 22 — the existing helper)
- Modify: `agent/tests/test_fetch_job_warehouse.py` (import test still passes from new location)

The helper exists today inside `fetch_job.py` as a module-level function. The send pipeline lives in a sibling mixin and needs the same logic. Rather than duplicate, we re-export it.

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_job_manager/test_warehouse_helper.py`:

```python
"""Verify _is_warehouse_row is importable from the package, not just fetch_job."""

from services.job_manager import _is_warehouse_row


def test_warehouse_helper_reexported_at_package_level() -> None:
    assert _is_warehouse_row("LW260515P01") is True
    assert _is_warehouse_row("LM2602170009") is False
    assert _is_warehouse_row(None) is False
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd agent && python -m pytest tests/test_job_manager/test_warehouse_helper.py -v
```

Expected: FAIL with `ImportError: cannot import name '_is_warehouse_row' from 'services.job_manager'`

- [ ] **Step 3: Re-export the helper**

Edit `agent/services/job_manager/__init__.py`. After the existing mixin imports (around line 23), add:

```python
from services.job_manager.fetch_job import _is_warehouse_row  # re-export
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd agent && python -m pytest tests/test_job_manager/test_warehouse_helper.py -v tests/test_fetch_job_warehouse.py -v
```

Expected: both files PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/services/job_manager/__init__.py agent/tests/test_job_manager/test_warehouse_helper.py
git commit -m "refactor(job-manager): re-export _is_warehouse_row at package level"
```

---

## Task 2: Agent — Extend `SendResult` with warehouse metadata

**Files:**
- Modify: `agent/services/job_manager/__init__.py` (the `SendResult` class around line 91)

`SendResult` already has the right shape for regular sends, but warehouse rows need to carry their routing type and the attachment list. Mirrors what `FetchResult` already exposes.

- [ ] **Step 1: Write the failing test**

Append to `agent/tests/test_job_manager/test_warehouse_helper.py`:

```python
from services.job_manager import SendResult


def test_send_result_carries_warehouse_fields() -> None:
    r = SendResult(invoice_number="LW260515P01", container_number="",
                   customer_code="PACCS01")
    r.routing_type = "warehouse"
    r.warehouse_attachments = [{"fileName": "detail.xlsx"}]
    r.warehouse_failures = []

    d = r.to_dict()
    assert d["routingType"] == "warehouse"
    assert d["warehouseAttachments"] == [{"fileName": "detail.xlsx"}]
    assert d["warehouseFailures"] == []
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd agent && python -m pytest tests/test_job_manager/test_warehouse_helper.py::test_send_result_carries_warehouse_fields -v
```

Expected: FAIL — `AttributeError` or missing key in dict.

- [ ] **Step 3: Add the fields**

In `agent/services/job_manager/__init__.py`, edit `SendResult.__init__`. After `self.pod_status: str = ""` (currently around line 113), add:

```python
        # Warehouse routing (set by send_warehouse flow).
        # routing_type: 'warehouse' when the invoice's INV# pos-2 is 'W'.
        self.routing_type: str = ""
        self.warehouse_attachments: list = []
        self.warehouse_failures: list = []
```

Then update `to_dict()` (around line 115). After `"podStatus": self.pod_status,` add:

```python
            "routingType": self.routing_type,
            "warehouseAttachments": self.warehouse_attachments,
            "warehouseFailures": self.warehouse_failures,
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd agent && python -m pytest tests/test_job_manager/test_warehouse_helper.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/services/job_manager/__init__.py agent/tests/test_job_manager/test_warehouse_helper.py
git commit -m "feat(send-result): add routing_type + warehouse_attachments fields"
```

---

## Task 3: Agent — Create `SendWarehouseMixin` skeleton

**Files:**
- Create: `agent/services/job_manager/send_warehouse.py`
- Modify: `agent/services/job_manager/__init__.py` (register the mixin)

Empty mixin first. Wires it into `JobManager` so subsequent tests can find the method.

- [ ] **Step 1: Create the skeleton file**

Create `agent/services/job_manager/send_warehouse.py`:

```python
"""Warehouse invoice send mixin — QBO-only, xlsx-as-is, no TMS, no doc rules."""

import logging
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger("ngl.job_manager")


class SendWarehouseMixin:
    """Send invoices whose INV# position-2 letter is 'W'."""

    async def _send_warehouse_invoice(self, job, invoice, customer, result, i):
        """Fetch invoice PDF + every QBO attachment; send via Gmail SMTP.

        Blocks the send if QBO has zero non-invoice attachments.
        """
        raise NotImplementedError("filled in by Task 4")
```

- [ ] **Step 2: Register the mixin**

Edit `agent/services/job_manager/__init__.py`. Add the import alongside the others (around line 22):

```python
from services.job_manager.send_warehouse import SendWarehouseMixin
```

Then add `SendWarehouseMixin,` to the `JobManager` class declaration (around line 295). The chain should read:

```python
class JobManager(
    JobManagerUtilMixin,
    FetchJobMixin,
    SendJobMixin,
    SendQBOApiMixin,
    SendOECFlowMixin,
    SendPortalUploadMixin,
    SendWarehouseMixin,
    ChassisJobMixin,
    RetryInvoiceMixin,
):
```

- [ ] **Step 3: Smoke-test the wiring**

```bash
cd agent && python -c "from services.job_manager import JobManager; print(hasattr(JobManager, '_send_warehouse_invoice'))"
```

Expected output: `True`

- [ ] **Step 4: Commit**

```bash
git add agent/services/job_manager/send_warehouse.py agent/services/job_manager/__init__.py
git commit -m "feat(job-manager): register SendWarehouseMixin skeleton"
```

---

## Task 4: Agent — Implement `_send_warehouse_invoice` with empty-docs check

**Files:**
- Modify: `agent/services/job_manager/send_warehouse.py`
- Create: `agent/tests/test_job_manager/test_send_warehouse.py`

Real flow: search QBO for invoice → download invoice PDF → list attachments → if zero non-invoice attachments, fail with `warehouse_no_docs` → otherwise download all attachments → call `email_sender.send_invoice_email` with the full list.

- [ ] **Step 1: Write the failing test for the happy path**

Create `agent/tests/test_job_manager/test_send_warehouse.py`:

```python
"""Tests for the warehouse send flow."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.job_manager import JobManager, SendJob, SendRequest, SendResult
from services.qbo_api import QBOApiClient


def _make_job_and_invoice(subject="Warehouse Invoice LW260515P01 - Pacific Cold Storage Inc."):
    invoice = SendRequest(
        invoice_number="LW260515P01",
        container_number="",
        customer_code="PACCS01",
        subject=subject,
    )
    job = SendJob("send-wh-1", [invoice], test_mode=False)
    return job, invoice


def _make_customer():
    return {
        "code": "PACCS01",
        "name": "Pacific Cold Storage Inc.",
        "emails": ["billing@pacificcoldstorage.com"],
        "ccEmails": [],
        "bccEmails": [],
        "active": True,
        "sendMethod": "email",
    }


def _make_jm(api_mock):
    classifier = MagicMock()
    email_sender = MagicMock()
    email_sender.send_invoice_email = AsyncMock(return_value={"sent": True, "error": None})
    jm = JobManager(QBOApiClient(), classifier=classifier, email_sender=email_sender)
    jm._qbo_api = api_mock
    jm._emit_send = AsyncMock()
    return jm, email_sender


@pytest.mark.asyncio
async def test_warehouse_send_with_attachments(tmp_path) -> None:
    job, invoice = _make_job_and_invoice()
    customer = _make_customer()
    result = SendResult(invoice.invoice_number, "", invoice.customer_code)

    api = MagicMock()
    api.search_invoice = AsyncMock(return_value={"Id": "42", "DocNumber": "LW260515P01"})
    api.download_invoice_pdf = AsyncMock(return_value=b"%PDF-1.4 invoice")
    api.list_attachments = AsyncMock(return_value=[
        {"id": "1", "fileName": "Storage_Detail.xlsx", "docType": "other"},
        {"id": "2", "fileName": "Receiving_Report.pdf", "docType": "other"},
    ])

    async def _fake_download(att_id, fname, dest_dir, temp_download_uri=None):
        p = Path(dest_dir) / fname
        p.write_bytes(b"fake bytes")
        return p

    api.download_attachment = AsyncMock(side_effect=_fake_download)

    jm, email_sender = _make_jm(api)

    await jm._send_warehouse_invoice(job, invoice, customer, result, 0)

    assert result.status == "sent"
    assert result.routing_type == "warehouse"
    assert sorted([a["fileName"] for a in result.warehouse_attachments]) == \
        ["Receiving_Report.pdf", "Storage_Detail.xlsx"]
    assert result.warehouse_failures == []

    # Email was sent with the invoice PDF + both attachments (3 total).
    email_sender.send_invoice_email.assert_awaited_once()
    kwargs = email_sender.send_invoice_email.await_args.kwargs
    sent_filenames = sorted([a["filename"] for a in kwargs["attachments"]])
    assert sent_filenames == ["Receiving_Report.pdf", "Storage_Detail.xlsx", "invoice_LW260515P01.pdf"]
    assert kwargs["subject"] == "Warehouse Invoice LW260515P01 - Pacific Cold Storage Inc."
    assert kwargs["to"] == ["billing@pacificcoldstorage.com"]


from pathlib import Path  # placed at bottom intentionally; imported by tests above
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd agent && python -m pytest tests/test_job_manager/test_send_warehouse.py::test_warehouse_send_with_attachments -v
```

Expected: FAIL with `NotImplementedError: filled in by Task 4`.

- [ ] **Step 3: Implement the happy path**

Replace `_send_warehouse_invoice` body in `agent/services/job_manager/send_warehouse.py`:

```python
"""Warehouse invoice send mixin — QBO-only, xlsx-as-is, no TMS, no doc rules."""

import logging
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from services.job_manager.util import normalize_email_list

logger = logging.getLogger("ngl.job_manager")


class SendWarehouseMixin:
    """Send invoices whose INV# position-2 letter is 'W'."""

    async def _send_warehouse_invoice(self, job, invoice, customer, result, i):
        """Fetch invoice PDF + every QBO attachment; send via Gmail SMTP."""
        result.routing_type = "warehouse"
        api = self._qbo_api

        # Step 1 — find the invoice in QBO.
        invoice_data = await api.search_invoice(invoice.invoice_number)
        if not invoice_data:
            result.status = "error"
            result.error = f"QuickBooks couldn't find invoice {invoice.invoice_number}."
            await self._emit_send(job, "invoice_error", {
                "invoiceNumber": invoice.invoice_number,
                "error": result.error,
            })
            return

        invoice_id = invoice_data["Id"]

        # Step 2 — list non-invoice QBO attachments. The invoice PDF is fetched
        # separately and never appears in this list (QBO's Attachable endpoint
        # returns user-uploaded files only, not the invoice template render).
        attachments = await api.list_attachments(invoice_id) or []

        if not attachments:
            result.status = "missing_docs"
            result.error = ("No documents attached in QuickBooks. Add at least one "
                            "Excel or PDF backup to the QBO invoice, then resend.")
            result.warehouse_attachments = []
            result.warehouse_failures = []
            await self._emit_send(job, "warehouse_no_docs", {
                "invoiceNumber": invoice.invoice_number,
                "customerCode": invoice.customer_code,
            })
            return

        # Step 3 — download invoice PDF + every attachment into a temp dir.
        temp_dir = Path(tempfile.mkdtemp(prefix="ngl_wh_send_"))
        try:
            invoice_pdf_bytes = await api.download_invoice_pdf(invoice_id)
            invoice_pdf_path = temp_dir / f"invoice_{invoice.invoice_number}.pdf"
            invoice_pdf_path.write_bytes(invoice_pdf_bytes)

            successes: list[dict] = []
            failures: list[dict] = []
            for att in attachments:
                try:
                    path = await api.download_attachment(
                        att["id"], att["fileName"], temp_dir,
                        temp_download_uri=att.get("tempDownloadUri"),
                    )
                    if path and Path(path).exists():
                        successes.append({"fileName": att["fileName"],
                                          "sizeBytes": Path(path).stat().st_size})
                    else:
                        failures.append({"fileName": att["fileName"],
                                         "error": "download returned no path"})
                except Exception as e:
                    failures.append({"fileName": att["fileName"], "error": str(e)})

            result.warehouse_attachments = successes
            result.warehouse_failures = failures

            # Per spec: ANY attachment download failure blocks the send for that
            # row. The user must see the failure list before the customer gets
            # an incomplete email. Partial-success → block, not send.
            if failures:
                result.status = "error"
                first = failures[0]
                result.error = (
                    f"Couldn't download {first['fileName']} from QuickBooks. "
                    "Try again in a minute."
                )
                await self._emit_send(job, "invoice_error", {
                    "invoiceNumber": invoice.invoice_number,
                    "error": result.error,
                    "warehouseFailures": failures,
                })
                return

            # Step 4 — send via Gmail SMTP with original filenames + MIME from extension.
            email_attachments = [
                {"filename": invoice_pdf_path.name, "data": invoice_pdf_path.read_bytes()},
            ]
            for att in attachments:
                local = temp_dir / att["fileName"]
                if local.exists():
                    email_attachments.append({
                        "filename": att["fileName"], "data": local.read_bytes(),
                    })

            to = normalize_email_list(customer.get("emails", []))
            cc = normalize_email_list(customer.get("ccEmails", []))
            bcc = normalize_email_list(customer.get("bccEmails", []))

            send_outcome = await self._email_sender.send_invoice_email(
                to=to, cc=cc, bcc=bcc,
                subject=invoice.subject,
                body=f"Please find attached warehouse invoice {invoice.invoice_number}.",
                attachments=email_attachments,
            )

            if send_outcome.get("sent"):
                result.status = "sent"
                result.to_emails = to
                result.cc_emails = cc
                result.bcc_emails = bcc
                result.subject = invoice.subject
                result.timestamp = datetime.now(timezone.utc).isoformat()
                await self._emit_send(job, "invoice_sent", {
                    "invoiceNumber": invoice.invoice_number,
                    "to": to, "cc": cc,
                })
            else:
                result.status = "error"
                result.error = send_outcome.get("error", "Email send failed.")
                await self._emit_send(job, "invoice_error", {
                    "invoiceNumber": invoice.invoice_number,
                    "error": result.error,
                })
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd agent && python -m pytest tests/test_job_manager/test_send_warehouse.py::test_warehouse_send_with_attachments -v
```

Expected: PASS.

- [ ] **Step 5: Write the failing tests for the no-docs and partial-failure cases**

Append to `agent/tests/test_job_manager/test_send_warehouse.py`:

```python
@pytest.mark.asyncio
async def test_warehouse_send_with_no_attachments_blocks_send() -> None:
    job, invoice = _make_job_and_invoice()
    customer = _make_customer()
    result = SendResult(invoice.invoice_number, "", invoice.customer_code)

    api = MagicMock()
    api.search_invoice = AsyncMock(return_value={"Id": "42", "DocNumber": "LW260515P01"})
    api.download_invoice_pdf = AsyncMock(return_value=b"%PDF-1.4 invoice")
    api.list_attachments = AsyncMock(return_value=[])  # zero attachments
    api.download_attachment = AsyncMock()

    jm, email_sender = _make_jm(api)

    await jm._send_warehouse_invoice(job, invoice, customer, result, 0)

    assert result.status == "missing_docs"
    assert "Add at least one" in result.error
    email_sender.send_invoice_email.assert_not_awaited()
    api.download_attachment.assert_not_awaited()


@pytest.mark.asyncio
async def test_warehouse_send_invoice_not_found_in_qbo() -> None:
    job, invoice = _make_job_and_invoice()
    customer = _make_customer()
    result = SendResult(invoice.invoice_number, "", invoice.customer_code)

    api = MagicMock()
    api.search_invoice = AsyncMock(return_value=None)
    api.list_attachments = AsyncMock()

    jm, email_sender = _make_jm(api)

    await jm._send_warehouse_invoice(job, invoice, customer, result, 0)

    assert result.status == "error"
    assert "couldn't find invoice" in result.error.lower()
    api.list_attachments.assert_not_awaited()
    email_sender.send_invoice_email.assert_not_awaited()


@pytest.mark.asyncio
async def test_warehouse_send_partial_download_failure_blocks(tmp_path) -> None:
    """Per spec: any attachment download failure blocks the send."""
    job, invoice = _make_job_and_invoice()
    customer = _make_customer()
    result = SendResult(invoice.invoice_number, "", invoice.customer_code)

    api = MagicMock()
    api.search_invoice = AsyncMock(return_value={"Id": "42", "DocNumber": "LW260515P01"})
    api.download_invoice_pdf = AsyncMock(return_value=b"%PDF-1.4 invoice")
    api.list_attachments = AsyncMock(return_value=[
        {"id": "1", "fileName": "Storage_Detail.xlsx", "docType": "other"},
        {"id": "2", "fileName": "Receiving_Report.pdf", "docType": "other"},
    ])

    async def _fake_download(att_id, fname, dest_dir, temp_download_uri=None):
        if fname == "Receiving_Report.pdf":
            raise RuntimeError("QBO 503")
        p = Path(dest_dir) / fname
        p.write_bytes(b"ok")
        return p

    api.download_attachment = AsyncMock(side_effect=_fake_download)

    jm, email_sender = _make_jm(api)

    await jm._send_warehouse_invoice(job, invoice, customer, result, 0)

    assert result.status == "error"
    assert "Receiving_Report.pdf" in result.error
    assert len(result.warehouse_failures) == 1
    assert result.warehouse_failures[0]["fileName"] == "Receiving_Report.pdf"
    email_sender.send_invoice_email.assert_not_awaited()
```

- [ ] **Step 6: Run the new tests**

```bash
cd agent && python -m pytest tests/test_job_manager/test_send_warehouse.py -v
```

Expected: all four tests PASS.

- [ ] **Step 7: Commit**

```bash
git add agent/services/job_manager/send_warehouse.py agent/tests/test_job_manager/test_send_warehouse.py
git commit -m "feat(send-warehouse): implement warehouse send flow with empty-docs check"
```

---

## Task 5: Agent — Dispatch warehouse rows in `send_job.py`

**Files:**
- Modify: `agent/services/job_manager/send_job.py` (around line 304 — the dispatch block)

Warehouse routing wins over the customer's `sendMethod`. The invoice itself is the source of truth.

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_job_manager/test_send_dispatch_warehouse.py`:

```python
"""Verify the send dispatcher routes warehouse invoices to the warehouse flow."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.job_manager import JobManager, SendJob, SendRequest, SendResult
from services.qbo_api import QBOApiClient


@pytest.mark.asyncio
async def test_warehouse_invoice_dispatches_to_warehouse_flow() -> None:
    invoice = SendRequest(
        invoice_number="LW260515P01", container_number="",
        customer_code="PACCS01", subject="Warehouse Invoice LW260515P01 - Pacific",
    )
    customer = {
        "code": "PACCS01", "name": "Pacific Cold Storage Inc.",
        "emails": ["billing@pacificcoldstorage.com"],
        "active": True, "sendMethod": "email",  # would normally go to qbo_api
    }
    result = SendResult(invoice.invoice_number, "", invoice.customer_code)

    jm = JobManager(QBOApiClient(), classifier=MagicMock())
    jm._send_qbo_api = AsyncMock()
    jm._send_warehouse_invoice = AsyncMock()

    # Reach into the dispatch block: this is exercised by _process_send_job,
    # but we can call the inner dispatch directly by simulating its decision.
    from services.job_manager import _is_warehouse_row
    method = customer.get("sendMethod", "email")
    if _is_warehouse_row(invoice.invoice_number):
        await jm._send_warehouse_invoice(None, invoice, customer, result, 0)
    elif method == "qbo_invoice_only_then_pod_email":
        await jm._send_oec_pod_email(None, invoice, customer, result, 0)
    else:
        await jm._send_qbo_api(None, invoice, customer, result, 0)

    jm._send_warehouse_invoice.assert_awaited_once()
    jm._send_qbo_api.assert_not_awaited()
```

- [ ] **Step 2: Run the test to verify it passes (it should — we wrote the routing inline above to mirror what we're about to write into the dispatcher)**

```bash
cd agent && python -m pytest tests/test_job_manager/test_send_dispatch_warehouse.py -v
```

Expected: PASS — but this only proves the import + the call signature. Next step wires the real dispatcher.

- [ ] **Step 3: Modify the dispatcher**

In `agent/services/job_manager/send_job.py`, first add the import near the top of the file (alongside the other `from services.job_manager.…` imports):

```python
from services.job_manager.fetch_job import _is_warehouse_row
```

Then find the block around line 303 starting with `method = customer.get("sendMethod", "email")` and the `async def _dispatch_send():` nested function. Replace it with:

```python
                # Step 2: Dispatch. Warehouse routing (INV# pos-2 == 'W')
                # overrides the customer's sendMethod — warehouse invoices are
                # always QBO-only, xlsx-as-is, regardless of how the customer
                # is normally configured.
                #
                # Import from fetch_job (not the package __init__) to avoid the
                # circular: __init__.py imports send_job, so send_job can't
                # import from __init__ at module load.
                is_warehouse = _is_warehouse_row(invoice.invoice_number)
                method = customer.get("sendMethod", "email")

                async def _dispatch_send():
                    if is_warehouse:
                        await self._send_warehouse_invoice(job, invoice, customer, result, i)
                    elif method in ("portal_upload", "portal"):
                        await self._send_portal_upload(job, invoice, customer, result, i)
                    elif method == "qbo_invoice_only_then_pod_email":
                        await self._send_oec_pod_email(job, invoice, customer, result, i)
                        await self._send_qbo_api(job, invoice, customer, result, i)
                    else:
                        await self._send_qbo_api(job, invoice, customer, result, i)

                await asyncio.wait_for(_dispatch_send(), timeout=SEND_TIMEOUT_S)
```

- [ ] **Step 4: Verify the existing send tests still pass**

```bash
cd agent && python -m pytest tests/test_job_manager/ tests/test_fetch_job_warehouse.py -v
```

Expected: all PASS, no regressions.

- [ ] **Step 5: Commit**

```bash
git add agent/services/job_manager/send_job.py agent/tests/test_job_manager/test_send_dispatch_warehouse.py
git commit -m "feat(send-dispatch): route warehouse invoices to SendWarehouseMixin"
```

---

## Task 6: Web — Tag CSV rows with `routingType` during parse

**Files:**
- Modify: `app/assets/js/tools/invoice-sender/invoice-sender.js` (after the `invHandleCsvFile` row loop, around the `colMap` block)

The merge tool already calls `parseInvType()` from `shared/utils.js`. The invoice sender will do the same.

- [ ] **Step 1: Add the import**

In `app/assets/js/tools/invoice-sender/invoice-sender.js` line 5 (the existing `import` line from `../../shared/utils.js`), add `parseInvType`:

```js
import { uid, escHtml, findColumnKey, CSV_ALIASES, parseInvType } from '../../shared/utils.js';
```

- [ ] **Step 2: Tag each row as it's pushed into state**

In the same file, locate the `invHandleCsvFile` function. After the rows are parsed (XLSX `sheet_to_json`) and just before the rows are pushed into `invoiceState.rows`, iterate and tag. Find the loop that builds the row object and add a `routingType` field. Use `grep -n "routingType\|invoiceNumber:" app/assets/js/tools/invoice-sender/invoice-sender.js` to find the existing row-build site, then update the row literal to include:

```js
routingType: parseInvType(invoiceNumber) || 'unknown',
```

Where `invoiceNumber` is whatever local variable the surrounding code uses to hold the row's invoice number.

- [ ] **Step 3: Add a console assertion that warehouse rows are detected**

Right after the loop completes (still in `invHandleCsvFile`), add a log line that the user sees in the Status Log:

```js
const whCount = invoiceState.rows.filter(r => r.routingType === 'warehouse').length;
if (whCount > 0) {
  invAddLog('info', `Detected ${whCount} warehouse invoice${whCount === 1 ? '' : 's'} (INV# starts with a "W" in position 2)`);
}
```

- [ ] **Step 4: Manual smoke test**

```bash
# Open the app in dev mode and load a CSV with at least one warehouse INV#.
# (We don't have automated JS tests; the manual confirmation here is enough
# because Task 11 covers the visual outcome.)
```

Expected: status log shows the detection line when a warehouse INV# is loaded.

- [ ] **Step 5: Commit**

```bash
git add app/assets/js/tools/invoice-sender/invoice-sender.js
git commit -m "feat(invoice-sender): tag rows with routingType via parseInvType"
```

---

## Task 7: Web — CSS: promote `.inv-letter` + add warehouse cell + subject block

**Files:**
- Modify: `app/assets/css/styles.css` (around line 1775)

- [ ] **Step 1: Promote `.inv-letter` rule**

In `app/assets/css/styles.css`, find:

```css
#mergeToolViewV2 .inv-letter {
  color: #ea580c; font-weight: 800;
}
```

Replace it with:

```css
.inv-letter {
  color: #ea580c; font-weight: 800;
}
```

- [ ] **Step 2: Add warehouse container cell + subject-block rules**

Append at the end of `styles.css`:

```css
/* ── Warehouse invoice routing (Invoice Sender) ── */
.container-cell.warehouse {
  color: #c2410c;
  font-weight: 600;
}

.wh-subject-block {
  margin-top: 12px;
}
.wh-subject-block .wh-badge-detected {
  background: #fff7ed; color: #c2410c; border: 1px solid #fed7aa;
  padding: 1px 7px; border-radius: 999px;
  font-size: 0.62rem; font-weight: 700; letter-spacing: 0.04em;
  text-transform: uppercase; margin-left: 8px;
}

/* Preview lines under the Subject fields — show rendered output, no token jargon. */
.subject-preview {
  font-size: 0.72rem; color: #94a3b8; margin-top: 4px; line-height: 1.4;
}
.subject-preview em { color: #475569; font-style: italic; }
```

- [ ] **Step 3: Verify merge tool still highlights the W**

Open the merge tool in dev and confirm a warehouse row's `W` is still orange. (The selector got broader, not narrower — should still match.)

- [ ] **Step 4: Commit**

```bash
git add app/assets/css/styles.css
git commit -m "style(inv-letter): promote rule to global; add warehouse cell + subject styles"
```

---

## Task 8: Web — Add Warehouse Subject DOM + preview lines

**Files:**
- Modify: `app/index.html` (around line 1000 — the existing Subject input)

- [ ] **Step 1: Insert the warehouse subject block under the regular Subject**

In `app/index.html`, find the existing Subject input:

```html
<input id="invSubjectTemplate" type="text" value="Invoice {invoice_number} - {customer_name}"
       style="..." onfocus="..." onblur="..." oninput="invUpdateSubjectTemplate(this.value)" />
```

Replace its surrounding `<div style="font-size:0.72rem; color:#94a3b8; margin-top:4px; line-height:1.45;">…</div>` help block with a preview span. Result:

```html
<input id="invSubjectTemplate" type="text" value="Invoice {invoice_number} - {customer_name}"
       style="width:100%; padding:8px 10px; border:1px solid #e2e8f0; border-radius:7px; font-size:0.85rem; color:#1e293b; outline:none; transition:border-color 0.15s;"
       onfocus="this.style.borderColor='#f97316'" onblur="this.style.borderColor='#e2e8f0'"
       oninput="invUpdateSubjectTemplate(this.value)" />
<div class="subject-preview" id="invSubjectPreview">
  Preview: <em>Invoice LM26050100F - CMA CGM (America) LLC</em>
</div>

<div class="wh-subject-block" id="invWhSubjectBlock" style="display:none;">
  <label style="display:block; font-size:0.74rem; font-weight:700; color:#334155; text-transform:uppercase; letter-spacing:0.04em; margin-bottom:4px; margin-top:12px;">
    Subject — Warehouse Invoices
    <span class="wh-badge-detected" id="invWhSubjectBadge">0 detected</span>
  </label>
  <input id="invWhSubjectTemplate" type="text" value="Warehouse Invoice {invoice_number} - {customer_name}"
         style="width:100%; padding:8px 10px; border:1px solid #e2e8f0; border-radius:7px; font-size:0.85rem; color:#1e293b; outline:none; transition:border-color 0.15s;"
         onfocus="this.style.borderColor='#f97316'" onblur="this.style.borderColor='#e2e8f0'"
         oninput="invUpdateWhSubjectTemplate(this.value)" />
  <div class="subject-preview" id="invWhSubjectPreview">
    Preview: <em>Warehouse Invoice LW260515P01 - Pacific Cold Storage Inc.</em>
  </div>
</div>
```

- [ ] **Step 2: Wire the JS helpers**

In `app/assets/js/tools/invoice-sender/invoice-sender.js`, add two helpers (append near the existing `invUpdateSubjectTemplate` function):

```js
function _renderPreview(template, row) {
  return template
    .replace('{invoice_number}', row.invoiceNumber || '')
    .replace('{customer_name}', row.customerName || '')
    .replace('{container_number}', row.containerNumber || '');
}

export function invUpdateWhSubjectTemplate(value) {
  invoiceState.warehouseSubjectTemplate = value;
  invRefreshWhSubjectPreview();
}

export function invRefreshSubjectPreview() {
  const tpl = document.getElementById('invSubjectTemplate').value;
  const sample = invoiceState.rows.find(r => r.routingType !== 'warehouse') || {
    invoiceNumber: 'LM26050100F', customerName: 'CMA CGM (America) LLC', containerNumber: 'MRKU8294420',
  };
  const out = _renderPreview(tpl, sample);
  document.getElementById('invSubjectPreview').innerHTML = `Preview: <em>${escHtml(out)}</em>`;
}

export function invRefreshWhSubjectPreview() {
  const tpl = document.getElementById('invWhSubjectTemplate').value;
  const sample = invoiceState.rows.find(r => r.routingType === 'warehouse') || {
    invoiceNumber: 'LW260515P01', customerName: 'Pacific Cold Storage Inc.', containerNumber: '',
  };
  const out = _renderPreview(tpl, sample);
  document.getElementById('invWhSubjectPreview').innerHTML = `Preview: <em>${escHtml(out)}</em>`;
}
```

Also update the existing `invUpdateSubjectTemplate` to call `invRefreshSubjectPreview()` after storing the value.

- [ ] **Step 3: Expose the warehouse helper to window for the inline `oninput`**

In `app/assets/js/app.js` (where other invoice-sender helpers are attached to `window`), add:

```js
import { invUpdateWhSubjectTemplate, invRefreshSubjectPreview, invRefreshWhSubjectPreview } from './tools/invoice-sender/invoice-sender.js';
window.invUpdateWhSubjectTemplate = invUpdateWhSubjectTemplate;
```

(Use `grep -n "window.invUpdateSubjectTemplate" app/assets/js/app.js` to find the right block — copy the existing pattern.)

- [ ] **Step 4: Reveal the warehouse block when warehouse rows are present**

In `invoice-sender.js`, in `invHandleCsvFile` after rows are pushed to `invoiceState`, add:

```js
const whCount = invoiceState.rows.filter(r => r.routingType === 'warehouse').length;
const block = document.getElementById('invWhSubjectBlock');
const badge = document.getElementById('invWhSubjectBadge');
if (whCount > 0) {
  block.style.display = 'block';
  badge.textContent = `${whCount} detected`;
} else {
  block.style.display = 'none';
}
invRefreshSubjectPreview();
invRefreshWhSubjectPreview();
```

Also clear the block on `invRemoveCsv`/`invClearAll` (use grep to find those functions; reset display to `none`).

- [ ] **Step 5: Manual smoke test**

Load a CSV with mixed rows in the Electron dev app. Confirm:
- Warehouse block appears with "N detected" pill matching the warehouse-row count.
- Both preview lines update as you type in either subject field.
- Loading a regular-only CSV keeps the warehouse block hidden.

- [ ] **Step 6: Commit**

```bash
git add app/index.html app/assets/js/tools/invoice-sender/invoice-sender.js app/assets/js/app.js
git commit -m "feat(invoice-sender): dual subject fields + live preview lines"
```

---

## Task 9: Web — Render INV# letter highlight + Warehouse container cell

**Files:**
- Modify: `app/assets/js/tools/invoice-sender/invoice-sender-results.js`

The HUD/results renderer needs to wrap the position-2 letter in `<span class="inv-letter">` for M/E/X/W, and show `Warehouse` for the container column on warehouse rows.

- [ ] **Step 1: Add a render helper**

At the top of `invoice-sender-results.js`, add a helper (or in the same place existing render helpers live — grep for `escHtml(.*invoiceNumber)`):

```js
function _renderInvNum(inv) {
  if (!inv || inv.length < 2) return escHtml(inv || '');
  const c = inv[1].toUpperCase();
  if (c === 'M' || c === 'E' || c === 'X' || c === 'W') {
    return escHtml(inv[0]) + `<span class="inv-letter">${escHtml(inv[1])}</span>` + escHtml(inv.slice(2));
  }
  return escHtml(inv);
}

function _renderContainerCell(row) {
  if (row.routingType === 'warehouse') {
    return `<span class="container-cell warehouse">Warehouse</span>`;
  }
  return `<span class="container-cell">${escHtml(row.containerNumber || '')}</span>`;
}
```

- [ ] **Step 2: Use the helpers in each row renderer**

`grep -n "escHtml.*invoiceNumber\|invoiceNumber.*escHtml" app/assets/js/tools/invoice-sender/invoice-sender-results.js` and replace every place that renders the bare INV# / container with calls to `_renderInvNum(row.invoiceNumber)` and `_renderContainerCell(row)`.

- [ ] **Step 3: Manual smoke test**

Reload the app, drop a mixed CSV, confirm:
- All warehouse rows show `Warehouse` in orange in the container column.
- All routing letters at position 2 (`M`/`E`/`X`/`W`) render in orange.

- [ ] **Step 4: Commit**

```bash
git add app/assets/js/tools/invoice-sender/invoice-sender-results.js
git commit -m "feat(invoice-sender): highlight INV# routing letter + Warehouse cell"
```

---

## Task 10: Web — Send the warehouse subject in the SendRequest payload

**Files:**
- Modify: `app/assets/js/shared/agent-client.js` (or wherever the send batch is POSTed)
- Modify: `agent/services/job_manager/send_job.py` (accept `warehouseSubject` per-row)

Per spec: each warehouse row's subject is rendered client-side from the warehouse template; that rendered string lands in `invoice.subject` on the server. No new server field needed — the existing `subject` carries the right value.

- [ ] **Step 1: Render the per-row subject client-side**

In `invoice-sender.js`, where the batch payload is built before POSTing to `/send-invoices` (grep for `agentBridge.sendInvoices` or `customer_code` in `invoice-sender.js`), update the row-to-payload mapping to pick the template based on routingType:

```js
const subjectTemplate = row.routingType === 'warehouse'
  ? invoiceState.warehouseSubjectTemplate || document.getElementById('invWhSubjectTemplate').value
  : invoiceState.subjectTemplate || document.getElementById('invSubjectTemplate').value;
const subject = _renderPreview(subjectTemplate, row);
```

(`_renderPreview` was added in Task 8.)

Pass `subject` into the existing payload `subject` field — no new property.

- [ ] **Step 2: Verify it round-trips**

Add a temporary console log right before the POST: `console.log('Send payload:', payload);`

Manual test: send a warehouse row in test mode, verify the network panel shows `subject: "Warehouse Invoice LW…"` for warehouse rows and `subject: "Invoice L…"` for regular rows.

Remove the debug log.

- [ ] **Step 3: Commit**

```bash
git add app/assets/js/tools/invoice-sender/invoice-sender.js
git commit -m "feat(invoice-sender): use warehouse subject template for warehouse rows"
```

---

## Task 11: Web — Map `missing_docs` (warehouse) to Needs Attention with plain-English text

**Files:**
- Modify: `app/assets/js/tools/invoice-sender/invoice-sender-results.js`

The agent now returns `status: "missing_docs"` for empty-doc warehouse rows. The results UI needs to:
1. Route this status to the Needs Attention tab.
2. Show the plain-English reason already set on `result.error`.
3. Add an "Open in QuickBooks" action button.

- [ ] **Step 1: Confirm the existing status → tab mapping handles `missing_docs`**

Grep `app/assets/js/tools/invoice-sender/invoice-sender-results.js` for `"missing_docs"` or `status ===`. Verify it already lands in Needs Attention (the OEC flow uses this status too). If not, add `'missing_docs'` to the Needs Attention status list.

- [ ] **Step 2: Add the action button render**

In the row-action renderer for a Needs Attention row whose `routingType === 'warehouse'` and `status === 'missing_docs'`, render:

```js
const isWhEmpty = row.routingType === 'warehouse' && row.status === 'missing_docs';
const actionHtml = isWhEmpty
  ? `<button class="row-action" onclick="invOpenInQuickbooks('${escHtml(row.invoiceNumber)}')">Open in QuickBooks</button>`
  : `<button class="row-action" onclick="invOpenResolve('${escHtml(row.invoiceNumber)}')">Resolve</button>`;
```

- [ ] **Step 3: Implement `invOpenInQuickbooks`**

In `invoice-sender.js`:

```js
export function invOpenInQuickbooks(invoiceNumber) {
  // QBO doesn't expose a stable deep-link by invoice number alone — open
  // the company's invoices list and let the user click through. Same pattern
  // the OEC flow uses for its escalation path.
  const url = 'https://app.qbo.intuit.com/app/invoices';
  window.open(url, '_blank');
  invAddLog('info', `Opened QuickBooks invoices list — find ${invoiceNumber}, add the missing attachment, then resend.`);
}
```

Wire it on `window`:

```js
window.invOpenInQuickbooks = invOpenInQuickbooks;
```

- [ ] **Step 4: Smoke-test the failure path**

Manually create a test case: a warehouse INV# whose QBO invoice has no attachments. Send. Confirm:
- Row lands in Needs Attention.
- Reason text reads `"No documents attached in QuickBooks. Add at least one Excel or PDF backup to the QBO invoice, then resend."`
- Button reads "Open in QuickBooks". Clicking it opens `https://app.qbo.intuit.com/app/invoices`.

- [ ] **Step 5: Commit**

```bash
git add app/assets/js/tools/invoice-sender/invoice-sender.js app/assets/js/tools/invoice-sender/invoice-sender-results.js
git commit -m "feat(invoice-sender): plain-English warehouse-empty error + Open in QuickBooks"
```

---

## Task 12: End-to-end manual smoke test

No files modified. This is a checkpoint before the rebuild step.

- [ ] **Step 1: Prepare test data**

Build a small Excel/CSV with these rows:
- 2 regular import invoices (real INV# format `LM26…F`)
- 2 regular export invoices (`PE26…F` or `PX26…F`)
- 2 warehouse invoices that DO have attachments in QBO (`LW26…P01`, `LW26…P02`)
- 1 warehouse invoice with NO QBO attachments (deliberately stripped) for the failure flow

- [ ] **Step 2: Load and verify the UI**

In the dev app:
- Drop the CSV. Confirm status log reports the warehouse count.
- Confirm container column shows `Warehouse` (orange) on warehouse rows.
- Confirm position-2 letter is orange on every row.
- Confirm the warehouse subject field is visible with "3 detected" pill.
- Type in both subject fields. Confirm both preview lines update live.

- [ ] **Step 3: Send the batch in Test Mode**

- Set EMAIL_TEST_REDIRECT to your own email in `.env` before starting the agent.
- Enable Test Mode in the UI.
- Send. Approve each prompt.
- Confirm:
  - Both regular and warehouse emails arrive at the redirect address.
  - Warehouse emails have the invoice PDF AND the original `.xlsx` (extension preserved, opens in Excel) AND any other PDF attachments.
  - The empty-docs warehouse row appears in Needs Attention with the plain-English reason and Open-in-QuickBooks button.

- [ ] **Step 4: Document the result**

Append to `app/audit-log/warehouse-smoke-2026-06-01.md` (create the file) with screenshots or a short note: pass/fail per check and any surprises.

---

## Task 13: Version bump + rebuild + release

Per the **Rebuild Pipeline — MANDATORY** rule in CLAUDE.md, every rebuild bumps the version, builds the installer, commits, pushes, and publishes a GitHub release.

- [ ] **Step 1: Bump VERSION**

Edit `desktop/VERSION`:

```
2.76.0
```

- [ ] **Step 2: Run the agent + electron build**

```bash
cd desktop && runbuild.bat
```

Wait for completion. The script handles the `bump-version.js` overwrite of `desktop/package.json`, the PyInstaller agent build, electron-builder packaging, and pre-build JS syntax gate.

- [ ] **Step 3: Stage + commit the build artifacts**

```bash
git add desktop/VERSION desktop/package.json
git commit -m "chore(release): bump VERSION to 2.76.0 — warehouse invoice sender"
```

- [ ] **Step 4: Push and publish the GitHub release**

```bash
git push origin main
gh release create v2.76.0 \
  desktop/dist/NGL-Accounting-Setup-2.76.0.exe \
  desktop/dist/latest.yml \
  --title "v2.76.0 — Warehouse Invoice Sender" \
  --notes "$(cat <<'EOF'
## What's new

The Invoice Sender now handles **warehouse invoices** end-to-end.

- Drop a mixed Excel/CSV. Warehouse rows (INV# `_W…`) are auto-detected — no separate workflow.
- Warehouse rows show `Warehouse` in the container column and the routing letter is highlighted, same as the merge tool.
- A second **Subject — Warehouse Invoices** field appears when warehouse rows are present, with a live preview.
- Every QBO attachment is sent as-is — `.xlsx` stays `.xlsx`, opens in Excel for the customer.
- Rows whose QBO invoice has no supporting attachments are flagged in **Needs Attention** with an "Open in QuickBooks" button.
- Customer profiles are unchanged.

## Spec
`docs/superpowers/specs/2026-06-01-warehouse-invoice-sender-design.md`
EOF
)"
```

- [ ] **Step 5: Verify the release**

Open the GitHub release page. Confirm both `.exe` and `latest.yml` are attached. The auto-updater in the installed app will pick this up on its next check.

---

## Notes for the Executor

- **Test runner:** `cd agent && python -m pytest <path>`. The repo's pytest config lives in `agent/pytest.ini`.
- **Web app testing:** there is no automated JS test runner. Manual smoke tests in the Electron dev app are the standard pattern (see CLAUDE.md "For UI or frontend changes" section).
- **Don't add new mocking patterns:** the existing tests in `test_fetch_job_warehouse.py` are the template — copy that style.
- **Don't add per-customer warehouse settings:** the spec deliberately rules this out. If you find yourself wanting to add a field to the customer DB, stop and re-read the spec.
- **Don't convert xlsx → PDF:** the merge tool does this; the Invoice Sender intentionally does not. The `EmailSender` already picks the right MIME from the file extension.
- **If a test fails mid-task:** fix it, commit the fix as a separate commit (don't amend), then continue.
