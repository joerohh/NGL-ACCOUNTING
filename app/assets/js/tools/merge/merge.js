// ══════════════════════════════════════════════════════════
//  MERGE TOOL — Excel parsing, PDF handling, merge logic
// ══════════════════════════════════════════════════════════
import { state } from '../../shared/state.js';
import {
  uid, fmtSize, escHtml, readAsArrayBuffer, triggerDownload,
  normalizeHeader, findColumnKey, CONTAINER_ALIASES, INVOICE_ALIASES,
} from '../../shared/utils.js';
import { addLog, setProgress } from '../../shared/log.js';
import { agentBridge } from '../../shared/agent-client.js';

// ── Document Type Registry ──
// To add a new type: just add an entry here. Everything else adapts automatically.
const DOC_TYPES = [
  { key: 'invoice', label: 'Invoice',  plural: 'Invoices', pattern: /invoice|inv[_\s\-]|billing/i,                        priority: 0 },
  { key: 'pod',     label: 'POD',      plural: 'PODs',     pattern: /pod|proof[_\s\-]?of[_\s\-]?delivery|delivery/i,      priority: 1 },
  { key: 'bl',      label: 'BL',       plural: 'BLs',      pattern: /\bbl\b|bol|bill[_\s\-]?of[_\s\-]?lading/i,          priority: 2 },
];

// ── Register hook: agent-client calls this when a file is injected ──
agentBridge.hooks.onFileInjected = function(name) {
  addLog('success', `[Agent] Injected: ${name}`);
  renderPdfQueue();
  if (state.mode === 'auto') renderContainerGroups();
  updateQueueCount();
};

// ── Merge Mode & Sort Controls ──
const RUN_BTN_SVG = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>';

function setMergeMode(mode) {
  state.mergeMode = mode;
  document.querySelectorAll('#mergeModeGroup .merge-opt').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.mode === mode);
  });
  updateRunButtonLabel();
}

function setSortOrder(order) {
  state.sortOrder = order;
  document.querySelectorAll('#sortOrderGroup .merge-opt').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.sort === order);
  });
}

function updateRunButtonLabel() {
  const btn = document.getElementById('runAutoBtn');
  if (!btn || state.isProcessing) return;
  const labels = {
    'per-container': 'Run Auto Merge',
    'all-in-one':    'Merge All Into One PDF',
    ...Object.fromEntries(DOC_TYPES.map(dt => [`${dt.key}-only`, `Merge ${dt.plural} Only`])),
  };
  btn.innerHTML = `${RUN_BTN_SVG} ${labels[state.mergeMode] || 'Run Auto Merge'}`;
}

// ── Logging: addLog, clearLog, toggleLog, setProgress → shared/log.js ──


// ── Mode Management ──
export function setMode(mode) {
  state.mode = mode;
  const badge = document.getElementById('modeBadge');
  const dot   = document.getElementById('modeDot');
  const label = document.getElementById('modeLabel');
  badge.className = 'mode-badge';

  const idleHint     = document.getElementById('idleHint');
  const autoActions  = document.getElementById('autoActions');
  const manualActions= document.getElementById('manualActions');
  const pdfQueue     = document.getElementById('pdfQueue');
  const groupsView   = document.getElementById('containerGroupsView');

  if (mode === 'idle') {
    badge.classList.add('mode-idle');
    dot.className = 'badge-dot idle-dot';
    label.textContent = 'Idle';
    idleHint.style.display = '';
    autoActions.style.display = 'none';
    manualActions.style.display = 'none';
    pdfQueue.style.display = '';
    groupsView.style.display = 'none';
  } else if (mode === 'auto') {
    badge.classList.add('mode-auto');
    dot.className = 'badge-dot auto-dot';
    label.textContent = 'Auto Mode';
    idleHint.style.display = 'none';
    autoActions.style.display = 'flex';
    manualActions.style.display = 'none';
    pdfQueue.style.display = 'none';
    groupsView.style.display = '';
  } else if (mode === 'manual') {
    badge.classList.add('mode-manual');
    dot.className = 'badge-dot manual-dot';
    label.textContent = 'Manual Mode';
    idleHint.style.display = 'none';
    autoActions.style.display = 'none';
    manualActions.style.display = '';
    pdfQueue.style.display = '';
    groupsView.style.display = 'none';
  }

  updateQueueCount();
}

export function updateQueueCount() {
  const el = document.getElementById('queueCount');
  const n  = state.pdfs.length;
  el.textContent = n === 0 ? '0 files' : `${n} file${n !== 1 ? 's' : ''}`;
}


// ── Excel Handling ──
function handleExcelInputChange(input) {
  if (input.files && input.files[0]) handleExcelFile(input.files[0]);
}

export async function handleExcelFile(file) {
  addLog('info', `Parsing: ${file.name}`);
  try {
    const buf = await readAsArrayBuffer(file);
    const wb  = XLSX.read(buf, { type: 'array' });
    const ws  = wb.Sheets[wb.SheetNames[0]];
    const rows = XLSX.utils.sheet_to_json(ws);

    if (rows.length === 0) {
      addLog('error', 'Excel file is empty or unreadable');
      return;
    }

    const headers = Object.keys(rows[0]);

    // Fuzzy column detection
    const containerKey = findColumnKey(headers, CONTAINER_ALIASES);
    const invoiceKey   = findColumnKey(headers, INVOICE_ALIASES);

    if (!containerKey) {
      addLog('error', 'No "Container Number" column found');
      addLog('warning', `Found columns: ${headers.join(', ')}`);
      addLog('info', 'Expected something like "Container Number", "CONT #", "Cont#", "CNTR", etc.');
      return;
    }

    addLog('info', `Matched container column: "${containerKey}"`);
    if (invoiceKey) {
      addLog('info', `Matched invoice column: "${invoiceKey}"`);
    } else {
      addLog('warning', `No invoice number column found — agent will search by container number instead`);
      addLog('info', `Available columns: ${headers.join(', ')}`);
    }

    // Parse rows — deduplicate by container number
    const seen = new Set();
    const parsed = [];
    for (const row of rows) {
      const cn = String(row[containerKey] || '').trim();
      if (!cn || seen.has(cn.toLowerCase())) continue;
      seen.add(cn.toLowerCase());
      parsed.push({
        containerNumber: cn,
        invoiceNumber: invoiceKey ? String(row[invoiceKey] || '').trim() : '',
      });
    }

    if (parsed.length === 0) {
      addLog('error', 'No valid container numbers found in the spreadsheet');
      return;
    }

    state.excelRows = parsed;
    addLog('success', `Parsed ${parsed.length} container numbers from "${file.name}"`);
    parsed.slice(0, 5).forEach(r => {
      const inv = r.invoiceNumber ? ` (Inv: ${r.invoiceNumber})` : '';
      addLog('info', `  → ${r.containerNumber}${inv}`);
    });
    if (parsed.length > 5) addLog('info', `  → ...and ${parsed.length - 5} more`);

    // Update drop zone UI
    const dz   = document.getElementById('excelDropZone');
    const drop = document.getElementById('excelDropContent');
    const loaded = document.getElementById('excelLoadedState');
    dz.classList.add('has-file');
    drop.style.display = 'none';
    loaded.style.display = 'flex';
    document.getElementById('excelFileName').textContent = file.name;
    const invCount = parsed.filter(r => r.invoiceNumber).length;
    document.getElementById('excelFileSub').textContent =
      `${parsed.length} containers` + (invCount ? ` · ${invCount} invoice numbers` : '');

    setMode('auto');
    renderContainerGroups();
    addLog('info', 'Now upload the PDF documents to match against these containers');

  } catch (err) {
    addLog('error', 'Failed to parse Excel: ' + err.message);
  }
}

function removeExcel() {
  state.excelRows = [];
  document.getElementById('excelInput').value = '';
  document.getElementById('excelDropZone').classList.remove('has-file');
  document.getElementById('excelDropContent').style.display = '';
  document.getElementById('excelLoadedState').style.display = 'none';
  document.getElementById('containerGroupsView').innerHTML = '';
  document.getElementById('failureReport').style.display = 'none';
  document.getElementById('saveOutputBtn').style.display = 'none';
  state.mergeResults = [];

  setMode(state.pdfs.length > 0 ? 'manual' : 'idle');
  addLog('info', 'Excel manifest removed — switched to Manual Mode');
}


// ── PDF Handling ──
function handlePdfInputChange(input) {
  if (input.files && input.files.length > 0) {
    handlePdfFiles(Array.from(input.files));
    input.value = '';
  }
}

export function handlePdfFiles(files) {
  const pdfs    = files.filter(f => /\.pdf$/i.test(f.name));
  const skipped = files.length - pdfs.length;
  if (skipped > 0) addLog('warning', `Skipped ${skipped} non-PDF file(s)`);
  if (pdfs.length === 0) return;

  const newPdfs = pdfs.map(f => ({ id: uid(), name: f.name, size: f.size, file: f }));
  state.pdfs.push(...newPdfs);
  addLog('info', `Added ${newPdfs.length} PDF${newPdfs.length !== 1 ? 's' : ''} to queue`);

  if (state.mode !== 'auto') setMode('manual');

  renderPdfQueue();
  if (state.mode === 'auto') renderContainerGroups();
  updateQueueCount();
}

function removePdf(id) {
  state.pdfs = state.pdfs.filter(p => p.id !== id);
  renderPdfQueue();
  if (state.mode === 'auto') renderContainerGroups();
  updateQueueCount();
  if (state.pdfs.length === 0 && state.mode === 'manual') setMode('idle');
}


// ── Rendering — PDF Queue (Manual Mode) ──
const ICON_PDF = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#ea580c" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>`;
const ICON_GRIP = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="9" cy="5" r="1"/><circle cx="9" cy="12" r="1"/><circle cx="9" cy="19" r="1"/><circle cx="15" cy="5" r="1"/><circle cx="15" cy="12" r="1"/><circle cx="15" cy="19" r="1"/></svg>`;
const ICON_TRASH = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4h6v2"/></svg>`;

const EMPTY_QUEUE_HTML = `
  <div style="display:flex; flex-direction:column; align-items:center; justify-content:center; min-height:170px; text-align:center; padding:20px;">
    <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="#d1d5db" stroke-width="1.5" style="margin-bottom:12px;">
      <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/>
    </svg>
    <div style="font-size:0.9rem; color:#94a3b8;">No documents in queue</div>
    <div style="font-size:0.8rem; color:#cbd5e1; margin-top:4px;">Drop PDFs above to get started</div>
  </div>`;

export function renderPdfQueue() {
  const queue = document.getElementById('pdfQueue');

  if (state.pdfs.length === 0) {
    queue.innerHTML = EMPTY_QUEUE_HTML;
    if (state.sortableInstance) { state.sortableInstance.destroy(); state.sortableInstance = null; }
    return;
  }

  queue.innerHTML = state.pdfs.map((pdf, i) => `
    <div class="pdf-card" data-id="${pdf.id}">
      <span class="drag-handle" title="Drag to reorder">${ICON_GRIP}</span>
      ${ICON_PDF}
      <div style="flex:1; min-width:0;">
        <div style="font-size:0.9rem; font-weight:500; color:#1e293b; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${escHtml(pdf.name)}</div>
        <div style="font-size:0.8rem; color:#94a3b8; margin-top:2px;">${fmtSize(pdf.size)} &nbsp;·&nbsp; Position ${i + 1}</div>
      </div>
      <button
        onclick="removePdf('${pdf.id}')"
        style="background:none; border:none; cursor:pointer; color:#cbd5e1; flex-shrink:0; padding:5px; border-radius:5px; line-height:0;"
        onmouseover="this.style.color='#dc2626'; this.style.background='#fef2f2'"
        onmouseout="this.style.color='#cbd5e1'; this.style.background='none'"
        title="Remove">${ICON_TRASH}
      </button>
    </div>`).join('');

  updateQueueCount();
  initSortable();
}


// ── Rendering — Container Groups (Auto Mode) ──
export function classifyPdf(name) {
  for (const dt of DOC_TYPES) {
    if (dt.pattern.test(name)) return dt.key;
  }
  return 'other';
}

export function renderContainerGroups() {
  const view = document.getElementById('containerGroupsView');
  if (state.excelRows.length === 0) { view.innerHTML = ''; return; }

  view.innerHTML = state.excelRows.map(row => {
    const matched = state.pdfs.filter(p =>
      p.name.toLowerCase().includes(row.containerNumber.toLowerCase())
    );
    const isMatch = matched.length > 0;

    // Classify matched files by type
    const byType = {};
    for (const dt of DOC_TYPES) byType[dt.key] = matched.filter(p => classifyPdf(p.name) === dt.key);
    const otherFiles = matched.filter(p => classifyPdf(p.name) === 'other');

    return `
      <div class="container-group ${isMatch ? 'matched' : 'unmatched'}">
        <div class="container-group-header">
          <span style="font-size:0.95rem; font-weight:700; color:#0f172a; font-family:monospace; letter-spacing:0.02em;">${escHtml(row.containerNumber)}</span>
          <span style="margin-left:auto; font-size:0.8rem; font-weight:600; color:${isMatch ? '#16a34a' : '#d97706'};">
            ${isMatch ? `${matched.length} file${matched.length !== 1 ? 's' : ''}` : 'No match'}
          </span>
        </div>

        ${DOC_TYPES.map(dt => {
          const files = byType[dt.key];
          const has = files.length > 0;
          return `<div class="doc-check-row">
            <input type="checkbox" ${has ? 'checked' : ''} disabled />
            <span class="doc-check-label">${dt.label}</span>
            ${has
              ? files.map(f => `<span class="doc-check-file" title="${escHtml(f.name)}">${escHtml(f.name)}</span>`).join('')
              : `<span class="doc-check-missing">Not uploaded</span>`
            }
          </div>`;
        }).join('')}

        ${otherFiles.length > 0 ? otherFiles.map(f => `
          <div class="doc-check-row">
            <input type="checkbox" checked disabled />
            <span class="doc-check-label">Other</span>
            <span class="doc-check-file" title="${escHtml(f.name)}">${escHtml(f.name)}</span>
          </div>`).join('') : ''}

        ${!isMatch ? `<div style="font-size:0.8rem; color:#94a3b8; padding:4px 6px; margin-top:2px;">Upload a PDF with "${escHtml(row.containerNumber)}" in the filename</div>` : ''}
        ${row.invoiceNumber ? `<div style="font-size:0.75rem; color:#94a3b8; padding:2px 6px;">Invoice #: ${escHtml(row.invoiceNumber)}</div>` : ''}
      </div>`;
  }).join('');

  updateQueueCount();
}


// ── Sortable ──
function initSortable() {
  if (state.sortableInstance) { state.sortableInstance.destroy(); state.sortableInstance = null; }
  if (state.pdfs.length <= 1) return;

  state.sortableInstance = new Sortable(document.getElementById('pdfQueue'), {
    animation: 200,
    easing: 'cubic-bezier(0.25, 1, 0.5, 1)',
    ghostClass: 'sortable-ghost',
    chosenClass: 'sortable-chosen',
    dragClass: 'sortable-drag',
    handle: '.drag-handle',
    onEnd(evt) {
      const moved = state.pdfs.splice(evt.oldIndex, 1)[0];
      state.pdfs.splice(evt.newIndex, 0, moved);
      setTimeout(() => renderPdfQueue(), 0);
    },
  });
}


// ── PDF Merging (core) ──
async function mergePdfFiles(pdfs) {
  const { PDFDocument } = PDFLib;
  const merged = await PDFDocument.create();

  for (const pdf of pdfs) {
    try {
      const buf  = await readAsArrayBuffer(pdf.file);
      const doc  = await PDFDocument.load(buf, { ignoreEncryption: true });
      const pages = await merged.copyPages(doc, doc.getPageIndices());
      pages.forEach(p => merged.addPage(p));
    } catch (err) {
      throw new Error(`"${pdf.name}": ${err.message}`);
    }
  }

  return merged.save();
}


// ── Performance Helpers ──
async function preloadAllPdfs(pdfs) {
  const bufferMap = new Map();
  await Promise.all(pdfs.map(async (pdf) => {
    bufferMap.set(pdf.id, await readAsArrayBuffer(pdf.file));
  }));
  return bufferMap;
}

function buildMatchIndex(rows, pdfs) {
  const index = new Map();
  const entries = pdfs.map(p => ({ pdf: p, lower: p.name.toLowerCase() }));
  for (const row of rows) {
    const cn = row.containerNumber.toLowerCase();
    index.set(row.containerNumber, entries.filter(e => e.lower.includes(cn)).map(e => e.pdf));
  }
  return index;
}

function getWorkerCount() {
  const cores = navigator.hardwareConcurrency || 4;
  return Math.max(2, Math.min(6, Math.floor(cores / 2) - 1));
}

function createWorkerPool(size) {
  const resolvers = new Map();
  const workers = [];
  for (let i = 0; i < size; i++) {
    const w = new Worker(new URL('./merge-worker.js', import.meta.url));
    w.onmessage = (e) => {
      const r = resolvers.get(e.data.taskId);
      if (r) { resolvers.delete(e.data.taskId); r(e.data); }
    };
    w.onerror = () => {
      state._workersFailed = true;
      for (const [, r] of resolvers) r({ type: 'error', error: 'Worker failed' });
      resolvers.clear();
    };
    workers.push(w);
  }
  let robin = 0;
  return {
    submit(task) {
      return new Promise((resolve) => {
        const taskId = uid();
        resolvers.set(taskId, resolve);
        const bufs = task.pdfBuffers.map(b => b.slice(0));
        workers[robin++ % size].postMessage(
          { ...task, taskId, pdfBuffers: bufs }, bufs
        );
      });
    },
    terminate() { workers.forEach(w => w.terminate()); },
  };
}

async function mergePerContainerSequential(rows, failures, bufferMap, matchIndex) {
  const { PDFDocument } = PDFLib;
  const total = rows.length;
  const datePrefix = getDatePrefix();
  const subfolder = SUBFOLDER_MAP['per-container'];

  addLog('info', 'Pre-parsing PDF documents…');
  const docCache = new Map();
  for (const pdf of state.pdfs) {
    const buf = bufferMap.get(pdf.id);
    if (!buf) continue;
    try {
      docCache.set(pdf.id, await PDFDocument.load(buf, { ignoreEncryption: true, updateMetadata: false }));
    } catch (err) {
      addLog('warning', `Cannot parse ${pdf.name}: ${err.message}`);
    }
  }

  for (let i = 0; i < total; i++) {
    const row = rows[i];
    const t0 = performance.now();
    setProgress(Math.round((i / total) * 100), `Processing ${i + 1} / ${total}: ${row.containerNumber}`);

    const matched = matchIndex.get(row.containerNumber) || [];
    if (!matched.length) {
      addLog('warning', `No match for ${row.containerNumber} — skipped`);
      failures.push({ containerNumber: row.containerNumber, reason: 'No matching PDF filename' });
      continue;
    }

    const ordered = getOrderedMatchedPdfs(matched);
    addLog('info', `  Found: ${ordered.map(m => m.name).join(', ')}`);

    try {
      const merged = await PDFDocument.create();
      for (const pdf of ordered) {
        const doc = docCache.get(pdf.id);
        if (!doc) throw new Error(`"${pdf.name}": not parsed`);
        const pages = await merged.copyPages(doc, doc.getPageIndices());
        pages.forEach(p => merged.addPage(p));
      }
      const bytes = await merged.save({
        objectsPerTick: Infinity,
        updateFieldAppearances: false,
        addDefaultPage: false,
      });
      const ms = Math.round(performance.now() - t0);
      const filename = `${datePrefix}_${row.containerNumber}_merged.pdf`;
      state.mergeResults.push({ containerNumber: row.containerNumber, bytes, filename, subfolder });
      addLog('success', `  → ${filename} (${fmtSize(bytes.length)}) [${ms}ms]`);
    } catch (err) {
      addLog('error', `Failed to merge ${row.containerNumber}: ${err.message}`);
      failures.push({ containerNumber: row.containerNumber, reason: err.message });
    }

    if (i % 10 === 9) await new Promise(r => setTimeout(r, 0));
  }
}


// ── Merge Helpers ──
function getDatePrefix() {
  const d = new Date();
  return String(d.getMonth() + 1).padStart(2, '0') + '.' + String(d.getDate()).padStart(2, '0');
}

function getSortedRows() {
  const rows = [...state.excelRows];
  if (state.sortOrder === 'container') {
    rows.sort((a, b) => a.containerNumber.localeCompare(b.containerNumber, undefined, { numeric: true }));
  } else if (state.sortOrder === 'invoice') {
    rows.sort((a, b) => (a.invoiceNumber || '').localeCompare(b.invoiceNumber || '', undefined, { numeric: true }));
  }
  return rows;
}

function getOrderedMatchedPdfs(matched) {
  const priority = Object.fromEntries(DOC_TYPES.map(dt => [dt.key, dt.priority]));
  const fallback = DOC_TYPES.length + 1;
  return [...matched].sort((a, b) =>
    (priority[classifyPdf(a.name)] ?? fallback) - (priority[classifyPdf(b.name)] ?? fallback)
  );
}

const SUBFOLDER_MAP = {
  'per-container': 'One to One Merge',
  'all-in-one':    'All Documents Merged',
  ...Object.fromEntries(DOC_TYPES.map(dt => [`${dt.key}-only`, `${dt.plural} Only`])),
};

async function mergePerContainer(rows, failures, bufferMap, matchIndex) {
  const total = rows.length;
  const datePrefix = getDatePrefix();
  const subfolder = SUBFOLDER_MAP['per-container'];

  // Try Web Workers for parallel merging
  if (window.Worker && !state._workersFailed) {
    let pool;
    try {
      const workerCount = getWorkerCount();
      pool = createWorkerPool(workerCount);
      addLog('info', `Using ${workerCount} parallel workers (${navigator.hardwareConcurrency || '?'} logical cores)`);
    } catch (err) {
      state._workersFailed = true;
      addLog('warning', 'Workers unavailable, using sequential merge');
      return mergePerContainerSequential(rows, failures, bufferMap, matchIndex);
    }

    let completed = 0;
    const tasks = [];

    for (const row of rows) {
      const matched = matchIndex.get(row.containerNumber) || [];
      if (!matched.length) {
        addLog('warning', `No match for ${row.containerNumber} — skipped`);
        failures.push({ containerNumber: row.containerNumber, reason: 'No matching PDF filename' });
        completed++;
        setProgress(Math.round((completed / total) * 100), `${completed} / ${total} containers`);
        continue;
      }

      const ordered = getOrderedMatchedPdfs(matched);
      addLog('info', `  ${row.containerNumber}: ${ordered.map(m => m.name).join(', ')}`);

      const pdfBuffers = ordered.map(p => bufferMap.get(p.id));
      const pdfNames = ordered.map(p => p.name);
      const filename = `${datePrefix}_${row.containerNumber}_merged.pdf`;

      const task = pool.submit({
        containerNumber: row.containerNumber,
        pdfBuffers, pdfNames, filename, subfolder,
      }).then((result) => {
        completed++;
        setProgress(Math.round((completed / total) * 100), `${completed} / ${total} containers`);
        if (result.type === 'error') {
          addLog('error', `Failed to merge ${result.containerNumber}: ${result.error}`);
          failures.push({ containerNumber: result.containerNumber, reason: result.error });
        } else {
          state.mergeResults.push({
            containerNumber: result.containerNumber,
            bytes: result.mergedBytes,
            filename: result.filename,
            subfolder: result.subfolder,
          });
          addLog('success', `  → ${result.filename} (${fmtSize(result.mergedBytes.length)})`);
        }
      });
      tasks.push(task);
    }

    await Promise.all(tasks);
    pool.terminate();
    return;
  }

  // Sequential fallback (file:// protocol or Workers blocked)
  return mergePerContainerSequential(rows, failures, bufferMap, matchIndex);
}

async function mergeAllInOne(rows, failures, bufferMap, matchIndex) {
  const { PDFDocument } = PDFLib;
  const allPdfs = [];
  const total = rows.length;
  const subfolder = SUBFOLDER_MAP['all-in-one'];

  for (let i = 0; i < total; i++) {
    const row = rows[i];
    setProgress(Math.round((i / total) * 80), `Collecting ${i + 1} / ${total}: ${row.containerNumber}`);

    const matched = matchIndex.get(row.containerNumber) || [];
    if (!matched.length) {
      addLog('warning', `No match for ${row.containerNumber} — skipped`);
      failures.push({ containerNumber: row.containerNumber, reason: 'No matching PDF filename' });
      continue;
    }

    const ordered = getOrderedMatchedPdfs(matched);
    allPdfs.push(...ordered);
    addLog('info', `  ${row.containerNumber}: ${ordered.map(m => m.name).join(', ')}`);
  }

  if (!allPdfs.length) { addLog('error', 'No PDFs to merge'); return; }

  setProgress(85, `Merging ${allPdfs.length} PDFs into one document…`);
  addLog('info', `Merging ${allPdfs.length} total PDFs into one document`);

  try {
    const merged = await PDFDocument.create();
    for (const pdf of allPdfs) {
      const buf = bufferMap.get(pdf.id);
      const doc = await PDFDocument.load(buf, { ignoreEncryption: true, updateMetadata: false });
      const pages = await merged.copyPages(doc, doc.getPageIndices());
      pages.forEach(p => merged.addPage(p));
    }
    const bytes = await merged.save({ objectsPerTick: Infinity, updateFieldAppearances: false, addDefaultPage: false });
    const containerCount = state.excelRows.length;
    const filename = `${getDatePrefix()}_${containerCount}_merged.pdf`;
    state.mergeResults.push({ containerNumber: 'ALL', bytes, filename, subfolder });
    addLog('success', `→ ${filename} (${fmtSize(bytes.length)})`);
  } catch (err) {
    addLog('error', 'Merge failed: ' + err.message);
  }
}

async function mergeByType(rows, failures, bufferMap, matchIndex, type) {
  const { PDFDocument } = PDFLib;
  const typePdfs = [];
  const total = rows.length;
  const dt = DOC_TYPES.find(d => d.key === type);
  const typeLabel = dt ? dt.plural : type;
  const subfolder = SUBFOLDER_MAP[`${type}-only`] || `${typeLabel} Only`;

  for (let i = 0; i < total; i++) {
    const row = rows[i];
    setProgress(Math.round((i / total) * 80), `Scanning ${i + 1} / ${total}: ${row.containerNumber}`);

    const matched = matchIndex.get(row.containerNumber) || [];
    const typeFiles = matched.filter(p => classifyPdf(p.name) === type);

    if (!typeFiles.length) {
      addLog('warning', `No ${typeLabel} for ${row.containerNumber}`);
      failures.push({ containerNumber: row.containerNumber, reason: `No ${typeLabel} found` });
      continue;
    }

    typePdfs.push(...typeFiles);
    addLog('info', `  ${row.containerNumber}: ${typeFiles.map(m => m.name).join(', ')}`);
  }

  if (!typePdfs.length) { addLog('error', `No ${typeLabel} PDFs found`); return; }

  setProgress(85, `Merging ${typePdfs.length} ${typeLabel} PDFs…`);
  addLog('info', `Merging ${typePdfs.length} ${typeLabel} PDFs into one document`);

  try {
    const merged = await PDFDocument.create();
    for (const pdf of typePdfs) {
      const buf = bufferMap.get(pdf.id);
      const doc = await PDFDocument.load(buf, { ignoreEncryption: true, updateMetadata: false });
      const pages = await merged.copyPages(doc, doc.getPageIndices());
      pages.forEach(p => merged.addPage(p));
    }
    const bytes = await merged.save({ objectsPerTick: Infinity, updateFieldAppearances: false, addDefaultPage: false });
    const containerCount = state.excelRows.length;
    const filename = `${getDatePrefix()}_${typeLabel}_${containerCount}_merged.pdf`;
    state.mergeResults.push({ containerNumber: type.toUpperCase() + '_ONLY', bytes, filename, subfolder });
    addLog('success', `→ ${filename} (${fmtSize(bytes.length)})`);
  } catch (err) {
    addLog('error', `${typeLabel} merge failed: ` + err.message);
  }
}


// ── Auto Merge ──
async function runAutoMerge() {
  if (state.isProcessing) return;
  if (!state.excelRows.length) { addLog('error', 'No Excel manifest loaded'); return; }
  if (!state.pdfs.length)      { addLog('error', 'No PDFs uploaded'); return; }

  state.isProcessing = true;
  state.mergeResults = [];

  const btn = document.getElementById('runAutoBtn');
  btn.disabled = true;
  btn.innerHTML = `<svg class="spinner" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg> Processing…`;

  document.getElementById('failureReport').style.display = 'none';
  document.getElementById('saveOutputBtn').style.display = 'none';
  setProgress(0, 'Starting…');

  const modeLabel = {
    'per-container': 'Per Container', 'all-in-one': 'All in One',
    ...Object.fromEntries(DOC_TYPES.map(dt => [`${dt.key}-only`, `${dt.plural} Only`])),
  };
  const sortLabel = { 'excel': 'Excel order', 'container': 'Container #', 'invoice': 'Invoice #' };
  addLog('info', `──── Auto Merge started (${modeLabel[state.mergeMode]}, sort: ${sortLabel[state.sortOrder]}) ────`);

  const t0 = performance.now();

  // Pre-load all PDFs in parallel
  addLog('info', `Pre-loading ${state.pdfs.length} PDFs…`);
  const readStart = performance.now();
  const bufferMap = await preloadAllPdfs(state.pdfs);
  addLog('info', `Pre-loaded ${state.pdfs.length} PDFs (${Math.round(performance.now() - readStart)}ms)`);

  // Build match index (pre-compute container → PDF mapping)
  const rows = getSortedRows();
  const matchIndex = buildMatchIndex(rows, state.pdfs);
  const failures = [];

  if (state.mergeMode === 'per-container') {
    await mergePerContainer(rows, failures, bufferMap, matchIndex);
  } else if (state.mergeMode === 'all-in-one') {
    await mergeAllInOne(rows, failures, bufferMap, matchIndex);
  } else if (state.mergeMode.endsWith('-only')) {
    const typeKey = state.mergeMode.replace('-only', '');
    await mergeByType(rows, failures, bufferMap, matchIndex, typeKey);
  }

  const elapsed = Math.round(performance.now() - t0);
  setProgress(100, 'Done');
  setTimeout(() => setProgress(null), 1500);

  addLog('info', '──────────────────────────');
  addLog('success', `Complete: ${state.mergeResults.length} merged · ${failures.length} failed · ${(elapsed / 1000).toFixed(1)}s total`);

  if (failures.length > 0) {
    addLog('warning', `Failures: ${failures.map(f => f.containerNumber).join(', ')}`);
    renderFailureReport(failures);
  }

  if (state.mergeResults.length > 0) {
    document.getElementById('saveOutputBtn').style.display = 'flex';
    addLog('info', state.agentConnected
      ? 'Click "Save to Folder" to save merged files'
      : 'Start the agent server, then click "Save to Folder"');
  }

  btn.disabled = false;
  updateRunButtonLabel();
  state.isProcessing = false;
}

function renderFailureReport(failures) {
  const rpt = document.getElementById('failureReport');
  const lst = document.getElementById('failureList');
  lst.innerHTML = failures.map(f => `
    <div class="failure-row">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#dc2626" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      <span style="font-family:monospace; color:#dc2626; font-weight:600;">${escHtml(f.containerNumber)}</span>
      <span style="color:#94a3b8; font-size:0.8rem; margin-left:auto;">${escHtml(f.reason)}</span>
    </div>`).join('');
  rpt.style.display = 'block';
}


// ── Manual Merge ──
async function runManualMerge() {
  if (state.isProcessing) return;
  if (!state.pdfs.length) { addLog('error', 'No PDFs in queue'); return; }

  state.isProcessing = true;

  const btn = document.getElementById('quickMergeBtn');
  btn.disabled = true;
  btn.innerHTML = `<svg class="spinner" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg> Merging…`;

  setProgress(30, `Merging ${state.pdfs.length} PDFs…`);
  addLog('info', `Manual merge: ${state.pdfs.length} file${state.pdfs.length !== 1 ? 's' : ''} in order:`);
  state.pdfs.forEach((p, i) => addLog('info', `  ${i + 1}. ${p.name}`));

  try {
    const { PDFDocument } = PDFLib;
    const buffers = await Promise.all(state.pdfs.map(p => readAsArrayBuffer(p.file)));
    const merged = await PDFDocument.create();
    for (let i = 0; i < state.pdfs.length; i++) {
      const doc = await PDFDocument.load(buffers[i], { ignoreEncryption: true, updateMetadata: false });
      const pages = await merged.copyPages(doc, doc.getPageIndices());
      pages.forEach(p => merged.addPage(p));
    }
    const bytes = await merged.save({ objectsPerTick: Infinity, updateFieldAppearances: false, addDefaultPage: false });
    setProgress(100, 'Done!');
    const blob = new Blob([bytes], { type: 'application/pdf' });
    triggerDownload(blob, 'NGL_Merged.pdf');
    addLog('success', `Downloaded: NGL_Merged.pdf (${fmtSize(bytes.length)})`);
  } catch (err) {
    addLog('error', 'Merge failed: ' + err.message);
  }

  setTimeout(() => setProgress(null), 1500);
  btn.disabled = false;
  btn.innerHTML = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M6 21V9a9 9 0 0 0 9 9"/></svg> Quick Merge &amp; Download`;
  state.isProcessing = false;
}


// ── Save Merged Output ──
async function saveMergedOutput() {
  if (!state.mergeResults.length) {
    addLog('error', 'No merged results yet. Run Auto Merge first.');
    return;
  }

  if (!state.agentConnected) {
    addLog('error', 'Agent server is not running. Start the agent server first, then try again.');
    return;
  }

  addLog('info', `Saving ${state.mergeResults.length} file${state.mergeResults.length !== 1 ? 's' : ''} to output folder…`);
  setProgress(50, 'Saving to folder…');

  const result = await agentBridge.saveToFolder(state.mergeResults);
  if (result && !result.error) {
    setProgress(100, 'Saved!');
    addLog('success', `Saved ${result.saved}/${result.total} files → ${result.outputDir}`);
    addLog('info', 'Output folder opened in Explorer');
  } else {
    addLog('error', 'Save failed: ' + (result?.error || 'Unknown error'));
  }

  setTimeout(() => setProgress(null), 1500);
}


// ── Clear All ──
function clearAll() {
  state.pdfs         = [];
  state.excelRows    = [];
  state.mergeResults = [];
  state.isProcessing = false;
  state.mergeMode    = 'per-container';
  state.sortOrder    = 'excel';

  document.getElementById('pdfInput').value   = '';
  document.getElementById('excelInput').value = '';

  // Reset excel zone
  document.getElementById('excelDropZone').classList.remove('has-file');
  document.getElementById('excelDropContent').style.display    = '';
  document.getElementById('excelLoadedState').style.display    = 'none';

  // Reset queue
  document.getElementById('pdfQueue').innerHTML          = EMPTY_QUEUE_HTML;
  document.getElementById('containerGroupsView').innerHTML = '';
  document.getElementById('failureReport').style.display  = 'none';
  document.getElementById('saveOutputBtn').style.display  = 'none';

  // Reset merge options UI
  document.querySelectorAll('#mergeModeGroup .merge-opt').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.mode === 'per-container');
  });
  document.querySelectorAll('#sortOrderGroup .merge-opt').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.sort === 'excel');
  });

  if (state.sortableInstance) { state.sortableInstance.destroy(); state.sortableInstance = null; }

  setProgress(null);
  setMode('idle');
  updateRunButtonLabel();
  addLog('info', 'All files cleared — ready for new job');
}

// ── Window assignments for inline HTML handlers ──
window.handleExcelInputChange = handleExcelInputChange;
window.removeExcel = removeExcel;
window.handlePdfInputChange = handlePdfInputChange;
window.setMergeMode = setMergeMode;
window.setSortOrder = setSortOrder;
window.runAutoMerge = runAutoMerge;
window.saveMergedOutput = saveMergedOutput;
window.runManualMerge = runManualMerge;
window.clearAll = clearAll;
window.removePdf = removePdf;
