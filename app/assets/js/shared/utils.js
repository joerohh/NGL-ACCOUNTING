// ══════════════════════════════════════════════════════════
//  UTILITIES — pure helpers (no DOM, no state)
// ══════════════════════════════════════════════════════════

export function uid() { return Math.random().toString(36).slice(2, 10); }

export function fmtSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1048576).toFixed(1) + ' MB';
}

export function escHtml(str) {
  const d = document.createElement('div');
  d.appendChild(document.createTextNode(String(str)));
  return d.innerHTML;
}

export function readAsArrayBuffer(file) {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload  = e => res(e.target.result);
    r.onerror = () => rej(new Error('Cannot read: ' + file.name));
    r.readAsArrayBuffer(file);
  });
}

export function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a   = Object.assign(document.createElement('a'), { href: url, download: filename });
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

// ── Log constants ──
export const LOG_PREFIXES = { info: '[INFO]', success: '[OK]  ', error: '[ERR] ', warning: '[WARN]' };
export const LOG_COLORS   = { info: '#94a3b8', success: '#4ade80', error: '#f87171', warning: '#fbbf24' };

// ── Fuzzy header matching ──
export function normalizeHeader(raw) {
  return String(raw).toLowerCase().replace(/[^a-z0-9]/g, '');
}

// Master alias lists — shared by Merge Tool and Invoice Sender.
// Update HERE to keep both tools in sync.
export const CSV_ALIASES = {
  invoiceNumber:   ['invoicenumber', 'invoice', 'invoiceid', 'invoiceno', 'inv', 'invno', 'invnumber', 'invnum', 'invid', 'docnumber', 'docno', 'invoicenum'],
  containerNumber: ['containernumber', 'container', 'containerid', 'containerno', 'cont', 'contno', 'contnumber', 'cntr', 'cntrnumber', 'cntrno', 'cntrid', 'ctr', 'ctrno', 'ctrnumber', 'equipment', 'equipmentnumber', 'equipmentno', 'equipmentid', 'eqno', 'eqnumber'],
  workOrderNumber: ['workordernumber', 'workorder', 'workorderno', 'workorderid', 'wo', 'wono', 'wonumber', 'woid', 'wonum'],
  customerName:    ['customername', 'customer', 'name', 'client', 'clientname', 'companyname', 'company'],
  invoiceDate:     ['invoicedate', 'date', 'invdate', 'docdate', 'createdate', 'txndate', 'transactiondate'],
  dueDate:         ['duedate', 'due', 'paymentdue', 'dueby', 'paydate'],
  amount:          ['amount', 'total', 'balance', 'amountdue', 'openbalance', 'totalamount', 'invoiceamount', 'balancedue', 'bill'],
  email:           ['email', 'emailaddress', 'billemail', 'contactemail', 'customeremail', 'billtoemail'],
  poNumber:        ['ponumber', 'po', 'purchaseorder', 'purchaseordernumber', 'pono', 'ponum', 'purchaseordernum'],
  bolNumber:       ['bolnumber', 'bol', 'billoflading', 'blnumber', 'bl', 'billofladingno'],
  customerCode:    ['customercode', 'custcode', 'customer_code', 'code', 'custid', 'customerid', 'custno', 'billto'],
  subject:         ['subject', 'emailsubject', 'subjectline', 'emailtitle'],
  doSenderEmail:   ['dosenderemail', 'do_sender_email', 'dosender', 'do_email', 'deliveryordersender', 'deliveryorderemail', 'do_sender', 'dosenderemailaddress'],
};

// Convenience shortcuts used by the Merge Tool
export const CONTAINER_ALIASES = CSV_ALIASES.containerNumber;
export const INVOICE_ALIASES   = CSV_ALIASES.invoiceNumber;
export const WO_ALIASES        = CSV_ALIASES.workOrderNumber;

export function findColumnKey(headers, aliases) {
  // Try exact normalized match first
  for (const header of headers) {
    const norm = normalizeHeader(header);
    if (aliases.includes(norm)) return header;
  }
  // Then try: header contains an alias (e.g. header "billingemail" contains alias "email")
  // Do NOT check if alias contains header — that causes false matches
  // (e.g. alias "ccemail" contains header "email", wrongly matching the Email column for CC)
  for (const header of headers) {
    const norm = normalizeHeader(header);
    if (aliases.some(a => norm.includes(a))) return header;
  }
  return null;
}

// ── Merge filename helpers — shared between merge.js (v1) and merge-v2.js (v2 in M4) ──

/** Returns "MM.DD" for today (e.g., "05.06"). */
export function getDatePrefix() {
  const d = new Date();
  return String(d.getMonth() + 1).padStart(2, '0') + '.' + String(d.getDate()).padStart(2, '0');
}

/**
 * Build the filename for a merged-output PDF.
 * Pattern: {datePrefix}_{key}_{container}_merged.pdf
 *   - key = INV# if present
 *   - else WO# if present
 *   - else (omitted entirely — collapses to {datePrefix}_{container}_merged.pdf)
 *
 * Both INV# and WO# are trimmed; empty strings count as "missing".
 *
 * @param {{containerNumber: string, invoiceNumber?: string, workOrderNumber?: string}} row
 * @param {string} datePrefix - typically from getDatePrefix()
 * @returns {string}
 */
export function buildMergedFilename(row, datePrefix) {
  if (!row.containerNumber) throw new Error('buildMergedFilename: row.containerNumber is required');
  const sanitize = s => String(s).replace(/[<>:"/\\|?*\x00-\x1f]/g, '_');
  const inv = (row.invoiceNumber || '').trim();
  const wo  = (row.workOrderNumber || '').trim();
  const key = inv || wo;
  const container = sanitize(row.containerNumber);
  if (key) return `${datePrefix}_${sanitize(key)}_${container}_merged.pdf`;
  return `${datePrefix}_${container}_merged.pdf`;
}

// ── Doc-fetch routing (M3) ──────────────────────────────────────────────────
// Decide whether a row is an import or export based on the INV# prefix
// (primary) and the WO# letter (fallback). See spec
// docs/superpowers/specs/2026-05-06-merge-tool-v2-m3-fetching-design.md.

/**
 * Parse the type letter at INV# position 2.
 * @returns {'import' | 'export' | null}
 */
export function parseInvType(inv) {
  if (!inv || inv.length < 2) return null;
  const c = inv[1].toUpperCase();
  if (c === 'M') return 'import';
  if (c === 'E') return 'export';
  return null;
}

/**
 * Parse the type letter from WO# (M=import, X=export, anywhere in the string).
 * @returns {'import' | 'export' | null}
 */
export function parseWoType(wo) {
  if (!wo) return null;
  const upper = wo.toUpperCase();
  if (upper.includes('X')) return 'export';
  if (upper.includes('M')) return 'import';
  return null;
}

/**
 * Decide the doc-fetch type for a row. INV# prefix is primary, WO# letter
 * is the fallback when the prefix doesn't parse.
 * @param {{invoiceNumber?: string, workOrderNumber?: string}} row
 * @returns {{ type: 'import' | 'export' | 'unknown', expectedDoc: 'POD' | 'BOL/POL' | '?' }}
 */
export function routingDecisionFor(row) {
  const fromInv = parseInvType(row.invoiceNumber);
  if (fromInv) {
    return { type: fromInv, expectedDoc: fromInv === 'import' ? 'POD' : 'BOL/POL' };
  }
  const fromWo = parseWoType(row.workOrderNumber);
  if (fromWo) {
    return { type: fromWo, expectedDoc: fromWo === 'import' ? 'POD' : 'BOL/POL' };
  }
  return { type: 'unknown', expectedDoc: '?' };
}
