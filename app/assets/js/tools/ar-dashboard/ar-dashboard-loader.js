// AR Dashboard — workbook loader
// Reads a single AR_AGING_*.xlsx file, parses all 7 sheets into the in-memory model.

import { arState } from '../../shared/state.js';
import { arBuildARRow, arParseCustomerField } from './ar-dashboard-model.js';

export async function arLoadWorkbook(file) {
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
}

function parseWorkbook(wb, filename) {
  // Find both AR_<date> sheets; the newer date is "today", the older is "yesterday".
  const arSheets = wb.SheetNames
    .filter(s => /^AR_\d\d_\d\d_\d\d$/.test(s))
    .map(s => ({ name: s, date: parseAgingSheetDate(s) }))
    .sort((a, b) => (b.date || '').localeCompare(a.date || ''));

  if (arSheets.length === 0) throw new Error('Workbook is missing the AR_<date> sheet');

  const todaySheet = arSheets[0];
  const yestSheet = arSheets[1] || null;

  return {
    filename,
    today_date: todaySheet.date,
    yesterday_date: yestSheet ? yestSheet.date : null,
    ar_register:   parseArSheet(wb.Sheets[todaySheet.name]),
    ar_yesterday:  yestSheet ? parseArSheet(wb.Sheets[yestSheet.name]) : [],
    collections:   parseCollectionsSheet(wb.Sheets['COL']),
    collections_tagged: parseCollectionsSheet(wb.Sheets['COL (INV)']),
    schedule:      parseScheduleSheet(wb.Sheets['Schedule']),
    tms_rows:      parseTmsSheet(wb.Sheets['TMS']),
    adjustments:   parseAdjustmentSheet(wb.Sheets['ADJUSTMENT']),
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
  return rows.slice(1)
    .filter(r => r.some(c => c != null && c !== ''))
    .map(r => arBuildARRow(r));
}

function parseCollectionsSheet(sheet) {
  if (!sheet) return [];
  // QBO Daily Collection layout: title rows, header, then data rows
  // interleaved with band rows and a grand-total row at the end.
  const rows = XLSX.utils.sheet_to_json(sheet, { header: 1, defval: null });
  const out = [];
  for (const r of rows) {
    if (!r || !r[1]) continue;
    if (r[1] !== 'Invoice' && r[1] !== 'Payment') continue;
    const customerField = arParseCustomerField(r[3]);
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
    const customerField = arParseCustomerField(r[2]);
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

window.arLoadWorkbook = arLoadWorkbook;
