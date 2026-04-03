// ══════════════════════════════════════════════════════════════════
//  APP — Navigation, Init, Responsive Layout & Auth Gate
// ══════════════════════════════════════════════════════════════════
import { state, invoiceState } from './shared/state.js';
import { addLog, invAddLog } from './shared/log.js';
import { setupDrop } from './shared/dom-helpers.js';
import { escHtml } from './shared/utils.js';
import { LS_CUSTOMERS, LS_REMEMBER_ME } from './shared/constants.js';
import { agentBridge } from './shared/agent-client.js';
import { agentHealthCheck } from './agent-ui.js';
import { renderPdfQueue, updateUI, handleExcelFile, handlePdfFiles } from './tools/merge/merge.js';
import { invInitDropZones } from './tools/invoice-sender/invoice-sender.js';
import { custLoadCustomers } from './tools/customers/customers.js';
import { settingsLoad } from './tools/settings/settings.js';

// Prevent browser from opening files dropped anywhere on the page
document.addEventListener('dragover', function(e) { e.preventDefault(); });
document.addEventListener('drop', function(e) { e.preventDefault(); });


// ══════════════════════════════════════════════════════════
//  AUTH GATE — login before showing the app
// ══════════════════════════════════════════════════════════

function showLogin() {
  document.getElementById('loginScreen').style.display = '';
  document.getElementById('appLayout').style.display = 'none';
}

function showApp(user) {
  state.currentUser = user;
  document.getElementById('loginScreen').style.display = 'none';
  document.getElementById('appLayout').style.display = '';

  // Update sidebar user info
  const nameEl = document.getElementById('sidebarDisplayName');
  const roleEl = document.getElementById('sidebarUserRole');
  if (nameEl) nameEl.textContent = user.displayName || user.username;
  if (roleEl) roleEl.textContent = user.role;
}

async function doLogin() {
  const username = document.getElementById('loginUsername').value.trim();
  const password = document.getElementById('loginPassword').value;
  const errorEl = document.getElementById('loginError');
  const btn = document.getElementById('loginBtn');
  const btnText = document.getElementById('loginBtnText');

  if (!username || !password) {
    errorEl.textContent = 'Enter your username and password.';
    errorEl.style.display = '';
    return;
  }

  btn.disabled = true;
  btnText.textContent = 'Signing in...';
  errorEl.style.display = 'none';

  const result = await agentBridge.login(username, password);

  btn.disabled = false;
  btnText.textContent = 'Sign In';

  if (result.error) {
    errorEl.textContent = result.error;
    errorEl.style.display = '';
    return;
  }

  // Save credentials if "Remember me" is checked
  const rememberMe = document.getElementById('loginRememberMe').checked;
  if (rememberMe) {
    localStorage.setItem(LS_REMEMBER_ME, btoa(JSON.stringify({ u: username, p: password })));
  } else {
    localStorage.removeItem(LS_REMEMBER_ME);
  }

  showApp(result.user);
  initApp();
}

function doLogout() {
  agentBridge.logout();
  state.currentUser = null;
  localStorage.removeItem(LS_REMEMBER_ME);
  showLogin();
  // Clear password field and uncheck remember me for next login
  document.getElementById('loginPassword').value = '';
  document.getElementById('loginRememberMe').checked = false;
  document.getElementById('loginError').style.display = 'none';
}

// Auth hooks — agentBridge calls these when session expires
agentBridge.hooks.onAuthRequired = showLogin;

// Global handlers for inline onclick
window.doLogin = doLogin;
window.doLogout = doLogout;


// ══════════════════════════════════════════════════════════
//  STARTUP — try to restore session, else show login
// ══════════════════════════════════════════════════════════

async function startup() {
  // Check if agent is running first
  const statusEl = document.getElementById('loginAgentStatus');

  agentBridge._loadSavedAuth();

  const health = await agentBridge.checkHealth();
  if (health) {
    if (statusEl) { statusEl.textContent = 'Agent connected'; statusEl.style.color = '#16a34a'; }
    state.agentConnected = true;
  } else {
    if (statusEl) { statusEl.textContent = 'Agent offline — start the agent server first'; statusEl.style.color = '#dc2626'; }
    state.agentConnected = false;
  }

  // Check if the server requires login (auth middleware may be disabled)
  let authRequired = true;
  if (state.agentConnected) {
    try {
      const res = await fetch(agentBridge.baseUrl + '/auth/token');
      if (res.ok) {
        const data = await res.json();
        authRequired = !!data.loginRequired;
      }
    } catch { /* assume auth required if we can't check */ }
  }

  // If auth is not enforced, skip login and go straight to app
  if (!authRequired) {
    showApp({ username: 'local', displayName: 'Local User', role: 'admin' });
    initApp();
    return;
  }

  // Try to restore existing session
  if (agentBridge.isLoggedIn()) {
    const valid = await agentBridge.validateSession();
    if (valid) {
      showApp(agentBridge.getCurrentUser());
      initApp();
      return;
    }
  }

  // Try auto-login from "Remember me" saved credentials
  const saved = localStorage.getItem(LS_REMEMBER_ME);
  if (saved && state.agentConnected) {
    try {
      const { u, p } = JSON.parse(atob(saved));
      const result = await agentBridge.login(u, p);
      if (!result.error) {
        showApp(result.user);
        initApp();
        return;
      }
      // Saved credentials invalid — clear them
      localStorage.removeItem(LS_REMEMBER_ME);
    } catch {
      localStorage.removeItem(LS_REMEMBER_ME);
    }
  }

  // No valid session — show login
  showLogin();
  // Pre-check "Remember me" if credentials were previously saved (but expired)
  // Focus username field
  document.getElementById('loginUsername').focus();
}

let _appInitialized = false;

function initApp() {
  if (_appInitialized) return;
  _appInitialized = true;

  renderPdfQueue();
  updateUI();
  addLog('info', '// NGL Transportation Accounting v2.1');
  addLog('info', '// 100% client-side — no files leave your machine');
  addLog('info', '// Drop an Excel manifest + PDFs to merge by container, or just drop PDFs to combine');

  // Check agent health on load, then every 15 seconds
  agentHealthCheck();
  setInterval(agentHealthCheck, 15000);

  // Enter key triggers search in history filters
  document.querySelectorAll('.history-filter-input').forEach(el => {
    el.addEventListener('keydown', e => { if (e.key === 'Enter') loadSessionHistory(0); });
  });

  // Start on home page
  switchTool('home');

  // Initialize Invoice Sender drop zones
  invInitDropZones();
  invAddLog('info', '// Invoice Sending Tool ready');
  invAddLog('info', '// Upload a CSV export and PDF attachments to get started');
}


// ── Tool Navigation ──
function switchTool(tool) {
  state.activeTool = tool;

  // Close any open tool switcher menus
  document.querySelectorAll('.tool-switcher-menu').forEach(m => m.classList.remove('open'));

  // Hide all views
  document.getElementById('homeView').style.display = 'none';
  document.getElementById('mergeToolView').style.display = 'none';
  document.getElementById('invoiceSenderView').style.display = 'none';
  document.getElementById('customerView').style.display = 'none';
  document.getElementById('settingsView').style.display = 'none';

  // Show selected view
  if (tool === 'home') {
    document.getElementById('homeView').style.display = '';
    refreshHomeMetrics();
    loadSessionHistory();
  } else if (tool === 'merge') {
    document.getElementById('mergeToolView').style.display = '';
  } else if (tool === 'invoice-sender') {
    document.getElementById('invoiceSenderView').style.display = '';
    invInitDropZones();
  } else if (tool === 'customers') {
    document.getElementById('customerView').style.display = '';
    custLoadCustomers();
  } else if (tool === 'settings') {
    document.getElementById('settingsView').style.display = '';
    settingsLoad();
  }

  // Update sidebar subtitle
  const subtitles = {
    'home': 'Accounting Suite',
    'merge': 'Merging Tool',
    'invoice-sender': 'Invoice Sender',
    'customers': 'Customer Management',
    'settings': 'Settings',
  };
  document.getElementById('headerSubtitle').textContent = subtitles[tool] || '';

  // Show/hide merge-specific controls in sidebar footer
  const mergeControls = document.getElementById('mergeToolControls');
  mergeControls.style.display = tool === 'merge' ? 'flex' : 'none';

  // Update sidebar nav active states
  document.getElementById('navMerge').classList.toggle('active', tool === 'merge');
  document.getElementById('navInvoiceSender').classList.toggle('active', tool === 'invoice-sender');
  document.getElementById('navCustomers').classList.toggle('active', tool === 'customers');
  document.getElementById('navSettings').classList.toggle('active', tool === 'settings');
}

// ── Home Dashboard Metrics ──
function refreshHomeMetrics() {
  // Pending invoices from sender state (guard against TDZ during init)
  let pending = 0;
  try { pending = (invoiceState && invoiceState.invoices) ? invoiceState.invoices.length : 0; } catch (_) {}
  document.getElementById('metricPending').textContent = pending || '--';

  // PDFs merged today (from merge results)
  const merged = (state.mergeResults) ? state.mergeResults.length : 0;
  document.getElementById('metricMerged').textContent = merged;

  // Emails sent — try to fetch from agent audit stats
  document.getElementById('metricSent').textContent = '--';
  if (state.agentConnected) {
    agentBridge._authFetch('http://localhost:8787/audit/stats')
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data && data.sent !== undefined) {
          document.getElementById('metricSent').textContent = data.sent;
        }
      })
      .catch(() => {});
  }

  // Customer count from localStorage
  try {
    const custData = JSON.parse(localStorage.getItem(LS_CUSTOMERS) || '{}');
    const activeCount = Object.values(custData).filter(c => c.active !== false).length;
    document.getElementById('metricCustomers').textContent = activeCount || '--';
  } catch {
    document.getElementById('metricCustomers').textContent = '--';
  }
}

// ── Tool Switcher Dropdown ──
function toggleToolSwitcher(btn) {
  const menu = btn.nextElementSibling;
  const isOpen = menu.classList.contains('open');
  // Close all menus first
  document.querySelectorAll('.tool-switcher-menu').forEach(m => m.classList.remove('open'));
  if (!isOpen) menu.classList.add('open');
}

// Close tool switcher when clicking outside
document.addEventListener('click', function(e) {
  if (!e.target.closest('.tool-switcher')) {
    document.querySelectorAll('.tool-switcher-menu').forEach(m => m.classList.remove('open'));
  }
});


// ══════════════════════════════════════════════════════════
//  DROP ZONE EVENTS  (setupDrop is in shared/dom-helpers.js)
// ══════════════════════════════════════════════════════════
setupDrop('excelDropZone', files => {
  const excel = files.find(f => /\.(xlsx|xls|csv)$/i.test(f.name));
  if (excel) handleExcelFile(excel);
  else {
    const pdfs = files.filter(f => /\.pdf$/i.test(f.name));
    if (pdfs.length) { addLog('warning', 'Dropped PDFs in the Excel zone — adding to PDF queue'); handlePdfFiles(pdfs); }
    else addLog('warning', 'Expected an .xlsx, .xls, or .csv file');
  }
});

setupDrop('pdfDropZone', files => {
  const excel = files.find(f => /\.(xlsx|xls|csv)$/i.test(f.name));
  if (excel) { addLog('info', 'Detected Excel file in PDF zone — processing as manifest'); handleExcelFile(excel); }
  handlePdfFiles(files.filter(f => /\.pdf$/i.test(f.name)));
});


// ══════════════════════════════════════════════════════════
//  RESPONSIVE GRID
// ══════════════════════════════════════════════════════════
function applyResponsiveLayout() {
  const grid = document.getElementById('mainGrid');
  if (window.innerWidth < 900) {
    grid.style.gridTemplateColumns = '1fr';
  } else {
    grid.style.gridTemplateColumns = '1fr 1fr';
  }
}
window.addEventListener('resize', applyResponsiveLayout);
applyResponsiveLayout();


// ══════════════════════════════════════════════════════════
//  SESSION HISTORY — audit log viewer on home dashboard
// ══════════════════════════════════════════════════════════
let _historyPage = 0;
const HISTORY_PAGE_SIZE = 25;

async function loadSessionHistory(page) {
  if (!state.agentConnected) return;
  if (page !== undefined) _historyPage = page;

  const filters = {
    date: document.getElementById('historyFilterDate').value || '',
    customer: document.getElementById('historyFilterCustomer').value.trim(),
    status: document.getElementById('historyFilterStatus').value,
    invoice: document.getElementById('historyFilterInvoice').value.trim(),
    limit: HISTORY_PAGE_SIZE,
    offset: _historyPage * HISTORY_PAGE_SIZE,
  };

  const data = await agentBridge.getAuditLog(filters);
  const tbody = document.getElementById('historyTableBody');

  if (data.error || !data.entries || data.entries.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" class="history-empty">'
      + (data.error ? 'Could not load history' : 'No entries found') + '</td></tr>';
    document.getElementById('historyPagination').innerHTML = '';
    return;
  }

  tbody.innerHTML = data.entries.map(e => {
    const time = formatHistoryTime(e.timestamp);
    const toList = (e.toEmails || []).join(', ') || '--';
    const detail = e.error || (e.attachmentsFound || []).join(', ') || '--';
    const statusClass = (e.status || '').replace(/\s/g, '_');
    return `<tr>
      <td title="${escHtml(e.timestamp || '')}">${time}</td>
      <td><strong>${escHtml(e.invoiceNumber || '--')}</strong></td>
      <td>${escHtml(e.containerNumber || '--')}</td>
      <td>${escHtml(e.customerCode || '--')}</td>
      <td><span class="history-status ${statusClass}">${escHtml(e.status || '--')}</span></td>
      <td style="max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(toList)}">${escHtml(toList)}</td>
      <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(detail)}">${escHtml(detail)}</td>
    </tr>`;
  }).join('');

  // Pagination
  const total = data.total || 0;
  const totalPages = Math.ceil(total / HISTORY_PAGE_SIZE);
  const pag = document.getElementById('historyPagination');

  if (totalPages <= 1) {
    pag.innerHTML = `<span>${total} entr${total === 1 ? 'y' : 'ies'}</span><span></span>`;
    return;
  }

  let btns = '';
  btns += `<button class="history-page-btn" onclick="loadSessionHistory(0)" ${_historyPage === 0 ? 'disabled' : ''}>&laquo;</button>`;
  btns += `<button class="history-page-btn" onclick="loadSessionHistory(${_historyPage - 1})" ${_historyPage === 0 ? 'disabled' : ''}>&lsaquo;</button>`;

  // Show up to 5 page numbers around current
  const start = Math.max(0, _historyPage - 2);
  const end = Math.min(totalPages, start + 5);
  for (let i = start; i < end; i++) {
    btns += `<button class="history-page-btn ${i === _historyPage ? 'active' : ''}" onclick="loadSessionHistory(${i})">${i + 1}</button>`;
  }

  btns += `<button class="history-page-btn" onclick="loadSessionHistory(${_historyPage + 1})" ${_historyPage >= totalPages - 1 ? 'disabled' : ''}>&rsaquo;</button>`;
  btns += `<button class="history-page-btn" onclick="loadSessionHistory(${totalPages - 1})" ${_historyPage >= totalPages - 1 ? 'disabled' : ''}>&raquo;</button>`;

  pag.innerHTML = `<span>${total} entr${total === 1 ? 'y' : 'ies'} &middot; page ${_historyPage + 1} of ${totalPages}</span><div class="history-page-btns">${btns}</div>`;
}

function formatHistoryTime(ts) {
  if (!ts) return '--';
  try {
    const d = new Date(ts.includes('T') ? ts : ts.replace(' ', 'T') + 'Z');
    const now = new Date();
    const diff = now - d;
    if (diff < 60000) return 'Just now';
    if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago';
    if (diff < 86400000) return Math.floor(diff / 3600000) + 'h ago';
    if (diff < 172800000) return 'Yesterday';
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  } catch { return ts.slice(0, 16); }
}

async function exportSessionHistory() {
  if (!state.agentConnected) return;
  await agentBridge.exportAuditLog();
}

// ── Window assignments for inline HTML handlers ──
window.switchTool = switchTool;
window.toggleToolSwitcher = toggleToolSwitcher;
window.refreshHomeMetrics = refreshHomeMetrics;
window.loadSessionHistory = loadSessionHistory;
window.exportSessionHistory = exportSessionHistory;

// ── Boot ──
startup();
