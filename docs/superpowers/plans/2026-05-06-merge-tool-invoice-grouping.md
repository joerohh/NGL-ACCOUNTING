# Merge Tool — Invoice Grouping Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the merge tool from silently dropping invoice rows that share a container with another row. Switch the unit of work from container to invoice, name outputs with the new INV# → WO# → container fallback, fix both the v1 production tool and the v2 Beta tool, then ship via the full rebuild pipeline.

**Architecture:** Pure frontend change — three JS files (`shared/utils.js`, `tools/merge/merge.js`, `tools/merge/merge-v2.js`) plus one CSS rule. No agent / Python / database / API changes. v1 gets the full fix end-to-end (parser → dedup → filename → UI). v2 gets the parser + validator + UI changes; its filename logic activates when v2's M4 (Merging) milestone lands and uses the same shared helper introduced here.

**Tech Stack:** Vanilla JS ES modules · global `XLSX` from CDN · existing `findColumnKey` + `normalizeHeader` from `shared/utils.js` · existing v2 `val-badge` CSS scoped to `#mergeToolViewV2`. Manual smoke verification (no JS test framework in this codebase).

**Spec:** [`docs/superpowers/specs/2026-05-06-merge-tool-invoice-grouping-design.md`](../specs/2026-05-06-merge-tool-invoice-grouping-design.md)

**Mockup:** [`app/mockups/merge-v2-invoice-grouping-mockup.html`](../../../app/mockups/merge-v2-invoice-grouping-mockup.html)

---

## File Structure

| File | Action | Why |
|---|---|---|
| `app/assets/js/shared/utils.js` | Modify | Add `workOrderNumber` to `CSV_ALIASES`; export `WO_ALIASES` shortcut; add `getDatePrefix()` and `buildMergedFilename(row, datePrefix)` shared helpers |
| `app/assets/js/tools/merge/merge.js` | Modify | Read WO# from Excel; remove container dedup; dedupe by INV# only; use shared `buildMergedFilename()`; UI tweaks (prominent INV#, yellow Verify banner) |
| `app/assets/js/tools/merge/merge-v2.js` | Modify | Read WO# in `parseExcelFile()`; rewrite `validateRows()` (dedupe by INV# only, drop `dup-diff-inv`); update `miss-inv` message + checkbox default; new yellow VERIFY badge; optional WO# column in Review table |
| `app/assets/css/styles.css` | Modify (append one rule) | New `.val-badge.warn` class (yellow) for v2's VERIFY state |
| `desktop/VERSION` | Modify | Bump `2.46` → `2.47` |
| `desktop/package.json` | Auto via `node bump-version.js` | electron-builder reads version from here |

The agent (`agent/`), `state.js`, `state` shape, `agent-bridge.js`, all routers/services, and the SQLite database are untouched.

---

## Sample test files

Already present in `docs/`:

- **`docs/no sav.xlsx`** — the file that triggered this fix. **NGL INVOICE** sheet has 110 invoice rows for 100 unique containers. The 10 known overlaps (CONAIR drayage pattern + 1 True Value) are documented in [the spec's Background section](../specs/2026-05-06-merge-tool-invoice-grouping-design.md). Headers used: `EQUIPMENT` (container), `INV #` (invoice), `WO #` (work order), `NAME` (customer).
- **`docs/NGL INVOICE 05.05.2026 (1).xlsx`** — clean batch with no shared-container rows; useful as the "happy path" smoke test (regression check that the changes don't break a normal batch).
- **`docs/idea nouva weekly 04.13-04.19_formatted.xlsx`** — second clean batch; also useful for regression.

For tasks that need a hand-crafted edge case (true exact duplicate, missing INV#), the implementer can copy any row in Excel and either re-paste it (true dup) or clear the `INV #` cell (miss-inv).

---

## How to test each task

This codebase has no JS test framework. Verification is manual via dev mode + DevTools console.

```
cd desktop
npm start          # launches Electron in dev mode pointing at app/
```

Inside the app:

1. **For v1 verification:** Settings → toggle **Merge Tool — Beta** OFF. Switch to the Merge tool (sidebar). Drop `docs/no sav.xlsx`.
2. **For v2 verification:** Settings → toggle **Merge Tool — Beta** ON. Switch to the Merge tool. Drop `docs/no sav.xlsx`.
3. Open DevTools (Ctrl+Shift+I) for inline checks.

Each task's **Verify** step says exactly what to check.

---

## Task 1: Add WO# alias + shared filename helpers in `utils.js`

**Files:**
- Modify: `app/assets/js/shared/utils.js:48-65`

- [ ] **Step 1: Add `workOrderNumber` entry to `CSV_ALIASES`**

In `app/assets/js/shared/utils.js`, find the `CSV_ALIASES` object (around line 48). Add a new entry between `containerNumber` and `customerName`:

```js
export const CSV_ALIASES = {
  invoiceNumber:   ['invoicenumber', 'invoice', 'invoiceid', 'invoiceno', 'inv', 'invno', 'invnumber', 'invnum', 'invid', 'docnumber', 'docno', 'invoicenum'],
  containerNumber: ['containernumber', 'container', 'containerid', 'containerno', 'cont', 'contno', 'contnumber', 'cntr', 'cntrnumber', 'cntrno', 'cntrid', 'ctr', 'ctrno', 'ctrnumber', 'equipment', 'equipmentnumber', 'equipmentno', 'equipmentid', 'eqno', 'eqnumber'],
  workOrderNumber: ['workordernumber', 'workorder', 'workorderno', 'workorderid', 'wo', 'wono', 'wonumber', 'woid', 'wonum'],
  customerName:    ['customername', 'customer', 'name', 'client', 'clientname', 'companyname', 'company'],
  // ...rest unchanged
```

- [ ] **Step 2: Add `WO_ALIASES` shortcut export**

Find the convenience shortcuts block (around line 64):

```js
// Convenience shortcuts used by the Merge Tool
export const CONTAINER_ALIASES = CSV_ALIASES.containerNumber;
export const INVOICE_ALIASES   = CSV_ALIASES.invoiceNumber;
```

Add a third line right below `INVOICE_ALIASES`:

```js
export const WO_ALIASES        = CSV_ALIASES.workOrderNumber;
```

- [ ] **Step 3: Add `getDatePrefix()` and `buildMergedFilename()` helpers**

Append the following block at the end of `utils.js` (after `findColumnKey`):

```js
// ── Merge filename helpers — shared between merge.js (v1) and merge-v2.js (v2 in M4) ──

/** Returns "MM.DD" for today (e.g., "05.06"). */
export function getDatePrefix() {
  const d = new Date();
  return String(d.getMonth() + 1).padStart(2, '0') + '.' + String(d.getDate()).padStart(2, '0');
}

/**
 * Build the filename for a merged-output PDF.
 * Pattern: {datePrefix}_{key}_{container}_merged.pdf
 *   - key = INV# if present
 *   - else WO# if present
 *   - else (omitted entirely — collapses to {datePrefix}_{container}_merged.pdf)
 *
 * Both INV# and WO# are trimmed; empty strings count as "missing".
 *
 * @param {{containerNumber: string, invoiceNumber?: string, workOrderNumber?: string}} row
 * @param {string} datePrefix - typically from getDatePrefix()
 * @returns {string}
 */
export function buildMergedFilename(row, datePrefix) {
  const inv = (row.invoiceNumber || '').trim();
  const wo  = (row.workOrderNumber || '').trim();
  const key = inv || wo;
  if (key) return `${datePrefix}_${key}_${row.containerNumber}_merged.pdf`;
  return `${datePrefix}_${row.containerNumber}_merged.pdf`;
}
```

- [ ] **Step 4: Verify in DevTools console**

Restart `npm start` (Ctrl+C if running, then `npm start`). Open DevTools console in the running app. Paste:

```js
import('./assets/js/shared/utils.js').then(m => {
  console.log('WO_ALIASES:', m.WO_ALIASES);
  console.log('match WO #:', m.findColumnKey(['Container', 'WO #', 'Inv #'], m.WO_ALIASES));
  console.log('match Workorder No:', m.findColumnKey(['Workorder No', 'CNTR'], m.WO_ALIASES));
  console.log('match Work Order:', m.findColumnKey(['Work Order'], m.WO_ALIASES));
  console.log('inv only:', m.buildMergedFilename({containerNumber:'AAAU1234567', invoiceNumber:'INV-1'}, '05.06'));
  console.log('wo fallback:', m.buildMergedFilename({containerNumber:'AAAU1234567', workOrderNumber:'WO-9'}, '05.06'));
  console.log('container only:', m.buildMergedFilename({containerNumber:'AAAU1234567'}, '05.06'));
});
```

Expected output:

```
WO_ALIASES: (9) ['workordernumber', ...]
match WO #: WO #
match Workorder No: Workorder No
match Work Order: Work Order
inv only: 05.06_INV-1_AAAU1234567_merged.pdf
wo fallback: 05.06_WO-9_AAAU1234567_merged.pdf
container only: 05.06_AAAU1234567_merged.pdf
```

- [ ] **Step 5: Commit**

```bash
git add app/assets/js/shared/utils.js
git commit -m "feat(shared): add WO# aliases + buildMergedFilename helper"
```

---

## Task 2: v1 — parse WO# from Excel; update status text

**Files:**
- Modify: `app/assets/js/tools/merge/merge.js:7-8` (import line)
- Modify: `app/assets/js/tools/merge/merge.js:127-211` (`handleExcelFile`)

- [ ] **Step 1: Add `WO_ALIASES` to the imports**

At the top of `app/assets/js/tools/merge/merge.js`, the existing import block is:

```js
import {
  uid, fmtSize, escHtml, readAsArrayBuffer, triggerDownload,
  normalizeHeader, findColumnKey, CONTAINER_ALIASES, INVOICE_ALIASES,
} from '../../shared/utils.js';
```

Replace with:

```js
import {
  uid, fmtSize, escHtml, readAsArrayBuffer, triggerDownload,
  normalizeHeader, findColumnKey, CONTAINER_ALIASES, INVOICE_ALIASES, WO_ALIASES,
} from '../../shared/utils.js';
```

- [ ] **Step 2: Detect the WO# column in `handleExcelFile`**

Find the block (around line 147-149):

```js
    // Fuzzy column detection
    const containerKey = findColumnKey(headers, CONTAINER_ALIASES);
    const invoiceKey   = findColumnKey(headers, INVOICE_ALIASES);
```

Add a third line below:

```js
    // Fuzzy column detection
    const containerKey = findColumnKey(headers, CONTAINER_ALIASES);
    const invoiceKey   = findColumnKey(headers, INVOICE_ALIASES);
    const woKey        = findColumnKey(headers, WO_ALIASES);
```

- [ ] **Step 3: Add a log line announcing the WO column when matched**

Right after the existing `if (invoiceKey) { ... } else { ... }` block (around line 159-164), add:

```js
    if (woKey) {
      addLog('info', `Matched work-order column: "${woKey}"`);
    }
```

(No warning when `woKey` is null — WO# is fully optional.)

- [ ] **Step 4: Capture `workOrderNumber` on each parsed row**

In the row-parsing loop (around line 169-177), find:

```js
      parsed.push({
        containerNumber: cn,
        invoiceNumber: invoiceKey ? String(row[invoiceKey] || '').trim() : '',
      });
```

Replace with:

```js
      parsed.push({
        containerNumber: cn,
        invoiceNumber: invoiceKey ? String(row[invoiceKey] || '').trim() : '',
        workOrderNumber: woKey ? String(row[woKey] || '').trim() : '',
      });
```

(Don't change the dedup logic in this task — that's Task 3.)

- [ ] **Step 5: Verify the WO# column is being read**

Restart `npm start`. In v1 (Beta toggle OFF), drop `docs/no sav.xlsx`. Open DevTools console and run:

```js
state.excelRows.slice(0, 3)
```

Expected: each entry shows three fields, including a non-empty `workOrderNumber`. Example:

```js
[
  {containerNumber: 'TEMU7007065', invoiceNumber: 'PE26050025F', workOrderNumber: 'PX2605040005'},
  {containerNumber: 'SEGU6736718', invoiceNumber: 'PE26050024F', workOrderNumber: 'PX2605040006'},
  {containerNumber: 'ONEU0081837', invoiceNumber: 'LM26050141F', workOrderNumber: 'LM2604220018'},
]
```

The Status Log (collapsible at the bottom) should show: `Matched work-order column: "WO #"`.

- [ ] **Step 6: Commit**

```bash
git add app/assets/js/tools/merge/merge.js
git commit -m "feat(merge/v1): parse optional WO# column from Excel"
```

---

## Task 3: v1 — replace container dedup with INV#-only dedup

**Files:**
- Modify: `app/assets/js/tools/merge/merge.js:166-202` (the parse loop + status text)

- [ ] **Step 1: Replace the parse-and-dedup block**

In `handleExcelFile`, find the block starting `// Parse rows — deduplicate by container number` (around line 166):

```js
    // Parse rows — deduplicate by container number
    const seen = new Set();
    const parsed = [];
    for (const row of rows) {
      const cn = String(row[containerKey] || '').trim();
      if (!cn || seen.has(cn.toLowerCase())) continue;
      seen.add(cn.toLowerCase());
      parsed.push({
        containerNumber: cn,
        invoiceNumber: invoiceKey ? String(row[invoiceKey] || '').trim() : '',
        workOrderNumber: woKey ? String(row[woKey] || '').trim() : '',
      });
    }
```

Replace with:

```js
    // Parse rows — keep every row with a container number.
    // Dedup is now by INV# only (see invoice-grouping spec 2026-05-06).
    const seenInv = new Map();   // invLower → original rowIndex (1-based for messages)
    const parsed = [];
    const skipped = [];
    let rowIndex = 0;
    for (const row of rows) {
      rowIndex++;
      const cn = String(row[containerKey] || '').trim();
      if (!cn) continue;          // a row without a container is unusable
      const inv = invoiceKey ? String(row[invoiceKey] || '').trim() : '';
      const wo  = woKey      ? String(row[woKey]      || '').trim() : '';

      // Same INV# as a previous row? Skip the duplicate.
      if (inv) {
        const invLower = inv.toLowerCase();
        const priorIdx = seenInv.get(invLower);
        if (priorIdx !== undefined) {
          skipped.push({ rowIndex, containerNumber: cn, invoiceNumber: inv, priorIndex: priorIdx });
          continue;
        }
        seenInv.set(invLower, rowIndex);
      }
      // Rows with no INV# are never deduplicated (they get a Verify flag in the UI).

      parsed.push({
        containerNumber: cn,
        invoiceNumber: inv,
        workOrderNumber: wo,
      });
    }
```

- [ ] **Step 2: Update the status report to show counts and skipped rows**

Immediately after the loop, find the block (around line 184-202):

```js
    state.excelRows = parsed;
    addLog('success', `Parsed ${parsed.length} container numbers from "${file.name}"`);
    parsed.slice(0, 5).forEach(r => {
      const inv = r.invoiceNumber ? ` (Inv: ${r.invoiceNumber})` : '';
      addLog('info', `  → ${r.containerNumber}${inv}`);
    });
    if (parsed.length > 5) addLog('info', `  → ...and ${parsed.length - 5} more`);

    // Update drop zone UI
    const dz   = document.getElementById('excelDropZone');
    const drop = document.getElementById('excelDropContent');
    const loaded = document.getElementById('excelLoadedState');
    dz.classList.add('has-file');
    drop.style.display = 'none';
    loaded.style.display = 'flex';
    document.getElementById('excelFileName').textContent = file.name;
    const invCount = parsed.filter(r => r.invoiceNumber).length;
    document.getElementById('excelFileSub').textContent =
      `${parsed.length} containers` + (invCount ? ` · ${invCount} invoice numbers` : '');
```

Replace with:

```js
    state.excelRows = parsed;
    state.skippedRows = skipped;     // expose for renderFailureReport in Task 5
    const uniqueContainers = new Set(parsed.map(r => r.containerNumber.toLowerCase())).size;
    const missInv = parsed.filter(r => !r.invoiceNumber).length;
    addLog('success', `Parsed ${parsed.length} invoice row${parsed.length !== 1 ? 's' : ''} (${uniqueContainers} unique container${uniqueContainers !== 1 ? 's' : ''})`);
    if (skipped.length > 0) {
      addLog('warning', `Skipped ${skipped.length} duplicate row${skipped.length !== 1 ? 's' : ''} (same INV# as another row):`);
      skipped.forEach(s => addLog('warning', `  → row ${s.rowIndex}: INV ${s.invoiceNumber} (already seen at row ${s.priorIndex})`));
    }
    if (missInv > 0) {
      addLog('warning', `${missInv} row${missInv !== 1 ? 's' : ''} missing INV# — flagged for verification`);
    }
    parsed.slice(0, 5).forEach(r => {
      const inv = r.invoiceNumber ? ` (Inv: ${r.invoiceNumber})` : '';
      addLog('info', `  → ${r.containerNumber}${inv}`);
    });
    if (parsed.length > 5) addLog('info', `  → ...and ${parsed.length - 5} more`);

    // Update drop zone UI
    const dz   = document.getElementById('excelDropZone');
    const drop = document.getElementById('excelDropContent');
    const loaded = document.getElementById('excelLoadedState');
    dz.classList.add('has-file');
    drop.style.display = 'none';
    loaded.style.display = 'flex';
    document.getElementById('excelFileName').textContent = file.name;
    document.getElementById('excelFileSub').textContent =
      `${parsed.length} invoice${parsed.length !== 1 ? 's' : ''} · ${uniqueContainers} unique container${uniqueContainers !== 1 ? 's' : ''}`;
```

- [ ] **Step 3: Initialize `state.skippedRows` to an empty array on Excel removal**

Find the `removeExcel()` function (around line 213-226) and add `state.skippedRows = [];` right after `state.mergeResults = [];`:

```js
function removeExcel() {
  state.excelRows = [];
  state.skippedRows = [];
  // ...rest unchanged
```

Also update `clearAll()` (around line 879-914) to add the same line right after `state.mergeResults = [];`:

```js
function clearAll() {
  state.pdfs         = [];
  state.excelRows    = [];
  state.skippedRows  = [];
  state.mergeResults = [];
  // ...rest unchanged
```

- [ ] **Step 4: Verify against `docs/no sav.xlsx`**

Restart `npm start`. With Beta toggle OFF, drop `docs/no sav.xlsx`. In DevTools console:

```js
console.log('rows:', state.excelRows.length);                              // expected: 110
console.log('unique:', new Set(state.excelRows.map(r => r.containerNumber.toLowerCase())).size);  // expected: 100
console.log('skipped:', state.skippedRows);                                // expected: []  (no exact INV# repeats in this file)

// CONAIR pair check — both invoices should be present
const conair = state.excelRows.filter(r => r.containerNumber === 'CAAU7378645');
console.log('CAAU7378645 invoices:', conair.map(r => r.invoiceNumber));
// expected: ['PM26050063F', 'PM26050062F']  (or similar — both present)
```

Status Log should read: `Parsed 110 invoice rows (100 unique containers)`. The drop-zone subtitle below the file name should read: `110 invoices · 100 unique containers`.

- [ ] **Step 5: Verify true-duplicate handling with a hand-crafted file**

Open `docs/no sav.xlsx` in Excel, copy any data row, paste it as an exact duplicate of itself (same INV#, same container, same WO#). Save as `docs/test-exact-dup.xlsx`. Drop it in v1 (Beta OFF).

Expected Status Log:

```
Parsed 110 invoice rows (100 unique containers)
Skipped 1 duplicate row (same INV# as another row):
  → row N: INV {invoiceNumber} (already seen at row M)
```

Delete the test file when done: `del "docs\test-exact-dup.xlsx"`.

- [ ] **Step 6: Commit**

```bash
git add app/assets/js/tools/merge/merge.js
git commit -m "fix(merge/v1): stop dropping rows; dedupe by INV# only"
```

---

## Task 4: v1 — wire shared `buildMergedFilename` into per-container merge

**Files:**
- Modify: `app/assets/js/tools/merge/merge.js:7-8` (import line)
- Modify: `app/assets/js/tools/merge/merge.js:463-520, 553-619` (mergePerContainerSequential + mergePerContainer)
- Modify: `app/assets/js/tools/merge/merge.js:524-527` (delete local `getDatePrefix`)

- [ ] **Step 1: Import `getDatePrefix` and `buildMergedFilename`**

Update the import line at the top of `merge.js`:

```js
import {
  uid, fmtSize, escHtml, readAsArrayBuffer, triggerDownload,
  normalizeHeader, findColumnKey, CONTAINER_ALIASES, INVOICE_ALIASES, WO_ALIASES,
  getDatePrefix, buildMergedFilename,
} from '../../shared/utils.js';
```

- [ ] **Step 2: Delete the local `getDatePrefix` function**

Find and delete the block (around line 524-527):

```js
function getDatePrefix() {
  const d = new Date();
  return String(d.getMonth() + 1).padStart(2, '0') + '.' + String(d.getDate()).padStart(2, '0');
}
```

(The imported helper from utils.js replaces it. Other call sites — `mergeAllInOne`, `mergeByType` — keep working because they call the same name.)

- [ ] **Step 3: Use `buildMergedFilename` in `mergePerContainerSequential`**

Find the line (around line 510) inside `mergePerContainerSequential`:

```js
      const filename = `${datePrefix}_${row.containerNumber}_merged.pdf`;
```

Replace with:

```js
      const filename = buildMergedFilename(row, datePrefix);
```

- [ ] **Step 4: Use `buildMergedFilename` in the parallel `mergePerContainer`**

Find the matching line (around line 589) inside the parallel branch:

```js
      const filename = `${datePrefix}_${row.containerNumber}_merged.pdf`;
```

Replace with:

```js
      const filename = buildMergedFilename(row, datePrefix);
```

- [ ] **Step 5: Verify filenames against `docs/no sav.xlsx`**

Restart `npm start`. With Beta OFF and the agent server running, drop `docs/no sav.xlsx`, then drop the source PDFs from `c:/Users/Joseph/AppData/Local/Programs/ngl-accounting/resources/agent/ngl-agent/output/One to One Merge/` (the 110 files we renamed earlier — *or* a smaller subset for a quick smoke). Click **Run Merge**. When it finishes, in DevTools console:

```js
state.mergeResults.length                          // expected: 110 (or N for the subset)
state.mergeResults.slice(0, 5).map(r => r.filename)
// expected: each starts MM.DD_<INV#>_<container>_merged.pdf
```

Click **Save to Folder** to confirm the agent accepts the new filenames (no errors in Status Log; folder opens in Explorer with all expected files).

- [ ] **Step 6: Commit**

```bash
git add app/assets/js/tools/merge/merge.js
git commit -m "feat(merge/v1): name outputs with INV#/WO# fallback chain"
```

---

## Task 5: v1 — UI: prominent INV# + yellow Verify banner + skipped-row report

**Files:**
- Modify: `app/assets/js/tools/merge/merge.js:313-360` (`renderContainerGroups`)
- Modify: `app/assets/js/tools/merge/merge.js:796-806` (`renderFailureReport`)
- Modify: `app/assets/css/styles.css` (append styles for the new card states)

- [ ] **Step 1: Replace the card header in `renderContainerGroups`**

In `renderContainerGroups` (around line 328-335), find the existing header block:

```js
    return `
      <div class="container-group ${isMatch ? 'matched' : 'unmatched'}">
        <div class="container-group-header">
          <span style="font-size:0.95rem; font-weight:700; color:#0f172a; font-family:monospace; letter-spacing:0.02em;">${escHtml(row.containerNumber)}</span>
          <span style="margin-left:auto; font-size:0.8rem; font-weight:600; color:${isMatch ? '#16a34a' : '#d97706'};">
            ${isMatch ? `${matched.length} file${matched.length !== 1 ? 's' : ''}` : 'No match'}
          </span>
        </div>
```

Replace with:

```js
    const missInv = !row.invoiceNumber;
    const cardClasses = ['container-group'];
    if (isMatch) cardClasses.push('matched'); else cardClasses.push('unmatched');
    if (missInv) cardClasses.push('miss-inv');

    return `
      <div class="${cardClasses.join(' ')}">
        ${missInv ? `<div class="verify-banner">⚠ No invoice number — please check before sending. Will merge with WO# as filename key.</div>` : ''}
        <div class="container-group-header">
          <span style="font-size:0.95rem; font-weight:700; color:#0f172a; font-family:monospace; letter-spacing:0.02em;">${escHtml(row.containerNumber)}</span>
          ${row.invoiceNumber
            ? `<span style="font-size:0.8rem; font-weight:600; color:#475569; margin-left:10px;">INV ${escHtml(row.invoiceNumber)}</span>`
            : ''}
          <span style="margin-left:auto; font-size:0.8rem; font-weight:600; color:${isMatch ? '#16a34a' : '#d97706'};">
            ${isMatch ? `${matched.length} file${matched.length !== 1 ? 's' : ''}` : 'No match'}
          </span>
        </div>
```

- [ ] **Step 2: Remove the now-redundant Invoice # subtitle**

Inside the same return block, find (around line 358):

```js
        ${row.invoiceNumber ? `<div style="font-size:0.75rem; color:#94a3b8; padding:2px 6px;">Invoice #: ${escHtml(row.invoiceNumber)}</div>` : ''}
```

Delete that entire line. (The INV# now lives in the header.)

- [ ] **Step 3: Append CSS for the new banner and miss-inv card state**

Append the following block at the end of `app/assets/css/styles.css`:

```css
/* Merge Tool v1 — Invoice-Grouping Fix (2026-05-06) */
.container-group.miss-inv {
  border-left: 3px solid #f59e0b;
  background: #fffbeb;
}
.container-group .verify-banner {
  background: #fde68a;
  color: #78350f;
  font-size: 0.78rem;
  font-weight: 600;
  padding: 6px 10px;
  border-radius: 6px 6px 0 0;
  border-bottom: 1px solid #fcd34d;
}
```

- [ ] **Step 4: Extend `renderFailureReport` to also surface skipped rows**

Find `renderFailureReport` (around line 796-806):

```js
function renderFailureReport(failures) {
  const rpt = document.getElementById('failureReport');
  const lst = document.getElementById('failureList');
  lst.innerHTML = failures.map(f => `
    <div class="failure-row">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#dc2626" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      <span style="font-family:monospace; color:#dc2626; font-weight:600;">${escHtml(f.containerNumber)}</span>
      <span style="color:#94a3b8; font-size:0.8rem; margin-left:auto;">${escHtml(f.reason)}</span>
    </div>`).join('');
  rpt.style.display = 'block';
}
```

Replace with:

```js
function renderFailureReport(failures) {
  const rpt = document.getElementById('failureReport');
  const lst = document.getElementById('failureList');
  const failureRows = failures.map(f => `
    <div class="failure-row">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#dc2626" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      <span style="font-family:monospace; color:#dc2626; font-weight:600;">${escHtml(f.containerNumber)}</span>
      <span style="color:#94a3b8; font-size:0.8rem; margin-left:auto;">${escHtml(f.reason)}</span>
    </div>`).join('');
  const skippedRows = (state.skippedRows || []).map(s => `
    <div class="failure-row" style="background:#fffbeb;">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#d97706" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
      <span style="font-family:monospace; color:#92400e; font-weight:600;">Row ${s.rowIndex}</span>
      <span style="color:#92400e; font-size:0.8rem; margin-left:8px;">${escHtml(s.containerNumber)}</span>
      <span style="color:#92400e; font-size:0.8rem; margin-left:auto;">Skipped — duplicate of row ${s.priorIndex} (INV ${escHtml(s.invoiceNumber)})</span>
    </div>`).join('');
  lst.innerHTML = failureRows + skippedRows;
  rpt.style.display = (failureRows || skippedRows) ? 'block' : 'none';
}
```

- [ ] **Step 5: Show the failure report even when only skipped rows exist**

In `runAutoMerge` (around line 779-782), find:

```js
  if (failures.length > 0) {
    addLog('warning', `Failures: ${failures.map(f => f.containerNumber).join(', ')}`);
    renderFailureReport(failures);
  }
```

Replace with:

```js
  if (failures.length > 0 || (state.skippedRows && state.skippedRows.length > 0)) {
    if (failures.length > 0) addLog('warning', `Failures: ${failures.map(f => f.containerNumber).join(', ')}`);
    renderFailureReport(failures);
  }
```

- [ ] **Step 6: Verify the UI**

Restart `npm start`. With Beta OFF, drop `docs/no sav.xlsx`. Visual check:

- The container-groups area shows **110 cards** (scrollable). The 10 CONAIR-pattern containers (CAAU7378645, CAIU7597610, DRYU9802611, FFAU1240328, MSBU7332761, MSDU4299142, MSDU6039360, SMCU1303640, TCNU1109244, YMMU4141051) appear **twice each**, with **different INV# in the card header** (e.g., one card shows `CAAU7378645    INV PM26050062F`, the next shows `CAAU7378645    INV PM26050063F`).
- No cards show a yellow Verify banner (because no rows in this file have a missing INV#).

To test the missing-INV case: open `docs/no sav.xlsx` in Excel, clear the `INV #` cell on any one row, save as `docs/test-miss-inv.xlsx`. Drop it. Find that row's card — it should have a yellow background, an orange left border, and the yellow `⚠ No invoice number — please check before sending. Will merge with WO# as filename key.` banner across the top. Delete the test file when done: `del "docs\test-miss-inv.xlsx"`.

To test the skipped-rows report: re-create `docs/test-exact-dup.xlsx` from Task 3 step 5. Drop it. Run merge with any PDFs in queue (or none — just to trigger the report). The Failure Report section should show the duplicate row in yellow with text like `Row N · CAAU7378645 · Skipped — duplicate of row M (INV PM...)`.

- [ ] **Step 7: Commit**

```bash
git add app/assets/js/tools/merge/merge.js app/assets/css/styles.css
git commit -m "feat(merge/v1): prominent INV# + Verify banner + skipped-rows report"
```

---

## Task 6: v2 — parse WO# in `parseExcelFile`

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js:139-199` (`parseExcelFile`)
- Modify: `app/assets/js/tools/merge/merge-v2.js:11-19` (`v2State` row shape comment)

- [ ] **Step 1: Detect WO# column inside `parseExcelFile`**

Find the column-detection block (around line 167-169):

```js
  const headers = Object.keys(sheetRows[0]);
  const containerKey = findColumnKey(headers, CSV_ALIASES.containerNumber);
  const invoiceKey   = findColumnKey(headers, CSV_ALIASES.invoiceNumber);
  const customerKey  = findCustomerColumn(headers);
```

Add a `woKey` line right below `invoiceKey`:

```js
  const headers = Object.keys(sheetRows[0]);
  const containerKey = findColumnKey(headers, CSV_ALIASES.containerNumber);
  const invoiceKey   = findColumnKey(headers, CSV_ALIASES.invoiceNumber);
  const woKey        = findColumnKey(headers, CSV_ALIASES.workOrderNumber);
  const customerKey  = findCustomerColumn(headers);
```

- [ ] **Step 2: Capture `workOrderNumber` on each row**

In the row-parsing loop (around line 178-191), find:

```js
    rows.push({
      rowNum: i + 2,            // sheet row 1 is headers, so first data row → 2
      containerNumber: cn,
      invoiceNumber: invoiceKey ? String(r[invoiceKey] || '').trim() : '',
      customer: customerKey ? String(r[customerKey] || '').trim() : '',
      // selected/status/statusReason filled in by validateRows()
      selected: false,
      status: 'ok',
      statusReason: '',
    });
```

Replace with:

```js
    rows.push({
      rowNum: i + 2,            // sheet row 1 is headers, so first data row → 2
      containerNumber: cn,
      invoiceNumber: invoiceKey ? String(r[invoiceKey] || '').trim() : '',
      workOrderNumber: woKey ? String(r[woKey] || '').trim() : '',
      customer: customerKey ? String(r[customerKey] || '').trim() : '',
      // selected/status/statusReason filled in by validateRows()
      selected: false,
      status: 'ok',
      statusReason: '',
    });
```

- [ ] **Step 3: Update the return value to include `woKey`**

Find the return at the end of `parseExcelFile` (around line 198):

```js
  return { rows, headers, containerKey, invoiceKey, customerKey };
```

Replace with:

```js
  return { rows, headers, containerKey, invoiceKey, woKey, customerKey };
```

- [ ] **Step 4: Update the row-shape comment in `v2State`**

Find the comment in `v2State` (around line 17):

```js
  rows: [],                  // Array<{rowNum, containerNumber, invoiceNumber, customer, selected, status, statusReason}>
```

Replace with:

```js
  rows: [],                  // Array<{rowNum, containerNumber, invoiceNumber, workOrderNumber, customer, selected, status, statusReason}>
```

- [ ] **Step 5: Verify in DevTools console**

Restart `npm start`. With Beta toggle ON, switch to Merge tool, drop `docs/no sav.xlsx`. In DevTools console:

```js
v2State.rows.slice(0, 3).map(r => ({c: r.containerNumber, i: r.invoiceNumber, w: r.workOrderNumber}))
// expected: each row has all three fields populated
```

Drop a clean file like `docs/NGL INVOICE 05.05.2026 (1).xlsx` next — confirm `workOrderNumber` is present (or empty string if that file lacks the column — which it does have, per the spec).

- [ ] **Step 6: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge/v2): parse optional WO# column in parseExcelFile"
```

---

## Task 7: v2 — rewrite `validateRows()` to dedupe by INV# only

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js:201-244` (`validateRows`)

- [ ] **Step 1: Replace the entire `validateRows` function**

Find the function (lines 201-244):

```js
function validateRows(rows) {
  // Walk in Excel order. Track first occurrence of each container.
  const firstSeen = new Map();    // containerLower → { rowNum, invoiceNumber }
  for (const row of rows) {
    const key = row.containerNumber.toLowerCase();
    const prior = firstSeen.get(key);

    if (!prior) {
      firstSeen.set(key, { rowNum: row.rowNum, invoiceNumber: row.invoiceNumber });
      if (!row.invoiceNumber) {
        row.status = 'miss-inv';
        row.statusReason = "No invoice number — we'll search by container instead";
        row.selected = true;
      } else {
        row.status = 'ok';
        row.statusReason = '';
        row.selected = true;
      }
      continue;
    }

    // This row's container was seen earlier.
    // It's a same-content dup if either: both rows have the same invoice (case/whitespace-insensitive)
    // OR both rows have no invoice number at all (truly identical content).
    const bothMissing = !row.invoiceNumber && !prior.invoiceNumber;
    const sameInv = bothMissing || (
      row.invoiceNumber &&
      prior.invoiceNumber &&
      row.invoiceNumber.trim().toLowerCase() === prior.invoiceNumber.trim().toLowerCase()
    );

    if (sameInv) {
      row.status = 'dup-same-inv';
      row.statusReason = bothMissing
        ? `Same container as row ${prior.rowNum} — both have no invoice number, will be skipped`
        : `Exact duplicate of row ${prior.rowNum} — will be skipped`;
    } else {
      row.status = 'dup-diff-inv';
      row.statusReason = `Same container as row ${prior.rowNum}, but different invoice number`;
    }
    row.selected = false;          // duplicates default to unchecked; user can opt back in
  }
  return rows;
}
```

Replace with:

```js
function validateRows(rows) {
  // Dedup by INV# only. Container/WO# are NOT used for duplicate detection.
  // (See spec docs/superpowers/specs/2026-05-06-merge-tool-invoice-grouping-design.md §2.)
  const seenInv = new Map();   // invLower → rowNum where it was first seen
  for (const row of rows) {
    const inv = (row.invoiceNumber || '').trim();

    if (!inv) {
      // Soft-flag: keep the row, default-checked, yellow VERIFY badge.
      row.status = 'miss-inv';
      row.statusReason = 'No invoice number — please check before sending. Will merge with WO# as filename key.';
      row.selected = true;
      continue;
    }

    const invLower = inv.toLowerCase();
    const priorRowNum = seenInv.get(invLower);
    if (priorRowNum !== undefined) {
      // True duplicate: same INV# as a previous row.
      row.status = 'dup-same-inv';
      row.statusReason = `Exact duplicate of row ${priorRowNum} (same INV# ${inv}) — will be skipped`;
      row.selected = false;
      continue;
    }

    seenInv.set(invLower, row.rowNum);
    row.status = 'ok';
    row.statusReason = '';
    row.selected = true;
  }
  return rows;
}
```

- [ ] **Step 2: Verify against `docs/no sav.xlsx`**

Restart `npm start`. With Beta ON, drop `docs/no sav.xlsx`. The Review state should land on the **success card** ("All 110 rows checked out") because there are no duplicate INV#s and (assuming all rows have INV#) no miss-inv rows.

In DevTools console:

```js
v2State.rows.length                                           // expected: 110
v2State.rows.filter(r => r.status === 'ok').length            // expected: 110
v2State.rows.filter(r => r.status === 'dup-same-inv').length  // expected: 0
v2State.rows.filter(r => r.status === 'miss-inv').length      // expected: 0
v2State.rows.filter(r => r.selected).length                   // expected: 110

// Spot-check the CONAIR pair (used to be flagged as dup-diff-inv)
const conair = v2State.rows.filter(r => r.containerNumber === 'CAAU7378645');
console.log(conair.map(r => ({inv: r.invoiceNumber, status: r.status, selected: r.selected})));
// expected: both rows show status:'ok', selected:true
```

- [ ] **Step 3: Verify true duplicate handling**

Reuse `docs/test-exact-dup.xlsx` from Task 3 step 5 (or recreate it: copy any row in `no sav.xlsx`, paste as exact duplicate, save as `test-exact-dup.xlsx`). Drop it in v2.

Expected: Review state lands on the **issues card** (yellow header). The duplicate row shows a `Duplicate` badge with text `"Exact duplicate of row N (same INV# ...) — will be skipped"`. Click "All" tab — confirm the duplicate row is unchecked.

Delete the test file: `del "docs\test-exact-dup.xlsx"`.

- [ ] **Step 4: Verify miss-inv handling**

Open `docs/no sav.xlsx`, clear the `INV #` cell on any row, save as `docs/test-miss-inv.xlsx`. Drop it in v2.

Expected: Review issues card shows 1 issue. The affected row has the existing red **Missing Inv #** badge (the yellow one comes in Task 8), with text `"No invoice number — please check before sending. Will merge with WO# as filename key."`. Confirm checkbox is **checked** by default (soft-flag, not hard-skip).

Delete the test file when done.

- [ ] **Step 5: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "fix(merge/v2): dedupe by INV# only; drop dup-diff-inv classification"
```

---

## Task 8: v2 — yellow VERIFY badge for `miss-inv`

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js:372-380` (badge construction in `rowMarkup`)
- Modify: `app/assets/css/styles.css:974-987` (val-badge block — add `.warn` class)

- [ ] **Step 1: Add the `.val-badge.warn` rule in CSS**

In `app/assets/css/styles.css`, find the existing v2 val-badge block (around lines 974-987). Add two new rules right after the `miss` rules:

```css
#mergeToolViewV2 .val-badge.warn  { background: #fef3c7; color: #92400e; border: 1px solid #fde68a; }
#mergeToolViewV2 .val-badge.warn .dot { background: #f59e0b; }
```

The block should look like:

```css
#mergeToolViewV2 .val-badge.ok    { background: #f0fdf4; color: #15803d; border: 1px solid #bbf7d0; }
#mergeToolViewV2 .val-badge.dup   { background: #fef3c7; color: #92400e; border: 1px solid #fde68a; }
#mergeToolViewV2 .val-badge.miss  { background: #fef2f2; color: #b91c1c; border: 1px solid #fca5a5; }
#mergeToolViewV2 .val-badge.warn  { background: #fef3c7; color: #92400e; border: 1px solid #fde68a; }
#mergeToolViewV2 .val-badge .dot { width: 6px; height: 6px; border-radius: 50%; }
#mergeToolViewV2 .val-badge.ok .dot { background: #16a34a; }
#mergeToolViewV2 .val-badge.dup .dot { background: #f59e0b; }
#mergeToolViewV2 .val-badge.miss .dot { background: #dc2626; }
#mergeToolViewV2 .val-badge.warn .dot { background: #f59e0b; }
```

- [ ] **Step 2: Switch the `miss-inv` badge to use the new `.warn` class**

In `merge-v2.js` `rowMarkup` (around lines 372-380), find:

```js
  let badge = '';
  if (row.status === 'miss-inv') {
    badge = `<span class="val-badge miss"><span class="dot"></span>Missing Inv #</span>`;
  } else if (row.status === 'dup-same-inv' || row.status === 'dup-diff-inv') {
    badge = `<span class="val-badge dup"><span class="dot"></span>Duplicate</span>`;
  }
  const reasonLine = row.statusReason
    ? `<div style="font-size:0.72rem; color:${row.status === 'miss-inv' ? '#b91c1c' : '#92400e'}; margin-top:3px;">${escHtml(row.statusReason)}</div>`
    : '';
```

Replace with:

```js
  let badge = '';
  if (row.status === 'miss-inv') {
    badge = `<span class="val-badge warn"><span class="dot"></span>Verify</span>`;
  } else if (row.status === 'dup-same-inv') {
    badge = `<span class="val-badge dup"><span class="dot"></span>Exact dup</span>`;
  }
  const reasonLine = row.statusReason
    ? `<div style="font-size:0.72rem; color:#92400e; margin-top:3px;">${escHtml(row.statusReason)}</div>`
    : '';
```

(`dup-diff-inv` is no longer produced — the conditional is dropped. The reason-line color simplifies to amber since both remaining states use amber-family colors.)

- [ ] **Step 3: Verify visually**

Restart `npm start`. Recreate `docs/test-miss-inv.xlsx` (clear an INV cell). Drop it in v2 (Beta ON).

Expected: the row's Validation column shows a **yellow** badge labeled `Verify`, with the new statusReason text below in amber. The row's checkbox is **checked**. Background of the row is normal (the existing `.row-issue` styling already gives it a subtle yellow tint).

Recreate `docs/test-exact-dup.xlsx`. Drop it in v2.

Expected: the duplicate row's Validation column shows the existing amber **Exact dup** badge (the wording changed from "Duplicate" to "Exact dup", which matches the spec language).

Delete the test files when done.

- [ ] **Step 4: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js app/assets/css/styles.css
git commit -m "feat(merge/v2): yellow Verify badge for miss-inv; rename dup label"
```

---

## Task 9: v2 — optional WO# column in Review table

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js:382-390` (`rowMarkup`)
- Modify: `app/assets/js/tools/merge/merge-v2.js:489-499` (success card table head)
- Modify: `app/assets/js/tools/merge/merge-v2.js:580-593` (issues card table head)
- Modify: `app/assets/js/tools/merge/merge-v2.js:392-398` (`renderTbodyHTML` colspan)

- [ ] **Step 1: Add a helper that detects whether the current rows have any WO#**

At the top of `merge-v2.js`, near the other small helpers (after `validateRows` definition, around line 244), add:

```js
function hasAnyWO() {
  return v2State.rows.some(r => (r.workOrderNumber || '').trim() !== '');
}
```

- [ ] **Step 2: Conditionally render the WO# `<td>` in `rowMarkup`**

In `rowMarkup` (around lines 382-389), find:

```js
  return `<tr class="${trClass}" data-row-num="${row.rowNum}">
    <td class="check-col"><input type="checkbox" class="row-check" ${checkAttr} onchange="window.v2ToggleRow(${row.rowNum}, this.checked)" /></td>
    <td style="color:#94a3b8; font-size:0.8rem;">${row.rowNum}</td>
    <td><span class="mono">${escHtml(row.containerNumber)}</span></td>
    <td>${invDisplay}</td>
    <td>${customerDisplay}</td>
    <td>${badge}${reasonLine}</td>
  </tr>`;
```

Replace with:

```js
  const woCell = hasAnyWO()
    ? `<td>${row.workOrderNumber ? `<span class="mono">${escHtml(row.workOrderNumber)}</span>` : '<span style="color:#cbd5e1;">—</span>'}</td>`
    : '';

  return `<tr class="${trClass}" data-row-num="${row.rowNum}">
    <td class="check-col"><input type="checkbox" class="row-check" ${checkAttr} onchange="window.v2ToggleRow(${row.rowNum}, this.checked)" /></td>
    <td style="color:#94a3b8; font-size:0.8rem;">${row.rowNum}</td>
    <td><span class="mono">${escHtml(row.containerNumber)}</span></td>
    <td>${invDisplay}</td>
    ${woCell}
    <td>${customerDisplay}</td>
    <td>${badge}${reasonLine}</td>
  </tr>`;
```

- [ ] **Step 3: Conditionally render the WO# `<th>` in `renderReviewWithIssues`**

In `renderReviewWithIssues` (around lines 580-593), find:

```js
        <thead>
          <tr>
            <th class="check-col"><input type="checkbox" id="v2MasterCheck" onclick="window.v2ToggleAll(this.checked)" /></th>
            <th>Row</th>
            <th>Container</th>
            <th>Invoice #</th>
            <th>Customer</th>
            <th>Validation</th>
          </tr>
        </thead>
```

Replace with:

```js
        <thead>
          <tr>
            <th class="check-col"><input type="checkbox" id="v2MasterCheck" onclick="window.v2ToggleAll(this.checked)" /></th>
            <th>Row</th>
            <th>Container</th>
            <th>Invoice #</th>
            ${hasAnyWO() ? '<th>WO #</th>' : ''}
            <th>Customer</th>
            <th>Validation</th>
          </tr>
        </thead>
```

- [ ] **Step 4: Same change in `renderReviewSuccess`**

In `renderReviewSuccess` (around lines 489-499), find the same `<thead>` block (inside the `expanded` template):

```js
            <thead>
              <tr>
                <th class="check-col"><input type="checkbox" id="v2MasterCheck" onclick="window.v2ToggleAll(this.checked)" /></th>
                <th>Row</th>
                <th>Container</th>
                <th>Invoice #</th>
                <th>Customer</th>
                <th>Validation</th>
              </tr>
            </thead>
```

Replace with:

```js
            <thead>
              <tr>
                <th class="check-col"><input type="checkbox" id="v2MasterCheck" onclick="window.v2ToggleAll(this.checked)" /></th>
                <th>Row</th>
                <th>Container</th>
                <th>Invoice #</th>
                ${hasAnyWO() ? '<th>WO #</th>' : ''}
                <th>Customer</th>
                <th>Validation</th>
              </tr>
            </thead>
```

- [ ] **Step 5: Update `renderTbodyHTML`'s empty-state colspan**

In `renderTbodyHTML` (around lines 392-398), find:

```js
function renderTbodyHTML() {
  const rows = getVisibleRows();
  if (rows.length === 0) {
    return `<tr><td colspan="6" style="padding:20px; text-align:center; color:#94a3b8;">No rows match.</td></tr>`;
  }
  return rows.map(rowMarkup).join('');
}
```

Replace with:

```js
function renderTbodyHTML() {
  const rows = getVisibleRows();
  if (rows.length === 0) {
    const cols = hasAnyWO() ? 7 : 6;
    return `<tr><td colspan="${cols}" style="padding:20px; text-align:center; color:#94a3b8;">No rows match.</td></tr>`;
  }
  return rows.map(rowMarkup).join('');
}
```

- [ ] **Step 6: Verify the WO# column appears and disappears correctly**

Restart `npm start`. With Beta ON:

1. Drop `docs/no sav.xlsx` (has `WO #` column). Click "Show all 110 rows" link on the success card. Confirm the table now has 7 columns including a **WO #** column between Invoice # and Customer. WO# values appear monospaced.

2. Drop `docs/idea nouva weekly 04.13-04.19_formatted.xlsx`. Check whether it has a WO# column — if yes, the column should appear; if not, the table should still be 6 columns wide. (Either result is fine — confirms the conditional logic.)

3. Type something in the search box that returns no rows (e.g., "ZZZZZ"). Confirm the empty-state cell spans the correct number of columns (no visual misalignment).

- [ ] **Step 7: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge/v2): optional WO# column in Review table"
```

---

## Task 10: End-to-end smoke test

**Files:** None modified — verification only.

- [ ] **Step 1: v1 full-batch verification**

Restart `npm start`. Settings → toggle Merge Tool — Beta **OFF**. Confirm agent server is running (status indicator at the top should be green).

1. Switch to the Merge tool.
2. Drop `docs/no sav.xlsx`. Status Log: `Parsed 110 invoice rows (100 unique containers)`. Drop-zone subtitle: `110 invoices · 100 unique containers`.
3. Drop the 110 source PDFs from `c:/Users/Joseph/AppData/Local/Programs/ngl-accounting/resources/agent/ngl-agent/output/One to One Merge/`. (Yes, you'll be using the renamed-already PDFs as the input to test the round-trip — that's fine. The merge will match by container substring; the new INV# in their filenames is ignored by the matcher.)
4. Click **Run Merge**.
5. After it finishes, in DevTools console:

```js
state.mergeResults.length                                              // expected: 110
new Set(state.mergeResults.map(r => r.filename)).size                  // expected: 110 (no duplicate filenames)
state.mergeResults.filter(r => r.filename.includes('PM26050063F')).length  // expected: 1 (the CONAIR drayage invoice)
```

6. Click **Save to Folder**. The agent writes 110 files. Status Log shows: `Saved 110/110 files → ...`. Open the resulting folder — confirm 110 PDFs with the `MM.DD_<INV#>_<container>_merged.pdf` pattern.

- [ ] **Step 2: v2 review-state verification**

Settings → toggle Merge Tool — Beta **ON**. Refresh the app (Ctrl+R inside the dev window).

1. Switch to the Merge tool.
2. Drop `docs/no sav.xlsx`. Confirm Review state lands on the **success card** ("All 110 rows checked out"). Click "Show all 110 rows".
3. Search for `CAAU7378645`. Confirm **two rows** are visible, both with status badge `OK` (green), both with checked checkboxes. Their INV# columns should differ. WO# column should show the same value (`PM2604260005`) for both.
4. Clear the search.
5. Click "All" tab. Total count: 110. Issues count: 0.

- [ ] **Step 3: Regression test — clean batch**

With Beta OFF, drop `docs/NGL INVOICE 05.05.2026 (1).xlsx`. Confirm:

- Status Log shows `Parsed N invoice rows (N unique containers)` where the two numbers are equal (no duplicates expected in this clean batch).
- No yellow Verify banners on any cards.
- No skipped-rows entries in any failure report.

Repeat the same drop with Beta ON. Confirm v2 lands on the success card with no issues.

- [ ] **Step 4: Edit the spec's verification section to mark scenarios verified**

Open `docs/superpowers/specs/2026-05-06-merge-tool-invoice-grouping-design.md`. The spec's Verification section lists 5 success criteria. Add a `✅` prefix to each one that the smoke test above just confirmed. Leave any unverified ones unmarked. Commit the spec change separately:

```bash
git add docs/superpowers/specs/2026-05-06-merge-tool-invoice-grouping-design.md
git commit -m "docs(merge): mark invoice-grouping spec criteria as verified"
```

(No code changes in this task — just verification + a docs commit.)

---

## Task 11: Bump VERSION + run rebuild

**Files:**
- Modify: `desktop/VERSION`

- [ ] **Step 1: Bump the version**

```bash
echo "2.47" > desktop/VERSION
```

Verify:

```bash
cat desktop/VERSION
```

Expected: `2.47`

- [ ] **Step 2: Sync `desktop/package.json`**

```bash
cd desktop && node bump-version.js && cd ..
```

This script reads `desktop/VERSION` and writes `desktop/package.json`'s `version` field. Verify:

```bash
grep '"version"' desktop/package.json
```

Expected: `"version": "2.47.0",`

- [ ] **Step 3: Run the non-interactive rebuild via PowerShell**

Use the recipe from `feedback_use_runbuild_for_rebuild.md` — `Start-Process` with the empty-stdin file gotcha:

```powershell
$buildDir = "C:\Users\Joseph\Desktop\NGL ACCOUNTING SERVICE\desktop"
$emptyFile = "$buildDir\.empty-stdin"
Set-Content -Path $emptyFile -Value "" -NoNewline -Encoding ASCII

$p = Start-Process -FilePath "$buildDir\runbuild.bat" `
  -WorkingDirectory $buildDir `
  -RedirectStandardInput $emptyFile `
  -RedirectStandardOutput "$buildDir\build-log-2.47.txt" `
  -RedirectStandardError "$buildDir\build-log-2.47-err.txt" `
  -PassThru -Wait -NoNewWindow

Remove-Item $emptyFile -ErrorAction SilentlyContinue
"ExitCode: $($p.ExitCode)"
```

Run this with `run_in_background: true` (the build takes several minutes). Use the Monitor tool to tail `desktop/build-log-2.47.txt`:

```
tail -F desktop/build-log-2.47.txt | grep -E "^[ ]*\[(1/|2/|3/|INFO|ERROR|WARN|OK|spec)|===|complete!|FAILED|Traceback|not recognized|electron-builder|building.*target|signing|Packaging|Built|nsis"
```

Wait for the success sentinel `===ELECTRON_BUILD_DONE===`. Expected build time: ~3-6 minutes.

- [ ] **Step 4: Verify build artifacts**

```bash
ls -la "desktop/dist/NGL_ACCOUNTING_INSTALLER_v2.47.0.exe" "desktop/dist/latest.yml"
```

Both should exist. Check `latest.yml`:

```bash
cat desktop/dist/latest.yml
```

Expected: `version: 2.47.0` and a `path:` line referencing `NGL_ACCOUNTING_INSTALLER_v2.47.0.exe`.

If exitCode was non-zero or either file is missing, **stop and investigate the build log** — do NOT proceed to commit/push/release.

---

## Task 12: Commit, push, and publish GitHub release

**Files:** None modified — git/gh operations only.

- [ ] **Step 1: Stage everything**

```bash
git status
```

Confirm the changed files match the implementation surface table:
- `app/assets/js/shared/utils.js`
- `app/assets/js/tools/merge/merge.js`
- `app/assets/js/tools/merge/merge-v2.js`
- `app/assets/css/styles.css`
- `desktop/VERSION`
- `desktop/package.json`

If any feature commits from Tasks 1-9 were missed, stage them now. The Task 10 spec commit + Task 11 VERSION/package.json should be the last things outstanding.

- [ ] **Step 2: Commit the version bump**

```bash
git add desktop/VERSION desktop/package.json
git commit -m "chore: bump version to 2.47.0 (merge tool invoice grouping fix)"
```

- [ ] **Step 3: Push to remote**

```bash
git push origin main
```

- [ ] **Step 4: Create the GitHub release**

```bash
gh release create v2.47.0 \
  "desktop/dist/NGL_ACCOUNTING_INSTALLER_v2.47.0.exe" \
  "desktop/dist/latest.yml" \
  --title "v2.47.0 — Merge Tool: invoice grouping fix" \
  --notes "$(cat <<'EOF'
## Fixed
- **Merge tool no longer silently drops invoice rows** when two invoices share the same container. The 2026-05-05 batch was missing 10 PDFs (CONAIR drayage companions + 1 True Value side-charge) because of this bug. Every invoice row now produces its own merged PDF.

## Changed
- **New filename pattern:** `MM.DD_<INV#>_<container>_merged.pdf` (falls back to WO# then container alone if INV# is missing).
- **Excel parser now reads an optional WO# column** via fuzzy alias matching (`WO #`, `WO#`, `Workorder No`, `Work Order Number`, etc.).
- **Duplicate detection now uses INV# only** — same container + same WO# + different INV# is treated as a real billing split (not a duplicate).
- **Rows with a missing INV#** are flagged for review with a yellow Verify badge but still produce a merged PDF named with the WO# fallback.
- v2 Beta tool: removed the false-positive "different invoice" duplicate warning. Same-container-different-invoice rows now show as plain OK rows, both checked by default.

## Spec & design
- [Spec](docs/superpowers/specs/2026-05-06-merge-tool-invoice-grouping-design.md)
- [Mockup](app/mockups/merge-v2-invoice-grouping-mockup.html)
EOF
)"
```

- [ ] **Step 5: Verify the release**

```bash
gh release view v2.47.0
```

Confirm the release page shows both attached files (`.exe` and `.yml`) and the release notes render correctly.

- [ ] **Step 6: Wait for the auto-updater confirmation**

The packaged app on the user's machine checks for updates on startup. Restart the desktop app (close it from the system tray, then re-open via the desktop shortcut). Within ~30 seconds it should show a "Restart to apply update" prompt. Once they accept, the new 2.47.0 build is live.

---

## Self-review notes

Before handing off for execution, the spec's 5 verification scenarios all map to tasks:

1. *Re-running the merge against `docs/no sav.xlsx` produces 110 PDFs* → Task 10 Step 1
2. *Exact-INV# duplicate produces one PDF, second is shown as `dup-same-inv`* → Task 7 Step 3 (v2) + Task 3 Step 5 (v1)
3. *Row with no INV# but a WO# produces a PDF named with the WO#* → Task 5 Step 6 (v1 visual) + the soft-flag wiring in Task 7 (v2)
4. *Excel without any WO# column still works* → Task 6 Step 5 (v2 with the second test file) + Task 2 Step 5 (v1 — `WO #` is optional, no warning when absent)
5. *v2 no longer shows "Same container as row X, but different invoice number" warning* → Task 7 Step 2

No spec requirement is left untested.
