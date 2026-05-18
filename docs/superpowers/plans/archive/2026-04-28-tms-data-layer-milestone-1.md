# TMS Data Layer — Milestone 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `agent/services/tms_data/` package — a layered cascade between tools and the three data sources (QBO API, TMS API, TMS browser). No tool migration in this milestone; this is pure backend foundation that future milestones build on.

**Architecture:** A new package with one public class (`TMSDataLayer`) that exposes per-row data accessors (`enrich_invoice`, `get_document`, `get_documents`) and per-job retry-state methods (`get_failed_rows`, `retry_failed_row`, `retry_all_failed`, `reset_for_new_job`). The cascade tries TMS API after QBO; the TMS browser is opt-in only via the explicit `source="browser"` parameter — never auto-invoked. Failures during a batch accumulate in a per-job tracker for the future Failed Rows UI box.

**Tech Stack:** Python 3.10+, FastAPI (existing), httpx (existing), pytest (existing). No new dependencies.

**Reference:** [docs/superpowers/specs/2026-04-28-tms-data-layer-design.md](../specs/2026-04-28-tms-data-layer-design.md)

---

## File Structure

### New files (created)
```
agent/services/tms_data/
├── __init__.py            # public TMSDataLayer class
├── enriched_invoice.py    # EnrichedInvoice + FailedRow dataclasses
├── extractors.py          # field extractors (QBO + TMS WO record)
├── failed_rows.py         # FailedRowsTracker class (per-job state)
├── cascade.py             # TMS API path (run_enrich, run_document)
└── browser_path.py        # explicit TMS browser path (run_enrich, run_document)

agent/tests/test_tms_data/
├── __init__.py
├── test_enriched_invoice.py
├── test_extractors.py
├── test_failed_rows.py
├── test_cascade.py
├── test_browser_path.py
└── test_data_layer.py
```

### Existing files modified
- `agent/main.py` — instantiate and inject `TMSDataLayer` (one-time wiring; no router yet)

### Files NOT touched in this milestone
- `agent/services/qbo_api/` — read-only consumer
- `agent/services/tms_api.py` — read-only consumer
- `agent/services/tms_browser/` — read-only consumer
- `agent/services/job_manager/` — migrated in milestone 2

### Why this split
Each file has a single responsibility:
- `enriched_invoice.py` — pure data shapes, zero logic
- `extractors.py` — pure functions, no state, no I/O
- `failed_rows.py` — stateful tracker with no I/O
- `cascade.py` and `browser_path.py` — async I/O paths, parallel structure for "API vs browser"
- `__init__.py` — composition root that wires the pieces together

This makes the eventual "delete the browser fallback" cleanup a single-file delete (`browser_path.py`) plus minor edits to `__init__.py`.

---

## Setup

All commands assume current working directory is the repo root: `c:\Users\Joseph\Desktop\NGL ACCOUNTING SERVICE`.

To run tests: `python -m pytest agent/tests/test_tms_data/ -v`

To run a single test file: `python -m pytest agent/tests/test_tms_data/test_extractors.py -v`

---

## Task 1: Test directory scaffold

**Files:**
- Create: `agent/tests/test_tms_data/__init__.py`
- Create: `agent/services/tms_data/__init__.py` (placeholder — will be filled in Task 11)

- [ ] **Step 1: Create empty test package init**

Create `agent/tests/test_tms_data/__init__.py` with empty content.

- [ ] **Step 2: Create empty service package init**

Create `agent/services/tms_data/__init__.py` with content:

```python
"""TMS Data Layer — layered cascade between tools and data sources.

QBO API (primary) → TMS API (fast fallback) → TMS browser (opt-in only).

See docs/superpowers/specs/2026-04-28-tms-data-layer-design.md.
"""
```

- [ ] **Step 3: Verify pytest discovers the new test directory**

Run: `python -m pytest agent/tests/test_tms_data/ -v --collect-only`
Expected: `no tests ran in 0.0Xs` (or "no tests collected") — confirms the directory exists and is recognized.

- [ ] **Step 4: Commit**

```bash
git add agent/services/tms_data/__init__.py agent/tests/test_tms_data/__init__.py
git commit -m "scaffold: tms_data package + test directory"
```

---

## Task 2: EnrichedInvoice + FailedRow dataclasses

**Files:**
- Create: `agent/services/tms_data/enriched_invoice.py`
- Create: `agent/tests/test_tms_data/test_enriched_invoice.py`

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_tms_data/test_enriched_invoice.py`:

```python
"""Tests for EnrichedInvoice and FailedRow dataclasses."""

import sys
import time
from pathlib import Path

# Add agent/ to path so imports work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from services.tms_data.enriched_invoice import EnrichedInvoice, FailedRow


class TestEnrichedInvoice:
    def test_construct_with_all_fields(self):
        ei = EnrichedInvoice(
            wo_no="LM2602170009",
            container_no="KKFU7654819",
            chassis="ABC1234",
            cnee="ACME LOGISTICS",
            do_sender_email="ops@example.com",
            sources={
                "wo_no": "qbo",
                "container_no": "qbo",
                "chassis": "tms_api",
                "cnee": "qbo",
                "do_sender_email": "tms_api",
            },
        )
        assert ei.wo_no == "LM2602170009"
        assert ei.chassis == "ABC1234"
        assert ei.sources["chassis"] == "tms_api"

    def test_construct_with_missing_fields(self):
        ei = EnrichedInvoice(
            wo_no=None,
            container_no=None,
            chassis=None,
            cnee=None,
            do_sender_email=None,
            sources={
                "wo_no": "missing",
                "container_no": "missing",
                "chassis": "missing",
                "cnee": "missing",
                "do_sender_email": "missing",
            },
        )
        assert ei.wo_no is None
        assert ei.sources["chassis"] == "missing"


class TestFailedRow:
    def test_construct_for_enrich_failure(self):
        fr = FailedRow(
            row_id="row-abc123",
            invoice_number="LM26040454F",
            container_number="KKFU7654819",
            operation="enrich_invoice",
            doc_type=None,
            error_message="TMS API 500 Internal Server Error",
            failed_at_source="tms_api",
            timestamp=time.time(),
        )
        assert fr.operation == "enrich_invoice"
        assert fr.doc_type is None

    def test_construct_for_document_failure(self):
        fr = FailedRow(
            row_id="row-def456",
            invoice_number="LM26040454F",
            container_number="KKFU7654819",
            operation="get_document",
            doc_type="POD",
            error_message="POD URL returned 404",
            failed_at_source="tms_api",
            timestamp=time.time(),
        )
        assert fr.doc_type == "POD"
        assert fr.failed_at_source == "tms_api"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest agent/tests/test_tms_data/test_enriched_invoice.py -v`
Expected: `ImportError` or `ModuleNotFoundError: No module named 'services.tms_data.enriched_invoice'`

- [ ] **Step 3: Implement the dataclasses**

Create `agent/services/tms_data/enriched_invoice.py`:

```python
"""Data shapes returned by the TMS Data Layer."""

from dataclasses import dataclass, field
from typing import Literal, Optional

# Where each enriched field came from. "missing" means none of the sources had it.
FieldSource = Literal["qbo", "tms_api", "tms_browser", "missing"]


@dataclass
class EnrichedInvoice:
    """A QBO invoice enriched with data filled in from TMS where missing.

    Each field is the best value found across the cascade. The `sources` dict
    records which source provided each value, so the UI can show provenance.
    """
    wo_no: Optional[str]
    container_no: Optional[str]
    chassis: Optional[str]
    cnee: Optional[str]
    do_sender_email: Optional[str]
    sources: dict[str, FieldSource] = field(default_factory=dict)


@dataclass
class FailedRow:
    """One row in a job's failed-rows list. The UI shows these with Retry buttons."""
    row_id: str                           # opaque ID assigned by the data layer
    invoice_number: str
    container_number: Optional[str]
    operation: Literal["enrich_invoice", "get_document"]
    doc_type: Optional[str]               # populated only for get_document failures
    error_message: str                    # one-line preview for the UI
    failed_at_source: Literal["tms_api", "tms_browser"]
    timestamp: float                      # unix seconds
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest agent/tests/test_tms_data/test_enriched_invoice.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add agent/services/tms_data/enriched_invoice.py agent/tests/test_tms_data/test_enriched_invoice.py
git commit -m "feat(tms_data): EnrichedInvoice + FailedRow dataclasses"
```

---

## Task 3: QBO field extractors

The QBO API client already has `_extract_chassis` and `_extract_cnee` instance methods on `QBOInvoicesMixin`. The `extract_wo_from_invoice` function lives in `services/job_manager/util.py`. We wrap them in a uniform module-level interface so the cascade doesn't depend on a live `QBOApiClient` instance for pure-data extraction.

**Files:**
- Create: `agent/services/tms_data/extractors.py` (QBO portion only — TMS portion in Task 4)
- Create: `agent/tests/test_tms_data/test_extractors.py` (QBO portion only)

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_tms_data/test_extractors.py`:

```python
"""Tests for QBO and TMS WO field extractors."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from services.tms_data.extractors import (
    extract_wo_from_qbo,
    extract_chassis_from_qbo,
    extract_cnee_from_qbo,
)


class TestExtractWoFromQbo:
    def test_finds_wo_in_ngl_ref_field(self):
        invoice_data = {
            "CustomField": [
                {"Name": "NGL REF#", "StringValue": "LM2603300024/CUST-REF-99"},
            ],
        }
        assert extract_wo_from_qbo(invoice_data) == "LM2603300024"

    def test_returns_none_when_no_ref_field(self):
        invoice_data = {"CustomField": []}
        assert extract_wo_from_qbo(invoice_data) is None

    def test_returns_none_for_invalid_input(self):
        assert extract_wo_from_qbo({}) is None
        assert extract_wo_from_qbo(None) is None


class TestExtractChassisFromQbo:
    def test_returns_string_when_present(self):
        # Use the same shape `_extract_chassis` reads.
        # Function delegates to QBOInvoicesMixin._extract_chassis under the hood.
        invoice_data = {
            "CustomField": [
                {"Name": "CHASSIS#", "StringValue": "ABC1234"},
            ],
        }
        result = extract_chassis_from_qbo(invoice_data)
        # The delegate may normalize/strip; just assert truthy with our value.
        assert result == "ABC1234" or (result and "ABC1234" in result)

    def test_returns_none_when_absent(self):
        assert extract_chassis_from_qbo({}) is None


class TestExtractCneeFromQbo:
    def test_returns_none_when_absent(self):
        assert extract_cnee_from_qbo({}) is None

    def test_returns_none_for_invalid_input(self):
        assert extract_cnee_from_qbo(None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest agent/tests/test_tms_data/test_extractors.py -v`
Expected: `ModuleNotFoundError: No module named 'services.tms_data.extractors'`

- [ ] **Step 3: Implement the QBO extractors**

Create `agent/services/tms_data/extractors.py`:

```python
"""Pure field extractors for QBO invoices and TMS WO records.

QBO extractors delegate to existing helpers in the codebase to avoid duplication.
TMS WO extractors live here because the TMS API client only exposes a subset.
"""

from typing import Optional

from services.job_manager.util import extract_wo_from_invoice
from services.qbo_api.invoices import QBOInvoicesMixin


# Single shared instance — the chassis/CNEE methods are pure (no I/O, no state).
_qbo_helper = QBOInvoicesMixin()


# ── QBO invoice extractors ──────────────────────────────────────────

def extract_wo_from_qbo(invoice_data) -> Optional[str]:
    """Pull the WO# from a QBO invoice's NGL REF# custom field."""
    return extract_wo_from_invoice(invoice_data)


def extract_chassis_from_qbo(invoice_data) -> Optional[str]:
    """Pull chassis number from a QBO invoice (custom field)."""
    if not isinstance(invoice_data, dict):
        return None
    try:
        return _qbo_helper._extract_chassis(invoice_data)
    except Exception:
        return None


def extract_cnee_from_qbo(invoice_data) -> Optional[str]:
    """Pull CNEE from QBO invoice CustomerMemo (arrow-chain routing)."""
    if not isinstance(invoice_data, dict):
        return None
    try:
        return _qbo_helper._extract_cnee(invoice_data)
    except Exception:
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest agent/tests/test_tms_data/test_extractors.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add agent/services/tms_data/extractors.py agent/tests/test_tms_data/test_extractors.py
git commit -m "feat(tms_data): QBO field extractors (delegate to existing helpers)"
```

---

## Task 4: TMS WO field extractors

**Files:**
- Modify: `agent/services/tms_data/extractors.py` (add TMS extractors)
- Modify: `agent/tests/test_tms_data/test_extractors.py` (add TMS tests)

The TMS WO API record likely has these field names based on the existing `TMSApiClient.extract_*` static methods and the test endpoint at [agent/routers/tms.py:69-80](../../agent/routers/tms.py). For chassis, the exact field name isn't yet confirmed — the extractor tries common candidates.

- [ ] **Step 1: Add the failing tests**

Append to `agent/tests/test_tms_data/test_extractors.py`:

```python
from services.tms_data.extractors import (
    extract_chassis_from_tms_wo,
    extract_cnee_from_tms_wo,
    extract_container_from_tms_wo,
    extract_do_sender_from_tms_wo,
    extract_document_url_from_tms_wo,
)


SAMPLE_WO = {
    "wo_no": "LM2602170009",
    "category": "import",
    "container_no": "KKFU7654819",
    "chassis_no": "ABC1234",
    "billto": "ACME LOGISTICS",
    "do_sender": ["ops@example.com", "secondary@example.com"],
    "documents": [
        {"type_": "POD", "file_url": "https://cdn.example/pod.pdf"},
        {"type_": "BL", "file_url": "https://cdn.example/bl.pdf"},
        {"type_": "DO", "file_url": "https://cdn.example/do.pdf"},
    ],
}


class TestExtractChassisFromTmsWo:
    def test_finds_chassis_no(self):
        assert extract_chassis_from_tms_wo(SAMPLE_WO) == "ABC1234"

    def test_finds_chassis_alternate_name(self):
        assert extract_chassis_from_tms_wo({"chassis": "XYZ5678"}) == "XYZ5678"

    def test_finds_chassis_third_alternate(self):
        assert extract_chassis_from_tms_wo({"chassis_number": "QQQ9999"}) == "QQQ9999"

    def test_returns_none_when_absent(self):
        assert extract_chassis_from_tms_wo({}) is None

    def test_returns_none_for_non_dict(self):
        assert extract_chassis_from_tms_wo(None) is None
        assert extract_chassis_from_tms_wo("string") is None


class TestExtractCneeFromTmsWo:
    def test_finds_billto(self):
        assert extract_cnee_from_tms_wo(SAMPLE_WO) == "ACME LOGISTICS"

    def test_finds_consignee_alternate(self):
        assert extract_cnee_from_tms_wo({"consignee": "BETA CO"}) == "BETA CO"

    def test_returns_none_when_absent(self):
        assert extract_cnee_from_tms_wo({}) is None


class TestExtractContainerFromTmsWo:
    def test_finds_container(self):
        assert extract_container_from_tms_wo(SAMPLE_WO) == "KKFU7654819"

    def test_returns_none_when_absent(self):
        assert extract_container_from_tms_wo({}) is None


class TestExtractDoSenderFromTmsWo:
    def test_finds_first_email(self):
        assert extract_do_sender_from_tms_wo(SAMPLE_WO) == "ops@example.com"

    def test_returns_none_for_empty_array(self):
        assert extract_do_sender_from_tms_wo({"do_sender": []}) is None

    def test_returns_none_when_no_at_sign(self):
        assert extract_do_sender_from_tms_wo({"do_sender": ["not-an-email"]}) is None


class TestExtractDocumentUrlFromTmsWo:
    def test_finds_pod_url(self):
        assert extract_document_url_from_tms_wo(SAMPLE_WO, "POD") == "https://cdn.example/pod.pdf"

    def test_finds_bl_url(self):
        assert extract_document_url_from_tms_wo(SAMPLE_WO, "BL") == "https://cdn.example/bl.pdf"

    def test_case_insensitive_match(self):
        assert extract_document_url_from_tms_wo(SAMPLE_WO, "pod") == "https://cdn.example/pod.pdf"

    def test_returns_none_when_doc_type_missing(self):
        assert extract_document_url_from_tms_wo(SAMPLE_WO, "MISSING") is None

    def test_returns_none_for_empty_documents(self):
        assert extract_document_url_from_tms_wo({"documents": []}, "POD") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest agent/tests/test_tms_data/test_extractors.py -v`
Expected: 5 new test classes failing with `ImportError: cannot import name 'extract_chassis_from_tms_wo'`

- [ ] **Step 3: Add the TMS WO extractors**

Append to `agent/services/tms_data/extractors.py`:

```python
# ── TMS WO record extractors ────────────────────────────────────────

def extract_chassis_from_tms_wo(wo) -> Optional[str]:
    """Pull chassis from a TMS WO record. Tries multiple field-name candidates."""
    if not isinstance(wo, dict):
        return None
    for key in ("chassis_no", "chassis", "chassis_number"):
        v = wo.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def extract_cnee_from_tms_wo(wo) -> Optional[str]:
    """Pull consignee/billto from a TMS WO record."""
    if not isinstance(wo, dict):
        return None
    for key in ("billto", "bill_to", "consignee", "cnee"):
        v = wo.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def extract_container_from_tms_wo(wo) -> Optional[str]:
    """Pull container number from a TMS WO record."""
    if not isinstance(wo, dict):
        return None
    v = wo.get("container_no")
    return v.strip() if isinstance(v, str) and v.strip() else None


def extract_do_sender_from_tms_wo(wo) -> Optional[str]:
    """Pull D/O sender email from a TMS WO record (first email in do_sender array)."""
    if not isinstance(wo, dict):
        return None
    senders = wo.get("do_sender") or []
    if not isinstance(senders, list):
        return None
    for s in senders:
        if isinstance(s, str) and "@" in s:
            return s.strip()
    return None


def extract_document_url_from_tms_wo(wo, doc_type: str) -> Optional[str]:
    """Pull the file_url for a given doc type from a TMS WO record (case-insensitive)."""
    if not isinstance(wo, dict) or not doc_type:
        return None
    target = doc_type.upper()
    for doc in wo.get("documents") or []:
        if not isinstance(doc, dict):
            continue
        t = (doc.get("type_") or "").upper()
        if t == target and doc.get("file_url"):
            return doc["file_url"]
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest agent/tests/test_tms_data/test_extractors.py -v`
Expected: all tests pass (16 total)

- [ ] **Step 5: Commit**

```bash
git add agent/services/tms_data/extractors.py agent/tests/test_tms_data/test_extractors.py
git commit -m "feat(tms_data): TMS WO record field extractors"
```

---

## Task 5: FailedRowsTracker — record + retrieve

**Files:**
- Create: `agent/services/tms_data/failed_rows.py`
- Create: `agent/tests/test_tms_data/test_failed_rows.py`

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_tms_data/test_failed_rows.py`:

```python
"""Tests for FailedRowsTracker — per-job failure state."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from services.tms_data.failed_rows import FailedRowsTracker


class TestRecordAndRetrieve:
    def test_empty_for_unknown_job(self):
        t = FailedRowsTracker()
        assert t.get_rows("unknown-job") == []

    def test_record_one_failure(self):
        t = FailedRowsTracker()
        row_id = t.record_failure(
            job_id="job-1",
            invoice_number="LM26040454F",
            container_number="KKFU7654819",
            operation="get_document",
            doc_type="POD",
            error_message="TMS API 500",
            source="tms_api",
        )
        assert row_id.startswith("row-")
        rows = t.get_rows("job-1")
        assert len(rows) == 1
        assert rows[0].row_id == row_id
        assert rows[0].operation == "get_document"
        assert rows[0].doc_type == "POD"
        assert rows[0].failed_at_source == "tms_api"

    def test_record_multiple_failures(self):
        t = FailedRowsTracker()
        t.record_failure("job-1", "INV1", "C1", "get_document", "POD", "err1", "tms_api")
        t.record_failure("job-1", "INV2", "C2", "enrich_invoice", None, "err2", "tms_api")
        rows = t.get_rows("job-1")
        assert len(rows) == 2

    def test_jobs_isolated(self):
        t = FailedRowsTracker()
        t.record_failure("job-A", "INV1", "C1", "get_document", "POD", "err", "tms_api")
        t.record_failure("job-B", "INV2", "C2", "get_document", "BL", "err", "tms_api")
        assert len(t.get_rows("job-A")) == 1
        assert len(t.get_rows("job-B")) == 1
        assert t.get_rows("job-A")[0].invoice_number == "INV1"
        assert t.get_rows("job-B")[0].invoice_number == "INV2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest agent/tests/test_tms_data/test_failed_rows.py -v`
Expected: `ModuleNotFoundError: No module named 'services.tms_data.failed_rows'`

- [ ] **Step 3: Implement the tracker (record + get only — remove/reset in Task 6)**

Create `agent/services/tms_data/failed_rows.py`:

```python
"""Per-job failed-rows tracker for the TMS Data Layer.

Each job (Invoice Sender batch, Chassis Finder run, etc.) accumulates failures
here. The tools' UIs read this list to render the Failed Rows box.
"""

import time
import uuid
from typing import Literal, Optional

from services.tms_data.enriched_invoice import FailedRow


class FailedRowsTracker:
    """In-memory store of failed rows, keyed by job_id."""

    def __init__(self) -> None:
        self._rows: dict[str, list[FailedRow]] = {}

    def record_failure(
        self,
        job_id: str,
        invoice_number: str,
        container_number: Optional[str],
        operation: Literal["enrich_invoice", "get_document"],
        doc_type: Optional[str],
        error_message: str,
        source: Literal["tms_api", "tms_browser"],
    ) -> str:
        """Record a failure. Returns the row_id (caller passes it back for retry)."""
        row_id = f"row-{uuid.uuid4().hex[:8]}"
        row = FailedRow(
            row_id=row_id,
            invoice_number=invoice_number,
            container_number=container_number,
            operation=operation,
            doc_type=doc_type,
            error_message=error_message,
            failed_at_source=source,
            timestamp=time.time(),
        )
        self._rows.setdefault(job_id, []).append(row)
        return row_id

    def get_rows(self, job_id: str) -> list[FailedRow]:
        """Return the current failed-rows list for a job (empty if unknown)."""
        return list(self._rows.get(job_id, []))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest agent/tests/test_tms_data/test_failed_rows.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add agent/services/tms_data/failed_rows.py agent/tests/test_tms_data/test_failed_rows.py
git commit -m "feat(tms_data): FailedRowsTracker — record + retrieve"
```

---

## Task 6: FailedRowsTracker — remove + reset + find

**Files:**
- Modify: `agent/services/tms_data/failed_rows.py`
- Modify: `agent/tests/test_tms_data/test_failed_rows.py`

- [ ] **Step 1: Add failing tests**

Append to `agent/tests/test_tms_data/test_failed_rows.py`:

```python
class TestRemoveRow:
    def test_remove_existing_row_returns_true(self):
        t = FailedRowsTracker()
        row_id = t.record_failure("job-1", "INV1", "C1", "get_document", "POD", "err", "tms_api")
        assert t.remove_row("job-1", row_id) is True
        assert t.get_rows("job-1") == []

    def test_remove_unknown_row_returns_false(self):
        t = FailedRowsTracker()
        t.record_failure("job-1", "INV1", "C1", "get_document", "POD", "err", "tms_api")
        assert t.remove_row("job-1", "row-nonexistent") is False
        assert len(t.get_rows("job-1")) == 1

    def test_remove_unknown_job_returns_false(self):
        t = FailedRowsTracker()
        assert t.remove_row("unknown-job", "row-X") is False


class TestFindRow:
    def test_finds_existing_row(self):
        t = FailedRowsTracker()
        row_id = t.record_failure("job-1", "INV1", "C1", "get_document", "POD", "err", "tms_api")
        row = t.find_row("job-1", row_id)
        assert row is not None
        assert row.invoice_number == "INV1"

    def test_returns_none_for_unknown_row(self):
        t = FailedRowsTracker()
        assert t.find_row("job-1", "row-X") is None


class TestReset:
    def test_reset_clears_job(self):
        t = FailedRowsTracker()
        t.record_failure("job-1", "INV1", "C1", "get_document", "POD", "err", "tms_api")
        t.record_failure("job-1", "INV2", "C2", "get_document", "BL", "err", "tms_api")
        t.reset("job-1")
        assert t.get_rows("job-1") == []

    def test_reset_unknown_job_is_safe(self):
        t = FailedRowsTracker()
        t.reset("unknown-job")  # must not raise

    def test_reset_does_not_affect_other_jobs(self):
        t = FailedRowsTracker()
        t.record_failure("job-A", "INV1", "C1", "get_document", "POD", "err", "tms_api")
        t.record_failure("job-B", "INV2", "C2", "get_document", "BL", "err", "tms_api")
        t.reset("job-A")
        assert t.get_rows("job-A") == []
        assert len(t.get_rows("job-B")) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest agent/tests/test_tms_data/test_failed_rows.py -v`
Expected: failures with `AttributeError: 'FailedRowsTracker' object has no attribute 'remove_row'` (or `find_row` / `reset`)

- [ ] **Step 3: Add the methods**

Append to `agent/services/tms_data/failed_rows.py` inside the `FailedRowsTracker` class:

```python
    def find_row(self, job_id: str, row_id: str) -> Optional[FailedRow]:
        """Look up a single failed row by job + row_id. None if not found."""
        for r in self._rows.get(job_id, []):
            if r.row_id == row_id:
                return r
        return None

    def remove_row(self, job_id: str, row_id: str) -> bool:
        """Remove a failed row (e.g., because retry succeeded). True if removed."""
        rows = self._rows.get(job_id)
        if not rows:
            return False
        for i, r in enumerate(rows):
            if r.row_id == row_id:
                rows.pop(i)
                return True
        return False

    def reset(self, job_id: str) -> None:
        """Clear all failed rows for a job (called when the job ends)."""
        self._rows.pop(job_id, None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest agent/tests/test_tms_data/test_failed_rows.py -v`
Expected: all tests pass (10 total)

- [ ] **Step 5: Commit**

```bash
git add agent/services/tms_data/failed_rows.py agent/tests/test_tms_data/test_failed_rows.py
git commit -m "feat(tms_data): FailedRowsTracker — remove, find, reset"
```

---

## Task 7: cascade.run_enrich — TMS API enrichment path

**Files:**
- Create: `agent/services/tms_data/cascade.py`
- Create: `agent/tests/test_tms_data/test_cascade.py`

`run_enrich` takes a QBO invoice + a TMS API client. It first reads what's already in the QBO invoice (free), then if anything's missing AND we have a WO#, it calls TMS API to fill blanks. Returns `(EnrichedInvoice, error_message_or_None)`. The error is non-None if the TMS API call failed.

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_tms_data/test_cascade.py`:

```python
"""Tests for cascade.run_enrich and run_document — TMS API path."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from services.tms_data.cascade import run_enrich


# Helpers ────────────────────────────────────────────────────────────

def _qbo_invoice(*, wo=None, chassis=None, cnee=None) -> dict:
    """Build a minimal QBO invoice dict that the QBO extractors can parse."""
    custom_fields = []
    if wo:
        custom_fields.append({"Name": "NGL REF#", "StringValue": f"{wo}/CUST-REF"})
    if chassis:
        custom_fields.append({"Name": "CHASSIS#", "StringValue": chassis})
    invoice = {"CustomField": custom_fields}
    if cnee:
        invoice["CustomerMemo"] = {"value": f"DELIVER TO ->{cnee}"}
    return invoice


def _tms_wo(*, container=None, chassis=None, do_sender=None) -> dict:
    wo = {}
    if container:
        wo["container_no"] = container
    if chassis:
        wo["chassis_no"] = chassis
    if do_sender:
        wo["do_sender"] = [do_sender]
    return wo


# Tests ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_uses_qbo_when_complete():
    """When QBO has everything, no TMS API call is made."""
    invoice = _qbo_invoice(wo="LM2602170009", chassis="ABC1234", cnee="ACME")
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock()

    enriched, err = await run_enrich(invoice, tms_api)

    assert err is None
    assert enriched.wo_no == "LM2602170009"
    assert enriched.chassis == "ABC1234"
    assert enriched.sources["chassis"] == "qbo"
    tms_api.get_work_order.assert_not_called()


@pytest.mark.asyncio
async def test_falls_back_to_tms_api_for_missing_chassis():
    """QBO has WO# but no chassis. TMS API is called and chassis is filled in."""
    invoice = _qbo_invoice(wo="LM2602170009")  # no chassis
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value=_tms_wo(chassis="XYZ5678"))

    enriched, err = await run_enrich(invoice, tms_api)

    assert err is None
    assert enriched.chassis == "XYZ5678"
    assert enriched.sources["chassis"] == "tms_api"
    tms_api.get_work_order.assert_called_once_with("LM2602170009")


@pytest.mark.asyncio
async def test_no_tms_call_when_no_wo_number():
    """Without a WO#, the TMS API can't be called at all."""
    invoice = _qbo_invoice()  # no WO#
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock()

    enriched, err = await run_enrich(invoice, tms_api)

    assert err is None
    assert enriched.wo_no is None
    assert enriched.sources["chassis"] == "missing"
    tms_api.get_work_order.assert_not_called()


@pytest.mark.asyncio
async def test_tms_api_returns_none_marks_missing():
    """TMS API returns None (404) — fields stay missing, no error."""
    invoice = _qbo_invoice(wo="LM2602170009")
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value=None)

    enriched, err = await run_enrich(invoice, tms_api)

    assert err is None  # 404 is not a "failure" — just no data
    assert enriched.chassis is None
    assert enriched.sources["chassis"] == "missing"


@pytest.mark.asyncio
async def test_tms_api_raises_returns_error():
    """TMS API raises an exception — returns error string."""
    invoice = _qbo_invoice(wo="LM2602170009")
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(side_effect=RuntimeError("DNS failed"))

    enriched, err = await run_enrich(invoice, tms_api)

    assert err is not None
    assert "DNS failed" in err
    assert enriched.chassis is None
```

- [ ] **Step 2: Add pytest-asyncio dependency check + run test**

Run: `python -c "import pytest_asyncio; print(pytest_asyncio.__version__)"`

If this fails with `ModuleNotFoundError`, install: `pip install pytest-asyncio`

Then add to `agent/tests/conftest.py` (create if missing) or to a new conftest in `agent/tests/test_tms_data/conftest.py`:

```python
"""Shared pytest config for tms_data tests."""

import pytest_asyncio

# pytest-asyncio mode: auto runs all `async def` tests as async automatically.
pytest_plugins = ("pytest_asyncio",)


def pytest_collection_modifyitems(config, items):
    """Auto-mark async tests so we don't need @pytest.mark.asyncio everywhere."""
    import asyncio
    import pytest as _pytest
    for item in items:
        if asyncio.iscoroutinefunction(item.function):
            item.add_marker(_pytest.mark.asyncio)
```

Run test: `python -m pytest agent/tests/test_tms_data/test_cascade.py -v`
Expected: `ModuleNotFoundError: No module named 'services.tms_data.cascade'`

- [ ] **Step 3: Implement run_enrich**

Create `agent/services/tms_data/cascade.py`:

```python
"""TMS API cascade — run_enrich and run_document.

Each function returns (result, error_message_or_None). The data layer wraps
these calls and records failures into FailedRowsTracker on error.
"""

import logging
from pathlib import Path
from typing import Optional, Tuple

from services.tms_data.enriched_invoice import EnrichedInvoice
from services.tms_data.extractors import (
    extract_chassis_from_qbo,
    extract_chassis_from_tms_wo,
    extract_cnee_from_qbo,
    extract_cnee_from_tms_wo,
    extract_container_from_tms_wo,
    extract_do_sender_from_tms_wo,
    extract_document_url_from_tms_wo,
    extract_wo_from_qbo,
)

logger = logging.getLogger("ngl.tms_data.cascade")


async def run_enrich(invoice_data: dict, tms_api) -> Tuple[EnrichedInvoice, Optional[str]]:
    """Build an EnrichedInvoice from QBO data, filling blanks via TMS API.

    Returns (enriched, error). error is non-None only when the TMS API call
    raised (DNS, network, 5xx). 404 is treated as no-data, not an error.
    """
    # Step 1: read what QBO already gave us
    wo_no = extract_wo_from_qbo(invoice_data)
    chassis = extract_chassis_from_qbo(invoice_data)
    cnee = extract_cnee_from_qbo(invoice_data)
    container_no = None  # QBO invoices don't carry container# directly
    do_sender_email = None  # QBO custom fields rarely carry this

    sources = {
        "wo_no": "qbo" if wo_no else "missing",
        "container_no": "missing",
        "chassis": "qbo" if chassis else "missing",
        "cnee": "qbo" if cnee else "missing",
        "do_sender_email": "missing",
    }

    # Step 2: short-circuit if QBO already has everything we'd need from TMS
    needs_tms = (
        wo_no  # can only call TMS if we know the WO#
        and (not chassis or not cnee or not container_no or not do_sender_email)
    )
    if not needs_tms:
        return EnrichedInvoice(
            wo_no=wo_no, container_no=container_no, chassis=chassis,
            cnee=cnee, do_sender_email=do_sender_email, sources=sources,
        ), None

    # Step 3: call TMS API
    try:
        wo = await tms_api.get_work_order(wo_no)
    except Exception as e:
        logger.warning("TMS API enrich failed for WO %s: %s", wo_no, e)
        return EnrichedInvoice(
            wo_no=wo_no, container_no=container_no, chassis=chassis,
            cnee=cnee, do_sender_email=do_sender_email, sources=sources,
        ), str(e)

    if not wo:
        # 404 / not found is not a failure — just no data to fill in.
        return EnrichedInvoice(
            wo_no=wo_no, container_no=container_no, chassis=chassis,
            cnee=cnee, do_sender_email=do_sender_email, sources=sources,
        ), None

    # Step 4: fill in any blanks from the TMS WO record
    if not chassis:
        v = extract_chassis_from_tms_wo(wo)
        if v:
            chassis = v
            sources["chassis"] = "tms_api"

    if not cnee:
        v = extract_cnee_from_tms_wo(wo)
        if v:
            cnee = v
            sources["cnee"] = "tms_api"

    if not container_no:
        v = extract_container_from_tms_wo(wo)
        if v:
            container_no = v
            sources["container_no"] = "tms_api"

    if not do_sender_email:
        v = extract_do_sender_from_tms_wo(wo)
        if v:
            do_sender_email = v
            sources["do_sender_email"] = "tms_api"

    return EnrichedInvoice(
        wo_no=wo_no, container_no=container_no, chassis=chassis,
        cnee=cnee, do_sender_email=do_sender_email, sources=sources,
    ), None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest agent/tests/test_tms_data/test_cascade.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add agent/services/tms_data/cascade.py agent/tests/test_tms_data/test_cascade.py agent/tests/test_tms_data/conftest.py
git commit -m "feat(tms_data): cascade.run_enrich (QBO→TMS API path)"
```

---

## Task 8: cascade.run_document — TMS API document download path

**Files:**
- Modify: `agent/services/tms_data/cascade.py` (add `run_document`)
- Modify: `agent/tests/test_tms_data/test_cascade.py` (add document tests)

- [ ] **Step 1: Write the failing tests**

Append to `agent/tests/test_tms_data/test_cascade.py`:

```python
from services.tms_data.cascade import run_document


@pytest.mark.asyncio
async def test_run_document_downloads_pod(tmp_path):
    invoice = _qbo_invoice(wo="LM2602170009")
    wo = {
        "documents": [{"type_": "POD", "file_url": "https://cdn.example/pod.pdf"}],
    }
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value=wo)
    tms_api.download_document = AsyncMock(return_value=b"%PDF-1.4 sample bytes")

    path, err = await run_document(invoice, "POD", tmp_path, tms_api)

    assert err is None
    assert path is not None
    assert path.exists()
    assert path.read_bytes() == b"%PDF-1.4 sample bytes"
    tms_api.download_document.assert_called_once_with("https://cdn.example/pod.pdf")


@pytest.mark.asyncio
async def test_run_document_no_wo_returns_none(tmp_path):
    invoice = _qbo_invoice()  # no WO#
    tms_api = AsyncMock()

    path, err = await run_document(invoice, "POD", tmp_path, tms_api)

    assert path is None
    assert err is not None
    assert "WO" in err  # mentions missing WO#


@pytest.mark.asyncio
async def test_run_document_doc_type_not_in_wo_returns_none(tmp_path):
    invoice = _qbo_invoice(wo="LM2602170009")
    wo = {"documents": [{"type_": "BL", "file_url": "https://cdn.example/bl.pdf"}]}
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value=wo)

    path, err = await run_document(invoice, "POD", tmp_path, tms_api)

    assert path is None
    assert err is None  # no error — just no POD on this WO


@pytest.mark.asyncio
async def test_run_document_get_wo_raises_returns_error(tmp_path):
    invoice = _qbo_invoice(wo="LM2602170009")
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(side_effect=RuntimeError("network down"))

    path, err = await run_document(invoice, "POD", tmp_path, tms_api)

    assert path is None
    assert err is not None
    assert "network down" in err


@pytest.mark.asyncio
async def test_run_document_download_returns_none_is_error(tmp_path):
    """download_document returning None means CDN failed — that's an error."""
    invoice = _qbo_invoice(wo="LM2602170009")
    wo = {"documents": [{"type_": "POD", "file_url": "https://cdn.example/pod.pdf"}]}
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value=wo)
    tms_api.download_document = AsyncMock(return_value=None)

    path, err = await run_document(invoice, "POD", tmp_path, tms_api)

    assert path is None
    assert err is not None
    assert "download" in err.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest agent/tests/test_tms_data/test_cascade.py -v -k run_document`
Expected: `ImportError: cannot import name 'run_document' from 'services.tms_data.cascade'`

- [ ] **Step 3: Implement run_document**

Append to `agent/services/tms_data/cascade.py`:

```python
async def run_document(
    invoice_data: dict,
    doc_type: str,
    dest_dir: Path,
    tms_api,
) -> Tuple[Optional[Path], Optional[str]]:
    """Download one document via TMS API. Returns (path, error).

    error is None when the doc isn't present on the WO (not a failure).
    error is non-None when the TMS API call raised or download failed.
    """
    wo_no = extract_wo_from_qbo(invoice_data)
    if not wo_no:
        return None, "Cannot fetch from TMS API: no WO# on QBO invoice"

    try:
        wo = await tms_api.get_work_order(wo_no)
    except Exception as e:
        logger.warning("TMS API get_work_order failed for %s: %s", wo_no, e)
        return None, str(e)

    if not wo:
        # 404 — WO doesn't exist or API doesn't have it.
        return None, None

    url = extract_document_url_from_tms_wo(wo, doc_type)
    if not url:
        # WO exists but doesn't have this doc type.
        return None, None

    try:
        data = await tms_api.download_document(url)
    except Exception as e:
        logger.warning("TMS API download_document failed for %s: %s", url, e)
        return None, str(e)

    if not data:
        return None, f"Document download returned no data for {doc_type}"

    dest = dest_dir / f"{wo_no}_{doc_type}.pdf"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest, None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest agent/tests/test_tms_data/test_cascade.py -v`
Expected: all 10 tests pass

- [ ] **Step 5: Commit**

```bash
git add agent/services/tms_data/cascade.py agent/tests/test_tms_data/test_cascade.py
git commit -m "feat(tms_data): cascade.run_document (TMS API doc download)"
```

---

## Task 9: browser_path.run_enrich — explicit TMS browser path

**Files:**
- Create: `agent/services/tms_data/browser_path.py`
- Create: `agent/tests/test_tms_data/test_browser_path.py`

The browser path is invoked only when the user clicks "Retry (Browser)". It uses the existing `tms_browser` instance to look up the same fields, scraping from the Detail Info tab.

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_tms_data/test_browser_path.py`:

```python
"""Tests for browser_path.run_enrich and run_document — TMS browser path."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from services.tms_data.browser_path import run_enrich_browser, run_document_browser


def _qbo_invoice(*, wo=None) -> dict:
    custom_fields = []
    if wo:
        custom_fields.append({"Name": "NGL REF#", "StringValue": f"{wo}/CUST-REF"})
    return {"CustomField": custom_fields}


@pytest.mark.asyncio
async def test_browser_enrich_requires_wo():
    invoice = _qbo_invoice()  # no WO#
    tms_browser = AsyncMock()

    enriched, err = await run_enrich_browser(invoice, tms_browser)

    assert err is not None
    assert "WO" in err


@pytest.mark.asyncio
async def test_browser_enrich_uses_browser_when_wo_present():
    invoice = _qbo_invoice(wo="LM2602170009")
    tms_browser = AsyncMock()
    tms_browser.fetch_detail_info = AsyncMock(return_value={
        "container_no": "KKFU7654819",
        "chassis": "BROWSERCHX",
        "do_sender_email": "browser@example.com",
    })

    enriched, err = await run_enrich_browser(invoice, tms_browser)

    assert err is None
    assert enriched.chassis == "BROWSERCHX"
    assert enriched.sources["chassis"] == "tms_browser"
    assert enriched.do_sender_email == "browser@example.com"
    assert enriched.sources["do_sender_email"] == "tms_browser"


@pytest.mark.asyncio
async def test_browser_enrich_browser_raises_returns_error():
    invoice = _qbo_invoice(wo="LM2602170009")
    tms_browser = AsyncMock()
    tms_browser.fetch_detail_info = AsyncMock(side_effect=RuntimeError("not logged in"))

    enriched, err = await run_enrich_browser(invoice, tms_browser)

    assert err is not None
    assert "not logged in" in err


@pytest.mark.asyncio
async def test_browser_document_uses_fetch_doc_by_wo(tmp_path):
    invoice = _qbo_invoice(wo="LM2602170009")
    tms_browser = AsyncMock()
    expected_path = tmp_path / "LM2602170009_POD.pdf"
    expected_path.write_bytes(b"browser pod")
    tms_browser.fetch_doc_by_wo = AsyncMock(return_value=expected_path)
    tms_browser.bc_detail_type_segment = lambda inv_no: "import"

    path, err = await run_document_browser(invoice, "POD", tmp_path, tms_browser, "LM26040454F")

    assert err is None
    assert path == expected_path


@pytest.mark.asyncio
async def test_browser_document_returns_none_when_browser_returns_none(tmp_path):
    invoice = _qbo_invoice(wo="LM2602170009")
    tms_browser = AsyncMock()
    tms_browser.fetch_doc_by_wo = AsyncMock(return_value=None)
    tms_browser.bc_detail_type_segment = lambda inv_no: "import"

    path, err = await run_document_browser(invoice, "POD", tmp_path, tms_browser, "LM26040454F")

    assert path is None
    assert err is None  # browser said "no doc", not an error
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest agent/tests/test_tms_data/test_browser_path.py -v`
Expected: `ModuleNotFoundError: No module named 'services.tms_data.browser_path'`

- [ ] **Step 3: Implement the browser path**

Create `agent/services/tms_data/browser_path.py`:

```python
"""Explicit TMS browser path — invoked only when user clicks 'Retry (Browser)'.

This module mirrors the shape of cascade.py so the data layer can swap between
them based on the `source` parameter passed by callers.

Note: the methods called on the browser (`fetch_detail_info`, `fetch_doc_by_wo`,
`bc_detail_type_segment`) already exist or are thin shims around existing methods.
"""

import logging
from pathlib import Path
from typing import Optional, Tuple

from services.tms_data.enriched_invoice import EnrichedInvoice
from services.tms_data.extractors import (
    extract_chassis_from_qbo,
    extract_cnee_from_qbo,
    extract_wo_from_qbo,
)

logger = logging.getLogger("ngl.tms_data.browser_path")


async def run_enrich_browser(
    invoice_data: dict,
    tms_browser,
) -> Tuple[EnrichedInvoice, Optional[str]]:
    """Browser-driven enrichment. Used only when the user explicitly opts in."""
    wo_no = extract_wo_from_qbo(invoice_data)
    chassis = extract_chassis_from_qbo(invoice_data)
    cnee = extract_cnee_from_qbo(invoice_data)
    container_no = None
    do_sender_email = None

    sources = {
        "wo_no": "qbo" if wo_no else "missing",
        "container_no": "missing",
        "chassis": "qbo" if chassis else "missing",
        "cnee": "qbo" if cnee else "missing",
        "do_sender_email": "missing",
    }

    if not wo_no:
        return EnrichedInvoice(
            wo_no=wo_no, container_no=container_no, chassis=chassis,
            cnee=cnee, do_sender_email=do_sender_email, sources=sources,
        ), "Cannot fetch from TMS browser: no WO# on QBO invoice"

    try:
        details = await tms_browser.fetch_detail_info(wo_no)
    except Exception as e:
        logger.warning("TMS browser fetch_detail_info failed for %s: %s", wo_no, e)
        return EnrichedInvoice(
            wo_no=wo_no, container_no=container_no, chassis=chassis,
            cnee=cnee, do_sender_email=do_sender_email, sources=sources,
        ), str(e)

    if not isinstance(details, dict):
        return EnrichedInvoice(
            wo_no=wo_no, container_no=container_no, chassis=chassis,
            cnee=cnee, do_sender_email=do_sender_email, sources=sources,
        ), None

    if not chassis and details.get("chassis"):
        chassis = details["chassis"]
        sources["chassis"] = "tms_browser"
    if not cnee and details.get("cnee"):
        cnee = details["cnee"]
        sources["cnee"] = "tms_browser"
    if details.get("container_no"):
        container_no = details["container_no"]
        sources["container_no"] = "tms_browser"
    if details.get("do_sender_email"):
        do_sender_email = details["do_sender_email"]
        sources["do_sender_email"] = "tms_browser"

    return EnrichedInvoice(
        wo_no=wo_no, container_no=container_no, chassis=chassis,
        cnee=cnee, do_sender_email=do_sender_email, sources=sources,
    ), None


async def run_document_browser(
    invoice_data: dict,
    doc_type: str,
    dest_dir: Path,
    tms_browser,
    invoice_number: str,
) -> Tuple[Optional[Path], Optional[str]]:
    """Browser-driven document fetch using the existing direct-URL fetcher."""
    wo_no = extract_wo_from_qbo(invoice_data)
    if not wo_no:
        return None, "Cannot fetch from TMS browser: no WO# on QBO invoice"

    try:
        detail_type = tms_browser.bc_detail_type_segment(invoice_number)
    except Exception as e:
        logger.warning("Failed to derive detail_type for %s: %s", invoice_number, e)
        detail_type = None

    if not detail_type:
        return None, "Cannot derive TMS detail-type segment from invoice number"

    try:
        path = await tms_browser.fetch_doc_by_wo(
            wo_no, detail_type, doc_type, "", invoice_number, dest_dir,
        )
    except Exception as e:
        logger.warning("TMS browser fetch_doc_by_wo failed for %s/%s: %s",
                       wo_no, doc_type, e)
        return None, str(e)

    return path, None
```

> **Note for milestone 2:** the test mocks a `fetch_detail_info` method on `tms_browser` that does not yet exist. It is a thin shim around the existing `_grid_extract_*` methods plus a Detail Info navigation. Implementing it is part of milestone 2 (Invoice Sender migration) — not this milestone. For milestone 1, the unit tests use mocks; the real method is added when first needed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest agent/tests/test_tms_data/test_browser_path.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add agent/services/tms_data/browser_path.py agent/tests/test_tms_data/test_browser_path.py
git commit -m "feat(tms_data): browser_path (opt-in retry via TMS browser)"
```

---

## Task 10: TMSDataLayer — `enrich_invoice`

**Files:**
- Modify: `agent/services/tms_data/__init__.py`
- Create: `agent/tests/test_tms_data/test_data_layer.py`

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_tms_data/test_data_layer.py`:

```python
"""Tests for the public TMSDataLayer class."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from services.tms_data import TMSDataLayer


def _qbo_invoice(*, wo=None, chassis=None) -> dict:
    custom_fields = []
    if wo:
        custom_fields.append({"Name": "NGL REF#", "StringValue": f"{wo}/CUST-REF"})
    if chassis:
        custom_fields.append({"Name": "CHASSIS#", "StringValue": chassis})
    return {"CustomField": custom_fields}


@pytest.mark.asyncio
async def test_enrich_invoice_uses_api_by_default():
    qbo_api = AsyncMock()
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value={"chassis_no": "FROMTMS"})
    tms_browser = AsyncMock()

    dl = TMSDataLayer(qbo_api, tms_api, tms_browser)
    enriched = await dl.enrich_invoice("job-1", _qbo_invoice(wo="LM01"))

    assert enriched.chassis == "FROMTMS"
    assert enriched.sources["chassis"] == "tms_api"
    tms_browser.fetch_detail_info.assert_not_called()


@pytest.mark.asyncio
async def test_enrich_invoice_uses_browser_when_requested():
    qbo_api = AsyncMock()
    tms_api = AsyncMock()
    tms_browser = AsyncMock()
    tms_browser.fetch_detail_info = AsyncMock(return_value={"chassis": "FROMBROWSER"})

    dl = TMSDataLayer(qbo_api, tms_api, tms_browser)
    enriched = await dl.enrich_invoice("job-1", _qbo_invoice(wo="LM01"), source="browser")

    assert enriched.chassis == "FROMBROWSER"
    assert enriched.sources["chassis"] == "tms_browser"
    tms_api.get_work_order.assert_not_called()


@pytest.mark.asyncio
async def test_enrich_invoice_records_failure_in_failed_rows():
    qbo_api = AsyncMock()
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(side_effect=RuntimeError("DNS fail"))
    tms_browser = AsyncMock()

    dl = TMSDataLayer(qbo_api, tms_api, tms_browser)
    invoice = _qbo_invoice(wo="LM01")
    invoice["DocNumber"] = "LM26040454F"

    enriched = await dl.enrich_invoice("job-1", invoice)

    rows = dl.get_failed_rows("job-1")
    assert len(rows) == 1
    assert rows[0].invoice_number == "LM26040454F"
    assert rows[0].operation == "enrich_invoice"
    assert rows[0].failed_at_source == "tms_api"
    assert "DNS fail" in rows[0].error_message
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest agent/tests/test_tms_data/test_data_layer.py -v`
Expected: `ImportError: cannot import name 'TMSDataLayer' from 'services.tms_data'`

- [ ] **Step 3: Implement TMSDataLayer (enrich_invoice + get_failed_rows only — others in later tasks)**

Replace contents of `agent/services/tms_data/__init__.py`:

```python
"""TMS Data Layer — layered cascade between tools and data sources.

QBO API (primary) → TMS API (fast fallback) → TMS browser (opt-in only).

See docs/superpowers/specs/2026-04-28-tms-data-layer-design.md.
"""

import logging
from pathlib import Path
from typing import Literal, Optional

from services.tms_data.browser_path import run_document_browser, run_enrich_browser
from services.tms_data.cascade import run_document, run_enrich
from services.tms_data.enriched_invoice import EnrichedInvoice, FailedRow
from services.tms_data.failed_rows import FailedRowsTracker

logger = logging.getLogger("ngl.tms_data")

Source = Literal["api", "browser"]


class TMSDataLayer:
    """Single gateway between tools and the QBO/TMS sources.

    Tools call enrich_invoice / get_document / get_documents. Failures land
    in the per-job FailedRowsTracker; the UI reads them via get_failed_rows
    and the user can retry via retry_failed_row / retry_all_failed.
    """

    def __init__(self, qbo_api, tms_api, tms_browser) -> None:
        self._qbo_api = qbo_api
        self._tms_api = tms_api
        self._tms_browser = tms_browser
        self._failed = FailedRowsTracker()

    # ── Per-row data access ────────────────────────────────────────

    async def enrich_invoice(
        self,
        job_id: str,
        invoice_data: dict,
        source: Source = "api",
    ) -> EnrichedInvoice:
        """Fill in missing chassis / CNEE / D/O sender from TMS.

        Failures during the cascade are recorded in the failed-rows tracker
        but the partially-filled EnrichedInvoice is still returned.
        """
        if source == "browser":
            enriched, err = await run_enrich_browser(invoice_data, self._tms_browser)
            failed_at = "tms_browser"
        else:
            enriched, err = await run_enrich(invoice_data, self._tms_api)
            failed_at = "tms_api"

        if err:
            self._failed.record_failure(
                job_id=job_id,
                invoice_number=str(invoice_data.get("DocNumber") or ""),
                container_number=enriched.container_no,
                operation="enrich_invoice",
                doc_type=None,
                error_message=err,
                source=failed_at,
            )

        return enriched

    # ── Failed-rows queries (more methods added in Tasks 11-13) ────

    def get_failed_rows(self, job_id: str) -> list[FailedRow]:
        return self._failed.get_rows(job_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest agent/tests/test_tms_data/test_data_layer.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add agent/services/tms_data/__init__.py agent/tests/test_tms_data/test_data_layer.py
git commit -m "feat(tms_data): TMSDataLayer.enrich_invoice + get_failed_rows"
```

---

## Task 11: TMSDataLayer — `get_document` + `get_documents`

**Files:**
- Modify: `agent/services/tms_data/__init__.py`
- Modify: `agent/tests/test_tms_data/test_data_layer.py`

- [ ] **Step 1: Add failing tests**

Append to `agent/tests/test_tms_data/test_data_layer.py`:

```python
@pytest.mark.asyncio
async def test_get_document_via_api(tmp_path):
    qbo_api = AsyncMock()
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value={
        "documents": [{"type_": "POD", "file_url": "https://cdn.example/pod.pdf"}],
    })
    tms_api.download_document = AsyncMock(return_value=b"pod data")
    tms_browser = AsyncMock()

    dl = TMSDataLayer(qbo_api, tms_api, tms_browser)
    invoice = _qbo_invoice(wo="LM01")
    invoice["DocNumber"] = "LM26040454F"

    path = await dl.get_document("job-1", invoice, "POD", tmp_path)

    assert path is not None
    assert path.read_bytes() == b"pod data"
    assert dl.get_failed_rows("job-1") == []


@pytest.mark.asyncio
async def test_get_document_records_failure_on_api_error(tmp_path):
    qbo_api = AsyncMock()
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(side_effect=RuntimeError("network"))
    tms_browser = AsyncMock()

    dl = TMSDataLayer(qbo_api, tms_api, tms_browser)
    invoice = _qbo_invoice(wo="LM01")
    invoice["DocNumber"] = "LM26040454F"

    path = await dl.get_document("job-1", invoice, "POD", tmp_path)

    assert path is None
    rows = dl.get_failed_rows("job-1")
    assert len(rows) == 1
    assert rows[0].operation == "get_document"
    assert rows[0].doc_type == "POD"


@pytest.mark.asyncio
async def test_get_documents_returns_dict_of_found(tmp_path):
    qbo_api = AsyncMock()
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value={
        "documents": [
            {"type_": "POD", "file_url": "https://cdn.example/pod.pdf"},
            {"type_": "BL", "file_url": "https://cdn.example/bl.pdf"},
        ],
    })

    async def _download(url):
        return b"pod" if "pod" in url else b"bl"
    tms_api.download_document = AsyncMock(side_effect=_download)
    tms_browser = AsyncMock()

    dl = TMSDataLayer(qbo_api, tms_api, tms_browser)
    invoice = _qbo_invoice(wo="LM01")
    invoice["DocNumber"] = "LM26040454F"

    paths = await dl.get_documents("job-1", invoice, ["POD", "BL", "MISSING"], tmp_path)

    assert "POD" in paths
    assert "BL" in paths
    assert "MISSING" not in paths
    assert paths["POD"].read_bytes() == b"pod"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest agent/tests/test_tms_data/test_data_layer.py -v -k get_document`
Expected: `AttributeError: 'TMSDataLayer' object has no attribute 'get_document'`

- [ ] **Step 3: Add the methods**

Append inside the `TMSDataLayer` class in `agent/services/tms_data/__init__.py` (place these methods immediately after `enrich_invoice`):

```python
    async def get_document(
        self,
        job_id: str,
        invoice_data: dict,
        doc_type: str,
        dest_dir: Path,
        source: Source = "api",
    ) -> Optional[Path]:
        """Fetch a single doc to disk. None if not found anywhere."""
        if source == "browser":
            invoice_number = str(invoice_data.get("DocNumber") or "")
            path, err = await run_document_browser(
                invoice_data, doc_type, dest_dir, self._tms_browser, invoice_number,
            )
            failed_at = "tms_browser"
        else:
            path, err = await run_document(
                invoice_data, doc_type, dest_dir, self._tms_api,
            )
            failed_at = "tms_api"

        if err:
            self._failed.record_failure(
                job_id=job_id,
                invoice_number=str(invoice_data.get("DocNumber") or ""),
                container_number=None,
                operation="get_document",
                doc_type=doc_type,
                error_message=err,
                source=failed_at,
            )
        return path

    async def get_documents(
        self,
        job_id: str,
        invoice_data: dict,
        doc_types: list[str],
        dest_dir: Path,
        source: Source = "api",
    ) -> dict[str, Path]:
        """Fetch multiple docs. Returns dict of doc_type → path (only found ones)."""
        out: dict[str, Path] = {}
        for dt in doc_types:
            p = await self.get_document(job_id, invoice_data, dt, dest_dir, source)
            if p is not None:
                out[dt] = p
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest agent/tests/test_tms_data/test_data_layer.py -v`
Expected: all 6 tests pass

- [ ] **Step 5: Commit**

```bash
git add agent/services/tms_data/__init__.py agent/tests/test_tms_data/test_data_layer.py
git commit -m "feat(tms_data): TMSDataLayer.get_document + get_documents"
```

---

## Task 12: TMSDataLayer — `retry_failed_row` + `retry_all_failed` + `reset_for_new_job`

**Files:**
- Modify: `agent/services/tms_data/__init__.py`
- Modify: `agent/tests/test_tms_data/test_data_layer.py`

- [ ] **Step 1: Add failing tests**

Append to `agent/tests/test_tms_data/test_data_layer.py`:

```python
@pytest.mark.asyncio
async def test_retry_failed_row_with_browser_succeeds(tmp_path):
    """A row that failed on TMS API can be retried via browser and the success removes it from the box."""
    qbo_api = AsyncMock()
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(side_effect=RuntimeError("api down"))
    tms_browser = AsyncMock()
    tms_browser.fetch_doc_by_wo = AsyncMock(return_value=tmp_path / "POD_browser.pdf")
    (tmp_path / "POD_browser.pdf").write_bytes(b"pod browser")
    tms_browser.bc_detail_type_segment = lambda inv_no: "import"

    dl = TMSDataLayer(qbo_api, tms_api, tms_browser)
    invoice = _qbo_invoice(wo="LM01")
    invoice["DocNumber"] = "LM26040454F"

    # Original API attempt fails, row recorded
    await dl.get_document("job-1", invoice, "POD", tmp_path)
    assert len(dl.get_failed_rows("job-1")) == 1
    row_id = dl.get_failed_rows("job-1")[0].row_id

    # Note: real implementation needs to remember the original invoice + dest_dir
    # for the retry. For this test we just verify the retry method exists and
    # routes correctly via source="browser".
    succeeded = await dl.retry_failed_row("job-1", row_id, source="browser")

    assert succeeded is True
    assert dl.get_failed_rows("job-1") == []


@pytest.mark.asyncio
async def test_retry_failed_row_unknown_id_returns_false():
    qbo_api = AsyncMock()
    tms_api = AsyncMock()
    tms_browser = AsyncMock()

    dl = TMSDataLayer(qbo_api, tms_api, tms_browser)
    succeeded = await dl.retry_failed_row("job-1", "row-nonexistent", source="api")

    assert succeeded is False


def test_reset_for_new_job_clears_rows():
    qbo_api = AsyncMock()
    tms_api = AsyncMock()
    tms_browser = AsyncMock()

    dl = TMSDataLayer(qbo_api, tms_api, tms_browser)
    dl._failed.record_failure(
        "job-1", "INV1", "C1", "get_document", "POD", "err", "tms_api",
    )
    dl.reset_for_new_job("job-1")

    assert dl.get_failed_rows("job-1") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest agent/tests/test_tms_data/test_data_layer.py -v -k "retry or reset"`
Expected: `AttributeError: 'TMSDataLayer' object has no attribute 'retry_failed_row'`

- [ ] **Step 3: Implement retry methods**

The retry method needs to remember the original call context (invoice, dest_dir). Append a helper to record context when a failure happens, and the retry methods themselves.

Modify `agent/services/tms_data/__init__.py` to:

1. Add an internal context map alongside `self._failed` (in `__init__`):
```python
        self._retry_ctx: dict[str, dict] = {}  # row_id -> {invoice_data, dest_dir, doc_type}
```

2. Update `enrich_invoice` and `get_document` to record context when they record a failure. Replace the `if err:` block in **both** methods so they save context. For `enrich_invoice`, add after `record_failure`:
```python
            row_id_for_ctx = self._failed.get_rows(job_id)[-1].row_id
            self._retry_ctx[row_id_for_ctx] = {
                "operation": "enrich_invoice",
                "invoice_data": invoice_data,
            }
```

For `get_document`, add after `record_failure`:
```python
            row_id_for_ctx = self._failed.get_rows(job_id)[-1].row_id
            self._retry_ctx[row_id_for_ctx] = {
                "operation": "get_document",
                "invoice_data": invoice_data,
                "doc_type": doc_type,
                "dest_dir": dest_dir,
            }
```

3. Append three new methods to the class:

```python
    async def retry_failed_row(
        self,
        job_id: str,
        row_id: str,
        source: Source,
    ) -> bool:
        """Re-run a failed row's original op using the chosen source.

        Removes the row from the failed-rows list on success. Leaves it
        (with updated error) on continued failure.
        """
        row = self._failed.find_row(job_id, row_id)
        ctx = self._retry_ctx.get(row_id)
        if row is None or ctx is None:
            return False

        # Remove the old failure entry first; new failures (if any) will be re-recorded.
        self._failed.remove_row(job_id, row_id)
        self._retry_ctx.pop(row_id, None)

        if ctx["operation"] == "enrich_invoice":
            enriched = await self.enrich_invoice(job_id, ctx["invoice_data"], source=source)
            # Success = no new failure for this invoice was just recorded.
            return not any(
                r.invoice_number == row.invoice_number and r.operation == "enrich_invoice"
                for r in self._failed.get_rows(job_id)
            )
        elif ctx["operation"] == "get_document":
            path = await self.get_document(
                job_id, ctx["invoice_data"], ctx["doc_type"],
                ctx["dest_dir"], source=source,
            )
            return path is not None
        return False

    async def retry_all_failed(self, job_id: str, source: Source) -> dict:
        """Retry every row currently in the failed-rows list. Returns counts."""
        rows = self._failed.get_rows(job_id)
        succeeded = 0
        still_failed = 0
        for r in rows:
            ok = await self.retry_failed_row(job_id, r.row_id, source)
            if ok:
                succeeded += 1
            else:
                still_failed += 1
        return {"succeeded": succeeded, "still_failed": still_failed}

    def reset_for_new_job(self, job_id: str) -> None:
        """Clear all failed rows + retry context for a job."""
        rows = self._failed.get_rows(job_id)
        for r in rows:
            self._retry_ctx.pop(r.row_id, None)
        self._failed.reset(job_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest agent/tests/test_tms_data/test_data_layer.py -v`
Expected: all 9 tests pass

- [ ] **Step 5: Commit**

```bash
git add agent/services/tms_data/__init__.py agent/tests/test_tms_data/test_data_layer.py
git commit -m "feat(tms_data): retry_failed_row + retry_all_failed + reset_for_new_job"
```

---

## Task 13: Wire `TMSDataLayer` into `agent/main.py`

The data layer needs to be instantiated once at agent startup so future milestones can pass it into job mixins. We attach it to the FastAPI app state but **don't** wire it into any router or job manager yet.

**Files:**
- Modify: `agent/main.py`

- [ ] **Step 1: Read the relevant section of main.py**

Run: `python -m pytest agent/tests/test_tms_data/ -v` (full suite — should be all green before main.py edits)
Expected: 30+ tests passing

Open `agent/main.py` and locate where `_qbo_api` and `_tms_api` are instantiated (likely in the FastAPI lifespan or module top-level).

- [ ] **Step 2: Add import + instantiation**

In `agent/main.py`, near the top:

```python
from services.tms_data import TMSDataLayer
```

Wherever `_qbo_api`, `_tms_api`, and the TMS browser instance are created (search for `QBOApiClient(` and `TMSApiClient(`), add directly after them:

```python
_tms_data = TMSDataLayer(_qbo_api, _tms_api, _tms_browser)
```

If the surrounding code attaches services to `app.state` or passes them into routers, attach `_tms_data` the same way:

```python
app.state.tms_data = _tms_data
```

- [ ] **Step 3: Verify the agent starts cleanly**

Run: `python agent/main.py` (or `python -m uvicorn agent.main:app --port 8787`)
Expected: agent starts without import errors. Tail the log — look for any `[ngl.tms_data]` log lines (none expected yet, since nothing calls into it).

Stop the agent (Ctrl+C).

- [ ] **Step 4: Smoke test — call /tms/api-test if a real WO# is known**

If you have a known TMS work order number on hand, run with the agent up:

```bash
curl http://localhost:8787/tms/api-test/LM2602170009
```

Expected: existing test endpoint returns the WO record with `documents`, `doSender`, etc. This is unchanged behavior — we're only confirming the data layer doesn't break the existing wiring.

- [ ] **Step 5: Commit**

```bash
git add agent/main.py
git commit -m "feat(tms_data): wire TMSDataLayer into agent startup"
```

---

## Task 14: Smoke test against real TMS API to validate extractors

Validate that `extract_chassis_from_tms_wo` finds the chassis on a real WO record. The current implementation tries `chassis_no`, `chassis`, and `chassis_number` — at least one of these will match what TMS returns. This task confirms which one and adjusts if needed.

**Files:**
- (potentially) Modify: `agent/services/tms_data/extractors.py`

- [ ] **Step 1: Start the agent**

Run: `python agent/main.py` (or however the agent is normally started)

- [ ] **Step 2: Add a temporary debug endpoint**

In `agent/routers/tms.py`, append at the end of the file:

```python
@router.get("/api-fields/{wo_no}")
async def tms_api_field_inspect(wo_no: str):
    """Temporary: dump the full WO API response to inspect field names.
    Remove after extractor validation."""
    if not _tms_api.is_configured():
        raise HTTPException(status_code=400,
                            detail="TMS_CLIENT_ID/SECRET not set in .env")
    wo = await _tms_api.get_work_order(wo_no)
    if not wo:
        raise HTTPException(status_code=404, detail=f"WO {wo_no} not found")
    return {"wo_no": wo_no, "all_fields": list(wo.keys()), "raw": wo}
```

- [ ] **Step 3: Restart the agent and inspect a known WO**

```bash
curl http://localhost:8787/tms/api-fields/LM2602170009
```

(Use any WO# that you know has a chassis assigned. From memory the test containers are KKFU7654819 → LM2602170009, MAGU5764069 → LM2602170008.)

Look at the `all_fields` array in the response. Note which field name carries the chassis (likely `chassis_no` per the spec).

- [ ] **Step 4: If the field name differs from the extractor candidates, update**

Open `agent/services/tms_data/extractors.py` and update the `extract_chassis_from_tms_wo` function's tuple of candidates if needed. Same for CNEE if `billto` isn't the actual field name.

If both candidates are correct (`chassis_no` for chassis, `billto` for CNEE), no change needed.

- [ ] **Step 5: Remove the debug endpoint**

Delete the `/api-fields/{wo_no}` route from `agent/routers/tms.py` — it was scaffolding only.

- [ ] **Step 6: Run all tms_data tests one more time**

Run: `python -m pytest agent/tests/test_tms_data/ -v`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add agent/services/tms_data/extractors.py agent/routers/tms.py
git commit -m "chore(tms_data): validate extractors against real TMS API; remove debug endpoint"
```

---

## Task 15: Bundle the design doc with milestone 1 code

Per the project's commit pattern, the spec ships alongside its first implementation.

**Files:**
- Already in place: `docs/superpowers/specs/2026-04-28-tms-data-layer-design.md`
- Already in place: `docs/superpowers/plans/2026-04-28-tms-data-layer-milestone-1.md`

- [ ] **Step 1: Verify both docs are present**

Run: `ls docs/superpowers/specs docs/superpowers/plans`
Expected: both files listed.

- [ ] **Step 2: Run the full test suite one final time**

Run: `python -m pytest agent/tests/ -v`
Expected: all tests pass (existing + new tms_data tests).

- [ ] **Step 3: Commit the docs**

```bash
git add docs/superpowers/specs/2026-04-28-tms-data-layer-design.md docs/superpowers/plans/2026-04-28-tms-data-layer-milestone-1.md
git commit -m "docs: TMS Data Layer design spec + milestone 1 plan"
```

- [ ] **Step 4: Verify clean working tree**

Run: `git status`
Expected: `nothing to commit, working tree clean`

---

## Milestone 1 — End State

At the end of this plan:
- `agent/services/tms_data/` package exists with full unit-test coverage
- `agent/main.py` instantiates a `TMSDataLayer` at startup and exposes it as `app.state.tms_data`
- No tool calls into the data layer yet — that's milestone 2
- No version bump, no rebuild, no GitHub release — there's no user-visible change yet
- Design doc + milestone 1 plan are in the repo

**Why no rebuild?** The agent has new dead code that's not invoked by any tool. Releasing without milestone 2 would push out an installer that adds a few hundred lines of unused code. Per the spec, milestone 2's Invoice Sender migration is the first user-visible change — that milestone ends with the version bump, rebuild, and GitHub release that delivers both milestone 1 and milestone 2 to co-workers.

**Next milestone:** Invoice Sender migration. Will require its own plan (`docs/superpowers/plans/2026-XX-XX-tms-data-layer-milestone-2-invoice-sender.md`). When ready, run the writing-plans skill again.

---

## Self-Review Notes

- All 15 tasks have concrete code, exact paths, and runnable commands. No "TBD" or "implement appropriate error handling" placeholders.
- Type and method names are consistent across tasks: `EnrichedInvoice`, `FailedRow`, `FailedRowsTracker`, `TMSDataLayer`, `Source = Literal["api", "browser"]`.
- Spec coverage:
  - **Public class shape (spec section "The Public Interface")** → Tasks 10-12
  - **Cascade behavior (spec "Behavior in plain English")** → Tasks 7-8
  - **Failed Rows tracker** → Tasks 5-6, exposed via Tasks 10-12
  - **Browser-only opt-in path** → Task 9, routed via Tasks 10-11
  - **Per-job state + reset** → Task 12
  - **Hard Invariants section** → Not directly applicable to milestone 1 (no tool migration yet); will be enforced and tested in milestone 2's plan
- One unresolved spec item is intentionally deferred: Open Question #4 (QBO-side failure UX) — the data layer in milestone 1 only records TMS-side failures. QBO failures continue to surface through existing per-tool error events. Bringing them into the Failed Rows box is a milestone 2 decision.
