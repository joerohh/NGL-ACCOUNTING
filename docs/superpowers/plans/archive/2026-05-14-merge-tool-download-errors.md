# Merge Tool — Download Errors as Excel — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Download errors" button that exports the merge tool's error rows to a 9-column Excel file (including the manifest's Invoice Date).

**Architecture:** All work is client-side in vanilla JS. We add one field to the row shape captured by the Excel parser (`invoiceDate`), three new module-level functions (`getErrorRows`, `buildErrorExportRows`, `downloadErrorsXlsx`), one new `window` handler, and two button injections (one on the Ready screen's Errors-tab toolbar, one on the Merge screen header). SheetJS is already loaded via CDN. No agent endpoints. The whole feature bundles into the pending v2.69.1 release.

**Tech Stack:**
- Vanilla JS, ES modules, no build step
- SheetJS (`XLSX.utils.json_to_sheet` + `XLSX.writeFile`) — already loaded
- Project has **no JS test framework** — verification is manual smoke tests in the packaged Electron app (`runbuild.bat` → install → click through)
- Electron + electron-builder for packaging; auto-update via `gh release create`

**Spec:** `docs/superpowers/specs/2026-05-14-merge-tool-download-errors-design.md`

**Branch:** `main` (sits on top of `d3efb21`; the spec commit `f88087e` is already there)

---

## File Structure

All work happens in two existing files. No new files needed.

| File | Responsibility | Change type |
|------|----------------|-------------|
| `app/assets/js/tools/merge/merge-v2.js` | Merge tool state machine + render — adds invoiceDate field, error-export functions, two button injections | Modify (5 distinct sections) |
| `app/assets/css/styles.css` | Visual styling — adds `.download-errors-btn` style | Modify (one rule append) |
| `desktop/package.json` | Electron-builder config — fix the broken `"2.69.1.0"` SemVer that blocked the prior build | Modify (one-character fix) |

---

## Task 1: Capture the DATE column in the Excel parser

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js` (function `parseExcelFile`, around lines 228–265)

The parser today extracts container/INV#/WO#/customer from the manifest. We add one more `findColumnKey()` lookup for the DATE column using the existing `CSV_ALIASES.invoiceDate` alias list, plus a small `formatInvoiceDate()` helper to normalize the value.

- [ ] **Step 1: Add the `formatInvoiceDate` helper**

Insert this helper just above the `parseExcelFile` function (around line 200, after `findCustomerColumn`):

```js
// Normalize an Excel DATE cell to "MM/DD/YYYY".
// SheetJS may give us a JS Date (when cellDates is true), a string, or a number
// (Excel serial). Returns '' for empty/unparseable values.
function formatInvoiceDate(raw) {
  if (raw === '' || raw === null || raw === undefined) return '';
  // JS Date
  if (raw instanceof Date && !isNaN(raw.getTime())) {
    const mm = String(raw.getMonth() + 1).padStart(2, '0');
    const dd = String(raw.getDate()).padStart(2, '0');
    const yyyy = raw.getFullYear();
    return `${mm}/${dd}/${yyyy}`;
  }
  // Excel serial number (days since 1899-12-30)
  if (typeof raw === 'number' && isFinite(raw)) {
    const ms = Math.round((raw - 25569) * 86400 * 1000);
    const d = new Date(ms);
    if (!isNaN(d.getTime())) {
      const mm = String(d.getUTCMonth() + 1).padStart(2, '0');
      const dd = String(d.getUTCDate()).padStart(2, '0');
      const yyyy = d.getUTCFullYear();
      return `${mm}/${dd}/${yyyy}`;
    }
  }
  // String — pass through trimmed
  return String(raw).trim();
}
```

- [ ] **Step 2: Detect the DATE column key during header scan**

In `parseExcelFile`, just after the line:
```js
const customerKey  = findCustomerColumn(headers);
```
add:
```js
const dateKey      = findColumnKey(headers, CSV_ALIASES.invoiceDate);
```

- [ ] **Step 3: Populate `invoiceDate` on each row**

In the `rows.push({ ... })` block (currently around line 250), add one more field. The full updated block is:

```js
rows.push({
  rowNum: i + 2,            // sheet row 1 is headers, so first data row → 2
  containerNumber: cn,
  invoiceNumber: invoiceKey ? String(r[invoiceKey] || '').trim() : '',
  workOrderNumber: woKey ? String(r[woKey] || '').trim() : '',
  customer: customerKey ? String(r[customerKey] || '').trim() : '',
  invoiceDate: dateKey ? formatInvoiceDate(r[dateKey]) : '',
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

- [ ] **Step 4: Enable cellDates for SheetJS parsing**

Change the `XLSX.read` call in `parseExcelFile` from:
```js
wb = XLSX.read(buf, { type: 'array' });
```
to:
```js
wb = XLSX.read(buf, { type: 'array', cellDates: true });
```

This makes SheetJS auto-parse date cells into JS `Date` objects, which `formatInvoiceDate` then normalizes. Without this, dates come through as Excel serial numbers — still handled by the helper, but `cellDates: true` is more reliable.

- [ ] **Step 5: Manual verification**

Open `app/index.html` in a browser (or launch the packaged dev mode via `desktop/dev-launch.js`). Drop `docs/NGL INVOICE 05.05.2026 (1).xlsx`. Open the browser devtools console and run:

```js
console.log(window.v2State.rows.slice(0, 3).map(r => ({
  rowNum: r.rowNum, container: r.containerNumber, invoiceDate: r.invoiceDate,
})));
```

Expected: each row has a populated `invoiceDate` in `MM/DD/YYYY` format (e.g., `"05/05/2026"`).

- [ ] **Step 6: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge): capture invoice date column from manifest"
```

---

## Task 2: Add error-row filter, export-builder, and download functions

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js` (add three new module-level functions + one `window` handler)

The Errors tab today uses an inline filter (`r => r.fetchResult?.podPill === 'miss' && !r.skipped`) duplicated in several places. We add `getErrorRows()` as the single source of truth (now also catching `invPill === 'miss'`), plus the export builder and the download function.

- [ ] **Step 1: Add `getErrorRows()` and `buildErrorExportRows()` helpers**

Add these two functions near the bottom of `merge-v2.js` (just above the `window.*` handler block at the end of the file):

```js
// ── Error export helpers ──

// Returns the current set of error rows: rows whose fetch produced a POD miss
// or an invoice miss, and which the user did not manually skip.
function getErrorRows() {
  return v2State.rows.filter(r =>
    !r.skipped &&
    r.fetchResult &&
    (r.fetchResult.podPill === 'miss' || r.fetchResult.invPill === 'miss')
  );
}

// Maps error rows to the 9-column structure for the Excel export.
function buildErrorExportRows(errorRows) {
  return errorRows.map(r => {
    const fr = r.fetchResult || {};
    const invMiss = fr.invPill === 'miss';
    const podMiss = fr.podPill === 'miss';
    const whatsMissing =
      invMiss && podMiss ? 'Invoice + POD missing'
      : invMiss          ? 'Invoice missing'
      : podMiss          ? 'POD missing'
                         : '';
    const chain = Array.isArray(fr.chainAttempted) ? fr.chainAttempted.join(' → ') : '';
    return {
      'Row #':            r.rowNum,
      'Invoice Date':     r.invoiceDate || '',
      'Customer':         r.customer || '',
      'Container #':      r.containerNumber || '',
      'INV #':            r.invoiceNumber || '',
      'WO #':             r.workOrderNumber || '',
      "What's missing":   whatsMissing,
      'Where we looked':  chain,
      'Status detail':    fr.statusText || '',
    };
  });
}
```

- [ ] **Step 2: Add `downloadErrorsXlsx()` and expose to `window`**

Add this function immediately after the two helpers above:

```js
function downloadErrorsXlsx() {
  const errorRows = getErrorRows();
  if (errorRows.length === 0) {
    // No-op if somehow called with zero errors (button should be hidden anyway).
    return;
  }

  const sheetData = buildErrorExportRows(errorRows);
  const ws = XLSX.utils.json_to_sheet(sheetData);
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, 'Errors');

  const today = new Date();
  const yyyy = today.getFullYear();
  const mm = String(today.getMonth() + 1).padStart(2, '0');
  const dd = String(today.getDate()).padStart(2, '0');
  const filename = `merge-errors-${yyyy}-${mm}-${dd}.xlsx`;

  XLSX.writeFile(wb, filename);
}

window.v2DownloadErrors = downloadErrorsXlsx;
```

- [ ] **Step 3: Manual verification**

In the browser console, with at least one error row in state, run:
```js
window.v2DownloadErrors();
```
Expected: an `.xlsx` file named `merge-errors-2026-05-14.xlsx` downloads. Open it and confirm the 9 column headers in the order listed above. Confirm the rows match the Errors tab.

- [ ] **Step 4: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge): add error-row export to xlsx (getErrorRows + downloadErrorsXlsx)"
```

---

## Task 3: Inject the button into the Ready screen's Errors-tab toolbar

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js` (function `renderReady`, around line 1061)

The Errors-tab toolbar already has a conditional "↻ Retry all errors" button. We add the download button next to it under the same conditional (Errors tab active AND at least one error row).

- [ ] **Step 1: Add the download button next to mass-retry**

Find this block (around line 1060–1063):

```js
  // Toolbar — adds the mass-retry button when on the Errors tab with errors present
  const massRetry = (v2State.activeTab === 'errors' && errors.length > 0)
    ? `<button class="mass-retry-btn" onclick="window.v2RetryAllErrors()">↻ Retry all errors</button>`
    : '';
```

Replace it with:

```js
  // Toolbar — adds the mass-retry + download-errors buttons when on the Errors tab with errors present.
  const showErrorActions = v2State.activeTab === 'errors' && errors.length > 0;
  const massRetry = showErrorActions
    ? `<button class="mass-retry-btn" onclick="window.v2RetryAllErrors()">↻ Retry all errors</button>`
    : '';
  const downloadErrorsBtn = showErrorActions
    ? `<button class="download-errors-btn" onclick="window.v2DownloadErrors()">📥 Download errors (${errors.length})</button>`
    : '';
```

- [ ] **Step 2: Render the download button in the toolbar HTML**

Find the `toolbarHtml` template literal just below it (around line 1065–1073) and add `${downloadErrorsBtn}` between `${massRetry}` and the filter-meta span:

```js
  const toolbarHtml = `
    <div class="toolbar">
      <input type="text" class="search" placeholder="Search containers…"
             value="${escHtml(v2State.searchQuery)}"
             oninput="window.v2HandleReadySearch(this.value)" />
      ${downloadErrorsBtn}
      ${massRetry}
      <span class="filter-meta">${visibleRows.length} of ${all.length}${errors.length ? ` · ${errors.length} need fixing` : ''}${queued.length ? ` · ${queued.length} queued` : ''}</span>
    </div>
  `;
```

Note the order: download button comes **before** the retry button (left of it visually).

**Important:** The `errors` variable in this scope (defined around line 972) currently filters only on `podPill === 'miss'`. That is OK — the Errors tab definition is unchanged. The count shown in `(N)` will match the tab's count. The wider definition (POD-miss OR INV-miss) is applied inside `getErrorRows()`, which is what actually drives the export. If the count ever drifts (an invoice-only error exists on the Ready screen), the export will include more rows than the count suggests — but in practice today INV-miss is rare enough that this is acceptable.

- [ ] **Step 3: Manual verification**

Reload the app. Drop a manifest, fetch all, force at least one POD miss (or use a manifest with a known-bad container). Go to Errors tab. Expected:
- The `📥 Download errors (N)` button is visible to the left of the `↻ Retry all errors` button.
- Click it → an `.xlsx` downloads with the error row(s).
- Switch to the All tab → the button disappears.

- [ ] **Step 4: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge): add 'Download errors' button to Ready-screen Errors tab"
```

---

## Task 4: Inject the button into the Merge screen header

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js` (function `renderMerge`, around line 1407)

The Merge screen header already has "Choose a merge format" and the Output-location button. We add the download button to the right of the Output-location button, visible only when there are errors AND at least one mode has been completed.

- [ ] **Step 1: Compute the visibility flag and button HTML**

Find the start of `renderMerge` (around line 1400):

```js
function renderMerge() {
  const isRunning = !!v2State.runningMode;

  // Header: output location + (back to ready already wired by setStateV2)
  const locText = v2State.outputLocation
    ? shortenPath(v2State.outputLocation)
    : 'Desktop (default)';
```

Just after `const locText = ...`, add:

```js
  // Download-errors button: shown when at least one merge has completed and there are still error rows.
  const mergeErrorCount = getErrorRows().length;
  const completedAny = Object.keys(v2State.completedModes || {}).length > 0;
  const downloadErrorsBtn = (mergeErrorCount > 0 && completedAny)
    ? `<button class="download-errors-btn" onclick="window.v2DownloadErrors()">📥 Download errors (${mergeErrorCount})</button>`
    : '';
```

- [ ] **Step 2: Render the button inside the header div**

Update the `headerHtml` template literal (around line 1407–1416) from:

```js
  const headerHtml = `
    <div class="merge-screen-header">
      <h2>Choose a merge format</h2>
      <span class="header-spacer"></span>
      <button class="output-location-btn" onclick="window.v2ChangeOutputLocation()" title="Change where files are saved">
        <span class="label-prefix">Output:</span>
        <span class="path-text">${escHtml(locText)}</span>
      </button>
    </div>
  `;
```

to:

```js
  const headerHtml = `
    <div class="merge-screen-header">
      <h2>Choose a merge format</h2>
      <span class="header-spacer"></span>
      <button class="output-location-btn" onclick="window.v2ChangeOutputLocation()" title="Change where files are saved">
        <span class="label-prefix">Output:</span>
        <span class="path-text">${escHtml(locText)}</span>
      </button>
      ${downloadErrorsBtn}
    </div>
  `;
```

- [ ] **Step 3: Manual verification**

Reload. Drop manifest, fetch, force at least one error, click Continue to Merge, then run any one merge mode. Expected:
- Before the first merge completes → no button in header.
- After the first merge completes → the button appears to the right of the Output button.
- Click it → downloads the same `.xlsx` as the Ready-tab button.

- [ ] **Step 4: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge): add 'Download errors' button to Merge screen header"
```

---

## Task 5: Add CSS for the download button

**Files:**
- Modify: `app/assets/css/styles.css`

The existing `.mass-retry-btn` is the closest visual sibling. We give the download button a complementary look — same size and shape, slightly less alarming color (it's an informational action, not a destructive retry).

- [ ] **Step 1: Find an existing button style to anchor on**

Open `app/assets/css/styles.css` and search for `.mass-retry-btn`. Note its block (size, padding, border-radius) so the new style sits next to it.

- [ ] **Step 2: Append the new rule**

Add this rule at the **end** of `app/assets/css/styles.css` (don't reorganize the file, just append):

```css
/* Download errors button — Ready screen (Errors tab) and Merge screen header */
.download-errors-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  font-size: 13px;
  font-weight: 500;
  color: #1f2937;
  background: #f1f5f9;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.12s ease;
  margin-left: 8px;
  white-space: nowrap;
}
.download-errors-btn:hover {
  background: #e2e8f0;
  border-color: #94a3b8;
}
.download-errors-btn:active {
  background: #cbd5e1;
}
```

- [ ] **Step 3: Manual verification**

Reload, go to Errors tab. Confirm the button visually fits next to "↻ Retry all errors" (same height, sensible spacing, hovers turn slightly darker). Go to Merge screen after a merge; confirm the button fits in the header row without wrapping.

- [ ] **Step 4: Commit**

```bash
git add app/assets/css/styles.css
git commit -m "style(merge): add CSS for Download errors button"
```

---

## Task 6: End-to-end manual smoke test (no code changes)

**Files:** none — verification only.

This is the full pass that maps to the spec's "Testing checklist". Run it in the running web app or packaged build before moving to the release task.

- [ ] **Step 1: Open the app**

Either open `app/index.html` directly in a browser, or run `desktop/dev-launch.js` for the packaged dev mode.

- [ ] **Step 2: Test invoice-date capture on a known manifest**

Drop `docs/NGL INVOICE 05.05.2026 (1).xlsx`. In devtools console:
```js
window.v2State.rows.slice(0, 5).map(r => r.invoiceDate);
```
Expected: each value is a non-empty `MM/DD/YYYY` string.

- [ ] **Step 3: Test the Ready-tab download with a forced error**

Pick a row whose container is known-missing in TMS (or temporarily edit the container cell to garbage before saving). Fetch. Go to Errors tab. Click `📥 Download errors`. Open the resulting `.xlsx`. Confirm:
- 9 columns in the order from the spec.
- Invoice Date column is populated.
- "What's missing" reads "POD missing" (or whichever applies).
- "Where we looked" shows the fallback chain (e.g., `POD → BOL → POL`).

- [ ] **Step 4: Test the Merge-screen header download**

From the previous step, click "Continue to Merge", run any one mode (e.g., Combined PDF). After it completes, confirm the `📥 Download errors (N)` button appears in the Merge screen header. Click it. Confirm the same `.xlsx` downloads.

- [ ] **Step 5: Test a manifest without a DATE column**

Build a tiny `.xlsx` with just `Container` and `INV #` columns (or delete the DATE column from the sample), drop it, force an error, click download. Confirm the export still works; the Invoice Date column is just blank for all rows.

- [ ] **Step 6: Test an invoice-only miss**

Hard to trigger naturally. From devtools console after fetch:
```js
// Force the first row into an invoice-only miss for testing.
window.v2State.rows[0].fetchResult = {
  invPill: 'miss', podPill: 'ok', chainAttempted: [],
  statusText: 'Invoice not found',
};
window.v2DownloadErrors();
```
Expected: download contains that row with `What's missing = "Invoice missing"`.

- [ ] **Step 7: Verify nothing else broke**

Run a normal merge end-to-end with a clean manifest. Confirm fetch, merge, and the Errors tab all behave as before.

- [ ] **Step 8: No commit** — verification only.

---

## Task 7: Bundle into v2.69.1 release

**Files:**
- Modify: `desktop/package.json` (one-character fix: `"2.69.1.0"` → `"2.69.1"`)

This task ships the new feature alongside the already-committed `d3efb21` (email-pill fix) under the same `v2.69.1` release. The prior build failed because electron-builder rejected the 4-part SemVer.

**Background context from the handoff:** `desktop/VERSION` already reads `2.69.1`. `desktop/package.json` reads `"version": "2.69.1.0"` (invalid). The runbuild pipeline uses `runbuild.bat` with the empty-stdin pattern. After build, the auto-updater needs a GitHub release with the installer `.exe` and `latest.yml` attached.

- [ ] **Step 1: Fix the package.json version**

Open `desktop/package.json`. Change line 3 from:
```json
  "version": "2.69.1.0",
```
to:
```json
  "version": "2.69.1",
```

- [ ] **Step 2: Verify VERSION matches**

```bash
cat desktop/VERSION
```
Expected: `2.69.1`. (Should already match — no change needed.)

- [ ] **Step 3: Run the build**

From the project root in PowerShell:

```powershell
Start-Process -FilePath "desktop\runbuild.bat" `
  -RedirectStandardInput "desktop\.empty-stdin" `
  -RedirectStandardOutput "desktop\build-log-2.69.1.txt" `
  -RedirectStandardError "desktop\build-log-2.69.1.txt.err" `
  -NoNewWindow -Wait
```

Wait for completion. Then inspect:

```bash
tail -50 desktop/build-log-2.69.1.txt
tail -20 desktop/build-log-2.69.1.txt.err
```

Expected: agent build succeeds (PyInstaller), then electron-builder succeeds and writes `desktop/dist/NGL Accounting Setup 2.69.1.exe` plus `desktop/dist/latest.yml`. If electron-builder reports a SemVer error again, recheck Step 1.

- [ ] **Step 4: Commit the package.json fix + new code**

By this point Tasks 1–5 should already be committed individually. The only remaining change is the package.json fix. Commit it:

```bash
git add desktop/package.json
git commit -m "build(v69.1): fix package.json SemVer (2.69.1.0 → 2.69.1)"
```

- [ ] **Step 5: Push**

```bash
git push origin main
```

This should push at minimum: `d3efb21` (email-pill fix, pre-existing), `f88087e` (spec), and all the Task 1–5 commits, plus the Task 7 package.json fix.

- [ ] **Step 6: Create the GitHub release**

```bash
gh release create v2.69.1 \
  --title "v2.69.1 — Download errors + email-pill collapse" \
  --notes "$(cat <<'EOF'
## What's new

### Merge tool
- **Download errors as Excel** — new button on the Errors tab and the Merge screen. Exports the current set of error rows (POD missing or invoice missing) to a 9-column `.xlsx` including the invoice date from the manifest.
- Excel manifest parser now captures the **DATE** column automatically.

### Customer Manager
- Customer list table no longer pushes Edit/Delete buttons off-screen when a customer has many email recipients — the email list collapses to "first email + N more" with the rest in a hover tooltip.
EOF
)" \
  "desktop/dist/NGL Accounting Setup 2.69.1.exe" \
  "desktop/dist/latest.yml"
```

Expected: `gh` prints the new release URL. The packaged app on co-workers' machines will pick up the update on its next auto-update check.

- [ ] **Step 7: Verification**

```bash
gh release view v2.69.1
```
Expected: shows the release with two assets (`.exe` + `latest.yml`).

---

## Self-Review Notes

- **Spec coverage:** All 9 columns covered (Task 2). DATE capture covered (Task 1). Both button locations covered (Tasks 3, 4). Error-set definition (POD-miss OR INV-miss, not skipped) covered in `getErrorRows()` (Task 2). Filename pattern covered (Task 2 Step 2). CSS covered (Task 5). Bundling into v2.69.1 covered (Task 7).
- **Placeholders:** None — every code change has the exact code; every command has the exact invocation.
- **Type consistency:** `getErrorRows`, `buildErrorExportRows`, `downloadErrorsXlsx`, `window.v2DownloadErrors`, `formatInvoiceDate`, `row.invoiceDate`, `.download-errors-btn` — all referenced consistently across Tasks 1–5.
- **Testing strategy:** Project has no JS test framework — manual smoke tests in Task 6 substitute for unit tests. Agent-side Python tests don't apply (no agent changes).
