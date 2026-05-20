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
  routingDecisionFor, uid, fmtSize,
} from '../../shared/utils.js';
import { agentBridge } from '../../shared/agent-client.js';
import {
  MODES as MODES_LIST, modeByKey,
  saveMergedFiles, subfolderFor,
} from './merge-v2-output.js';
import { runMergeMode } from './merge-v2-engine.js';

// ── Module-local state ──
const v2State = {
  subMode: 'empty',          // empty | loading | review | fetching | ready | merge
  excelFile: null,
  excelHeaders: [],
  rows: [],
  loadingError: null,
  searchQuery: '',
  sortMode: 'excel',
  activeTab: 'all',
  routingTypeFilter: 'all', // 'all' | 'import' | 'export' | 'warehouse' | 'unknown'
  showAllInSuccess: false,
  // M3: fetch + sidebar
  jobId: null,
  eventSource: null,
  fetchProgress: 0,
  fetchTotal: 0,
  fetchCurrentContainer: '',
  lastFetchedContainer: '',
  openSidebarRow: null,
  queuedRetries: [],
  completedContainers: null,
  // M4: merge screen
  outputLocation: null,           // absolute path; null → agent uses OUTPUT_DIR
  completedModes: {},             // { modeKey: { stats, files: [{filename, path}], completedAt: Date } }
  runningMode: null,              // modeKey of in-progress merge (null = nothing running)
  mergeProgress: { done: 0, total: 0, current: '' },
  confirmPopup: null,             // null | { uncheckedCount, onContinue, onCancel }
};

// ── localStorage for output location ──
const LS_OUTPUT_LOCATION = 'mergeV2OutputLocation';
function loadSavedOutputLocation() {
  try { return localStorage.getItem(LS_OUTPUT_LOCATION) || null; } catch { return null; }
}
function saveOutputLocation(path) {
  try {
    if (path) localStorage.setItem(LS_OUTPUT_LOCATION, path);
    else localStorage.removeItem(LS_OUTPUT_LOCATION);
  } catch {}
}

// State group is what the header/toggle buttons key off.
// Within a group, sub-states (loading vs empty, fetching vs review) pick which renderer fires.
const STATE_GROUP = {
  empty: 's1', loading: 's1',
  review: 's2', fetching: 's2',
  ready: 's3',
  merge: 's4',
};

const STATES = {
  s1: () => v2State.subMode === 'loading' ? renderLoading() : renderEmpty(),
  s2: () => v2State.subMode === 'fetching' ? renderFetching() : renderReview(),
  s3: () => renderReady(),
  s4: () => renderMerge(),
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
  v2State.outputLocation = loadSavedOutputLocation();
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
        // Fire-and-forget pause — we don't await it; if it fails, the agent will
        // notice no consumer and tear down on its own
        agentBridge._authFetch(
          `${agentBridge.baseUrl}/jobs/${encodeURIComponent(v2State.jobId)}/pause`,
          { method: 'POST' }
        ).catch(() => {});
        v2State.jobId = null;
      }
    } catch (_) { /* best-effort cleanup, never throw */ }

    v2State.excelFile = null;
    v2State.excelHeaders = [];
    v2State.rows = [];
    v2State.loadingError = null;
    v2State.searchQuery = '';
    v2State.sortMode = 'excel';
    v2State.activeTab = 'all';
    v2State.routingTypeFilter = 'all';
    v2State.showAllInSuccess = false;
    v2State.fetchProgress = 0;
    v2State.fetchTotal = 0;
    v2State.fetchCurrentContainer = '';
    v2State.lastFetchedContainer = '';
    v2State.openSidebarRow = null;
    v2State.queuedRetries = [];
    v2State.completedContainers = null;
    v2State.completedModes = {};
    v2State.runningMode = null;
    v2State.mergeProgress = { done: 0, total: 0, current: '' };
    v2State.confirmPopup = null;
    // outputLocation is preserved across runs — survives the reset.
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
  // After any re-render that includes the sidebar, latch SortableJS onto the
  // doc list so the user can drag attachments to reorder. Re-init each render
  // because innerHTML wipes prior Sortable instances along with the DOM.
  if (v2State.openSidebarRow !== null && v2State.openSidebarRow >= 0) {
    const row = v2State.rows[v2State.openSidebarRow];
    if (row) initSortableForRow(row);
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
  v2State.routingTypeFilter = 'all';
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

// Normalize an Excel DATE cell to "MM/DD/YYYY".
// SheetJS may give us a JS Date (when cellDates is true), a string, or a number
// (Excel serial). Returns '' for empty/unparseable values.
function formatInvoiceDate(raw) {
  if (raw === '' || raw === null || raw === undefined) return '';
  // JS Date — use UTC getters because SheetJS with cellDates:true returns
  // dates anchored to UTC midnight; local-time getters would show the prior
  // day in any negative-UTC-offset timezone.
  if (raw instanceof Date && !isNaN(raw.getTime())) {
    const mm = String(raw.getUTCMonth() + 1).padStart(2, '0');
    const dd = String(raw.getUTCDate()).padStart(2, '0');
    const yyyy = raw.getUTCFullYear();
    return `${mm}/${dd}/${yyyy}`;
  }
  // Excel serial number (days since 1899-12-30)
  if (typeof raw === 'number' && isFinite(raw)) {
    const ms = Math.round((raw - 25569) * 86400 * 1000);
    const d = new Date(ms);
    if (!isNaN(d.getTime())) {
      const mm = String(d.getUTCMonth() + 1).padStart(2, '0');
      const dd = String(d.getUTCDate()).padStart(2, '0');
      const yyyy = d.getUTCFullYear();
      return `${mm}/${dd}/${yyyy}`;
    }
  }
  // String — pass through trimmed
  return String(raw).trim();
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
    wb = XLSX.read(buf, { type: 'array', cellDates: true });
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
  const dateKey      = findColumnKey(headers, CSV_ALIASES.invoiceDate);

  // containerKey may be absent for warehouse-only manifests — that is allowed.

  const rows = [];
  for (let i = 0; i < sheetRows.length; i++) {
    const r = sheetRows[i];
    const inv = invoiceKey ? String(r[invoiceKey] || '').trim() : '';
    const cn  = containerKey ? String(r[containerKey] || '').trim() : '';
    const wo  = woKey ? String(r[woKey] || '').trim() : '';

    // INV# is required for all rows. Rows with no INV# are skipped silently
    // (they are typically blank trailer rows in the Excel export).
    if (!inv && !cn) continue;  // completely blank row — skip

    const decision = routingDecisionFor({
      invoiceNumber: inv,
      workOrderNumber: wo,
    });

    // Non-warehouse rows require a container number.
    if (decision.type !== 'warehouse' && !cn) continue;

    rows.push({
      id: uid(),                // stable per-row id (used by side-panel doc list)
      rowNum: i + 2,            // sheet row 1 is headers, so first data row → 2
      // Warehouse rows carry no real container — strip any placeholder value.
      containerNumber: decision.type === 'warehouse' ? '' : cn,
      invoiceNumber: inv,
      workOrderNumber: decision.type === 'warehouse' ? '' : wo,
      customer: customerKey ? String(r[customerKey] || '').trim() : '',
      invoiceDate: dateKey ? formatInvoiceDate(r[dateKey]) : '',
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
    return { error: 'No usable rows — every row was missing both an INV# and a Container #.' };
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
        <div class="drop-help">Needs an INV# column. Container # is optional (required only for import/export rows).</div>
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
  if (v2State.routingTypeFilter && v2State.routingTypeFilter !== 'all') {
    rows = rows.filter(r => r.routingType === v2State.routingTypeFilter);
  }
  if (v2State.searchQuery) {
    const q = v2State.searchQuery.toLowerCase();
    rows = rows.filter(r => r.containerNumber.toLowerCase().includes(q));
  }
  return sortRows(rows, v2State.sortMode);
}

function routingTypeFilterTabs() {
  const total      = v2State.rows.length;
  const imports    = v2State.rows.filter(r => r.routingType === 'import').length;
  const exports_   = v2State.rows.filter(r => r.routingType === 'export').length;
  const warehouses = v2State.rows.filter(r => r.routingType === 'warehouse').length;
  const unknown    = v2State.rows.filter(r => r.routingType === 'unknown').length;
  const f = v2State.routingTypeFilter || 'all';
  const btn = (key, label, count) => `
    <button class="tab ${f === key ? 'active' : ''}" onclick="window.v2SetRoutingTypeFilter('${key}')">
      ${label} <span class="count">${count}</span>
    </button>`;
  return `
    <div class="filter-tabs">
      ${btn('all', 'All', total)}
      ${btn('import', 'Import', imports)}
      ${btn('export', 'Export', exports_)}
      ${btn('warehouse', 'Warehouse', warehouses)}
      ${btn('unknown', 'Unknown', unknown)}
    </div>
  `;
}

function v2SetRoutingTypeFilter(key) {
  v2State.routingTypeFilter = key;
  setStateV2('review');
}
window.v2SetRoutingTypeFilter = v2SetRoutingTypeFilter;

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
  if (row.routingType === 'import')    return `<span class="will-chip import">POD</span>`;
  if (row.routingType === 'export')    return `<span class="will-chip export">BL/POL</span>`;
  if (row.routingType === 'warehouse') return `<span class="will-chip whdocs">All QBO Docs</span>`;
  return `<span class="will-chip unknown">?</span>`;
}

function highlightInvLetter(inv) {
  if (!inv || inv.length < 2) return escHtml(inv);
  const c = inv[1].toUpperCase();
  if (c === 'M' || c === 'E' || c === 'X' || c === 'W') {
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

  const isWarehouse = row.routingType === 'warehouse';

  const containerCell = isWarehouse
    ? `<td><span class="na-cell">—</span></td>`
    : `<td><span class="mono">${escHtml(row.containerNumber)}</span></td>`;

  const woCell = hasAnyWO()
    ? (isWarehouse
        ? `<td><span class="na-cell">—</span></td>`
        : `<td>${row.workOrderNumber ? `<span class="mono">${escHtml(row.workOrderNumber)}</span>` : '<span style="color:#cbd5e1;">—</span>'}</td>`)
    : '';

  const willCell = `<td>${willChipFor(row)}</td>`;

  return `<tr class="${trClass}" data-row-num="${row.rowNum}">
    <td class="check-col"><input type="checkbox" class="row-check" ${checkAttr} onchange="window.v2ToggleRow(${row.rowNum}, this.checked)" /></td>
    <td style="color:#94a3b8; font-size:0.8rem;">${row.rowNum}</td>
    ${containerCell}
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
    const expected = row.expectedDoc === 'BL/POL' ? 'BL' : (row.expectedDoc === '?' ? '?' : 'POD');
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
  if (row.fetchResult && !row.skipped) {
    return `<td><button class="view-details-btn" onclick="event.stopPropagation(); window.v2OpenSidebar(${rowIdx})" title="View fetch details">ⓘ View</button></td>`;
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

  // M4: errored rows are unchecked by default but still INTERACTIVE.
  // Skipped (dedup) rows remain disabled — they can't be merged at all.
  const hasFetch    = !!row.fetchResult;
  const isErrorRow  = row.fetchResult?.podPill === 'miss';
  const isSkipped   = !!row.skipped;
  const interactive = hasFetch && !isSkipped;
  const checkAttrs  = `${row.selected && interactive ? 'checked' : ''} ${interactive ? '' : 'disabled'}`;
  const checkTitle  = isErrorRow ? 'This row is missing its POD/BL. If checked, the merge will include only the invoice page for this container.'
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

  const isWarehouse = row.routingType === 'warehouse';
  const containerCell = isWarehouse
    ? `<td><span class="na-cell">—</span></td>`
    : `<td><span class="mono">${escHtml(row.containerNumber)}</span></td>`;

  return `<tr class="${trClass}" ${trAttrs} data-row-idx="${rowIdx}">
    ${checkColMaybe}
    ${containerCell}
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
  const manualCount = v2State.rows.filter(r => r.manualPodFile).length;
  const helpText = manualCount > 0
    ? `${manualCount} manual upload${manualCount === 1 ? '' : 's'} attached. Drop more to override fetched docs.`
    : 'Drop or click to add PDFs for missing or late docs. Filename must contain the container #.';

  return `
    <div class="top-bar">
      <div class="file-summary">
        <div class="icon-box xlsx">XLS</div>
        <div class="text">
          <div class="name">${fname}</div>
          <div class="meta">${total} unique container${total !== 1 ? 's' : ''}</div>
        </div>
      </div>
      <label class="pdf-drop-card ${manualCount > 0 ? 'has-manual' : ''}" for="v2BulkPdfInput"
             ondragover="event.preventDefault(); this.classList.add('drag-over');"
             ondragleave="this.classList.remove('drag-over');"
             ondrop="event.preventDefault(); this.classList.remove('drag-over'); window.v2HandleBulkPdfDrop(event.dataTransfer.files);">
        <div class="icon-box pdf">PDF</div>
        <div class="text">
          <div class="label-line">PDFs <span class="count-pill">${manualCount}</span></div>
          <div class="help">${helpText}</div>
        </div>
        <input type="file" id="v2BulkPdfInput" accept=".pdf" multiple
               style="display:none;"
               onchange="window.v2HandleBulkPdfDrop(this.files); this.value='';" />
      </label>
    </div>
  `;
}

async function v2HandleBulkPdfDrop(fileList) {
  const files = Array.from(fileList).filter(f => f.name.toLowerCase().endsWith('.pdf'));
  if (files.length === 0) {
    alert('Only .pdf files are accepted.');
    return;
  }

  // Build a lookup of every identifier from the manifest.
  // identifier = { idType: 'container'|'inv', value: 'lowercased', rowIdx: N }
  const identifiers = [];
  for (let i = 0; i < v2State.rows.length; i++) {
    const r = v2State.rows[i];
    const cn = (r.containerNumber || '').toLowerCase().trim();
    const inv = (r.invoiceNumber || '').toLowerCase().trim();
    if (cn) identifiers.push({ idType: 'container', value: cn, rowIdx: i });
    if (inv) identifiers.push({ idType: 'inv', value: inv, rowIdx: i });
  }

  const matchedSummary = [];  // [{ fileName, rowNums: [1,2,3] }]
  const unmatched = [];

  for (const file of files) {
    const lower = file.name.toLowerCase();
    const hits = new Map();  // rowIdx → true

    for (const id of identifiers) {
      if (lower.includes(id.value)) {
        hits.set(id.rowIdx, true);
      }
    }

    if (hits.size === 0) {
      unmatched.push(file.name);
      continue;
    }

    const rowNums = [];
    for (const rowIdx of hits.keys()) {
      const row = v2State.rows[rowIdx];
      // Attach to every matching row's document list
      row.documents = row.documents || [];
      row.documents.push({
        id: uid(), name: file.name, source: 'added',
        converted: false, pageCount: 0, sizeBytes: file.size,
        failReason: null, _file: file,
      });
      // Promote row to "ready" if it was previously blocked by a missing POD
      if (row.fetchResult?.podPill === 'miss') {
        row.fetchResult.podPill = 'ok';
        row.fetchResult.statusText = 'Manual upload';
        row.selected = true;
      }
      rowNums.push(row.rowNum);
    }
    matchedSummary.push({ fileName: file.name, rowNums });
  }

  setStateV2(v2State.subMode);

  // Build the feedback message
  const multiAttach = matchedSummary.filter(m => m.rowNums.length > 1);
  const total = matchedSummary.length;
  let msg = `${total} of ${files.length} PDF${files.length === 1 ? '' : 's'} matched.`;
  if (multiAttach.length > 0) {
    msg += '\n\nMulti-row attaches:';
    for (const m of multiAttach) {
      msg += `\n  • ${m.fileName} → Rows ${m.rowNums.join(', ')}`;
    }
  }
  if (unmatched.length > 0) {
    const sample = unmatched.slice(0, 3).map(n => `\n  • ${n}`).join('');
    const more = unmatched.length > 3 ? `\n  …and ${unmatched.length - 3} more` : '';
    msg += `\n\nNo container # or INV# match in filename for:${sample}${more}`;
  }
  if (total > 0 || unmatched.length > 0) alert(msg);
}
window.v2HandleBulkPdfDrop = v2HandleBulkPdfDrop;

function routingSummaryBand() {
  const imports    = v2State.rows.filter(r => r.routingType === 'import').length;
  const exports_   = v2State.rows.filter(r => r.routingType === 'export').length;
  const warehouses = v2State.rows.filter(r => r.routingType === 'warehouse').length;
  const unknown    = v2State.rows.filter(r => r.routingType === 'unknown').length;
  return `
    <div class="routing-summary">
      <span class="label">Will fetch</span>
      <span class="group">
        <span class="chip import">POD</span>
        <strong>${imports}</strong> import${imports !== 1 ? 's' : ''}
      </span>
      <span class="group">
        <span class="chip export">BL/POL</span>
        <strong>${exports_}</strong> export${exports_ !== 1 ? 's' : ''}
      </span>
      <span class="group">
        <span class="chip warehouse">All QBO Docs</span>
        <strong>${warehouses}</strong> warehouse
      </span>
      ${unknown ? `<span class="group">
        <span class="chip unknown">?</span>
        <strong>${unknown}</strong> unknown
      </span>` : ''}
      <span class="hint">Decided by INV# letter (M / E / W) · falls back to WO# letter when prefix is non-standard</span>
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
    ${routingTypeFilterTabs()}
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
    ${routingTypeFilterTabs()}
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
  // Use fetchTotal (= unique container count from job_started) for consistency
  // across progress, tabs, and toolbar. Falls back to selected count before SSE arrives.
  const total = v2State.fetchTotal || v2State.rows.filter(r => r.selected).length || v2State.rows.length;
  const done = v2State.fetchProgress;
  const cur = v2State.fetchCurrentContainer || '—';
  const percent = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;

  // Tabs split by current state
  const fetchedRows = v2State.rows.filter(r => r.fetchResult && r.fetchResult.podPill !== 'miss');
  const failedRows  = v2State.rows.filter(r => r.fetchResult?.podPill === 'miss');
  const fetchedCount = fetchedRows.length;
  const failedCount  = failedRows.length;
  const allCount     = v2State.rows.length;

  // Active tab — default to 'all' if not yet set or doesn't apply to fetching
  const fetchTab = (v2State.activeTab === 'fetched' || v2State.activeTab === 'failed')
    ? v2State.activeTab
    : 'all';

  // Filter rows by active tab
  let visibleRows;
  if (fetchTab === 'fetched')      visibleRows = fetchedRows;
  else if (fetchTab === 'failed')  visibleRows = failedRows;
  else                              visibleRows = v2State.rows;

  const bodyRows = visibleRows.map(row => {
    const i = v2State.rows.indexOf(row);
    return fetchRowMarkup(i, row, {
      includeCheck: false,
      activeErrorIdx: v2State.openSidebarRow,
    });
  }).join('');

  // Sidebar overlay — opening Fix Error mid-fetch should NOT pause the fetch
  const sidebarHtml = (v2State.openSidebarRow !== null && v2State.openSidebarRow >= 0)
    ? renderSidebar(v2State.openSidebarRow)
    : '';

  return `
    ${topBarWithDrop()}
    ${routingSummaryBand()}
    <div class="progress-line">
      <div class="now">
        <strong>Fetching ${done} / ${total}</strong>
        &nbsp; <span class="container-name">${escHtml(cur)}</span>
      </div>
      <div class="progress-track"><div class="progress-fill" style="width:${percent}%;"></div></div>
      <button class="pause-btn" onclick="window.v2CancelFetch()" title="Pause — you can resume from where you left off">⏸ Pause</button>
      <button class="cancel-link" onclick="window.v2CancelAndReset()" title="Cancel and discard all progress">Cancel</button>
    </div>
    <div class="tabs-row">
      <div class="tabs">
        <button class="tab ${fetchTab === 'all' ? 'active' : ''}" onclick="window.v2HandleFetchTab('all')">All <span class="count">${allCount}</span></button>
        <button class="tab ${fetchTab === 'fetched' ? 'active' : ''}" onclick="window.v2HandleFetchTab('fetched')">Fetched <span class="count" id="v2FetchTabFetchedCount">${fetchedCount}</span></button>
        <button class="tab has-issues ${fetchTab === 'failed' ? 'active' : ''}" onclick="window.v2HandleFetchTab('failed')">Failed <span class="count" id="v2FetchTabFailedCount">${failedCount}</span></button>
      </div>
    </div>
    <div class="toolbar">
      <input type="text" class="search" placeholder="Search containers…" />
      <span class="filter-meta" id="v2FetchToolbarMeta">${done} / ${total} fetched · ${failedCount} failed</span>
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
    ${sidebarHtml}
  `;
}

function v2HandleFetchTab(tab) {
  v2State.activeTab = tab;
  setStateV2('fetching');   // stays on fetching state, just re-renders with the new filter
}
window.v2HandleFetchTab = v2HandleFetchTab;
function renderReady() {
  const all = v2State.rows;
  const queued = all.filter(r => !r.fetchResult && !r.skipped);
  // Error/ready sets use the canonical error definition (POD-miss OR invoice-miss).
  // See getErrorRows() — the export and these filters must agree so the Errors-tab count,
  // the button label, and the downloaded xlsx all line up.
  const errors = all.filter(r => !r.skipped && r.fetchResult && (r.fetchResult.podPill === 'miss' || r.fetchResult.invPill === 'miss'));
  const ready  = all.filter(r => !r.skipped && r.fetchResult && r.fetchResult.podPill !== 'miss' && r.fetchResult.invPill !== 'miss');

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
  // M4: errored rows can also be selected; they'll merge with invoice-page-only.
  const selectableRows = all.filter(r => r.fetchResult && !r.skipped);
  const selected = selectableRows.filter(r => r.selected).length;

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
        <button class="merge-btn resume" onclick="window.v2ResumeFetch()" title="Pick up where the fetch left off">
          ↻ Resume fetch
          <span class="count-badge">${queued.length} queued</span>
        </button>
        <button class="merge-btn" id="v2BtnContinueMerge" ${selected === 0 ? 'disabled' : ''}
                onclick="window.v2ClickContinueMerge()" title="Merge what's already fetched — queued rows wait for Resume">
          Continue to Merge
          <span class="count-badge"><span class="sel-count">${selected}</span> selected</span>
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

  // Toolbar — adds the mass-retry + download-errors buttons when on the Errors tab with errors present.
  const showErrorActions = v2State.activeTab === 'errors' && errors.length > 0;
  const massRetry = showErrorActions
    ? `<button class="mass-retry-btn" onclick="window.v2RetryAllErrors()">↻ Retry all errors</button>`
    : '';
  const downloadErrorsBtn = showErrorActions
    ? `<button class="download-errors-btn" onclick="window.v2DownloadErrors()">📥 Download errors (${errors.length})</button>`
    : '';

  const toolbarHtml = `
    <div class="toolbar">
      <input type="text" class="search" placeholder="Search containers…"
             value="${escHtml(v2State.searchQuery)}"
             oninput="window.v2HandleReadySearch(this.value)" />
      ${downloadErrorsBtn}
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
    ${renderConfirmPopup()}
  `;
}

// ── Side-panel document list (Task 14) ─────────────────────────────
//
// Each row keeps a customizable `documents` array — the canonical list of
// PDFs that will be merged for that row. The user can drag to reorder,
// click × to remove (local only — never touches QBO), drop in extras with
// the + Add document zone, or click Reset to restore the original fetched
// list. State lives on the row; merge engine (Task 17) reads from it.
//
// Doc shape:
//   { id, name, source: 'qbo'|'tms'|'added', converted,
//     pageCount, sizeBytes, failReason, _file? }
function humanizeFailReason(reason) {
  if (!reason) return '';
  const r = String(reason);
  if (r.includes('password'))             return "Couldn't convert — file is password-protected";
  if (r.includes('corrupt'))              return "Couldn't convert — file is corrupt";
  if (r.includes('timeout'))              return "Couldn't convert — took too long";
  if (r.includes('empty'))               return "Couldn't convert — workbook is empty";
  if (r.includes('excel_not_installed'))  return "Couldn't convert — Excel isn't installed";
  if (r === 'download_failed')            return "Couldn't download from QBO";
  if (r === 'unsupported_type')           return "Unsupported file type — only PDF and Excel are converted";
  return "Couldn't process this file";
}

function buildInitialDocList(row) {
  const docs = [];
  const fr = row.fetchResult;
  if (!fr) return docs;

  // Invoice is always present (downloaded for all row types in Task 8)
  const invName = fr.invoiceFile || row.invoiceFile;
  if (invName) {
    docs.push({
      id: uid(), name: invName, source: 'qbo',
      converted: false, pageCount: 1, sizeBytes: 0, failReason: null,
    });
  }

  if (row.routingType === 'warehouse') {
    for (const att of (fr.warehouseAttachments || [])) {
      docs.push({
        id: uid(), name: att.fileName, source: 'qbo',
        converted: !!att.converted, pageCount: att.pageCount || 0,
        sizeBytes: att.sizeBytes || 0, failReason: null,
      });
    }
    for (const fail of (fr.warehouseFailures || [])) {
      docs.push({
        id: uid(), name: fail.fileName, source: 'qbo',
        converted: false, pageCount: 0, sizeBytes: 0,
        failReason: fail.reason,
      });
    }
  } else {
    // Import/export — single POD/BL/POL doc
    if (fr.podFile) {
      docs.push({
        id: uid(), name: fr.podFile,
        source: fr.podSource || 'qbo',  // 'qbo' or 'tms'
        converted: false, pageCount: 0, sizeBytes: 0, failReason: null,
      });
    }
  }
  row._originalDocs = JSON.parse(JSON.stringify(docs));  // snapshot for Reset
  return docs;
}

function ensureDocList(row) {
  if (!row.documents) row.documents = buildInitialDocList(row);
  return row.documents;
}

function renderAttachmentRow(rowId, doc) {
  const classes = ['attachment-row'];
  if (doc.source === 'added') classes.push('manual');
  if (doc.failReason) classes.push('fail');

  const dragHandle = `
    <span class="drag-handle" title="Drag to reorder">
      <svg width="10" height="14" viewBox="0 0 10 14" fill="currentColor">
        <circle cx="2" cy="2" r="1.2"/><circle cx="8" cy="2" r="1.2"/>
        <circle cx="2" cy="7" r="1.2"/><circle cx="8" cy="7" r="1.2"/>
        <circle cx="2" cy="12" r="1.2"/><circle cx="8" cy="12" r="1.2"/>
      </svg>
    </span>`;

  const icon = doc.failReason
    ? `<span class="icon fail" aria-hidden="true">
         <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
       </span>`
    : doc.source === 'added'
      ? `<span class="icon manual" aria-hidden="true">
           <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12h14"/></svg>
         </span>`
      : `<span class="icon ok" aria-hidden="true">
           <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
         </span>`;

  const subtitle = doc.failReason
    ? `<div class="fail-reason">${escHtml(humanizeFailReason(doc.failReason))}</div>`
    : (doc.pageCount || doc.sizeBytes)
      ? `<div class="meta">${doc.pageCount ? doc.pageCount + ' pages' : ''}${doc.pageCount && doc.sizeBytes ? ' · ' : ''}${doc.sizeBytes ? fmtSize(doc.sizeBytes) : ''}</div>`
      : '';

  const sourceTag =
    doc.source === 'tms'   ? '<span class="tag tms">From TMS</span>' :
    doc.source === 'added' ? '<span class="tag added">Added by you</span>' :
                             '<span class="tag qbo">From QBO</span>';
  const convertTag = doc.converted ? '<span class="tag convert">XLSX → PDF</span>' : '';

  return `
    <div class="${classes.join(' ')}" data-doc-id="${doc.id}">
      ${dragHandle}
      ${icon}
      <div style="flex:1; min-width:0;">
        <div class="name">${escHtml(doc.name)}</div>
        ${subtitle}
      </div>
      ${sourceTag}
      ${convertTag}
      <button class="remove-btn" title="Remove from this merge (doesn't touch QBO)"
              onclick="window.v2RemoveDoc('${rowId}', '${doc.id}')">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>
  `;
}

function renderDocumentsSection(row) {
  const docs = ensureDocList(row);
  const rowId = row.id;
  const count = docs.length;
  const headerLabel = count === 1 ? '1 file' : `${count} files`;

  const docsMarkup = docs.length === 0
    ? `<div style="padding: 10px 4px; color:#94a3b8; font-size: 0.8rem;">No documents fetched yet.</div>`
    : docs.map(d => renderAttachmentRow(rowId, d)).join('');

  return `
    <div class="ds-section">
      <div class="ds-section-label">Documents in this merge · ${headerLabel}</div>
      <div data-doc-list="${rowId}">
        ${docsMarkup}
      </div>
      <button class="add-doc-zone" type="button" onclick="window.v2OpenAddDocPicker('${rowId}')">
        <svg class="plus-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12h14"/></svg>
        <span class="label-line">Add document</span>
        <span class="help-line">drop a PDF or click to browse</span>
      </button>
      <div class="reset-row">
        <span>Drag handles to reorder · × to remove</span>
        <span class="reset-link" onclick="window.v2ResetDocs('${rowId}')">Reset</span>
      </div>
    </div>
  `;
}

function initSortableForRow(row) {
  if (!window.Sortable) return;
  const list = document.querySelector(`[data-doc-list="${row.id}"]`);
  if (!list) return;
  Sortable.create(list, {
    handle: '.drag-handle',
    animation: 150,
    onEnd: (evt) => {
      if (evt.oldIndex === evt.newIndex) return;
      const docs = row.documents || [];
      const moved = docs.splice(evt.oldIndex, 1)[0];
      docs.splice(evt.newIndex, 0, moved);
      // Don't re-render — Sortable already moved the DOM nodes
    },
  });
}

// ── Per-row doc action handlers (exposed on window) ──
window.v2RemoveDoc = function(rowId, docId) {
  const row = v2State.rows.find(r => r.id === rowId);
  if (!row) return;
  row.documents = (row.documents || []).filter(d => d.id !== docId);
  setStateV2(v2State.subMode);
};

window.v2OpenAddDocPicker = function(rowId) {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = 'application/pdf';
  input.multiple = true;
  input.onchange = e => window.v2AddDocsToRow(rowId, e.target.files);
  input.click();
};

window.v2AddDocsToRow = function(rowId, files) {
  const row = v2State.rows.find(r => r.id === rowId);
  if (!row || !files || files.length === 0) return;
  row.documents = row.documents || [];
  for (const f of files) {
    row.documents.push({
      id: uid(), name: f.name, source: 'added',
      converted: false, pageCount: 0, sizeBytes: f.size,
      failReason: null, _file: f,
    });
  }
  setStateV2(v2State.subMode);
};

window.v2ResetDocs = function(rowId) {
  const row = v2State.rows.find(r => r.id === rowId);
  if (!row || !row._originalDocs) return;
  row.documents = JSON.parse(JSON.stringify(row._originalDocs));
  setStateV2(v2State.subMode);
};

function renderSidebar(rowIdx) {
  const row = v2State.rows[rowIdx];
  if (!row) return '';

  const isResolved = !!(row.fetchResult && row.fetchResult.podPill !== 'miss');
  const sidebarClass = `detail-sidebar open${isResolved ? ' resolved' : ''}`;

  const isExport = row.routingType === 'export';
  const docName = isExport ? 'BL or POL' : (row.routingType === 'unknown' ? 'POD, BL, or POL' : 'POD');

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

  // Error / empty banners (shown only after the row has been fetched)
  const docs = row.documents || [];
  const failedDocs = docs.filter(d => d.failReason);
  const hasZeroDocs = docs.length === 0;
  const hasPartialFailure = !hasZeroDocs && failedDocs.length > 0;
  let banner = '';
  if (hasZeroDocs && row.fetchResult) {
    banner = `<div class="panel-empty-banner">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
      </svg>
      <span><strong>No documents on QBO.</strong> Upload one manually below — this row can't merge without it.</span>
    </div>`;
  } else if (hasPartialFailure) {
    banner = `<div class="panel-error-banner">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
      </svg>
      <span><strong>${failedDocs.length} attachment${failedDocs.length > 1 ? 's' : ''} couldn't be converted.</strong>
        The merge will continue with what worked — drop in a replacement if you have one.</span>
    </div>`;
  }

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
      ${banner}
      <div class="ds-body">
        ${bodyTop}
      </div>
      ${footer}
    </div>
    <div class="sidebar-backdrop open" onclick="window.v2CloseSidebar()"></div>
  `;
}

function classifyError(row, docName) {
  // Returns { title, body } for the "What Happened" block based on what the
  // backend actually told us. Three categories: invoice-not-in-QBO, system
  // error (network/timeout/auth), or genuine doc-not-found.
  const fr = row.fetchResult || {};
  const status = fr.statusText || '';
  const msg = fr.message || '';
  const chain = fr.chainAttempted || [];

  if (status === 'Invoice not in QBO') {
    return {
      title: 'Invoice not found in QuickBooks',
      body: `QuickBooks returned no invoice with number <code>${escHtml(row.invoiceNumber || '')}</code>. Verify the INV# in your spreadsheet matches what's in QBO, then Retry — or upload the ${escHtml(docName)} manually.`,
    };
  }

  if (status === 'Error') {
    // System error — categorize by message pattern
    if (/getaddrinfo|name or service|11001|enotfound|dns/i.test(msg)) {
      return {
        title: "Couldn't reach the server",
        body: 'The agent failed to resolve the TMS or QBO hostname. Check your internet connection, then verify TMS and QBO are connected (Settings → Status). Once they\'re back, click Retry.',
      };
    }
    if (/timed? ?out|timeout/i.test(msg)) {
      return {
        title: 'Request timed out',
        body: 'TMS or QBO took too long to respond. Try Retry once or twice — if it keeps timing out, upload the document manually.',
      };
    }
    if (/login|auth|unauthor|401|403/i.test(msg)) {
      return {
        title: 'Login required',
        body: 'Your TMS or QBO session expired. Open Settings to re-authorize, then click Retry.',
      };
    }
    return {
      title: 'Error during fetch',
      body: msg ? escHtml(msg) : 'Something went wrong before TMS could respond. Try Retry, or upload the document manually.',
    };
  }

  // Genuine "not found" — chain was actually tried and exhausted
  const triedTypes = chain.filter(s => s.outcome === 'tms_miss').map(s => s.type);
  if (triedTypes.length > 0) {
    return {
      title: `${escHtml(docName)} not found in TMS`,
      body: `TMS confirmed it has no ${triedTypes.join(', ')} on file for this container. Upload the ${escHtml(docName)} manually below.`,
    };
  }
  return {
    title: `${escHtml(docName)} not found`,
    body: msg ? escHtml(msg) : 'No documents returned for this container. Upload manually below.',
  };
}

function renderErrorBody(row, traceHtml, docName) {
  const { title, body } = classifyError(row, docName);
  const rowIdx = v2State.rows.indexOf(row);
  const isQueuedForRetry = v2State.queuedRetries.some(q => q.rowIdx === rowIdx);
  const retryBtn = isQueuedForRetry
    ? `<button class="retry-api-btn" disabled>⏳ Queued — will retry after current fetch</button>`
    : `<button class="retry-api-btn" onclick="window.v2RetryRow(${rowIdx})">↻ Retry API call</button>`;
  return `
    <div class="ds-section">
      <div class="ds-section-label">Customer</div>
      <div style="font-size:0.92rem; font-weight:600; color:#0f172a;">${escHtml(row.customer || '—')}</div>
    </div>

    <div class="ds-section">
      <div class="ds-section-label">What Happened</div>
      <div class="happened-block">
        <div class="title">${title}</div>
        <div class="body">${body}</div>
      </div>
    </div>

    <div class="ds-section">
      <div class="ds-section-label">Routing trace</div>
      ${traceHtml}
    </div>

    <div class="ds-section">
      <div class="ds-section-label">Resolve</div>
      ${retryBtn}
      <div class="resolve-divider"><span>or upload manually</span></div>
      <label class="ds-upload" for="v2UploadInput-${row.rowNum}">
        <div class="icon">⬆</div>
        <div class="title">Drop ${escHtml(docName)} for ${escHtml(row.containerNumber)}</div>
        <div class="help">.pdf only — replaces whatever the API would have returned</div>
        <input type="file" id="v2UploadInput-${row.rowNum}" accept=".pdf"
               onchange="window.v2HandleSidebarUpload(${rowIdx}, this.files)" />
      </label>
    </div>
  `;
}

function renderResolvedBody(row, traceHtml) {
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

    ${renderDocumentsSection(row)}
  `;
}

function renderRoutingTrace(row) {
  const fr = row.fetchResult;
  const lines = [];
  // Header lines: routing decision
  if (row.routingType === 'import') {
    lines.push({ cls: 'note', marker: '→', text: `INV# <code>${escHtml(row.invoiceNumber)}</code> pos-2 → <strong>import</strong>` });
    lines.push({ cls: 'note', marker: '→', text: 'Plan: try POD → BL → POL → IT' });
  } else if (row.routingType === 'export') {
    lines.push({ cls: 'note', marker: '→', text: `INV# <code>${escHtml(row.invoiceNumber)}</code> pos-2 → <strong>export</strong>` });
    lines.push({ cls: 'note', marker: '→', text: 'Plan: try BL → POL → ITE' });
  } else {
    lines.push({ cls: 'note', marker: '→', text: 'INV# prefix non-standard — fell back to safety chain' });
    lines.push({ cls: 'note', marker: '→', text: 'Plan: try POD → BL → POL → IT → ITE' });
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

  // Final note — distinguish "we actually tried and exhausted the chain"
  // from "we never got far enough to try" (system error, invoice missing).
  if (fr?.podPill === 'miss' && !row.manualPodFile) {
    const status = fr?.statusText || '';
    const triedAny = chain.some(s => s.outcome === 'tms_miss' || s.outcome === 'tms_hit');
    if (status === 'Invoice not in QBO') {
      lines.push({ cls: 'note', marker: '!', text: "Couldn't find this invoice in QuickBooks — never got to check TMS" });
    } else if (status === 'Error' || (chain.length > 0 && chain.every(s => s.outcome === 'tms_error'))) {
      lines.push({ cls: 'note', marker: '!', text: "Something went wrong before we could finish — try Retry once you're connected" });
    } else if (triedAny) {
      lines.push({ cls: 'note', marker: '!', text: "TMS doesn't have this document — please upload it manually below" });
    } else {
      lines.push({ cls: 'note', marker: '!', text: "No documents found for this container — please upload one manually below" });
    }
  }

  const linesHtml = lines.map(l =>
    `<div class="step ${l.cls}"><span class="marker">${l.marker}</span><span class="text">${l.text}</span></div>`
  ).join('');
  return `<div class="routing-trace">${linesHtml}</div>`;
}
// ── M4: pre-merge confirmation popup ──
function renderConfirmPopup() {
  if (!v2State.confirmPopup) return '';
  const { uncheckedCount } = v2State.confirmPopup;
  return `
    <div class="v2-modal-backdrop" onclick="window.v2CancelConfirm()"></div>
    <div class="v2-modal" onclick="event.stopPropagation()">
      <div class="v2-modal-title"><span class="icon">ⓘ</span> Confirm merge selection</div>
      <div class="v2-modal-body">
        ${uncheckedCount} row${uncheckedCount === 1 ? '' : 's'} ${uncheckedCount === 1 ? 'is' : 'are'} unchecked and will not be included in this merge.
      </div>
      <div class="v2-modal-actions">
        <button class="btn-secondary" onclick="window.v2CancelConfirm()">Go Back</button>
        <button class="btn-primary" onclick="window.v2AcceptConfirm()">Continue ▶</button>
      </div>
    </div>
  `;
}

function v2AcceptConfirm() {
  const cb = v2State.confirmPopup?.onContinue;
  v2State.confirmPopup = null;
  if (cb) cb();
}
function v2CancelConfirm() {
  v2State.confirmPopup = null;
  setStateV2(v2State.subMode);   // re-render to drop the popup
}

window.v2AcceptConfirm = v2AcceptConfirm;
window.v2CancelConfirm = v2CancelConfirm;

// ── M4: Merge screen (pickable + completed cards) ──
function renderMerge() {
  const isRunning = !!v2State.runningMode;

  // Header: output location + (back to ready already wired by setStateV2)
  const locText = v2State.outputLocation
    ? shortenPath(v2State.outputLocation)
    : 'Desktop (default)';
  // Download-errors button: shown when at least one merge has completed and there are still error rows.
  const mergeErrorCount = getErrorRows().length;
  const completedAny = Object.keys(v2State.completedModes || {}).length > 0;
  const downloadErrorsBtn = (mergeErrorCount > 0 && completedAny)
    ? `<button class="download-errors-btn" onclick="window.v2DownloadErrors()">📥 Download errors (${mergeErrorCount})</button>`
    : '';
  const headerHtml = `
    <div class="merge-screen-header">
      <h2>Choose a merge format</h2>
      <span class="header-spacer"></span>
      <button class="output-location-btn" onclick="window.v2ChangeOutputLocation()" title="Change where files are saved">
        <span class="label-prefix">Output:</span>
        <span class="path-text">${escHtml(locText)}</span>
      </button>
      ${downloadErrorsBtn}
    </div>
  `;

  // Optional running banner with progress (shared with the in-flight onProgress patcher)
  const runningBanner = isRunning
    ? `<div class="merge-running-banner">${runningBannerContent(v2State.runningMode, v2State.mergeProgress)}</div>`
    : '';

  // Group cards by group (per-container vs combined)
  const perCont = MODES_LIST.filter(m => m.group === 'per-container');
  const combined = MODES_LIST.filter(m => m.group === 'combined');

  return `
    ${headerHtml}
    ${runningBanner}
    <div class="mode-group">
      <div class="mode-group-label">Per-container outputs (one PDF per container)</div>
      <div class="mode-grid">
        ${perCont.map(m => renderModeCard(m, isRunning)).join('')}
      </div>
    </div>
    <div class="mode-group">
      <div class="mode-group-label">Single combined output (one PDF total)</div>
      <div class="mode-grid">
        ${combined.map(m => renderModeCard(m, isRunning)).join('')}
      </div>
    </div>
  `;
}

function renderModeCard(mode, anyRunning) {
  const completed = v2State.completedModes[mode.key];
  const running = v2State.runningMode === mode.key;
  const dim = anyRunning && !running && !completed;

  if (running) {
    return `
      <div class="mode-card running">
        <div class="title">${escHtml(mode.title)}</div>
        <div class="description">${escHtml(mode.description)}</div>
      </div>
    `;
  }
  if (completed) {
    const stats = formatCompletedStats(completed);
    const showOpenFile = (mode.group === 'combined');   // hide for per-container
    return `
      <div class="mode-card completed">
        <div class="title">${escHtml(mode.title)}</div>
        <div class="stats-line">${escHtml(stats)}</div>
        <div class="completed-actions">
          ${showOpenFile ? `<button class="card-action-btn" onclick="window.v2OpenOutputFile('${mode.key}')">Open File</button>` : ''}
          <button class="card-action-btn" onclick="window.v2OpenOutputFolder('${mode.key}')">Open Folder</button>
          <button class="card-action-btn" onclick="window.v2RerunMode('${mode.key}')">Re-run</button>
        </div>
      </div>
    `;
  }
  // Pickable
  return `
    <button class="mode-card ${dim ? 'dim' : ''}" onclick="window.v2ClickModeCard('${mode.key}')">
      <div class="title">${escHtml(mode.title)}</div>
      <div class="description">${escHtml(mode.description)}</div>
    </button>
  `;
}

function modeNameOf(modeKey) {
  const m = MODES_LIST.find(x => x.key === modeKey);
  return m ? m.title : modeKey;
}

// Inner HTML of the running banner — shared between full render and mid-merge progress patch.
function runningBannerContent(modeKey, p) {
  const pct = p.total > 0 ? Math.round((p.done / p.total) * 100) : 0;
  return `
    <span>⟳ Merging ${escHtml(modeNameOf(modeKey))}…</span>
    <span style="color:#94a3b8;">${p.done} / ${p.total}${p.current ? ' · ' + escHtml(p.current) : ''}</span>
    <span style="margin-left:auto; font-weight:600;">${pct}%</span>
  `;
}

function formatCompletedStats(completed) {
  const { stats, completedAt } = completed;
  const time = completedAt instanceof Date
    ? completedAt.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
    : '';
  const fileLabel = stats.fileCount === 1 ? '1 PDF' : `${stats.fileCount} PDFs`;
  return `${fileLabel} · ${stats.totalPages} pages · ${formatBytes(stats.totalBytes)}${time ? ` · ${time}` : ''}`;
}

function formatBytes(bytes) {
  // Sub-MB outputs would round to "0.0 MB" — show KB instead so users don't think the file is empty.
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function shortenPath(p) {
  if (!p) return '';
  if (p.length <= 36) return p;
  // Keep drive + last two folders
  const parts = p.replace(/\\/g, '/').split('/').filter(Boolean);
  if (parts.length <= 3) return p;
  return `${parts[0]}\\…\\${parts[parts.length - 2]}\\${parts[parts.length - 1]}`;
}

async function v2ClickModeCard(modeKey) {
  // Block re-entry while another merge is running.
  if (v2State.runningMode) return;

  // Build the row set: selected, non-skipped, non-errored OR errored-but-checked.
  // Engine handles the case where document is missing — silently produces invoice-only entry.
  const rows = v2State.rows.filter(r => r.selected && r.fetchResult && !r.skipped);
  if (rows.length === 0) {
    alert('No rows selected. Go back to Ready and check at least one row.');
    return;
  }

  // Materialize row.documents for any row whose side panel was never opened —
  // the engine (Task 17) iterates row.documents, so it must be populated.
  for (const r of rows) ensureDocList(r);

  v2State.runningMode = modeKey;
  v2State.mergeProgress = { done: 0, total: rows.length, current: '' };
  setStateV2('merge');   // shows running banner + spinner card

  try {
    const result = await runMergeMode({
      rows,
      jobId: v2State.jobId,
      modeKey,
      onProgress: (p) => {
        v2State.mergeProgress = p;
        // Update only the banner DOM — avoid full re-render mid-merge.
        const banner = document.querySelector('.merge-running-banner');
        if (banner) banner.innerHTML = runningBannerContent(modeKey, p);
      },
    });

    if (result.files.length === 0) {
      alert('Merge produced no files. Check the row selection and try again.');
      return;
    }

    // Write to disk
    const saveRes = await saveMergedFiles({
      files: result.files,
      modeKey,
      baseLocation: v2State.outputLocation,
      openFolder: false,   // don't auto-open; user clicks Open Folder if they want
    });
    if (saveRes.error) {
      alert(`Save failed: ${saveRes.error}`);
      return;
    }

    // Record completion. Pull file paths back from the agent response so Open File works.
    v2State.completedModes[modeKey] = {
      stats: result.stats,
      files: (saveRes.files || []).filter(f => !f.error),
      outputDir: saveRes.outputDir,
      completedAt: new Date(),
    };
  } catch (err) {
    console.error('Merge failed:', err);
    alert(`Merge failed: ${err.message}`);
  } finally {
    v2State.runningMode = null;
    v2State.mergeProgress = { done: 0, total: 0, current: '' };
    setStateV2('merge');
  }
}

function v2OpenOutputFile(modeKey) {
  const completed = v2State.completedModes[modeKey];
  if (!completed || completed.files.length === 0) return;
  // Single-output modes have exactly one file. We hide the button for per-container modes.
  const target = completed.files[0]?.path;
  if (target) agentBridge.openPath(target);
}

function v2OpenOutputFolder(modeKey) {
  const completed = v2State.completedModes[modeKey];
  if (!completed) return;
  if (completed.outputDir) agentBridge.openPath(completed.outputDir);
}

function v2RerunMode(modeKey) {
  // Re-run uses the same row selection. saveMergedFiles already passes overwriteFolder: true.
  // Drop the existing completion record so the card flips back to running while it works.
  delete v2State.completedModes[modeKey];
  v2ClickModeCard(modeKey);
}

async function v2ChangeOutputLocation() {
  const res = await agentBridge.pickFolder(v2State.outputLocation);
  if (res.error) { alert(`Couldn't open folder picker: ${res.error}`); return; }
  if (!res.path) return;   // user cancelled
  v2State.outputLocation = res.path;
  saveOutputLocation(res.path);
  setStateV2('merge');
}

function v2BackToReady() {
  // Preserves rows + completedModes — user can come back and run more modes.
  setStateV2('ready');
}

function v2NewMerge() {
  const completedCount = Object.keys(v2State.completedModes || {}).length;
  if (completedCount > 0) {
    if (!confirm(`This will reset the workflow and clear ${completedCount} completed merge${completedCount === 1 ? '' : 's'} from the Merge screen. (Files on disk are kept.) Continue?`)) {
      return;
    }
  }
  setStateV2('empty');
}

window.v2ClickModeCard = v2ClickModeCard;
window.v2OpenOutputFile = v2OpenOutputFile;
window.v2OpenOutputFolder = v2OpenOutputFolder;
window.v2RerunMode = v2RerunMode;
window.v2ChangeOutputLocation = v2ChangeOutputLocation;
window.v2BackToReady = v2BackToReady;
window.v2NewMerge = v2NewMerge;

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
  v2State.completedContainers = new Set();
  // Clear any stale fetchResult on rows we're about to fetch
  for (const row of v2State.rows) {
    if (row.selected) { row.fetchResult = null; row.skipped = false; }
  }

  setStateV2('fetching');

  try {
    v2State.jobIncludesInvoice = true;
    v2State.jobIncludesDoc = true;
    const result = await agentBridge.fetchMissing(containers, ['invoice', 'pod']);
    if (result.error || !result.jobId) {
      throw new Error(result.error || 'Agent did not return a jobId');
    }
    v2State.jobId = result.jobId;
    openSseStream(result.jobId);
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
  // M4: errored rows now toggle with the master too (they can be opted-in for invoice-only).
  for (const row of v2State.rows) {
    if (row.fetchResult && !row.skipped) {
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
    // M4: count errored-but-checked rows too — they merge as invoice-only.
    cnt.textContent = v2State.rows.filter(r => r.selected && r.fetchResult && !r.skipped).length;
  }
}
function v2ClickContinueMerge() {
  // Count unchecked rows that COULD have been merged (have fetchResult, not skipped).
  // Errored-but-unchecked rows count too — they could be opted into via the new interactive checkbox.
  const candidateRows = v2State.rows.filter(r => r.fetchResult && !r.skipped);
  const uncheckedCount = candidateRows.filter(r => !r.selected).length;

  const proceed = () => setStateV2('merge');

  if (uncheckedCount > 0) {
    v2State.confirmPopup = { uncheckedCount, onContinue: proceed };
    setStateV2(v2State.subMode);   // re-render Ready with the popup overlay
    return;
  }
  proceed();
}
async function v2RetryAllErrors() {
  const errors = v2State.rows.filter(r => r.fetchResult?.podPill === 'miss' && !r.skipped);
  if (errors.length === 0) return;

  // Dedup by container
  const seen = new Set();
  const containers = [];
  for (const row of errors) {
    const key = row.containerNumber.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    containers.push({
      containerNumber: row.containerNumber,
      invoiceNumber: row.invoiceNumber,
    });
    // Reset row state so the SSE handler re-marks them
    row.fetchResult = null;
  }

  v2State.fetchProgress = 0;
  v2State.fetchTotal = containers.length;
  v2State.fetchCurrentContainer = '';
  v2State.completedContainers = new Set();

  setStateV2('fetching');

  try {
    v2State.jobIncludesInvoice = false;
    v2State.jobIncludesDoc = true;
    const result = await agentBridge.fetchMissing(containers, ['pod']);
    if (result.error || !result.jobId) {
      throw new Error(result.error || 'Agent did not return a jobId');
    }
    v2State.jobId = result.jobId;
    openSseStream(result.jobId);
  } catch (err) {
    alert(`Couldn't start mass retry: ${err.message}`);
    setStateV2('ready');
  }
}

async function v2ResumeFetch() {
  // Find queued rows (selected, no fetchResult, not skipped)
  const queued = v2State.rows.filter(r => r.selected && !r.fetchResult && !r.skipped);
  if (queued.length === 0) return;

  // Dedup by container
  const seen = new Set();
  const containers = [];
  for (const row of queued) {
    const key = row.containerNumber.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    containers.push({
      containerNumber: row.containerNumber,
      invoiceNumber: row.invoiceNumber,
    });
  }

  // Carry over fetchProgress so the progress label reads "Fetching N+1 / total"
  v2State.fetchTotal = containers.length;
  v2State.fetchProgress = 0;
  v2State.fetchCurrentContainer = '';
  v2State.completedContainers = new Set();

  setStateV2('fetching');
  try {
    v2State.jobIncludesInvoice = true;
    v2State.jobIncludesDoc = true;
    const result = await agentBridge.fetchMissing(containers, ['invoice', 'pod']);
    if (result.error || !result.jobId) {
      throw new Error(result.error || 'Agent did not return a jobId');
    }
    v2State.jobId = result.jobId;
    openSseStream(result.jobId);
  } catch (err) {
    alert(`Couldn't resume fetch: ${err.message}`);
    setStateV2('ready');
  }
}

window.v2HandleReadyTab     = v2HandleReadyTab;
window.v2HandleReadySearch  = v2HandleReadySearch;
window.v2ToggleAllReady     = v2ToggleAllReady;
window.v2ToggleFetchRow     = v2ToggleFetchRow;
window.v2ClickContinueMerge = v2ClickContinueMerge;
window.v2RetryAllErrors     = v2RetryAllErrors;
window.v2ResumeFetch        = v2ResumeFetch;

function openSseStream(jobId) {
  // Route through agentBridge.streamProgress — it adds the auth token via
  // ?token= query param (EventSource can't set headers), wires every named
  // event the backend emits, handles auto-retry, and closes on terminal events.
  v2State.eventSource = agentBridge.streamProgress(jobId, (evt) => {
    if (v2State.subMode !== 'fetching') return;   // ignore events after cancel/teardown
    handleSseEvent(evt);
  });
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
      updateLiveCounters();
      break;

    case 'container_complete':
      bumpProgress(evt.containerNumber);
      v2State.lastFetchedContainer = evt.containerNumber || v2State.lastFetchedContainer;
      updateLiveCounters();
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
      updateLiveCounters();
      break;
    }

    case 'pod_missing':
      patchRow(evt.containerNumber, {
        invPill: 'ok',
        podPill: 'miss',
        podLabel: '—',
        statusText: 'Needs PDF',
        chainAttempted: evt.chain_attempted || [],
        message: evt.message || 'No POD/BL/POL/IT/ITE found',
      });
      updateLiveCounters();
      break;

    // Backend exits _process_one_container early on these — no container_complete
    // follows. Mark the row as a miss so it shows in the Errors tab + nudge progress.
    case 'not_found':
      patchRow(evt.containerNumber, {
        invPill: 'miss', podPill: 'miss', podLabel: '—',
        statusText: 'Invoice not in QBO',
        chainAttempted: [],
        message: `Invoice ${evt.invoiceNumber || ''} not found in QBO`,
      });
      bumpProgress(evt.containerNumber);
      updateLiveCounters();
      break;

    case 'container_error':
      patchRow(evt.containerNumber, {
        invPill: 'miss', podPill: 'miss', podLabel: '—',
        statusText: 'Error',
        chainAttempted: [],
        message: evt.error || 'Unknown error during fetch',
      });
      bumpProgress(evt.containerNumber);
      updateLiveCounters();
      break;

    case 'login_required':
      alert(`QBO login required: ${evt.message || 'Please re-authorize via Settings.'}`);
      finalizeFetch({ cancelled: true });
      break;

    case 'job_complete':
      finalizeFetch({ cancelled: false });
      break;

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
    // Fresh fetch → drop the cached doc list so the side panel rebuilds from the
    // new fetchResult (and the Reset snapshot reflects what was just fetched).
    row.documents = undefined;
    row._originalDocs = undefined;
    // Snapshot per-file jobIds so a POD-only retry doesn't clobber the row's pointer to the
    // folder where the invoice already lives. Each fetchMissing call sets the doc-type flags
    // below; we only update the corresponding jobId field. Legacy fetchJobId is kept as a
    // single fallback so older code paths and the engine's haveAnyJob guard still work.
    if (v2State.jobIncludesInvoice) {
      row.invoiceJobId = v2State.jobId || row.invoiceJobId || null;
    }
    if (v2State.jobIncludesDoc) {
      row.docJobId = v2State.jobId || row.docJobId || null;
    }
    row.fetchJobId = v2State.jobId || row.fetchJobId || null;
    rerenderFetchRow(i);
  }
}

function rerenderFetchRow(rowIdx) {
  const tbody = document.getElementById('v2FetchTbody') || document.getElementById('v2ReadyTbody');
  if (!tbody) return;
  const tr = tbody.querySelector(`tr[data-row-idx="${rowIdx}"]`);
  if (!tr) {
    // Row isn't in the current view (e.g. user filtered to Fetched/Failed tab and this row
    // just changed state). Trigger a full re-render so the row appears in the right tab.
    if (v2State.subMode === 'fetching' && (v2State.activeTab === 'fetched' || v2State.activeTab === 'failed')) {
      setStateV2('fetching');
    }
    return;
  }
  const fresh = document.createElement('tbody');
  fresh.innerHTML = fetchRowMarkup(rowIdx, v2State.rows[rowIdx], {
    includeCheck: !!tbody.id.startsWith('v2Ready'),
    activeErrorIdx: v2State.openSidebarRow,
  });
  const newTr = fresh.firstElementChild;
  newTr.classList.add('flash-update');
  tr.replaceWith(newTr);
}

// Dedup progress increments by container — guards against duplicate SSE events
// (reconnects, backend race, etc.) from pushing fetchProgress past fetchTotal.
function bumpProgress(container) {
  const key = (container || '').toLowerCase();
  if (!key) return;
  if (!v2State.completedContainers) v2State.completedContainers = new Set();
  if (v2State.completedContainers.has(key)) return;
  v2State.completedContainers.add(key);
  v2State.fetchProgress = Math.min(v2State.fetchTotal || Infinity, v2State.fetchProgress + 1);
}

// Surgical updates while Fetching — keeps progress label, progress bar,
// tab counts, and toolbar meta in sync without a full re-render.
function updateLiveCounters() {
  const total = v2State.fetchTotal;
  const done = v2State.fetchProgress;
  const cur = v2State.fetchCurrentContainer || '—';
  const fetchedCount = v2State.rows.filter(r => r.fetchResult && r.fetchResult.podPill !== 'miss').length;
  const failedCount  = v2State.rows.filter(r => r.fetchResult?.podPill === 'miss').length;
  const percent = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;

  const now = document.querySelector('#mergeToolViewV2 .progress-line .now');
  if (now) now.innerHTML = `<strong>Fetching ${done} / ${total}</strong> &nbsp; <span class="container-name">${escHtml(cur)}</span>`;
  const fill = document.querySelector('#mergeToolViewV2 .progress-fill');
  if (fill) fill.style.width = `${percent}%`;
  const fc = document.getElementById('v2FetchTabFetchedCount');
  if (fc) fc.textContent = fetchedCount;
  const ec = document.getElementById('v2FetchTabFailedCount');
  if (ec) ec.textContent = failedCount;
  const meta = document.getElementById('v2FetchToolbarMeta');
  if (meta) meta.textContent = `${done} / ${total} fetched · ${failedCount} failed`;
}

function finalizeFetch({ cancelled }) {
  if (v2State.eventSource) {
    v2State.eventSource.close();
    v2State.eventSource = null;
  }
  // Keep v2State.jobId — rows store their own fetchJobId via patchRow, but we leave the
  // last-active job reference around as a fallback for any code that hasn't been per-row-ified.
  // Rows that never got a fetchResult stay null → they render as queued in Ready.
  setStateV2('ready');
  // If the user queued single-row retries while the main fetch was running,
  // run them now that we're idle. Sequential to avoid hammering the agent.
  if (v2State.queuedRetries.length > 0) {
    processQueuedRetries();
  }
}

async function processQueuedRetries() {
  // Snapshot — new retries can still be queued while we process
  const queue = v2State.queuedRetries.slice();
  v2State.queuedRetries = [];
  for (const { rowIdx } of queue) {
    // v2RetryRow checks subMode and runs immediately when not 'fetching'
    try { await v2RetryRow(rowIdx); }
    catch (err) { console.warn('Queued retry failed for row', rowIdx, err); }
  }
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
    await agentBridge._authFetch(
      `${agentBridge.baseUrl}/jobs/${encodeURIComponent(v2State.jobId)}/pause`,
      { method: 'POST' }
    );
  } catch (err) {
    console.warn('Cancel POST failed:', err);
  }
  // Don't transition here — wait for the SSE 'job_paused' event from the backend.
  // If the SSE stream dies before the event arrives, manually finalize.
  setTimeout(() => {
    if (v2State.subMode === 'fetching') finalizeFetch({ cancelled: true });
  }, 2000);
}
window.v2CancelFetch = v2CancelFetch;

async function v2CancelAndReset() {
  if (!confirm('Cancel the fetch and discard all progress? You\'ll need to start over from the spreadsheet.')) return;
  // setStateV2('empty') runs the defensive teardown — closes SSE + POSTs /pause —
  // and resets every v2State field to clean slate.
  setStateV2('empty');
}
window.v2CancelAndReset = v2CancelAndReset;

// Sidebar is an overlay — opening/closing it should NOT change subMode.
// During a live fetch, the user wants the sidebar to float over the
// Fetching state while the rest of the batch keeps running. Re-render
// whichever state we're already in.
function v2OpenSidebar(rowIdx) {
  v2State.openSidebarRow = rowIdx;
  setStateV2(v2State.subMode);
}
function v2CloseSidebar() {
  v2State.openSidebarRow = -1;
  setStateV2(v2State.subMode);
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
  setStateV2(v2State.subMode);
}
function v2NextError(currentIdx) {
  const next = nextErrorIndex(currentIdx);
  v2State.openSidebarRow = next >= 0 ? next : -1;
  setStateV2(v2State.subMode);
}
function v2PrevError(currentIdx) {
  const prev = prevErrorIndex(currentIdx);
  if (prev >= 0) {
    v2State.openSidebarRow = prev;
    setStateV2(v2State.subMode);
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

  // If a fetch is currently running, queue the retry to run after it
  // finishes. Two concurrent fetch jobs against the same agent can race
  // (shared QBO/TMS sessions, browser pool), so we serialize.
  if (v2State.subMode === 'fetching') {
    if (!v2State.queuedRetries.find(q => q.rowIdx === rowIdx)) {
      v2State.queuedRetries.push({ rowIdx });
    }
    const btn = document.querySelector('#v2DetailSidebar .retry-api-btn');
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '⏳ Queued — will retry after current fetch';
    }
    return;
  }

  // Show pending state on the button
  const btn = document.querySelector('#v2DetailSidebar .retry-api-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Retrying…'; }

  try {
    v2State.jobIncludesInvoice = false;
    v2State.jobIncludesDoc = true;
    const result = await agentBridge.fetchMissing(
      [{ containerNumber: row.containerNumber, invoiceNumber: row.invoiceNumber }],
      ['pod'],
    );
    if (result.error || !result.jobId) {
      throw new Error(result.error || 'Agent did not return a jobId');
    }

    // Subscribe to a one-shot stream — we only care about events for our row
    await new Promise((resolve) => {
      const handle = agentBridge.streamProgress(result.jobId, (evt) => {
        // Terminal events without a containerNumber (job_complete, job_paused) end the wait
        if (evt.type === 'job_complete' || evt.type === 'job_paused') {
          handle.close(); resolve();
          return;
        }
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
          // Repoint this row's POD at the retry job's folder so the merge engine finds the
          // newly-downloaded {cn}_pod.pdf. Leave invoiceJobId alone — POD-only retries don't
          // touch the invoice file. fetchJobId is updated as the legacy fallback.
          row.docJobId = result.jobId;
          row.fetchJobId = result.jobId;
          handle.close(); resolve();
        } else if (evt.type === 'pod_missing') {
          row.fetchResult = {
            invPill: 'ok', podPill: 'miss', podLabel: '—',
            statusText: 'Needs PDF',
            chainAttempted: evt.chain_attempted || [],
            message: evt.message || '',
          };
          handle.close(); resolve();
        } else if (evt.type === 'not_found') {
          row.fetchResult = {
            invPill: 'miss', podPill: 'miss', podLabel: '—',
            statusText: 'Invoice not in QBO',
            chainAttempted: [],
            message: `Invoice ${evt.invoiceNumber || ''} not found in QBO`,
          };
          handle.close(); resolve();
        } else if (evt.type === 'container_error') {
          row.fetchResult = {
            invPill: 'miss', podPill: 'miss', podLabel: '—',
            statusText: 'Error',
            chainAttempted: [],
            message: evt.error || 'Unknown error during retry',
          };
          handle.close(); resolve();
        }
      });
    });

    setStateV2('ready');   // re-renders sidebar to ✓ Resolved if pod_found landed
  } catch (err) {
    if (btn) { btn.disabled = false; btn.textContent = '↻ Retry API call'; }
    alert(`Retry failed: ${err.message}`);
  }
}
window.v2RetryRow = v2RetryRow;

// ── Error export helpers ──

// Returns the current set of error rows: rows whose fetch produced a POD miss
// or an invoice miss, and which the user did not manually skip.
function getErrorRows() {
  return v2State.rows.filter(r =>
    !r.skipped &&
    r.fetchResult &&
    (r.fetchResult.podPill === 'miss' || r.fetchResult.invPill === 'miss')
  );
}

// Maps error rows to the 9-column structure for the Excel export.
function buildErrorExportRows(errorRows) {
  return errorRows.map(r => {
    const fr = r.fetchResult || {};
    const invMiss = fr.invPill === 'miss';
    const podMiss = fr.podPill === 'miss';
    const whatsMissing =
      invMiss && podMiss ? 'Invoice + POD missing'
      : invMiss          ? 'Invoice missing'
      : podMiss          ? 'POD missing'
                         : '';
    const chain = Array.isArray(fr.chainAttempted) ? fr.chainAttempted.join(' → ') : '';
    return {
      'Row #':            r.rowNum,
      'Invoice Date':     r.invoiceDate || '',
      'Customer':         r.customer || '',
      'Container #':      r.containerNumber || '',
      'INV #':            r.invoiceNumber || '',
      'WO #':             r.workOrderNumber || '',
      "What's missing":   whatsMissing,
      'Where we looked':  chain,
      'Status detail':    fr.statusText || '',
    };
  });
}

function downloadErrorsXlsx() {
  const errorRows = getErrorRows();
  if (errorRows.length === 0) {
    // No-op if somehow called with zero errors (button should be hidden anyway).
    return;
  }

  const sheetData = buildErrorExportRows(errorRows);
  const ws = XLSX.utils.json_to_sheet(sheetData);
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, 'Errors');

  const today = new Date();
  const yyyy = today.getFullYear();
  const mm = String(today.getMonth() + 1).padStart(2, '0');
  const dd = String(today.getDate()).padStart(2, '0');
  const filename = `merge-errors-${yyyy}-${mm}-${dd}.xlsx`;

  XLSX.writeFile(wb, filename);
}

window.v2DownloadErrors = downloadErrorsXlsx;

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
  // Surface the manual upload as an "added" doc so it shows in the docs list
  // and ships with the merge. Done here (not at render time) so the row's
  // shape is correct before any code reads row.documents.
  ensureDocList(row);
  const already = (row.documents || []).some(d => d._file === file);
  if (!already) {
    row.documents = row.documents || [];
    row.documents.push({
      id: uid(),
      name: file.name,
      source: 'added',
      converted: false,
      pageCount: 0,
      sizeBytes: file.size || 0,
      failReason: null,
      _file: file,
    });
  }
  // Mark fetchResult as "ok" — manual upload bypasses the chain
  row.fetchResult = {
    invPill: 'ok',
    podPill: 'ok',
    podLabel: row.routingType === 'export' ? 'BL' : 'POD',  // best-effort label; real type from filename or user
    statusText: 'Manual upload',
    chainAttempted: row.fetchResult?.chainAttempted || [],
    message: '',
  };
  setStateV2('ready');   // re-renders sidebar to ✓ Resolved
}
window.v2HandleSidebarUpload = v2HandleSidebarUpload;
