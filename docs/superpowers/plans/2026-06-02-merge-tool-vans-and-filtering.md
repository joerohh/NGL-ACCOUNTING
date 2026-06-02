# Merge Tool — Trailer Vans + Filtering + Queue/Error UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship trailer-van invoice routing, batched queued retries, a unified filter system, a read-only fetch-time sidebar, and a progress strip with pause/cancel to the merge tool — all behind a local-install-only build (no GitHub release until user-approved).

**Architecture:** Frontend changes flow from data layer (routing logic in `shared/utils.js`) outward to the merge tool UI (`merge-v2.js`) and stylesheet. Backend adds a `van` branch to the existing TMS POD-fallback router plus a new pause endpoint. Tests live alongside the agent code in `agent/tests/`. The build step ends with a local `.exe` only — no `gh release create`.

**Tech Stack:** Vanilla ES modules (no build step) · pdf-lib · SheetJS · SortableJS · JSZip · FastAPI (Python) · Playwright · electron-builder

**Spec:** `docs/superpowers/specs/2026-06-02-merge-tool-vans-and-filtering-design.md`
**Mockup (visual source of truth):** `app/mockups/merge-filters.html`

---

## File Map

### Frontend
- **Modify** `app/assets/js/shared/utils.js` — add `V` to `parseInvType`; van branch in `routingDecisionFor`; highlight `V` in `renderInvoiceNumberHtml`
- **Modify** `app/assets/js/tools/merge/merge-v2.js` — van filter chip · embedded doc-type pills · Customer dropdown · sortable column headers · master checkbox · status-scoped action buttons · interactive queued checkboxes · clickable rows during Fetching · progress strip + timer + pause/cancel · batched `processQueuedRetries`
- **Modify** `app/assets/js/tools/merge/merge-v2-output.js` — van filename rule
- **Modify** `app/assets/css/styles.css` — `.will-chip.van`, Customer dropdown, sortable column header, progress strip, Pause/Cancel button styles

### Backend (agent)
- **Modify** `agent/services/job_manager/fetch_job.py` — add `V` branch in `_tms_pod_fallback`; honor pause flag in container loop
- **Modify** `agent/services/job_manager/__init__.py` — add `paused` attribute on `Job`
- **Modify** `agent/routers/jobs.py` — add `POST /jobs/{id}/pause` and `POST /jobs/{id}/resume` endpoints

### Tests
- **Create** `agent/tests/test_job_manager/test_fetch_job_van_routing.py`
- **Modify** `agent/tests/test_routers_jobs.py` (or create `test_jobs_pause_resume.py`)
- **Create** `agent/tests/test_shared_utils_van.py` (frontend logic mirror, optional — only if there's a JS test harness; otherwise skip)

### Build
- **Modify** `desktop/VERSION` (bump to `2.77.0`)
- **Run** `desktop/runbuild.bat` locally
- **Do NOT** run `gh release create` — user installs the local `.exe` for testing

---

## Phase 1 — Trailer van routing (frontend logic)

### Task 1: Add `V` → `'van'` to `parseInvType`

**Files:**
- Modify: `app/assets/js/shared/utils.js:126-133`

- [ ] **Step 1: Read the current `parseInvType` to confirm structure**

Run: `Read app/assets/js/shared/utils.js offset=126 limit=10`

Expected output:
```js
export function parseInvType(inv) {
  if (!inv || inv.length < 2) return null;
  const c = inv[1].toUpperCase();
  if (c === 'M') return 'import';
  if (c === 'E') return 'export';
  if (c === 'W') return 'warehouse';
  return null;
}
```

- [ ] **Step 2: Add the `V` case**

Edit `app/assets/js/shared/utils.js` — change the function body to:

```js
export function parseInvType(inv) {
  if (!inv || inv.length < 2) return null;
  const c = inv[1].toUpperCase();
  if (c === 'M') return 'import';
  if (c === 'E') return 'export';
  if (c === 'W') return 'warehouse';
  if (c === 'V') return 'van';
  return null;
}
```

- [ ] **Step 3: Quick sanity check in browser console**

Open `app/index.html`, open DevTools, run:
```js
const u = await import('./assets/js/shared/utils.js');
console.log(u.parseInvType('SV26050013F')); // expect: 'van'
console.log(u.parseInvType('MM26050039F')); // expect: 'import'
console.log(u.parseInvType('XYZ'));          // expect: null
```

- [ ] **Step 4: Commit**

```bash
git add app/assets/js/shared/utils.js
git commit -m "feat(merge): route INV# 'V' prefix to van type"
```

---

### Task 2: Extend `routingDecisionFor` for van

**Files:**
- Modify: `app/assets/js/shared/utils.js:153-167`

- [ ] **Step 1: Update `routingDecisionFor` to handle `'van'`**

In `routingDecisionFor`, before the existing `if (fromInv)` block (line 158), add a van check. New body:

```js
export function routingDecisionFor(row) {
  const fromInv = parseInvType(row.invoiceNumber);
  if (fromInv === 'warehouse') {
    return { type: 'warehouse', expectedDoc: 'All QBO Docs' };
  }
  if (fromInv === 'van') {
    return { type: 'van', expectedDoc: '?' };
  }
  if (fromInv) {
    return { type: fromInv, expectedDoc: fromInv === 'import' ? 'POD' : 'BL/POL' };
  }
  const fromWo = parseWoType(row.workOrderNumber);
  if (fromWo) {
    return { type: fromWo, expectedDoc: fromWo === 'import' ? 'POD' : 'BL/POL' };
  }
  return { type: 'unknown', expectedDoc: '?' };
}
```

`expectedDoc: '?'` is correct for van — the Will Fetch chip is set in `merge-v2.js` (Task 4), not from `expectedDoc`. Van rows try the full TMS chain, so there's no single "expected" doc.

- [ ] **Step 2: Test in console**

```js
const u = await import('./assets/js/shared/utils.js');
u.routingDecisionFor({ invoiceNumber: 'SV26050013F' });
// expect: { type: 'van', expectedDoc: '?' }
```

- [ ] **Step 3: Commit**

```bash
git add app/assets/js/shared/utils.js
git commit -m "feat(merge): routingDecisionFor returns van type for V prefix"
```

---

### Task 3: Highlight `V` letter in `renderInvoiceNumberHtml`

**Files:**
- Modify: `app/assets/js/shared/utils.js:177-184`

- [ ] **Step 1: Add `V` to the highlight set**

Replace the function body so the letter check includes `V`:

```js
export function renderInvoiceNumberHtml(inv) {
  if (!inv || inv.length < 2) return escHtml(inv || '');
  const c = inv[1].toUpperCase();
  if (c === 'M' || c === 'E' || c === 'X' || c === 'W' || c === 'V') {
    return escHtml(inv[0]) + '<span class="inv-letter">' + escHtml(inv[1]) + '</span>' + escHtml(inv.slice(2));
  }
  return escHtml(inv);
}
```

- [ ] **Step 2: Commit**

```bash
git add app/assets/js/shared/utils.js
git commit -m "feat(merge): highlight V routing letter in invoice cell"
```

---

### Task 4: Add `van` filter chip + Will Fetch pill in `merge-v2.js`

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js` — multiple spots: state defaults, chip rendering, summary band, Will Fetch chip switch

- [ ] **Step 1: Find and read each `routingType` switch**

Run these in parallel:
- `Grep "routingType === 'warehouse'" app/assets/js/tools/merge/merge-v2.js -n`
- `Grep "willChipFor\|will-chip" app/assets/js/tools/merge/merge-v2.js -n`
- `Grep "routingTypeFilter\|routingSummaryBand\|routingTypeFilterTabs" app/assets/js/tools/merge/merge-v2.js -n`

Identify every spot that handles `import` / `export` / `warehouse` / `unknown` — each needs a `van` branch.

- [ ] **Step 2: Update `willChipFor` (around `merge-v2.js:508-512`)**

Add the van case. New function:

```js
function willChipFor(row) {
  if (row.routingType === 'import')    return `<span class="will-chip import">POD</span>`;
  if (row.routingType === 'export')    return `<span class="will-chip export">BL/POL</span>`;
  if (row.routingType === 'warehouse') return `<span class="will-chip whdocs">All QBO Docs</span>`;
  if (row.routingType === 'van')       return `<span class="will-chip van">TMS Docs</span>`;
  return `<span class="will-chip unknown">?</span>`;
}
```

- [ ] **Step 3: Update `routingTypeFilterTabs` (around `merge-v2.js:451-475`)**

Add a `van` chip between `warehouse` and `unknown`. Current shape (paraphrased):
```js
const types = [
  ['all', 'All'],
  ['import', 'Imports', imports],
  ['export', 'Exports', exports_],
  ['warehouse', 'Warehouse', warehouses],
  ['unknown', 'Unknown', unknown],
];
```

Insert `['van', 'Vans', vans]` (computed from `rows.filter(r => r.routingType === 'van').length`) before `unknown`:

```js
function routingTypeFilterTabs() {
  const imports    = v2State.rows.filter(r => r.routingType === 'import').length;
  const exports_   = v2State.rows.filter(r => r.routingType === 'export').length;
  const warehouses = v2State.rows.filter(r => r.routingType === 'warehouse').length;
  const vans       = v2State.rows.filter(r => r.routingType === 'van').length;
  const unknown    = v2State.rows.filter(r => r.routingType === 'unknown').length;
  const f = v2State.routingTypeFilter || 'all';
  const all = v2State.rows.length;
  const types = [
    ['all', 'All', all],
    ['import', 'Imports', imports],
    ['export', 'Exports', exports_],
    ['warehouse', 'Warehouse', warehouses],
    ['van', 'Vans', vans],
    ['unknown', 'Unknown', unknown],
  ];
  return `<div class="chip-row">` + types.map(([key, label, n]) => `
    <button class="chip ${f === key ? 'active' : ''}" onclick="window.v2SetRoutingTypeFilter('${key}')">
      <span class="chip-name">${label}</span>
      ${key === 'all' ? '' : `<span class="will-chip ${key}">${willChipLabel(key)}</span>`}
      <span class="count">${n}</span>
    </button>
  `).join('') + `</div>`;
}

function willChipLabel(key) {
  return ({ import: 'POD', export: 'BL/POL', warehouse: 'All QBO Docs', van: 'TMS Docs', unknown: '?' })[key] || '';
}
```

Note: this incorporates the **embedded doc-type pill** design from the mockup. If the existing chip rendering doesn't have the inline `will-chip`, copy the structure from `app/mockups/merge-filters.html` (search for `renderTypeChips`).

- [ ] **Step 4: Update `routingSummaryBand` (around `merge-v2.js:836-845`) to include vans**

```js
function routingSummaryBand() {
  const imports    = v2State.rows.filter(r => r.routingType === 'import').length;
  const exports_   = v2State.rows.filter(r => r.routingType === 'export').length;
  const warehouses = v2State.rows.filter(r => r.routingType === 'warehouse').length;
  const vans       = v2State.rows.filter(r => r.routingType === 'van').length;
  const unknown    = v2State.rows.filter(r => r.routingType === 'unknown').length;
  return `
    <div class="routing-summary">
      <strong>WILL FETCH</strong>
      <span class="will-chip import">POD</span> ${imports} imports ·
      <span class="will-chip export">BL/POL</span> ${exports_} exports ·
      <span class="will-chip whdocs">All QBO Docs</span> ${warehouses} warehouse ·
      <span class="will-chip van">TMS Docs</span> ${vans} vans ·
      <span class="will-chip unknown">?</span> ${unknown} unknown
    </div>`;
}
```

- [ ] **Step 5: Manually open `app/index.html`, drop the May errors workbook, verify**

Verify in the Review screen:
- 25 rows route to `van` (Vans chip count)
- Each van row shows orange `TMS Docs` Will Fetch chip
- Clicking the `Vans` chip filters the table to only V-prefix rows

- [ ] **Step 6: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge): add Vans filter chip with TMS Docs pill"
```

---

### Task 5: Van filename rule in `merge-v2-output.js`

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2-output.js`
- Modify: `app/assets/js/shared/utils.js:106-115` (buildMergedFilename)

- [ ] **Step 1: Update `buildMergedFilename` to handle van rows**

Van rows have unreliable "container" values (`T1022`, `Special`, PO numbers). Use `{date}_{INV#}_{WO#}_merged.pdf` for vans — drop the container entirely.

Replace `buildMergedFilename` body in `app/assets/js/shared/utils.js`:

```js
export function buildMergedFilename(row, datePrefix) {
  const sanitize = s => String(s).replace(/[<>:"/\\|?*\x00-\x1f]/g, '_');
  const inv = (row.invoiceNumber || '').trim();
  const wo  = (row.workOrderNumber || '').trim();

  // Van rows: skip the container field, use INV# + WO#
  if (row.routingType === 'van') {
    if (!inv) throw new Error('buildMergedFilename: van row needs invoice number');
    if (!wo)  return `${datePrefix}_${sanitize(inv)}_merged.pdf`;
    return `${datePrefix}_${sanitize(inv)}_${sanitize(wo)}_merged.pdf`;
  }

  // Original behavior for non-van rows — keep container in the filename
  if (!row.containerNumber) throw new Error('buildMergedFilename: row.containerNumber is required');
  const key = inv || wo;
  const container = sanitize(row.containerNumber);
  if (key) return `${datePrefix}_${sanitize(key)}_${container}_merged.pdf`;
  return `${datePrefix}_${container}_merged.pdf`;
}
```

- [ ] **Step 2: Check `merge-v2-output.js` for any van-specific output logic**

Run: `Grep "routingType" app/assets/js/tools/merge/merge-v2-output.js -n`

If output mode/path builders check `routingType` (they do — warehouse has its own path), add a parallel `van` branch. Vans land under the standard `Per Container` and `Combined` output folders (same as imports — TMS Docs filename will be the merged PDF).

Concrete change: any path or label switch with `import` / `export` / `warehouse` cases needs a `van` case. Treat van like import for output-mode purposes (single-doc merge into per-container PDF).

- [ ] **Step 3: Test the filename builder in console**

```js
const u = await import('./assets/js/shared/utils.js');
u.buildMergedFilename({ routingType: 'van', invoiceNumber: 'SV26050013F', workOrderNumber: 'SV2605060003' }, '2026-06-02');
// expect: '2026-06-02_SV26050013F_SV2605060003_merged.pdf'
```

- [ ] **Step 4: Commit**

```bash
git add app/assets/js/shared/utils.js app/assets/js/tools/merge/merge-v2-output.js
git commit -m "feat(merge): van filename uses INV# + WO# (drops trailer ID)"
```

---

### Task 6: Add `.will-chip.van` style

**Files:**
- Modify: `app/assets/css/styles.css` — find existing `.will-chip.import` rule, add `.will-chip.van` next to it

- [ ] **Step 1: Find the will-chip color rules**

Run: `Grep ".will-chip" app/assets/css/styles.css -n`

- [ ] **Step 2: Add van variant**

Add after the existing variants:
```css
.will-chip.van { background: #fef3c7; color: #92400e; }
```

(Matches the mockup palette — soft amber background, dark amber text.)

- [ ] **Step 3: Commit**

```bash
git add app/assets/css/styles.css
git commit -m "style(merge): add TMS Docs pill color for van type"
```

---

## Phase 2 — Backend van routing (agent)

### Task 7: Add `V` branch to `_tms_pod_fallback`

**Files:**
- Modify: `agent/services/job_manager/fetch_job.py:72-168`

- [ ] **Step 1: Read the existing `_tms_pod_fallback` routing block (lines 100-125)**

Confirm shape — it picks `doc_types` and `wo_kind` based on INV# pos-2 letter, falling back to WO# letter.

- [ ] **Step 2: Add van branch**

Insert a `V` check before the existing `M`/`E` checks. New routing block:

```python
# INV# pos-2 primary
inv_letter = inv_no[1] if len(inv_no) >= 2 else ""
if inv_letter == "V":
    doc_types = ("POD", "POL", "BL", "IT", "ITE")
    wo_kind = "van (by INV#)"
elif inv_letter == "M":
    doc_types = ("POD", "BL", "POL", "IT")
    wo_kind = "import (by INV#)"
elif inv_letter == "E":
    doc_types = ("BL", "POL", "ITE")
    wo_kind = "export (by INV#)"
elif "X" in wo_no:
    doc_types = ("BL", "POL", "ITE")
    wo_kind = "export (by WO#)"
elif "M" in wo_no:
    doc_types = ("POD", "BL", "POL", "IT")
    wo_kind = "import (by WO#)"
else:
    doc_types = ("POD", "BL", "POL", "IT", "ITE")
    wo_kind = "unknown"
```

The order `POD → POL → BL → IT → ITE` matches the spec for vans.

- [ ] **Step 3: Set `result.routing_type = "van"` when V invoice is detected**

Around the warehouse short-circuit (`fetch_job.py:336-337`), the warehouse helper sets `result.routing_type = "warehouse"`. For vans, do the equivalent earlier in the flow (or rely on the existing flow tagging it via the trace).

Add a helper near `_is_warehouse_row`:

```python
def _is_van_row(invoice_number: str) -> bool:
    """Mirror of routingDecisionFor() — van = INV# position-2 is 'V'."""
    if not invoice_number or len(invoice_number) < 2:
        return False
    return invoice_number[1].upper() == "V"
```

Then where `_is_warehouse_row` is checked and `routing_type` is set, add a parallel check that sets `result.routing_type = "van"` (but does NOT short-circuit — van runs the full QBO invoice + TMS fallback path, unlike warehouse).

- [ ] **Step 4: Verify TMS data layer supports `van` URL routing**

Per `MEMORY.md` (TMS Data Layer rebuild + TMS REST API surface notes), `services/tms_data` already handles the `van` type for `/bc-detail/document/van/{wo}`. Sanity check by running:

```bash
grep -rn "\"van\"\|'van'" agent/services/tms_data/
```

If `van` shows up in the route mappings, no changes needed. If it doesn't, add it to whatever URL builder the data layer uses (same pattern as `import` / `export`).

- [ ] **Step 5: Run existing fetch tests to confirm nothing regresses**

```bash
cd agent && python -m pytest tests/test_job_manager/ -v
```

Expected: all existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add agent/services/job_manager/fetch_job.py
git commit -m "feat(agent): route V-prefix invoices to van TMS fallback chain"
```

---

### Task 8: Test van routing

**Files:**
- Create: `agent/tests/test_job_manager/test_fetch_job_van_routing.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for van (V-prefix) routing in the fetch job."""
from services.job_manager.fetch_job import _is_van_row, _is_warehouse_row


def test_is_van_row_recognises_V_prefix():
    assert _is_van_row("SV26050013F") is True
    assert _is_van_row("PV2605260003") is True


def test_is_van_row_rejects_non_V_prefixes():
    assert _is_van_row("MM26050039F") is False  # import
    assert _is_van_row("SW04302026TRL") is False  # warehouse
    assert _is_van_row("SE26050006F") is False  # export
    assert _is_van_row("") is False
    assert _is_van_row("X") is False  # too short
    assert _is_van_row(None) is False


def test_van_and_warehouse_are_mutually_exclusive():
    """A row should never be both van and warehouse."""
    for inv in ["SV26050013F", "SW04302026TRL", "MM26050039F", "SE26050006F"]:
        assert not (_is_van_row(inv) and _is_warehouse_row(inv))
```

- [ ] **Step 2: Run the test to verify it fails (before implementing `_is_van_row` in Task 7 Step 3)**

Run: `cd agent && python -m pytest tests/test_job_manager/test_fetch_job_van_routing.py -v`
Expected: ImportError because `_is_van_row` doesn't exist yet.

If Task 7 was already done, the test should pass. That's fine — the discipline is each test having both a failing and passing observation; if you can't observe failure for a function already defined, note in commit message "added regression tests; impl pre-existed".

- [ ] **Step 3: Verify the test passes**

After `_is_van_row` is defined (from Task 7 Step 3):

Run: `cd agent && python -m pytest tests/test_job_manager/test_fetch_job_van_routing.py -v`
Expected: PASS (all 3 tests)

- [ ] **Step 4: Add an integration test that exercises the doc_types chain**

Extend the same file:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path


@pytest.mark.asyncio
async def test_van_invoice_picks_POD_POL_BL_IT_ITE_chain(tmp_path, monkeypatch):
    """V-prefix invoices should request docs in order POD → POL → BL → IT → ITE."""
    from services.job_manager.fetch_job import FetchJobMixin
    from services.job_manager import ContainerRequest, Job

    # Mock TMS data layer to record what doc_types it was asked for
    requested = []
    async def fake_get_document(job_id, invoice_data, doc_type, dest_dir, source="api"):
        requested.append(doc_type)
        return None  # always miss so the chain walks through all types

    mixin = FetchJobMixin()
    mixin._tms_data = MagicMock()
    mixin._tms_data.get_document = fake_get_document

    container = ContainerRequest(container_number="T1022", invoice_number="SV26050013F")
    job = Job("test", [container])
    job.download_dir = tmp_path
    job.events = MagicMock()
    job.events.put = AsyncMock()

    invoice_data = {"workOrderNumber": "SV2605060003"}
    dest = tmp_path / "doc.pdf"
    result, chain = await mixin._tms_pod_fallback(job, container, invoice_data, dest)

    assert requested == ["POD", "POL", "BL", "IT", "ITE"]
    assert result is None  # all missed in our mock
    assert len(chain) == 5
    assert all(step["outcome"] == "tms_miss" for step in chain)
```

- [ ] **Step 5: Run the new test**

Run: `cd agent && python -m pytest tests/test_job_manager/test_fetch_job_van_routing.py::test_van_invoice_picks_POD_POL_BL_IT_ITE_chain -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agent/tests/test_job_manager/test_fetch_job_van_routing.py
git commit -m "test(agent): cover V-prefix routing and TMS chain order"
```

---

## Phase 3 — Batched queued retries (Issue 1 fix)

### Task 9: Replace `processQueuedRetries` with batched version

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js:2480-2489`

- [ ] **Step 1: Read the existing function and the batched-pattern reference**

Confirm `processQueuedRetries` (around line 2480) loops sequentially with `await v2RetryRow`. Confirm `v2ResumeFetch` (around line 2151) shows the batched pattern.

- [ ] **Step 2: Replace `processQueuedRetries`**

```js
async function processQueuedRetries() {
  // Snapshot — new retries can still be queued while we process
  const queue = v2State.queuedRetries.slice();
  v2State.queuedRetries = [];
  if (queue.length === 0) return;

  // Resolve rowIdx → row; dedup by container so a single fetchMissing job
  // covers every retried row in one shot (mirrors v2ResumeFetch).
  const seen = new Set();
  const containers = [];
  for (const { rowIdx } of queue) {
    const row = v2State.rows[rowIdx];
    if (!row) continue;
    const key = (row.containerNumber || row.invoiceNumber || '').toLowerCase();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    containers.push({
      containerNumber: row.containerNumber,
      invoiceNumber: row.invoiceNumber,
    });
    // Reset row state so the SSE handler re-marks them
    row.fetchResult = null;
  }
  if (containers.length === 0) return;

  // Reuse the live-fetch UI affordances (progress counter, SSE stream handler)
  v2State.fetchTotal = containers.length;
  v2State.fetchProgress = 0;
  v2State.fetchCurrentContainer = '';
  v2State.completedContainers = new Set();

  setStateV2('fetching');
  try {
    v2State.jobIncludesInvoice = false;
    v2State.jobIncludesDoc = true;
    const result = await agentBridge.fetchMissing(containers, ['pod']);
    if (result.error || !result.jobId) {
      throw new Error(result.error || 'Agent did not return a jobId');
    }
    v2State.jobId = result.jobId;
    openSseStream(result.jobId);
  } catch (err) {
    alert(`Couldn't process queued retries: ${err.message}`);
    setStateV2('ready');
  }
}
```

- [ ] **Step 3: Manual test**

Open the app, run a fetch that produces multiple errors. While the fetch is running, click "Try Again" on 3+ error rows via the sidebar. After the main fetch settles, observe:
- The fetching toolbar should pop up ONCE for all queued retries (not once per row)
- The progress counter should show `N` containers, not 1 at a time
- All retried rows should update at roughly the same time

- [ ] **Step 4: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "fix(merge): batch queued single-row retries into one fetch job"
```

---

## Phase 4 — Filter system (Customer dropdown, sortable headers, master checkbox)

### Task 10: Add Customer dropdown to filter bar

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js` — state defaults, toolbar HTML, filter logic
- Modify: `app/assets/css/styles.css` — dropdown styling (copy from `app/mockups/merge-filters.html`)

- [ ] **Step 1: Add `customerFilter: 'all'` to `v2State` defaults**

In `merge-v2.js` near where `routingTypeFilter: 'all'` is initialized (`merge-v2.js:31` area), add:

```js
customerFilter: 'all',
```

And reset to `'all'` everywhere `routingTypeFilter` is reset (e.g. `merge-v2.js:124`, `merge-v2.js:195`).

- [ ] **Step 2: Add a Customer `<select>` to the toolbar**

Find the toolbar/filter-bar in `renderReady` and `renderReview` (around `merge-v2.js:1193-1202` and similar). Add a `<select>` populated from unique customers:

```js
function renderCustomerDropdown() {
  const counts = new Map();
  for (const r of v2State.rows) {
    if (!r.customer) continue;
    counts.set(r.customer, (counts.get(r.customer) || 0) + 1);
  }
  const customers = Array.from(counts.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  const selected = v2State.customerFilter || 'all';
  return `<select class="customer-select" onchange="window.v2SetCustomerFilter(this.value)">
    <option value="all" ${selected === 'all' ? 'selected' : ''}>All customers (${v2State.rows.length})</option>
    ${customers.map(([name, n]) => `
      <option value="${escHtml(name)}" ${selected === name ? 'selected' : ''}>${escHtml(name)} (${n})</option>
    `).join('')}
  </select>`;
}

window.v2SetCustomerFilter = function (value) {
  v2State.customerFilter = value;
  rerenderTbody();   // or setStateV2 of the current state
};
```

Insert the dropdown call into the toolbar HTML in both Review and Ready render paths.

- [ ] **Step 3: Apply the customer filter in the visible-rows pipeline**

Find where `routingTypeFilter` is applied (`merge-v2.js:441-442`). Add the customer filter right after:

```js
if (v2State.routingTypeFilter && v2State.routingTypeFilter !== 'all') {
  rows = rows.filter(r => r.routingType === v2State.routingTypeFilter);
}
if (v2State.customerFilter && v2State.customerFilter !== 'all') {
  rows = rows.filter(r => r.customer === v2State.customerFilter);
}
```

- [ ] **Step 4: CSS — copy `.customer-select` styles from the mockup**

From `app/mockups/merge-filters.html`, copy `.filter-bar select` → rename to `.customer-select` and append to `styles.css`. Approximate:

```css
.customer-select {
  padding: 8px 28px 8px 12px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  font-size: 13px;
  background: #fff;
  color: #0f172a;
  cursor: pointer;
  appearance: none;
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6' fill='none'><path d='M1 1l4 4 4-4' stroke='%2364748b' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'/></svg>");
  background-repeat: no-repeat;
  background-position: right 10px center;
  font-family: inherit;
}
.customer-select:focus { outline: none; border-color: #ea580c; box-shadow: 0 0 0 3px rgba(234,88,12,0.12); }
```

- [ ] **Step 5: Verify in browser**

Drop the May errors workbook → confirm dropdown lists `AJ LOGISVALUE USA INC`, `BNX SHIPPING INC. - GA`, `COOK CHEMICAL CO`, `IDEA NUOVA`, `NVH USA, INC`, etc. sorted A–Z with per-customer counts. Selecting one filters the table.

- [ ] **Step 6: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js app/assets/css/styles.css
git commit -m "feat(merge): add Customer filter dropdown to toolbar"
```

---

### Task 11: Sortable column headers (replace Sort dropdown if present)

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js` — state, head render, sort logic
- Modify: `app/assets/css/styles.css` — `.sortable` styles

- [ ] **Step 1: Add `sortKey` + `sortDir` to state**

```js
sortKey: null,         // 'cont' | 'inv' | 'cust' | 'status' | null
sortDir: 'asc',        // 'asc' | 'desc'
```

- [ ] **Step 2: Make column headers clickable**

Find the table head render (look for `<th>` definitions around `merge-v2.js:520-540` or wherever the table is composed). Replace static `<th>` for sortable columns with:

```js
function thSortable(label, key) {
  const active = v2State.sortKey === key;
  const arrow = active ? (v2State.sortDir === 'asc' ? '▲' : '▼') : '↕';
  return `<th class="sortable ${active ? 'sort-active' : ''}" onclick="window.v2HandleHeaderSort('${key}')">
    ${label} <span class="sort-arrow">${arrow}</span>
  </th>`;
}
```

Use it for `CONTAINER`, `INVOICE #`, `CUSTOMER`, `STATUS`. Leave WILL FETCH and DOCUMENTS non-sortable.

- [ ] **Step 3: Add `v2HandleHeaderSort` — three-click cycle**

```js
window.v2HandleHeaderSort = function (key) {
  if (v2State.sortKey !== key) {
    v2State.sortKey = key;
    v2State.sortDir = 'asc';
  } else if (v2State.sortDir === 'asc') {
    v2State.sortDir = 'desc';
  } else {
    v2State.sortKey = null;
    v2State.sortDir = 'asc';
  }
  setStateV2(v2State.state);  // re-render
};
```

- [ ] **Step 4: Apply sort in the visible-rows pipeline**

After all filters in the rendering pipeline, sort:

```js
if (v2State.sortKey) {
  const dir = v2State.sortDir === 'desc' ? -1 : 1;
  const cmp = {
    cont:   (a, b) => (a.containerNumber || '').localeCompare(b.containerNumber || ''),
    inv:    (a, b) => (a.invoiceNumber || '').localeCompare(b.invoiceNumber || ''),
    cust:   (a, b) => (a.customer || '').localeCompare(b.customer || ''),
    status: (a, b) => statusSortRank(a) - statusSortRank(b),
  }[v2State.sortKey];
  if (cmp) rows.sort((a, b) => cmp(a, b) * dir);
}

function statusSortRank(r) {
  if (r.fetchResult?.podPill === 'miss' || r.fetchResult?.invPill === 'miss') return 0; // errors first
  if (!r.fetchResult) return 1; // queued
  return 2; // ok
}
```

- [ ] **Step 5: CSS — `.sortable` styles**

```css
.sortable { cursor: pointer; user-select: none; transition: color 120ms, background-color 120ms; }
.sortable:hover { color: #0f172a; background: #f1f5f9; }
.sortable .sort-arrow { display: inline-block; margin-left: 4px; font-size: 10px; color: #94a3b8; }
.sortable.sort-active { color: #ea580c; background: #fff7ed; }
.sortable.sort-active .sort-arrow { color: #ea580c; }
```

- [ ] **Step 6: Manual verify**

Click CUSTOMER → rows sort A–Z. Click again → Z–A. Click again → back to default order. Active column tints orange.

- [ ] **Step 7: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js app/assets/css/styles.css
git commit -m "feat(merge): clickable sortable column headers"
```

---

### Task 12: Master checkbox (header cell) — bulk-toggles visible rows

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js`

- [ ] **Step 1: Replace the header check cell with an input**

Find the `<thead>` block. Where the empty check column header sits, replace with:

```js
`<th class="check-col"><input type="checkbox" id="v2MasterCheck"
        onchange="window.v2ToggleAllVisible(this.checked)" /></th>`
```

- [ ] **Step 2: Implement `v2ToggleAllVisible` to operate on the currently filtered/sorted visible rows**

```js
window.v2ToggleAllVisible = function (checked) {
  const visible = getCurrentlyVisibleRows();  // helper from Step 4
  for (const row of visible) {
    if (row.skipped) continue;
    row.selected = !!checked;
  }
  rerenderTbody();
  updateMasterIndeterminate();
};
```

- [ ] **Step 3: After each tbody rerender, update master checkbox state (checked / unchecked / indeterminate)**

```js
function updateMasterIndeterminate() {
  const master = document.getElementById('v2MasterCheck');
  if (!master) return;
  const visible = getCurrentlyVisibleRows();
  const total = visible.filter(r => !r.skipped).length;
  const checked = visible.filter(r => r.selected && !r.skipped).length;
  if (total === 0) { master.checked = false; master.indeterminate = false; master.disabled = true; return; }
  master.disabled = false;
  master.indeterminate = checked > 0 && checked < total;
  master.checked = checked === total;
}
```

Call `updateMasterIndeterminate()` at the end of `rerenderTbody()`.

- [ ] **Step 4: Extract `getCurrentlyVisibleRows()` — the same pipeline used in render**

Centralize the filter/search/sort pipeline so master + render share it:

```js
function getCurrentlyVisibleRows() {
  let rows = v2State.rows.slice();
  if (v2State.routingTypeFilter && v2State.routingTypeFilter !== 'all') {
    rows = rows.filter(r => r.routingType === v2State.routingTypeFilter);
  }
  if (v2State.customerFilter && v2State.customerFilter !== 'all') {
    rows = rows.filter(r => r.customer === v2State.customerFilter);
  }
  // Status tab filter (post-fetch)
  if (v2State.activeTab === 'errors') {
    rows = rows.filter(r => !r.skipped && r.fetchResult && (r.fetchResult.podPill === 'miss' || r.fetchResult.invPill === 'miss'));
  } else if (v2State.activeTab === 'queued') {
    rows = rows.filter(r => !r.fetchResult && !r.skipped);
  }
  // Search
  if (v2State.searchQuery) {
    const q = v2State.searchQuery.toLowerCase();
    rows = rows.filter(r =>
      (r.containerNumber || '').toLowerCase().includes(q)
      || (r.invoiceNumber || '').toLowerCase().includes(q)
      || (r.workOrderNumber || '').toLowerCase().includes(q)
      || (r.customer || '').toLowerCase().includes(q)
    );
  }
  // Sort applied here too (so master matches render)
  if (v2State.sortKey) {
    const dir = v2State.sortDir === 'desc' ? -1 : 1;
    const cmp = {
      cont:   (a, b) => (a.containerNumber || '').localeCompare(b.containerNumber || ''),
      inv:    (a, b) => (a.invoiceNumber || '').localeCompare(b.invoiceNumber || ''),
      cust:   (a, b) => (a.customer || '').localeCompare(b.customer || ''),
      status: (a, b) => statusSortRank(a) - statusSortRank(b),
    }[v2State.sortKey];
    if (cmp) rows.sort((a, b) => cmp(a, b) * dir);
  }
  return rows;
}
```

Then refactor renderers to call this instead of inline filtering.

- [ ] **Step 5: Manual verify the master checkbox flow**

1. Drop May errors workbook → run fetch
2. Click Errors tab → click master checkbox → all error rows uncheck
3. Click All tab → confirm only OK rows are still checked
4. Click `Continue to Merge` → confirm count excludes the unchecked errors

- [ ] **Step 6: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge): master checkbox toggles all visible rows"
```

---

### Task 13: Hybrid status-scoped action buttons

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js`

- [ ] **Step 1: Update the action-row render in `renderReady`**

In `renderReady` (`merge-v2.js:1090-1200`), refactor the action-button block. Currently `Continue to Merge` + `Resume fetch` show conditionally based on partial state. Add `Retry errors` button alongside, and scope visibility by `activeTab`:

```js
const errorRows = all.filter(r => !r.skipped && r.fetchResult && (r.fetchResult.podPill === 'miss' || r.fetchResult.invPill === 'miss'));
const selectedErrors = errorRows.filter(r => r.selected).length;
const selectedQueued = queued.filter(r => r.selected).length;
const showRetry  = (v2State.activeTab === 'all' || v2State.activeTab === 'errors') && selectedErrors > 0;
const showResume = (v2State.activeTab === 'all' || v2State.activeTab === 'queued') && selectedQueued > 0;

const actionButtons = `
  ${showRetry ? `<button class="merge-btn retry-secondary" onclick="window.v2RetryAllErrors()">↻ Retry errors <span class="count-badge">${selectedErrors}</span></button>` : ''}
  ${showResume ? `<button class="merge-btn resume" onclick="window.v2ResumeFetch()">↻ Resume fetch <span class="count-badge">${selectedQueued} queued</span></button>` : ''}
  <button class="merge-btn" id="v2BtnContinueMerge" ${selected === 0 ? 'disabled' : ''}
          onclick="window.v2ClickContinueMerge()">
    Continue to Merge <span class="count-badge"><span class="sel-count">${selected}</span> selected</span>
  </button>
`;
```

Wire into both the `isPartial` and non-partial branches of the action-bar template.

- [ ] **Step 2: Add `.merge-btn.retry-secondary` style**

```css
.merge-btn.retry-secondary { background: #fff; color: #dc2626; border: 1px solid #fecaca; }
.merge-btn.retry-secondary:hover { background: #fef2f2; border-color: #fca5a5; }
```

- [ ] **Step 3: Manual verify**

1. Fetch with errors and queued rows present
2. All tab → see all three buttons
3. Errors tab → only Retry + Continue to Merge
4. Queued tab → only Resume + Continue to Merge

- [ ] **Step 4: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js app/assets/css/styles.css
git commit -m "feat(merge): scope Retry/Resume action buttons by active status tab"
```

---

### Task 14: Make queued row checkboxes interactive

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js`

- [ ] **Step 1: Update the row checkbox `disabled` rule**

Find the row check rendering (around `merge-v2.js:617-625`). Currently:
```js
const interactive = hasFetch && !isSkipped;
const checkAttrs  = `${row.selected && interactive ? 'checked' : ''} ${interactive ? '' : 'disabled'}`;
```

Change so queued rows ARE interactive (only skipped rows stay disabled):

```js
const interactive = !isSkipped;
const checkAttrs  = `${row.selected && interactive ? 'checked' : ''} ${interactive ? '' : 'disabled'}`;
const checkTitle  = isErrorRow ? 'This row is missing its POD/BL. If checked, the merge will include only the invoice page for this container.'
                 : isQueued ? 'Uncheck to remove from queue (won\'t be fetched on Resume)'
                 : row.skipped ? 'Skipped — re-click Fix Error to undo'
                 : '';
```

- [ ] **Step 2: Verify `v2ResumeFetch` already filters by `selected`**

Look at `merge-v2.js:2151-2167` — `queued = v2State.rows.filter(r => r.selected && !r.fetchResult && !r.skipped)`. ✓ Already correct.

- [ ] **Step 3: Manual verify**

1. Run fetch, click Queued tab
2. Uncheck a queued row → Resume fetch counter decreases by 1
3. Uncheck all queued rows → Resume fetch button disappears

- [ ] **Step 4: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge): allow unchecking queued rows to remove from Resume"
```

---

## Phase 5 — Fetch-time interactivity (read-only sidebar)

### Task 15: Allow row clicks during Fetching state

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js`

- [ ] **Step 1: Find the row `onclick` and `interactive` flags in the Fetching state**

The fetching-state row rendering currently disables clicks because `fetchResult` is null. Decouple "row clickable to open sidebar" from "sidebar is fully interactive."

- [ ] **Step 2: Update row render to add click handler in fetching state too**

Where the `trAttrs` is built (around `merge-v2.js:627-629`):

```js
const trAttrs = (isError || v2State.subMode === 'fetching')
  ? `onclick="window.v2OpenSidebar(${rowIdx})" style="cursor:pointer;"`
  : '';
```

- [ ] **Step 3: Update `renderSidebar` to detect fetching state and render read-only**

In `renderSidebar` (`merge-v2.js:1476`):

```js
function renderSidebar(rowIdx) {
  const row = v2State.rows[rowIdx];
  if (!row) return '';

  const isFetching = v2State.subMode === 'fetching' && !row.fetchResult && !row.skipped;
  const isResolved = !!(row.fetchResult && row.fetchResult.podPill !== 'miss');
  // ... rest of existing logic ...

  if (isFetching) {
    return renderFetchingSidebar(rowIdx, row);
  }
  // ... original error / resolved paths ...
}
```

- [ ] **Step 4: Implement `renderFetchingSidebar` matching the mockup**

```js
function renderFetchingSidebar(rowIdx, row) {
  const isInFlight = row.containerNumber === v2State.fetchCurrentContainer;
  const subtitle = `${escHtml(row.containerNumber || 'Warehouse')} · Invoice ${escHtml(row.invoiceNumber || '—')}`;
  const titleText = isInFlight ? 'Fetching Container' : 'Container Queued';
  const bannerText = isInFlight
    ? '<strong>This row is being fetched right now.</strong> Updates appear live; actions unlock when this row settles.'
    : '<strong>This row is queued.</strong> It will start as soon as an in-flight slot opens up. You can uncheck the row to skip.';

  const docName = (row.routingType === 'export') ? 'BL or POL'
                : (row.routingType === 'warehouse') ? 'any QBO attachment'
                : (row.routingType === 'van') ? 'TMS document'
                : (row.routingType === 'unknown') ? 'POD, BL, or POL'
                : 'POD';

  return `
    <div class="detail-sidebar open" id="v2DetailSidebar">
      <div class="ds-header">
        <div class="ds-icon" style="background:#fff7ed;color:#ea580c;">!</div>
        <div>
          <div class="ds-title">${titleText}</div>
          <div class="ds-subtitle">${subtitle}</div>
        </div>
        <button class="ds-close" onclick="window.v2CloseSidebar()">×</button>
      </div>
      <div class="panel-empty-banner" style="background:#fff7ed;border-bottom-color:#fed7aa;color:#9a3412;">
        <span>${bannerText}</span>
      </div>
      <div class="ds-body">
        <div class="ds-section">
          <div class="ds-section-label">Customer</div>
          <div style="font-size:0.92rem;font-weight:600;color:#0f172a;">${escHtml(row.customer || '—')}</div>
        </div>
        <div class="ds-section">
          <div class="ds-section-label">What's Happening</div>
          <div class="happened-block" style="background:#fff7ed;border-color:#fed7aa;">
            <div class="title" style="color:#9a3412;">${isInFlight ? 'Pulling documents now' : 'Waiting in queue'}</div>
            <div class="body" style="color:#7c2d12;">
              Routing type: <strong>${escHtml(row.routingType)}</strong>.
              ${isInFlight ? 'Invoice PDF pulled · now walking the doc chain on TMS.' : 'The fetcher will pick this up shortly.'}
            </div>
          </div>
        </div>
        <div class="ds-section">
          <div class="ds-section-label">Resolve</div>
          <button class="retry-api-btn" disabled title="Available when this row finishes fetching">↻ Retry API call</button>
          <div class="resolve-divider"><span>or upload manually</span></div>
          <label class="ds-upload" style="opacity:0.55;cursor:not-allowed;pointer-events:none;">
            <div class="icon">⬆</div>
            <div class="title">Drop ${escHtml(docName)} for ${escHtml(row.containerNumber || row.workOrderNumber || row.invoiceNumber)}</div>
            <div class="help">.pdf only — replaces whatever the API would have returned</div>
          </label>
        </div>
      </div>
      <div class="ds-footer">
        <button class="skip-link" disabled title="Available when fetch completes">Skip this one</button>
        <button class="nav-btn" disabled title="Available when fetch completes">← Prev</button>
        <button class="next-issue-btn" disabled title="Wait for fetch to finish">Next Error</button>
      </div>
    </div>
    <div class="sidebar-backdrop open" onclick="window.v2CloseSidebar()"></div>
  `;
}
```

- [ ] **Step 5: Manual verify**

Start a fetch with many containers. Click a queued or in-flight row → sidebar opens with disabled Retry/Skip/Upload buttons. Close, verify the fetch continues uninterrupted.

- [ ] **Step 6: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge): read-only sidebar opens on row click during fetch"
```

---

## Phase 6 — Progress strip + Pause/Cancel

### Task 16: Add `/jobs/{id}/pause` and `/jobs/{id}/resume` endpoints

**Files:**
- Modify: `agent/services/job_manager/__init__.py` — add `paused: bool` attribute to `Job`
- Modify: `agent/routers/jobs.py` — new endpoints

- [ ] **Step 1: Add `paused = False` to `Job` dataclass / class**

Read `agent/services/job_manager/__init__.py` for the `Job` definition. Add a `paused: bool = False` field (plus an `asyncio.Event` if you want efficient waits — see Step 3).

```python
# In Job class definition
paused: bool = False
```

If `Job` uses an `__init__`, initialize `self.paused = False`.

- [ ] **Step 2: Add the pause/resume endpoints**

In `agent/routers/jobs.py`, add:

```python
@router.post("/{job_id}/pause", dependencies=[Depends(require_auth)])
async def pause_job(job_id: str):
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    job.paused = True
    logger.info("Job %s paused by user", job_id)
    return {"ok": True, "paused": True}


@router.post("/{job_id}/resume", dependencies=[Depends(require_auth)])
async def resume_job(job_id: str):
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    job.paused = False
    logger.info("Job %s resumed by user", job_id)
    return {"ok": True, "paused": False}
```

Match the import + auth-wrapping style of the existing endpoints in the same file.

- [ ] **Step 3: Honor the pause flag in the container dispatch loop**

Find the loop that iterates containers (in `fetch_job.py` — search for `for request in job.requests` or similar). Before dispatching each container:

```python
while job.paused:
    await asyncio.sleep(0.5)
    if job.cancelled:
        return  # cancel beats pause
```

In-flight containers run to completion; only new dispatches are gated.

- [ ] **Step 4: Test the endpoints**

```python
# agent/tests/test_routers_jobs.py — add to existing file
def test_pause_and_resume_flip_job_flag(client, sample_job):
    r = client.post(f"/jobs/{sample_job.id}/pause", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert r.json()["paused"] is True
    assert sample_job.paused is True

    r = client.post(f"/jobs/{sample_job.id}/resume", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert r.json()["paused"] is False
    assert sample_job.paused is False


def test_pause_404_when_job_missing(client):
    r = client.post("/jobs/nope/pause", headers=AUTH_HEADERS)
    assert r.status_code == 404
```

Match the existing fixture style (`client`, `sample_job`, `AUTH_HEADERS`) from the rest of the file — read it first to see the actual names.

- [ ] **Step 5: Run tests**

```bash
cd agent && python -m pytest tests/test_routers_jobs.py -v -k "pause"
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agent/services/job_manager/__init__.py agent/routers/jobs.py agent/services/job_manager/fetch_job.py agent/tests/test_routers_jobs.py
git commit -m "feat(agent): pause/resume endpoints with dispatcher pause gate"
```

---

### Task 17: Add progress strip to status-tabs row (Fetching state)

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js` — fetching toolbar
- Modify: `app/assets/css/styles.css` — progress strip styles

- [ ] **Step 1: Locate the existing fetching toolbar render**

Run: `Grep "v2FetchToolbar\|fetchProgress\|fetching toolbar" app/assets/js/tools/merge/merge-v2.js -n`

Find where the Fetching state renders its progress indicator (likely a meta line like `"${done} / ${total} fetched · ${failedCount} failed"` at `merge-v2.js:2460-2462`).

- [ ] **Step 2: Replace the fetching toolbar with the mockup's full strip layout**

Copy the `.progress-strip` HTML from `app/mockups/merge-filters.html` (search the mockup for `progressStrip` and `progress-bar`). Render it in the Fetching state alongside the status tabs (which during fetch should NOT show All/Errors/Queued — those don't apply yet).

```js
function renderFetchingStrip() {
  const done = v2State.fetchProgress || 0;
  const total = v2State.fetchTotal || v2State.rows.length;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  const elapsed = v2State.fetchStartedAt ? Date.now() - v2State.fetchStartedAt : 0;
  const msPerRow = done > 0 ? elapsed / done : 0;
  const remainingMs = (total - done) * msPerRow;
  const paused = !!v2State.fetchPaused;

  return `
    <div class="progress-strip">
      ${paused ? '⏸' : '<div class="spinner"></div>'}
      <span class="progress-main">${done} / ${total}</span>
      <div class="progress-bar"><div style="width:${pct}%"></div></div>
      <span class="progress-stat"><strong>${fmtTime(elapsed)}</strong> elapsed</span>
      <span class="sep">·</span>
      <span class="progress-stat">~<strong>${fmtTime(remainingMs)}</strong> left</span>
      <span class="sep">·</span>
      <span class="progress-stat"><strong>~${(msPerRow/1000).toFixed(1)}s</strong>/row</span>
    </div>
    <button class="pause-btn" onclick="window.v2TogglePauseFetch()">${paused ? '▶ Resume' : '⏸ Pause'}</button>
    <button class="cancel-btn" onclick="window.v2CancelFetch()">✕ Cancel</button>
  `;
}

function fmtTime(ms) {
  const s = Math.max(0, Math.round(ms / 1000));
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2,'0')}:${String(s % 60).padStart(2,'0')}`;
}
```

Wire `renderFetchingStrip()` into the Fetching state's render. Tick a setInterval every 1s while fetching to refresh elapsed/ETA — clear it when transitioning out of fetching.

- [ ] **Step 3: Track `fetchStartedAt` and `fetchPaused`**

In `v2ClickFetch` / fetch-start handlers, set `v2State.fetchStartedAt = Date.now()`. Reset on Cancel and on state transition.

- [ ] **Step 4: Implement `v2TogglePauseFetch` and `v2CancelFetch`**

```js
window.v2TogglePauseFetch = async function () {
  if (!v2State.jobId) return;
  if (v2State.fetchPaused) {
    await agentBridge.resumeJob(v2State.jobId);
    v2State.fetchPaused = false;
  } else {
    await agentBridge.pauseJob(v2State.jobId);
    v2State.fetchPaused = true;
  }
  rerenderFetchingToolbar();
};

window.v2CancelFetch = async function () {
  if (!v2State.jobId) return;
  if (!confirm('Cancel the fetch? Rows already fetched keep their data; queued rows stay queued.')) return;
  await agentBridge.cancelJob(v2State.jobId);
  finalizeFetch({ cancelled: true });
};
```

- [ ] **Step 5: Add `pauseJob` / `resumeJob` to `agentBridge`**

Open `app/assets/js/shared/agent-client.js`, add (matching the existing `cancelJob` pattern):

```js
async pauseJob(jobId) {
  return this._post(`/jobs/${jobId}/pause`);
},
async resumeJob(jobId) {
  return this._post(`/jobs/${jobId}/resume`);
},
```

- [ ] **Step 6: CSS — progress strip + pause/cancel buttons**

Copy from `app/mockups/merge-filters.html`:

```css
.progress-strip {
  display: flex; align-items: center; gap: 12px;
  padding: 10px 16px;
  background: #fff7ed; border: 1px solid #fed7aa; border-radius: 10px;
  font-size: 13px; color: #9a3412; flex: 1;
}
.progress-strip .progress-main { font-weight: 700; color: #7c2d12; flex-shrink: 0; }
.progress-strip .progress-bar { flex: 1; height: 8px; background: #ffedd5; border-radius: 999px; overflow: hidden; min-width: 120px; }
.progress-strip .progress-bar > div { height: 100%; background: #ea580c; transition: width 200ms; }
.progress-strip .progress-stat { color: #9a3412; font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 11.5px; flex-shrink: 0; }
.progress-strip .progress-stat strong { color: #7c2d12; }
.progress-strip .sep { color: #fed7aa; flex-shrink: 0; }

.pause-btn { background: #fff; color: #334155; border: 1px solid #cbd5e1; padding: 8px 14px; font-size: 13px; font-weight: 600; border-radius: 8px; cursor: pointer; }
.pause-btn:hover { background: #f1f5f9; border-color: #94a3b8; }
.cancel-btn { background: #fff; color: #dc2626; border: 1px solid #fca5a5; padding: 8px 14px; font-size: 13px; font-weight: 600; border-radius: 8px; cursor: pointer; }
.cancel-btn:hover { background: #fef2f2; border-color: #dc2626; }
```

- [ ] **Step 7: Manual verify**

Run a fetch with 20+ containers. Confirm:
- Live elapsed timer ticks every second
- ETA computes once the first few rows finish
- Pause button stops the dispatcher (next container doesn't start; in-flight finishes); label flips to "▶ Resume"
- Resume continues
- Cancel terminates and moves to Ready state with whatever's done

- [ ] **Step 8: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js app/assets/js/shared/agent-client.js app/assets/css/styles.css
git commit -m "feat(merge): progress strip with timer + pause/cancel during fetch"
```

---

## Phase 7 — Build & test gate (local-only install)

### Task 18: Bump VERSION + run local build (no GitHub release)

**Files:**
- Modify: `desktop/VERSION`

- [ ] **Step 1: Bump VERSION**

```bash
echo "2.77.0" > desktop/VERSION
```

- [ ] **Step 2: Confirm pre-build JS gate passes**

```bash
node desktop/check-js.js
```
Expected: exit 0, "JS syntax OK" for every checked file. If fails, fix syntax before continuing.

- [ ] **Step 3: Run `runbuild.bat` (NOT `build-all.bat`)**

Per `MEMORY.md` user feedback, `runbuild.bat` is the non-interactive sibling that doesn't pause/block. From an empty stdin:

```powershell
Start-Process -FilePath "C:\Users\Joseph\Desktop\NGL ACCOUNTING SERVICE\desktop\runbuild.bat" -Wait -NoNewWindow -RedirectStandardInput "NUL"
```

Or simply run `desktop/runbuild.bat` from the user's terminal. Wait for completion. The output is `desktop/dist/win-unpacked/NGL Accounting.exe` and an installer `.exe`.

- [ ] **Step 4: STOP HERE — do NOT publish a GitHub release**

The user wants this version local-install-only for testing. Co-workers will keep auto-updating from the previous GitHub release (v2.76.3) until the user explicitly says to ship.

Do NOT run any of:
- `gh release create`
- `gh release upload`
- `git push --tags`

Tell the user:
> "Local installer built at `desktop/dist/NGL-Accounting-Setup-2.77.0.exe`. Run it on your machine to install. Co-workers' apps will not see this version. When you've finished testing and want to ship, tell me and I'll publish the GH release."

- [ ] **Step 5: Commit version bump + code changes**

```bash
git add desktop/VERSION
git commit -m "chore(release): bump VERSION to 2.77.0 — vans + filtering + queue UX (test build)"
```

- [ ] **Step 6: Push to remote (so the user can checkout from another machine if needed)**

```bash
git push
```

But again — **no `gh release create`**.

---

## Self-review checklist

Before handing off, the implementer should confirm:

- [ ] All `parseInvType` cases (M / E / W / V / unknown) covered in both frontend (`utils.js`) and backend (`_tms_pod_fallback` and `_is_van_row`)
- [ ] `routingDecisionFor`, `willChipFor`, `routingTypeFilterTabs`, `routingSummaryBand` all include `van`
- [ ] `processQueuedRetries` no longer awaits in a per-row loop — single batched `fetchMissing` call
- [ ] Master checkbox uses the same `getCurrentlyVisibleRows` pipeline as the renderer
- [ ] Pause endpoint is gated behind `require_auth` like the rest of `/jobs/*`
- [ ] Cancel button hits the existing cancel endpoint (don't reinvent it)
- [ ] No `gh release create` ran. Co-workers' auto-updater still points at v2.76.3.
- [ ] Mockup at `app/mockups/merge-filters.html` was the visual reference for every UI change

## Acceptance test (manual, post-build)

After installing the local `.exe`:

1. **Trailer van routing** — drop May errors workbook. 25 rows should show `Vans` chip count. Each shows orange `TMS Docs` Will Fetch pill. Filename in merged output uses `INV#_WO#`.
2. **Batched queued retries** — during a running fetch, click "Try Again" on 3+ error rows via the sidebar. Wait for fetch to settle. Observe ONE batched fetch job for all queued retries (not N sequential jobs).
3. **Filter system** — Customer dropdown lists customers A–Z with counts. Click `Vans` chip + `NVH USA, INC` → table shows only NVH van rows. Click CUSTOMER header → rows sort A–Z. Click again → Z–A. Click again → cleared.
4. **Master checkbox** — Errors tab → master checkbox → all errors uncheck. Switch to All tab → unchecked errors stay unchecked. Continue to Merge count excludes them.
5. **Read-only sidebar** — start a fetch with 20+ rows. Click any row → sidebar opens with header/banner/customer/what's-happening sections; Retry / Skip / Upload all disabled with tooltips.
6. **Pause / Cancel** — during a fetch, click Pause. In-flight container finishes; no new ones start. Click Resume → continues. Click Cancel → confirms, terminates, lands in Ready state.
7. **Auto-updater gate** — co-worker's machine still reports `2.76.3` (no auto-update triggered).
