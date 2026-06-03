// AR Dashboard — output workbook writer.
//
// Given a build result from arBuildToday() + the parsed input buffers,
// produce an .xlsx file matching the sheet shape Jihyun's hand-built
// workbooks use. The R1 loader (ar-dashboard-loader.js) re-reads it.
//
// Sheets, in order:
//   1. AR_<today>     — today's reconciled register
//   2. AR_<yesterday> — yesterday's register, carried forward for diffing
//   3. COL            — QBO Daily Collection rows, raw
//   4. COL (INV)      — COL + PARTIAL PAID / SHORT PAID tag column
//   5. Schedule       — QBO Daily Schedule rows, raw
//   6. TMS            — TMS rows the engine settled
//   7. ADJUSTMENT     — TMS rows whose TOTAL_AMT != 0 (the deltas)

const X = () => {
  const x = globalThis.XLSX;
  if (!x) throw new Error('SheetJS (XLSX) not loaded');
  return x;
};

// AR sheet column order:
//   NEW ID, COMPANY, NGL INV #, EQUIPMENT#, DATE, AGING,
//   REF NO, MBL NO, AMOUNT, PAID, BALANCE, MEMO, AR STATUS, WO #
const AR_HEADERS = [
  'NEW ID', 'COMPANY', 'NGL INV #', 'EQUIPMENT#', 'DATE', 'AGING',
  'REF NO', 'MBL NO', 'AMOUNT', 'PAID', 'BALANCE', 'MEMO', 'AR STATUS', 'WO #',
];

function arRowToTuple(r) {
  return [
    r.new_id, r.company, r.inv, r.equipment, r.date, r.aging,
    r.ref_no, r.mbl_no, r.amount, r.paid, r.balance, r.memo, r.ar_status, r.wo,
  ];
}

function fmtSheetDate(d) {
  // 2026-05-19 → "AR_05_19_26"
  if (!d) {
    const now = new Date();
    const mm = String(now.getMonth() + 1).padStart(2, '0');
    const dd = String(now.getDate()).padStart(2, '0');
    const yy = String(now.getFullYear()).slice(-2);
    return `AR_${mm}_${dd}_${yy}`;
  }
  if (d instanceof Date) {
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    const yy = String(d.getFullYear()).slice(-2);
    return `AR_${mm}_${dd}_${yy}`;
  }
  // 'YYYY-MM-DD' string
  const [y, m, day] = d.split('-');
  return `AR_${m}_${day}_${y.slice(-2)}`;
}

function fmtFilenameDate(d) {
  const dt = d instanceof Date ? d : (() => {
    const [y, m, day] = (d || '').split('-');
    return new Date(+y, +m - 1, +day);
  })();
  const mm = String(dt.getMonth() + 1).padStart(2, '0');
  const dd = String(dt.getDate()).padStart(2, '0');
  return `AR_AGING_${mm}_${dd}_${dt.getFullYear()}.xlsx`;
}

// ----- Sheet builders -----

function sheetFromAOA(aoa) {
  return X().utils.aoa_to_sheet(aoa);
}

function buildArSheet(arRows) {
  return sheetFromAOA([AR_HEADERS, ...arRows.map(arRowToTuple)]);
}

function buildCollectionsSheet(qboCollectionRows) {
  // Raw COL: header + each row as 8 cols (shift left so the R1 loader's
  // index map lines up — see parseCollectionsSheet in ar-dashboard-loader.js).
  // Layout: payment_date, txn_type, check_no, customer, invoice_or_ref, amount, open_balance, account
  const header = ['Payment Date', 'Type', 'Num', 'Name', 'Invoice / Ref', 'Amount', 'Open Balance', 'Account'];
  const rows = qboCollectionRows.map(r => [
    r.payment_date, r.txn_type, r.txn_number, r.customer, r.invoice_or_ref, r.amount, r.open_balance, r.account,
  ]);
  return sheetFromAOA([header, ...rows]);
}

function buildCollectionsTaggedSheet(qboCollectionRows) {
  // COL (INV) — same as COL plus a tag column for PARTIAL PAID / SHORT PAID
  const header = ['Payment Date', 'Type', 'Num', 'Name', 'Invoice / Ref', 'Amount', 'Open Balance', 'Account', 'Tag'];
  const rows = qboCollectionRows.map(r => {
    let tag = '';
    const bal = Number(r.open_balance);
    const amt = Number(r.amount);
    if (r.txn_type === 'Invoice') {
      if (!isNaN(bal) && bal > 0.01) tag = 'PARTIAL PAID';
      else if (!isNaN(bal) && !isNaN(amt) && bal < amt && bal > 0) tag = 'SHORT PAID';
    }
    return [r.payment_date, r.txn_type, r.txn_number, r.customer, r.invoice_or_ref, r.amount, r.open_balance, r.account, tag];
  });
  return sheetFromAOA([header, ...rows]);
}

function buildScheduleSheet(qboScheduleRows) {
  // Schedule layout: date, txn_type, customer, inv, ref, cntr_chassis, bl, amount
  const header = ['Date', 'Type', 'Name', 'Num', 'Ref', 'Container / Chassis', 'BL', 'Amount'];
  const rows = qboScheduleRows.map(r => [
    r.date, r.txn_type, r.customer, r.inv, r.ref, r.cntr_chassis, r.bl, r.amount,
  ]);
  return sheetFromAOA([header, ...rows]);
}

function buildTmsSheet(tmsRows) {
  // TMS sheet — R1 loader reads cols 0-16: wo_div, type, id, name, status, date,
  // wo_no, equipment, cat, total_amt, inv_no, qb_id, ref_no, mbl_booking, inv_amt,
  // paid_received, qb_date.
  const header = [
    'WO DIV', 'TYPE', 'ID', 'NAME', 'STATUS', 'DATE',
    'WO NO', 'EQUIPMENT', 'CAT', 'TOTAL AMT', 'INV NO', 'QB ID',
    'REF NO', 'MBL/BOOKING', 'INV AMT', 'PAID/RECEIVED', 'QB DATE',
  ];
  const rows = tmsRows.map(r => [
    r.wo_div, r.type, r.id, r.name, r.status, r.date,
    r.wo_no, r.equipment, r.cat, r.total_amt, r.inv_no, r.qb_id,
    r.ref_no, r.mbl_booking, r.inv_amt, r.paid_received, r.qb_date,
  ]);
  return sheetFromAOA([header, ...rows]);
}

function buildAdjustmentSheet(adjustmentRows) {
  // R1 loader reads cols 0-16: div, type, id, name, status, date, wo_no,
  // equipment, cat, amount_difference, inv_no, qb_id, ref_no, mbl_booking,
  // revised_invoice_amount, paid_received, qb_date.
  const header = [
    'DIV', 'TYPE', 'ID', 'NAME', 'STATUS', 'DATE',
    'WO NO', 'EQUIPMENT', 'CAT', 'AMOUNT DIFFERENCE', 'INV NO', 'QB ID',
    'REF NO', 'MBL/BOOKING', 'REVISED INV AMOUNT', 'PAID/RECEIVED', 'QB DATE',
  ];
  const rows = adjustmentRows.map(r => [
    r.wo_div, r.type, r.id, r.name, r.status, r.date,
    r.wo_no, r.equipment, r.cat, r.amount_difference, r.inv_no, r.qb_id,
    r.ref_no, r.mbl_booking, r.inv_amt, r.paid_received, r.qb_date,
  ]);
  return sheetFromAOA([header, ...rows]);
}

// ----- Column widths — approximate Jihyun's hand-built workbook -----

const AR_WIDTHS = [10, 28, 14, 14, 11, 7, 16, 18, 12, 12, 12, 32, 11, 14];

function setColWidths(sheet, widths) {
  sheet['!cols'] = widths.map(w => ({ wch: w }));
}

// ---------------------------------------------------------------------------
// Public entry
// ---------------------------------------------------------------------------

export function arBuildWriteWorkbook(buildResult, buildInputs) {
  const xlsx = X();
  const wb = xlsx.utils.book_new();

  const targetDate = buildResult.__targetDate || buildResult.target_date || new Date();
  const todaySheetName = fmtSheetDate(targetDate);
  const yestSheetName = fmtSheetDate(buildResult.yest_date);

  // 1. AR_<today>
  const todaySheet = buildArSheet(buildResult.today_ar);
  setColWidths(todaySheet, AR_WIDTHS);
  xlsx.utils.book_append_sheet(wb, todaySheet, todaySheetName);

  // 2. AR_<yesterday> — carry yesterday's register forward
  const yestRows = buildInputs.yesterday.parsed.ar_register;
  const yestSheet = buildArSheet(yestRows);
  setColWidths(yestSheet, AR_WIDTHS);
  xlsx.utils.book_append_sheet(wb, yestSheet, yestSheetName);

  // 3. COL
  const colSheet = buildCollectionsSheet(buildInputs.qbo_collection.parsed.rows);
  setColWidths(colSheet, [11, 9, 10, 28, 14, 12, 12, 18]);
  xlsx.utils.book_append_sheet(wb, colSheet, 'COL');

  // 4. COL (INV)
  const colInvSheet = buildCollectionsTaggedSheet(buildInputs.qbo_collection.parsed.rows);
  setColWidths(colInvSheet, [11, 9, 10, 28, 14, 12, 12, 18, 13]);
  xlsx.utils.book_append_sheet(wb, colInvSheet, 'COL (INV)');

  // 5. Schedule
  const schedSheet = buildScheduleSheet(buildInputs.qbo_schedule.parsed.rows);
  setColWidths(schedSheet, [11, 9, 28, 14, 16, 20, 18, 12]);
  xlsx.utils.book_append_sheet(wb, schedSheet, 'Schedule');

  // 6. TMS
  const tmsSheet = buildTmsSheet(buildResult.tms_rows);
  setColWidths(tmsSheet, [8, 6, 10, 28, 9, 11, 14, 14, 6, 12, 14, 10, 16, 18, 12, 14, 11]);
  xlsx.utils.book_append_sheet(wb, tmsSheet, 'TMS');

  // 7. ADJUSTMENT
  const adjSheet = buildAdjustmentSheet(buildResult.adjustment_rows);
  setColWidths(adjSheet, [8, 6, 10, 28, 9, 11, 14, 14, 6, 14, 14, 10, 16, 18, 14, 14, 11]);
  xlsx.utils.book_append_sheet(wb, adjSheet, 'ADJUSTMENT');

  const bytes = new Uint8Array(xlsx.write(wb, { bookType: 'xlsx', type: 'array' }));
  const filename = fmtFilenameDate(targetDate);

  return { bytes, filename, sheets: ['AR_today', 'AR_yesterday', 'COL', 'COL (INV)', 'Schedule', 'TMS', 'ADJUSTMENT'] };
}

window.arBuildWriteWorkbook = arBuildWriteWorkbook;
