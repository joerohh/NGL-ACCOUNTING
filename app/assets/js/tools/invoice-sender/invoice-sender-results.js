// Invoice Sender — Results view (Fix 1) for v2.62.
// Activated after a send job completes. Replaces the live progress table
// with a tabbed results table.

import { sendState, invoiceState } from '../../shared/state.js';
import { escHtml } from '../../shared/utils.js';

const SLOT_LABELS = { pod: 'POD', bol: 'BOL', pol: 'POL', do: 'DO', pl: 'PL' };

// Treat both new 'missing_docs' and legacy 'skipped_no_attachments' as failed-missing-docs.
function isMissingDocs(row) {
  return row.sendStatus === 'missing_docs' || row.sendStatus === 'skipped_no_attachments';
}

function isErrored(row) {
  return row.sendStatus === 'error';
}

function isFailed(row) {
  return isMissingDocs(row) || isErrored(row);
}

function badgeFor(row) {
  if (row.sendStatus === 'sent') return { cls: 'sent', text: '✓ Sent' };
  if (row.sendStatus === 'in_progress') return { cls: 'in-progress', text: '⟳ Sending…' };
  if (row.sendStatus === 'skipped') return { cls: 'skipped', text: 'Skipped' };
  if (isMissingDocs(row)) {
    const missing = (row.missingDocs || []).map(d => SLOT_LABELS[String(d).toLowerCase()] || String(d).toUpperCase());
    if (missing.length === 0) return { cls: 'missing-doc', text: '⚠ Missing Docs' };
    if (missing.length === 1) return { cls: 'missing-doc', text: `⚠ ${missing[0]} Missing` };
    return { cls: 'missing-doc', text: `⚠ ${missing.join(' + ')} Missing` };
  }
  if (isErrored(row)) {
    const msg = (row.errorMessage || '').toLowerCase();
    if (msg.includes('timeout') || msg.includes('did not respond') || msg.includes('didn')) {
      return { cls: 'api-error', text: '⚡ QBO Error' };
    }
    return { cls: 'missing-doc', text: '⚠ Error' };
  }
  return { cls: 'skipped', text: row.sendStatus || 'Pending' };
}

function getFailedRows() {
  return invoiceState.invoices.filter(isFailed);
}

function getSentRows() {
  return invoiceState.invoices.filter(r => r.sendStatus === 'sent');
}

function getAllRows() {
  // Failed first, then sent, then everything else
  const failed = getFailedRows();
  const sent = getSentRows();
  const other = invoiceState.invoices.filter(r => !isFailed(r) && r.sendStatus !== 'sent');
  return [...failed, ...sent, ...other];
}

function bindTabClicks() {
  document.querySelectorAll('#invSendResults .v62-tab').forEach(btn => {
    btn.onclick = () => {
      sendState.currentTab = btn.dataset.tab;
      renderResults();
    };
  });
}

export function showResultsView() {
  const el = document.getElementById('invSendResults');
  if (!el) return;
  // Pick default tab based on whether failures exist
  const failed = getFailedRows();
  sendState.currentTab = failed.length > 0 ? 'needs-attention' : 'all';
  el.style.display = 'grid';
  bindTabClicks();
  renderResults();
}

export function hideResultsView() {
  const el = document.getElementById('invSendResults');
  if (!el) return;
  el.style.display = 'none';
  el.classList.remove('panel-open');
}

export function renderResults() {
  const failed = getFailedRows();
  const sent = getSentRows();
  const all = invoiceState.invoices.length;

  // Tab counts
  const issueCountEl = document.getElementById('invTabIssueCount');
  const sentCountEl = document.getElementById('invTabSentCount');
  const allCountEl = document.getElementById('invTabAllCount');
  if (issueCountEl) issueCountEl.textContent = failed.length;
  if (sentCountEl) sentCountEl.textContent = sent.length;
  if (allCountEl) allCountEl.textContent = all;

  // Alert banner
  const banner = document.getElementById('invSendAlertBanner');
  if (banner) {
    if (failed.length > 0) {
      banner.style.display = 'flex';
      const titleEl = document.getElementById('invSendBannerTitle');
      const subEl = document.getElementById('invSendBannerSubtitle');
      if (titleEl) {
        titleEl.textContent = failed.length === 1
          ? '1 invoice needs your attention.'
          : `${failed.length} invoices need your attention.`;
      }
      if (subEl) {
        subEl.textContent = sent.length > 0
          ? `The other ${sent.length} sent successfully — click each failed row to fix it without leaving this page.`
          : `Click each failed row to fix it without leaving this page.`;
      }
    } else {
      banner.style.display = 'none';
    }
  }

  // Active tab class
  document.querySelectorAll('#invSendResults .v62-tab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === sendState.currentTab);
  });

  renderTable();
}

function renderTable() {
  let rows;
  if (sendState.currentTab === 'needs-attention') rows = getFailedRows();
  else if (sendState.currentTab === 'sent') rows = getSentRows();
  else rows = getAllRows();

  const wrap = document.getElementById('invResultsTableWrap');
  if (!wrap) return;

  if (rows.length === 0) {
    const msg = sendState.currentTab === 'needs-attention'
      ? '<strong>All caught up.</strong><div style="font-size:0.85rem; color:#94a3b8; margin-top:6px;">No failures to fix.</div>'
      : '<strong>No invoices in this tab.</strong>';
    wrap.innerHTML = `<div style="text-align:center; padding:48px; color:#475569;">
      <div style="font-size:2rem; margin-bottom:8px;">✓</div>${msg}
    </div>`;
    return;
  }

  wrap.innerHTML = `
    <table class="inv-table" style="width:100%; border-collapse:collapse;">
      <thead>
        <tr>
          <th style="text-align:left; padding:8px; border-bottom:1px solid #e2e8f0; font-size:0.78rem; color:#64748b;">Invoice</th>
          <th style="text-align:left; padding:8px; border-bottom:1px solid #e2e8f0; font-size:0.78rem; color:#64748b;">Container</th>
          <th style="text-align:left; padding:8px; border-bottom:1px solid #e2e8f0; font-size:0.78rem; color:#64748b;">Customer</th>
          <th style="text-align:left; padding:8px; border-bottom:1px solid #e2e8f0; font-size:0.78rem; color:#64748b;">Status</th>
          <th style="text-align:right; padding:8px; border-bottom:1px solid #e2e8f0; font-size:0.78rem; color:#64748b;">Action</th>
        </tr>
      </thead>
      <tbody>
        ${rows.map(renderRow).join('')}
      </tbody>
    </table>
  `;

  // Wire row clicks
  wrap.querySelectorAll('tr[data-invoice]').forEach(tr => {
    tr.onclick = (e) => {
      if (e.target.closest('.v62-action-btn')) return;
      const fn = window.invOpenPanelForInvoice;
      if (typeof fn === 'function') fn(tr.dataset.invoice);
    };
  });
  wrap.querySelectorAll('.v62-action-btn').forEach(btn => {
    btn.onclick = (e) => {
      e.stopPropagation();
      const fn = window.invOpenPanelForInvoice;
      if (typeof fn === 'function') fn(btn.dataset.invoice);
    };
  });
}

function renderRow(r) {
  const b = badgeFor(r);
  const failed = isFailed(r);
  let action = '';
  if (failed) {
    if (isErrored(r) && !isMissingDocs(r)) {
      action = `<button class="v62-action-btn retry" data-invoice="${escHtml(r.invoiceNumber)}">↻ Retry</button>`;
    } else {
      action = `<button class="v62-action-btn attach" data-invoice="${escHtml(r.invoiceNumber)}">📎 Attach &amp; Retry</button>`;
    }
  }
  const rowStyle = failed ? 'cursor:pointer;' : '';
  return `<tr data-invoice="${escHtml(r.invoiceNumber)}" style="${rowStyle}">
    <td style="padding:8px; border-bottom:1px solid #f1f5f9; font-family:'SF Mono', Consolas, monospace; font-size:0.84rem;">${escHtml(r.invoiceNumber)}</td>
    <td style="padding:8px; border-bottom:1px solid #f1f5f9; font-family:'SF Mono', Consolas, monospace; font-size:0.82rem; color:#475569;">${escHtml(r.containerNumber || '—')}</td>
    <td style="padding:8px; border-bottom:1px solid #f1f5f9; font-size:0.82rem;">${escHtml(r.customerCode || '')} ${escHtml(r.customerName || '')}</td>
    <td style="padding:8px; border-bottom:1px solid #f1f5f9;"><span class="v62-badge ${b.cls}">${b.text}</span></td>
    <td style="padding:8px; border-bottom:1px solid #f1f5f9; text-align:right;">${action}</td>
  </tr>`;
}

// Expose for invoice-sender.js + Task 9 panel + Task 11 retry
window.invShowResultsView = showResultsView;
window.invHideResultsView = hideResultsView;
window.invRenderResults = renderResults;
