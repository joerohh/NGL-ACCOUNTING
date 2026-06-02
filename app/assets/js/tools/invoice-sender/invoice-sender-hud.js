// ══════════════════════════════════════════════════════════════════
//  Invoice Sender — Combined Results HUD (v2.71)
//
//  Replaces the v2.62 stacked surfaces (Send Complete card + filter +
//  table + tabbed results) with a single state-machine HUD that
//  morphs across Idle / Uploaded / Sending / Complete.
//
//  Reads from invoiceState.invoices[] and sendState. Writes to the
//  v64-* DOM scaffolded in index.html (the legacy v62 surfaces are
//  kept as hidden compat shims during this migration).
//
//  Spec:  docs/superpowers/specs/2026-05-14-invoice-sender-results-redesign-design.md
//  Mockup: app/mockups/v2.70-results-hud-mockup.html
// ══════════════════════════════════════════════════════════════════

import { invoiceState, sendState, state } from '../../shared/state.js';
import { escHtml, renderInvoiceNumberHtml, renderContainerCellHtml } from '../../shared/utils.js';

// ────────────────────────────────────────────────────────────────
// Pill taxonomy — final label + severity per state
// ────────────────────────────────────────────────────────────────
const PILL = {
  sent:            { label: 'Sent',                       severity: 'ok' },
  sending:         { label: 'Sending…',                   severity: 'live' },
  queued:          { label: 'Queued',                     severity: 'queued' },
  ready:           { label: 'Ready to Send',              severity: 'ready' },
  // Pre-send validation pills (set by invValidateRowsOnUpload)
  val_missing_inv: { label: 'Missing INV#',               severity: 'warn' },
  val_missing_fld: { label: 'Missing Field',              severity: 'warn' },
  val_no_customer: { label: 'No Customer Match',          severity: 'warn' },
  val_no_email:    { label: 'No Email on File',           severity: 'warn' },
  val_dup_inv:     { label: 'Duplicate INV#',             severity: 'warn' },
  val_needs_rev:   { label: 'Customer Needs Review',      severity: 'warn' },
  // Post-send failure pills
  fail_pod:        { label: 'POD Missing',                severity: 'warn' },
  fail_pod_bol:    { label: 'POD + BOL Missing',          severity: 'warn' },
  fail_docs:       { label: 'Docs Missing',               severity: 'warn' },
  fail_timeout:    { label: 'QuickBooks Timed Out',       severity: 'error' },
  fail_auth:       { label: 'Signed Out of QuickBooks',   severity: 'error' },
  fail_network:    { label: 'No Internet',                severity: 'error' },
  fail_mismatch:   { label: 'Amount Mismatch',            severity: 'warn' },
  fail_unknown:    { label: 'Unexpected Error',           severity: 'error' },
  skipped:         { label: 'Skipped',                    severity: 'queued' },
};

// Resolve panel content per pill — What's wrong / What to do / action
const PILL_HINT = {
  val_missing_inv: {
    whatWrong: "This Excel row has no invoice number, so we can't match it to QuickBooks.",
    whatTodo:  "Fix the invoice number in your Excel and re-upload.",
    actionLabel: 'Open invoice in QBO',
  },
  val_missing_fld: {
    whatWrong: "A required field is empty in this row — we need it before we can send.",
    whatTodo:  "Fill in the missing value in your Excel and re-upload.",
    actionLabel: 'Open Excel',
  },
  val_no_customer: {
    whatWrong: "Customer code doesn't match any customer in your address book.",
    whatTodo:  "Open Customer Manager and add this customer, or fix the code in Excel.",
    actionLabel: 'Open Customer Manager',
  },
  val_no_email: {
    whatWrong: "This customer doesn't have an email address on file.",
    whatTodo:  "Add their email in Customer Manager, then re-upload.",
    actionLabel: 'Open Customer Manager',
  },
  val_dup_inv: {
    whatWrong: "Two rows in this Excel share the same invoice number.",
    whatTodo:  "Remove the duplicate from your Excel and re-upload.",
    actionLabel: 'Open Excel',
  },
  val_needs_rev: {
    whatWrong: "This customer is flagged for review — their profile is incomplete or in draft.",
    whatTodo:  "Open Customer Manager and finish setting them up before sending.",
    actionLabel: 'Open customer profile',
  },
  fail_pod: {
    whatWrong: "The POD wasn't found in QuickBooks or TMS. The customer requires it before we can send.",
    whatTodo:  "Open the WO in TMS, upload the POD, then come back here and click Retry.",
    actionLabel: 'Upload to TMS',
    secondaryAction: 'Try fetching again',
    dropzone: true,
    dropzoneLabel: 'Need this send to go now? Attach for this send only.',
    tmsDeepLink: true,
  },
  fail_pod_bol: {
    whatWrong: "Both POD and BOL are missing from QuickBooks and TMS. The customer requires both.",
    whatTodo:  "Upload the missing files to TMS so they're there for future batches too.",
    actionLabel: 'Upload to TMS',
    secondaryAction: 'Try fetching again',
    dropzone: true,
    dropzoneLabel: 'Need this send to go now? Attach for this send only.',
    tmsDeepLink: true,
  },
  fail_docs: {
    whatWrong: "Required documents are missing for this customer.",
    whatTodo:  "Upload the missing files to TMS, or attach them locally for this send only.",
    actionLabel: 'Upload to TMS',
    secondaryAction: 'Try fetching again',
    dropzone: true,
    dropzoneLabel: 'Need this send to go now? Attach for this send only.',
    tmsDeepLink: true,
  },
  fail_timeout: {
    whatWrong: "QuickBooks didn't respond in time when we tried to send this invoice.",
    whatTodo:  "Could be a slow connection or QuickBooks being busy. Try again in a moment.",
    actionLabel: 'Try Again',
  },
  fail_auth: {
    whatWrong: "You've been signed out of QuickBooks. The invoice didn't send.",
    whatTodo:  "Sign back in to QuickBooks, then retry the failed batch.",
    actionLabel: 'Sign back in to QuickBooks',
  },
  fail_network: {
    whatWrong: "We couldn't reach QuickBooks — looks like your internet dropped.",
    whatTodo:  "Check your connection and try again.",
    actionLabel: 'Try Again',
  },
  fail_mismatch: {
    whatWrong: "Something in QuickBooks doesn't match what's in your Excel for this invoice.",
    whatTodo:  "Open the invoice in QuickBooks to verify, or fix the Excel and re-upload.",
    actionLabel: 'Open invoice in QBO',
  },
  fail_unknown: {
    whatWrong: "Something unexpected went wrong when we tried to send this invoice.",
    whatTodo:  "Try again. If it keeps failing, share the technical detail below with support.",
    actionLabel: 'Try Again',
  },
  skipped: {
    whatWrong: "This invoice was skipped during the last send.",
    whatTodo:  "Most often this happens when a row was deselected or had an earlier validation issue.",
    actionLabel: 'Open invoice in QBO',
  },
};

// ────────────────────────────────────────────────────────────────
// Pure helpers — pill mapping + stage detection + URL builders
// ────────────────────────────────────────────────────────────────

// Map a row to its current PILL key based on validation + send state
function pillKeyFor(row, stageHint) {
  const ss = row.sendStatus;

  // Live transient pills first (during Sending stage)
  if (ss === 'sending' || ss === 'in_progress') return 'sending';

  // Terminal pills
  if (ss === 'sent') return 'sent';
  if (ss === 'skipped') return 'skipped';

  if (ss === 'missing_docs' || ss === 'skipped_no_attachments') {
    const m = (row.missingDocs || []).map(d => String(d).toLowerCase());
    const hasPod = m.includes('pod');
    const hasBol = m.includes('bol');
    if (hasPod && hasBol) return 'fail_pod_bol';
    if (hasPod) return 'fail_pod';
    if (m.length > 0) return 'fail_docs';
    return 'fail_docs';
  }

  if (ss === 'mismatch') return 'fail_mismatch';

  if (ss === 'error') {
    const msg = (row.errorMessage || '').toLowerCase();
    if (msg.includes('401') || msg.includes('unauthor') || msg.includes('signed out') || msg.includes('token expired')) return 'fail_auth';
    if (msg.includes('timeout') || msg.includes('timed out') || msg.includes('did not respond')) return 'fail_timeout';
    if (msg.includes('enotfound') || msg.includes('connection refused') || msg.includes('network')) return 'fail_network';
    return 'fail_unknown';
  }

  // Pre-send validation — map existing validationStatus values to pill keys
  if (row._valPill) return row._valPill;
  if (row.validationStatus === 'no_match')   return 'val_no_customer';
  if (row.validationStatus === 'no_email')   return 'val_no_email';
  if (row.validationStatus === 'portal')     return null; // portal customers are fine
  if (row.validationStatus === 'pending')    return null; // not yet validated, no pill
  if (row.validationStatus && row.validationStatus !== 'ready' && row.validationStatus !== 'ok') {
    return 'val_missing_fld'; // unknown validation issue
  }

  // Queued during send
  if (stageHint === 'sending' && (ss === 'not_sent' || !ss)) return 'queued';

  // Validated and not yet sent — show "Ready to Send" so the user knows the row is good
  if ((stageHint === 'uploaded' || stageHint === 'idle') &&
      (row.validationStatus === 'ready' || row.validationStatus === 'ok') &&
      (!ss || ss === 'not_sent')) {
    return 'ready';
  }

  return null;
}

function isFailedPill(key) {
  return key && key.startsWith('fail_');
}
function isValidationPill(key) {
  return key && key.startsWith('val_');
}
function isNeedsAttention(key) {
  return isFailedPill(key) || isValidationPill(key);
}

// Detect the current HUD stage
function detectStage() {
  const rows = invoiceState.invoices || [];
  if (rows.length === 0) return 'idle';
  if (sendState.isRunning) return 'sending';
  // Check if any row has post-send status (a send has happened)
  const anyPostSend = rows.some(r => {
    const ss = r.sendStatus;
    return ss === 'sent' || ss === 'error' || ss === 'missing_docs' ||
           ss === 'skipped_no_attachments' || ss === 'mismatch' || ss === 'skipped';
  });
  if (anyPostSend && !sendState.isRunning) return 'complete';
  return 'uploaded';
}

// Build TMS deep link from row data
// Returns null if no WO# resolved — caller falls back to container search URL.
function tmsDeepLinkUrl(row) {
  const wo = (row.tmsWoNumber || row.workOrderNumber || '').toUpperCase().trim();
  if (!wo) return null;
  // Type routing: M = import, X = export, V = van, B = brokerage
  let type = 'import';
  if (wo.includes('X')) type = 'export';
  else if (wo.includes('V')) type = 'van';
  else if (wo.includes('B')) type = 'brokerage';
  // else default 'import' (M is the most common)
  return `https://nglinnovation.net/bc-detail/document/${type}/${wo}`;
}

function tmsSearchUrl(row) {
  const ctn = (row.containerNumber || '').toUpperCase().trim();
  if (!ctn) return 'https://nglinnovation.net/main/imp';
  return `https://nglinnovation.net/main/imp?search=${encodeURIComponent(ctn)}`;
}

// ────────────────────────────────────────────────────────────────
// Banner severity + copy
// ────────────────────────────────────────────────────────────────
function pickBannerSeverity(rows, total) {
  const failed = rows.filter(r => isNeedsAttention(pillKeyFor(r, 'complete')));
  if (failed.length === 0) return { sev: 'ok', count: 0 };
  // Red threshold: any auth/network failure OR ≥25% failure
  const hasHard = failed.some(r => {
    const k = pillKeyFor(r, 'complete');
    return k === 'fail_auth' || k === 'fail_network' || k === 'fail_timeout';
  });
  const ratio = failed.length / Math.max(total, 1);
  if (hasHard || ratio >= 0.25) return { sev: 'error', count: failed.length };
  return { sev: 'warn', count: failed.length };
}

function bannerCopy(sev, failedCount, total, sentCount, startedAt, finishedAt) {
  const startStr = startedAt ? formatTime(startedAt) : '—';
  const finishStr = finishedAt ? formatTime(finishedAt) : '—';
  const dur = startedAt && finishedAt ? formatDuration(finishedAt - startedAt) : '—';

  if (sev === 'ok') {
    return {
      icon: '✓',
      title: `All ${total} invoices sent successfully`,
      sub: `Started ${startStr} · finished ${finishStr} · total ${dur}`,
      actions: [{ label: '📋 View Audit', onclick: 'invLoadAuditLog' }],
    };
  }
  if (sev === 'warn') {
    return {
      icon: '⚠',
      title: `${failedCount} invoice${failedCount === 1 ? '' : 's'} need a fix before they can send`,
      sub: `${sentCount} sent successfully · finished ${finishStr} · total ${dur}`,
      actions: [{ label: `↻ Retry the Fixed Ones (0)`, disabled: true, onclick: 'invHudRetryFixed' }],
    };
  }
  // error
  return {
    icon: '!',
    title: `${failedCount} invoice${failedCount === 1 ? '' : 's'} couldn't send — looks like something went wrong with QuickBooks`,
    sub: `${sentCount} invoices sent before the problem started. Try the action below, then Retry.`,
    actions: [{ label: `↻ Retry These (${failedCount})`, onclick: 'invHudRetryFailed' }],
  };
}

function formatTime(ts) {
  if (!ts) return '—';
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}
function formatDuration(ms) {
  if (!ms || ms < 0) return '—';
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}m ${r}s`;
}

// ────────────────────────────────────────────────────────────────
// Render
// ────────────────────────────────────────────────────────────────

// Re-render the whole HUD based on current state
export function invHudRender() {
  const stage = detectStage();
  const rows = invoiceState.invoices || [];
  const total = rows.length;

  // Progress strip — visible only in sending stage
  const ps = document.getElementById('invProgressStrip');
  if (ps) {
    if (stage === 'sending') ps.classList.add('is-active');
    else ps.classList.remove('is-active');
  }

  // Banner — visible only in complete stage
  const banner = document.getElementById('invAlertBanner');
  const card = document.getElementById('invResultsSection');
  if (banner && card) {
    banner.className = 'v64-alert-banner';
    if (stage === 'complete') {
      const sentRows = rows.filter(r => r.sendStatus === 'sent');
      const { sev, count } = pickBannerSeverity(rows, total);
      const finishedAt = sendState._finishedAt || Date.now();
      const copy = bannerCopy(sev, count, total, sentRows.length, sendState.startTime, finishedAt);
      banner.classList.add('is-active', sev);
      const icon = document.getElementById('invAlertIcon');
      const title = document.getElementById('invAlertTitle');
      const sub = document.getElementById('invAlertSub');
      const actions = document.getElementById('invAlertActions');
      if (icon) icon.textContent = copy.icon;
      if (title) title.textContent = copy.title;
      if (sub) sub.textContent = copy.sub;
      if (actions) {
        actions.innerHTML = (copy.actions || []).map(a => {
          const cls = ['v64-alert-btn'];
          if (a.primary) cls.push('primary');
          const onclick = a.onclick ? ` onclick="if(typeof window.${a.onclick}==='function'){window.${a.onclick}()}"` : '';
          return `<button type="button" class="${cls.join(' ')}"${a.disabled ? ' disabled' : ''}${onclick}>${escHtml(a.label)}</button>`;
        }).join('');
      }
      card.classList.add('has-banner');
    } else {
      card.classList.remove('has-banner');
    }
  }

  // Toolbar — visible only in complete stage
  const toolbar = document.getElementById('invToolbar');
  if (toolbar) {
    if (stage === 'complete') {
      toolbar.classList.remove('hidden');
      const finishedAt = sendState._finishedAt || Date.now();
      const start = sendState.startTime;
      const tbStart = document.getElementById('invTbStart');
      const tbTotal = document.getElementById('invTbTotal');
      const tbAvg = document.getElementById('invTbAvg');
      if (tbStart) tbStart.textContent = formatTime(start);
      if (tbTotal) tbTotal.textContent = (start && finishedAt) ? formatDuration(finishedAt - start) : '—';
      const processed = rows.filter(r => r.sendStatus && r.sendStatus !== 'not_sent').length;
      if (tbAvg) tbAvg.textContent = (start && finishedAt && processed > 0)
        ? formatDuration(Math.round((finishedAt - start) / processed)).replace(/\s/g, '')
        : '—';
    } else {
      toolbar.classList.add('hidden');
    }
  }

  // Tab counts
  let cNeeds = 0, cSent = 0;
  rows.forEach(r => {
    const k = pillKeyFor(r, stage);
    if (isNeedsAttention(k)) cNeeds++;
    if (r.sendStatus === 'sent') cSent++;
  });
  const elN = document.getElementById('invCNeeds'); if (elN) elN.textContent = cNeeds;
  const elS = document.getElementById('invCSent');  if (elS) elS.textContent = cSent;
  const elA = document.getElementById('invCAll');   if (elA) elA.textContent = total;

  // Auto-tab on stage transitions
  if (stage === 'uploaded' && _lastStage !== 'uploaded') {
    sendState.currentTab = cNeeds > 0 ? 'needs' : 'all';
  } else if (stage === 'complete' && _lastStage !== 'complete') {
    sendState.currentTab = cNeeds > 0 ? 'needs' : 'sent';
  }
  _lastStage = stage;

  applyActiveTab();
  renderRowsForActiveTab(stage);

  // Progress strip live update (Sending stage)
  if (stage === 'sending') {
    const sentRows = rows.filter(r => r.sendStatus === 'sent').length;
    const processed = rows.filter(r => r.sendStatus && r.sendStatus !== 'not_sent').length;
    const pct = total > 0 ? Math.round(sentRows / total * 100) : 0;
    const psBar = document.getElementById('invPsBar');
    const psFrac = document.getElementById('invPsFraction');
    const psPct = document.getElementById('invPsPct');
    if (psBar) psBar.style.width = pct + '%';
    if (psFrac) psFrac.textContent = `${sentRows} / ${total}`;
    if (psPct) psPct.textContent = `${pct}% complete`;
    const psStart = document.getElementById('invPsStart');
    const psElapsed = document.getElementById('invPsElapsed');
    const psRemaining = document.getElementById('invPsRemaining');
    const psAvg = document.getElementById('invPsAvg');
    const start = sendState.startTime;
    if (psStart) psStart.textContent = formatTime(start);
    if (start) {
      const elapsedMs = Date.now() - start;
      if (psElapsed) psElapsed.textContent = formatDuration(elapsedMs);
      if (processed > 0) {
        const avgMs = elapsedMs / processed;
        if (psAvg) psAvg.textContent = (avgMs / 1000).toFixed(1) + 's';
        const remainingMs = avgMs * (total - sentRows);
        if (psRemaining) psRemaining.textContent = '~' + formatDuration(remainingMs);
      } else {
        if (psAvg) psAvg.textContent = '—';
        if (psRemaining) psRemaining.textContent = '—';
      }
    }
  }
}

let _lastStage = null;

function applyActiveTab() {
  const tab = sendState.currentTab || 'needs';
  document.querySelectorAll('#invTabs .v64-tab').forEach(b => {
    b.classList.toggle('active', b.dataset.tab === tab);
  });
}

function renderRowsForActiveTab(stage) {
  const list = document.getElementById('invRowList');
  if (!list) return;
  const tab = sendState.currentTab || 'needs';
  const rows = invoiceState.invoices || [];
  let filtered;
  if (tab === 'sent') filtered = rows.filter(r => r.sendStatus === 'sent');
  else if (tab === 'all') filtered = rows;
  else filtered = rows.filter(r => isNeedsAttention(pillKeyFor(r, stage)));

  if (!filtered.length) {
    let icon, msg;
    if (tab === 'needs') {
      icon = '✓';
      msg = rows.length === 0
        ? 'Nothing flagged. Upload an Excel to get started.'
        : "Nothing to fix. All invoices look ready.";
    } else if (tab === 'sent') {
      icon = '—';
      msg = 'No invoices sent yet.';
    } else {
      icon = '📋';
      msg = 'Upload a TMS Excel to see invoices here.';
    }
    list.innerHTML = `<div class="v64-empty"><div class="v64-empty-icon">${icon}</div>${escHtml(msg)}</div>`;
    return;
  }

  list.innerHTML = filtered.map(row => {
    const pillKey = pillKeyFor(row, stage);
    const pill = pillKey ? PILL[pillKey] : null;
    const pillHtml = pill ? `<span class="v64-pill ${pill.severity}"><span class="dot"></span>${escHtml(pill.label)}</span>` : '';
    const needsResolve = isNeedsAttention(pillKey);
    return `
      <div class="v64-row" data-id="${escHtml(row.id || '')}">
        <div class="v64-row-inv">${renderInvoiceNumberHtml(row.invoiceNumber || '')}</div>
        <div class="v64-row-ctn">${renderContainerCellHtml(row)}</div>
        <div class="v64-row-cust">
          <span class="v64-cust-code">${escHtml(row.customerCode || '')}</span>
          ${escHtml(row.customerName || '')}
        </div>
        <div class="v64-row-status">${pillHtml}</div>
        <div class="v64-row-action">
          ${needsResolve ? '<button class="v64-row-resolve" type="button">Resolve →</button>' : ''}
        </div>
      </div>`;
  }).join('');

  // Wire row clicks to open detail panel
  list.querySelectorAll('.v64-row').forEach(el => {
    const id = el.dataset.id;
    el.addEventListener('click', () => invHudOpenResolve(id));
  });
}

// ────────────────────────────────────────────────────────────────
// Detail panel (Resolve flow)
// ────────────────────────────────────────────────────────────────
export function invHudOpenResolve(rowId) {
  const row = (invoiceState.invoices || []).find(r => String(r.id) === String(rowId));
  if (!row) return;
  const pillKey = pillKeyFor(row, detectStage());
  const hint = PILL_HINT[pillKey];

  document.querySelectorAll('#invRowList .v64-row').forEach(el => {
    el.classList.toggle('is-active', String(el.dataset.id) === String(rowId));
  });

  const titleEl = document.getElementById('invPanelTitle');
  const metaEl = document.getElementById('invPanelMeta');
  const bodyEl = document.getElementById('invPanelBody');
  if (!titleEl || !metaEl || !bodyEl) return;

  const pill = pillKey ? PILL[pillKey] : null;
  const pillHtml = pill ? `<span class="v64-pill ${pill.severity}"><span class="dot"></span>${escHtml(pill.label)}</span>` : '';
  titleEl.innerHTML = `Invoice ${escHtml(row.invoiceNumber || '(blank)')} ${pillHtml}`;
  metaEl.textContent = `Container ${row.containerNumber || '—'} · ${row.customerCode || ''}${row.customerCode && row.customerName ? ' — ' : ''}${row.customerName || ''}`;

  if (!hint) {
    bodyEl.innerHTML = `
      <div class="v64-panel-section">
        <div class="v64-panel-section-label">Status</div>
        <div class="v64-panel-section-body">${row.sendStatus === 'sent'
          ? 'This invoice was sent successfully — no action needed.'
          : 'No action needed for this row.'}</div>
      </div>`;
  } else {
    // Build primary + optional secondary button
    const buttons = [];
    const primaryOnclick = hint.tmsDeepLink
      ? `invHudOpenTMS('${escHtml(row.id)}')`
      : `invHudPrimaryAction('${escHtml(row.id)}','${escHtml(pillKey)}')`;
    buttons.push(`<button type="button" class="v64-panel-btn" onclick="${primaryOnclick}">${escHtml(hint.actionLabel)}</button>`);
    if (hint.secondaryAction) {
      buttons.push(`<button type="button" class="v64-panel-btn secondary" onclick="invHudSecondaryAction('${escHtml(row.id)}','${escHtml(pillKey)}')">${escHtml(hint.secondaryAction)}</button>`);
    }

    // Drop zone (POD-missing flow only)
    let dzHtml = '';
    if (hint.dropzone) {
      const dzLabel = hint.dropzoneLabel || 'Drop the file here for this send only.';
      dzHtml = `
        <div class="v64-dz-block">
          <div class="v64-dz-sublabel">${escHtml(dzLabel)}</div>
          <div class="v64-dropzone" data-id="${escHtml(row.id)}">
            <div class="v64-dz-icon">⬆</div>
            <div class="v64-dz-text">
              <div class="v64-dz-title">Drop the file here</div>
              <div class="v64-dz-sub">PDF, JPG, PNG · or click to browse</div>
            </div>
          </div>
        </div>`;
    }

    // Technical detail (for failed rows)
    let techHtml = '';
    if (row.errorMessage) {
      techHtml = `
        <details class="v64-tech">
          <summary>Technical detail (for support)</summary>
          <div class="v64-tech-body">${escHtml(row.errorMessage)}</div>
        </details>`;
    }

    bodyEl.innerHTML = `
      <div class="v64-panel-section">
        <div class="v64-panel-section-label">What's wrong</div>
        <div class="v64-panel-section-body">${escHtml(hint.whatWrong)}</div>
      </div>
      <div class="v64-panel-section">
        <div class="v64-panel-section-label">What to do</div>
        <div class="v64-panel-section-body">${escHtml(hint.whatTodo)}</div>
        <div class="v64-panel-action">
          ${buttons.join('')}
        </div>
        ${dzHtml}
      </div>
      ${techHtml}`;
  }

  document.getElementById('invDetailPanel').classList.add('is-open');
  document.getElementById('invPanelBackdrop').classList.add('is-open');
  sendState.activePanelInvoiceId = row.id;
}

export function invHudCloseResolve() {
  const panel = document.getElementById('invDetailPanel');
  const back = document.getElementById('invPanelBackdrop');
  if (panel) panel.classList.remove('is-open');
  if (back) back.classList.remove('is-open');
  document.querySelectorAll('#invRowList .v64-row').forEach(el => el.classList.remove('is-active'));
  sendState.activePanelInvoiceId = null;
}

// ────────────────────────────────────────────────────────────────
// Action wiring — Upload to TMS deep link + retry + primary actions
// ────────────────────────────────────────────────────────────────

function invHudOpenTMS(rowId) {
  const row = (invoiceState.invoices || []).find(r => String(r.id) === String(rowId));
  if (!row) return;
  const url = tmsDeepLinkUrl(row) || tmsSearchUrl(row);
  // window.open works in both browser and Electron — in Electron, it opens
  // links in the user's default browser by default (configured in main.js).
  window.open(url, '_blank', 'noopener,noreferrer');
}

function invHudPrimaryAction(rowId, pillKey) {
  // Stub for non-TMS primary actions. Hooks into existing handlers if available.
  if (pillKey === 'fail_auth' && typeof window.agentOpenQboLogin === 'function') {
    window.agentOpenQboLogin();
    return;
  }
  if (pillKey === 'fail_timeout' || pillKey === 'fail_network' || pillKey === 'fail_unknown') {
    invHudRetryRow(rowId);
    return;
  }
  if ((pillKey === 'val_no_customer' || pillKey === 'val_no_email' || pillKey === 'val_needs_rev')
      && typeof window.switchTool === 'function') {
    window.switchTool('customer');
    invHudCloseResolve();
    return;
  }
  // Fallback — no-op for unrecognized actions
  console.log('[invHud] Primary action not wired for pill:', pillKey);
}

function invHudSecondaryAction(rowId, pillKey) {
  // Currently only used for "Try fetching again" on TMS doc-missing pills
  invHudRetryRow(rowId);
}

function invHudRetryRow(rowId) {
  if (typeof window.invRetryInvoice === 'function') {
    window.invRetryInvoice(rowId);
  } else {
    console.log('[invHud] invRetryInvoice not available — retry not yet implemented for v2.71');
  }
}

function invHudRetryFixed() {
  // Stub — retry all rows whose validation has cleared since last send
  console.log('[invHud] Retry the fixed ones — not yet wired');
}
function invHudRetryFailed() {
  // Stub — retry all failed rows (used by red banner)
  console.log('[invHud] Retry these failed rows — not yet wired');
}

// ────────────────────────────────────────────────────────────────
// Live tick (during Sending stage) — re-renders progress strip
// ────────────────────────────────────────────────────────────────
let _tickHandle = null;
export function invHudStartTick() {
  if (_tickHandle) return;
  _tickHandle = setInterval(() => {
    if (!sendState.isRunning) {
      invHudStopTick();
      return;
    }
    invHudRender();
  }, 900);
}
export function invHudStopTick() {
  if (_tickHandle) {
    clearInterval(_tickHandle);
    _tickHandle = null;
  }
}

// ────────────────────────────────────────────────────────────────
// Init — wire static event listeners (tabs, panel close, etc.)
// ────────────────────────────────────────────────────────────────
let _inited = false;
export function invHudInit() {
  if (_inited) return;
  _inited = true;

  // Tab clicks
  const tabs = document.getElementById('invTabs');
  if (tabs) {
    tabs.addEventListener('click', e => {
      const t = e.target.closest('.v64-tab');
      if (!t) return;
      sendState.currentTab = t.dataset.tab;
      applyActiveTab();
      renderRowsForActiveTab(detectStage());
    });
  }

  // Side panel close
  const closeBtn = document.getElementById('invPanelCloseBtn');
  if (closeBtn) closeBtn.addEventListener('click', invHudCloseResolve);
  const backdrop = document.getElementById('invPanelBackdrop');
  if (backdrop) backdrop.addEventListener('click', invHudCloseResolve);

  // Escape closes panel
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && document.getElementById('invDetailPanel')?.classList.contains('is-open')) {
      invHudCloseResolve();
    }
  });

  // First render to populate empty state
  invHudRender();
}

// ────────────────────────────────────────────────────────────────
// Window exports for inline HTML handlers + cross-module access
// ────────────────────────────────────────────────────────────────
window.invHudRender = invHudRender;
window.invHudOpenResolve = invHudOpenResolve;
window.invHudCloseResolve = invHudCloseResolve;
window.invHudOpenTMS = invHudOpenTMS;
window.invHudPrimaryAction = invHudPrimaryAction;
window.invHudSecondaryAction = invHudSecondaryAction;
window.invHudRetryFixed = invHudRetryFixed;
window.invHudRetryFailed = invHudRetryFailed;
window.invHudStartTick = invHudStartTick;
window.invHudStopTick = invHudStopTick;
