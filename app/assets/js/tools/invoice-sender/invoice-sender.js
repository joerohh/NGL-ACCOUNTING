// ══════════════════════════════════════════════════════════════════
//  INVOICE SENDING TOOL — CSV parsing, table, send flow, audit
// ══════════════════════════════════════════════════════════════════
import { state, invoiceState, sendState } from '../../shared/state.js';
import { uid, escHtml, findColumnKey, CSV_ALIASES } from '../../shared/utils.js';
import { setupDrop } from '../../shared/dom-helpers.js';
import { invAddLog, invClearLog, invSetProgress, invToggleLog } from '../../shared/log.js';
import { agentBridge } from '../../shared/agent-client.js';
import { LS_SEND_HISTORY, LS_LAST_SEND_RUN } from '../../shared/constants.js';
import { fetchJobResultsForHistory } from '../session-history/session-history.js';

// CSV column aliases — imported from shared/utils.js (single source of truth)
const INV_CSV_ALIASES = CSV_ALIASES;

// ── Logging: invAddLog, invClearLog, invToggleLog, invSetProgress → shared/log.js ──

// ── Drop handlers (called directly from HTML attributes — guaranteed to work) ──
function invHandleCsvDrop(event) {
  event.preventDefault();
  event.stopPropagation();
  const zone = document.getElementById('invCsvDropZone');
  if (zone) zone.classList.remove('drag-over');
  const files = Array.from((event.dataTransfer || {}).files || []);
  const csv = files.find(function(f) { return /\.(xlsx|xls|csv)$/i.test(f.name); });
  if (csv) invHandleCsvFile(csv);
  else if (files.length) invAddLog('warning', 'Expected a .csv, .xlsx, or .xls file — got: ' + files.map(function(f){return f.name;}).join(', '));
}

// ── Drop Zone Init ──
let _invDropZonesReady = false;
export function invInitDropZones() {
  if (_invDropZonesReady) return;
  _invDropZonesReady = true;

  // CSV drop zone
  setupDrop('invCsvDropZone', function(files) {
    const csv = files.find(function(f) { return /\.(xlsx|xls|csv)$/i.test(f.name); });
    if (csv) invHandleCsvFile(csv);
    else invAddLog('warning', 'Expected a .csv, .xlsx, or .xls file');
  });

  // Also add click-to-browse on the CSV zone
  const csvZone = document.getElementById('invCsvDropZone');
  if (csvZone) {
    csvZone.addEventListener('click', function(e) {
      // Don't trigger if clicking the delete button inside loaded state
      if (e.target.closest('button')) return;
      document.getElementById('invCsvInput').click();
    });
  }

  // Prevent browser from opening files dropped outside drop zones
  const view = document.getElementById('invoiceSenderView');
  if (view) {
    view.addEventListener('dragover', function(e) { e.preventDefault(); });
    view.addEventListener('drop', function(e) {
      // Only handle if the drop wasn't caught by a specific drop zone
      if (!e.target.closest('.drop-zone')) {
        e.preventDefault();
        // Auto-route: Excel → CSV zone
        const files = Array.from(e.dataTransfer.files);
        const csv = files.find(function(f) { return /\.(xlsx|xls|csv)$/i.test(f.name); });
        if (csv) invHandleCsvFile(csv);
      }
    });
  }
}

// ── CSV Handling ──
function invHandleCsvInput(input) {
  if (input.files && input.files[0]) invHandleCsvFile(input.files[0]);
  input.value = '';
}

function invHandleCsvFile(file) {
  invAddLog('info', 'Parsing: ' + file.name);

  const reader = new FileReader();
  reader.onload = function(e) {
    try {
      const data = new Uint8Array(e.target.result);
      const workbook = XLSX.read(data, { type: 'array' });
      const sheet = workbook.Sheets[workbook.SheetNames[0]];
      const rows = XLSX.utils.sheet_to_json(sheet, { defval: '' });

      if (rows.length === 0) {
        invAddLog('error', 'No data rows found in file');
        return;
      }

      // Find columns using fuzzy matching
      const headers = Object.keys(rows[0]);
      const colMap = {};
      for (const [field, aliases] of Object.entries(INV_CSV_ALIASES)) {
        colMap[field] = findColumnKey(headers, aliases);
      }

      // Require invoice number column
      if (!colMap.invoiceNumber) {
        invAddLog('error', 'Could not find an Invoice Number column. Check your CSV headers.');
        return;
      }
      // Customer code is optional — warn but still load the file
      if (!colMap.customerCode) {
        invAddLog('warning', 'No Customer Code / BillTo column found — invoices will show as "No Match". You can still review and send manually.');
      }

      // Log found columns
      for (const [field, key] of Object.entries(colMap)) {
        if (key) invAddLog('info', 'Mapped: ' + field + ' → "' + key + '"');
      }
      if (!colMap.email) invAddLog('warning', 'No email column found — you can add emails manually in Review');
      if (!colMap.amount) invAddLog('warning', 'No amount column found');
      if (colMap.doSenderEmail) invAddLog('info', 'Found DO Sender Email column — OEC invoices will use it');

      // Parse rows into invoices
      invoiceState.invoices = [];
      let skipped = 0;
      for (const row of rows) {
        const invNum = String(colMap.invoiceNumber ? row[colMap.invoiceNumber] : '').trim();
        const custCode = String(colMap.customerCode ? row[colMap.customerCode] : '').trim();
        if (!invNum && !custCode) { skipped++; continue; }

        invoiceState.invoices.push({
          id: uid(),
          invoiceNumber: invNum,
          customerName: String(colMap.customerName ? row[colMap.customerName] : '').trim(),
          invoiceDate: String(colMap.invoiceDate ? row[colMap.invoiceDate] : '').trim(),
          dueDate: String(colMap.dueDate ? row[colMap.dueDate] : '').trim(),
          amount: String(colMap.amount ? row[colMap.amount] : '').trim(),
          email: String(colMap.email ? row[colMap.email] : '').trim(),
          poNumber: String(colMap.poNumber ? row[colMap.poNumber] : '').trim(),
          containerNumber: String(colMap.containerNumber ? row[colMap.containerNumber] : '').trim(),
          bolNumber: String(colMap.bolNumber ? row[colMap.bolNumber] : '').trim(),
          customerCode: custCode,
          subject: String(colMap.subject ? row[colMap.subject] : '').trim(),
          doSenderEmail: String(colMap.doSenderEmail ? row[colMap.doSenderEmail] : '').trim(),
          emailOverride: null,
          subjectOverride: null,
          // Customer profile enrichment (populated by invEnrichWithCustomerProfiles)
          customerMatch: null,
          resolvedEmails: [],
          resolvedCc: [],
          resolvedBcc: [],
          sendMethod: 'email',
          requiredDocs: [],
          customerActive: true,
          validationStatus: 'pending', // ready, no_match, portal, no_email
          sendStatus: null,    // not_sent, in_progress, sent, skipped, skipped_no_attachments, error
          sentAt: null,
          errorMessage: null,
        });
      }

      if (skipped > 0) invAddLog('warning', 'Skipped ' + skipped + ' empty rows');

      invoiceState.csvLoaded = true;
      invAddLog('success', 'Loaded ' + invoiceState.invoices.length + ' invoices from ' + file.name);

      // Enrich with customer profiles from localStorage
      invEnrichWithCustomerProfiles();

      // Restore send history from localStorage (persists across refresh)
      invRestoreSendHistory();

      // Update UI
      document.getElementById('invCsvDropContent').style.display = 'none';
      const loaded = document.getElementById('invCsvLoadedState');
      loaded.style.display = 'flex';
      document.getElementById('invCsvFileName').textContent = file.name;
      document.getElementById('invCsvFileSub').textContent = invoiceState.invoices.length + ' invoices loaded';
      document.getElementById('invCsvDropZone').classList.add('has-file');

      invRenderTable();
      invUpdateGenerateBtn();

    } catch (err) {
      invAddLog('error', 'Failed to parse file: ' + err.message);
    }
  };
  reader.readAsArrayBuffer(file);
}

function invRemoveCsv() {
  invoiceState.csvLoaded = false;
  invoiceState.invoices = [];
  invoiceState.selectedIds.clear();

  document.getElementById('invCsvDropContent').style.display = '';
  document.getElementById('invCsvLoadedState').style.display = 'none';
  document.getElementById('invCsvDropZone').classList.remove('has-file');

  invRenderTable();
  invUpdateSummary();
  invUpdateGenerateBtn();
  invAddLog('info', 'CSV removed');
}

// ── Customer Profile Enrichment ──
function invEnrichWithCustomerProfiles() {
  const allCustomers = agentBridge._custRead();
  let matched = 0, noMatch = 0, portal = 0, noEmail = 0, needsDoEmail = 0;

  for (const inv of invoiceState.invoices) {
    const code = (inv.customerCode || '').toUpperCase();
    if (!code) {
      inv.validationStatus = 'no_match';
      inv.customerMatch = null;
      noMatch++;
      continue;
    }

    const customer = allCustomers[code];
    if (!customer || customer.active === false) {
      inv.validationStatus = 'no_match';
      inv.customerMatch = null;
      noMatch++;
      continue;
    }

    inv.customerMatch = customer;
    inv.customerName = inv.customerName || customer.name || '';
    inv.resolvedEmails = customer.emails || [];
    inv.resolvedCc = customer.ccEmails || [];
    inv.resolvedBcc = customer.bccEmails || [];
    inv.sendMethod = customer.sendMethod || 'email';
    inv.requiredDocs = customer.requiredDocs || [];
    inv.customerActive = customer.active !== false;

    if (inv.sendMethod === 'portal_upload' || inv.sendMethod === 'portal') {
      inv.validationStatus = 'ready';
      portal++;
      matched++;
    } else if (inv.sendMethod === 'qbo_invoice_only_then_pod_email') {
      // OEC flow — DO sender email is optional (POD goes to static podEmailTo regardless)
      if (!inv.doSenderEmail) {
        needsDoEmail++;
      }
      inv.validationStatus = 'ready';
      matched++;
    } else if (inv.resolvedEmails.length === 0) {
      inv.validationStatus = 'no_email';
      noEmail++;
    } else {
      inv.validationStatus = 'ready';
      matched++;
    }
  }

  let logMsg = 'Customer matching: ' + matched + ' ready';
  if (portal > 0) logMsg += ', ' + portal + ' portal';
  if (needsDoEmail > 0) logMsg += ', ' + needsDoEmail + ' OEC (no DO email — optional)';
  if (noEmail > 0) logMsg += ', ' + noEmail + ' no email';
  if (noMatch > 0) logMsg += ', ' + noMatch + ' not found';
  invAddLog('info', logMsg);
}

// ── Subject Template ──
function invRenderSubject(template, inv) {
  return template
    .replace(/\{invoice_number\}/g, inv.invoiceNumber || '')
    .replace(/\{customer_name\}/g, inv.customerName || '')
    .replace(/\{amount\}/g, inv.amount || '')
    .replace(/\{due_date\}/g, inv.dueDate || '')
    .replace(/\{invoice_date\}/g, inv.invoiceDate || '')
    .replace(/\{po_number\}/g, inv.poNumber || '')
    .replace(/\{container_number\}/g, inv.containerNumber || '')
    .replace(/\{bol_number\}/g, inv.bolNumber || '')
    .trim();
}

function invUpdateSubjectTemplate(val) {
  invoiceState.subjectTemplate = val;
  invRenderTable();
}

// ── Summary Bar ──
function invUpdateSummary() {
  const bar = document.getElementById('invSummaryBar');
  if (invoiceState.invoices.length === 0) {
    bar.style.display = 'none';
    return;
  }

  const total = invoiceState.invoices.length;
  const ready = invoiceState.invoices.filter(function(i) { return i.validationStatus === 'ready'; }).length;
  const portal = invoiceState.invoices.filter(function(i) { return i.sendMethod === 'portal_upload' || i.sendMethod === 'portal'; }).length;
  const oec = invoiceState.invoices.filter(function(i) { return i.sendMethod === 'qbo_invoice_only_then_pod_email'; }).length;
  const noMatch = invoiceState.invoices.filter(function(i) { return i.validationStatus === 'no_match'; }).length;
  const noEmail = invoiceState.invoices.filter(function(i) { return i.validationStatus === 'no_email'; }).length;
  const selected = invoiceState.selectedIds.size;

  bar.style.display = 'flex';
  bar.innerHTML =
    '<div class="inv-summary-item"><strong>' + total + '</strong> invoices</div>' +
    '<div class="inv-summary-item" style="color:#16a34a;"><strong>' + ready + '</strong> ready</div>' +
    (portal > 0 ? '<div class="inv-summary-item" style="color:#d97706;"><strong>' + portal + '</strong> portal</div>' : '') +
    (oec > 0 ? '<div class="inv-summary-item" style="color:#ea580c;"><strong>' + oec + '</strong> OEC</div>' : '') +
    (noMatch > 0 ? '<div class="inv-summary-item" style="color:#dc2626;"><strong>' + noMatch + '</strong> no match</div>' : '') +
    (noEmail > 0 ? '<div class="inv-summary-item" style="color:#dc2626;"><strong>' + noEmail + '</strong> no email</div>' : '') +
    (selected > 0 ? '<div class="inv-summary-item" style="color:#ea580c;"><strong>' + selected + '</strong> selected</div>' : '');
}

// ── Table Rendering ──
function invRenderTable() {
  const tbody = document.getElementById('invTableBody');

  if (invoiceState.invoices.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; padding:50px 20px; color:#94a3b8;">' +
      '<div style="margin-bottom:8px;">' +
      '<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#d1d5db" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">' +
      '<rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="3" x2="9" y2="21"/>' +
      '</svg></div>Upload a TMS Excel to see invoices here</td></tr>';
    return;
  }

  // Apply send status filter if active
  const filterEl = document.getElementById('invSendFilter');
  const filterVal = filterEl ? filterEl.value : 'all';
  let displayInvoices = invoiceState.invoices;
  if (filterVal !== 'all') {
    displayInvoices = invoiceState.invoices.filter(function(inv) {
      if (filterVal === 'skipped') return inv.sendStatus === 'skipped' || inv.sendStatus === 'skipped_no_attachments';
      return inv.sendStatus === filterVal;
    });
  }

  let html = '';
  for (const inv of displayInvoices) {
    const subject = inv.resendSubject || inv.subject || inv.subjectOverride || invRenderSubject(invoiceState.subjectTemplate, inv);
    const isSelected = invoiceState.selectedIds.has(inv.id);

    // Customer column — show code + name from profile
    let customerHtml = '';
    if (inv.customerMatch) {
      customerHtml = '<span style="font-weight:600; color:#0f172a;">' + escHtml(inv.customerCode) + '</span>' +
        '<br><span style="font-size:0.75rem; color:#64748b;">' + escHtml(inv.customerMatch.name || '') + '</span>';
    } else if (inv.customerCode) {
      customerHtml = '<span style="font-weight:600; color:#dc2626;">' + escHtml(inv.customerCode) + '</span>' +
        '<br><span style="font-size:0.72rem; color:#dc2626;">Not found</span>';
    } else {
      customerHtml = '<span style="color:#94a3b8;">—</span>';
    }

    // Send method badge
    let methodHtml = '';
    if (inv.sendMethod === 'portal_upload' || inv.sendMethod === 'portal') {
      methodHtml = '<span class="status-badge" style="background:#fef3c7; color:#92400e; font-size:0.72rem;">Portal</span>';
    } else if (inv.sendMethod === 'qbo_invoice_only_then_pod_email') {
      methodHtml = '<span class="status-badge" style="background:#ffedd5; color:#9a3412; font-size:0.72rem;">QBO + POD</span>';
      if (inv.doSenderEmail) {
        methodHtml += '<br><span style="font-size:0.68rem; color:#64748b;" title="DO sender: ' + escHtml(inv.doSenderEmail) + '">DO: ' + escHtml(inv.doSenderEmail) + '</span>';
      }
    } else if (inv.resolvedEmails.length > 0) {
      methodHtml = '<span class="status-badge" style="background:#dcfce7; color:#166534; font-size:0.72rem;">Email</span>' +
        '<br><span style="font-size:0.68rem; color:#64748b;" title="' + escHtml(inv.resolvedEmails.join(', ')) + '">' +
        escHtml(inv.resolvedEmails[0]) + (inv.resolvedEmails.length > 1 ? ' +' + (inv.resolvedEmails.length - 1) : '') + '</span>';
    } else if (inv.customerMatch) {
      methodHtml = '<span class="status-badge" style="background:#fee2e2; color:#991b1b; font-size:0.72rem;">No Email</span>';
    } else {
      methodHtml = '<span style="color:#94a3b8;">—</span>';
    }

    // Status badge — show send status if available, otherwise validation status
    let statusHtml = '';
    if (inv.sendStatus) {
      switch (inv.sendStatus) {
        case 'not_sent':
          statusHtml = '<span class="status-badge status-not-sent">Not Sent</span>';
          break;
        case 'in_progress':
          statusHtml = '<span class="status-badge status-in-progress">In Progress</span>';
          break;
        case 'sent':
          statusHtml = '<span class="status-badge status-sent">Sent</span>';
          if (inv.sentAt) statusHtml += '<br><span style="font-size:0.68rem; color:#64748b;">' + inv.sentAt + '</span>';
          break;
        case 'sent_no_pod':
          statusHtml = '<span class="status-badge" style="background:#fef3c7; color:#92400e; border:1px solid #f59e0b;">Sent (No POD)</span>';
          if (inv.errorMessage) statusHtml += '<br><span style="font-size:0.68rem; color:#92400e;" title="' + escHtml(inv.errorMessage) + '">' + escHtml(inv.errorMessage.substring(0, 50)) + '</span>';
          break;
        case 'skipped_no_attachments':
          statusHtml = '<span class="status-badge status-skipped">No Attachments</span>';
          break;
        case 'skipped':
          statusHtml = '<span class="status-badge status-skipped">Skipped</span>';
          break;
        case 'error':
          statusHtml = '<span class="status-badge status-error">Error</span>';
          if (inv.errorMessage) statusHtml += '<br><span style="font-size:0.68rem; color:#991b1b;" title="' + escHtml(inv.errorMessage) + '">' + escHtml(inv.errorMessage.substring(0, 40)) + '</span>';
          break;
        default:
          statusHtml = '<span class="status-badge status-missing">' + escHtml(inv.sendStatus) + '</span>';
      }
    } else {
      switch (inv.validationStatus) {
        case 'ready':
          statusHtml = '<span class="status-badge status-ready">Ready</span>';
          break;
        case 'no_email':
          statusHtml = '<span class="status-badge" style="background:#fee2e2; color:#991b1b;">No Email</span>';
          break;
        case 'no_match':
          statusHtml = '<span class="status-badge" style="background:#fee2e2; color:#991b1b;">No Match</span>';
          break;
        default:
          statusHtml = '<span class="status-badge status-missing">Pending</span>';
      }
    }

    // Row background tint based on status
    let rowClass = isSelected ? 'selected' : '';
    let rowStyle = '';
    if (inv.validationStatus === 'no_match' || inv.validationStatus === 'no_email') {
      rowStyle = 'background:#fef2f2;';
    } else if (inv.sendMethod === 'portal_upload' || inv.sendMethod === 'portal') {
      rowStyle = 'background:#fffbeb;';
    } else if (inv.sendMethod === 'qbo_invoice_only_then_pod_email') {
      rowStyle = 'background:#fff7ed;';
    }

    html += '<tr class="' + rowClass + '" style="' + rowStyle + '">' +
      '<td style="padding-left:14px;"><input type="checkbox" ' + (isSelected ? 'checked' : '') +
        ' onchange="invToggleRow(\'' + inv.id + '\', this.checked)" /></td>' +
      '<td style="font-family:monospace; font-weight:600; color:#0f172a;">' + escHtml(inv.invoiceNumber) + '</td>' +
      '<td style="font-family:monospace; font-size:0.82rem; color:#334155;">' + escHtml(inv.containerNumber || '—') + '</td>' +
      '<td>' + customerHtml + '</td>' +
      '<td style="text-align:right; font-family:monospace; color:#0f172a;">' + escHtml(inv.amount || '—') + '</td>' +
      '<td>' + methodHtml + '</td>' +
      '<td style="color:#64748b; font-size:0.82rem; max-width:200px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="' + escHtml(subject) + '">' + escHtml(subject) + '</td>' +
      '<td>' + statusHtml + '</td>' +
      '</tr>';
  }

  tbody.innerHTML = html;
  invUpdateSummary();
}

// ── Selection ──
function invToggleRow(id, checked) {
  if (checked) invoiceState.selectedIds.add(id);
  else invoiceState.selectedIds.delete(id);
  invRenderTable();
}

function invToggleAll(checked) {
  if (checked) {
    for (const inv of invoiceState.invoices) invoiceState.selectedIds.add(inv.id);
  } else {
    invoiceState.selectedIds.clear();
  }
  invRenderTable();
}

// ── Generate Button State ──
export function invUpdateGenerateBtn() {
  const hasInvoices = invoiceState.invoices.length > 0;
  const sendBtn = document.getElementById('invSendQboBtn');
  if (sendBtn) sendBtn.disabled = !hasInvoices || !state.agentConnected;
}

// ── Review Modal ──
function invOpenReview(id) {
  const inv = invoiceState.invoices.find(function(i) { return i.id === id; });
  if (!inv) return;
  invoiceState.reviewingId = id;

  const subject = inv.resendSubject || inv.subjectOverride || invRenderSubject(invoiceState.subjectTemplate, inv);
  const email = inv.emailOverride || inv.email;

  document.getElementById('reviewModalTitle').textContent = 'Review — Invoice #' + inv.invoiceNumber;
  document.getElementById('reviewModalBody').innerHTML =
    // Invoice summary grid
    '<div style="display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:20px;">' +
      '<div><div class="modal-field-label">Invoice #</div><div style="font-size:0.95rem; font-weight:600; color:#0f172a; font-family:monospace;">' + escHtml(inv.invoiceNumber) + '</div></div>' +
      '<div><div class="modal-field-label">Customer</div><div style="font-size:0.95rem; font-weight:600; color:#0f172a;">' + escHtml(inv.customerName) + '</div></div>' +
      '<div><div class="modal-field-label">Amount</div><div style="font-size:0.95rem; font-weight:600; color:#0f172a;">' + escHtml(inv.amount || '—') + '</div></div>' +
      '<div><div class="modal-field-label">Due Date</div><div style="font-size:0.95rem; color:#475569;">' + escHtml(inv.dueDate || '—') + '</div></div>' +
    '</div>' +
    // Email field
    '<div style="margin-bottom:16px;">' +
      '<label class="modal-field-label">Email To</label>' +
      '<input id="reviewEmail" type="email" value="' + escHtml(email || '') + '" class="modal-input" placeholder="recipient@example.com" />' +
      (!email ? '<div style="font-size:0.75rem; color:#d97706; margin-top:4px;">No email found in CSV. Enter one manually.</div>' : '') +
    '</div>' +
    // Subject field
    '<div style="margin-bottom:20px;">' +
      '<label class="modal-field-label">Subject Line</label>' +
      '<input id="reviewSubject" type="text" value="' + escHtml(subject) + '" class="modal-input" />' +
    '</div>' +
    // OEC flow: DO sender email field
    (inv.sendMethod === 'qbo_invoice_only_then_pod_email' ?
      '<div style="margin-bottom:16px; padding:12px; background:#fff7ed; border:1px solid #fed7aa; border-radius:8px;">' +
        '<div style="font-size:0.75rem; font-weight:600; color:#9a3412; margin-bottom:8px;">QBO + POD Email Flow</div>' +
        '<div style="margin-bottom:10px;">' +
          '<label class="modal-field-label">DO Sender Email</label>' +
          '<input id="reviewDoSenderEmail" type="email" value="' + escHtml(inv.doSenderEmail || '') + '" class="modal-input" placeholder="do_sender@example.com" />' +
          (!inv.doSenderEmail ? '<div style="font-size:0.75rem; color:#64748b; margin-top:4px;">Optional — if provided, DO sender will also receive the POD email.</div>' : '') +
        '</div>' +
        '<div style="font-size:0.75rem; color:#78716c;">' +
          '<strong>POD will be sent to:</strong> ' + escHtml((inv.customerMatch && inv.customerMatch.podEmailTo || []).concat(inv.doSenderEmail ? [inv.doSenderEmail] : []).join(', ') || '—') +
          '<br><strong>CC:</strong> ' + escHtml((inv.customerMatch && inv.customerMatch.podEmailCc || []).join(', ') || 'ar@ngltrans.net') +
        '</div>' +
      '</div>'
    : '') +
    // Reference info
    '<div style="display:flex; gap:8px; flex-wrap:wrap;">' +
      (inv.poNumber ? '<span style="font-size:0.75rem; background:#f1f5f9; padding:3px 8px; border-radius:4px; color:#475569;">PO: ' + escHtml(inv.poNumber) + '</span>' : '') +
      (inv.containerNumber ? '<span style="font-size:0.75rem; background:#f1f5f9; padding:3px 8px; border-radius:4px; color:#475569;">Container: ' + escHtml(inv.containerNumber) + '</span>' : '') +
      (inv.bolNumber ? '<span style="font-size:0.75rem; background:#f1f5f9; padding:3px 8px; border-radius:4px; color:#475569;">BOL: ' + escHtml(inv.bolNumber) + '</span>' : '') +
    '</div>';

  document.getElementById('reviewModal').classList.add('open');
}

function invCloseReview() {
  invoiceState.reviewingId = null;
  document.getElementById('reviewModal').classList.remove('open');
}

function invSaveReview() {
  const inv = invoiceState.invoices.find(function(i) { return i.id === invoiceState.reviewingId; });
  if (!inv) return;

  const emailVal = document.getElementById('reviewEmail').value.trim();
  const subjectVal = document.getElementById('reviewSubject').value.trim();
  const defaultSubject = invRenderSubject(invoiceState.subjectTemplate, inv);

  inv.emailOverride = (emailVal !== inv.email) ? emailVal : null;
  inv.subjectOverride = (subjectVal !== defaultSubject) ? subjectVal : null;

  // Save DO sender email if OEC flow
  var doEmailInput = document.getElementById('reviewDoSenderEmail');
  if (doEmailInput) {
    var doVal = doEmailInput.value.trim();
    inv.doSenderEmail = doVal;
    // Re-validate: if OEC flow and now has DO email, upgrade to ready
    // OEC: DO email is optional — status stays 'ready' regardless
  }

  invCloseReview();
  invRenderTable();
  invAddLog('info', 'Updated Invoice #' + inv.invoiceNumber);
}

// Close modal on Escape key
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape' && invoiceState.reviewingId) invCloseReview();
});

// Close modal on backdrop click
document.getElementById('reviewModal').addEventListener('click', function(e) {
  if (e.target === this) invCloseReview();
});

// ── Clear All (Invoice Sender) ──
function invClearAll() {
  invoiceState.csvLoaded = false;
  invoiceState.invoices = [];
  invoiceState.selectedIds.clear();
  invoiceState.reviewingId = null;

  // Reset CSV UI
  document.getElementById('invCsvDropContent').style.display = '';
  document.getElementById('invCsvLoadedState').style.display = 'none';
  document.getElementById('invCsvDropZone').classList.remove('has-file');

  // Reset table and summary
  invRenderTable();
  invUpdateSummary();
  invUpdateGenerateBtn();
  invAddLog('info', 'All cleared — ready for new job');
}

// ── Responsive Layout (Invoice Sender) ──
function invApplyResponsive() {
  const grid = document.getElementById('invControlsGrid');
  if (!grid) return;
  if (window.innerWidth < 900) {
    grid.style.gridTemplateColumns = '1fr';
  } else {
    grid.style.gridTemplateColumns = '1fr 1fr';
  }
}
window.addEventListener('resize', invApplyResponsive);


// ══════════════════════════════════════════════════════════════════
//  INVOICE SENDING — AGENT INTEGRATION
// ══════════════════════════════════════════════════════════════════

// ── Send Status Persistence (localStorage) ──
function invSaveSendStatus(invoiceNumber, status, extra) {
  try {
    const history = JSON.parse(localStorage.getItem(LS_SEND_HISTORY) || '{}');
    history[invoiceNumber] = {
      sendStatus: status,
      sentAt: extra.sentAt || null,
      error: extra.errorMessage || null,
      updatedAt: new Date().toISOString(),
    };
    localStorage.setItem(LS_SEND_HISTORY, JSON.stringify(history));
  } catch (e) { /* localStorage full or unavailable */ }
}

function invRestoreSendHistory() {
  try {
    const history = JSON.parse(localStorage.getItem(LS_SEND_HISTORY) || '{}');
    const thirtyDaysAgo = Date.now() - (30 * 24 * 60 * 60 * 1000);
    let cleaned = false;
    // Clean old entries + restore matching invoices
    for (const invNum in history) {
      const entry = history[invNum];
      if (entry.updatedAt && new Date(entry.updatedAt).getTime() < thirtyDaysAgo) {
        delete history[invNum];
        cleaned = true;
        continue;
      }
      const inv = invoiceState.invoices.find(function(i) { return i.invoiceNumber === invNum; });
      if (inv) {
        inv.sendStatus = entry.sendStatus;
        inv.sentAt = entry.sentAt;
        inv.errorMessage = entry.error;
      }
    }
    if (cleaned) localStorage.setItem(LS_SEND_HISTORY, JSON.stringify(history));
    // Show filter if any invoice has a sendStatus
    if (invoiceState.invoices.some(function(i) { return i.sendStatus; })) {
      const filterWrap = document.getElementById('invSendFilterWrap');
      if (filterWrap) filterWrap.style.display = '';
      invUpdateSendStatusBar();
      invUpdateResendBtn();
    }
  } catch (e) { /* ignore */ }
}

function invSaveLastRunSummary(summary) {
  try {
    localStorage.setItem(LS_LAST_SEND_RUN, JSON.stringify({
      timestamp: new Date().toISOString(),
      sent: summary.sent,
      skipped: summary.skipped,
      errors: summary.errors,
      mismatches: summary.mismatches,
      missingDocs: summary.missingDocs || 0,
      noAttachments: summary.noAttachments || 0,
      total: summary.total,
    }));
  } catch (e) { /* ignore */ }
}

function invUpdateSendStatusBar() {
  const bar = document.getElementById('invSendStatusBar');
  if (!bar) return;
  const hasSendStatus = invoiceState.invoices.some(function(i) { return i.sendStatus; });
  if (!hasSendStatus) { bar.style.display = 'none'; return; }

  const counts = { sent: 0, sent_no_pod: 0, not_sent: 0, in_progress: 0, skipped: 0, skipped_no_attachments: 0, error: 0 };
  invoiceState.invoices.forEach(function(inv) {
    if (inv.sendStatus && counts.hasOwnProperty(inv.sendStatus)) counts[inv.sendStatus]++;
  });

  bar.style.display = 'flex';
  bar.innerHTML =
    '<div class="inv-summary-item" style="color:#16a34a;"><strong>' + counts.sent + '</strong> sent</div>' +
    (counts.sent_no_pod > 0 ? '<div class="inv-summary-item" style="color:#92400e;"><strong>' + counts.sent_no_pod + '</strong> sent (no POD)</div>' : '') +
    (counts.in_progress > 0 ? '<div class="inv-summary-item" style="color:#d97706;"><strong>' + counts.in_progress + '</strong> in progress</div>' : '') +
    (counts.not_sent > 0 ? '<div class="inv-summary-item" style="color:#dc2626;"><strong>' + counts.not_sent + '</strong> not sent</div>' : '') +
    (counts.skipped + counts.skipped_no_attachments > 0 ? '<div class="inv-summary-item" style="color:#64748b;"><strong>' + (counts.skipped + counts.skipped_no_attachments) + '</strong> skipped</div>' : '') +
    (counts.error > 0 ? '<div class="inv-summary-item" style="color:#991b1b;"><strong>' + counts.error + '</strong> errors</div>' : '');
}

function invResendAll() {
  const sentInvoices = invoiceState.invoices.filter(inv =>
    inv.sendStatus === 'sent' || inv.sendStatus === 'sent_no_pod'
  );
  if (sentInvoices.length === 0) {
    alert('No previously sent invoices to resend.');
    return;
  }
  if (!confirm('Reset ' + sentInvoices.length + ' sent invoice(s) so they can be sent again?')) return;
  sentInvoices.forEach(inv => {
    inv.sendStatus = null;
    inv.sentAt = null;
    inv.errorMessage = null;
    inv.isResend = true;
    // Force the subject to [NGL_INV_REVISED] so Gmail creates a new thread
    inv.resendSubject = '[NGL_INV_REVISED] ' + inv.invoiceNumber + ' - Container#' + inv.containerNumber + ' (Revised)';
  });
  // Clear from localStorage history too
  try {
    const history = JSON.parse(localStorage.getItem(LS_SEND_HISTORY) || '{}');
    sentInvoices.forEach(inv => { delete history[inv.invoiceNumber]; });
    localStorage.setItem(LS_SEND_HISTORY, JSON.stringify(history));
  } catch (e) { /* ignore */ }
  invRenderTable();
  invUpdateSendStatusBar();
  invUpdateResendBtn();
  invAddLog('info', 'Cleared sent status for ' + sentInvoices.length + ' invoice(s). Ready to send again.');
}

function invUpdateResendBtn() {
  const btn = document.getElementById('invResendAllBtn');
  if (!btn) return;
  const hasSent = invoiceState.invoices.some(inv =>
    inv.sendStatus === 'sent' || inv.sendStatus === 'sent_no_pod'
  );
  btn.style.display = hasSent ? '' : 'none';
}

function invToggleTestLimitVisibility() {
  const checked = document.getElementById('invTestModeToggle')?.checked;
  const wrap = document.getElementById('invTestLimitWrap');
  if (wrap) wrap.style.display = checked ? 'flex' : 'none';
}

async function invSendViaQBO() {
  if (sendState.isRunning) {
    invAddLog('warning', 'A send job is already running. Wait for it to finish or pause it first.');
    return;
  }
  if (!state.agentConnected) {
    invAddLog('error', 'Agent is not connected. Start the agent server first.');
    return;
  }

  // Only send invoices that are ready AND not already sent
  let readyInvoices = invoiceState.invoices.filter(inv =>
    inv.validationStatus === 'ready' && inv.sendStatus !== 'sent' && inv.sendStatus !== 'sent_no_pod'
  );
  if (readyInvoices.length === 0) {
    // Check if all ready invoices were already sent (retry scenario)
    const alreadySent = invoiceState.invoices.filter(inv =>
      inv.validationStatus === 'ready' && (inv.sendStatus === 'sent' || inv.sendStatus === 'sent_no_pod')
    );
    if (alreadySent.length > 0) {
      const msg = 'All ready invoices have already been sent (' + alreadySent.length + ' total).\n\nDo you want to resend them?';
      if (confirm(msg)) {
        alreadySent.forEach(inv => {
          inv.sendStatus = '';
          inv.isResend = true;
          inv.resendSubject = '[NGL_INV_REVISED] ' + inv.invoiceNumber + ' - Container#' + inv.containerNumber + ' (Revised)';
        });
        invAddLog('info', 'Resending ' + alreadySent.length + ' invoices...');
        readyInvoices = alreadySent;
      } else {
        return;
      }
    } else {
      const noMatch = invoiceState.invoices.filter(inv => inv.validationStatus === 'no_match').length;
      const noEmail = invoiceState.invoices.filter(inv => inv.validationStatus === 'no_email').length;
      const pending = invoiceState.invoices.filter(inv => inv.validationStatus === 'pending').length;
      let msg = 'No invoices are ready to send.';
      if (noMatch > 0) msg += '\n• ' + noMatch + ' have no customer match (check Customer Code column)';
      if (noEmail > 0) msg += '\n• ' + noEmail + ' are missing email addresses';
      if (pending > 0) msg += '\n• ' + pending + ' are still pending validation';
      invAddLog('error', msg.replace(/\n/g, ' '));
      alert(msg);
      return;
    }
  }

  // Use selected or all ready
  const selectedIds = invoiceState.selectedIds;
  const toSend = selectedIds.size > 0
    ? readyInvoices.filter(inv => selectedIds.has(inv.id))
    : readyInvoices;

  if (toSend.length === 0) {
    invAddLog('warning', 'No ready invoices selected for sending.');
    return;
  }

  // Customer profiles are already synced to the agent on startup and via
  // the Customer Manager — no need to re-import on every send click.

  // Build request — include subject from Excel (or fallback template)
  // resendSubject is set by invResendAll() — forces [NGL_INV_REVISED] prefix
  let invoicePayload = toSend.map(inv => ({
    invoiceNumber: inv.invoiceNumber,
    containerNumber: inv.containerNumber,
    customerCode: inv.customerCode,
    amount: inv.amount || '',
    subject: inv.resendSubject || inv.subject || invRenderSubject(invoiceState.subjectTemplate, inv),
    doSenderEmail: inv.doSenderEmail || '',
    isResend: inv.isResend || false,
  }));

  const testMode = document.getElementById('invTestModeToggle')?.checked || false;

  // In test mode, limit to the first N invoices
  if (testMode) {
    const testLimit = parseInt(document.getElementById('invTestLimitInput')?.value) || 5;
    const totalCount = invoicePayload.length;
    if (testLimit < totalCount) {
      invoicePayload = invoicePayload.slice(0, testLimit);
      invAddLog('info', '[TEST RUN] Limiting to first ' + testLimit + ' of ' + totalCount + ' invoices');
    }
  }

  const modeLabel = testMode ? ' [TEST MODE — approval required]' : '';

  invAddLog('info', 'Starting QBO send for ' + invoicePayload.length + ' invoices...' + modeLabel);

  // Reset send state
  sendState.jobId = null;
  sendState.isRunning = true;
  sendState.testMode = testMode;
  sendState.results = [];
  sendState.sent = 0;
  sendState.skipped = 0;
  sendState.errors = 0;
  sendState.mismatches = 0;
  sendState.missingDocs = 0;
  sendState.startTime = Date.now();
  sendState.completedCount = 0;

  // Clear previous send statuses for invoices about to be sent
  invoicePayload.forEach(function(p) {
    const inv = invoiceState.invoices.find(function(i) { return i.invoiceNumber === p.invoiceNumber; });
    if (inv) { inv.sendStatus = null; inv.sentAt = null; inv.errorMessage = null; }
  });
  invRenderTable();

  // Show progress panel
  invShowSendProgress();

  const result = await agentBridge.sendInvoices(invoicePayload, testMode);
  if (result.error) {
    invAddLog('error', 'Failed to start send job: ' + result.error);
    sendState.isRunning = false;
    return;
  }

  sendState.jobId = result.jobId;
  invAddLog('info', 'Send job started: ' + result.jobId + ' (' + result.total + ' invoices)');

  // Stream events
  sendState.eventSource = agentBridge.streamProgress(result.jobId, invHandleSendEvent);
}

function invUpdateInvoiceSendStatus(invoiceNumber, status, extra) {
  extra = extra || {};
  const inv = invoiceState.invoices.find(function(i) { return i.invoiceNumber === invoiceNumber; });
  if (inv) {
    inv.sendStatus = status;
    if (extra.sentAt) inv.sentAt = extra.sentAt;
    if (extra.errorMessage) inv.errorMessage = extra.errorMessage;
    invSaveSendStatus(invoiceNumber, status, extra);
    invRenderTable();
  }
  // Show filter dropdown once any invoice has a sendStatus
  const filterWrap = document.getElementById('invSendFilterWrap');
  if (filterWrap && invoiceState.invoices.some(function(i) { return i.sendStatus; })) {
    filterWrap.style.display = '';
  }
  invUpdateSendStatusBar();
  invUpdateResendBtn();
}

// ── Send event handlers (dispatch map) ──

const _sendEventHandlers = {
  // ── Job lifecycle ──
  send_job_started(event) {
    invAddLog('info', 'Send job started — processing ' + event.total + ' invoices');
    invoiceState.invoices.forEach(function(inv) {
      if (inv.validationStatus === 'ready' && !inv.sendStatus) {
        inv.sendStatus = 'not_sent';
        invSaveSendStatus(inv.invoiceNumber, 'not_sent', {});
      }
    });
    invRenderTable();
  },
  login_required(event) {
    invAddLog('error', 'QBO SESSION EXPIRED — please log in and retry');
    sendState.isRunning = false;
  },
  job_paused(event) {
    invAddLog('warning', 'Send job paused');
    sendState.isRunning = false;
  },
  send_job_complete(event) {
    sendState.isRunning = false;
    invAddLog('success', '═══ SEND JOB COMPLETE ═══');
    invAddLog('info', '  Sent: ' + event.sent + ' | Skipped: ' + event.skipped + ' | Errors: ' + event.errors +
      ' | Mismatches: ' + event.mismatches + ' | Missing Docs: ' + (event.missingDocs || 0) +
      ' | No Attachments: ' + (event.noAttachments || 0));
    invSaveLastRunSummary(event);
    invShowSendResults(event);
    // Push to session history
    if (sendState.jobId) fetchJobResultsForHistory(sendState.jobId, 'send', event);
  },
  connection_warning(event) {
    invAddLog('warning', event.message);
    invShowConnectionWarning(event.message);
  },
  connection_lost(event) {
    sendState.isRunning = false;
    invAddLog('error', event.message);
    invShowConnectionLost(event.message);
  },

  // ── Standard QBO send flow ──
  invoice_start(event) {
    invAddLog('info', '[' + (event.index + 1) + '/' + event.total + '] Processing: ' + event.invoiceNumber);
    invUpdateSendProgress(event.index, event.total);
    invUpdateInvoiceSendStatus(event.invoiceNumber, 'in_progress');
  },
  searching_invoice(event) {
    invAddLog('info', '  Searching QBO for: ' + event.invoiceNumber);
    invSetStepText('Searching QBO for ' + event.invoiceNumber + '...');
  },
  invoice_not_found(event) {
    sendState.errors++;
    sendState.completedCount++;
    invAddLog('error', '  NOT FOUND in QBO: ' + event.invoiceNumber);
    invUpdateInvoiceSendStatus(event.invoiceNumber, 'error', { errorMessage: 'Not found in QBO' });
  },
  verifying_invoice(event) {
    invAddLog('info', '  Verifying: ' + event.invoiceNumber + ' / Container ' + event.containerNumber);
    invSetStepText('Verifying invoice details...');
  },
  invoice_mismatch(event) {
    sendState.mismatches++;
    sendState.completedCount++;
    invAddLog('error', '  MISMATCH: ' + event.reason);
    invUpdateInvoiceSendStatus(event.invoiceNumber, 'error', { errorMessage: 'Mismatch: ' + event.reason });
  },
  invoice_amount_warning(event) {
    invAddLog('warning', '  NOTE: ' + event.note);
  },
  checking_attachments(event) {
    invAddLog('info', '  Checking attachments...');
    invSetStepText('Checking attachments...');
  },
  invoice_missing_docs(event) {
    sendState.missingDocs++;
    sendState.completedCount++;
    invAddLog('warning', '  MISSING DOCS: ' + event.missing.join(', '));
    invUpdateInvoiceSendStatus(event.invoiceNumber, 'skipped_no_attachments', { errorMessage: 'Missing: ' + event.missing.join(', ') });
  },
  opening_send_form(event) {
    invAddLog('info', '  Opening Review & Send form...');
    invSetStepText('Opening send form...');
  },
  filling_send_form(event) {
    invAddLog('info', '  Filling: To=' + event.toEmails.join(', ') + ' | Subject=' + event.subject);
    invSetStepText('Filling email form...');
  },
  awaiting_approval(event) {
    invAddLog('warning', '  WAITING FOR APPROVAL — review the form in QBO browser');
    invShowInlineApproval(event);
  },
  approval_confirmed(event) {
    invAddLog('info', '  Approved — sending...');
  },
  sending_invoice(event) {
    invAddLog('info', '  Clicking Send...');
    invSetStepText('Sending...');
  },
  invoice_sent(event) {
    sendState.sent++;
    sendState.completedCount++;
    invAddLog('success', '  SENT: ' + event.invoiceNumber + ' → ' + event.toEmails.join(', '));
    invUpdateInvoiceSendStatus(event.invoiceNumber, 'sent', { sentAt: new Date().toLocaleTimeString() });
    invSetStepText('');
  },
  invoice_skipped(event) {
    sendState.skipped++;
    sendState.completedCount++;
    if (event.reason === 'duplicate') {
      invAddLog('warning', '  DUPLICATE: ' + event.invoiceNumber + ' — already sent recently, skipping');
      invUpdateInvoiceSendStatus(event.invoiceNumber, 'sent', { errorMessage: 'Duplicate — already sent' });
    } else if (event.reason === 'no_attachments') {
      invAddLog('warning', '  SKIPPED: ' + event.invoiceNumber + ' (' + event.reason + ')');
      invUpdateInvoiceSendStatus(event.invoiceNumber, 'skipped_no_attachments');
    } else {
      invAddLog('warning', '  SKIPPED: ' + event.invoiceNumber + ' (' + event.reason + ')');
      invUpdateInvoiceSendStatus(event.invoiceNumber, 'skipped', { errorMessage: event.reason });
    }
  },
  invoice_error(event) {
    sendState.errors++;
    sendState.completedCount++;
    invAddLog('error', '  ERROR: ' + event.error);
    invUpdateInvoiceSendStatus(event.invoiceNumber, 'error', { errorMessage: event.error });
  },

  // ── OEC flow events ──
  oec_qbo_sending(event) {
    invAddLog('info', '  [OEC] Sending invoice via QBO (invoice only)...');
  },
  oec_qbo_sent(event) {
    invAddLog('success', '  [OEC] QBO invoice sent (invoice attachment only)');
  },
  oec_downloading_pod(event) {
    invAddLog('info', '  [OEC] Downloading POD from QBO...');
  },
  oec_sending_pod_email(event) {
    invAddLog('info', '  [OEC] Sending POD email to: ' + (event.to || []).join(', '));
  },
  oec_pod_email_sent(event) {
    sendState.sent++;
    sendState.completedCount++;
    invAddLog('success', '  [OEC] POD email sent successfully' +
      (event.doSenderIncluded ? ' (D/O Sender CC: ' + event.doSenderEmail + ')' : ' (no D/O Sender in CC)'));
    invUpdateInvoiceSendStatus(event.invoiceNumber, 'sent', { sentAt: new Date().toLocaleTimeString() });
  },
  oec_pod_email_failed(event) {
    invAddLog('error', '  [OEC] POD email failed: ' + event.error);
    invUpdateInvoiceSendStatus(event.invoiceNumber, 'error', { errorMessage: 'QBO sent but POD email failed: ' + event.error });
  },
  oec_do_sender_resolved(event) {
    invAddLog('success', '  [OEC] D/O Sender: ' + event.doSenderEmail + ' (from ' + event.doSenderSource + ')');
  },
  oec_do_sender_missing(event) {
    invAddLog('warning', '  [OEC] D/O Sender not found' + (event.failureReason ? ' — ' + event.failureReason : ' — missing from TMS and CSV'));
  },
  do_sender_from_cache(event) {
    invAddLog('info', '  [OEC] D/O sender from cache: ' + (event.doSenderEmail || '') + ' (previous successful lookup)');
  },

  // ── TMS flow events ──
  tms_fetching_pod(event) {
    invAddLog('info', '  [TMS] ' + (event.message || 'Searching TMS for POD...') + (event.containerNumber ? ' (container: ' + event.containerNumber + ')' : ''));
  },
  tms_pod_downloaded(event) {
    invAddLog('success', '  [TMS] POD downloaded from TMS: ' + (event.fileName || ''));
  },
  tms_pod_not_found(event) {
    invAddLog('warning', '  [TMS] POD not found in TMS for container ' + (event.containerNumber || ''));
  },
  tms_login_required(event) {
    invAddLog('warning', '  [TMS] ' + (event.message || 'TMS login required — please log in now'));
    invSetStepText('Waiting for TMS login...');
    invShowTmsLoginPrompt();
  },
  tms_logged_in(event) {
    invAddLog('success', '  [TMS] ' + (event.message || 'TMS login successful'));
    invSetStepText('TMS connected — fetching POD...');
    invDismissTmsLoginPrompt();
  },
  tms_login_timeout(event) {
    invAddLog('warning', '  [TMS] ' + (event.message || 'TMS login timed out — continuing without POD'));
    invSetStepText('Continuing without TMS...');
    invDismissTmsLoginPrompt();
  },
  tms_not_available(event) {
    invAddLog('warning', '  [TMS] ' + (event.message || 'TMS browser not available — D/O sender lookup skipped'));
  },
  tms_not_logged_in(event) {
    invAddLog('warning', '  [TMS] ' + (event.message || 'TMS not logged in — D/O sender lookup skipped'));
  },
  tms_fetching_do_sender(event) {
    invAddLog('info', '  [TMS] Fetching D/O sender from TMS for ' + (event.containerNumber || ''));
  },
  tms_do_sender_extraction_failed(event) {
    invAddLog('error', '  [TMS] D/O sender extraction failed: ' + (event.message || 'unknown reason'));
  },

  // ── Portal flow events ──
  portal_downloading(event) {
    invAddLog('info', '  [Portal] Downloading invoice + POD from QBO...');
  },
  portal_merging(event) {
    invAddLog('info', '  [Portal] Merging invoice and POD into one PDF...');
  },
  portal_uploading(event) {
    invAddLog('info', '  [Portal] Uploading to portal...');
  },
  portal_upload_success(event) {
    sendState.sent++;
    sendState.completedCount++;
    invAddLog('success', '  [Portal] Successfully uploaded to portal');
    invUpdateInvoiceSendStatus(event.invoiceNumber, 'sent', { sentAt: new Date().toLocaleTimeString() });
  },
  portal_upload_failed(event) {
    sendState.errors++;
    sendState.completedCount++;
    invAddLog('error', '  [Portal] Upload failed: ' + event.error);
    invUpdateInvoiceSendStatus(event.invoiceNumber, 'error', { errorMessage: 'Portal upload failed: ' + event.error });
  },

  // ── Retry events ──
  retrying_attachments(event) {
    invAddLog('info', '  Retrying attachment check...');
  },
};

function invHandleSendEvent(event) {
  const handler = _sendEventHandlers[event.type];
  if (handler) handler(event);
  invUpdateSendTally();
}

function invShowSendProgress() {
  let panel = document.getElementById('invSendProgressPanel');
  if (!panel) {
    // Create panel above the table
    const container = document.getElementById('invTableContainer');
    if (!container) return;
    panel = document.createElement('div');
    panel.id = 'invSendProgressPanel';
    panel.className = 'send-progress-panel';
    container.parentElement.insertBefore(panel, container);
  }
  panel.style.display = '';
  const testBadge = sendState.testMode
    ? ' <span style="background:#fef3c7; color:#92400e; font-size:0.72rem; padding:2px 8px; border-radius:4px; font-weight:600; margin-left:8px;">TEST MODE</span>'
    : '';
  panel.innerHTML = `
    <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:8px;">
      <strong style="font-size:0.9rem;">Sending Invoices${testBadge}</strong>
      <button class="btn btn-secondary" style="padding:5px 12px; font-size:0.78rem;" onclick="invPauseSendJob()">Pause</button>
    </div>
    <div style="background:#e2e8f0; border-radius:6px; height:8px; overflow:hidden;">
      <div id="invSendProgressBar" style="background:linear-gradient(90deg, #ea580c, #f97316); height:100%; width:0%; transition:width 0.3s;"></div>
    </div>
    <div id="invSendProgressText" style="font-size:0.8rem; color:#64748b; margin-top:6px;">Starting...</div>
    <div id="invSendStepText" style="font-size:0.78rem; color:#94a3b8; margin-top:2px;"></div>
    <div id="invSendTimeInfo" style="font-size:0.78rem; color:#94a3b8; margin-top:4px;"></div>
    <div class="send-tally" id="invSendTally">
      <span class="send-tally-item sent">Sent: 0</span>
      <span class="send-tally-item skipped">Skipped: 0</span>
      <span class="send-tally-item error">Errors: 0</span>
      <span class="send-tally-item mismatch">Mismatches: 0</span>
    </div>
  `;
}

function invUpdateSendProgress(current, total) {
  const bar = document.getElementById('invSendProgressBar');
  const text = document.getElementById('invSendProgressText');
  if (bar) bar.style.width = Math.round((current / total) * 100) + '%';
  if (text) text.textContent = 'Processing ' + (current + 1) + ' of ' + total + '...';

  // Elapsed time + ETA
  var timeEl = document.getElementById('invSendTimeInfo');
  if (timeEl && sendState.startTime) {
    var elapsed = Date.now() - sendState.startTime;
    var elapsedStr = _fmtDuration(elapsed);
    var etaStr = '';
    if (sendState.completedCount > 0) {
      var avgMs = elapsed / sendState.completedCount;
      var remaining = (total - sendState.completedCount) * avgMs;
      etaStr = ' \u2022 ~' + _fmtDuration(remaining) + ' remaining';
    }
    timeEl.textContent = 'Elapsed: ' + elapsedStr + etaStr;
  }
}

function _fmtDuration(ms) {
  var totalSec = Math.round(ms / 1000);
  if (totalSec < 60) return totalSec + 's';
  var m = Math.floor(totalSec / 60);
  var s = totalSec % 60;
  if (m < 60) return m + 'm ' + s + 's';
  var h = Math.floor(m / 60);
  m = m % 60;
  return h + 'h ' + m + 'm';
}

function invSetStepText(text) {
  var el = document.getElementById('invSendStepText');
  if (el) el.textContent = text;
}

function invUpdateSendTally() {
  const tally = document.getElementById('invSendTally');
  if (!tally) return;
  tally.innerHTML = `
    <span class="send-tally-item sent">Sent: ${sendState.sent}</span>
    <span class="send-tally-item skipped">Skipped: ${sendState.skipped}</span>
    <span class="send-tally-item error">Errors: ${sendState.errors}</span>
    <span class="send-tally-item mismatch">Mismatches: ${sendState.mismatches}</span>
  `;
}

function invShowSendResults(summary) {
  const panel = document.getElementById('invSendProgressPanel');
  if (!panel) return;

  // Calculate timing stats
  var timeHtml = '';
  if (sendState.startTime) {
    var totalMs = Date.now() - sendState.startTime;
    var totalStr = _fmtDuration(totalMs);
    var totalProcessed = (summary.sent || 0) + (summary.skipped || 0) + (summary.errors || 0) +
        (summary.mismatches || 0) + (summary.missingDocs || 0) + (summary.noAttachments || 0);
    var avgStr = totalProcessed > 0 ? _fmtDuration(totalMs / totalProcessed) + '/invoice' : '';
    timeHtml = '<div style="margin-top:10px; font-size:0.82rem; color:#64748b;">' +
      'Total time: <strong>' + totalStr + '</strong>' +
      (avgStr ? ' &bull; Average: <strong>' + avgStr + '</strong>' : '') +
      '</div>';
  }

  panel.innerHTML = `
    <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:12px;">
      <strong style="font-size:0.95rem;">Send Complete</strong>
      <button class="btn btn-secondary" style="padding:5px 12px; font-size:0.78rem;" onclick="this.closest('.send-progress-panel').style.display='none'">Dismiss</button>
    </div>
    <div style="display:flex; gap:20px; flex-wrap:wrap;">
      <div style="text-align:center;">
        <div style="font-size:1.5rem; font-weight:700; color:#16a34a;">${summary.sent}</div>
        <div style="font-size:0.75rem; color:#64748b;">Sent</div>
      </div>
      <div style="text-align:center;">
        <div style="font-size:1.5rem; font-weight:700; color:#d97706;">${summary.skipped}</div>
        <div style="font-size:0.75rem; color:#64748b;">Skipped</div>
      </div>
      <div style="text-align:center;">
        <div style="font-size:1.5rem; font-weight:700; color:#dc2626;">${summary.errors}</div>
        <div style="font-size:0.75rem; color:#64748b;">Errors</div>
      </div>
      <div style="text-align:center;">
        <div style="font-size:1.5rem; font-weight:700; color:#dc2626;">${summary.mismatches}</div>
        <div style="font-size:0.75rem; color:#64748b;">Mismatches</div>
      </div>
      <div style="text-align:center;">
        <div style="font-size:1.5rem; font-weight:700; color:#d97706;">${summary.missingDocs || 0}</div>
        <div style="font-size:0.75rem; color:#64748b;">Missing Docs</div>
      </div>
      <div style="text-align:center;">
        <div style="font-size:1.5rem; font-weight:700; color:#64748b;">${summary.noAttachments || 0}</div>
        <div style="font-size:0.75rem; color:#64748b;">No Attachments</div>
      </div>
    </div>
    ${timeHtml}
    <div style="margin-top:12px; display:flex; gap:8px;">
      <button class="btn btn-secondary" style="font-size:0.82rem;" onclick="invLoadAuditLog()">View Audit Log</button>
      <button class="btn btn-secondary" style="font-size:0.82rem;" onclick="agentBridge.exportAuditLog()">Download Report (CSV)</button>
    </div>
  `;
}

function invShowConnectionWarning(message) {
  const panel = document.getElementById('invSendProgressPanel');
  if (!panel) return;
  // Update the progress text to show reconnecting status
  const text = document.getElementById('invSendProgressText');
  if (text) {
    text.innerHTML = '<span style="color:#d97706; font-weight:600;">⚠ ' + message + '</span>';
  }
}

function invShowConnectionLost(message) {
  const panel = document.getElementById('invSendProgressPanel');
  if (!panel) return;
  panel.innerHTML = `
    <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:12px;">
      <strong style="font-size:0.95rem; color:#dc2626;">Connection Lost</strong>
      <button class="btn btn-secondary" style="padding:5px 12px; font-size:0.78rem;" onclick="this.closest('.send-progress-panel').style.display='none'">Dismiss</button>
    </div>
    <div style="padding:12px; background:#fef2f2; border:1px solid #fecaca; border-radius:8px; color:#991b1b; font-size:0.85rem;">
      <strong>The connection to the agent server was lost.</strong><br>
      <span style="font-size:0.8rem; color:#b91c1c;">${message}</span>
      <div style="margin-top:10px; font-size:0.8rem; color:#64748b;">
        Progress so far: Sent ${sendState.sent} | Skipped ${sendState.skipped} | Errors ${sendState.errors}
      </div>
    </div>
    <div style="margin-top:10px; display:flex; gap:8px;">
      <button class="btn btn-primary" style="font-size:0.82rem;" onclick="invSendViaQBO()">Retry Send</button>
      <button class="btn btn-secondary" style="font-size:0.82rem;" onclick="invLoadAuditLog()">View Audit Log</button>
    </div>
  `;
}

function invShowInlineApproval(event) {
  const panel = document.getElementById('invSendProgressPanel');
  if (!panel) return;

  // Remove any previous approval section
  const existing = document.getElementById('invApprovalSection');
  if (existing) existing.remove();

  // Flow-specific theming
  const flow = event.flowType || 'qbo_standard';
  let borderColor, bgColor, dotColor, titleColor, borderDetail, hintBg, hintBorder;
  let title, hint, approveLabel;

  if (flow === 'oec_pod_email') {
    borderColor = '#ea580c'; bgColor = '#fff7ed'; dotColor = '#ea580c'; titleColor = '#9a3412';
    borderDetail = '#ffedd5'; hintBg = '#fff7ed'; hintBorder = '#fed7aa';
    title = 'OEC POD Email Ready — Review Recipients';
    hint = 'The QBO invoice was already sent. This will send the POD as a separate email via Gmail.';
    approveLabel = 'Send POD Email';
  } else if (flow === 'portal_upload') {
    borderColor = '#d97706'; bgColor = '#fffbeb'; dotColor = '#d97706'; titleColor = '#92400e';
    borderDetail = '#fef3c7'; hintBg = '#fffbeb'; hintBorder = '#fde68a';
    title = 'Portal Upload Ready — Review Before Uploading';
    hint = 'Invoice + POD have been merged into one PDF. This will upload it to the portal.';
    approveLabel = 'Upload to Portal';
  } else {
    borderColor = '#16a34a'; bgColor = '#f0fdf4'; dotColor = '#16a34a'; titleColor = '#15803d';
    borderDetail = '#dcfce7'; hintBg = '#fffbeb'; hintBorder = '#fde68a';
    title = 'Invoice Email Ready — Review Before Sending';
    hint = 'This will send the invoice + attachments via Gmail. Review recipients and subject above before approving.';
    approveLabel = 'Approve &amp; Send';
  }

  const section = document.createElement('div');
  section.id = 'invApprovalSection';
  section.style.cssText = `margin-top:12px; border:2px solid ${borderColor}; border-radius:10px; padding:16px; background:${bgColor}; animation: approvalPulse 1.5s ease-in-out infinite;`;
  section.innerHTML = `
    <style>
      @keyframes approvalPulse {
        0%, 100% { box-shadow: 0 0 0 0 ${borderColor}4d; }
        50% { box-shadow: 0 0 12px 4px ${borderColor}40; }
      }
    </style>
    <div style="display:flex; align-items:center; gap:8px; margin-bottom:10px;">
      <div style="width:10px; height:10px; border-radius:50%; background:${dotColor}; animation: approvalPulse 1s ease-in-out infinite;"></div>
      <strong style="font-size:0.88rem; color:${titleColor};">${title}</strong>
      <span style="font-size:0.75rem; color:#64748b; margin-left:auto;">Invoice ${event.index + 1} of ${event.total}</span>
    </div>
    <div style="background:#fff; border-radius:8px; padding:12px 14px; margin-bottom:12px; font-size:0.8rem; line-height:1.7; border:1px solid ${borderDetail};">
      <div><strong>Invoice:</strong> ${escHtml(event.invoiceNumber)}</div>
      <div><strong>Container:</strong> ${escHtml(event.containerNumber)}</div>
      <div><strong>Customer:</strong> ${escHtml(event.customerCode)}</div>
      <div><strong>${flow === 'portal_upload' ? 'Portal Client:' : 'To:'}</strong> ${escHtml((event.toEmails || []).join(', '))}</div>
      ${flow === 'oec_pod_email'
        ? '<div style="margin-top:6px;"><strong>CC:</strong> <span style="font-size:0.7rem; color:#64748b; margin-left:4px;">(editable for OEC)</span></div>' +
          '<input id="invApprovalCcInput" type="text" value="' + escHtml((event.ccEmails || []).join(', ')) + '" style="width:100%; padding:7px 10px; border:1px solid #d1d5db; border-radius:6px; font-size:0.8rem; margin-top:2px; box-sizing:border-box;" />' +
          '<div style="margin-top:4px; font-size:0.7rem; color:#94a3b8; line-height:1.5;">' +
            'Sources: ' + (event.ccEmails || []).map(function(e) {
              return '<span style="background:#f1f5f9; padding:1px 5px; border-radius:3px; margin-right:3px;">' + escHtml(e) +
                (event.doSenderEmail && e === event.doSenderEmail ? ' <span style="color:#059669;">(D/O Sender)</span>' : ' <span style="color:#64748b;">(Customer)</span>') +
              '</span>';
            }).join('') +
          '</div>'
        : ((event.ccEmails || []).length ? '<div><strong>CC:</strong> ' + escHtml(event.ccEmails.join(', ')) + '</div>' : '')
      }
      ${event.bccEmails && event.bccEmails.length ? '<div><strong>BCC:</strong> ' + escHtml(event.bccEmails.join(', ')) + '</div>' : ''}
      ${flow === 'oec_pod_email' ? (event.doSenderMissing
        ? '<div style="margin-top:8px; padding:8px 10px; background:#fffbeb; border:1px solid #fde68a; border-radius:6px; color:#92400e; font-size:0.78rem; line-height:1.5;">' +
          '<strong style="color:#d97706;">&#9888; D/O Sender not found</strong>' +
          (event.tmsFailureReason ? ' — ' + escHtml(event.tmsFailureReason) : ' — missing from TMS and CSV') +
          '<br><span style="font-size:0.72rem;">You can manually type the D/O sender email in the CC field above before sending.</span>' +
          '</div>'
        : '<div style="margin-top:4px;"><strong>D/O Sender:</strong> ' + escHtml(event.doSenderEmail || '') + ' <span style="display:inline-flex; align-items:center; gap:3px; margin-left:6px; padding:1px 7px; border-radius:4px; font-size:0.7rem; font-weight:600; background:#d1fae5; color:#065f46; border:1px solid #6ee7b7;">&#10003; From ' + escHtml(event.doSenderSource || '') + '</span></div>'
      ) : ''}
      <div><strong>Subject:</strong> ${escHtml(event.subject)}</div>
      <div><strong>Attachments:</strong> ${(event.attachmentsFound || []).length ? escHtml(event.attachmentsFound.join(', ')) : '<span style="color:#d97706;">None detected</span>'}${event.podSource ? ' <span style="display:inline-flex; align-items:center; gap:3px; margin-left:6px; padding:1px 7px; border-radius:4px; font-size:0.7rem; font-weight:600; background:' + (event.podSource === 'QBO' ? '#dbeafe; color:#1e40af; border:1px solid #93c5fd' : '#d1fae5; color:#065f46; border:1px solid #6ee7b7') + ';">' + (event.podSource === 'QBO' ? '&#9745;' : '&#9745;') + ' Found in ' + escHtml(event.podSource) + '</span>' : ''}</div>
      ${event.emailBody ? '<div style="margin-top:10px; padding:10px 12px; background:#f8fafc; border:1px solid #e2e8f0; border-radius:6px;"><strong style="display:block; margin-bottom:6px;">Email Body Preview:</strong><pre style="white-space:pre-wrap; word-wrap:break-word; font-family:inherit; font-size:0.78rem; color:#334155; margin:0;">' + escHtml(event.emailBody) + '</pre></div>' : ''}
    </div>
    <div style="font-size:0.75rem; color:#64748b; margin-bottom:12px; background:${hintBg}; border:1px solid ${hintBorder}; border-radius:6px; padding:8px 10px;">
      ${hint}
    </div>
    <div style="display:flex; gap:10px;">
      <button id="invInlineApproveBtn" onclick="invApprovalDecision(true)" style="flex:1; padding:11px 20px; border-radius:8px; border:none; background:${borderColor}; color:#fff; font-size:0.9rem; font-weight:700; cursor:pointer; transition:all 0.15s;">
        ${approveLabel}
      </button>
      <button id="invInlineSkipBtn" onclick="invApprovalDecision(false)" style="padding:11px 20px; border-radius:8px; border:1px solid #e2e8f0; background:#fff; color:#64748b; font-size:0.9rem; font-weight:600; cursor:pointer; transition:all 0.15s;">
        Skip
      </button>
    </div>
  `;
  panel.appendChild(section);

  // Scroll the approval section into view
  section.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

async function invApprovalDecision(approve) {
  // Read editable CC field before removing the section
  var ccOverride = null;
  var ccInput = document.getElementById('invApprovalCcInput');
  if (ccInput) {
    var rawCc = ccInput.value.trim();
    ccOverride = rawCc
      ? rawCc.split(',').map(function(e) { return e.trim(); }).filter(Boolean)
      : [];
  }

  const section = document.getElementById('invApprovalSection');
  if (section) section.remove();

  if (!sendState.jobId) return;

  if (approve) {
    if (ccOverride !== null) {
      invAddLog('info', '  You approved — sending with CC: ' + (ccOverride.length ? ccOverride.join(', ') : '(none)'));
    } else {
      invAddLog('info', '  You approved — sending now...');
    }
    var payload = ccOverride !== null ? { ccOverride: ccOverride } : undefined;
    const res = await agentBridge.approveSend(sendState.jobId, payload);
    if (res.error) invAddLog('error', '  Failed to approve: ' + res.error);
  } else {
    invAddLog('warning', '  You skipped this invoice');
    const res = await agentBridge.skipSend(sendState.jobId);
    if (res.error) invAddLog('error', '  Failed to skip: ' + res.error);
  }
}

function invShowTmsLoginPrompt() {
  const panel = document.getElementById('invSendProgressPanel');
  if (!panel) return;

  // Remove any previous TMS prompt
  invDismissTmsLoginPrompt();

  const section = document.createElement('div');
  section.id = 'invTmsLoginPrompt';
  section.style.cssText = 'margin-top:12px; border:2px solid #d97706; border-radius:10px; padding:16px; background:#fffbeb; animation: approvalPulse 1.5s ease-in-out infinite;';
  section.innerHTML = `
    <div style="display:flex; align-items:center; gap:8px; margin-bottom:10px;">
      <div style="width:10px; height:10px; border-radius:50%; background:#d97706; animation: approvalPulse 1s ease-in-out infinite;"></div>
      <strong style="font-size:0.88rem; color:#92400e;">TMS Login Required</strong>
    </div>
    <div style="font-size:0.82rem; color:#78350f; line-height:1.6; margin-bottom:10px;">
      The agent needs to access TMS to download the POD.<br>
      A TMS login window has been opened — <strong>please log in now</strong>.<br>
      <span style="color:#92400e;">The agent is waiting (2 min timeout).</span>
    </div>
  `;
  panel.appendChild(section);
  section.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function invDismissTmsLoginPrompt() {
  const el = document.getElementById('invTmsLoginPrompt');
  if (el) el.remove();
}

async function invPauseSendJob() {
  if (!sendState.jobId) return;
  try {
    await agentBridge._authFetch(agentBridge.baseUrl + '/jobs/' + sendState.jobId + '/pause', { method: 'POST' });
    invAddLog('warning', 'Pause requested...');
  } catch (e) {
    invAddLog('error', 'Failed to pause: ' + e.message);
  }
}


// ══════════════════════════════════════════════════════════════════
//  AUDIT LOG PANEL
// ══════════════════════════════════════════════════════════════════

async function invLoadAuditLog() {
  let panel = document.getElementById('invAuditPanel');
  if (!panel) {
    // Create panel at bottom of invoice sender
    const view = document.getElementById('invoiceSenderView');
    if (!view) return;
    panel = document.createElement('div');
    panel.id = 'invAuditPanel';
    panel.style.cssText = 'max-width:1280px; margin:0 auto; padding:0 24px 40px;';
    view.appendChild(panel);
  }
  panel.style.display = '';
  panel.innerHTML = '<div style="text-align:center; padding:20px; color:#94a3b8;">Loading audit log...</div>';

  const data = await agentBridge.getAuditLog({ limit: 100 });
  if (data.error) {
    panel.innerHTML = '<div class="panel-card" style="text-align:center; color:#dc2626;">Failed to load audit log: ' + escHtml(data.error) + '</div>';
    return;
  }

  const entries = data.entries || [];
  let html = `
    <div class="panel-card" style="padding:16px;">
      <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:12px;">
        <div class="section-label" style="margin:0;">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#ea580c" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
          Audit Log (${data.total} entries)
        </div>
        <div style="display:flex; gap:6px;">
          <button class="btn btn-secondary" style="padding:5px 12px; font-size:0.78rem;" onclick="agentBridge.exportAuditLog()">Export CSV</button>
          <button class="btn btn-secondary" style="padding:5px 12px; font-size:0.78rem;" onclick="document.getElementById('invAuditPanel').style.display='none'">Close</button>
        </div>
      </div>`;

  if (entries.length === 0) {
    html += '<div style="text-align:center; padding:30px; color:#94a3b8;">No audit entries yet. Send invoices to see the log here.</div>';
  } else {
    html += `<div style="overflow-x:auto;"><table class="audit-table"><thead><tr>
      <th>Time</th><th>Invoice #</th><th>Container</th><th>Customer</th><th>Status</th><th>Emails</th><th>Subject</th>
    </tr></thead><tbody>`;
    for (const e of entries) {
      const time = e.timestamp ? new Date(e.timestamp).toLocaleString() : '';
      const statusCls = 'status-' + (e.status || 'error');
      html += `<tr>
        <td style="white-space:nowrap; font-size:0.78rem;">${escHtml(time)}</td>
        <td><strong>${escHtml(e.invoiceNumber || '')}</strong></td>
        <td>${escHtml(e.containerNumber || '')}</td>
        <td>${escHtml(e.customerCode || '')}</td>
        <td><span class="status-badge ${statusCls}">${escHtml((e.status || '').toUpperCase())}</span></td>
        <td style="font-size:0.78rem;">${escHtml((e.toEmails || []).join(', '))}</td>
        <td style="font-size:0.78rem; max-width:200px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${escHtml(e.subject || '')}</td>
      </tr>`;
    }
    html += '</tbody></table></div>';
  }

  html += '</div>';
  panel.innerHTML = html;
}

// ── Window assignments for inline HTML handlers ──
window.invHandleCsvDrop = invHandleCsvDrop;
window.invHandleCsvInput = invHandleCsvInput;
window.invRemoveCsv = invRemoveCsv;
window.invUpdateSubjectTemplate = invUpdateSubjectTemplate;
window.invToggleRow = invToggleRow;
window.invToggleAll = invToggleAll;
window.invOpenReview = invOpenReview;
window.invCloseReview = invCloseReview;
window.invSaveReview = invSaveReview;
window.invRenderTable = invRenderTable;
window.invClearAll = invClearAll;
window.invSendViaQBO = invSendViaQBO;
window.invPauseSendJob = invPauseSendJob;
window.invApprovalDecision = invApprovalDecision;
window.invLoadAuditLog = invLoadAuditLog;
window.invToggleTestLimitVisibility = invToggleTestLimitVisibility;
window.invResendAll = invResendAll;
