'use strict';
// ══════════════════════════════════════════════════════════════════
//  ██ CUSTOMER MANAGEMENT ██
// ══════════════════════════════════════════════════════════════════

async function custLoadCustomers() {
  const search = (document.getElementById('custSearchInput')?.value || '').trim();
  const activeOnly = document.getElementById('custActiveFilter')?.value === 'active';
  const data = await agentBridge.getCustomers(search, activeOnly);
  custRenderTableData(data.customers);
  document.getElementById('custCount').textContent = data.total + ' customer' + (data.total !== 1 ? 's' : '');
}

function custRenderTable() {
  custLoadCustomers();
}

function _custMethodBadge(method) {
  if (method === 'qbo_invoice_only_then_pod_email')
    return ' <span class="tag-pill" style="background:#ffedd5;color:#9a3412;font-size:0.7rem;">QBO + POD</span>';
  if (method === 'portal_upload' || method === 'portal')
    return ' <span class="tag-pill" style="background:#fef3c7;color:#92400e;font-size:0.7rem;">PORTAL</span>';
  return '';
}

function custRenderTableData(customers) {
  const tbody = document.getElementById('custTableBody');
  const empty = document.getElementById('custEmptyState');

  if (!customers || customers.length === 0) {
    tbody.innerHTML = '';
    empty.style.display = '';
    return;
  }
  empty.style.display = 'none';

  const DOC_CLASSES = { pod: 'doc-pod', invoice: 'doc-invoice', bol: 'doc-bol', pol: 'doc-pol' };

  tbody.innerHTML = customers.map(c => `
    <tr>
      <td><strong style="font-family:monospace; font-size:0.82rem;">${escHtml(c.code)}</strong></td>
      <td>${escHtml(c.name)}${_custMethodBadge(c.sendMethod)}</td>
      <td>${(c.emails || []).map(e => '<span class="tag-pill email-tag">' + escHtml(e) + '</span>').join(' ')}</td>
      <td>${(c.sendMethod === 'qbo_invoice_only_then_pod_email' || c.sendMethod === 'portal_upload')
        ? '<span class="tag-pill" style="background:#f1f5f9;color:#94a3b8;">N/A</span>'
        : (c.requiredDocs || []).length === 0
        ? '<span class="tag-pill" style="background:#ecfdf5;color:#065f46;">ALL</span>'
        : (c.requiredDocs || []).map(d => {
        if (d.includes('/')) {
          const display = d.split('/').map(p => p.trim().toUpperCase()).join(' / ');
          return '<span class="tag-pill" style="background:#fef3c7;color:#92400e;">' + escHtml(display) + '</span>';
        }
        return '<span class="tag-pill ' + (DOC_CLASSES[d] || '') + '">' + escHtml(d.toUpperCase()) + '</span>';
      }).join(' ')}</td>
      <td style="max-width:150px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:#94a3b8;">${escHtml(c.notes || '')}</td>
      <td>${c.active !== false ? '<span class="status-badge status-ready">Active</span>' : '<span class="status-badge status-missing">Inactive</span>'}</td>
      <td style="text-align:right; white-space:nowrap;">
        <button class="btn btn-secondary" style="padding:5px 10px; font-size:0.78rem;" onclick="custEdit('${escHtml(c.code)}')">Edit</button>
        ${c.active !== false ?
          `<button class="btn btn-danger" style="padding:5px 10px; font-size:0.78rem; margin-left:4px;" onclick="custDelete('${escHtml(c.code)}')">Delete</button>` :
          `<button class="btn btn-success" style="padding:5px 10px; font-size:0.78rem; margin-left:4px;" onclick="custReactivate('${escHtml(c.code)}')">Activate</button>`
        }
      </td>
    </tr>
  `).join('');
}

// ── Send Method show/hide ──
function custSendMethodChanged() {
  const method = document.getElementById('custSendMethod').value;
  const podSection = document.getElementById('custPodEmailSection');
  const portalSection = document.getElementById('custPortalSection');
  const hint = document.getElementById('custSendMethodHint');
  const docRulesSection = document.getElementById('custDocRulesSection');
  const docInfoOec = document.getElementById('custDocInfoOec');
  const docInfoPortal = document.getElementById('custDocInfoPortal');

  podSection.style.display = (method === 'qbo_invoice_only_then_pod_email') ? '' : 'none';
  portalSection.style.display = (method === 'portal_upload') ? '' : 'none';

  // Show/hide doc rules vs info panels based on method
  const isStandard = (method === 'email' || method === 'qbo_standard');
  docRulesSection.style.display = isStandard ? '' : 'none';
  docInfoOec.style.display = (method === 'qbo_invoice_only_then_pod_email') ? '' : 'none';
  docInfoPortal.style.display = (method === 'portal_upload') ? '' : 'none';

  if (method === 'qbo_invoice_only_then_pod_email') {
    hint.textContent = 'Send ONLY the invoice via QBO, then email the POD separately.';
  } else if (method === 'portal_upload') {
    hint.textContent = 'Merge invoice + POD and upload to a carrier portal (no QBO email).';
  } else {
    hint.textContent = 'Send all attachments via QuickBooks Online email.';
  }
}

function _custClearMethodFields() {
  // Clear POD email fields
  custClearTags('custPodEmailToTags');
  custClearTags('custPodEmailCcTags');
  document.getElementById('custPodEmailSubject').value = 'POD — {container_number} — {customer_name}';
  document.getElementById('custPodEmailBody').value = 'Please find the attached Proof of Delivery for container {container_number}.';
  // Clear portal fields
  document.getElementById('custPortalUrl').value = '';
  document.getElementById('custPortalClient').value = '';
  // Reset visibility
  document.getElementById('custPodEmailSection').style.display = 'none';
  document.getElementById('custPortalSection').style.display = 'none';
  document.getElementById('custDocRulesSection').style.display = '';
  document.getElementById('custDocInfoOec').style.display = 'none';
  document.getElementById('custDocInfoPortal').style.display = 'none';
  document.getElementById('custSendMethodHint').textContent = 'Send all attachments via QuickBooks Online email.';
}

// ── Modal open/close ──
function custOpenModal(code) {
  custEditingCode = code || null;
  document.getElementById('custModalTitle').textContent = code ? 'Edit Customer' : 'Add Customer';
  document.getElementById('custSaveBtn').textContent = code ? 'Update Customer' : 'Save Customer';

  // Clear form
  document.getElementById('custCodeInput').value = '';
  document.getElementById('custNameInput').value = '';
  document.getElementById('custNotesInput').value = '';
  document.getElementById('custCodeInput').disabled = false;
  document.getElementById('custSendMethod').value = 'email';
  custClearTags('custEmailTags');
  custClearTags('custCcTags');
  custClearTags('custBccTags');
  _custClearMethodFields();
  custClearDocRules();

  document.getElementById('custModal').classList.add('open');
}

function custCloseModal() {
  document.getElementById('custModal').classList.remove('open');
  custEditingCode = null;
}

async function custEdit(code) {
  const customer = await agentBridge.getCustomer(code);
  if (!customer) return;

  custEditingCode = code;
  document.getElementById('custModalTitle').textContent = 'Edit Customer';
  document.getElementById('custSaveBtn').textContent = 'Update Customer';
  document.getElementById('custCodeInput').value = customer.code;
  document.getElementById('custCodeInput').disabled = true;
  document.getElementById('custNameInput').value = customer.name;
  document.getElementById('custNotesInput').value = customer.notes || '';

  // Populate email tags
  custClearTags('custEmailTags');
  (customer.emails || []).forEach(e => custAddTag('custEmailTags', e));

  // Populate CC tags
  custClearTags('custCcTags');
  (customer.ccEmails || []).forEach(e => custAddTag('custCcTags', e));

  // Populate BCC tags
  custClearTags('custBccTags');
  (customer.bccEmails || []).forEach(e => custAddTag('custBccTags', e));

  // Set send method + conditional fields
  const method = customer.sendMethod || 'email';
  document.getElementById('custSendMethod').value = method;
  _custClearMethodFields();

  if (method === 'qbo_invoice_only_then_pod_email') {
    (customer.podEmailTo || []).forEach(e => custAddTag('custPodEmailToTags', e));
    (customer.podEmailCc || []).forEach(e => custAddTag('custPodEmailCcTags', e));
    document.getElementById('custPodEmailSubject').value = customer.podEmailSubject || 'POD — {container_number} — {customer_name}';
    document.getElementById('custPodEmailBody').value = customer.podEmailBody || 'Please find the attached Proof of Delivery for container {container_number}.';
  } else if (method === 'portal_upload') {
    document.getElementById('custPortalUrl').value = customer.portalUrl || '';
    document.getElementById('custPortalClient').value = customer.portalClient || '';
  }
  custSendMethodChanged();

  // Populate doc rules
  custSetDocRules(customer.requiredDocs || []);

  document.getElementById('custModal').classList.add('open');
}

async function custSave() {
  const code = document.getElementById('custCodeInput').value.trim().toUpperCase();
  const name = document.getElementById('custNameInput').value.trim();
  const notes = document.getElementById('custNotesInput').value.trim();
  const emails = custGetTags('custEmailTags');
  const ccEmails = custGetTags('custCcTags');
  const bccEmails = custGetTags('custBccTags');
  const sendMethod = document.getElementById('custSendMethod').value;
  // For OEC/portal, required docs don't apply — the routing handles it
  const isStandard = (sendMethod === 'email' || sendMethod === 'qbo_standard');
  const requiredDocs = isStandard ? custGetDocRules() : [];

  // Collect method-specific fields
  const data = { code, name, emails, ccEmails, bccEmails, sendMethod, requiredDocs, notes };

  if (sendMethod === 'qbo_invoice_only_then_pod_email') {
    data.podEmailTo = custGetTags('custPodEmailToTags');
    data.podEmailCc = custGetTags('custPodEmailCcTags');
    data.podEmailSubject = document.getElementById('custPodEmailSubject').value.trim();
    data.podEmailBody = document.getElementById('custPodEmailBody').value.trim();
  } else if (sendMethod === 'portal_upload') {
    data.portalUrl = document.getElementById('custPortalUrl').value.trim();
    data.portalClient = document.getElementById('custPortalClient').value.trim();
  }

  if (!code || !name) {
    alert('Customer code and name are required.');
    return;
  }

  let result;
  if (custEditingCode) {
    result = await agentBridge.updateCustomer(custEditingCode, data);
  } else {
    result = await agentBridge.createCustomer(data);
  }

  if (result.error) {
    alert('Error: ' + result.error);
    return;
  }

  custCloseModal();
  custLoadCustomers();
}

async function custDelete(code) {
  if (!confirm('Deactivate customer ' + code + '? They can be reactivated later.')) return;
  await agentBridge.deleteCustomer(code);
  custLoadCustomers();
}

async function custReactivate(code) {
  await agentBridge.updateCustomer(code, { active: true });
  custLoadCustomers();
}

// ── Tag Input Helpers ──
const _custTagMap = {
  email: 'custEmailTags', cc: 'custCcTags', bcc: 'custBccTags',
  podEmailTo: 'custPodEmailToTags', podEmailCc: 'custPodEmailCcTags',
};

function custHandleTagKey(event, type) {
  if (event.key !== 'Enter') return;
  event.preventDefault();
  const input = event.target;
  const value = input.value.trim();
  if (!value) return;

  const containerId = _custTagMap[type] || type;
  custAddTag(containerId, value);
  input.value = '';
}

function custAddTag(containerId, value) {
  const container = document.getElementById(containerId);
  const input = container.querySelector('input');
  const tag = document.createElement('span');
  tag.className = 'tag-item';
  tag.innerHTML = escHtml(value) + ' <span class="tag-remove" onclick="this.parentElement.remove()">&times;</span>';
  tag.dataset.value = value;
  container.insertBefore(tag, input);
}

function custFlushTagInput(containerId) {
  const container = document.getElementById(containerId);
  const input = container ? container.querySelector('input') : null;
  if (input && input.value.trim()) {
    custAddTag(containerId, input.value.trim());
    input.value = '';
  }
}

function custGetTags(containerId) {
  custFlushTagInput(containerId);
  const tags = document.getElementById(containerId).querySelectorAll('.tag-item');
  return Array.from(tags).map(t => t.dataset.value);
}

function custClearTags(containerId) {
  const container = document.getElementById(containerId);
  container.querySelectorAll('.tag-item').forEach(t => t.remove());
}

// ── Required Docs: checkbox + OR-group logic ──
function custSetDocMode(mode) {
  _custDocMode = mode;
  const allBtn = document.getElementById('custDocModeAll');
  const specBtn = document.getElementById('custDocModeSpecific');
  const panel = document.getElementById('custDocSpecificPanel');
  const hint = document.getElementById('custDocModeHint');

  if (mode === 'all') {
    allBtn.classList.add('doc-mode-active');
    specBtn.classList.remove('doc-mode-active');
    panel.style.display = 'none';
    hint.textContent = 'All attachments on the invoice will be included when sending.';
  } else {
    specBtn.classList.add('doc-mode-active');
    allBtn.classList.remove('doc-mode-active');
    panel.style.display = 'block';
    hint.textContent = 'Only checked documents are required. Invoice will be blocked if any are missing.';
  }
  custDocCheckChanged();
}

function custDocCheckChanged() {
  // Show/hide OR-group section when 2+ checkboxes are checked
  const checked = _custGetCheckedDocs();
  const orSection = document.getElementById('custOrGroupSection');
  if (checked.length >= 2) {
    orSection.style.display = 'block';
    _custRefreshOrDropdowns();
  } else {
    orSection.style.display = 'none';
  }
  _custRefreshRuleSummary();
}

function _custGetCheckedDocs() {
  const checkboxes = document.querySelectorAll('#custDocSpecificPanel input[type="checkbox"]');
  return Array.from(checkboxes).filter(cb => cb.checked).map(cb => cb.value);
}

function _custRefreshOrDropdowns() {
  const checked = _custGetCheckedDocs();
  // Only show docs that are checked AND not already in an OR group
  const inGroups = new Set(_custOrGroups.flat());
  const available = checked.filter(d => !inGroups.has(d));

  for (const selId of ['custOrLeft', 'custOrRight']) {
    const sel = document.getElementById(selId);
    const current = sel.value;
    sel.innerHTML = '<option value="">Pick...</option>' +
      available.map(d => `<option value="${d}" ${d === current ? 'selected' : ''}>${d.toUpperCase()}</option>`).join('');
  }
}

function custAddOrGroup() {
  const left = document.getElementById('custOrLeft').value;
  const right = document.getElementById('custOrRight').value;
  if (!left || !right || left === right) {
    alert('Select two different document types to link.');
    return;
  }
  // Don't allow if either is already in a group
  const inGroups = new Set(_custOrGroups.flat());
  if (inGroups.has(left) || inGroups.has(right)) {
    alert('One of those docs is already in an OR group. Remove it first.');
    return;
  }
  _custOrGroups.push([left, right]);
  // Uncheck both individual checkboxes (they're now an OR pair, not individual requirements)
  _custRefreshOrGroupList();
  _custRefreshOrDropdowns();
  _custRefreshRuleSummary();
}

function custRemoveOrGroup(idx) {
  _custOrGroups.splice(idx, 1);
  _custRefreshOrGroupList();
  _custRefreshOrDropdowns();
  _custRefreshRuleSummary();
}

function _custRefreshOrGroupList() {
  const list = document.getElementById('custOrGroupList');
  if (_custOrGroups.length === 0) {
    list.innerHTML = '';
    return;
  }
  list.innerHTML = _custOrGroups.map((g, i) =>
    `<span class="or-group-pill">${g.map(d => d.toUpperCase()).join(' <span style="color:#b45309;">or</span> ')}` +
    `<span class="or-remove" onclick="custRemoveOrGroup(${i})">&times;</span></span>`
  ).join('');
}

function _custRefreshRuleSummary() {
  const summary = document.getElementById('custDocRuleSummary');
  if (_custDocMode === 'all') { summary.style.display = 'none'; return; }

  const rules = custGetDocRules();
  if (rules.length === 0) { summary.style.display = 'none'; return; }

  summary.style.display = 'block';
  const parts = rules.map(r => {
    if (r.includes('/')) {
      return '<strong>' + r.split('/').map(p => p.toUpperCase()).join('</strong> or <strong>') + '</strong>';
    }
    return '<strong>' + r.toUpperCase() + '</strong>';
  });
  summary.innerHTML = 'Rule: Must have ' + parts.join(' <span style="color:#ea580c;">AND</span> ');
}

function custGetDocRules() {
  // Build the requiredDocs array from checkboxes + OR groups
  if (_custDocMode === 'all') return [];

  const checked = _custGetCheckedDocs();
  const inOrGroups = new Set(_custOrGroups.flat());

  // Individual requirements (checked but not in any OR group)
  const singles = checked.filter(d => !inOrGroups.has(d));

  // OR groups (only include if BOTH docs in the group are checked)
  const orRules = _custOrGroups
    .filter(g => g.every(d => checked.includes(d)))
    .map(g => g.join('/'));

  return [...singles, ...orRules];
}

function custSetDocRules(rules) {
  // Load requiredDocs array into the checkbox UI
  _custOrGroups = [];

  if (!rules || rules.length === 0) {
    custSetDocMode('all');
    // Uncheck all
    document.querySelectorAll('#custDocSpecificPanel input[type="checkbox"]').forEach(cb => cb.checked = false);
    return;
  }

  custSetDocMode('specific');
  // Uncheck all first
  document.querySelectorAll('#custDocSpecificPanel input[type="checkbox"]').forEach(cb => cb.checked = false);

  for (const rule of rules) {
    if (rule.includes('/')) {
      // OR group
      const parts = rule.split('/').map(p => p.trim().toLowerCase());
      _custOrGroups.push(parts);
      // Check both checkboxes
      parts.forEach(p => {
        const cb = document.querySelector(`#custDocSpecificPanel input[type="checkbox"][value="${p}"]`);
        if (cb) cb.checked = true;
      });
    } else {
      // Single requirement
      const cb = document.querySelector(`#custDocSpecificPanel input[type="checkbox"][value="${rule.toLowerCase()}"]`);
      if (cb) cb.checked = true;
    }
  }

  _custRefreshOrGroupList();
  custDocCheckChanged();
}

function custClearDocRules() {
  _custOrGroups = [];
  _custDocMode = 'all';
  document.querySelectorAll('#custDocSpecificPanel input[type="checkbox"]').forEach(cb => cb.checked = false);
  custSetDocMode('all');
}

// ── Import/Export ──
function custOpenImportModal() {
  document.getElementById('custImportModal').classList.add('open');
  document.getElementById('custImportStatus').textContent = '';
}

function custCloseImportModal() {
  document.getElementById('custImportModal').classList.remove('open');
}

// Fuzzy column aliases for customer import
const CUST_IMPORT_ALIASES = {
  code:         ['code', 'customercode', 'custcode', 'customerid', 'custid', 'custno', 'customercode', 'customer_code'],
  name:         ['name', 'customername', 'customer', 'companyname', 'company', 'clientname', 'client'],
  emails:       ['email', 'emails', 'emailaddress', 'contactemail', 'billemail', 'customeremail', 'toemail'],
  ccEmails:     ['cc', 'ccemails', 'ccemail', 'ccaddress'],
  bccEmails:    ['bcc', 'bccemails', 'bccemail'],
  requiredDocs: ['requireddocs', 'requireddocuments', 'docs', 'documents', 'attachmentrules', 'required'],
  sendMethod:   ['sendmethod', 'method', 'sendvia', 'deliverymethod', 'sendtype'],
  notes:        ['notes', 'note', 'comments', 'comment', 'memo'],
};

function custParseExcelRows(rows) {
  /* Parse spreadsheet rows into customer objects using fuzzy column matching. */
  if (!rows || rows.length === 0) return [];

  const headers = Object.keys(rows[0]);
  const colMap = {};
  for (const [field, aliases] of Object.entries(CUST_IMPORT_ALIASES)) {
    colMap[field] = findColumnKey(headers, aliases);
  }

  if (!colMap.code) return { error: 'No "Code" or "Customer Code" column found. Found: ' + headers.join(', ') };
  if (!colMap.name) return { error: 'No "Name" or "Customer Name" column found. Found: ' + headers.join(', ') };

  const customers = [];
  for (const row of rows) {
    const code = String(colMap.code ? row[colMap.code] : '').trim();
    const name = String(colMap.name ? row[colMap.name] : '').trim();
    if (!code || !name) continue;

    // Split comma-separated values into arrays
    const splitCsv = (val) => String(val || '').split(/[,;]+/).map(s => s.trim()).filter(Boolean);

    customers.push({
      code,
      name,
      emails: splitCsv(colMap.emails ? row[colMap.emails] : ''),
      ccEmails: splitCsv(colMap.ccEmails ? row[colMap.ccEmails] : ''),
      bccEmails: splitCsv(colMap.bccEmails ? row[colMap.bccEmails] : ''),
      requiredDocs: splitCsv(colMap.requiredDocs ? row[colMap.requiredDocs] : '').map(d => d.toLowerCase()),
      sendMethod: String(colMap.sendMethod ? row[colMap.sendMethod] : 'email').trim().toLowerCase() || 'email',
      notes: String(colMap.notes ? row[colMap.notes] : '').trim(),
    });
  }
  return customers;
}

async function custHandleImportFile(event) {
  const file = event.target.files[0];
  if (!file) return;

  const statusEl = document.getElementById('custImportStatus');
  statusEl.textContent = 'Reading file...';

  try {
    const ext = file.name.split('.').pop().toLowerCase();
    let customers;

    if (ext === 'json') {
      // JSON import — keep existing logic
      const text = await file.text();
      const data = JSON.parse(text);
      customers = Array.isArray(data) ? data : (data.customers || []);
    } else {
      // Excel / CSV import via SheetJS
      const buf = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = (e) => resolve(new Uint8Array(e.target.result));
        reader.onerror = () => reject(new Error('Failed to read file'));
        reader.readAsArrayBuffer(file);
      });

      const wb = XLSX.read(buf, { type: 'array' });
      const ws = wb.Sheets[wb.SheetNames[0]];
      const rows = XLSX.utils.sheet_to_json(ws, { defval: '' });

      if (!rows || rows.length === 0) {
        statusEl.innerHTML = '<span style="color:#dc2626;">No data rows found in file.</span>';
        return;
      }

      const parsed = custParseExcelRows(rows);
      if (parsed.error) {
        statusEl.innerHTML = '<span style="color:#dc2626;">' + escHtml(parsed.error) + '</span>';
        return;
      }
      customers = parsed;
    }

    if (!customers || customers.length === 0) {
      statusEl.innerHTML = '<span style="color:#dc2626;">No customer records found in file.</span>';
      return;
    }

    statusEl.textContent = 'Importing ' + customers.length + ' customers...';
    const result = await agentBridge.importCustomers(customers);

    if (result.error) {
      statusEl.innerHTML = '<span style="color:#dc2626;">Error: ' + escHtml(result.error) + '</span>';
      return;
    }

    statusEl.innerHTML = '<span style="color:#16a34a;">Imported ' + result.total + ' customers (' + result.created + ' new, ' + result.updated + ' updated)</span>';
    custLoadCustomers();
  } catch (err) {
    statusEl.innerHTML = '<span style="color:#dc2626;">Failed to parse file: ' + escHtml(err.message) + '</span>';
  }

  event.target.value = '';
}

async function custExport() {
  const customers = await agentBridge.exportCustomers();
  if (!customers || customers.length === 0) {
    alert('No customers to export.');
    return;
  }

  // Build rows with friendly column names that match import aliases
  const rows = customers.map(c => ({
    'Code':          c.code || '',
    'Name':          c.name || '',
    'Emails':        (c.emails || []).join(', '),
    'CC Emails':     (c.ccEmails || []).join(', '),
    'BCC Emails':    (c.bccEmails || []).join(', '),
    'Send Method':   c.sendMethod || 'email',
    'Required Docs': (c.requiredDocs || []).join(', '),
    'Notes':         c.notes || '',
    'Active':        c.active !== false ? 'Yes' : 'No',
  }));

  const ws = XLSX.utils.json_to_sheet(rows);
  // Auto-width columns
  const colWidths = Object.keys(rows[0] || {}).map(k => ({ wch: Math.max(k.length, 18) }));
  ws['!cols'] = colWidths;

  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, 'Customers');

  const buf = XLSX.write(wb, { type: 'array', bookType: 'xlsx' });
  const blob = new Blob([buf], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
  triggerDownload(blob, 'customers_export.xlsx');
}
