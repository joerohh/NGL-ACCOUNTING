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
