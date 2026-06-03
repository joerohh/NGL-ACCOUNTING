# AR Dashboard — Build-Flow Primary Empty State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Flip the AR Dashboard empty state so building today's workbook is the headline action (5-drop UI inlined on the page) and loading a pre-built workbook becomes a small secondary zone. Engine and writer are untouched.

**Architecture:** The drop UI moves out of its current modal and onto the page; the modal becomes preview-only. State machine and engine integration are unchanged. The loaded view's existing `Load different workbook →` link is relabeled to `Build different day →`.

**Tech Stack:** vanilla JS (ESM, no build step), CSS, ExcelJS (already loaded), existing agent endpoints (no changes).

**Related spec:** `docs/superpowers/specs/2026-06-03-ar-dashboard-build-ui-primary-design.md`

---

## File map

| File | Change |
|---|---|
| `app/assets/js/tools/ar-dashboard/ar-dashboard-build-ui.js` | Add `arRenderBuildPage(view)` export. Split: drop UI → page; preview UI → modal-only. Remove the old combined-modal entrypoint. |
| `app/assets/js/tools/ar-dashboard/ar-dashboard.js` | Rewrite `renderEmptyState()` to render page shell (header + build host + separator + secondary drop). Inflate build host via `arRenderBuildPage()`. |
| `app/assets/js/tools/ar-dashboard/ar-dashboard-views.js` | Relabel one link string. Click handler unchanged. |
| `app/assets/css/styles.css` | Add `.ar-build-page*` styles. Keep existing `.ar-build-modal*` styles for the preview modal. |
| `desktop/VERSION` | Bump to `2.78.9`. |

---

## Task 1: Refactor `ar-dashboard-build-ui.js` — split page-drop from modal-preview

**Files:**
- Modify: `app/assets/js/tools/ar-dashboard/ar-dashboard-build-ui.js`

This file currently exports `arOpenBuildModal()` which mounts a modal overlay and renders either the drop UI or the preview UI based on `arState.buildResult`. The refactor exposes `arRenderBuildPage(view)` for page-level drop rendering and keeps the modal only for preview.

- [ ] **Step 1.1: Add module-level `pageHost` reference**

After the existing `const SLOT_BY_KEY = ...` line, add:

```js
// Page-level renderer host. Set when arRenderBuildPage() is called.
// null when no page-level build flow is mounted (e.g., the user has loaded
// a pre-built workbook instead).
let pageHost = null;
```

- [ ] **Step 1.2: Add the new public entry point `arRenderBuildPage(view)`**

Replace the current `arOpenBuildModal()` function with this new export:

```js
export function arRenderBuildPage(view) {
  pageHost = view;
  arState.buildResult = null;
  arState.buildOpenKpi = null;
  for (const s of SLOTS) arState.buildInputs[s.key] = null;
  arState.buildSaveFolder = arState.buildSaveFolder || localStorage.getItem(SAVE_FOLDER_KEY) || null;
  renderPageContent();
}
```

- [ ] **Step 1.3: Add `rerender()` — re-renders whichever surface is active**

Add directly below `arRenderBuildPage`. This replaces the old `render()` function. It picks the active surface (preview modal if it exists in the DOM, otherwise the page host) and re-renders just that one:

```js
function rerender() {
  const preview = document.getElementById('arBuildPreview');
  if (preview) {
    preview.innerHTML = previewModalHtml();
    preview.addEventListener('click', onClick);
    return;
  }
  if (pageHost) {
    pageHost.innerHTML = pageDropHtml();
    wirePageEvents(pageHost);
  }
}
```

Replace the body of `arRenderBuildPage(view)` to call `rerender()` instead of `renderPageContent()`:

```js
export function arRenderBuildPage(view) {
  pageHost = view;
  arState.buildResult = null;
  arState.buildOpenKpi = null;
  for (const s of SLOTS) arState.buildInputs[s.key] = null;
  arState.buildSaveFolder = arState.buildSaveFolder || localStorage.getItem(SAVE_FOLDER_KEY) || null;
  rerender();
}
```

- [ ] **Step 1.4: Add `pageDropHtml()` — page-layout version of the drop UI**

This mirrors the current `renderDrop()` body but drops the `.ar-build-modal-overlay`/`.ar-build-modal` wrappers (the page IS the surface) and uses new `.ar-build-page*` classes that will be added in Task 3.

Add as a new function:

```js
function pageDropHtml() {
  const readyCount = SLOTS.filter(s => arState.buildInputs[s.key] && arState.buildInputs[s.key].parsed).length;
  const allReady = readyCount === SLOTS.length;
  const rowsHtml = SLOTS.map((slot, idx) => slotRowHtml(slot, idx + 1)).join('');

  return `
    <div class="ar-build-page" role="region" aria-label="Build today's workbook">
      <div class="ar-build-page-intro">
        Drop the <b>4 daily files</b> from QBO, TAB BANK, and TMS. The engine reconciles them against yesterday's workbook to produce today's AR aging.
      </div>
      <div class="ar-drop-list">${rowsHtml}</div>
      <div class="ar-build-hint">💡 <span><b>Tip:</b> you can drop all 4 files at once — the page sorts them into the right slot by filename.</span></div>
      <div class="ar-build-page-footer">
        <span class="footer-note">
          <span class="progress-pill ${allReady ? 'ready' : ''}">${readyCount} of ${SLOTS.length} ready</span>
          ${allReady ? '· ready to build' : '· drop the rest to enable Build'}
        </span>
        <button class="ngl-btn ngl-btn-secondary" data-action="cancel-page">Clear all</button>
        <button class="ngl-btn ngl-btn-primary ${allReady ? '' : 'disabled'}" data-action="run-build" ${allReady ? '' : 'disabled'}>Run build →</button>
      </div>
      <input type="file" id="arBuildFileInput" accept=".xlsx,.xls" multiple style="display:none" />
    </div>
  `;
}
```

- [ ] **Step 1.5: Extract slot-row HTML into a helper**

The current `renderDrop()` has the per-slot HTML inline. Pull it out so `pageDropHtml()` can reuse it cleanly:

```js
function slotRowHtml(slot, stepNum) {
  const input = arState.buildInputs[slot.key];
  if (input && input.parsed) {
    return `
      <div class="ar-drop-row done" data-slot="${slot.key}">
        <div class="ar-drop-num">✓</div>
        <div class="ar-drop-body">
          <div class="ar-drop-title">${escHtml(slot.label)} <span class="ar-drop-hint">${escHtml(slot.hint)}</span></div>
          <div class="ar-drop-done">Parsed · <span class="filename">${escHtml(input.file.name)}</span> · ${escHtml(slot.doneSummary(input.parsed))}</div>
        </div>
        <div class="ar-drop-zone-done">
          <span class="change-link" data-action="clear" data-slot="${slot.key}">Use a different file</span>
        </div>
      </div>`;
  }
  if (input && input.error) {
    return `
      <div class="ar-drop-row error" data-slot="${slot.key}">
        <div class="ar-drop-num">!</div>
        <div class="ar-drop-body">
          <div class="ar-drop-title">${escHtml(slot.label)} <span class="ar-drop-hint">${escHtml(slot.hint)}</span></div>
          <div class="ar-drop-error">Couldn't read <span class="filename">${escHtml(input.file.name)}</span> — ${escHtml(input.error)}</div>
        </div>
        <div class="ar-drop-zone" data-action="open-picker" data-slot="${slot.key}">
          <div class="icon">📂</div>
          <div class="label">Try again</div>
          <div class="sub">drop or click</div>
        </div>
      </div>`;
  }
  return `
    <div class="ar-drop-row" data-slot="${slot.key}">
      <div class="ar-drop-num">${stepNum}</div>
      <div class="ar-drop-body">
        <div class="ar-drop-title">${escHtml(slot.label)} <span class="ar-drop-hint">${escHtml(slot.hint)}</span></div>
        <p>${escHtml(slot.blurb)}</p>
      </div>
      <div class="ar-drop-zone" data-action="open-picker" data-slot="${slot.key}">
        <div class="icon">📂</div>
        <div class="label">Drop file</div>
        <div class="sub">or click to browse</div>
      </div>
    </div>`;
}
```

Then delete the equivalent inline mapping from the old `renderDrop()` (and delete `renderDrop()` itself in step 1.9 below).

- [ ] **Step 1.6: Wire page-level events**

Add `wirePageEvents()` — handles click, drag/drop, and file-input change on the page host:

```js
function wirePageEvents(root) {
  root.addEventListener('click', onClick);
  const dropZone = root.querySelector('.ar-build-page');
  if (dropZone) {
    dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-active'); });
    dropZone.addEventListener('dragleave', e => { if (e.target === dropZone) dropZone.classList.remove('drag-active'); });
    dropZone.addEventListener('drop', onDrop);
  }
  const fileInput = root.querySelector('#arBuildFileInput');
  if (fileInput) fileInput.addEventListener('change', onFileInputChange);
}
```

- [ ] **Step 1.7: Add the preview modal lifecycle (`openPreview`, `closePreview`)**

Add directly below `wirePageEvents`. The pattern: create-then-rerender on open, remove-then-rerender on close — `rerender()` figures out which surface is active.

```js
function openPreview() {
  if (!document.getElementById('arBuildPreview')) {
    const node = document.createElement('div');
    node.id = 'arBuildPreview';
    node.className = 'ar-build-modal-overlay';
    document.body.appendChild(node);
  }
  rerender();
}

function closePreview() {
  const node = document.getElementById('arBuildPreview');
  if (node) node.remove();
  arState.buildResult = null;
  arState.buildOpenKpi = null;
  rerender();
}
```

- [ ] **Step 1.8: Rename `renderPreview()` → `previewModalHtml()`**

The existing `renderPreview()` returns the preview HTML. Rename it to `previewModalHtml()` for naming symmetry with `pageDropHtml()`. The body stays identical except: in the close-x button, change the data-action from `close` (the old modal-wide close) to `close-preview`, since the page persists after the preview closes.

Find this line near the top of `renderPreview()`:

```js
<span class="close-x" data-action="close" style="margin-left:8px">×</span>
```

Replace with:

```js
<span class="close-x" data-action="close-preview" style="margin-left:8px">×</span>
```

- [ ] **Step 1.9: Delete `renderDrop()`, `render()`, and `close()` — they're replaced**

Search for the function declarations `function renderDrop()`, `function render()`, and `function close()`. Delete all three. They were the old modal-only flow.

- [ ] **Step 1.10: Update `runBuild()` to open the preview modal**

Find this block at the end of `runBuild()`:

```js
result.__buildMs = performance.now() - t0;
result.__targetDate = targetDate;
arState.buildResult = result;
arState.buildOpenKpi = null;
render();
```

Replace with:

```js
result.__buildMs = performance.now() - t0;
result.__targetDate = targetDate;
arState.buildResult = result;
arState.buildOpenKpi = null;
openPreview();
```

- [ ] **Step 1.11: Update the click handler `onClick()`**

In `onClick`, replace the `close` branch and every `render()` call with the new equivalents:

Find:

```js
if (action === 'close') return close();
```

Replace with:

```js
if (action === 'close-preview') return closePreview();
if (action === 'cancel-page') {
  for (const s of SLOTS) arState.buildInputs[s.key] = null;
  arState.buildResult = null;
  return rerender();
}
```

The `back-to-drop` branch currently reads:

```js
if (action === 'back-to-drop') {
  arState.buildResult = null;
  arState.buildOpenKpi = null;
  return render();
}
```

Replace with:

```js
if (action === 'back-to-drop') {
  return closePreview();
}
```

(`closePreview` already clears `buildResult` and `buildOpenKpi`.)

Replace **every** remaining `return render();` and `render();` call inside `onClick()` with `rerender()`. There are two — one in the `clear` branch, and one in the `toggle-kpi` branch. After the change both branches read:

```js
if (action === 'clear') {
  const slot = t.dataset.slot;
  arState.buildInputs[slot] = null;
  return rerender();
}
// ...
if (action === 'toggle-kpi') {
  const k = t.dataset.kpi;
  arState.buildOpenKpi = arState.buildOpenKpi === k ? null : k;
  return rerender();
}
```

- [ ] **Step 1.12: Update `routeFileToSlot()`, `pickFolder()`, and `saveWorkbook()`**

Replace every remaining `render()` call with `rerender()`, and the `close()` call in `saveWorkbook` with `closePreview()`.

Find in `routeFileToSlot`:
```js
  render();
}
```
Replace with:
```js
  rerender();
}
```

Find in `pickFolder`:
```js
    render();
```
Replace with:
```js
    rerender();
```

Find in `saveWorkbook` near the end (after the `arLoadWorkbook(file)` fallback path):
```js
  close();
}
```
Replace with:
```js
  closePreview();
}
```

- [ ] **Step 1.13: Replace the `window.arOpenBuildModal` export**

Find at the bottom of the file:

```js
window.arOpenBuildModal = arOpenBuildModal;
```

Replace with:

```js
window.arRenderBuildPage = arRenderBuildPage;
```

- [ ] **Step 1.14: Verify syntax**

Run: `node desktop/check-js.js`
Expected: `===CHECK_JS_OK===` printed (the script exits 0 if all changed JS parses cleanly).

- [ ] **Step 1.15: Commit**

```bash
git add app/assets/js/tools/ar-dashboard/ar-dashboard-build-ui.js
git commit -m "refactor(ar-dashboard): split page drop from preview modal in build-ui

Page-level renderer arRenderBuildPage(view) renders the drop UI into a
host element. Preview stays as a modal opened via openPreview(). Removes
the combined arOpenBuildModal entrypoint; renderPageContent()/closePreview()
drive the new state machine."
```

---

## Task 2: Rewrite `ar-dashboard.js` empty state

**Files:**
- Modify: `app/assets/js/tools/ar-dashboard/ar-dashboard.js`

The current `renderEmptyState(view)` renders a single large drop zone for pre-built workbooks plus a small footer link to open the build modal. Replace with the page-shell layout that hosts the build flow primary and the pre-built drop zone secondary.

- [ ] **Step 2.1: Update the import to include `arRenderBuildPage`**

Find:

```js
import './ar-dashboard-build-ui.js';
```

Replace with:

```js
import { arRenderBuildPage } from './ar-dashboard-build-ui.js';
```

- [ ] **Step 2.2: Rewrite `renderEmptyState()`**

Replace the entire current function body with:

```js
function renderEmptyState(view) {
  const today = new Date().toLocaleDateString('en-US', {
    weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
  });
  view.innerHTML = `
    <div class="ar-empty-shell">
      <div class="ar-empty-header">
        <h1>Build today's AR workbook</h1>
        <p class="subtitle">Drop the daily files; the engine reconciles them against yesterday's workbook.</p>
        <p class="date-line">${today}</p>
      </div>
      <div id="arBuildPageHost"></div>
      <div class="ar-empty-divider">
        <span>or</span>
      </div>
      <div class="ar-empty-secondary">
        <h2>Already have a pre-built workbook?</h2>
        <div class="ar-secondary-drop" id="arPrebuiltDropZone">
          <div class="drop-icon">📄</div>
          <div class="drop-title">Drop AR_AGING_MM_DD_YYYY.xlsx</div>
          <div class="drop-help">or click to browse · accepts .xlsx · .xls</div>
        </div>
        <input type="file" id="arFileInput" accept=".xlsx,.xls" style="display:none" />
      </div>
    </div>
  `;
  // Mount the build flow into its host
  const host = view.querySelector('#arBuildPageHost');
  arRenderBuildPage(host);

  // Secondary pre-built drop zone — keep the existing behavior
  const dz = view.querySelector('#arPrebuiltDropZone');
  const fi = view.querySelector('#arFileInput');
  dz.addEventListener('click', () => fi.click());
  fi.addEventListener('change', handleFileSelected);
  dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('drop-hover'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('drop-hover'));
  dz.addEventListener('drop', e => {
    e.preventDefault();
    dz.classList.remove('drop-hover');
    const file = e.dataTransfer.files && e.dataTransfer.files[0];
    if (file && typeof window.arLoadWorkbook === 'function') {
      window.arLoadWorkbook(file);
    }
  });
}
```

- [ ] **Step 2.3: Verify syntax**

Run: `node desktop/check-js.js`
Expected: `===CHECK_JS_OK===`.

- [ ] **Step 2.4: Commit**

```bash
git add app/assets/js/tools/ar-dashboard/ar-dashboard.js
git commit -m "feat(ar-dashboard): build flow primary on empty state

Empty state now leads with 'Build today's AR workbook' + the inlined
5-drop UI. Pre-built drop zone moves to a small secondary section below
an 'or' divider."
```

---

## Task 3: Add `.ar-empty-*` and `.ar-build-page*` CSS

**Files:**
- Modify: `app/assets/css/styles.css`

The existing `.ar-build-modal*` styles drive the preview modal (still used). Add new page-level classes for the empty-state shell and the inlined build host. Append to the end of the file.

- [ ] **Step 3.1: Append the page CSS block**

Append to the end of `app/assets/css/styles.css`:

```css
/* =========================================================================
   AR Dashboard — Empty state (build flow primary, pre-built secondary)
   ========================================================================= */

.ar-empty-shell {
  max-width: 760px; margin: 0 auto; padding: 24px 20px 40px;
  font-family: 'Segoe UI', -apple-system, sans-serif; color: #1e293b;
}
.ar-empty-header { text-align: center; margin-bottom: 18px; }
.ar-empty-header h1 {
  font-size: 1.45rem; font-weight: 800; color: #0f172a; margin: 0 0 4px;
}
.ar-empty-header .subtitle {
  font-size: 0.86rem; color: #475569; line-height: 1.5; margin: 0 0 6px;
}
.ar-empty-header .date-line {
  font-size: 0.74rem; color: #94a3b8; font-weight: 600; margin: 0;
}

/* The build host — the 5-slot drop UI lives inside here */
.ar-build-page {
  background: #fff; border: 1px solid #e2e8f0; border-radius: 10px;
  padding: 16px 18px; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
}
.ar-build-page.drag-active {
  box-shadow: 0 0 0 3px rgba(234, 88, 12, 0.35), 0 1px 3px rgba(0, 0, 0, 0.04);
  border-color: #ea580c;
}
.ar-build-page-intro {
  font-size: 0.8rem; color: #475569; padding: 0 0 12px; line-height: 1.5;
}
.ar-build-page-intro b { color: #0f172a; }
.ar-build-page-footer {
  padding: 12px 0 0; margin-top: 12px;
  border-top: 1px solid #e2e8f0;
  display: flex; gap: 10px; align-items: center;
}
.ar-build-page-footer .footer-note {
  font-size: 0.74rem; color: #64748b; margin-right: auto;
  display: flex; align-items: center; gap: 8px;
}

/* "or" divider between primary build flow and secondary drop zone */
.ar-empty-divider {
  display: flex; align-items: center; gap: 12px;
  margin: 22px 0 18px; color: #94a3b8;
  font-size: 0.74rem; text-transform: uppercase;
  letter-spacing: 0.08em; font-weight: 700;
}
.ar-empty-divider::before,
.ar-empty-divider::after {
  content: ''; flex: 1; height: 1px; background: #e2e8f0;
}

/* Secondary pre-built drop zone */
.ar-empty-secondary { text-align: center; }
.ar-empty-secondary h2 {
  font-size: 0.86rem; font-weight: 700; color: #475569;
  margin: 0 0 8px;
}
.ar-secondary-drop {
  background: #fff; border: 1px dashed #cbd5e1; border-radius: 10px;
  padding: 18px 16px; cursor: pointer;
  transition: border-color 0.12s, background 0.12s;
}
.ar-secondary-drop:hover,
.ar-secondary-drop.drop-hover {
  border-color: #ea580c; background: #fff7ed;
}
.ar-secondary-drop .drop-icon {
  font-size: 1.4rem; color: #94a3b8; margin-bottom: 4px;
}
.ar-secondary-drop .drop-title {
  font-size: 0.86rem; font-weight: 700; color: #0f172a;
}
.ar-secondary-drop .drop-help {
  font-size: 0.74rem; color: #64748b; margin-top: 2px;
}
```

- [ ] **Step 3.2: Commit**

```bash
git add app/assets/css/styles.css
git commit -m "style(ar-dashboard): page-level styles for build-primary empty state

Adds .ar-empty-shell, .ar-build-page, .ar-empty-divider, and
.ar-secondary-drop classes used by the new empty state. Existing
.ar-build-modal* classes remain (still drive the preview modal)."
```

---

## Task 4: Relabel `Load different workbook` → `Build different day`

**Files:**
- Modify: `app/assets/js/tools/ar-dashboard/ar-dashboard-views.js:42`

- [ ] **Step 4.1: Find the link text**

In `app/assets/js/tools/ar-dashboard/ar-dashboard-views.js`, line 42 reads:

```js
<a class="right-link" id="arUnloadBtn">Load different workbook →</a>
```

- [ ] **Step 4.2: Replace the link text**

Change the text content (id and class stay the same so the existing click handler binds correctly):

```js
<a class="right-link" id="arUnloadBtn">Build different day →</a>
```

- [ ] **Step 4.3: Verify syntax**

Run: `node desktop/check-js.js`
Expected: `===CHECK_JS_OK===`.

- [ ] **Step 4.4: Commit**

```bash
git add app/assets/js/tools/ar-dashboard/ar-dashboard-views.js
git commit -m "feat(ar-dashboard): relabel unload link to 'Build different day →'

The loaded view's existing 'Load different workbook →' link now reads
'Build different day →' to match the new empty state where the page is
the build flow. Click handler unchanged."
```

---

## Task 5: Smoke test — manual verification

No automated browser harness exists. Run the app and confirm the scenarios below all behave correctly.

- [ ] **Step 5.1: Open the AR Dashboard from a fresh state**

Launch the desktop app. Navigate to AR Dashboard. With no workbook loaded, confirm:
- Page header reads "Build today's AR workbook" with today's date below
- The 5-slot build UI is the dominant element on the page
- "or" divider separates the build flow from the secondary section
- "Already have a pre-built workbook?" appears below the divider with a small drop zone

- [ ] **Step 5.2: Drop all 5 daily files at once**

Drag all 5 files from `C:/Users/Joseph/Desktop/NGL ACCOUNTING SERVICE - TEST DATA/AR_AGING_assets/build-2026-05-15/` onto the build host. Confirm:
- All 5 slots auto-route by filename match (rows turn green with `✓`)
- Footer reads "5 of 5 ready · ready to build"
- "Run build →" button enables

- [ ] **Step 5.3: Click "Run build →"**

Confirm the preview modal opens as before: headline + 4 KPI tiles + source strip + save folder pill + Back / Save buttons.

- [ ] **Step 5.4: Click Back in the preview**

Confirm:
- Preview modal closes
- The page-level build flow is still visible
- All 5 slots are STILL FILLED (the drop state persists; `closePreview()` clears only `buildResult` and `buildOpenKpi`, not `buildInputs`, so the user does not have to re-drop)
- "Run build →" button is still enabled because 5/5 slots remain ready

- [ ] **Step 5.5: Re-drop the 5 files and Save**

Confirm:
- Preview shows totals
- "Save & open dashboard →" writes the workbook
- The dashboard transitions to the loaded view with the new workbook

- [ ] **Step 5.6: Click "Build different day →" in the loaded view's data bar**

Confirm:
- Returns to the empty state (the build flow page)
- Slots are empty, ready for a new build

- [ ] **Step 5.7: Drop a pre-built workbook on the secondary zone**

Drag any `AR_AGING_MM_DD_YYYY.xlsx` file onto the small secondary drop zone. Confirm:
- The dashboard loads the file (same behavior as before the change)

---

## Task 6: Ship v2.78.9

- [ ] **Step 6.1: Bump VERSION**

Edit `desktop/VERSION` from `2.78.8` to `2.78.9`.

- [ ] **Step 6.2: Run the full build pipeline**

Run via PowerShell (the empty-stdin pattern documented in memory `feedback_use_runbuild_for_rebuild.md`):

```powershell
$root = "C:\Users\Joseph\Desktop\NGL ACCOUNTING SERVICE"
if (-not (Test-Path "$root\empty_stdin.txt")) { New-Item -ItemType File -Path "$root\empty_stdin.txt" -Force | Out-Null }
Remove-Item -Path "$root\build.log","$root\build.err" -ErrorAction SilentlyContinue
Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "`"$root\desktop\runbuild.bat`"" -WorkingDirectory $root -RedirectStandardInput "$root\empty_stdin.txt" -RedirectStandardOutput "$root\build.log" -RedirectStandardError "$root\build.err" -NoNewWindow -PassThru
```

Wait for `===ELECTRON_BUILD_DONE===` in `build.log`.

- [ ] **Step 6.3: Verify installer exists**

```bash
ls "desktop/dist/NGL_ACCOUNTING_INSTALLER_v2.78.9.exe" "desktop/dist/latest.yml"
```

Expected: both files listed, sizes > 0.

- [ ] **Step 6.4: Stage and commit VERSION + package.json**

```bash
git add desktop/VERSION desktop/package.json
git commit -m "chore(release): bump VERSION to 2.78.9 — build-primary empty state"
```

- [ ] **Step 6.5: Push to origin/main**

```bash
git push origin main
```

- [ ] **Step 6.6: Create GitHub release with installer + latest.yml**

```bash
gh release create v2.78.9 \
  "desktop/dist/NGL_ACCOUNTING_INSTALLER_v2.78.9.exe" \
  "desktop/dist/latest.yml" \
  --title "v2.78.9 — AR Dashboard build flow primary" \
  --notes "Flips the AR Dashboard empty state: building today's workbook from the 5 daily files is now the headline action (inlined on the page). Loading a pre-built workbook moves to a small secondary drop zone below an 'or' divider. The loaded-view 'Load different workbook' link is relabeled to 'Build different day →'. Engine and writer are unchanged from v2.78.8."
```

Expected: command prints the release URL.

---

## Acceptance criteria recap

1. ✅ Opening the AR Dashboard with no workbook loaded shows the 5-drop build UI as the primary surface; the pre-built drop zone is visually smaller and below an "or" divider.
2. ✅ Dropping all 5 files + clicking Build → opens the preview modal and shows correct totals.
3. ✅ Saving from the preview modal writes the workbook and auto-loads it into the dashboard.
4. ✅ The loaded view's data-bar link reads `Build different day →` and returns to the empty state.
5. ✅ The secondary drop zone for pre-built workbooks still works.
6. ✅ Engine and writer are untouched — no regression to the 99.71% correctness verified in v2.78.7/v2.78.8.
