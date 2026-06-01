# AR Dashboard Release 1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Status (2026-06-01):** Plan front matter + scope updated to reflect spec v2 (Jihyun's input folded in). New phases L (Supabase R/W) and M (Inline editing + Overpayment Workflow + TAB BANK error handling) are outlined at the end but **detailed steps are TBD pending Jihyun's answers to 6 open questions** (see spec §9). Existing Phases A–K are valid for scaffolding work that doesn't depend on those answers.

**Goal:** Ship Release 1 of the AR Dashboard — Tool #4 in NGL Accounting. The dashboard loads a hand-built `AR_AGING_*.xlsx` workbook and presents it as a reconciliation cockpit with 10 tabs, an Exceptions worklist (10 categories) that surfaces mismatches across TAB BANK / QBO / TMS / AR data, write affordances (inline edit + per-row memos + overpayment workflow), and cross-tool actions (Email customer, Email TAB BANK on error, Open in QBO, Copy TAB BANK report). The build engine that produces the workbook is **deferred to a follow-on plan** (R2). R1 is useful standalone — replaces lookup + surfaces exceptions + lets her FIX them in place.

**Architecture:**
- Mostly client-side JS for UI/workbook parsing. **Supabase R/W from day one** for: customer matching (read), inline edits + per-row memos + manual entries + resolved exceptions (write).
- Workbook is parsed in the browser with SheetJS (already loaded).
- New tool module `app/assets/js/tools/ar-dashboard/` with multiple sub-files (mirrors `tools/merge/` pattern).
- Native ES modules, no build step. `app.js` statically imports the entry point and routes `switchTool('ar-dashboard')` to it.
- State held in an `arState` object on `window`, consistent with how other tools (`state`, `invoiceState`) work.
- UI uses existing visual tokens (`--ngl-orange #ea580c`, slate text `#1e293b`, `#e2e8f0` borders, two-pane split pattern from mockups).
- Tab routing is internal to the tool (active-tab state on `arState`, render dispatched on switch).

**Tech Stack:** Vanilla HTML / CSS / ES module JS (no framework, no build). SheetJS (already loaded via CDN). Supabase JS client (already used by Customer Manager). No agent endpoints in R1 (those land in R2 for QBO auto-fetch).

**Spec:** `docs/superpowers/specs/2026-05-20-ar-dashboard-design.md` (updated 2026-06-01)

**Scope explicitly in R1:**
- Load 1 workbook (drop or browse)
- Parse all 7 sheets into the in-memory model
- 10 tabs: Summary, AR Register, Collections, Overdue, Partial Pays, New Invoices, TMS, Adjustments, Suspense, Customers
- Exception detection in **10 categories** (added cat 9 TAB BANK posting error, cat 10 UC awaiting reclassification)
- Exceptions worklist on Summary tab (the centerpiece)
- **Inline editing of any AR Register row** (amount, paid, balance, memo, status) with day-over-day persistence in Supabase
- **Per-row memo notes** — freeform, persisted across rebuilds via Supabase
- **Overpayment Workflow modal** — 4-step guided process (confirm → push to TMS/QBO → create credit → land in AR)
- **TAB BANK error workflow** — paste-ready email body + "awaiting correction" status
- **UC reclassification linkage** — link prior-day UC rows to later Payment rows by check#+customer+amount
- Cross-tool actions: Email customer (jump to Invoice Sender) · Email TAB BANK (new) · Open invoice in QBO · Add manual entry · Edit row · Edit memo · Overpayment workflow
- Today + Week views (Month/Quarter/Year defer to R3)

**Scope explicitly NOT in R1:**
- Build engine (R2)
- QBO API auto-fetch (R2)
- Daily snapshot writes to Supabase (R2)
- Month / Quarter / Year views (R3)
- Per-customer history pages (R3)
- Older-invoices write-off lifecycle (R3, may use R1's inline edit as workaround)
- Management Q&A (R4)

**Rollout:** Bundled into the next version. Standard ship pipeline applies: bump VERSION → `runbuild.bat` → commit + push → `gh release create` with installer + `latest.yml`.

---

## Plan Amendments (2026-06-01) — what changed since the original plan

### Existing tasks needing extension during execution

| Task | What needs to change |
|---|---|
| **Task 5 — AR Register tab** | Add inline edit affordance per row (amount, paid, balance, memo, status). On commit, write override to Supabase + show "edited" pip. See new Phase L for Supabase wiring. |
| **Task 11 — Exception detection** | Add 2 new classifiers: (cat 9) TAB BANK posting error — same check# on multiple rows with conflicting status, OR check# applied where deposit ≠ AR balance; (cat 10) UC awaiting reclassification — prior-day UC row with no matching Payment yet. |
| **Task 12 — Summary tab** | Exceptions worklist now shows 10 categories instead of 8. UI fits new cats. |
| **Task 14 — Cross-tool actions** | Add 4 new actions: Email TAB BANK (cat 9 action), Edit row, Edit memo, Open Overpayment Workflow modal (cat 3 action). See new Phase M. |

### New phases (outlined; detailed steps pending Jihyun answers)

- **Phase L — Supabase R/W layer.** Wire `ar_row_overrides`, `ar_memos`, `ar_manual_entries`, `ar_exceptions` tables. Load overrides into the in-memory model after workbook parse. Write changes on edit commit. Optimistic UI with rollback on network failure.
- **Phase M — Inline editing + Overpayment Workflow + TAB BANK error workflow.** The actual UI affordances that USE the Supabase layer from Phase L. Inline-edit cells in AR Register. Overpayment Workflow modal (4 steps). Email TAB BANK template + "awaiting correction" status banner.

### New files to add (extends File Structure table below)

- `app/assets/js/tools/ar-dashboard/ar-dashboard-supabase.js` — Supabase R/W (new)
- `app/assets/js/tools/ar-dashboard/ar-dashboard-overpayment.js` — Overpayment Workflow modal (new)

### Dependencies on Jihyun's open questions

| Question (spec §9) | Affects |
|---|---|
| Q1 — TAB BANK report format | Task 14 (existing) — final shape of clipboard string |
| Q2 — TMS data error decision rule | Task 11 cat 5 — confidence threshold |
| Q3 — Auto-flag "unaccounted-for" rows | Task 11 — whether to add an implicit cat 11 |
| Q4 — Write-off lifecycle | Possibly Phase M — whether `WRITE_OFF` is just a status or needs its own flow |
| Q5 — Overpayment step-2 verification | Phase M Overpayment Workflow — auto re-fetch vs trust checkbox |
| Q6 — UC auto-link confidence | Task 11 cat 10 + Phase L — automatic clear vs human confirm |

**Recommendation:** start Phases A–C (scaffolding + loader + tab shell) now — those are independent of Jihyun's answers. Pause before Tasks 5 / 11 / 12 / 14 + Phases L / M until her answers land.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `app/assets/js/tools/ar-dashboard/ar-dashboard.js` | Entry point: state machine, tab routing, top-level render | **Create** |
| `app/assets/js/tools/ar-dashboard/ar-dashboard-loader.js` | Parse a workbook file into the in-memory model | **Create** |
| `app/assets/js/tools/ar-dashboard/ar-dashboard-model.js` | Pure helpers: aging buckets, AR row constructor, exception classifiers | **Create** |
| `app/assets/js/tools/ar-dashboard/ar-dashboard-exceptions.js` | Detect all 10 exception categories from the model | **Create** |
| `app/assets/js/tools/ar-dashboard/ar-dashboard-views.js` | Render functions for each of the 10 tabs | **Create** |
| `app/assets/js/tools/ar-dashboard/ar-dashboard-actions.js` | Cross-tool actions: email jump, QBO deep link, add manual entry, email TAB BANK | **Create** |
| `app/assets/js/tools/ar-dashboard/ar-dashboard-supabase.js` | Supabase R/W: overrides, memos, manual entries, resolved exceptions | **Create** (Phase L) |
| `app/assets/js/tools/ar-dashboard/ar-dashboard-overpayment.js` | Overpayment Workflow modal (4-step guided process) | **Create** (Phase M) |
| `app/index.html` | Sidebar nav entry, `#arDashboardView` container, file-drop overlay markup | Modify |
| `app/assets/js/app.js` | `switchTool('ar-dashboard')` case + `initArDashboard()` import + subtitle entry | Modify |
| `app/assets/css/styles.css` | Add `.ar-*` utility classes (panels, tables, two-pane split, exceptions worklist styling, inline-edit cell, overpayment modal) | Modify |
| `app/assets/js/shared/state.js` | Add `window.arState` declaration | Modify |

---

## Phase A — Scaffold & wiring

### Task 1: Stub the tool module + add it to switchTool

**Files:**
- Create: `app/assets/js/tools/ar-dashboard/ar-dashboard.js`
- Modify: `app/index.html` (sidebar entry + view container)
- Modify: `app/assets/js/app.js` (switchTool case)
- Modify: `app/assets/js/shared/state.js` (arState declaration)

- [ ] **Step 1.1: Read state.js to find the right place to add `arState`**

Run: `cat app/assets/js/shared/state.js`

Note the pattern (other tools have global state objects on window). Add `arState` consistently — at the end of the file, exported the same way as siblings.

- [ ] **Step 1.2: Add `arState` to state.js**

Append to `app/assets/js/shared/state.js`:

```js
// ── AR Dashboard state ──
window.arState = {
  loaded: false,                 // true once a workbook is parsed
  filename: null,                // currently-loaded filename
  model: null,                   // parsed in-memory model (see ar-dashboard-loader.js)
  activeTab: 'summary',          // which sub-tab is visible
  selectedRows: {},              // per-tab selection state, e.g. { collections: 'A0905186835' }
  exceptions: [],                // flat list of detected exceptions
  manualEntries: [],             // user-added rows (credit memos, warehouse, etc.)
  resolvedExceptions: new Set(), // exception IDs marked resolved in this session
};
```

- [ ] **Step 1.3: Create the empty tool file**

Create `app/assets/js/tools/ar-dashboard/ar-dashboard.js`:

```js
// AR Dashboard — Tool #4
// Reconciliation cockpit. Loads a pre-built AR_AGING_*.xlsx workbook and presents
// it as 9 tabs + Exceptions worklist + cross-tool actions.
//
// State: window.arState (see shared/state.js)
// Spec:  docs/superpowers/specs/2026-05-20-ar-dashboard-design.md

window.initArDashboard = function initArDashboard() {
  const view = document.getElementById('arDashboardView');
  if (!view) return;

  if (!arState.loaded) {
    renderEmptyState(view);
  } else {
    renderLoaded(view);
  }
};

function renderEmptyState(view) {
  view.innerHTML = `
    <div class="ar-empty-card">
      <div class="ar-empty-icon">📊</div>
      <h3>Load today's AR aging workbook</h3>
      <p>Drop an <code>AR_AGING_MM_DD_YYYY.xlsx</code> file here or browse to pick one. The dashboard will parse all 7 sheets and surface today's reconciliation exceptions.</p>
      <button class="ngl-btn ngl-btn-primary" id="arBrowseBtn">Browse for workbook</button>
      <input type="file" id="arFileInput" accept=".xlsx,.xls" style="display:none" />
    </div>
  `;
  view.querySelector('#arBrowseBtn').addEventListener('click', () => {
    view.querySelector('#arFileInput').click();
  });
  view.querySelector('#arFileInput').addEventListener('change', handleFileSelected);
  // Drop-zone wiring is set up in app.js (consistent with other tools).
}

function renderLoaded(view) {
  // Implemented in Phase D when views.js is wired in.
  view.innerHTML = `<div>Loaded: ${arState.filename} (${arState.model?.ar_register?.length ?? 0} rows)</div>`;
}

function handleFileSelected(e) {
  const file = e.target.files && e.target.files[0];
  if (!file) return;
  // Loader is implemented in Phase B.
  if (typeof window.arLoadWorkbook === 'function') {
    window.arLoadWorkbook(file);
  } else {
    console.warn('AR loader not yet implemented');
  }
}
```

- [ ] **Step 1.4: Find sidebar + view container patterns in index.html**

Run: `grep -n "navCustomers\|customerView" app/index.html | head -8`

Note the exact pattern. The sidebar `nav-item` and the matching view container should follow the same structure.

- [ ] **Step 1.5: Add sidebar entry in index.html**

Find the sidebar nav block (search for `navCustomers`). Add a new nav item directly after `navCustomers`:

```html
<div class="nav-item" id="navArDashboard" onclick="switchTool('ar-dashboard')">
  <span class="nav-ico">📊</span>
  <span class="nav-label">AR Dashboard</span>
</div>
```

Match the indentation and structure of the surrounding nav items exactly.

- [ ] **Step 1.6: Add view container in index.html**

Find the existing `<div id="customerView">` block. After its closing `</div>`, add:

```html
<div id="arDashboardView" class="tool-view" style="display:none"></div>
```

- [ ] **Step 1.7: Wire switchTool in app.js**

Open `app/assets/js/app.js`. Find the `switchTool` function (line ~353). Inside the function:

1. In the "Hide all views" block, add:
```js
document.getElementById('arDashboardView').style.display = 'none';
```

2. Add a new `else if` branch alongside the other tool branches:
```js
} else if (tool === 'ar-dashboard') {
  document.getElementById('arDashboardView').style.display = '';
  if (window.initArDashboard) window.initArDashboard();
}
```

3. In the `subtitles` object, add:
```js
'ar-dashboard': 'AR Dashboard',
```

4. In the sidebar active-state block, add:
```js
const navArDashboard = document.getElementById('navArDashboard');
if (navArDashboard) navArDashboard.classList.toggle('active', tool === 'ar-dashboard');
```

- [ ] **Step 1.8: Statically import the tool from app.js**

Open `app/assets/js/app.js`. Find the existing static imports at the top (lines 1-20). Add:

```js
import './tools/ar-dashboard/ar-dashboard.js';
```

- [ ] **Step 1.9: Open the app, navigate to AR Dashboard, verify empty state appears**

Run: `start "" "app/index.html"` (or have the user open the app)

Expected: clicking the new "AR Dashboard" nav item shows the empty card with "Load today's AR aging workbook" + Browse button. The Browse button opens a file picker (file selection has no effect yet — that's Phase B).

- [ ] **Step 1.10: Commit**

```bash
git add app/assets/js/tools/ar-dashboard/ar-dashboard.js \
        app/assets/js/shared/state.js \
        app/assets/js/app.js \
        app/index.html
git commit -m "$(cat <<'EOF'
feat(ar-dashboard): scaffold tool #4 — empty state + sidebar entry

Adds the AR Dashboard scaffold: nav entry, view container, switchTool
routing, and an empty state with a file-picker. No workbook parsing yet.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase B — Workbook loader + in-memory model

### Task 2: Define the in-memory model + AR row constructor

**Files:**
- Create: `app/assets/js/tools/ar-dashboard/ar-dashboard-model.js`

- [ ] **Step 2.1: Create the model file**

Create `app/assets/js/tools/ar-dashboard/ar-dashboard-model.js`:

```js
// AR Dashboard — pure model helpers
// No DOM, no I/O. Just data transforms used by the loader and exception detectors.

// AR register row schema (matches Excel column order on AR_<date> sheet).
window.arBuildARRow = function arBuildARRow(tuple) {
  // tuple is a SheetJS row array in column order:
  //   [NEW ID, COMPANY, NGL INV #, EQUIPMENT#, DATE, AGING,
  //    REF NO, MBL NO, AMOUNT, PAID, BALANCE, MEMO, AR STATUS, WO #]
  return {
    new_id:    tuple[0]  ?? null,
    company:   tuple[1]  ?? null,
    inv:       tuple[2]  ?? null,
    equipment: tuple[3]  ?? null,
    date:      tuple[4]  ?? null,
    aging:     tuple[5]  ?? null,
    ref_no:    tuple[6]  ?? null,
    mbl_no:    tuple[7]  ?? null,
    amount:    tuple[8]  ?? null,
    paid:      tuple[9]  ?? null,
    balance:   tuple[10] ?? null,
    memo:      tuple[11] ?? null,
    ar_status: tuple[12] ?? null,
    wo:        tuple[13] ?? null,
  };
};

// Compute aging bucket label from a day count.
window.arAgingBucket = function arAgingBucket(days) {
  if (days == null) return '';
  if (days < 30)  return 'A.0~29';
  if (days < 60)  return 'B.30~59';
  if (days < 90)  return 'C.60~89';
  if (days < 120) return 'D.90~119';
  return 'E.120+';
};

// Numeric tolerance for amount comparisons (avoids floating-point noise).
window.AR_AMT_EPS = 0.01;

// Compare two amounts with epsilon tolerance.
window.arAmtEq = function arAmtEq(a, b) {
  if (a == null && b == null) return true;
  if (a == null || b == null) return false;
  return Math.abs(Number(a) - Number(b)) < window.AR_AMT_EPS;
};

// Detect whether an open balance indicates short / over / full pay.
// Returns 'full' | 'short' | 'over' | 'unknown'.
window.arClassifyOpenBalance = function arClassifyOpenBalance(openBalance) {
  if (openBalance == null) return 'unknown';
  const n = Number(openBalance);
  if (Math.abs(n) < window.AR_AMT_EPS) return 'full';
  if (n > 0) return 'short';
  return 'over';
};

// Parse "[CUSTID] Customer Name" strings out of QBO Daily Collection rows.
window.arParseCustomerField = function arParseCustomerField(field) {
  if (!field || typeof field !== 'string') return { id: null, name: field || '' };
  if (field[0] === '[') {
    const close = field.indexOf(']');
    if (close > 0) {
      return { id: field.slice(1, close), name: field.slice(close + 1).trim() };
    }
  }
  return { id: null, name: field };
};
```

- [ ] **Step 2.2: Sanity-check in browser console**

Open the app's dev tools, navigate to AR Dashboard, then in console:

```js
arBuildARRow(['CUST01', 'TEST CO', 'INV-001', null, null, 45, 'R', 'MBL', 1000, 800, 200, null, 'B.30~59', 'WO-1']).balance
// Expected: 200

arAgingBucket(45)        // Expected: 'B.30~59'
arAgingBucket(0)         // Expected: 'A.0~29'
arAgingBucket(200)       // Expected: 'E.120+'

arClassifyOpenBalance(0)     // Expected: 'full'
arClassifyOpenBalance(50)    // Expected: 'short'
arClassifyOpenBalance(-25)   // Expected: 'over'

arParseCustomerField('[101FOO01] 101 FOODS')
// Expected: { id: '101FOO01', name: '101 FOODS' }
```

If any of these are wrong, fix the helper before moving on.

- [ ] **Step 2.3: Import model into index.html (script tag) — N/A, it's ES module loaded via app.js**

Add the import to the top of `ar-dashboard.js`:

```js
import './ar-dashboard-model.js';
```

- [ ] **Step 2.4: Commit**

```bash
git add app/assets/js/tools/ar-dashboard/ar-dashboard-model.js \
        app/assets/js/tools/ar-dashboard/ar-dashboard.js
git commit -m "feat(ar-dashboard): pure model helpers (AR row, aging bucket, classification)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Workbook loader — parse all 7 sheets

**Files:**
- Create: `app/assets/js/tools/ar-dashboard/ar-dashboard-loader.js`

- [ ] **Step 3.1: Create the loader file**

Create `app/assets/js/tools/ar-dashboard/ar-dashboard-loader.js`:

```js
// AR Dashboard — workbook loader
// Reads a single AR_AGING_*.xlsx file, parses all 7 sheets into the in-memory model.

window.arLoadWorkbook = async function arLoadWorkbook(file) {
  try {
    const buf = await file.arrayBuffer();
    const wb = XLSX.read(buf, { type: 'array', cellDates: true });
    const model = parseWorkbook(wb, file.name);
    arState.loaded = true;
    arState.filename = file.name;
    arState.model = model;
    // Exception detection happens in Phase G after exceptions.js exists.
    if (window.arDetectExceptions) {
      arState.exceptions = window.arDetectExceptions(model);
    }
    if (window.initArDashboard) window.initArDashboard();
  } catch (e) {
    console.error('Failed to load workbook', e);
    alert(`Failed to load ${file.name}: ${e.message}`);
  }
};

function parseWorkbook(wb, filename) {
  const arSheetName = wb.SheetNames.find(s => /^AR_\d\d_\d\d_\d\d$/.test(s));
  const yestSheetName = wb.SheetNames.find(s => /^AR_\d\d_\d\d_\d\d$/.test(s) && s !== arSheetName);

  if (!arSheetName) throw new Error('Workbook is missing the AR_<date> sheet');

  const todayDate = parseAgingSheetDate(arSheetName);
  const yesterdayDate = yestSheetName ? parseAgingSheetDate(yestSheetName) : null;

  return {
    filename,
    today_date: todayDate,
    yesterday_date: yesterdayDate,
    ar_register: parseArSheet(wb.Sheets[arSheetName]),
    ar_yesterday: yestSheetName ? parseArSheet(wb.Sheets[yestSheetName]) : [],
    collections: parseCollectionsSheet(wb.Sheets['COL']),
    collections_tagged: parseCollectionsSheet(wb.Sheets['COL (INV)']),
    schedule: parseScheduleSheet(wb.Sheets['Schedule']),
    tms_rows: parseTmsSheet(wb.Sheets['TMS']),
    adjustments: parseAdjustmentSheet(wb.Sheets['ADJUSTMENT']),
  };
}

function parseAgingSheetDate(name) {
  // AR_05_19_26 → '2026-05-19'
  const m = name.match(/^AR_(\d\d)_(\d\d)_(\d\d)$/);
  if (!m) return null;
  return `20${m[3]}-${m[1]}-${m[2]}`;
}

function parseArSheet(sheet) {
  if (!sheet) return [];
  const rows = XLSX.utils.sheet_to_json(sheet, { header: 1, defval: null });
  // Row 0 is header. Skip rows where every cell is null.
  return rows.slice(1)
    .filter(r => r.some(c => c != null && c !== ''))
    .map(r => window.arBuildARRow(r));
}

function parseCollectionsSheet(sheet) {
  if (!sheet) return [];
  // QBO Daily Collection layout: 4 title rows, then header on row 4, then data rows
  // interleaved with blanks and a grand-total row at the end.
  const rows = XLSX.utils.sheet_to_json(sheet, { header: 1, defval: null });
  const out = [];
  for (const r of rows) {
    if (!r || !r[1]) continue;
    if (r[1] !== 'Invoice' && r[1] !== 'Payment') continue;
    const customerField = window.arParseCustomerField(r[3]);
    out.push({
      payment_date: r[0],
      txn_type: r[1],
      check_no: r[2],
      customer_id: customerField.id,
      customer_name: customerField.name,
      invoice_or_ref: r[4],
      amount: r[5],
      open_balance: r[6],
      account: r[7],
    });
  }
  return out;
}

function parseScheduleSheet(sheet) {
  if (!sheet) return [];
  const rows = XLSX.utils.sheet_to_json(sheet, { header: 1, defval: null });
  const out = [];
  for (const r of rows) {
    if (!r || !r[1]) continue;
    if (r[1] !== 'Invoice') continue;
    const customerField = window.arParseCustomerField(r[2]);
    out.push({
      date: r[0],
      customer_id: customerField.id,
      customer_name: customerField.name,
      inv: r[3],
      ref: r[4],
      cntr_chassis: r[5],
      bl: r[6],
      amount: r[7],
    });
  }
  return out;
}

function parseTmsSheet(sheet) {
  if (!sheet) return [];
  const rows = XLSX.utils.sheet_to_json(sheet, { header: 1, defval: null });
  // Header on row 0. Skip blank rows.
  return rows.slice(1)
    .filter(r => r && r.some(c => c != null && c !== ''))
    .map(r => ({
      wo_div:        r[0],
      type:          r[1],
      id:            r[2],
      name:          r[3],
      status:        r[4],
      date:          r[5],
      wo_no:         r[6],
      equipment:     r[7],
      cat:           r[8],
      total_amt:     r[9],
      inv_no:        r[10],
      qb_id:         r[11],
      ref_no:        r[12],
      mbl_booking:   r[13],
      inv_amt:       r[14],
      paid_received: r[15],
      qb_date:       r[16],
    }));
}

function parseAdjustmentSheet(sheet) {
  if (!sheet) return [];
  const rows = XLSX.utils.sheet_to_json(sheet, { header: 1, defval: null });
  return rows.slice(1)
    .filter(r => r && r.some(c => c != null && c !== ''))
    .map(r => ({
      div:                    r[0],
      type:                   r[1],
      id:                     r[2],
      name:                   r[3],
      status:                 r[4],
      date:                   r[5],
      wo_no:                  r[6],
      equipment:              r[7],
      cat:                    r[8],
      amount_difference:      r[9],
      inv_no:                 r[10],
      qb_id:                  r[11],
      ref_no:                 r[12],
      mbl_booking:            r[13],
      revised_invoice_amount: r[14],
      paid_received:          r[15],
      qb_date:                r[16],
    }));
}
```

- [ ] **Step 3.2: Add the loader import to ar-dashboard.js**

In `app/assets/js/tools/ar-dashboard/ar-dashboard.js`, add to the imports at the top:

```js
import './ar-dashboard-loader.js';
```

- [ ] **Step 3.3: Drop the 5/19 target workbook into the empty state and confirm parsing**

Manually drag `app/AR_AGING_assets/5/20 data (5/19)/AR_AGING_05_19_2026.xlsx` onto the file picker (or browse to it).

Expected: the temporary "loaded" message shows `Loaded: AR_AGING_05_19_2026.xlsx (4149 rows)`.

In the browser console, inspect:

```js
arState.model.ar_register.length      // 4149
arState.model.collections.length      // 42 (Invoice + Payment lines)
arState.model.schedule.length         // 102
arState.model.tms_rows.length         // 104
arState.model.adjustments.length      // 8
arState.model.today_date              // '2026-05-19'
arState.model.yesterday_date          // '2026-05-18'
```

If any of these are wildly off (e.g., 0), inspect the workbook structure with `XLSX.utils.sheet_to_json(wb.Sheets['Schedule'])` and adjust the parser.

- [ ] **Step 3.4: Commit**

```bash
git add app/assets/js/tools/ar-dashboard/ar-dashboard-loader.js \
        app/assets/js/tools/ar-dashboard/ar-dashboard.js
git commit -m "feat(ar-dashboard): workbook loader parses all 7 sheets into model

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase C — Loaded shell (tab strip + active-tab routing)

### Task 4: Render the loaded shell with empty tabs

**Files:**
- Create: `app/assets/js/tools/ar-dashboard/ar-dashboard-views.js`
- Modify: `app/assets/js/tools/ar-dashboard/ar-dashboard.js`
- Modify: `app/assets/css/styles.css`

- [ ] **Step 4.1: Add the AR-specific styles to styles.css**

Append to `app/assets/css/styles.css`:

```css
/* ── AR Dashboard ── */
.ar-empty-card { background:#fff; border:1px solid #e2e8f0; border-radius:10px;
  padding:40px 32px; max-width:480px; margin:80px auto; text-align:center; }
.ar-empty-card .ar-empty-icon { font-size:2rem; margin-bottom:14px; }
.ar-empty-card h3 { font-size:1.05rem; font-weight:800; color:#0f172a; margin-bottom:6px; }
.ar-empty-card p { font-size:0.85rem; color:#64748b; margin:0 0 14px; line-height:1.45; }
.ar-empty-card code { font-family:'Consolas',monospace; background:#f1f5f9; padding:1px 6px;
  border-radius:4px; font-size:0.78rem; }

.ar-shell { display:flex; flex-direction:column; height:100%; background:#f8fafc; }
.ar-data-bar { display:flex; gap:8px; align-items:center; background:#fff;
  border-bottom:1px solid #e2e8f0; padding:8px 16px; font-size:0.78rem; color:#475569; }
.ar-data-bar .pill { background:#dcfce7; color:#166534; padding:2px 8px; border-radius:4px;
  font-size:0.68rem; font-weight:700; }
.ar-data-bar .filename { color:#0f172a; font-weight:700; font-family:'Consolas',monospace; font-size:0.76rem; }
.ar-data-bar .sep { color:#cbd5e1; }
.ar-data-bar .right-link { margin-left:auto; color:#ea580c; font-weight:700; cursor:pointer; text-decoration:none; }

.ar-tabs { display:flex; border-bottom:2px solid #e2e8f0; background:#fff; padding:0 16px; overflow-x:auto; }
.ar-tab { padding:9px 14px; background:none; border:none; border-bottom:3px solid transparent;
  margin-bottom:-2px; font-size:0.85rem; font-weight:600; color:#64748b; cursor:pointer;
  display:flex; align-items:center; gap:7px; white-space:nowrap; }
.ar-tab:hover { color:#0f172a; }
.ar-tab.active { color:#ea580c; border-bottom-color:#ea580c; }
.ar-tab .count { background:#f1f5f9; color:#475569; padding:1px 7px; border-radius:999px;
  font-size:0.68rem; font-weight:700; }
.ar-tab.active .count { background:#ea580c; color:#fff; }
.ar-tab .count.urgent { background:#fee2e2; color:#b91c1c; }

.ar-tab-body { flex:1; padding:14px 20px; overflow-y:auto; }

.ar-section-h { display:flex; align-items:center; gap:10px; font-size:0.95rem; font-weight:800;
  color:#0f172a; padding:8px 12px; margin:0 0 10px; background:#fff;
  border-left:4px solid #ea580c; border-radius:0 6px 6px 0;
  border-top:1px solid #e2e8f0; border-right:1px solid #e2e8f0; border-bottom:1px solid #e2e8f0;
  box-shadow:0 1px 2px rgba(0,0,0,0.03); }
.ar-section-h .sub { font-size:0.74rem; color:#64748b; font-weight:500; margin-left:auto; }
```

- [ ] **Step 4.2: Create the views file with the shell renderer and empty tab stubs**

Create `app/assets/js/tools/ar-dashboard/ar-dashboard-views.js`:

```js
// AR Dashboard — view renderers
// Each function takes the model + active tab and returns HTML (or applies it).

const TABS = [
  { id: 'summary',     label: 'Summary',      countOf: m => null },
  { id: 'ar-register', label: 'AR Register',  countOf: m => m.ar_register.length },
  { id: 'collections', label: 'Collections',  countOf: m => uniqueChecks(m.collections).length },
  { id: 'overdue',     label: 'Overdue',      countOf: m => m.ar_register.filter(r => (r.aging ?? 0) >= 30 && (r.balance ?? 0) > 0).length },
  { id: 'partial',     label: 'Partial Pays', countOf: m => m.ar_register.filter(r => (r.paid ?? 0) > 0 && (r.balance ?? 0) > 0).length },
  { id: 'new',         label: 'New Invoices', countOf: m => m.schedule.length },
  { id: 'tms',         label: 'TMS',          countOf: m => m.tms_rows.length },
  { id: 'adjustments', label: 'Adjustments',  countOf: m => m.adjustments.length },
  { id: 'suspense',    label: 'Suspense',     countOf: m => (arState.exceptions || []).filter(e => e.category === 'suspense').length, urgent: true },
  { id: 'customers',   label: 'Customers',    countOf: m => uniqueCustomers(m.ar_register).length },
];

function uniqueChecks(collections) {
  const s = new Set();
  for (const c of collections) if (c.check_no) s.add(c.check_no);
  return [...s];
}

function uniqueCustomers(register) {
  const s = new Set();
  for (const r of register) if (r.new_id) s.add(r.new_id);
  return [...s];
}

window.arRenderLoaded = function arRenderLoaded(view) {
  const m = arState.model;
  view.innerHTML = `
    <div class="ar-shell">
      <div class="ar-data-bar">
        <span class="pill">Loaded</span>
        <span>Workbook:</span><span class="filename">${escapeHtml(arState.filename)}</span>
        <span class="sep">|</span>
        <span>Today: ${m.today_date}</span>
        <span class="sep">|</span>
        <span>Yesterday: ${m.yesterday_date ?? '(none)'}</span>
        <a class="right-link" id="arUnloadBtn">Load different workbook →</a>
      </div>
      <div class="ar-tabs" id="arTabs">
        ${TABS.map(t => renderTabButton(t, m)).join('')}
      </div>
      <div class="ar-tab-body" id="arTabBody"></div>
    </div>
  `;

  view.querySelector('#arUnloadBtn').addEventListener('click', () => {
    arState.loaded = false;
    arState.filename = null;
    arState.model = null;
    arState.exceptions = [];
    if (window.initArDashboard) window.initArDashboard();
  });

  view.querySelectorAll('.ar-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      arState.activeTab = btn.dataset.tab;
      view.querySelectorAll('.ar-tab').forEach(b => b.classList.toggle('active', b === btn));
      renderActiveTab();
    });
  });

  renderActiveTab();
};

function renderTabButton(tab, model) {
  const active = arState.activeTab === tab.id ? 'active' : '';
  const count = tab.countOf(model);
  const urgentClass = tab.urgent && count > 0 ? 'urgent' : '';
  const countHtml = (count != null) ? `<span class="count ${urgentClass}">${count}</span>` : '';
  return `<button class="ar-tab ${active}" data-tab="${tab.id}">${tab.label}${countHtml}</button>`;
}

function renderActiveTab() {
  const body = document.getElementById('arTabBody');
  const tab = arState.activeTab;
  switch (tab) {
    case 'summary':     return window.arRenderSummary && window.arRenderSummary(body);
    case 'ar-register': return window.arRenderRegister && window.arRenderRegister(body);
    case 'collections': return window.arRenderCollections && window.arRenderCollections(body);
    case 'overdue':     return window.arRenderOverdue && window.arRenderOverdue(body);
    case 'partial':     return window.arRenderPartial && window.arRenderPartial(body);
    case 'new':         return window.arRenderNew && window.arRenderNew(body);
    case 'tms':         return window.arRenderTms && window.arRenderTms(body);
    case 'adjustments': return window.arRenderAdjustments && window.arRenderAdjustments(body);
    case 'suspense':    return window.arRenderSuspense && window.arRenderSuspense(body);
    case 'customers':   return window.arRenderCustomers && window.arRenderCustomers(body);
    default: body.innerHTML = `<div>Tab "${tab}" not yet implemented.</div>`;
  }
}

function escapeHtml(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"]/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;' }[c]));
}
window.arEscapeHtml = escapeHtml;
```

- [ ] **Step 4.3: Wire `renderLoaded` to use the new shell renderer**

In `app/assets/js/tools/ar-dashboard/ar-dashboard.js`, replace the placeholder `renderLoaded` function:

```js
function renderLoaded(view) {
  if (window.arRenderLoaded) {
    window.arRenderLoaded(view);
  } else {
    view.innerHTML = `<div>views.js not loaded yet</div>`;
  }
}
```

And add the import at the top:

```js
import './ar-dashboard-views.js';
```

- [ ] **Step 4.4: Visual verification — load the 5/19 workbook and confirm shell renders**

Re-load the 5/19 workbook. Expected:
- Data bar at top shows `Loaded · AR_AGING_05_19_2026.xlsx · Today: 2026-05-19 · Yesterday: 2026-05-18`
- Tab strip shows 10 tabs (Summary active, with counts on others — e.g., "AR Register 4149", "New Invoices 102", "Adjustments 8")
- Clicking a tab activates it (orange underline + body says "not yet implemented" placeholder)
- "Load different workbook →" link returns to empty state

- [ ] **Step 4.5: Commit**

```bash
git add app/assets/js/tools/ar-dashboard/ar-dashboard-views.js \
        app/assets/js/tools/ar-dashboard/ar-dashboard.js \
        app/assets/css/styles.css
git commit -m "feat(ar-dashboard): loaded shell with tab strip + active routing

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase D — AR Register tab (the base data view)

### Task 5: AR Register tab — sortable, searchable table

**Files:**
- Modify: `app/assets/js/tools/ar-dashboard/ar-dashboard-views.js`
- Modify: `app/assets/css/styles.css`

- [ ] **Step 5.1: Add the AR register table styles to styles.css**

Append to `app/assets/css/styles.css`:

```css
.ar-toolbar { display:flex; align-items:center; gap:8px; padding:6px 10px; background:#fff;
  border:1px solid #e2e8f0; border-bottom:none; border-radius:8px 8px 0 0;
  font-size:0.78rem; color:#64748b; flex-wrap:wrap; }
.ar-toolbar input.search { flex:1; min-width:120px; padding:5px 10px; border:1px solid #cbd5e1;
  border-radius:5px; font-size:0.8rem; color:#334155; }
.ar-toolbar .meta { color:#94a3b8; margin-left:auto; font-size:0.72rem; font-weight:600; }

.ar-table-wrap { background:#fff; border:1px solid #e2e8f0; border-top:none;
  border-radius:0 0 8px 8px; overflow:hidden; max-height:calc(100vh - 280px); overflow-y:auto; }
.ar-table { border-collapse:collapse; font-size:0.82rem; width:100%; }
.ar-table th { text-align:left; font-size:0.66rem; font-weight:700; color:#94a3b8;
  letter-spacing:0.03em; text-transform:uppercase; padding:7px 10px;
  border-bottom:1px solid #e2e8f0; background:#fafafa; white-space:nowrap;
  position:sticky; top:0; cursor:pointer; }
.ar-table th .sort-arrow { color:#cbd5e1; margin-left:4px; }
.ar-table th.sorted-asc .sort-arrow,
.ar-table th.sorted-desc .sort-arrow { color:#ea580c; }
.ar-table td { padding:6px 10px; border-bottom:1px solid #f1f5f9; color:#1e293b;
  vertical-align:middle; white-space:nowrap; }
.ar-table tr:hover td { background:#fff7ed; }
.ar-table .num { font-variant-numeric:tabular-nums; text-align:right; }
.ar-table .id { font-family:'Consolas',monospace; font-size:0.74rem; color:#64748b; }
.ar-table .customer { font-weight:700; color:#0f172a; }
.ar-table .neg { color:#b91c1c; }
.ar-table .status-A { color:#15803d; font-weight:600; }
.ar-table .status-B { color:#a16207; font-weight:600; }
.ar-table .status-C { color:#c2410c; font-weight:600; }
.ar-table .status-D { color:#b91c1c; font-weight:600; }
.ar-table .status-E { color:#7c2d12; font-weight:600; }
```

- [ ] **Step 5.2: Add the AR Register renderer to views.js**

Append to `app/assets/js/tools/ar-dashboard/ar-dashboard-views.js`:

```js
// ── AR Register tab ──

const REGISTER_COLUMNS = [
  { key: 'new_id',   label: 'ID',        cell: r => `<td class="id">${escapeHtml(r.new_id)}</td>` },
  { key: 'company',  label: 'Customer',  cell: r => `<td class="customer">${escapeHtml(r.company)}</td>` },
  { key: 'inv',      label: 'NGL Inv#',  cell: r => `<td class="id">${escapeHtml(r.inv)}</td>` },
  { key: 'date',     label: 'Date',      cell: r => `<td>${formatDate(r.date)}</td>` },
  { key: 'aging',    label: 'Aging',     cell: r => `<td class="num">${r.aging ?? ''}</td>` },
  { key: 'amount',   label: 'Amount',    cell: r => `<td class="num">${formatMoney(r.amount)}</td>` },
  { key: 'paid',     label: 'Paid',      cell: r => `<td class="num">${formatMoney(r.paid)}</td>` },
  { key: 'balance',  label: 'Balance',   cell: r => `<td class="num ${(r.balance ?? 0) < 0 ? 'neg' : ''}">${formatMoney(r.balance)}</td>` },
  { key: 'ar_status', label: 'Status',   cell: r => `<td>${formatStatus(r.ar_status)}</td>` },
  { key: 'memo',     label: 'Memo',      cell: r => `<td>${escapeHtml(r.memo)}</td>` },
];

let registerSort = { key: 'aging', dir: 'desc' };
let registerSearch = '';

window.arRenderRegister = function arRenderRegister(body) {
  const rows = arState.model.ar_register;
  body.innerHTML = `
    <h3 class="ar-section-h">AR Register
      <span class="sub">${rows.length} invoices · ${uniqueCustomers(rows).length} customers</span>
    </h3>
    <div class="ar-toolbar">
      <input type="text" class="search" id="arRegSearch" placeholder="Search invoice #, customer, ref, MBL, container..." value="${escapeHtml(registerSearch)}" />
      <span class="meta" id="arRegMeta"></span>
    </div>
    <div class="ar-table-wrap">
      <table class="ar-table">
        <thead><tr id="arRegHead"></tr></thead>
        <tbody id="arRegBody"></tbody>
      </table>
    </div>
  `;
  body.querySelector('#arRegSearch').addEventListener('input', e => {
    registerSearch = e.target.value.toLowerCase();
    paintRegister();
  });
  paintRegister();
};

function paintRegister() {
  const headRow = document.getElementById('arRegHead');
  const bodyEl = document.getElementById('arRegBody');
  const meta = document.getElementById('arRegMeta');
  if (!headRow || !bodyEl) return;

  headRow.innerHTML = REGISTER_COLUMNS.map(c => {
    const cls = registerSort.key === c.key ? (registerSort.dir === 'asc' ? 'sorted-asc' : 'sorted-desc') : '';
    const arrow = cls === 'sorted-asc' ? '↑' : (cls === 'sorted-desc' ? '↓' : '↕');
    return `<th data-key="${c.key}" class="${cls}">${c.label} <span class="sort-arrow">${arrow}</span></th>`;
  }).join('');
  headRow.querySelectorAll('th').forEach(th => {
    th.addEventListener('click', () => {
      const k = th.dataset.key;
      if (registerSort.key === k) {
        registerSort.dir = registerSort.dir === 'asc' ? 'desc' : 'asc';
      } else {
        registerSort = { key: k, dir: 'desc' };
      }
      paintRegister();
    });
  });

  let rows = arState.model.ar_register;
  if (registerSearch) {
    const q = registerSearch;
    rows = rows.filter(r =>
      (r.inv && r.inv.toLowerCase().includes(q)) ||
      (r.company && r.company.toLowerCase().includes(q)) ||
      (r.new_id && r.new_id.toLowerCase().includes(q)) ||
      (r.ref_no && String(r.ref_no).toLowerCase().includes(q)) ||
      (r.mbl_no && String(r.mbl_no).toLowerCase().includes(q)) ||
      (r.equipment && String(r.equipment).toLowerCase().includes(q))
    );
  }
  rows = [...rows].sort((a, b) => {
    const av = a[registerSort.key], bv = b[registerSort.key];
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    const cmp = (typeof av === 'number' && typeof bv === 'number') ? (av - bv) : String(av).localeCompare(String(bv));
    return registerSort.dir === 'asc' ? cmp : -cmp;
  });

  // Cap render to first 500 for perf; show note if truncated.
  const truncated = rows.length > 500;
  const renderRows = truncated ? rows.slice(0, 500) : rows;
  bodyEl.innerHTML = renderRows.map(r => `<tr>${REGISTER_COLUMNS.map(c => c.cell(r)).join('')}</tr>`).join('');
  meta.textContent = truncated
    ? `Showing first 500 of ${rows.length} matched rows`
    : `${rows.length} rows`;
}

function formatMoney(v) {
  if (v == null || v === '') return '';
  return Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatDate(v) {
  if (!v) return '';
  if (v instanceof Date) {
    return `${(v.getMonth()+1).toString().padStart(2,'0')}/${v.getDate().toString().padStart(2,'0')}/${v.getFullYear()}`;
  }
  return escapeHtml(v);
}

function formatStatus(s) {
  if (!s) return '';
  const letter = s[0] || '';
  return `<span class="status-${letter}">${escapeHtml(s)}</span>`;
}

window.arFormatMoney = formatMoney;
window.arFormatDate = formatDate;
window.arFormatStatus = formatStatus;
```

- [ ] **Step 5.3: Verify AR Register tab works**

Load the 5/19 workbook. Click "AR Register" tab.

Expected:
- Table renders with header row + first 500 rows
- Total count shown in section header ("4149 invoices · 183 customers")
- Search box filters in real time (try typing "AMNEX" — should narrow to ~20-50 rows)
- Click any column header — sorts ascending; click again — sorts descending; arrow indicator updates
- Status column shows colored badges (A green, B yellow, C orange, D red, E dark brown)
- Negative balances show in red

- [ ] **Step 5.4: Commit**

```bash
git add app/assets/js/tools/ar-dashboard/ar-dashboard-views.js \
        app/assets/css/styles.css
git commit -m "feat(ar-dashboard): AR Register tab with sort + search

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase E — Collections tab (the "what left" audit)

### Task 6: Collections tab — grouped by check#

**Files:**
- Modify: `app/assets/js/tools/ar-dashboard/ar-dashboard-views.js`
- Modify: `app/assets/css/styles.css`

- [ ] **Step 6.1: Add Collections styling**

Append to `app/assets/css/styles.css`:

```css
.ar-two-pane { display:grid; grid-template-columns: 1fr 340px; gap:12px; }
.ar-two-pane .pane-left { min-width:0; }
.ar-two-pane .pane-right { background:#fff; border:1px solid #e2e8f0; border-radius:8px;
  padding:14px 16px; max-height:calc(100vh - 240px); overflow-y:auto; }

.ar-table tr.group-row { background:#fff7ed; cursor:pointer; }
.ar-table tr.group-row td { background:#fff7ed; font-weight:700; color:#9a3412;
  font-size:0.78rem; padding:8px 10px; border-top:1px solid #fed7aa;
  border-bottom:1px solid #fed7aa; }
.ar-table tr.group-row:hover td { background:#ffedd5; }
.ar-table tr.group-row .chevron { display:inline-block; width:12px; color:#c2410c;
  font-size:0.7rem; transition:transform 0.15s; margin-right:6px; }
.ar-table tr.group-row.expanded .chevron { transform:rotate(90deg); }

.ar-status-chip { display:inline-flex; align-items:center; gap:4px; padding:1px 7px;
  border-radius:999px; font-size:0.68rem; font-weight:700; }
.ar-status-chip.ok { background:#dcfce7; color:#166534; }
.ar-status-chip.warn { background:#fef3c7; color:#92400e; }
.ar-status-chip.danger { background:#fee2e2; color:#b91c1c; }

.ar-detail-empty { color:#94a3b8; font-size:0.82rem; text-align:center; padding:24px 12px; }
.ar-detail-empty .ico { font-size:1.6rem; margin-bottom:8px; }

.ar-pane-section { margin-bottom:14px; }
.ar-pane-section h4 { font-size:0.76rem; color:#64748b; text-transform:uppercase;
  letter-spacing:0.04em; font-weight:700; margin-bottom:6px; }
.ar-pane-section .row { font-size:0.82rem; color:#0f172a; padding:3px 0; }
.ar-pane-section .row .key { color:#64748b; }
```

- [ ] **Step 6.2: Add Collections renderer**

Append to `app/assets/js/tools/ar-dashboard/ar-dashboard-views.js`:

```js
// ── Collections tab ──

window.arRenderCollections = function arRenderCollections(body) {
  const checks = groupCollectionsByCheck(arState.model.collections);
  const totalCollected = checks.reduce((s, c) => s + (c.payment_amount || 0), 0);
  const totalInvoices = checks.reduce((s, c) => s + c.invoices.length, 0);

  body.innerHTML = `
    <h3 class="ar-section-h">Collections — yesterday's payments
      <span class="sub">${checks.length} checks · ${totalInvoices} invoices · ${formatMoney(totalCollected)} collected</span>
    </h3>
    <div class="ar-two-pane">
      <div class="pane-left">
        <div class="ar-toolbar">
          <input type="text" class="search" id="arColSearch" placeholder="Search check#, customer, invoice..." />
          <button class="ngl-btn ngl-btn-secondary" id="arColExpandAll" style="font-size:0.72rem; padding:4px 9px;">Expand all</button>
          <span class="meta" id="arColMeta"></span>
        </div>
        <div class="ar-table-wrap">
          <table class="ar-table">
            <thead><tr>
              <th>Check#</th><th>Customer</th>
              <th class="num">Invoices</th><th class="num">Amount</th><th>Status</th>
            </tr></thead>
            <tbody id="arColBody"></tbody>
          </table>
        </div>
      </div>
      <div class="pane-right" id="arColDetail">
        ${detailEmptyState('Select a check to see its applied invoices', '📋')}
      </div>
    </div>
  `;

  const expandedChecks = new Set();
  const searchState = { q: '' };
  body.querySelector('#arColSearch').addEventListener('input', e => {
    searchState.q = e.target.value.toLowerCase();
    paint();
  });
  body.querySelector('#arColExpandAll').addEventListener('click', () => {
    if (expandedChecks.size === checks.length) {
      expandedChecks.clear();
    } else {
      checks.forEach(c => expandedChecks.add(c.check_no));
    }
    paint();
  });

  function paint() {
    const filtered = filterChecks(checks, searchState.q);
    const tbody = body.querySelector('#arColBody');
    tbody.innerHTML = filtered.flatMap(c => renderCheckGroup(c, expandedChecks.has(c.check_no))).join('');
    body.querySelector('#arColMeta').textContent = `${filtered.length} of ${checks.length} checks`;

    tbody.querySelectorAll('tr.group-row').forEach(tr => {
      tr.addEventListener('click', () => {
        const ck = tr.dataset.check;
        const check = checks.find(c => c.check_no === ck);
        if (expandedChecks.has(ck)) {
          expandedChecks.delete(ck);
        } else {
          expandedChecks.add(ck);
        }
        renderDetail(check);
        paint();
      });
    });
  }

  function renderDetail(check) {
    const detailEl = body.querySelector('#arColDetail');
    detailEl.innerHTML = `
      <div class="ar-pane-section">
        <h4>Check ${escapeHtml(check.check_no)}</h4>
        <div class="row"><span class="key">Customer:</span> ${escapeHtml(check.customer_name)}</div>
        <div class="row"><span class="key">Customer ID:</span> ${escapeHtml(check.customer_id)}</div>
        <div class="row"><span class="key">Total:</span> $${formatMoney(check.payment_amount)}</div>
        <div class="row"><span class="key">Account:</span> ${escapeHtml(check.account)}</div>
        <div class="row"><span class="key">Posted:</span> ${formatDate(check.payment_date)}</div>
      </div>
      <div class="ar-pane-section">
        <h4>Applied invoices (${check.invoices.length})</h4>
        ${check.invoices.map(inv => `
          <div class="row">
            <code class="id" style="font-family:Consolas,monospace; font-size:0.76rem;">${escapeHtml(inv.invoice_or_ref)}</code>
            <span class="num" style="float:right;">$${formatMoney(inv.amount)}</span>
          </div>
        `).join('')}
      </div>
    `;
  }

  paint();
};

function groupCollectionsByCheck(rows) {
  const byCheck = new Map();
  for (const r of rows) {
    if (!r.check_no) continue;
    if (!byCheck.has(r.check_no)) {
      byCheck.set(r.check_no, {
        check_no: r.check_no,
        customer_id: r.customer_id,
        customer_name: r.customer_name,
        account: r.account,
        payment_date: r.payment_date,
        payment_amount: 0,
        invoices: [],
      });
    }
    const group = byCheck.get(r.check_no);
    if (r.txn_type === 'Payment') {
      group.payment_amount = r.amount ?? 0;
    } else if (r.txn_type === 'Invoice') {
      group.invoices.push(r);
    }
  }
  return [...byCheck.values()];
}

function filterChecks(checks, q) {
  if (!q) return checks;
  return checks.filter(c =>
    (c.check_no && c.check_no.toLowerCase().includes(q)) ||
    (c.customer_name && c.customer_name.toLowerCase().includes(q)) ||
    c.invoices.some(i => i.invoice_or_ref && i.invoice_or_ref.toLowerCase().includes(q))
  );
}

function renderCheckGroup(c, expanded) {
  const reconciled = checkReconciledStatus(c);
  const chevron = expanded ? '▼' : '▶';
  const groupClass = expanded ? 'group-row expanded' : 'group-row';
  const lines = [
    `<tr class="${groupClass}" data-check="${escapeHtml(c.check_no)}">
       <td><span class="chevron">${chevron}</span>${escapeHtml(c.check_no)}</td>
       <td class="customer">${escapeHtml(c.customer_name)}</td>
       <td class="num">${c.invoices.length}</td>
       <td class="num">${formatMoney(c.payment_amount)}</td>
       <td>${renderReconciledChip(reconciled)}</td>
     </tr>`
  ];
  if (expanded) {
    for (const inv of c.invoices) {
      lines.push(`<tr>
        <td></td>
        <td colspan="1" class="id">${escapeHtml(inv.invoice_or_ref)}</td>
        <td></td>
        <td class="num">${formatMoney(inv.amount)}</td>
        <td>${inv.open_balance != null && Math.abs(inv.open_balance) >= 0.01
              ? `<span class="ar-status-chip warn">Bal $${formatMoney(inv.open_balance)}</span>`
              : `<span class="ar-status-chip ok">Cleared</span>`}</td>
      </tr>`);
    }
  }
  return lines;
}

function checkReconciledStatus(check) {
  // Sum of applied invoice amounts vs total payment amount.
  const applied = check.invoices.reduce((s, i) => s + (i.amount || 0), 0);
  const total = check.payment_amount || 0;
  if (Math.abs(applied - total) < 0.01) return 'reconciled';
  if (applied < total) return 'partial';
  return 'unposted';
}

function renderReconciledChip(status) {
  if (status === 'reconciled') return '<span class="ar-status-chip ok">✓ Reconciled</span>';
  if (status === 'partial')    return '<span class="ar-status-chip warn">⚠ Partial</span>';
  return '<span class="ar-status-chip danger">✗ Unposted</span>';
}

function detailEmptyState(text, icon) {
  return `<div class="ar-detail-empty"><div class="ico">${icon}</div>${escapeHtml(text)}</div>`;
}

window.arDetailEmptyState = detailEmptyState;
```

- [ ] **Step 6.3: Verify Collections tab works**

Load the 5/19 workbook → click Collections tab.

Expected:
- Section header: `Collections — yesterday's payments · N checks · M invoices · $... collected`
- Table grouped by check (orange-tinted rows). Each check shows: check#, customer, # invoices applied, total amount, ✓ Reconciled chip
- Click a check row → expands to show each applied invoice with amount + Cleared/Bal chip
- "Expand all" toggles all groups
- Right pane shows check details + applied invoice list
- Search filters by check#, customer, or invoice #

- [ ] **Step 6.4: Commit**

```bash
git add app/assets/js/tools/ar-dashboard/ar-dashboard-views.js \
        app/assets/css/styles.css
git commit -m "feat(ar-dashboard): Collections tab — grouped by check with reconciliation chips

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase F — Overdue / Partial Pays / New Invoices / TMS / Adjustments / Customers tabs

These tabs share a common pattern: filter the existing model into a list, render as a table inside a two-pane split (table left, detail panel right). Each tab adds its own filter logic + detail panel content.

### Task 7: Overdue tab — call list sorted by oldest

- [ ] **Step 7.1: Add the Overdue renderer to views.js**

Append to `app/assets/js/tools/ar-dashboard/ar-dashboard-views.js`:

```js
// ── Overdue tab ──

window.arRenderOverdue = function arRenderOverdue(body) {
  const rows = arState.model.ar_register
    .filter(r => (r.aging ?? 0) >= 30 && (r.balance ?? 0) > 0)
    .sort((a, b) => (b.aging ?? 0) - (a.aging ?? 0));
  const total = rows.reduce((s, r) => s + (r.balance ?? 0), 0);

  body.innerHTML = `
    <h3 class="ar-section-h">Overdue — today's call list
      <span class="sub">${rows.length} invoices · $${formatMoney(total)} outstanding</span>
    </h3>
    <div class="ar-two-pane">
      <div class="pane-left">
        <div class="ar-toolbar">
          <input type="text" class="search" id="arOdSearch" placeholder="Search customer, invoice, ref..." />
          <span class="meta">${rows.length} overdue</span>
        </div>
        <div class="ar-table-wrap">
          <table class="ar-table">
            <thead><tr>
              <th>Customer</th><th>Invoice</th><th class="num">Aging</th>
              <th class="num">Balance</th><th>Status</th>
            </tr></thead>
            <tbody id="arOdBody"></tbody>
          </table>
        </div>
      </div>
      <div class="pane-right" id="arOdDetail">
        ${detailEmptyState('Select a row to see customer + invoice details + actions', '📞')}
      </div>
    </div>
  `;

  const search = { q: '' };
  body.querySelector('#arOdSearch').addEventListener('input', e => {
    search.q = e.target.value.toLowerCase();
    paint();
  });

  function paint() {
    const filtered = search.q
      ? rows.filter(r =>
          (r.company && r.company.toLowerCase().includes(search.q)) ||
          (r.inv && r.inv.toLowerCase().includes(search.q)) ||
          (r.ref_no && String(r.ref_no).toLowerCase().includes(search.q)))
      : rows;
    const tbody = body.querySelector('#arOdBody');
    tbody.innerHTML = filtered.slice(0, 500).map(r => `
      <tr data-inv="${escapeHtml(r.inv)}">
        <td class="customer">${escapeHtml(r.company)}</td>
        <td class="id">${escapeHtml(r.inv)}</td>
        <td class="num">${r.aging ?? ''} d</td>
        <td class="num">${formatMoney(r.balance)}</td>
        <td>${formatStatus(r.ar_status)}</td>
      </tr>
    `).join('');
    tbody.querySelectorAll('tr').forEach(tr => {
      tr.addEventListener('click', () => {
        const r = rows.find(x => x.inv === tr.dataset.inv);
        renderDetail(r);
      });
    });
  }

  function renderDetail(r) {
    const detailEl = body.querySelector('#arOdDetail');
    detailEl.innerHTML = `
      <div class="ar-pane-section">
        <h4>${escapeHtml(r.company)}</h4>
        <div class="row"><span class="key">Customer ID:</span> ${escapeHtml(r.new_id)}</div>
        <div class="row"><span class="key">Invoice:</span> <code>${escapeHtml(r.inv)}</code></div>
        <div class="row"><span class="key">WO:</span> ${escapeHtml(r.wo)}</div>
        <div class="row"><span class="key">Issued:</span> ${formatDate(r.date)}</div>
        <div class="row"><span class="key">Aging:</span> ${r.aging} days</div>
        <div class="row"><span class="key">Amount:</span> $${formatMoney(r.amount)}</div>
        <div class="row"><span class="key">Paid:</span> $${formatMoney(r.paid)}</div>
        <div class="row"><span class="key">Balance:</span> <strong>$${formatMoney(r.balance)}</strong></div>
        ${r.memo ? `<div class="row"><span class="key">Memo:</span> ${escapeHtml(r.memo)}</div>` : ''}
      </div>
      <div class="ar-pane-section">
        <h4>Actions</h4>
        <button class="ngl-btn ngl-btn-primary" style="margin-bottom:6px; width:100%;" onclick="window.arEmailCustomer && arEmailCustomer('${escapeHtml(r.new_id)}', '${escapeHtml(r.inv)}')">✉ Email customer</button>
        <button class="ngl-btn ngl-btn-secondary" style="margin-bottom:6px; width:100%;" onclick="window.arOpenInQbo && arOpenInQbo('${escapeHtml(r.inv)}')">🔗 Open in QBO</button>
      </div>
    `;
  }

  paint();
};
```

- [ ] **Step 7.2: Verify Overdue tab works**

Load the 5/19 workbook → click Overdue tab.

Expected:
- Counts in section header
- Table sorted oldest-first
- Search works
- Click a row → detail panel shows customer info + Email + QBO buttons
- Buttons don't do anything yet (actions implemented in Phase J)

- [ ] **Step 7.3: Commit**

```bash
git add app/assets/js/tools/ar-dashboard/ar-dashboard-views.js
git commit -m "feat(ar-dashboard): Overdue tab — call list sorted by oldest

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 8: Partial Pays / New Invoices tabs

- [ ] **Step 8.1: Add both renderers**

Append to `app/assets/js/tools/ar-dashboard/ar-dashboard-views.js`:

```js
// ── Partial Pays tab ──

window.arRenderPartial = function arRenderPartial(body) {
  const rows = arState.model.ar_register
    .filter(r => (r.paid ?? 0) > 0 && (r.balance ?? 0) > 0)
    .sort((a, b) => (b.balance ?? 0) - (a.balance ?? 0));
  const total = rows.reduce((s, r) => s + (r.balance ?? 0), 0);

  body.innerHTML = `
    <h3 class="ar-section-h">Partial Pays — invoices with partial payment received
      <span class="sub">${rows.length} invoices · $${formatMoney(total)} still open</span>
    </h3>
    <div class="ar-table-wrap">
      <table class="ar-table">
        <thead><tr>
          <th>Customer</th><th>Invoice</th><th class="num">Amount</th>
          <th class="num">Paid</th><th class="num">Balance</th><th>Memo</th>
        </tr></thead>
        <tbody>
          ${rows.map(r => `
            <tr>
              <td class="customer">${escapeHtml(r.company)}</td>
              <td class="id">${escapeHtml(r.inv)}</td>
              <td class="num">${formatMoney(r.amount)}</td>
              <td class="num">${formatMoney(r.paid)}</td>
              <td class="num"><strong>${formatMoney(r.balance)}</strong></td>
              <td>${escapeHtml(r.memo)}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `;
};

// ── New Invoices tab ──

window.arRenderNew = function arRenderNew(body) {
  const rows = arState.model.schedule;
  const total = rows.reduce((s, r) => s + (r.amount ?? 0), 0);

  body.innerHTML = `
    <h3 class="ar-section-h">New Invoices — added to AR
      <span class="sub">${rows.length} new · $${formatMoney(total)} invoiced</span>
    </h3>
    <div class="ar-table-wrap">
      <table class="ar-table">
        <thead><tr>
          <th>Date</th><th>Customer</th><th>Invoice</th>
          <th>Ref / WO</th><th>Container</th><th>B/L</th><th class="num">Amount</th>
        </tr></thead>
        <tbody>
          ${rows.map(r => `
            <tr>
              <td>${formatDate(r.date)}</td>
              <td class="customer">${escapeHtml(r.customer_name)}</td>
              <td class="id">${escapeHtml(r.inv)}</td>
              <td class="id">${escapeHtml(r.ref)}</td>
              <td class="id">${escapeHtml(r.cntr_chassis)}</td>
              <td class="id">${escapeHtml(r.bl)}</td>
              <td class="num">${formatMoney(r.amount)}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `;
};
```

- [ ] **Step 8.2: Verify both tabs**

Expected: each tab renders its own filtered table with header counts.

- [ ] **Step 8.3: Commit**

```bash
git add app/assets/js/tools/ar-dashboard/ar-dashboard-views.js
git commit -m "feat(ar-dashboard): Partial Pays + New Invoices tabs

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 9: TMS + Adjustments tabs

- [ ] **Step 9.1: Add both renderers**

Append to `app/assets/js/tools/ar-dashboard/ar-dashboard-views.js`:

```js
// ── TMS tab ──

window.arRenderTms = function arRenderTms(body) {
  const rows = arState.model.tms_rows;
  body.innerHTML = `
    <h3 class="ar-section-h">TMS — matched invoices
      <span class="sub">${rows.length} rows · TMS source = billing-side truth</span>
    </h3>
    <div class="ar-table-wrap">
      <table class="ar-table">
        <thead><tr>
          <th>Div</th><th>Customer</th><th>WO #</th><th>Equipment</th>
          <th>NGL Inv #</th><th class="num">Inv Amount</th><th class="num">Paid/Received</th>
        </tr></thead>
        <tbody>
          ${rows.map(r => `
            <tr>
              <td>${escapeHtml(r.wo_div)}</td>
              <td class="customer">${escapeHtml(r.name)}</td>
              <td class="id">${escapeHtml(r.wo_no)}</td>
              <td class="id">${escapeHtml(r.equipment)}</td>
              <td class="id">${escapeHtml(r.inv_no)}</td>
              <td class="num">${formatMoney(r.inv_amt)}</td>
              <td class="num">${formatMoney(r.paid_received)}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `;
};

// ── Adjustments tab ──

window.arRenderAdjustments = function arRenderAdjustments(body) {
  const rows = arState.model.adjustments;
  const total = rows.reduce((s, r) => s + (r.amount_difference ?? 0), 0);
  body.innerHTML = `
    <h3 class="ar-section-h">Adjustments — TMS revised these invoice amounts
      <span class="sub">${rows.length} adjustments · net change ${total < 0 ? '−' : ''}$${formatMoney(Math.abs(total))}</span>
    </h3>
    <div class="ar-table-wrap">
      <table class="ar-table">
        <thead><tr>
          <th>Div</th><th>Customer</th><th>WO #</th><th>NGL Inv #</th>
          <th class="num">Amount Δ</th><th class="num">Revised Amount</th><th class="num">Paid</th>
        </tr></thead>
        <tbody>
          ${rows.map(r => `
            <tr>
              <td>${escapeHtml(r.div)}</td>
              <td class="customer">${escapeHtml(r.name)}</td>
              <td class="id">${escapeHtml(r.wo_no)}</td>
              <td class="id">${escapeHtml(r.inv_no)}</td>
              <td class="num ${(r.amount_difference ?? 0) < 0 ? 'neg' : ''}">${formatMoney(r.amount_difference)}</td>
              <td class="num">${formatMoney(r.revised_invoice_amount)}</td>
              <td class="num">${formatMoney(r.paid_received)}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `;
};
```

- [ ] **Step 9.2: Verify + commit**

```bash
git add app/assets/js/tools/ar-dashboard/ar-dashboard-views.js
git commit -m "feat(ar-dashboard): TMS + Adjustments tabs

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 10: Customers tab — per-customer rollup

- [ ] **Step 10.1: Add the Customers renderer**

Append to `app/assets/js/tools/ar-dashboard/ar-dashboard-views.js`:

```js
// ── Customers tab ──

window.arRenderCustomers = function arRenderCustomers(body) {
  const rollup = buildCustomerRollup(arState.model.ar_register);

  body.innerHTML = `
    <h3 class="ar-section-h">Customers — rollup
      <span class="sub">${rollup.length} customers · $${formatMoney(rollup.reduce((s,r) => s + r.balance, 0))} total balance</span>
    </h3>
    <div class="ar-toolbar">
      <input type="text" class="search" id="arCustSearch" placeholder="Search customer..." />
      <span class="meta">Sorted by balance desc</span>
    </div>
    <div class="ar-table-wrap">
      <table class="ar-table">
        <thead><tr>
          <th>ID</th><th>Customer</th><th class="num">Invoices</th>
          <th class="num">Balance</th><th class="num">Oldest Aging</th>
          <th>Bucket spread</th>
        </tr></thead>
        <tbody id="arCustBody"></tbody>
      </table>
    </div>
  `;

  const search = { q: '' };
  body.querySelector('#arCustSearch').addEventListener('input', e => {
    search.q = e.target.value.toLowerCase();
    paint();
  });

  function paint() {
    const filtered = search.q
      ? rollup.filter(c => c.name.toLowerCase().includes(search.q) || (c.id && c.id.toLowerCase().includes(search.q)))
      : rollup;
    const tbody = body.querySelector('#arCustBody');
    tbody.innerHTML = filtered.map(c => `
      <tr>
        <td class="id">${escapeHtml(c.id)}</td>
        <td class="customer">${escapeHtml(c.name)}</td>
        <td class="num">${c.invoice_count}</td>
        <td class="num"><strong>${formatMoney(c.balance)}</strong></td>
        <td class="num">${c.oldest_aging} d</td>
        <td>${renderBucketChips(c.buckets)}</td>
      </tr>
    `).join('');
  }

  paint();
};

function buildCustomerRollup(register) {
  const byCust = new Map();
  for (const r of register) {
    const id = r.new_id || '(no id)';
    if (!byCust.has(id)) {
      byCust.set(id, {
        id: r.new_id,
        name: r.company,
        invoice_count: 0,
        balance: 0,
        oldest_aging: 0,
        buckets: { A: 0, B: 0, C: 0, D: 0, E: 0 },
      });
    }
    const c = byCust.get(id);
    c.invoice_count++;
    c.balance += r.balance ?? 0;
    c.oldest_aging = Math.max(c.oldest_aging, r.aging ?? 0);
    const letter = r.ar_status ? r.ar_status[0] : null;
    if (letter && c.buckets[letter] != null) c.buckets[letter]++;
  }
  return [...byCust.values()].sort((a, b) => b.balance - a.balance);
}

function renderBucketChips(b) {
  const colors = { A: '#16a34a', B: '#facc15', C: '#f97316', D: '#dc2626', E: '#7c2d12' };
  return Object.entries(b)
    .filter(([_, n]) => n > 0)
    .map(([letter, n]) => `<span style="display:inline-block; background:${colors[letter]}; color:#fff; padding:1px 6px; border-radius:3px; font-size:0.66rem; font-weight:700; margin-right:3px;">${letter} ${n}</span>`)
    .join('');
}
```

- [ ] **Step 10.2: Verify + commit**

```bash
git add app/assets/js/tools/ar-dashboard/ar-dashboard-views.js
git commit -m "feat(ar-dashboard): Customers tab — per-customer rollup with bucket spread

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase G — Exception detection

### Task 11: Exception detection module

**Files:**
- Create: `app/assets/js/tools/ar-dashboard/ar-dashboard-exceptions.js`

- [ ] **Step 11.1: Create the exceptions module**

Create `app/assets/js/tools/ar-dashboard/ar-dashboard-exceptions.js`:

```js
// AR Dashboard — exception detection
// Inspects the loaded model + returns a flat list of exceptions across 8 categories.
//
// Each exception has:
//   id           stable string identifier
//   category     'suspense' | 'short' | 'over' | 'posting_gap' | 'amount_disagreement'
//                | 'customer_mismatch' | 'missing_tms' | 'non_factored'
//   severity     'urgent' | 'warning' | 'info'
//   invoice      invoice # (if applicable)
//   customer     customer name + id
//   details      object — category-specific data shown in worklist row
//   suggested_action  short string

window.arDetectExceptions = function arDetectExceptions(model) {
  const out = [];
  out.push(...detectShortPays(model));
  out.push(...detectOverPays(model));
  out.push(...detectAmountDisagreements(model));
  out.push(...detectMissingTms(model));
  // Suspense + customer-mismatch require the raw TAB BANK file (loaded separately
  // via the optional Source-Files drop). When that drop exists, these run too.
  if (model.tab_bank) {
    out.push(...detectSuspense(model));
    out.push(...detectCustomerMismatches(model));
    out.push(...detectNonFactored(model));
    out.push(...detectPostingGaps(model));
  }
  return out;
};

function detectShortPays(model) {
  return model.ar_register
    .filter(r => (r.paid ?? 0) > 0 && (r.balance ?? 0) > 0)
    .map(r => ({
      id: `short:${r.inv}`,
      category: 'short',
      severity: 'warning',
      invoice: r.inv,
      customer: { id: r.new_id, name: r.company },
      details: {
        amount: r.amount,
        paid: r.paid,
        balance: r.balance,
        memo: r.memo,
      },
      suggested_action: 'Call customer or write off',
    }));
}

function detectOverPays(model) {
  return model.ar_register
    .filter(r => (r.balance ?? 0) < -0.01)
    .map(r => ({
      id: `over:${r.inv}`,
      category: 'over',
      severity: 'warning',
      invoice: r.inv,
      customer: { id: r.new_id, name: r.company },
      details: { amount: r.amount, paid: r.paid, balance: r.balance },
      suggested_action: 'Apply to another invoice or create credit memo',
    }));
}

function detectAmountDisagreements(model) {
  // ADJUSTMENT sheet IS the authoritative list of amount disagreements
  // (these were already flagged + applied by the build engine).
  return model.adjustments.map(adj => ({
    id: `amount:${adj.inv_no}`,
    category: 'amount_disagreement',
    severity: 'info',
    invoice: adj.inv_no,
    customer: { id: adj.id, name: adj.name },
    details: {
      delta: adj.amount_difference,
      revised: adj.revised_invoice_amount,
      paid_received: adj.paid_received,
      wo_no: adj.wo_no,
    },
    suggested_action: 'Confirm TMS revision applied correctly',
  }));
}

function detectMissingTms(model) {
  // Invoices in QBO Schedule but not in TMS Reconcile.
  const tmsInvs = new Set(model.tms_rows.map(t => t.inv_no).filter(Boolean));
  return model.schedule
    .filter(s => s.inv && !tmsInvs.has(s.inv))
    .map(s => ({
      id: `notms:${s.inv}`,
      category: 'missing_tms',
      severity: 'info',
      invoice: s.inv,
      customer: { id: s.customer_id, name: s.customer_name },
      details: { amount: s.amount, cntr_chassis: s.cntr_chassis, ref: s.ref },
      suggested_action: 'Confirm warehouse / manual entry',
    }));
}

function detectSuspense(model) {
  if (!model.tab_bank) return [];
  return model.tab_bank
    .filter(r => r.pmt_type === 'Unapplied Cash' && r.desc !== 'NON-FACTORED')
    .map(r => ({
      id: `suspense:${r.check}:${r.invoice}`,
      category: 'suspense',
      severity: 'urgent',
      invoice: r.invoice,
      customer: { id: r.debtor_code, name: r.debtor_name },
      details: {
        check: r.check,
        amount: r.collected_amount,
      },
      suggested_action: 'Match to customer + apply',
    }));
}

function detectCustomerMismatches(model) {
  if (!model.tab_bank) return [];
  // For each TAB BANK Payment row, compare debtor name against the customer field
  // of QBO Daily Collection rows for the same check#.
  const byCheck = new Map();
  for (const c of model.collections) {
    if (c.check_no) {
      const list = byCheck.get(c.check_no) || [];
      list.push(c);
      byCheck.set(c.check_no, list);
    }
  }
  const out = [];
  for (const t of model.tab_bank) {
    if (t.pmt_type !== 'Payment') continue;
    const qboHits = byCheck.get(t.check) || [];
    if (qboHits.length === 0) continue;
    const qboCust = qboHits[0].customer_name || '';
    if (!fuzzyNameMatch(t.debtor_name, qboCust)) {
      out.push({
        id: `cmismatch:${t.check}`,
        category: 'customer_mismatch',
        severity: 'warning',
        invoice: null,
        customer: { id: t.debtor_code, name: t.debtor_name },
        details: {
          check: t.check,
          tab_bank_name: t.debtor_name,
          qbo_name: qboCust,
        },
        suggested_action: 'Confirm correct customer',
      });
    }
  }
  return out;
}

function detectNonFactored(model) {
  if (!model.tab_bank) return [];
  return model.tab_bank
    .filter(r => r.desc === 'NON-FACTORED' && r.pmt_type === 'Unapplied Cash')
    .map(r => ({
      id: `nonfact:${r.check}`,
      category: 'non_factored',
      severity: 'info',
      invoice: null,
      customer: { id: null, name: '(suspense)' },
      details: { check: r.check, amount: r.collected_amount },
      suggested_action: 'No action — informational',
    }));
}

function detectPostingGaps(model) {
  if (!model.tab_bank) return [];
  // TAB BANK Payment rows whose check# doesn't appear in QBO Daily Collection.
  const qboChecks = new Set(model.collections.map(c => c.check_no).filter(Boolean));
  const seen = new Set();
  const out = [];
  for (const t of model.tab_bank) {
    if (t.pmt_type !== 'Payment') continue;
    if (qboChecks.has(t.check)) continue;
    if (seen.has(t.check)) continue;
    seen.add(t.check);
    out.push({
      id: `gap:${t.check}`,
      category: 'posting_gap',
      severity: 'urgent',
      invoice: null,
      customer: { id: t.debtor_code, name: t.debtor_name },
      details: { check: t.check, amount: t.amount },
      suggested_action: 'Post in QBO',
    });
  }
  return out;
}

function fuzzyNameMatch(a, b) {
  if (!a || !b) return false;
  const norm = s => String(s).toLowerCase().replace(/[^a-z0-9]/g, '');
  const na = norm(a), nb = norm(b);
  if (na === nb) return true;
  // Treat names matching the longer prefix (90%) as equivalent.
  const len = Math.min(na.length, nb.length);
  const longer = Math.max(na.length, nb.length);
  if (len / longer < 0.5) return false;
  return na.startsWith(nb) || nb.startsWith(na);
}

window.arFuzzyNameMatch = fuzzyNameMatch;
```

- [ ] **Step 11.2: Add the import in ar-dashboard.js**

```js
import './ar-dashboard-exceptions.js';
```

- [ ] **Step 11.3: Verify in console — exception detection runs and populates arState**

Load the 5/19 workbook. In console:

```js
arState.exceptions.length
// Should be > 0 — at minimum the 8 ADJUSTMENT-derived "amount_disagreement" + short pays
arState.exceptions.filter(e => e.category === 'short').length
// Should match Partial Pays tab count (5/19: small number, maybe 0-6)
arState.exceptions.filter(e => e.category === 'amount_disagreement').length
// Should match Adjustments tab count (5/19: 8)
arState.exceptions.filter(e => e.category === 'missing_tms').length
// Schedule rows whose inv# isn't in TMS — likely warehouse + special invoices
```

- [ ] **Step 11.4: Commit**

```bash
git add app/assets/js/tools/ar-dashboard/ar-dashboard-exceptions.js \
        app/assets/js/tools/ar-dashboard/ar-dashboard.js
git commit -m "feat(ar-dashboard): exception detection — 8 categories from loaded model

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase H — Summary tab + Exceptions worklist (the centerpiece)

### Task 12: Summary tab with KPIs + aging bar + exceptions worklist

**Files:**
- Modify: `app/assets/js/tools/ar-dashboard/ar-dashboard-views.js`
- Modify: `app/assets/css/styles.css`

- [ ] **Step 12.1: Add Summary styling**

Append to `app/assets/css/styles.css`:

```css
.ar-suspense-banner { display:flex; align-items:center; gap:14px;
  background:linear-gradient(90deg, #fff7ed 0%, #fffbeb 100%);
  border:1px solid #fed7aa; border-left:4px solid #ea580c;
  border-radius:8px; padding:10px 14px; margin-bottom:12px; }
.ar-suspense-banner .sb-ico { width:28px; height:28px; border-radius:50%;
  background:#ea580c; color:#fff; display:flex; align-items:center;
  justify-content:center; font-size:0.95rem; font-weight:800; }
.ar-suspense-banner .sb-body { flex:1; min-width:0; }
.ar-suspense-banner .sb-title { font-size:0.86rem; font-weight:800; color:#9a3412; }
.ar-suspense-banner .sb-sub { font-size:0.74rem; color:#9a3412;
  opacity:0.85; margin-top:1px; }
.ar-suspense-banner .sb-cta { background:#ea580c; color:#fff; padding:7px 13px;
  border-radius:6px; font-size:0.78rem; font-weight:700; border:none; cursor:pointer; }

.ar-kpi-grid { display:grid; grid-template-columns:repeat(4, 1fr); gap:10px; margin-bottom:12px; }
.ar-kpi { background:#fff; border:1px solid #e2e8f0; border-radius:8px; padding:11px 13px;
  cursor:pointer; transition:border-color 0.12s; }
.ar-kpi:hover { border-color:#ea580c; }
.ar-kpi.alert { border-color:#fed7aa; background:linear-gradient(180deg, #fff7ed 0%, #fff 60%); }
.ar-kpi .label { font-size:0.63rem; color:#94a3b8; text-transform:uppercase;
  letter-spacing:0.04em; font-weight:700; margin-bottom:3px; }
.ar-kpi .val { font-size:1.25rem; font-weight:800; color:#0f172a; font-variant-numeric:tabular-nums; }
.ar-kpi.alert .val { color:#9a3412; }
.ar-kpi .sub { font-size:0.7rem; color:#64748b; margin-top:2px; }
.ar-kpi .link { font-size:0.68rem; color:#ea580c; font-weight:700; margin-top:6px; }

.ar-aging-block { background:#fff; border:1px solid #e2e8f0; border-radius:8px; padding:12px 14px; margin-bottom:12px; }
.ar-aging-bar { display:flex; height:22px; border-radius:5px; overflow:hidden;
  border:1px solid #e2e8f0; margin-bottom:8px; }
.ar-aging-bar .seg { display:flex; align-items:center; justify-content:center;
  font-size:0.68rem; font-weight:700; color:#fff; text-shadow:0 1px 0 rgba(0,0,0,0.15); }
.ar-aging-bar .s-green { background:#16a34a; }
.ar-aging-bar .s-yellow { background:#facc15; color:#78350f; text-shadow:none; }
.ar-aging-bar .s-orange { background:#f97316; }
.ar-aging-bar .s-red { background:#dc2626; }
.ar-aging-bar .s-brown { background:#7c2d12; }
.ar-aging-legend { display:flex; gap:14px; flex-wrap:wrap; font-size:0.72rem; color:#475569; }
.ar-aging-legend .lg { display:flex; align-items:center; gap:5px; }
.ar-aging-legend .lg .dot { width:9px; height:9px; border-radius:50%; }
.ar-aging-legend .lg .amt { font-variant-numeric:tabular-nums; font-weight:700; color:#0f172a; margin-left:3px; }

.ar-worklist { background:#fff; border:1px solid #e2e8f0; border-radius:8px; margin-bottom:12px; }
.ar-worklist .wl-header { padding:10px 14px; border-bottom:1px solid #e2e8f0;
  font-size:0.85rem; font-weight:800; color:#0f172a;
  display:flex; align-items:center; gap:10px; }
.ar-worklist .wl-header .count { background:#fee2e2; color:#b91c1c;
  padding:2px 9px; border-radius:999px; font-size:0.7rem; font-weight:700; }
.ar-worklist .wl-sections { display:flex; flex-direction:column; }
.ar-worklist .wl-section { border-bottom:1px solid #f1f5f9; }
.ar-worklist .wl-section:last-child { border-bottom:none; }
.ar-worklist .wl-section-header { padding:8px 14px; cursor:pointer;
  display:flex; align-items:center; gap:8px;
  background:#fafafa; font-size:0.78rem; color:#0f172a; font-weight:700; }
.ar-worklist .wl-section-header:hover { background:#f1f5f9; }
.ar-worklist .wl-section-header .chevron { color:#64748b; }
.ar-worklist .wl-section-header .right { margin-left:auto; font-size:0.7rem; color:#94a3b8; font-weight:500; }
.ar-worklist .wl-section-body { padding:6px 14px 10px; display:none; }
.ar-worklist .wl-section.open .wl-section-body { display:block; }
.ar-worklist .wl-row { display:flex; gap:10px; padding:6px 0;
  border-bottom:1px solid #f8fafc; font-size:0.78rem; align-items:center; }
.ar-worklist .wl-row:last-child { border-bottom:none; }
.ar-worklist .wl-row .wl-amt { font-variant-numeric:tabular-nums; font-weight:700; color:#0f172a; }
.ar-worklist .wl-row .wl-action { margin-left:auto; }
```

- [ ] **Step 12.2: Add Summary renderer**

Append to `app/assets/js/tools/ar-dashboard/ar-dashboard-views.js`:

```js
// ── Summary tab ──

const EXCEPTION_SECTIONS = [
  { key: 'suspense',            label: 'Bank suspense (research needed)',     icon: '!', severity: 'urgent' },
  { key: 'short',               label: 'Short pays',                           icon: '−', severity: 'warning' },
  { key: 'over',                label: 'Over pays',                            icon: '+', severity: 'warning' },
  { key: 'posting_gap',         label: 'Posting gaps (TAB BANK ↔ QBO)',        icon: '⚠', severity: 'urgent' },
  { key: 'amount_disagreement', label: 'Amount disagreements (TMS adjustments)', icon: 'Δ', severity: 'info' },
  { key: 'customer_mismatch',   label: 'Customer name mismatches',             icon: '?', severity: 'warning' },
  { key: 'missing_tms',         label: 'Missing TMS records (warehouse?)',     icon: '⌘', severity: 'info' },
  { key: 'non_factored',        label: 'NON-FACTORED informational',           icon: 'i', severity: 'info' },
];

window.arRenderSummary = function arRenderSummary(body) {
  const m = arState.model;
  const exceptions = arState.exceptions || [];
  const urgentCount = exceptions.filter(e => e.severity === 'urgent').length;

  const totalAr = m.ar_register.reduce((s, r) => s + (r.balance ?? 0), 0);
  const totalCollected = m.collections
    .filter(c => c.txn_type === 'Payment')
    .reduce((s, c) => s + (c.amount ?? 0), 0);
  const totalNew = m.schedule.reduce((s, r) => s + (r.amount ?? 0), 0);
  const overdueCount = m.ar_register.filter(r => (r.aging ?? 0) >= 30 && (r.balance ?? 0) > 0).length;
  const overdueAmt = m.ar_register
    .filter(r => (r.aging ?? 0) >= 30 && (r.balance ?? 0) > 0)
    .reduce((s, r) => s + (r.balance ?? 0), 0);

  body.innerHTML = `
    ${urgentCount > 0 ? renderSuspenseBanner(urgentCount, exceptions) : ''}

    <h3 class="ar-section-h">Reconciliation worklist
      <span class="sub">Work these to zero by end of day</span>
    </h3>
    <div class="ar-worklist" id="arWorklist">
      <div class="wl-header">
        Exceptions found <span class="count">${exceptions.length}</span>
      </div>
      <div class="wl-sections">
        ${EXCEPTION_SECTIONS.map(s => renderExceptionSection(s, exceptions)).join('')}
      </div>
    </div>

    <h3 class="ar-section-h">Today's summary
      <span class="sub">As of ${m.today_date}</span>
    </h3>
    <div class="ar-kpi-grid">
      <div class="ar-kpi" data-jump="collections">
        <div class="label">Collected</div>
        <div class="val">$${formatMoney(totalCollected)}</div>
        <div class="sub">${m.collections.filter(c => c.txn_type === 'Invoice').length} invoices · ${uniqueChecks(m.collections).length} checks</div>
        <div class="link">View collections →</div>
      </div>
      <div class="ar-kpi alert" data-jump="suspense">
        <div class="label">Exceptions (urgent)</div>
        <div class="val">${urgentCount}</div>
        <div class="sub">Need research before close</div>
        <div class="link">Open worklist →</div>
      </div>
      <div class="ar-kpi" data-jump="overdue">
        <div class="label">Overdue (30+ days)</div>
        <div class="val">${overdueCount}</div>
        <div class="sub">$${formatMoney(overdueAmt)} outstanding</div>
        <div class="link">View overdue →</div>
      </div>
      <div class="ar-kpi" data-jump="new">
        <div class="label">New invoices</div>
        <div class="val">${m.schedule.length}</div>
        <div class="sub">$${formatMoney(totalNew)} added to AR</div>
        <div class="link">View new →</div>
      </div>
    </div>

    <h3 class="ar-section-h">Aging breakdown
      <span class="sub">$${formatMoney(totalAr)} total open</span>
    </h3>
    <div class="ar-aging-block">
      ${renderAgingBar(m.ar_register)}
    </div>
  `;

  // KPI tile click → jump to tab
  body.querySelectorAll('.ar-kpi[data-jump]').forEach(el => {
    el.addEventListener('click', () => {
      arState.activeTab = el.dataset.jump;
      if (window.initArDashboard) window.initArDashboard();
    });
  });

  // Exception section toggle
  body.querySelectorAll('.wl-section-header').forEach(h => {
    h.addEventListener('click', () => {
      h.parentElement.classList.toggle('open');
    });
  });

  // Suspense banner CTA
  const sbCta = body.querySelector('.sb-cta');
  if (sbCta) {
    sbCta.addEventListener('click', () => {
      arState.activeTab = 'suspense';
      if (window.initArDashboard) window.initArDashboard();
    });
  }
};

function renderSuspenseBanner(urgentCount, exceptions) {
  const totalAmt = exceptions
    .filter(e => e.severity === 'urgent' && e.details && e.details.amount != null)
    .reduce((s, e) => s + (e.details.amount || 0), 0);
  return `
    <div class="ar-suspense-banner">
      <div class="sb-ico">!</div>
      <div class="sb-body">
        <div class="sb-title">${urgentCount} urgent exception${urgentCount > 1 ? 's' : ''} need your attention today${totalAmt ? ` · $${formatMoney(totalAmt)}` : ''}</div>
        <div class="sb-sub">Click through the worklist below to resolve each one before close.</div>
      </div>
      <button class="sb-cta">Open worklist →</button>
    </div>
  `;
}

function renderExceptionSection(section, exceptions) {
  const rows = exceptions.filter(e => e.category === section.key);
  if (rows.length === 0) return '';
  const totalAmt = rows
    .map(e => e.details && (e.details.amount ?? e.details.balance ?? e.details.delta ?? 0))
    .reduce((s, n) => s + (n || 0), 0);
  return `
    <div class="wl-section">
      <div class="wl-section-header">
        <span class="chevron">▶</span>
        <span>${section.icon}</span>
        <span>${section.label}</span>
        <span class="right">${rows.length} · ${totalAmt ? '$' + formatMoney(Math.abs(totalAmt)) : ''}</span>
      </div>
      <div class="wl-section-body">
        ${rows.slice(0, 50).map(e => `
          <div class="wl-row">
            <span>${escapeHtml(e.customer?.name || '')}</span>
            <span class="id" style="font-family:Consolas,monospace;">${escapeHtml(e.invoice || e.details.check || '')}</span>
            <span class="wl-amt">$${formatMoney(e.details.amount ?? e.details.balance ?? e.details.delta ?? 0)}</span>
            <span class="wl-action" style="color:#94a3b8; font-size:0.72rem;">${escapeHtml(e.suggested_action)}</span>
          </div>
        `).join('')}
        ${rows.length > 50 ? `<div style="font-size:0.72rem; color:#94a3b8; padding:4px 0;">+${rows.length - 50} more — open the tab to see all</div>` : ''}
      </div>
    </div>
  `;
}

function renderAgingBar(register) {
  const buckets = { A: 0, B: 0, C: 0, D: 0, E: 0 };
  for (const r of register) {
    const letter = r.ar_status ? r.ar_status[0] : null;
    if (letter && buckets[letter] != null) buckets[letter] += Math.max(0, r.balance ?? 0);
  }
  const total = Object.values(buckets).reduce((a, b) => a + b, 0) || 1;
  const segs = [
    { letter: 'A', cls: 's-green',  label: 'Under 30 days' },
    { letter: 'B', cls: 's-yellow', label: '30–59' },
    { letter: 'C', cls: 's-orange', label: '60–89' },
    { letter: 'D', cls: 's-red',    label: '90–119' },
    { letter: 'E', cls: 's-brown',  label: 'Over 120' },
  ];
  return `
    <div class="ar-aging-bar">
      ${segs.map(s => {
        const pct = (buckets[s.letter] / total) * 100;
        if (pct < 1) return '';
        return `<div class="seg ${s.cls}" style="width:${pct}%">${pct.toFixed(0)}%</div>`;
      }).join('')}
    </div>
    <div class="ar-aging-legend">
      ${segs.map(s => `<span class="lg"><span class="dot" style="background:${segDotColor(s.cls)}"></span>${s.label}<span class="amt">$${formatMoney(buckets[s.letter])}</span></span>`).join('')}
    </div>
  `;
}

function segDotColor(cls) {
  return { 's-green':'#16a34a', 's-yellow':'#facc15', 's-orange':'#f97316', 's-red':'#dc2626', 's-brown':'#7c2d12' }[cls];
}
```

- [ ] **Step 12.3: Add a Suspense tab renderer**

Append to `app/assets/js/tools/ar-dashboard/ar-dashboard-views.js`:

```js
// ── Suspense tab ──

window.arRenderSuspense = function arRenderSuspense(body) {
  const suspenseRows = (arState.exceptions || []).filter(e => e.category === 'suspense');
  const nonFactored = (arState.exceptions || []).filter(e => e.category === 'non_factored');

  body.innerHTML = `
    <h3 class="ar-section-h">Suspense — unapplied payments needing research
      <span class="sub">${suspenseRows.length} real · ${nonFactored.length} non-factored auto-resolved</span>
    </h3>
    ${suspenseRows.length === 0 && nonFactored.length === 0 ? `
      <div class="ar-empty-card" style="margin-top:20px;">
        <div class="ar-empty-icon">✓</div>
        <h3>No suspense items</h3>
        <p>Either everything matched cleanly, or the loaded workbook doesn't include the raw TAB BANK file (drop the Collection_Payment.xlsx alongside the workbook to enable suspense detection).</p>
      </div>` : `
      <div class="ar-table-wrap">
        <table class="ar-table">
          <thead><tr>
            <th>Check #</th><th>Debtor (per TAB BANK)</th>
            <th class="num">Amount</th><th>Type</th><th>Suggested action</th>
          </tr></thead>
          <tbody>
            ${suspenseRows.map(e => `
              <tr>
                <td class="id">${escapeHtml(e.details.check)}</td>
                <td class="customer">${escapeHtml(e.customer.name)}</td>
                <td class="num">${formatMoney(e.details.amount)}</td>
                <td><span class="ar-status-chip danger">Needs research</span></td>
                <td>${escapeHtml(e.suggested_action)}</td>
              </tr>
            `).join('')}
            ${nonFactored.map(e => `
              <tr>
                <td class="id">${escapeHtml(e.details.check)}</td>
                <td>—</td>
                <td class="num">${formatMoney(e.details.amount)}</td>
                <td><span class="ar-status-chip ok">Non-factored</span></td>
                <td>${escapeHtml(e.suggested_action)}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    `}
  `;
};
```

- [ ] **Step 12.4: Verify the Summary + Suspense tabs render correctly**

Load the 5/19 workbook. Expected on Summary:
- Banner appears if there are urgent exceptions (count > 0)
- Exceptions worklist shows collapsible sections — only categories with rows visible
- 4 KPI tiles: Collected · Exceptions (urgent) · Overdue · New invoices
- Aging bar with 5 colored segments + dollar legend
- Click any KPI tile → jumps to corresponding tab

Suspense tab: shows real suspense + non-factored if loaded with a TAB BANK file; otherwise shows the empty-card explanation.

- [ ] **Step 12.5: Commit**

```bash
git add app/assets/js/tools/ar-dashboard/ar-dashboard-views.js \
        app/assets/css/styles.css
git commit -m "feat(ar-dashboard): Summary tab + Exceptions worklist + Suspense tab

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase I — Optional TAB BANK source-file drop (enables suspense detection)

### Task 13: Allow dropping TAB BANK Collection_Payment.xlsx alongside the workbook

**Files:**
- Modify: `app/assets/js/tools/ar-dashboard/ar-dashboard-loader.js`
- Modify: `app/assets/js/tools/ar-dashboard/ar-dashboard.js`

- [ ] **Step 13.1: Extend loader to accept an optional TAB BANK file**

Replace `arLoadWorkbook` in `app/assets/js/tools/ar-dashboard/ar-dashboard-loader.js` with:

```js
window.arLoadWorkbook = async function arLoadWorkbook(file, tabBankFile = null) {
  try {
    const buf = await file.arrayBuffer();
    const wb = XLSX.read(buf, { type: 'array', cellDates: true });
    const model = parseWorkbook(wb, file.name);

    if (tabBankFile) {
      const tabBuf = await tabBankFile.arrayBuffer();
      const tabWb = XLSX.read(tabBuf, { type: 'array', cellDates: true });
      model.tab_bank = parseTabBankSheet(tabWb.Sheets[tabWb.SheetNames[0]]);
    }

    arState.loaded = true;
    arState.filename = file.name;
    arState.model = model;
    if (window.arDetectExceptions) {
      arState.exceptions = window.arDetectExceptions(model);
    }
    if (window.initArDashboard) window.initArDashboard();
  } catch (e) {
    console.error('Failed to load workbook', e);
    alert(`Failed to load ${file.name}: ${e.message}`);
  }
};

function parseTabBankSheet(sheet) {
  if (!sheet) return [];
  const rows = XLSX.utils.sheet_to_json(sheet, { header: 1, defval: null });
  return rows.slice(1)
    .filter(r => r && r[0])
    .map(r => ({
      check:             r[0],
      amount:            r[1],
      debtor_name:       r[2],
      debtor_code:       r[3],
      post_date:         r[4],
      pmt_type:          r[5],
      invoice:           r[6],
      invoice_date:      r[7],
      purchase_date:     r[8],
      invoice_amount:    r[9],
      collected_amount:  r[10],
      chargeback_amount: r[11],
      po:                r[12],
      desc:              r[13],
    }));
}
```

- [ ] **Step 13.2: Update the empty state to accept two files (workbook + optional TAB BANK)**

Replace the `renderEmptyState` function in `ar-dashboard.js`:

```js
function renderEmptyState(view) {
  view.innerHTML = `
    <div class="ar-empty-card">
      <div class="ar-empty-icon">📊</div>
      <h3>Load today's AR aging workbook</h3>
      <p>Drop an <code>AR_AGING_MM_DD_YYYY.xlsx</code> file here. Optionally also drop the <code>Collection_Payment.xlsx</code> (TAB BANK remittance) to enable suspense detection + customer-name mismatch checks.</p>
      <input type="file" id="arWbInput" accept=".xlsx,.xls" />
      <p style="margin-top:10px;"><small>Optional TAB BANK remittance file:</small></p>
      <input type="file" id="arTabInput" accept=".xlsx,.xls" />
      <button class="ngl-btn ngl-btn-primary" id="arLoadBtn" style="margin-top:14px;">Load</button>
    </div>
  `;
  view.querySelector('#arLoadBtn').addEventListener('click', () => {
    const wbInput = view.querySelector('#arWbInput');
    const tabInput = view.querySelector('#arTabInput');
    const wbFile = wbInput.files && wbInput.files[0];
    const tabFile = tabInput.files && tabInput.files[0];
    if (!wbFile) {
      alert('Please pick a workbook file');
      return;
    }
    window.arLoadWorkbook(wbFile, tabFile || null);
  });
}
```

- [ ] **Step 13.3: Verify loading with TAB BANK drop**

Load `AR_AGING_05_19_2026.xlsx` + `Collection_Payment 051926.xlsx` together.

Expected:
- `arState.model.tab_bank.length` is ~42 (the row count of the 5/19 TAB BANK file)
- `arState.exceptions` now includes `suspense`, `non_factored`, `customer_mismatch`, `posting_gap` entries
- Summary tab shows the suspense banner with the urgent count

Then load just the workbook (no TAB BANK file).

Expected:
- `arState.model.tab_bank` is undefined
- Exceptions list is smaller (only `short`, `over`, `amount_disagreement`, `missing_tms`)
- Suspense tab shows the "drop TAB BANK file to enable" empty card

- [ ] **Step 13.4: Commit**

```bash
git add app/assets/js/tools/ar-dashboard/ar-dashboard-loader.js \
        app/assets/js/tools/ar-dashboard/ar-dashboard.js
git commit -m "feat(ar-dashboard): optional TAB BANK drop enables suspense detection

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase J — Cross-tool actions

### Task 14: Cross-tool actions (Email customer, Open in QBO, Add manual entry)

> **DROPPED 2026-06-01:** The "Copy TAB BANK report" sub-step has been removed. There is no daily summary report Jihyun sends back to TAB BANK — the "TAB BANK report" she receives every morning IS the Collection_Payment.xlsx remittance, which we already ingest as a source input. The remaining cross-tool actions (Email customer, Open in QBO, Add manual entry) are still in scope. Skip the Copy TAB BANK report sub-steps below; implement the other actions per the existing detail.

**Files:**
- Create: `app/assets/js/tools/ar-dashboard/ar-dashboard-actions.js`
- Modify: `app/assets/js/tools/ar-dashboard/ar-dashboard.js`

- [ ] **Step 14.1: Create the actions file**

Create `app/assets/js/tools/ar-dashboard/ar-dashboard-actions.js`:

```js
// AR Dashboard — cross-tool actions
// All actions assume arState.model is loaded.

window.arCopyTabBankReport = function arCopyTabBankReport() {
  const m = arState.model;
  if (!m) return;

  // Generate a plain-text summary of yesterday's collections that can be pasted
  // into the TAB BANK portal as the daily report.
  const checks = {};
  for (const c of m.collections) {
    if (!c.check_no || c.txn_type !== 'Payment') continue;
    checks[c.check_no] = {
      check: c.check_no,
      customer: c.customer_name,
      amount: c.amount,
    };
  }

  const lines = [
    `NGL Transportation — Daily Collection Report`,
    `Date: ${m.today_date}`,
    ``,
    `Check #             Customer                                              Amount`,
    `------------------  ----------------------------------------------------  -----------`,
    ...Object.values(checks).map(c =>
      `${(c.check || '').padEnd(20)}${(c.customer || '').slice(0, 52).padEnd(54)}${('$' + Number(c.amount || 0).toFixed(2)).padStart(12)}`
    ),
    ``,
    `Total: $${Object.values(checks).reduce((s, c) => s + (c.amount || 0), 0).toFixed(2)}`,
    `Checks: ${Object.values(checks).length}`,
  ];

  const text = lines.join('\n');
  navigator.clipboard.writeText(text).then(() => {
    flashToast(`TAB BANK report copied to clipboard (${Object.values(checks).length} checks)`);
  }).catch(err => {
    console.error('Clipboard write failed', err);
    alert('Could not copy. Open the dev tools console to see the report and copy manually.');
    console.log(text);
  });
};

window.arEmailCustomer = function arEmailCustomer(customerId, invoiceNo) {
  // Jump to Invoice Sender with the customer pre-selected.
  // The invoice sender exposes a `prefillSendForCustomer` global if it's loaded.
  if (window.switchTool) window.switchTool('invoice-sender');
  if (window.prefillSendForCustomer) {
    window.prefillSendForCustomer(customerId, invoiceNo);
  } else {
    console.warn('prefillSendForCustomer not found — landed on Invoice Sender empty');
  }
};

window.arOpenInQbo = function arOpenInQbo(invoiceNo) {
  // Use the QBO API integration if available, otherwise fall back to manual URL.
  if (window.agentBridge && window.agentBridge.openQboInvoice) {
    window.agentBridge.openQboInvoice(invoiceNo);
  } else {
    flashToast(`QBO deep-link not configured. Search ${invoiceNo} in QBO manually.`);
  }
};

window.arAddManualEntry = function arAddManualEntry() {
  const row = promptForManualEntry();
  if (!row) return;
  arState.manualEntries.push(row);
  arState.model.ar_register.push(row);
  if (window.arDetectExceptions) {
    arState.exceptions = window.arDetectExceptions(arState.model);
  }
  if (window.initArDashboard) window.initArDashboard();
  flashToast(`Added manual entry: ${row.inv}`);
};

function promptForManualEntry() {
  const inv = prompt('Invoice # (NGL Inv #):');
  if (!inv) return null;
  const customer = prompt('Customer name:');
  if (!customer) return null;
  const amount = parseFloat(prompt('Amount:'));
  if (!amount || isNaN(amount)) return null;
  return {
    new_id: null,
    company: customer,
    inv: inv,
    equipment: null,
    date: new Date(),
    aging: 0,
    ref_no: null,
    mbl_no: null,
    amount: amount,
    paid: 0,
    balance: amount,
    memo: 'Manually added',
    ar_status: window.arAgingBucket(0),
    wo: null,
    _manual: true,
  };
}

function flashToast(msg) {
  const t = document.createElement('div');
  t.textContent = msg;
  t.style.cssText = 'position:fixed; bottom:20px; right:20px; background:#0f172a; color:#fff; padding:10px 14px; border-radius:6px; font-size:0.82rem; z-index:9999; box-shadow:0 4px 12px rgba(0,0,0,0.18);';
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2400);
}

window.arFlashToast = flashToast;
```

- [ ] **Step 14.2: Import actions in ar-dashboard.js**

Add the import:

```js
import './ar-dashboard-actions.js';
```

- [ ] **Step 14.3: Add quick action buttons to the Summary tab**

In `ar-dashboard-views.js`, find the `arRenderSummary` function. Inside the body template, after the aging breakdown section (before the closing `body.innerHTML = \``), add:

```js
    <h3 class="ar-section-h">Quick actions
      <span class="sub">Common daily tasks</span>
    </h3>
    <div style="display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin-bottom:14px;">
      <button class="ngl-btn ngl-btn-primary" onclick="window.arCopyTabBankReport && arCopyTabBankReport()" style="padding:11px 14px;">
        📋 Copy TAB BANK report
      </button>
      <button class="ngl-btn ngl-btn-secondary" onclick="window.arAddManualEntry && arAddManualEntry()" style="padding:11px 14px;">
        + Add manual entry
      </button>
      <button class="ngl-btn ngl-btn-secondary" onclick="arState.activeTab='overdue'; window.initArDashboard()" style="padding:11px 14px;">
        📞 Today's call list
      </button>
    </div>
```

- [ ] **Step 14.4: Verify the actions**

Reload, navigate to Summary.

- Click "Copy TAB BANK report" → toast confirms · paste into a text editor → confirm the formatted report has the right checks + customers + amounts
- Click "Add manual entry" → 3 prompts → confirm new row appears in AR Register tab
- Click "Today's call list" → switches to Overdue tab

Email + QBO buttons in Overdue/detail panels:
- Click any Email button in the Overdue detail panel → app navigates to Invoice Sender (the prefill warning may show in console — that's fine, R1 doesn't require Invoice Sender prefill to be wired)
- Click any "Open in QBO" button → toast says deep-link not configured (this is OK in R1; the integration is in R2)

- [ ] **Step 14.5: Commit**

```bash
git add app/assets/js/tools/ar-dashboard/ar-dashboard-actions.js \
        app/assets/js/tools/ar-dashboard/ar-dashboard.js \
        app/assets/js/tools/ar-dashboard/ar-dashboard-views.js
git commit -m "feat(ar-dashboard): cross-tool actions — Copy TAB BANK, Email, QBO link, Add manual

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase L — Supabase R/W layer (NEW — outlined, detailed steps pending Jihyun)

**Goal:** Wire the Supabase tables that back R1's write affordances. This phase is the foundation Phase M sits on top of.

**New Supabase tables to add (migrations live in `agent/services/database.py` Supabase init alongside customers + users):**

```sql
ar_row_overrides   (invoice_id, column_name, new_value, original_value, edited_by, edited_at)
ar_memos           (invoice_id, memo_text, edited_by, edited_at)
ar_manual_entries  (id, invoice_id, customer_id, amount, date, memo, source, created_by, created_at)
ar_exceptions      (id, category, invoice_id, customer_id, details_json, resolved, resolved_by, resolved_at)
```

**Tasks (to be detailed once Jihyun answers Q6 — auto-link confidence):**

- [ ] **Task L1:** Add Supabase table migrations in `agent/services/database.py` (already the project pattern).
- [ ] **Task L2:** Create `ar-dashboard-supabase.js` with REST helpers (read overrides, read memos, write override, write memo, write manual entry, write resolved exception).
- [ ] **Task L3:** Hook into workbook load — after parsing the workbook into the in-memory model, fetch all `ar_row_overrides` and `ar_memos` for the workbook's date range and apply them on top.
- [ ] **Task L4:** Optimistic UI pattern — on edit commit, update the in-memory model immediately, fire-and-forget Supabase write, surface failure as a toast + rollback if the write fails.
- [ ] **Task L5:** Smoke test: load the workbook, edit a memo, refresh the app, verify the memo persists.

**Open dependency:** Q6 (UC auto-link confidence) — affects whether `ar_exceptions` writes happen automatically (auto-link match found) or require human confirm.

---

## Phase M — Inline editing + Overpayment Workflow + TAB BANK error (NEW — outlined, detailed steps pending Jihyun)

**Goal:** The user-visible affordances that USE Phase L's Supabase layer.

**Tasks:**

- [ ] **Task M1: Inline edit cells in AR Register table.** Click-to-edit on amount, paid, balance, memo, status. Commit on Enter or blur. Show "edited" pip (orange dot) on rows with overrides. Hover/click pip to see audit history. Writes to `ar_row_overrides` via Phase L. (No open question dependency — implement after L is in place.)

- [ ] **Task M2: Per-row memo input affordance.** Dedicated memo column with inline text input. Same persistence flow as M1 but writes to `ar_memos` table instead. (No open question dependency.)

- [ ] **Task M3: Overpayment Workflow modal.** Create `ar-dashboard-overpayment.js` exporting `openOverpaymentModal({ row, exceptionId })`. 4-step wizard:
  1. Confirm overpayment (computed amount, source check, original invoice).
  2. Push to TMS/QBO (deep-link button + "done" checkbox). **Pending Q5** — verify with re-fetch or trust checkbox?
  3. Create credit memo (auto-populated memo, editable).
  4. Persist — writes to `ar_manual_entries` + closes exception row.

- [ ] **Task M4: Email TAB BANK template (cat 9 action).** Generate paste-ready email body with check#, invoice#, deposit amount, and the corrective request. Open user's default mail client OR copy to clipboard with a toast. Mark the row "awaiting TAB BANK correction" and exclude from posting until cleared.

- [ ] **Task M5: UC reclassification linkage (cat 10 logic).** During exception detection (extends Task 11), find UC rows from yesterday whose check# + customer + amount matches a Payment row today. **Pending Q6** — auto-clear the UC row, or surface as a confirm-to-link action?

- [ ] **Task M6: TAB BANK posting error detection (cat 9 logic).** During exception detection (extends Task 11), find: (a) same check# appearing on 2+ TAB BANK rows for the same customer with conflicting Pmt Type, OR (b) a check# whose deposit amount doesn't match the AR balance for the invoice it was applied to.

**Open dependencies:**
- Q4 (write-off lifecycle) — may add a Task M7 if she needs a dedicated flow vs just using inline status edit.
- Q5 (overpayment verification) — affects Task M3 step 2.
- Q6 (UC auto-link) — affects Tasks M5.

---

## Phase K — Polish + ship

### Task 15: Drop-zone support on the empty state

**Files:**
- Modify: `app/assets/js/tools/ar-dashboard/ar-dashboard.js`

- [ ] **Step 15.1: Add drag-and-drop wiring**

In `ar-dashboard.js`, expand `renderEmptyState` to add drag/drop handlers:

```js
function renderEmptyState(view) {
  view.innerHTML = `
    <div class="ar-empty-card" id="arEmptyCard" style="border:2px dashed #cbd5e1;">
      <div class="ar-empty-icon">📊</div>
      <h3>Load today's AR aging workbook</h3>
      <p>Drop the <code>AR_AGING_MM_DD_YYYY.xlsx</code> file here. Optionally also drop the <code>Collection_Payment.xlsx</code> (TAB BANK remittance) at the same time.</p>
      <input type="file" id="arWbInput" accept=".xlsx,.xls" multiple />
      <button class="ngl-btn ngl-btn-primary" id="arLoadBtn" style="margin-top:14px;">Load</button>
    </div>
  `;
  const dz = view.querySelector('#arEmptyCard');
  dz.addEventListener('dragover', e => { e.preventDefault(); dz.style.borderColor = '#ea580c'; });
  dz.addEventListener('dragleave', () => { dz.style.borderColor = '#cbd5e1'; });
  dz.addEventListener('drop', e => {
    e.preventDefault();
    dz.style.borderColor = '#cbd5e1';
    handleFilesPicked([...(e.dataTransfer.files || [])]);
  });

  view.querySelector('#arWbInput').addEventListener('change', e => {
    handleFilesPicked([...(e.target.files || [])]);
  });

  view.querySelector('#arLoadBtn').addEventListener('click', () => {
    const files = [...(view.querySelector('#arWbInput').files || [])];
    if (files.length === 0) return alert('Pick or drop at least the AR_AGING workbook');
    handleFilesPicked(files);
  });
}

function handleFilesPicked(files) {
  let wbFile = files.find(f => /AR_AGING/i.test(f.name));
  let tabFile = files.find(f => /Collection_Payment/i.test(f.name));
  if (!wbFile) wbFile = files[0];
  if (!wbFile) return;
  window.arLoadWorkbook(wbFile, tabFile || null);
}
```

- [ ] **Step 15.2: Verify drag-drop works**

Drag the workbook + TAB BANK files together onto the empty card. They should load with TAB BANK detection.

- [ ] **Step 15.3: Commit**

```bash
git add app/assets/js/tools/ar-dashboard/ar-dashboard.js
git commit -m "feat(ar-dashboard): drag-and-drop empty state accepts both files

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 16: Bump version + run the full ship pipeline

**Files:**
- Modify: `desktop/VERSION`

- [ ] **Step 16.1: Bump VERSION**

Open `desktop/VERSION` and bump to the next release number (whatever is current + 0.1.0 minor; e.g., `2.74.0`).

```bash
cat desktop/VERSION
# Current value (example): 2.73.0
echo "2.74.0" > desktop/VERSION
cat desktop/VERSION
# Confirm: 2.74.0
```

- [ ] **Step 16.2: Run the build pipeline**

```bash
cd desktop && runbuild.bat
```

Expected: PyInstaller builds the agent, electron-builder packages the installer, no JS syntax errors caught by `check-js.js`. If anything fails — diagnose and fix, do not commit a broken build.

- [ ] **Step 16.3: Commit the VERSION bump**

```bash
git add desktop/VERSION
git commit -m "chore: bump VERSION to 2.74.0"
git push origin main
```

- [ ] **Step 16.4: Publish the GitHub release**

```bash
gh release create v2.74.0 \
  "desktop/dist/NGL Accounting Setup 2.74.0.exe" \
  "desktop/dist/latest.yml" \
  --title "v2.74.0 — AR Dashboard Release 1" \
  --notes "Initial release of the AR Dashboard (Tool #4). Loads a hand-built AR_AGING workbook and presents it as a 10-tab reconciliation cockpit with Exceptions worklist, cross-tool actions, and Suspense detection (with optional TAB BANK file drop)."
```

- [ ] **Step 16.5: Smoke-test the installed app**

After auto-update lands on a test machine (or after manual install):
- Launch the app, click AR Dashboard
- Load `AR_AGING_05_19_2026.xlsx`
- Tour all 10 tabs — confirm data renders correctly on each
- Click Copy TAB BANK report → paste into Notepad → format looks right
- Add a manual entry → confirm it appears in AR Register tab
- Drop both the workbook + Collection_Payment file → confirm Suspense tab shows exception rows

If anything is broken, fix it on `main` and ship a patch release (`v2.74.1`).

---

## Final Notes

- **R2 is a separate plan.** This R1 ships the dashboard standalone. The build engine that auto-fetches QBO + parses TAB BANK + produces the workbook is the next plan's scope.
- **Supabase writes deferred.** R1 is purely client-side. R2 introduces the daily snapshot writes.
- **Period selector deferred.** R1 ships single-day-only. R3 adds Week / Month / Quarter / Year.
- **Mockup refresh deferred.** The committed mockups still emphasize KPI tiles. They were design artifacts, not normative. The implemented Summary tab follows the spec (Exceptions worklist as centerpiece) and supersedes them.

## Self-Review

**Spec coverage check:**
- ✓ Tool #4 placement: Task 1 adds nav + view container
- ✓ 5 inputs (workbook + TAB BANK drop): Tasks 3 + 13
- ✓ All 9 tabs (Summary, AR Register, Collections, Overdue, Partial Pays, New, TMS, Adjustments, Suspense, Customers): Tasks 5-12
- ✓ Reconciliation cockpit with Exceptions worklist as centerpiece: Task 12
- ✓ 8 exception categories: Task 11
- ✓ Cross-tool actions (Copy TAB BANK report, Email customer, Open QBO, Add manual entry): Task 14
- ✓ Two-pane split pattern on list tabs: Tasks 6, 7
- ✓ Visual tokens (NGL orange, slate text, e2e8f0 borders): Tasks 4, 6, 12 (CSS additions)
- ✗ Period selector: explicitly deferred to R3 (noted in plan + spec)
- ✗ Build engine: explicitly deferred to R2 (noted in plan + spec)
- ✗ Supabase writes: explicitly deferred to R2 (noted in plan + spec)

**Placeholder scan:**
- No "TBD", "TODO", "fill in details", or vague-handwaving steps
- Every step that touches code has a complete code block
- Every command has expected output

**Type / signature consistency:**
- `arBuildARRow` returns the consistent schema used by `parseArSheet`, exception detectors, table renderers
- `arState` shape matches initial declaration (state.js) + how it's used everywhere
- `arDetectExceptions(model)` signature is the same in calling code (loader) and definition (exceptions.js)
- Exception object shape (`id`, `category`, `severity`, `invoice`, `customer`, `details`, `suggested_action`) is consistent in detector and worklist renderer
- Window-global function names match prefixed `ar*` convention: `arLoadWorkbook`, `arDetectExceptions`, `arRenderSummary`, etc.
