# Merge Tool v2 — Milestone 1 (Foundation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a v2 merge UI shell that lives alongside the legacy single-screen merge tool, gated by a Settings toggle. Empty state, Loading state, and a stubbed Review state render correctly. No real merge logic is wired yet — that comes in M2/M3/M4.

**Architecture:** New ES module at `app/assets/js/tools/merge/merge-v2.js` implements a 4-state machine (Empty → Review → Ready → Done) using the same vanilla-JS pattern as the existing tools (no framework). A new `<div id="mergeToolViewV2">` container in `index.html` holds the v2 UI; `switchTool('merge')` chooses between the legacy `mergeToolView` and `mergeToolViewV2` based on a `localStorage` flag toggled from Settings. CSS is appended to the shared `app/assets/css/styles.css` under a `[merge-v2]` comment block.

**Tech Stack:** Vanilla HTML/CSS/JS (ES modules), no build step for the web app, Tailwind via CDN, pdf-lib via CDN. Electron + PyInstaller for packaging.

**Spec reference:** `docs/superpowers/specs/2026-05-05-merge-tool-ux-refinement-design.md`

---

## File Structure

**Create:**
- `app/assets/js/tools/merge/merge-v2.js` (~250 lines for M1) — state machine, render functions for Empty/Loading/Review-stub. Exports `initMergeV2()` and `setStateV2(name)`.

**Modify:**
- `app/index.html`
  - Add `<div id="mergeToolViewV2" style="display:none;">…</div>` after the existing `mergeToolView` (~line 945).
  - Add a v2 toggle checkbox in the settings view (around line ~1250 — find the existing settings panel structure and append).
  - Add `<script type="module">` import for merge-v2.js (or import via app.js).
- `app/assets/js/app.js`
  - Update `switchTool('merge')` (around line 341) to read `localStorage.getItem('mergeToolV2')` and show v2 view when enabled.
  - Wire `initMergeV2()` call when v2 view is shown for the first time.
- `app/assets/css/styles.css`
  - Append the v2 CSS block (centered-stage, big-drop, app-layout, sidebar already exists, top-bar, doc-pill, fix-error-btn, output-row/group, ds-* sidebar, etc.) — copied from the mockup file.
- `app/assets/js/tools/settings/settings.js`
  - Add a `setMergeToolV2(enabled)` setter that writes `localStorage` and re-renders.
- `desktop/VERSION`
  - Bump from `2.44` → `2.45.0`.

**Out of scope for M1:**
- Excel parsing for the v2 flow — comes in M2.
- Container-row rendering with real data — M2.
- Fetch pipeline integration — M3.
- Sidebar with real auto-save — M3.
- Run Merge wiring — M4.
- Removing the legacy UI — M5.

---

## Important guidance for the engineer

- **No TDD here.** This is frontend visual scaffolding. The verification step is manual: load the packaged app, toggle the v2 flag in Settings, drop an Excel file, watch the state transition. Tests live with logic (M2+ engine wiring), not with CSS.
- **Don't touch the legacy `merge.js`.** It must remain fully functional. Users can flip back to legacy from Settings at any time.
- **Mockup is the visual source of truth.** When porting CSS/markup, copy verbatim from `app/mockups/merge-tool-redesign.html`. Don't reinterpret — the mockup is what was approved.
- **Use existing helpers.** `app/assets/js/shared/utils.js` has `escHtml`, `fmtSize`, file-reader helpers. Reuse them, don't reimplement.
- **Don't add a build step.** All scripts load directly via `<script type="module">`. CSS via `<link>`.
- **Standing rule from CLAUDE.md:** Every rebuild must complete the full pipeline — bump VERSION, build, commit, push, GH release. Don't stop at "built locally."
- **Use `runbuild.bat`, not `build-all.bat`** for the agent rebuild step (per `feedback_use_runbuild_for_rebuild` memory).

---

## Task 1: Bump VERSION + add v2 settings toggle

**Files:**
- Modify: `desktop/VERSION`
- Modify: `app/assets/js/tools/settings/settings.js`
- Modify: `app/index.html` (settings view section)

- [ ] **Step 1: Bump version**

Edit `desktop/VERSION`. Replace `2.44` with `2.45.0`.

- [ ] **Step 2: Locate the settings view in index.html**

Run: `grep -n 'id="settingsView"' "app/index.html"`
Note the line number. The settings view is a `<div id="settingsView">` somewhere around line 1250–1450.

- [ ] **Step 3: Find the settings panel structure**

Read 80 lines starting from the line found in Step 2. Look for an existing `<div class="settings-section">` or similar container that holds toggles (alerting, debug, etc.). The new v2 toggle goes inside that container (or its own `settings-section`).

- [ ] **Step 4: Add the v2 toggle markup**

Append this block inside the settings panel (after any existing toggle, before the closing tag of the section):

```html
<div class="settings-section" id="mergeV2Section">
  <h3 class="settings-section-title">Merge Tool — Beta</h3>
  <p style="color:#64748b; font-size:0.86rem; margin-bottom:10px;">
    Try the new step-by-step merge flow. Reload the app after toggling.
  </p>
  <label style="display:flex; align-items:center; gap:10px; cursor:pointer;">
    <input type="checkbox" id="settingsMergeV2Toggle" onchange="setMergeToolV2(this.checked)" />
    <span>Use new Merge Tool UI</span>
  </label>
</div>
```

- [ ] **Step 5: Add `setMergeToolV2()` to settings.js**

Open `app/assets/js/tools/settings/settings.js`. Locate the `settingsLoad()` function. Add this function next to it:

```javascript
export function setMergeToolV2(enabled) {
  if (enabled) {
    localStorage.setItem('mergeToolV2', '1');
  } else {
    localStorage.removeItem('mergeToolV2');
  }
  // The toggle takes effect on the next switchTool('merge') call,
  // OR on the next page load.
  alert(enabled
    ? 'New Merge UI enabled. Reload the app to see it.'
    : 'New Merge UI disabled. Reload the app to revert.');
}
```

- [ ] **Step 6: Sync the checkbox state in `settingsLoad()`**

Inside `settingsLoad()`, after any existing toggle initialization, append:

```javascript
const v2Toggle = document.getElementById('settingsMergeV2Toggle');
if (v2Toggle) v2Toggle.checked = !!localStorage.getItem('mergeToolV2');
```

- [ ] **Step 7: Expose the function on `window`**

Find where other settings functions are exposed in the same file (look for `window.settingsLoad = settingsLoad;` or similar). Add:

```javascript
window.setMergeToolV2 = setMergeToolV2;
```

- [ ] **Step 8: Manual verify**

Open `app/index.html` directly in a browser (file://). Click `Settings` in the sidebar. The new "Merge Tool — Beta" section should be visible with an unchecked toggle. Click it; an alert should appear, and reloading should show the checkbox still checked. Disable it again before continuing.

- [ ] **Step 9: Commit**

```bash
git add desktop/VERSION app/index.html app/assets/js/tools/settings/settings.js
git commit -m "feat(merge-v2): scaffold settings toggle + version bump

Adds 'Use new Merge Tool UI' toggle in Settings, gated by
localStorage.mergeToolV2. No view yet — Task 2 wires the new
container; Task 4 wires switchTool() to honor the flag.

VERSION → 2.45.0 (M1 of 5 milestones for the v2 merge tool port +
refinement; spec at docs/superpowers/specs/2026-05-05-merge-tool-
ux-refinement-design.md)."
```

---

## Task 2: Add `<div id="mergeToolViewV2">` container

**Files:**
- Modify: `app/index.html` (add new container after the existing `mergeToolView`)

- [ ] **Step 1: Locate the end of `mergeToolView`**

Run: `grep -n 'id="mergeToolView"' "app/index.html"`
Note the start line. Find the matching closing `</div>` for that element by reading forward. It's a long block — scroll through with `Read` until you find the closing tag (around line 940–960).

- [ ] **Step 2: Insert the v2 container immediately after**

After the closing `</div>` of `mergeToolView`, paste:

```html
<!-- ═══════════════ MERGE TOOL V2 (BETA) ═══════════════ -->
<div id="mergeToolViewV2" style="display:none;">
  <!-- Tool header — matches existing tool-header pattern, plus header action buttons -->
  <div class="v2-tool-header">
    <div class="v2-tool-title-group">
      <div class="v2-tool-title-icon">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M6 21V9a9 9 0 0 0 9 9"/>
        </svg>
      </div>
      <div>
        <div class="v2-tool-title">Merging Tool</div>
        <div style="font-size:0.74rem; color:#94a3b8;">Combine invoices + PODs into per-container PDFs</div>
      </div>
    </div>
    <div class="v2-tool-actions">
      <button class="header-action-btn back-to-ready-btn" id="v2BtnBackToReady" style="display:none;">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
        Back to Ready
      </button>
      <button class="header-action-btn new-merge-btn" id="v2BtnNewMerge" style="display:none;">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        New Merge
      </button>
    </div>
  </div>
  <!-- Work area — populated by merge-v2.js setStateV2() -->
  <div class="v2-work-area" id="v2WorkArea"></div>
  <!-- Hidden inputs for file selection -->
  <input type="file" id="v2ExcelInput" accept=".xlsx,.xls,.csv" style="display:none;" />
  <input type="file" id="v2PdfInput" accept=".pdf" multiple style="display:none;" />
</div>
```

- [ ] **Step 3: Manual verify**

Reload `app/index.html` in browser. Click Merge in sidebar. The legacy view still shows (because the toggle is off). No console errors.

- [ ] **Step 4: Commit**

```bash
git add app/index.html
git commit -m "feat(merge-v2): add mergeToolViewV2 container shell

New container hidden by default. Has v2 tool header with the two
header action buttons (Back to Ready, New Merge — both display:none
until M4) and an empty work area populated by merge-v2.js.

Hidden file inputs for Excel + PDF live here so they're scoped to
the v2 view (avoids clashing with legacy excelInput/pdfInput)."
```

---

## Task 3: Append v2 CSS to styles.css

**Files:**
- Modify: `app/assets/css/styles.css` (append to end)

- [ ] **Step 1: Read the mockup CSS block**

Open `app/mockups/merge-tool-redesign.html`. The `<style>` block starts around line 6 and runs through line ~940. We want a subset — the rules that don't already exist in `styles.css`.

- [ ] **Step 2: Identify the rules to copy**

Skip these (they already exist or conflict with the live app's overall layout):
- `* { box-sizing: ... }` global reset (already handled)
- `body { ... }` (already styled)
- `.sidebar`, `.sidebar-logo`, `.sidebar-nav-item` etc. (live app already has its own sidebar)
- `.app-layout` (live app uses its own layout)

Copy these rules verbatim, prefixing each selector with `#mergeToolViewV2 ` to scope them to the v2 view (so they don't leak into other tools):

- `.centered-stage`, `.centered-stage h1`, `.centered-stage .subtitle`
- `.big-drop`, `.big-drop:hover`, `.big-drop .drop-icon`, `.big-drop .drop-title`, `.big-drop .drop-help`, `.big-drop .drop-types`, `.big-drop.kind-excel:hover .drop-icon`, `.big-drop.kind-pdf .drop-icon`, `.big-drop.loading`, `.big-drop.loading:hover`, `.big-drop .big-spinner`
- `@keyframes spin` (declare once, NOT scoped — it's a keyframe)
- `.hint-chip`, `.hint-chip .step-num`
- `.stage-file-summary`
- `.review-card`, `.review-card.has-issues`, `.review-card.all-clear`, `.review-head`, `.review-head .badge`, `.review-head .badge.warn`, `.review-head .badge.ok`, `.review-head h3`, `.review-head .sub`
- `.issue-group`, `.issue-group:last-child`, `.issue-group .igh`, `.issue-row`, `.issue-row:last-child`, `.issue-row .ref`, `.issue-row .where`
- `.review-actions`, `.review-card.all-clear .review-actions`, `.review-actions .secondary-btn`, `.review-actions .secondary-btn:hover`
- `.pdf-drop-card` and all variants
- `.val-badge` and variants
- `.top-action-btn` and variants
- `.sidebar-backdrop` and variants
- `.detail-sidebar` and all `.ds-*` rules
- `.merge-table` and all variants
- `.merge-table .check-col`
- `.mono`, `.mono-sub`
- `.doc-row`, `.doc-pill` and all 4 state variants (ok/fallback/miss/queued)
- `.status-text` and variants
- `.small-spinner`
- `.row-action`, `.fix-error-btn`
- `.progress-line` and children
- `.bottom-utilities`, `.util-card`, `.util-header`, `.util-body`
- `.outputs-card`, `.output-row`, `.output-row.done`, `.output-row.pending`, `.output-info`, `.output-name`, `.output-meta`, `.output-actions`, `.output-action`, `.output-action.primary`, `.output-run-hint`
- `.output-group`, `.output-group-head`, `.output-group-title`, `.output-group-completed *`, `.output-group-count`, `.output-group-action`
- `.tool-switcher-btn` (rename to `.v2-tool-switcher-btn` if it conflicts), `.header-action-btn`, `.header-action-btn.new-merge-btn`
- `.tabs-row`, `.tabs`, `.tab`, `.tab.active`, `.tab .count`, `.tab.active .count`, `.tab.has-issues .count`
- `.toolbar`, `.toolbar input.search`, `select.sort-select`, `.toolbar .filter-meta`
- `.merge-modes-card`, `.merge-modes-help`, `.mode-actions-grid`, `.mode-action` and variants, `.merge-modes-footer`
- `.ready-action-bar`, `.ready-status`, `.ready-action-right`, `.merge-btn`, `.merge-btn.primary-merge`, `.merge-btn .count-badge`
- `.controls-line`, `.dropdown-btn`, `.summary-line`
- `.top-bar`, `.file-summary` and children
- `.containers-preview`, `.container-list`, `.container-list .chip`, `.container-list .chip-more`
- `.v2-tool-header`, `.v2-tool-title-group`, `.v2-tool-title`, `.v2-tool-title-icon`, `.v2-tool-actions`, `.v2-work-area` — these are NEW (the mockup uses different class names; we use `v2-` prefix to avoid conflict). See Step 3 below for the markup; CSS for them is straight copies of the mockup's `.tool-header`, `.tool-title-group`, `.tool-title`, `.tool-title-icon`, `.tool-actions`, `.work-area` rules.

- [ ] **Step 3: Add the v2-prefixed tool-header rules**

These are NEW rules (not in the mockup as `v2-*` — we're prefixing). Append:

```css
/* ════════════════════════════════════════════════
   MERGE V2 — tool header (mirrors live app's tool-header sizing
   but uses v2- prefix so scoping stays clean)
   ════════════════════════════════════════════════ */
#mergeToolViewV2 .v2-tool-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 24px; border-bottom: 1px solid #e2e8f0; background: #fff;
}
#mergeToolViewV2 .v2-tool-title-group { display: flex; align-items: center; gap: 12px; }
#mergeToolViewV2 .v2-tool-title { font-size: 1.1rem; font-weight: 700; color: #0f172a; }
#mergeToolViewV2 .v2-tool-title-icon {
  width: 32px; height: 32px; border-radius: 8px;
  background: #fff7ed; color: #ea580c;
  display: flex; align-items: center; justify-content: center;
}
#mergeToolViewV2 .v2-tool-actions { display: flex; align-items: center; gap: 10px; }
#mergeToolViewV2 .v2-work-area { padding: 24px 28px; min-height: calc(100vh - 200px); }
```

- [ ] **Step 4: Append a header marker comment, then all CSS rules**

At the end of `app/assets/css/styles.css`, append:

```css

/* ════════════════════════════════════════════════════════════════════
   MERGE TOOL V2 (BETA) — scoped to #mergeToolViewV2
   Source of truth: app/mockups/merge-tool-redesign.html
   Rules below are copied verbatim from the mockup's <style> block,
   with each non-keyframe selector prefixed with `#mergeToolViewV2 `
   to keep them isolated.
   ════════════════════════════════════════════════════════════════════ */
```

Then below it, paste the prefixed rules from Step 2. (Use a text-find with regex `^\.([\w-]+)` → `#mergeToolViewV2 .$1` if your editor supports it, or do it manually.)

Below them, append the keyframes (these stay un-prefixed since they're global):

```css
@keyframes spin { to { transform: rotate(360deg); } }
```

(If `@keyframes spin` already exists in `styles.css`, skip this — don't redefine.)

- [ ] **Step 5: Manual verify**

Reload `app/index.html`. Open DevTools → Sources → `assets/css/styles.css`. Search for `#mergeToolViewV2`. Confirm the rules are present. Click through each existing tool (Home, Merge legacy, Invoice Sender, Customers, Settings) — confirm no visual regressions. The legacy merge view should look identical to before.

- [ ] **Step 6: Commit**

```bash
git add app/assets/css/styles.css
git commit -m "feat(merge-v2): port mockup CSS scoped to #mergeToolViewV2

All visual rules from app/mockups/merge-tool-redesign.html copied
to styles.css with each selector prefixed by #mergeToolViewV2 so
the legacy merge tool and all other views are unaffected. Keyframes
remain global. Adds v2-prefixed tool-header rules for the v2-only
header action buttons."
```

---

## Task 4: Wire `switchTool('merge')` to honor the toggle

**Files:**
- Modify: `app/assets/js/app.js`

- [ ] **Step 1: Open `app.js` and locate `switchTool` (around line 322)**

The function has a block of `display = 'none'` calls followed by `if (tool === 'merge') { ... }`. We need to choose between v1 and v2 there.

- [ ] **Step 2: Hide the v2 view alongside other views**

In the "Hide all views" block, add:

```javascript
document.getElementById('mergeToolViewV2').style.display = 'none';
```

- [ ] **Step 3: Branch on the toggle when showing the merge tool**

Replace this:

```javascript
} else if (tool === 'merge') {
  document.getElementById('mergeToolView').style.display = '';
}
```

With:

```javascript
} else if (tool === 'merge') {
  if (localStorage.getItem('mergeToolV2')) {
    document.getElementById('mergeToolViewV2').style.display = '';
    if (window.initMergeV2) window.initMergeV2();
  } else {
    document.getElementById('mergeToolView').style.display = '';
  }
}
```

- [ ] **Step 4: Manual verify (legacy path)**

Reload. Toggle is OFF (default). Click Merge — legacy view shows. Drop an Excel file — works as before. No regressions.

- [ ] **Step 5: Manual verify (v2 path with empty work area)**

Open Settings → enable the v2 toggle → reload. Click Merge — the v2 view shows but the work area is empty (because `merge-v2.js` doesn't exist yet — that's Task 5). Browser console will warn that `window.initMergeV2` is undefined. That's expected at this checkpoint.

- [ ] **Step 6: Commit**

```bash
git add app/assets/js/app.js
git commit -m "feat(merge-v2): switchTool() routes to v1 or v2 by localStorage

Reads localStorage.mergeToolV2 — when set, mergeToolViewV2 is shown
and initMergeV2() is called. Otherwise the legacy mergeToolView
shows as before. Both views are hidden in the 'hide all' block at
the start of switchTool() so toggling between tools cleans up
correctly."
```

---

## Task 5: Create `merge-v2.js` skeleton with state machine

**Files:**
- Create: `app/assets/js/tools/merge/merge-v2.js`
- Modify: `app/index.html` (add `<script type="module">` import)

- [ ] **Step 1: Create the file**

Create `app/assets/js/tools/merge/merge-v2.js` with this content:

```javascript
// ══════════════════════════════════════════════════════════
//  MERGE TOOL V2 — state machine, render functions
//  Spec: docs/superpowers/specs/2026-05-05-merge-tool-ux-refinement-design.md
//  Source mockup: app/mockups/merge-tool-redesign.html
// ══════════════════════════════════════════════════════════
import { escHtml } from '../../shared/utils.js';

// ── Module-local state ──
const v2State = {
  subMode: 'empty',        // empty | loading | review | fetching | ready | merging | done
  excelFile: null,         // File handle (Step 2 wiring; M2 will parse it)
  pendingMode: null,       // mode about to run (set by startMerge())
  completedModes: [],      // array of mode keys that have produced output this session
  lastCompletedMode: null, // for the success banner / focus
};

const STATE_GROUP = {
  empty: 's1', loading: 's1',
  review: 's2', fetching: 's2',
  ready: 's3',
  merging: 's4', done: 's4',
};

const STATES = {
  s1: () => v2State.subMode === 'loading' ? renderLoading() : renderEmpty(),
  s2: () => v2State.subMode === 'fetching' ? renderFetching() : renderReview(),
  s3: () => renderReady(),
  s4: () => v2State.subMode === 'merging' ? renderMerging() : renderDone(),
};

let _initialized = false;

// ── Public entry — called from app.js when the v2 view is shown ──
export function initMergeV2() {
  if (_initialized) {
    // Re-render in case state changed since last view
    setStateV2(v2State.subMode);
    return;
  }
  _initialized = true;
  // Wire excel input change handler
  const xinput = document.getElementById('v2ExcelInput');
  if (xinput) xinput.addEventListener('change', handleExcelChange);
  // Initial render
  setStateV2('empty');
}

// ── State setter ──
export function setStateV2(name) {
  if (name === 'empty') {
    v2State.completedModes = [];
    v2State.lastCompletedMode = null;
    v2State.excelFile = null;
  }
  v2State.subMode = name;
  const group = STATE_GROUP[name] || name;
  // Header buttons (M1: both stay hidden until M4)
  const back = document.getElementById('v2BtnBackToReady');
  const fresh = document.getElementById('v2BtnNewMerge');
  if (back)  back.style.display  = (group === 's4') ? '' : 'none';
  if (fresh) fresh.style.display = (group === 's2' || group === 's3' || group === 's4') ? '' : 'none';
  // Render
  const renderer = STATES[group];
  const wa = document.getElementById('v2WorkArea');
  if (renderer && wa) wa.innerHTML = renderer();
}

// ── Excel drop trigger (Empty state click) ──
function triggerExcel() {
  document.getElementById('v2ExcelInput').click();
}

function handleExcelChange(e) {
  const file = e.target.files[0];
  if (!file) return;
  v2State.excelFile = file;
  setStateV2('loading');
  // M1: simulated transition. M2 will parse the workbook here.
  setTimeout(() => {
    if (v2State.subMode === 'loading') setStateV2('review');
  }, 1200);
}

// ── State renderers ──

function renderEmpty() {
  return `
    <div class="centered-stage">
      <h1>Start a new merge</h1>
      <p class="subtitle">Upload your Excel manifest first — we'll match PDFs to it next.</p>
      <div class="big-drop kind-excel" onclick="window.v2TriggerExcel()">
        <div class="drop-icon">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="15" x2="21" y2="15"/><line x1="9" y1="3" x2="9" y2="21"/>
          </svg>
        </div>
        <div class="drop-title">Drop Excel Manifest</div>
        <div class="drop-help">Needs a Container Number column to match PDFs automatically</div>
        <div class="drop-types">.xlsx · .xls · .csv</div>
      </div>
      <div class="hint-chip"><span class="step-num">2</span> We'll check the manifest, then fetch from APIs</div>
    </div>
  `;
}

function renderLoading() {
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

function renderReview() {
  // M1 stub: real implementation in M2
  const fname = v2State.excelFile ? escHtml(v2State.excelFile.name) : '(no file)';
  return `
    <div class="centered-stage">
      <h1>Review (M2 stub)</h1>
      <p class="subtitle">Excel parsing comes in Milestone 2. Loaded file: <strong>${fname}</strong></p>
      <button class="merge-btn" onclick="window.v2SetState('empty')">Start over</button>
    </div>
  `;
}

function renderFetching() { return `<div class="centered-stage"><h1>Fetching (M3)</h1></div>`; }
function renderReady()    { return `<div class="centered-stage"><h1>Ready (M3)</h1></div>`; }
function renderMerging()  { return `<div class="centered-stage"><h1>Merging (M4)</h1></div>`; }
function renderDone()     { return `<div class="centered-stage"><h1>Done (M4)</h1></div>`; }

// ── Expose to inline onclick handlers in render strings ──
window.v2TriggerExcel = triggerExcel;
window.v2SetState = setStateV2;
window.initMergeV2 = initMergeV2;
```

- [ ] **Step 2: Add the import to index.html**

Run: `grep -n 'src="assets/js/app.js"' "app/index.html"`

Note the line. Just before that `<script>` tag, add:

```html
<script type="module">
  import { initMergeV2 } from './assets/js/tools/merge/merge-v2.js';
  window.initMergeV2 = initMergeV2;
</script>
```

(This pre-loads the module so `window.initMergeV2` is available before `app.js` runs `switchTool`. The module also self-assigns to `window` at the end, but the explicit import is cleaner and ensures the file actually loads.)

- [ ] **Step 3: Manual verify — Empty state renders**

Reload. Settings toggle is ON. Click Merge. The Empty state should render: heading "Start a new merge", green Excel-style drop card, "Step 2 — We'll check the manifest…" hint chip below.

If the work area is blank: check DevTools console for module-load errors. Common issue: a typo in the import path.

- [ ] **Step 4: Manual verify — Drop transitions Empty → Loading → Review stub**

Click the drop card. The native file picker opens. Pick any `.xlsx` file (the legacy merge tool's test files in your repo work). The work area should:
- Flash to the Loading state with a spinner for ~1.2 seconds
- Then show the Review stub: "Review (M2 stub)" with the file name displayed
- "Start over" button returns to Empty

- [ ] **Step 5: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js app/index.html
git commit -m "feat(merge-v2): state machine + Empty/Loading/Review-stub

- merge-v2.js: 4-group state machine (Empty/Loading | Review/Fetching
  | Ready | Merging/Done) with renderEmpty / renderLoading rendering
  the mockup's centered-stage layout. renderReview is a stub showing
  the loaded file name; renderFetching/Ready/Merging/Done are
  one-line placeholders.
- handleExcelChange wired to <input id=v2ExcelInput>: stashes the
  File, transitions to Loading, then setTimeout 1.2s -> Review (M2
  will replace the timeout with real workbook parsing).
- initMergeV2() exported and exposed on window so app.js can call
  it when the v2 view is shown."
```

---

## Task 6: Manual end-to-end verification

**Files:** none (verification only)

- [ ] **Step 1: Start in legacy mode**

Reload `app/index.html` with Settings toggle OFF. Click Merge — legacy single-screen view loads. Drop an Excel and a PDF. Run a merge. Confirm the legacy flow still produces the merged output. Roll back: click "Start Over" or refresh.

- [ ] **Step 2: Switch to v2**

Settings → enable toggle → reload. Click Merge — Empty state renders. Drop an Excel — Loading appears, then Review stub. Click "Start over" — back to Empty.

- [ ] **Step 3: Switch back**

Settings → disable toggle → reload. Click Merge — legacy view back. Confirm `state.excelRows` from the legacy state object is still empty / not corrupted.

- [ ] **Step 4: Browse other tools while v2 is enabled**

With toggle ON, click Invoice Sender → drop a CSV → confirm Invoice Sender works normally. Click Customers → confirm normal. Click Settings → confirm toggle still shows checked. Switch back to Merge — Empty state renders again.

- [ ] **Step 5: Console-check**

DevTools → Console. Should be free of red errors during all of the above. Yellow warnings about deprecated APIs are OK.

If anything fails, fix forward and re-commit before moving to Task 7. **Do NOT proceed to Task 7 with broken state.**

---

## Task 7: Build, test packaged app, ship

**Files:** none directly (rebuild pipeline)

- [ ] **Step 1: Run the agent rebuild**

Run from the project root:

```bash
desktop/runbuild.bat
```

(Per the `feedback_use_runbuild_for_rebuild` memory: don't use `build-all.bat` — it's interactive. Use the non-interactive `runbuild.bat` sibling.)

Expected: PyInstaller builds the agent into `desktop/dist/`, then electron-builder produces `desktop/dist/win-unpacked/NGL Accounting.exe` and the installer `.exe` + `latest.yml`.

If the build fails: read the error, fix, retry. Do not skip to Step 2 with a broken build.

- [ ] **Step 2: Run the packaged app**

Launch `desktop/dist/win-unpacked/NGL Accounting.exe` (or the desktop shortcut if it points there).

Repeat the verifications from Task 6, Steps 1–5 — but inside the packaged Electron app, not the browser. The agent should be running in the background. Toggle on/off works, both flows render correctly.

- [ ] **Step 3: Push and release (per CLAUDE.md standing rule)**

```bash
git push
gh release create v2.45.0 \
  "desktop/dist/NGL Accounting Setup 2.45.0.exe" \
  "desktop/dist/latest.yml" \
  --title "v2.45.0 — Merge Tool v2 (M1: Foundation)" \
  --notes "Adds the v2 merge UI shell behind a Settings toggle.

This is M1 of 5 milestones for the merge-tool UI redesign. The new
flow is currently Empty + Loading states + a stub Review screen.
Real Excel parsing comes in v2.46 (M2). The legacy merge UI is
unchanged and is the default.

To try the new UI: Settings → Merge Tool — Beta → toggle on →
reload. Toggle off any time to revert.

Spec: docs/superpowers/specs/2026-05-05-merge-tool-ux-refinement-design.md
Plan: docs/superpowers/plans/2026-05-05-merge-tool-v2-m1-foundation.md"
```

The exact installer filename may vary depending on electron-builder config — check `desktop/dist/` first if the command fails.

- [ ] **Step 4: Verify auto-update**

Open the packaged app on your machine — it should detect v2.45.0 from the GH release and prompt to update (or auto-update on next restart). This proves M1 is shippable.

---

## Acceptance criteria (M1 complete when all true)

- [ ] `desktop/VERSION` reads `2.45.0`.
- [ ] `Settings → Merge Tool — Beta` toggle exists and persists across reloads.
- [ ] With toggle OFF: legacy merge flow works exactly as it did at v2.44 (Excel + PDFs → merged PDF).
- [ ] With toggle ON: clicking Merge in the sidebar shows the v2 Empty state. Dropping an Excel transitions to Loading, then to a Review stub displaying the file name.
- [ ] No console errors in either mode.
- [ ] All other tools (Invoice Sender, Customers, etc.) function normally regardless of toggle state.
- [ ] Git: every task committed separately on `main` (or a feature branch if user prefers).
- [ ] Packaged Electron build at `desktop/dist/win-unpacked/NGL Accounting.exe` runs successfully and reflects the toggle behavior.
- [ ] GH release `v2.45.0` exists with installer + `latest.yml` attached.

When all 9 are checked, M1 is done. Brainstorm M2 in a fresh session.
