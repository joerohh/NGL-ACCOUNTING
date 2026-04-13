// ══════════════════════════════════════════════════════════════════
//  APP — Navigation, Init, Responsive Layout & Auth Gate
// ══════════════════════════════════════════════════════════════════
import { state, invoiceState } from './shared/state.js';
import { addLog, invAddLog } from './shared/log.js';
import { setupDrop } from './shared/dom-helpers.js';
import { escHtml } from './shared/utils.js';
import { LS_CUSTOMERS } from './shared/constants.js';
import { agentBridge } from './shared/agent-client.js';
import { agentHealthCheck } from './agent-ui.js';
import { renderPdfQueue, updateUI, handleExcelFile, handlePdfFiles } from './tools/merge/merge.js';
import { invInitDropZones } from './tools/invoice-sender/invoice-sender.js';
import { custLoadCustomers } from './tools/customers/customers.js';
import { settingsLoad } from './tools/settings/settings.js';
import { renderSessionHistory } from './tools/session-history/session-history.js';

// Prevent browser from opening files dropped anywhere on the page
document.addEventListener('dragover', function(e) { e.preventDefault(); });
document.addEventListener('drop', function(e) { e.preventDefault(); });


// ══════════════════════════════════════════════════════════
//  AUTH GATE — login before showing the app
// ══════════════════════════════════════════════════════════

function hideAllScreens() {
  document.getElementById('loginScreen').style.display = 'none';
  document.getElementById('setupScreen').style.display = 'none';
  document.getElementById('appLayout').style.display = 'none';
}

function showLogin() {
  hideAllScreens();
  document.getElementById('loginScreen').style.display = '';
}

function showSetup() {
  hideAllScreens();
  document.getElementById('setupScreen').style.display = '';
}

function showApp(user) {
  state.currentUser = user;
  hideAllScreens();
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

  const rememberMe = document.getElementById('loginRememberMe').checked;
  const result = await agentBridge.login(username, password, rememberMe);

  btn.disabled = false;
  btnText.textContent = 'Sign In';

  if (result.error) {
    errorEl.textContent = result.error;
    errorEl.style.display = '';
    return;
  }

  // JWT is persisted by agentBridge._saveAuth(). "Remember me" controls the
  // token expiry (30 days vs 72 hours) — no credentials stored in localStorage.

  showApp(result.user);
  initApp();
}

function doLogout() {
  agentBridge.logout();
  state.currentUser = null;
  showLogin();
  // Clear password field and uncheck remember me for next login
  document.getElementById('loginPassword').value = '';
  document.getElementById('loginRememberMe').checked = false;
  document.getElementById('loginError').style.display = 'none';
}

async function doSetup() {
  const username = document.getElementById('setupUsername').value.trim();
  const displayName = document.getElementById('setupDisplayName').value.trim();
  const password = document.getElementById('setupPassword').value;
  const confirm = document.getElementById('setupPasswordConfirm').value;
  const errorEl = document.getElementById('setupError');
  const btn = document.getElementById('setupBtn');
  const btnText = document.getElementById('setupBtnText');

  if (!username) { errorEl.textContent = 'Username is required.'; errorEl.style.display = ''; return; }
  if (password.length < 4) { errorEl.textContent = 'Password must be at least 4 characters.'; errorEl.style.display = ''; return; }
  if (password !== confirm) { errorEl.textContent = 'Passwords do not match.'; errorEl.style.display = ''; return; }

  btn.disabled = true;
  btnText.textContent = 'Creating account...';
  errorEl.style.display = 'none';

  try {
    const res = await fetch(agentBridge.baseUrl + '/auth/setup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password, displayName: displayName || username }),
    });
    const data = await res.json();

    btn.disabled = false;
    btnText.textContent = 'Create Admin Account';

    if (!res.ok) {
      errorEl.textContent = data.detail || 'Setup failed.';
      errorEl.style.display = '';
      return;
    }

    // Setup complete — show login screen so they can sign in
    showLogin();
    document.getElementById('loginUsername').value = username;
    document.getElementById('loginUsername').focus();
  } catch (e) {
    btn.disabled = false;
    btnText.textContent = 'Create Admin Account';
    errorEl.textContent = 'Cannot connect to agent: ' + e.message;
    errorEl.style.display = '';
  }
}

// ── Google Sign-In (redirect flow) ──

let _googlePollTimer = null;

async function initGoogleSignIn() {
  try {
    const res = await fetch(agentBridge.baseUrl + '/auth/google/available');
    if (!res.ok) return;
    const data = await res.json();
    if (!data.available) return;

    const section = document.getElementById('googleLoginSection');
    if (section) section.style.display = '';
  } catch { /* Google Sign-In not available */ }
}

function doGoogleLogin() {
  if (!state.agentConnected) {
    document.getElementById('loginError').textContent = 'Agent is offline.';
    document.getElementById('loginError').style.display = '';
    return;
  }

  const btn = document.getElementById('googleLoginBtn');
  const btnText = document.getElementById('googleLoginBtnText');
  btn.disabled = true;
  btnText.textContent = 'Opening Google...';

  // Open Google login in system browser
  window.open(agentBridge.baseUrl + '/auth/google/login', '_blank');

  // Poll for completion
  btnText.textContent = 'Waiting for sign-in...';
  const startTime = Date.now();
  const TIMEOUT_MS = 5 * 60 * 1000;

  if (_googlePollTimer) clearInterval(_googlePollTimer);
  _googlePollTimer = setInterval(async () => {
    try {
      const res = await fetch(agentBridge.baseUrl + '/auth/google/poll');
      if (res.ok) {
        const data = await res.json();
        if (data.authenticated) {
          clearInterval(_googlePollTimer);
          _googlePollTimer = null;
          agentBridge._saveAuth(data.token, data.user);
          btn.disabled = false;
          btnText.textContent = 'Sign in with Google';
          showApp(data.user);
          initApp();
          return;
        }
      }
    } catch { /* keep polling */ }

    if (Date.now() - startTime > TIMEOUT_MS) {
      clearInterval(_googlePollTimer);
      _googlePollTimer = null;
      btn.disabled = false;
      btnText.textContent = 'Sign in with Google';
      document.getElementById('loginError').textContent = 'Google sign-in timed out. Try again.';
      document.getElementById('loginError').style.display = '';
    }
  }, 2000);
}

// Auth hooks — agentBridge calls these when session expires
agentBridge.hooks.onAuthRequired = showLogin;

// Global handlers for inline onclick
window.doLogin = doLogin;
window.doLogout = doLogout;
window.doSetup = doSetup;
window.doGoogleLogin = doGoogleLogin;


// ══════════════════════════════════════════════════════════
//  STARTUP — try to restore session, else show login
// ══════════════════════════════════════════════════════════

async function startup() {
  agentBridge._loadSavedAuth();

  // Update agent status on both login and setup screens
  const statusEls = [document.getElementById('loginAgentStatus'), document.getElementById('setupAgentStatus')];
  function setAgentStatus(text, color) {
    statusEls.forEach(el => { if (el) { el.textContent = text; el.style.color = color; } });
  }

  const health = await agentBridge.checkHealth();
  if (health) {
    setAgentStatus('Agent connected', '#16a34a');
    state.agentConnected = true;
  } else {
    setAgentStatus('Agent offline — start the agent server first', '#dc2626');
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

  // Check if first-run setup is needed (no users exist yet)
  if (state.agentConnected) {
    try {
      const res = await fetch(agentBridge.baseUrl + '/auth/setup-required');
      if (res.ok) {
        const data = await res.json();
        if (data.setupRequired) {
          showSetup();
          document.getElementById('setupUsername').focus();
          return;
        }
      }
    } catch { /* fall through to login */ }
  }

  // Try to restore existing session (JWT persisted in localStorage)
  if (agentBridge.isLoggedIn()) {
    const valid = await agentBridge.validateSession();
    if (valid) {
      showApp(agentBridge.getCurrentUser());
      initApp();
      return;
    }
  }

  // No valid session — show login
  showLogin();
  initGoogleSignIn();
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
  document.getElementById('sessionHistoryView').style.display = 'none';

  // Show selected view
  if (tool === 'home') {
    document.getElementById('homeView').style.display = '';
    refreshHomeMetrics();
  } else if (tool === 'merge') {
    document.getElementById('mergeToolView').style.display = '';
  } else if (tool === 'invoice-sender') {
    document.getElementById('invoiceSenderView').style.display = '';
    invInitDropZones();
  } else if (tool === 'customers') {
    document.getElementById('customerView').style.display = '';
    custLoadCustomers();
  } else if (tool === 'session-history') {
    document.getElementById('sessionHistoryView').style.display = '';
    renderSessionHistory();
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
    'session-history': 'Session History',
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
  document.getElementById('navSessionHistory').classList.toggle('active', tool === 'session-history');
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


// ── Window assignments for inline HTML handlers ──
window.switchTool = switchTool;
window.toggleToolSwitcher = toggleToolSwitcher;
window.refreshHomeMetrics = refreshHomeMetrics;

// ── Boot ──
startup();
