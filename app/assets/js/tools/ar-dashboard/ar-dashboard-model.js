// AR Dashboard — pure model helpers
// No DOM, no I/O. Just data transforms used by the loader and exception detectors.

// AR register row schema (matches Excel column order on AR_<date> sheet).
// Tuple is a SheetJS row array in column order:
//   [NEW ID, COMPANY, NGL INV #, EQUIPMENT#, DATE, AGING,
//    REF NO, MBL NO, AMOUNT, PAID, BALANCE, MEMO, AR STATUS, WO #]
export function arBuildARRow(tuple) {
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

export function arAgingBucket(days) {
  if (days == null) return '';
  if (days < 30)  return 'A.0~29';
  if (days < 60)  return 'B.30~59';
  if (days < 90)  return 'C.60~89';
  if (days < 120) return 'D.90~119';
  return 'E.120+';
}

export const AR_AMT_EPS = 0.01;

export function arAmtEq(a, b) {
  if (a == null && b == null) return true;
  if (a == null || b == null) return false;
  return Math.abs(Number(a) - Number(b)) < AR_AMT_EPS;
}

// Returns 'full' | 'short' | 'over' | 'unknown'.
export function arClassifyOpenBalance(openBalance) {
  if (openBalance == null) return 'unknown';
  const n = Number(openBalance);
  if (Math.abs(n) < AR_AMT_EPS) return 'full';
  if (n > 0) return 'short';
  return 'over';
}

// Parse "[CUSTID] Customer Name" strings out of QBO Daily Collection rows.
export function arParseCustomerField(field) {
  if (!field || typeof field !== 'string') return { id: null, name: field || '' };
  if (field[0] === '[') {
    const close = field.indexOf(']');
    if (close > 0) {
      return { id: field.slice(1, close), name: field.slice(close + 1).trim() };
    }
  }
  return { id: null, name: field };
}

// Window attachments for console debugging + cross-file access without imports.
window.arBuildARRow = arBuildARRow;
window.arAgingBucket = arAgingBucket;
window.AR_AMT_EPS = AR_AMT_EPS;
window.arAmtEq = arAmtEq;
window.arClassifyOpenBalance = arClassifyOpenBalance;
window.arParseCustomerField = arParseCustomerField;
