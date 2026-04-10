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
