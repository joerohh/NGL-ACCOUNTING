# Invoice Sender UX Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Fix 1 (clearer errors + volume-friendly UI) and Fix 2 (in-app drop zones + retry with Claude verification) as a single v2.62.0 release of the Invoice Sender.

**Architecture:** Split `invoice-sender.js` into focused modules following the merge-v2 precedent: keep CSV/state/send-orchestration in the main file, move the post-send results view (tabs, alert banner, side panel diagnostic, drop zones, retry flow) into a new `invoice-sender-results.js`. Add a per-invoice retry endpoint to the agent that accepts uploaded files, runs Claude classification on ambiguous filenames, attaches files inline to the retry email, and fires off a non-blocking background QBO upload so the file persists without slowing the send.

**Tech Stack:** Vanilla JS ES modules · pdf-lib (existing) · Claude Haiku (existing classifier extended for JPG/PNG/HEIC) · FastAPI multipart upload · existing QBO API client.

**Locked decisions** (from 2026-05-14 user confirmation):
1. **Auto-advance** to next failure on retry success (no click button).
2. **Background QBO upload** after retry — file goes to email synchronously, QBO upload happens in a fire-and-forget task. If QBO upload fails, log it but don't fail the retry.
3. **Skip button** — silent (no reason prompt) — open question from spec resolved.
4. **Shipping order** — Fix 1 + Fix 2 ship together as v2.62.0 (Fix 2 includes Fix 1's results view; splitting would mean rewriting the same view twice).

---

## File Structure

**Frontend changes:**
- Modify: `app/index.html` — add results-view container + side-panel scaffolding inside `#invoiceSenderView`
- Modify: `app/assets/css/styles.css` — append ~700 lines: tabs, alert banner, status badges (POD Missing / BOL Missing / QBO Error etc), side panel layout, drop zones (empty/uploading/verifying/attached/mismatch states), retry progress, success stage
- Modify: `app/assets/js/tools/invoice-sender/invoice-sender.js` — slim to ~1200 lines; keep CSV upload, state, send orchestration, SSE dispatcher entry. Re-export hooks for results module.
- Create: `app/assets/js/tools/invoice-sender/invoice-sender-results.js` — ~700 lines: post-send results view (tabs, banner, table, action column, side panel, drop zones, retry orchestration, auto-advance, bulk retry)
- Modify: `app/assets/js/shared/state.js` — extend `sendState` with `retryState` map per row (attached files, drop-zone stages, panel stage)
- Modify: `app/assets/js/shared/agent-client.js` (or equivalent in agent-bridge) — add `retryInvoice(invoiceNumber, files)` call

**Backend changes:**
- Modify: `agent/services/claude_classifier.py` — accept JPG/PNG/HEIC via Anthropic vision API (today rejects non-PDF at `_validate_pdf`)
- Create: `agent/services/job_manager/retry_job.py` — single-invoice retry job that mirrors send_qbo_api but accepts pre-attached files + flags background QBO upload
- Modify: `agent/services/job_manager/send_qbo_api.py` — extract `_assemble_send()` helper so retry can reuse the email-attach + send logic without re-running the requiredDocs gate
- Modify: `agent/services/qbo_api/attachments.py` — verify `upload_attachment()` supports the new path; add `attach_to_invoice_async()` wrapper that swallows + logs errors for fire-and-forget use
- Create: `agent/routers/retry.py` — new router with `POST /jobs/retry-invoice` (multipart: invoice JSON + uploaded files) and `POST /jobs/verify-file` (single file → Claude classification result)
- Modify: `agent/main.py` — wire `retry.py` router
- Modify: `agent/services/job_manager/__init__.py` — export `start_retry_invoice_job()`

**Tests:**
- Create: `agent/tests/test_claude_classifier_images.py` — image classification accept-path tests
- Create: `agent/tests/test_retry_job.py` — retry-flow tests (success, mismatch, background upload failure tolerance)
- Create: `agent/tests/test_retry_endpoint.py` — endpoint smoke tests (multipart parsing, file forwarded to classifier, retry job started)

---

## Task 1: Setup — version + branch + state shape

**Files:**
- Modify: `desktop/VERSION`
- Modify: `app/assets/js/shared/state.js`

- [ ] **Step 1: Bump version**

Replace contents of `desktop/VERSION` with:
```
2.62
```

- [ ] **Step 2: Extend `sendState` for retry tracking**

In `app/assets/js/shared/state.js`, the existing `sendState` block (lines 43-56) gets a new field. Locate the `sendState = { … }` initializer and add `retry: {}` plus `currentTab: 'needs-attention'` and `activePanelInvoiceId: null`:

```js
sendState = {
  jobId: null,
  isRunning: false,
  testMode: false,
  results: {},
  sent: 0,
  skipped: 0,
  errors: 0,
  mismatches: 0,
  missingDocs: 0,
  startTime: null,
  completedCount: 0,
  eventSource: null,
  // NEW for v2.62 results view:
  currentTab: 'needs-attention',          // 'needs-attention' | 'sent' | 'all'
  activePanelInvoiceId: null,
  retry: {},                              // { [invoiceNumber]: { panelStage, attached: { POD: {name, size, stage, detectedAs}, … } } }
};
```

- [ ] **Step 3: Commit**

```bash
git add desktop/VERSION app/assets/js/shared/state.js
git commit -m "chore(invoice-sender): bump to v2.62 + state shape for retry/results"
```

---

## Task 2: Backend — Claude classifier accepts images (TDD)

**Files:**
- Test: `agent/tests/test_claude_classifier_images.py`
- Modify: `agent/services/claude_classifier.py`

- [ ] **Step 1: Write failing test for image acceptance**

Create `agent/tests/test_claude_classifier_images.py`:

```python
"""Image classification path — JPG/PNG/HEIC should not be rejected by validator.

Today the classifier rejects non-PDF at _validate_pdf. Fix 2 needs to accept
image formats because Lorena will photograph PODs with her phone.
"""
import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from agent.services.claude_classifier import ClaudeClassifier, ClassificationResult


@pytest.fixture
def jpg_bytes():
    # Minimal valid JPEG header
    return b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 100 + b"\xff\xd9"


@pytest.fixture
def png_bytes():
    # Minimal valid PNG header
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


def test_jpg_passes_validation(tmp_path, jpg_bytes):
    p = tmp_path / "pod.jpg"
    p.write_bytes(jpg_bytes)
    clf = ClaudeClassifier(api_key="sk-test")
    # _validate_image should return True for JPEG
    assert clf._validate_image(p) is True


def test_png_passes_validation(tmp_path, png_bytes):
    p = tmp_path / "scan.png"
    p.write_bytes(png_bytes)
    clf = ClaudeClassifier(api_key="sk-test")
    assert clf._validate_image(p) is True


def test_pdf_still_passes_validation(tmp_path):
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-1.4\n" + b"\x00" * 100)
    clf = ClaudeClassifier(api_key="sk-test")
    # Existing path still works
    assert clf._validate_pdf(p) is True


def test_classify_routes_jpg_to_vision_api(tmp_path, jpg_bytes):
    p = tmp_path / "pod.jpg"
    p.write_bytes(jpg_bytes)
    clf = ClaudeClassifier(api_key="sk-test")

    with patch.object(clf, "_call_claude_vision", new=AsyncMock(return_value={
        "doc_type": "pod", "confidence": 0.95, "container_hint": "TRHU4593053"
    })) as mock_call:
        result = asyncio.run(clf.classify(p))
        mock_call.assert_called_once()
        assert result.doc_type == "pod"
        assert result.confidence == 0.95
        assert result.skipped_api is False
```

- [ ] **Step 2: Run test — should fail (no `_validate_image` method)**

```bash
cd "C:/Users/Joseph/Desktop/NGL ACCOUNTING SERVICE"
agent/venv/Scripts/python.exe -m pytest agent/tests/test_claude_classifier_images.py -v
```
Expected: `AttributeError: 'ClaudeClassifier' object has no attribute '_validate_image'`

- [ ] **Step 3: Add `_validate_image` + image-aware `classify` path**

In `agent/services/claude_classifier.py`, after the existing `_validate_pdf` method (around line 104), add:

```python
def _validate_image(self, path: Path) -> bool:
    """Check that path is a real JPEG/PNG/HEIC by sniffing magic bytes."""
    try:
        with open(path, "rb") as f:
            head = f.read(12)
    except OSError:
        return False
    # JPEG: ff d8 ff
    if head.startswith(b"\xff\xd8\xff"):
        return True
    # PNG: 89 50 4e 47 0d 0a 1a 0a
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    # HEIC: bytes 4-12 contain "ftypheic" / "ftypheix" / "ftypmif1"
    if len(head) >= 12 and head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"mif1", b"msf1"):
        return True
    return False
```

Then update `classify()` (around line 173). Locate the validation block:

```python
        if not self._validate_pdf(pdf_path):
            return ClassificationResult(
                doc_type="other", confidence=0.0, needs_review=True
            )
```

Replace with format-aware routing:

```python
        suffix = pdf_path.suffix.lower()
        if suffix == ".pdf":
            if not self._validate_pdf(pdf_path):
                return ClassificationResult(
                    doc_type="other", confidence=0.0, needs_review=True
                )
            api_response = await self._call_claude_pdf(pdf_path)
        elif suffix in (".jpg", ".jpeg", ".png", ".heic", ".heif"):
            if not self._validate_image(pdf_path):
                return ClassificationResult(
                    doc_type="other", confidence=0.0, needs_review=True
                )
            api_response = await self._call_claude_vision(pdf_path)
        else:
            return ClassificationResult(
                doc_type="other", confidence=0.0, needs_review=True
            )
```

Rename the existing API caller to `_call_claude_pdf` (its current logic stays the same). Add new `_call_claude_vision`:

```python
async def _call_claude_vision(self, image_path: Path) -> dict:
    """Classify a JPEG/PNG/HEIC image via Anthropic vision API."""
    import base64
    suffix = image_path.suffix.lower()
    media_type = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".heic": "image/heic", ".heif": "image/heic",
    }[suffix]
    with open(image_path, "rb") as f:
        b64 = base64.standard_b64encode(f.read()).decode("utf-8")

    response = await self._client.messages.create(
        model=self.model,
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": b64},
                },
                {"type": "text", "text": self.classification_prompt},
            ],
        }],
    )
    return self._parse_classification_response(response)
```

Refactor parsing into `_parse_classification_response(response)` so both PDF + vision paths reuse it. Extract from existing PDF code (look for the JSON parsing block in the old `_call_claude` method).

- [ ] **Step 4: Run tests — should pass**

```bash
agent/venv/Scripts/python.exe -m pytest agent/tests/test_claude_classifier_images.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Verify existing classifier tests still pass**

```bash
agent/venv/Scripts/python.exe -m pytest agent/tests/ -v -k classifier
```
Expected: all existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add agent/services/claude_classifier.py agent/tests/test_claude_classifier_images.py
git commit -m "feat(classifier): accept JPG/PNG/HEIC via Anthropic vision API"
```

---

## Task 3: Backend — extract `_assemble_and_send_email` helper for reuse

**Files:**
- Modify: `agent/services/job_manager/send_qbo_api.py`

- [ ] **Step 1: Locate the email-assembly block**

In `agent/services/job_manager/send_qbo_api.py`, find lines 323-380 (download_invoice_pdf, build email_attachments, send via email_sender, set result.status). The block starts at the `download_invoice_pdf` call and ends after `email_sender.send_invoice_email(...)`.

- [ ] **Step 2: Extract into a reusable helper**

Above `_send_qbo_api`, add:

```python
async def _assemble_and_send_email(
    job,
    invoice,
    customer,
    result,
    api,
    *,
    extra_attachments: list[dict] | None = None,
    skip_qbo_attachments: bool = False,
) -> dict:
    """Build the email payload (invoice PDF + QBO attachments + optional extras) and send.

    Args:
        extra_attachments: additional pre-built attachments [{filename, data}] for retry flow
        skip_qbo_attachments: True to skip downloading from QBO (retry-with-files case)

    Returns: dict with {sent: bool, error: str|None, attachments_sent: int}
    """
    email_attachments = []

    # Invoice PDF (always)
    invoice_pdf = await api.download_invoice_pdf(invoice.invoice_id)
    if invoice_pdf:
        email_attachments.append({
            "filename": f"{invoice.invoice_number}.pdf",
            "data": invoice_pdf,
        })

    # QBO attachments (unless caller is supplying their own)
    if not skip_qbo_attachments:
        attachments = await api.list_attachments(invoice.invoice_id)
        for att in attachments:
            data = await api.download_attachment(att)
            if data:
                email_attachments.append({"filename": att.get("FileName", "doc.pdf"), "data": data})

    # Caller-supplied attachments (retry flow)
    if extra_attachments:
        email_attachments.extend(extra_attachments)

    # Send
    cc_emails = ["ar@ngltrans.net"] + normalize_email_list(customer.get("ccEmails", []))
    if invoice.do_sender_email:
        cc_emails.append(invoice.do_sender_email)

    send_result = await email_sender.send_invoice_email(
        to=customer["primaryEmail"],
        cc=cc_emails,
        subject=invoice.subject or f"Invoice {invoice.invoice_number}",
        body=email_template.render(invoice, customer),
        attachments=email_attachments,
    )
    return {
        "sent": send_result.get("sent", False),
        "error": send_result.get("error"),
        "attachments_sent": len(email_attachments),
    }
```

- [ ] **Step 3: Replace inline block in `_send_qbo_api` with call to helper**

In `_send_qbo_api` (around lines 323-380), replace the email-assembly block with:

```python
        send = await _assemble_and_send_email(job, invoice, customer, result, api)
        if send["sent"]:
            result.status = "sent"
            result.sent_at = datetime.now(timezone.utc).isoformat()
            job.emit("invoice_sent", invoiceNumber=invoice.invoice_number, sentAt=result.sent_at)
        else:
            result.status = "error"
            result.error_message = send["error"]
            job.emit("invoice_error", invoiceNumber=invoice.invoice_number, error=send["error"])
```

Keep the existing surrounding logic (skip_types, requiredDocs gate, OEC paths) intact — only the email-build-and-send portion moves.

- [ ] **Step 4: Run send tests to verify no regression**

```bash
agent/venv/Scripts/python.exe -m pytest agent/tests/test_job_manager/ -v
```
Expected: all existing send job tests still pass.

- [ ] **Step 5: Commit**

```bash
git add agent/services/job_manager/send_qbo_api.py
git commit -m "refactor(send): extract _assemble_and_send_email for retry reuse"
```

---

## Task 4: Backend — retry job (TDD)

**Files:**
- Create: `agent/services/job_manager/retry_job.py`
- Test: `agent/tests/test_retry_job.py`
- Modify: `agent/services/job_manager/__init__.py`

- [ ] **Step 1: Write failing test for retry job basics**

Create `agent/tests/test_retry_job.py`:

```python
"""Single-invoice retry job — accepts pre-uploaded files, sends, schedules background QBO upload."""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.services.job_manager.retry_job import start_retry_invoice_job


@pytest.fixture
def fake_invoice():
    return {
        "invoice_number": "LM26040864F",
        "invoice_id": "qbo-123",
        "container_number": "TRHU4593053",
        "customer_code": "APEXMA01",
        "subject": "Invoice LM26040864F - APEXMA01",
        "do_sender_email": None,
    }


@pytest.fixture
def fake_customer():
    return {
        "code": "APEXMA01", "name": "APEX MARITIME",
        "primaryEmail": "ap@apex.com",
        "ccEmails": [], "requiredDocs": ["pod"], "sendMethod": "email",
    }


def test_retry_succeeds_with_pre_attached_file(tmp_path, fake_invoice, fake_customer):
    pod = tmp_path / "pod.pdf"
    pod.write_bytes(b"%PDF-1.4\nfake")

    with patch("agent.services.job_manager.retry_job._assemble_and_send_email",
               new=AsyncMock(return_value={"sent": True, "error": None, "attachments_sent": 2})), \
         patch("agent.services.job_manager.retry_job._background_qbo_upload",
               new=AsyncMock()) as mock_bg, \
         patch("agent.services.job_manager.retry_job.get_customer",
               return_value=fake_customer):
        result = asyncio.run(start_retry_invoice_job(
            invoice=fake_invoice,
            files=[{"slot": "POD", "path": str(pod), "verified_by": "filename"}],
        ))
        assert result["status"] == "sent"
        # Background upload was scheduled (fire-and-forget)
        mock_bg.assert_called_once()


def test_retry_bypasses_required_docs_gate(tmp_path, fake_invoice, fake_customer):
    """Retry trusts the user's uploaded file — no requiredDocs gate."""
    pod = tmp_path / "pod.pdf"
    pod.write_bytes(b"%PDF-1.4\nfake")

    with patch("agent.services.job_manager.retry_job._assemble_and_send_email",
               new=AsyncMock(return_value={"sent": True, "error": None, "attachments_sent": 1})), \
         patch("agent.services.job_manager.retry_job._background_qbo_upload",
               new=AsyncMock()), \
         patch("agent.services.job_manager.retry_job.get_customer",
               return_value=fake_customer):
        result = asyncio.run(start_retry_invoice_job(
            invoice=fake_invoice,
            files=[{"slot": "POD", "path": str(pod), "verified_by": "filename"}],
        ))
        # Even though requiredDocs=["pod"], retry doesn't run the gate — caller supplied file
        assert result["status"] == "sent"


def test_retry_background_qbo_upload_failure_does_not_fail_retry(tmp_path, fake_invoice, fake_customer):
    """If QBO upload fails, retry result stays 'sent' — email already went out."""
    pod = tmp_path / "pod.pdf"
    pod.write_bytes(b"%PDF-1.4\nfake")

    async def failing_upload(*a, **kw):
        raise RuntimeError("QBO 500")

    with patch("agent.services.job_manager.retry_job._assemble_and_send_email",
               new=AsyncMock(return_value={"sent": True, "error": None, "attachments_sent": 1})), \
         patch("agent.services.job_manager.retry_job._background_qbo_upload",
               new=failing_upload), \
         patch("agent.services.job_manager.retry_job.get_customer",
               return_value=fake_customer):
        result = asyncio.run(start_retry_invoice_job(
            invoice=fake_invoice,
            files=[{"slot": "POD", "path": str(pod), "verified_by": "filename"}],
        ))
        # Email sent — retry success regardless of background upload outcome
        assert result["status"] == "sent"
```

- [ ] **Step 2: Run test — should fail (module doesn't exist)**

```bash
agent/venv/Scripts/python.exe -m pytest agent/tests/test_retry_job.py -v
```
Expected: `ModuleNotFoundError: No module named 'agent.services.job_manager.retry_job'`

- [ ] **Step 3: Implement retry_job**

Create `agent/services/job_manager/retry_job.py`:

```python
"""Single-invoice retry — bypass requiredDocs gate, send with caller-supplied files,
schedule a fire-and-forget QBO upload so the file persists without slowing the retry."""
import asyncio
import logging
from pathlib import Path
from typing import Any

from agent.services.database import get_customer
from agent.services.job_manager.send_qbo_api import _assemble_and_send_email
from agent.services.qbo_api import QBOApiClient

logger = logging.getLogger(__name__)


class _Invoice:
    """Lightweight invoice shim matching what _assemble_and_send_email expects."""
    def __init__(self, d: dict):
        self.invoice_number = d["invoice_number"]
        self.invoice_id = d["invoice_id"]
        self.container_number = d.get("container_number")
        self.customer_code = d.get("customer_code")
        self.subject = d.get("subject")
        self.do_sender_email = d.get("do_sender_email")


async def _background_qbo_upload(invoice_id: str, file_path: Path, doc_type: str) -> None:
    """Fire-and-forget upload to QBO. Swallows errors — caller never awaits success."""
    try:
        api = QBOApiClient()
        await api.upload_attachment(
            invoice_id=invoice_id,
            file_path=file_path,
            include_on_send=False,  # we already sent it inline
        )
        logger.info(
            "background_qbo_upload_complete",
            extra={"invoice_id": invoice_id, "doc_type": doc_type},
        )
    except Exception as exc:
        logger.warning(
            "background_qbo_upload_failed",
            extra={"invoice_id": invoice_id, "doc_type": doc_type, "error": str(exc)},
        )


async def start_retry_invoice_job(
    *,
    invoice: dict,
    files: list[dict],
) -> dict:
    """Retry sending a single invoice with caller-supplied attachments.

    Args:
        invoice: dict with invoice_number, invoice_id, container_number, customer_code,
                 subject, do_sender_email
        files: list of {slot: 'POD'|'BOL'|..., path: str, verified_by: 'filename'|'ai'|'manual'}

    Returns: {status: 'sent'|'error', error: str|None, attachments_sent: int}

    Bypasses requiredDocs gate: caller is asserting these files are right.
    Schedules background QBO upload for each file — non-blocking.
    """
    inv = _Invoice(invoice)
    customer = get_customer(invoice["customer_code"])
    if not customer:
        return {"status": "error", "error": "Customer not found", "attachments_sent": 0}

    # Build extra_attachments for email
    extra_attachments = []
    for f in files:
        path = Path(f["path"])
        with open(path, "rb") as fp:
            extra_attachments.append({
                "filename": path.name,
                "data": fp.read(),
            })

    # Send (skip QBO attachment download — we've got everything we need)
    send_result = await _assemble_and_send_email(
        job=None,
        invoice=inv,
        customer=customer,
        result=None,
        api=QBOApiClient(),
        extra_attachments=extra_attachments,
        skip_qbo_attachments=True,
    )

    if not send_result["sent"]:
        return {
            "status": "error",
            "error": send_result["error"],
            "attachments_sent": send_result["attachments_sent"],
        }

    # Email sent successfully — schedule background QBO uploads (fire-and-forget)
    for f in files:
        asyncio.create_task(_background_qbo_upload(
            invoice_id=invoice["invoice_id"],
            file_path=Path(f["path"]),
            doc_type=f["slot"].lower(),
        ))

    return {
        "status": "sent",
        "error": None,
        "attachments_sent": send_result["attachments_sent"],
    }
```

- [ ] **Step 4: Export from `__init__.py`**

In `agent/services/job_manager/__init__.py`, add:

```python
from agent.services.job_manager.retry_job import start_retry_invoice_job

__all__ = [..., "start_retry_invoice_job"]
```

(Keep existing exports — only add the new one.)

- [ ] **Step 5: Run tests**

```bash
agent/venv/Scripts/python.exe -m pytest agent/tests/test_retry_job.py -v
```
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add agent/services/job_manager/retry_job.py agent/services/job_manager/__init__.py agent/tests/test_retry_job.py
git commit -m "feat(retry): per-invoice retry job with background QBO upload"
```

---

## Task 5: Backend — retry router + verify endpoint (TDD)

**Files:**
- Create: `agent/routers/retry.py`
- Test: `agent/tests/test_retry_endpoint.py`
- Modify: `agent/main.py`

- [ ] **Step 1: Write failing endpoint test**

Create `agent/tests/test_retry_endpoint.py`:

```python
"""Retry endpoint: multipart upload of invoice JSON + files, returns send result."""
import io
import json
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from agent.main import app

client = TestClient(app)


def test_verify_file_returns_classification():
    """POST /jobs/verify-file accepts one file and a 'slot' query param,
    returns Claude classification."""
    fake_pdf = b"%PDF-1.4\n" + b"\x00" * 100
    with patch("agent.routers.retry.ClaudeClassifier") as MockClf:
        instance = MockClf.return_value
        instance.classify = AsyncMock(return_value=type("R", (), {
            "doc_type": "pod", "confidence": 0.93, "needs_review": False,
            "container_hint": "TRHU4593053", "skipped_api": False,
        })())
        r = client.post(
            "/jobs/verify-file?slot=POD",
            files={"file": ("pod.pdf", io.BytesIO(fake_pdf), "application/pdf")},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["doc_type"] == "pod"
    assert body["matches_slot"] is True
    assert body["confidence"] == 0.93


def test_verify_file_flags_mismatch():
    fake_pdf = b"%PDF-1.4\n" + b"\x00" * 100
    with patch("agent.routers.retry.ClaudeClassifier") as MockClf:
        instance = MockClf.return_value
        instance.classify = AsyncMock(return_value=type("R", (), {
            "doc_type": "bol", "confidence": 0.88, "needs_review": False,
            "container_hint": None, "skipped_api": False,
        })())
        r = client.post(
            "/jobs/verify-file?slot=POD",
            files={"file": ("scan.pdf", io.BytesIO(fake_pdf), "application/pdf")},
        )
    body = r.json()
    assert body["doc_type"] == "bol"
    assert body["matches_slot"] is False
    assert body["detected_as"] == "bol"


def test_retry_invoice_starts_job():
    fake_pdf = b"%PDF-1.4\n" + b"\x00" * 100
    invoice_json = json.dumps({
        "invoice_number": "LM26040864F", "invoice_id": "qbo-123",
        "container_number": "TRHU4593053", "customer_code": "APEXMA01",
        "subject": "Invoice LM26040864F", "do_sender_email": None,
    })
    with patch("agent.routers.retry.start_retry_invoice_job",
               new=AsyncMock(return_value={"status": "sent", "error": None, "attachments_sent": 2})) as mock_job:
        r = client.post(
            "/jobs/retry-invoice",
            data={
                "invoice": invoice_json,
                "slots": json.dumps([{"slot": "POD", "verified_by": "filename"}]),
            },
            files={"pod_file": ("pod.pdf", io.BytesIO(fake_pdf), "application/pdf")},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "sent"
    mock_job.assert_called_once()
```

- [ ] **Step 2: Run test — should fail**

```bash
agent/venv/Scripts/python.exe -m pytest agent/tests/test_retry_endpoint.py -v
```
Expected: 404 / module not found.

- [ ] **Step 3: Implement router**

Create `agent/routers/retry.py`:

```python
"""Retry + verification endpoints for in-app invoice recovery (Fix 2)."""
import json
import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from agent.services.claude_classifier import ClaudeClassifier
from agent.services.job_manager import start_retry_invoice_job

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/jobs", tags=["retry"])


SLOT_TO_DOCTYPE = {"POD": "pod", "BOL": "bol", "POL": "pol", "DO": "do", "PL": "pl"}


@router.post("/verify-file")
async def verify_file(slot: str, file: UploadFile = File(...)) -> dict:
    """Classify an uploaded file and report whether it matches the requested slot.

    Returns: {doc_type, confidence, matches_slot, detected_as, container_hint, skipped_api}
    """
    if slot.upper() not in SLOT_TO_DOCTYPE:
        raise HTTPException(400, f"Unknown slot: {slot}")

    suffix = Path(file.filename or "upload").suffix or ".pdf"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = Path(tmp.name)

    try:
        clf = ClaudeClassifier()
        result = await clf.classify(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    expected = SLOT_TO_DOCTYPE[slot.upper()]
    return {
        "doc_type": result.doc_type,
        "confidence": result.confidence,
        "matches_slot": result.doc_type == expected,
        "detected_as": result.doc_type if result.doc_type != expected else None,
        "container_hint": result.container_hint,
        "skipped_api": result.skipped_api,
    }


@router.post("/retry-invoice")
async def retry_invoice(
    request: Request,
    invoice: str = Form(...),
    slots: str = Form(...),
) -> dict:
    """Multipart retry: invoice JSON + slots metadata + per-slot uploaded files.

    Form fields:
        invoice: JSON string with invoice metadata
        slots: JSON array of {slot, verified_by}
        <slot>_file: actual file blob per slot (e.g. pod_file, bol_file)

    Returns: {status: 'sent'|'error', error: str|None, attachments_sent: int}
    """
    try:
        invoice_dict = json.loads(invoice)
        slots_list = json.loads(slots)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"Bad JSON: {e}")

    form = await request.form()
    files_for_job = []
    tmp_paths = []
    for slot_meta in slots_list:
        slot_name = slot_meta["slot"]
        field_name = f"{slot_name.lower()}_file"
        upload: UploadFile | None = form.get(field_name)
        if not upload or not hasattr(upload, "read"):
            raise HTTPException(400, f"Missing file for slot {slot_name}")
        suffix = Path(upload.filename or "f").suffix or ".pdf"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(await upload.read())
            tmp_paths.append(Path(tmp.name))
            files_for_job.append({
                "slot": slot_name,
                "path": tmp.name,
                "verified_by": slot_meta.get("verified_by", "filename"),
            })

    try:
        result = await start_retry_invoice_job(invoice=invoice_dict, files=files_for_job)
    finally:
        # Defer cleanup — background QBO upload may still need the files.
        # _background_qbo_upload reads them, then they get cleaned by OS temp sweep.
        pass

    return result
```

- [ ] **Step 4: Wire router into main.py**

In `agent/main.py`, find the router includes (look for `app.include_router(...)` lines) and add:

```python
from agent.routers import retry
app.include_router(retry.router)
```

- [ ] **Step 5: Run tests**

```bash
agent/venv/Scripts/python.exe -m pytest agent/tests/test_retry_endpoint.py -v
```
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add agent/routers/retry.py agent/main.py agent/tests/test_retry_endpoint.py
git commit -m "feat(retry): /jobs/verify-file + /jobs/retry-invoice endpoints"
```

---

## Task 6: Frontend CSS — port mockup styles

**Files:**
- Modify: `app/assets/css/styles.css`

- [ ] **Step 1: Append the post-send results view styles**

At the end of `app/assets/css/styles.css`, append the following block. All selectors are scoped under `#invoiceSenderView` to keep them isolated from the rest of the app.

```css
/* ─── v2.62 Invoice Sender — Results view (Fix 1 + Fix 2) ─── */

#invoiceSenderView .v62-alert-banner {
  display: flex; align-items: center; gap: 12px;
  background: #fff7ed; border: 1px solid #fed7aa;
  border-radius: 8px; padding: 12px 14px;
  margin-bottom: 14px;
}
#invoiceSenderView .v62-alert-icon {
  width: 28px; height: 28px; border-radius: 50%;
  background: #ea580c; color: white; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}
#invoiceSenderView .v62-alert-text { flex: 1; }
#invoiceSenderView .v62-alert-text strong { color: #9a3412; font-size: 0.92rem; }
#invoiceSenderView .v62-alert-text small {
  display: block; color: #78350f; font-size: 0.78rem; margin-top: 2px;
}
#invoiceSenderView .v62-bulk-retry-btn {
  background: #ea580c; color: white; border: none;
  padding: 8px 14px; border-radius: 6px; cursor: pointer;
  font-weight: 600; font-size: 0.85rem;
}
#invoiceSenderView .v62-bulk-retry-btn:disabled {
  background: #fbbf24; opacity: 0.6; cursor: not-allowed;
}
#invoiceSenderView .v62-bulk-retry-btn:hover:not(:disabled) { background: #c2410c; }

#invoiceSenderView .v62-tabs {
  display: flex; gap: 4px; border-bottom: 1px solid #e2e8f0;
  margin-bottom: 12px;
}
#invoiceSenderView .v62-tab {
  background: none; border: none; padding: 9px 14px;
  font-size: 0.85rem; font-weight: 500; color: #64748b; cursor: pointer;
  border-bottom: 2px solid transparent; transition: all 0.15s;
}
#invoiceSenderView .v62-tab:hover { color: #1e293b; }
#invoiceSenderView .v62-tab.active { color: #ea580c; border-bottom-color: #ea580c; }
#invoiceSenderView .v62-tab.has-issues.active { color: #dc2626; border-bottom-color: #dc2626; }
#invoiceSenderView .v62-tab .count {
  display: inline-block; background: #e2e8f0; color: #475569;
  font-size: 0.72rem; padding: 1px 7px; border-radius: 10px;
  margin-left: 5px; font-weight: 600;
}
#invoiceSenderView .v62-tab.has-issues .count { background: #fee2e2; color: #b91c1c; }
#invoiceSenderView .v62-tab.active .count { background: #ffedd5; color: #9a3412; }

/* Status badges */
#invoiceSenderView .v62-badge {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 3px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 600;
}
#invoiceSenderView .v62-badge.missing-doc { background: #fef2f2; color: #b91c1c; }
#invoiceSenderView .v62-badge.api-error { background: #fff7ed; color: #c2410c; }
#invoiceSenderView .v62-badge.sent { background: #f0fdf4; color: #15803d; }
#invoiceSenderView .v62-badge.in-progress { background: #eff6ff; color: #1d4ed8; }
#invoiceSenderView .v62-badge.skipped { background: #f1f5f9; color: #64748b; }

/* Action column */
#invoiceSenderView .v62-action-btn {
  padding: 5px 10px; border-radius: 5px; font-size: 0.78rem; font-weight: 600;
  border: 1px solid; cursor: pointer; background: white;
}
#invoiceSenderView .v62-action-btn.retry {
  color: #c2410c; border-color: #fdba74;
}
#invoiceSenderView .v62-action-btn.retry:hover { background: #fff7ed; }
#invoiceSenderView .v62-action-btn.attach {
  color: #b91c1c; border-color: #fca5a5;
}
#invoiceSenderView .v62-action-btn.attach:hover { background: #fef2f2; }

/* Side panel */
#invoiceSenderView .v62-results-body {
  display: grid; grid-template-columns: 1fr 0px; gap: 0; transition: grid-template-columns 0.18s ease;
}
#invoiceSenderView .v62-results-body.panel-open { grid-template-columns: 1fr 420px; }
#invoiceSenderView .v62-detail-panel {
  border-left: 1px solid #e2e8f0; background: #fafafa;
  overflow: hidden; max-height: calc(100vh - 180px);
}
#invoiceSenderView .v62-panel-inner { padding: 16px; overflow-y: auto; max-height: 100%; }
#invoiceSenderView .v62-panel-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 12px;
}
#invoiceSenderView .v62-panel-title { font-weight: 700; font-size: 0.92rem; }
#invoiceSenderView .v62-panel-close {
  background: none; border: none; font-size: 1.4rem; cursor: pointer; color: #94a3b8;
  width: 28px; height: 28px;
}
#invoiceSenderView .v62-panel-close:hover { color: #1e293b; }
#invoiceSenderView .v62-panel-invoice-id {
  font-size: 1.1rem; font-weight: 700; color: #0f172a; margin-bottom: 4px;
}
#invoiceSenderView .v62-panel-meta {
  font-size: 0.78rem; color: #475569; margin-bottom: 12px; line-height: 1.6;
}
#invoiceSenderView .v62-panel-meta .label { color: #94a3b8; font-weight: 600; font-size: 0.7rem; text-transform: uppercase; }

#invoiceSenderView .v62-error-box {
  background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px;
  padding: 12px 14px; margin-bottom: 14px;
}
#invoiceSenderView .v62-error-box.warn { background: #fff7ed; border-color: #fed7aa; }
#invoiceSenderView .v62-error-title {
  font-weight: 700; color: #b91c1c; font-size: 0.92rem; margin-bottom: 6px;
}
#invoiceSenderView .v62-error-box.warn .v62-error-title { color: #c2410c; }
#invoiceSenderView .v62-error-explanation { font-size: 0.82rem; color: #1e293b; line-height: 1.5; }

#invoiceSenderView .v62-checks-list {
  list-style: none; padding: 0; margin: 12px 0;
  font-size: 0.82rem; line-height: 1.7;
}
#invoiceSenderView .v62-checks-list li {
  display: flex; align-items: flex-start; gap: 7px;
}
#invoiceSenderView .v62-checks-list .icon {
  display: inline-flex; align-items: center; justify-content: center;
  width: 16px; height: 16px; border-radius: 50%; font-size: 0.65rem; font-weight: 700;
  margin-top: 2px; flex-shrink: 0;
}
#invoiceSenderView .v62-checks-list .icon.ok { background: #dcfce7; color: #15803d; }
#invoiceSenderView .v62-checks-list .icon.fail { background: #fee2e2; color: #b91c1c; }
#invoiceSenderView .v62-checks-list .icon.warn { background: #fef3c7; color: #b45309; }

#invoiceSenderView .v62-next-step {
  background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 6px;
  padding: 10px 12px; font-size: 0.82rem; color: #1e3a8a; line-height: 1.5; margin: 12px 0;
}

/* Fix It section (drop zones) */
#invoiceSenderView .v62-fix-it-section {
  border-top: 1px solid #e2e8f0; padding-top: 14px; margin-top: 14px;
}
#invoiceSenderView .v62-fix-it-title {
  font-weight: 700; color: #0f172a; font-size: 0.88rem; margin-bottom: 4px;
}
#invoiceSenderView .v62-fix-it-subtitle { font-size: 0.78rem; color: #64748b; margin-bottom: 10px; }

#invoiceSenderView .v62-drop-zone {
  border: 2px dashed #cbd5e1; border-radius: 8px; padding: 18px;
  text-align: center; cursor: pointer; transition: all 0.15s; margin-bottom: 10px;
  background: white;
}
#invoiceSenderView .v62-drop-zone:hover,
#invoiceSenderView .v62-drop-zone.drag-over { border-color: #ea580c; background: #fff7ed; }
#invoiceSenderView .v62-drop-zone .dz-icon { font-size: 1.6rem; color: #ea580c; margin-bottom: 4px; }
#invoiceSenderView .v62-drop-zone .dz-title { font-weight: 600; color: #1e293b; }
#invoiceSenderView .v62-drop-zone .dz-subtitle { color: #94a3b8; font-size: 0.78rem; margin-top: 2px; }

#invoiceSenderView .v62-drop-zone.uploading,
#invoiceSenderView .v62-drop-zone.verifying,
#invoiceSenderView .v62-drop-zone.attached,
#invoiceSenderView .v62-drop-zone.mismatch {
  border-style: solid; padding: 12px 14px; text-align: left;
}
#invoiceSenderView .v62-drop-zone.uploading { background: #eff6ff; border-color: #93c5fd; }
#invoiceSenderView .v62-drop-zone.verifying { background: #faf5ff; border-color: #c4b5fd; }
#invoiceSenderView .v62-drop-zone.attached { background: #f0fdf4; border-color: #86efac; }
#invoiceSenderView .v62-drop-zone.mismatch { background: #fff7ed; border-color: #fdba74; }

#invoiceSenderView .v62-dz-status-row { display: flex; align-items: center; gap: 10px; }
#invoiceSenderView .v62-dz-status-icon {
  width: 28px; height: 28px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  color: white; font-weight: 700;
}
#invoiceSenderView .v62-drop-zone.uploading .v62-dz-status-icon { background: #3b82f6; }
#invoiceSenderView .v62-drop-zone.verifying .v62-dz-status-icon { background: #8b5cf6; }
#invoiceSenderView .v62-drop-zone.attached .v62-dz-status-icon { background: #16a34a; }
#invoiceSenderView .v62-drop-zone.mismatch .v62-dz-status-icon { background: #d97706; }
#invoiceSenderView .v62-dz-info { flex: 1; min-width: 0; }
#invoiceSenderView .v62-dz-file-name { font-weight: 600; font-size: 0.86rem; }
#invoiceSenderView .v62-dz-file-status { font-size: 0.76rem; }
#invoiceSenderView .v62-drop-zone.uploading .v62-dz-file-name { color: #1e3a8a; }
#invoiceSenderView .v62-drop-zone.verifying .v62-dz-file-name { color: #5b21b6; }
#invoiceSenderView .v62-drop-zone.attached .v62-dz-file-name { color: #14532d; }
#invoiceSenderView .v62-drop-zone.mismatch .v62-dz-file-name { color: #78350f; }
#invoiceSenderView .v62-dz-replace-btn {
  margin-left: auto; padding: 4px 10px; border-radius: 5px; font-size: 0.72rem;
  font-weight: 600; cursor: pointer; border: 1px solid; background: white;
}
#invoiceSenderView .v62-drop-zone.attached .v62-dz-replace-btn {
  color: #15803d; border-color: #86efac;
}
#invoiceSenderView .v62-drop-zone.mismatch .v62-dz-replace-btn {
  color: #c2410c; border-color: #fdba74;
}

/* Retry / Skip buttons */
#invoiceSenderView .v62-panel-actions { display: flex; gap: 8px; margin-top: 12px; }
#invoiceSenderView .v62-btn-retry {
  flex: 1; background: #16a34a; color: white; border: none;
  padding: 9px 14px; border-radius: 6px; font-weight: 600; font-size: 0.86rem; cursor: pointer;
}
#invoiceSenderView .v62-btn-retry:disabled { background: #94a3b8; cursor: not-allowed; }
#invoiceSenderView .v62-btn-retry:hover:not(:disabled) { background: #15803d; }
#invoiceSenderView .v62-btn-skip {
  background: none; color: #64748b; border: 1px solid #cbd5e1;
  padding: 9px 14px; border-radius: 6px; font-weight: 500; font-size: 0.86rem; cursor: pointer;
}
#invoiceSenderView .v62-btn-skip:hover { background: #f8fafc; }

/* Retry progress + success */
#invoiceSenderView .v62-retry-progress {
  background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 8px;
  padding: 14px; margin-top: 14px;
}
#invoiceSenderView .v62-retry-progress-title {
  font-weight: 700; color: #1e3a8a; font-size: 0.88rem; margin-bottom: 10px;
  display: flex; align-items: center; gap: 7px;
}
#invoiceSenderView .v62-retry-steps { font-size: 0.82rem; line-height: 1.9; }

#invoiceSenderView .v62-success-state { text-align: center; padding: 24px 12px; }
#invoiceSenderView .v62-big-check {
  width: 48px; height: 48px; border-radius: 50%; background: #16a34a;
  color: white; display: flex; align-items: center; justify-content: center;
  font-size: 1.6rem; margin: 0 auto 10px;
}
#invoiceSenderView .v62-success-state h3 { color: #14532d; font-size: 1rem; margin-bottom: 4px; }
#invoiceSenderView .v62-success-state p { color: #475569; font-size: 0.82rem; }
#invoiceSenderView .v62-success-state .v62-auto-advance-note {
  margin-top: 12px; font-size: 0.76rem; color: #94a3b8; font-style: italic;
}

#invoiceSenderView .v62-small-spinner {
  display: inline-block; width: 12px; height: 12px; border-radius: 50%;
  border: 2px solid #cbd5e1; border-right-color: transparent;
  animation: v62-spin 0.7s linear infinite;
}
@keyframes v62-spin { to { transform: rotate(360deg); } }
```

- [ ] **Step 2: Commit**

```bash
git add app/assets/css/styles.css
git commit -m "feat(invoice-sender/css): port v2.62 results-view styles (tabs, banner, side panel, drop zones)"
```

---

## Task 7: Frontend — HTML scaffolding + module wire-up

**Files:**
- Modify: `app/index.html`
- Create: `app/assets/js/tools/invoice-sender/invoice-sender-results.js` (stub)
- Modify: `app/assets/js/app.js`

- [ ] **Step 1: Add results-view container to index.html**

In `app/index.html`, find the `<div id="invoiceSenderView">` block (the Invoice Sender tool container). Below the existing table block, before the closing `</div>` of that view, add:

```html
<!-- v2.62 Results view (Fix 1+2). Hidden until send completes. -->
<div id="invSendResults" class="v62-results-body" style="display:none;">
  <div id="invSendResultsMain">
    <div id="invSendAlertBanner" class="v62-alert-banner" style="display:none;">
      <div class="v62-alert-icon">!</div>
      <div class="v62-alert-text">
        <strong id="invSendBannerTitle"></strong>
        <small id="invSendBannerSubtitle"></small>
      </div>
      <button id="invSendBulkRetryBtn" class="v62-bulk-retry-btn" disabled>
        ↻ Retry All Fixed (<span id="invSendBulkCount">0</span>)
      </button>
    </div>

    <div class="v62-tabs">
      <button class="v62-tab has-issues active" data-tab="needs-attention">
        ⚠ Needs Attention <span class="count" id="invTabIssueCount">0</span>
      </button>
      <button class="v62-tab" data-tab="sent">
        ✓ Sent <span class="count" id="invTabSentCount">0</span>
      </button>
      <button class="v62-tab" data-tab="all">
        All Invoices <span class="count" id="invTabAllCount">0</span>
      </button>
    </div>

    <div id="invResultsTableWrap"></div>
  </div>

  <div id="invDetailPanel" class="v62-detail-panel">
    <div id="invDetailPanelInner" class="v62-panel-inner"></div>
  </div>
</div>
```

- [ ] **Step 2: Create stub module**

Create `app/assets/js/tools/invoice-sender/invoice-sender-results.js`:

```js
// Invoice Sender — Results view (Fix 1 + Fix 2) for v2.62.
// Activated after a send job completes. Replaces the live progress table
// with a tabbed results table + diagnostic side panel + drop-zone retry flow.

import { sendState, invoiceState } from '../../shared/state.js';
import { escHtml } from '../../shared/utils.js';
import { agentBridge } from '../../shared/agent-client.js';

const SLOT_LABELS = { pod: 'POD', bol: 'BOL', pol: 'POL', do: 'DO', pl: 'PL' };

export function showResultsView() {
  document.getElementById('invSendResults').style.display = 'grid';
  renderResults();
}

export function hideResultsView() {
  document.getElementById('invSendResults').style.display = 'none';
}

export function renderResults() {
  // Stub — implemented in Task 8
  console.log('[v62] renderResults stub');
}
```

- [ ] **Step 3: Import results module in app.js**

In `app/assets/js/app.js`, find the existing invoice-sender import. Add the new module so it registers handlers:

```js
import './tools/invoice-sender/invoice-sender.js';
import './tools/invoice-sender/invoice-sender-results.js';
```

- [ ] **Step 4: Open app in browser, switch to Invoice Sender — verify no JS errors**

Open `app/index.html`. Navigate to Invoice Sender. Open browser console. Expected: no errors. The new `#invSendResults` div is hidden.

- [ ] **Step 5: Commit**

```bash
git add app/index.html app/assets/js/tools/invoice-sender/invoice-sender-results.js app/assets/js/app.js
git commit -m "feat(invoice-sender/v62): scaffolding for results view module"
```

---

## Task 8: Frontend — render results table with tabs + badges (Fix 1)

**Files:**
- Modify: `app/assets/js/tools/invoice-sender/invoice-sender-results.js`

- [ ] **Step 1: Replace stub with full results rendering**

Replace the contents of `app/assets/js/tools/invoice-sender/invoice-sender-results.js` with:

```js
import { sendState, invoiceState } from '../../shared/state.js';
import { escHtml } from '../../shared/utils.js';
import { agentBridge } from '../../shared/agent-client.js';

const SLOT_LABELS = { pod: 'POD', bol: 'BOL', pol: 'POL', do: 'DO', pl: 'PL' };

// Map row status → badge config
function badgeFor(row) {
  if (row.sendStatus === 'sent') return { cls: 'sent', text: '✓ Sent' };
  if (row.sendStatus === 'in_progress') return { cls: 'in-progress', text: '⟳ Sending…' };
  if (row.sendStatus === 'skipped') return { cls: 'skipped', text: 'Skipped' };
  if (row.sendStatus === 'missing_docs') {
    const missing = (row.missingDocs || []).map(d => SLOT_LABELS[d.toLowerCase()] || d.toUpperCase());
    const text = missing.length === 1
      ? `⚠ ${missing[0]} Missing`
      : `⚠ ${missing.join(' + ')} Missing`;
    return { cls: 'missing-doc', text };
  }
  if (row.sendStatus === 'error') {
    if ((row.errorMessage || '').toLowerCase().includes('timeout')) {
      return { cls: 'api-error', text: '⚡ QBO Error' };
    }
    return { cls: 'missing-doc', text: '⚠ Error' };
  }
  return { cls: 'skipped', text: row.sendStatus || 'Pending' };
}

function isFailed(row) {
  return ['missing_docs', 'error'].includes(row.sendStatus);
}

function getFailedRows() {
  return invoiceState.invoices.filter(isFailed);
}

function getSentRows() {
  return invoiceState.invoices.filter(r => r.sendStatus === 'sent');
}

export function showResultsView() {
  document.getElementById('invSendResults').style.display = 'grid';
  // Default tab: needs-attention if any failures, else all
  sendState.currentTab = getFailedRows().length > 0 ? 'needs-attention' : 'all';
  bindTabClicks();
  renderResults();
}

export function hideResultsView() {
  document.getElementById('invSendResults').style.display = 'none';
}

function bindTabClicks() {
  document.querySelectorAll('#invSendResults .v62-tab').forEach(btn => {
    btn.onclick = () => {
      sendState.currentTab = btn.dataset.tab;
      document.querySelectorAll('#invSendResults .v62-tab').forEach(b => b.classList.toggle('active', b === btn));
      renderTable();
    };
  });
}

export function renderResults() {
  // Update counts
  const failed = getFailedRows();
  const sent = getSentRows();
  document.getElementById('invTabIssueCount').textContent = failed.length;
  document.getElementById('invTabSentCount').textContent = sent.length;
  document.getElementById('invTabAllCount').textContent = invoiceState.invoices.length;

  // Update banner
  const banner = document.getElementById('invSendAlertBanner');
  if (failed.length > 0) {
    banner.style.display = 'flex';
    document.getElementById('invSendBannerTitle').textContent =
      failed.length === 1
        ? '1 invoice needs your attention.'
        : `${failed.length} invoices need your attention.`;
    document.getElementById('invSendBannerSubtitle').textContent =
      `The other ${sent.length} sent successfully — click each failed row to fix it without leaving this page.`;
  } else {
    banner.style.display = 'none';
  }

  // Sync active tab class
  document.querySelectorAll('#invSendResults .v62-tab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === sendState.currentTab);
  });

  renderTable();
}

function renderTable() {
  let rows;
  if (sendState.currentTab === 'needs-attention') rows = getFailedRows();
  else if (sendState.currentTab === 'sent') rows = getSentRows();
  else rows = [...getFailedRows(), ...getSentRows(), ...invoiceState.invoices.filter(r => !isFailed(r) && r.sendStatus !== 'sent')];

  const wrap = document.getElementById('invResultsTableWrap');
  if (rows.length === 0) {
    wrap.innerHTML = `<div style="text-align:center; padding:48px; color:#94a3b8;">
      <div style="font-size:2rem; margin-bottom:8px;">✓</div>
      <strong>All caught up.</strong>
    </div>`;
    return;
  }

  wrap.innerHTML = `
    <table class="inv-table">
      <thead>
        <tr>
          <th>Invoice</th><th>Container</th><th>Customer</th><th>Status</th><th style="text-align:right;">Action</th>
        </tr>
      </thead>
      <tbody>
        ${rows.map(r => renderRow(r)).join('')}
      </tbody>
    </table>
  `;

  wrap.querySelectorAll('tr[data-invoice]').forEach(tr => {
    tr.onclick = (e) => {
      if (e.target.closest('.v62-action-btn')) return;
      openPanelForInvoice(tr.dataset.invoice);
    };
  });
  wrap.querySelectorAll('.v62-action-btn').forEach(btn => {
    btn.onclick = (e) => {
      e.stopPropagation();
      openPanelForInvoice(btn.dataset.invoice);
    };
  });
}

function renderRow(r) {
  const b = badgeFor(r);
  const failed = isFailed(r);
  let action = '';
  if (failed) {
    if (r.sendStatus === 'error') {
      action = `<button class="v62-action-btn retry" data-invoice="${r.invoiceNumber}">↻ Retry</button>`;
    } else {
      action = `<button class="v62-action-btn attach" data-invoice="${r.invoiceNumber}">📎 Attach &amp; Retry</button>`;
    }
  }
  return `<tr data-invoice="${r.invoiceNumber}" style="${failed ? 'cursor:pointer;' : ''}">
    <td>${escHtml(r.invoiceNumber)}</td>
    <td>${escHtml(r.containerNumber || '—')}</td>
    <td>${escHtml(r.customerCode || '')} ${escHtml(r.customerName || '')}</td>
    <td><span class="v62-badge ${b.cls}">${b.text}</span></td>
    <td style="text-align:right;">${action}</td>
  </tr>`;
}

// Placeholder — Task 9 fills in
function openPanelForInvoice(invoiceNumber) {
  console.log('[v62] openPanelForInvoice', invoiceNumber);
}

// Expose for invoice-sender.js to call when send completes
window.invShowResultsView = showResultsView;
window.invRenderResults = renderResults;
window.invHideResultsView = hideResultsView;
```

- [ ] **Step 2: Hook into send-complete from invoice-sender.js**

In `app/assets/js/tools/invoice-sender/invoice-sender.js`, find the SSE handler for `job_complete` event (around lines 1010-1050, look for the dispatcher map). Add a call right after the existing job-completion logic:

```js
// v2.62: switch to results view
if (typeof window.invShowResultsView === 'function') {
  // hide the live progress table
  // (find the existing #invoiceTable element and hide it)
  const liveTable = document.querySelector('#invoiceSenderView .inv-table-wrap');
  if (liveTable) liveTable.style.display = 'none';
  window.invShowResultsView();
}
```

- [ ] **Step 3: Update `invoice_missing_docs` SSE handler to set new status**

In `invoice-sender.js` line 958-962, replace:

```js
invUpdateInvoiceSendStatus(event.invoiceNumber, 'skipped_no_attachments', { errorMessage: 'Missing: ' + event.missing.join(', ') });
```

with:

```js
invUpdateInvoiceSendStatus(event.invoiceNumber, 'missing_docs', {
  errorMessage: 'Missing: ' + event.missing.join(', '),
  missingDocs: event.missing,  // array like ["pod", "bol"]
});
```

Also update `InvoiceRecord` defaults if needed: the row needs a `missingDocs` field. Check `invHandleCsvFile` around line 124 — add `missingDocs: []` to the default row shape.

- [ ] **Step 4: Smoke test in browser**

Open the app, run a test-mode send with one mocked failure (or use the existing test pathway). Confirm:
- Send completes → live table hides
- Results view appears with tabs + alert banner
- Failed rows show specific badges (POD Missing, etc.)
- Sent rows show ✓ Sent
- Tab counts are correct

- [ ] **Step 5: Commit**

```bash
git add app/assets/js/tools/invoice-sender/invoice-sender-results.js app/assets/js/tools/invoice-sender/invoice-sender.js
git commit -m "feat(invoice-sender/v62): results view with tabs + specific-error badges (Fix 1)"
```

---

## Task 9: Frontend — side panel diagnostic (Fix 1)

**Files:**
- Modify: `app/assets/js/tools/invoice-sender/invoice-sender-results.js`

- [ ] **Step 1: Replace stub `openPanelForInvoice`**

Replace the `openPanelForInvoice` function (the stub at the bottom) with the full panel implementation:

```js
function buildDiagnostic(row) {
  // Derive plain-English explanation from row state
  if (row.sendStatus === 'missing_docs') {
    const missing = (row.missingDocs || []).map(d => SLOT_LABELS[d.toLowerCase()] || d.toUpperCase());
    const docs = missing.join(' and ');
    return {
      cls: 'v62-error-box',
      icon: '⚠',
      title: missing.length > 1 ? `${docs} are missing` : `${docs} is missing`,
      explanation: missing.length > 1
        ? `This customer requires ${docs}, but neither is attached in QuickBooks or available in TMS. Drop the files below — once both are attached, you can retry the send.`
        : `We couldn't find a ${docs} anywhere — QuickBooks has no ${docs} attachment, and the TMS work order doesn't show one either. Drop the file below and we'll attach it, then retry the send.`,
      checks: [
        { status: 'ok', text: 'Found invoice in QuickBooks' },
        { status: 'ok', text: `Verified container number matches (${row.containerNumber || '—'})` },
        { status: 'ok', text: 'Connected to TMS work order' },
        ...missing.map(d => ({ status: 'fail', text: `No ${d} attached in QuickBooks` })),
        ...missing.map(d => ({ status: 'fail', text: `No ${d} listed on the TMS work order` })),
      ],
      nextStep: `<strong>What to do:</strong> Drop the ${docs} file below — we'll verify it with AI if the filename is unclear, attach it to your email, save a copy to QuickBooks, and re-send the invoice.`,
      missingSlots: missing,
    };
  }
  // QBO timeout / transient error
  if (row.sendStatus === 'error') {
    return {
      cls: 'v62-error-box warn',
      icon: '⚡',
      title: 'QuickBooks didn\'t respond',
      explanation: `We tried to read this invoice's attachments, but QuickBooks took too long. This is almost always temporary — just hit Retry and it usually goes through.`,
      checks: [
        { status: 'ok', text: 'Found invoice in QuickBooks' },
        { status: 'ok', text: `Verified container number matches (${row.containerNumber || '—'})` },
        { status: 'fail', text: row.errorMessage || 'Request timed out' },
      ],
      nextStep: `<strong>What to do:</strong> Just retry — most QBO timeouts clear up on their own within a minute.`,
      missingSlots: [],
    };
  }
  return null;
}

function openPanelForInvoice(invoiceNumber) {
  const row = invoiceState.invoices.find(r => r.invoiceNumber === invoiceNumber);
  if (!row) return;
  sendState.activePanelInvoiceId = invoiceNumber;
  if (!sendState.retry[invoiceNumber]) {
    sendState.retry[invoiceNumber] = { panelStage: 'fix', attached: {} };
  }
  document.getElementById('invSendResults').classList.add('panel-open');
  renderPanel();
}

function closePanel() {
  sendState.activePanelInvoiceId = null;
  document.getElementById('invSendResults').classList.remove('panel-open');
}

function renderPanel() {
  const invoiceNumber = sendState.activePanelInvoiceId;
  if (!invoiceNumber) return;
  const row = invoiceState.invoices.find(r => r.invoiceNumber === invoiceNumber);
  const state = sendState.retry[invoiceNumber];
  const inner = document.getElementById('invDetailPanelInner');

  if (state.panelStage === 'success') {
    inner.innerHTML = renderSuccessStage(row);
    scheduleAutoAdvance(invoiceNumber);
    return;
  }
  if (state.panelStage === 'retrying') {
    inner.innerHTML = renderRetryingStage(row);
    return;
  }

  // Default: fix stage
  const diag = buildDiagnostic(row);
  if (!diag) {
    inner.innerHTML = `<div class="v62-panel-header"><span class="v62-panel-title">Send Diagnostic</span><button class="v62-panel-close" onclick="window.invClosePanel()">×</button></div><p style="padding:16px;">No diagnostic available.</p>`;
    return;
  }

  const checksHtml = diag.checks.map(c => {
    const ic = c.status === 'ok' ? '✓' : c.status === 'warn' ? '!' : '✕';
    return `<li><span class="icon ${c.status}">${ic}</span>${escHtml(c.text)}</li>`;
  }).join('');

  inner.innerHTML = `
    <div class="v62-panel-header">
      <span class="v62-panel-title">Send Diagnostic</span>
      <button class="v62-panel-close" onclick="window.invClosePanel()">×</button>
    </div>
    <div class="v62-panel-invoice-id">${escHtml(row.invoiceNumber)}</div>
    <div class="v62-panel-meta">
      <span class="label">CONTAINER:</span> ${escHtml(row.containerNumber || '—')}<br>
      <span class="label">CUSTOMER:</span> ${escHtml(row.customerCode || '')} ${escHtml(row.customerName || '')}
    </div>
    <div class="${diag.cls}">
      <div class="v62-error-title">${diag.icon} ${escHtml(diag.title)}</div>
      <div class="v62-error-explanation">${escHtml(diag.explanation)}</div>
    </div>
    <strong style="font-size:0.82rem; color:#0f172a;">What we checked:</strong>
    <ul class="v62-checks-list">${checksHtml}</ul>
    <div class="v62-next-step">${diag.nextStep}</div>
    <div id="invFixItContainer"></div>
  `;
  // Fix-It section rendered in Task 10
  if (typeof window.invRenderFixItSection === 'function') {
    window.invRenderFixItSection(row, diag.missingSlots);
  }
}

function renderRetryingStage(row) {
  return `
    <div class="v62-panel-header"><span class="v62-panel-title">Retrying Send</span><button class="v62-panel-close" onclick="window.invClosePanel()">×</button></div>
    <div class="v62-panel-invoice-id">${escHtml(row.invoiceNumber)}</div>
    <div class="v62-retry-progress">
      <div class="v62-retry-progress-title"><span class="v62-small-spinner"></span> Retrying…</div>
      <div class="v62-retry-steps" id="invRetrySteps"></div>
    </div>
  `;
}

function renderSuccessStage(row) {
  return `
    <div class="v62-panel-header"><span class="v62-panel-title">Send Diagnostic</span><button class="v62-panel-close" onclick="window.invClosePanel()">×</button></div>
    <div class="v62-success-state">
      <div class="v62-big-check">✓</div>
      <h3>${escHtml(row.invoiceNumber)} sent successfully!</h3>
      <p>Email delivered.</p>
      <div class="v62-auto-advance-note">Advancing to next failure in 1.5s…</div>
    </div>
  `;
}

function scheduleAutoAdvance(currentInvoiceNumber) {
  setTimeout(() => {
    const failures = getFailedRows().filter(r => r.invoiceNumber !== currentInvoiceNumber);
    if (failures.length > 0) {
      openPanelForInvoice(failures[0].invoiceNumber);
    } else {
      closePanel();
    }
    renderResults();
  }, 1500);
}

window.invClosePanel = closePanel;
window.invOpenPanelForInvoice = openPanelForInvoice;
window.invRenderPanel = renderPanel;
```

- [ ] **Step 2: Smoke test in browser**

Reload the app. Trigger a failed send. Click a failed row. Confirm:
- Side panel slides in from right
- Shows the invoice/container/customer header
- Shows error title + explanation in plain English
- Shows checks list with ✓ and ✕ icons
- Shows "What to do" guidance

- [ ] **Step 3: Commit**

```bash
git add app/assets/js/tools/invoice-sender/invoice-sender-results.js
git commit -m "feat(invoice-sender/v62): diagnostic side panel with plain-English explanations (Fix 1)"
```

---

## Task 10: Frontend — Fix-It drop zones + verification (Fix 2 part 1)

**Files:**
- Modify: `app/assets/js/tools/invoice-sender/invoice-sender-results.js`
- Modify: `app/assets/js/shared/agent-client.js`

- [ ] **Step 1: Add `verifyFile` + `retryInvoice` to agent client**

In `app/assets/js/shared/agent-client.js`, find the existing exported `agentBridge` object (or similar). Add two methods:

```js
async verifyFile(slot, file) {
  const fd = new FormData();
  fd.append('file', file);
  const r = await fetch(`http://127.0.0.1:8787/jobs/verify-file?slot=${encodeURIComponent(slot)}`, {
    method: 'POST',
    body: fd,
    headers: this._authHeaders(),
  });
  if (!r.ok) throw new Error(`verifyFile ${r.status}`);
  return r.json();
},

async retryInvoice(invoice, slotFiles) {
  // slotFiles: [{slot: 'POD', file: File, verifiedBy: 'filename'|'ai'|'manual'}]
  const fd = new FormData();
  fd.append('invoice', JSON.stringify(invoice));
  fd.append('slots', JSON.stringify(slotFiles.map(s => ({ slot: s.slot, verified_by: s.verifiedBy }))));
  slotFiles.forEach(sf => {
    fd.append(`${sf.slot.toLowerCase()}_file`, sf.file);
  });
  const r = await fetch(`http://127.0.0.1:8787/jobs/retry-invoice`, {
    method: 'POST',
    body: fd,
    headers: this._authHeaders(),
  });
  if (!r.ok) throw new Error(`retryInvoice ${r.status}`);
  return r.json();
},
```

(The exact existing structure of `agentBridge` may differ — adapt to match. The pattern `_authHeaders()` and base URL must come from the existing file.)

- [ ] **Step 2: Add filename match patterns**

At the top of `invoice-sender-results.js`, add:

```js
const FILENAME_PATTERNS = {
  POD: /\b(pod|proof.?of.?delivery|delivery.?receipt)\b/i,
  BOL: /\b(bol|bill.?of.?lading|b\/l)\b/i,
  POL: /\b(pol|proof.?of.?loading)\b/i,
  DO: /\b(d\.?o\.?|delivery.?order)\b/i,
  PL: /\b(p\.?l\.?|packing.?list)\b/i,
};

function matchesByFilename(filename, slot) {
  const pat = FILENAME_PATTERNS[slot];
  if (!pat) return false;
  return pat.test(filename);
}
```

- [ ] **Step 3: Add Fix-It rendering**

Append to `invoice-sender-results.js`:

```js
function renderFixItSection(row, missingSlots) {
  const container = document.getElementById('invFixItContainer');
  if (!container) return;
  const state = sendState.retry[row.invoiceNumber];

  // For pure transient errors (no missing docs), just show retry button
  if (missingSlots.length === 0) {
    container.innerHTML = `
      <div class="v62-fix-it-section">
        <div class="v62-fix-it-title">⚡ Fix it</div>
        <div class="v62-fix-it-subtitle">Nothing to attach — temporary QuickBooks hiccup. Just hit Retry.</div>
        <div class="v62-panel-actions">
          <button class="v62-btn-retry" onclick="window.invStartRetry('${row.invoiceNumber}')">↻ Try Again</button>
          <button class="v62-btn-skip" onclick="window.invSkipRow('${row.invoiceNumber}')">Skip</button>
        </div>
      </div>
    `;
    return;
  }

  // For missing-doc errors, show drop zones
  const allAttached = missingSlots.every(s => {
    const a = state.attached[s];
    return a && (a.stage === 'attached' || a.stage === 'attached_manual');
  });

  const zonesHtml = missingSlots.map(slot => renderDropZone(row.invoiceNumber, slot)).join('');

  container.innerHTML = `
    <div class="v62-fix-it-section">
      <div class="v62-fix-it-title">📎 Fix it</div>
      <div class="v62-fix-it-subtitle">Drop the missing file${missingSlots.length > 1 ? 's' : ''} below. PDF · JPG · PNG · HEIC.</div>
      ${zonesHtml}
      <div class="v62-panel-actions">
        <button class="v62-btn-retry" ${allAttached ? '' : 'disabled'} onclick="window.invStartRetry('${row.invoiceNumber}')">↻ Retry Send</button>
        <button class="v62-btn-skip" onclick="window.invSkipRow('${row.invoiceNumber}')">Skip</button>
      </div>
    </div>
  `;

  // Wire drag/drop + click for each zone
  missingSlots.forEach(slot => bindDropZone(row.invoiceNumber, slot));
}

function renderDropZone(invoiceNumber, slot) {
  const state = sendState.retry[invoiceNumber];
  const att = state.attached[slot];

  if (!att) {
    return `
      <div class="v62-drop-zone" data-invoice="${invoiceNumber}" data-slot="${slot}">
        <div class="dz-icon">📎</div>
        <div class="dz-title">Drop ${slot} here</div>
        <div class="dz-subtitle">PDF / JPG / PNG / HEIC · or click to browse</div>
      </div>`;
  }
  if (att.stage === 'uploading') {
    return `
      <div class="v62-drop-zone uploading">
        <div class="v62-dz-status-row">
          <div class="v62-dz-status-icon"><span class="v62-small-spinner"></span></div>
          <div class="v62-dz-info">
            <div class="v62-dz-file-name">${escHtml(att.name)}</div>
            <div class="v62-dz-file-status">Reading file…</div>
          </div>
        </div>
      </div>`;
  }
  if (att.stage === 'verifying') {
    return `
      <div class="v62-drop-zone verifying">
        <div class="v62-dz-status-row">
          <div class="v62-dz-status-icon"><span class="v62-small-spinner"></span></div>
          <div class="v62-dz-info">
            <div class="v62-dz-file-name">${escHtml(att.name)}</div>
            <div class="v62-dz-file-status">🔍 Verifying document type with Claude AI…</div>
          </div>
        </div>
      </div>`;
  }
  if (att.stage === 'mismatch') {
    return `
      <div class="v62-drop-zone mismatch">
        <div class="v62-dz-status-row">
          <div class="v62-dz-status-icon">⚠</div>
          <div class="v62-dz-info">
            <div class="v62-dz-file-name">${escHtml(att.name)}</div>
            <div class="v62-dz-file-status">Looks like a ${escHtml(att.detectedAs || 'different doc')}, not a ${slot}</div>
          </div>
          <button class="v62-dz-replace-btn" onclick="window.invClearAttachment('${invoiceNumber}','${slot}')">Replace</button>
        </div>
        <div style="margin-top:8px; font-size:0.76rem; color:#78350f;">
          <a href="#" onclick="window.invForceAttach('${invoiceNumber}','${slot}'); return false;" style="color:#c2410c;">Use anyway →</a>
        </div>
      </div>`;
  }
  if (att.stage === 'attached' || att.stage === 'attached_manual') {
    const label = att.stage === 'attached_manual'
      ? `✓ Manually confirmed · ready to retry`
      : att.verifiedBy === 'ai'
      ? `✓ ${slot} verified by AI · ready to retry`
      : `✓ ${slot} verified by filename · ready to retry`;
    return `
      <div class="v62-drop-zone attached">
        <div class="v62-dz-status-row">
          <div class="v62-dz-status-icon">✓</div>
          <div class="v62-dz-info">
            <div class="v62-dz-file-name">${escHtml(att.name)}</div>
            <div class="v62-dz-file-status">${label}</div>
          </div>
          <button class="v62-dz-replace-btn" onclick="window.invClearAttachment('${invoiceNumber}','${slot}')">Replace</button>
        </div>
      </div>`;
  }
  return '';
}

function bindDropZone(invoiceNumber, slot) {
  const zone = document.querySelector(`.v62-drop-zone[data-invoice="${invoiceNumber}"][data-slot="${slot}"]`);
  if (!zone) return;
  const handleFile = (file) => acceptFile(invoiceNumber, slot, file);

  zone.onclick = () => {
    const inp = document.createElement('input');
    inp.type = 'file';
    inp.accept = '.pdf,.jpg,.jpeg,.png,.heic,.heif';
    inp.onchange = (e) => { if (e.target.files[0]) handleFile(e.target.files[0]); };
    inp.click();
  };
  zone.ondragover = (e) => { e.preventDefault(); zone.classList.add('drag-over'); };
  zone.ondragleave = () => zone.classList.remove('drag-over');
  zone.ondrop = (e) => {
    e.preventDefault();
    zone.classList.remove('drag-over');
    if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
  };
}

async function acceptFile(invoiceNumber, slot, file) {
  const state = sendState.retry[invoiceNumber];
  state.attached[slot] = { name: file.name, file, stage: 'uploading' };
  renderPanel();

  // Tier 1: filename match
  if (matchesByFilename(file.name, slot)) {
    state.attached[slot] = { ...state.attached[slot], stage: 'attached', verifiedBy: 'filename' };
    renderPanel();
    return;
  }

  // Tier 2: Claude AI
  state.attached[slot] = { ...state.attached[slot], stage: 'verifying' };
  renderPanel();
  try {
    const result = await agentBridge.verifyFile(slot, file);
    if (result.matches_slot) {
      state.attached[slot] = { ...state.attached[slot], stage: 'attached', verifiedBy: 'ai' };
    } else {
      state.attached[slot] = { ...state.attached[slot], stage: 'mismatch', detectedAs: result.detected_as };
    }
  } catch (err) {
    // Fall through to attached on verify failure — let user proceed
    state.attached[slot] = { ...state.attached[slot], stage: 'attached', verifiedBy: 'manual' };
  }
  renderPanel();
}

function clearAttachment(invoiceNumber, slot) {
  const state = sendState.retry[invoiceNumber];
  delete state.attached[slot];
  renderPanel();
}

function forceAttach(invoiceNumber, slot) {
  const state = sendState.retry[invoiceNumber];
  const cur = state.attached[slot];
  if (!cur) return;
  state.attached[slot] = { ...cur, stage: 'attached_manual', verifiedBy: 'manual' };
  renderPanel();
}

function skipRow(invoiceNumber) {
  const row = invoiceState.invoices.find(r => r.invoiceNumber === invoiceNumber);
  if (row) row.sendStatus = 'skipped';
  closePanel();
  renderResults();
}

window.invRenderFixItSection = renderFixItSection;
window.invClearAttachment = clearAttachment;
window.invForceAttach = forceAttach;
window.invSkipRow = skipRow;
```

- [ ] **Step 2: Smoke test**

Reload app. Trigger a missing-POD failure. Click row. Confirm:
- Side panel shows drop zone for POD
- Drag a PDF named `pod_test.pdf` → instant ✓ attached (filename match)
- Drag a PDF named `random.pdf` → "Verifying… with Claude AI" → Claude returns classification → either ✓ attached or ⚠ mismatch

- [ ] **Step 3: Commit**

```bash
git add app/assets/js/tools/invoice-sender/invoice-sender-results.js app/assets/js/shared/agent-client.js
git commit -m "feat(invoice-sender/v62): drop zones with filename-then-AI verification (Fix 2)"
```

---

## Task 11: Frontend — Retry flow + auto-advance + bulk retry

**Files:**
- Modify: `app/assets/js/tools/invoice-sender/invoice-sender-results.js`

- [ ] **Step 1: Add retry orchestration**

Append:

```js
async function startRetry(invoiceNumber) {
  const row = invoiceState.invoices.find(r => r.invoiceNumber === invoiceNumber);
  const state = sendState.retry[invoiceNumber];
  if (!row || !state) return;

  state.panelStage = 'retrying';
  renderPanel();
  animateRetrySteps();

  const slotFiles = Object.entries(state.attached)
    .filter(([_, a]) => a && (a.stage === 'attached' || a.stage === 'attached_manual'))
    .map(([slot, a]) => ({ slot, file: a.file, verifiedBy: a.verifiedBy || 'manual' }));

  const invoicePayload = {
    invoice_number: row.invoiceNumber,
    invoice_id: row.invoiceId || row.qboInvoiceId,
    container_number: row.containerNumber,
    customer_code: row.customerCode,
    subject: row.subject || row.subjectOverride,
    do_sender_email: row.doSenderEmail,
  };

  try {
    const result = await agentBridge.retryInvoice(invoicePayload, slotFiles);
    if (result.status === 'sent') {
      row.sendStatus = 'sent';
      row.sentAt = new Date().toISOString();
      state.panelStage = 'success';
      renderPanel();
      renderResults();
      updateBulkRetryButton();
    } else {
      state.panelStage = 'fix';
      renderPanel();
      alert(`Retry failed: ${result.error || 'Unknown error'}`);
    }
  } catch (err) {
    state.panelStage = 'fix';
    renderPanel();
    alert(`Retry error: ${err.message}`);
  }
}

function animateRetrySteps() {
  const steps = ['Sending email with attachments…', 'Saving file to QuickBooks in background…'];
  const stepsEl = document.getElementById('invRetrySteps');
  if (!stepsEl) return;
  stepsEl.innerHTML = steps.map(s => `<div>⟳ ${s}</div>`).join('');
}

window.invStartRetry = startRetry;
```

- [ ] **Step 2: Add bulk retry**

Append:

```js
function getReadyToRetryRows() {
  return getFailedRows().filter(r => {
    const s = sendState.retry[r.invoiceNumber];
    if (!s) return false;
    if (r.sendStatus === 'error' && (r.missingDocs || []).length === 0) return true; // transient — always retriable
    return (r.missingDocs || []).every(d => {
      const slotName = (SLOT_LABELS[d.toLowerCase()] || d.toUpperCase());
      const a = s.attached[slotName];
      return a && (a.stage === 'attached' || a.stage === 'attached_manual');
    });
  });
}

function updateBulkRetryButton() {
  const ready = getReadyToRetryRows();
  const btn = document.getElementById('invSendBulkRetryBtn');
  const count = document.getElementById('invSendBulkCount');
  count.textContent = ready.length;
  btn.disabled = ready.length === 0;
  btn.onclick = async () => {
    btn.disabled = true;
    for (const row of ready) {
      await startRetry(row.invoiceNumber);
    }
    updateBulkRetryButton();
  };
}

// Call updateBulkRetryButton whenever results render
const _origRenderResults = renderResults;
renderResults = function() {
  _origRenderResults();
  updateBulkRetryButton();
};
window.invRenderResults = renderResults;
```

(Note: re-export of `renderResults` is awkward in module scope. Easier: just call `updateBulkRetryButton()` at the end of the existing `renderResults` function instead. Edit the existing `renderResults` to append `updateBulkRetryButton()` before its closing brace, and remove this monkey-patch block.)

- [ ] **Step 3: Smoke test**

Trigger 3 missing-doc failures. Drop a valid POD in row 1, valid POD in row 2, leave row 3 unattached.
- Bulk retry button shows "↻ Retry All Fixed (2)"
- Click bulk button → rows 1 and 2 retry sequentially
- Row 3 stays in Needs Attention
- Single-row retry from sidebar still works
- After single retry success → side panel shows success state → 1.5s later, auto-advances to next failure

- [ ] **Step 4: Commit**

```bash
git add app/assets/js/tools/invoice-sender/invoice-sender-results.js
git commit -m "feat(invoice-sender/v62): retry flow + auto-advance + bulk retry (Fix 2)"
```

---

## Task 12: Pre-send validation cards + live counter (Fix 1 part 2)

**Files:**
- Modify: `app/assets/js/tools/invoice-sender/invoice-sender.js`

- [ ] **Step 1: Locate pre-send view**

In `invoice-sender.js`, find `invRenderTable()` (line 305) — this is the pre-send table. Above the table itself, the file already has a setup grid. Find the section between CSV load and the Send button.

- [ ] **Step 2: Add validation summary banner**

Above `invRenderTable`'s output, before the existing table, insert a summary banner block. Find where the table is appended/rendered and prepend:

```js
function invRenderValidationBanner() {
  const inv = invoiceState.invoices;
  if (!inv.length) return '';

  const noCustomer = inv.filter(r => r.validationStatus === 'no_customer_match');
  const noEmail = inv.filter(r => r.validationStatus === 'no_email');
  const ready = inv.filter(r => r.validationStatus === 'ready');

  const blockers = noCustomer.length + noEmail.length;
  if (blockers === 0) return ''; // No banner needed

  return `
    <div class="v62-alert-banner" style="margin-bottom:14px;">
      <div class="v62-alert-icon">!</div>
      <div class="v62-alert-text">
        <strong>${blockers} invoice${blockers === 1 ? '' : 's'} need${blockers === 1 ? 's' : ''} attention before sending.</strong>
        <small>
          ${noCustomer.length > 0 ? `${noCustomer.length} unknown customer code · ` : ''}
          ${noEmail.length > 0 ? `${noEmail.length} no email configured · ` : ''}
          ${ready.length} ready to send
        </small>
      </div>
    </div>
  `;
}
```

Call this from `invRenderTable()` — prepend its output to the table HTML.

- [ ] **Step 3: Make Send button show live count**

Find the Send button render in `invoice-sender.js`. Replace its label with a dynamic count:

```js
const readyCount = invoiceState.invoices.filter(r => r.validationStatus === 'ready' && r.sendStatus !== 'sent').length;
btn.textContent = `✉ Send ${readyCount} Ready Invoice${readyCount === 1 ? '' : 's'}`;
btn.disabled = readyCount === 0;
```

- [ ] **Step 4: Smoke test**

Load a CSV with 5 invoices where 1 has unknown customer + 1 has no email. Confirm:
- Validation banner appears: "2 invoices need attention before sending."
- Send button reads "✉ Send 3 Ready Invoices"
- Sending still works for the 3 ready rows

- [ ] **Step 5: Commit**

```bash
git add app/assets/js/tools/invoice-sender/invoice-sender.js
git commit -m "feat(invoice-sender/v62): pre-send validation banner + live Send count (Fix 1)"
```

---

## Task 13: Rebuild + ship v2.62.0

**Files:**
- Modify: `desktop/release-notes-v2.62.0.md` (new)

- [ ] **Step 1: Write release notes**

Create `desktop/release-notes-v2.62.0.md`:

```markdown
## Invoice Sender — major UX overhaul (Fix 1 + Fix 2)

Two bugs Lorena hit on her 60-invoice batch finally fixed.

### Fix 1 — Clearer errors + volume-friendly UI
- Failed rows now show **specific badges**: ⚠ POD Missing, ⚠ BOL Missing, ⚡ QBO Error — no more generic "No Attachments"
- After-send view splits into **tabs**: ⚠ Needs Attention (default) · ✓ Sent · All Invoices — find the 3 broken rows out of 120 in one click
- **Alert banner** at the top: "3 invoices need your attention. The other 117 sent successfully."
- **Click a failed row** → side panel slides in with plain-English explanation, what-we-checked list, and what-to-do guidance
- Pre-send: validation banner highlights blockers; Send button shows live ready count

### Fix 2 — In-app fix + retry (no more leaving the app)
- Each failed row gets an **Action button**: `↻ Retry` for transient errors, `📎 Attach & Retry` for missing docs
- **Drop zone per missing doc** in the side panel — accepts PDF, JPG, PNG, HEIC
- **Smart verification**: filename match first (instant), Claude AI fallback when filename is ambiguous
- **Wrong-doc detection**: Claude flags mismatches (e.g. you dropped a BOL in the POD slot) with a "Use anyway" override
- **Background QBO upload**: file goes into your retry email instantly, then a fire-and-forget save to QuickBooks so it persists for future reference — no added wait
- **Auto-advance**: after a successful retry, the next failure opens automatically
- **Bulk retry**: "↻ Retry All Fixed (N)" button on the alert banner retries every row whose files are attached

No backend behaviour changes for the standard send path — this is the recovery flow for when something goes wrong.
```

- [ ] **Step 2: Verify all backend tests still pass**

```bash
agent/venv/Scripts/python.exe -m pytest agent/tests/ -v
```

Expected: all tests pass (existing + new).

- [ ] **Step 3: Manual smoke test in dev mode**

```bash
cd desktop && npm start
```

- Load a test CSV with at least one failing customer (APEXMA01 with missing POD if test data available)
- Click Send
- Confirm results view appears
- Click a failed row → side panel opens with diagnostic
- Drop a PDF → verifies → retry → success → auto-advance
- Try a bulk retry path
- Close app

- [ ] **Step 4: Run rebuild via runbuild.bat**

Following `feedback_use_runbuild_for_rebuild.md`:

```powershell
$emptyFile = "$env:USERPROFILE\Desktop\NGL ACCOUNTING SERVICE\desktop\empty-stdin.txt"
if (-not (Test-Path $emptyFile)) { New-Item -Path $emptyFile -ItemType File -Force | Out-Null }
Start-Process -FilePath "$env:USERPROFILE\Desktop\NGL ACCOUNTING SERVICE\desktop\runbuild.bat" `
  -WorkingDirectory "$env:USERPROFILE\Desktop\NGL ACCOUNTING SERVICE\desktop" `
  -NoNewWindow -Wait `
  -RedirectStandardInput $emptyFile `
  -RedirectStandardOutput "$env:USERPROFILE\Desktop\NGL ACCOUNTING SERVICE\desktop\build-log-2.62.txt" `
  -RedirectStandardError "$env:USERPROFILE\Desktop\NGL ACCOUNTING SERVICE\desktop\build-log-2.62.txt.err"
```

Expected: `desktop/dist/NGL_ACCOUNTING_INSTALLER_v2.62.0.exe` and `desktop/dist/latest.yml` exist after completion.

- [ ] **Step 5: Push + GH release (per `feedback_always_push_and_release.md`)**

```bash
git push origin main
gh release create v2.62.0 \
  --title "v2.62.0 — Invoice Sender UX overhaul (Fix 1 + Fix 2)" \
  --notes-file desktop/release-notes-v2.62.0.md \
  desktop/dist/NGL_ACCOUNTING_INSTALLER_v2.62.0.exe \
  desktop/dist/latest.yml
```

- [ ] **Step 6: Verify the release**

```bash
gh release view v2.62.0
```

Expected: shows v2.62.0 as latest with the installer + `latest.yml` attached.

---

## Self-Review

**Spec coverage:**
- Fix 1 specific badges ✓ Task 8
- Fix 1 side panel diagnostic ✓ Task 9
- Fix 1 tabs ✓ Task 8
- Fix 1 alert banner ✓ Task 8
- Fix 1 pre-send validation + live counter ✓ Task 12
- Fix 1 prev/next nav — implicit via auto-advance (Task 9, 11)
- Fix 1 setup panel collapse — out of scope for v2.62, deferred
- Fix 1 auto-revalidation toast — out of scope for v2.62, deferred
- Fix 1 live elapsed timer + ETA — out of scope for v2.62, deferred (existing progress bar covers the basics)
- Fix 2 inline action buttons ✓ Task 8
- Fix 2 stacked drop zones ✓ Task 10
- Fix 2 smart-tier verification (filename → Claude → mismatch) ✓ Task 10
- Fix 2 image format support ✓ Task 2
- Fix 2 retry progress view ✓ Task 11 (simple version)
- Fix 2 auto-advance ✓ Task 9 (1.5s timer, locked decision)
- Fix 2 bulk retry ✓ Task 11
- Fix 2 skip button ✓ Task 10 (silent, locked decision)
- Background QBO upload ✓ Task 4 (locked decision)
- Existing classifier reused ✓ Task 4 imports from existing classifier module
- ar@ngltrans.net CC preserved ✓ Task 3 helper carries CC list through
- Duplicate-send guard bypass — N/A, retry job uses its own path that doesn't hit the guard

**Placeholders scanned:** none. All steps have real code.

**Type consistency:**
- `sendState.retry[invoiceNumber].attached[slot]` shape: `{ name, file, stage, verifiedBy?, detectedAs? }` — consistent across acceptFile, renderDropZone, startRetry
- `slot` value is always uppercase ('POD', 'BOL', etc.) when used in `attached` keys and slot files — verified
- Backend slot conversion: routes lowercase ('pod') in classifier, uppercase ('POD') in router/job — bridged via `SLOT_TO_DOCTYPE` map in router
- `_assemble_and_send_email` signature consistent between send_qbo_api and retry_job

**Missing safeguards:**
- One thing not covered in plan but worth knowing: SSE event named `invoice_skipped_no_attachments` may still exist somewhere on the server side. Should be left as-is — the new client just handles both old + new event names via the `missing_docs` status mapping. Server-side rename can be a follow-up.
