// AR Dashboard — output workbook writer.
//
// Given a build result from arBuildToday() + the parsed input buffers,
// produce an .xlsx file matching the shape of Jihyun's hand-built workbook.
// The R1 loader (ar-dashboard-loader.js) re-reads the same shape.
//
// Sheets, in order (matches Jihyun's tab order — AR_yesterday goes LAST):
//   1. AR_<today>     — today's reconciled register, 15 cols (col O blank)
//   2. COL            — QBO Daily Collection with QBO report header preserved
//   3. COL (INV)      — same as COL but filtered to Invoice rows only
//   4. Schedule       — QBO Daily Schedule with QBO report header preserved
//   5. TMS            — TMS rows the engine settled
//   6. ADJUSTMENT     — TMS rows whose amount changed (deltas)
//   7. AR_<yesterday> — yesterday's register, carried forward for diffing

const X = () => {
  const x = globalThis.XLSX;
  if (!x) throw new Error('SheetJS (XLSX) not loaded');
  return x;
};

// ───────────────────────────────────────────────────────────────────────
// Sheet header strings — match Jihyun's exact text (verified against her
// 06/02/2026 and 05/18/2026 workbooks).
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

// ───────────────────────────────────────────────────────────────────────
// Column widths — measured from Jihyun's hand-built workbooks
// ───────────────────────────────────────────────────────────────────────
const AR_WIDTHS = [
  13.7, 42.2, 18.3, 15.8, 12.2, 10.3,
  33.7, 25.3, 14.0, 13.1, 13.7, 55.2, 12.8, 14.2, 12.4,  // 15th = blank col O
];
const COL_WIDTHS = [19.4, 14.1, 16.9, 33.5, 21.1, 15.9, 11.7, 17.9];
const COL_INV_WIDTHS = [19.4, 14.1, 16.9, 37.4, 21.1, 13.4, 11.7, 17.9];
const SCHEDULE_WIDTHS = [15.7, 11.0, 44.1, 17.0, 31.0, 25.1, 19.3, 14.8, 12.9];
const TMS_WIDTHS = [
  8.6, 7.5, 10.7, 24.6, 9.6, 11.8,
  15.0, 13.9, 10.7, 12.9, 16.8, 7.5,
  24.6, 18.3, 12.9, 10.2, 11.8, 11.4,
];
const ADJUSTMENT_WIDTHS = [
  8.8, 10.6, 12.7, 26.4, 13.4, 10.9,
  16.2, 17.1, 9.7, 13.6, 17.9, 11.0,
  13.9, 21.3, 19.2, 16.2, 14.4,
];

// ───────────────────────────────────────────────────────────────────────
// Helpers
// ───────────────────────────────────────────────────────────────────────

function setColWidths(sheet, widths) {
  sheet['!cols'] = widths.map(w => ({ wch: w }));
}

function freezeRow(sheet, ySplit) {
  // Lock rows 1..ySplit so headers stay visible when scrolling.
  sheet['!views'] = [{ state: 'frozen', ySplit }];
}

function setAutoFilter(sheet, headerRow, ncols, lastRow) {
  // Excel auto-filter dropdowns on the header row across ncols columns.
  const lastCol = X().utils.encode_col(ncols - 1);
  sheet['!autofilter'] = { ref: `A${headerRow}:${lastCol}${lastRow}` };
}

function asDate(v) {
  // Convert MM/DD/YYYY string to a real Date so Excel recognizes it.
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

function fmtLongDate(d) {
  // "June 2, 2026" for the QBO report header line
  const dt = d instanceof Date ? d : new Date(d);
  return dt.toLocaleDateString('en-US', {
    year: 'numeric', month: 'long', day: 'numeric',
  });
}

function arStatusFormula(rowNum) {
  // Live formula — auto-updates when AGING column F is edited.
  return `IF(F${rowNum}="", "", CHOOSE(1+(F${rowNum}>=30)+(F${rowNum}>=60)+(F${rowNum}>=90)+(F${rowNum}>=120), "A.0~29", "B.30~59", "C.60~89", "D.90~119", "E.120+"))`;
}

// ───────────────────────────────────────────────────────────────────────
// AR sheet builder
// ───────────────────────────────────────────────────────────────────────

function buildArSheet(arRows) {
  const xlsx = X();
  // Build 2D array: header row + one row per invoice + a trailing blank col O.
  const aoa = [
    [...AR_HEADERS, null],
    ...arRows.map(r => [
      r.new_id, r.company, r.inv, r.equipment, asDate(r.date), r.aging,
      r.ref_no, r.mbl_no, r.amount, r.paid, r.balance, r.memo,
      r.ar_status,  // placeholder — overwritten by formula below
      r.wo, null,   // null = blank col O
    ]),
  ];
  const sheet = xlsx.utils.aoa_to_sheet(aoa, { cellDates: true });

  // Replace col M (AR STATUS) with the live formula for every data row.
  for (let i = 0; i < arRows.length; i++) {
    const rowNum = i + 2;
    const addr = xlsx.utils.encode_cell({ c: 12, r: i + 1 });
    sheet[addr] = { t: 's', f: arStatusFormula(rowNum) };
  }

  setColWidths(sheet, AR_WIDTHS);
  freezeRow(sheet, 1);
  setAutoFilter(sheet, 1, 14, arRows.length + 1);
  return sheet;
}

// ───────────────────────────────────────────────────────────────────────
// COL / COL (INV) — preserve the QBO Daily Collection report header
// (3 title rows + blank + headers + data + blank + grand-total SUBTOTAL).
// ───────────────────────────────────────────────────────────────────────

function buildCollectionsSheet(rows, targetDate, invoiceOnly) {
  const xlsx = X();
  const filtered = invoiceOnly ? rows.filter(r => r.txn_type === 'Invoice') : rows;
  const aoa = [
    ['NGL Transportation, Inc.'],
    ['Daily Collection Report'],
    [fmtLongDate(targetDate)],
    [],                                                    // row 4 blank
    COL_HEADERS,                                           // row 5 headers
    [],                                                    // row 6 blank
  ];
  // Data rows start at row 7.
  for (const r of filtered) {
    aoa.push([
      asDate(r.payment_date), r.txn_type, r.txn_number, r.customer,
      r.invoice_or_ref, r.amount, r.open_balance, r.account,
    ]);
  }
  // Blank + grand-total + blank.
  const lastDataRow = 6 + filtered.length;  // 7..lastDataRow inclusive
  aoa.push([]);                              // blank
  aoa.push([null, null, null, null, null, { f: `SUBTOTAL(9,F7:F${lastDataRow})` }]);
  aoa.push([]);                              // trailing blank

  const sheet = xlsx.utils.aoa_to_sheet(aoa, { cellDates: true });
  setColWidths(sheet, invoiceOnly ? COL_INV_WIDTHS : COL_WIDTHS);
  freezeRow(sheet, 5);                       // keep title + header in view
  setAutoFilter(sheet, 5, 8, lastDataRow);
  return sheet;
}

// ───────────────────────────────────────────────────────────────────────
// Schedule — preserve the QBO Daily Schedule List report header
// ───────────────────────────────────────────────────────────────────────

function buildScheduleSheet(rows, targetDate) {
  const xlsx = X();
  const aoa = [
    ['NGL Transportation, Inc.'],
    ['Daily Schedule List'],
    [fmtLongDate(targetDate)],
    [],
    SCHEDULE_HEADERS,
    [],
  ];
  for (const r of rows) {
    aoa.push([
      asDate(r.date), r.txn_type, r.customer, r.inv, r.ref,
      r.cntr_chassis, r.bl, r.amount,
    ]);
  }
  const lastDataRow = 6 + rows.length;
  aoa.push([]);
  aoa.push([null, null, null, null, null, null, null, { f: `SUBTOTAL(9,H7:H${lastDataRow})` }]);
  aoa.push([]);

  const sheet = xlsx.utils.aoa_to_sheet(aoa, { cellDates: true });
  setColWidths(sheet, SCHEDULE_WIDTHS);
  freezeRow(sheet, 5);
  setAutoFilter(sheet, 5, 8, lastDataRow);
  return sheet;
}

// ───────────────────────────────────────────────────────────────────────
// TMS / ADJUSTMENT — simple header + data layout (no QBO title rows)
// ───────────────────────────────────────────────────────────────────────

function buildTmsSheet(rows) {
  const xlsx = X();
  const aoa = [
    TMS_HEADERS,
    ...rows.map(r => [
      r.wo_div, r.type, r.id, r.name, r.status, asDate(r.date),
      r.wo_no, r.equipment, r.cat, r.total_amt, r.inv_no, r.qb_id,
      r.ref_no, r.mbl_booking, r.inv_amt, r.paid_received, asDate(r.qb_date),
    ]),
  ];
  const sheet = xlsx.utils.aoa_to_sheet(aoa, { cellDates: true });
  setColWidths(sheet, TMS_WIDTHS);
  freezeRow(sheet, 1);
  setAutoFilter(sheet, 1, 17, rows.length + 1);
  return sheet;
}

function buildAdjustmentSheet(rows) {
  const xlsx = X();
  const aoa = [
    ADJUSTMENT_HEADERS,
    ...rows.map(r => [
      r.wo_div, r.type, r.id, r.name, r.status, asDate(r.date),
      r.wo_no, r.equipment, r.cat, r.amount_difference, r.inv_no, r.qb_id,
      r.ref_no, r.mbl_booking, r.inv_amt, r.paid_received, asDate(r.qb_date),
    ]),
  ];
  const sheet = xlsx.utils.aoa_to_sheet(aoa, { cellDates: true });
  setColWidths(sheet, ADJUSTMENT_WIDTHS);
  freezeRow(sheet, 1);
  setAutoFilter(sheet, 1, 17, rows.length + 1);
  return sheet;
}

// ───────────────────────────────────────────────────────────────────────
// Public entry — assemble the workbook in Jihyun's tab order
// ───────────────────────────────────────────────────────────────────────

export function arBuildWriteWorkbook(buildResult, buildInputs) {
  const xlsx = X();
  const wb = xlsx.utils.book_new();

  const targetDate = buildResult.__targetDate || buildResult.target_date || new Date();
  const todaySheetName = fmtSheetDate(targetDate);
  const yestSheetName = fmtSheetDate(buildResult.yest_date);

  // Tab order: AR_today, COL, COL (INV), Schedule, TMS, ADJUSTMENT, AR_yesterday.
  xlsx.utils.book_append_sheet(wb, buildArSheet(buildResult.today_ar), todaySheetName);
  xlsx.utils.book_append_sheet(wb, buildCollectionsSheet(buildInputs.qbo_collection.parsed.rows, targetDate, false), 'COL');
  xlsx.utils.book_append_sheet(wb, buildCollectionsSheet(buildInputs.qbo_collection.parsed.rows, targetDate, true), 'COL (INV)');
  xlsx.utils.book_append_sheet(wb, buildScheduleSheet(buildInputs.qbo_schedule.parsed.rows, targetDate), 'Schedule');
  xlsx.utils.book_append_sheet(wb, buildTmsSheet(buildResult.tms_rows), 'TMS');
  xlsx.utils.book_append_sheet(wb, buildAdjustmentSheet(buildResult.adjustment_rows), 'ADJUSTMENT');
  xlsx.utils.book_append_sheet(wb, buildArSheet(buildInputs.yesterday.parsed.ar_register), yestSheetName);

  const bytes = new Uint8Array(xlsx.write(wb, { bookType: 'xlsx', type: 'array', cellDates: true }));
  const filename = fmtFilenameDate(targetDate);
  return {
    bytes,
    filename,
    sheets: [todaySheetName, 'COL', 'COL (INV)', 'Schedule', 'TMS', 'ADJUSTMENT', yestSheetName],
  };
}

window.arBuildWriteWorkbook = arBuildWriteWorkbook;
