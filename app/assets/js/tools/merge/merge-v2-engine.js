// ══════════════════════════════════════════════════════════
//  MERGE TOOL V2 — engine module
//  pdf-lib-based merging across the 6 v2 modes.
//  Spec: docs/superpowers/specs/2026-05-07-merge-tool-v2-m4-merging-done-design.md
// ══════════════════════════════════════════════════════════
import { agentBridge } from '../../shared/agent-client.js';
import {
  MODES, modeByKey,
  perContainerFilename, singleOutputFilename,
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

// ── Pre-load both files for one row. Returns { invoiceBuf, docBuf } (either may be null). ──
//   Uses row.fetchJobId (set when the SSE event landed) so rows from different fetch jobs
//   each look in the right downloads/{jobId}/ folder. Falls back to the dispatcher's jobId.
async function preloadRowFiles(fallbackJobId, row) {
  const jobId = row.fetchJobId || fallbackJobId;
  if (!jobId) return { invoiceBuf: null, docBuf: null };
  const cn = row.containerNumber;
  const [invoiceBuf, docBuf] = await Promise.all([
    fetchAgentFile(jobId, `${cn}_invoice.pdf`),
    // Errored rows skip the doc fetch entirely — saves a 404 round-trip.
    row.fetchResult?.podPill === 'miss'
      ? Promise.resolve(null)
      : fetchAgentFile(jobId, `${cn}_pod.pdf`),
  ]);
  return { invoiceBuf, docBuf };
}

// ── Concatenate page-arrays into one PDFDocument and serialize ──
//   Returns { bytes, pageCount } so callers can tally pages without re-loading.
async function concatPages(pageGroups) {
  const { PDFDocument } = PDFLib;
  const out = await PDFDocument.create();
  let pageCount = 0;
  for (const group of pageGroups) {
    if (!group) continue;
    const src = await PDFDocument.load(group, { ignoreEncryption: true, updateMetadata: false });
    const pages = await out.copyPages(src, src.getPageIndices());
    pages.forEach(p => out.addPage(p));
    pageCount += pages.length;
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
async function runPerContainer(rows, jobId, modeKey, onProgress) {
  const files = [];
  const usedNames = new Set();
  let totalPages = 0;
  let totalBytes = 0;

  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    onProgress?.({ done: i, total: rows.length, current: row.containerNumber });

    const { invoiceBuf, docBuf } = await preloadRowFiles(jobId, row);
    if (!invoiceBuf && !docBuf) continue;  // nothing to merge for this row

    let bufs;
    if (modeKey === 'per-container')               bufs = [invoiceBuf, docBuf];
    else if (modeKey === 'per-container-invoice')  bufs = [invoiceBuf];
    else if (modeKey === 'per-container-document') bufs = [docBuf];
    else throw new Error(`runPerContainer: not a per-container mode: ${modeKey}`);

    bufs = bufs.filter(Boolean);
    if (bufs.length === 0) continue;

    const { bytes: merged, pageCount } = await concatPages(bufs);
    const desiredName = perContainerFilename(row, modeKey);
    const finalName = uniqueFilename(desiredName, usedNames);
    files.push({ filename: finalName, bytes: merged });
    totalBytes += merged.byteLength;
    totalPages += pageCount;
  }

  onProgress?.({ done: rows.length, total: rows.length, current: '' });
  return { files, stats: { fileCount: files.length, totalPages, totalBytes } };
}

// ── Single-output modes ──
async function runCombined(rows, jobId, modeKey, onProgress) {
  const { PDFDocument } = PDFLib;
  // We can't reuse concatPages here — we keep one out doc alive across rows
  // to stream pages straight in, so the final PDF is built incrementally.
  const out = await PDFDocument.create();
  let totalPages = 0;

  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    onProgress?.({ done: i, total: rows.length, current: row.containerNumber });

    const { invoiceBuf, docBuf } = await preloadRowFiles(jobId, row);

    let bufs;
    if (modeKey === 'combined')           bufs = [invoiceBuf, docBuf];
    else if (modeKey === 'invoice-only')  bufs = [invoiceBuf];
    else if (modeKey === 'document-only') bufs = [docBuf];
    else throw new Error(`runCombined: not a combined mode: ${modeKey}`);

    for (const buf of bufs) {
      if (!buf) continue;
      const src = await PDFDocument.load(buf, { ignoreEncryption: true, updateMetadata: false });
      const pages = await out.copyPages(src, src.getPageIndices());
      pages.forEach(p => out.addPage(p));
      totalPages += pages.length;
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
  const haveAnyJob = jobId || rows.some(r => r.fetchJobId);
  if (!haveAnyJob) {
    throw new Error('runMergeMode: no jobId on rows or dispatcher — cannot locate fetched files');
  }
  if (mode.group === 'per-container') {
    return runPerContainer(rows, jobId, modeKey, onProgress);
  }
  return runCombined(rows, jobId, modeKey, onProgress);
}
