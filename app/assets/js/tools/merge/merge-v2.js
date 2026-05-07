// ══════════════════════════════════════════════════════════
//  MERGE TOOL V2 — state machine + render functions
//  Spec:   docs/superpowers/specs/2026-05-05-merge-tool-ux-refinement-design.md
//  Plan:   docs/superpowers/plans/2026-05-05-merge-tool-v2-m1-foundation.md
//  Mockup: app/mockups/merge-tool-redesign.html
//
//  M1 (Foundation): Empty + Loading states render; Review is a stub
//  showing the loaded file name. Real Excel parsing comes in M2.
// ══════════════════════════════════════════════════════════
import {
  escHtml, readAsArrayBuffer, findColumnKey, CSV_ALIASES,
  routingDecisionFor,
} from '../../shared/utils.js';

// ── Module-local state ──
const v2State = {
  subMode: 'empty',          // empty | loading | review | fetching | ready | merging | done
  excelFile: null,
  excelHeaders: [],
  rows: [],                  // M3: each row gains status, fetchResult, manualPodFile, skipped fields
  loadingError: null,
  searchQuery: '',
  sortMode: 'excel',
  activeTab: 'all',          // all | issues | errors | queued
  showAllInSuccess: false,
  // ── M3: fetch + sidebar ──
  jobId: null,               // active fetch job id
  eventSource: null,         // SSE EventSource handle (closed on teardown)
  fetchProgress: 0,          // X in "Fetching X / N"
  fetchTotal: 0,             // N in "Fetching X / N"
  fetchCurrentContainer: '', // shown next to the progress label
  lastFetchedContainer: '',  // shown in "Last fetched: <c>" meta line on Resume
  openSidebarRow: null,      // index into v2State.rows; null = sidebar closed
  // ── M4 placeholders (unchanged from M2) ──
  pendingMode: null,
  completedModes: [],
  lastCompletedMode: null,
};

// State group is what the header/toggle buttons key off.
// Within a group, sub-states (loading vs empty, fetching vs review) pick which renderer fires.
const STATE_GROUP = {
  empty: 's1', loading: 's1',
  review: 's2', fetching: 's2',
  ready: 's3',
  merging: 's4', done: 's4',
};

const STATES = {
  s1: () => v2State.subMode === 'loading' ? renderLoading() : renderEmpty(),
  s2: () => v2State.subMode === 'fetching' ? renderFetching() : renderReview(),
  s3: () => renderReady(),
  s4: () => v2State.subMode === 'merging' ? renderMerging() : renderDone(),
};

let _initialized = false;

// ── Public entry — called from app.js when the v2 view is shown ──
export function initMergeV2() {
  if (_initialized) {
    // Re-render in case state changed since last view (e.g., navigated away then back)
    setStateV2(v2State.subMode);
    return;
  }
  _initialized = true;
  // Wire the hidden Excel input — change event picks up the file from native picker
  const xinput = document.getElementById('v2ExcelInput');
  if (xinput) xinput.addEventListener('change', handleExcelChange);
  setStateV2('empty');
}

// ── Public state setter ──
export function setStateV2(name) {
  // Entering Empty resets the session (used by `+ New Merge` header button + Start over)
  if (name === 'empty') {
    // M3: defensive teardown — close any SSE / cancel any active job before nuking state
    try {
      if (v2State.eventSource) {
        v2State.eventSource.close();
        v2State.eventSource = null;
      }
      if (v2State.jobId) {
        // Fire-and-forget cancel — we don't await it; if it fails, the agent will
        // notice no consumer and tear down on its own
        fetch(`http://localhost:8787/jobs/${encodeURIComponent(v2State.jobId)}/cancel`, {
          method: 'POST',
        }).catch(() => {});
        v2State.jobId = null;
      }
    } catch (_) { /* best-effort cleanup, never throw */ }

    v2State.completedModes = [];
    v2State.lastCompletedMode = null;
    v2State.excelFile = null;
    v2State.excelHeaders = [];
    v2State.rows = [];
    v2State.loadingError = null;
    v2State.searchQuery = '';
    v2State.sortMode = 'excel';
    v2State.activeTab = 'all';
    v2State.showAllInSuccess = false;
    v2State.fetchProgress = 0;
    v2State.fetchTotal = 0;
    v2State.fetchCurrentContainer = '';
    v2State.lastFetchedContainer = '';
    v2State.openSidebarRow = null;
    const xinput = document.getElementById('v2ExcelInput');
    if (xinput) xinput.value = '';
  }
  v2State.subMode = name;
  const group = STATE_GROUP[name] || name;
  // Header action button visibility — both stay hidden until M3/M4 actually need them
  const back = document.getElementById('v2BtnBackToReady');
  const fresh = document.getElementById('v2BtnNewMerge');
  if (back)  back.style.display  = (group === 's4') ? '' : 'none';
  if (fresh) fresh.style.display = (group === 's2' || group === 's3' || group === 's4') ? '' : 'none';
  // Render
  const renderer = STATES[group];
  const wa = document.getElementById('v2WorkArea');
  if (renderer && wa) wa.innerHTML = renderer();
  // After any full re-render of the review pane, sync the visuals that depend on
  // imperative DOM state (indeterminate checkbox, dynamic fetch count).
  if (v2State.subMode === 'review') {
    updateMasterCheckbox();
    updateFetchButton();
  }
}

// ── Excel drop / pick ──
function triggerExcel() {
  const xinput = document.getElementById('v2ExcelInput');
  if (xinput) xinput.click();
}

async function handleExcelChange(e) {
  const file = e.target.files && e.target.files[0];
  if (!file) return;
  v2State.excelFile = file;
  v2State.loadingError = null;
  setStateV2('loading');

  const result = await parseExcelFile(file);

  if (result.error) {
    v2State.loadingError = result.error;
    setStateV2('loading');     // re-render with error state (renderLoading uses this field)
    const xinput = document.getElementById('v2ExcelInput');
    if (xinput) xinput.value = '';
    return;
  }

  v2State.rows = validateRows(result.rows);
  v2State.excelHeaders = result.headers;
  // Default-active tab: Issues if any issue exists, All otherwise.
  const hasIssues = v2State.rows.some(r => r.status !== 'ok');
  v2State.activeTab = hasIssues ? 'issues' : 'all';
  v2State.searchQuery = '';
  v2State.sortMode = 'excel';
  v2State.showAllInSuccess = false;
  setStateV2('review');
}

// ── Excel parsing + column detection ──
function findCustomerColumn(headers) {
  // Friendly name first (e.g. "NAME" → "FREIGHT FLEX LLC")
  const byName = findColumnKey(headers, CSV_ALIASES.customerName);
  if (byName) return byName;
  // Fall back to a code column (e.g. "BILLTO" → "IDEANU01")
  return findColumnKey(headers, CSV_ALIASES.customerCode);
}

async function parseExcelFile(file) {
  let buf;
  try {
    buf = await readAsArrayBuffer(file);
  } catch (err) {
    return { error: 'Could not read the file. Try saving and re-uploading.' };
  }

  let wb;
  try {
    wb = XLSX.read(buf, { type: 'array' });
  } catch (err) {
    return { error: `Couldn't read this file as Excel — it may be corrupted or in an old format. Try saving as .xlsx in Excel and re-uploading. (Details: ${err.message})` };
  }

  if (!wb || !wb.SheetNames) {
    return { error: "Couldn't read this file as an Excel workbook." };
  }
  const sheetName = wb.SheetNames[0];
  if (!sheetName) return { error: 'This Excel file has no sheets.' };
  const ws = wb.Sheets[sheetName];
  const sheetRows = XLSX.utils.sheet_to_json(ws, { defval: '' });

  if (sheetRows.length === 0) {
    return { error: 'The first sheet has no data rows. Make sure the first row is a header row and there is at least one data row below.' };
  }

  const headers = Object.keys(sheetRows[0]);
  const containerKey = findColumnKey(headers, CSV_ALIASES.containerNumber);
  const invoiceKey   = findColumnKey(headers, CSV_ALIASES.invoiceNumber);
  const woKey        = findColumnKey(headers, CSV_ALIASES.workOrderNumber);
  const customerKey  = findCustomerColumn(headers);

  if (!containerKey) {
    return {
      error: `Couldn't find a Container column. Looked at: ${headers.join(', ')}. Expected something like "Container Number", "CONT NO", or "Equipment".`,
    };
  }

  const rows = [];
  for (let i = 0; i < sheetRows.length; i++) {
    const r = sheetRows[i];
    const cn = String(r[containerKey] || '').trim();
    if (!cn) continue;          // skip blank container rows entirely
    const decision = routingDecisionFor({
      invoiceNumber: invoiceKey ? String(r[invoiceKey] || '').trim() : '',
      workOrderNumber: woKey ? String(r[woKey] || '').trim() : '',
    });

    rows.push({
      rowNum: i + 2,            // sheet row 1 is headers, so first data row → 2
      containerNumber: cn,
      invoiceNumber: invoiceKey ? String(r[invoiceKey] || '').trim() : '',
      workOrderNumber: woKey ? String(r[woKey] || '').trim() : '',
      customer: customerKey ? String(r[customerKey] || '').trim() : '',
      selected: false,
      status: 'ok',
      statusReason: '',
      // M3 routing
      routingType: decision.type,
      expectedDoc: decision.expectedDoc,
      fetchResult: null,
      manualPodFile: null,
      skipped: false,
    });
  }

  if (rows.length === 0) {
    return { error: 'No usable rows — every row had an empty Container cell.' };
  }

  return { rows, headers, containerKey, invoiceKey, woKey, customerKey };
}

function validateRows(rows) {
  // Dedup by INV# only. Container/WO# are NOT used for duplicate detection.
  // (See spec docs/superpowers/specs/2026-05-06-merge-tool-invoice-grouping-design.md §2.)
  const seenInv = new Map();   // invLower → rowNum where it was first seen
  for (const row of rows) {
    const inv = (row.invoiceNumber || '').trim();

    if (!inv) {
      // Soft-flag: keep the row, default-checked, yellow VERIFY badge.
      row.status = 'miss-inv';
      row.statusReason = 'No invoice number — please check before sending. Will merge with WO# as filename key.';
      row.selected = true;
      continue;
    }

    const invLower = inv.toLowerCase();
    const priorRowNum = seenInv.get(invLower);
    if (priorRowNum !== undefined) {
      // True duplicate: same INV# as a previous row.
      row.status = 'dup-same-inv';
      row.statusReason = `Exact duplicate of row ${priorRowNum} (same INV# ${inv}) — will be skipped`;
      row.selected = false;
      continue;
    }

    seenInv.set(invLower, row.rowNum);
    row.status = 'ok';
    row.statusReason = '';
    row.selected = true;
  }
  return rows;
}

function hasAnyWO() {
  return v2State.rows.some(r => (r.workOrderNumber || '').trim() !== '');
}

// ── State renderers ──

function renderEmpty() {
  return `
    <div class="centered-stage">
      <h1>Start a new merge</h1>
      <p class="subtitle">Upload your Excel manifest first — we'll match PDFs to it next.</p>
      <div class="big-drop kind-excel" onclick="window.v2TriggerExcel()">
        <div class="drop-icon">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="15" x2="21" y2="15"/><line x1="9" y1="3" x2="9" y2="21"/>
          </svg>
        </div>
        <div class="drop-title">Drop Excel Manifest</div>
        <div class="drop-help">Needs a Container Number column to match PDFs automatically</div>
        <div class="drop-types">.xlsx · .xls · .csv</div>
      </div>
      <div class="hint-chip"><span class="step-num">2</span> We'll check the manifest, then fetch from APIs</div>
    </div>
  `;
}

function renderLoading() {
  if (v2State.loadingError) {
    return `
      <div class="centered-stage">
        <h1>Couldn't read this file</h1>
        <p class="subtitle error-text">${escHtml(v2State.loadingError)}</p>
        <div class="big-drop kind-excel" onclick="window.v2TriggerExcel()">
          <div class="drop-icon">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="15" x2="21" y2="15"/><line x1="9" y1="3" x2="9" y2="21"/>
            </svg>
          </div>
          <div class="drop-title">Try a different file</div>
          <div class="drop-help">Drop or pick another Excel manifest</div>
          <div class="drop-types">.xlsx · .xls · .csv</div>
        </div>
      </div>
    `;
  }
  return `
    <div class="centered-stage">
      <h1>Start a new merge</h1>
      <p class="subtitle">Reading your manifest…</p>
      <div class="big-drop loading">
        <div class="big-spinner"></div>
        <div class="drop-title">Loading…</div>
        <div class="drop-help">Reading container numbers and checking for issues</div>
      </div>
      <div class="hint-chip"><span class="step-num">2</span> We'll check the manifest, then fetch from APIs</div>
    </div>
  `;
}

function topBarOnlyExcel() {
  const fname = v2State.excelFile ? escHtml(v2State.excelFile.name) : '';
  const total = v2State.rows.length;
  const issueCount = v2State.rows.filter(r => r.status !== 'ok').length;
  const meta = issueCount === 0
    ? `${total} unique container${total !== 1 ? 's' : ''}`
    : `${total} unique container${total !== 1 ? 's' : ''} · ${issueCount} issue${issueCount !== 1 ? 's' : ''}`;
  return `
    <div class="top-bar" style="grid-template-columns: 1fr;">
      <div class="file-summary">
        <div class="icon-box xlsx">XLS</div>
        <div class="text">
          <div class="name">${fname}</div>
          <div class="meta">${escHtml(meta)}</div>
        </div>
        <button onclick="window.v2SetState('empty')">Replace</button>
      </div>
    </div>
  `;
}

function getVisibleRows() {
  let rows = v2State.rows;
  if (v2State.activeTab === 'issues') {
    rows = rows.filter(r => r.status !== 'ok');
  }
  if (v2State.searchQuery) {
    const q = v2State.searchQuery.toLowerCase();
    rows = rows.filter(r => r.containerNumber.toLowerCase().includes(q));
  }
  return sortRows(rows, v2State.sortMode);
}

function sortRows(rows, mode) {
  const out = rows.slice();
  if (mode === 'excel') {
    out.sort((a, b) => a.rowNum - b.rowNum);
  } else if (mode === 'container') {
    out.sort((a, b) => a.containerNumber.localeCompare(b.containerNumber, undefined, { numeric: true }));
  } else if (mode === 'invoice') {
    out.sort((a, b) => {
      // empty invoices sort last
      if (!a.invoiceNumber && !b.invoiceNumber) return a.rowNum - b.rowNum;
      if (!a.invoiceNumber) return 1;
      if (!b.invoiceNumber) return -1;
      return a.invoiceNumber.localeCompare(b.invoiceNumber, undefined, { numeric: true });
    });
  } else if (mode === 'issues-first') {
    out.sort((a, b) => {
      const aIssue = a.status !== 'ok' ? 0 : 1;
      const bIssue = b.status !== 'ok' ? 0 : 1;
      if (aIssue !== bIssue) return aIssue - bIssue;
      return a.rowNum - b.rowNum;
    });
  }
  return out;
}

function selectedCount()        { return v2State.rows.filter(r => r.selected).length; }
function issuesCount()          { return v2State.rows.filter(r => r.status !== 'ok').length; }

function willChipFor(row) {
  if (row.routingType === 'import') return `<span class="will-chip import">POD</span>`;
  if (row.routingType === 'export') return `<span class="will-chip export">BOL/POL</span>`;
  return `<span class="will-chip unknown">?</span>`;
}

function highlightInvLetter(inv) {
  if (!inv || inv.length < 2) return escHtml(inv);
  const c = inv[1].toUpperCase();
  if (c === 'M' || c === 'E' || c === 'X') {
    return escHtml(inv[0]) + `<span class="inv-letter">${escHtml(inv[1])}</span>` + escHtml(inv.slice(2));
  }
  return escHtml(inv);
}

function rowMarkup(row) {
  const checkAttr = row.selected ? 'checked' : '';
  const trClass = row.status === 'ok' ? '' : 'row-issue';
  const invDisplay = row.invoiceNumber
    ? `<span class="mono mono-sub">${highlightInvLetter(row.invoiceNumber)}</span>`
    : `<span class="mono mono-sub" style="color:#dc2626;">— missing —</span>`;
  const customerDisplay = row.customer
    ? escHtml(row.customer)
    : `<span style="color:#cbd5e1;">—</span>`;

  let badge = '';
  if (row.status === 'miss-inv') {
    badge = `<span class="val-badge warn"><span class="dot"></span>Verify</span>`;
  } else if (row.status === 'dup-same-inv') {
    badge = `<span class="val-badge dup"><span class="dot"></span>Exact dup</span>`;
  }
  const reasonLine = row.statusReason
    ? `<div style="font-size:0.72rem; color:#92400e; margin-top:3px;">${escHtml(row.statusReason)}</div>`
    : '';

  const woCell = hasAnyWO()
    ? `<td>${row.workOrderNumber ? `<span class="mono">${escHtml(row.workOrderNumber)}</span>` : '<span style="color:#cbd5e1;">—</span>'}</td>`
    : '';

  const willCell = `<td>${willChipFor(row)}</td>`;

  return `<tr class="${trClass}" data-row-num="${row.rowNum}">
    <td class="check-col"><input type="checkbox" class="row-check" ${checkAttr} onchange="window.v2ToggleRow(${row.rowNum}, this.checked)" /></td>
    <td style="color:#94a3b8; font-size:0.8rem;">${row.rowNum}</td>
    <td><span class="mono">${escHtml(row.containerNumber)}</span></td>
    <td>${invDisplay}</td>
    ${woCell}
    <td>${customerDisplay}</td>
    ${willCell}
    <td>${badge}${reasonLine}</td>
  </tr>`;
}

function docPills(row) {
  const fr = row.fetchResult;
  if (!fr) {
    // Queued / not-yet-fetched
    const expected = row.expectedDoc === 'BOL/POL' ? 'BOL' : (row.expectedDoc === '?' ? '?' : 'POD');
    return `<div class="doc-row">
      <span class="doc-pill queued">INV</span>
      <span class="doc-pill queued">${expected}</span>
    </div>`;
  }
  return `<div class="doc-row">
    <span class="doc-pill ${fr.invPill}">INV</span>
    <span class="doc-pill ${fr.podPill}">${escHtml(fr.podLabel)}</span>
  </div>`;
}

function fetchStatusCell(row) {
  if (row.skipped) return `<span class="status-text skipped">Skipped</span>`;
  if (!row.fetchResult) return `<span class="status-text queued">Queued</span>`;
  const fr = row.fetchResult;
  if (fr.podPill === 'miss') return `<span class="status-text issue">${escHtml(fr.statusText || 'Needs PDF')}</span>`;
  if (fr.podPill === 'fallback') return `<span class="status-text ready">${escHtml(fr.statusText || 'Fetched (fallback)')}</span>`;
  return `<span class="status-text ready">${escHtml(fr.statusText || 'Fetched')}</span>`;
}

function fetchActionCell(rowIdx, row) {
  if (row.fetchResult && row.fetchResult.podPill === 'miss' && !row.skipped) {
    return `<td><button class="fix-error-btn" onclick="event.stopPropagation(); window.v2OpenSidebar(${rowIdx})">⚠ Fix Error</button></td>`;
  }
  return `<td></td>`;
}

function fetchRowMarkup(rowIdx, row, opts) {
  const isError = row.fetchResult?.podPill === 'miss' && !row.skipped;
  const isQueued = !row.fetchResult && !row.skipped;
  const isActive = isError && opts.activeErrorIdx === rowIdx;

  const trClass = [
    isError ? 'row-issue' : '',
    isActive ? 'row-active-error' : '',
    isQueued ? 'row-queued' : '',
  ].filter(Boolean).join(' ');

  const checkable = !!row.fetchResult && row.fetchResult.podPill !== 'miss' && !row.skipped;
  const checkAttrs = `${row.selected && checkable ? 'checked' : ''} ${!checkable ? 'disabled' : ''}`;
  const checkTitle = isError ? 'Fix the error before this can be merged'
                   : isQueued ? 'Not yet fetched'
                   : row.skipped ? 'Skipped — re-click Fix Error to undo'
                   : '';

  const trAttrs = isError
    ? `onclick="window.v2OpenSidebar(${rowIdx})" style="cursor:pointer;"`
    : '';

  const checkColMaybe = opts.includeCheck
    ? `<td class="check-col" onclick="event.stopPropagation()">
         <input type="checkbox" class="row-check" ${checkAttrs} title="${checkTitle}"
                onchange="window.v2ToggleFetchRow(${rowIdx}, this.checked)" />
       </td>`
    : '';

  return `<tr class="${trClass}" ${trAttrs} data-row-idx="${rowIdx}">
    ${checkColMaybe}
    <td><span class="mono">${escHtml(row.containerNumber)}</span></td>
    <td><span class="mono mono-sub">${highlightInvLetter(row.invoiceNumber)}</span></td>
    <td>${row.customer ? escHtml(row.customer) : '<span style="color:#cbd5e1;">—</span>'}</td>
    <td>${willChipFor(row)}</td>
    <td>${docPills(row)}</td>
    <td>${fetchStatusCell(row)}</td>
    ${fetchActionCell(rowIdx, row)}
  </tr>`;
}

function renderTbodyHTML() {
  const rows = getVisibleRows();
  if (rows.length === 0) {
    const cols = hasAnyWO() ? 8 : 7;
    return `<tr><td colspan="${cols}" style="padding:20px; text-align:center; color:#94a3b8;">No rows match.</td></tr>`;
  }
  return rows.map(rowMarkup).join('');
}

function rerenderTbody() {
  const tbody = document.getElementById('v2ReviewTbody');
  if (!tbody) return;
  tbody.innerHTML = renderTbodyHTML();
  updateMasterCheckbox();
  updateFilterMeta();
}

function updateFilterMeta() {
  const el = document.getElementById('v2FilterMeta');
  if (!el) return;
  const visible = getVisibleRows().length;
  const total = v2State.rows.length;
  const issues = issuesCount();
  if (v2State.searchQuery) {
    el.textContent = `${visible} of ${total} match · ${issues} issue${issues !== 1 ? 's' : ''}`;
  } else {
    el.textContent = `${total} unique · ${issues} issue${issues !== 1 ? 's' : ''}`;
  }
}

function updateMasterCheckbox() {
  const master = document.getElementById('v2MasterCheck');
  if (!master) return;
  const visible = getVisibleRows();
  if (visible.length === 0) {
    master.checked = false; master.indeterminate = false; return;
  }
  const checkedCount = visible.filter(r => r.selected).length;
  if (checkedCount === 0) {
    master.checked = false; master.indeterminate = false;
  } else if (checkedCount === visible.length) {
    master.checked = true;  master.indeterminate = false;
  } else {
    master.checked = false; master.indeterminate = true;
  }
}

function updateFetchButton() {
  const btn = document.getElementById('v2BtnFetch');
  if (!btn) return;
  const sel = selectedCount();
  const countSpan = btn.querySelector('.fetch-count');
  if (countSpan) {
    countSpan.textContent = sel;
    // Rebuild button label entirely so the "Document(s)" pluralization stays in sync.
    btn.innerHTML = `
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
      Fetch <span class="fetch-count">${sel}</span> Document${sel !== 1 ? 's' : ''}
    `;
  }
  if (sel === 0) {
    btn.setAttribute('disabled', '');
    btn.title = 'Check at least one row to fetch';
  } else {
    btn.removeAttribute('disabled');
    btn.removeAttribute('title');
  }
}

function topBarWithDrop() {
  const fname = v2State.excelFile ? escHtml(v2State.excelFile.name) : '';
  const total = v2State.rows.length;
  return `
    <div class="top-bar">
      <div class="file-summary">
        <div class="icon-box xlsx">XLS</div>
        <div class="text">
          <div class="name">${fname}</div>
          <div class="meta">${total} unique container${total !== 1 ? 's' : ''}</div>
        </div>
      </div>
      <div class="pdf-drop-card" title="Bulk PDF drop wires up in M4">
        <div class="icon-box pdf">PDF</div>
        <div class="text">
          <div class="label-line">PDFs <span class="count-pill">0</span></div>
          <div class="help">Drop bulk PDFs here for missing or late docs (M4)</div>
        </div>
      </div>
    </div>
  `;
}

function routingSummaryBand() {
  const imports  = v2State.rows.filter(r => r.routingType === 'import').length;
  const exports_ = v2State.rows.filter(r => r.routingType === 'export').length;
  const unknown  = v2State.rows.filter(r => r.routingType === 'unknown').length;
  return `
    <div class="routing-summary">
      <span class="label">Will fetch</span>
      <span class="group">
        <span class="chip import">POD</span>
        <strong>${imports}</strong> import${imports !== 1 ? 's' : ''}
      </span>
      <span class="group">
        <span class="chip export">BOL/POL</span>
        <strong>${exports_}</strong> export${exports_ !== 1 ? 's' : ''}
      </span>
      ${unknown ? `<span class="group">
        <span class="chip unknown">?</span>
        <strong>${unknown}</strong> unknown
      </span>` : ''}
      <span class="hint">Decided by INV# letter (M/E) · falls back to WO# letter when prefix is non-standard</span>
    </div>
  `;
}

function renderReview() {
  const hasIssues = v2State.rows.some(r => r.status !== 'ok');
  return hasIssues
    ? renderReviewWithIssues()
    : renderReviewSuccess();
}

function renderReviewSuccess() {
  const total = v2State.rows.length;
  let expanded = '';
  if (v2State.showAllInSuccess) {
    const sortOpts = [
      ['excel',         'Sort: Excel Order'],
      ['container',     'Sort: Container #'],
      ['invoice',       'Sort: Invoice #'],
    ].map(([v, lbl]) => `<option value="${v}" ${v2State.sortMode === v ? 'selected' : ''}>${lbl}</option>`).join('');
    expanded = `
      <div style="margin-top:24px;">
        <div class="toolbar">
          <input type="text" class="search" placeholder="Search containers…"
                 value="${escHtml(v2State.searchQuery)}"
                 oninput="window.v2HandleSearch(this.value)" />
          <select class="sort-select" onchange="window.v2HandleSort(this.value)">
            ${sortOpts}
          </select>
          <span class="filter-meta" id="v2FilterMeta">${total} unique · 0 issues</span>
        </div>
        <div class="table-wrap">
          <table class="merge-table">
            <thead>
              <tr>
                <th class="check-col"><input type="checkbox" id="v2MasterCheck" onclick="window.v2ToggleAll(this.checked)" /></th>
                <th>Row</th>
                <th>Container</th>
                <th>Invoice #</th>
                ${hasAnyWO() ? '<th>WO #</th>' : ''}
                <th>Customer</th>
                <th>Will fetch</th>
                <th>Validation</th>
              </tr>
            </thead>
            <tbody id="v2ReviewTbody">${renderTbodyHTML()}</tbody>
          </table>
        </div>
      </div>
    `;
  }
  const linkLabel = v2State.showAllInSuccess
    ? 'Hide rows ▲'
    : `Show all ${total} rows ▼`;
  return `
    ${topBarOnlyExcel()}
    ${routingSummaryBand()}
    <div class="review-success-card">
      <div class="check-icon">
        <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="20 6 9 17 4 12"/>
        </svg>
      </div>
      <div class="title">All ${total} row${total !== 1 ? 's' : ''} checked out</div>
      <div class="subtitle">Ready to fetch documents.</div>
      <button class="fetch-btn" id="v2BtnFetch" onclick="window.v2ClickFetch()">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
        Fetch <span class="fetch-count">${total}</span> Document${total !== 1 ? 's' : ''}
      </button>
      <div>
        <button class="show-all-link" onclick="window.v2ToggleShowAll()">${linkLabel}</button>
      </div>
      ${expanded}
    </div>
  `;
}

function renderReviewWithIssues() {
  const total = v2State.rows.length;
  const issues = issuesCount();
  const okCount = total - issues;
  const sel = selectedCount();
  const fetchDisabled = sel === 0 ? 'disabled' : '';
  const sortOpts = [
    ['excel',         'Sort: Excel Order'],
    ['container',     'Sort: Container #'],
    ['invoice',       'Sort: Invoice #'],
    ['issues-first',  'Sort: Issues first'],
  ].map(([v, lbl]) => `<option value="${v}" ${v2State.sortMode === v ? 'selected' : ''}>${lbl}</option>`).join('');

  return `
    ${topBarOnlyExcel()}
    ${routingSummaryBand()}

    <div class="controls-line" style="background:#fffbeb; border:1px solid #fde68a; border-radius:10px; padding:11px 16px;">
      <div style="font-size:0.86rem; color:#78350f;">
        <strong style="color:#92400e;">${issues} issue${issues !== 1 ? 's' : ''}</strong> in your manifest — review the rows below, then start the fetch.
      </div>
      <div class="summary-line" style="margin-left:auto; color:#78350f;">
        <span class="ok">●</span> <strong>${okCount}</strong> ok &nbsp;·&nbsp;
        <span class="warn">●</span> <strong>${issues}</strong> issue${issues !== 1 ? 's' : ''}
      </div>
    </div>

    <div class="tabs-row">
      <div class="tabs">
        <button class="tab ${v2State.activeTab === 'all' ? 'active' : ''}" onclick="window.v2HandleTabClick('all')">All <span class="count">${total}</span></button>
        <button class="tab has-issues ${v2State.activeTab === 'issues' ? 'active' : ''}" onclick="window.v2HandleTabClick('issues')">Issues <span class="count">${issues}</span></button>
      </div>
      <div style="padding-bottom:8px;">
        <button class="top-action-btn" id="v2BtnFetch" ${fetchDisabled} onclick="window.v2ClickFetch()">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
          Fetch <span class="fetch-count">${sel}</span> Document${sel !== 1 ? 's' : ''}
        </button>
      </div>
    </div>

    <div class="toolbar">
      <input type="text" class="search" placeholder="Search containers…"
             value="${escHtml(v2State.searchQuery)}"
             oninput="window.v2HandleSearch(this.value)" />
      <select class="sort-select" onchange="window.v2HandleSort(this.value)">
        ${sortOpts}
      </select>
      <span class="filter-meta" id="v2FilterMeta">${total} unique · ${issues} issue${issues !== 1 ? 's' : ''}</span>
    </div>

    <div class="table-wrap">
      <table class="merge-table">
        <thead>
          <tr>
            <th class="check-col"><input type="checkbox" id="v2MasterCheck" onclick="window.v2ToggleAll(this.checked)" /></th>
            <th>Row</th>
            <th>Container</th>
            <th>Invoice #</th>
            ${hasAnyWO() ? '<th>WO #</th>' : ''}
            <th>Customer</th>
            <th>Will fetch</th>
            <th>Validation</th>
          </tr>
        </thead>
        <tbody id="v2ReviewTbody">${renderTbodyHTML()}</tbody>
      </table>
    </div>
  `;
}

function renderFetching() {
  const total = v2State.rows.filter(r => r.selected).length || v2State.rows.length;
  const done = v2State.fetchProgress;
  const cur = v2State.fetchCurrentContainer || '—';
  const percent = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;

  // Tabs split by current state
  const fetchedCount = v2State.rows.filter(r => r.fetchResult && r.fetchResult.podPill !== 'miss').length;
  const failedCount  = v2State.rows.filter(r => r.fetchResult?.podPill === 'miss').length;
  const allCount     = v2State.rows.length;

  // Body — show all rows (fetched + queued + failed)
  const bodyRows = v2State.rows.map((row, i) => fetchRowMarkup(i, row, {
    includeCheck: false,
    activeErrorIdx: null,
  })).join('');

  return `
    ${topBarWithDrop()}
    ${routingSummaryBand()}
    <div class="progress-line">
      <div class="now">
        <strong>Fetching ${done} / ${total}</strong>
        &nbsp; <span class="container-name">${escHtml(cur)}</span>
      </div>
      <div class="progress-track"><div class="progress-fill" style="width:${percent}%;"></div></div>
      <button class="cancel-btn" onclick="window.v2CancelFetch()">Cancel</button>
    </div>
    <div class="tabs-row">
      <div class="tabs">
        <button class="tab active">All <span class="count">${allCount}</span></button>
        <button class="tab">Fetched <span class="count">${fetchedCount}</span></button>
        <button class="tab has-issues">Failed <span class="count">${failedCount}</span></button>
      </div>
    </div>
    <div class="toolbar">
      <input type="text" class="search" placeholder="Search containers…" />
      <span class="filter-meta">${done} / ${total} fetched · ${failedCount} failed</span>
    </div>
    <div class="table-wrap">
      <table class="merge-table">
        <thead><tr>
          <th>Container</th><th>Invoice #</th><th>Customer</th>
          <th>Will fetch</th><th>Documents</th><th>Status</th><th></th>
        </tr></thead>
        <tbody id="v2FetchTbody">${bodyRows}</tbody>
      </table>
    </div>
  `;
}
function renderReady() {
  const all = v2State.rows;
  const queued = all.filter(r => !r.fetchResult && !r.skipped);
  const errors = all.filter(r => r.fetchResult?.podPill === 'miss' && !r.skipped);
  const ready  = all.filter(r => r.fetchResult && r.fetchResult.podPill !== 'miss' && !r.skipped);

  // Active tab — Errors-default-when-errors rule
  if (errors.length > 0 && v2State.activeTab !== 'errors' && v2State.activeTab !== 'queued') {
    v2State.activeTab = 'errors';
  } else if (errors.length === 0 && queued.length === 0) {
    if (v2State.activeTab === 'errors' || v2State.activeTab === 'queued') {
      v2State.activeTab = 'all';
    }
  }

  // Filter rows based on active tab
  let visibleRows;
  if (v2State.activeTab === 'errors') visibleRows = errors;
  else if (v2State.activeTab === 'queued') visibleRows = queued;
  else visibleRows = all;

  // Apply search
  if (v2State.searchQuery) {
    const q = v2State.searchQuery.toLowerCase();
    visibleRows = visibleRows.filter(r => r.containerNumber.toLowerCase().includes(q));
  }

  // Selection count for the action button
  const selected = ready.filter(r => r.selected).length;

  const isPartial = queued.length > 0;

  // Action bar
  const actionBar = isPartial ? `
    <div class="ready-action-bar">
      <div class="ready-status">
        <span style="color:#16a34a;">●</span> <strong>${ready.length} of ${ready.length + errors.length}</strong> fetched & ready
        <span style="color:#cbd5e1; margin: 0 6px;">·</span>
        <span style="color:#d97706;">●</span> <strong>${errors.length}</strong> need fixing
        <span style="color:#cbd5e1; margin: 0 6px;">·</span>
        <span style="color:#94a3b8;">●</span> <strong>${queued.length}</strong> queued
      </div>
      <div class="ready-action-right">
        <button class="merge-btn resume" onclick="window.v2ResumeFetch()">
          ↻ Resume fetch
          <span class="count-badge">${queued.length} queued</span>
          <span class="last-fetched-meta">Last fetched: ${escHtml(v2State.lastFetchedContainer || '—')}</span>
        </button>
      </div>
    </div>
  ` : `
    <div class="ready-action-bar">
      <div class="ready-status">
        <span style="color:#16a34a;">●</span> <strong>${ready.length} of ${all.length}</strong> ready to merge
        ${errors.length ? `<span style="color:#cbd5e1; margin: 0 6px;">·</span>
          <span style="color:#d97706;">●</span> <strong>${errors.length}</strong> need fixing
          <span style="color:#94a3b8;">(click any error row)</span>` : ''}
      </div>
      <div class="ready-action-right">
        <button class="merge-btn" id="v2BtnContinueMerge" ${selected === 0 ? 'disabled' : ''}
                onclick="window.v2ClickContinueMerge()">
          Continue to Merge
          <span class="count-badge"><span class="sel-count">${selected}</span> selected</span>
        </button>
      </div>
    </div>
  `;

  // Tabs
  const tabsHtml = `
    <div class="tabs-row">
      <div class="tabs">
        <button class="tab ${v2State.activeTab === 'all' ? 'active' : ''}" onclick="window.v2HandleReadyTab('all')">
          All <span class="count">${all.length}</span>
        </button>
        <button class="tab has-issues ${v2State.activeTab === 'errors' ? 'active' : ''}" onclick="window.v2HandleReadyTab('errors')">
          Errors <span class="count">${errors.length}</span>
        </button>
        ${isPartial ? `<button class="tab queued-tab ${v2State.activeTab === 'queued' ? 'active' : ''}" onclick="window.v2HandleReadyTab('queued')">
          Queued <span class="count">${queued.length}</span>
        </button>` : ''}
      </div>
    </div>
  `;

  // Toolbar — adds the mass-retry button when on the Errors tab with errors present
  const massRetry = (v2State.activeTab === 'errors' && errors.length > 0)
    ? `<button class="mass-retry-btn" onclick="window.v2RetryAllErrors()">↻ Retry all errors</button>`
    : '';

  const toolbarHtml = `
    <div class="toolbar">
      <input type="text" class="search" placeholder="Search containers…"
             value="${escHtml(v2State.searchQuery)}"
             oninput="window.v2HandleReadySearch(this.value)" />
      ${massRetry}
      <span class="filter-meta">${visibleRows.length} of ${all.length}${errors.length ? ` · ${errors.length} need fixing` : ''}${queued.length ? ` · ${queued.length} queued` : ''}</span>
    </div>
  `;

  // Table
  const bodyRows = visibleRows.map(row => {
    const idx = v2State.rows.indexOf(row);
    return fetchRowMarkup(idx, row, {
      includeCheck: true,
      activeErrorIdx: v2State.openSidebarRow,
    });
  }).join('');

  const tableHtml = `
    <div class="table-wrap">
      <table class="merge-table">
        <thead><tr>
          <th class="check-col"><input type="checkbox" id="v2ReadyMaster" onclick="window.v2ToggleAllReady(this.checked)" /></th>
          <th>Container</th><th>Invoice #</th><th>Customer</th>
          <th>Will fetch</th><th>Documents</th><th>Status</th><th></th>
        </tr></thead>
        <tbody id="v2ReadyTbody">${bodyRows}</tbody>
      </table>
    </div>
  `;

  // Sidebar (auto-opens on first error if not yet set)
  if (v2State.openSidebarRow === null && errors.length > 0) {
    v2State.openSidebarRow = v2State.rows.indexOf(errors[0]);
  }
  const sidebarHtml = (v2State.openSidebarRow !== null && v2State.openSidebarRow >= 0)
    ? renderSidebar(v2State.openSidebarRow)
    : '';

  return `
    ${topBarWithDrop()}
    ${routingSummaryBand()}
    ${actionBar}
    ${tabsHtml}
    ${toolbarHtml}
    ${tableHtml}
    ${sidebarHtml}
  `;
}

function renderSidebar(rowIdx) {
  const row = v2State.rows[rowIdx];
  if (!row) return '';

  const isResolved = !!(row.fetchResult && row.fetchResult.podPill !== 'miss');
  const sidebarClass = `detail-sidebar open${isResolved ? ' resolved' : ''}`;

  const isExport = row.routingType === 'export';
  const docName = isExport ? 'BOL or POL' : (row.routingType === 'unknown' ? 'POD, BOL, or POL' : 'POD');

  // Count remaining errors (excluding this one and any skipped)
  const remaining = v2State.rows.filter((r, i) =>
    i !== rowIdx
    && r.fetchResult?.podPill === 'miss'
    && !r.skipped
  ).length;

  // Routing trace from chain_attempted (passed via fetchResult)
  const trace = renderRoutingTrace(row);

  // Body — happens or resolved
  const bodyTop = isResolved
    ? renderResolvedBody(row, trace)
    : renderErrorBody(row, trace, docName);

  // Footer
  const isDone = remaining === 0;
  const footer = `
    <div class="ds-footer">
      <button class="skip-link" onclick="window.v2SkipRow(${rowIdx})">Skip this one</button>
      <button class="nav-btn" onclick="window.v2PrevError(${rowIdx})" title="Previous error">← Prev</button>
      ${isDone
        ? `<button class="next-issue-btn done" onclick="window.v2CloseSidebar()">Done — close sidebar ✓</button>`
        : `<button class="next-issue-btn" onclick="window.v2NextError(${rowIdx})">
             Next Error <span class="count-badge">${remaining} left</span> →
           </button>`
      }
    </div>
  `;

  return `
    <div class="${sidebarClass}" id="v2DetailSidebar">
      <div class="ds-header">
        <div class="ds-icon">${isResolved ? '✓' : '!'}</div>
        <div>
          <div class="ds-title">${isResolved ? 'Resolved' : 'Fix Container Error'}</div>
          <div class="ds-subtitle">${escHtml(row.containerNumber)} · Invoice ${escHtml(row.invoiceNumber || '—')}</div>
        </div>
        <button class="ds-close" onclick="window.v2CloseSidebar()">×</button>
      </div>
      <div class="ds-body">
        ${bodyTop}
      </div>
      ${footer}
    </div>
    <div class="sidebar-backdrop open" onclick="window.v2CloseSidebar()"></div>
  `;
}

function renderErrorBody(row, traceHtml, docName) {
  return `
    <div class="ds-section">
      <div class="ds-section-label">Customer</div>
      <div style="font-size:0.92rem; font-weight:600; color:#0f172a;">${escHtml(row.customer || '—')}</div>
    </div>

    <div class="ds-section">
      <div class="ds-section-label">What Happened</div>
      <div class="happened-block">
        <div class="title">${escHtml(docName)} not found in TMS</div>
        <div class="body">${escHtml(row.fetchResult?.message || 'No documents returned by TMS for this container.')}</div>
      </div>
    </div>

    <div class="ds-section">
      <div class="ds-section-label">Routing trace</div>
      ${traceHtml}
    </div>

    <div class="ds-section">
      <div class="ds-section-label">Resolve</div>
      <button class="retry-api-btn" onclick="window.v2RetryRow(${v2State.rows.indexOf(row)})">↻ Retry API call</button>
      <div class="resolve-divider"><span>or upload manually</span></div>
      <label class="ds-upload" for="v2UploadInput-${row.rowNum}">
        <div class="icon">⬆</div>
        <div class="title">Drop ${escHtml(docName)} for ${escHtml(row.containerNumber)}</div>
        <div class="help">.pdf only — replaces whatever the API would have returned</div>
        <input type="file" id="v2UploadInput-${row.rowNum}" accept=".pdf"
               onchange="window.v2HandleSidebarUpload(${v2State.rows.indexOf(row)}, this.files)" />
      </label>
    </div>
  `;
}

function renderResolvedBody(row, traceHtml) {
  const file = row.manualPodFile;
  const summary = file
    ? `<div class="ds-attached">
         <div class="name">${escHtml(file.name)}</div>
         <div class="size">${(file.size / 1024 / 1024).toFixed(2)} MB</div>
         <button class="replace" onclick="document.getElementById('v2UploadInput-${row.rowNum}').click()">Replace</button>
         <input type="file" id="v2UploadInput-${row.rowNum}" accept=".pdf" style="display:none;"
                onchange="window.v2HandleSidebarUpload(${v2State.rows.indexOf(row)}, this.files)" />
       </div>`
    : `<div class="ds-attached">
         <div class="name">Retry succeeded — fetched from TMS</div>
         <div class="size">${escHtml(row.fetchResult?.podLabel || '')}</div>
       </div>`;

  return `
    <div class="ds-section">
      <div class="ds-section-label">Customer</div>
      <div style="font-size:0.92rem; font-weight:600; color:#0f172a;">${escHtml(row.customer || '—')}</div>
    </div>

    <div class="ds-section">
      <div class="ds-section-label">Resolved</div>
      <div class="resolved-block">
        <div class="title">${escHtml(row.fetchResult?.statusText || 'Fetched')}</div>
        <div class="body">This row is now ready to merge.</div>
      </div>
    </div>

    <div class="ds-section">
      <div class="ds-section-label">Routing trace</div>
      ${traceHtml}
    </div>

    <div class="ds-section">
      <div class="ds-section-label">Attached</div>
      ${summary}
    </div>
  `;
}

function renderRoutingTrace(row) {
  const fr = row.fetchResult;
  const lines = [];
  // Header lines: routing decision
  if (row.routingType === 'import') {
    lines.push({ cls: 'note', marker: '→', text: `INV# <code>${escHtml(row.invoiceNumber)}</code> pos-2 → <strong>import</strong>` });
    lines.push({ cls: 'note', marker: '→', text: 'Plan: try POD → BOL → POL → IT' });
  } else if (row.routingType === 'export') {
    lines.push({ cls: 'note', marker: '→', text: `INV# <code>${escHtml(row.invoiceNumber)}</code> pos-2 → <strong>export</strong>` });
    lines.push({ cls: 'note', marker: '→', text: 'Plan: try BOL → POL → ITE' });
  } else {
    lines.push({ cls: 'note', marker: '→', text: 'INV# prefix non-standard — fell back to safety chain' });
    lines.push({ cls: 'note', marker: '→', text: 'Plan: try POD → BOL → POL → IT → ITE' });
  }

  // Steps from chain_attempted
  const chain = fr?.chainAttempted || [];
  for (const step of chain) {
    if (step.outcome === 'tms_hit') {
      lines.push({ cls: 'success', marker: '✓', text: `TMS: <code>${escHtml(step.type)}</code> found` });
    } else if (step.outcome === 'tms_miss') {
      lines.push({ cls: 'fail', marker: '✗', text: `TMS: no <code>${escHtml(step.type)}</code>` });
    } else if (step.outcome === 'tms_error') {
      lines.push({ cls: 'fail', marker: '✗', text: `TMS: <code>${escHtml(step.type)}</code> errored (timeout or API)` });
    }
  }

  // Manual upload completion line
  if (row.manualPodFile) {
    lines.push({ cls: 'success', marker: '✓', text: `Manual upload: <code>${escHtml(row.manualPodFile.name)}</code>` });
  }

  // Final note
  if (fr?.podPill === 'miss' && !row.manualPodFile) {
    lines.push({ cls: 'note', marker: '!', text: 'Exhausted chain — manual upload required' });
  }

  const linesHtml = lines.map(l =>
    `<div class="step ${l.cls}"><span class="marker">${l.marker}</span><span class="text">${l.text}</span></div>`
  ).join('');
  return `<div class="routing-trace">${linesHtml}</div>`;
}
function renderMerging()  { return `<div class="centered-stage"><h1>Merging (M4)</h1><p class="subtitle">Coming in Milestone 4.</p></div>`; }
function renderDone()     { return `<div class="centered-stage"><h1>Done (M4)</h1><p class="subtitle">Coming in Milestone 4.</p></div>`; }

// ── Expose to inline onclick handlers in render strings ──
window.v2TriggerExcel = triggerExcel;
window.v2SetState = setStateV2;

async function v2ClickFetch() {
  // Build the fetch payload — dedup selected rows by container.
  const selected = v2State.rows.filter(r => r.selected);
  const seen = new Set();
  const containers = [];
  for (const row of selected) {
    const key = row.containerNumber.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    containers.push({
      containerNumber: row.containerNumber,
      invoiceNumber: row.invoiceNumber,
    });
  }

  if (containers.length === 0) {
    alert('No rows selected to fetch.');
    return;
  }

  // Reset progress state
  v2State.fetchProgress = 0;
  v2State.fetchTotal = containers.length;
  v2State.fetchCurrentContainer = '';
  v2State.lastFetchedContainer = '';
  // Clear any stale fetchResult on rows we're about to fetch
  for (const row of v2State.rows) {
    if (row.selected) { row.fetchResult = null; row.skipped = false; }
  }

  setStateV2('fetching');

  try {
    const res = await fetch('http://localhost:8787/jobs/fetch-missing', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ containers, doc_types: ['invoice', 'pod'] }),
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Agent rejected fetch: ${res.status} ${text}`);
    }
    const { jobId } = await res.json();
    v2State.jobId = jobId;
    openSseStream(jobId);
  } catch (err) {
    alert(`Couldn't start fetch: ${err.message}\n\nIs the agent running? Check Settings.`);
    setStateV2('review');
  }
}
window.v2ClickFetch = v2ClickFetch;

function v2HandleReadyTab(tab) {
  v2State.activeTab = tab;
  setStateV2('ready');
}
function v2HandleReadySearch(value) {
  v2State.searchQuery = value;
  setStateV2('ready');
}
function v2ToggleAllReady(checked) {
  for (const row of v2State.rows) {
    if (row.fetchResult && row.fetchResult.podPill !== 'miss' && !row.skipped) {
      row.selected = !!checked;
    }
  }
  setStateV2('ready');
}
function v2ToggleFetchRow(rowIdx, checked) {
  const row = v2State.rows[rowIdx];
  if (!row) return;
  row.selected = !!checked;
  // Update count badge live (don't full re-render — keep search focus)
  const cnt = document.querySelector('#v2BtnContinueMerge .sel-count');
  if (cnt) {
    cnt.textContent = v2State.rows.filter(r => r.selected && r.fetchResult && r.fetchResult.podPill !== 'miss').length;
  }
}
function v2ClickContinueMerge() {
  setStateV2('merging');   // M4 stub
}
function v2RetryAllErrors() {
  console.log('v2RetryAllErrors — wired in Task 14');
}
function v2ResumeFetch() {
  console.log('v2ResumeFetch — wired in Task 15');
}

window.v2HandleReadyTab     = v2HandleReadyTab;
window.v2HandleReadySearch  = v2HandleReadySearch;
window.v2ToggleAllReady     = v2ToggleAllReady;
window.v2ToggleFetchRow     = v2ToggleFetchRow;
window.v2ClickContinueMerge = v2ClickContinueMerge;
window.v2RetryAllErrors     = v2RetryAllErrors;
window.v2ResumeFetch        = v2ResumeFetch;

function openSseStream(jobId) {
  // Use plain EventSource — the existing agentBridge wraps this but pulls in
  // auth-token logic we don't need here (agent runs locally).
  const url = `http://localhost:8787/jobs/${encodeURIComponent(jobId)}/stream`;
  const es = new EventSource(url);
  v2State.eventSource = es;

  es.onmessage = (e) => {
    if (v2State.subMode !== 'fetching') return;   // ignore events after cancel/teardown
    let evt;
    try { evt = JSON.parse(e.data); } catch { return; }
    handleSseEvent(evt);
  };
  es.onerror = () => {
    // EventSource auto-retries; if we want hard-fail on max retries, do it here.
    console.warn('[v2 SSE] connection error — EventSource auto-retrying');
  };
}

function handleSseEvent(evt) {
  switch (evt.type) {
    case 'job_started':
      v2State.fetchTotal = evt.total;
      // Re-render to update the total in the progress label
      setStateV2('fetching');
      break;

    case 'container_start':
      v2State.fetchCurrentContainer = evt.containerNumber || '';
      updateProgressLine();
      break;

    case 'container_complete':
      v2State.fetchProgress += 1;
      v2State.lastFetchedContainer = evt.containerNumber || v2State.lastFetchedContainer;
      updateProgressLine();
      // The actual row update came in via prior pod_found / pod_missing events
      break;

    case 'pod_found': {
      const tmsType = evt.tms_doc_type || null;
      const fromTms = !!tmsType;
      patchRow(evt.containerNumber, {
        invPill: 'ok',
        podPill: fromTms ? 'fallback' : 'ok',
        podLabel: tmsType || 'POD',
        statusText: fromTms ? `Fetched (${tmsType})` : 'Fetched',
        chainAttempted: evt.chain_attempted || [],
        message: '',
      });
      break;
    }

    case 'pod_missing':
      patchRow(evt.containerNumber, {
        invPill: 'ok',
        podPill: 'miss',
        podLabel: '—',
        statusText: 'Needs PDF',
        chainAttempted: evt.chain_attempted || [],
        message: evt.message || 'No POD/BOL/POL/IT/ITE found',
      });
      break;

    case 'job_completed':
      finalizeFetch({ cancelled: false });
      break;

    case 'job_cancelled':
    case 'job_paused':
      finalizeFetch({ cancelled: true });
      break;
  }
}

function patchRow(container, fetchResult) {
  // Same-container dedup: apply the result to ALL invoice rows sharing this container
  const containerLower = (container || '').toLowerCase();
  for (let i = 0; i < v2State.rows.length; i++) {
    const row = v2State.rows[i];
    if (row.containerNumber.toLowerCase() !== containerLower) continue;
    row.fetchResult = { ...fetchResult };
    rerenderFetchRow(i);
  }
}

function rerenderFetchRow(rowIdx) {
  const tbody = document.getElementById('v2FetchTbody') || document.getElementById('v2ReadyTbody');
  if (!tbody) return;
  const tr = tbody.querySelector(`tr[data-row-idx="${rowIdx}"]`);
  if (!tr) return;
  const fresh = document.createElement('tbody');
  fresh.innerHTML = fetchRowMarkup(rowIdx, v2State.rows[rowIdx], {
    includeCheck: !!tbody.id.startsWith('v2Ready'),
    activeErrorIdx: v2State.openSidebarRow,
  });
  const newTr = fresh.firstElementChild;
  newTr.classList.add('flash-update');
  tr.replaceWith(newTr);
}

function updateProgressLine() {
  const now = document.querySelector('#mergeToolViewV2 .progress-line .now');
  const fill = document.querySelector('#mergeToolViewV2 .progress-fill');
  if (!now || !fill) return;
  const total = v2State.fetchTotal;
  const done = v2State.fetchProgress;
  const cur = v2State.fetchCurrentContainer || '—';
  now.innerHTML = `<strong>Fetching ${done} / ${total}</strong> &nbsp; <span class="container-name">${escHtml(cur)}</span>`;
  fill.style.width = total > 0 ? `${Math.min(100, Math.round((done / total) * 100))}%` : '0%';
}

function finalizeFetch({ cancelled }) {
  if (v2State.eventSource) {
    v2State.eventSource.close();
    v2State.eventSource = null;
  }
  // Rows that never got a fetchResult stay null → they render as queued in Ready
  // (only relevant on cancel; on completion every row should have a result)
  setStateV2('ready');
}

function v2ToggleShowAll() {
  v2State.showAllInSuccess = !v2State.showAllInSuccess;
  setStateV2('review');         // re-renders the whole review pane
}
window.v2ToggleShowAll = v2ToggleShowAll;
function v2HandleTabClick(tab) {
  v2State.activeTab = tab;
  // Re-render the full pane so the active-tab visual updates.
  setStateV2('review');
}
function v2HandleSearch(value) {
  v2State.searchQuery = value;
  rerenderTbody();
}
function v2HandleSort(mode) {
  v2State.sortMode = mode;
  rerenderTbody();
}
function v2ToggleRow(rowNum, checked) {
  const row = v2State.rows.find(r => r.rowNum === rowNum);
  if (!row) return;
  row.selected = !!checked;
  updateMasterCheckbox();
  updateFetchButton();
}

function v2ToggleAll(checked) {
  const visibleRowNums = new Set(getVisibleRows().map(r => r.rowNum));
  for (const row of v2State.rows) {
    if (visibleRowNums.has(row.rowNum)) row.selected = !!checked;
  }
  rerenderTbody();
  updateFetchButton();
}
window.v2HandleTabClick = v2HandleTabClick;
window.v2HandleSearch   = v2HandleSearch;
window.v2HandleSort     = v2HandleSort;
window.v2ToggleRow      = v2ToggleRow;
window.v2ToggleAll      = v2ToggleAll;
window.initMergeV2 = initMergeV2;

async function v2CancelFetch() {
  if (!v2State.jobId) return;
  try {
    await fetch(`http://localhost:8787/jobs/${encodeURIComponent(v2State.jobId)}/cancel`, {
      method: 'POST',
    });
  } catch (err) {
    console.warn('Cancel POST failed:', err);
  }
  // Don't transition here — wait for the SSE 'job_cancelled' / 'job_paused' event.
  // If the SSE stream dies before the event arrives, manually finalize.
  setTimeout(() => {
    if (v2State.subMode === 'fetching') finalizeFetch({ cancelled: true });
  }, 2000);
}
window.v2CancelFetch = v2CancelFetch;

function v2OpenSidebar(rowIdx) {
  v2State.openSidebarRow = rowIdx;
  setStateV2('ready');
}
function v2CloseSidebar() {
  v2State.openSidebarRow = -1;
  setStateV2('ready');
}
function v2SkipRow(rowIdx) {
  const row = v2State.rows[rowIdx];
  if (!row) return;
  row.skipped = true;
  // Advance to next un-skipped error
  const next = nextErrorIndex(rowIdx);
  if (next >= 0) {
    v2State.openSidebarRow = next;
  } else {
    v2State.openSidebarRow = -1;   // no more errors
  }
  setStateV2('ready');
}
function v2NextError(currentIdx) {
  const next = nextErrorIndex(currentIdx);
  v2State.openSidebarRow = next >= 0 ? next : -1;
  setStateV2('ready');
}
function v2PrevError(currentIdx) {
  const prev = prevErrorIndex(currentIdx);
  if (prev >= 0) {
    v2State.openSidebarRow = prev;
    setStateV2('ready');
  }
}

function nextErrorIndex(fromIdx) {
  for (let i = fromIdx + 1; i < v2State.rows.length; i++) {
    const r = v2State.rows[i];
    if (r.fetchResult?.podPill === 'miss' && !r.skipped) return i;
  }
  // Wrap around
  for (let i = 0; i < fromIdx; i++) {
    const r = v2State.rows[i];
    if (r.fetchResult?.podPill === 'miss' && !r.skipped) return i;
  }
  return -1;
}
function prevErrorIndex(fromIdx) {
  for (let i = fromIdx - 1; i >= 0; i--) {
    const r = v2State.rows[i];
    if (r.fetchResult?.podPill === 'miss' && !r.skipped) return i;
  }
  // Wrap around backward
  for (let i = v2State.rows.length - 1; i > fromIdx; i--) {
    const r = v2State.rows[i];
    if (r.fetchResult?.podPill === 'miss' && !r.skipped) return i;
  }
  return -1;
}

window.v2OpenSidebar  = v2OpenSidebar;
window.v2CloseSidebar = v2CloseSidebar;
window.v2SkipRow      = v2SkipRow;
window.v2NextError    = v2NextError;
window.v2PrevError    = v2PrevError;

async function v2RetryRow(rowIdx) {
  const row = v2State.rows[rowIdx];
  if (!row) return;

  // Show pending state on the button
  const btn = document.querySelector('#v2DetailSidebar .retry-api-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Retrying…'; }

  try {
    const res = await fetch('http://localhost:8787/jobs/fetch-missing', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        containers: [{ containerNumber: row.containerNumber, invoiceNumber: row.invoiceNumber }],
        doc_types: ['pod'],
      }),
    });
    if (!res.ok) throw new Error(`Agent returned ${res.status}`);
    const { jobId } = await res.json();

    // Subscribe to a one-shot stream — we only care about pod_found / pod_missing for our row
    await new Promise((resolve) => {
      const url = `http://localhost:8787/jobs/${encodeURIComponent(jobId)}/stream`;
      const es = new EventSource(url);
      es.onmessage = (e) => {
        let evt; try { evt = JSON.parse(e.data); } catch { return; }
        if (evt.containerNumber !== row.containerNumber) return;
        if (evt.type === 'pod_found') {
          row.fetchResult = {
            invPill: 'ok',
            podPill: evt.tms_doc_type ? 'fallback' : 'ok',
            podLabel: evt.tms_doc_type || 'POD',
            statusText: evt.tms_doc_type ? `Fetched (${evt.tms_doc_type})` : 'Fetched',
            chainAttempted: evt.chain_attempted || [],
            message: '',
          };
          es.close(); resolve();
        } else if (evt.type === 'pod_missing') {
          row.fetchResult = {
            invPill: 'ok', podPill: 'miss', podLabel: '—',
            statusText: 'Needs PDF',
            chainAttempted: evt.chain_attempted || [],
            message: evt.message || '',
          };
          es.close(); resolve();
        } else if (evt.type === 'job_completed' || evt.type === 'job_cancelled') {
          es.close(); resolve();
        }
      };
      es.onerror = () => { es.close(); resolve(); };
    });

    setStateV2('ready');   // re-renders sidebar to ✓ Resolved if pod_found landed
  } catch (err) {
    if (btn) { btn.disabled = false; btn.textContent = '↻ Retry API call'; }
    alert(`Retry failed: ${err.message}`);
  }
}
window.v2RetryRow = v2RetryRow;

function v2HandleSidebarUpload(rowIdx, fileList) {
  const row = v2State.rows[rowIdx];
  if (!row) return;
  const file = fileList && fileList[0];
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.pdf')) {
    alert('Only .pdf files are accepted.');
    return;
  }

  row.manualPodFile = file;
  // Mark fetchResult as "ok" — manual upload bypasses the chain
  row.fetchResult = {
    invPill: 'ok',
    podPill: 'ok',
    podLabel: row.routingType === 'export' ? 'BOL' : 'POD',  // best-effort label; real type from filename or user
    statusText: 'Manual upload',
    chainAttempted: row.fetchResult?.chainAttempted || [],
    message: '',
  };
  setStateV2('ready');   // re-renders sidebar to ✓ Resolved
}
window.v2HandleSidebarUpload = v2HandleSidebarUpload;
