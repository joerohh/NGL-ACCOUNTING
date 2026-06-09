# AR Dashboard — TAB BANK Short-Pay Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch the AR engine to use TAB BANK's `collected_amount` per invoice as the source of truth for what got paid, so the 8 missing short-pay invoices from today's 06/08 comparison reappear in the program output. Also adds SUSPENSE row handling, overpay detection, and fixes one bug in the v2.79.0 MVP posting-gap math.

**Architecture:** `detectTabBankExceptions` becomes `analyzeTabBank` returning `{exceptions, tbAppliedByInv, tbByCheck, tbByInvoice}`. Called from `arBuildToday` BEFORE Phase 2 (was Phase 5b). Phase 2 refactors to prefer TAB BANK collected_amount per invoice, falling back to QBO Collection when no TAB BANK row exists. Adds a second pass through TAB-BANK-only invoices to catch bank-deposited-but-not-QBO-posted cases.

**Tech Stack:** vanilla JS (ESM, no build step), ExcelJS (already loaded), Node 20 for the test harness.

**Related spec:** `docs/superpowers/specs/2026-06-09-ar-tab-bank-short-pay-design.md`

---

## File map

| File | Change |
|---|---|
| `app/assets/js/tools/ar-dashboard/ar-dashboard-build.js` | Refactor `detectTabBankExceptions` to return `{exceptions, tbAppliedByInv, tbByCheck, tbByInvoice}`. Add SUSPENSE row surfacing. Add per-invoice short_pay/overpay detection (consumes optional `ar_register` arg). Fix MVP posting-gap to sum `collected_amount`. Refactor `arBuildToday` Phase 2 to prefer TAB BANK per-invoice over QBO, with second pass for TAB-BANK-only invoices. |
| `app/assets/js/tools/ar-dashboard/ar-dashboard-build-ui.js` | Add new exception kinds to `tabBankExceptionLabel` helper. Extend `renderKpiDetail('exception')` table with TAB BANK $ / QBO $ / Affected INV# columns. |
| `app/assets/js/tools/ar-dashboard/ar-dashboard-build-writer.js` | Extend `EXCEPTIONS_HEADERS` + `EXCEPTIONS_WIDTHS` with new columns. Update `buildExceptionsWorksheet` to populate them. |
| `tools/test_tab_bank_exceptions.mjs` | Existing tests update to use `.exceptions` shape. Add new tests for SUSPENSE, short_pay, overpay, tbAppliedByInv. |
| `tools/compare_program_vs_manual.py` | (Already exists from today's session.) Will be used in T8 to verify the 8 missing invoices reappear. |
| `desktop/VERSION` | Bump `2.79.0` → `2.79.1`. |

---

## Task 1: Update tests for the new shape + add short-pay tests (failing)

**Files:**
- Modify: `tools/test_tab_bank_exceptions.mjs`

The existing MVP tests treat the return value as an `Array`. After this slice, it returns `{exceptions, tbAppliedByInv, tbByCheck, tbByInvoice}`. Update existing assertions AND add new tests for SUSPENSE handling, short_pay, overpay, and the posting-gap collected_amount fix.

- [ ] **Step 1.1: Add `tbRow` collected_amount + sample-AR helpers at top of fixtures block**

After the existing `tbRow` function (around line 24), modify the signature to allow collected_amount and invoice:

```js
function tbRow(check, amount, debtor_name, desc = null, opts = {}) {
  return {
    check, amount, debtor_name, debtor_code: null, post_date: null,
    pmt_type: opts.pmt_type ?? null,
    invoice: opts.invoice ?? null,
    invoice_date: null, purchase_date: null,
    invoice_amount: opts.invoice_amount ?? null,
    collected_amount: opts.collected_amount ?? amount,  // default = amount (single-row case)
    chargeback_amount: opts.chargeback_amount ?? null,
    po: null, desc,
  };
}

function arRow(inv, amount, paid = 0) {
  return { inv, amount, paid, balance: amount - paid, company: 'TEST CUST', memo: null };
}
```

- [ ] **Step 1.2: Update all existing tests to use `.exceptions` instead of treating result as array**

For each existing `test(...)` block in the file, change:

```js
const out = detectTabBankExceptions(tab_bank, qbo_collection);
assert.equal(out.length, 1);
assert.equal(out[0].kind, ...);
```

to:

```js
const result = detectTabBankExceptions(tab_bank, qbo_collection);
assert.equal(result.exceptions.length, 1);
assert.equal(result.exceptions[0].kind, ...);
```

Apply this rewrite to ALL 10 existing tests. Specifically: search for `out.length`, replace with `result.exceptions.length`. Search for `out[0]`, replace with `result.exceptions[0]`. Search for `out =`, replace with `result =`. Then replace any `JSON.stringify(out)` with `JSON.stringify(result.exceptions)`.

- [ ] **Step 1.3: Add SUSPENSE exception test**

Append to the test file (before the `console.log(failed === 0 ...)` block):

```js
test('emits tab_bank_suspense_row exception per SUSPENSE row', () => {
  const tab_bank = { rows: [
    tbRow('A001', 2023, 'SUSPENSE', null, { invoice: 'BMOU5186013', collected_amount: 2023 }),
    tbRow('A001', 0, 'SUSPENSE', null, { invoice: '0905182273UC', collected_amount: -2023, pmt_type: 'Payment' }),
    tbRow('A001', 2023, 'SUSPENSE', null, { invoice: 'HMMU6980181', collected_amount: 0, pmt_type: 'Unapplied Cash' }),
  ] };
  const result = detectTabBankExceptions({ rows: tab_bank.rows }, { rows: [] });
  // Expect 3 SUSPENSE exceptions, one per row (no posting-gap from these)
  const suspenseExc = result.exceptions.filter(e => e.kind === 'tab_bank_suspense_row');
  assert.equal(suspenseExc.length, 3);
  assert.equal(suspenseExc[0].check_no, 'A001');
  assert.equal(suspenseExc[0].amount, 2023);
  assert.equal(suspenseExc[0].tab_bank_customer, 'SUSPENSE');
  // Posting-gap detection: SUSPENSE rows should NOT generate posting_gap_qbo_missing
  const gaps = result.exceptions.filter(e => e.kind.startsWith('posting_gap'));
  assert.equal(gaps.length, 0);
});
```

- [ ] **Step 1.4: Add per-invoice short_pay test (single invoice)**

Append:

```js
test('emits short_pay when TAB BANK collected_amount less than AR balance owed', () => {
  // AR has invoice LM26040200F with $1802.56 amount, $0 paid (so owed=$1802.56)
  // TAB BANK has one row paying $1767.56 toward that invoice. Shortage $35.
  const tab_bank = { rows: [
    tbRow('A0906038795', 1767.56, 'TOP TRANS INTERNATIONAL INC', null,
      { invoice: 'LM26040200F', collected_amount: 1767.56 }),
  ] };
  const qbo_collection = { rows: [
    qboRow('Invoice', 'A0906038795', '5678-TOP TRANS INTERNATIONAL INC', 1802.56, 'LM26040200F'),
  ] };
  const ar_register = [arRow('LM26040200F', 1802.56, 0)];
  const result = detectTabBankExceptions(tab_bank, qbo_collection, ar_register);
  const sp = result.exceptions.filter(e => e.kind === 'short_pay');
  assert.equal(sp.length, 1);
  assert.equal(sp[0].check_no, 'A0906038795');
  assert.equal(Math.round(sp[0].shortage * 100) / 100, 35);
  assert.equal(sp[0].tb_collected, 1767.56);
  assert.equal(sp[0].ar_balance_owed, 1802.56);
  assert.deepEqual(sp[0].affected_invs, ['LM26040200F']);
});
```

- [ ] **Step 1.5: Add per-invoice short_pay test (multi-invoice from one check#)**

Append:

```js
test('short_pay reads per-row collected_amount across multiple invoices for same check#', () => {
  // Check 208064 split across 7 rows in TAB BANK (one per invoice).
  // Each invoice owed $441; each row collected $336; each is $105 short.
  const tab_bank = { rows: [
    tbRow('208064', 336, 'IDC LOGISTICS', null, { invoice: 'LM26040682F', collected_amount: 336 }),
    tbRow('208064', 336, 'IDC LOGISTICS', null, { invoice: 'LM26040683F', collected_amount: 336 }),
    tbRow('208064', 336, 'IDC LOGISTICS', null, { invoice: 'LM26040684F', collected_amount: 336 }),
  ] };
  const qbo_collection = { rows: [
    qboRow('Invoice', '208064', '1234-IDC LOGISTICS', 441, 'LM26040682F'),
    qboRow('Invoice', '208064', '1234-IDC LOGISTICS', 441, 'LM26040683F'),
    qboRow('Invoice', '208064', '1234-IDC LOGISTICS', 441, 'LM26040684F'),
  ] };
  const ar_register = [
    arRow('LM26040682F', 441, 0),
    arRow('LM26040683F', 441, 0),
    arRow('LM26040684F', 441, 0),
  ];
  const result = detectTabBankExceptions(tab_bank, qbo_collection, ar_register);
  const sp = result.exceptions.filter(e => e.kind === 'short_pay');
  assert.equal(sp.length, 3, 'one short_pay per invoice');
  for (const e of sp) {
    assert.equal(Math.round(e.shortage * 100) / 100, 105);
    assert.equal(e.tb_collected, 336);
  }
});
```

- [ ] **Step 1.6: Add per-invoice overpay test**

Append:

```js
test('emits overpay when TAB BANK collected_amount exceeds AR balance owed', () => {
  // AR has invoice owing $441; TAB BANK collected $476. Overpaid $35.
  const tab_bank = { rows: [
    tbRow('OP01', 476, 'OVERPAY CUST', null, { invoice: 'INV-X', collected_amount: 476 }),
  ] };
  const qbo_collection = { rows: [] };
  const ar_register = [arRow('INV-X', 441, 0)];
  const result = detectTabBankExceptions(tab_bank, qbo_collection, ar_register);
  const op = result.exceptions.filter(e => e.kind === 'overpay');
  assert.equal(op.length, 1);
  assert.equal(Math.round(op[0].overage * 100) / 100, 35);
  assert.equal(op[0].tb_collected, 476);
});
```

- [ ] **Step 1.7: Add tbAppliedByInv map structure test**

Append:

```js
test('returns tbAppliedByInv map keyed by invoice with sum_collected + check_nos', () => {
  // Same invoice has TWO TAB BANK rows from different days (or same day) — should sum.
  const tab_bank = { rows: [
    tbRow('CK1', 100, 'A', null, { invoice: 'INV-1', collected_amount: 100 }),
    tbRow('CK2', 50,  'A', null, { invoice: 'INV-1', collected_amount: 50 }),
    tbRow('CK3', 200, 'A', null, { invoice: 'INV-2', collected_amount: 200 }),
  ] };
  const result = detectTabBankExceptions(tab_bank, { rows: [] });
  assert.ok(result.tbAppliedByInv instanceof Map);
  const inv1 = result.tbAppliedByInv.get('INV-1');
  assert.equal(inv1.sum_collected, 150);
  assert.deepEqual(inv1.check_nos.sort(), ['CK1', 'CK2']);
  const inv2 = result.tbAppliedByInv.get('INV-2');
  assert.equal(inv2.sum_collected, 200);
});
```

- [ ] **Step 1.8: Add MVP posting-gap fix test (sum collected_amount, not amount)**

Append:

```js
test('posting_gap_qbo_missing sums collected_amount across repeated rows (not amount)', () => {
  // 6 TAB BANK rows for same check#, repeating `amount` 4 times. collected_amount nets to $2023.
  // QBO has nothing → posting_gap_qbo_missing with amount = $2023 (NOT $8092).
  const tab_bank = { rows: [
    tbRow('CK99', 2023, 'REAL CUST', null, { invoice: 'I1', collected_amount: 2023 }),
    tbRow('CK99', 0,    'REAL CUST', null, { invoice: 'I1', collected_amount: -2023 }),
    tbRow('CK99', 0,    'REAL CUST', null, { invoice: 'I1', collected_amount: 0 }),
    tbRow('CK99', 2023, 'REAL CUST', null, { invoice: 'I1', collected_amount: 2023 }),
    tbRow('CK99', 0,    'REAL CUST', null, { invoice: 'I1', collected_amount: 0 }),
    tbRow('CK99', 2023, 'REAL CUST', null, { invoice: 'I1', collected_amount: 0 }),
  ] };
  const result = detectTabBankExceptions(tab_bank, { rows: [] });
  const gap = result.exceptions.find(e => e.kind === 'posting_gap_qbo_missing');
  assert.ok(gap, 'should emit posting_gap_qbo_missing');
  assert.equal(gap.amount, 2023, 'amount should be summed collected_amount, not sum of amount column');
});
```

- [ ] **Step 1.9: Run tests — expect failures**

Run: `node tools/test_tab_bank_exceptions.mjs`
Expected: many failures with messages about `.exceptions is undefined` (because the function still returns an array, not an object) and `tab_bank_suspense_row` / `short_pay` / `overpay` not appearing. Exit code 1.

- [ ] **Step 1.10: Commit**

```bash
git add tools/test_tab_bank_exceptions.mjs
git commit -m "test(ar-dashboard): expand tests for short-pay + SUSPENSE + tbAppliedByInv (failing)

Existing 10 tests updated to read .exceptions off the new return shape.
Adds 7 new tests covering tab_bank_suspense_row emission per SUSPENSE row,
single-invoice short_pay, multi-invoice short_pay per row, overpay,
tbAppliedByInv map structure, and the MVP posting-gap fix to sum
collected_amount instead of amount.

Failing until detectTabBankExceptions refactor lands in Task 2."
```

---

## Task 2: Refactor `detectTabBankExceptions` — new return shape + per-invoice detection

**Files:**
- Modify: `app/assets/js/tools/ar-dashboard/ar-dashboard-build.js`

Change the return type to `{exceptions, tbAppliedByInv, tbByCheck, tbByInvoice}`. Add SUSPENSE row surfacing. Add per-invoice short_pay/overpay detection (consumes optional `ar_register` arg). Fix the MVP posting-gap math.

- [ ] **Step 2.1: Replace the entire `detectTabBankExceptions` function**

Find the existing function (between the `// TAB BANK exception detection (spec 2026-06-09)` divider and the `// Browser console hook` divider). Replace its entire body with:

```js
export function detectTabBankExceptions(tab_bank, qbo_collection, ar_register = null) {
  const exceptions = [];
  const tbByCheck = new Map();
  const tbByInvoice = new Map();
  const tbAppliedByInv = new Map();

  if (!tab_bank || !tab_bank.rows || !qbo_collection || !qbo_collection.rows) {
    return { exceptions, tbAppliedByInv, tbByCheck, tbByInvoice };
  }

  // ---- Partition TAB BANK rows: real | suspense | non-factored ----
  const tbRows = [];
  const suspenseRows = [];
  let nonFactoredCount = 0;
  for (const r of tab_bank.rows) {
    if (!r || r.check == null || r.check === '') continue;
    const debtor = (r.debtor_name || '').toString().trim().toUpperCase();
    if (r.desc === 'NON-FACTORED') { nonFactoredCount++; continue; }
    if (debtor === 'SUSPENSE') { suspenseRows.push(r); continue; }
    tbRows.push(r);
  }

  // ---- Build indexes from real (non-SUSPENSE, non-NON-FACTORED) rows ----
  for (const r of tbRows) {
    const ckey = String(r.check);
    if (!tbByCheck.has(ckey)) tbByCheck.set(ckey, []);
    tbByCheck.get(ckey).push(r);

    if (r.invoice != null && r.invoice !== '') {
      const ikey = String(r.invoice).trim();
      if (!tbByInvoice.has(ikey)) tbByInvoice.set(ikey, []);
      tbByInvoice.get(ikey).push(r);
    }
  }

  // ---- Build tbAppliedByInv (sum collected_amount per invoice) ----
  for (const [inv, rows] of tbByInvoice) {
    const sum_collected = rows.reduce((s, r) => s + num(r.collected_amount), 0);
    const check_nos = [...new Set(rows.map(r => r.check).filter(v => v != null && v !== '').map(String))];
    tbAppliedByInv.set(inv, { sum_collected, check_nos, rows });
  }

  // ---- QBO Collection index ----
  const qboIndex = new Map();
  for (const line of qbo_collection.rows) {
    if (!line || line.txn_type !== 'Invoice') continue;
    if (line.txn_number == null || line.txn_number === '') continue;
    const key = String(line.txn_number);
    if (!qboIndex.has(key)) qboIndex.set(key, []);
    qboIndex.get(key).push(line);
  }

  // ---- All-NON-FACTORED guardrail (spec §8 risk mitigation) ----
  if (tbByCheck.size === 0 && nonFactoredCount > 0 && qboIndex.size > 0) {
    exceptions.push({
      kind: 'info_all_non_factored',
      check_no: '',
      message: 'TAB BANK file appears to contain only NON-FACTORED rows — gap detection skipped',
      amount: null,
      tab_bank_row: null, qbo_row: null,
      tab_bank_customer: null, qbo_customer: null,
    });
    return { exceptions, tbAppliedByInv, tbByCheck, tbByInvoice };
  }

  // ---- Posting gap: TAB BANK has check# but QBO does not (sum collected_amount) ----
  for (const [checkNo, rows] of tbByCheck) {
    if (qboIndex.has(checkNo)) continue;
    const totalCollected = rows.reduce((s, r) => s + num(r.collected_amount), 0);
    if (Math.abs(totalCollected) < 0.01) continue; // net-zero, no real gap
    const firstRow = rows[0];
    exceptions.push({
      kind: 'posting_gap_qbo_missing',
      check_no: checkNo,
      message: `Bank received $${totalCollected.toFixed(2)} on check# ${checkNo} from ${firstRow.debtor_name || '(no customer)'} but no QBO payment recorded`,
      amount: totalCollected,
      tab_bank_row: firstRow,
      qbo_row: null,
      tab_bank_customer: firstRow.debtor_name || null,
      qbo_customer: null,
    });
  }

  // ---- Posting gap: QBO has check# but TAB BANK does not ----
  for (const [checkNo, qboLines] of qboIndex) {
    if (tbByCheck.has(checkNo)) continue;
    const firstLine = qboLines[0];
    const total = qboLines.reduce((s, l) => s + num(l.amount), 0);
    exceptions.push({
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
  for (const [checkNo, tbRowsForCheck] of tbByCheck) {
    const qboLines = qboIndex.get(checkNo);
    if (!qboLines || qboLines.length === 0) continue;
    const tbName = (tbRowsForCheck.find(r => r.debtor_name) || {}).debtor_name || '';
    const qboName = qboLines[0].customer || '';
    const nTb = normalizeCustomerName(tbName);
    const nQbo = normalizeCustomerName(qboName);
    if (nTb && nQbo && nTb !== nQbo) {
      const totalCollected = tbRowsForCheck.reduce((s, r) => s + num(r.collected_amount), 0);
      exceptions.push({
        kind: 'customer_mismatch',
        check_no: checkNo,
        message: `Check# ${checkNo} — TAB BANK says ${tbName}, QBO says ${qboName}`,
        amount: totalCollected,
        tab_bank_row: tbRowsForCheck[0],
        qbo_row: qboLines[0],
        tab_bank_customer: tbName,
        qbo_customer: qboName,
      });
    }
  }

  // ---- Per-invoice short_pay / overpay (requires ar_register) ----
  if (ar_register) {
    for (const arRow of ar_register) {
      if (!arRow || !arRow.inv) continue;
      const inv = String(arRow.inv).trim();
      const tb = tbAppliedByInv.get(inv);
      if (!tb || Math.abs(tb.sum_collected) < 0.01) continue;
      const owed = num(arRow.amount) - num(arRow.paid);
      const collected = tb.sum_collected;
      const diff = owed - collected; // positive = short; negative = over
      if (diff > 0.01) {
        exceptions.push({
          kind: 'short_pay',
          check_no: tb.check_nos.join(', '),
          message: `${inv} short-paid by $${diff.toFixed(2)} — TAB BANK applied $${collected.toFixed(2)}, balance owed $${owed.toFixed(2)}`,
          amount: diff,
          shortage: diff,
          tb_collected: collected,
          ar_balance_owed: owed,
          affected_invs: [inv],
          tab_bank_row: tb.rows[0],
          qbo_row: null,
          tab_bank_customer: tb.rows[0].debtor_name || null,
          qbo_customer: arRow.company || null,
        });
      } else if (diff < -0.01) {
        exceptions.push({
          kind: 'overpay',
          check_no: tb.check_nos.join(', '),
          message: `${inv} OVERPAID by $${(-diff).toFixed(2)} — TAB BANK applied $${collected.toFixed(2)}, balance owed $${owed.toFixed(2)}`,
          amount: -diff,
          overage: -diff,
          tb_collected: collected,
          ar_balance_owed: owed,
          affected_invs: [inv],
          tab_bank_row: tb.rows[0],
          qbo_row: null,
          tab_bank_customer: tb.rows[0].debtor_name || null,
          qbo_customer: arRow.company || null,
        });
      }
    }
  }

  // ---- SUSPENSE row exceptions (one per row) ----
  for (const r of suspenseRows) {
    exceptions.push({
      kind: 'tab_bank_suspense_row',
      check_no: String(r.check || ''),
      message: `Check# ${r.check} for $${num(r.amount).toFixed(2)} marked SUSPENSE in TAB BANK — customer not identified by bank. Investigate which customer/invoice this belongs to.`,
      amount: num(r.amount),
      tb_collected: num(r.collected_amount),
      affected_invs: r.invoice ? [String(r.invoice)] : [],
      tab_bank_row: r,
      qbo_row: null,
      tab_bank_customer: 'SUSPENSE',
      qbo_customer: null,
    });
  }

  // ---- Sort: short_pay > overpay > qbo_missing > tab_missing > suspense > customer_mismatch > info ----
  const kindOrder = {
    short_pay: 0,
    overpay: 1,
    posting_gap_qbo_missing: 2,
    posting_gap_tab_missing: 3,
    tab_bank_suspense_row: 4,
    customer_mismatch: 5,
    info_all_non_factored: 6,
  };
  exceptions.sort((a, b) => (kindOrder[a.kind] ?? 99) - (kindOrder[b.kind] ?? 99));

  return { exceptions, tbAppliedByInv, tbByCheck, tbByInvoice };
}
```

- [ ] **Step 2.2: Run tests — expect PASS**

Run: `node tools/test_tab_bank_exceptions.mjs`
Expected: all 17 tests pass (10 original updated + 7 new). Exit code 0.

If anything fails, fix incrementally — don't skip ahead.

- [ ] **Step 2.3: Verify whole-tree JS syntax**

Run: `node desktop/check-js.js`
Expected: `===CHECK_JS_OK===`.

- [ ] **Step 2.4: Commit**

```bash
git add app/assets/js/tools/ar-dashboard/ar-dashboard-build.js
git commit -m "feat(ar-dashboard): detectTabBankExceptions returns {exceptions, tbAppliedByInv, indexes}

Switches to per-invoice TAB BANK truth via collected_amount. Adds three
new exception kinds: short_pay, overpay, tab_bank_suspense_row. Surfaces
each SUSPENSE row individually rather than filtering silently.

MVP fix: posting_gap_qbo_missing now sums collected_amount (net-balances
correctly when SUSPENSE rows repeat the amount column).

Optional ar_register arg drives per-invoice short_pay/overpay detection.
When omitted (MVP test scenarios), only the check#-level detections run.

All 17 unit tests pass."
```

---

## Task 3: Wire `arBuildToday` to consume `tbAppliedByInv` in Phase 2

**Files:**
- Modify: `app/assets/js/tools/ar-dashboard/ar-dashboard-build.js`

Move the TAB BANK analysis to BEFORE Phase 2. Phase 2 prefers TAB BANK's per-invoice `collected_amount` when present; falls back to QBO Collection arithmetic otherwise. Add a second pass that walks `tbAppliedByInv.keys()` to catch invoices that have TAB BANK activity but no QBO Collection match.

- [ ] **Step 3.1: Replace Phase 1 → Phase 2 region in `arBuildToday`**

Find this block in `arBuildToday` (currently lines 92–128 roughly — the Phase 1 clone + Phase 2 collections):

```js
  // ----- Phase 1 — clone yesterday's AR register
  // keyed by inv# so phases 2/3/5 can mutate in place
  const todayAr = new Map();
  for (const row of yesterday.ar_register) {
    if (row.inv) todayAr.set(row.inv, { ...row });
  }

  // ----- Phase 2 — apply collections (QBO arithmetic only)
  const collectionByInvoice = new Map();
  for (const line of qbo_collection.rows) {
    if (line.txn_type !== 'Invoice') continue;
    const inv = line.invoice_or_ref;
    if (!inv) continue;
    if (!collectionByInvoice.has(inv)) collectionByInvoice.set(inv, []);
    collectionByInvoice.get(inv).push(line);
  }

  const todayStr = fmtTodayStr(target_date);

  for (const [inv, lines] of collectionByInvoice) {
    const row = todayAr.get(inv);
    if (!row) continue;
    let totalApplied = 0;
    for (const l of lines) totalApplied += num(l.amount);
    const newPaid = num(row.paid) + totalApplied;
    const newBalance = num(row.amount) - newPaid;
    if (Math.abs(newBalance) < AMT_EPS || newBalance < -AMT_EPS) {
      todayAr.delete(inv);
      continue;
    }
    const checkNo = lines[0].txn_number;
    row.paid = newPaid;
    row.balance = newBalance;
    if (!row.memo) {
      row.memo = `Short paid ${todayStr} #${checkNo}`;
    }
  }
```

Replace with:

```js
  // ----- Phase 1 — clone yesterday's AR register
  // keyed by inv# so phases 2/3/5 can mutate in place
  const todayAr = new Map();
  for (const row of yesterday.ar_register) {
    if (row.inv) todayAr.set(row.inv, { ...row });
  }

  // ----- Phase 1b — analyze TAB BANK (spec 2026-06-09 short-pay)
  // We need tbAppliedByInv BEFORE Phase 2 so Phase 2 can prefer TAB BANK
  // collected_amount over QBO Collection amount.
  const tabBankAnalysis = tab_bank
    ? detectTabBankExceptions(tab_bank, qbo_collection, yesterday.ar_register)
    : { exceptions: [], tbAppliedByInv: new Map(), tbByCheck: new Map(), tbByInvoice: new Map() };
  const tabBankExceptions = tabBankAnalysis.exceptions;
  const tbAppliedByInv = tabBankAnalysis.tbAppliedByInv;

  // ----- Phase 2 — apply collections (TAB BANK preferred, QBO fallback)
  const collectionByInvoice = new Map();
  for (const line of qbo_collection.rows) {
    if (line.txn_type !== 'Invoice') continue;
    const inv = line.invoice_or_ref;
    if (!inv) continue;
    if (!collectionByInvoice.has(inv)) collectionByInvoice.set(inv, []);
    collectionByInvoice.get(inv).push(line);
  }

  const todayStr = fmtTodayStr(target_date);
  const processedInvoices = new Set();

  // Pass 1: walk QBO-keyed invoices; prefer TAB BANK collected_amount when present.
  for (const [inv, lines] of collectionByInvoice) {
    const row = todayAr.get(inv);
    if (!row) continue;
    const tb = tbAppliedByInv.get(inv);
    const totalApplied = (tb && Math.abs(tb.sum_collected) > 0.01)
      ? tb.sum_collected
      : lines.reduce((s, l) => s + num(l.amount), 0);
    const checkNo = (tb && tb.check_nos.length > 0)
      ? tb.check_nos[0]
      : lines[0].txn_number;
    const newPaid = num(row.paid) + totalApplied;
    const newBalance = num(row.amount) - newPaid;
    processedInvoices.add(inv);
    if (Math.abs(newBalance) < AMT_EPS || newBalance < -AMT_EPS) {
      todayAr.delete(inv);
      continue;
    }
    row.paid = newPaid;
    row.balance = newBalance;
    if (!row.memo) {
      row.memo = `Short paid ${todayStr} #${checkNo}`;
    }
  }

  // Pass 2: walk TAB-BANK-only invoices (have TAB BANK activity but no QBO post).
  // Without this pass, money the bank actually received but QBO hasn't logged yet
  // would never reduce the AR balance.
  for (const [inv, tb] of tbAppliedByInv) {
    if (processedInvoices.has(inv)) continue;
    const row = todayAr.get(inv);
    if (!row) continue;
    if (Math.abs(tb.sum_collected) < 0.01) continue;
    const newPaid = num(row.paid) + tb.sum_collected;
    const newBalance = num(row.amount) - newPaid;
    const checkNo = tb.check_nos[0] || '';
    if (Math.abs(newBalance) < AMT_EPS || newBalance < -AMT_EPS) {
      todayAr.delete(inv);
      continue;
    }
    row.paid = newPaid;
    row.balance = newBalance;
    if (!row.memo) {
      row.memo = `Short paid ${todayStr} #${checkNo}`;
    }
  }
```

- [ ] **Step 3.2: Remove the old Phase 5b block (it's now in Phase 1b)**

Find this block further down in `arBuildToday`:

```js
  // ----- Phase 5b — TAB BANK exceptions (spec 2026-06-09)
  const tabBankExceptions = tab_bank
    ? detectTabBankExceptions(tab_bank, qbo_collection)
    : [];
```

Replace with just a comment (the work moved to Phase 1b):

```js
  // ----- Phase 5b — (moved to Phase 1b per spec 2026-06-09 short-pay)
```

- [ ] **Step 3.3: Verify `tab_bank_exceptions` still on the return**

Confirm the return object still has `tab_bank_exceptions: tabBankExceptions` — no change needed to the return statement (it references the same variable name, just now assigned in Phase 1b instead of Phase 5b).

- [ ] **Step 3.4: Verify syntax**

Run: `node desktop/check-js.js`
Expected: `===CHECK_JS_OK===`.

- [ ] **Step 3.5: Re-run unit tests (no regression)**

Run: `node tools/test_tab_bank_exceptions.mjs`
Expected: all 17 still pass.

- [ ] **Step 3.6: Re-run baseline verify harness — no engine regression on existing build days**

Run: `node tools/verify_ar_build_js.mjs`
Expected: baseline parity ≈ same as before this slice (the pre-existing 5/12 and 5/13 drift we flagged in the MVP is unchanged). The new Phase 2 behavior is a STRICT superset — for any invoice without a TAB BANK row, the QBO fallback gives identical results to the old code. The baseline workbooks may show NEW behavior on some 5/19 rows (which has TAB BANK data); investigate any DEEPER regression beyond the pre-existing 5/12 / 5/13 gaps.

If a build day regresses by MORE than 0.10pp BEYOND its pre-existing baseline gap → STOP and investigate before committing.

- [ ] **Step 3.7: Commit**

```bash
git add app/assets/js/tools/ar-dashboard/ar-dashboard-build.js
git commit -m "feat(ar-dashboard): Phase 2 prefers TAB BANK collected_amount over QBO

Moves TAB BANK analysis to Phase 1b so Phase 2 can consume
tbAppliedByInv. For each invoice, Phase 2 uses TAB BANK's per-row
collected_amount when present (the source of truth for what actually
got deposited), falling back to QBO Collection amount only when the
invoice has no TAB BANK row.

Adds a second Phase 2 pass over TAB-BANK-only invoices so that bank
deposits not yet posted in QBO still reduce the AR balance.

This recovers the 8 short-paid invoices that dropped off today's
program output vs Jihyun's hand-built 06/08 workbook."
```

---

## Task 4: Update preview modal — new kinds + extended table

**Files:**
- Modify: `app/assets/js/tools/ar-dashboard/ar-dashboard-build-ui.js`

Add new exception kinds to the label helper and extend the detail table to show short-pay context columns (TAB BANK $, QBO Balance Owed, Affected INV#).

- [ ] **Step 4.1: Extend `tabBankExceptionLabel` helper**

Find the existing helper:

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

Replace with:

```js
function tabBankExceptionLabel(kind) {
  switch (kind) {
    case 'short_pay':               return 'Short pay (bank deposit < owed)';
    case 'overpay':                 return 'Overpay (bank deposit > owed)';
    case 'posting_gap_qbo_missing': return 'Bank deposit, no QBO post';
    case 'posting_gap_tab_missing': return 'QBO post, no bank record';
    case 'tab_bank_suspense_row':   return 'TAB BANK SUSPENSE (unidentified customer)';
    case 'customer_mismatch':       return 'Customer name mismatch';
    case 'info_all_non_factored':   return 'TAB BANK file all NON-FACTORED';
    default: return kind;
  }
}
```

- [ ] **Step 4.2: Extend `renderKpiDetail('exception')` table**

Find the existing exception detail panel (in `renderKpiDetail`):

```js
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
```

Replace with:

```js
    const rows = data.exceptions.slice(0, 50);
    return `
      <div class="ar-detail-panel">
        <div class="dp-head">
          <div class="dp-title">TAB BANK mismatches</div>
          <div class="dp-sub">${data.exceptions.length} rows · sorted by urgency · review before saving</div>
        </div>
        ${rowsTable(
          ['Issue', 'Check #', 'Amount', 'TAB BANK $', 'Owed', 'Affected INV#', 'TAB BANK Customer', 'QBO Customer'],
          rows.map(e => [
            tabBankExceptionLabel(e.kind),
            e.check_no,
            e.amount != null ? fmtMoney(e.amount) : '—',
            e.tb_collected != null ? fmtMoney(e.tb_collected) : '—',
            e.ar_balance_owed != null ? fmtMoney(e.ar_balance_owed) : '—',
            (e.affected_invs && e.affected_invs.length > 0) ? e.affected_invs.join(', ') : '—',
            e.tab_bank_customer || '—',
            e.qbo_customer || '—',
          ]),
          data.exceptions.length,
          ['', 'mono', 'num', 'num', 'num', 'mono', '', ''],
        )}
      </div>`;
```

- [ ] **Step 4.3: Verify syntax**

Run: `node desktop/check-js.js`
Expected: `===CHECK_JS_OK===`.

- [ ] **Step 4.4: Commit**

```bash
git add app/assets/js/tools/ar-dashboard/ar-dashboard-build-ui.js
git commit -m "feat(ar-dashboard): preview modal table for short_pay / overpay / SUSPENSE

Detail panel grows three columns: TAB BANK \$, Owed, Affected INV#.
Label helper covers the three new exception kinds. Sort order from
the engine is preserved (short_pay first, then overpay, etc.)."
```

---

## Task 5: Update writer EXCEPTIONS sheet — new columns

**Files:**
- Modify: `app/assets/js/tools/ar-dashboard/ar-dashboard-build-writer.js`

Extend `EXCEPTIONS_HEADERS` / `EXCEPTIONS_WIDTHS` with TAB BANK $, Owed, Affected INV#. Populate from the new exception fields. Add new kinds to `kindLabel`.

- [ ] **Step 5.1: Update header + width constants**

Find the existing constants:

```js
const EXCEPTIONS_HEADERS = [
  'Kind', 'Check #', 'Amount', 'TAB BANK Customer', 'QBO Customer', 'Detected Issue', 'Notes',
];
const EXCEPTIONS_WIDTHS = [28, 12, 12, 28, 28, 60, 30];
```

Replace with:

```js
const EXCEPTIONS_HEADERS = [
  'Kind', 'Check #', 'Amount', 'TAB BANK $', 'Owed', 'Affected INV#',
  'TAB BANK Customer', 'QBO Customer', 'Detected Issue', 'Notes',
];
const EXCEPTIONS_WIDTHS = [32, 14, 14, 14, 14, 30, 28, 28, 60, 30];
```

- [ ] **Step 5.2: Update `buildExceptionsWorksheet` body**

Find the existing function. Replace the entire function with:

```js
function buildExceptionsWorksheet(wb, exceptions) {
  const ws = wb.addWorksheet('EXCEPTIONS', { views: [{ state: 'frozen', ySplit: 1 }] });
  setColWidths(ws, EXCEPTIONS_WIDTHS);
  const header = ws.addRow(EXCEPTIONS_HEADERS);
  styleRowAll(header, 17, FONT_BODY_BOLD, ALIGN_CENTER, BORDER_ALL);
  const kindLabel = (kind) => {
    switch (kind) {
      case 'short_pay':               return 'Short pay (bank deposit < owed)';
      case 'overpay':                 return 'Overpay (bank deposit > owed)';
      case 'posting_gap_qbo_missing': return 'Bank deposit, no QBO post';
      case 'posting_gap_tab_missing': return 'QBO post, no bank record';
      case 'tab_bank_suspense_row':   return 'TAB BANK SUSPENSE (unidentified customer)';
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
      e.tb_collected != null ? e.tb_collected : '',
      e.ar_balance_owed != null ? e.ar_balance_owed : '',
      (e.affected_invs && e.affected_invs.length > 0) ? e.affected_invs.join(', ') : '',
      e.tab_bank_customer || '',
      e.qbo_customer || '',
      e.message || '',
      '', // Notes — Jihyun fills this in
    ]);
    styleRowAll(row, 17, FONT_BODY, ALIGN_CENTER, null);
    row.getCell(6).alignment = ALIGN_LEFT;  // Affected INV#
    row.getCell(7).alignment = ALIGN_LEFT;  // TAB BANK Customer
    row.getCell(8).alignment = ALIGN_LEFT;  // QBO Customer
    row.getCell(9).alignment = ALIGN_LEFT;  // Detected Issue
    row.getCell(10).alignment = ALIGN_LEFT; // Notes
    row.getCell(3).numFmt = NF_MONEY;       // Amount
    row.getCell(4).numFmt = NF_MONEY;       // TAB BANK $
    row.getCell(5).numFmt = NF_MONEY;       // Owed
  }
  ws.autoFilter = {
    from: { row: 1, column: 1 },
    to: { row: Math.max(exceptions.length, 0) + 1, column: 10 },
  };
  return ws;
}
```

- [ ] **Step 5.3: Verify syntax**

Run: `node desktop/check-js.js`
Expected: `===CHECK_JS_OK===`.

- [ ] **Step 5.4: Commit**

```bash
git add app/assets/js/tools/ar-dashboard/ar-dashboard-build-writer.js
git commit -m "feat(ar-dashboard): EXCEPTIONS sheet adds TAB BANK \$ / Owed / Affected INV# cols

Three new columns appear between Amount and TAB BANK Customer, sourced
from short_pay / overpay exception fields. Other exception kinds leave
the new columns blank. Standard NF_MONEY formatting for the three
money columns. Adds short_pay / overpay / tab_bank_suspense_row to
the kindLabel mapping."
```

---

## Task 6: Bump VERSION to 2.79.1

**Files:**
- Modify: `desktop/VERSION`

- [ ] **Step 6.1: Edit VERSION**

Replace the single line of `desktop/VERSION` with:

```
2.79.1
```

- [ ] **Step 6.2: Commit**

```bash
git add desktop/VERSION
git commit -m "chore(release): bump VERSION to 2.79.1 — TAB BANK short-pay detection"
```

---

## Task 7: Local build (no push, no GH release)

Owner-only preview per spec §9. Coworkers stay on v2.78.8 until smoke + Jihyun's next-morning sign-off.

- [ ] **Step 7.1: Kick the build**

Run via PowerShell:

```powershell
$root = "C:\Users\Joseph\Desktop\NGL ACCOUNTING SERVICE"
if (-not (Test-Path "$root\empty_stdin.txt")) { New-Item -ItemType File -Path "$root\empty_stdin.txt" -Force | Out-Null }
Remove-Item -Path "$root\build.log","$root\build.err" -ErrorAction SilentlyContinue
Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "`"$root\desktop\runbuild.bat`"" -WorkingDirectory $root -RedirectStandardInput "$root\empty_stdin.txt" -RedirectStandardOutput "$root\build.log" -RedirectStandardError "$root\build.err" -NoNewWindow -PassThru
```

Wait for `===ELECTRON_BUILD_DONE===` in `build.log`.

- [ ] **Step 7.2: Verify artifacts**

Run:

```bash
ls -la "desktop/dist/NGL_ACCOUNTING_INSTALLER_v2.79.1.exe" "desktop/dist/latest.yml" "desktop/dist/win-unpacked/NGL Accounting.exe"
```

Expected: all three present, sizes > 0, timestamps within the last 10 minutes.

- [ ] **Step 7.3: Do NOT push.**
- [ ] **Step 7.4: Do NOT create a GH release.**

---

## Task 8: Verify against 06/08/2026 program-vs-manual data

The 8 missing-invoice gap from today's earlier session is THE acceptance criterion. This task re-runs the program build with v2.79.1 against the same inputs and confirms the gap closes.

- [ ] **Step 8.1: Build today's workbook via the packaged app**

(USER): launch v2.79.1 from desktop shortcut, drop the 5 input files for 06/08/2026, click Run build → Save. Save to a fresh folder to avoid clobbering the v2.79.0 program file we already have.

- [ ] **Step 8.2: Copy the new program file into PROGRAMvsManual/**

Replace the existing `PROGRAMvsManual/AR_AGING_06_08_2026.xlsx` with the new one. Keep `AR_AGING_06_08_2026_manual.xlsx` (Jihyun's hand-built) unchanged.

- [ ] **Step 8.3: Re-run comparison**

Run: `agent/venv/Scripts/python.exe tools/compare_program_vs_manual.py`

Expected output highlights:
- **`In MANUAL only` count drops from 10 to 2** (the 2 remaining are the overpayment credits LM26060374F / LM26060375F — out of scope for this slice)
- **The 8 short-pay invoices now appear in PROGRAM AR_today with these EXACT balances:**
  - LM26040200F → $35
  - LM26040682F → $105
  - LM26040683F → $105
  - LM26040684F → $105
  - LM26040685F → $105
  - LM26040686F → $105
  - LM26040688F → $105
  - PE26050012F → $40
- **Each has the memo** `Short paid 06/08/2026 #<checkno>` matching Jihyun's
- **AR_today cell-level match jumps from 86.07% to ≥99.5%**
- **EXCEPTIONS sheet contains** `short_pay` rows for the three check#s (208064, A0906038795, 18813), plus any `overpay` and `tab_bank_suspense_row` rows the engine emitted

If any of the 8 balances is off by more than $0.01 → STOP and investigate before T9.

- [ ] **Step 8.4: Re-run baseline verify harness**

Run: `node tools/verify_ar_build_js.mjs`

Expected: the pre-existing 5/12 and 5/13 drift remains; no NEW build days regress beyond their pre-existing baselines. If a new build day regresses → investigate before T9.

- [ ] **Step 8.5: Commit the updated PROGRAMvsManual files (optional)**

If you want the v2.79.1 program file checked in for future regression reference:

```bash
git add PROGRAMvsManual/AR_AGING_06_08_2026.xlsx
git commit -m "test(ar-dashboard): v2.79.1 program-vs-manual reference for 06/08/2026"
```

(Or skip if these files should stay out of git.)

---

## Task 9: Manual smoke test (USER) + Jihyun's next-morning build

- [ ] **Step 9.1: Launch v2.79.1**

Close any running copy of the app. Launch from desktop shortcut. Confirm title bar reads **v2.79.1**.

- [ ] **Step 9.2: Run today's morning AR build with Jihyun**

Use the actual TAB BANK + QBO + TMS files from tomorrow morning's batch. Click Run build → review the preview modal. Confirm:
- "Suspense / exceptions" KPI tile shows a real count
- Clicking the tile shows the detail table with short_pay / overpay / SUSPENSE rows (if any)
- Sort order has short_pay first, then overpay, then posting gaps

- [ ] **Step 9.3: Save the workbook, open it, compare to Jihyun's manual build**

Open the saved `AR_AGING_*.xlsx`. Confirm:
- Sheet order: `AR_<today>` · `COL` · `COL (INV)` · `Schedule` · `TMS` · `ADJUSTMENT` · `EXCEPTIONS` · `AR_<yesterday>`
- EXCEPTIONS sheet has the new columns (Kind / Check # / Amount / TAB BANK $ / Owed / Affected INV# / TAB BANK Customer / QBO Customer / Detected Issue / Notes)
- Short pays that Jihyun would have flagged manually now appear in PROGRAM AR with the same balances + memos

- [ ] **Step 9.4: Jihyun sign-off**

Show Jihyun the program-built workbook. If she signs off ("this matches what I would have built by hand"), the slice is verified for production.

- [ ] **Step 9.5: Report back**

Tell Claude one of:
- "Smoke pass + Jihyun sign-off — ready to ship to coworkers" → Claude proceeds to push + GH release (bumping VERSION to 2.80.0 if you want a clean minor for the ship).
- "Smoke pass but want more verification before coworkers" → Claude pauses; we run more days.
- "Smoke fail: [what broke]" → Claude pauses and investigates.

---

## Acceptance criteria recap

1. ✅ All 17 unit tests pass (10 updated + 7 new for SUSPENSE / short_pay / overpay / tbAppliedByInv).
2. ✅ Baseline verify harness shows no NEW regressions (pre-existing 5/12 and 5/13 drift unchanged).
3. ✅ 06/08/2026 program build: 8 missing-invoice gap closes, AR_today match % ≥ 99.5%, all 8 short-pay balances + memos match Jihyun's exactly.
4. ✅ EXCEPTIONS sheet contains the new short_pay / overpay / tab_bank_suspense_row rows with TAB BANK $ / Owed / Affected INV# populated.
5. ✅ v2.79.1 builds locally; no push, no GH release until Jihyun signs off (Task 9).
6. ✅ Jihyun confirms the program-built workbook matches what she would have hand-built.
