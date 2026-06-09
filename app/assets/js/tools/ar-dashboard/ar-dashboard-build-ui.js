// AR Dashboard — build flow (5-drop input + summary preview + save).
//
// State lives on arState.buildInputs / arState.buildResult (see shared/state.js).
// Engine + parsers come from ./ar-dashboard-build*.js (M1).
//
// The drop UI is rendered into a page-level host (arRenderBuildPage) and the
// preview is rendered into a body-level modal overlay (openPreview).

import { arState } from '../../shared/state.js';
import {
  parseYesterdaysWorkbook,
  parseQboDailyCollection,
  parseQboDailySchedule,
  parseTabBankRemittance,
  parseTmsReconcile,
} from './ar-dashboard-build-loader.js';
import { arBuildToday } from './ar-dashboard-build.js';

const SAVE_FOLDER_KEY = 'arBuildSaveFolder';

// ---------------------------------------------------------------------------
// Slot definitions — order = visible row order
// ---------------------------------------------------------------------------

const SLOTS = [
  {
    key: 'yesterday',
    label: "Yesterday's workbook",
    hint: 'AR_AGING_MM_DD_YYYY.xlsx',
    blurb: 'The most recent hand-built or built-by-this-tool workbook.',
    parse: parseYesterdaysWorkbook,
    doneSummary: (parsed) => `${parsed.ar_register.length.toLocaleString()} rows · ${parsed.sheet_name}`,
    fileNameMatch: (name) => /^AR_AGING_.*\.xlsx?$/i.test(name),
    autoFill: true,
  },
  {
    key: 'qbo_collection',
    label: 'QBO Daily Collection',
    hint: 'NGL Transportation, Inc._Daily Collection Report.xlsx',
    blurb: "Yesterday's payments — Invoice + Payment lines per customer.",
    parse: parseQboDailyCollection,
    doneSummary: (parsed) => `${parsed.rows.length.toLocaleString()} invoice/payment lines`,
    fileNameMatch: (name) => /daily collection/i.test(name),
  },
  {
    key: 'qbo_schedule',
    label: 'QBO Daily Schedule',
    hint: 'NGL Transportation, Inc._Daily Schedule List.xlsx',
    blurb: "Today's new invoices created in QBO.",
    parse: parseQboDailySchedule,
    doneSummary: (parsed) => `${parsed.rows.length.toLocaleString()} new invoices`,
    fileNameMatch: (name) => /daily schedule/i.test(name),
  },
  {
    key: 'tab_bank',
    label: 'TAB BANK Remittance',
    hint: 'Collection_Payment.xlsx',
    blurb: "Yesterday's TAB BANK posting — for catching posting errors (used in M3).",
    parse: parseTabBankRemittance,
    doneSummary: (parsed) => `${parsed.rows.length.toLocaleString()} remittance rows`,
    fileNameMatch: (name) => /collection[_ ]?payment/i.test(name),
  },
  {
    key: 'tms_reconcile',
    label: 'TMS Reconcile',
    hint: 'APAR RECONCILE_MMDDYY.xlsx',
    blurb: 'TMS-side AR rows — amount reconciliation + new-invoice settling.',
    parse: parseTmsReconcile,
    doneSummary: (parsed) => `${parsed.rows.length.toLocaleString()} AR rows`,
    fileNameMatch: (name) => /apar\s+reconcile/i.test(name),
  },
];

const SLOT_BY_KEY = Object.fromEntries(SLOTS.map(s => [s.key, s]));

// Page-level renderer host. Set when arRenderBuildPage() is called.
// null when no page-level build flow is mounted (e.g., the user has loaded
// a pre-built workbook instead).
let pageHost = null;

// ---------------------------------------------------------------------------
// Public entry — render the build flow into a page host element
// ---------------------------------------------------------------------------

export function arRenderBuildPage(view) {
  pageHost = view;
  arState.buildResult = null;
  arState.buildOpenKpi = null;
  for (const s of SLOTS) arState.buildInputs[s.key] = null;
  arState.buildSaveFolder = arState.buildSaveFolder || localStorage.getItem(SAVE_FOLDER_KEY) || null;
  rerender();
}

function rerender() {
  const preview = document.getElementById('arBuildPreview');
  if (preview) {
    preview.innerHTML = previewModalHtml();
    preview.addEventListener('click', onClick);
    return;
  }
  if (pageHost) {
    pageHost.innerHTML = pageDropHtml();
    wirePageEvents(pageHost);
  }
}

function openPreview() {
  if (!document.getElementById('arBuildPreview')) {
    const node = document.createElement('div');
    node.id = 'arBuildPreview';
    node.className = 'ar-build-modal-overlay';
    document.body.appendChild(node);
  }
  rerender();
}

function closePreview() {
  const node = document.getElementById('arBuildPreview');
  if (node) node.remove();
  arState.buildResult = null;
  arState.buildOpenKpi = null;
  rerender();
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function todayFmt() {
  const d = new Date();
  const opts = { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' };
  return d.toLocaleDateString(undefined, opts);
}

function escHtml(s) {
  return (s == null ? '' : String(s))
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function fmtMoney(n) {
  if (n == null || isNaN(n)) return '$—';
  const v = Math.round(n * 100) / 100;
  return (v < 0 ? '−$' : '$') + Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtMoneyShort(n) {
  if (n == null || isNaN(n)) return '$—';
  const v = Math.round(n);
  return (v < 0 ? '−$' : '$') + Math.abs(v).toLocaleString();
}

// ---------------------------------------------------------------------------
// Page-level drop UI
// ---------------------------------------------------------------------------

function pageDropHtml() {
  const readyCount = SLOTS.filter(s => arState.buildInputs[s.key] && arState.buildInputs[s.key].parsed).length;
  const allReady = readyCount === SLOTS.length;
  const rowsHtml = SLOTS.map((slot, idx) => slotRowHtml(slot, idx + 1)).join('');

  return `
    <div class="ar-build-page" role="region" aria-label="Build today's workbook">
      <div class="ar-build-page-intro">
        Drop the <b>4 daily files</b> from QBO, TAB BANK, and TMS. The engine reconciles them against yesterday's workbook to produce today's AR aging.
      </div>
      <div class="ar-drop-list">${rowsHtml}</div>
      <div class="ar-build-hint">💡 <span><b>Tip:</b> you can drop all 4 files at once — the page sorts them into the right slot by filename.</span></div>
      <div class="ar-build-page-footer">
        <span class="footer-note">
          <span class="progress-pill ${allReady ? 'ready' : ''}">${readyCount} of ${SLOTS.length} ready</span>
          ${allReady ? '· ready to build' : '· drop the rest to enable Build'}
        </span>
        <button class="ngl-btn ngl-btn-secondary" data-action="cancel-page">Clear all</button>
        <button class="ngl-btn ngl-btn-primary ${allReady ? '' : 'disabled'}" data-action="run-build" ${allReady ? '' : 'disabled'}>Run build →</button>
      </div>
      <input type="file" id="arBuildFileInput" accept=".xlsx,.xls" multiple style="display:none" />
    </div>
  `;
}

function slotRowHtml(slot, stepNum) {
  const input = arState.buildInputs[slot.key];
  if (input && input.parsed) {
    return `
      <div class="ar-drop-row done" data-slot="${slot.key}">
        <div class="ar-drop-num">✓</div>
        <div class="ar-drop-body">
          <div class="ar-drop-title">${escHtml(slot.label)} <span class="ar-drop-hint">${escHtml(slot.hint)}</span></div>
          <div class="ar-drop-done">Parsed · <span class="filename">${escHtml(input.file.name)}</span> · ${escHtml(slot.doneSummary(input.parsed))}</div>
        </div>
        <div class="ar-drop-zone-done">
          <span class="change-link" data-action="clear" data-slot="${slot.key}">Use a different file</span>
        </div>
      </div>`;
  }
  if (input && input.error) {
    return `
      <div class="ar-drop-row error" data-slot="${slot.key}">
        <div class="ar-drop-num">!</div>
        <div class="ar-drop-body">
          <div class="ar-drop-title">${escHtml(slot.label)} <span class="ar-drop-hint">${escHtml(slot.hint)}</span></div>
          <div class="ar-drop-error">Couldn't read <span class="filename">${escHtml(input.file.name)}</span> — ${escHtml(input.error)}</div>
        </div>
        <div class="ar-drop-zone" data-action="open-picker" data-slot="${slot.key}">
          <div class="icon">📂</div>
          <div class="label">Try again</div>
          <div class="sub">drop or click</div>
        </div>
      </div>`;
  }
  return `
    <div class="ar-drop-row" data-slot="${slot.key}">
      <div class="ar-drop-num">${stepNum}</div>
      <div class="ar-drop-body">
        <div class="ar-drop-title">${escHtml(slot.label)} <span class="ar-drop-hint">${escHtml(slot.hint)}</span></div>
        <p>${escHtml(slot.blurb)}</p>
      </div>
      <div class="ar-drop-zone" data-action="open-picker" data-slot="${slot.key}">
        <div class="icon">📂</div>
        <div class="label">Drop file</div>
        <div class="sub">or click to browse</div>
      </div>
    </div>`;
}

// ---------------------------------------------------------------------------
// Preview modal
// ---------------------------------------------------------------------------

function previewModalHtml() {
  const r = arState.buildResult;
  const yestAr = arState.buildInputs.yesterday.parsed.ar_register;
  const todayAr = r.today_ar;

  const sumBalance = (rows) => rows.reduce((acc, x) => acc + (Number(x.balance) || 0), 0);
  const yestTotal = sumBalance(yestAr);
  const todayTotal = sumBalance(todayAr);
  const netChange = todayTotal - yestTotal;

  const yestInvSet = new Set(yestAr.map(x => x.inv).filter(Boolean));
  const todayInvSet = new Set(todayAr.map(x => x.inv).filter(Boolean));
  const newInvs = todayAr.filter(x => x.inv && !yestInvSet.has(x.inv));
  const paidOffInvs = yestAr.filter(x => x.inv && !todayInvSet.has(x.inv));

  const newAmount = newInvs.reduce((a, x) => a + (Number(x.amount) || 0), 0);
  const paidAmount = paidOffInvs.reduce((a, x) => a + (Number(x.balance) || 0), 0);

  const adjustments = r.adjustment_rows || [];
  const adjustmentAmount = adjustments.reduce((a, x) => a + Math.abs(Number(x.amount_difference) || 0), 0);

  const exceptions = r.tab_bank_exceptions || [];

  const netClass = netChange > 0 ? 'up' : netChange < 0 ? 'down' : 'flat';
  const netSign = netChange > 0 ? '+' : '';

  const openKpi = arState.buildOpenKpi;

  const sourceStrip = `
    <div class="ar-source-strip">
      <span class="src"><b>Yesterday's workbook</b> ${escHtml(arState.buildInputs.yesterday.file.name)} · ${yestAr.length.toLocaleString()} rows</span>
      <span class="src"><b>QBO Collection</b> ${arState.buildInputs.qbo_collection.parsed.rows.length.toLocaleString()} lines</span>
      <span class="src"><b>QBO Schedule</b> ${arState.buildInputs.qbo_schedule.parsed.rows.length.toLocaleString()} new invoices</span>
      <span class="src"><b>TAB BANK</b> ${arState.buildInputs.tab_bank.parsed.rows.length.toLocaleString()} rows</span>
      <span class="src"><b>TMS Reconcile</b> ${arState.buildInputs.tms_reconcile.parsed.rows.length.toLocaleString()} AR rows</span>
      <span class="age-note">Aged forward <b>+${r.age_delta} day${r.age_delta === 1 ? '' : 's'}</b></span>
    </div>`;

  const kpis = [
    { key: 'new',       label: 'New invoices',         icoClass: 'blue',   val: newInvs.length,      sub: `From QBO Schedule · ${fmtMoneyShort(newAmount)} added` },
    { key: 'paid',      label: 'Paid off',             icoClass: 'green',  val: paidOffInvs.length,  sub: `From QBO Collection · ${fmtMoneyShort(paidAmount)} cleared` },
    { key: 'adjust',    label: 'Amount adjustments',   icoClass: 'purple', val: adjustments.length,  sub: `From TMS · ${fmtMoneyShort(adjustmentAmount)} net change` },
    { key: 'exception', label: 'Suspense / exceptions', icoClass: 'red',   val: exceptions.length,   sub: exceptions.length ? 'Bank / QBO mismatches need review before saving' : 'No TAB BANK mismatches detected', alert: true },
  ];

  const kpisHtml = kpis.map(k => `
    <div class="ar-kpi ${k.alert ? 'alert' : ''} ${openKpi === k.key ? 'active' : ''}" data-action="toggle-kpi" data-kpi="${k.key}">
      <div class="lbl"><span class="ico ${k.icoClass}">●</span> ${escHtml(k.label)}</div>
      <div class="val">${k.val.toLocaleString()}</div>
      <div class="sub">${escHtml(k.sub)}</div>
      <div class="pop-hint">${openKpi === k.key ? '↓ shown below' : 'click to see rows →'}</div>
    </div>`).join('');

  const detailHtml = openKpi
    ? renderKpiDetail(openKpi, { newInvs, paidOffInvs, adjustments, exceptions })
    : '';

  const saveFolder = arState.buildSaveFolder || '(pick a folder to save)';

  return `
    <div class="ar-build-modal preview" role="dialog" aria-modal="true" aria-label="Today's workbook preview">
      <div class="ar-build-header">
        <h4>Today's workbook is ready</h4>
        <span class="date-sub">· ${todayFmt()}</span>
        <span class="build-ms">Built in ${(r.__buildMs || 0).toFixed(0)} ms</span>
        <span class="close-x" data-action="close-preview" style="margin-left:8px">×</span>
      </div>
      <div class="ar-build-body">
        <div class="ar-headline">
          <div class="stat">
            <div class="lbl">Yesterday's AR</div>
            <div class="val">${fmtMoneyShort(yestTotal)}</div>
          </div>
          <div class="arrow">→</div>
          <div class="stat">
            <div class="lbl">Today's AR</div>
            <div class="val">${fmtMoneyShort(todayTotal)}</div>
          </div>
          <div class="delta ${netClass}">
            <span class="d-label">Net change</span>
            <span class="d-val">${netSign}${fmtMoneyShort(netChange)}</span>
          </div>
        </div>
        ${sourceStrip}
        <div class="ar-section-h">What changed today<span class="sub">click a tile to see the rows</span></div>
        <div class="ar-kpi-grid">${kpisHtml}</div>
        ${detailHtml}
      </div>
      <div class="ar-build-footer">
        <span class="footer-note">
          Save to <span class="path-pill">${escHtml(saveFolder)}</span>
          <a class="folder-link" data-action="pick-folder">change folder</a>
        </span>
        <button class="ngl-btn ngl-btn-secondary" data-action="back-to-drop">Back</button>
        <button class="ngl-btn ngl-btn-primary" data-action="save">Save &amp; open dashboard →</button>
      </div>
    </div>`;
}

function tabBankExceptionLabel(kind) {
  switch (kind) {
    case 'posting_gap_qbo_missing': return 'Bank deposit, no QBO post';
    case 'posting_gap_tab_missing': return 'QBO post, no bank record';
    case 'customer_mismatch':       return 'Customer name mismatch';
    case 'info_all_non_factored':   return 'TAB BANK file all NON-FACTORED';
    default: return kind;
  }
}

function renderKpiDetail(kpiKey, data) {
  if (kpiKey === 'new') {
    const rows = data.newInvs.slice(0, 50);
    const total = data.newInvs.reduce((a, x) => a + (Number(x.amount) || 0), 0);
    return `
      <div class="ar-detail-panel">
        <div class="dp-head">
          <div class="dp-title">New invoices today</div>
          <div class="dp-sub">${data.newInvs.length} rows · ${fmtMoney(total)} total · from QBO Daily Schedule</div>
        </div>
        ${rowsTable(['NGL INV #', 'Customer', 'Container / Chassis', 'REF NO', 'MBL', 'Amount', 'WO #'], rows.map(r => [
          r.inv, r.company, r.equipment, r.ref_no, r.mbl_no, fmtMoney(r.amount), r.wo,
        ]), data.newInvs.length, ['mono', '', '', '', '', 'num', 'mono muted'])}
      </div>`;
  }
  if (kpiKey === 'paid') {
    const rows = data.paidOffInvs.slice(0, 50);
    return `
      <div class="ar-detail-panel">
        <div class="dp-head">
          <div class="dp-title">Invoices paid off</div>
          <div class="dp-sub">${data.paidOffInvs.length} rows · from QBO Daily Collection</div>
        </div>
        ${rowsTable(['NGL INV #', 'Customer', 'Original amount', 'Was due', 'Memo'], rows.map(r => [
          r.inv, r.company, fmtMoney(r.amount), fmtMoney(r.balance), r.memo,
        ]), data.paidOffInvs.length, ['mono', '', 'num', 'num', ''])}
      </div>`;
  }
  if (kpiKey === 'adjust') {
    const rows = data.adjustments.slice(0, 50);
    return `
      <div class="ar-detail-panel">
        <div class="dp-head">
          <div class="dp-title">Amount adjustments</div>
          <div class="dp-sub">${data.adjustments.length} rows · from TMS Reconcile</div>
        </div>
        ${rowsTable(['INV #', 'Customer', 'Yest amount', 'New amount', 'Δ'], rows.map(t => {
          const delta = Number(t.amount_difference) || 0;
          return [t.inv_no, t.name, fmtMoney((Number(t.inv_amt) || 0) - delta), fmtMoney(t.inv_amt), `${delta >= 0 ? '+' : ''}${fmtMoney(delta)}`];
        }), data.adjustments.length, ['mono', '', 'num muted', 'num', 'num pos'])}
      </div>`;
  }
  if (kpiKey === 'exception') {
    if (data.exceptions.length === 0) {
      return `
        <div class="ar-detail-panel">
          <div class="dp-head">
            <div class="dp-title">TAB BANK mismatches</div>
            <div class="dp-sub">No exceptions detected in this build</div>
          </div>
          <div style="padding:24px; text-align:center; color:#64748b; font-size:0.78rem">
            Every TAB BANK check# matches a QBO payment with the same customer.
          </div>
        </div>`;
    }
    const rows = data.exceptions.slice(0, 50);
    return `
      <div class="ar-detail-panel">
        <div class="dp-head">
          <div class="dp-title">TAB BANK mismatches</div>
          <div class="dp-sub">${data.exceptions.length} rows · sorted by urgency · review before saving</div>
        </div>
        ${rowsTable(['Issue', 'Check #', 'Amount', 'TAB BANK Customer', 'QBO Customer'], rows.map(e => [
          tabBankExceptionLabel(e.kind),
          e.check_no,
          e.amount != null ? fmtMoney(e.amount) : '—',
          e.tab_bank_customer || '—',
          e.qbo_customer || '—',
        ]), data.exceptions.length, ['', 'mono', 'num', '', ''])}
      </div>`;
  }
  return '';
}

function rowsTable(headers, rows, totalCount, colClasses = []) {
  const thead = headers.map((h, i) => `<th class="${colClasses[i] || ''}">${escHtml(h)}</th>`).join('');
  const tbody = rows.map(r => '<tr>' + r.map((cell, i) => `<td class="${colClasses[i] || ''}">${cell == null ? '' : escHtml(cell)}</td>`).join('') + '</tr>').join('');
  const moreRow = totalCount > rows.length
    ? `<tr><td colspan="${headers.length}" class="more-row">… ${totalCount - rows.length} more rows</td></tr>`
    : '';
  return `<table class="ar-dp-table"><thead><tr>${thead}</tr></thead><tbody>${tbody}${moreRow}</tbody></table>`;
}

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------

function wirePageEvents(root) {
  root.addEventListener('click', onClick);
  const dropZone = root.querySelector('.ar-build-page');
  if (dropZone) {
    dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-active'); });
    dropZone.addEventListener('dragleave', e => { if (e.target === dropZone) dropZone.classList.remove('drag-active'); });
    dropZone.addEventListener('drop', onDrop);
  }
  const fileInput = root.querySelector('#arBuildFileInput');
  if (fileInput) fileInput.addEventListener('change', onFileInputChange);
}

let pickerTargetSlot = null;

function onClick(e) {
  const t = e.target.closest('[data-action]');
  if (!t) return;
  const action = t.dataset.action;

  if (action === 'close-preview') return closePreview();
  if (action === 'cancel-page') {
    for (const s of SLOTS) arState.buildInputs[s.key] = null;
    arState.buildResult = null;
    return rerender();
  }
  if (action === 'clear') {
    const slot = t.dataset.slot;
    arState.buildInputs[slot] = null;
    return rerender();
  }
  if (action === 'open-picker') {
    pickerTargetSlot = t.dataset.slot;
    document.getElementById('arBuildFileInput').click();
    return;
  }
  if (action === 'run-build') return runBuild();
  if (action === 'back-to-drop') {
    return closePreview();
  }
  if (action === 'toggle-kpi') {
    const k = t.dataset.kpi;
    arState.buildOpenKpi = arState.buildOpenKpi === k ? null : k;
    return rerender();
  }
  if (action === 'pick-folder') return pickFolder();
  if (action === 'save') return saveWorkbook();
}

function onFileInputChange(e) {
  const files = Array.from(e.target.files || []);
  e.target.value = '';
  if (files.length === 0) return;
  // If user picked one file via a specific slot's picker, route to that slot.
  if (pickerTargetSlot && files.length === 1) {
    routeFileToSlot(files[0], pickerTargetSlot);
    pickerTargetSlot = null;
  } else {
    autoRouteFiles(files);
    pickerTargetSlot = null;
  }
}

function onDrop(e) {
  e.preventDefault();
  e.currentTarget.classList.remove('drag-active');
  const files = Array.from(e.dataTransfer.files || []);
  if (files.length === 0) return;
  autoRouteFiles(files);
}

function autoRouteFiles(files) {
  // Detect slot by filename pattern. Files that don't match any slot are reported.
  const pending = [];
  const unmatched = [];
  for (const file of files) {
    const slot = SLOTS.find(s => s.fileNameMatch(file.name));
    if (slot) pending.push([file, slot.key]);
    else unmatched.push(file);
  }
  for (const [file, key] of pending) routeFileToSlot(file, key);
  if (unmatched.length) {
    alert(`Couldn't match these files to a slot:\n${unmatched.map(f => '• ' + f.name).join('\n')}\n\nDrag each file onto its row, or click that row's drop zone to browse.`);
  }
}

async function routeFileToSlot(file, slotKey) {
  const slot = SLOT_BY_KEY[slotKey];
  if (!slot) return;
  try {
    const buf = await file.arrayBuffer();
    const parsed = slot.parse(buf);
    arState.buildInputs[slotKey] = { file, buf, parsed, error: null };
  } catch (e) {
    arState.buildInputs[slotKey] = { file, buf: null, parsed: null, error: e.message || String(e) };
  }
  rerender();
}

// ---------------------------------------------------------------------------
// Run build
// ---------------------------------------------------------------------------

function runBuild() {
  const all = SLOTS.every(s => arState.buildInputs[s.key] && arState.buildInputs[s.key].parsed);
  if (!all) return;
  const yesterday = arState.buildInputs.yesterday.parsed;
  const targetDate = inferTargetDate(yesterday);
  const t0 = performance.now();
  let result;
  try {
    result = arBuildToday({
      yesterday,
      qbo_collection: arState.buildInputs.qbo_collection.parsed,
      qbo_schedule:   arState.buildInputs.qbo_schedule.parsed,
      tab_bank:       arState.buildInputs.tab_bank.parsed,
      tms_reconcile:  arState.buildInputs.tms_reconcile.parsed,
      target_date:    targetDate,
    });
  } catch (e) {
    alert(`Build failed: ${e.message}`);
    return;
  }
  result.__buildMs = performance.now() - t0;
  result.__targetDate = targetDate;
  arState.buildResult = result;
  arState.buildOpenKpi = null;
  openPreview();
}

function inferTargetDate(yesterday) {
  // Yesterday's workbook sheet_date is YYYY-MM-DD. Target = next build day.
  // Friday workbook → built Monday (+3 days); otherwise +1.
  if (!yesterday || !yesterday.sheet_date) return new Date();
  const [y, m, d] = yesterday.sheet_date.split('-').map(Number);
  const dt = new Date(y, m - 1, d);
  const dow = dt.getDay();
  dt.setDate(dt.getDate() + (dow === 5 ? 3 : 1));
  return dt;
}

// ---------------------------------------------------------------------------
// Save
// ---------------------------------------------------------------------------

async function pickFolder() {
  if (!window.agentBridge || typeof window.agentBridge.pickFolder !== 'function') {
    alert('Agent connection required to pick a save folder.');
    return;
  }
  const res = await window.agentBridge.pickFolder(arState.buildSaveFolder || undefined);
  if (res && res.path) {
    arState.buildSaveFolder = res.path;
    localStorage.setItem(SAVE_FOLDER_KEY, res.path);
    rerender();
  }
}

async function saveWorkbook() {
  if (!arState.buildResult) return;
  if (!arState.buildSaveFolder) {
    await pickFolder();
    if (!arState.buildSaveFolder) return;
  }
  if (typeof window.arBuildWriteWorkbook !== 'function') {
    alert('Writer module not loaded. Reload the app and try again.');
    return;
  }

  const { bytes, filename } = await window.arBuildWriteWorkbook(
    arState.buildResult,
    arState.buildInputs,
  );

  // Base64 for the agent transport
  const b64 = bytesToBase64(bytes);
  const saveRes = await window.agentBridge.saveBatchOutput({
    files: [{ filename, data: b64, subfolder: '' }],
    baseLocation: arState.buildSaveFolder,
    overwriteFolder: false,
    openFolder: false,
  });
  if (saveRes && saveRes.error) {
    alert(`Save failed: ${saveRes.error}`);
    return;
  }

  // Round-trip: load the workbook we just built using the R1 loader, swap into arState.
  // For M2, the in-memory model from M1's engine is what we display — full R1 parity is
  // verified by re-parsing the written file (done in dev via verify_ar_build_js.mjs).
  if (typeof window.arLoadWorkbookFromBytes === 'function') {
    await window.arLoadWorkbookFromBytes(bytes, filename);
  } else if (typeof window.arLoadWorkbookFromBuffer === 'function') {
    await window.arLoadWorkbookFromBuffer(bytes.buffer, filename);
  } else {
    // Fall back: drop into a synthetic File and reuse the R1 loader.
    const file = new File([bytes], filename, { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
    if (window.arLoadWorkbook) await window.arLoadWorkbook(file);
  }
  closePreview();
}

function bytesToBase64(bytes) {
  // Browser-safe base64 of Uint8Array
  let bin = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(bin);
}

// ---------------------------------------------------------------------------
// Window hooks
// ---------------------------------------------------------------------------

window.arRenderBuildPage = arRenderBuildPage;
