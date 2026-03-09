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

export const CONTAINER_ALIASES = [
  'containernumber', 'container', 'containerid', 'containerno',
  'cont', 'contno', 'contnumber', 'cntr', 'cntrnumber', 'cntrno', 'cntrid',
  'ctr', 'ctrno', 'ctrnumber', 'equipment', 'equipmentnumber', 'equipmentno', 'equipmentid', 'eqno', 'eqnumber',
];
export const INVOICE_ALIASES = [
  'invoicenumber', 'invoice', 'invoiceid', 'invoiceno',
  'inv', 'invno', 'invnumber', 'invnum', 'invid',
  'docnumber', 'docno',
];

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
