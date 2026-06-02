// AR Dashboard — build engine source-file loaders.
//
// Each parser takes an ArrayBuffer (or Uint8Array) and returns a normalized
// in-memory shape mirroring the Python reference at tools/verify_ar_build.py.
//
// IMPORTANT: column indexing here matches the RAW QBO/TAB BANK/TMS xlsx files,
// NOT the processed AR_AGING_*.xlsx output (which is shifted by 1 column on the
// COL sheet). The R1 loader at ar-dashboard-loader.js reads the processed output;
// this loader reads the raw inputs.
//
// Reads SheetJS off `globalThis.XLSX` so the same code runs in the browser (CDN
// global) and in Node (test harness injects it before import).

const X = () => {
  const x = globalThis.XLSX;
  if (!x) throw new Error('SheetJS (XLSX) not loaded');
  return x;
};

function readWorkbook(buf) {
  const data = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
  return X().read(data, { type: 'array', cellDates: true });
}

function sheetRows(sheet) {
  if (!sheet) return [];
  return X().utils.sheet_to_json(sheet, { header: 1, defval: null, raw: true });
}

function parseCustomerField(field) {
  if (!field || typeof field !== 'string') return { id: null, name: field || '' };
  if (field[0] === '[') {
    const close = field.indexOf(']');
    if (close > 0) {
      return { id: field.slice(1, close), name: field.slice(close + 1).trim() };
    }
  }
  return { id: null, name: field };
}

// ---------------------------------------------------------------------------
// Yesterday's workbook → AR rows
// ---------------------------------------------------------------------------

function parseAgingSheetDate(name) {
  // AR_05_19_26 → '2026-05-19'
  const m = name.match(/^AR_(\d\d)_(\d\d)_(\d\d)$/);
  if (!m) return null;
  return `20${m[3]}-${m[1]}-${m[2]}`;
}

function buildArRow(tuple) {
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
}

export function parseYesterdaysWorkbook(buf) {
  const wb = readWorkbook(buf);
  const arName = wb.SheetNames.find(s => /^AR_\d\d_\d\d_\d\d$/.test(s));
  if (!arName) throw new Error('Yesterday\'s workbook missing AR_<date> sheet');
  const date = parseAgingSheetDate(arName);
  const rows = sheetRows(wb.Sheets[arName]);
  const ar = rows.slice(1)
    .filter(r => r && r.some(c => c != null && c !== ''))
    .map(buildArRow);
  return { ar_register: ar, sheet_date: date, sheet_name: arName };
}

// ---------------------------------------------------------------------------
// QBO Daily Collection Report (raw xlsx)
// ---------------------------------------------------------------------------
// Column layout (mirrors verify_ar_build.py load_qbo_collection):
//   r[0] band/group, r[1] payment_date, r[2] txn_type, r[3] txn_number,
//   r[4] customer, r[5] invoice_or_ref, r[6] amount, r[7] open_balance, r[8] account

export function parseQboDailyCollection(buf) {
  const wb = readWorkbook(buf);
  const ws = wb.Sheets[wb.SheetNames[0]];
  const rows = sheetRows(ws);
  if (rows.length === 0) throw new Error('QBO Daily Collection: workbook is empty');
  const out = [];
  for (const r of rows) {
    if (!r) continue;
    if (r[2] !== 'Invoice' && r[2] !== 'Payment') continue;
    out.push({
      payment_date:   r[1],
      txn_type:       r[2],
      txn_number:     r[3],
      customer:       r[4],
      invoice_or_ref: r[5],
      amount:         r[6],
      open_balance:   r[7],
      account:        r[8] ?? null,
    });
  }
  if (out.length === 0) {
    throw new Error('QBO Daily Collection: 0 Invoice/Payment rows — wrong file?');
  }
  return { rows: out };
}

// ---------------------------------------------------------------------------
// QBO Daily Schedule List (raw xlsx)
// ---------------------------------------------------------------------------
// Column layout (mirrors verify_ar_build.py load_qbo_schedule):
//   r[0] date, r[1] txn_type, r[2] customer, r[3] inv, r[4] ref,
//   r[5] cntr_chassis, r[6] bl, r[7] amount

export function parseQboDailySchedule(buf) {
  const wb = readWorkbook(buf);
  const ws = wb.Sheets[wb.SheetNames[0]];
  const rows = sheetRows(ws);
  if (rows.length === 0) throw new Error('QBO Daily Schedule: workbook is empty');
  const out = [];
  for (const r of rows) {
    if (!r) continue;
    if (!r[0]) continue;
    if (typeof r[1] !== 'string' || r[1] !== 'Invoice') continue;
    out.push({
      date:         r[0],
      txn_type:     r[1],
      customer:     r[2],
      inv:          r[3],
      ref:          r[4],
      cntr_chassis: r[5],
      bl:           r[6],
      amount:       r[7],
    });
  }
  if (out.length === 0) {
    throw new Error('QBO Daily Schedule: 0 Invoice rows — wrong file?');
  }
  return { rows: out };
}

// ---------------------------------------------------------------------------
// TAB BANK Collection_Payment (raw xlsx)
// ---------------------------------------------------------------------------

export function parseTabBankRemittance(buf) {
  const wb = readWorkbook(buf);
  const ws = wb.Sheets[wb.SheetNames[0]];
  const rows = sheetRows(ws);
  if (rows.length === 0) throw new Error('TAB BANK: workbook is empty');
  const out = [];
  for (let i = 1; i < rows.length; i++) {
    const r = rows[i];
    if (!r || !r[0]) continue;
    out.push({
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
    });
  }
  if (out.length === 0) {
    throw new Error('TAB BANK: 0 data rows — wrong file?');
  }
  return { rows: out };
}

// ---------------------------------------------------------------------------
// TMS APAR Reconcile (raw xlsx)
// ---------------------------------------------------------------------------

export function parseTmsReconcile(buf) {
  const wb = readWorkbook(buf);
  const ws = wb.Sheets[wb.SheetNames[0]];
  const rows = sheetRows(ws);
  if (rows.length === 0) throw new Error('TMS Reconcile: workbook is empty');
  let header = null;
  const out = [];
  for (const r of rows) {
    if (!r) continue;
    if (header == null && (r[0] === '' || r[0] == null) && r[1] === 'NO') {
      header = r;
      continue;
    }
    if (!header) continue;
    if (typeof r[1] !== 'number') continue;
    out.push({
      no:             r[1],
      fold:           r[2],
      wo_div:         r[3],
      type:           r[4],
      id:             r[5],
      id_div:         r[6],
      name:           r[7],
      status:         r[8],
      date:           r[9],
      wo_no:          r[10],
      equipment:      r[11],
      cat:            r[12],
      total_amt:      r[13],
      inv_no:         r[14],
      qb_id:          r[15],
      ref_no:         r[16],
      mbl_booking:    r[17],
      inv_amt:        r[18],
      paid_received:  r[19],
      qb_date:        r.length > 20 ? r[20] : null,
    });
  }
  if (!header) {
    throw new Error('TMS Reconcile: header row not found (expected "" + "NO") — wrong file?');
  }
  if (out.length === 0) {
    throw new Error('TMS Reconcile: 0 data rows after header — wrong file?');
  }
  return { rows: out };
}

// Re-export the per-row helpers so the engine can use them too without
// importing model.js (which depends on shared/state via the R1 chain).
export { parseCustomerField, parseAgingSheetDate, buildArRow };
