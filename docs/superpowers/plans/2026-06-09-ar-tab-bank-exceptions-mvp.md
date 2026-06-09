# AR Dashboard — TAB BANK Exceptions MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect TAB BANK posting gaps + customer name mismatches during the daily AR build, surface them in the preview modal KPI tile + a new EXCEPTIONS sheet in the saved workbook.

**Architecture:** Single new pure function `detectTabBankExceptions(tab_bank, qbo_collection)` in the engine. Called from the existing `arBuildToday()` after Phase 5. Result attaches to the build return as `tab_bank_exceptions`. Preview modal reads from there; writer adds a new `EXCEPTIONS` sheet between `ADJUSTMENT` and `AR_yesterday`. No engine refactor.

**Tech Stack:** vanilla JS (ESM, no build step), ExcelJS (already loaded), Node 20 + SheetJS CDN tarball for the test harness (per `reference_xlsx_node_install.md`).

**Related spec:** `docs/superpowers/specs/2026-06-09-ar-tab-bank-exceptions-mvp-design.md`

---

## File map

| File | Change |
|---|---|
| `app/assets/js/tools/ar-dashboard/ar-dashboard-build.js` | Add `detectTabBankExceptions()`; call from `arBuildToday()`; attach to return. |
| `app/assets/js/tools/ar-dashboard/ar-dashboard-build-ui.js` | Replace `exceptions = []` placeholder with `r.tab_bank_exceptions`; replace empty-state branch of `renderKpiDetail('exception', ...)` with a real table. |
| `app/assets/js/tools/ar-dashboard/ar-dashboard-build-writer.js` | Add `EXCEPTIONS_HEADERS`, `EXCEPTIONS_WIDTHS`, `buildExceptionsWorksheet()`; insert into the sheet pipeline. Update return `sheets` array. |
| `tools/test_tab_bank_exceptions.mjs` | NEW. Node test harness asserting all 5 detection cases on synthetic fixtures. |
| `desktop/VERSION` | Bump `2.78.9` → `2.79.0`. |

---

## Task 1: Write the failing unit test for `detectTabBankExceptions`

**Files:**
- Create: `tools/test_tab_bank_exceptions.mjs`

This test runs in plain Node (no framework). It builds synthetic TAB BANK + QBO Collection inputs that match the parsed schema (per `ar-dashboard-build-loader.js`), calls `detectTabBankExceptions()`, and asserts the returned exception list. Covers all five cases from the spec: `posting_gap_qbo_missing`, `posting_gap_tab_missing`, `customer_mismatch`, NON-FACTORED filter, and a clean match (no exception).

- [ ] **Step 1.1: Create the test file**

Create `tools/test_tab_bank_exceptions.mjs` with this content:

```js
// Standalone Node test for detectTabBankExceptions.
// Run: node tools/test_tab_bank_exceptions.mjs
// Exits 0 on all-pass, 1 on any failure.

import assert from 'node:assert/strict';

// Engine module uses ESM; load it dynamically so we can use top-level await
// without needing a package.json type=module on the tools/ dir.
const { detectTabBankExceptions } = await import(
  '../app/assets/js/tools/ar-dashboard/ar-dashboard-build.js'
);

// Synthetic fixtures match the parsed shape produced by ar-dashboard-build-loader.js.
// TAB BANK row keys: check, amount, debtor_name, debtor_code, post_date, pmt_type,
//                    invoice, invoice_date, purchase_date, invoice_amount,
//                    collected_amount, chargeback_amount, po, desc
// QBO Collection row keys: payment_date, txn_type, txn_number, customer,
//                          invoice_or_ref, amount, open_balance, account

function tbRow(check, amount, debtor_name, desc = null) {
  return {
    check, amount, debtor_name, debtor_code: null, post_date: null, pmt_type: null,
    invoice: null, invoice_date: null, purchase_date: null, invoice_amount: null,
    collected_amount: null, chargeback_amount: null, po: null, desc,
  };
}

function qboRow(txn_type, txn_number, customer, amount, invoice_or_ref = null) {
  return {
    payment_date: null, txn_type, txn_number, customer,
    invoice_or_ref, amount, open_balance: null, account: null,
  };
}

let failed = 0;
function test(name, fn) {
  try { fn(); console.log(`  ✓ ${name}`); }
  catch (e) { failed++; console.error(`  ✗ ${name}\n    ${e.message}`); }
}

console.log('detectTabBankExceptions');

test('emits posting_gap_qbo_missing when TAB BANK has check# QBO does not', () => {
  const tab_bank = { rows: [tbRow('12345', 1200, 'ACME LOGISTICS')] };
  const qbo_collection = { rows: [] };
  const out = detectTabBankExceptions(tab_bank, qbo_collection);
  assert.equal(out.length, 1);
  assert.equal(out[0].kind, 'posting_gap_qbo_missing');
  assert.equal(out[0].check_no, '12345');
  assert.equal(out[0].amount, 1200);
  assert.equal(out[0].tab_bank_customer, 'ACME LOGISTICS');
});

test('emits posting_gap_tab_missing when QBO has check# TAB BANK does not', () => {
  const tab_bank = { rows: [] };
  const qbo_collection = { rows: [
    qboRow('Invoice', '67890', '1234-GLOBAL FREIGHT', 850, 'INV-1'),
  ] };
  const out = detectTabBankExceptions(tab_bank, qbo_collection);
  assert.equal(out.length, 1);
  assert.equal(out[0].kind, 'posting_gap_tab_missing');
  assert.equal(out[0].check_no, '67890');
  assert.equal(out[0].amount, 850);
  assert.equal(out[0].qbo_customer, '1234-GLOBAL FREIGHT');
});

test('skips QBO rows where txn_type is not Invoice (header / sub-total rows)', () => {
  const tab_bank = { rows: [] };
  const qbo_collection = { rows: [
    qboRow('Payment', '99999', 'should-be-ignored', 0),
    qboRow(null, '88888', null, 0),
  ] };
  const out = detectTabBankExceptions(tab_bank, qbo_collection);
  assert.equal(out.length, 0);
});

test('skips TAB BANK rows with desc = NON-FACTORED (informational)', () => {
  const tab_bank = { rows: [
    tbRow('22222', 500, 'NON-FACTORED CUST', 'NON-FACTORED'),
  ] };
  const qbo_collection = { rows: [] };
  const out = detectTabBankExceptions(tab_bank, qbo_collection);
  assert.equal(out.length, 0);
});

test('skips TAB BANK rows with no check value', () => {
  const tab_bank = { rows: [tbRow(null, 100, 'NO CHECK')] };
  const qbo_collection = { rows: [] };
  const out = detectTabBankExceptions(tab_bank, qbo_collection);
  assert.equal(out.length, 0);
});

test('emits customer_mismatch when normalized names differ', () => {
  const tab_bank = { rows: [tbRow('11111', 450, 'XYZ TRANSPORT')] };
  const qbo_collection = { rows: [
    qboRow('Invoice', '11111', '5678-ACME LOGISTICS', 450, 'INV-2'),
  ] };
  const out = detectTabBankExceptions(tab_bank, qbo_collection);
  assert.equal(out.length, 1);
  assert.equal(out[0].kind, 'customer_mismatch');
  assert.equal(out[0].check_no, '11111');
  assert.equal(out[0].tab_bank_customer, 'XYZ TRANSPORT');
  assert.equal(out[0].qbo_customer, '5678-ACME LOGISTICS');
});

test('does NOT emit customer_mismatch when normalized names match (id-prefix stripped)', () => {
  const tab_bank = { rows: [tbRow('33333', 600, 'GLOBAL FREIGHT')] };
  const qbo_collection = { rows: [
    qboRow('Invoice', '33333', '1234-GLOBAL FREIGHT', 600, 'INV-3'),
  ] };
  const out = detectTabBankExceptions(tab_bank, qbo_collection);
  assert.equal(out.length, 0, 'should be empty, got: ' + JSON.stringify(out));
});

test('does NOT emit customer_mismatch when only entity suffix differs (INC vs LLC)', () => {
  const tab_bank = { rows: [tbRow('44444', 700, 'ACME LOGISTICS INC.')] };
  const qbo_collection = { rows: [
    qboRow('Invoice', '44444', '9999-ACME LOGISTICS LLC', 700, 'INV-4'),
  ] };
  const out = detectTabBankExceptions(tab_bank, qbo_collection);
  assert.equal(out.length, 0, 'should be empty, got: ' + JSON.stringify(out));
});

test('groups multiple QBO Invoice lines under same check# (one check pays many invoices)', () => {
  // Same check# 55555 applied to two invoices in QBO; TAB BANK has one matching row.
  const tab_bank = { rows: [tbRow('55555', 1500, 'BIG CUSTOMER')] };
  const qbo_collection = { rows: [
    qboRow('Invoice', '55555', '1111-BIG CUSTOMER', 800, 'INV-5a'),
    qboRow('Invoice', '55555', '1111-BIG CUSTOMER', 700, 'INV-5b'),
  ] };
  const out = detectTabBankExceptions(tab_bank, qbo_collection);
  assert.equal(out.length, 0, 'matched check# + matched name = no exception');
});

test('all-NON-FACTORED TAB BANK file produces an info exception and skips gap detection', () => {
  const tab_bank = { rows: [
    tbRow('NF1', 100, 'NF CUST 1', 'NON-FACTORED'),
    tbRow('NF2', 200, 'NF CUST 2', 'NON-FACTORED'),
  ] };
  const qbo_collection = { rows: [
    qboRow('Invoice', '99999', '1234-REAL CUSTOMER', 500, 'INV-9'),
  ] };
  const out = detectTabBankExceptions(tab_bank, qbo_collection);
  // Per spec §8 risk mitigation: emit a single info exception, no false positives.
  assert.equal(out.length, 1);
  assert.equal(out[0].kind, 'info_all_non_factored');
});

console.log(failed === 0
  ? `\nAll tests passed.`
  : `\n${failed} test(s) failed.`);
process.exit(failed === 0 ? 0 : 1);
```

- [ ] **Step 1.2: Run the test to confirm it fails (function not yet exported)**

Run: `node tools/test_tab_bank_exceptions.mjs`
Expected: process exits non-zero, error contains `detectTabBankExceptions` is not a function (or `undefined`).

- [ ] **Step 1.3: Commit the test**

```bash
git add tools/test_tab_bank_exceptions.mjs
git commit -m "test(ar-dashboard): unit tests for detectTabBankExceptions (failing)

Synthetic-fixture coverage for all 5 detection cases:
posting_gap_qbo_missing, posting_gap_tab_missing, customer_mismatch,
NON-FACTORED filter, clean matches with normalization. Failing until
the engine function lands in Task 2."
```

---

## Task 2: Implement `detectTabBankExceptions` in the engine

**Files:**
- Modify: `app/assets/js/tools/ar-dashboard/ar-dashboard-build.js`

Add the new function and its helper. Do NOT wire it into `arBuildToday()` yet — that's Task 3. This task focuses on the standalone function so the test from Task 1 passes.

- [ ] **Step 2.1: Add the customer-name normalizer helper**

Insert this function near the top of `ar-dashboard-build.js`, immediately after the existing `num()` helper (around line 34):

```js
// Conservative customer-name normalizer for TAB BANK ↔ QBO comparison.
// Strips id-prefix, entity suffixes, punctuation; uppercases; collapses ws.
// See spec §4.3 — prefers false negatives over false positives.
function normalizeCustomerName(name) {
  if (!name || typeof name !== 'string') return '';
  let s = name.toUpperCase();
  // Strip leading "1234-" id prefix (QBO customer field format)
  s = s.replace(/^\d+\s*-\s*/, '');
  // Strip trailing entity suffix tokens — repeat once for ", INC." patterns
  const suffixRe = /[,.\s]+(INC|LLC|LTD|CO|CORP|CORPORATION)\.?$/i;
  s = s.replace(suffixRe, '');
  s = s.replace(suffixRe, '');
  // Strip non-alphanumeric chars except spaces, collapse whitespace
  s = s.replace(/[^A-Z0-9 ]+/g, ' ');
  s = s.replace(/\s+/g, ' ').trim();
  return s;
}
```

- [ ] **Step 2.2: Add the `detectTabBankExceptions` function**

Add this function at the end of the file, before the `if (typeof window !== 'undefined')` window-hook block (around line 240):

```js
// ---------------------------------------------------------------------------
// TAB BANK exception detection (spec 2026-06-09)
// ---------------------------------------------------------------------------

export function detectTabBankExceptions(tab_bank, qbo_collection) {
  const out = [];
  if (!tab_bank || !tab_bank.rows || !qbo_collection || !qbo_collection.rows) {
    return out;
  }

  // ---- Pre-filter TAB BANK rows ----
  const tbRows = tab_bank.rows.filter(r =>
    r && r.check != null && r.check !== '' && r.desc !== 'NON-FACTORED'
  );
  const nonFactoredCount = tab_bank.rows.filter(r => r && r.desc === 'NON-FACTORED').length;
  const totalTb = tab_bank.rows.filter(r => r && r.check != null && r.check !== '').length;

  // ---- All-NON-FACTORED guardrail (spec §8 risk mitigation) ----
  if (totalTb > 0 && tbRows.length === 0 && nonFactoredCount > 0) {
    out.push({
      kind: 'info_all_non_factored',
      check_no: '',
      message: 'TAB BANK file appears to contain only NON-FACTORED rows — gap detection skipped',
      amount: null,
      tab_bank_row: null,
      qbo_row: null,
      tab_bank_customer: null,
      qbo_customer: null,
    });
    return out;
  }

  // ---- Build indexes keyed by check# (stringified for safe Map keys) ----
  const tbIndex = new Map();
  for (const r of tbRows) tbIndex.set(String(r.check), r);

  const qboIndex = new Map();
  for (const line of qbo_collection.rows) {
    if (!line || line.txn_type !== 'Invoice') continue;
    if (line.txn_number == null || line.txn_number === '') continue;
    const key = String(line.txn_number);
    if (!qboIndex.has(key)) qboIndex.set(key, []);
    qboIndex.get(key).push(line);
  }

  // ---- Posting gap: TAB BANK has check# but QBO does not ----
  for (const [checkNo, tbRow] of tbIndex) {
    if (qboIndex.has(checkNo)) continue;
    out.push({
      kind: 'posting_gap_qbo_missing',
      check_no: checkNo,
      message: `Bank received $${num(tbRow.amount).toFixed(2)} on check# ${checkNo} from ${tbRow.debtor_name || '(no customer)'} but no QBO payment recorded`,
      amount: num(tbRow.amount),
      tab_bank_row: tbRow,
      qbo_row: null,
      tab_bank_customer: tbRow.debtor_name || null,
      qbo_customer: null,
    });
  }

  // ---- Posting gap: QBO has check# but TAB BANK does not ----
  for (const [checkNo, qboLines] of qboIndex) {
    if (tbIndex.has(checkNo)) continue;
    const firstLine = qboLines[0];
    const total = qboLines.reduce((s, l) => s + num(l.amount), 0);
    out.push({
      kind: 'posting_gap_tab_missing',
      check_no: checkNo,
      message: `QBO has a $${total.toFixed(2)} payment on check# ${checkNo} but TAB BANK has no record`,
      amount: total,
      tab_bank_row: null,
      qbo_row: firstLine,
      tab_bank_customer: null,
      qbo_customer: firstLine.customer || null,
    });
  }

  // ---- Customer name mismatch: check# in both, names differ after normalization ----
  for (const [checkNo, tbRow] of tbIndex) {
    const qboLines = qboIndex.get(checkNo);
    if (!qboLines || qboLines.length === 0) continue;
    const tbName = tbRow.debtor_name || '';
    const qboName = qboLines[0].customer || '';
    const nTb = normalizeCustomerName(tbName);
    const nQbo = normalizeCustomerName(qboName);
    if (nTb && nQbo && nTb !== nQbo) {
      out.push({
        kind: 'customer_mismatch',
        check_no: checkNo,
        message: `Check# ${checkNo} — TAB BANK says ${tbName}, QBO says ${qboName}`,
        amount: num(tbRow.amount),
        tab_bank_row: tbRow,
        qbo_row: qboLines[0],
        tab_bank_customer: tbName,
        qbo_customer: qboName,
      });
    }
  }

  // ---- Sort: qbo_missing → tab_missing → customer_mismatch (most urgent first) ----
  const kindOrder = {
    info_all_non_factored: -1,
    posting_gap_qbo_missing: 0,
    posting_gap_tab_missing: 1,
    customer_mismatch: 2,
  };
  out.sort((a, b) => (kindOrder[a.kind] ?? 99) - (kindOrder[b.kind] ?? 99));

  return out;
}
```

- [ ] **Step 2.3: Expose on `window` for browser console testing**

In the existing `if (typeof window !== 'undefined')` block at the bottom of the file, add:

```js
  window.detectTabBankExceptions = detectTabBankExceptions;
```

After the change the block reads:

```js
if (typeof window !== 'undefined') {
  window.arBuildToday = arBuildToday;
  window.arBuildTodayFromBuffers = arBuildTodayFromBuffers;
  window.arComputeAgeDelta = computeAgeDelta;
  window.detectTabBankExceptions = detectTabBankExceptions;
}
```

- [ ] **Step 2.4: Run the test — expect PASS**

Run: `node tools/test_tab_bank_exceptions.mjs`
Expected: `All tests passed.` printed, exit code 0.

- [ ] **Step 2.5: Verify JS syntax (whole-tree gate)**

Run: `node desktop/check-js.js`
Expected: `===CHECK_JS_OK===`.

- [ ] **Step 2.6: Commit**

```bash
git add app/assets/js/tools/ar-dashboard/ar-dashboard-build.js
git commit -m "feat(ar-dashboard): detectTabBankExceptions — posting gaps + name mismatch

Pure detection function over TAB BANK ↔ QBO Collection. Three exception
kinds: posting_gap_qbo_missing (bank deposit, no QBO post),
posting_gap_tab_missing (QBO post, no bank record), customer_mismatch
(check# in both, normalized names differ). Conservative name normalizer
strips id-prefix + entity suffixes (INC/LLC/CO/CORP). NON-FACTORED
rows filtered as informational. All-NON-FACTORED guardrail emits an
info exception and skips gap detection. Unit tests pass."
```

---

## Task 3: Wire detection into `arBuildToday`

**Files:**
- Modify: `app/assets/js/tools/ar-dashboard/ar-dashboard-build.js`

Call the new detector from `arBuildToday()` after Phase 5 (TMS reconcile) and attach to the return.

- [ ] **Step 3.1: Add the call after Phase 5**

Find this block at the end of `arBuildToday` (around line 200 — the comment `// ----- Phase 6 — package the auxiliary outputs`):

```js
  // ----- Phase 6 — package the auxiliary outputs
  // Today's AR as an array, sorted by inv for deterministic ordering
  const todayArArray = Array.from(todayAr.values());

  return {
    today_ar: todayArArray,
    yest_date: yesterday.sheet_date,
    target_date: target_date,
    tms_rows: tmsSheetRows,
    adjustment_rows: adjustmentRows,
    new_invs: Array.from(newInvs),
    age_delta: delta,
    // Carry raw inputs so the M2 writer can produce COL / COL (INV) / Schedule sheets
    raw: {
      qbo_collection: qbo_collection.rows,
      qbo_schedule: qbo_schedule.rows,
      tab_bank: tab_bank ? tab_bank.rows : [],
    },
  };
}
```

Replace with:

```js
  // ----- Phase 5b — TAB BANK exceptions (spec 2026-06-09)
  const tabBankExceptions = tab_bank
    ? detectTabBankExceptions(tab_bank, qbo_collection)
    : [];

  // ----- Phase 6 — package the auxiliary outputs
  // Today's AR as an array, sorted by inv for deterministic ordering
  const todayArArray = Array.from(todayAr.values());

  return {
    today_ar: todayArArray,
    yest_date: yesterday.sheet_date,
    target_date: target_date,
    tms_rows: tmsSheetRows,
    adjustment_rows: adjustmentRows,
    tab_bank_exceptions: tabBankExceptions,
    new_invs: Array.from(newInvs),
    age_delta: delta,
    // Carry raw inputs so the M2 writer can produce COL / COL (INV) / Schedule sheets
    raw: {
      qbo_collection: qbo_collection.rows,
      qbo_schedule: qbo_schedule.rows,
      tab_bank: tab_bank ? tab_bank.rows : [],
    },
  };
}
```

- [ ] **Step 3.2: Verify syntax**

Run: `node desktop/check-js.js`
Expected: `===CHECK_JS_OK===`.

- [ ] **Step 3.3: Re-run the verify harness — no regression on existing build days**

Run: `node tools/verify_ar_build_js.mjs`
Expected: existing test days still match within their baseline tolerance. The harness compares today_ar / tms_rows / adjustment_rows cell-by-cell — `tab_bank_exceptions` is a new key the harness ignores, so adding it does NOT affect parity numbers.

If any baseline regresses by more than 0.10pp the script exits non-zero — STOP and investigate before committing.

- [ ] **Step 3.4: Commit**

```bash
git add app/assets/js/tools/ar-dashboard/ar-dashboard-build.js
git commit -m "feat(ar-dashboard): wire detectTabBankExceptions into arBuildToday

Calls detectTabBankExceptions after Phase 5 TMS reconcile and attaches
the result as tab_bank_exceptions on the build return. Existing engine
parity unchanged — verified against the 6 baseline build days."
```

---

## Task 4: Preview modal — populate KPI tile + detail panel

**Files:**
- Modify: `app/assets/js/tools/ar-dashboard/ar-dashboard-build-ui.js`

Two edits: read `exceptions` from `r.tab_bank_exceptions`, and replace the empty-state branch of `renderKpiDetail('exception', ...)` with a real table.

- [ ] **Step 4.1: Replace the exceptions placeholder**

In `previewModalHtml()`, find this line (around line 230):

```js
  const exceptions = []; // M3 will populate this
```

Replace with:

```js
  const exceptions = r.tab_bank_exceptions || [];
```

- [ ] **Step 4.2: Update the KPI tile subtitle wording**

In the same function, find this entry in the `kpis` array:

```js
    { key: 'exception', label: 'Suspense / exceptions', icoClass: 'red',   val: exceptions.length,   sub: exceptions.length ? 'Need your attention before saving' : 'None detected (M3 will add detectors)', alert: true },
```

Replace with:

```js
    { key: 'exception', label: 'Suspense / exceptions', icoClass: 'red',   val: exceptions.length,   sub: exceptions.length ? 'Bank / QBO mismatches need review before saving' : 'No TAB BANK mismatches detected', alert: true },
```

- [ ] **Step 4.3: Replace `renderKpiDetail('exception', ...)` empty branch with a real table**

Find the existing `exception` branch in `renderKpiDetail()` (around line 357):

```js
  if (kpiKey === 'exception') {
    if (data.exceptions.length === 0) {
      return `
        <div class="ar-detail-panel">
          <div class="dp-head">
            <div class="dp-title">Suspense items to triage</div>
            <div class="dp-sub">No exceptions detected in this build</div>
          </div>
          <div style="padding:24px; text-align:center; color:#64748b; font-size:0.78rem">
            Detectors for TAB BANK posting errors + UC reclassification land in M3.
          </div>
        </div>`;
    }
    return '<div class="ar-detail-panel"><!-- M3 exception list --></div>';
  }
```

Replace with:

```js
  if (kpiKey === 'exception') {
    if (data.exceptions.length === 0) {
      return `
        <div class="ar-detail-panel">
          <div class="dp-head">
            <div class="dp-title">TAB BANK mismatches</div>
            <div class="dp-sub">No exceptions detected in this build</div>
          </div>
          <div style="padding:24px; text-align:center; color:#64748b; font-size:0.78rem">
            Every TAB BANK check# matches a QBO payment with the same customer.
          </div>
        </div>`;
    }
    const rows = data.exceptions.slice(0, 50);
    return `
      <div class="ar-detail-panel">
        <div class="dp-head">
          <div class="dp-title">TAB BANK mismatches</div>
          <div class="dp-sub">${data.exceptions.length} rows · sorted by urgency · review before saving</div>
        </div>
        ${rowsTable(['Issue', 'Check #', 'Amount', 'TAB BANK Customer', 'QBO Customer'], rows.map(e => [
          tabBankExceptionLabel(e.kind),
          e.check_no,
          e.amount != null ? fmtMoney(e.amount) : '—',
          e.tab_bank_customer || '—',
          e.qbo_customer || '—',
        ]), data.exceptions.length, ['', 'mono', 'num', '', ''])}
      </div>`;
  }
```

- [ ] **Step 4.4: Add the `tabBankExceptionLabel` helper**

Add this function above `renderKpiDetail()` (anywhere in the file before its first use is fine; place it just before `renderKpiDetail(...)`):

```js
function tabBankExceptionLabel(kind) {
  switch (kind) {
    case 'posting_gap_qbo_missing': return 'Bank deposit, no QBO post';
    case 'posting_gap_tab_missing': return 'QBO post, no bank record';
    case 'customer_mismatch':       return 'Customer name mismatch';
    case 'info_all_non_factored':   return 'TAB BANK file all NON-FACTORED';
    default: return kind;
  }
}
```

- [ ] **Step 4.5: Verify syntax**

Run: `node desktop/check-js.js`
Expected: `===CHECK_JS_OK===`.

- [ ] **Step 4.6: Commit**

```bash
git add app/assets/js/tools/ar-dashboard/ar-dashboard-build-ui.js
git commit -m "feat(ar-dashboard): preview modal surfaces TAB BANK exceptions

KPI tile now counts r.tab_bank_exceptions; click expands to a real table
with Issue / Check # / Amount / TAB BANK Customer / QBO Customer. Sort
order from the engine (qbo_missing → tab_missing → customer_mismatch)
is preserved. Empty state message updated to match the new detector
scope (no longer references the M3 placeholder)."
```

---

## Task 5: Writer — add the EXCEPTIONS sheet

**Files:**
- Modify: `app/assets/js/tools/ar-dashboard/ar-dashboard-build-writer.js`

Add a new sheet between `ADJUSTMENT` and `AR_yesterday`. Style matches TMS / ADJUSTMENT (frozen row 1, autofilter, Calibri 10pt, bordered+centered header row).

- [ ] **Step 5.1: Add header + width constants**

After the existing `ADJUSTMENT_WIDTHS` constant (around line 56), add:

```js
const EXCEPTIONS_HEADERS = [
  'Kind', 'Check #', 'Amount', 'TAB BANK Customer', 'QBO Customer', 'Detected Issue', 'Notes',
];
const EXCEPTIONS_WIDTHS = [28, 12, 12, 28, 28, 60, 30];
```

- [ ] **Step 5.2: Add `buildExceptionsWorksheet` function**

After the existing `buildAdjustmentWorksheet` function (around line 330, before the `// Public entry —` divider comment), add:

```js
function buildExceptionsWorksheet(wb, exceptions) {
  const ws = wb.addWorksheet('EXCEPTIONS', { views: [{ state: 'frozen', ySplit: 1 }] });
  setColWidths(ws, EXCEPTIONS_WIDTHS);
  const header = ws.addRow(EXCEPTIONS_HEADERS);
  styleRowAll(header, 17, FONT_BODY_BOLD, ALIGN_CENTER, BORDER_ALL);
  const kindLabel = (kind) => {
    switch (kind) {
      case 'posting_gap_qbo_missing': return 'Bank deposit, no QBO post';
      case 'posting_gap_tab_missing': return 'QBO post, no bank record';
      case 'customer_mismatch':       return 'Customer name mismatch';
      case 'info_all_non_factored':   return 'TAB BANK file all NON-FACTORED';
      default: return kind;
    }
  };
  for (const e of exceptions) {
    const row = ws.addRow([
      kindLabel(e.kind),
      e.check_no || '',
      e.amount,
      e.tab_bank_customer || '',
      e.qbo_customer || '',
      e.message || '',
      '', // Notes — Jihyun fills this in
    ]);
    styleRowAll(row, 17, FONT_BODY, ALIGN_CENTER, null);
    row.getCell(4).alignment = ALIGN_LEFT;  // TAB BANK Customer
    row.getCell(5).alignment = ALIGN_LEFT;  // QBO Customer
    row.getCell(6).alignment = ALIGN_LEFT;  // Detected Issue
    row.getCell(7).alignment = ALIGN_LEFT;  // Notes
    row.getCell(3).numFmt = NF_MONEY;       // Amount
  }
  ws.autoFilter = {
    from: { row: 1, column: 1 },
    to: { row: Math.max(exceptions.length, 0) + 1, column: 7 },
  };
  return ws;
}
```

- [ ] **Step 5.3: Insert the sheet into the pipeline**

Find the sheet calls in `arBuildWriteWorkbook` (around line 346):

```js
  buildArWorksheet(wb, todaySheetName, buildResult.today_ar);
  buildCollectionsWorksheet(wb, 'COL', buildInputs.qbo_collection.parsed.rows, targetDate, 'Invoice');
  buildCollectionsWorksheet(wb, 'COL (INV)', buildInputs.qbo_collection.parsed.rows, targetDate, 'Payment');
  buildScheduleWorksheet(wb, buildInputs.qbo_schedule.parsed.rows, targetDate);
  buildTmsWorksheet(wb, buildResult.tms_rows);
  buildAdjustmentWorksheet(wb, buildResult.adjustment_rows);
  buildArWorksheet(wb, yestSheetName, buildInputs.yesterday.parsed.ar_register);
```

Insert the EXCEPTIONS sheet between ADJUSTMENT and AR_yesterday:

```js
  buildArWorksheet(wb, todaySheetName, buildResult.today_ar);
  buildCollectionsWorksheet(wb, 'COL', buildInputs.qbo_collection.parsed.rows, targetDate, 'Invoice');
  buildCollectionsWorksheet(wb, 'COL (INV)', buildInputs.qbo_collection.parsed.rows, targetDate, 'Payment');
  buildScheduleWorksheet(wb, buildInputs.qbo_schedule.parsed.rows, targetDate);
  buildTmsWorksheet(wb, buildResult.tms_rows);
  buildAdjustmentWorksheet(wb, buildResult.adjustment_rows);
  buildExceptionsWorksheet(wb, buildResult.tab_bank_exceptions || []);
  buildArWorksheet(wb, yestSheetName, buildInputs.yesterday.parsed.ar_register);
```

- [ ] **Step 5.4: Update the returned `sheets` array**

A few lines below in the same `arBuildWriteWorkbook` function, find:

```js
    sheets: [todaySheetName, 'COL', 'COL (INV)', 'Schedule', 'TMS', 'ADJUSTMENT', yestSheetName],
```

Replace with:

```js
    sheets: [todaySheetName, 'COL', 'COL (INV)', 'Schedule', 'TMS', 'ADJUSTMENT', 'EXCEPTIONS', yestSheetName],
```

- [ ] **Step 5.5: Update the file-header comment block (sheet order list)**

At the top of the file (lines 7–17), the comment lists the sheet order. Find:

```js
// Sheet order (matches her tab order — AR_yesterday goes LAST):
//   1. AR_<today>     — today's reconciled register, 15 cols (col O blank)
//   2. COL            — all QBO Daily Collection rows; Invoice rows hidden
//                       (the "payments" view she uses)
//   3. COL (INV)      — all QBO Daily Collection rows; Payment rows hidden
//                       (the "invoices" view she uses)
//   4. Schedule       — QBO Daily Schedule with report header
//   5. TMS            — TMS rows the engine settled
//   6. ADJUSTMENT     — TMS rows whose amount changed (deltas)
//   7. AR_<yesterday> — yesterday's register, carried forward for diffing
```

Replace with:

```js
// Sheet order (matches her tab order — AR_yesterday goes LAST):
//   1. AR_<today>     — today's reconciled register, 15 cols (col O blank)
//   2. COL            — all QBO Daily Collection rows; Invoice rows hidden
//                       (the "payments" view she uses)
//   3. COL (INV)      — all QBO Daily Collection rows; Payment rows hidden
//                       (the "invoices" view she uses)
//   4. Schedule       — QBO Daily Schedule with report header
//   5. TMS            — TMS rows the engine settled
//   6. ADJUSTMENT     — TMS rows whose amount changed (deltas)
//   7. EXCEPTIONS     — TAB BANK ↔ QBO mismatches (posting gaps + name mismatch)
//   8. AR_<yesterday> — yesterday's register, carried forward for diffing
```

- [ ] **Step 5.6: Verify syntax**

Run: `node desktop/check-js.js`
Expected: `===CHECK_JS_OK===`.

- [ ] **Step 5.7: Commit**

```bash
git add app/assets/js/tools/ar-dashboard/ar-dashboard-build-writer.js
git commit -m "feat(ar-dashboard): EXCEPTIONS sheet in saved workbook

New sheet between ADJUSTMENT and AR_yesterday with the TAB BANK
mismatch table. Columns: Kind / Check # / Amount / TAB BANK Customer /
QBO Customer / Detected Issue / Notes. Standard TMS/ADJUSTMENT styling
(frozen row 1, autofilter, Calibri 10pt, bordered+centered header).
Notes column intentionally blank for Jihyun to use as a scratchpad."
```

---

## Task 6: Bump VERSION to 2.79.0

**Files:**
- Modify: `desktop/VERSION`

- [ ] **Step 6.1: Edit VERSION**

Open `desktop/VERSION` and replace its single line:

```
2.79.0
```

- [ ] **Step 6.2: Commit**

```bash
git add desktop/VERSION
git commit -m "chore(release): bump VERSION to 2.79.0 — TAB BANK exceptions MVP"
```

---

## Task 7: Local build (no push, no GH release)

This is a **owner-only preview build** per spec §9. Coworkers stay on v2.78.8 until owner verification passes.

- [ ] **Step 7.1: Kick the build**

Run via PowerShell (the empty-stdin pattern documented in memory `feedback_use_runbuild_for_rebuild.md`):

```powershell
$root = "C:\Users\Joseph\Desktop\NGL ACCOUNTING SERVICE"
if (-not (Test-Path "$root\empty_stdin.txt")) { New-Item -ItemType File -Path "$root\empty_stdin.txt" -Force | Out-Null }
Remove-Item -Path "$root\build.log","$root\build.err" -ErrorAction SilentlyContinue
Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "`"$root\desktop\runbuild.bat`"" -WorkingDirectory $root -RedirectStandardInput "$root\empty_stdin.txt" -RedirectStandardOutput "$root\build.log" -RedirectStandardError "$root\build.err" -NoNewWindow -PassThru
```

Wait for `===ELECTRON_BUILD_DONE===` in `build.log`. Build typically takes 4–6 minutes (PyInstaller + Electron).

- [ ] **Step 7.2: Verify artifacts**

Run:

```bash
ls -la "desktop/dist/NGL_ACCOUNTING_INSTALLER_v2.79.0.exe" "desktop/dist/latest.yml" "desktop/dist/win-unpacked/NGL Accounting.exe"
```

Expected: all three present, sizes > 0, timestamps within the last 10 minutes.

- [ ] **Step 7.3: Do NOT push to origin/main**
- [ ] **Step 7.4: Do NOT create a GitHub release**

The user has explicitly asked this NOT to ship to coworkers until smoke verification passes.

---

## Task 8: Manual smoke test (USER)

The user runs the desktop app from `desktop/dist/win-unpacked/NGL Accounting.exe` (their desktop shortcut points there per memory `feedback_app_not_website.md`).

- [ ] **Step 8.1: Launch the v2.79.0 build**

Close any running copy of the app. Launch from the desktop shortcut. Confirm title bar reads **v2.79.0**.

- [ ] **Step 8.2: Build today's workbook with the existing test inputs**

Navigate to AR Dashboard. Drop all 5 files from one of the test build folders (e.g. `C:/Users/Joseph/Desktop/NGL ACCOUNTING SERVICE - TEST DATA/AR_AGING_assets/build-2026-05-15/`). Click **Run build →**.

- [ ] **Step 8.3: Verify the KPI tile**

In the preview modal, the "Suspense / exceptions" tile shows a real count (might be 0 if the test data has no mismatches — that's fine). Subtitle no longer references "M3 will add detectors". If count > 0, the tile retains its red `alert` styling.

- [ ] **Step 8.4: Click the KPI tile**

Verify the detail panel below shows either:
- The empty state: "No TAB BANK mismatches detected" with the explanatory subtitle, OR
- A table with columns Issue / Check # / Amount / TAB BANK Customer / QBO Customer, sorted with `posting_gap_qbo_missing` rows first.

- [ ] **Step 8.5: Save the workbook**

Click **Save & open dashboard →**. The workbook writes to disk and the dashboard transitions to the loaded view.

- [ ] **Step 8.6: Open the written file in Excel**

Navigate to the saved folder and open the new `AR_AGING_*.xlsx`. Verify:
- Sheet order: `AR_<today>` · `COL` · `COL (INV)` · `Schedule` · `TMS` · `ADJUSTMENT` · `EXCEPTIONS` · `AR_<yesterday>`
- The `EXCEPTIONS` sheet has the 7 columns and the header row is bordered/centered
- Autofilter and frozen-top-row are active
- If the engine emitted exceptions, the rows are present with the same data as the preview detail panel

- [ ] **Step 8.7: Report back**

Tell Claude one of:
- "Smoke pass" — proceed to extended testing with real production inputs
- "Smoke fail: [what broke]" — Claude pauses and investigates before any further build

---

## Acceptance criteria recap

1. ✅ `detectTabBankExceptions` unit tests all pass on synthetic fixtures (Task 1+2).
2. ✅ Existing engine parity unchanged on the 6 baseline build days (Task 3 verify step).
3. ✅ Preview modal KPI tile populates from `r.tab_bank_exceptions`; click expands to a real table (Task 4).
4. ✅ Saved workbook contains the EXCEPTIONS sheet between ADJUSTMENT and AR_yesterday with the 7 specified columns (Task 5).
5. ✅ v2.79.0 builds locally without push or GH release (Tasks 6+7).
6. ✅ User smoke confirms KPI + sheet behavior in the packaged app (Task 8).
