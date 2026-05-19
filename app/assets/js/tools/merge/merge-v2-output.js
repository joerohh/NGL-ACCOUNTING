// ══════════════════════════════════════════════════════════
//  MERGE TOOL V2 — output module
//  - 6-mode metadata
//  - filename + path builders
//  - save flow (base64 + POST to agent)
//  Spec: docs/superpowers/specs/2026-05-07-merge-tool-v2-m4-merging-done-design.md
// ══════════════════════════════════════════════════════════
import { agentBridge } from '../../shared/agent-client.js';

// ── Mode metadata ──
//   key:         stable identifier used in state + filenames
//   group:       'per-container' | 'combined' (drives which row of cards on the screen)
//   title:       display name on the card
//   description: one-line subtitle on the card
//   subfolder:   first-level folder under "Merge Outputs/"

export const MODES = [
  // Per-invoice outputs (one PDF per invoice row)
  {
    key: 'per-container',
    group: 'per-container',
    title: 'Per Invoice',
    description: "One PDF per invoice. Each file contains that invoice and its supporting document combined.",
    subfolder: 'Per Invoice',
  },
  {
    key: 'per-container-invoice',
    group: 'per-container',
    title: 'Per Invoice — Invoice Only',
    description: 'One PDF per invoice, containing only the invoice itself.',
    subfolder: 'Per Invoice — Invoice Only',
  },
  {
    key: 'per-container-document',
    group: 'per-container',
    title: 'Per Invoice — Document Only',
    description: 'One PDF per invoice, containing only the supporting document — POD, BL, POL, IT, ITE, or warehouse attachments.',
    subfolder: 'Per Invoice — Document Only',
  },
  // Single combined output (one PDF total)
  {
    key: 'combined',
    group: 'combined',
    title: 'Combined PDF',
    description: 'Single PDF with every invoice and document stacked into one big file.',
    subfolder: 'Combined PDF',
  },
  {
    key: 'invoice-only',
    group: 'combined',
    title: 'Invoice Only',
    description: 'Single PDF containing all the invoices.',
    subfolder: 'Invoice Only',
  },
  {
    key: 'document-only',
    group: 'combined',
    title: 'Document Only',
    description: 'Single PDF containing all the supporting documents.',
    subfolder: 'Document Only',
  },
];

export function modeByKey(key) {
  return MODES.find(m => m.key === key) || null;
}

// ── Date helpers ──

export function dateFolderParts(d = new Date()) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return {
    monthFolder: `${y}-${m}`,           // "2026-05"
    dateFolder:  `${y}-${m}-${day}`,    // "2026-05-07"
    dateStamp:   `${y}-${m}-${day}`,    // same — used in filenames
  };
}

// ── Path builder ──
//   Returns the subfolder relative to baseLocation, e.g.:
//     "Per Invoice/2026-05/2026-05-07"

export function subfolderFor(modeKey, when = new Date()) {
  const mode = modeByKey(modeKey);
  if (!mode) throw new Error(`Unknown mode key: ${modeKey}`);
  const { monthFolder, dateFolder } = dateFolderParts(when);
  return `Merge Outputs/${mode.subfolder}/${monthFolder}/${dateFolder}`;
}

// ── Filename builders ──
//   Per-container modes: one filename per row.
//   Single-output modes: one filename total.

function sanitizeFilenamePart(s) {
  // Strip path separators and characters Windows rejects in filenames.
  return String(s || '').replace(/[\/\\:*?"<>|]/g, '_').trim();
}

export function perContainerFilename(row, modeKey) {
  // Use container as primary, fall back to WO# then INV#.
  const container = sanitizeFilenamePart(row.containerNumber);
  const inv = sanitizeFilenamePart(row.invoiceNumber);
  const wo = sanitizeFilenamePart(row.workOrderNumber);
  const stem = container || wo || inv || `row-${row.rowNum}`;
  const invSuffix = inv ? `_${inv}` : '';

  if (modeKey === 'per-container') {
    return `${stem}${invSuffix}.pdf`;
  }
  if (modeKey === 'per-container-invoice') {
    return `${stem}${invSuffix}_INV.pdf`;
  }
  if (modeKey === 'per-container-document') {
    // podLabel is one of POD/BL/POL/IT/ITE on success or '—' on miss/error.
    // Treat the em-dash sentinel as "no label" so we don't emit "FOO_—.pdf".
    const rawLabel = row.fetchResult?.podLabel;
    const docLabel = (rawLabel && rawLabel !== '—')
      ? sanitizeFilenamePart(rawLabel)
      : 'DOC';
    return `${stem}${invSuffix}_${docLabel}.pdf`;
  }
  throw new Error(`perContainerFilename: not a per-container mode: ${modeKey}`);
}

export function singleOutputFilename(modeKey, when = new Date()) {
  const { dateStamp } = dateFolderParts(when);
  const map = {
    'combined':      `Combined_${dateStamp}.pdf`,
    'invoice-only':  `Invoices_${dateStamp}.pdf`,
    'document-only': `Documents_${dateStamp}.pdf`,
  };
  const f = map[modeKey];
  if (!f) throw new Error(`singleOutputFilename: not a single-output mode: ${modeKey}`);
  return f;
}

// ── Save flow ──
//   files: [{ filename, bytes: Uint8Array }]
//   baseLocation: absolute path the user picked (or null → agent uses OUTPUT_DIR)
//   modeKey: drives the subfolder
//   openFolder: whether to open Explorer at the target after writing

export async function saveMergedFiles({ files, modeKey, baseLocation, openFolder = false }) {
  if (!Array.isArray(files) || files.length === 0) {
    return { error: 'No files to save' };
  }
  const subfolder = subfolderFor(modeKey);

  // Convert each Uint8Array → base64. Chunk to avoid call-stack blowups on large PDFs.
  const items = files.map(f => {
    const bytes = f.bytes instanceof Uint8Array ? f.bytes : new Uint8Array(f.bytes);
    let binary = '';
    const CHUNK = 8192;
    for (let i = 0; i < bytes.length; i += CHUNK) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
    }
    return { filename: f.filename, data: btoa(binary), subfolder };
  });

  return agentBridge.saveBatchOutput({
    files: items,
    baseLocation,
    overwriteFolder: true,   // M4 spec: same-day overwrite is the default
    openFolder,
  });
}
