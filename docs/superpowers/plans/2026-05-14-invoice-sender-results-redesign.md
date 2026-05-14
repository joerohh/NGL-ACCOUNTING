# Invoice Sender — Combined Results HUD Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/superpowers/specs/2026-05-14-invoice-sender-results-redesign-design.md`

**Goal:** Replace the three stacked Invoice Sender result UIs (old Send Complete card + old filter + table + new v2.62 tabbed view) with a single combined HUD that lives above Status Log and morphs across pre-send / sending / post-send states.

**Architecture:** Frontend-only (no agent changes). DOM restructure in `index.html` + new CSS tokens mirroring the merge tool + a state-machine-driven render in `invoice-sender-results.js` reading from existing `invoiceState` / `sendState`. Pre-send validation runs immediately on Excel parse and assigns `validationStatus` to each row. The same `#invResultsSection` renders Idle / Uploaded / Sending / Complete states with different banner + toolbar + tab states.

**Tech Stack:** Vanilla ES modules (no build step for JS), Tailwind via CDN, Electron packaged via `runbuild.bat`. No JS unit test framework — verification is manual via the packaged app.

**Testing pattern:** This codebase has no JS test runner. Each task's "verify" step runs the app (`app/index.html` directly in a browser for quick checks, or `runbuild.bat` for full packaged-app testing) and follows a manual checklist. Per `feedback_app_not_website.md`, the final smoke test must be in the packaged Electron app — but quick mid-development checks can be in the browser to save build time.

---

## File Structure

Files modified or created:

| File | Responsibility |
| --- | --- |
| `app/index.html` | DOM restructure: delete old surfaces, add `#invResultsSection` above Status Log, rename `#invProgressContainer` → `#invProgressStrip` |
| `app/assets/css/styles.css` | New tokens for progress strip, alert banner severities (warn/error/ok), Resolve button, val-badge variants |
| `app/assets/js/tools/invoice-sender/invoice-sender.js` | Delete `invShowSendResults()`, `_renderSendTally()`, old filter/summary code; add `invValidateRowsOnUpload()`; rewire progress strip live updates; route TMS Failed Rows into `invoiceState.invoices[]` |
| `app/assets/js/tools/invoice-sender/invoice-sender-results.js` | Extend `badgeFor()` with full pill taxonomy; rewrite `showResultsView()` and `renderResults()` with state machine; add `pickBannerSeverity()`, `getBannerCopy()`, `pickDefaultTab()`; extend `buildDiagnostic()` for every pill; render toolbar; use Resolve button |
| `desktop/VERSION` | Bump from `2.63.0` to `2.64.0` |

No new files. No agent-side changes.

---

## Conventions used in this plan

- **Manual verify step**: open the app (browser or packaged), do the listed actions, confirm the listed outcomes. If any check fails, fix before committing.
- **Commit message convention**: matches recent project style (`fix(invoice-sender/v64):`, `feat(invoice-sender/v64):`, `style(invoice-sender/v64):`).
- **Class prefix**: existing v62-* classes are renamed to v64-* where the structure changes meaningfully; otherwise reused. Where this plan invents a new class, it uses `v64-` prefix to make the new component identifiable.

---

### Task 1: CSS — add new visual tokens for the results HUD

**Files:**
- Modify: `app/assets/css/styles.css` — append at end of file

**Why first:** Pure additive change. No callers yet, so no breakage. Lets later tasks reference final class names.

- [ ] **Step 1: Append the new CSS block to `app/assets/css/styles.css`**

Append this exact block at the very end of the file (after the last existing `}`):

```css
/* ════════════════════════════════════════════════════════════════
   INVOICE SENDER v64 — Combined Results HUD
   Mirrors merge tool palette: amber for warn, red for miss, green for ok.
   ════════════════════════════════════════════════════════════════ */

/* Progress strip — sits above the results card during active send */
.v64-progress-strip {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 13px 18px;
  margin-bottom: 14px;
}
.v64-progress-strip .v64-ps-head {
  display: flex; justify-content: space-between; align-items: baseline;
  font-size: 0.86rem; margin-bottom: 7px;
}
.v64-progress-strip .v64-ps-head strong { color: #0f172a; font-weight: 700; }
.v64-progress-strip .v64-ps-head .v64-ps-pct { color: #475569; font-size: 0.78rem; }
.v64-progress-strip .v64-ps-bar-bg {
  height: 6px; background: #e2e8f0; border-radius: 3px; overflow: hidden;
}
.v64-progress-strip .v64-ps-bar-fg {
  height: 100%; background: linear-gradient(90deg, #ea580c, #f97316);
  border-radius: 3px; transition: width 0.3s ease;
}
.v64-progress-strip .v64-ps-meta {
  display: flex; gap: 14px; align-items: center; flex-wrap: wrap;
  font-size: 0.75rem; color: #64748b; margin-top: 8px;
}
.v64-progress-strip .v64-ps-meta strong { color: #1e293b; font-weight: 700; }
.v64-progress-strip .v64-ps-meta .v64-ps-divider { color: #cbd5e1; }

/* Results card wrapper — always visible above Status Log */
.v64-results-card {
  background: #fff; border: 1px solid #e2e8f0; border-radius: 12px;
  overflow: hidden; margin-bottom: 20px;
}

/* Alert banner inside the card — three severities */
.v64-alert-banner {
  padding: 13px 18px;
  display: flex; align-items: center; gap: 14px;
  border-bottom: 1px solid #e2e8f0;
}
.v64-alert-banner .v64-ab-icon {
  width: 32px; height: 32px; border-radius: 50%;
  color: white; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: 0.95rem;
}
.v64-alert-banner .v64-ab-text { flex: 1; min-width: 0; }
.v64-alert-banner .v64-ab-title { font-size: 0.95rem; font-weight: 700; color: #0f172a; line-height: 1.25; }
.v64-alert-banner .v64-ab-sub { font-size: 0.8rem; color: #475569; margin-top: 3px; }
.v64-alert-banner .v64-ab-actions { display: flex; gap: 8px; flex-shrink: 0; }
.v64-alert-banner button {
  border: none; border-radius: 7px; cursor: pointer;
  padding: 8px 14px; font-size: 0.82rem; font-weight: 700;
}

/* Soft warning (amber) */
.v64-alert-banner.v64-ab-warn { background: #fffbeb; border-bottom-color: #fde68a; }
.v64-alert-banner.v64-ab-warn .v64-ab-icon { background: #f59e0b; }
.v64-alert-banner.v64-ab-warn .v64-ab-primary { background: #ea580c; color: #fff; }
.v64-alert-banner.v64-ab-warn .v64-ab-primary:hover:not(:disabled) { background: #c2410c; }
.v64-alert-banner.v64-ab-warn .v64-ab-primary:disabled { background: #fed7aa; cursor: not-allowed; }

/* Hard error (red) */
.v64-alert-banner.v64-ab-error { background: #fef2f2; border-bottom-color: #fca5a5; }
.v64-alert-banner.v64-ab-error .v64-ab-icon { background: #dc2626; }
.v64-alert-banner.v64-ab-error .v64-ab-title { color: #7f1d1d; }
.v64-alert-banner.v64-ab-error .v64-ab-sub { color: #b91c1c; }
.v64-alert-banner.v64-ab-error .v64-ab-primary { background: #dc2626; color: #fff; }
.v64-alert-banner.v64-ab-error .v64-ab-primary:hover:not(:disabled) { background: #b91c1c; }
.v64-alert-banner.v64-ab-error .v64-ab-secondary { background: #fff; border: 1px solid #cbd5e1; color: #475569; font-weight: 600; }
.v64-alert-banner.v64-ab-error .v64-ab-secondary:hover { border-color: #dc2626; color: #b91c1c; }

/* All-clean (green) */
.v64-alert-banner.v64-ab-ok { background: #f0fdf4; border-bottom-color: #bbf7d0; }
.v64-alert-banner.v64-ab-ok .v64-ab-icon { background: #16a34a; }
.v64-alert-banner.v64-ab-ok .v64-ab-title { color: #14532d; }
.v64-alert-banner.v64-ab-ok .v64-ab-sub { color: #166534; }
.v64-alert-banner.v64-ab-ok .v64-ab-primary { background: #fff; color: #16a34a; border: 1px solid #bbf7d0; }
.v64-alert-banner.v64-ab-ok .v64-ab-primary:hover { background: #f0fdf4; border-color: #16a34a; }

/* Tab bar with embedded toolbar */
.v64-tabbar {
  display: flex; align-items: center;
  border-bottom: 1px solid #e2e8f0;
  padding: 0 16px;
  background: #fafbfc;
}
.v64-tabs { display: flex; gap: 4px; flex: 1; }
.v64-tab {
  padding: 12px 14px; background: none; border: none;
  font-size: 0.85rem; color: #64748b; font-weight: 600;
  border-bottom: 2px solid transparent; margin-bottom: -1px;
  cursor: pointer; display: flex; align-items: center; gap: 7px;
}
.v64-tab.active { color: #ea580c; border-bottom-color: #ea580c; }
.v64-tab.v64-tab-warn { color: #b45309; }
.v64-tab.v64-tab-error { color: #b91c1c; }
.v64-tab .v64-tab-count {
  padding: 1px 8px; border-radius: 10px;
  font-size: 0.72rem; font-weight: 700;
  background: #e2e8f0; color: #475569;
}
.v64-tab.v64-tab-warn .v64-tab-count { background: #fde68a; color: #92400e; }
.v64-tab.v64-tab-error .v64-tab-count { background: #fca5a5; color: #7f1d1d; }
.v64-tab.active .v64-tab-count { background: #fed7aa; color: #9a3412; }

.v64-toolbar {
  display: flex; align-items: center; gap: 12px;
  font-size: 0.74rem; color: #64748b;
}
.v64-toolbar.v64-toolbar-idle { color: #94a3b8; font-style: italic; }
.v64-toolbar .v64-tb-stat strong { color: #1e293b; font-weight: 700; }
.v64-toolbar .v64-tb-divider { color: #cbd5e1; }
.v64-toolbar button {
  background: none; border: none; color: #64748b;
  font-size: 0.74rem; font-weight: 600;
  padding: 5px 8px; cursor: pointer; border-radius: 4px;
  display: inline-flex; align-items: center; gap: 5px;
}
.v64-toolbar button:hover { background: #e2e8f0; color: #ea580c; }

/* Results table — overrides the legacy .inv-table when inside #invResultsTableWrap */
#invResultsTableWrap .v64-table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
#invResultsTableWrap .v64-table thead th {
  text-align: left; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.04em;
  color: #64748b; font-weight: 700; padding: 11px 12px;
  background: #fff; border-bottom: 1px solid #e2e8f0;
}
#invResultsTableWrap .v64-table tbody td {
  padding: 10px 12px; border-bottom: 1px solid #f1f5f9; color: #1e293b; vertical-align: middle;
}
#invResultsTableWrap .v64-table tbody tr.v64-row-fail { background: #fffbf2; }
#invResultsTableWrap .v64-table tbody tr.v64-row-fail-hard { background: #fef6f6; }
#invResultsTableWrap .v64-table tbody tr.v64-row-sending { background: #f0f9ff; }
#invResultsTableWrap .v64-table tbody tr:hover { background: #fafbfc; }
#invResultsTableWrap .v64-table tbody tr.v64-row-fail:hover,
#invResultsTableWrap .v64-table tbody tr.v64-row-fail-hard:hover { background: #fef6e4; }

/* Val-badge pills inside the results table — direct copy of merge tool's .val-badge */
.v64-pill {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 3px 9px; border-radius: 11px;
  font-size: 0.72rem; font-weight: 700; white-space: nowrap;
}
.v64-pill .v64-pill-dot { width: 6px; height: 6px; border-radius: 50%; }
.v64-pill.v64-pill-warn { background: #fef3c7; color: #92400e; border: 1px solid #fde68a; }
.v64-pill.v64-pill-warn .v64-pill-dot { background: #f59e0b; }
.v64-pill.v64-pill-miss { background: #fef2f2; color: #b91c1c; border: 1px solid #fca5a5; }
.v64-pill.v64-pill-miss .v64-pill-dot { background: #dc2626; }
.v64-pill.v64-pill-ok { background: #f0fdf4; color: #15803d; border: 1px solid #bbf7d0; }
.v64-pill.v64-pill-ok .v64-pill-dot { background: #16a34a; }
.v64-pill.v64-pill-sending { background: #dbeafe; color: #1e40af; border: 1px solid #93c5fd; }
.v64-pill.v64-pill-sending .v64-pill-dot { background: #3b82f6; }
.v64-pill.v64-pill-queued { background: #f1f5f9; color: #64748b; border: 1px solid #cbd5e1; }
.v64-pill.v64-pill-queued .v64-pill-dot { background: #94a3b8; }

/* Resolve button — outlined orange, replaces v62-action-btn for failed rows */
.v64-resolve-btn {
  background: #fff; color: #ea580c; border: 1px solid #fed7aa;
  padding: 4px 11px; border-radius: 6px;
  font-size: 0.72rem; font-weight: 700; cursor: pointer;
}
.v64-resolve-btn:hover { background: #fff7ed; border-color: #ea580c; }

/* Empty-state inside the results table */
.v64-empty-state {
  text-align: center; padding: 48px 24px; color: #94a3b8;
}
.v64-empty-state .v64-es-icon { font-size: 2rem; margin-bottom: 8px; opacity: 0.6; }
.v64-empty-state strong { color: #475569; }

/* Technical detail block inside the detail panel */
.v64-tech-detail {
  margin-top: 14px; padding-top: 11px; border-top: 1px dashed #cbd5e1;
}
.v64-tech-detail .v64-td-head {
  display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;
}
.v64-tech-detail .v64-td-label {
  font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.05em;
  color: #64748b; font-weight: 700;
}
.v64-tech-detail .v64-td-copy {
  background: none; border: none; color: #64748b;
  font-size: 0.72rem; cursor: pointer; padding: 3px 7px; border-radius: 4px;
}
.v64-tech-detail .v64-td-copy:hover { background: #e2e8f0; color: #ea580c; }
.v64-tech-detail .v64-td-raw {
  background: #f1f5f9; border: 1px solid #e2e8f0; border-radius: 6px;
  padding: 8px 10px; font-family: 'Consolas', 'Monaco', monospace;
  font-size: 0.72rem; color: #475569; line-height: 1.5;
  white-space: pre-wrap; word-break: break-all;
}
```

- [ ] **Step 2: Verify (visual smoke check)**

Open `app/index.html` directly in a browser. The styles add new classes but don't yet apply to any element, so the page should look exactly the same as before. Open DevTools → Console: no CSS parse errors.

- [ ] **Step 3: Commit**

```bash
git add app/assets/css/styles.css
git commit -m "style(invoice-sender/v64): add results HUD CSS tokens

Mirrors merge tool palette. Adds v64-progress-strip, v64-alert-banner
(warn/error/ok severities), v64-tabbar with embedded toolbar, v64-pill
variants, v64-resolve-btn, v64-tech-detail block. Pure additive — no
elements consume these classes yet.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: HTML — restructure DOM (delete old surfaces, add `#invResultsSection`)

**Files:**
- Modify: `app/index.html` — delete and replace blocks around lines 1021-1150

**Why second:** Now the CSS exists, the new HTML can use the new classes immediately. After this task, the page will look closer to the mockup (though without the JS to populate it, banners/toolbars stay empty).

- [ ] **Step 1: Delete `#invSummaryBar` + `#invSendStatusBar` (lines ~1029-1031)**

Find this block in `app/index.html`:

```html
  <!-- ── Summary Bar ── -->
  <div id="invSummaryBar" class="inv-summary-bar" style="display:none;"></div>
  <div id="invSendStatusBar" class="inv-summary-bar" style="display:none;"></div>
```

Delete all three lines (the comment + the two divs).

- [ ] **Step 2: Delete `#invSendFilterWrap` (lines ~1033-1043)**

Find and delete:

```html
  <!-- ── Send Status Filter ── -->
  <div id="invSendFilterWrap" style="display:none; margin-bottom:8px;">
    <select id="invSendFilter" onchange="invRenderTable()" style="padding:5px 10px; font-size:0.8rem; border:1px solid #cbd5e1; border-radius:6px; color:#334155; background:#fff; cursor:pointer;">
      <option value="all">All Invoices</option>
      <option value="not_sent">Not Sent</option>
      <option value="in_progress">In Progress</option>
      <option value="sent">Sent</option>
      <option value="skipped">Skipped / No Attachments</option>
      <option value="error">Error</option>
    </select>
  </div>
```

- [ ] **Step 3: Delete the entire `#invTableContainer` block (lines ~1045-1073)**

Find and delete the comment header + `<div id="invTableContainer">` through its closing `</div>` (inclusive of the table inside).

- [ ] **Step 4: Delete `#invFailedRowsBox` block (lines ~1076-1095)**

Find and delete the comment header + the entire `<div id="invFailedRowsBox">` block through its closing `</div>`.

- [ ] **Step 5: Replace `#invProgressContainer` with `#invProgressStrip`**

Find the existing block (around line 1022):

```html
  <!-- ── Progress Bar ── -->
  <div id="invProgressContainer" style="display:none; margin-bottom:16px;">
    <div style="height:5px; background:#e2e8f0; border-radius:3px; overflow:hidden;">
      <div id="invProgressBar" style="height:100%; background:linear-gradient(90deg,#ea580c,#f97316); width:0%; transition:width 0.3s ease;"></div>
    </div>
    <div id="invProgressLabel" style="font-size:0.8rem; color:#64748b; margin-top:5px; text-align:center;"></div>
  </div>
```

Replace with:

```html
  <!-- ── Progress Strip (during active send) ── -->
  <div id="invProgressStrip" class="v64-progress-strip" style="display:none;">
    <div class="v64-ps-head">
      <span><strong id="invPsLabel">Sending invoices…</strong> <span id="invPsCount">0 / 0</span></span>
      <span class="v64-ps-pct" id="invPsPct">0% complete</span>
    </div>
    <div class="v64-ps-bar-bg"><div class="v64-ps-bar-fg" id="invPsBar" style="width:0%;"></div></div>
    <div class="v64-ps-meta">
      <span>Started at <strong id="invPsStarted">—</strong></span>
      <span class="v64-ps-divider">·</span>
      <span>Elapsed <strong id="invPsElapsed">—</strong></span>
      <span class="v64-ps-divider">·</span>
      <span><strong id="invPsEta">—</strong> remaining</span>
      <span class="v64-ps-divider">·</span>
      <span><strong id="invPsAvg">—</strong>/invoice</span>
    </div>
  </div>
```

- [ ] **Step 6: Replace the existing `#invSendResults` block at the bottom (lines ~1118-1150) with the new `#invResultsSection`**

Currently after Status Log there's:

```html
  <!-- v2.62 Results view (Fix 1 + Fix 2). Hidden until send completes. -->
  <div id="invSendResults" class="v62-results-body" style="display:none; margin-top:20px;">
    …
  </div>
```

**Delete it entirely.** Then **insert the following block ABOVE Status Log**, immediately after `#invProgressStrip` (and after deleting `#invFailedRowsBox`):

```html
  <!-- ══════════════════════════════════════════════════════════════
       v2.64 Combined Results HUD — always visible
       ══════════════════════════════════════════════════════════════ -->
  <div id="invResultsSection" class="v64-results-card v62-results-body">
    <div id="invSendResultsMain">
      <!-- Alert banner (controlled by JS — class set to v64-ab-warn / error / ok, display toggled) -->
      <div id="invSendAlertBanner" class="v64-alert-banner" style="display:none;">
        <div class="v64-ab-icon" id="invSendBannerIcon">!</div>
        <div class="v64-ab-text">
          <div class="v64-ab-title" id="invSendBannerTitle"></div>
          <div class="v64-ab-sub" id="invSendBannerSubtitle"></div>
        </div>
        <div class="v64-ab-actions" id="invSendBannerActions"></div>
      </div>

      <!-- Tab bar with embedded toolbar -->
      <div class="v64-tabbar">
        <div class="v64-tabs">
          <button class="v64-tab v64-tab-warn" data-tab="needs-attention">⚠ Needs Attention <span class="v64-tab-count" id="invTabIssueCount">0</span></button>
          <button class="v64-tab" data-tab="sent">✓ Sent <span class="v64-tab-count" id="invTabSentCount">0</span></button>
          <button class="v64-tab active" data-tab="all">All Invoices <span class="v64-tab-count" id="invTabAllCount">0</span></button>
        </div>
        <div class="v64-toolbar v64-toolbar-idle" id="invResultsToolbar">
          <span>— upload an Excel to begin —</span>
        </div>
      </div>

      <!-- Table area — empty state OR table, rendered by JS -->
      <div id="invResultsTableWrap"></div>
    </div>

    <!-- Detail side panel (Fix 2 — unchanged structurally) -->
    <div id="invDetailPanel" class="v62-detail-panel">
      <div id="invDetailPanelInner" class="v62-panel-inner"></div>
    </div>
  </div>
```

Note: keep the old `v62-results-body` class on the outer div for backward compatibility with the existing panel-open behavior in `invoice-sender-results.js`. The new `v64-results-card` is added alongside.

- [ ] **Step 7: Verify (browser open)**

Open `app/index.html` in a browser. Navigate to Invoice Sender. Expect:
1. No JS errors in the console (some may appear referencing the deleted IDs — that's fine and will be fixed in Task 3).
2. The new `#invResultsSection` is visible above Status Log (currently empty since JS hasn't been wired).
3. The 3 tabs are visible with `0` counts.
4. Toolbar shows "— upload an Excel to begin —" placeholder text.
5. No "Send Complete" stat card anywhere.
6. No old filter dropdown.
7. No old `#invTableContainer` (the "Upload a TMS Excel to see invoices here" prompt is gone — it'll come back via empty-state JS in Task 6).

Some console errors from `invoice-sender.js` referencing deleted DOM IDs are EXPECTED at this point — Task 3 cleans them up.

- [ ] **Step 8: Commit**

```bash
git add app/index.html
git commit -m "refactor(invoice-sender/v64): restructure DOM for combined results HUD

Delete: invSummaryBar, invSendStatusBar, invSendFilterWrap,
invTableContainer, invFailedRowsBox. Rename invProgressContainer →
invProgressStrip with new template (started-at, elapsed, ETA, avg/inv).
Move invSendResults → invResultsSection above Status Log; add tabbar
with embedded toolbar slot. JS wiring in subsequent tasks.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: JS — delete old rendering code

**Files:**
- Modify: `app/assets/js/tools/invoice-sender/invoice-sender.js`

**Why now:** With the DOM gone, the old JS that targets `#invSendProgressPanel`, `#invSummaryBar`, etc. is dead weight and throws. Clean it up so subsequent tasks build on a quiet state.

- [ ] **Step 1: Delete `invShowSendResults()` (currently lines ~1288-1343) — the "Send Complete" card writer**

Find the function `function invShowSendResults(summary) { … }` (starts at line ~1288, ends around line 1343 with the closing `}` before `function invShowConnectionWarning`). Delete the entire function definition.

- [ ] **Step 2: Delete its two callers**

Find the two call sites (around lines 964 and 977):

```js
    invShowSendResults(event);
```

Delete both lines.

- [ ] **Step 3: Delete `_renderSendTally()` and references**

Search for `function _renderSendTally` (around line ~1270-1286). Delete the full function. Also delete any call sites — search for `_renderSendTally(` and remove those lines.

- [ ] **Step 4: Delete code reading `#invSummaryBar` (line ~279)**

Find:

```js
  const bar = document.getElementById('invSummaryBar');
```

Read the surrounding 10-20 lines to identify the containing function (likely `invUpdateSummary` or similar). The function's job was to render counts into the deleted bar. **Replace the function body** with a no-op:

```js
function invUpdateSummary() {
  // Summary now lives in the v64 tab counts; rendering happens via window.invRenderResults()
  if (typeof window.invRenderResults === 'function') window.invRenderResults();
}
```

(Adjust the function name if grep shows it's different. The intent: the public name stays the same so other callers keep working, but the body just triggers a v64 re-render.)

- [ ] **Step 5: Delete code reading `#invSendStatusBar` (line ~725)**

Similar: find the function containing `document.getElementById('invSendStatusBar')` and convert its body to a no-op or `invRenderResults()` trigger. Most likely the function name pattern is `_renderSendStatus` or similar.

- [ ] **Step 6: Delete `#invSendProgressPanel` references**

Search for `invSendProgressPanel`. Aside from the now-deleted `invShowSendResults`, there are references in:
- `invShowConnectionWarning` (line ~1346)
- `invShowConnectionLost` (line ~1356)
- Plus 3-4 more

For each function that writes into `#invSendProgressPanel`, replace the panel-writing block with calls to surface the same info via the status log:

```js
function invShowConnectionWarning(message) {
  invAddLog('warning', '⚠ ' + message);
}

function invShowConnectionLost(message) {
  invAddLog('error', '⚠ Connection lost: ' + message);
}
```

Any other panel-writing functions found in the search: convert similarly. The connection-state UX moves into the new banner system in Task 6+8; for now, log messages are an acceptable bridge.

- [ ] **Step 7: Delete `#invFailedRowsBox` population code (lines ~1675-1700ish)**

Find:

```js
  const box = document.getElementById('invFailedRowsBox');
  const list = document.getElementById('invFailedRowsList');
  const count = document.getElementById('invFailedRowsCount');
```

Identify the containing function (likely `invRenderFailedRows` or `invSetFailedRows`). The function's body interacts with the deleted DOM. **Replace the body** with a stub that stores the failed rows on `invoiceState.invoices` for Task 9 to handle:

```js
function invRenderFailedRows(failedRows) {
  // v64: TMS fetch failures merge into invoiceState.invoices and surface via Needs Attention tab.
  // Actual merging logic lives in Task 9; this stub keeps the existing callers safe.
  if (typeof window.invRenderResults === 'function') window.invRenderResults();
}
```

- [ ] **Step 8: Update `invRenderTable()` to delegate to `window.invRenderResults`**

Find `function invRenderTable()` (line ~305). It writes into the now-deleted `#invTableBody`. Replace the entire function body with:

```js
function invRenderTable() {
  // v64: the legacy invRenderTable() now drives the unified results HUD.
  // All rendering (banner, tabs, toolbar, table, empty state) lives in invoice-sender-results.js.
  if (typeof window.invRenderResults === 'function') window.invRenderResults();
}
```

Keep `invRenderTable` exported / globally available so existing callers (lines 174, 193, 274, 445, 454, 614, 641, 768, 896, 924, 947, 1139, etc.) keep working.

- [ ] **Step 9: Verify (browser open)**

Open `app/index.html` in a browser → Invoice Sender. Expect:
1. **No JS errors in console** when the page loads.
2. **Upload a small Excel file** → no errors. The Needs Attention / Sent / All Invoices tab counts may not yet update correctly (those come in Task 6), but uploading must not throw.
3. **Click Send button (without doing a real send)** — no errors before the agent call.

- [ ] **Step 10: Commit**

```bash
git add app/assets/js/tools/invoice-sender/invoice-sender.js
git commit -m "refactor(invoice-sender/v64): delete old surface renderers

Removes invShowSendResults() (Send Complete card), _renderSendTally(),
invSummaryBar/invSendStatusBar writers, and the standalone failed-rows
box. invRenderTable() now delegates to window.invRenderResults(); panel-
writing functions for connection state log instead.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: JS — add pre-send row validation

**Files:**
- Modify: `app/assets/js/tools/invoice-sender/invoice-sender.js`

**Why now:** The render layer needs a `validationStatus` field on each row to assign pre-send pills. This task computes it.

- [ ] **Step 1: Add `invValidateRowsOnUpload()` near the top of invoice-sender.js (after the imports, before `invHandleCsvDrop`)**

Insert this new function around line 17 (after the imports + the alias line):

```js
// ══════════════════════════════════════════════════════════════════
//  Pre-send row validation (v64)
//  Assigns row.validationStatus + row.validationDetail for the
//  Needs Attention tab pills. Runs after every Excel parse and after
//  every PDF-match refresh.
// ══════════════════════════════════════════════════════════════════
//   validationStatus values:
//     'missing_inv'         — INV# cell blank
//     'missing_field'       — Container / Customer Code / Amount / Bill-To empty
//     'duplicate_inv'       — Two rows share an INV#
//     'no_pdf_match'        — No PDF in dropped attachments matches the container
//     'no_customer_match'   — Customer code doesn't match any DB entry
//     'no_email'            — Customer matched but has no email on file
//     'customer_needs_review' — Customer has needsReview=true
//     null                  — Row is clean
export function invValidateRowsOnUpload() {
  const rows = invoiceState.invoices || [];
  if (rows.length === 0) return;

  const seenInvoiceNumbers = new Map();   // invNo → first row index that used it
  const pdfContainers = new Set(
    (state.pdfs || []).map(p => (p.containerNumber || '').toUpperCase()).filter(Boolean)
  );
  const customersByCode = (typeof agentBridge !== 'undefined' && typeof agentBridge._custRead === 'function')
    ? agentBridge._custRead().reduce((acc, c) => { acc[(c.code || '').toUpperCase()] = c; return acc; }, {})
    : {};

  rows.forEach((row, idx) => {
    row.validationStatus = null;
    row.validationDetail = null;

    // 1. Missing INV#
    if (!row.invoiceNumber || String(row.invoiceNumber).trim() === '') {
      row.validationStatus = 'missing_inv';
      return;
    }

    // 2. Duplicate INV#
    const invKey = String(row.invoiceNumber).trim().toUpperCase();
    if (seenInvoiceNumbers.has(invKey)) {
      row.validationStatus = 'duplicate_inv';
      row.validationDetail = { otherIndex: seenInvoiceNumbers.get(invKey) };
      // Also flag the first occurrence retroactively
      const firstRow = rows[seenInvoiceNumbers.get(invKey)];
      if (firstRow && !firstRow.validationStatus) {
        firstRow.validationStatus = 'duplicate_inv';
        firstRow.validationDetail = { otherIndex: idx };
      }
      return;
    }
    seenInvoiceNumbers.set(invKey, idx);

    // 3. Missing required fields (Container, Customer Code, Amount, Bill-To)
    const missingFields = [];
    if (!row.containerNumber) missingFields.push('Container');
    if (!row.customerCode) missingFields.push('Customer Code');
    if (row.amount == null || row.amount === '') missingFields.push('Amount');
    if (!row.customerName && !row.email) missingFields.push('Bill-To');
    if (missingFields.length > 0) {
      row.validationStatus = 'missing_field';
      row.validationDetail = { fields: missingFields };
      return;
    }

    // 4. Customer code in Excel doesn't match any DB customer
    const code = (row.customerCode || '').toUpperCase();
    const customer = customersByCode[code];
    if (!customer) {
      row.validationStatus = 'no_customer_match';
      row.validationDetail = { code: row.customerCode };
      return;
    }

    // 5. Customer flagged needs-review
    if (customer.needsReview) {
      row.validationStatus = 'customer_needs_review';
      row.validationDetail = { customerCode: customer.code, customerName: customer.name };
      return;
    }

    // 6. No email on file for this customer
    const hasEmail = (customer.emails && customer.emails.length > 0)
      || customer.email
      || (row.email && row.email.trim() !== '');
    if (!hasEmail) {
      row.validationStatus = 'no_email';
      row.validationDetail = { customerCode: customer.code };
      return;
    }

    // 7. No PDF in the dropped attachments matches the container
    //    (Skip this check if no PDFs have been uploaded yet — the user might upload after Excel.)
    if (pdfContainers.size > 0) {
      const container = (row.containerNumber || '').toUpperCase();
      if (!pdfContainers.has(container)) {
        row.validationStatus = 'no_pdf_match';
        row.validationDetail = { container: row.containerNumber };
        return;
      }
    }
  });
}
```

- [ ] **Step 2: Call `invValidateRowsOnUpload()` from `invHandleCsvFile()` after rows are loaded**

Find the `invHandleCsvFile` function (line ~75). Look for where rows are pushed into `invoiceState.invoices` (around line 170-175, just before the call to `invRenderTable()`). Insert the validation call BEFORE `invRenderTable()`:

```js
      // …existing parsing logic that fills invoiceState.invoices…

      invValidateRowsOnUpload();   // ← v64: assign per-row validationStatus

      document.getElementById('invCsvFileName').textContent = file.name;
      document.getElementById('invCsvFileSub').textContent = invoiceState.invoices.length + ' invoices loaded';
      document.getElementById('invCsvDropZone').classList.add('has-file');

      invRenderTable();
      invUpdateGenerateBtn();
```

- [ ] **Step 3: Call validation on PDF upload as well**

Search for the function that handles PDF drops (likely `invHandlePdfDrop` or where `state.pdfs` is mutated). After PDFs are added/removed, call `invValidateRowsOnUpload()` then `invRenderTable()`. Look at the existing `invMatchPdfsToInvoices` or similar function — if there is one, append the validation call. If not, find the PDF-drop handler and add it there.

Pattern to apply:

```js
// inside the PDF drop handler, after state.pdfs is mutated:
invValidateRowsOnUpload();
invRenderTable();
```

- [ ] **Step 4: Call validation when customer DB is refreshed (so `no_customer_match` / `no_email` pills update live)**

Find `invEnrichWithCustomerProfiles()` (line ~200). At the end of the function, after enrichment is complete:

```js
function invEnrichWithCustomerProfiles() {
  // …existing logic…
  invValidateRowsOnUpload();
  invRenderTable();
}
```

- [ ] **Step 5: Verify (browser open)**

Open the app, navigate to Invoice Sender. Upload an Excel file. Then in the DevTools console:

```js
import('./assets/js/shared/state.js').then(m => console.table(m.invoiceState.invoices.map(r => ({ inv: r.invoiceNumber, cust: r.customerCode, status: r.validationStatus, detail: r.validationDetail }))));
```

Expected: each row shows a `validationStatus` (or `null` if clean). Try test cases:
- A row with blank INV# → `missing_inv`.
- Two rows with the same INV# → both flagged `duplicate_inv`.
- A row whose customer code isn't in your DB → `no_customer_match`.

The pills don't render yet (Task 5), but the data should be correct.

- [ ] **Step 6: Commit**

```bash
git add app/assets/js/tools/invoice-sender/invoice-sender.js
git commit -m "feat(invoice-sender/v64): pre-send row validation

Adds invValidateRowsOnUpload() that assigns row.validationStatus +
row.validationDetail per the v64 pill taxonomy. Hooked into Excel
parse, PDF upload, and customer DB refresh.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: JS-results — extend `badgeFor()` with the full pill taxonomy

**Files:**
- Modify: `app/assets/js/tools/invoice-sender/invoice-sender-results.js`

**Why now:** The validator writes the data; this task turns the data into pills.

- [ ] **Step 1: Replace the existing `badgeFor()` function (currently around line 23-41) and its helper `SLOT_LABELS` constant with the new pill taxonomy**

Find the existing code at the top of `invoice-sender-results.js`:

```js
const SLOT_LABELS = { pod: 'POD', bol: 'BOL', pol: 'POL', do: 'DO', pl: 'PL' };

// …isMissingDocs, isErrored, isFailed unchanged…

function badgeFor(row) {
  // existing logic — replace entirely
}
```

Replace the existing `badgeFor` body with this expanded implementation. **Keep** the existing `isMissingDocs`, `isErrored`, `isFailed`, `SLOT_LABELS`:

```js
// ── v64 Pill taxonomy ────────────────────────────────────────────
// Returns { cls, text, severity } where cls is the CSS modifier
// (v64-pill-warn / v64-pill-miss / v64-pill-ok / v64-pill-sending /
// v64-pill-queued) and severity is 'warn' | 'miss' | 'ok' | 'sending' |
// 'queued' for downstream logic.

function badgeFor(row) {
  // 1. Validation pills (pre-send) take precedence
  if (row.validationStatus) {
    return validationBadge(row);
  }

  // 2. Send-state pills
  if (row.sendStatus === 'sent') {
    return { cls: 'v64-pill-ok', text: 'Sent', severity: 'ok' };
  }
  if (row.sendStatus === 'in_progress') {
    return { cls: 'v64-pill-sending', text: 'Sending…', severity: 'sending' };
  }
  if (row.sendStatus === 'queued' || row.sendStatus === 'pending') {
    return { cls: 'v64-pill-queued', text: 'Queued', severity: 'queued' };
  }
  if (row.sendStatus === 'skipped') {
    return { cls: 'v64-pill-warn', text: 'Skipped', severity: 'warn' };
  }

  // 3. Failure pills
  if (isMissingDocs(row)) {
    return missingDocsBadge(row);
  }
  if (isErrored(row)) {
    return errorBadge(row);
  }
  if (row.sendStatus === 'mismatch') {
    return mismatchBadge(row);
  }

  // 4. Default — no send attempted yet, no validation issue
  return { cls: 'v64-pill-queued', text: 'Ready', severity: 'queued' };
}

function validationBadge(row) {
  const d = row.validationDetail || {};
  switch (row.validationStatus) {
    case 'missing_inv':
      return { cls: 'v64-pill-warn', text: 'Missing INV#', severity: 'warn' };
    case 'missing_field': {
      const fields = Array.isArray(d.fields) ? d.fields : [];
      if (fields.length === 1) return { cls: 'v64-pill-warn', text: `Missing ${fields[0]}`, severity: 'warn' };
      if (fields.length >= 2) return { cls: 'v64-pill-warn', text: `${fields.length} Fields Empty`, severity: 'warn' };
      return { cls: 'v64-pill-warn', text: 'Missing Field', severity: 'warn' };
    }
    case 'duplicate_inv':
      return { cls: 'v64-pill-warn', text: 'Duplicate INV#', severity: 'warn' };
    case 'no_pdf_match':
      return { cls: 'v64-pill-warn', text: 'No PDF Match', severity: 'warn' };
    case 'no_customer_match':
      return { cls: 'v64-pill-warn', text: 'No Customer Match', severity: 'warn' };
    case 'no_email':
      return { cls: 'v64-pill-warn', text: 'No Email on File', severity: 'warn' };
    case 'customer_needs_review':
      return { cls: 'v64-pill-warn', text: 'Customer Needs Review', severity: 'warn' };
    case 'tms_fetch_failed':
      return { cls: 'v64-pill-warn', text: 'TMS Fetch Failed', severity: 'warn' };
    default:
      return { cls: 'v64-pill-warn', text: row.validationStatus, severity: 'warn' };
  }
}

function missingDocsBadge(row) {
  const missing = (row.missingDocs || []).map(d => SLOT_LABELS[String(d).toLowerCase()] || String(d).toUpperCase());
  if (missing.length === 0) return { cls: 'v64-pill-warn', text: 'Docs Missing', severity: 'warn' };
  if (missing.length === 1) return { cls: 'v64-pill-warn', text: `${missing[0]} Missing`, severity: 'warn' };
  return { cls: 'v64-pill-warn', text: `${missing.join(' + ')} Missing`, severity: 'warn' };
}

function mismatchBadge(row) {
  const field = (row.mismatchField || '').toLowerCase();
  if (field === 'amount') return { cls: 'v64-pill-warn', text: 'Amount Mismatch', severity: 'warn' };
  if (field === 'customer') return { cls: 'v64-pill-warn', text: 'Customer Mismatch', severity: 'warn' };
  return { cls: 'v64-pill-warn', text: 'Mismatch', severity: 'warn' };
}

// Maps a raw error string to one of the v64 post-send error pills.
// Returns { kind, text } where kind is the validation-style key.
export function classifyError(errorMessage) {
  const msg = String(errorMessage || '').toLowerCase();
  if (msg.includes('401') || msg.includes('unauthorized') || msg.includes('signed out') || msg.includes('session expired')) {
    return { kind: 'signed_out', text: 'Signed Out of QuickBooks' };
  }
  if (msg.includes('timeout') || msg.includes('did not respond') || msg.includes('timed out') || msg.includes('504')) {
    return { kind: 'qbo_timed_out', text: 'QuickBooks Timed Out' };
  }
  if (msg.includes('econnref') || msg.includes('enotfound') || msg.includes('network') || msg.includes('no internet') || msg.includes('dns') || msg.includes('5xx') || msg.match(/\b5\d\d\b/)) {
    return { kind: 'no_internet', text: 'No Internet' };
  }
  return { kind: 'unexpected_error', text: 'Unexpected Error' };
}

function errorBadge(row) {
  const c = classifyError(row.errorMessage);
  return { cls: 'v64-pill-miss', text: c.text, severity: 'miss' };
}
```

- [ ] **Step 2: Update `renderRow()` to use the new pill class**

Find `renderRow()` (line ~182). The existing version uses `v62-badge`. Replace the pill `<span>` line:

```js
    <td style="padding:8px; border-bottom:1px solid #f1f5f9;"><span class="v62-badge ${b.cls}">${b.text}</span></td>
```

With (using the new pill markup):

```js
    <td style="padding:10px 12px; border-bottom:1px solid #f1f5f9;">
      <span class="v64-pill ${b.cls}"><span class="v64-pill-dot"></span>${b.text}</span>
    </td>
```

Also update the action cell to use the new Resolve button (full Resolve flow work happens in Task 8 — for now just put the placeholder button class):

```js
    <td style="padding:8px; border-bottom:1px solid #f1f5f9; text-align:right;">${action}</td>
```

Replace `action` rendering above (around lines 185-192):

```js
  let action = '';
  if (failed || isValidationFailure(r)) {
    action = `<button class="v64-resolve-btn" data-invoice="${escHtml(r.invoiceNumber)}">Resolve</button>`;
  }
```

Add this helper near the top of the file (next to `isFailed`):

```js
function isValidationFailure(row) {
  return !!row.validationStatus;
}
```

- [ ] **Step 3: Update row click + action button wiring to recognise both failed-send and validation-failure rows**

Find the existing wiring (around lines 165-180):

```js
  wrap.querySelectorAll('tr[data-invoice]').forEach(tr => {
    tr.onclick = (e) => {
      if (e.target.closest('.v62-action-btn')) return;
      …
```

Update to:

```js
  wrap.querySelectorAll('tr[data-invoice]').forEach(tr => {
    tr.onclick = (e) => {
      if (e.target.closest('.v64-resolve-btn') || e.target.closest('.v62-action-btn')) return;
      const fn = window.invOpenPanelForInvoice;
      if (typeof fn === 'function') fn(tr.dataset.invoice);
    };
  });
  wrap.querySelectorAll('.v64-resolve-btn, .v62-action-btn').forEach(btn => {
    btn.onclick = (e) => {
      e.stopPropagation();
      const fn = window.invOpenPanelForInvoice;
      if (typeof fn === 'function') fn(btn.dataset.invoice);
    };
  });
```

- [ ] **Step 4: Verify (browser open)**

Open the app. Upload a few different Excel files:
1. One with a blank INV# row → expect `Missing INV#` pill on that row.
2. One with two identical INV# rows → expect both rows show `Duplicate INV#`.
3. One with a customer code that isn't in the DB → expect `No Customer Match`.
4. Click a failed row → existing detail panel opens (will be cleaned up in Task 8; today it shows old content).

Open DevTools Console — no errors.

- [ ] **Step 5: Commit**

```bash
git add app/assets/js/tools/invoice-sender/invoice-sender-results.js
git commit -m "feat(invoice-sender/v64): full pill taxonomy in badgeFor()

Renders v64 pills with explicit-reason names (Missing INV#, No Customer
Match, QuickBooks Timed Out, etc.). Adds classifyError() pattern matcher
mapping raw error strings to friendly post-send pill kinds. Resolve
button replaces v62 retry/attach buttons on failed rows.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: JS-results — state-machine-driven render orchestration

**Files:**
- Modify: `app/assets/js/tools/invoice-sender/invoice-sender-results.js`

**Why now:** The pills are right; now the surrounding components (banner, toolbar, tabs, empty states) need to react to the 5 stages from the spec.

- [ ] **Step 1: Add the stage detector at the top of `invoice-sender-results.js` (after imports)**

```js
// ── v64 State machine ────────────────────────────────────────────
// Stages: 'idle' | 'uploaded' | 'sending' | 'complete-failures' | 'complete-clean'

export function getStage() {
  const rows = invoiceState.invoices || [];
  if (rows.length === 0) return 'idle';

  // Sending: there's an active job OR any row is currently in-progress/queued
  if (sendState.isRunning || sendState.jobId) {
    return 'sending';
  }

  // Has the user clicked Send at any point this session?
  const everSent = (sendState.startTime != null) || rows.some(r => r.sendStatus === 'sent' || r.sendStatus === 'error' || r.sendStatus === 'missing_docs' || r.sendStatus === 'mismatch' || r.sendStatus === 'skipped' || r.sendStatus === 'skipped_no_attachments');

  if (!everSent) return 'uploaded';

  // Send has run — check for any failures
  const hasFailures = rows.some(r => isFailed(r) || (r.validationStatus && r.sendStatus !== 'sent'));
  return hasFailures ? 'complete-failures' : 'complete-clean';
}
```

- [ ] **Step 2: Add severity picker + banner copy helpers**

```js
// ── Banner severity + copy ────────────────────────────────────────

const HARD_ERROR_KINDS = new Set(['signed_out', 'no_internet']);

export function pickBannerSeverity(stage, rows) {
  if (stage === 'idle' || stage === 'uploaded' || stage === 'sending') return 'hidden';
  if (stage === 'complete-clean') return 'ok';

  // Stage is 'complete-failures' — pick warn vs error
  const failedRows = rows.filter(r => isFailed(r) || (r.validationStatus && r.sendStatus !== 'sent'));
  const total = rows.length;
  const errorClassifications = failedRows.filter(r => isErrored(r)).map(r => classifyError(r.errorMessage).kind);
  const anyHard = errorClassifications.some(k => HARD_ERROR_KINDS.has(k));
  const failRatio = total > 0 ? failedRows.length / total : 0;

  if (anyHard || failRatio >= 0.25) return 'error';
  return 'warn';
}

function _fmtTimeOfDay(ms) {
  if (!ms) return '—';
  const d = new Date(ms);
  let h = d.getHours();
  const m = d.getMinutes();
  const ampm = h >= 12 ? 'PM' : 'AM';
  h = h % 12 || 12;
  return `${h}:${m.toString().padStart(2, '0')} ${ampm}`;
}

function _fmtDuration(ms) {
  if (!ms || ms < 0) return '—';
  const s = Math.floor(ms / 1000);
  if (s < 60) return s + 's';
  const m = Math.floor(s / 60);
  const rs = s % 60;
  if (m < 60) return `${m}m ${rs}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

export function getBannerCopy(stage, severity, rows) {
  const failed = rows.filter(r => isFailed(r) || (r.validationStatus && r.sendStatus !== 'sent'));
  const sent = rows.filter(r => r.sendStatus === 'sent');
  const startedAt = _fmtTimeOfDay(sendState.startTime);
  const finishedAtMs = sendState.endTime || Date.now();
  const finishedAt = _fmtTimeOfDay(finishedAtMs);
  const totalDur = _fmtDuration(finishedAtMs - (sendState.startTime || finishedAtMs));

  if (severity === 'ok') {
    return {
      icon: '✓',
      title: `All ${rows.length} invoices sent successfully`,
      sub: `Started ${startedAt} · finished ${finishedAt} · total ${totalDur}`,
      actions: [
        { kind: 'primary', text: '📋 View Audit', onClick: 'invLoadAuditLog' },
      ],
    };
  }

  if (severity === 'warn') {
    const fixedCount = failed.filter(r => !r.validationStatus && !isFailed(r)).length;
    return {
      icon: '!',
      title: failed.length === 1
        ? '1 invoice needs a fix before it can send'
        : `${failed.length} invoices need a fix before they can send`,
      sub: `${sent.length} sent successfully · finished at ${finishedAt}`,
      actions: [
        { kind: 'primary', text: `↻ Retry the Fixed Ones (${fixedCount})`, onClick: 'invRetryFixedRows', disabled: fixedCount === 0 },
      ],
    };
  }

  if (severity === 'error') {
    // Pick the dominant error reason for the title
    const kindCounts = {};
    failed.filter(r => isErrored(r)).forEach(r => {
      const k = classifyError(r.errorMessage).kind;
      kindCounts[k] = (kindCounts[k] || 0) + 1;
    });
    const topKind = Object.keys(kindCounts).sort((a, b) => kindCounts[b] - kindCounts[a])[0];
    const reasonByKind = {
      signed_out: 'looks like you got signed out of QuickBooks',
      no_internet: 'the connection to QuickBooks went down',
      qbo_timed_out: 'QuickBooks was unresponsive',
      unexpected_error: 'something unexpected went wrong',
    };
    const reasonText = reasonByKind[topKind] || 'something unexpected went wrong';
    const actionByKind = {
      signed_out: { text: 'Sign back in to QuickBooks', onClick: 'invSignBackIntoQbo' },
      no_internet: { text: 'Check connection', onClick: 'invShowConnectionInfo' },
      qbo_timed_out: null,
      unexpected_error: null,
    };
    const extraAction = actionByKind[topKind];
    const actions = [];
    if (extraAction) actions.push({ kind: 'secondary', ...extraAction });
    actions.push({ kind: 'primary', text: `↻ Retry These (${failed.length})`, onClick: 'invRetryFailedRows' });
    return {
      icon: '!',
      title: `${failed.length} invoices couldn't send — ${reasonText}`,
      sub: `${sent.length} invoices sent before the problem started. Take action above, then click Retry.`,
      actions,
    };
  }

  return null;
}

export function pickDefaultTab(stage, rows) {
  if (stage === 'idle' || stage === 'uploaded') {
    const validationFailures = rows.filter(r => !!r.validationStatus);
    return validationFailures.length > 0 ? 'needs-attention' : 'all';
  }
  if (stage === 'sending') return sendState.currentTab || 'all';
  if (stage === 'complete-failures') return 'needs-attention';
  if (stage === 'complete-clean') return 'sent';
  return 'all';
}
```

- [ ] **Step 3: Rewrite `showResultsView()` and `renderResults()` to drive the state machine**

Find the existing functions (around lines 68-127). Replace them with:

```js
export function showResultsView() {
  // v64: the results section is always visible — this function exists for backward compat.
  const el = document.getElementById('invResultsSection') || document.getElementById('invSendResults');
  if (el) el.style.display = '';
  bindTabClicks();
  renderResults();
}

export function hideResultsView() {
  // v64: no-op (always visible). Kept for backward compat.
  const el = document.getElementById('invResultsSection') || document.getElementById('invSendResults');
  if (el) el.classList.remove('panel-open');
}

export function renderResults() {
  const rows = invoiceState.invoices || [];
  const stage = getStage();

  // Determine default tab on stage transition
  const lastStage = sendState._lastRenderedStage;
  if (lastStage !== stage && (stage === 'complete-failures' || stage === 'complete-clean' || (stage === 'uploaded' && lastStage === 'idle'))) {
    sendState.currentTab = pickDefaultTab(stage, rows);
  }
  sendState._lastRenderedStage = stage;

  // Tab counts
  const failed = rows.filter(r => isFailed(r) || (r.validationStatus && r.sendStatus !== 'sent'));
  const sent = rows.filter(r => r.sendStatus === 'sent');
  const setText = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
  setText('invTabIssueCount', failed.length);
  setText('invTabSentCount', sent.length);
  setText('invTabAllCount', rows.length);

  // Tab active class
  document.querySelectorAll('#invResultsSection .v64-tab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === sendState.currentTab);
  });

  // Banner
  renderBanner(stage, rows);

  // Toolbar
  renderToolbar(stage);

  // Table
  renderTable();
}

function renderBanner(stage, rows) {
  const banner = document.getElementById('invSendAlertBanner');
  if (!banner) return;
  const severity = pickBannerSeverity(stage, rows);
  if (severity === 'hidden') {
    banner.style.display = 'none';
    return;
  }
  banner.style.display = 'flex';
  banner.classList.remove('v64-ab-warn', 'v64-ab-error', 'v64-ab-ok');
  banner.classList.add('v64-ab-' + severity);

  const copy = getBannerCopy(stage, severity, rows);
  if (!copy) { banner.style.display = 'none'; return; }

  const iconEl = document.getElementById('invSendBannerIcon');
  const titleEl = document.getElementById('invSendBannerTitle');
  const subEl = document.getElementById('invSendBannerSubtitle');
  const actionsEl = document.getElementById('invSendBannerActions');
  if (iconEl) iconEl.textContent = copy.icon;
  if (titleEl) titleEl.textContent = copy.title;
  if (subEl) subEl.textContent = copy.sub;

  if (actionsEl) {
    actionsEl.innerHTML = '';
    copy.actions.forEach(a => {
      const btn = document.createElement('button');
      btn.className = 'v64-ab-' + (a.kind || 'primary');
      btn.textContent = a.text;
      btn.disabled = !!a.disabled;
      btn.onclick = () => {
        const fn = window[a.onClick];
        if (typeof fn === 'function') fn();
      };
      actionsEl.appendChild(btn);
    });
  }
}
```

- [ ] **Step 4: Add the new `renderTable()` empty states**

Find the existing `renderTable()` (line ~129). Update the empty-state block:

```js
function renderTable() {
  let rows;
  if (sendState.currentTab === 'needs-attention') {
    rows = (invoiceState.invoices || []).filter(r => isFailed(r) || (r.validationStatus && r.sendStatus !== 'sent'));
  } else if (sendState.currentTab === 'sent') {
    rows = getSentRows();
  } else {
    rows = invoiceState.invoices || [];
  }

  const wrap = document.getElementById('invResultsTableWrap');
  if (!wrap) return;

  if (rows.length === 0) {
    wrap.innerHTML = renderEmptyState(sendState.currentTab);
    return;
  }

  // …rest of renderTable unchanged — table markup and row wiring…
  // (KEEP the existing wrap.innerHTML = `<table…>` block from the existing renderTable)
}

function renderEmptyState(tab) {
  const stage = getStage();
  if (tab === 'all') {
    if (stage === 'idle') {
      return `<div class="v64-empty-state">
        <div class="v64-es-icon">📋</div>
        <strong>Upload a TMS Excel to see invoices here.</strong>
      </div>`;
    }
    return `<div class="v64-empty-state"><strong>No invoices.</strong></div>`;
  }
  if (tab === 'needs-attention') {
    if (stage === 'idle' || stage === 'uploaded') {
      return `<div class="v64-empty-state">
        <div class="v64-es-icon">✓</div>
        <strong>Nothing flagged.</strong>
        <div style="font-size:0.85rem; color:#94a3b8; margin-top:6px;">${stage === 'idle' ? 'Upload an Excel to get started.' : 'All rows passed validation — ready to send.'}</div>
      </div>`;
    }
    return `<div class="v64-empty-state">
      <div class="v64-es-icon">✓</div>
      <strong>Nothing to fix.</strong>
      <div style="font-size:0.85rem; color:#94a3b8; margin-top:6px;">All invoices sent cleanly.</div>
    </div>`;
  }
  if (tab === 'sent') {
    if (stage === 'idle' || stage === 'uploaded') {
      return `<div class="v64-empty-state"><strong>No invoices sent yet.</strong></div>`;
    }
    return `<div class="v64-empty-state"><strong>No invoices sent in this batch.</strong></div>`;
  }
  return '';
}
```

- [ ] **Step 5: Add `bindTabClicks()` for the new `.v64-tab` selector**

Find the existing `bindTabClicks()` (line ~59). Update the selector:

```js
function bindTabClicks() {
  document.querySelectorAll('#invResultsSection .v64-tab').forEach(btn => {
    btn.onclick = () => {
      sendState.currentTab = btn.dataset.tab;
      renderResults();
    };
  });
}
```

- [ ] **Step 6: Add toolbar renderer**

```js
function renderToolbar(stage) {
  const tb = document.getElementById('invResultsToolbar');
  if (!tb) return;

  if (stage === 'idle') {
    tb.className = 'v64-toolbar v64-toolbar-idle';
    tb.innerHTML = '<span>— upload an Excel to begin —</span>';
    return;
  }
  if (stage === 'uploaded') {
    tb.className = 'v64-toolbar v64-toolbar-idle';
    tb.innerHTML = '<span>— ready to send —</span>';
    return;
  }
  if (stage === 'sending') {
    tb.className = 'v64-toolbar v64-toolbar-idle';
    tb.innerHTML = '<span>— send in progress —</span>';
    return;
  }

  // Complete: full toolbar
  const startedAt = _fmtTimeOfDay(sendState.startTime);
  const endMs = sendState.endTime || Date.now();
  const totalMs = endMs - (sendState.startTime || endMs);
  const processed = (invoiceState.invoices || []).filter(r => r.sendStatus && r.sendStatus !== 'pending' && r.sendStatus !== 'queued').length;
  const avgMs = processed > 0 ? totalMs / processed : 0;

  tb.className = 'v64-toolbar';
  tb.innerHTML = `
    <span class="v64-tb-stat">Started <strong>${startedAt}</strong></span>
    <span class="v64-tb-divider">·</span>
    <span class="v64-tb-stat">Total <strong>${_fmtDuration(totalMs)}</strong></span>
    <span class="v64-tb-divider">·</span>
    <span class="v64-tb-stat"><strong>${_fmtDuration(avgMs)}</strong>/inv</span>
    <span class="v64-tb-divider">·</span>
    <button onclick="if(window.agentBridge && window.agentBridge.exportAuditLog) window.agentBridge.exportAuditLog();">⬇ Report</button>
    <button onclick="if(window.invLoadAuditLog) window.invLoadAuditLog();">📋 Audit</button>
  `;
}
```

- [ ] **Step 7: Update the `window.invShowResultsView` / `window.invRenderResults` exports** (line ~204)

Should already exist; just make sure they're present:

```js
window.invShowResultsView = showResultsView;
window.invHideResultsView = hideResultsView;
window.invRenderResults = renderResults;
```

- [ ] **Step 8: Verify (browser open)**

Run through this manual test set:

1. **Cold start, no Excel.** All Invoices tab active, empty state shows "Upload a TMS Excel to see invoices here". No banner, toolbar idle.
2. **Upload a clean Excel (no validation issues).** All Invoices populates. Needs Attention shows "Nothing flagged." No banner. Toolbar reads "— ready to send —".
3. **Upload an Excel with 2 missing INV# rows.** Tab auto-switches to Needs Attention. Two rows visible with `Missing INV#` pill. Each row has a Resolve button.
4. **Click a tab manually.** Tab switching works.

Console must have no errors.

- [ ] **Step 9: Commit**

```bash
git add app/assets/js/tools/invoice-sender/invoice-sender-results.js
git commit -m "feat(invoice-sender/v64): state-machine render orchestration

Adds getStage(), pickBannerSeverity(), getBannerCopy(), pickDefaultTab().
Rewrites showResultsView() and renderResults() to drive the 5-stage HUD
(idle/uploaded/sending/complete-failures/complete-clean). New empty
states per tab. Toolbar idle text + full post-send stats. Banner severity
picker (warn/error/ok) with dynamic copy.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: JS — progress strip live updates during send

**Files:**
- Modify: `app/assets/js/tools/invoice-sender/invoice-sender.js`

**Why now:** The strip lives in the DOM (Task 2) but has no driver. This task wires the SSE handlers to update its fields live.

- [ ] **Step 1: Find the existing SSE per-row event handler**

Search `invoice-sender.js` for `invSetProgress` calls or for where `sendState.sent`, `sendState.errors`, `sendState.completedCount` get incremented during the send job. The handler is likely inside `invSendViaQBO()` (line ~789) or in a function it calls (`invHandleSendEvent` or similar).

Look for code like:

```js
if (event.kind === 'invoice_sent' || event.kind === 'invoice_skipped' || ...) {
  sendState.sent++;
  invSetProgress(…);
}
```

- [ ] **Step 2: Add `invRenderProgressStrip()` function and call it from the per-row event handler**

Insert this new function near `invSetProgress` or at the bottom of the file (before the legacy `invShowConnectionWarning` was):

```js
function invRenderProgressStrip() {
  const strip = document.getElementById('invProgressStrip');
  if (!strip) return;

  const isRunning = !!(sendState.isRunning || sendState.jobId);
  if (!isRunning) {
    strip.style.display = 'none';
    return;
  }
  strip.style.display = 'block';

  const total = (invoiceState.invoices || []).length;
  const done = sendState.completedCount || 0;
  const pct = total > 0 ? Math.floor((done / total) * 100) : 0;
  const setText = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };

  setText('invPsLabel', 'Sending invoices…');
  setText('invPsCount', `${done} / ${total}`);
  setText('invPsPct', `${pct}% complete`);
  const bar = document.getElementById('invPsBar');
  if (bar) bar.style.width = pct + '%';

  // Started at
  if (sendState.startTime) {
    const d = new Date(sendState.startTime);
    let h = d.getHours();
    const m = d.getMinutes();
    const ampm = h >= 12 ? 'PM' : 'AM';
    h = h % 12 || 12;
    setText('invPsStarted', `${h}:${m.toString().padStart(2, '0')} ${ampm}`);
  }

  // Elapsed
  const elapsedMs = sendState.startTime ? (Date.now() - sendState.startTime) : 0;
  setText('invPsElapsed', _fmtDurationLocal(elapsedMs));

  // ETA
  if (done > 0 && total > done) {
    const avgMs = elapsedMs / done;
    const remainingMs = avgMs * (total - done);
    setText('invPsEta', `~${_fmtDurationLocal(remainingMs)}`);
    setText('invPsAvg', _fmtDurationLocal(avgMs));
  } else {
    setText('invPsEta', '—');
    setText('invPsAvg', '—');
  }
}

function _fmtDurationLocal(ms) {
  if (!ms || ms < 0) return '—';
  const s = Math.floor(ms / 1000);
  if (s < 60) return s + 's';
  const m = Math.floor(s / 60);
  const rs = s % 60;
  if (m < 60) return `${m}m ${rs}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}
```

- [ ] **Step 3: Hook the strip refresh into the SSE event flow**

In the per-row event handler (the one identified in Step 1), after `sendState.completedCount++` (or wherever progress is tracked), add:

```js
invRenderProgressStrip();
if (typeof window.invRenderResults === 'function') window.invRenderResults();
```

The `invRenderResults()` call ensures tab counts and row badges update live too.

- [ ] **Step 4: Add a 1-second interval that updates the elapsed time even between SSE events**

Find where the send job starts (around `invSendViaQBO`). After `sendState.isRunning = true` is set, start an interval:

```js
sendState._progressStripInterval = setInterval(() => {
  invRenderProgressStrip();
}, 1000);
```

When the send completes (search for where `sendState.isRunning = false` is set, or where `sendState.endTime` should be set), clear the interval and hide the strip:

```js
if (sendState._progressStripInterval) {
  clearInterval(sendState._progressStripInterval);
  sendState._progressStripInterval = null;
}
sendState.endTime = Date.now();
invRenderProgressStrip();   // one final call to hide the strip
if (typeof window.invRenderResults === 'function') window.invRenderResults();
```

- [ ] **Step 5: Add `endTime` field to `sendState` if it doesn't exist**

Open `app/assets/js/shared/state.js` and confirm `sendState` has these fields (some may already be present from existing code):

```js
export const sendState = {
  // …existing fields…
  startTime: null,
  endTime: null,            // ← v64: set on send completion
  completedCount: 0,
  isRunning: false,
  _lastRenderedStage: null, // ← v64: stage transition tracking
  _progressStripInterval: null,
  // …
};
```

Add any missing fields.

- [ ] **Step 6: Verify (manual smoke in browser)**

This step needs an actual send to verify, which requires the packaged app + a real QBO connection. **Skip to the smoke test in Task 11 for full validation.** For now, browser-level check:

1. Upload an Excel.
2. In console, run:

```js
import('./assets/js/shared/state.js').then(m => {
  m.sendState.isRunning = true;
  m.sendState.startTime = Date.now() - 60000;
  m.sendState.completedCount = 5;
  window.invRenderResults();
});
```

3. Watch the progress strip appear with `Started [60s ago] · Elapsed 1m 0s · ETA based on 12s/inv`.

Console must have no errors.

- [ ] **Step 7: Commit**

```bash
git add app/assets/js/tools/invoice-sender/invoice-sender.js app/assets/js/shared/state.js
git commit -m "feat(invoice-sender/v64): live progress strip with timer

Adds invRenderProgressStrip() that updates started-at, elapsed, ETA, and
avg/invoice live during a send. Hooked into the SSE per-row handler plus
a 1s interval for elapsed-time-only updates between events. sendState
gains endTime + interval handle for cleanup on done.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: JS-results — Resolve detail panel content per pill type

**Files:**
- Modify: `app/assets/js/tools/invoice-sender/invoice-sender-results.js`

**Why now:** Resolve buttons already render (Task 5). The side panel still uses the old Fix 2 diagnostic logic that only knows about `missing_docs` / `error`. This task extends it for every pill.

- [ ] **Step 1: Replace `buildDiagnostic()` (line ~210) with a dispatch table covering every pill kind**

```js
function buildDiagnostic(row) {
  // Validation pills (pre-send)
  if (row.validationStatus) {
    return validationDiagnostic(row);
  }
  if (isMissingDocs(row)) {
    return missingDocsDiagnostic(row);
  }
  if (isErrored(row)) {
    return errorDiagnostic(row);
  }
  if (row.sendStatus === 'mismatch') {
    return mismatchDiagnostic(row);
  }
  return null;
}

function validationDiagnostic(row) {
  const d = row.validationDetail || {};
  switch (row.validationStatus) {
    case 'missing_inv':
      return {
        cls: 'v62-error-box', title: 'Invoice number is missing',
        explanation: "This row from your Excel doesn't have an invoice number. We can't send it without one.",
        nextStep: '<strong>What to do:</strong> Click the field below to type the invoice number, then Save.',
        actionLabel: 'Save',
        actionField: 'invoiceNumber',
      };
    case 'missing_field': {
      const fields = Array.isArray(d.fields) ? d.fields : [];
      return {
        cls: 'v62-error-box', title: `${fields.join(' and ')} ${fields.length === 1 ? 'is' : 'are'} missing`,
        explanation: 'The Excel row has empty cells for fields that QuickBooks requires.',
        nextStep: `<strong>What to do:</strong> Fill in the missing field${fields.length > 1 ? 's' : ''} below, then Save.`,
        actionLabel: 'Save',
        actionField: '__multi__',
        missingFields: fields,
      };
    }
    case 'duplicate_inv':
      return {
        cls: 'v62-error-box', title: 'This invoice number appears twice in your Excel',
        explanation: 'Two rows share the same invoice number. QuickBooks will only let you send one of them.',
        nextStep: '<strong>What to do:</strong> Decide which one to keep — or keep both if they really are different invoices.',
        actionLabel: 'Keep this one',
        actionField: '__dupe__',
      };
    case 'no_pdf_match':
      return {
        cls: 'v62-error-box', title: 'No PDF matches this container',
        explanation: "None of the PDFs you dropped match this invoice's container number.",
        nextStep: '<strong>What to do:</strong> Drop the right PDF below — or open QuickBooks and confirm the invoice already has its attachments.',
        actionLabel: 'Upload PDF',
        actionField: '__pdf_drop__',
      };
    case 'no_customer_match':
      return {
        cls: 'v62-error-box', title: 'No customer in your list matches this code',
        explanation: `The customer code "${escHtml(d.code || '')}" isn't in Customer Manager. We can't send to a customer we don't know.`,
        nextStep: '<strong>What to do:</strong> Either pick an existing customer to match this code, or create a new customer.',
        actionLabel: 'Open Customer Manager',
        actionField: '__open_customers__',
      };
    case 'no_email':
      return {
        cls: 'v62-error-box', title: "This customer doesn't have an email on file",
        explanation: 'We need at least one email address to send the invoice.',
        nextStep: '<strong>What to do:</strong> Type the email below and save — Customer Manager will update too.',
        actionLabel: 'Save email',
        actionField: 'email',
      };
    case 'customer_needs_review':
      return {
        cls: 'v62-error-box', title: 'Customer is flagged "Needs Review"',
        explanation: 'Someone (maybe you) marked this customer profile as needing review before we send to them.',
        nextStep: '<strong>What to do:</strong> Open the customer profile, confirm the details, and unflag.',
        actionLabel: 'Open customer profile',
        actionField: '__open_customer_profile__',
      };
    case 'tms_fetch_failed':
      return {
        cls: 'v62-error-box', title: 'TMS document fetch failed for this row',
        explanation: 'We tried to pull documents from TMS for this invoice but the fetch did not complete.',
        nextStep: '<strong>What to do:</strong> Click Retry to fetch again, or upload the documents manually.',
        actionLabel: 'Retry TMS fetch',
        actionField: '__retry_tms__',
      };
    default:
      return null;
  }
}

function missingDocsDiagnostic(row) {
  const missing = (row.missingDocs || []).map(d => SLOT_LABELS[String(d).toLowerCase()] || String(d).toUpperCase());
  const isPlural = missing.length > 1;
  const docs = missing.length > 0 ? missing.join(' and ') : 'a document';
  return {
    cls: 'v62-error-box',
    title: isPlural ? `${docs} are missing` : `${docs} is missing`,
    explanation: isPlural
      ? `This customer requires ${docs}. We couldn't find them in QuickBooks or on the TMS work order.`
      : `We couldn't find the ${docs} — not in QuickBooks, not on the TMS work order.`,
    nextStep: `<strong>What to do:</strong> Drop the ${docs} file${isPlural ? 's' : ''} below. We'll attach them and retry automatically.`,
    actionLabel: 'Upload + Retry',
    actionField: '__doc_drop__',
    missingSlots: missing,
  };
}

function errorDiagnostic(row) {
  const c = classifyError(row.errorMessage);
  switch (c.kind) {
    case 'qbo_timed_out':
      return {
        cls: 'v62-error-box warn',
        title: "QuickBooks didn't respond in time",
        explanation: 'QuickBooks took too long to reply. This is almost always temporary.',
        nextStep: '<strong>What to do:</strong> Click Try Again — most timeouts clear up on their own.',
        actionLabel: 'Try Again',
        actionField: '__retry_send__',
        rawError: row.errorMessage,
      };
    case 'signed_out':
      return {
        cls: 'v62-error-box',
        title: 'You got signed out of QuickBooks',
        explanation: 'Your QuickBooks session expired during the send.',
        nextStep: '<strong>What to do:</strong> Use the "Sign back in to QuickBooks" button at the top, then click Try Again here.',
        actionLabel: 'Try Again',
        actionField: '__retry_send__',
        rawError: row.errorMessage,
      };
    case 'no_internet':
      return {
        cls: 'v62-error-box',
        title: "Couldn't reach QuickBooks",
        explanation: 'Either your internet dropped or QuickBooks is having a problem on their end.',
        nextStep: '<strong>What to do:</strong> Check your internet, then click Try Again.',
        actionLabel: 'Try Again',
        actionField: '__retry_send__',
        rawError: row.errorMessage,
      };
    default:
      return {
        cls: 'v62-error-box',
        title: 'Something unexpected went wrong',
        explanation: "We don't have a friendly explanation for this one — the technical detail is below for support.",
        nextStep: '<strong>What to do:</strong> Try sending again. If it keeps failing, copy the technical detail below and send it to your administrator.',
        actionLabel: 'Try Again',
        actionField: '__retry_send__',
        rawError: row.errorMessage,
      };
  }
}

function mismatchDiagnostic(row) {
  const field = (row.mismatchField || 'amount').toLowerCase();
  return {
    cls: 'v62-error-box warn',
    title: `${field === 'customer' ? 'Customer' : 'Amount'} doesn't match QuickBooks`,
    explanation: `Your Excel says one ${field}, QuickBooks shows another. We paused before sending so you can decide.`,
    nextStep: '<strong>What to do:</strong> Pick which value is correct. We\'ll send with that one.',
    actionLabel: 'Use Excel value',
    actionField: '__mismatch_choose__',
    rawError: row.errorMessage,
  };
}
```

- [ ] **Step 2: Update `renderPanel()` (line ~299) to render the Technical Detail block when a raw error is present**

Find `function renderPanel()`. Locate where the diagnostic content is injected. Inside the rendered HTML, append (at the bottom of the panel inner content, before the existing close):

```js
  // …existing rendered HTML…
  if (diagnostic && diagnostic.rawError) {
    html += `
      <div class="v64-tech-detail">
        <div class="v64-td-head">
          <span class="v64-td-label">Technical detail (for support)</span>
          <button class="v64-td-copy" onclick="navigator.clipboard.writeText(${JSON.stringify(diagnostic.rawError)}).then(()=>{this.textContent='Copied ✓';setTimeout(()=>this.textContent='📋 Copy',1500);});">📋 Copy</button>
        </div>
        <div class="v64-td-raw">${escHtml(diagnostic.rawError)}</div>
      </div>
    `;
  }
```

(Adjust the syntax to fit the existing render pattern — whether `renderPanel()` builds a single string or appends to inner.innerHTML, the technical detail block should go at the bottom of the panel content.)

- [ ] **Step 3: Wire the Resolve button "Sign back in to QuickBooks" and "Retry the Fixed Ones" actions to global functions**

The banner buttons in Task 6 call `window.invSignBackIntoQbo`, `window.invShowConnectionInfo`, `window.invRetryFixedRows`, `window.invRetryFailedRows`. Add stubs in `invoice-sender.js` (around the bottom of the file, near the other `window.` exports):

```js
window.invRetryFixedRows = function invRetryFixedRows() {
  // Builds a new send job with only the rows whose validation+send status has cleared since the last send.
  const eligible = (invoiceState.invoices || []).filter(r => !r.validationStatus && (isFailed(r) || isMissingDocs(r) || isErrored(r)) === false && r.sendStatus !== 'sent');
  if (eligible.length === 0) {
    invAddLog('warning', 'No rows are ready to retry yet — fix the issues first.');
    return;
  }
  // Hand off to the existing send flow with the eligible row IDs preselected.
  invoiceState.selectedIds = new Set(eligible.map(r => r.id));
  invSendViaQBO();
};

window.invRetryFailedRows = function invRetryFailedRows() {
  // Retries every previously-failed row regardless of fix status (for hard errors like "signed out").
  const failed = (invoiceState.invoices || []).filter(r => isFailed(r));
  invoiceState.selectedIds = new Set(failed.map(r => r.id));
  invSendViaQBO();
};

window.invSignBackIntoQbo = function invSignBackIntoQbo() {
  // Trigger the existing QBO OAuth flow.
  if (window.agentBridge && typeof window.agentBridge.startQboLogin === 'function') {
    window.agentBridge.startQboLogin();
  } else if (typeof window.qboLogin === 'function') {
    window.qboLogin();
  } else {
    invAddLog('warning', 'QBO sign-in trigger not available — please use the agent panel.');
  }
};

window.invShowConnectionInfo = function invShowConnectionInfo() {
  invAddLog('info', 'Check that your internet is connected and QuickBooks is up at status.intuit.com.');
};
```

The `isFailed`/`isMissingDocs`/`isErrored` references inside `invRetryFixedRows` need access — either import them from `invoice-sender-results.js` or duplicate the small helpers locally inside `invoice-sender.js`. Pick whichever fits the existing module style; if `invoice-sender-results.js` already exports them, add `import { isFailed, isMissingDocs, isErrored } from './invoice-sender-results.js';` at the top of `invoice-sender.js`. Otherwise inline:

```js
// Local copies for retry-eligibility checks
function _isMissingDocs(r) { return r.sendStatus === 'missing_docs' || r.sendStatus === 'skipped_no_attachments'; }
function _isErrored(r) { return r.sendStatus === 'error'; }
function _isFailed(r) { return _isMissingDocs(r) || _isErrored(r); }
```

And use the underscore-prefixed versions inside `invRetryFixedRows` / `invRetryFailedRows`.

- [ ] **Step 4: Verify (browser open)**

1. Upload an Excel with rows that trigger every validation pill (blank INV#, dup INV#, missing field, unmatched customer code, etc.).
2. Click Resolve on each — verify the side panel content matches the pill (right title, plain-English explanation, plain-English next step).
3. For at least one row, simulate a post-send error in DevTools console:

```js
import('./assets/js/shared/state.js').then(m => {
  const r = m.invoiceState.invoices[0];
  r.sendStatus = 'error';
  r.errorMessage = 'Request timed out after 30000ms';
  window.invRenderResults();
});
```

4. Click Resolve on that row → expect "QuickBooks didn't respond in time" + Try Again + Technical Detail block with Copy button.
5. Click 📋 Copy on the Technical Detail block → paste somewhere → expect the raw error string.

Console must be clean.

- [ ] **Step 5: Commit**

```bash
git add app/assets/js/tools/invoice-sender/invoice-sender-results.js app/assets/js/tools/invoice-sender/invoice-sender.js
git commit -m "feat(invoice-sender/v64): Resolve panel content per pill type

Extends buildDiagnostic() with branches for every v64 pill kind
(validation + post-send). Each branch returns plain-English title /
explanation / next step / action button label. Adds Technical Detail
block with 📋 Copy button for raw errors. Wires global handlers for
invRetryFixedRows, invRetryFailedRows, invSignBackIntoQbo,
invShowConnectionInfo.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: JS — TMS Failed Rows merge into Needs Attention

**Files:**
- Modify: `app/assets/js/tools/invoice-sender/invoice-sender.js`

**Why now:** TMS-side fetch failures currently lived in the deleted `#invFailedRowsBox`. They need to merge into the same `invoiceState.invoices[]` so they surface in Needs Attention.

- [ ] **Step 1: Find the TMS failure handler**

Search `invoice-sender.js` for `tms_fetch_failed`, `tms_failure`, or for the SSE event kind that delivers TMS doc-fetch errors. The handler is likely in the same area as the deleted `invRenderFailedRows` stub (around line 1675).

- [ ] **Step 2: Update the handler to set `validationStatus = 'tms_fetch_failed'` on the matching row**

Pattern (adjust the exact field names to match the SSE event payload — likely `container_number` or `invoice_number`):

```js
function handleTmsFetchFailure(event) {
  // Find the invoice row by container or invoice number
  const ref = event.containerNumber || event.invoiceNumber;
  if (!ref) return;

  const row = (invoiceState.invoices || []).find(r =>
    r.containerNumber === ref || r.invoiceNumber === ref
  );
  if (!row) return;

  row.validationStatus = 'tms_fetch_failed';
  row.validationDetail = {
    reason: event.reason || event.errorMessage || 'TMS fetch did not complete',
    raw: event,
  };

  if (typeof window.invRenderResults === 'function') window.invRenderResults();
}
```

Replace the existing failed-rows-box logic with this. Where the previous code called `invRenderFailedRows(failedRows)`, instead loop and call `handleTmsFetchFailure(event)` per row.

- [ ] **Step 3: Verify (browser open + console simulation)**

Open the app. Upload an Excel. In DevTools console:

```js
import('./assets/js/shared/state.js').then(m => {
  const row = m.invoiceState.invoices[0];
  row.validationStatus = 'tms_fetch_failed';
  row.validationDetail = { reason: 'WO not found for container ABCD1234567' };
  window.invRenderResults();
});
```

Expected:
- Tab switches to (or Needs Attention has) 1 item with the pill "TMS Fetch Failed".
- Clicking Resolve on that row opens the panel with the new TMS-specific diagnostic from Task 8.

- [ ] **Step 4: Commit**

```bash
git add app/assets/js/tools/invoice-sender/invoice-sender.js
git commit -m "feat(invoice-sender/v64): merge TMS fetch failures into Needs Attention

TMS doc-fetch failures now set validationStatus='tms_fetch_failed' on the
matching invoiceState row instead of populating the deleted FailedRowsBox.
The row surfaces in the Needs Attention tab with a TMS Fetch Failed pill
and the matching Resolve diagnostic.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Bump version + initial build check

**Files:**
- Modify: `desktop/VERSION`

**Why now:** Per `feedback_version_bump.md`, every rebuild bumps VERSION. Doing this BEFORE smoke testing so the packaged installer carries the right version label.

- [ ] **Step 1: Bump VERSION**

```bash
# Replace contents of desktop/VERSION
echo "2.64.0" > desktop/VERSION
```

Or open `desktop/VERSION` and replace `2.63.0` with `2.64.0`.

- [ ] **Step 2: Verify the JS syntax gate passes (per reference_build_js_check.md)**

```bash
node desktop/check-js.js
```

Expected: clean exit, no errors. (This catches the v2.62-style SyntaxError before a full build runs.)

- [ ] **Step 3: Commit**

```bash
git add desktop/VERSION
git commit -m "build(v64): bump version to 2.64.0 for combined results HUD release

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Packaged-app smoke test

**Files:** None. Runs the build script, exercises the app, fixes any bugs found.

**Why now:** Per `feedback_always_push_and_release.md` (updated 2026-05-14), the new pipeline requires manual smoke-testing of the packaged installer before push. This task is the gate.

- [ ] **Step 1: Build the packaged installer via the PowerShell pattern**

Per `feedback_use_runbuild_for_rebuild.md`, use:

```bash
# Create the empty stdin file referenced in the runbuild pattern (only needed once per session)
: > desktop/.empty-stdin

# Kick off runbuild.bat (foreground or background — adapt to current session needs)
powershell.exe -NoProfile -Command "Start-Process -FilePath 'desktop\runbuild.bat' -Wait -NoNewWindow -RedirectStandardOutput 'desktop\build-log-2.64.txt' -RedirectStandardError 'desktop\build-log-2.64.txt.err' -RedirectStandardInput 'desktop\.empty-stdin'"
```

Expected: `desktop/dist/NGL Accounting Setup 2.64.0.exe` exists. Build log shows no JS syntax errors.

If the JS check fails, look at the error, fix it in the source file, commit the fix as `fix(invoice-sender/v64): <error description>`, and re-run.

- [ ] **Step 2: Install + launch the packaged app**

Install `NGL Accounting Setup 2.64.0.exe` (overwriting the previous install). Launch the app. Log in.

- [ ] **Step 3: Run through the 9 smoke scenarios from spec §14**

Each scenario should pass. Document any bugs in a simple checklist as you go; fix and re-test after.

1. **Cold start, no Excel.** Invoice Sender opens. All Invoices tab active. Empty state shows "📋 Upload a TMS Excel to see invoices here". No progress strip. No banner. Toolbar reads "— upload an Excel to begin —".

2. **Upload Excel with 5 validation issues (one of each: missing INV#, missing Amount, duplicate INV#, unmatched customer code, customer with no email).** Tab auto-switches to Needs Attention. Five rows visible, each with the correct pill. Click Resolve on each row → panel content matches.

3. **Send a clean batch of 5 invoices.** Progress strip appears. `Started at HH:MM`, elapsed ticks every second, ETA + avg fill in after the first row. Row badges tick `Queued` → `Sending…` → `Sent`. On done: green banner ("All 5 invoices sent successfully…"), tab auto-switches to Sent. Toolbar shows Started + Total + Avg/inv + Report + Audit buttons. Click Report → CSV downloads. Click Audit → audit log loads.

4. **Send a batch with 2 missing-doc failures and 1 simulated QBO timeout.** On done: amber banner ("3 invoices need a fix…"), "Retry the Fixed Ones (0)" button is disabled. Resolve a missing-doc by dropping a PDF in the panel → row clears → button now enabled with count 1. Click it → retry sends that row.

5. **Send a batch, force QBO to return 401 on the first row** (Tools → DevTools → Application → Cookies, delete the QBO session cookie before clicking Send). On done: red banner ("X invoices couldn't send — looks like you got signed out of QuickBooks"), "Sign back in to QuickBooks" + "Retry These (X)" buttons. Click sign-back-in → QBO OAuth flow opens.

6. **Send a batch where ≥25% of rows fail (use a small batch — 4 rows, 1 fail = 25%).** Red banner triggered by ratio.

7. **Excel parsed, then PDFs uploaded later.** Pre-send pills initially include `No PDF Match` on every row (because no PDFs yet). After dropping PDFs, those pills clear automatically for matching containers.

8. **Mid-send the user opens the side panel for an already-Sent row.** Panel opens cleanly, shows a minimal "✓ Sent" state (no Resolve action needed). Close button works.

9. **All clean, 50+ sent.** Green banner, View Audit button works, no Retry button (nothing to retry).

- [ ] **Step 4: Fix any bugs surfaced by smoke**

For each bug, edit the source, save, re-run Step 1 to rebuild, re-test. Commit each fix as `fix(invoice-sender/v64): <description>`.

- [ ] **Step 5: Once all 9 scenarios pass, mark this task complete**

Do NOT push yet. Wait for explicit user OK (per `feedback_always_push_and_release.md` updated 2026-05-14).

---

### Task 12: Ship (user-gated)

**Files:** None — just git/gh operations.

**Why last:** Manual smoke test must pass first; user must explicitly OK before push.

- [ ] **Step 1: Wait for user OK**

After Task 11 passes, summarize the smoke results to the user and ask for explicit "push it" before continuing.

- [ ] **Step 2: Push to remote**

```bash
git push origin main
```

- [ ] **Step 3: Create GitHub release with installer + latest.yml**

```bash
gh release create v2.64.0 \
  "desktop/dist/NGL Accounting Setup 2.64.0.exe" \
  "desktop/dist/latest.yml" \
  --title "v2.64.0 — Combined Results HUD redesign" \
  --notes "$(cat <<'EOF'
## What changed
- Combined the three stacked Invoice Sender result UIs (old "Send Complete" summary card + old filter+table + new v2.62 tabbed view) into one HUD above Status Log
- "Needs Attention" tab now surfaces every blocker: pre-send validation issues (missing INV#, unmatched customer, no email, no PDF, duplicates) + post-send failures + TMS fetch failures
- New row-level "Resolve" button opens an updated detail panel with plain-English explanations and a Technical Detail block with 📋 Copy for support
- Progress strip during send now shows when the send started, live elapsed time, ETA, and average per invoice
- Banner severity: amber for soft warnings, red for hard system errors (signed-out / no internet), green for all-clean
- All pill labels rewritten in plain English — "QBO API timeout" → "QuickBooks Timed Out", "Unknown Customer" → "No Customer Match", etc.
- Color palette mirrors the merge tool exactly so both tools feel like one app

## Tech
- No agent changes — frontend-only
- Mirrors merge tool palette tokens directly
EOF
)"
```

- [ ] **Step 4: Update MEMORY.md**

Add a note under "## Merge-tool UX redesign" or create a new section "## Invoice Sender Combined Results HUD" with one line pointing to a memory file that captures the v2.64 shipment. Use existing memory file pattern — append to MEMORY.md and create the referenced memory file.

```bash
# Pseudo — adapt to actual structure:
# Edit C:/Users/Joseph/.claude/projects/.../memory/MEMORY.md to add:
# - [Invoice Sender Combined Results HUD](project_invoice_sender_combined_hud.md) — SHIPPED v2.64.0 on 2026-05-14. Replaces v2.62 Fix 1 + Fix 2 with single combined HUD; pre-send validation + post-send results unified in one tabbed component.
```

Create the referenced `project_invoice_sender_combined_hud.md` with key implementation notes (file paths, state machine summary, pill taxonomy).

---

## Self-Review

Quick fresh-eyes pass against the spec:

**1. Spec coverage:**
- §4 UX decisions → Tasks 1 (CSS), 2 (DOM), 5-6 (Resolve button + state machine), 8 (panel copy). ✓
- §5 DOM structure → Tasks 2 (HTML restructure), 3 (delete old JS targeting deleted DOM). ✓
- §6 State machine → Task 6 (`getStage`, `pickDefaultTab`, render orchestration). ✓
- §6.1 Empty state copy → Task 6 (`renderEmptyState`). ✓
- §7 Pill taxonomy → Tasks 4 (validator), 5 (`badgeFor` + helpers). ✓
- §8 Banner → Task 6 (`pickBannerSeverity`, `getBannerCopy`). ✓
- §9 Resolve flow → Task 8 (`buildDiagnostic` per pill kind + Technical Detail block). ✓
- §10 Toolbar → Task 6 (`renderToolbar`). ✓
- §11 Progress strip → Tasks 2 (DOM), 7 (live render + interval). ✓
- §12 Color tokens → Task 1. ✓
- §13 Files touched → Tasks 1-9 cover all four files. ✓
- §14 Smoke tests → Task 11 has all 9. ✓
- §15 Migration / rollout → Tasks 10 (version bump), 12 (ship). ✓
- TMS Failed Rows merge → Task 9. ✓

No spec gaps found.

**2. Placeholder scan:** No "TBD" / "TODO" / "fill in later". Every code step has a complete code block.

**3. Type consistency:** `validationStatus` strings (`missing_inv`, `missing_field`, `duplicate_inv`, `no_pdf_match`, `no_customer_match`, `no_email`, `customer_needs_review`, `tms_fetch_failed`) are used consistently across Task 4 (validator), Task 5 (`validationBadge`), Task 8 (`validationDiagnostic`), Task 9 (TMS handler). `classifyError` kinds (`signed_out`, `no_internet`, `qbo_timed_out`, `unexpected_error`) used consistently across Task 5 (`errorBadge`), Task 6 (`pickBannerSeverity`, `getBannerCopy`), Task 8 (`errorDiagnostic`). CSS class names (`v64-pill-*`, `v64-ab-*`, `v64-resolve-btn`) consistent across Tasks 1, 2, 5, 8.

Plan is consistent, no inline fixes needed.
