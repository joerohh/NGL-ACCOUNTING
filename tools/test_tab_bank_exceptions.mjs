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
