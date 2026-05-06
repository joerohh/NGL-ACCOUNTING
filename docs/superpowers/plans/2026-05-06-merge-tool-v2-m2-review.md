# Merge Tool V2 — M2 (Review state) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the M1 Review-state stub with a real Excel parser, validation, success card / tabbed table split, live search/sort/selection, and a Fetch button that bridges to the M3 fetching stub.

**Architecture:** Frontend-only, beta-toggle isolated. All work lives in `app/assets/js/tools/merge/merge-v2.js` (~160 → ~500 lines) plus a single new CSS block in `app/assets/css/styles.css` for the success card. The legacy merge tool (`merge.js`, `state.excelRows`, `mergeToolView`) is not touched. Re-renders are scoped to `<tbody>` during interactive operations so the search input never loses focus mid-keystroke.

**Tech Stack:** Vanilla JS ES modules · global `XLSX` from CDN · existing `CSV_ALIASES` + `findColumnKey` + `readAsArrayBuffer` + `escHtml` from `shared/utils.js` · pre-shipped CSS scoped to `#mergeToolViewV2`.

**Spec:** `docs/superpowers/specs/2026-05-06-merge-tool-v2-m2-review-design.md`

**Mockup:** `app/mockups/merge-tool-redesign.html` (lines 1193-1257 are Review state)

---

## File Structure

| File | Action | Why |
|---|---|---|
| `app/assets/js/tools/merge/merge-v2.js` | Modify | Real `renderReview()`, parser, validator, sort/search/selection, tbody-only re-render helpers |
| `app/assets/css/styles.css` | Modify (append) | Add `.review-success-card` block scoped to `#mergeToolViewV2` |
| `app/index.html` | No changes | Hidden file inputs + `v2WorkArea` already present from M1 |
| `desktop/VERSION` | Modify | Bump to `2.46` for the M2 ship |
| `desktop/package.json` | Modify (auto via `bump-version.js`) | electron-builder reads version from here |

The legacy `merge.js`, `state.js`, and `mergeToolView` HTML are untouched.

---

## Sample test files

Both already exist in `docs/`:

- **`docs/NGL INVOICE 05.05.2026 (1).xlsx`** — clean headers, customer column resolves via `NAME` alias to friendly name (`FREIGHT FLEX LLC`).
- **`docs/idea nouva weekly 04.13-04.19_formatted.xlsx`** — clean headers, customer column resolves via `BILLTO` alias to a code (`IDEANU01`).

To force duplicates and missing-invoice scenarios for testing, the implementer can copy a row in either file using Excel and clear the `INV#` cell to test `miss-inv`. Or they can craft a tiny test file with three rows: two identical (exact dup) plus one with the same container but a different invoice (`dup-diff-inv`).

---

## How to test each task

This codebase has no JS test framework. Verification is manual via dev mode + DevTools console. The recipe:

```
cd desktop
npm start             # launches Electron in dev mode pointing at app/
```

Inside the app:
1. Settings → toggle **Merge Tool — Beta** ON (if not already).
2. Switch to the Merge tool (sidebar).
3. Drop the test Excel.
4. Open DevTools (Ctrl+Shift+I) and run the verification commands inline.

Each task's "Verify" step says exactly what to do.

---

## Task 1: Pre-flight + extend `v2State` with new fields

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js:11-19`

- [ ] **Step 1: Open the file and confirm M1 baseline still works**

```
cd desktop && npm start
```

In the app: Settings → toggle Merge Tool — Beta. Switch to Merge. Drop any `.xlsx`. Confirm you see the M1 stub (`Review (M2 stub)` with the file name). Stop the dev process (`Ctrl+C` in the terminal).

- [ ] **Step 2: Replace the `v2State` block**

In `app/assets/js/tools/merge/merge-v2.js`, replace the existing block at lines 12-19:

```js
// ── Module-local state ──
const v2State = {
  subMode: 'empty',          // empty | loading | review | fetching | ready | merging | done
  excelFile: null,           // File handle
  excelHeaders: [],          // Captured for diagnostics when alias matching fails
  rows: [],                  // Array<{rowNum, containerNumber, invoiceNumber, customer, selected, status, statusReason}>
  loadingError: null,        // Inline error shown on the loading state when parse fails
  searchQuery: '',           // Live search box value
  sortMode: 'excel',         // 'excel' | 'container' | 'invoice' | 'issues-first'
  activeTab: 'all',          // 'all' | 'issues'
  showAllInSuccess: false,   // Success-card "Show all rows" expander toggle
  pendingMode: null,         // mode about to run (M4)
  completedModes: [],        // mode keys that produced output this session (M4)
  lastCompletedMode: null,   // for the Done banner / focus (M4)
};
```

- [ ] **Step 3: Update the `setStateV2('empty')` reset path**

Find the `if (name === 'empty')` block (around line 56-63) and replace its body:

```js
  if (name === 'empty') {
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
    const xinput = document.getElementById('v2ExcelInput');
    if (xinput) xinput.value = '';
  }
```

- [ ] **Step 4: Verify**

```
cd desktop && npm start
```

In DevTools console while on the merge tool view:
```js
console.log(window.initMergeV2);   // should be a function
```
Drop any `.xlsx`. Open DevTools and confirm no errors in the console. The M1 stub still renders.

- [ ] **Step 5: Commit**

```
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge-v2/m2): extend v2State with rows, search, sort, tab, expander fields"
```

---

## Task 2: Excel parser + column detection (no UI yet)

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js:10` (imports), and add a new section after the renderers

- [ ] **Step 1: Update imports**

Replace the import line at the top of the file:

```js
import { escHtml, readAsArrayBuffer, findColumnKey, CSV_ALIASES } from '../../shared/utils.js';
```

- [ ] **Step 2: Add the customer column finder helper**

Add this just above the `// ── State renderers ──` comment block:

```js
// ── Excel parsing + column detection ──
function findCustomerColumn(headers) {
  // Friendly name first (e.g. "NAME" → "FREIGHT FLEX LLC")
  const byName = findColumnKey(headers, CSV_ALIASES.customerName);
  if (byName) return byName;
  // Fall back to a code column (e.g. "BILLTO" → "IDEANU01")
  return findColumnKey(headers, CSV_ALIASES.customerCode);
}
```

- [ ] **Step 3: Add the parser**

Below `findCustomerColumn`, add:

```js
async function parseExcelFile(file) {
  let buf;
  try {
    buf = await readAsArrayBuffer(file);
  } catch (err) {
    return { error: 'Could not read the file. Try saving and re-uploading.' };
  }

  let wb;
  try {
    wb = XLSX.read(buf, { type: 'array' });
  } catch (err) {
    return { error: `Couldn't parse this file as Excel: ${err.message}` };
  }

  const sheetName = wb.SheetNames[0];
  if (!sheetName) return { error: 'This Excel file has no sheets.' };
  const ws = wb.Sheets[sheetName];
  const sheetRows = XLSX.utils.sheet_to_json(ws, { defval: '' });

  if (sheetRows.length === 0) {
    return { error: 'The first sheet has no data rows. Make sure the first row is a header row and there is at least one data row below.' };
  }

  const headers = Object.keys(sheetRows[0]);
  const containerKey = findColumnKey(headers, CSV_ALIASES.containerNumber);
  const invoiceKey   = findColumnKey(headers, CSV_ALIASES.invoiceNumber);
  const customerKey  = findCustomerColumn(headers);

  if (!containerKey) {
    return {
      error: `Couldn't find a Container column. Looked at: ${headers.join(', ')}. Expected something like "Container Number", "CONT NO", or "Equipment".`,
    };
  }

  const rows = [];
  for (let i = 0; i < sheetRows.length; i++) {
    const r = sheetRows[i];
    const cn = String(r[containerKey] || '').trim();
    if (!cn) continue;          // skip blank container rows entirely
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
  }

  if (rows.length === 0) {
    return { error: 'No usable rows — every row had an empty Container cell.' };
  }

  return { rows, headers, containerKey, invoiceKey, customerKey };
}
```

- [ ] **Step 4: Replace the `handleExcelChange` body**

Find the existing `handleExcelChange` (around line 83-94) and replace it with:

```js
async function handleExcelChange(e) {
  const file = e.target.files && e.target.files[0];
  if (!file) return;
  v2State.excelFile = file;
  v2State.loadingError = null;
  setStateV2('loading');

  const result = await parseExcelFile(file);

  if (result.error) {
    v2State.loadingError = result.error;
    setStateV2('loading');     // re-render with error state (renderLoading uses this field)
    return;
  }

  v2State.rows = result.rows;          // validation will tag these in Task 3
  v2State.excelHeaders = result.headers;
  v2State.activeTab = 'all';           // Task 3 will switch to issues-when-issues-exist
  v2State.searchQuery = '';
  v2State.sortMode = 'excel';
  v2State.showAllInSuccess = false;
  setStateV2('review');
}
```

- [ ] **Step 5: Update `renderLoading()` to show errors**

Replace the existing `renderLoading()` function (around lines 118-131):

```js
function renderLoading() {
  if (v2State.loadingError) {
    return `
      <div class="centered-stage">
        <h1>Couldn't read this file</h1>
        <p class="subtitle">${escHtml(v2State.loadingError)}</p>
        <div class="big-drop kind-excel" onclick="window.v2TriggerExcel()">
          <div class="drop-icon">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="15" x2="21" y2="15"/><line x1="9" y1="3" x2="9" y2="21"/>
            </svg>
          </div>
          <div class="drop-title">Try a different file</div>
          <div class="drop-help">Drop or pick another Excel manifest</div>
          <div class="drop-types">.xlsx · .xls · .csv</div>
        </div>
      </div>
    `;
  }
  return `
    <div class="centered-stage">
      <h1>Start a new merge</h1>
      <p class="subtitle">Reading your manifest…</p>
      <div class="big-drop loading">
        <div class="big-spinner"></div>
        <div class="drop-title">Loading…</div>
        <div class="drop-help">Reading container numbers and checking for issues</div>
      </div>
      <div class="hint-chip"><span class="step-num">2</span> We'll check the manifest, then fetch from APIs</div>
    </div>
  `;
}
```

- [ ] **Step 6: Verify**

```
cd desktop && npm start
```

Drop `docs/NGL INVOICE 05.05.2026 (1).xlsx`. The Review stub still shows (no real review UI yet — that's Task 4+) but in DevTools:

```js
// Module state isn't directly exposed, but you can verify via the file picker:
document.getElementById('v2ExcelInput').files[0].name
// Should show "NGL INVOICE 05.05.2026 (1).xlsx"
```

Test the error path: drop a non-Excel file (rename a `.txt` to `.xlsx` and drop it). The loading state should show "Couldn't read this file" with the error message under the title.

Drop a real `.xlsx` again. The Review stub should appear without errors.

- [ ] **Step 7: Commit**

```
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge-v2/m2): real Excel parser + column detection + error path"
```

---

## Task 3: Validation logic (status tagging + selection defaults)

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js` (add `validateRows`, wire into `handleExcelChange`)

- [ ] **Step 1: Add `validateRows`**

Just below `parseExcelFile`, add:

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
    // If both invoices match (case/whitespace-insensitive), it's an exact dup.
    const sameInv =
      row.invoiceNumber &&
      prior.invoiceNumber &&
      row.invoiceNumber.trim().toLowerCase() === prior.invoiceNumber.trim().toLowerCase();

    if (sameInv) {
      row.status = 'dup-same-inv';
      row.statusReason = `Exact duplicate of row ${prior.rowNum} — will be skipped`;
    } else {
      row.status = 'dup-diff-inv';
      row.statusReason = `Same container as row ${prior.rowNum}, but different invoice number`;
    }
    row.selected = false;          // duplicates default to unchecked; user can opt back in
  }
  return rows;
}
```

- [ ] **Step 2: Wire validation + tab default into `handleExcelChange`**

Replace the post-parse section of `handleExcelChange` (the `if (result.error)` and below):

```js
  if (result.error) {
    v2State.loadingError = result.error;
    setStateV2('loading');
    return;
  }

  v2State.rows = validateRows(result.rows);
  v2State.excelHeaders = result.headers;
  // Default-active tab: Issues if any issue exists, All otherwise.
  const hasIssues = v2State.rows.some(r => r.status !== 'ok');
  v2State.activeTab = hasIssues ? 'issues' : 'all';
  v2State.searchQuery = '';
  v2State.sortMode = 'excel';
  v2State.showAllInSuccess = false;
  setStateV2('review');
```

- [ ] **Step 3: Verify**

```
cd desktop && npm start
```

Drop `docs/NGL INVOICE 05.05.2026 (1).xlsx`. The Review stub still shows. Now in DevTools console expose state for inspection — paste this temporarily into the console:

```js
// One-off probe: import the module's internal state by calling a renderer twice
// and inspecting the DOM. We can't see v2State directly, so instead we'll add
// a debug exposure for this verification only:
window._v2ProbeRows = () => Array.from(document.querySelectorAll('#mergeToolViewV2 *')).length;
```

Better verification: open the M1 stub, confirm the file loads, then for now insert a temporary `console.log(v2State.rows)` line in `handleExcelChange` after `validateRows`, save, restart dev mode, and verify the parsed rows print to the console with `status`, `selected`, etc. populated.

Expected for `NGL INVOICE`: every row has `status: 'ok'` and `selected: true` (no duplicates expected in that file). The `customer` field should be friendly names like `FREIGHT FLEX LLC`.

Then craft a quick duplicate test: open the Excel in Excel, copy any row, paste below it (creating an exact dup), save as `docs/test-dup.xlsx`, drop it. Console should show one row with `status: 'dup-same-inv'`, `selected: false`. Remove the temporary `console.log` after verifying.

- [ ] **Step 4: Commit**

```
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge-v2/m2): validation — duplicate + missing-invoice tagging"
```

---

## Task 4: CSS for `.review-success-card`

**Files:**
- Modify: `app/assets/css/styles.css` (append a new block at the end)

- [ ] **Step 1: Find the M2 styles block**

Open `app/assets/css/styles.css`. Search for the marker comment introduced by M1: `MERGE TOOL V2 (BETA) — scoped to #mergeToolViewV2`. The styles for v2 end somewhere after line ~1300.

- [ ] **Step 2: Append the success-card block**

Add this block to the end of the V2 section (just before the next un-related section, or at end of file):

```css
/* ── M2: Review-state success card (no-issues happy path) ── */
#mergeToolViewV2 .review-success-card {
  width: 100%;
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  border-radius: 14px;
  padding: 36px 32px;
  text-align: center;
  margin-bottom: 18px;
}
#mergeToolViewV2 .review-success-card .check-icon {
  width: 56px; height: 56px;
  margin: 0 auto 14px;
  background: #16a34a; color: white; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
}
#mergeToolViewV2 .review-success-card .title {
  font-size: 1.1rem; font-weight: 700; color: #15803d;
  margin-bottom: 4px;
}
#mergeToolViewV2 .review-success-card .subtitle {
  font-size: 0.9rem; color: #166534; margin-bottom: 18px;
}
#mergeToolViewV2 .review-success-card .fetch-btn {
  background: #ea580c; color: white; border: none;
  padding: 11px 24px; border-radius: 8px;
  font-size: 0.92rem; font-weight: 700;
  cursor: pointer; font-family: inherit;
  display: inline-flex; align-items: center; gap: 8px;
}
#mergeToolViewV2 .review-success-card .fetch-btn:hover { background: #c2410c; }
#mergeToolViewV2 .review-success-card .fetch-btn:disabled,
#mergeToolViewV2 .review-success-card .fetch-btn[disabled] {
  background: #fed7aa; cursor: not-allowed;
}
#mergeToolViewV2 .review-success-card .show-all-link {
  display: inline-block; margin-top: 16px;
  background: none; border: none; cursor: pointer;
  font-size: 0.84rem; color: #15803d; font-family: inherit;
  text-decoration: underline;
}
#mergeToolViewV2 .review-success-card .show-all-link:hover { color: #14532d; }

/* Inline error hint shown on the loading state when parsing fails */
#mergeToolViewV2 .centered-stage p.subtitle.error-text {
  color: #b91c1c;
}
```

- [ ] **Step 3: Verify the CSS loads**

```
cd desktop && npm start
```

In DevTools console:
```js
const probe = document.createElement('div');
probe.id = 'mergeToolViewV2'; document.body.appendChild(probe);
const card = document.createElement('div');
card.className = 'review-success-card'; probe.appendChild(card);
getComputedStyle(card).backgroundColor;
// Expected: "rgb(240, 253, 244)"
probe.remove();
```

If the color matches, CSS is loaded.

- [ ] **Step 4: Commit**

```
git add app/assets/css/styles.css
git commit -m "feat(merge-v2/m2): CSS for review success card"
```

---

## Task 5: `renderReview()` — success path

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js` (replace `renderReview()`, add `renderReviewSuccess()`)

- [ ] **Step 1: Add a top-bar helper used by both paths**

Just above the existing `function renderReview()` (around line 133), add:

```js
function topBarOnlyExcel() {
  const fname = v2State.excelFile ? escHtml(v2State.excelFile.name) : '';
  const total = v2State.rows.length;
  const issueCount = v2State.rows.filter(r => r.status !== 'ok').length;
  const meta = issueCount === 0
    ? `${total} unique container${total !== 1 ? 's' : ''}`
    : `${total} unique container${total !== 1 ? 's' : ''} · ${issueCount} issue${issueCount !== 1 ? 's' : ''}`;
  return `
    <div class="top-bar" style="grid-template-columns: 1fr;">
      <div class="file-summary">
        <div class="icon-box xlsx">XLS</div>
        <div class="text">
          <div class="name">${fname}</div>
          <div class="meta">${escHtml(meta)}</div>
        </div>
        <button onclick="window.v2SetState('empty')">Replace</button>
      </div>
    </div>
  `;
}
```

- [ ] **Step 2: Replace `renderReview()`**

Replace the existing `renderReview()` (currently the M1 stub):

```js
function renderReview() {
  const hasIssues = v2State.rows.some(r => r.status !== 'ok');
  return hasIssues
    ? renderReviewWithIssues()
    : renderReviewSuccess();
}
```

- [ ] **Step 3: Add `renderReviewSuccess()`**

Below `renderReview()`, add:

```js
function renderReviewSuccess() {
  const total = v2State.rows.length;
  // Tasks 6+ replace this stub with the real expanded-table markup
  const expanded = v2State.showAllInSuccess
    ? `<div style="margin-top:18px; padding:16px; background:#fff; border:1px solid #e2e8f0; border-radius:10px; color:#94a3b8; font-size:0.86rem;">
         (Expanded table — wired in later tasks)
       </div>`
    : '';
  const linkLabel = v2State.showAllInSuccess
    ? 'Hide rows ▲'
    : `Show all ${total} rows ▼`;
  return `
    ${topBarOnlyExcel()}
    <div class="review-success-card">
      <div class="check-icon">
        <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="20 6 9 17 4 12"/>
        </svg>
      </div>
      <div class="title">All ${total} row${total !== 1 ? 's' : ''} checked out</div>
      <div class="subtitle">Ready to fetch documents.</div>
      <button class="fetch-btn" id="v2BtnFetch" onclick="window.v2ClickFetch()">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
        Fetch ${total} Document${total !== 1 ? 's' : ''}
      </button>
      <div>
        <button class="show-all-link" onclick="window.v2ToggleShowAll()">${linkLabel}</button>
      </div>
      ${expanded}
    </div>
  `;
}
```

- [ ] **Step 4: Add a stub `renderReviewWithIssues()` so renderReview() doesn't crash**

Below `renderReviewSuccess()`, add:

```js
function renderReviewWithIssues() {
  // Real implementation comes in Task 6
  return `
    ${topBarOnlyExcel()}
    <div class="centered-stage" style="margin-top:24px;">
      <p class="subtitle" style="color:#94a3b8;">(Issues-path table — wired in later tasks)</p>
    </div>
  `;
}
```

- [ ] **Step 5: Wire the click handlers used by the success card**

Below the existing `window.v2SetState = setStateV2;` line, add:

```js
function v2ClickFetch() { setStateV2('fetching'); }
function v2ToggleShowAll() {
  v2State.showAllInSuccess = !v2State.showAllInSuccess;
  setStateV2('review');         // re-renders the whole review pane
}
window.v2ClickFetch = v2ClickFetch;
window.v2ToggleShowAll = v2ToggleShowAll;
```

- [ ] **Step 6: Verify**

```
cd desktop && npm start
```

Drop `docs/NGL INVOICE 05.05.2026 (1).xlsx`. Expected:
- Top bar shows the file name + `N unique containers`
- Big green success card: "All N rows checked out" + "Ready to fetch documents."
- Orange `Fetch N Documents` button
- Link below: `Show all N rows ▼`

Click `Show all N rows ▼` → the placeholder text `(Expanded table — wired in later tasks)` appears. Click again (now reads `Hide rows ▲`) → it disappears.

Click `Fetch N Documents` → state transitions to the M1 fetching stub (`Fetching (M3)`).

Click the browser back button doesn't apply — instead click `+ New Merge` in the header to reset, drop the file again. Confirm the success card returns. (If `+ New Merge` doesn't show, that's the M1 visibility rule based on state; the success card itself remains the focus of this task.)

- [ ] **Step 7: Commit**

```
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge-v2/m2): renderReview success card + Fetch + Show-all expander"
```

---

## Task 6: Issues-path chrome (top bar reuse + summary banner + tabs row + toolbar) — table still stub

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js` (real `renderReviewWithIssues()`)

- [ ] **Step 1: Add helpers for filter/sort math (used by counts in this task and tbody in Task 7)**

Just above `function renderReview()`, add:

```js
function getVisibleRows() {
  let rows = v2State.rows;
  if (v2State.activeTab === 'issues') {
    rows = rows.filter(r => r.status !== 'ok');
  }
  if (v2State.searchQuery) {
    const q = v2State.searchQuery.toLowerCase();
    rows = rows.filter(r => r.containerNumber.toLowerCase().includes(q));
  }
  return sortRows(rows, v2State.sortMode);
}

function sortRows(rows, mode) {
  const out = rows.slice();
  if (mode === 'excel') {
    out.sort((a, b) => a.rowNum - b.rowNum);
  } else if (mode === 'container') {
    out.sort((a, b) => a.containerNumber.localeCompare(b.containerNumber, undefined, { numeric: true }));
  } else if (mode === 'invoice') {
    out.sort((a, b) => {
      // empty invoices sort last
      if (!a.invoiceNumber && !b.invoiceNumber) return a.rowNum - b.rowNum;
      if (!a.invoiceNumber) return 1;
      if (!b.invoiceNumber) return -1;
      return a.invoiceNumber.localeCompare(b.invoiceNumber, undefined, { numeric: true });
    });
  } else if (mode === 'issues-first') {
    out.sort((a, b) => {
      const aIssue = a.status !== 'ok' ? 0 : 1;
      const bIssue = b.status !== 'ok' ? 0 : 1;
      if (aIssue !== bIssue) return aIssue - bIssue;
      return a.rowNum - b.rowNum;
    });
  }
  return out;
}

function selectedCount()        { return v2State.rows.filter(r => r.selected).length; }
function issuesCount()          { return v2State.rows.filter(r => r.status !== 'ok').length; }
function selectableVisible()    { return getVisibleRows().filter(r => r.status !== 'dup-same-inv'); }
// (dup-same-inv rows are still selectable via checkbox per spec — user can opt them in.
//  We'll allow toggling on every visible row uniformly. selectableVisible() is reserved
//  for the master-checkbox sync logic in Task 9 if we ever scope it differently.)
```

- [ ] **Step 2: Replace `renderReviewWithIssues()` with real markup (table is still stub)**

Replace the placeholder `renderReviewWithIssues()` from Task 5:

```js
function renderReviewWithIssues() {
  const total = v2State.rows.length;
  const issues = issuesCount();
  const okCount = total - issues;
  const sel = selectedCount();
  const fetchDisabled = sel === 0 ? 'disabled' : '';
  const sortOpts = [
    ['excel',         'Sort: Excel Order'],
    ['container',     'Sort: Container #'],
    ['invoice',       'Sort: Invoice #'],
    ['issues-first',  'Sort: Issues first'],
  ].map(([v, lbl]) => `<option value="${v}" ${v2State.sortMode === v ? 'selected' : ''}>${lbl}</option>`).join('');

  return `
    ${topBarOnlyExcel()}

    <div class="controls-line" style="background:#fffbeb; border:1px solid #fde68a; border-radius:10px; padding:11px 16px;">
      <div style="font-size:0.86rem; color:#78350f;">
        <strong style="color:#92400e;">${issues} issue${issues !== 1 ? 's' : ''}</strong> in your manifest — review the rows below, then start the fetch.
      </div>
      <div class="summary-line" style="margin-left:auto; color:#78350f;">
        <span class="ok">●</span> <strong>${okCount}</strong> ok &nbsp;·&nbsp;
        <span class="warn">●</span> <strong>${issues}</strong> issue${issues !== 1 ? 's' : ''}
      </div>
    </div>

    <div class="tabs-row">
      <div class="tabs">
        <button class="tab ${v2State.activeTab === 'all' ? 'active' : ''}" onclick="window.v2HandleTabClick('all')">All <span class="count">${total}</span></button>
        <button class="tab has-issues ${v2State.activeTab === 'issues' ? 'active' : ''}" onclick="window.v2HandleTabClick('issues')">Issues <span class="count">${issues}</span></button>
      </div>
      <div style="padding-bottom:8px;">
        <button class="top-action-btn" id="v2BtnFetch" ${fetchDisabled} onclick="window.v2ClickFetch()">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
          Fetch <span class="fetch-count">${sel}</span> Document${sel !== 1 ? 's' : ''}
        </button>
      </div>
    </div>

    <div class="toolbar">
      <input type="text" class="search" placeholder="Search containers…"
             value="${escHtml(v2State.searchQuery)}"
             oninput="window.v2HandleSearch(this.value)" />
      <select class="sort-select" onchange="window.v2HandleSort(this.value)">
        ${sortOpts}
      </select>
      <span class="filter-meta" id="v2FilterMeta">${total} unique · ${issues} issue${issues !== 1 ? 's' : ''}</span>
    </div>

    <div class="table-wrap">
      <table class="merge-table">
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
        <tbody id="v2ReviewTbody">
          <tr><td colspan="6" style="padding:20px; text-align:center; color:#94a3b8;">(Table body — wired in Task 7)</td></tr>
        </tbody>
      </table>
    </div>
  `;
}
```

- [ ] **Step 3: Stub the handlers wired in this task**

Below the existing `window.v2ClickFetch = v2ClickFetch;` line, add:

```js
function v2HandleTabClick(tab) {
  v2State.activeTab = tab;
  setStateV2('review');         // simple full re-render for now; tbody-only re-render is wired in Task 7
}
function v2HandleSearch(value) {
  v2State.searchQuery = value;
  // tbody-only re-render comes in Task 7; for now, do nothing visible
}
function v2HandleSort(mode) {
  v2State.sortMode = mode;
  // tbody-only re-render comes in Task 7
}
function v2ToggleAll(_checked) {
  // wired in Task 9
}
window.v2HandleTabClick = v2HandleTabClick;
window.v2HandleSearch   = v2HandleSearch;
window.v2HandleSort     = v2HandleSort;
window.v2ToggleAll      = v2ToggleAll;
```

- [ ] **Step 4: Verify**

```
cd desktop && npm start
```

Make a quick test file with at least one duplicate (open NGL INVOICE in Excel, copy any row, paste below it, save as `docs/test-issues.xlsx`). Drop it.

Expected:
- Top bar shows file name + `N unique containers · M issues`
- Yellow summary banner: `M issues in your manifest…`
- Right-side tally: `● ok · ● M issues`
- Tabs: `All [N]` (inactive) and `Issues [M]` (active, red badge)
- Fetch button at the right of the tabs row, disabled (since duplicates default to `selected: false`, count is `N-M` minus the original duplicates that were unchecked → if the only issue is one duplicate, count = N-1, button enabled). Verify the live count reads correctly.
- Toolbar: search box, sort dropdown defaulting to "Excel Order", filter-meta text.
- Table body shows the placeholder text.

Click the "All" tab → clicks switch the active tab visually (full re-render fires). The placeholder still shows since tbody is stubbed.

- [ ] **Step 5: Commit**

```
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge-v2/m2): issues-path chrome — banner + tabs + toolbar"
```

---

## Task 7: Render the table body + tbody-only re-render helpers

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js`

- [ ] **Step 1: Add row-rendering helpers and the tbody renderer**

Just above `getVisibleRows()` (or below it — order doesn't matter), add:

```js
function rowMarkup(row) {
  const checkAttr = row.selected ? 'checked' : '';
  const trClass = row.status === 'ok' ? '' : 'row-issue';
  const invDisplay = row.invoiceNumber
    ? `<span class="mono mono-sub">${escHtml(row.invoiceNumber)}</span>`
    : `<span class="mono mono-sub" style="color:#dc2626;">— missing —</span>`;
  const customerDisplay = row.customer
    ? escHtml(row.customer)
    : `<span style="color:#cbd5e1;">—</span>`;

  let badge = '';
  if (row.status === 'miss-inv') {
    badge = `<span class="val-badge miss"><span class="dot"></span>Missing Inv #</span>`;
  } else if (row.status === 'dup-same-inv' || row.status === 'dup-diff-inv') {
    badge = `<span class="val-badge dup"><span class="dot"></span>Duplicate</span>`;
  }
  const reasonLine = row.statusReason
    ? `<div style="font-size:0.72rem; color:${row.status === 'miss-inv' ? '#b91c1c' : '#92400e'}; margin-top:3px;">${escHtml(row.statusReason)}</div>`
    : '';

  return `<tr class="${trClass}" data-row-num="${row.rowNum}">
    <td class="check-col"><input type="checkbox" class="row-check" ${checkAttr} onchange="window.v2ToggleRow(${row.rowNum}, this.checked)" /></td>
    <td style="color:#94a3b8; font-size:0.8rem;">${row.rowNum}</td>
    <td><span class="mono">${escHtml(row.containerNumber)}</span></td>
    <td>${invDisplay}</td>
    <td>${customerDisplay}</td>
    <td>${badge}${reasonLine}</td>
  </tr>`;
}

function renderTbodyHTML() {
  const rows = getVisibleRows();
  if (rows.length === 0) {
    return `<tr><td colspan="6" style="padding:20px; text-align:center; color:#94a3b8;">No rows match.</td></tr>`;
  }
  return rows.map(rowMarkup).join('');
}
```

- [ ] **Step 2: Use `renderTbodyHTML()` from `renderReviewWithIssues()`**

Replace the placeholder tbody (the `<tr><td colspan="6" …>` line) inside `renderReviewWithIssues()` with:

```html
        <tbody id="v2ReviewTbody">${renderTbodyHTML()}</tbody>
```

- [ ] **Step 3: Add the tbody-only re-render function**

Just below `renderTbodyHTML()`, add:

```js
function rerenderTbody() {
  const tbody = document.getElementById('v2ReviewTbody');
  if (!tbody) return;
  tbody.innerHTML = renderTbodyHTML();
  updateMasterCheckbox();
  updateFilterMeta();
}

function updateFilterMeta() {
  const el = document.getElementById('v2FilterMeta');
  if (!el) return;
  const visible = getVisibleRows().length;
  const total = v2State.rows.length;
  const issues = issuesCount();
  if (v2State.searchQuery) {
    el.textContent = `${visible} of ${total} match · ${issues} issue${issues !== 1 ? 's' : ''}`;
  } else {
    el.textContent = `${total} unique · ${issues} issue${issues !== 1 ? 's' : ''}`;
  }
}

function updateMasterCheckbox() {
  const master = document.getElementById('v2MasterCheck');
  if (!master) return;
  const visible = getVisibleRows();
  if (visible.length === 0) {
    master.checked = false; master.indeterminate = false; return;
  }
  const checkedCount = visible.filter(r => r.selected).length;
  if (checkedCount === 0) {
    master.checked = false; master.indeterminate = false;
  } else if (checkedCount === visible.length) {
    master.checked = true;  master.indeterminate = false;
  } else {
    master.checked = false; master.indeterminate = true;
  }
}
```

- [ ] **Step 4: Wire search + sort + tab handlers to use tbody-only re-render**

Replace the three handler stubs from Task 6:

```js
function v2HandleTabClick(tab) {
  v2State.activeTab = tab;
  // Re-render the full pane so the active-tab visual updates.
  setStateV2('review');
}
function v2HandleSearch(value) {
  v2State.searchQuery = value;
  rerenderTbody();
}
function v2HandleSort(mode) {
  v2State.sortMode = mode;
  rerenderTbody();
}
```

(Note: `v2HandleTabClick` still does a full re-render because the active-tab class lives on the tab buttons, not the tbody. This is acceptable — the search input doesn't have focus when the user clicks a tab.)

- [ ] **Step 5: Verify**

```
cd desktop && npm start
```

Drop `docs/test-issues.xlsx` (the file with a duplicate from Task 6).

Expected:
- Issues tab is active by default; the table shows ONLY rows where `status !== 'ok'` (i.e. only the duplicate rows).
- Each row's Validation cell shows the right badge (`Duplicate` or `Missing Inv #`) and reason text below.
- Click `All` tab → all N rows render. The duplicate row(s) have a tinted background (`row-issue`).
- Type a partial container # in the search box → table filters live (and the input keeps focus — verify by typing several characters fast).
- Pick "Sort: Container #" → rows reorder alphabetically by container.
- Pick "Sort: Issues first" → duplicate rows appear at the top (within the All tab).
- Filter-meta text on the right of the toolbar updates with `M of N match` when searching.

Drop `docs/NGL INVOICE 05.05.2026 (1).xlsx` (no issues). The success card from Task 5 still renders — confirm we didn't break that path.

- [ ] **Step 6: Commit**

```
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge-v2/m2): table body + tbody-only re-render for search/sort/tab"
```

---

## Task 8: Per-row checkbox + master-checkbox + Fetch button live count

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js`

- [ ] **Step 1: Implement `v2ToggleRow` and `v2ToggleAll`**

Replace the stubs from Task 6:

```js
function v2ToggleRow(rowNum, checked) {
  const row = v2State.rows.find(r => r.rowNum === rowNum);
  if (!row) return;
  row.selected = !!checked;
  updateMasterCheckbox();
  updateFetchButton();
}

function v2ToggleAll(checked) {
  const visibleRowNums = new Set(getVisibleRows().map(r => r.rowNum));
  for (const row of v2State.rows) {
    if (visibleRowNums.has(row.rowNum)) row.selected = !!checked;
  }
  rerenderTbody();
  updateFetchButton();
}
```

- [ ] **Step 2: Add `updateFetchButton`**

Just below `updateMasterCheckbox()`, add:

```js
function updateFetchButton() {
  const btn = document.getElementById('v2BtnFetch');
  if (!btn) return;
  const sel = selectedCount();
  // Find and update the count span (issues path)
  const countSpan = btn.querySelector('.fetch-count');
  if (countSpan) {
    countSpan.textContent = sel;
    // Also update the surrounding "Document(s)" pluralization — set the last text child.
    // Easiest: rebuild button label entirely.
    btn.innerHTML = `
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
      Fetch <span class="fetch-count">${sel}</span> Document${sel !== 1 ? 's' : ''}
    `;
  }
  if (sel === 0) {
    btn.setAttribute('disabled', '');
    btn.title = 'Check at least one row to fetch';
  } else {
    btn.removeAttribute('disabled');
    btn.removeAttribute('title');
  }
}
```

- [ ] **Step 3: Initialize master checkbox state on full render**

In `renderReviewWithIssues()`, the master checkbox markup currently has no initial state. After `setStateV2('review')` returns and the markup is in the DOM, `updateMasterCheckbox` won't have run yet. Wire a call in `setStateV2`.

Find the existing `setStateV2` function. After the renderer assigns `wa.innerHTML`, the function ends. We need to call `updateMasterCheckbox` and `updateFetchButton` after the assignment. Add the following at the end of `setStateV2`, just before the closing brace:

```js
  // After any full re-render, sync the indeterminate-required visuals
  if (v2State.subMode === 'review' && v2State.rows.some(r => r.status !== 'ok')) {
    // updateMasterCheckbox/updateFetchButton no-op safely if elements are absent
    updateMasterCheckbox();
    updateFetchButton();
  }
```

- [ ] **Step 4: Verify**

```
cd desktop && npm start
```

Drop `docs/test-issues.xlsx`.

Expected:
- Issues tab is active. Each visible row's checkbox reflects its `selected` default (duplicates unchecked, miss-inv checked).
- Click an unchecked duplicate → master goes indeterminate. Fetch button count increments by 1.
- Click again → master returns to whatever state matches the rest. Count decrements.
- Switch to All tab. Master checkbox should reflect: indeterminate if not all visible are checked, checked if all are.
- Click master while in indeterminate → all visible become selected. Fetch count rises to total.
- Click master again → all visible become unselected. Fetch count drops to 0. Fetch button is disabled and hover shows "Check at least one row to fetch".
- Type a partial container # in search → table narrows. Master reflects the visible subset's state.
- Click master while filtered → only filtered rows toggle. Clear the search → see that selection persisted on rows that weren't in the filter.
- Pick "Sort: Issues first" → rows reorder; selection state stays on the right rows.

- [ ] **Step 5: Commit**

```
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge-v2/m2): per-row + master checkboxes + live Fetch button count"
```

---

## Task 9: Wire the success-card "Show all rows" expander to the real table

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js` (`renderReviewSuccess`)

The success path's expanded-rows view should reuse the same toolbar + table the issues path uses, but without tabs (since there are no issues to filter to).

- [ ] **Step 1: Replace the placeholder block in `renderReviewSuccess()`**

Find the `expanded` variable in `renderReviewSuccess()` and replace with:

```js
  let expanded = '';
  if (v2State.showAllInSuccess) {
    const sortOpts = [
      ['excel',         'Sort: Excel Order'],
      ['container',     'Sort: Container #'],
      ['invoice',       'Sort: Invoice #'],
    ].map(([v, lbl]) => `<option value="${v}" ${v2State.sortMode === v ? 'selected' : ''}>${lbl}</option>`).join('');
    expanded = `
      <div style="margin-top:24px;">
        <div class="toolbar">
          <input type="text" class="search" placeholder="Search containers…"
                 value="${escHtml(v2State.searchQuery)}"
                 oninput="window.v2HandleSearch(this.value)" />
          <select class="sort-select" onchange="window.v2HandleSort(this.value)">
            ${sortOpts}
          </select>
          <span class="filter-meta" id="v2FilterMeta">${total} unique · 0 issues</span>
        </div>
        <div class="table-wrap">
          <table class="merge-table">
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
            <tbody id="v2ReviewTbody">${renderTbodyHTML()}</tbody>
          </table>
        </div>
      </div>
    `;
  }
```

- [ ] **Step 2: Make the success card's Fetch button also use the live-count update**

In `renderReviewSuccess()`, find the Fetch button markup and update it so the count span has the same class `fetch-count` and `id="v2BtnFetch"` for `updateFetchButton` to find:

The button is already keyed `id="v2BtnFetch"`. Update its inner HTML so it has a `.fetch-count` span:

```js
      <button class="fetch-btn" id="v2BtnFetch" onclick="window.v2ClickFetch()">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
        Fetch <span class="fetch-count">${total}</span> Document${total !== 1 ? 's' : ''}
      </button>
```

- [ ] **Step 3: Make the success path also call updateMasterCheckbox/updateFetchButton on render**

Update the trailing block in `setStateV2()` to also fire when in success path:

```js
  if (v2State.subMode === 'review') {
    updateMasterCheckbox();
    updateFetchButton();
  }
```

(Replace the previous `&& v2State.rows.some(...)` condition with this simpler one.)

- [ ] **Step 4: Verify**

```
cd desktop && npm start
```

Drop `docs/NGL INVOICE 05.05.2026 (1).xlsx`. Success card renders.

- Click `Show all N rows ▼` → toolbar + table appear below the card.
- Search for a partial container # → table filters live, success card and Fetch button stay visible above.
- Pick "Sort: Container #" → table reorders.
- Toggle a row's checkbox → success card's Fetch button count decrements.
- Toggle master → all visible rows toggle. Fetch button count updates.
- Click `Hide rows ▲` → table collapses. State (search/sort/selection) is preserved (re-expand to verify).

- [ ] **Step 5: Commit**

```
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge-v2/m2): success-card 'Show all rows' wires to real table"
```

---

## Task 10: Edge cases + smoke test pass

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js` (only if a defect surfaces)

This task is a structured smoke test — no new code unless a defect surfaces.

- [ ] **Step 1: Test both real sample files**

```
cd desktop && npm start
```

Drop `docs/idea nouva weekly 04.13-04.19_formatted.xlsx`. Expected:
- Customer column shows codes (e.g. `IDEANU01`) since the file has `BILLTO` not `NAME`.
- Container column = `CONT NO`, parsed correctly.
- Invoice column = `INV#`, parsed correctly.
- If duplicates exist (check by inspecting rows), they're tagged.

Drop `docs/NGL INVOICE 05.05.2026 (1).xlsx`. Expected:
- Customer column shows friendly names (e.g. `FREIGHT FLEX LLC`) from `NAME`.
- All rows `ok` → success card.

- [ ] **Step 2: Test the error path — unparseable file**

Rename `desktop/build-log-2.36.txt.err` (any text file at hand) to a copy named `not-an-excel.xlsx`. Drop it.

Expected: loading state shows "Couldn't read this file" or "Couldn't parse this file as Excel" with the error message in the subtitle. Drop a real Excel after — the loading state recovers.

- [ ] **Step 3: Test the error path — Excel without a Container column**

In Excel, create a quick file with headers `Foo`, `Bar`, `Baz` and one data row. Save as `docs/no-container.xlsx`. Drop it.

Expected: loading state shows "Couldn't find a Container column. Looked at: Foo, Bar, Baz. Expected something like..."

- [ ] **Step 4: Test the error path — empty file**

In Excel, create a file with only a header row (no data rows). Save. Drop it.

Expected: loading state shows "The first sheet has no data rows..."

- [ ] **Step 5: Test all three duplicate variants**

Create `docs/test-all-issues.xlsx` with these rows (using NGL INVOICE format):
- Row 2: `KKFU7654819` / `INV-001` / `CUSTOMER A` (`ok`)
- Row 3: `MAGU5764069` / `INV-002` / `CUSTOMER B` (`ok`)
- Row 4: `KKFU7654819` / `INV-001` / `CUSTOMER A` (`dup-same-inv` — exact dup of row 2)
- Row 5: `KKFU7654819` / `INV-003` / `CUSTOMER A` (`dup-diff-inv` — same container, different invoice)
- Row 6: `TCLU8830712` / `` / `CUSTOMER C` (`miss-inv` — empty invoice)

Drop. Expected:
- Issues tab active by default with `[3]` count
- Row 4: Duplicate badge, reason "Exact duplicate of row 2 — will be skipped", checkbox unchecked
- Row 5: Duplicate badge, reason "Same container as row 2, but different invoice number", checkbox unchecked
- Row 6: Missing Inv # badge, reason "No invoice number — we'll search by container instead", checkbox checked
- Fetch button reads `Fetch 3 Documents` (rows 2, 3, 6 — duplicates unchecked by default)
- Click row 5's checkbox → Fetch reads `Fetch 4 Documents`, master goes indeterminate
- Master checkbox click in indeterminate → all 5 visible rows checked → `Fetch 5 Documents`
- Switch to All tab → all 5 rows visible
- Sort "Issues first" → rows 4, 5, 6 at top, rows 2-3 at bottom
- Search "KKFU" → 3 rows shown (rows 2, 4, 5)
- Search "MAGU" → 1 row shown
- Clear search → all rows return; selection state preserved
- Click `+ New Merge` (header) → returns to Empty state

- [ ] **Step 6: Test the customer fallback chain**

Take the test file from Step 5. In Excel, replace the `NAME` header with `BILLTO` and the row values with codes (e.g., `CUST-A`). Save as `docs/test-billto.xlsx`. Drop.

Expected: customer column displays `CUST-A` (code, friendly-name fallback worked).

Then remove the `BILLTO` column entirely. Save and drop. Expected: customer column shows `—` (em-dash, gray) on every row. No errors.

- [ ] **Step 7: Test focus preservation during search**

Drop `docs/test-all-issues.xlsx`. Click the search box and type quickly: `K`, `K`, `F`, `U`. The input should retain focus the whole time. The table should filter as you type. (If focus is lost mid-keystroke, that's a regression — the rerender is hitting the wrong scope.)

- [ ] **Step 8: Commit (even if no code changes)**

If no defects, mark the smoke test passed:

```
git commit --allow-empty -m "test(merge-v2/m2): smoke test pass — all paths verified"
```

If a defect was fixed during this task:

```
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "fix(merge-v2/m2): <defect summary>"
```

---

## Task 11: Version bump + build + push + GitHub release

**Files:**
- Modify: `desktop/VERSION`
- The build pipeline modifies `desktop/package.json` automatically.

This is the standard ship pipeline per the user's mandatory workflow (CLAUDE.md, Rebuild Pipeline section). Do not skip steps. Do not ask "want me to push?" — just do it.

- [ ] **Step 1: Bump VERSION**

```
cat desktop/VERSION       # current: 2.45
echo 2.46 > desktop/VERSION
cat desktop/VERSION       # confirms: 2.46
```

(VERSION is two-part — `2.46` not `2.46.0`. The `bump-version.js` step in the build pipeline appends `.0` to produce `package.json` version `2.46.0`.)

- [ ] **Step 2: Run the build via runbuild.bat (background)**

From the repo root:

```
powershell -Command "Start-Process -FilePath 'cmd.exe' -ArgumentList '/c desktop\\runbuild.bat > desktop\\build-log.txt 2>&1' -RedirectStandardInput 'NUL' -WindowStyle Hidden -Wait"
```

(Per `feedback_use_runbuild_for_rebuild.md`: this is the non-interactive invocation pattern. The empty-stdin redirect avoids hang on `pause` lines.)

When it finishes, the installer is at `desktop/dist/NGL_ACCOUNTING_INSTALLER_v2.46.0.exe` and `desktop/dist/latest.yml`.

- [ ] **Step 3: Verify the build**

```
ls desktop/dist/NGL_ACCOUNTING_INSTALLER_v2.46.0.exe
ls desktop/dist/latest.yml
```

Both should exist. If not, check `desktop/build-log.txt` for the error.

- [ ] **Step 4: Stage + commit version bump**

```
git add desktop/VERSION desktop/package.json
git commit -m "chore: bump version to 2.46.0 (M2 — Review state shipped)"
```

- [ ] **Step 5: Push**

```
git push origin main
```

- [ ] **Step 6: Create GitHub release**

```
gh release create v2.46.0 \
  desktop/dist/NGL_ACCOUNTING_INSTALLER_v2.46.0.exe \
  desktop/dist/latest.yml \
  --title "v2.46.0 — Merge Tool V2 M2 (Review state)" \
  --notes "Behind the Settings → Merge Tool — Beta toggle. Drops Excel → real Review state with success card or tabbed table, sort/search/selection. Fetch button still bridges to the M3 stub — actual fetching ships in M2's successor."
```

- [ ] **Step 7: Verify auto-update target**

In a separate terminal, confirm `latest.yml` advertises the right version:

```
cat desktop/dist/latest.yml | head -5
# Expected: version: 2.46.0
```

- [ ] **Step 8: Done**

The shipped installer + GH release lets the existing user's Electron auto-updater pick up M2 on next launch.

---

## Self-Review

**Spec coverage:**
- Data model fields (rows, search, sort, tab, headers, expander) → Task 1 ✓
- Excel parsing flow + fail-fast errors → Task 2 ✓
- Column detection (container, invoice, customer with fallback) → Task 2 ✓
- Validation rules table (status priority, plain-English messages) → Task 3 ✓
- Top bar with Replace button → Task 5 (helper) + Tasks 5-6 (use sites) ✓
- Success card with Fetch + Show-all expander → Task 5 + Task 9 ✓
- Issues path: yellow banner, tabs, toolbar, table → Tasks 6-7 ✓
- Tabs default-active rule → Task 3 (handleExcelChange sets activeTab) ✓
- Hide tabs entirely on success-path expansion → Task 9 (no tabs in expanded markup) ✓
- Sort with 4 modes (3 in success-path expand, 4 in issues-path) → Tasks 6-7, 9 ✓
- Search by container, focus-preserving via tbody-only re-render → Task 7 ✓
- Selection: per-row + master + indeterminate state → Task 8 ✓
- Fetch button live count + disabled state → Task 8 ✓
- Fetch button transitions to M1 fetching stub → Task 5 ✓
- Replace button → empty (already in M1, exercised in Task 6 verification) ✓
- CSS new block for success card → Task 4 ✓
- Build/ship pipeline per user mandatory workflow → Task 11 ✓

**Placeholder scan:** No "TBD" / "TODO" / "implement later" / "fill in details" / "similar to Task N" patterns. Each step contains the actual code an engineer needs.

**Type consistency:** State field names (`rows`, `searchQuery`, `sortMode`, `activeTab`, `showAllInSuccess`, `loadingError`, `excelHeaders`) used consistently. Function names: `parseExcelFile`, `validateRows`, `findCustomerColumn`, `getVisibleRows`, `sortRows`, `selectedCount`, `issuesCount`, `renderTbodyHTML`, `rerenderTbody`, `updateMasterCheckbox`, `updateFilterMeta`, `updateFetchButton`, `renderReviewSuccess`, `renderReviewWithIssues`, `topBarOnlyExcel` — all match across tasks. Window globals: `v2TriggerExcel`, `v2SetState`, `v2HandleSearch`, `v2HandleSort`, `v2HandleTabClick`, `v2ToggleRow`, `v2ToggleAll`, `v2ToggleShowAll`, `v2ClickFetch` — all consistent.

**Task ordering:** Each task ends with the app in a working state. After Task 1 the M1 baseline still works. After Task 5 the success card renders. After Task 6 the issues path chrome renders. After Task 7 the table works. After Tasks 8-9 selection + expander work. After Task 10 the smoke test passes. Task 11 ships.
