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

// ── Shared helper for empty pane-right state ──

function detailEmptyState(text, icon) {
  return `<div class="ar-detail-empty"><div class="ico">${icon}</div>${escapeHtml(text)}</div>`;
}
window.arDetailEmptyState = detailEmptyState;

// ── Collections tab ──

export function arRenderCollections(body) {
  const checks = groupCollectionsByCheck(arState.model.collections);
  const totalCollected = checks.reduce((s, c) => s + (c.payment_amount || 0), 0);
  const totalInvoices = checks.reduce((s, c) => s + c.invoices.length, 0);

  body.innerHTML = `
    <h3 class="ar-section-h">Collections — yesterday's payments
      <span class="sub">${checks.length} checks · ${totalInvoices} invoices · $${formatMoney(totalCollected)} collected</span>
    </h3>
    <div class="ar-two-pane">
      <div class="pane-left">
        <div class="ar-toolbar">
          <input type="text" class="search" id="arColSearch" placeholder="Search check#, customer, invoice..." />
          <button class="ngl-btn ngl-btn-secondary" id="arColExpandAll" style="font-size:0.72rem; padding:4px 9px;">Expand all</button>
          <span class="meta" id="arColMeta"></span>
        </div>
        <div class="ar-table-wrap">
          <table class="ar-table">
            <thead><tr>
              <th>Check#</th><th>Customer</th>
              <th class="num">Invoices</th><th class="num">Amount</th><th>Status</th>
            </tr></thead>
            <tbody id="arColBody"></tbody>
          </table>
        </div>
      </div>
      <div class="pane-right" id="arColDetail">
        ${detailEmptyState('Select a check to see its applied invoices', '📋')}
      </div>
    </div>
  `;

  const expandedChecks = new Set();
  const searchState = { q: '' };
  body.querySelector('#arColSearch').addEventListener('input', e => {
    searchState.q = e.target.value.toLowerCase();
    paint();
  });
  body.querySelector('#arColExpandAll').addEventListener('click', () => {
    if (expandedChecks.size === checks.length) {
      expandedChecks.clear();
    } else {
      checks.forEach(c => expandedChecks.add(c.check_no));
    }
    paint();
  });

  function paint() {
    const filtered = filterChecks(checks, searchState.q);
    const tbody = body.querySelector('#arColBody');
    tbody.innerHTML = filtered.flatMap(c => renderCheckGroup(c, expandedChecks.has(c.check_no))).join('');
    body.querySelector('#arColMeta').textContent = `${filtered.length} of ${checks.length} checks`;

    tbody.querySelectorAll('tr.group-row').forEach(tr => {
      tr.addEventListener('click', () => {
        const ck = tr.dataset.check;
        const check = checks.find(c => c.check_no === ck);
        if (expandedChecks.has(ck)) {
          expandedChecks.delete(ck);
        } else {
          expandedChecks.add(ck);
        }
        renderDetail(check);
        paint();
      });
    });
  }

  function renderDetail(check) {
    const detailEl = body.querySelector('#arColDetail');
    detailEl.innerHTML = `
      <div class="ar-pane-section">
        <h4>Check ${escapeHtml(check.check_no)}</h4>
        <div class="row"><span class="key">Customer:</span> ${escapeHtml(check.customer_name)}</div>
        <div class="row"><span class="key">Customer ID:</span> ${escapeHtml(check.customer_id)}</div>
        <div class="row"><span class="key">Total:</span> $${formatMoney(check.payment_amount)}</div>
        <div class="row"><span class="key">Account:</span> ${escapeHtml(check.account)}</div>
        <div class="row"><span class="key">Posted:</span> ${formatDate(check.payment_date)}</div>
      </div>
      <div class="ar-pane-section">
        <h4>Applied invoices (${check.invoices.length})</h4>
        ${check.invoices.map(inv => `
          <div class="row">
            <code class="id" style="font-family:Consolas,monospace; font-size:0.76rem;">${escapeHtml(inv.invoice_or_ref)}</code>
            <span class="num" style="float:right;">$${formatMoney(inv.amount)}</span>
          </div>
        `).join('')}
      </div>
    `;
  }

  paint();
}

function groupCollectionsByCheck(rows) {
  const byCheck = new Map();
  for (const r of rows) {
    if (!r.check_no) continue;
    if (!byCheck.has(r.check_no)) {
      byCheck.set(r.check_no, {
        check_no: r.check_no,
        customer_id: r.customer_id,
        customer_name: r.customer_name,
        account: r.account,
        payment_date: r.payment_date,
        payment_amount: 0,
        invoices: [],
      });
    }
    const group = byCheck.get(r.check_no);
    if (r.txn_type === 'Payment') {
      group.payment_amount = r.amount ?? 0;
    } else if (r.txn_type === 'Invoice') {
      group.invoices.push(r);
    }
  }
  return [...byCheck.values()];
}

function filterChecks(checks, q) {
  if (!q) return checks;
  return checks.filter(c =>
    (c.check_no && String(c.check_no).toLowerCase().includes(q)) ||
    (c.customer_name && c.customer_name.toLowerCase().includes(q)) ||
    c.invoices.some(i => i.invoice_or_ref && String(i.invoice_or_ref).toLowerCase().includes(q))
  );
}

function renderCheckGroup(c, expanded) {
  const reconciled = checkReconciledStatus(c);
  const chevron = expanded ? '▼' : '▶';
  const groupClass = expanded ? 'group-row expanded' : 'group-row';
  const lines = [
    `<tr class="${groupClass}" data-check="${escapeHtml(c.check_no)}">
       <td><span class="chevron">${chevron}</span>${escapeHtml(c.check_no)}</td>
       <td class="customer">${escapeHtml(c.customer_name)}</td>
       <td class="num">${c.invoices.length}</td>
       <td class="num">${formatMoney(c.payment_amount)}</td>
       <td>${renderReconciledChip(reconciled)}</td>
     </tr>`
  ];
  if (expanded) {
    for (const inv of c.invoices) {
      lines.push(`<tr>
        <td></td>
        <td class="id">${escapeHtml(inv.invoice_or_ref)}</td>
        <td></td>
        <td class="num">${formatMoney(inv.amount)}</td>
        <td>${inv.open_balance != null && Math.abs(inv.open_balance) >= 0.01
              ? `<span class="ar-status-chip warn">Bal $${formatMoney(inv.open_balance)}</span>`
              : `<span class="ar-status-chip ok">Cleared</span>`}</td>
      </tr>`);
    }
  }
  return lines;
}

function checkReconciledStatus(check) {
  const applied = check.invoices.reduce((s, i) => s + (i.amount || 0), 0);
  const total = check.payment_amount || 0;
  if (Math.abs(applied - total) < 0.01) return 'reconciled';
  if (applied < total) return 'partial';
  return 'unposted';
}

function renderReconciledChip(status) {
  if (status === 'reconciled') return '<span class="ar-status-chip ok">✓ Reconciled</span>';
  if (status === 'partial')    return '<span class="ar-status-chip warn">⚠ Partial</span>';
  return '<span class="ar-status-chip danger">✗ Unposted</span>';
}

window.arRenderCollections = arRenderCollections;

// ── Overdue tab ──

export function arRenderOverdue(body) {
  const rows = arState.model.ar_register
    .filter(r => (r.aging ?? 0) >= 30 && (r.balance ?? 0) > 0)
    .sort((a, b) => (b.aging ?? 0) - (a.aging ?? 0));
  const total = rows.reduce((s, r) => s + (r.balance ?? 0), 0);

  body.innerHTML = `
    <h3 class="ar-section-h">Overdue — today's call list
      <span class="sub">${rows.length} invoices · $${formatMoney(total)} outstanding</span>
    </h3>
    <div class="ar-two-pane">
      <div class="pane-left">
        <div class="ar-toolbar">
          <input type="text" class="search" id="arOdSearch" placeholder="Search customer, invoice, ref..." />
          <span class="meta">${rows.length} overdue</span>
        </div>
        <div class="ar-table-wrap">
          <table class="ar-table">
            <thead><tr>
              <th>Customer</th><th>Invoice</th><th class="num">Aging</th>
              <th class="num">Balance</th><th>Status</th>
            </tr></thead>
            <tbody id="arOdBody"></tbody>
          </table>
        </div>
      </div>
      <div class="pane-right" id="arOdDetail">
        ${detailEmptyState('Select a row to see customer + invoice details + actions', '📞')}
      </div>
    </div>
  `;

  const search = { q: '' };
  body.querySelector('#arOdSearch').addEventListener('input', e => {
    search.q = e.target.value.toLowerCase();
    paint();
  });

  function paint() {
    const filtered = search.q
      ? rows.filter(r =>
          (r.company && String(r.company).toLowerCase().includes(search.q)) ||
          (r.inv && String(r.inv).toLowerCase().includes(search.q)) ||
          (r.ref_no && String(r.ref_no).toLowerCase().includes(search.q)))
      : rows;
    const tbody = body.querySelector('#arOdBody');
    tbody.innerHTML = filtered.slice(0, 500).map(r => `
      <tr data-inv="${escapeHtml(r.inv)}">
        <td class="customer">${escapeHtml(r.company)}</td>
        <td class="id">${escapeHtml(r.inv)}</td>
        <td class="num">${r.aging ?? ''} d</td>
        <td class="num">${formatMoney(r.balance)}</td>
        <td>${formatStatus(r.ar_status)}</td>
      </tr>
    `).join('');
    tbody.querySelectorAll('tr').forEach(tr => {
      tr.addEventListener('click', () => {
        const r = rows.find(x => x.inv === tr.dataset.inv);
        renderDetail(r);
      });
    });
  }

  function renderDetail(r) {
    const detailEl = body.querySelector('#arOdDetail');
    detailEl.innerHTML = `
      <div class="ar-pane-section">
        <h4>${escapeHtml(r.company)}</h4>
        <div class="row"><span class="key">Customer ID:</span> ${escapeHtml(r.new_id)}</div>
        <div class="row"><span class="key">Invoice:</span> <code>${escapeHtml(r.inv)}</code></div>
        <div class="row"><span class="key">WO:</span> ${escapeHtml(r.wo)}</div>
        <div class="row"><span class="key">Issued:</span> ${formatDate(r.date)}</div>
        <div class="row"><span class="key">Aging:</span> ${r.aging} days</div>
        <div class="row"><span class="key">Amount:</span> $${formatMoney(r.amount)}</div>
        <div class="row"><span class="key">Paid:</span> $${formatMoney(r.paid)}</div>
        <div class="row"><span class="key">Balance:</span> <strong>$${formatMoney(r.balance)}</strong></div>
        ${r.memo ? `<div class="row"><span class="key">Memo:</span> ${escapeHtml(r.memo)}</div>` : ''}
      </div>
      <div class="ar-pane-section">
        <h4>Actions</h4>
        <button class="ngl-btn ngl-btn-primary" style="margin-bottom:6px; width:100%;" onclick="window.arEmailCustomer && arEmailCustomer('${escapeHtml(r.new_id)}', '${escapeHtml(r.inv)}')">✉ Email customer</button>
        <button class="ngl-btn ngl-btn-secondary" style="margin-bottom:6px; width:100%;" onclick="window.arOpenInQbo && arOpenInQbo('${escapeHtml(r.inv)}')">🔗 Open in QBO</button>
      </div>
    `;
  }

  paint();
}
window.arRenderOverdue = arRenderOverdue;

// ── Partial Pays tab ──

export function arRenderPartial(body) {
  const rows = arState.model.ar_register
    .filter(r => (r.paid ?? 0) > 0 && (r.balance ?? 0) > 0)
    .sort((a, b) => (b.balance ?? 0) - (a.balance ?? 0));
  const total = rows.reduce((s, r) => s + (r.balance ?? 0), 0);

  body.innerHTML = `
    <h3 class="ar-section-h">Partial Pays — invoices with partial payment received
      <span class="sub">${rows.length} invoices · $${formatMoney(total)} still open</span>
    </h3>
    <div class="ar-table-wrap">
      <table class="ar-table">
        <thead><tr>
          <th>Customer</th><th>Invoice</th><th class="num">Amount</th>
          <th class="num">Paid</th><th class="num">Balance</th><th>Memo</th>
        </tr></thead>
        <tbody>
          ${rows.map(r => `
            <tr>
              <td class="customer">${escapeHtml(r.company)}</td>
              <td class="id">${escapeHtml(r.inv)}</td>
              <td class="num">${formatMoney(r.amount)}</td>
              <td class="num">${formatMoney(r.paid)}</td>
              <td class="num"><strong>${formatMoney(r.balance)}</strong></td>
              <td>${escapeHtml(r.memo)}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `;
}
window.arRenderPartial = arRenderPartial;

// ── New Invoices tab ──

export function arRenderNew(body) {
  const rows = arState.model.schedule;
  const total = rows.reduce((s, r) => s + (r.amount ?? 0), 0);

  body.innerHTML = `
    <h3 class="ar-section-h">New Invoices — added to AR
      <span class="sub">${rows.length} new · $${formatMoney(total)} invoiced</span>
    </h3>
    <div class="ar-table-wrap">
      <table class="ar-table">
        <thead><tr>
          <th>Date</th><th>Customer</th><th>Invoice</th>
          <th>Ref / WO</th><th>Container</th><th>B/L</th><th class="num">Amount</th>
        </tr></thead>
        <tbody>
          ${rows.map(r => `
            <tr>
              <td>${formatDate(r.date)}</td>
              <td class="customer">${escapeHtml(r.customer_name)}</td>
              <td class="id">${escapeHtml(r.inv)}</td>
              <td class="id">${escapeHtml(r.ref)}</td>
              <td class="id">${escapeHtml(r.cntr_chassis)}</td>
              <td class="id">${escapeHtml(r.bl)}</td>
              <td class="num">${formatMoney(r.amount)}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `;
}
window.arRenderNew = arRenderNew;

// ── TMS tab ──

export function arRenderTms(body) {
  const rows = arState.model.tms_rows;
  body.innerHTML = `
    <h3 class="ar-section-h">TMS — matched invoices
      <span class="sub">${rows.length} rows · TMS source = billing-side truth</span>
    </h3>
    <div class="ar-table-wrap">
      <table class="ar-table">
        <thead><tr>
          <th>Div</th><th>Customer</th><th>WO #</th><th>Equipment</th>
          <th>NGL Inv #</th><th class="num">Inv Amount</th><th class="num">Paid/Received</th>
        </tr></thead>
        <tbody>
          ${rows.map(r => `
            <tr>
              <td>${escapeHtml(r.wo_div)}</td>
              <td class="customer">${escapeHtml(r.name)}</td>
              <td class="id">${escapeHtml(r.wo_no)}</td>
              <td class="id">${escapeHtml(r.equipment)}</td>
              <td class="id">${escapeHtml(r.inv_no)}</td>
              <td class="num">${formatMoney(r.inv_amt)}</td>
              <td class="num">${formatMoney(r.paid_received)}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `;
}
window.arRenderTms = arRenderTms;

// ── Adjustments tab ──

export function arRenderAdjustments(body) {
  const rows = arState.model.adjustments;
  const total = rows.reduce((s, r) => s + (r.amount_difference ?? 0), 0);
  body.innerHTML = `
    <h3 class="ar-section-h">Adjustments — TMS revised these invoice amounts
      <span class="sub">${rows.length} adjustments · net change ${total < 0 ? '−' : ''}$${formatMoney(Math.abs(total))}</span>
    </h3>
    <div class="ar-table-wrap">
      <table class="ar-table">
        <thead><tr>
          <th>Div</th><th>Customer</th><th>WO #</th><th>NGL Inv #</th>
          <th class="num">Amount Δ</th><th class="num">Revised Amount</th><th class="num">Paid</th>
        </tr></thead>
        <tbody>
          ${rows.map(r => `
            <tr>
              <td>${escapeHtml(r.div)}</td>
              <td class="customer">${escapeHtml(r.name)}</td>
              <td class="id">${escapeHtml(r.wo_no)}</td>
              <td class="id">${escapeHtml(r.inv_no)}</td>
              <td class="num ${(r.amount_difference ?? 0) < 0 ? 'neg' : ''}">${formatMoney(r.amount_difference)}</td>
              <td class="num">${formatMoney(r.revised_invoice_amount)}</td>
              <td class="num">${formatMoney(r.paid_received)}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `;
}
window.arRenderAdjustments = arRenderAdjustments;

// ── Customers tab ──

export function arRenderCustomers(body) {
  const rollup = buildCustomerRollup(arState.model.ar_register);

  body.innerHTML = `
    <h3 class="ar-section-h">Customers — rollup
      <span class="sub">${rollup.length} customers · $${formatMoney(rollup.reduce((s,r) => s + r.balance, 0))} total balance</span>
    </h3>
    <div class="ar-toolbar">
      <input type="text" class="search" id="arCustSearch" placeholder="Search customer..." />
      <span class="meta">Sorted by balance desc</span>
    </div>
    <div class="ar-table-wrap">
      <table class="ar-table">
        <thead><tr>
          <th>ID</th><th>Customer</th><th class="num">Invoices</th>
          <th class="num">Balance</th><th class="num">Oldest Aging</th>
          <th>Bucket spread</th>
        </tr></thead>
        <tbody id="arCustBody"></tbody>
      </table>
    </div>
  `;

  const search = { q: '' };
  body.querySelector('#arCustSearch').addEventListener('input', e => {
    search.q = e.target.value.toLowerCase();
    paint();
  });

  function paint() {
    const filtered = search.q
      ? rollup.filter(c =>
          (c.name && String(c.name).toLowerCase().includes(search.q)) ||
          (c.id && String(c.id).toLowerCase().includes(search.q)))
      : rollup;
    const tbody = body.querySelector('#arCustBody');
    tbody.innerHTML = filtered.map(c => `
      <tr>
        <td class="id">${escapeHtml(c.id)}</td>
        <td class="customer">${escapeHtml(c.name)}</td>
        <td class="num">${c.invoice_count}</td>
        <td class="num"><strong>${formatMoney(c.balance)}</strong></td>
        <td class="num">${c.oldest_aging} d</td>
        <td>${renderBucketChips(c.buckets)}</td>
      </tr>
    `).join('');
  }

  paint();
}
window.arRenderCustomers = arRenderCustomers;

function buildCustomerRollup(register) {
  const byCust = new Map();
  for (const r of register) {
    const id = r.new_id || '(no id)';
    if (!byCust.has(id)) {
      byCust.set(id, {
        id: r.new_id,
        name: r.company,
        invoice_count: 0,
        balance: 0,
        oldest_aging: 0,
        buckets: { A: 0, B: 0, C: 0, D: 0, E: 0 },
      });
    }
    const c = byCust.get(id);
    c.invoice_count++;
    c.balance += r.balance ?? 0;
    c.oldest_aging = Math.max(c.oldest_aging, r.aging ?? 0);
    const letter = r.ar_status ? String(r.ar_status)[0] : null;
    if (letter && c.buckets[letter] != null) c.buckets[letter]++;
  }
  return [...byCust.values()].sort((a, b) => b.balance - a.balance);
}

function renderBucketChips(b) {
  const colors = { A: '#16a34a', B: '#facc15', C: '#f97316', D: '#dc2626', E: '#7c2d12' };
  return Object.entries(b)
    .filter(([, n]) => n > 0)
    .map(([letter, n]) => `<span style="display:inline-block; background:${colors[letter]}; color:#fff; padding:1px 6px; border-radius:3px; font-size:0.66rem; font-weight:700; margin-right:3px;">${letter} ${n}</span>`)
    .join('');
}
