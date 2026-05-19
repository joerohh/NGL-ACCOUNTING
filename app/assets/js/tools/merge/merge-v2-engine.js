// ══════════════════════════════════════════════════════════
//  MERGE TOOL V2 — engine module
//  pdf-lib-based merging across the 6 v2 modes.
//  Spec: docs/superpowers/specs/2026-05-07-merge-tool-v2-m4-merging-done-design.md
//
//  Task 17 (v2.72 warehouse): per-row merging now iterates `row.documents`
//  (the canonical, user-editable doc list built by merge-v2.js). This picks
//  up warehouse multi-attachment, manual side-panel adds, and side-panel
//  reordering automatically. Import/export rows still merge as
//  invoice + POD/BL/POL because their `row.documents` array contains
//  exactly those two entries.
// ══════════════════════════════════════════════════════════
import { agentBridge } from '../../shared/agent-client.js';
import {
  MODES, modeByKey,
  perInvoiceFilename, singleOutputFilename,
} from './merge-v2-output.js';

// ── Fetch a single agent file as ArrayBuffer ──
async function fetchAgentFile(jobId, filename) {
  const url = `${agentBridge.baseUrl}/files/${encodeURIComponent(jobId)}/${encodeURIComponent(filename)}`;
  const res = await agentBridge._authFetch(url);
  if (!res.ok) {
    if (res.status === 404) return null;   // file not present (e.g., document missing)
    throw new Error(`Fetch ${filename} failed: HTTP ${res.status}`);
  }
  return await res.arrayBuffer();
}

// ── Read a File/Blob as ArrayBuffer ──
function blobToArrayBuffer(blob) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = () => reject(r.error);
    r.readAsArrayBuffer(blob);
  });
}

// ── Identify the invoice entry in a row's documents array ──
//   The agent renames the QBO invoice file to `<container>_invoice.pdf`
//   for both import/export and warehouse rows, so the filename suffix is a
//   reliable marker. Warehouse attachments keep their original filenames.
function isInvoiceDoc(doc) {
  return /_invoice\.pdf$/i.test(doc.name || '');
}

// ── Resolve which agent job folder holds a given doc ──
//   Manual uploads (`source==='added'` with _file) come straight from the
//   browser File object. Otherwise, invoice files live under invoiceJobId
//   (or fallback), and document files (POD/BL/POL/warehouse attachments)
//   live under docJobId (or fallback). This mirrors the split in the old
//   preloadRowFiles() so POD-only retries don't clobber the invoice path.
function jobIdForDoc(doc, row, fallbackJobId) {
  if (isInvoiceDoc(doc)) {
    return row.invoiceJobId || row.fetchJobId || fallbackJobId;
  }
  return row.docJobId || row.fetchJobId || fallbackJobId;
}

// ── Fetch bytes for a single doc entry (manual file or agent-saved) ──
async function fetchDocBytes(doc, row, fallbackJobId) {
  if (doc._file) {
    return await blobToArrayBuffer(doc._file);
  }
  const jobId = jobIdForDoc(doc, row, fallbackJobId);
  if (!jobId) return null;
  return await fetchAgentFile(jobId, doc.name);
}

// ── Filter a row's documents for a given mode ──
//   - 'per-container' / 'combined'           : everything
//   - 'per-container-invoice' / 'invoice-only': only invoice doc(s)
//   - 'per-container-document' / 'document-only': everything except invoice
//   Errored docs (failReason set) are always excluded — they have nothing
//   on disk to merge.
function docsForMode(row, modeKey) {
  const docs = (row.documents || []).filter(d => !d.failReason);
  switch (modeKey) {
    case 'per-container':
    case 'combined':
      return docs;
    case 'per-container-invoice':
    case 'invoice-only':
      return docs.filter(isInvoiceDoc);
    case 'per-container-document':
    case 'document-only':
      return docs.filter(d => !isInvoiceDoc(d));
    default:
      throw new Error(`docsForMode: unknown mode ${modeKey}`);
  }
}

// ── Load all bytes for a row's selected docs (in array order) ──
//   Returns the buffers as an array, dropping any that failed to fetch.
//   Logs warnings (not errors) for missing files so a single bad attachment
//   doesn't kill the whole merge.
async function loadRowDocBytes(row, modeKey, fallbackJobId) {
  const include = docsForMode(row, modeKey);
  const out = [];
  for (const doc of include) {
    let bytes = null;
    try {
      bytes = await fetchDocBytes(doc, row, fallbackJobId);
    } catch (e) {
      console.warn(`[merge] fetch failed for "${doc.name}":`, e.message || e);
      continue;
    }
    if (!bytes || !bytes.byteLength) continue;
    out.push(bytes);
  }
  return out;
}

// ── Concatenate page-arrays into one PDFDocument and serialize ──
//   Returns { bytes, pageCount } so callers can tally pages without re-loading.
//   Per-doc load failures are caught so one bad PDF doesn't kill the merge.
async function concatPages(pageGroups) {
  const { PDFDocument } = PDFLib;
  const out = await PDFDocument.create();
  let pageCount = 0;
  for (const group of pageGroups) {
    if (!group) continue;
    try {
      const src = await PDFDocument.load(group, { ignoreEncryption: true, updateMetadata: false });
      const pages = await out.copyPages(src, src.getPageIndices());
      pages.forEach(p => out.addPage(p));
      pageCount += pages.length;
    } catch (e) {
      console.warn('[merge] PDFDocument.load failed for one doc, skipping:', e.message || e);
    }
  }
  const bytes = await out.save({ updateFieldAppearances: false });
  return { bytes, pageCount };
}

// ── Filename collision dedup helper ──
//   Returns the original name if unused, else appends _2, _3, ... until unique.
function uniqueFilename(name, usedSet) {
  if (!usedSet.has(name)) {
    usedSet.add(name);
    return name;
  }
  // Split off extension so we suffix the stem, not the whole name.
  const dot = name.lastIndexOf('.');
  const stem = dot > 0 ? name.slice(0, dot) : name;
  const ext  = dot > 0 ? name.slice(dot)  : '';
  for (let i = 2; i < 10000; i++) {
    const candidate = `${stem}_${i}${ext}`;
    if (!usedSet.has(candidate)) {
      usedSet.add(candidate);
      return candidate;
    }
  }
  // Astronomical fallback — should never reach here with sane inputs
  throw new Error(`uniqueFilename: 10000 collisions on ${name}`);
}

// ── Per-container modes ──
//   Walks `row.documents` so warehouse rows (multi-attachment), manual
//   side-panel adds, and side-panel reordering all flow through. Skips
//   rows whose filtered doc list comes back empty (e.g. document-only on a
//   row whose POD was never fetched).
async function runPerContainer(rows, jobId, modeKey, onProgress) {
  const files = [];
  const usedNames = new Set();
  let totalPages = 0;
  let totalBytes = 0;

  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    onProgress?.({ done: i, total: rows.length, current: row.containerNumber });

    const bufs = await loadRowDocBytes(row, modeKey, jobId);
    if (bufs.length === 0) continue;

    const { bytes: merged, pageCount } = await concatPages(bufs);
    if (!pageCount) continue;  // every doc failed to load — nothing to write

    const desiredName = perInvoiceFilename(row, modeKey);
    const finalName = uniqueFilename(desiredName, usedNames);
    files.push({ filename: finalName, bytes: merged });
    totalBytes += merged.byteLength;
    totalPages += pageCount;
  }

  onProgress?.({ done: rows.length, total: rows.length, current: '' });
  return { files, stats: { fileCount: files.length, totalPages, totalBytes } };
}

// ── Single-output modes ──
//   Same iteration over row.documents, but streamed into one output PDF
//   across all rows.
async function runCombined(rows, jobId, modeKey, onProgress) {
  const { PDFDocument } = PDFLib;
  const out = await PDFDocument.create();
  let totalPages = 0;

  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    onProgress?.({ done: i, total: rows.length, current: row.containerNumber });

    const bufs = await loadRowDocBytes(row, modeKey, jobId);
    for (const buf of bufs) {
      try {
        const src = await PDFDocument.load(buf, { ignoreEncryption: true, updateMetadata: false });
        const pages = await out.copyPages(src, src.getPageIndices());
        pages.forEach(p => out.addPage(p));
        totalPages += pages.length;
      } catch (e) {
        console.warn('[merge] PDFDocument.load failed for one doc, skipping:', e.message || e);
      }
    }
  }

  onProgress?.({ done: rows.length, total: rows.length, current: '' });

  if (totalPages === 0) {
    return { files: [], stats: { fileCount: 0, totalPages: 0, totalBytes: 0 } };
  }

  const bytes = await out.save({ updateFieldAppearances: false });
  return {
    files: [{ filename: singleOutputFilename(modeKey), bytes }],
    stats: { fileCount: 1, totalPages, totalBytes: bytes.byteLength },
  };
}

// ── Public dispatcher ──
//   rows: filtered subset (selected, non-skipped, sorted as user wants on Ready)
//   jobId: the v2State.jobId from the most-recent fetch (fallback when row.fetchJobId is unset)
//   modeKey: one of MODES[].key
//   onProgress: optional ({ done, total, current }) => void

export async function runMergeMode({ rows, jobId, modeKey, onProgress }) {
  const mode = modeByKey(modeKey);
  if (!mode) throw new Error(`Unknown mode: ${modeKey}`);
  if (!rows || rows.length === 0) {
    return { files: [], stats: { fileCount: 0, totalPages: 0, totalBytes: 0 } };
  }
  // Need at least one source of jobId per row — either the row's own fetchJobId or the
  // dispatcher fallback. If both are missing for every row, we have no way to find files.
  // (Manual-only rows are an exception, but in practice every row has at least an invoice
  // from the agent fetch, so we keep the guard.)
  const haveAnyJob = jobId || rows.some(r => r.fetchJobId || r.invoiceJobId || r.docJobId);
  if (!haveAnyJob) {
    throw new Error('runMergeMode: no jobId on rows or dispatcher — cannot locate fetched files');
  }
  if (mode.group === 'per-container') {
    return runPerContainer(rows, jobId, modeKey, onProgress);
  }
  return runCombined(rows, jobId, modeKey, onProgress);
}
