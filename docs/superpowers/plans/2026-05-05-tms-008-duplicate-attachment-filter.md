# TMS-008 Duplicate Attachment Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the Invoice Sender from emailing the same POD multiple times to a customer when TMS uploads duplicate `Attachable` records to QBO. Apply a send-time dedup filter at all four call sites that handle QBO attachments, plus surface a small inline note in the UI when duplicates were skipped.

**Architecture:** One pure helper at `agent/services/qbo_api/dedup.py` with a single function `dedupe_attachments(attachments) -> (kept, skipped)`. Match key is `(filename.lower().strip(), size_int)`; tie-breaker is highest `int(att["id"])`. Stable input order is preserved. The four call sites in `agent/services/job_manager/` apply the helper, log INFO when `skipped > 0`, and emit an `attachments_deduped` SSE event so the Invoice Sender UI can render `"K duplicate attachments skipped"` on the affected row.

**Tech Stack:** Python 3.11+ (FastAPI agent on localhost:8787), `pytest` + `pytest-asyncio`, vanilla JS web app at `app/assets/js/tools/invoice-sender/invoice-sender.js`. Existing `_emit_send(job, type, payload)` pattern is the SSE plumbing; existing `_sendEventHandlers` map in `invoice-sender.js` is the frontend dispatch.

**Spec reference:** `docs/superpowers/specs/2026-05-05-workarounds-registry-and-tms-dup-filter-design.md` — Deliverable 2.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `agent/services/qbo_api/dedup.py` | Create | Pure helper: `dedupe_attachments(list[dict]) -> tuple[list[dict], list[dict]]`. No I/O, no logging. |
| `agent/tests/test_qbo_api_dedup.py` | Create | Unit tests for the helper. Covers screenshot pattern, casing/whitespace, same-name-different-size, empty list, ID-as-int comparison. |
| `agent/services/job_manager/send_qbo_api.py` | Modify | Wire dedup at lines ~178 and ~205 (after each `check_attachments`). Log + emit SSE when skipped > 0. The customer-visible bug fix. |
| `agent/services/job_manager/send_oec.py` | Modify | Wire dedup before POD selection at line ~58. Pick newest POD via highest-ID rule. Log + emit SSE when skipped > 0. |
| `agent/services/job_manager/send_portal.py` | Modify | Wire dedup before POD selection at line ~82. Pick newest POD via highest-ID rule. Log + emit SSE when skipped > 0. |
| `agent/services/job_manager/fetch_job.py` | Modify | Wire dedup at line ~133, replace `next(...)` with newest-ID POD pick. Log only (no SSE — fetch flow uses `_emit`, not `_emit_send`). |
| `agent/tests/test_job_manager/test_send_qbo_api_tms_data.py` | Modify | New integration test: `check_attachments` returns 5 duplicates → `email_attachments` contains no duplicate filenames. |
| `app/assets/js/tools/invoice-sender/invoice-sender.js` | Modify | Add `attachments_deduped` handler. Store note on the invoice row; render under the status badge. |
| `docs/tms-workarounds.md` | Modify | TMS-008 entry: Status `In progress` → `Active`. Replace spec-link line with concrete file refs. Update Quick Lookup table row. |
| `desktop/VERSION` | Modify | Bump version (mandatory rebuild pipeline). |

---

## Task 1: Create the dedup helper (TDD)

**Files:**
- Create: `agent/tests/test_qbo_api_dedup.py`
- Create: `agent/services/qbo_api/dedup.py`

- [ ] **Step 1: Write the failing tests**

Create `agent/tests/test_qbo_api_dedup.py` with the full content below. Six tests cover every edge case named in the spec.

```python
"""Unit tests for agent/services/qbo_api/dedup.py — TMS-008 dedup helper."""

from pathlib import Path
import sys

# Add agent/ to path so imports work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.qbo_api.dedup import dedupe_attachments


def _att(att_id, filename, size, doc_type="pod"):
    return {
        "id": att_id,
        "fileName": filename,
        "size": size,
        "contentType": "application/pdf",
        "tempDownloadUri": None,
        "docType": doc_type,
    }


class TestDedupeAttachments:
    def test_screenshot_pattern_5x_same_pod_keeps_highest_id(self):
        """The exact bug from the screenshot: 5x identical filename+size, distinct IDs."""
        atts = [
            _att("1001", "mm2603020032_ite_1775833088165.pdf", 13_312),
            _att("1002", "mm2603020032_ite_1775833088165.pdf", 13_312),
            _att("1003", "mm2603020032_ite_1775833088165.pdf", 13_312),
            _att("1004", "mm2603020032_ite_1775833088165.pdf", 13_312),
            _att("1005", "mm2603020032_ite_1775833088165.pdf", 13_312),
        ]
        kept, skipped = dedupe_attachments(atts)
        assert len(kept) == 1
        assert kept[0]["id"] == "1005"
        assert len(skipped) == 4
        assert {a["id"] for a in skipped} == {"1001", "1002", "1003", "1004"}

    def test_same_filename_different_size_both_kept(self):
        """Real revision (different size) is not a duplicate — both kept."""
        atts = [
            _att("100", "pod.pdf", 5_000),
            _att("101", "pod.pdf", 7_500),
        ]
        kept, skipped = dedupe_attachments(atts)
        assert len(kept) == 2
        assert skipped == []

    def test_empty_list(self):
        kept, skipped = dedupe_attachments([])
        assert kept == []
        assert skipped == []

    def test_filename_casing_and_whitespace_normalized(self):
        """Match key is (filename.lower().strip(), size) — casing and whitespace ignored."""
        atts = [
            _att("200", "POD.pdf", 1234),
            _att("201", "pod.pdf", 1234),
            _att("202", "  pod.pdf  ", 1234),
        ]
        kept, skipped = dedupe_attachments(atts)
        assert len(kept) == 1
        assert kept[0]["id"] == "202"
        assert len(skipped) == 2

    def test_id_compared_as_int_not_string(self):
        """QBO IDs are strings of digits; '100' < '99' lexicographically but 100 > 99 as int."""
        atts = [
            _att("99",  "pod.pdf", 1000),
            _att("100", "pod.pdf", 1000),
            _att("9",   "pod.pdf", 1000),
        ]
        kept, skipped = dedupe_attachments(atts)
        assert len(kept) == 1
        assert kept[0]["id"] == "100"
        assert {a["id"] for a in skipped} == {"99", "9"}

    def test_stable_order_preserved_for_kept(self):
        """When no duplicates, output order matches input order."""
        atts = [
            _att("1", "a.pdf", 100),
            _att("2", "b.pdf", 200),
            _att("3", "c.pdf", 300),
        ]
        kept, skipped = dedupe_attachments(atts)
        assert [a["id"] for a in kept] == ["1", "2", "3"]
        assert skipped == []

    def test_kept_order_preserves_first_appearance_position(self):
        """For mixed dupes + uniques, kept retains the position of the first occurrence
        of each match key (even if a later occurrence wins the ID tie-breaker)."""
        atts = [
            _att("10", "a.pdf", 100),       # group A — first appearance
            _att("20", "b.pdf", 200),
            _att("30", "a.pdf", 100),       # group A — wins tie-breaker (highest id), same position as id=10
            _att("40", "c.pdf", 300),
        ]
        kept, skipped = dedupe_attachments(atts)
        # 'a.pdf' winner is id=30 but slots into position 0 (where id=10 was)
        assert [a["fileName"] for a in kept] == ["a.pdf", "b.pdf", "c.pdf"]
        assert kept[0]["id"] == "30"
        assert len(skipped) == 1
        assert skipped[0]["id"] == "10"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd agent && python -m pytest tests/test_qbo_api_dedup.py -v`
Expected: All 7 tests FAIL with `ModuleNotFoundError: No module named 'services.qbo_api.dedup'`.

- [ ] **Step 3: Implement the helper**

Create `agent/services/qbo_api/dedup.py`:

```python
"""TMS-008 attachment dedup helper.

# WORKAROUND(TMS-008): see docs/tms-workarounds.md

When any new document is uploaded to a TMS work order, TMS re-uploads ALL prior
documents to the linked QBO invoice, producing exact-duplicate Attachable
records. This helper drops duplicates by (filename.lower().strip(), size) so
the Invoice Sender doesn't email five copies of the same POD.

Pure: no I/O, no logging. Caller is responsible for logging the outcome.
"""


def dedupe_attachments(attachments: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (kept, skipped).

    Two attachments are duplicates if (filename.lower().strip(), size) match.
    Tie-breaker: keep the attachment with the highest int(id) — QBO IDs are
    monotonic, so highest = most recent upload. IDs are compared as ints, NOT
    strings (QBO returns IDs as digit strings of varying lengths).

    Stable order: kept preserves the position of the first occurrence of each
    match key in the input list, even when a later occurrence wins the
    tie-breaker.
    """
    if not attachments:
        return [], []

    # Group indexes by match key, preserving first-occurrence ordering.
    groups: dict[tuple[str, int], list[int]] = {}
    order: list[tuple[str, int]] = []
    for idx, att in enumerate(attachments):
        key = ((att.get("fileName") or "").lower().strip(), att.get("size") or 0)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(idx)

    kept: list[dict] = []
    skipped: list[dict] = []
    for key in order:
        idxs = groups[key]
        if len(idxs) == 1:
            kept.append(attachments[idxs[0]])
            continue
        # Pick winner by highest int(id); rest go to skipped.
        winner_idx = max(idxs, key=lambda i: int(attachments[i].get("id") or 0))
        kept.append(attachments[winner_idx])
        for i in idxs:
            if i != winner_idx:
                skipped.append(attachments[i])

    return kept, skipped
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd agent && python -m pytest tests/test_qbo_api_dedup.py -v`
Expected: All 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/services/qbo_api/dedup.py agent/tests/test_qbo_api_dedup.py
git commit -m "feat(qbo_api): add TMS-008 attachment dedup helper

Pure helper at agent/services/qbo_api/dedup.py — drops duplicate QBO
Attachable records by (filename.lower().strip(), size) match key.
Tie-breaker: highest int(id) wins (QBO IDs are monotonic).
"
```

---

## Task 2: Wire send_qbo_api.py — the customer-visible bug

**Files:**
- Modify: `agent/services/job_manager/send_qbo_api.py:178`, `:205`

This is the actual bug from the screenshot. After both `check_attachments` calls (initial + post-TMS-cascade) we apply dedup, log when `skipped > 0`, and emit the SSE event.

- [ ] **Step 1: Add the helper import + dedup-and-emit utility at the top of `send_qbo_api.py`**

Open `agent/services/job_manager/send_qbo_api.py`. Replace lines 1-18 (the current imports + logger) with:

```python
"""QBO API send mixin — hybrid: QBO API for lookup/verify + Gmail SMTP for send."""

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from config import RESEND_NOTICE, TMS_FETCH_TIMEOUT_S
from services.email_template import build_invoice_email_html
from services.job_manager.util import (
    normalize_email_list,
    validate_and_append_email,
)
from services.qbo_api.dedup import dedupe_attachments

logger = logging.getLogger("ngl.job_manager")
```

- [ ] **Step 2: Add a private `_dedup_and_emit` helper to the mixin**

Inside `class SendQBOApiMixin:`, after the existing `_cleanup_temp` staticmethod (line 27), add this method. It centralizes the dedup + log + SSE emit pattern reused by all three job_manager send paths.

```python
    async def _dedup_and_emit(self, job, invoice_number: str,
                              attachments: list[dict]) -> list[dict]:
        """Run dedupe_attachments, log INFO if any skipped, and emit SSE event.

        Returns the kept list. SSE event 'attachments_deduped' is emitted only
        when skipped > 0.
        """
        kept, skipped = dedupe_attachments(attachments)
        if skipped:
            logger.info(
                "Deduped attachments for %s: kept %d of %d (skipped %d TMS duplicates)",
                invoice_number, len(kept), len(attachments), len(skipped),
            )
            await self._emit_send(job, "attachments_deduped", {
                "invoiceNumber": invoice_number,
                "kept": len(kept),
                "skipped": len(skipped),
                "skippedFiles": [a.get("fileName", "") for a in skipped],
            })
        return kept
```

- [ ] **Step 3: Wire dedup at the first `check_attachments` call (line ~178)**

Find this block in `_send_qbo_api` (currently around line 175-178):

```python
        att_check = await api.check_attachments(invoice_id, required_docs)
        result.attachments_found = att_check.get("found", [])
        result.attachments_missing = att_check.get("missing", [])
        all_attachments = att_check.get("attachments", [])
```

Replace the last line with the dedup call:

```python
        att_check = await api.check_attachments(invoice_id, required_docs)
        result.attachments_found = att_check.get("found", [])
        result.attachments_missing = att_check.get("missing", [])
        all_attachments = await self._dedup_and_emit(
            job, invoice.invoice_number, att_check.get("attachments", []),
        )
```

- [ ] **Step 4: Wire dedup at the second `check_attachments` call (line ~205, post-TMS cascade)**

Find this block (currently around lines 202-205):

```python
                if uploaded:
                    att_check = await api.check_attachments(invoice_id, required_docs)
                    result.attachments_found = att_check.get("found", [])
                    result.attachments_missing = att_check.get("missing", [])
                    all_attachments = att_check.get("attachments", [])
```

Replace the last line:

```python
                if uploaded:
                    att_check = await api.check_attachments(invoice_id, required_docs)
                    result.attachments_found = att_check.get("found", [])
                    result.attachments_missing = att_check.get("missing", [])
                    all_attachments = await self._dedup_and_emit(
                        job, invoice.invoice_number, att_check.get("attachments", []),
                    )
```

- [ ] **Step 5: Add the `# WORKAROUND` comment at the entry point (above the first dedup call)**

Add a one-line comment above the `all_attachments = await self._dedup_and_emit(...)` line from Step 3 (the first dedup site is the canonical entry point):

```python
        # WORKAROUND(TMS-008): see docs/tms-workarounds.md — drop duplicate Attachable records before email
        all_attachments = await self._dedup_and_emit(
            job, invoice.invoice_number, att_check.get("attachments", []),
        )
```

- [ ] **Step 6: Quick syntax + import check**

Run: `cd agent && python -c "from services.job_manager.send_qbo_api import SendQBOApiMixin; print('OK')"`
Expected: `OK` printed, no errors.

- [ ] **Step 7: Run the existing send_qbo_api integration test to make sure nothing regressed**

Run: `cd agent && python -m pytest tests/test_job_manager/test_send_qbo_api_tms_data.py -v`
Expected: All existing tests PASS.

- [ ] **Step 8: Commit**

```bash
git add agent/services/job_manager/send_qbo_api.py
git commit -m "fix(send_qbo_api): apply TMS-008 dedup before emailing attachments

Customer-visible bug: TMS re-uploads all prior docs to QBO when any new
document is added, so the same POD got emailed 5x to customers. Now we
dedupe the attachment list at both check points (initial + post-TMS
cascade) before they reach the email send.
"
```

---

## Task 3: Wire send_oec.py — pick newest POD via tie-breaker

**Files:**
- Modify: `agent/services/job_manager/send_oec.py:53-69`

OEC's POD email picks one POD from QBO. Today it picks "first matching"; after dedup it picks the newest (highest-ID) POD from the deduped list — which is the right one when TMS has uploaded both an old corrupted version and a fresh one.

- [ ] **Step 1: Add the helper import**

Open `agent/services/job_manager/send_oec.py`. Replace lines 1-20 with:

```python
"""OEC POD email mixin — sends POD/D-O email BEFORE the QBO invoice email.

As of the OEC flow reorder, this runs FIRST. It sets ``result.pod_status``
(``sent``/``failed``/``skipped``) but does NOT set ``result.status`` —
that's owned by the invoice-email step that runs afterwards.
"""

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

from config import TMS_FETCH_TIMEOUT_S
from services.job_manager.util import (
    normalize_email_list,
    validate_and_append_email,
)
from services.qbo_api.dedup import dedupe_attachments

logger = logging.getLogger("ngl.job_manager")
```

- [ ] **Step 2: Replace the POD-pick block with dedup + newest-by-ID selection**

Find this block (lines 52-69):

```python
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
                    logger.info("POD downloaded from QBO API: %s", pod_path.name)
                break
```

Replace with:

```python
        # Look for POD already attached to QBO before going to TMS.
        att_check = await api.check_attachments(invoice_id, ["invoice", "pod"])
        # WORKAROUND(TMS-008): see docs/tms-workarounds.md — drop duplicate Attachable records, pick newest POD
        all_attachments = await self._dedup_and_emit(
            job, invoice.invoice_number, att_check.get("attachments", []),
        )
        temp_dir = Path(tempfile.mkdtemp(prefix="ngl_pod_"))
        pod_path = None
        pod_source = None

        # Pick the newest POD via highest int(id) — same tie-breaker as the dedup helper.
        pod_candidates = [a for a in all_attachments
                          if a.get("docType") == "pod" and a.get("id")]
        if pod_candidates:
            chosen = max(pod_candidates, key=lambda a: int(a["id"]))
            await self._emit_send(job, "oec_downloading_pod", {
                "invoiceNumber": invoice.invoice_number,
            })
            pod_path = await api.download_attachment(
                chosen["id"], chosen.get("fileName", "pod.pdf"), temp_dir
            )
            if pod_path:
                pod_source = "QBO"
                logger.info("POD downloaded from QBO API: %s", pod_path.name)
```

- [ ] **Step 3: Syntax + import check**

Run: `cd agent && python -c "from services.job_manager.send_oec import SendOECFlowMixin; print('OK')"`
Expected: `OK`.

- [ ] **Step 4: Run the existing OEC tests to make sure nothing regressed**

Run: `cd agent && python -m pytest tests/test_job_manager/test_send_oec_tms_data.py -v`
Expected: All 3 existing tests PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/services/job_manager/send_oec.py
git commit -m "fix(send_oec): pick newest POD via TMS-008 dedup helper

Today the OEC POD email picks the first POD-typed attachment found.
After dedup we have at most one record per (filename, size); when there
are multiple distinct PODs (e.g. corrupted old + fresh new), pick the
highest int(id) — same tie-breaker the dedup helper uses, applied
across all POD candidates in the kept list.
"
```

---

## Task 4: Wire send_portal.py — pick newest POD via tie-breaker

**Files:**
- Modify: `agent/services/job_manager/send_portal.py:79-88`

Same shape as send_oec.py. Portal picks one POD to merge with the invoice PDF. After fix: dedupe, then pick highest-ID.

- [ ] **Step 1: Add the helper import**

Open `agent/services/job_manager/send_portal.py`. Replace lines 1-9 with:

```python
"""Portal upload mixin — download from QBO API, merge, upload to customer portal."""

import logging
import shutil
import tempfile
from pathlib import Path

from services.qbo_api.dedup import dedupe_attachments

logger = logging.getLogger("ngl.job_manager")
```

- [ ] **Step 2: Replace the POD-pick block**

Find this block (lines 78-88):

```python
        # Find and download POD from attachments
        att_check = await api.check_attachments(invoice_id, ["pod"])
        all_attachments = att_check.get("attachments", [])

        pod_path = None
        for att in all_attachments:
            if att.get("docType") == "pod" and att.get("id"):
                pod_path = await api.download_attachment(
                    att["id"], att.get("fileName", "pod.pdf"), temp_dir
                )
                if pod_path:
                    break
```

Replace with:

```python
        # Find and download POD from attachments
        att_check = await api.check_attachments(invoice_id, ["pod"])
        # WORKAROUND(TMS-008): see docs/tms-workarounds.md — drop duplicate Attachable records, pick newest POD
        all_attachments = await self._dedup_and_emit(
            job, invoice.invoice_number, att_check.get("attachments", []),
        )

        pod_path = None
        pod_candidates = [a for a in all_attachments
                          if a.get("docType") == "pod" and a.get("id")]
        if pod_candidates:
            chosen = max(pod_candidates, key=lambda a: int(a["id"]))
            pod_path = await api.download_attachment(
                chosen["id"], chosen.get("fileName", "pod.pdf"), temp_dir
            )
```

- [ ] **Step 3: Syntax + import check**

Run: `cd agent && python -c "from services.job_manager.send_portal import SendPortalUploadMixin; print('OK')"`
Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add agent/services/job_manager/send_portal.py
git commit -m "fix(send_portal): pick newest POD via TMS-008 dedup helper

Same fix as send_oec — dedupe attachment list, then pick highest int(id)
from POD candidates instead of first match. Defensive: if TMS uploaded
a corrupted 0-byte duplicate, we won't merge that into the portal PDF.
"
```

---

## Task 5: Wire fetch_job.py — pick newest POD via tie-breaker

**Files:**
- Modify: `agent/services/job_manager/fetch_job.py:133-134`

The fetch flow uses `_emit` (not `_emit_send`) — the SSE channel is different. Per spec, fetch logs only; no `attachments_deduped` event is emitted from this path (the Invoice Sender UI doesn't watch this channel). We still apply dedup so that a corrupted duplicate POD doesn't get picked.

- [ ] **Step 1: Read the import block of fetch_job.py to find the right insertion point**

Run: `cd agent && python -c "
import ast, pathlib
src = pathlib.Path('services/job_manager/fetch_job.py').read_text()
print(src.splitlines()[:30])
"`
Expected: a printable list of the first 30 lines.

(The exact existing import lines need to be left alone; only one new line is added.)

- [ ] **Step 2: Add the helper import**

In `agent/services/job_manager/fetch_job.py`, find the existing `from services...` imports near the top of the file. Add this import line in the same import group (alphabetical placement is fine):

```python
from services.qbo_api.dedup import dedupe_attachments
```

- [ ] **Step 3: Replace the POD-pick block at line ~133**

Find this block (lines 133-134):

```python
            attachments = await api.list_attachments(invoice_id)
            pod_att = next((a for a in attachments if a.get("docType") == "pod"), None)
```

Replace with:

```python
            attachments = await api.list_attachments(invoice_id)
            # WORKAROUND(TMS-008): see docs/tms-workarounds.md — drop duplicate Attachable records, pick newest POD
            kept, skipped = dedupe_attachments(attachments)
            if skipped:
                logger.info(
                    "Deduped attachments for %s: kept %d of %d (skipped %d TMS duplicates)",
                    container.invoice_number, len(kept), len(attachments), len(skipped),
                )
            pod_candidates = [a for a in kept if a.get("docType") == "pod"]
            pod_att = max(pod_candidates, key=lambda a: int(a["id"])) if pod_candidates else None
```

- [ ] **Step 4: Syntax + import check**

Run: `cd agent && python -c "from services.job_manager.fetch_job import FetchJobMixin; print('OK')"`
Expected: `OK` (the actual class name may differ — if the import fails with `ImportError: cannot import name`, run `cd agent && python -c "import services.job_manager.fetch_job; print('OK')"` instead).

- [ ] **Step 5: Commit**

```bash
git add agent/services/job_manager/fetch_job.py
git commit -m "fix(fetch_job): pick newest POD via TMS-008 dedup helper

Replace next(...) first-match with dedupe + highest-int(id). No SSE
event here — fetch flow uses _emit (job-events channel), not _emit_send
(send-events channel watched by Invoice Sender UI). Log only.
"
```

---

## Task 6: Integration test — assert no duplicate filenames in email_attachments

**Files:**
- Modify: `agent/tests/test_job_manager/test_send_qbo_api_tms_data.py`

Add one new test that drives the real send_qbo_api flow with 5 duplicate attachments returned from `check_attachments` and asserts the resulting `email_attachments` list (passed to `email_sender.send_invoice_email`) contains no duplicate filenames.

- [ ] **Step 1: Write the failing integration test**

Open `agent/tests/test_job_manager/test_send_qbo_api_tms_data.py` and append the following at the end of the file:

```python


@pytest.mark.asyncio
async def test_dedup_drops_5x_duplicate_pod_before_email(tmp_path, monkeypatch):
    """TMS-008: 5 duplicate POD Attachables → email_attachments has only the invoice PDF + 1 POD."""
    from services.job_manager import JobManager, SendResult
    from services.qbo_api import QBOApiClient

    jm = JobManager(QBOApiClient(), classifier=MagicMock())
    jm.set_tms_data(None)  # skip TMS cascade — only test the dedup at line ~178
    jm._tms = MagicMock(name="tms_browser_should_not_be_called")
    jm._email_sender = AsyncMock()
    jm._email_sender.send_invoice_email = AsyncMock(return_value={"sent": True})
    jm._emit_send = AsyncMock()
    jm._wait_for_approval = AsyncMock(return_value=True)
    jm._qbo_api = AsyncMock()
    jm._qbo_api.search_invoice = AsyncMock(return_value={
        "Id": "1", "DocNumber": "INV-1", "DueDate": "2026-05-15",
        "TotalAmt": 100, "CustomerRef": {"name": "[CODE] Test Customer"},
        "CustomField": [{"Name": "NGL REF#", "StringValue": "WO/Y"}],
    })
    jm._qbo_api.verify_invoice_details = AsyncMock(return_value={
        "verified": True, "found_container": "ABCU1",
    })
    # 5 duplicates of the same POD: same filename, same size, distinct IDs.
    duplicates = [
        {"id": str(1000 + i), "fileName": "mm2603020032_ite_1775833088165.pdf",
         "size": 13_312, "contentType": "application/pdf",
         "tempDownloadUri": None, "docType": "pod"}
        for i in range(5)
    ]
    jm._qbo_api.check_attachments = AsyncMock(return_value={
        "found": ["pod"], "missing": [], "allPresent": True,
        "attachments": duplicates,
    })
    jm._qbo_api.download_invoice_pdf = AsyncMock(return_value=b"%PDF-invoice")
    jm._qbo_api.get_invoice_link = AsyncMock(return_value="https://qbo.example/pay/1")

    # Mock httpx so the attachment download for the kept POD succeeds.
    import httpx
    class _FakeResp:
        status_code = 200
        content = b"%PDF-pod-data"
    class _FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, *a, **kw): return _FakeResp()
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    job = MagicMock(id="dup-1", test_mode=False)
    invoice = MagicMock(invoice_number="INV-1", container_number="ABCU1",
                        do_sender_email=None, customer_code="CUST",
                        amount=None, subject=None)
    customer = {"emails": ["customer@example.com"], "ccEmails": [],
                "bccEmails": [], "requiredDocs": [], "sendMethod": "qbo_api"}
    result = SendResult(invoice_number="INV-1", container_number="ABCU1",
                        customer_code="CUST")

    await jm._send_qbo_api(job, invoice, customer, result, 0)

    # Assert the email send was called with no duplicate filenames.
    assert jm._email_sender.send_invoice_email.await_count == 1
    sent_kwargs = jm._email_sender.send_invoice_email.call_args.kwargs
    email_attachments = sent_kwargs["attachments"]
    filenames = [a["filename"] for a in email_attachments]
    assert len(filenames) == len(set(filenames)), (
        f"email_attachments contains duplicate filenames: {filenames}"
    )
    # Specifically: invoice PDF + exactly one POD copy (5 dupes collapsed to 1).
    assert filenames.count("mm2603020032_ite_1775833088165.pdf") == 1

    # Assert the SSE 'attachments_deduped' event was emitted.
    emit_calls = [c for c in jm._emit_send.await_args_list
                  if len(c.args) >= 2 and c.args[1] == "attachments_deduped"]
    assert len(emit_calls) == 1
    payload = emit_calls[0].args[2]
    assert payload["invoiceNumber"] == "INV-1"
    assert payload["kept"] == 1
    assert payload["skipped"] == 4
    assert len(payload["skippedFiles"]) == 4
```

- [ ] **Step 2: Run the test**

Run: `cd agent && python -m pytest tests/test_job_manager/test_send_qbo_api_tms_data.py::test_dedup_drops_5x_duplicate_pod_before_email -v`
Expected: PASS. (If FAIL: debug — likely a missing mock attribute or an httpx flow path that's different from what's mocked.)

- [ ] **Step 3: Run the full test suite to confirm no regressions**

Run: `cd agent && python -m pytest tests/ -v`
Expected: All tests PASS, including the new one.

- [ ] **Step 4: Commit**

```bash
git add agent/tests/test_job_manager/test_send_qbo_api_tms_data.py
git commit -m "test(send_qbo_api): TMS-008 — 5 dup PODs collapse to 1 in email_attachments

Drives the full _send_qbo_api flow with five duplicate Attachables and
asserts that send_invoice_email receives no duplicate filenames and that
the attachments_deduped SSE event is emitted with kept=1, skipped=4.
"
```

---

## Task 7: Frontend — handle `attachments_deduped` SSE event

**Files:**
- Modify: `app/assets/js/tools/invoice-sender/invoice-sender.js`

Add a handler that stores the dedup note on the invoice row and re-renders the table so the note appears under the status badge.

- [ ] **Step 1: Add the `attachments_deduped` handler to the dispatch map**

Open `app/assets/js/tools/invoice-sender/invoice-sender.js`. Find the `// ── TMS flow events ──` section (around line 1043). Insert this handler just before the `tms_fetching_docs` entry (so it lives near the related TMS visibility events):

```javascript
  // ── TMS-008 dedup ──
  attachments_deduped(event) {
    const skipped = event.skipped || 0;
    if (skipped <= 0) return;
    const noun = skipped === 1 ? 'duplicate attachment' : 'duplicate attachments';
    invAddLog('info',
      '  [TMS-008] Skipped ' + skipped + ' ' + noun + ' for ' +
      (event.invoiceNumber || '') +
      (event.skippedFiles && event.skippedFiles.length
        ? ' (' + event.skippedFiles.join(', ') + ')'
        : ''));
    const inv = invoiceState.invoices.find(function(i) {
      return i.invoiceNumber === event.invoiceNumber;
    });
    if (inv) {
      inv.dedupNote = skipped + ' ' + noun + ' skipped';
      invRenderTable();
    }
  },
```

- [ ] **Step 2: Surface `dedupNote` under the Sent badge**

Find the `case 'sent':` branch in the `invRenderTable` switch (around line 374-377):

```javascript
        case 'sent':
          statusHtml = '<span class="status-badge status-sent">Sent</span>';
          if (inv.sentAt) statusHtml += '<br><span style="font-size:0.68rem; color:#64748b;">' + inv.sentAt + '</span>';
          break;
```

Replace with:

```javascript
        case 'sent':
          statusHtml = '<span class="status-badge status-sent">Sent</span>';
          if (inv.sentAt) statusHtml += '<br><span style="font-size:0.68rem; color:#64748b;">' + inv.sentAt + '</span>';
          if (inv.dedupNote) statusHtml += '<br><span style="font-size:0.68rem; color:#92400e;" title="TMS uploaded duplicate attachments — auto-skipped before sending">' + escHtml(inv.dedupNote) + '</span>';
          break;
```

Also update the `case 'sent_no_pod':` branch (around line 378-381) the same way:

Find:

```javascript
        case 'sent_no_pod':
          statusHtml = '<span class="status-badge" style="background:#fef3c7; color:#92400e; border:1px solid #f59e0b;">Sent (No POD)</span>';
          if (inv.errorMessage) statusHtml += '<br><span style="font-size:0.68rem; color:#92400e;" title="' + escHtml(inv.errorMessage) + '">' + escHtml(inv.errorMessage.substring(0, 50)) + '</span>';
          break;
```

Replace with:

```javascript
        case 'sent_no_pod':
          statusHtml = '<span class="status-badge" style="background:#fef3c7; color:#92400e; border:1px solid #f59e0b;">Sent (No POD)</span>';
          if (inv.errorMessage) statusHtml += '<br><span style="font-size:0.68rem; color:#92400e;" title="' + escHtml(inv.errorMessage) + '">' + escHtml(inv.errorMessage.substring(0, 50)) + '</span>';
          if (inv.dedupNote) statusHtml += '<br><span style="font-size:0.68rem; color:#92400e;" title="TMS uploaded duplicate attachments — auto-skipped before sending">' + escHtml(inv.dedupNote) + '</span>';
          break;
```

- [ ] **Step 3: Reset `dedupNote` when a row is re-prepared for resend**

Find the row-reset block (around line 822):

```javascript
    if (inv) { inv.sendStatus = null; inv.sentAt = null; inv.errorMessage = null; }
```

Replace with:

```javascript
    if (inv) { inv.sendStatus = null; inv.sentAt = null; inv.errorMessage = null; inv.dedupNote = null; }
```

- [ ] **Step 4: Manual smoke check (optional but recommended)**

Open `app/index.html` in a browser. Open DevTools console. Paste this snippet to simulate the event firing:

```javascript
// Pretend we have one invoice in state
invoiceState.invoices = [{ id: 'x', invoiceNumber: 'INV-TEST', sendStatus: 'sent', sentAt: '12:00:00 PM', dedupNote: null }];
invRenderTable();
// Fire the event
invHandleSendEvent({
  type: 'attachments_deduped',
  invoiceNumber: 'INV-TEST',
  kept: 1,
  skipped: 4,
  skippedFiles: ['pod.pdf', 'pod.pdf', 'pod.pdf', 'pod.pdf'],
});
```

Expected: the row's status cell now shows `Sent` + sentAt + `4 duplicate attachments skipped` in dark amber under it.

(If the table isn't visible because no upload happened yet, this step can be skipped — the integration test in Task 6 already verifies the SSE payload shape.)

- [ ] **Step 5: Commit**

```bash
git add app/assets/js/tools/invoice-sender/invoice-sender.js
git commit -m "feat(invoice-sender): surface TMS-008 dedup note in send results

When the agent emits 'attachments_deduped', append a small inline note
('K duplicate attachments skipped') under the Sent / Sent (No POD)
status badge. Also logs to the status log. Note is cleared when a row
is re-prepared for resend.
"
```

---

## Task 8: Update workarounds registry — TMS-008 → Active

**Files:**
- Modify: `docs/tms-workarounds.md`

- [ ] **Step 1: Update the Quick Lookup row**

In `docs/tms-workarounds.md`, find the TMS-008 row in the Quick Lookup table (around line 23):

```
| TMS-008 | Duplicate attachment filter on send                  | TMS    | Invoice Sender             | In progress |
```

Replace with:

```
| TMS-008 | Duplicate attachment filter on send                  | TMS    | Invoice Sender             | Active      |
```

- [ ] **Step 2: Update the TMS-008 detail entry**

Find the TMS-008 detailed entry (line ~36 onward). Replace the entire entry (from `### TMS-008 — Duplicate attachment filter on send` through the trailing blank line before `### TMS-007`) with:

```markdown
### TMS-008 — Duplicate attachment filter on send

- **Date added:** 2026-05-05
- **Tools affected:** Invoice Sender
- **Symptom:** Customers receive the same PDF (e.g. POD) attached multiple times
  in a single invoice email. Real-world case: `mm2603020032_ite_1775833088165.pdf`
  (13 KB) appeared 5× on one QBO invoice.
- **Root cause:** When any new document is uploaded to a TMS work order, TMS
  re-uploads ALL prior documents to the linked QBO invoice, producing exact-
  duplicate `Attachable` records on the QBO side.
- **Workaround:** Send-time dedup. A pure helper drops duplicates by
  `(filename.lower().strip(), size)` before the agent emails or uploads any
  attachment list. Tie-breaker: keep the highest QBO Attachable Id (newest
  upload). The QBO record itself is untouched. Skipped count is logged at
  INFO and surfaced to the Invoice Sender UI via the `attachments_deduped`
  SSE event so the user can see when TMS is misbehaving.
- **Files:**
  - Helper: `agent/services/qbo_api/dedup.py` (`dedupe_attachments`)
  - Call sites:
    - `agent/services/job_manager/send_qbo_api.py` (`_dedup_and_emit` + two `check_attachments` call points; the customer-visible bug)
    - `agent/services/job_manager/send_oec.py` (POD pick — newest by id)
    - `agent/services/job_manager/send_portal.py` (POD pick — newest by id)
    - `agent/services/job_manager/fetch_job.py` (POD pick — newest by id)
  - UI: `app/assets/js/tools/invoice-sender/invoice-sender.js` (`attachments_deduped` handler + `dedupNote` row field)
  - Tests: `agent/tests/test_qbo_api_dedup.py`, `agent/tests/test_job_manager/test_send_qbo_api_tms_data.py`
- **Status:** Active.
```

- [ ] **Step 3: Verify the file still parses cleanly**

Run: `cd "C:/Users/Joseph/Desktop/NGL ACCOUNTING SERVICE" && python -c "
import pathlib
md = pathlib.Path('docs/tms-workarounds.md').read_text()
assert 'TMS-008' in md
assert 'Active' in md.split('TMS-008')[1].split('TMS-007')[0]  # TMS-008 section says Active
assert 'In progress' not in md.split('TMS-008')[1].split('TMS-007')[0]
print('OK')
"`
Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add docs/tms-workarounds.md
git commit -m "docs(workarounds): TMS-008 In progress -> Active

Replace spec-link Status with concrete file refs now that the dedup
helper, four call-site wires, UI handler, and tests are all merged.
"
```

---

## Task 9: Ship — version bump, build, push, GitHub release

This project's CLAUDE.md mandates the full rebuild pipeline on every code change. No exceptions, no asking — just do it.

**Files:**
- Modify: `desktop/VERSION`

- [ ] **Step 1: Read the current version**

Run: `cat desktop/VERSION`
Expected: a single line like `2.39.0`. Note the value.

- [ ] **Step 2: Bump the version (patch increment for a bug fix)**

Edit `desktop/VERSION`. If it was `2.39.0`, write `2.39.1`. If the patch component is non-zero (e.g. `2.39.5`), bump to `2.39.6`. (The previous TMS perf release was 2.39.0 — patch bump is correct here since this is a follow-on bug fix.)

- [ ] **Step 3: Run the full build pipeline**

Run: `cd desktop && build-all.bat`
Expected: PyInstaller build completes, electron-builder produces `desktop/dist/NGL Accounting Setup <version>.exe` and `desktop/dist/latest.yml`. If the build fails, stop and diagnose — do NOT proceed without a green build.

- [ ] **Step 4: Commit the VERSION bump**

```bash
git add desktop/VERSION
git commit -m "chore: bump version to <new-version> for TMS-008 dedup release"
```

(Replace `<new-version>` with the actual bumped value, e.g. `2.39.1`.)

- [ ] **Step 5: Push to remote**

```bash
git push origin main
```
Expected: push succeeds.

- [ ] **Step 6: Create the GitHub release**

```bash
gh release create v<new-version> \
  "desktop/dist/NGL Accounting Setup <new-version>.exe" \
  "desktop/dist/latest.yml" \
  --title "v<new-version> — TMS-008 duplicate attachment filter" \
  --notes "$(cat <<'EOF'
## What's fixed

- **TMS-008**: Customers were receiving the same POD attached up to 5× in a single invoice email when TMS re-uploaded prior documents to the linked QBO invoice. The Invoice Sender now dedupes attachments by (filename, size) at send time, keeping the newest upload (highest QBO Attachable Id). The QBO record itself is untouched.
- Defensive dedup applied to OEC POD email, portal upload, and fetch flows (picks newest POD if multiple exist).
- Skipped count is logged and surfaced inline on the Invoice Sender row when TMS misbehaves.

See docs/tms-workarounds.md for the full registry entry.
EOF
)"
```

(Replace both `<new-version>` placeholders with the actual value, e.g. `2.39.1`.)

Expected: release URL printed, installer + latest.yml uploaded.

- [ ] **Step 7: Verify the release**

Run: `gh release view v<new-version>`
Expected: shows the release with both assets (`.exe` and `latest.yml`) attached.

---

## Self-review checklist (already run while writing)

**Spec coverage:**
- ✅ Helper at `agent/services/qbo_api/dedup.py`, single pure function (Task 1).
- ✅ Match key `(filename.lower().strip(), size)` (Task 1, helper body).
- ✅ Tie-breaker `int(att["id"])` not string compare (Task 1, dedicated test).
- ✅ Stable order preserved (Task 1, test_kept_order_preserves_first_appearance_position).
- ✅ Pure: no I/O, no logging inside helper (Task 1).
- ✅ All four call sites wired (Tasks 2-5).
- ✅ INFO log line on skipped > 0 (Task 2 helper, plus Task 5 fetch_job inline).
- ✅ SSE event `attachments_deduped` with `{invoiceNumber, kept, skipped, skippedFiles}` (Task 2 helper, emitted only when skipped > 0).
- ✅ UI inline note (Task 7).
- ✅ Unit test file `agent/tests/test_qbo_api_dedup.py` covering all listed edges (Task 1).
- ✅ Integration test in test_send_qbo_api_tms_data.py asserting no dup filenames in email_attachments (Task 6). (Spec offered "or test_send_qbo_api.py" — extending the existing file matches naming and scope better.)
- ✅ Workarounds registry: TMS-008 In progress → Active with concrete file refs (Task 8).
- ✅ Out of scope (no QBO deletion, no manual UI override, no TMS upload changes) — confirmed not present.

**Placeholder scan:** No TBD/TODO/"add validation" placeholders. Every code block is complete and copy-pasteable.

**Type consistency:** Function signature `dedupe_attachments(list[dict]) -> tuple[list[dict], list[dict]]` is identical across the helper definition (Task 1), the helper imports (Tasks 2-5), and the test imports (Task 1, Task 6). The `_dedup_and_emit` mixin method has consistent signature `(self, job, invoice_number: str, attachments: list[dict]) -> list[dict]` everywhere it's called.
