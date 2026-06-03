// AR Dashboard — build engine (6-phase pipeline).
//
// Ports tools/verify_ar_build.py:build_today() to JavaScript. The output is
// today's AR register plus the auxiliary sheet rows (TMS, ADJUSTMENT). The
// COL / COL (INV) / Schedule sheet payloads come straight from the raw inputs
// in the writer (M2) — they're not transformed here.
//
// Self-contained: no imports from arState or shared/. Browser exposes the
// entry points on window for console testing.

import {
  parseYesterdaysWorkbook,
  parseQboDailyCollection,
  parseQboDailySchedule,
  parseTabBankRemittance,
  parseTmsReconcile,
  parseCustomerField,
} from './ar-dashboard-build-loader.js';

const AMT_EPS = 0.01;

function agingBucket(days) {
  if (days == null) return '';
  if (days < 30)  return 'A.0~29';
  if (days < 60)  return 'B.30~59';
  if (days < 90)  return 'C.60~89';
  if (days < 120) return 'D.90~119';
  return 'E.120+';
}

function num(v) {
  return typeof v === 'number' ? v : 0;
}

// Calendar-days delta between two BUILD days (the day Jihyun runs the build
// for a given workbook date). Build day = workbook date + 1, except Friday
// workbooks are built on Monday (+3). Mirrors verify_ar_build.py main().
function buildDay(d) {
  const t = new Date(d.getTime());
  const dow = t.getDay(); // 0=Sun..6=Sat
  const add = dow === 5 ? 3 : 1; // Friday → +3
  t.setDate(t.getDate() + add);
  return t;
}

export function computeAgeDelta(yesterdayDate, todayDate) {
  if (!yesterdayDate || !todayDate) return 1;
  const a = buildDay(yesterdayDate);
  const b = buildDay(todayDate);
  const days = Math.round((b - a) / 86400000);
  return Math.max(1, days);
}

function fmtTodayStr(d) {
  // Match Python strftime("%m/%d/%Y") → "05/13/2026"
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${mm}/${dd}/${d.getFullYear()}`;
}

// ---------------------------------------------------------------------------
// Build pipeline
// ---------------------------------------------------------------------------

export function arBuildToday(inputs) {
  const {
    yesterday,       // parsed object from parseYesterdaysWorkbook
    qbo_collection,  // parsed from parseQboDailyCollection
    qbo_schedule,    // parsed from parseQboDailySchedule
    tab_bank,        // parsed from parseTabBankRemittance (currently unused — engine ignores TAB BANK arithmetic per spec §4.3)
    tms_reconcile,   // parsed from parseTmsReconcile
    target_date,     // JS Date — the workbook date we're building for
    age_delta,       // optional override; computed from buildDay deltas if omitted
  } = inputs;

  if (!yesterday) throw new Error('Build: yesterday\'s workbook is required');
  if (!qbo_collection) throw new Error('Build: QBO Daily Collection is required');
  if (!qbo_schedule) throw new Error('Build: QBO Daily Schedule is required');
  if (!tms_reconcile) throw new Error('Build: TMS Reconcile is required');
  if (!target_date || !(target_date instanceof Date)) {
    throw new Error('Build: target_date (Date) is required');
  }

  // Yesterday's date for age delta
  let yestDate = null;
  if (yesterday.sheet_date) {
    const [y, m, d] = yesterday.sheet_date.split('-').map(Number);
    yestDate = new Date(y, m - 1, d);
  }
  const delta = age_delta != null ? Math.max(1, age_delta) : computeAgeDelta(yestDate, target_date);

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

  // ----- Phase 3 — add new invoices from Schedule
  for (const sch of qbo_schedule.rows) {
    const inv = sch.inv;
    if (!inv || todayAr.has(inv)) continue;
    const cust = parseCustomerField(sch.customer);
    todayAr.set(inv, {
      new_id:    cust.id,
      company:   cust.name,
      inv:       inv,
      equipment: sch.cntr_chassis,
      date:      sch.date,
      aging:     0,
      ref_no:    sch.ref,
      mbl_no:    sch.bl,
      amount:    sch.amount,
      paid:      0,
      balance:   sch.amount,
      memo:      null,
      ar_status: agingBucket(0),
      wo:        null,
    });
  }

  // ----- Phase 4 — age every row by delta
  for (const row of todayAr.values()) {
    if (row.aging != null) {
      row.aging = row.aging + delta;
      row.ar_status = agingBucket(row.aging);
    }
  }

  // ----- Phase 5 — TMS reconcile
  const tmsSheetRows = [];
  const adjustmentRows = [];
  const newInvs = new Set();
  for (const sch of qbo_schedule.rows) {
    if (sch.inv) newInvs.add(sch.inv);
  }

  for (const t of tms_reconcile.rows) {
    if (t.type !== 'AR') continue;
    const inv = t.inv_no;
    if (!inv) continue;
    if (newInvs.has(inv)) {
      // NEW invoice. Per Jihyun 2026-06-02: "the most recently updated TMS
      // amount is the reference" → use t.inv_amt (current/revised), not
      // t.total_amt (original). They're equal for invoices with no
      // mid-creation adjustment; they diverge when TMS adjusts the rate.
      const row = todayAr.get(inv);
      if (row) {
        row.amount = t.inv_amt;
        row.balance = num(t.inv_amt) - num(row.paid);
        row.wo = t.wo_no;
      }
      tmsSheetRows.push(t);
    } else {
      // OLD invoice: TMS TOTAL_AMT is the DELTA from previous amount
      const delta = t.total_amt;
      if (delta != null && Math.abs(delta) > AMT_EPS) {
        const row = todayAr.get(inv);
        if (row) {
          row.amount = t.inv_amt;
          row.balance = num(t.inv_amt) - num(row.paid);
        }
        adjustmentRows.push({ ...t, amount_difference: delta });
      } else {
        tmsSheetRows.push(t);
      }
    }
  }

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

// ---------------------------------------------------------------------------
// Convenience: parse-and-build from raw ArrayBuffers in one call
// ---------------------------------------------------------------------------

export function arBuildTodayFromBuffers(buffers) {
  // buffers: { yesterday, qbo_collection, qbo_schedule, tab_bank, tms_reconcile }
  // target_date: optional override, otherwise derived from filename of yesterday
  const yesterday = parseYesterdaysWorkbook(buffers.yesterday);
  const qbo_collection = parseQboDailyCollection(buffers.qbo_collection);
  const qbo_schedule = parseQboDailySchedule(buffers.qbo_schedule);
  const tab_bank = buffers.tab_bank ? parseTabBankRemittance(buffers.tab_bank) : null;
  const tms_reconcile = parseTmsReconcile(buffers.tms_reconcile);
  return arBuildToday({
    yesterday, qbo_collection, qbo_schedule, tab_bank, tms_reconcile,
    target_date: buffers.target_date,
    age_delta: buffers.age_delta,
  });
}

// Browser console hook
if (typeof window !== 'undefined') {
  window.arBuildToday = arBuildToday;
  window.arBuildTodayFromBuffers = arBuildTodayFromBuffers;
  window.arComputeAgeDelta = computeAgeDelta;
}
