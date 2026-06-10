// Invoice Sender — Results view (Fix 1) for v2.62.
// Activated after a send job completes. Replaces the live progress table
// with a tabbed results table.

import { sendState, invoiceState } from '../../shared/state.js';
import { escHtml, renderInvoiceNumberHtml, renderContainerCellHtml } from '../../shared/utils.js';

const SLOT_LABELS = { pod: 'POD', bol: 'BOL', pol: 'POL', do: 'DO', pl: 'PL' };

// Treat both new 'missing_docs' and legacy 'skipped_no_attachments' as failed-missing-docs.
function isMissingDocs(row) {
  return row.sendStatus === 'missing_docs' || row.sendStatus === 'skipped_no_attachments';
}

function isErrored(row) {
  return row.sendStatus === 'error';
}

// 2026-06-10: new statuses surfaced by the TMS-direct send flow.
// Both indicate the send was held (no email went out) and the row should
// be retried once the upstream condition clears.
function isNeedsRetry(row) {
  return row.sendStatus === 'tms_unreachable' || row.sendStatus === 'pod_missing';
}

function isFailed(row) {
  return isMissingDocs(row) || isErrored(row) || isNeedsRetry(row);
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
  if (row.sendStatus === 'tms_unreachable') {
    return { cls: 'api-error', text: '⚡ TMS unreachable — retry when connection returns' };
  }
  if (row.sendStatus === 'pod_missing') {
    return { cls: 'missing-doc', text: '⚠ POD not yet on TMS — retry after checkpoint clears' };
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
      if (btn.dataset.action === 'open-qbo') {
        const fn = window.invOpenInQuickbooks;
        if (typeof fn === 'function') fn(btn.dataset.invoice);
        return;
      }
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
    if (r.routingType === 'warehouse' && isMissingDocs(r)) {
      action = `<button class="v62-action-btn qbo" data-action="open-qbo" data-invoice="${escHtml(r.invoiceNumber)}">Open in QuickBooks</button>`;
    } else if (isErrored(r) && !isMissingDocs(r)) {
      action = `<button class="v62-action-btn retry" data-invoice="${escHtml(r.invoiceNumber)}">↻ Retry</button>`;
    } else if (isNeedsRetry(r)) {
      action = `<button class="v62-action-btn retry" data-invoice="${escHtml(r.invoiceNumber)}">↻ Retry</button>`;
    } else {
      action = `<button class="v62-action-btn attach" data-invoice="${escHtml(r.invoiceNumber)}">📎 Attach &amp; Retry</button>`;
    }
  }
  const rowStyle = failed ? 'cursor:pointer;' : '';
  return `<tr data-invoice="${escHtml(r.invoiceNumber)}" style="${rowStyle}">
    <td style="padding:8px; border-bottom:1px solid #f1f5f9; font-family:'SF Mono', Consolas, monospace; font-size:0.84rem;">${renderInvoiceNumberHtml(r.invoiceNumber)}</td>
    <td style="padding:8px; border-bottom:1px solid #f1f5f9; font-family:'SF Mono', Consolas, monospace; font-size:0.82rem; color:#475569;">${renderContainerCellHtml(r)}</td>
    <td style="padding:8px; border-bottom:1px solid #f1f5f9; font-size:0.82rem;">${escHtml(r.customerCode || '')} ${escHtml(r.customerName || '')}</td>
    <td style="padding:8px; border-bottom:1px solid #f1f5f9;"><span class="v62-badge ${b.cls}">${b.text}</span></td>
    <td style="padding:8px; border-bottom:1px solid #f1f5f9; text-align:right;">${action}</td>
  </tr>`;
}

// Expose for invoice-sender.js + Task 9 panel + Task 11 retry
window.invShowResultsView = showResultsView;
window.invHideResultsView = hideResultsView;
window.invRenderResults = renderResults;

// ── Task 9: Diagnostic side panel (Fix 1) ─────────────────────────

function buildDiagnostic(row) {
  if (isMissingDocs(row)) {
    // Warehouse-empty case: backend already provides a plain-English
    // explanation in row.errorMessage. Surface it directly and route the
    // user to QuickBooks instead of the local Fix-It drop zones.
    if (row.routingType === 'warehouse') {
      const msg = row.errorMessage || row.error || "No documents attached in QuickBooks. Add at least one Excel or PDF backup to the QBO invoice, then resend.";
      return {
        cls: 'v62-error-box',
        title: 'No attachments in QuickBooks',
        explanation: msg,
        checks: [
          { status: 'ok', text: 'Found invoice in QuickBooks' },
          { status: 'fail', text: 'No Excel or PDF backup attached to the QBO invoice' },
        ],
        nextStep: `<strong>What to do:</strong> Open this invoice in QuickBooks, attach at least one Excel or PDF backup, then click Open in QuickBooks above and resend.`,
        missingSlots: [],
      };
    }
    const missing = (row.missingDocs || []).map(d => SLOT_LABELS[String(d).toLowerCase()] || String(d).toUpperCase());
    if (missing.length === 0) {
      return {
        cls: 'v62-error-box',
        title: 'Missing documents',
        explanation: "We couldn't find one of the documents this customer requires. The invoice was paused before sending so you can attach the missing file and retry.",
        checks: [
          { status: 'ok', text: 'Found invoice in QuickBooks' },
          { status: 'ok', text: `Verified container number matches (${row.containerNumber || '—'})` },
          { status: 'fail', text: 'Required document not attached in QuickBooks' },
          { status: 'fail', text: 'No matching document on the TMS work order' },
        ],
        nextStep: `<strong>What to do:</strong> Open this row, drop the missing file in the Fix-It area, then hit Retry.`,
        missingSlots: [],
      };
    }
    const docs = missing.join(' and ');
    const isPlural = missing.length > 1;
    return {
      cls: 'v62-error-box',
      title: isPlural ? `${docs} are missing` : `${docs} is missing`,
      explanation: isPlural
        ? `This customer requires ${docs}, but they're not attached in QuickBooks and not available on the TMS work order. Drop the files below — once attached, you can retry the send.`
        : `We couldn't find the ${docs} — QuickBooks has no ${docs} attachment, and the TMS work order doesn't show one either. Drop the file below and we'll attach it, then retry the send.`,
      checks: [
        { status: 'ok', text: 'Found invoice in QuickBooks' },
        { status: 'ok', text: `Verified container number matches (${row.containerNumber || '—'})` },
        { status: 'ok', text: 'Connected to TMS work order' },
        ...missing.map(d => ({ status: 'fail', text: `No ${d} attached in QuickBooks` })),
        ...missing.map(d => ({ status: 'fail', text: `No ${d} listed on the TMS work order` })),
      ],
      nextStep: `<strong>What to do:</strong> Drop the ${docs} file${isPlural ? 's' : ''} below. We'll check the filename, run Claude AI if needed, attach to your email, save a copy to QuickBooks, and re-send the invoice.`,
      missingSlots: missing,
    };
  }

  if (isErrored(row)) {
    const msg = (row.errorMessage || '').toLowerCase();
    const isTimeout = msg.includes('timeout') || msg.includes('did not respond') || msg.includes('didn');
    if (isTimeout) {
      return {
        cls: 'v62-error-box warn',
        title: "QuickBooks didn't respond",
        explanation: `We tried to read this invoice's attachments, but QuickBooks took too long. This is almost always temporary — just hit Retry and it usually goes through.`,
        checks: [
          { status: 'ok', text: 'Found invoice in QuickBooks' },
          { status: 'ok', text: `Verified container number matches (${row.containerNumber || '—'})` },
          { status: 'fail', text: row.errorMessage || 'Request timed out' },
        ],
        nextStep: `<strong>What to do:</strong> Just retry — most QBO timeouts clear up on their own within a minute.`,
        missingSlots: [],
      };
    }
    return {
      cls: 'v62-error-box',
      title: 'Send error',
      explanation: row.errorMessage || 'Something went wrong while sending this invoice.',
      checks: [
        { status: 'fail', text: row.errorMessage || 'Unknown error' },
      ],
      nextStep: `<strong>What to do:</strong> Check the error details above. If it looks transient, hit Retry.`,
      missingSlots: [],
    };
  }

  // 2026-06-10: TMS-direct flow held-send statuses.
  if (row.sendStatus === 'tms_unreachable') {
    return {
      cls: 'v62-error-box warn',
      title: 'TMS was unreachable',
      explanation: `We tried 3 times to pull this invoice's supporting docs from TMS but couldn't get through. The invoice was held — no email went out. This is almost always temporary.`,
      checks: [
        { status: 'ok', text: 'Found invoice in QuickBooks' },
        { status: 'ok', text: `Verified container number matches (${row.containerNumber || '—'})` },
        { status: 'fail', text: 'TMS unreachable after 3 retries' },
      ],
      nextStep: `<strong>What to do:</strong> Wait a minute, then hit Retry. If it keeps failing, check whether TMS is up.`,
      missingSlots: [],
    };
  }
  if (row.sendStatus === 'pod_missing') {
    return {
      cls: 'v62-error-box warn',
      title: 'POD not yet on TMS',
      explanation: `This customer requires a POD before the invoice goes out. TMS doesn't have one for this work order yet — usually that means the load is still at a checkpoint or hasn't been signed for. The invoice was held so the customer doesn't get an incomplete email.`,
      checks: [
        { status: 'ok', text: 'Found invoice in QuickBooks' },
        { status: 'ok', text: `Verified container number matches (${row.containerNumber || '—'})` },
        { status: 'ok', text: 'Connected to TMS work order' },
        { status: 'fail', text: 'No POD attached on the TMS work order' },
      ],
      nextStep: `<strong>What to do:</strong> Wait until the POD lands on TMS (usually after the load clears the checkpoint), then hit Retry.`,
      missingSlots: ['POD'],
    };
  }
  return null;
}

function openPanelForInvoice(invoiceNumber) {
  const row = invoiceState.invoices.find(r => r.invoiceNumber === invoiceNumber);
  if (!row) return;
  sendState.activePanelInvoiceId = invoiceNumber;
  if (!sendState.retry[invoiceNumber]) {
    sendState.retry[invoiceNumber] = { panelStage: 'fix', attached: {} };
  }
  const el = document.getElementById('invSendResults');
  if (el) el.classList.add('panel-open');
  renderPanel();
}

function closePanel() {
  sendState.activePanelInvoiceId = null;
  const el = document.getElementById('invSendResults');
  if (el) el.classList.remove('panel-open');
  const inner = document.getElementById('invDetailPanelInner');
  if (inner) inner.innerHTML = '';
}

function renderPanel() {
  const invoiceNumber = sendState.activePanelInvoiceId;
  if (!invoiceNumber) return;
  const row = invoiceState.invoices.find(r => r.invoiceNumber === invoiceNumber);
  if (!row) return;
  const state = sendState.retry[invoiceNumber] || { panelStage: 'fix', attached: {} };
  const inner = document.getElementById('invDetailPanelInner');
  if (!inner) return;

  // Stage dispatch: 'retrying' and 'success' are defined below by Task 11.
  // The fallthrough is the 'fix' stage rendered inline.
  if (state.panelStage === 'retrying' && typeof window.invRenderRetryingStage === 'function') {
    window.invRenderRetryingStage(row);
    return;
  }
  if (state.panelStage === 'success' && typeof window.invRenderSuccessStage === 'function') {
    window.invRenderSuccessStage(row);
    return;
  }


  const diag = buildDiagnostic(row);
  if (!diag) {
    inner.innerHTML = `
      <div class="v62-panel-header">
        <span class="v62-panel-title">Send Diagnostic</span>
        <button class="v62-panel-close" onclick="window.invClosePanel()">×</button>
      </div>
      <div style="padding:12px 0; color:#64748b;">No diagnostic available for this row.</div>
    `;
    return;
  }

  const checksHtml = diag.checks.map(c => {
    const ic = c.status === 'ok' ? '✓' : c.status === 'warn' ? '!' : '✕';
    return `<li><span class="icon ${c.status}">${ic}</span>${escHtml(c.text)}</li>`;
  }).join('');

  inner.innerHTML = `
    <div class="v62-panel-header">
      <span class="v62-panel-title">Send Diagnostic</span>
      <button class="v62-panel-close" onclick="window.invClosePanel()">×</button>
    </div>
    <div class="v62-panel-invoice-id">${escHtml(row.invoiceNumber)}</div>
    <div class="v62-panel-meta">
      <span class="label">CONTAINER:</span> ${escHtml(row.containerNumber || '—')}<br>
      <span class="label">CUSTOMER:</span> ${escHtml(row.customerCode || '')} ${escHtml(row.customerName || '')}
    </div>
    <div id="invPanelRecipients" class="v64-panel-recipients">
      <div class="v64-rcp-label">Will send to:</div>
      <div class="v64-rcp-loading">Checking the live customer record…</div>
    </div>
    <div class="${diag.cls}">
      <div class="v62-error-title">${escHtml(diag.title)}</div>
      <div class="v62-error-explanation">${escHtml(diag.explanation)}</div>
    </div>
    <strong style="font-size:0.82rem; color:#0f172a;">What we checked:</strong>
    <ul class="v62-checks-list">${checksHtml}</ul>
    <div class="v62-next-step">${diag.nextStep}</div>
    <div id="invFixItContainer"></div>
  `;

  // Task 10 fills this when it lands. Until then, no-op.
  if (typeof window.invRenderFixItSection === 'function') {
    window.invRenderFixItSection(row, diag.missingSlots);
  }

  // v64: pull the LIVE recipient list from the agent and surface it.
  // This is what Retry/Send will actually use — so if it disagrees with
  // what the user just edited in Customer Manager, the drift becomes
  // obvious BEFORE they click. Fire-and-forget on the same panel.
  renderLiveRecipients(row, invoiceNumber);
}

async function renderLiveRecipients(row, invoiceNumber) {
  const el = document.getElementById('invPanelRecipients');
  if (!el) return;
  // Only render if the panel is still showing this row when the fetch returns.
  const stillActive = () => sendState.activePanelInvoiceId === invoiceNumber && document.getElementById('invPanelRecipients') === el;

  const code = row.customerCode || '';
  if (!code) {
    if (!stillActive()) return;
    el.innerHTML = `
      <div class="v64-rcp-label">Will send to:</div>
      <div class="v64-rcp-warn">No customer code on this invoice — nothing will be sent.</div>
    `;
    return;
  }

  let live = null;
  try {
    if (window.agentBridge && typeof window.agentBridge.getCustomer === 'function') {
      live = await window.agentBridge.getCustomer(code);
    }
  } catch (_) { live = null; }

  if (!stillActive()) return;

  if (!live) {
    el.innerHTML = `
      <div class="v64-rcp-label">Will send to:</div>
      <div class="v64-rcp-warn">Couldn't reach the customer record — the send will fail until this is fixed.</div>
    `;
    return;
  }

  const to = Array.isArray(live.emails) ? live.emails : [];
  const cc = Array.isArray(live.ccEmails) ? live.ccEmails.slice() : [];
  // Agent always adds ar@ngltrans.net as a fixed Cc on the send (see retry_invoice.py).
  if (!cc.some(e => (e || '').toLowerCase() === 'ar@ngltrans.net')) cc.unshift('ar@ngltrans.net');
  const bcc = Array.isArray(live.bccEmails) ? live.bccEmails : [];

  // Drift detection: compare the live list to the row's cached resolvedEmails.
  const cachedTo = Array.isArray(row.resolvedEmails) ? row.resolvedEmails : [];
  const normSet = (xs) => new Set((xs || []).map(s => String(s || '').trim().toLowerCase()).filter(Boolean));
  const liveSet = normSet(to);
  const cachedSet = normSet(cachedTo);
  const added = [...cachedSet].filter(e => !liveSet.has(e));   // in your CM but agent won't send
  const removed = [...liveSet].filter(e => !cachedSet.has(e)); // agent will send to but not in your CM

  const renderList = (xs) => xs.length === 0
    ? '<span class="v64-rcp-none">(none)</span>'
    : '<ul class="v64-rcp-list">' + xs.map(e => `<li>${escHtml(e)}</li>`).join('') + '</ul>';

  let driftHtml = '';
  if (added.length > 0 || removed.length > 0) {
    const addedHtml = added.length > 0
      ? `<div><strong>In Customer Manager but agent doesn't have:</strong> ${added.map(e => escHtml(e)).join(', ')}</div>`
      : '';
    const removedHtml = removed.length > 0
      ? `<div><strong>Agent will send to (not visible in your CM list):</strong> ${removed.map(e => escHtml(e)).join(', ')}</div>`
      : '';
    driftHtml = `
      <div class="v64-rcp-drift">
        ⚠ The agent's recipient list doesn't match what you see in Customer Manager.
        ${addedHtml}${removedHtml}
        <div style="margin-top:6px; font-size:0.78rem;">Open Customer Manager, re-save HLLOGI01 (and check the toast says "Saved to cloud"), then come back and try again.</div>
      </div>
    `.replace(/HLLOGI01/g, escHtml(code));
  }

  el.innerHTML = `
    <div class="v64-rcp-label">Will send to:</div>
    <div class="v64-rcp-box">
      <div class="v64-rcp-section"><span class="v64-rcp-tag">To</span>${renderList(to)}</div>
      <div class="v64-rcp-section"><span class="v64-rcp-tag">Cc</span>${renderList(cc)}</div>
      ${bcc.length > 0 ? `<div class="v64-rcp-section"><span class="v64-rcp-tag">Bcc</span>${renderList(bcc)}</div>` : ''}
    </div>
    ${driftHtml}
  `;
}

// Hook ESC key to close panel
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && sendState.activePanelInvoiceId) {
    closePanel();
  }
});

window.invOpenPanelForInvoice = openPanelForInvoice;
window.invClosePanel = closePanel;
window.invRenderPanel = renderPanel;

// ── Task 10: Fix-It drop zones (Fix 2) ────────────────────────────

const FILENAME_PATTERNS = {
  POD: /\b(pod|proof.?of.?delivery|delivery.?receipt)\b/i,
  BOL: /\b(bol|bill.?of.?lading|b\.?l)\b/i,
  POL: /\b(pol|proof.?of.?loading)\b/i,
  DO:  /\b(d\.?o\.?|delivery.?order)\b/i,
  PL:  /\b(p\.?l\.?|packing.?list)\b/i,
};

function matchesByFilename(filename, slot) {
  const pat = FILENAME_PATTERNS[slot.toUpperCase()];
  if (!pat) return false;
  return pat.test(filename);
}

function allSlotsAttached(state, missingSlots) {
  return missingSlots.every(s => {
    const a = state.attached[s];
    return a && (a.stage === 'attached' || a.stage === 'attached_manual');
  });
}

function renderFixItSection(row, missingSlots) {
  const container = document.getElementById('invFixItContainer');
  if (!container) return;
  const state = sendState.retry[row.invoiceNumber];
  if (!state) return;

  // Pure transient error (no missing docs) → just retry/skip buttons, no drop zones
  if (missingSlots.length === 0) {
    container.innerHTML = `
      <div class="v62-fix-it-section">
        <div class="v62-fix-it-title">⚡ Fix it</div>
        <div class="v62-fix-it-subtitle">Nothing to attach — temporary QuickBooks hiccup. Just hit Retry.</div>
        <div class="v62-panel-actions">
          <button class="v62-btn-retry" onclick="window.invStartRetry('${escHtml(row.invoiceNumber)}')">↻ Try Again</button>
          <button class="v62-btn-skip" onclick="window.invSkipRow('${escHtml(row.invoiceNumber)}')">Skip</button>
        </div>
      </div>
    `;
    return;
  }

  // Missing-doc path: drop zones + retry/skip
  const allReady = allSlotsAttached(state, missingSlots);
  const zonesHtml = missingSlots.map(slot => renderDropZone(row.invoiceNumber, slot)).join('');

  container.innerHTML = `
    <div class="v62-fix-it-section">
      <div class="v62-fix-it-title">📎 Fix it</div>
      <div class="v62-fix-it-subtitle">Drop the missing file${missingSlots.length > 1 ? 's' : ''} below. Accepts PDF, JPG, PNG, HEIC.</div>
      ${zonesHtml}
      <div class="v62-panel-actions">
        <button class="v62-btn-retry" ${allReady ? '' : 'disabled'} onclick="window.invStartRetry('${escHtml(row.invoiceNumber)}')">↻ Retry Send</button>
        <button class="v62-btn-skip" onclick="window.invSkipRow('${escHtml(row.invoiceNumber)}')">Skip</button>
      </div>
    </div>
  `;

  missingSlots.forEach(slot => bindDropZone(row.invoiceNumber, slot));
}

function renderDropZone(invoiceNumber, slot) {
  const state = sendState.retry[invoiceNumber];
  const att = state.attached[slot];

  if (!att) {
    return `
      <div class="v62-drop-zone" data-invoice="${escHtml(invoiceNumber)}" data-slot="${escHtml(slot)}">
        <div class="dz-icon">📎</div>
        <div class="dz-title">Drop ${escHtml(slot)} here</div>
        <div class="dz-subtitle">PDF / JPG / PNG / HEIC · or click to browse</div>
      </div>`;
  }
  if (att.stage === 'uploading') {
    return `
      <div class="v62-drop-zone uploading">
        <div class="v62-dz-status-row">
          <div class="v62-dz-status-icon"><span class="v62-small-spinner"></span></div>
          <div class="v62-dz-info">
            <div class="v62-dz-file-name">${escHtml(att.name)}</div>
            <div class="v62-dz-file-status">Reading file…</div>
          </div>
        </div>
      </div>`;
  }
  if (att.stage === 'verifying') {
    return `
      <div class="v62-drop-zone verifying">
        <div class="v62-dz-status-row">
          <div class="v62-dz-status-icon"><span class="v62-small-spinner"></span></div>
          <div class="v62-dz-info">
            <div class="v62-dz-file-name">${escHtml(att.name)}</div>
            <div class="v62-dz-file-status">🔍 Verifying document type with Claude AI…</div>
          </div>
        </div>
      </div>`;
  }
  if (att.stage === 'mismatch') {
    return `
      <div class="v62-drop-zone mismatch" data-invoice="${escHtml(invoiceNumber)}" data-slot="${escHtml(slot)}">
        <div class="v62-dz-status-row">
          <div class="v62-dz-status-icon">⚠</div>
          <div class="v62-dz-info">
            <div class="v62-dz-file-name">${escHtml(att.name)}</div>
            <div class="v62-dz-file-status">Looks like a ${escHtml(att.detectedAs || 'different doc')}, not a ${escHtml(slot)}</div>
          </div>
          <button class="v62-dz-replace-btn" onclick="window.invClearAttachment('${escHtml(invoiceNumber)}','${escHtml(slot)}')">Replace</button>
        </div>
        <div style="margin-top:8px; font-size:0.76rem; color:#78350f;">
          <a href="#" onclick="window.invForceAttach('${escHtml(invoiceNumber)}','${escHtml(slot)}'); return false;" style="color:#c2410c;">Use anyway →</a>
        </div>
      </div>`;
  }
  if (att.stage === 'attached' || att.stage === 'attached_manual') {
    let label;
    if (att.stage === 'attached_manual') label = '✓ Manually confirmed · ready to retry';
    else if (att.verifiedBy === 'ai') label = `✓ ${slot} verified by AI · ready to retry`;
    else label = `✓ ${slot} verified by filename · ready to retry`;
    return `
      <div class="v62-drop-zone attached">
        <div class="v62-dz-status-row">
          <div class="v62-dz-status-icon">✓</div>
          <div class="v62-dz-info">
            <div class="v62-dz-file-name">${escHtml(att.name)}</div>
            <div class="v62-dz-file-status">${escHtml(label)}</div>
          </div>
          <button class="v62-dz-replace-btn" onclick="window.invClearAttachment('${escHtml(invoiceNumber)}','${escHtml(slot)}')">Replace</button>
        </div>
      </div>`;
  }
  return '';
}

function bindDropZone(invoiceNumber, slot) {
  const zone = document.querySelector(
    `.v62-drop-zone[data-invoice="${CSS.escape(invoiceNumber)}"][data-slot="${CSS.escape(slot)}"]`
  );
  if (!zone) return;
  const handleFile = (file) => acceptFile(invoiceNumber, slot, file);

  zone.onclick = () => {
    const inp = document.createElement('input');
    inp.type = 'file';
    inp.accept = '.pdf,.jpg,.jpeg,.png,.heic,.heif,application/pdf,image/*';
    inp.onchange = (e) => { if (e.target.files[0]) handleFile(e.target.files[0]); };
    inp.click();
  };
  zone.ondragover = (e) => { e.preventDefault(); e.stopPropagation(); zone.classList.add('drag-over'); };
  zone.ondragleave = () => zone.classList.remove('drag-over');
  zone.ondrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    zone.classList.remove('drag-over');
    if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
  };
}

async function acceptFile(invoiceNumber, slot, file) {
  const state = sendState.retry[invoiceNumber];
  if (!state) return;
  state.attached[slot] = { name: file.name, file, stage: 'uploading' };
  renderPanel();

  // Tier 1: filename match — instant attach, no API call
  if (matchesByFilename(file.name, slot)) {
    state.attached[slot] = { name: file.name, file, stage: 'attached', verifiedBy: 'filename' };
    renderPanel();
    return;
  }

  // Tier 2: Claude AI fallback
  state.attached[slot] = { name: file.name, file, stage: 'verifying' };
  renderPanel();
  try {
    const result = await window.agentBridge.verifyFile(slot, file);
    if (result.matches_slot) {
      state.attached[slot] = { name: file.name, file, stage: 'attached', verifiedBy: 'ai' };
    } else {
      state.attached[slot] = {
        name: file.name, file, stage: 'mismatch',
        detectedAs: result.detected_as || result.doc_type || 'unknown',
      };
    }
  } catch (err) {
    // On API failure, fall through to attached-manual — let user proceed
    console.warn('[v62] verifyFile failed', err);
    state.attached[slot] = { name: file.name, file, stage: 'attached_manual', verifiedBy: 'manual' };
  }
  renderPanel();
}

function clearAttachment(invoiceNumber, slot) {
  const state = sendState.retry[invoiceNumber];
  if (!state) return;
  delete state.attached[slot];
  renderPanel();
}

function forceAttach(invoiceNumber, slot) {
  const state = sendState.retry[invoiceNumber];
  if (!state) return;
  const cur = state.attached[slot];
  if (!cur) return;
  state.attached[slot] = { ...cur, stage: 'attached_manual', verifiedBy: 'manual' };
  renderPanel();
}

function skipRow(invoiceNumber) {
  const row = invoiceState.invoices.find(r => r.invoiceNumber === invoiceNumber);
  if (row) {
    row.sendStatus = 'skipped';
    row.errorMessage = 'Skipped by user';
  }
  // Persist via the existing status saver if present
  if (typeof window.invSaveSendStatus === 'function') {
    window.invSaveSendStatus(invoiceNumber, 'skipped', { errorMessage: 'Skipped by user' });
  }
  // Close panel + re-render results
  if (typeof window.invClosePanel === 'function') window.invClosePanel();
  if (typeof window.invRenderResults === 'function') window.invRenderResults();
}

window.invRenderFixItSection = renderFixItSection;
window.invClearAttachment = clearAttachment;
window.invForceAttach = forceAttach;
window.invSkipRow = skipRow;

// ── Task 11: Retry orchestration + auto-advance + bulk retry ──────

async function startRetry(invoiceNumber) {
  const row = invoiceState.invoices.find(r => r.invoiceNumber === invoiceNumber);
  const state = sendState.retry[invoiceNumber];
  if (!row || !state) return;

  state.panelStage = 'retrying';
  renderPanel();
  paintRetryProgress(/* in-flight */ true);

  // Build slotFiles from attached state
  const slotFiles = Object.entries(state.attached)
    .filter(([_, a]) => a && (a.stage === 'attached' || a.stage === 'attached_manual'))
    .map(([slot, a]) => ({
      slot,
      file: a.file,
      verifiedBy: a.verifiedBy || 'manual',
    }));

  // Resolve QBO invoice_id with fallback
  // TODO: rows don't currently carry a QBO invoice_id; backend resolves
  // invoice_id from invoice_number when this field is empty/unknown.
  // (rows may carry qboInvoiceId if a successful lookup happened earlier;
  // otherwise the agent resolves invoice_id from invoice_number on the backend side.)
  const invoicePayload = {
    invoice_number: row.invoiceNumber,
    invoice_id: row.qboInvoiceId || row.invoiceId || row.invoiceNumber,
    container_number: row.containerNumber || '',
    customer_code: row.customerCode || '',
    amount: row.amount || '',
    subject: row.subject || row.subjectOverride || null,
    do_sender_email: row.doSenderEmail || null,
  };

  try {
    const result = await window.agentBridge.retryInvoice(invoicePayload, slotFiles);
    if (result.status === 'sent') {
      // Mark row sent locally + persist
      row.sendStatus = 'sent';
      row.sentAt = new Date().toLocaleTimeString();
      row.errorMessage = '';
      row.missingDocs = [];
      if (typeof window.invSaveSendStatus === 'function') {
        window.invSaveSendStatus(invoiceNumber, 'sent', { sentAt: row.sentAt });
      }
      state.panelStage = 'success';
      renderPanel();
      renderSuccessAndAdvance(invoiceNumber);
    } else {
      state.panelStage = 'fix';
      renderPanel();
      alert('Retry failed: ' + (result.error || 'Unknown error'));
    }
  } catch (err) {
    state.panelStage = 'fix';
    renderPanel();
    alert('Retry error: ' + err.message);
  }
  // Refresh table + bulk button state
  if (typeof window.invRenderResults === 'function') window.invRenderResults();
  updateBulkRetryButton();
}

function paintRetryProgress(inflight) {
  const stepsEl = document.getElementById('invRetrySteps');
  if (!stepsEl) return;
  const steps = [
    '⟳ Sending email with attachments…',
    '⟳ Saving file to QuickBooks in background…',
  ];
  stepsEl.innerHTML = steps.map(s => `<div>${escHtml(s)}</div>`).join('');
}

function renderSuccessStage(row) {
  const inner = document.getElementById('invDetailPanelInner');
  if (!inner) return;
  inner.innerHTML = `
    <div class="v62-panel-header">
      <span class="v62-panel-title">Send Diagnostic</span>
      <button class="v62-panel-close" onclick="window.invClosePanel()">×</button>
    </div>
    <div class="v62-success-state">
      <div class="v62-big-check">✓</div>
      <h3>${escHtml(row.invoiceNumber)} sent successfully!</h3>
      <p>Email delivered.</p>
      <div class="v62-auto-advance-note">Moving to the next failure in 1.5s…</div>
    </div>
  `;
}

function renderRetryingStage(row) {
  const inner = document.getElementById('invDetailPanelInner');
  if (!inner) return;
  inner.innerHTML = `
    <div class="v62-panel-header">
      <span class="v62-panel-title">Retrying Send</span>
      <button class="v62-panel-close" onclick="window.invClosePanel()">×</button>
    </div>
    <div class="v62-panel-invoice-id">${escHtml(row.invoiceNumber)}</div>
    <div class="v62-panel-meta">
      <span class="label">CONTAINER:</span> ${escHtml(row.containerNumber || '—')}<br>
      <span class="label">CUSTOMER:</span> ${escHtml(row.customerCode || '')} ${escHtml(row.customerName || '')}
    </div>
    <div class="v62-retry-progress">
      <div class="v62-retry-progress-title"><span class="v62-small-spinner"></span> Retrying…</div>
      <div class="v62-retry-steps" id="invRetrySteps">
        <div>⟳ Sending email with attachments…</div>
        <div>⟳ Saving file to QuickBooks in background…</div>
      </div>
    </div>
  `;
}

// Expose the stage renderers so renderPanel() (declared earlier) can dispatch
// to them by panelStage. Done via window.* to avoid forward-reference TDZ issues.
window.invRenderRetryingStage = renderRetryingStage;
window.invRenderSuccessStage = renderSuccessStage;

function renderSuccessAndAdvance(currentInvoiceNumber) {
  setTimeout(() => {
    const failures = getFailedRows().filter(r => r.invoiceNumber !== currentInvoiceNumber);
    if (failures.length > 0) {
      openPanelForInvoice(failures[0].invoiceNumber);
    } else if (typeof window.invClosePanel === 'function') {
      window.invClosePanel();
    }
    if (typeof window.invRenderResults === 'function') window.invRenderResults();
  }, 1500);
}

// ── Bulk retry ────────────────────────────────────────────────────

function getReadyToRetryRows() {
  return getFailedRows().filter(r => {
    const s = sendState.retry[r.invoiceNumber];
    if (!s) return false;
    // Transient error (no missingDocs) → always retriable
    if (isErrored(r) && !isMissingDocs(r)) return true;
    // Missing-docs error → need every slot attached
    const missing = (r.missingDocs || []).map(d => SLOT_LABELS[String(d).toLowerCase()] || String(d).toUpperCase());
    if (missing.length === 0) return false; // no specifics, can't bulk-retry
    return missing.every(slot => {
      const a = s.attached[slot];
      return a && (a.stage === 'attached' || a.stage === 'attached_manual');
    });
  });
}

function updateBulkRetryButton() {
  const btn = document.getElementById('invSendBulkRetryBtn');
  const cnt = document.getElementById('invSendBulkCount');
  if (!btn || !cnt) return;
  const ready = getReadyToRetryRows();
  cnt.textContent = ready.length;
  btn.disabled = ready.length === 0;
  btn.onclick = async () => {
    btn.disabled = true;
    btn.textContent = `Retrying ${ready.length}…`;
    for (const row of ready) {
      // openPanel so user sees progress
      openPanelForInvoice(row.invoiceNumber);
      await startRetry(row.invoiceNumber);
    }
    // Restore button text
    btn.innerHTML = `↻ Retry All Fixed (<span id="invSendBulkCount">0</span>)`;
    updateBulkRetryButton();
  };
}

// Call updateBulkRetryButton whenever results re-render.
const _origRenderResults_t11 = window.invRenderResults;
window.invRenderResults = function v62RenderResultsWithBulk() {
  if (typeof _origRenderResults_t11 === 'function') _origRenderResults_t11();
  updateBulkRetryButton();
};

window.invStartRetry = startRetry;
window.invUpdateBulkRetryButton = updateBulkRetryButton;
