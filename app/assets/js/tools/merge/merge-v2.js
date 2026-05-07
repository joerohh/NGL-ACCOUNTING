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
function renderReady()    { return `<div class="centered-stage"><h1>Ready (M3)</h1><p class="subtitle">Coming in Milestone 3.</p></div>`; }
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

function openSseStream(jobId) {
  // Placeholder — Task 11 wires the real event handlers
  // (split this way so Task 9's PR is independently testable).
  console.log('openSseStream', jobId, '— handlers wired in Task 11');
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

window.v2CancelFetch    = () => { console.log('v2CancelFetch — wired in Task 13'); };
window.v2OpenSidebar    = (idx) => { console.log('v2OpenSidebar', idx, '— wired in Task 12'); };
window.v2ToggleFetchRow = (idx, checked) => { console.log('v2ToggleFetchRow', idx, checked, '— wired in Task 11'); };
