# Merge Tool V2 — M3 (Fetching + Ready) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Per `feedback_opus_for_heavy_tasks.md`: default subagents to **Opus** for the renderer + sidebar + SSE-wiring tasks; Sonnet only for trivial CSS tasks.

**Goal:** Wire the existing fetch/POD pipeline into the v2 merge tool's Fetching and Ready states. Bake in INV#-driven doc routing, the Will-fetch column, IT/ITE chain extension, error sidebar with auto-save and routing trace, and partial-completion handling for cancelled fetches.

**Architecture:** Frontend-heavy (`merge-v2.js` ~650 → ~1200 lines). One small backend change in `agent/services/job_manager/fetch_job.py` to switch routing primacy from WO# letter to INV# pos-2 letter and extend the doc chain with IT/ITE. SSE event payload gains a `chain_attempted` field so the sidebar can render an accurate routing trace.

**Tech Stack:** Vanilla JS ES modules · existing `agentBridge.streamProgress(jobId, onEvent)` SSE client · existing `POST /jobs/fetch-missing` agent endpoint · TMS browser already supports IT/ITE via `fetch_doc_by_wo` · pytest for backend tests · CSS-only animations (`@keyframes`).

**Spec:** [`docs/superpowers/specs/2026-05-06-merge-tool-v2-m3-fetching-design.md`](../specs/2026-05-06-merge-tool-v2-m3-fetching-design.md)

**Mockups:**
- [`app/mockups/merge-tool-m3-ready.html`](../../../app/mockups/merge-tool-m3-ready.html) — M3-specific (Will-fetch column, routing trace, Resume fetch variant)
- [`app/mockups/merge-tool-redesign.html`](../../../app/mockups/merge-tool-redesign.html) — parent v2.42 visual contract for sidebar + table chrome

---

## File Structure

| File | Action | Why |
|---|---|---|
| `app/assets/js/shared/utils.js` | Modify | Add `parseInvType(inv)`, `parseWoType(wo)`, and `routingDecisionFor(row)` pure helpers |
| `app/assets/js/tools/merge/merge-v2.js` | Modify | Most of M3: render Fetching/Ready, sidebar, SSE wiring, auto-save, retry, resume fetch, defensive teardown |
| `app/assets/css/styles.css` | Modify (append) | New M3 block scoped to `#mergeToolViewV2`: routing summary band, will-chip, doc-pill animation, sidebar, routing-trace |
| `agent/services/job_manager/fetch_job.py` | Modify | Switch `_tms_pod_fallback` to INV#-primary routing; extend chain with IT/ITE; return `chain_attempted` list; thread it into the `pod_found`/`pod_missing` SSE events |
| `agent/tests/test_job_manager/test_fetch_job_inv_routing.py` | Create | Unit tests for INV#-primary routing + IT/ITE chain + `chain_attempted` payload |
| `desktop/VERSION` | Modify | Bump `2.47` → `2.48` |
| `desktop/package.json` | Auto via `bump-version.js` | electron-builder reads version from here |

The legacy `merge.js`, `state.js`, `mergeToolView` HTML, `agent-bridge.js`, and the SQLite database are all untouched.

---

## How to test each task

This codebase has no JS test framework — frontend verification is manual via dev mode + DevTools console. Backend has pytest.

```bash
# Frontend dev (Electron, hot-reloads on file changes inside app/)
cd desktop && npm start

# Backend tests
cd agent && pytest tests/test_job_manager/test_fetch_job_inv_routing.py -v
```

Inside the dev-mode app:

1. Settings → toggle **Merge Tool — Beta** ON (already on from M2).
2. Switch to the Merge tool.
3. Open DevTools (Ctrl+Shift+I) for inline checks.

Each task's "Verify" step says exactly what to do.

### Sample test files

Already in `docs/`:
- **`docs/no sav.xlsx`** — 110 invoice rows, 100 unique containers, mixed import/export INV# prefixes (LM…, PE…, etc.). Use this for routing checks.
- **`docs/NGL INVOICE 05.05.2026 (1).xlsx`** — clean batch, regression check.
- **`docs/idea nouva weekly 04.13-04.19_formatted.xlsx`** — second clean batch.

For sidebar testing without a live agent: enable "Mock fetch" (we'll add a tiny dev shim in Task 9) so Fetching can be exercised without QBO/TMS connectivity.

---

## Task 1: Pre-flight + extend `v2State` for M3 fields

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js:11-25` (`v2State` object)
- Modify: `app/assets/js/tools/merge/merge-v2.js:60-95` (`setStateV2('empty')` reset path)

- [ ] **Step 1: Confirm M2/v2.47 baseline still works**

```
cd desktop && npm start
```

Settings → Merge Tool — Beta ON. Drop `docs/NGL INVOICE 05.05.2026 (1).xlsx`. Confirm the Review success card renders with `Continue to Merge →` button still reading the M2 wording (we rename in Task 9). Stop dev (`Ctrl+C`).

- [ ] **Step 2: Extend `v2State` with M3 fields**

In `app/assets/js/tools/merge/merge-v2.js`, find the `v2State` block (around line 12) and replace it with:

```js
// ── Module-local state ──
const v2State = {
  subMode: 'empty',          // empty | loading | review | fetching | ready | merging | done
  excelFile: null,
  excelHeaders: [],
  rows: [],                  // M3: each row gains status, fetchResult, manualPodFile, skipped fields
  loadingError: null,
  searchQuery: '',
  sortMode: 'excel',
  activeTab: 'all',          // all | issues | errors | queued
  showAllInSuccess: false,
  // ── M3: fetch + sidebar ──
  jobId: null,               // active fetch job id
  eventSource: null,         // SSE EventSource handle (closed on teardown)
  fetchProgress: 0,          // X in "Fetching X / N"
  fetchTotal: 0,             // N in "Fetching X / N"
  fetchCurrentContainer: '', // shown next to the progress label
  lastFetchedContainer: '',  // shown in "Last fetched: <c>" meta line on Resume
  openSidebarRow: null,      // index into v2State.rows; null = sidebar closed
  // ── M4 placeholders (unchanged from M2) ──
  pendingMode: null,
  completedModes: [],
  lastCompletedMode: null,
};
```

The row shape in `v2State.rows[i]` evolves to include these M3 fields (filled by SSE handlers in Task 11):

```
{
  rowNum, containerNumber, invoiceNumber, workOrderNumber, customer,
  selected, status, statusReason,
  // ── M3 additions ──
  routingType: 'import' | 'export' | 'unknown',  // set in Task 2
  expectedDoc: 'POD' | 'BOL/POL' | '?',          // set in Task 2
  fetchResult: {                                  // populated by SSE; null pre-fetch
    invPill: 'ok' | 'fallback' | 'miss' | 'queued',
    podPill: 'ok' | 'fallback' | 'miss' | 'queued',
    podLabel: 'POD' | 'BOL' | 'POL' | 'IT' | 'ITE' | '—',
    statusText: string,
    chainAttempted: [{ type: string, outcome: 'qbo_miss' | 'tms_miss' | 'qbo_hit' | 'tms_hit' }],
    message: string  // human reason on miss
  },
  manualPodFile: null,    // File blob set when user drops a PDF in the sidebar
  skipped: false,         // true after Skip in sidebar
}
```

(Don't try to materialize all those fields up front — `validateRows` from M2 keeps writing the original ones; SSE handlers add `fetchResult` later.)

- [ ] **Step 3: Update `setStateV2('empty')` reset to clear new fields**

Replace the body of the `if (name === 'empty')` block (around line 63-76):

```js
  if (name === 'empty') {
    // M3: defensive teardown — close any SSE / cancel any active job before nuking state
    try {
      if (v2State.eventSource) {
        v2State.eventSource.close();
        v2State.eventSource = null;
      }
      if (v2State.jobId) {
        // Fire-and-forget cancel — we don't await it; if it fails, the agent will
        // notice no consumer and tear down on its own
        fetch(`http://localhost:8787/jobs/${encodeURIComponent(v2State.jobId)}/cancel`, {
          method: 'POST',
        }).catch(() => {});
        v2State.jobId = null;
      }
    } catch (_) { /* best-effort cleanup, never throw */ }

    v2State.completedModes = [];
    v2State.lastCompletedMode = null;
    v2State.excelFile = null;
    v2State.excelHeaders = [];
    v2State.rows = [];
    v2State.loadingError = null;
    v2State.searchQuery = '';
    v2State.sortMode = 'excel';
    v2State.activeTab = 'all';
    v2State.showAllInSuccess = false;
    v2State.fetchProgress = 0;
    v2State.fetchTotal = 0;
    v2State.fetchCurrentContainer = '';
    v2State.lastFetchedContainer = '';
    v2State.openSidebarRow = null;
    const xinput = document.getElementById('v2ExcelInput');
    if (xinput) xinput.value = '';
  }
```

- [ ] **Step 4: Verify**

```
cd desktop && npm start
```

Drop `docs/NGL INVOICE 05.05.2026 (1).xlsx`. In DevTools console:

```js
// Internal state isn't directly exposed; verify M2 baseline still works:
document.querySelector('#mergeToolViewV2 .review-success-card .title')?.textContent
// Expected: "All N rows checked out"
```

Click `+ New Merge` (top header). Lands cleanly on Empty (no console errors). Drop the file again — works.

- [ ] **Step 5: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge/v2/m3): extend v2State with fetch + sidebar fields; defensive teardown"
```

---

## Task 2: Routing helpers in `shared/utils.js`

**Files:**
- Modify: `app/assets/js/shared/utils.js` (append new helpers)

- [ ] **Step 1: Append routing helpers**

At the end of `app/assets/js/shared/utils.js`, append:

```js
// ── Doc-fetch routing (M3) ──────────────────────────────────────────────────
// Decide whether a row is an import or export based on the INV# prefix
// (primary) and the WO# letter (fallback). See spec
// docs/superpowers/specs/2026-05-06-merge-tool-v2-m3-fetching-design.md.

/**
 * Parse the type letter at INV# position 2.
 * @returns {'import' | 'export' | null}
 */
export function parseInvType(inv) {
  if (!inv || inv.length < 2) return null;
  const c = inv[1].toUpperCase();
  if (c === 'M') return 'import';
  if (c === 'E') return 'export';
  return null;
}

/**
 * Parse the type letter from WO# (M=import, X=export, anywhere in the string).
 * @returns {'import' | 'export' | null}
 */
export function parseWoType(wo) {
  if (!wo) return null;
  const upper = wo.toUpperCase();
  if (upper.includes('X')) return 'export';
  if (upper.includes('M')) return 'import';
  return null;
}

/**
 * Decide the doc-fetch type for a row. INV# prefix is primary, WO# letter
 * is the fallback when the prefix doesn't parse.
 * @param {{invoiceNumber?: string, workOrderNumber?: string}} row
 * @returns {{ type: 'import' | 'export' | 'unknown', expectedDoc: 'POD' | 'BOL/POL' | '?' }}
 */
export function routingDecisionFor(row) {
  const fromInv = parseInvType(row.invoiceNumber);
  if (fromInv) {
    return { type: fromInv, expectedDoc: fromInv === 'import' ? 'POD' : 'BOL/POL' };
  }
  const fromWo = parseWoType(row.workOrderNumber);
  if (fromWo) {
    return { type: fromWo, expectedDoc: fromWo === 'import' ? 'POD' : 'BOL/POL' };
  }
  return { type: 'unknown', expectedDoc: '?' };
}
```

- [ ] **Step 2: Verify in DevTools console**

```
cd desktop && npm start
```

In DevTools console (with the merge tool open):

```js
import('./assets/js/shared/utils.js').then(m => {
  console.log(m.parseInvType('LM26050100F'));   // 'import'
  console.log(m.parseInvType('PE26050103F'));   // 'export'
  console.log(m.parseInvType('WEIRD-1234'));    // null (W is not M/E)
  console.log(m.parseInvType(''));              // null
  console.log(m.parseWoType('LM2605040001'));   // 'import'
  console.log(m.parseWoType('PX2605040004'));   // 'export'
  console.log(m.parseWoType('VA2605040007'));   // null
  console.log(m.routingDecisionFor({ invoiceNumber: 'LM26050100F' }));
  // { type: 'import', expectedDoc: 'POD' }
  console.log(m.routingDecisionFor({ invoiceNumber: 'WEIRD-1', workOrderNumber: 'LM2605040008' }));
  // { type: 'import', expectedDoc: 'POD' } — fell back to WO# letter
  console.log(m.routingDecisionFor({ invoiceNumber: 'GARBAGE', workOrderNumber: '' }));
  // { type: 'unknown', expectedDoc: '?' }
});
```

All eight assertions should match the comment.

- [ ] **Step 3: Commit**

```bash
git add app/assets/js/shared/utils.js
git commit -m "feat(shared): add INV#-primary routing helpers (parseInvType/parseWoType/routingDecisionFor)"
```

---

## Task 3: Backend — switch `_tms_pod_fallback` to INV#-primary routing + IT/ITE chain

**Files:**
- Modify: `agent/services/job_manager/fetch_job.py:65-136` (`_tms_pod_fallback`)
- Modify: `agent/services/job_manager/fetch_job.py:251-282` (caller — `_process_one_container`'s POD section)

- [ ] **Step 1: Rewrite `_tms_pod_fallback` to use INV# pos-2 + extended chain**

Replace the entire `_tms_pod_fallback` method (currently lines 65-136) with:

```python
    async def _tms_pod_fallback(
        self, job, container, invoice_data: dict, dest_path: Path,
    ):
        """Fetch a proof-of-delivery doc from TMS when QBO has none.

        Routes by INV# pos-2 letter (primary) → WO# letter (fallback):
          - INV# pos-2 = 'M' or WO# contains 'M' → import → POD → BOL → POL → IT
          - INV# pos-2 = 'E' or WO# contains 'X' → export → BOL → POL → ITE
          - neither parses → unknown → POD → BOL → POL → IT → ITE

        Returns a tuple: (success_doc_type | None, chain_attempted)
          - success_doc_type: 'POD' | 'BOL' | 'POL' | 'IT' | 'ITE' | None
          - chain_attempted: list[{'type': str, 'outcome': 'tms_hit' | 'tms_miss' | 'tms_error'}]
            in the order they were tried.

        Writes the file to dest_path on success.
        """
        if not self._tms_data:
            logger.info(
                "TMSDataLayer not configured — skipping TMS POD fallback for %s",
                container.container_number,
            )
            return None, []

        from services.tms_data.extractors import extract_wo_from_qbo
        wo_no = (extract_wo_from_qbo(invoice_data) or "").upper()
        inv_no = (container.invoice_number or "").upper()

        # INV# pos-2 primary
        inv_letter = inv_no[1] if len(inv_no) >= 2 else ""
        if inv_letter == "M":
            doc_types = ("POD", "BOL", "POL", "IT")
            wo_kind = "import (by INV#)"
        elif inv_letter == "E":
            doc_types = ("BOL", "POL", "ITE")
            wo_kind = "export (by INV#)"
        elif "X" in wo_no:
            doc_types = ("BOL", "POL", "ITE")
            wo_kind = "export (by WO#)"
        elif "M" in wo_no:
            doc_types = ("POD", "BOL", "POL", "IT")
            wo_kind = "import (by WO#)"
        else:
            doc_types = ("POD", "BOL", "POL", "IT", "ITE")
            wo_kind = "unknown"

        logger.info(
            "TMS POD fallback for %s: INV=%s WO=%s kind=%s trying=%s",
            container.container_number, inv_no or "<none>", wo_no or "<none>", wo_kind, doc_types,
        )

        chain_attempted: list[dict] = []
        temp_dir = Path(tempfile.mkdtemp(prefix="ngl_pod_fb_"))
        try:
            for doc_type in doc_types:
                try:
                    path = await asyncio.wait_for(
                        self._tms_data.get_document(
                            job.id, invoice_data, doc_type, temp_dir, source="api",
                        ),
                        timeout=TMS_FETCH_TIMEOUT_S,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "TMS get_document(%s) timed out for %s",
                        doc_type, container.container_number,
                    )
                    chain_attempted.append({"type": doc_type, "outcome": "tms_error"})
                    continue
                except Exception as e:
                    logger.warning(
                        "TMS get_document(%s) failed for %s: %s",
                        doc_type, container.container_number, e,
                    )
                    chain_attempted.append({"type": doc_type, "outcome": "tms_error"})
                    continue

                if path and path.exists():
                    if dest_path.exists():
                        dest_path.unlink()
                    shutil.move(str(path), str(dest_path))
                    logger.info(
                        "TMS POD fallback succeeded for %s via %s",
                        container.container_number, doc_type,
                    )
                    chain_attempted.append({"type": doc_type, "outcome": "tms_hit"})
                    return doc_type, chain_attempted

                chain_attempted.append({"type": doc_type, "outcome": "tms_miss"})
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        return None, chain_attempted
```

Key behavior changes vs the v2.44 version:
- `inv_letter` (INV# pos-2) is checked first; only when it doesn't match M/E does the function fall through to WO# letter
- IT and ITE are added at the end of each chain
- Returns a tuple `(doc_type | None, chain_attempted)` instead of just the doc type

- [ ] **Step 2: Update the caller in `_process_one_container`**

Find the block around line 251-282 (`if not pod_obtained:`). Replace with:

```python
            if not pod_obtained:
                # Fall back to TMS Data Layer. Routes by INV# pos-2 (M=import, E=export)
                # primary; falls back to WO# letter; tries POD/BOL/POL/IT/ITE chain.
                await self._emit(job, "tms_pod_searching", {
                    "containerNumber": container.container_number,
                })
                tms_doc_type, chain_attempted = await self._tms_pod_fallback(
                    job, container, invoice_data, new_path,
                )
                if tms_doc_type:
                    strip_motw(new_path)
                    result.pod_file = new_name

                    pod_classification = await self._classifier.classify(new_path)
                    if pod_classification.needs_review:
                        result.needs_review = True

                    await self._emit(job, "pod_found", {
                        "containerNumber": container.container_number,
                        "file": new_name,
                        "source": f"TMS ({tms_doc_type})",
                        "tms_doc_type": tms_doc_type,
                        "chain_attempted": chain_attempted,
                    })
                else:
                    result.pod_missing = True
                    await self._emit(job, "pod_missing", {
                        "containerNumber": container.container_number,
                        "message": (
                            f"No POD/BOL/POL/IT/ITE found in QBO or TMS for "
                            f"container {container.container_number}"
                        ),
                        "chain_attempted": chain_attempted,
                    })
```

- [ ] **Step 3: Run smoke tests on the rest of fetch_job to ensure nothing else broke**

```bash
cd agent && pytest tests/test_job_manager/ -v
```

Expected: all existing tests still pass. (We're adding new tests in Task 4.)

- [ ] **Step 4: Commit**

```bash
git add agent/services/job_manager/fetch_job.py
git commit -m "feat(agent/fetch): INV#-primary routing + IT/ITE chain + chain_attempted SSE field"
```

---

## Task 4: Backend — unit tests for the new routing

**Files:**
- Create: `agent/tests/test_job_manager/test_fetch_job_inv_routing.py`

- [ ] **Step 1: Write the test file**

Create `agent/tests/test_job_manager/test_fetch_job_inv_routing.py`:

```python
"""Unit tests for INV#-primary routing + IT/ITE chain in _tms_pod_fallback."""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.job_manager import JobManager
from services.job_manager.job import ContainerRequest, Job


@pytest.fixture
def job_manager():
    """JobManager with mocked TMS data layer that we control per-test."""
    jm = JobManager()
    jm._tms_data = MagicMock()
    jm._tms_data.get_document = AsyncMock(return_value=None)  # default: TMS has nothing
    return jm


@pytest.fixture
def job():
    """Minimal Job for use in _tms_pod_fallback calls."""
    requests = [ContainerRequest(container_number="TCLU8830712", invoice_number="LM26050100F")]
    return Job("test-job-1", requests)


@pytest.fixture
def container():
    return ContainerRequest(container_number="TCLU8830712", invoice_number="LM26050100F")


@pytest.fixture
def dest_path(tmp_path):
    return tmp_path / "tcl_pod.pdf"


@pytest.mark.asyncio
async def test_inv_letter_M_uses_import_chain(job_manager, job, container, dest_path):
    """INV# starting with 'LM' → primary signal says import → POD → BOL → POL → IT chain."""
    container.invoice_number = "LM26050100F"
    invoice_data = {"Id": "1", "DocNumber": "LM26050100F"}

    result_type, chain = await job_manager._tms_pod_fallback(job, container, invoice_data, dest_path)

    # All four import doc types tried in order
    attempted_types = [c["type"] for c in chain]
    assert attempted_types == ["POD", "BOL", "POL", "IT"], (
        f"Expected import chain [POD, BOL, POL, IT], got {attempted_types}"
    )
    assert result_type is None
    # All outcomes are tms_miss (mocked TMS returned None)
    assert all(c["outcome"] == "tms_miss" for c in chain)


@pytest.mark.asyncio
async def test_inv_letter_E_uses_export_chain(job_manager, job, container, dest_path):
    """INV# starting with 'PE' → primary signal says export → BOL → POL → ITE chain (no POD)."""
    container.invoice_number = "PE26050103F"
    invoice_data = {"Id": "1", "DocNumber": "PE26050103F"}

    result_type, chain = await job_manager._tms_pod_fallback(job, container, invoice_data, dest_path)

    attempted_types = [c["type"] for c in chain]
    assert attempted_types == ["BOL", "POL", "ITE"], (
        f"Expected export chain [BOL, POL, ITE], got {attempted_types}"
    )
    assert result_type is None


@pytest.mark.asyncio
async def test_inv_letter_E_first_choice_BOL_succeeds(job_manager, job, container, dest_path, tmp_path):
    """Export row's first try (BOL) succeeds → chain stops, result is 'BOL'."""
    container.invoice_number = "PE26050103F"
    invoice_data = {"Id": "1", "DocNumber": "PE26050103F"}

    # Mock TMS to return a real file on the FIRST call (BOL)
    fake_bol = tmp_path / "fake_bol.pdf"
    fake_bol.write_bytes(b"%PDF-1.4 fake")
    job_manager._tms_data.get_document = AsyncMock(return_value=fake_bol)

    result_type, chain = await job_manager._tms_pod_fallback(job, container, invoice_data, dest_path)

    assert result_type == "BOL"
    assert chain == [{"type": "BOL", "outcome": "tms_hit"}]
    assert dest_path.exists()


@pytest.mark.asyncio
async def test_garbled_inv_falls_back_to_wo_letter(job_manager, job, container, dest_path):
    """Non-standard INV# prefix → falls back to WO# letter routing."""
    container.invoice_number = "WEIRD-1234"
    # WO# in QBO custom field — passed via invoice_data
    invoice_data = {
        "Id": "1",
        "DocNumber": "WEIRD-1234",
        "CustomField": [{"Name": "NGL REF#", "StringValue": "LM2605040008/CUSTOMER"}],
    }

    result_type, chain = await job_manager._tms_pod_fallback(job, container, invoice_data, dest_path)

    attempted_types = [c["type"] for c in chain]
    # WO contains M → import chain
    assert attempted_types == ["POD", "BOL", "POL", "IT"]


@pytest.mark.asyncio
async def test_garbled_inv_garbled_wo_uses_safety_chain(job_manager, job, container, dest_path):
    """Both signals fail → safety chain POD → BOL → POL → IT → ITE (5 types)."""
    container.invoice_number = "GARBAGE"
    invoice_data = {"Id": "1", "DocNumber": "GARBAGE"}  # no CustomField → no WO# extracted

    result_type, chain = await job_manager._tms_pod_fallback(job, container, invoice_data, dest_path)

    attempted_types = [c["type"] for c in chain]
    assert attempted_types == ["POD", "BOL", "POL", "IT", "ITE"]


@pytest.mark.asyncio
async def test_inv_overrides_wo_when_they_disagree(job_manager, job, container, dest_path):
    """INV# says export but WO# says import → INV# wins (it's primary)."""
    container.invoice_number = "PE26050200F"  # export
    invoice_data = {
        "Id": "1",
        "DocNumber": "PE26050200F",
        "CustomField": [{"Name": "NGL REF#", "StringValue": "LM2605040999/CUST"}],  # WO says import
    }

    result_type, chain = await job_manager._tms_pod_fallback(job, container, invoice_data, dest_path)

    attempted_types = [c["type"] for c in chain]
    # INV# wins → export chain
    assert attempted_types == ["BOL", "POL", "ITE"]


@pytest.mark.asyncio
async def test_chain_attempted_records_errors_separately_from_misses(
    job_manager, job, container, dest_path
):
    """An exception during a fetch attempt is recorded as 'tms_error', not 'tms_miss'."""
    container.invoice_number = "PE26050103F"
    invoice_data = {"Id": "1", "DocNumber": "PE26050103F"}

    call_count = {"n": 0}

    async def flaky(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("connection reset")
        return None

    job_manager._tms_data.get_document = AsyncMock(side_effect=flaky)

    result_type, chain = await job_manager._tms_pod_fallback(job, container, invoice_data, dest_path)

    assert chain[0] == {"type": "BOL", "outcome": "tms_error"}
    assert chain[1] == {"type": "POL", "outcome": "tms_miss"}
    assert chain[2] == {"type": "ITE", "outcome": "tms_miss"}
    assert result_type is None


@pytest.mark.asyncio
async def test_no_tms_data_returns_empty_chain(container, dest_path):
    """When TMS layer isn't configured, return (None, []) without trying anything."""
    jm = JobManager()
    jm._tms_data = None  # explicitly disabled
    job = Job("t", [container])
    invoice_data = {"Id": "1"}

    result_type, chain = await jm._tms_pod_fallback(job, container, invoice_data, dest_path)

    assert result_type is None
    assert chain == []
```

- [ ] **Step 2: Run the tests — all should fail with ImportError or fail because the old single-return signature**

Wait, actually Task 3's code change made `_tms_pod_fallback` return a tuple, so these new tests should ALL pass on the first run. Run them:

```bash
cd agent && pytest tests/test_job_manager/test_fetch_job_inv_routing.py -v
```

Expected: 8 tests pass. If any fail, the most likely cause is fixture wiring — `JobManager()` constructor may need `_jobs`, `_qbo_api`, etc. initialized. Inspect failures and add the necessary fixture init.

- [ ] **Step 3: Run the full backend test suite to ensure no regressions**

```bash
cd agent && pytest tests/ -v
```

Expected: green. If anything else broke, the most likely cause is the Task 3 caller change — make sure both branches (`tms_doc_type` truthy and falsy) emit the new payload fields.

- [ ] **Step 4: Commit**

```bash
git add agent/tests/test_job_manager/test_fetch_job_inv_routing.py
git commit -m "test(agent/fetch): cover INV#-primary routing + IT/ITE chain + chain_attempted"
```

---

## Task 5: CSS — routing summary band + Will-fetch chip styles

**Files:**
- Modify: `app/assets/css/styles.css` (append new block)

- [ ] **Step 1: Append the new CSS at the end of the v2 section**

Find the last v2 CSS block (search for `MERGE TOOL V2 (BETA) — scoped to #mergeToolViewV2`). At the end of that section (just before any non-v2 styles), append:

```css
/* ── M3: routing summary band + Will-fetch column ─────────────────────────── */
#mergeToolViewV2 .routing-summary {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 10px 16px;
  margin-bottom: 14px;
  font-size: 0.84rem;
  color: #475569;
  display: flex; align-items: center; gap: 18px;
  flex-wrap: wrap;
}
#mergeToolViewV2 .routing-summary .label {
  font-weight: 700; color: #0f172a;
  font-size: 0.78rem;
  text-transform: uppercase; letter-spacing: 0.04em;
}
#mergeToolViewV2 .routing-summary .group {
  display: inline-flex; align-items: center; gap: 7px;
}
#mergeToolViewV2 .routing-summary .group strong { color: #0f172a; font-weight: 700; }
#mergeToolViewV2 .routing-summary .group .chip {
  padding: 2px 8px; border-radius: 4px;
  font-size: 0.7rem; font-weight: 700; letter-spacing: 0.04em;
}
#mergeToolViewV2 .routing-summary .group .chip.import  { background: #dbeafe; color: #1e40af; }
#mergeToolViewV2 .routing-summary .group .chip.export  { background: #f3e8ff; color: #6b21a8; }
#mergeToolViewV2 .routing-summary .group .chip.unknown { background: #f1f5f9; color: #94a3b8; }
#mergeToolViewV2 .routing-summary .hint {
  margin-left: auto;
  font-size: 0.78rem; color: #94a3b8;
}

/* Will-fetch chip rendered in the table cells */
#mergeToolViewV2 .will-chip {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 3px 9px; border-radius: 5px;
  font-size: 0.7rem; font-weight: 700;
  letter-spacing: 0.04em;
  white-space: nowrap;
}
#mergeToolViewV2 .will-chip.import  { background: #dbeafe; color: #1e40af; }
#mergeToolViewV2 .will-chip.export  { background: #f3e8ff; color: #6b21a8; }
#mergeToolViewV2 .will-chip.unknown { background: #f1f5f9; color: #94a3b8; }

/* INV# letter highlight (shows the routing letter at pos 2) */
#mergeToolViewV2 .inv-letter {
  color: #ea580c; font-weight: 800;
}
```

- [ ] **Step 2: Verify the CSS loads**

```
cd desktop && npm start
```

In DevTools console:

```js
const probe = document.createElement('div');
probe.id = 'mergeToolViewV2'; document.body.appendChild(probe);
const c = document.createElement('span'); c.className = 'will-chip import';
probe.appendChild(c);
console.log(getComputedStyle(c).backgroundColor); // expected: "rgb(219, 234, 254)"
probe.remove();
```

If the color matches, CSS is loaded.

- [ ] **Step 3: Commit**

```bash
git add app/assets/css/styles.css
git commit -m "feat(merge/v2/m3): CSS for routing summary band + Will-fetch chip"
```

---

## Task 6: Wire routing helpers into Review-state renderers

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js` — `parseExcelFile`, `renderReviewSuccess`, `renderReviewWithIssues`, `rowMarkup`

- [ ] **Step 1: Update imports**

At the top of `merge-v2.js`, replace the `import` line with:

```js
import {
  escHtml, readAsArrayBuffer, findColumnKey, CSV_ALIASES,
  routingDecisionFor,
} from '../../shared/utils.js';
```

- [ ] **Step 2: Compute `routingType` + `expectedDoc` on each row in `parseExcelFile`**

Find the row-pushing block in `parseExcelFile` (around `rows.push({ rowNum: ... })`). Add the routing decision before the push:

```js
    const decision = routingDecisionFor({
      invoiceNumber: invoiceKey ? String(r[invoiceKey] || '').trim() : '',
      workOrderNumber: woKey ? String(r[woKey] || '').trim() : '',
    });

    rows.push({
      rowNum: i + 2,
      containerNumber: cn,
      invoiceNumber: invoiceKey ? String(r[invoiceKey] || '').trim() : '',
      workOrderNumber: woKey ? String(r[woKey] || '').trim() : '',
      customer: customerKey ? String(r[customerKey] || '').trim() : '',
      selected: false,
      status: 'ok',
      statusReason: '',
      // M3 routing
      routingType: decision.type,
      expectedDoc: decision.expectedDoc,
      fetchResult: null,
      manualPodFile: null,
      skipped: false,
    });
```

- [ ] **Step 3: Add a `routingSummaryBand()` helper**

Just above `renderReview()`, add:

```js
function routingSummaryBand() {
  const imports  = v2State.rows.filter(r => r.routingType === 'import').length;
  const exports_ = v2State.rows.filter(r => r.routingType === 'export').length;
  const unknown  = v2State.rows.filter(r => r.routingType === 'unknown').length;
  return `
    <div class="routing-summary">
      <span class="label">Will fetch</span>
      <span class="group">
        <span class="chip import">POD</span>
        <strong>${imports}</strong> import${imports !== 1 ? 's' : ''}
      </span>
      <span class="group">
        <span class="chip export">BOL/POL</span>
        <strong>${exports_}</strong> export${exports_ !== 1 ? 's' : ''}
      </span>
      ${unknown ? `<span class="group">
        <span class="chip unknown">?</span>
        <strong>${unknown}</strong> unknown
      </span>` : ''}
      <span class="hint">Decided by INV# letter (M/E) · falls back to WO# letter when prefix is non-standard</span>
    </div>
  `;
}
```

- [ ] **Step 4: Add `willChipFor(row)` and `highlightInvLetter(inv)` helpers**

Just above `rowMarkup`, add:

```js
function willChipFor(row) {
  if (row.routingType === 'import') return `<span class="will-chip import">POD</span>`;
  if (row.routingType === 'export') return `<span class="will-chip export">BOL/POL</span>`;
  return `<span class="will-chip unknown">?</span>`;
}

function highlightInvLetter(inv) {
  if (!inv || inv.length < 2) return escHtml(inv);
  const c = inv[1].toUpperCase();
  if (c === 'M' || c === 'E' || c === 'X') {
    return escHtml(inv[0]) + `<span class="inv-letter">${escHtml(inv[1])}</span>` + escHtml(inv.slice(2));
  }
  return escHtml(inv);
}
```

- [ ] **Step 5: Update `rowMarkup` to use `highlightInvLetter` and add the Will-fetch column**

In `rowMarkup`, replace the existing `invDisplay` line:

```js
  const invDisplay = row.invoiceNumber
    ? `<span class="mono mono-sub">${highlightInvLetter(row.invoiceNumber)}</span>`
    : `<span class="mono mono-sub" style="color:#dc2626;">— missing —</span>`;
```

And add a `willCell` constant (just above the `return` statement that builds the `<tr>`):

```js
  const willCell = `<td>${willChipFor(row)}</td>`;
```

Then update the `<tr>` template to insert `${willCell}` between the `customer` cell and the `validation` (badge) cell. The existing template's `<td>` order is: check, rowNum, container, inv, woCell?, customer, validation. New order: **check, rowNum, container, inv, woCell?, customer, will, validation**:

```js
  return `<tr class="${trClass}" data-row-num="${row.rowNum}">
    <td class="check-col"><input type="checkbox" class="row-check" ${checkAttr} onchange="window.v2ToggleRow(${row.rowNum}, this.checked)" /></td>
    <td style="color:#94a3b8; font-size:0.8rem;">${row.rowNum}</td>
    <td><span class="mono">${escHtml(row.containerNumber)}</span></td>
    <td>${invDisplay}</td>
    ${woCell}
    <td>${customerDisplay}</td>
    ${willCell}
    <td>${badge}${reasonLine}</td>
  </tr>`;
```

- [ ] **Step 6: Add the matching `<th>Will fetch</th>` to both review thead blocks**

In `renderReviewWithIssues()`, find the `<thead>` block and update it:

```js
        <thead>
          <tr>
            <th class="check-col"><input type="checkbox" id="v2MasterCheck" onclick="window.v2ToggleAll(this.checked)" /></th>
            <th>Row</th>
            <th>Container</th>
            <th>Invoice #</th>
            ${hasAnyWO() ? '<th>WO #</th>' : ''}
            <th>Customer</th>
            <th>Will fetch</th>
            <th>Validation</th>
          </tr>
        </thead>
```

Same change in `renderReviewSuccess()`'s expanded-table thead.

- [ ] **Step 7: Update `renderTbodyHTML`'s empty-state colspan**

In `renderTbodyHTML`, the colspan was 6 (or 7 with WO#). With the new Will-fetch column it's 7 (or 8 with WO#):

```js
function renderTbodyHTML() {
  const rows = getVisibleRows();
  if (rows.length === 0) {
    const cols = hasAnyWO() ? 8 : 7;
    return `<tr><td colspan="${cols}" style="padding:20px; text-align:center; color:#94a3b8;">No rows match.</td></tr>`;
  }
  return rows.map(rowMarkup).join('');
}
```

- [ ] **Step 8: Add the routing summary band above the chrome in both renderers**

In `renderReviewWithIssues()`, insert `${routingSummaryBand()}` immediately after `${topBarOnlyExcel()}` (and before the issues banner / tabs).

In `renderReviewSuccess()`, the success-card path doesn't currently have a chrome area — but it makes sense to surface the band even on the happy path. Insert `${routingSummaryBand()}` immediately after `${topBarOnlyExcel()}` (above the green success card).

- [ ] **Step 9: Verify**

```
cd desktop && npm start
```

Drop `docs/no sav.xlsx` (mixed import/export INV#s).

Expected on Review:
- Routing summary band reads e.g. `Will fetch · [POD] 100 imports · [BOL/POL] 10 exports · Decided by INV# letter…`
- Per-row Will fetch column shows blue `POD` for LM/HM/PM rows, purple `BOL/POL` for LE/PE/HE rows
- INV# cell shows the type letter (M or E) bolded in orange: `L`**`M`**`26050100F`

Click "Show all 110 rows" on the success card. Confirm the Will fetch column appears in the expanded table too.

Drop a row with a non-standard INV# (e.g., manually craft `docs/test-unknown.xlsx` with one row `WEIRD-1234`/`VA2605040007`/...). The Will fetch column should show gray `?` for that row, and the routing summary band should include `· [?] 1 unknown`.

- [ ] **Step 10: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge/v2/m3): routing summary band + Will-fetch column in Review state"
```

---

## Task 7: CSS — Fetching/Ready chrome (progress, action bar variants, queued tab)

**Files:**
- Modify: `app/assets/css/styles.css` (append)

- [ ] **Step 1: Append**

After Task 5's block, append:

```css
/* ── M3: Fetching state — progress line + queued/error rows ───────────────── */
#mergeToolViewV2 .progress-line {
  background: #fff; border: 1px solid #e2e8f0;
  border-radius: 10px; padding: 12px 18px;
  display: flex; align-items: center; gap: 14px;
  margin-bottom: 14px;
}
#mergeToolViewV2 .progress-line .now { color: #475569; font-size: 0.86rem; flex-shrink: 0; }
#mergeToolViewV2 .progress-line .now strong { color: #0f172a; }
#mergeToolViewV2 .progress-line .container-name {
  font-family: 'SF Mono', Consolas, monospace;
  color: #1e40af; font-weight: 600; font-size: 0.82rem;
}
#mergeToolViewV2 .progress-track {
  flex: 1; height: 8px; background: #f1f5f9; border-radius: 999px; overflow: hidden;
}
#mergeToolViewV2 .progress-fill {
  height: 100%; background: #ea580c; border-radius: 999px;
  transition: width 0.3s ease-out;
}
#mergeToolViewV2 .cancel-btn {
  background: #fff; border: 1px solid #e2e8f0;
  border-radius: 7px; padding: 7px 14px;
  font-size: 0.82rem; cursor: pointer; color: #475569; font-family: inherit;
}
#mergeToolViewV2 .cancel-btn:hover { border-color: #dc2626; color: #dc2626; }

/* Ready action bar */
#mergeToolViewV2 .ready-action-bar {
  display: flex; align-items: center; gap: 14px;
  background: white; border: 1px solid #e2e8f0; border-radius: 10px;
  padding: 12px 18px; margin-bottom: 14px;
}
#mergeToolViewV2 .ready-status { color: #475569; font-size: 0.86rem; flex: 1; }
#mergeToolViewV2 .ready-status strong { color: #0f172a; }
#mergeToolViewV2 .ready-action-right { display: flex; align-items: center; gap: 10px; }
#mergeToolViewV2 .ready-action-right .control-label {
  font-size: 0.7rem; font-weight: 700; color: #94a3b8;
  text-transform: uppercase; letter-spacing: 0.06em;
}
#mergeToolViewV2 .merge-btn {
  padding: 9px 18px; background: #ea580c; color: white;
  border: none; border-radius: 8px; font-weight: 700; font-size: 0.86rem;
  cursor: pointer; display: inline-flex; align-items: center; gap: 8px;
  font-family: inherit;
}
#mergeToolViewV2 .merge-btn:hover { background: #c2410c; }
#mergeToolViewV2 .merge-btn:disabled { background: #fed7aa; cursor: not-allowed; }
#mergeToolViewV2 .merge-btn .count-badge {
  background: rgba(255,255,255,0.22);
  padding: 2px 9px; border-radius: 999px;
  font-size: 0.74rem; font-weight: 700;
}
#mergeToolViewV2 .merge-btn.resume {
  background: #2563eb;
}
#mergeToolViewV2 .merge-btn.resume:hover { background: #1d4ed8; }
#mergeToolViewV2 .merge-btn .last-fetched-meta {
  display: block; font-size: 0.66rem; font-weight: 500; opacity: 0.85;
  margin-top: 2px; text-align: left;
}

/* Queued tab + queued rows */
#mergeToolViewV2 .tab.queued-tab .count { background: #f1f5f9; color: #94a3b8; }
#mergeToolViewV2 tbody tr.row-queued { opacity: 0.55; }
#mergeToolViewV2 tbody tr.row-active-error {
  background: #fff7ed !important;
  box-shadow: inset 3px 0 0 #ea580c;
}

/* Doc-result pills */
#mergeToolViewV2 .doc-row { display: flex; gap: 6px; align-items: center; }
#mergeToolViewV2 .doc-pill {
  display: inline-flex; align-items: center;
  padding: 3px 10px; border-radius: 5px;
  font-size: 0.7rem; font-weight: 700; letter-spacing: 0.04em;
}
#mergeToolViewV2 .doc-pill.ok       { background: #dcfce7; color: #15803d; }
#mergeToolViewV2 .doc-pill.fallback { background: #fef3c7; color: #92400e; }
#mergeToolViewV2 .doc-pill.miss     { background: #fee2e2; color: #b91c1c; }
#mergeToolViewV2 .doc-pill.queued   { background: #f1f5f9; color: #94a3b8; }

/* Status text */
#mergeToolViewV2 .status-text { font-size: 0.8rem; font-weight: 600; }
#mergeToolViewV2 .status-text.ready  { color: #16a34a; }
#mergeToolViewV2 .status-text.issue  { color: #d97706; }
#mergeToolViewV2 .status-text.queued { color: #94a3b8; }
#mergeToolViewV2 .status-text.skipped {
  color: #94a3b8; font-style: italic;
  background: #f1f5f9; padding: 2px 7px; border-radius: 4px;
}

/* Fix Error button (soft red palette per parent spec) */
#mergeToolViewV2 .fix-error-btn {
  background: #fee2e2; color: #b91c1c; border: 1px solid #fca5a5;
  padding: 5px 11px; border-radius: 6px;
  font-size: 0.78rem; font-weight: 600; cursor: pointer;
  display: inline-flex; align-items: center; gap: 5px; font-family: inherit;
}
#mergeToolViewV2 .fix-error-btn:hover { background: #fecaca; border-color: #f87171; color: #991b1b; }

/* Mass retry button on the Errors tab toolbar */
#mergeToolViewV2 .toolbar .mass-retry-btn {
  background: #fff; border: 1px solid #fca5a5; color: #b91c1c;
  padding: 6px 12px; border-radius: 6px;
  font-size: 0.8rem; font-weight: 600; cursor: pointer; font-family: inherit;
  margin-left: 8px;
}
#mergeToolViewV2 .toolbar .mass-retry-btn:hover {
  background: #fee2e2; border-color: #f87171;
}
#mergeToolViewV2 .toolbar .mass-retry-btn:disabled {
  opacity: 0.5; cursor: not-allowed;
}

/* Row-update flash animation (live SSE) */
@keyframes v2-row-flash {
  0% { background: #fef3c7; }
  100% { background: transparent; }
}
#mergeToolViewV2 tr.flash-update { animation: v2-row-flash 1s ease-out; }

/* Doc-pill swap transition (fade in/out) */
#mergeToolViewV2 .doc-pill {
  transition: background 0.25s ease-out, color 0.25s ease-out;
}
```

- [ ] **Step 2: Verify load**

Same probe as Task 5; check `getComputedStyle` on a `.merge-btn.resume` element.

- [ ] **Step 3: Commit**

```bash
git add app/assets/css/styles.css
git commit -m "feat(merge/v2/m3): CSS for Fetching/Ready chrome + animation keyframes"
```

---

## Task 8: `renderFetching()` — real markup, no live data yet

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js`

- [ ] **Step 1: Add a `topBarWithDrop()` helper**

Above `renderReview()`, add:

```js
function topBarWithDrop() {
  const fname = v2State.excelFile ? escHtml(v2State.excelFile.name) : '';
  const total = v2State.rows.length;
  return `
    <div class="top-bar">
      <div class="file-summary">
        <div class="icon-box xlsx">XLS</div>
        <div class="text">
          <div class="name">${fname}</div>
          <div class="meta">${total} unique container${total !== 1 ? 's' : ''}</div>
        </div>
      </div>
      <div class="pdf-drop-card" title="Bulk PDF drop wires up in M4">
        <div class="icon-box pdf">PDF</div>
        <div class="text">
          <div class="label-line">PDFs <span class="count-pill">0</span></div>
          <div class="help">Drop bulk PDFs here for missing or late docs (M4)</div>
        </div>
      </div>
    </div>
  `;
}
```

- [ ] **Step 2: Add row helpers for Fetching/Ready states**

Below `rowMarkup()`, add:

```js
function docPills(row) {
  const fr = row.fetchResult;
  if (!fr) {
    // Queued / not-yet-fetched
    const expected = row.expectedDoc === 'BOL/POL' ? 'BOL' : (row.expectedDoc === '?' ? '?' : 'POD');
    return `<div class="doc-row">
      <span class="doc-pill queued">INV</span>
      <span class="doc-pill queued">${expected}</span>
    </div>`;
  }
  return `<div class="doc-row">
    <span class="doc-pill ${fr.invPill}">INV</span>
    <span class="doc-pill ${fr.podPill}">${escHtml(fr.podLabel)}</span>
  </div>`;
}

function fetchStatusCell(row) {
  if (row.skipped) return `<span class="status-text skipped">Skipped</span>`;
  if (!row.fetchResult) return `<span class="status-text queued">Queued</span>`;
  const fr = row.fetchResult;
  if (fr.podPill === 'miss') return `<span class="status-text issue">${escHtml(fr.statusText || 'Needs PDF')}</span>`;
  if (fr.podPill === 'fallback') return `<span class="status-text ready">${escHtml(fr.statusText || 'Fetched (fallback)')}</span>`;
  return `<span class="status-text ready">${escHtml(fr.statusText || 'Fetched')}</span>`;
}

function fetchActionCell(rowIdx, row) {
  if (row.fetchResult && row.fetchResult.podPill === 'miss' && !row.skipped) {
    return `<td><button class="fix-error-btn" onclick="event.stopPropagation(); window.v2OpenSidebar(${rowIdx})">⚠ Fix Error</button></td>`;
  }
  return `<td></td>`;
}

function fetchRowMarkup(rowIdx, row, opts) {
  const isError = row.fetchResult?.podPill === 'miss' && !row.skipped;
  const isQueued = !row.fetchResult && !row.skipped;
  const isActive = isError && opts.activeErrorIdx === rowIdx;

  const trClass = [
    isError ? 'row-issue' : '',
    isActive ? 'row-active-error' : '',
    isQueued ? 'row-queued' : '',
  ].filter(Boolean).join(' ');

  const checkable = !!row.fetchResult && row.fetchResult.podPill !== 'miss' && !row.skipped;
  const checkAttrs = `${row.selected && checkable ? 'checked' : ''} ${!checkable ? 'disabled' : ''}`;
  const checkTitle = isError ? 'Fix the error before this can be merged'
                   : isQueued ? 'Not yet fetched'
                   : row.skipped ? 'Skipped — re-click Fix Error to undo'
                   : '';

  const trAttrs = isError
    ? `onclick="window.v2OpenSidebar(${rowIdx})" style="cursor:pointer;"`
    : '';

  const checkColMaybe = opts.includeCheck
    ? `<td class="check-col" onclick="event.stopPropagation()">
         <input type="checkbox" class="row-check" ${checkAttrs} title="${checkTitle}"
                onchange="window.v2ToggleFetchRow(${rowIdx}, this.checked)" />
       </td>`
    : '';

  return `<tr class="${trClass}" ${trAttrs} data-row-idx="${rowIdx}">
    ${checkColMaybe}
    <td><span class="mono">${escHtml(row.containerNumber)}</span></td>
    <td><span class="mono mono-sub">${highlightInvLetter(row.invoiceNumber)}</span></td>
    <td>${row.customer ? escHtml(row.customer) : '<span style="color:#cbd5e1;">—</span>'}</td>
    <td>${willChipFor(row)}</td>
    <td>${docPills(row)}</td>
    <td>${fetchStatusCell(row)}</td>
    ${fetchActionCell(rowIdx, row)}
  </tr>`;
}
```

- [ ] **Step 3: Replace the `renderFetching()` stub**

Replace the existing one-line stub (around line 600):

```js
function renderFetching() {
  const total = v2State.rows.filter(r => r.selected).length || v2State.rows.length;
  const done = v2State.fetchProgress;
  const cur = v2State.fetchCurrentContainer || '—';
  const percent = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;

  // Tabs split by current state
  const fetchedCount = v2State.rows.filter(r => r.fetchResult && r.fetchResult.podPill !== 'miss').length;
  const failedCount  = v2State.rows.filter(r => r.fetchResult?.podPill === 'miss').length;
  const allCount     = v2State.rows.length;

  // Body — show all rows (fetched + queued + failed)
  const bodyRows = v2State.rows.map((row, i) => fetchRowMarkup(i, row, {
    includeCheck: false,
    activeErrorIdx: null,
  })).join('');

  return `
    ${topBarWithDrop()}
    ${routingSummaryBand()}
    <div class="progress-line">
      <div class="now">
        <strong>Fetching ${done} / ${total}</strong>
        &nbsp; <span class="container-name">${escHtml(cur)}</span>
      </div>
      <div class="progress-track"><div class="progress-fill" style="width:${percent}%;"></div></div>
      <button class="cancel-btn" onclick="window.v2CancelFetch()">Cancel</button>
    </div>
    <div class="tabs-row">
      <div class="tabs">
        <button class="tab active">All <span class="count">${allCount}</span></button>
        <button class="tab">Fetched <span class="count">${fetchedCount}</span></button>
        <button class="tab has-issues">Failed <span class="count">${failedCount}</span></button>
      </div>
    </div>
    <div class="toolbar">
      <input type="text" class="search" placeholder="Search containers…" />
      <span class="filter-meta">${done} / ${total} fetched · ${failedCount} failed</span>
    </div>
    <div class="table-wrap">
      <table class="merge-table">
        <thead><tr>
          <th>Container</th><th>Invoice #</th><th>Customer</th>
          <th>Will fetch</th><th>Documents</th><th>Status</th><th></th>
        </tr></thead>
        <tbody id="v2FetchTbody">${bodyRows}</tbody>
      </table>
    </div>
  `;
}
```

- [ ] **Step 4: Stub the handlers used by the markup (real wiring in next tasks)**

Below the existing window-export block, add stubs:

```js
window.v2CancelFetch    = () => { console.log('v2CancelFetch — wired in Task 13'); };
window.v2OpenSidebar    = (idx) => { console.log('v2OpenSidebar', idx, '— wired in Task 12'); };
window.v2ToggleFetchRow = (idx, checked) => { console.log('v2ToggleFetchRow', idx, checked, '— wired in Task 11'); };
```

- [ ] **Step 5: Verify**

```
cd desktop && npm start
```

Drop a small test file (e.g., `docs/NGL INVOICE 05.05.2026 (1).xlsx`). On Review, click `Continue to Merge →` (still labeled "Fetch N Documents" until Task 9 — that's fine for now). The state transitions to Fetching and renders:
- Top bar with file summary
- Routing summary band
- Progress line: `Fetching 0 / N` + 0% bar + Cancel button
- Tabs (counts all 0)
- Toolbar
- Table with all rows showing as "Queued" (gray pills, gray status text)

The Cancel button logs to console but doesn't do anything yet. That's expected.

- [ ] **Step 6: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge/v2/m3): renderFetching with real markup; row helpers + handler stubs"
```

---

## Task 9: Wire fetch launch + rename Fetch button to "Continue to Merge →"

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js`

- [ ] **Step 1: Replace `v2ClickFetch` with the real launch logic**

Find `v2ClickFetch` (currently `() => setStateV2('fetching');`) and replace with:

```js
async function v2ClickFetch() {
  // Build the fetch payload — dedup selected rows by container.
  const selected = v2State.rows.filter(r => r.selected);
  const seen = new Set();
  const containers = [];
  for (const row of selected) {
    const key = row.containerNumber.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    containers.push({
      containerNumber: row.containerNumber,
      invoiceNumber: row.invoiceNumber,
    });
  }

  if (containers.length === 0) {
    alert('No rows selected to fetch.');
    return;
  }

  // Reset progress state
  v2State.fetchProgress = 0;
  v2State.fetchTotal = containers.length;
  v2State.fetchCurrentContainer = '';
  v2State.lastFetchedContainer = '';
  // Clear any stale fetchResult on rows we're about to fetch
  for (const row of v2State.rows) {
    if (row.selected) { row.fetchResult = null; row.skipped = false; }
  }

  setStateV2('fetching');

  try {
    const res = await fetch('http://localhost:8787/jobs/fetch-missing', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ containers, doc_types: ['invoice', 'pod'] }),
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Agent rejected fetch: ${res.status} ${text}`);
    }
    const { jobId } = await res.json();
    v2State.jobId = jobId;
    openSseStream(jobId);
  } catch (err) {
    alert(`Couldn't start fetch: ${err.message}\n\nIs the agent running? Check Settings.`);
    setStateV2('review');
  }
}
window.v2ClickFetch = v2ClickFetch;
```

- [ ] **Step 2: Add `openSseStream(jobId)` placeholder (real handlers in Task 11)**

Just below `v2ClickFetch`:

```js
function openSseStream(jobId) {
  // Placeholder — Task 11 wires the real event handlers
  // (split this way so Task 9's PR is independently testable).
  console.log('openSseStream', jobId, '— handlers wired in Task 11');
}
```

- [ ] **Step 3: Rename the Fetch button to "Continue to Merge →" — wait, that's not right**

Actually the action button on the **Review** state should remain "Fetch N Documents" (it kicks off the fetch). The "Continue to Merge →" button is on the **Ready** state (Task 10). Don't rename anything in this task; the M2 button text is correct as-is.

- [ ] **Step 4: Verify**

```
cd desktop && npm start
```

Make sure the agent server is running (check the Agent online indicator in the header). Drop `docs/NGL INVOICE 05.05.2026 (1).xlsx`. Click `Fetch N Documents`. The state transitions to Fetching. Open DevTools Network tab — you should see one `POST /jobs/fetch-missing` request return 200 with `{ jobId: "..." }`. Console should log `openSseStream <jobId> — handlers wired in Task 11`.

The progress bar stays at 0% (no SSE wiring yet). That's expected.

If the agent isn't running, an alert pops up explaining and you stay on Review.

- [ ] **Step 5: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge/v2/m3): wire Fetch button — dedup, POST /jobs/fetch-missing, store jobId"
```

---

## Task 10: SSE event handlers + live row patches with animation

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js`

- [ ] **Step 1: Replace `openSseStream` with the real handler**

Replace the placeholder `openSseStream` from Task 9:

```js
function openSseStream(jobId) {
  // Use plain EventSource — the existing agentBridge wraps this but pulls in
  // auth-token logic we don't need here (agent runs locally).
  const url = `http://localhost:8787/jobs/${encodeURIComponent(jobId)}/stream`;
  const es = new EventSource(url);
  v2State.eventSource = es;

  es.onmessage = (e) => {
    if (v2State.subMode !== 'fetching') return;   // ignore events after cancel/teardown
    let evt;
    try { evt = JSON.parse(e.data); } catch { return; }
    handleSseEvent(evt);
  };
  es.onerror = () => {
    // EventSource auto-retries; if we want hard-fail on max retries, do it here.
    console.warn('[v2 SSE] connection error — EventSource auto-retrying');
  };
}

function handleSseEvent(evt) {
  switch (evt.type) {
    case 'job_started':
      v2State.fetchTotal = evt.total;
      // Re-render to update the total in the progress label
      setStateV2('fetching');
      break;

    case 'container_start':
      v2State.fetchCurrentContainer = evt.containerNumber || '';
      updateProgressLine();
      break;

    case 'container_complete':
      v2State.fetchProgress += 1;
      v2State.lastFetchedContainer = evt.containerNumber || v2State.lastFetchedContainer;
      updateProgressLine();
      // The actual row update came in via prior pod_found / pod_missing events
      break;

    case 'pod_found': {
      const tmsType = evt.tms_doc_type || null;
      const fromTms = !!tmsType;
      patchRow(evt.containerNumber, {
        invPill: 'ok',
        podPill: fromTms ? 'fallback' : 'ok',
        podLabel: tmsType || 'POD',
        statusText: fromTms ? `Fetched (${tmsType})` : 'Fetched',
        chainAttempted: evt.chain_attempted || [],
        message: '',
      });
      break;
    }

    case 'pod_missing':
      patchRow(evt.containerNumber, {
        invPill: 'ok',
        podPill: 'miss',
        podLabel: '—',
        statusText: 'Needs PDF',
        chainAttempted: evt.chain_attempted || [],
        message: evt.message || 'No POD/BOL/POL/IT/ITE found',
      });
      break;

    case 'job_completed':
      finalizeFetch({ cancelled: false });
      break;

    case 'job_cancelled':
    case 'job_paused':
      finalizeFetch({ cancelled: true });
      break;
  }
}

function patchRow(container, fetchResult) {
  // Same-container dedup: apply the result to ALL invoice rows sharing this container
  const containerLower = (container || '').toLowerCase();
  for (let i = 0; i < v2State.rows.length; i++) {
    const row = v2State.rows[i];
    if (row.containerNumber.toLowerCase() !== containerLower) continue;
    row.fetchResult = { ...fetchResult };
    rerenderFetchRow(i);
  }
}

function rerenderFetchRow(rowIdx) {
  const tbody = document.getElementById('v2FetchTbody') || document.getElementById('v2ReadyTbody');
  if (!tbody) return;
  const tr = tbody.querySelector(`tr[data-row-idx="${rowIdx}"]`);
  if (!tr) return;
  const fresh = document.createElement('tbody');
  fresh.innerHTML = fetchRowMarkup(rowIdx, v2State.rows[rowIdx], {
    includeCheck: !!tbody.id.startsWith('v2Ready'),
    activeErrorIdx: v2State.openSidebarRow,
  });
  const newTr = fresh.firstElementChild;
  newTr.classList.add('flash-update');
  tr.replaceWith(newTr);
}

function updateProgressLine() {
  const now = document.querySelector('#mergeToolViewV2 .progress-line .now');
  const fill = document.querySelector('#mergeToolViewV2 .progress-fill');
  if (!now || !fill) return;
  const total = v2State.fetchTotal;
  const done = v2State.fetchProgress;
  const cur = v2State.fetchCurrentContainer || '—';
  now.innerHTML = `<strong>Fetching ${done} / ${total}</strong> &nbsp; <span class="container-name">${escHtml(cur)}</span>`;
  fill.style.width = total > 0 ? `${Math.min(100, Math.round((done / total) * 100))}%` : '0%';
}

function finalizeFetch({ cancelled }) {
  if (v2State.eventSource) {
    v2State.eventSource.close();
    v2State.eventSource = null;
  }
  // Rows that never got a fetchResult stay null → they render as queued in Ready
  // (only relevant on cancel; on completion every row should have a result)
  setStateV2('ready');
}
```

- [ ] **Step 2: Wire the Cancel button**

Replace the placeholder `window.v2CancelFetch`:

```js
async function v2CancelFetch() {
  if (!v2State.jobId) return;
  try {
    await fetch(`http://localhost:8787/jobs/${encodeURIComponent(v2State.jobId)}/cancel`, {
      method: 'POST',
    });
  } catch (err) {
    console.warn('Cancel POST failed:', err);
  }
  // Don't transition here — wait for the SSE 'job_cancelled' / 'job_paused' event.
  // If the SSE stream dies before the event arrives, manually finalize.
  setTimeout(() => {
    if (v2State.subMode === 'fetching') finalizeFetch({ cancelled: true });
  }, 2000);
}
window.v2CancelFetch = v2CancelFetch;
```

- [ ] **Step 3: Verify**

```
cd desktop && npm start
```

Make sure agent + QBO + TMS are connected. Drop `docs/NGL INVOICE 05.05.2026 (1).xlsx`. Click Fetch.

Expected:
- Progress label updates as containers complete: `Fetching 1 / 14 · KKFU7654819` etc.
- Progress bar fills smoothly
- Each row's pills flip from gray-queued to green-ok (or amber-fallback for TMS hits) as `pod_found` events arrive
- A 1-second yellow flash highlights each row when it changes
- After all containers complete, state transitions to Ready (M3 stub renderReady — Task 11 replaces with real markup)

Click Cancel mid-fetch → fetch stops within ~2 seconds, state transitions to Ready.

- [ ] **Step 4: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge/v2/m3): SSE event handlers — live row patches + animation + cancel"
```

---

## Task 11: `renderReady()` — full + partial variants with selection

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js`

- [ ] **Step 1: Replace the `renderReady()` stub**

Replace the existing one-line stub:

```js
function renderReady() {
  const all = v2State.rows;
  const queued = all.filter(r => !r.fetchResult && !r.skipped);
  const errors = all.filter(r => r.fetchResult?.podPill === 'miss' && !r.skipped);
  const ready  = all.filter(r => r.fetchResult && r.fetchResult.podPill !== 'miss' && !r.skipped);

  // Active tab — Errors-default-when-errors rule
  if (errors.length > 0 && v2State.activeTab !== 'errors' && v2State.activeTab !== 'queued') {
    v2State.activeTab = 'errors';
  } else if (errors.length === 0 && queued.length === 0) {
    if (v2State.activeTab === 'errors' || v2State.activeTab === 'queued') {
      v2State.activeTab = 'all';
    }
  }

  // Filter rows based on active tab
  let visibleRows;
  if (v2State.activeTab === 'errors') visibleRows = errors;
  else if (v2State.activeTab === 'queued') visibleRows = queued;
  else visibleRows = all;

  // Apply search
  if (v2State.searchQuery) {
    const q = v2State.searchQuery.toLowerCase();
    visibleRows = visibleRows.filter(r => r.containerNumber.toLowerCase().includes(q));
  }

  // Selection count for the action button
  const selected = ready.filter(r => r.selected).length;

  const isPartial = queued.length > 0;

  // Action bar
  const actionBar = isPartial ? `
    <div class="ready-action-bar">
      <div class="ready-status">
        <span style="color:#16a34a;">●</span> <strong>${ready.length} of ${ready.length + errors.length}</strong> fetched & ready
        <span style="color:#cbd5e1; margin: 0 6px;">·</span>
        <span style="color:#d97706;">●</span> <strong>${errors.length}</strong> need fixing
        <span style="color:#cbd5e1; margin: 0 6px;">·</span>
        <span style="color:#94a3b8;">●</span> <strong>${queued.length}</strong> queued
      </div>
      <div class="ready-action-right">
        <button class="merge-btn resume" onclick="window.v2ResumeFetch()">
          ↻ Resume fetch
          <span class="count-badge">${queued.length} queued</span>
          <span class="last-fetched-meta">Last fetched: ${escHtml(v2State.lastFetchedContainer || '—')}</span>
        </button>
      </div>
    </div>
  ` : `
    <div class="ready-action-bar">
      <div class="ready-status">
        <span style="color:#16a34a;">●</span> <strong>${ready.length} of ${all.length}</strong> ready to merge
        ${errors.length ? `<span style="color:#cbd5e1; margin: 0 6px;">·</span>
          <span style="color:#d97706;">●</span> <strong>${errors.length}</strong> need fixing
          <span style="color:#94a3b8;">(click any error row)</span>` : ''}
      </div>
      <div class="ready-action-right">
        <button class="merge-btn" id="v2BtnContinueMerge" ${selected === 0 ? 'disabled' : ''}
                onclick="window.v2ClickContinueMerge()">
          Continue to Merge
          <span class="count-badge"><span class="sel-count">${selected}</span> selected</span>
        </button>
      </div>
    </div>
  `;

  // Tabs
  const tabsHtml = `
    <div class="tabs-row">
      <div class="tabs">
        <button class="tab ${v2State.activeTab === 'all' ? 'active' : ''}" onclick="window.v2HandleReadyTab('all')">
          All <span class="count">${all.length}</span>
        </button>
        <button class="tab has-issues ${v2State.activeTab === 'errors' ? 'active' : ''}" onclick="window.v2HandleReadyTab('errors')">
          Errors <span class="count">${errors.length}</span>
        </button>
        ${isPartial ? `<button class="tab queued-tab ${v2State.activeTab === 'queued' ? 'active' : ''}" onclick="window.v2HandleReadyTab('queued')">
          Queued <span class="count">${queued.length}</span>
        </button>` : ''}
      </div>
    </div>
  `;

  // Toolbar — adds the mass-retry button when on the Errors tab with errors present
  const massRetry = (v2State.activeTab === 'errors' && errors.length > 0)
    ? `<button class="mass-retry-btn" onclick="window.v2RetryAllErrors()">↻ Retry all errors</button>`
    : '';

  const toolbarHtml = `
    <div class="toolbar">
      <input type="text" class="search" placeholder="Search containers…"
             value="${escHtml(v2State.searchQuery)}"
             oninput="window.v2HandleReadySearch(this.value)" />
      ${massRetry}
      <span class="filter-meta">${visibleRows.length} of ${all.length}${errors.length ? ` · ${errors.length} need fixing` : ''}${queued.length ? ` · ${queued.length} queued` : ''}</span>
    </div>
  `;

  // Table
  const bodyRows = visibleRows.map(row => {
    const idx = v2State.rows.indexOf(row);
    return fetchRowMarkup(idx, row, {
      includeCheck: true,
      activeErrorIdx: v2State.openSidebarRow,
    });
  }).join('');

  const tableHtml = `
    <div class="table-wrap">
      <table class="merge-table">
        <thead><tr>
          <th class="check-col"><input type="checkbox" id="v2ReadyMaster" onclick="window.v2ToggleAllReady(this.checked)" /></th>
          <th>Container</th><th>Invoice #</th><th>Customer</th>
          <th>Will fetch</th><th>Documents</th><th>Status</th><th></th>
        </tr></thead>
        <tbody id="v2ReadyTbody">${bodyRows}</tbody>
      </table>
    </div>
  `;

  // Sidebar (auto-opens on first error if not yet set)
  if (v2State.openSidebarRow === null && errors.length > 0) {
    v2State.openSidebarRow = v2State.rows.indexOf(errors[0]);
  }
  const sidebarHtml = (v2State.openSidebarRow !== null && v2State.openSidebarRow >= 0)
    ? renderSidebar(v2State.openSidebarRow)
    : '';

  return `
    ${topBarWithDrop()}
    ${routingSummaryBand()}
    ${actionBar}
    ${tabsHtml}
    ${toolbarHtml}
    ${tableHtml}
    ${sidebarHtml}
  `;
}
```

- [ ] **Step 2: Add Ready-state interaction handlers (stubs for sidebar handlers — wired in Task 12)**

Below the existing `window.v2ClickFetch = v2ClickFetch;`:

```js
function v2HandleReadyTab(tab) {
  v2State.activeTab = tab;
  setStateV2('ready');
}
function v2HandleReadySearch(value) {
  v2State.searchQuery = value;
  setStateV2('ready');
}
function v2ToggleAllReady(checked) {
  for (const row of v2State.rows) {
    if (row.fetchResult && row.fetchResult.podPill !== 'miss' && !row.skipped) {
      row.selected = !!checked;
    }
  }
  setStateV2('ready');
}
function v2ToggleFetchRow(rowIdx, checked) {
  const row = v2State.rows[rowIdx];
  if (!row) return;
  row.selected = !!checked;
  // Update count badge live (don't full re-render — keep search focus)
  const cnt = document.querySelector('#v2BtnContinueMerge .sel-count');
  if (cnt) {
    cnt.textContent = v2State.rows.filter(r => r.selected && r.fetchResult && r.fetchResult.podPill !== 'miss').length;
  }
}
function v2ClickContinueMerge() {
  setStateV2('merging');   // M4 stub
}
function v2RetryAllErrors() {
  console.log('v2RetryAllErrors — wired in Task 14');
}
function v2ResumeFetch() {
  console.log('v2ResumeFetch — wired in Task 15');
}

window.v2HandleReadyTab     = v2HandleReadyTab;
window.v2HandleReadySearch  = v2HandleReadySearch;
window.v2ToggleAllReady     = v2ToggleAllReady;
window.v2ToggleFetchRow     = v2ToggleFetchRow;
window.v2ClickContinueMerge = v2ClickContinueMerge;
window.v2RetryAllErrors     = v2RetryAllErrors;
window.v2ResumeFetch        = v2ResumeFetch;
```

- [ ] **Step 3: Add a stub `renderSidebar()` so renderReady doesn't crash**

Just below `renderReady()`, add:

```js
function renderSidebar(rowIdx) {
  // Real implementation in Task 12
  return `<div class="detail-sidebar open" style="display:flex;">
    <div style="padding:20px; color:#94a3b8;">Sidebar markup — wired in Task 12 (rowIdx: ${rowIdx})</div>
  </div>
  <div class="sidebar-backdrop open" onclick="window.v2CloseSidebar()"></div>`;
}
window.v2CloseSidebar = () => { v2State.openSidebarRow = -1; setStateV2('ready'); };
window.v2OpenSidebar  = (idx) => { v2State.openSidebarRow = idx; setStateV2('ready'); };
```

- [ ] **Step 4: Verify**

```
cd desktop && npm start
```

Drop `docs/NGL INVOICE 05.05.2026 (1).xlsx`. Click Fetch. Wait for fetch to complete.

Expected on Ready (full variant):
- Routing summary band visible
- Action bar reads `● N of N ready to merge` (no errors expected on this clean file)
- Action button: orange `Continue to Merge · [N selected]`
- Tabs: All [N] active, Errors [0]
- Table shows all rows with green pills
- No sidebar (no errors to auto-open on)

If any rows had errors, sidebar would auto-open (with the Task 11 stub showing).

Test the partial-completion variant: drop the file again, click Fetch, hit Cancel after a few rows complete. State transitions to Ready with:
- `Resume fetch · N queued` blue button
- `Last fetched: <container>` meta line
- `Queued [N]` tab visible
- Queued rows render dimmed with gray pills

- [ ] **Step 5: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge/v2/m3): renderReady — full + partial variants + Continue to Merge button"
```

---

## Task 12: Real sidebar — header, body, footer, routing trace

**Files:**
- Modify: `app/assets/css/styles.css` (append sidebar block)
- Modify: `app/assets/js/tools/merge/merge-v2.js`

- [ ] **Step 1: Append sidebar CSS**

After Task 7's block, append:

```css
/* ── M3: error-resolution sidebar ─────────────────────────────────────────── */
#mergeToolViewV2 .sidebar-backdrop {
  position: fixed; inset: 0;
  background: rgba(15,23,42,0.35);
  z-index: 200;
  display: none;
}
#mergeToolViewV2 .sidebar-backdrop.open { display: block; }

#mergeToolViewV2 .detail-sidebar {
  position: fixed;
  top: 0; right: 0; bottom: 0;
  width: 520px; max-width: 90vw;
  background: #fff;
  border-left: 1px solid #e2e8f0;
  box-shadow: -8px 0 24px rgba(0,0,0,0.08);
  z-index: 201;
  display: none;
  flex-direction: column;
}
#mergeToolViewV2 .detail-sidebar.open { display: flex; }
#mergeToolViewV2 .detail-sidebar.resolved .ds-header {
  background: #f0fdf4; border-bottom-color: #bbf7d0;
}
#mergeToolViewV2 .detail-sidebar.resolved .ds-icon {
  background: #dcfce7; color: #16a34a;
}

#mergeToolViewV2 .ds-header {
  display: flex; align-items: center; gap: 12px;
  padding: 16px 20px;
  border-bottom: 1px solid #e2e8f0;
  background: #f8fafc;
  flex-shrink: 0;
}
#mergeToolViewV2 .ds-icon {
  width: 36px; height: 36px; border-radius: 8px;
  background: #fef2f2; color: #dc2626;
  display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: 1.1rem;
  flex-shrink: 0;
}
#mergeToolViewV2 .ds-title { font-size: 1rem; font-weight: 700; color: #0f172a; }
#mergeToolViewV2 .ds-subtitle {
  font-size: 0.8rem; color: #64748b; margin-top: 2px;
  font-family: 'SF Mono', Consolas, monospace;
}
#mergeToolViewV2 .ds-close {
  margin-left: auto;
  background: none; border: none; cursor: pointer;
  color: #94a3b8; padding: 6px; border-radius: 6px;
  font-size: 1.4rem; line-height: 1; font-family: inherit;
}
#mergeToolViewV2 .ds-close:hover { color: #1e293b; background: #fff; }

#mergeToolViewV2 .ds-body { flex: 1; overflow-y: auto; padding: 20px; }
#mergeToolViewV2 .ds-section { margin-bottom: 22px; }
#mergeToolViewV2 .ds-section:last-child { margin-bottom: 0; }
#mergeToolViewV2 .ds-section-label {
  font-size: 0.72rem; font-weight: 700; color: #64748b;
  text-transform: uppercase; letter-spacing: 0.05em;
  margin-bottom: 8px;
}

#mergeToolViewV2 .happened-block {
  background: #fef2f2; border: 1px solid #fca5a5;
  border-radius: 8px; padding: 12px 14px;
}
#mergeToolViewV2 .happened-block .title {
  font-size: 0.86rem; font-weight: 600; color: #b91c1c; margin-bottom: 4px;
}
#mergeToolViewV2 .happened-block .body {
  font-size: 0.8rem; color: #7f1d1d; line-height: 1.5;
}
#mergeToolViewV2 .happened-block code {
  font-family: 'SF Mono', Consolas, monospace;
  background: #fee2e2; padding: 1px 5px; border-radius: 3px; font-size: 0.78rem;
}
#mergeToolViewV2 .resolved-block {
  background: #f0fdf4; border: 1px solid #bbf7d0;
  border-radius: 8px; padding: 12px 14px;
}
#mergeToolViewV2 .resolved-block .title {
  font-size: 0.86rem; font-weight: 600; color: #15803d; margin-bottom: 4px;
}
#mergeToolViewV2 .resolved-block .body {
  font-size: 0.8rem; color: #14532d; line-height: 1.5;
}

#mergeToolViewV2 .routing-trace {
  background: #f8fafc; border: 1px solid #e2e8f0;
  border-radius: 8px; padding: 12px 14px; font-size: 0.82rem;
}
#mergeToolViewV2 .routing-trace .step {
  display: flex; align-items: flex-start; gap: 8px; margin-bottom: 6px;
}
#mergeToolViewV2 .routing-trace .step:last-child { margin-bottom: 0; }
#mergeToolViewV2 .routing-trace .step .marker { flex-shrink: 0; font-weight: 700; }
#mergeToolViewV2 .routing-trace .step.success .marker  { color: #16a34a; }
#mergeToolViewV2 .routing-trace .step.fallback .marker { color: #d97706; }
#mergeToolViewV2 .routing-trace .step.fail .marker     { color: #dc2626; }
#mergeToolViewV2 .routing-trace .step.note .marker     { color: #94a3b8; }
#mergeToolViewV2 .routing-trace .step .text { color: #475569; }
#mergeToolViewV2 .routing-trace code {
  font-family: 'SF Mono', Consolas, monospace;
  background: #f1f5f9; padding: 1px 5px; border-radius: 3px;
  font-size: 0.78rem; color: #1e293b;
}

#mergeToolViewV2 .retry-api-btn {
  width: 100%;
  padding: 10px 16px;
  background: #ea580c; color: white;
  border: none; border-radius: 8px;
  font-size: 0.88rem; font-weight: 700;
  cursor: pointer; font-family: inherit;
  display: flex; align-items: center; justify-content: center; gap: 8px;
}
#mergeToolViewV2 .retry-api-btn:hover { background: #c2410c; }
#mergeToolViewV2 .retry-api-btn:disabled { background: #fed7aa; cursor: not-allowed; }
#mergeToolViewV2 .resolve-divider {
  text-align: center; margin: 14px 0;
  font-size: 0.74rem; color: #94a3b8;
  text-transform: uppercase; letter-spacing: 0.06em;
  position: relative;
}
#mergeToolViewV2 .resolve-divider::before,
#mergeToolViewV2 .resolve-divider::after {
  content: ''; position: absolute; top: 50%;
  width: calc(50% - 70px); height: 1px; background: #e2e8f0;
}
#mergeToolViewV2 .resolve-divider::before { left: 0; }
#mergeToolViewV2 .resolve-divider::after  { right: 0; }
#mergeToolViewV2 .resolve-divider span { background: #fff; padding: 0 8px; position: relative; }

#mergeToolViewV2 .ds-upload {
  border: 2px dashed #fca5a5; border-radius: 10px;
  background: #fef2f2; padding: 22px 18px;
  text-align: center; cursor: pointer;
}
#mergeToolViewV2 .ds-upload:hover { border-color: #dc2626; background: #fee2e2; }
#mergeToolViewV2 .ds-upload .icon { font-size: 2rem; color: #dc2626; }
#mergeToolViewV2 .ds-upload .title { font-size: 0.9rem; font-weight: 600; color: #b91c1c; margin-top: 4px; }
#mergeToolViewV2 .ds-upload .help  { font-size: 0.78rem; color: #b91c1c; margin-top: 4px; opacity: 0.85; }
#mergeToolViewV2 .ds-upload input[type="file"] { display: none; }

#mergeToolViewV2 .ds-attached {
  background: #f0fdf4; border: 1px solid #bbf7d0;
  border-radius: 8px; padding: 12px 14px;
  display: flex; align-items: center; gap: 10px;
}
#mergeToolViewV2 .ds-attached .name {
  font-family: 'SF Mono', Consolas, monospace;
  font-size: 0.84rem; font-weight: 600; color: #15803d; flex: 1;
}
#mergeToolViewV2 .ds-attached .size { font-size: 0.78rem; color: #16a34a; }
#mergeToolViewV2 .ds-attached .replace {
  background: none; border: none; color: #15803d;
  font-size: 0.78rem; cursor: pointer; text-decoration: underline; font-family: inherit;
}

#mergeToolViewV2 .ds-footer {
  display: flex; align-items: center; gap: 10px;
  padding: 14px 20px;
  border-top: 1px solid #e2e8f0;
  background: #f8fafc;
  flex-shrink: 0;
}
#mergeToolViewV2 .skip-link {
  font-size: 0.82rem; color: #64748b;
  background: none; border: none; cursor: pointer;
  text-decoration: underline; font-family: inherit;
}
#mergeToolViewV2 .skip-link:hover { color: #dc2626; }
#mergeToolViewV2 .nav-btn {
  background: white; border: 1px solid #e2e8f0;
  border-radius: 7px; padding: 7px 12px;
  font-size: 0.82rem; cursor: pointer; color: #475569;
  display: inline-flex; align-items: center; gap: 4px; font-family: inherit;
}
#mergeToolViewV2 .nav-btn:hover { border-color: #ea580c; color: #ea580c; }
#mergeToolViewV2 .nav-btn:disabled { opacity: 0.4; cursor: not-allowed; }
#mergeToolViewV2 .next-issue-btn {
  margin-left: auto;
  background: #ea580c; color: white; border: none;
  padding: 11px 22px; border-radius: 8px;
  font-size: 0.92rem; font-weight: 700;
  cursor: pointer; font-family: inherit;
  display: inline-flex; align-items: center; gap: 8px;
  box-shadow: 0 2px 6px rgba(234,88,12,0.25);
}
#mergeToolViewV2 .next-issue-btn:hover { background: #c2410c; }
#mergeToolViewV2 .next-issue-btn .count-badge {
  background: rgba(255,255,255,0.22);
  padding: 1px 8px; border-radius: 999px;
  font-size: 0.74rem; font-weight: 700;
}
#mergeToolViewV2 .next-issue-btn.done {
  background: #16a34a; box-shadow: 0 2px 6px rgba(22,163,74,0.25);
}
#mergeToolViewV2 .next-issue-btn.done:hover { background: #15803d; }
```

- [ ] **Step 2: Replace the `renderSidebar()` stub with the real implementation**

In `merge-v2.js`, replace the stub:

```js
function renderSidebar(rowIdx) {
  const row = v2State.rows[rowIdx];
  if (!row) return '';

  const isResolved = !!(row.fetchResult && row.fetchResult.podPill !== 'miss');
  const sidebarClass = `detail-sidebar open${isResolved ? ' resolved' : ''}`;

  const isExport = row.routingType === 'export';
  const docName = isExport ? 'BOL or POL' : (row.routingType === 'unknown' ? 'POD, BOL, or POL' : 'POD');

  // Count remaining errors (excluding this one and any skipped)
  const remaining = v2State.rows.filter((r, i) =>
    i !== rowIdx
    && r.fetchResult?.podPill === 'miss'
    && !r.skipped
  ).length;

  // Routing trace from chain_attempted (passed via fetchResult)
  const trace = renderRoutingTrace(row);

  // Body — happens or resolved
  const bodyTop = isResolved
    ? renderResolvedBody(row, trace)
    : renderErrorBody(row, trace, docName);

  // Footer
  const isDone = remaining === 0;
  const footer = `
    <div class="ds-footer">
      <button class="skip-link" onclick="window.v2SkipRow(${rowIdx})">Skip this one</button>
      <button class="nav-btn" onclick="window.v2PrevError(${rowIdx})" title="Previous error">← Prev</button>
      ${isDone
        ? `<button class="next-issue-btn done" onclick="window.v2CloseSidebar()">Done — close sidebar ✓</button>`
        : `<button class="next-issue-btn" onclick="window.v2NextError(${rowIdx})">
             Next Error <span class="count-badge">${remaining} left</span> →
           </button>`
      }
    </div>
  `;

  return `
    <div class="${sidebarClass}" id="v2DetailSidebar">
      <div class="ds-header">
        <div class="ds-icon">${isResolved ? '✓' : '!'}</div>
        <div>
          <div class="ds-title">${isResolved ? 'Resolved' : 'Fix Container Error'}</div>
          <div class="ds-subtitle">${escHtml(row.containerNumber)} · Invoice ${escHtml(row.invoiceNumber || '—')}</div>
        </div>
        <button class="ds-close" onclick="window.v2CloseSidebar()">×</button>
      </div>
      <div class="ds-body">
        ${bodyTop}
      </div>
      ${footer}
    </div>
    <div class="sidebar-backdrop open" onclick="window.v2CloseSidebar()"></div>
  `;
}

function renderErrorBody(row, traceHtml, docName) {
  return `
    <div class="ds-section">
      <div class="ds-section-label">Customer</div>
      <div style="font-size:0.92rem; font-weight:600; color:#0f172a;">${escHtml(row.customer || '—')}</div>
    </div>

    <div class="ds-section">
      <div class="ds-section-label">What Happened</div>
      <div class="happened-block">
        <div class="title">${escHtml(docName)} not found in TMS</div>
        <div class="body">${escHtml(row.fetchResult?.message || 'No documents returned by TMS for this container.')}</div>
      </div>
    </div>

    <div class="ds-section">
      <div class="ds-section-label">Routing trace</div>
      ${traceHtml}
    </div>

    <div class="ds-section">
      <div class="ds-section-label">Resolve</div>
      <button class="retry-api-btn" onclick="window.v2RetryRow(${v2State.rows.indexOf(row)})">↻ Retry API call</button>
      <div class="resolve-divider"><span>or upload manually</span></div>
      <label class="ds-upload" for="v2UploadInput-${row.rowNum}">
        <div class="icon">⬆</div>
        <div class="title">Drop ${escHtml(docName)} for ${escHtml(row.containerNumber)}</div>
        <div class="help">.pdf only — replaces whatever the API would have returned</div>
        <input type="file" id="v2UploadInput-${row.rowNum}" accept=".pdf"
               onchange="window.v2HandleSidebarUpload(${v2State.rows.indexOf(row)}, this.files)" />
      </label>
    </div>
  `;
}

function renderResolvedBody(row, traceHtml) {
  const file = row.manualPodFile;
  const summary = file
    ? `<div class="ds-attached">
         <div class="name">${escHtml(file.name)}</div>
         <div class="size">${(file.size / 1024 / 1024).toFixed(2)} MB</div>
         <button class="replace" onclick="document.getElementById('v2UploadInput-${row.rowNum}').click()">Replace</button>
         <input type="file" id="v2UploadInput-${row.rowNum}" accept=".pdf" style="display:none;"
                onchange="window.v2HandleSidebarUpload(${v2State.rows.indexOf(row)}, this.files)" />
       </div>`
    : `<div class="ds-attached">
         <div class="name">Retry succeeded — fetched from TMS</div>
         <div class="size">${escHtml(row.fetchResult?.podLabel || '')}</div>
       </div>`;

  return `
    <div class="ds-section">
      <div class="ds-section-label">Customer</div>
      <div style="font-size:0.92rem; font-weight:600; color:#0f172a;">${escHtml(row.customer || '—')}</div>
    </div>

    <div class="ds-section">
      <div class="ds-section-label">Resolved</div>
      <div class="resolved-block">
        <div class="title">${escHtml(row.fetchResult?.statusText || 'Fetched')}</div>
        <div class="body">This row is now ready to merge.</div>
      </div>
    </div>

    <div class="ds-section">
      <div class="ds-section-label">Routing trace</div>
      ${traceHtml}
    </div>

    <div class="ds-section">
      <div class="ds-section-label">Attached</div>
      ${summary}
    </div>
  `;
}

function renderRoutingTrace(row) {
  const fr = row.fetchResult;
  const lines = [];
  // Header lines: routing decision
  if (row.routingType === 'import') {
    lines.push({ cls: 'note', marker: '→', text: `INV# <code>${escHtml(row.invoiceNumber)}</code> pos-2 → <strong>import</strong>` });
    lines.push({ cls: 'note', marker: '→', text: 'Plan: try POD → BOL → POL → IT' });
  } else if (row.routingType === 'export') {
    lines.push({ cls: 'note', marker: '→', text: `INV# <code>${escHtml(row.invoiceNumber)}</code> pos-2 → <strong>export</strong>` });
    lines.push({ cls: 'note', marker: '→', text: 'Plan: try BOL → POL → ITE' });
  } else {
    lines.push({ cls: 'note', marker: '→', text: 'INV# prefix non-standard — fell back to safety chain' });
    lines.push({ cls: 'note', marker: '→', text: 'Plan: try POD → BOL → POL → IT → ITE' });
  }

  // Steps from chain_attempted
  const chain = fr?.chainAttempted || [];
  for (const step of chain) {
    if (step.outcome === 'tms_hit') {
      lines.push({ cls: 'success', marker: '✓', text: `TMS: <code>${escHtml(step.type)}</code> found` });
    } else if (step.outcome === 'tms_miss') {
      lines.push({ cls: 'fail', marker: '✗', text: `TMS: no <code>${escHtml(step.type)}</code>` });
    } else if (step.outcome === 'tms_error') {
      lines.push({ cls: 'fail', marker: '✗', text: `TMS: <code>${escHtml(step.type)}</code> errored (timeout or API)` });
    }
  }

  // Manual upload completion line
  if (row.manualPodFile) {
    lines.push({ cls: 'success', marker: '✓', text: `Manual upload: <code>${escHtml(row.manualPodFile.name)}</code>` });
  }

  // Final note
  if (fr?.podPill === 'miss' && !row.manualPodFile) {
    lines.push({ cls: 'note', marker: '!', text: 'Exhausted chain — manual upload required' });
  }

  const linesHtml = lines.map(l =>
    `<div class="step ${l.cls}"><span class="marker">${l.marker}</span><span class="text">${l.text}</span></div>`
  ).join('');
  return `<div class="routing-trace">${linesHtml}</div>`;
}
```

- [ ] **Step 3: Verify**

```
cd desktop && npm start
```

Run a fetch with deliberately error-prone data (e.g., a row whose container won't be in QBO). When the fetch lands on Ready with errors, the sidebar auto-opens on the first error.

Expected:
- Header: red ⚠ icon + "Fix Container Error" + container/invoice subtitle
- Body: Customer · What Happened (red block, doc-type appropriate to routing) · Routing trace (with → plan lines + ✗ TMS misses) · Resolve (Retry button + dashed drop zone)
- Footer: "Skip this one" + "← Prev" + orange "Next Error · N left →"

Click the close (×) button → sidebar dismisses. Click an error row's Fix Error button → sidebar re-opens on that row.

- [ ] **Step 4: Commit**

```bash
git add app/assets/css/styles.css app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge/v2/m3): real error sidebar — header/body/footer/routing trace"
```

---

## Task 13: Sidebar navigation handlers (Skip / Prev / Next / Close)

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js`

- [ ] **Step 1: Replace the stub navigation handlers**

Replace the `window.v2OpenSidebar`, `window.v2CloseSidebar` stubs and add the new ones:

```js
function v2OpenSidebar(rowIdx) {
  v2State.openSidebarRow = rowIdx;
  setStateV2('ready');
}
function v2CloseSidebar() {
  v2State.openSidebarRow = -1;
  setStateV2('ready');
}
function v2SkipRow(rowIdx) {
  const row = v2State.rows[rowIdx];
  if (!row) return;
  row.skipped = true;
  // Advance to next un-skipped error
  const next = nextErrorIndex(rowIdx);
  if (next >= 0) {
    v2State.openSidebarRow = next;
  } else {
    v2State.openSidebarRow = -1;   // no more errors
  }
  setStateV2('ready');
}
function v2NextError(currentIdx) {
  const next = nextErrorIndex(currentIdx);
  v2State.openSidebarRow = next >= 0 ? next : -1;
  setStateV2('ready');
}
function v2PrevError(currentIdx) {
  const prev = prevErrorIndex(currentIdx);
  if (prev >= 0) {
    v2State.openSidebarRow = prev;
    setStateV2('ready');
  }
}

function nextErrorIndex(fromIdx) {
  for (let i = fromIdx + 1; i < v2State.rows.length; i++) {
    const r = v2State.rows[i];
    if (r.fetchResult?.podPill === 'miss' && !r.skipped) return i;
  }
  // Wrap around
  for (let i = 0; i < fromIdx; i++) {
    const r = v2State.rows[i];
    if (r.fetchResult?.podPill === 'miss' && !r.skipped) return i;
  }
  return -1;
}
function prevErrorIndex(fromIdx) {
  for (let i = fromIdx - 1; i >= 0; i--) {
    const r = v2State.rows[i];
    if (r.fetchResult?.podPill === 'miss' && !r.skipped) return i;
  }
  // Wrap around backward
  for (let i = v2State.rows.length - 1; i > fromIdx; i--) {
    const r = v2State.rows[i];
    if (r.fetchResult?.podPill === 'miss' && !r.skipped) return i;
  }
  return -1;
}

window.v2OpenSidebar  = v2OpenSidebar;
window.v2CloseSidebar = v2CloseSidebar;
window.v2SkipRow      = v2SkipRow;
window.v2NextError    = v2NextError;
window.v2PrevError    = v2PrevError;
```

- [ ] **Step 2: Verify with a multi-error scenario**

Drop a file where multiple containers won't be in QBO (e.g., manually craft `docs/test-multi-error.xlsx` with three nonsense container numbers like `FAKE000001`, `FAKE000002`, `FAKE000003`).

Expected:
- Fetch lands on Ready with 3 errors
- Sidebar auto-opens on FAKE000001
- Click "Next Error" → sidebar shows FAKE000002 (count says "1 left")
- Click "Next Error" → sidebar shows FAKE000003 (count says "0 left" — but text changed to green "Done — close sidebar ✓")
- Click "Done — close sidebar" → sidebar closes
- Re-open the sidebar by clicking FAKE000002's Fix Error button → sidebar shows on row 2
- Click "← Prev" → sidebar shows FAKE000001
- Click "Skip this one" on FAKE000001 → row gets the gray Skipped tag in the table behind, sidebar advances to FAKE000003 (since 2 was skipped to get there, next un-skipped error)

- [ ] **Step 3: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge/v2/m3): sidebar nav — Skip/Prev/Next/Close handlers"
```

---

## Task 14: Auto-save flow — Retry + PDF drop

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js`

- [ ] **Step 1: Implement `v2RetryRow` (single-container retry)**

Add:

```js
async function v2RetryRow(rowIdx) {
  const row = v2State.rows[rowIdx];
  if (!row) return;

  // Show pending state on the button
  const btn = document.querySelector('#v2DetailSidebar .retry-api-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Retrying…'; }

  try {
    const res = await fetch('http://localhost:8787/jobs/fetch-missing', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        containers: [{ containerNumber: row.containerNumber, invoiceNumber: row.invoiceNumber }],
        doc_types: ['pod'],
      }),
    });
    if (!res.ok) throw new Error(`Agent returned ${res.status}`);
    const { jobId } = await res.json();

    // Subscribe to a one-shot stream — we only care about pod_found / pod_missing for our row
    await new Promise((resolve) => {
      const url = `http://localhost:8787/jobs/${encodeURIComponent(jobId)}/stream`;
      const es = new EventSource(url);
      es.onmessage = (e) => {
        let evt; try { evt = JSON.parse(e.data); } catch { return; }
        if (evt.containerNumber !== row.containerNumber) return;
        if (evt.type === 'pod_found') {
          row.fetchResult = {
            invPill: 'ok',
            podPill: evt.tms_doc_type ? 'fallback' : 'ok',
            podLabel: evt.tms_doc_type || 'POD',
            statusText: evt.tms_doc_type ? `Fetched (${evt.tms_doc_type})` : 'Fetched',
            chainAttempted: evt.chain_attempted || [],
            message: '',
          };
          es.close(); resolve();
        } else if (evt.type === 'pod_missing') {
          row.fetchResult = {
            invPill: 'ok', podPill: 'miss', podLabel: '—',
            statusText: 'Needs PDF',
            chainAttempted: evt.chain_attempted || [],
            message: evt.message || '',
          };
          es.close(); resolve();
        } else if (evt.type === 'job_completed' || evt.type === 'job_cancelled') {
          es.close(); resolve();
        }
      };
      es.onerror = () => { es.close(); resolve(); };
    });

    setStateV2('ready');   // re-renders sidebar to ✓ Resolved if pod_found landed
  } catch (err) {
    if (btn) { btn.disabled = false; btn.textContent = '↻ Retry API call'; }
    alert(`Retry failed: ${err.message}`);
  }
}
window.v2RetryRow = v2RetryRow;
```

- [ ] **Step 2: Implement the PDF-drop handler**

```js
function v2HandleSidebarUpload(rowIdx, fileList) {
  const row = v2State.rows[rowIdx];
  if (!row) return;
  const file = fileList && fileList[0];
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.pdf')) {
    alert('Only .pdf files are accepted.');
    return;
  }

  row.manualPodFile = file;
  // Mark fetchResult as "ok" — manual upload bypasses the chain
  row.fetchResult = {
    invPill: 'ok',
    podPill: 'ok',
    podLabel: row.routingType === 'export' ? 'BOL' : 'POD',  // best-effort label; real type from filename or user
    statusText: 'Manual upload',
    chainAttempted: row.fetchResult?.chainAttempted || [],
    message: '',
  };
  setStateV2('ready');   // re-renders sidebar to ✓ Resolved
}
window.v2HandleSidebarUpload = v2HandleSidebarUpload;
```

- [ ] **Step 3: Verify both paths**

**Retry path:** Trigger an error row (use a known-good container manually broken — easiest is to disconnect QBO via Settings, run the fetch, then reconnect QBO and click Retry on a failed row). Or just mock by dropping a small file with a known-existing container that should fetch successfully on retry.

Expected: button shows "Retrying…" briefly, then sidebar body re-renders to a green ✓ Resolved view with the routing trace updated. Header tints green. Row in the table behind flips green.

**PDF drop path:** Click the "Drop POD for <container>" zone (or click anywhere in the dashed box). System file picker opens. Pick any `.pdf` file from your machine. Sidebar instantly re-renders to ✓ Resolved with `Attached: <filename> · X.XX MB` and a Replace link.

Click Replace → file picker re-opens. Pick a different file. Sidebar updates to show the new attachment.

- [ ] **Step 4: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge/v2/m3): auto-save — Retry API single-row + PDF drop in sidebar"
```

---

## Task 15: Mass retry button + Resume fetch

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js`

- [ ] **Step 1: Implement `v2RetryAllErrors`**

Replace the placeholder:

```js
async function v2RetryAllErrors() {
  const errors = v2State.rows.filter(r => r.fetchResult?.podPill === 'miss' && !r.skipped);
  if (errors.length === 0) return;

  // Dedup by container
  const seen = new Set();
  const containers = [];
  for (const row of errors) {
    const key = row.containerNumber.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    containers.push({
      containerNumber: row.containerNumber,
      invoiceNumber: row.invoiceNumber,
    });
    // Reset row state so the SSE handler re-marks them
    row.fetchResult = null;
  }

  v2State.fetchProgress = 0;
  v2State.fetchTotal = containers.length;
  v2State.fetchCurrentContainer = '';

  setStateV2('fetching');

  try {
    const res = await fetch('http://localhost:8787/jobs/fetch-missing', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ containers, doc_types: ['pod'] }),
    });
    if (!res.ok) throw new Error(`Agent rejected retry: ${res.status}`);
    const { jobId } = await res.json();
    v2State.jobId = jobId;
    openSseStream(jobId);
  } catch (err) {
    alert(`Couldn't start mass retry: ${err.message}`);
    setStateV2('ready');
  }
}
window.v2RetryAllErrors = v2RetryAllErrors;
```

- [ ] **Step 2: Implement `v2ResumeFetch`**

```js
async function v2ResumeFetch() {
  // Find queued rows (selected, no fetchResult, not skipped)
  const queued = v2State.rows.filter(r => r.selected && !r.fetchResult && !r.skipped);
  if (queued.length === 0) return;

  // Dedup by container
  const seen = new Set();
  const containers = [];
  for (const row of queued) {
    const key = row.containerNumber.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    containers.push({
      containerNumber: row.containerNumber,
      invoiceNumber: row.invoiceNumber,
    });
  }

  // Carry over fetchProgress so the progress label reads "Fetching N+1 / total"
  v2State.fetchTotal = containers.length;
  v2State.fetchProgress = 0;
  v2State.fetchCurrentContainer = '';

  setStateV2('fetching');
  try {
    const res = await fetch('http://localhost:8787/jobs/fetch-missing', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ containers, doc_types: ['invoice', 'pod'] }),
    });
    if (!res.ok) throw new Error(`Agent rejected resume: ${res.status}`);
    const { jobId } = await res.json();
    v2State.jobId = jobId;
    openSseStream(jobId);
  } catch (err) {
    alert(`Couldn't resume fetch: ${err.message}`);
    setStateV2('ready');
  }
}
window.v2ResumeFetch = v2ResumeFetch;
```

- [ ] **Step 3: Verify**

**Mass retry:** Run a fetch with a few error rows. On Ready, switch to Errors tab. Click `↻ Retry all errors`. State transitions to Fetching with just the error rows in the queue. After completion, lands back on Ready — rows that were re-fetchable have flipped green; rows still missing are still in the Errors tab.

**Resume fetch:** Drop a larger file. Click Fetch. After ~3 rows complete, hit Cancel. Land on Ready with `Resume fetch · N queued` button. Click Resume. Fetching state takes over with just the queued rows. After completion, Ready shows everything fetched.

- [ ] **Step 4: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge/v2/m3): mass retry button + Resume fetch button"
```

---

## Task 16: Smoke test — full acceptance criteria walkthrough

**Files:** None modified — verification only.

This task covers the spec's 12 acceptance criteria end-to-end. Stop and fix any defect before moving on.

- [ ] **Step 1: Routing summary visible on Review**

Drop `docs/no sav.xlsx`. Confirm Review's success card (or issues card) shows the routing summary band reading correct counts (e.g., `Will fetch · [POD] 100 imports · [BOL/POL] 10 exports`). Click "Show all 110 rows" — confirm Will-fetch column is populated per row with the right chip color.

- [ ] **Step 2: Live Fetching with animations**

Click Fetch. Watch the table — pills should fade-in from gray to green (or amber for fallbacks) as containers finish, with a 1-second yellow flash on each row update. Progress label updates: `Fetching X / N · <container>`. Progress bar fills smoothly.

- [ ] **Step 3: Cancel mid-fetch → Resume**

Cancel after a few rows finish. Land on Ready with the blue `Resume fetch · N queued` button + `Last fetched: <c>` meta line. Click Resume. Fetch picks up; eventually lands on full Ready.

- [ ] **Step 4: Sidebar auto-opens on first error**

If any errors exist on the Ready landing, sidebar should already be open on the first error. Routing trace block lists each step in the chain with ✗ markers for misses. Test with both an import row (chain shows `POD → BOL → POL → IT`) and an export row (chain shows `BOL → POL → ITE`).

- [ ] **Step 5: Retry API success path**

Click `↻ Retry API call` in the sidebar. (Use a row that's transient-failed if possible — otherwise the retry will re-confirm the miss.) On success, body re-renders to ✓ Resolved view, header tints green, footer's count drops by 1.

- [ ] **Step 6: PDF drop success path**

Click the upload zone. Pick any local PDF. Sidebar instantly shows `Attached: <filename> · X.XX MB` with a Replace link. Routing trace shows `✓ Manual upload: <filename>` line at the bottom. Row in the table flips green with a "Manual upload" status text.

- [ ] **Step 7: Skip behavior**

On a multi-error batch, click `Skip this one`. Row gets a gray "Skipped" tag in the table, sidebar advances to next error, footer count drops. Switch to All tab — confirm skipped row's checkbox is disabled. Click the row's `Fix Error` button → sidebar re-opens, skip is reversed.

- [ ] **Step 8: Export-row sidebar specifics**

Open the sidebar on a `PE…` (export) row's error. Confirm:
- Routing trace lists `BOL → POL → ITE` (no POD)
- Upload zone reads "Drop BOL or POL for <container>"

- [ ] **Step 9: Selection in All tab**

Switch to All tab. Toggle individual checkboxes — `Continue to Merge · [N selected]` count updates. Master checkbox toggles all selectable rows. Error rows' checkboxes are disabled.

- [ ] **Step 10: Continue to Merge**

Click `Continue to Merge →`. Lands on Merging (M4 stub renderMerging). No errors thrown.

- [ ] **Step 11: Mass retry**

Switch to Errors tab. Click `↻ Retry all errors`. All error containers re-fetched in one batch. Lands back on Ready.

- [ ] **Step 12: Defensive teardown**

From every state (Review, Fetching mid-flight, Ready with sidebar open, Merging stub), click `+ New Merge`. Confirm: (a) no console errors, (b) lands on Empty, (c) any active fetch is cancelled (check Network tab — `/jobs/{id}/cancel` posted), (d) any open EventSource is closed (no continued SSE traffic).

- [ ] **Step 13: Commit (empty if no fixes)**

```bash
# If no fixes were needed:
git commit --allow-empty -m "test(merge/v2/m3): smoke test pass — all 12 acceptance criteria verified"
```

If a defect was fixed during smoke: `git add` the fix and commit with `fix(merge/v2/m3): <summary>` instead.

---

## Task 17: Version bump + build + push + GitHub release

**Files:**
- Modify: `desktop/VERSION`

This is the standard ship pipeline per `feedback_use_runbuild_for_rebuild.md`, `feedback_always_push_and_release.md`, `feedback_publish_release.md`.

- [ ] **Step 1: Bump VERSION**

```bash
cat desktop/VERSION       # current: 2.47
echo 2.48 > desktop/VERSION
cat desktop/VERSION       # confirms: 2.48
```

- [ ] **Step 2: Sync `desktop/package.json`**

```bash
cd desktop && node bump-version.js && cd ..
grep '"version"' desktop/package.json
# Expected: "version": "2.48.0",
```

- [ ] **Step 3: Run rebuild via runbuild.bat (PowerShell pattern)**

```powershell
$buildDir = "C:\Users\Joseph\Desktop\NGL ACCOUNTING SERVICE\desktop"
$emptyFile = "$buildDir\.empty-stdin"
Set-Content -Path $emptyFile -Value "" -NoNewline -Encoding ASCII

$p = Start-Process -FilePath "$buildDir\runbuild.bat" `
  -WorkingDirectory $buildDir `
  -RedirectStandardInput $emptyFile `
  -RedirectStandardOutput "$buildDir\build-log-2.48.txt" `
  -RedirectStandardError "$buildDir\build-log-2.48-err.txt" `
  -PassThru -Wait -NoNewWindow

Remove-Item $emptyFile -ErrorAction SilentlyContinue
"ExitCode: $($p.ExitCode)"
```

Run with `run_in_background: true`. Use Monitor to tail `desktop/build-log-2.48.txt`. Wait for `===ELECTRON_BUILD_DONE===`. ~3-6 min.

- [ ] **Step 4: Verify build artifacts**

```bash
ls -la "desktop/dist/NGL_ACCOUNTING_INSTALLER_v2.48.0.exe" "desktop/dist/latest.yml"
cat desktop/dist/latest.yml | head -3   # version: 2.48.0
```

If exit code != 0 or files missing, **stop and investigate** the build log. Do not proceed.

- [ ] **Step 5: Stage + commit + push**

```bash
git add desktop/VERSION desktop/package.json
git commit -m "chore: bump version to 2.48.0 (M3 — Fetching + Ready states)"
git push origin main
```

- [ ] **Step 6: Create GitHub release**

```bash
gh release create v2.48.0 \
  "desktop/dist/NGL_ACCOUNTING_INSTALLER_v2.48.0.exe" \
  "desktop/dist/latest.yml" \
  --title "v2.48.0 — Merge Tool V2 M3 (Fetching + Ready)" \
  --notes "$(cat <<'EOF'
## What's new

Behind the **Settings → Merge Tool — Beta** toggle.

- **Fetching state** — live progress bar + per-row pill updates as containers come back from the agent. Cancel mid-fetch lands on Ready with partial data.
- **Ready state** — full and partial variants. Partial shows a blue `↻ Resume fetch · N queued` button with a "Last fetched: <c>" meta line.
- **INV#-driven doc routing** — the tool decides POD vs BOL/POL **before** the first API call by parsing the INV# pos-2 letter (M=import, E=export). Falls back to WO# letter, then a safety chain.
- **IT/ITE chain extension** — when POD/BOL/POL all miss in TMS, the agent now tries IT (pull-out ticket) for imports or ITE (return ticket) for exports as a last resort before manual upload.
- **Will-fetch column + routing summary band** — see what each row is going to fetch up front, and a batch-level summary above the table.
- **Error sidebar** — auto-opens on the first error when Ready loads. New "Routing trace" section shows the step-by-step fetch log (✗ TMS: no POD, ✗ TMS: no BOL, etc.). Auto-save on Retry success or PDF drop — sidebar re-renders to a green ✓ Resolved view, you can Replace if you grabbed the wrong PDF.
- **Skip / Mass retry** — Skip in the sidebar removes a row from the error workflow without merging it. New `↻ Retry all errors` button on the Errors tab re-fetches everything stuck in one batch.

## Spec & plan
- [Spec](docs/superpowers/specs/2026-05-06-merge-tool-v2-m3-fetching-design.md)
- [Plan](docs/superpowers/plans/2026-05-06-merge-tool-v2-m3-fetching.md)

Next milestone (M4): wire the actual merge engine + Done state outputs card.
EOF
)"
```

- [ ] **Step 7: Verify release**

```bash
gh release view v2.48.0
```

Confirm both attached files (`.exe` and `.yml`) and release notes render correctly.

- [ ] **Step 8: Update memory + roadmap**

Update `memory/project_merge_tool_ux_redesign.md` to mark M3 as SHIPPED and M4 as NEXT. Update `MEMORY.md`'s entry to match. Commit:

```bash
git add memory/project_merge_tool_ux_redesign.md memory/MEMORY.md
git commit -m "docs(memory): mark M3 shipped in v2.48.0"
git push origin main
```

(These memory files live outside the repo, but the user's auto-memory layer reads them — the commit is symbolic; the file edits themselves are what matters.)

Wait — `memory/` is NOT inside the repo (it lives in `~/.claude/projects/...`). Skip the git commit for memory updates; just edit the files directly. The MEMORY.md edit is enough.

- [ ] **Step 9: Done**

Auto-updater picks up M3 on next packaged-app launch.

---

## Self-Review

**Spec coverage check (every numbered item from the spec → task):**

| Spec section | Task |
|---|---|
| Routing rule (INV# pos-2 primary, WO# fallback, safety chain) | Tasks 2 + 3 |
| Doc chain by type (Import/Export/Unknown with IT/ITE) | Task 3 (backend) |
| Pill semantics (ok/fallback/miss/queued + dynamic labels) | Task 8 (`docPills`), Task 10 (`patchRow`) |
| Backend `_tms_pod_fallback` change + `chain_attempted` field | Tasks 3 + 4 |
| Fetching state — render | Task 8 |
| Fetching state — SSE wiring + animations | Task 10 |
| Cancel button → partial Ready | Task 10 (`v2CancelFetch`) |
| Ready state — full variant + Continue to Merge button | Task 11 |
| Ready state — partial variant + Resume fetch button | Tasks 11 + 15 |
| Errors-tab default-active | Task 11 |
| Sidebar auto-open on first error | Task 11 |
| Master + per-row checkbox sync | Task 11 |
| Same-container dedup at fetch launch | Task 9 (selected dedup) + Task 10 (`patchRow` applies result to all matching rows) |
| Sidebar header/body/footer + routing trace | Task 12 |
| Auto-save flow (Retry + PDF drop) → ✓ Resolved view | Tasks 12 + 14 |
| Skip behavior | Task 13 |
| Mass retry button | Task 15 |
| Out-of-band: animation timing | Task 7 (CSS keyframes) + Task 10 (`flash-update` class) |
| Out-of-band: cancel race protection (`subMode !== 'fetching'` guard) | Task 10 (`es.onmessage` early-return) |
| Out-of-band: sidebar state across re-renders | Task 11 (`v2State.openSidebarRow` survives) |
| Out-of-band: `+ New Merge` defensive teardown | Task 1 |
| Out-of-band: manual file persistence | Task 14 (`row.manualPodFile` lives on the row) |
| Acceptance criteria 1-12 | Task 16 |
| Version bump + build + push + release | Task 17 |

**Placeholder scan:** No "TBD" / "TODO" / "implement later". Every step has actual code. Stub handlers in early tasks (e.g., Task 8's `v2OpenSidebar = console.log`) are explicitly marked as stubs with the task that wires them.

**Type consistency:**
- `v2State.rows[i]` shape consistent: Task 1 declares it, Task 6 populates `routingType`/`expectedDoc`, Task 10 populates `fetchResult`, Task 14 populates `manualPodFile`, Task 13 populates `skipped`. No field renamed mid-plan.
- `routingDecisionFor` signature stable across Tasks 2 and 6.
- `chain_attempted` payload shape (`{type, outcome}`) consistent across Tasks 3, 4, 10, 12.
- Window globals: `v2OpenSidebar`, `v2CloseSidebar`, `v2SkipRow`, `v2NextError`, `v2PrevError`, `v2RetryRow`, `v2HandleSidebarUpload`, `v2RetryAllErrors`, `v2ResumeFetch`, `v2CancelFetch`, `v2ClickContinueMerge`, `v2HandleReadyTab`, `v2HandleReadySearch`, `v2ToggleAllReady`, `v2ToggleFetchRow` — all defined exactly once each.

**Task ordering check:** Each task ends with the app in a working state.
- After Task 1: M2 baseline still works, defensive teardown wired
- After Task 2: utilities exist, no UI change
- After Tasks 3-4: backend chain extended + tested, frontend unaffected
- After Task 5: CSS loaded, no UI change yet
- After Task 6: Review state shows new column + summary
- After Task 7: more CSS loaded
- After Task 8: Fetching state renders (static)
- After Task 9: Fetch button kicks off real job
- After Task 10: live updates work end-to-end
- After Task 11: Ready state renders both variants + sidebar stub
- After Task 12: real sidebar markup + routing trace
- After Task 13: sidebar nav works
- After Task 14: auto-save + retry work
- After Task 15: mass retry + resume work
- After Task 16: smoke-tested
- After Task 17: shipped
