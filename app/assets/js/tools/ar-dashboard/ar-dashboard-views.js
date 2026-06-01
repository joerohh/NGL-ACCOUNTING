// AR Dashboard — view renderers
// Each function takes the model + active tab and returns HTML (or applies it).

import { arState } from '../../shared/state.js';

const TABS = [
  { id: 'summary',     label: 'Summary',      countOf: () => null },
  { id: 'ar-register', label: 'AR Register',  countOf: m => m.ar_register.length },
  { id: 'collections', label: 'Collections',  countOf: m => uniqueChecks(m.collections).length },
  { id: 'overdue',     label: 'Overdue',      countOf: m => m.ar_register.filter(r => (r.aging ?? 0) >= 30 && (r.balance ?? 0) > 0).length },
  { id: 'partial',     label: 'Partial Pays', countOf: m => m.ar_register.filter(r => (r.paid ?? 0) > 0 && (r.balance ?? 0) > 0).length },
  { id: 'new',         label: 'New Invoices', countOf: m => m.schedule.length },
  { id: 'tms',         label: 'TMS',          countOf: m => m.tms_rows.length },
  { id: 'adjustments', label: 'Adjustments',  countOf: m => m.adjustments.length },
  { id: 'suspense',    label: 'Suspense',     countOf: () => (arState.exceptions || []).filter(e => e.category === 'suspense').length, urgent: true },
  { id: 'customers',   label: 'Customers',    countOf: m => uniqueCustomers(m.ar_register).length },
];

function uniqueChecks(collections) {
  const s = new Set();
  for (const c of collections) if (c.check_no) s.add(c.check_no);
  return [...s];
}

function uniqueCustomers(register) {
  const s = new Set();
  for (const r of register) if (r.new_id) s.add(r.new_id);
  return [...s];
}

export function arRenderLoaded(view) {
  const m = arState.model;
  view.innerHTML = `
    <div class="ar-shell">
      <div class="ar-data-bar">
        <span class="pill">Loaded</span>
        <span>Workbook:</span><span class="filename">${escapeHtml(arState.filename)}</span>
        <span class="sep">|</span>
        <span>Today: ${m.today_date}</span>
        <span class="sep">|</span>
        <span>Yesterday: ${m.yesterday_date ?? '(none)'}</span>
        <a class="right-link" id="arUnloadBtn">Load different workbook →</a>
      </div>
      <div class="ar-tabs" id="arTabs">
        ${TABS.map(t => renderTabButton(t, m)).join('')}
      </div>
      <div class="ar-tab-body" id="arTabBody"></div>
    </div>
  `;

  view.querySelector('#arUnloadBtn').addEventListener('click', () => {
    arState.loaded = false;
    arState.filename = null;
    arState.model = null;
    arState.exceptions = [];
    if (window.initArDashboard) window.initArDashboard();
  });

  view.querySelectorAll('.ar-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      arState.activeTab = btn.dataset.tab;
      view.querySelectorAll('.ar-tab').forEach(b => b.classList.toggle('active', b === btn));
      renderActiveTab();
    });
  });

  renderActiveTab();
}

function renderTabButton(tab, model) {
  const active = arState.activeTab === tab.id ? 'active' : '';
  const count = tab.countOf(model);
  const urgentClass = tab.urgent && count > 0 ? 'urgent' : '';
  const countHtml = (count != null) ? `<span class="count ${urgentClass}">${count}</span>` : '';
  return `<button class="ar-tab ${active}" data-tab="${tab.id}">${tab.label}${countHtml}</button>`;
}

function renderActiveTab() {
  const body = document.getElementById('arTabBody');
  if (!body) return;
  const tab = arState.activeTab;
  const placeholder = `<div style="color:#64748b; font-size:0.85rem; padding:20px;">Tab "${tab}" — coming in a later phase.</div>`;
  switch (tab) {
    case 'summary':     return window.arRenderSummary     ? window.arRenderSummary(body)     : (body.innerHTML = placeholder);
    case 'ar-register': return window.arRenderRegister    ? window.arRenderRegister(body)    : (body.innerHTML = placeholder);
    case 'collections': return window.arRenderCollections ? window.arRenderCollections(body) : (body.innerHTML = placeholder);
    case 'overdue':     return window.arRenderOverdue     ? window.arRenderOverdue(body)     : (body.innerHTML = placeholder);
    case 'partial':     return window.arRenderPartial     ? window.arRenderPartial(body)     : (body.innerHTML = placeholder);
    case 'new':         return window.arRenderNew         ? window.arRenderNew(body)         : (body.innerHTML = placeholder);
    case 'tms':         return window.arRenderTms         ? window.arRenderTms(body)         : (body.innerHTML = placeholder);
    case 'adjustments': return window.arRenderAdjustments ? window.arRenderAdjustments(body) : (body.innerHTML = placeholder);
    case 'suspense':    return window.arRenderSuspense    ? window.arRenderSuspense(body)    : (body.innerHTML = placeholder);
    case 'customers':   return window.arRenderCustomers   ? window.arRenderCustomers(body)   : (body.innerHTML = placeholder);
    default: body.innerHTML = placeholder;
  }
}

function escapeHtml(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"]/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;' }[c]));
}

window.arRenderLoaded = arRenderLoaded;
window.arEscapeHtml = escapeHtml;

// ── AR Register tab ──

const REGISTER_COLUMNS = [
  { key: 'new_id',    label: 'ID',       cell: r => `<td class="id">${escapeHtml(r.new_id)}</td>` },
  { key: 'company',   label: 'Customer', cell: r => `<td class="customer">${escapeHtml(r.company)}</td>` },
  { key: 'inv',       label: 'NGL Inv#', cell: r => `<td class="id">${escapeHtml(r.inv)}</td>` },
  { key: 'date',      label: 'Date',     cell: r => `<td>${formatDate(r.date)}</td>` },
  { key: 'aging',     label: 'Aging',    cell: r => `<td class="num">${r.aging ?? ''}</td>` },
  { key: 'amount',    label: 'Amount',   cell: r => `<td class="num">${formatMoney(r.amount)}</td>` },
  { key: 'paid',      label: 'Paid',     cell: r => `<td class="num">${formatMoney(r.paid)}</td>` },
  { key: 'balance',   label: 'Balance',  cell: r => `<td class="num ${(r.balance ?? 0) < 0 ? 'neg' : ''}">${formatMoney(r.balance)}</td>` },
  { key: 'ar_status', label: 'Status',   cell: r => `<td>${formatStatus(r.ar_status)}</td>` },
  { key: 'memo',      label: 'Memo',     cell: r => `<td>${escapeHtml(r.memo)}</td>` },
];

let registerSort = { key: 'aging', dir: 'desc' };
let registerSearch = '';

export function arRenderRegister(body) {
  const rows = arState.model.ar_register;
  body.innerHTML = `
    <h3 class="ar-section-h">AR Register
      <span class="sub">${rows.length} invoices · ${uniqueCustomers(rows).length} customers</span>
    </h3>
    <div class="ar-toolbar">
      <input type="text" class="search" id="arRegSearch" placeholder="Search invoice #, customer, ref, MBL, container..." value="${escapeHtml(registerSearch)}" />
      <span class="meta" id="arRegMeta"></span>
    </div>
    <div class="ar-table-wrap">
      <table class="ar-table">
        <thead><tr id="arRegHead"></tr></thead>
        <tbody id="arRegBody"></tbody>
      </table>
    </div>
  `;
  body.querySelector('#arRegSearch').addEventListener('input', e => {
    registerSearch = e.target.value.toLowerCase();
    paintRegister();
  });
  paintRegister();
}

function paintRegister() {
  const headRow = document.getElementById('arRegHead');
  const bodyEl = document.getElementById('arRegBody');
  const meta = document.getElementById('arRegMeta');
  if (!headRow || !bodyEl) return;

  headRow.innerHTML = REGISTER_COLUMNS.map(c => {
    const cls = registerSort.key === c.key ? (registerSort.dir === 'asc' ? 'sorted-asc' : 'sorted-desc') : '';
    const arrow = cls === 'sorted-asc' ? '↑' : (cls === 'sorted-desc' ? '↓' : '↕');
    return `<th data-key="${c.key}" class="${cls}">${c.label} <span class="sort-arrow">${arrow}</span></th>`;
  }).join('');
  headRow.querySelectorAll('th').forEach(th => {
    th.addEventListener('click', () => {
      const k = th.dataset.key;
      if (registerSort.key === k) {
        registerSort.dir = registerSort.dir === 'asc' ? 'desc' : 'asc';
      } else {
        registerSort = { key: k, dir: 'desc' };
      }
      paintRegister();
    });
  });

  let rows = arState.model.ar_register;
  if (registerSearch) {
    const q = registerSearch;
    rows = rows.filter(r =>
      (r.inv && String(r.inv).toLowerCase().includes(q)) ||
      (r.company && String(r.company).toLowerCase().includes(q)) ||
      (r.new_id && String(r.new_id).toLowerCase().includes(q)) ||
      (r.ref_no && String(r.ref_no).toLowerCase().includes(q)) ||
      (r.mbl_no && String(r.mbl_no).toLowerCase().includes(q)) ||
      (r.equipment && String(r.equipment).toLowerCase().includes(q))
    );
  }
  rows = [...rows].sort((a, b) => {
    const av = a[registerSort.key], bv = b[registerSort.key];
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    const cmp = (typeof av === 'number' && typeof bv === 'number') ? (av - bv) : String(av).localeCompare(String(bv));
    return registerSort.dir === 'asc' ? cmp : -cmp;
  });

  // Cap render to first 500 for perf; show note if truncated.
  const truncated = rows.length > 500;
  const renderRows = truncated ? rows.slice(0, 500) : rows;
  bodyEl.innerHTML = renderRows.map(r => `<tr>${REGISTER_COLUMNS.map(c => c.cell(r)).join('')}</tr>`).join('');
  meta.textContent = truncated
    ? `Showing first 500 of ${rows.length} matched rows`
    : `${rows.length} rows`;
}

function formatMoney(v) {
  if (v == null || v === '') return '';
  return Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatDate(v) {
  if (!v) return '';
  if (v instanceof Date) {
    return `${(v.getMonth()+1).toString().padStart(2,'0')}/${v.getDate().toString().padStart(2,'0')}/${v.getFullYear()}`;
  }
  return escapeHtml(v);
}

function formatStatus(s) {
  if (!s) return '';
  const letter = String(s)[0] || '';
  return `<span class="status-${letter}">${escapeHtml(s)}</span>`;
}

window.arRenderRegister = arRenderRegister;
window.arFormatMoney = formatMoney;
window.arFormatDate = formatDate;
window.arFormatStatus = formatStatus;
