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

function qboRow(txn_type, txn_number, customer, amount, invoice_or_ref = null) {
  return {
    payment_date: null, txn_type, txn_number, customer,
    invoice_or_ref, amount, open_balance: null, account: null,
  };
}

function arRow(inv, amount, paid = 0) {
  return { inv, amount, paid, balance: amount - paid, company: 'TEST CUST', memo: null };
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
  const result = detectTabBankExceptions(tab_bank, qbo_collection);
  assert.equal(result.exceptions.length, 1);
  assert.equal(result.exceptions[0].kind, 'posting_gap_qbo_missing');
  assert.equal(result.exceptions[0].check_no, '12345');
  assert.equal(result.exceptions[0].amount, 1200);
  assert.equal(result.exceptions[0].tab_bank_customer, 'ACME LOGISTICS');
});

test('emits posting_gap_tab_missing when QBO has check# TAB BANK does not', () => {
  const tab_bank = { rows: [] };
  const qbo_collection = { rows: [
    qboRow('Invoice', '67890', '1234-GLOBAL FREIGHT', 850, 'INV-1'),
  ] };
  const result = detectTabBankExceptions(tab_bank, qbo_collection);
  assert.equal(result.exceptions.length, 1);
  assert.equal(result.exceptions[0].kind, 'posting_gap_tab_missing');
  assert.equal(result.exceptions[0].check_no, '67890');
  assert.equal(result.exceptions[0].amount, 850);
  assert.equal(result.exceptions[0].qbo_customer, '1234-GLOBAL FREIGHT');
});

test('skips QBO rows where txn_type is not Invoice (header / sub-total rows)', () => {
  const tab_bank = { rows: [] };
  const qbo_collection = { rows: [
    qboRow('Payment', '99999', 'should-be-ignored', 0),
    qboRow(null, '88888', null, 0),
  ] };
  const result = detectTabBankExceptions(tab_bank, qbo_collection);
  assert.equal(result.exceptions.length, 0);
});

test('skips TAB BANK rows with desc = NON-FACTORED (informational)', () => {
  const tab_bank = { rows: [
    tbRow('22222', 500, 'NON-FACTORED CUST', 'NON-FACTORED'),
  ] };
  const qbo_collection = { rows: [] };
  const result = detectTabBankExceptions(tab_bank, qbo_collection);
  assert.equal(result.exceptions.length, 0);
});

test('skips TAB BANK rows with no check value', () => {
  const tab_bank = { rows: [tbRow(null, 100, 'NO CHECK')] };
  const qbo_collection = { rows: [] };
  const result = detectTabBankExceptions(tab_bank, qbo_collection);
  assert.equal(result.exceptions.length, 0);
});

test('emits customer_mismatch when normalized names differ', () => {
  const tab_bank = { rows: [tbRow('11111', 450, 'XYZ TRANSPORT')] };
  const qbo_collection = { rows: [
    qboRow('Invoice', '11111', '5678-ACME LOGISTICS', 450, 'INV-2'),
  ] };
  const result = detectTabBankExceptions(tab_bank, qbo_collection);
  assert.equal(result.exceptions.length, 1);
  assert.equal(result.exceptions[0].kind, 'customer_mismatch');
  assert.equal(result.exceptions[0].check_no, '11111');
  assert.equal(result.exceptions[0].tab_bank_customer, 'XYZ TRANSPORT');
  assert.equal(result.exceptions[0].qbo_customer, '5678-ACME LOGISTICS');
});

test('does NOT emit customer_mismatch when normalized names match (id-prefix stripped)', () => {
  const tab_bank = { rows: [tbRow('33333', 600, 'GLOBAL FREIGHT')] };
  const qbo_collection = { rows: [
    qboRow('Invoice', '33333', '1234-GLOBAL FREIGHT', 600, 'INV-3'),
  ] };
  const result = detectTabBankExceptions(tab_bank, qbo_collection);
  assert.equal(result.exceptions.length, 0, 'should be empty, got: ' + JSON.stringify(result.exceptions));
});

test('does NOT emit customer_mismatch when only entity suffix differs (INC vs LLC)', () => {
  const tab_bank = { rows: [tbRow('44444', 700, 'ACME LOGISTICS INC.')] };
  const qbo_collection = { rows: [
    qboRow('Invoice', '44444', '9999-ACME LOGISTICS LLC', 700, 'INV-4'),
  ] };
  const result = detectTabBankExceptions(tab_bank, qbo_collection);
  assert.equal(result.exceptions.length, 0, 'should be empty, got: ' + JSON.stringify(result.exceptions));
});

test('groups multiple QBO Invoice lines under same check# (one check pays many invoices)', () => {
  // Same check# 55555 applied to two invoices in QBO; TAB BANK has one matching row.
  const tab_bank = { rows: [tbRow('55555', 1500, 'BIG CUSTOMER')] };
  const qbo_collection = { rows: [
    qboRow('Invoice', '55555', '1111-BIG CUSTOMER', 800, 'INV-5a'),
    qboRow('Invoice', '55555', '1111-BIG CUSTOMER', 700, 'INV-5b'),
  ] };
  const result = detectTabBankExceptions(tab_bank, qbo_collection);
  assert.equal(result.exceptions.length, 0, 'matched check# + matched name = no exception');
});

test('all-NON-FACTORED TAB BANK file produces an info exception and skips gap detection', () => {
  const tab_bank = { rows: [
    tbRow('NF1', 100, 'NF CUST 1', 'NON-FACTORED'),
    tbRow('NF2', 200, 'NF CUST 2', 'NON-FACTORED'),
  ] };
  const qbo_collection = { rows: [
    qboRow('Invoice', '99999', '1234-REAL CUSTOMER', 500, 'INV-9'),
  ] };
  const result = detectTabBankExceptions(tab_bank, qbo_collection);
  // Per spec §8 risk mitigation: emit a single info exception, no false positives.
  assert.equal(result.exceptions.length, 1);
  assert.equal(result.exceptions[0].kind, 'info_all_non_factored');
});

// ===== NEW TESTS for short-pay slice (2026-06-09) =====

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

test('short_pay reads per-row collected_amount across multiple invoices for same check#', () => {
  // Check 208064 split across 3 rows in TAB BANK (one per invoice).
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

console.log(failed === 0
  ? `\nAll tests passed.`
  : `\n${failed} test(s) failed.`);
process.exit(failed === 0 ? 0 : 1);
