// AR Dashboard — output workbook writer (ExcelJS edition).
//
// Produces an .xlsx file that matches Jihyun's hand-built workbook
// pixel-for-pixel: fonts, borders, centering, hidden-row filter views,
// frozen panes, auto-filter, number formats, column widths.
//
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

const E = () => {
  const lib = window.ExcelJS;
  if (!lib) throw new Error('ExcelJS not loaded');
  return lib;
};

// ───────────────────────────────────────────────────────────────────────
// Header strings — verified against Jihyun's 06/02/2026 + 05/18/2026
// ───────────────────────────────────────────────────────────────────────
const AR_HEADERS = [
  'NEW ID', 'COMPANY', 'NGL INV #', 'EQUIPMENT#', 'DATE', 'AGING',
  'REF NO', 'MBL NO', 'AMOUNT', 'PAID', 'BALANCE', 'MEMO', 'AR STATUS', 'WO #',
];
const COL_HEADERS = [
  'Payment date', 'Transaction type', 'Transaction number', 'Customer',
  'Transaction number', 'Amount', 'Open Balance', 'Account name',
];
const SCHEDULE_HEADERS = [
  'Date', 'Transaction type', 'Customer', 'Num', 'NGL REF# / YOUR REF#',
  'CNTR# / CHASSIS#', 'B/L#', 'Amount',
];
const TMS_HEADERS = [
  'WO DIV', 'TYPE', 'ID', 'NAME', 'STATUS', 'DATE',
  'WO #', 'EQUIPMENT', 'CAT', 'TOTAL AMT', 'INV #', 'QB ID',
  'REF #', 'MBL/BOOKING #', 'INV AMT', 'PAID/RECEIVED', 'QB DATE',
];
const ADJUSTMENT_HEADERS = [
  'DIV', 'TYPE', 'ID', 'NAME', 'STATUS', 'DATE',
  'WO #', 'EQUIPMENT', 'CAT', 'Amount Difference', 'INV #', 'QB ID',
  'REF #', 'MBL/BOOKING #', 'Revised Invoice Amount', 'PAID/RECEIVED', 'QB DATE',
];

// Column widths measured from Jihyun's reference workbook
const AR_WIDTHS = [13.7, 42.2, 18.3, 15.8, 12.2, 10.3, 33.7, 25.3, 14.0, 13.1, 13.7, 55.2, 12.8, 14.2, 12.4];
const COL_WIDTHS = [19.4, 14.1, 16.9, 33.5, 21.1, 15.9, 11.7, 17.9];
const COL_INV_WIDTHS = [19.4, 14.1, 16.9, 37.4, 21.1, 13.4, 11.7, 17.9];
const SCHEDULE_WIDTHS = [15.7, 11.0, 44.1, 17.0, 31.0, 25.1, 19.3, 14.8, 12.9];
const TMS_WIDTHS = [8.6, 7.5, 10.7, 24.6, 9.6, 11.8, 15.0, 13.9, 10.7, 12.9, 16.8, 7.5, 24.6, 18.3, 12.9, 10.2, 11.8, 11.4];
const ADJUSTMENT_WIDTHS = [8.8, 10.6, 12.7, 26.4, 13.4, 10.9, 16.2, 17.1, 9.7, 13.6, 17.9, 11.0, 13.9, 21.3, 19.2, 16.2, 14.4];

// ───────────────────────────────────────────────────────────────────────
// Style constants
// ───────────────────────────────────────────────────────────────────────
const FONT_BODY = { name: 'Calibri', size: 10 };
const FONT_BODY_BOLD = { name: 'Calibri', size: 10, bold: true };
const FONT_TITLE = { name: 'Calibri', size: 14, bold: true };
const FONT_QBO_HEADER = { name: 'Arial', size: 14, bold: true };
const ALIGN_CENTER = { horizontal: 'center', vertical: 'center' };
const ALIGN_LEFT = { horizontal: 'left', vertical: 'center' };
const ALIGN_RIGHT = { horizontal: 'right', vertical: 'center' };
const BORDER_ALL = {
  top:    { style: 'thin' },
  left:   { style: 'thin' },
  right:  { style: 'thin' },
  bottom: { style: 'thin' },
};
const NF_MONEY = '"$"#,##0.00;[Red]-"$"#,##0.00';
const NF_DATE  = 'm/d/yyyy';

// ───────────────────────────────────────────────────────────────────────
// Helpers
// ───────────────────────────────────────────────────────────────────────

function asDate(v) {
  if (v == null || v === '') return null;
  if (v instanceof Date) return v;
  if (typeof v === 'string') {
    const m = v.match(/^(\d{1,2})\/(\d{1,2})\/(\d{2,4})$/);
    if (m) {
      const yr = m[3].length === 2 ? 2000 + Number(m[3]) : Number(m[3]);
      return new Date(yr, Number(m[1]) - 1, Number(m[2]));
    }
  }
  return v;
}

function fmtSheetDate(d) {
  if (!d) {
    const now = new Date();
    return `AR_${pad(now.getMonth()+1)}_${pad(now.getDate())}_${String(now.getFullYear()).slice(-2)}`;
  }
  if (d instanceof Date) {
    return `AR_${pad(d.getMonth()+1)}_${pad(d.getDate())}_${String(d.getFullYear()).slice(-2)}`;
  }
  const [y, m, day] = d.split('-');
  return `AR_${m}_${day}_${y.slice(-2)}`;
}

function fmtFilenameDate(d) {
  const dt = d instanceof Date ? d : (() => {
    const [y, m, day] = (d || '').split('-');
    return new Date(+y, +m - 1, +day);
  })();
  return `AR_AGING_${pad(dt.getMonth()+1)}_${pad(dt.getDate())}_${dt.getFullYear()}.xlsx`;
}

function fmtLongDate(d) {
  const dt = d instanceof Date ? d : new Date(d);
  return dt.toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' });
}

function pad(n) { return String(n).padStart(2, '0'); }

function arStatusFormula(rowNum) {
  return `IF(F${rowNum}="", "", CHOOSE(1+(F${rowNum}>=30)+(F${rowNum}>=60)+(F${rowNum}>=90)+(F${rowNum}>=120), "A.0~29", "B.30~59", "C.60~89", "D.90~119", "E.120+"))`;
}

function setColWidths(ws, widths) {
  widths.forEach((w, i) => { ws.getColumn(i + 1).width = w; });
}

function styleRowAll(row, ncols, font, align, border) {
  for (let c = 1; c <= ncols; c++) {
    const cell = row.getCell(c);
    if (font) cell.font = font;
    if (align) cell.alignment = align;
    if (border) cell.border = border;
  }
}

// ───────────────────────────────────────────────────────────────────────
// AR sheet (used for AR_today and AR_yesterday — same shape)
// ───────────────────────────────────────────────────────────────────────

function buildArWorksheet(wb, name, arRows) {
  const ws = wb.addWorksheet(name, { views: [{ state: 'frozen', ySplit: 1 }] });
  setColWidths(ws, AR_WIDTHS);

  // Header row
  const header = ws.addRow([...AR_HEADERS, null]);
  styleRowAll(header, 15, FONT_BODY_BOLD, ALIGN_CENTER, BORDER_ALL);
  ws.getRow(1).height = 14;

  // Data rows
  for (let i = 0; i < arRows.length; i++) {
    const r = arRows[i];
    const rowNum = i + 2;
    const row = ws.addRow([
      r.new_id, r.company, r.inv, r.equipment, asDate(r.date), r.aging,
      r.ref_no, r.mbl_no, r.amount, r.paid, r.balance, r.memo,
      null, r.wo, null,
    ]);
    // AR STATUS formula
    row.getCell(13).value = { formula: arStatusFormula(rowNum) };
    // Styling
    styleRowAll(row, 15, FONT_BODY, ALIGN_CENTER, BORDER_ALL);
    // Money columns
    row.getCell(9).numFmt = NF_MONEY;   // AMOUNT
    row.getCell(10).numFmt = NF_MONEY;  // PAID
    row.getCell(11).numFmt = NF_MONEY;  // BALANCE
    // DATE column
    if (row.getCell(5).value instanceof Date) row.getCell(5).numFmt = NF_DATE;
    // COMPANY and MEMO read better left-aligned
    row.getCell(2).alignment = ALIGN_LEFT;
    row.getCell(12).alignment = ALIGN_LEFT;
  }

  // Auto-filter on the 14 real columns (skip blank O)
  ws.autoFilter = { from: { row: 1, column: 1 }, to: { row: arRows.length + 1, column: 14 } };
  return ws;
}

// ───────────────────────────────────────────────────────────────────────
// COL / COL (INV) — same data, different rows hidden (Jihyun's view trick)
// ───────────────────────────────────────────────────────────────────────

function buildCollectionsWorksheet(wb, name, rows, targetDate, hideType) {
  // hideType: 'Invoice' → COL (payments view); 'Payment' → COL (INV) (invoices view)
  const ws = wb.addWorksheet(name, { views: [{ state: 'frozen', ySplit: 5 }] });
  setColWidths(ws, hideType === 'Payment' ? COL_INV_WIDTHS : COL_WIDTHS);

  // QBO report header (rows 1-4)
  ws.addRow(['NGL Transportation, Inc.']);
  ws.addRow(['Daily Collection Report']);
  ws.addRow([fmtLongDate(targetDate)]);
  ws.addRow([]);
  ws.getCell('A1').font = FONT_QBO_HEADER;
  ws.getCell('A2').font = FONT_BODY_BOLD;
  ws.getCell('A3').font = FONT_BODY_BOLD;

  // Header row (row 5)
  const header = ws.addRow(COL_HEADERS);
  styleRowAll(header, 8, FONT_BODY_BOLD, ALIGN_CENTER, BORDER_ALL);
  // Blank row 6 (matches QBO export)
  ws.addRow([]);

  // Data rows from row 7
  const firstDataRow = 7;
  let rowIdx = firstDataRow;
  for (const r of rows) {
    const row = ws.addRow([
      asDate(r.payment_date), r.txn_type, r.txn_number, r.customer,
      r.invoice_or_ref, r.amount, r.open_balance, r.account,
    ]);
    styleRowAll(row, 8, FONT_BODY, ALIGN_CENTER, null);
    row.getCell(4).alignment = ALIGN_LEFT;  // Customer (long text)
    row.getCell(6).numFmt = NF_MONEY;
    row.getCell(7).numFmt = NF_MONEY;
    if (row.getCell(1).value instanceof Date) row.getCell(1).numFmt = NF_DATE;
    // Hide rows of the off-type for this view
    if (r.txn_type === hideType) row.hidden = true;
    rowIdx++;
  }
  const lastDataRow = rowIdx - 1;

  // Blank + grand-total + blank
  ws.addRow([]);
  const totalRow = ws.addRow([null, null, null, null, null,
    { formula: `SUBTOTAL(9,F${firstDataRow}:F${lastDataRow})` }]);
  totalRow.getCell(6).font = FONT_BODY_BOLD;
  totalRow.getCell(6).numFmt = NF_MONEY;
  totalRow.getCell(6).border = { top: { style: 'thin' }, bottom: { style: 'double' } };
  ws.addRow([]);

  // Auto-filter
  ws.autoFilter = { from: { row: 5, column: 1 }, to: { row: lastDataRow, column: 8 } };
  return ws;
}

// ───────────────────────────────────────────────────────────────────────
// Schedule — QBO Daily Schedule report header preserved
// ───────────────────────────────────────────────────────────────────────

function buildScheduleWorksheet(wb, rows, targetDate) {
  const ws = wb.addWorksheet('Schedule', { views: [{ state: 'frozen', ySplit: 5 }] });
  setColWidths(ws, SCHEDULE_WIDTHS);

  ws.addRow(['NGL Transportation, Inc.']);
  ws.addRow(['Daily Schedule List']);
  ws.addRow([fmtLongDate(targetDate)]);
  ws.addRow([]);
  ws.getCell('A1').font = FONT_QBO_HEADER;
  ws.getCell('A2').font = FONT_BODY_BOLD;
  ws.getCell('A3').font = FONT_BODY_BOLD;

  const header = ws.addRow(SCHEDULE_HEADERS);
  styleRowAll(header, 8, FONT_BODY_BOLD, ALIGN_CENTER, BORDER_ALL);
  ws.addRow([]);

  const firstDataRow = 7;
  for (const r of rows) {
    const row = ws.addRow([
      asDate(r.date), r.txn_type, r.customer, r.inv, r.ref,
      r.cntr_chassis, r.bl, r.amount,
    ]);
    styleRowAll(row, 8, FONT_BODY, ALIGN_CENTER, null);
    row.getCell(3).alignment = ALIGN_LEFT;  // Customer
    row.getCell(8).numFmt = NF_MONEY;
    if (row.getCell(1).value instanceof Date) row.getCell(1).numFmt = NF_DATE;
  }
  const lastDataRow = firstDataRow + rows.length - 1;

  ws.addRow([]);
  const totalRow = ws.addRow([null, null, null, null, null, null, null,
    { formula: `SUBTOTAL(9,H${firstDataRow}:H${lastDataRow})` }]);
  totalRow.getCell(8).font = FONT_BODY_BOLD;
  totalRow.getCell(8).numFmt = NF_MONEY;
  totalRow.getCell(8).border = { top: { style: 'thin' }, bottom: { style: 'double' } };
  ws.addRow([]);

  ws.autoFilter = { from: { row: 5, column: 1 }, to: { row: lastDataRow, column: 8 } };
  return ws;
}

// ───────────────────────────────────────────────────────────────────────
// TMS / ADJUSTMENT — simple header + data layout, no QBO title rows
// ───────────────────────────────────────────────────────────────────────

function buildTmsWorksheet(wb, rows) {
  const ws = wb.addWorksheet('TMS', { views: [{ state: 'frozen', ySplit: 1 }] });
  setColWidths(ws, TMS_WIDTHS);
  const header = ws.addRow(TMS_HEADERS);
  styleRowAll(header, 17, FONT_BODY_BOLD, ALIGN_CENTER, BORDER_ALL);
  for (const r of rows) {
    const row = ws.addRow([
      r.wo_div, r.type, r.id, r.name, r.status, asDate(r.date),
      r.wo_no, r.equipment, r.cat, r.total_amt, r.inv_no, r.qb_id,
      r.ref_no, r.mbl_booking, r.inv_amt, r.paid_received, asDate(r.qb_date),
    ]);
    styleRowAll(row, 17, FONT_BODY, ALIGN_CENTER, null);
    row.getCell(4).alignment = ALIGN_LEFT;  // NAME
    row.getCell(10).numFmt = NF_MONEY;  // TOTAL AMT
    row.getCell(15).numFmt = NF_MONEY;  // INV AMT
    row.getCell(16).numFmt = NF_MONEY;  // PAID/RECEIVED
    if (row.getCell(6).value instanceof Date) row.getCell(6).numFmt = NF_DATE;
    if (row.getCell(17).value instanceof Date) row.getCell(17).numFmt = NF_DATE;
  }
  ws.autoFilter = { from: { row: 1, column: 1 }, to: { row: rows.length + 1, column: 17 } };
  return ws;
}

function buildAdjustmentWorksheet(wb, rows) {
  const ws = wb.addWorksheet('ADJUSTMENT', { views: [{ state: 'frozen', ySplit: 1 }] });
  setColWidths(ws, ADJUSTMENT_WIDTHS);
  const header = ws.addRow(ADJUSTMENT_HEADERS);
  styleRowAll(header, 17, FONT_BODY_BOLD, ALIGN_CENTER, BORDER_ALL);
  for (const r of rows) {
    const row = ws.addRow([
      r.wo_div, r.type, r.id, r.name, r.status, asDate(r.date),
      r.wo_no, r.equipment, r.cat, r.amount_difference, r.inv_no, r.qb_id,
      r.ref_no, r.mbl_booking, r.inv_amt, r.paid_received, asDate(r.qb_date),
    ]);
    styleRowAll(row, 17, FONT_BODY, ALIGN_CENTER, null);
    row.getCell(4).alignment = ALIGN_LEFT;
    row.getCell(10).numFmt = NF_MONEY;
    row.getCell(15).numFmt = NF_MONEY;
    row.getCell(16).numFmt = NF_MONEY;
    if (row.getCell(6).value instanceof Date) row.getCell(6).numFmt = NF_DATE;
    if (row.getCell(17).value instanceof Date) row.getCell(17).numFmt = NF_DATE;
  }
  ws.autoFilter = { from: { row: 1, column: 1 }, to: { row: rows.length + 1, column: 17 } };
  return ws;
}

// ───────────────────────────────────────────────────────────────────────
// Public entry — returns a Promise<{bytes, filename, sheets}>
// ───────────────────────────────────────────────────────────────────────

export async function arBuildWriteWorkbook(buildResult, buildInputs) {
  const ExcelJS = E();
  const wb = new ExcelJS.Workbook();
  wb.creator = 'NGL Accounting';
  wb.created = new Date();

  const targetDate = buildResult.__targetDate || buildResult.target_date || new Date();
  const todaySheetName = fmtSheetDate(targetDate);
  const yestSheetName = fmtSheetDate(buildResult.yest_date);

  buildArWorksheet(wb, todaySheetName, buildResult.today_ar);
  buildCollectionsWorksheet(wb, 'COL', buildInputs.qbo_collection.parsed.rows, targetDate, 'Invoice');
  buildCollectionsWorksheet(wb, 'COL (INV)', buildInputs.qbo_collection.parsed.rows, targetDate, 'Payment');
  buildScheduleWorksheet(wb, buildInputs.qbo_schedule.parsed.rows, targetDate);
  buildTmsWorksheet(wb, buildResult.tms_rows);
  buildAdjustmentWorksheet(wb, buildResult.adjustment_rows);
  buildArWorksheet(wb, yestSheetName, buildInputs.yesterday.parsed.ar_register);

  const arrayBuffer = await wb.xlsx.writeBuffer();
  const bytes = new Uint8Array(arrayBuffer);
  const filename = fmtFilenameDate(targetDate);
  return {
    bytes,
    filename,
    sheets: [todaySheetName, 'COL', 'COL (INV)', 'Schedule', 'TMS', 'ADJUSTMENT', yestSheetName],
  };
}

window.arBuildWriteWorkbook = arBuildWriteWorkbook;
