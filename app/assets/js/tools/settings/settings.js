// ══════════════════════════════════════════════════════════
//  SETTINGS — Credentials, user management, notifications
// ══════════════════════════════════════════════════════════
import { state } from '../../shared/state.js';
import { agentBridge } from '../../shared/agent-client.js';
import { agentHealthCheck } from '../../agent-ui.js';

export async function settingsLoad() {
  loadNotificationState();

  // Show/hide admin-only sections
  const user = agentBridge.getCurrentUser();
  const isAdmin = user && user.role === 'admin';
  const userMgmt = document.getElementById('userManagementSection');
  if (userMgmt) userMgmt.style.display = isAdmin ? '' : 'none';

  // Load user list if admin
  if (isAdmin && state.agentConnected) loadUserList();

  if (!state.agentConnected) {
    document.getElementById('settingsQboStatus').textContent = 'Agent offline';
    document.getElementById('settingsTmsStatus').textContent = 'Agent offline';
    return;
  }

  const creds = await agentBridge.getCredentials();
  if (creds.error) {
    return;
  }

  // Pre-fill TMS email (never pre-fill passwords)
  if (creds.tms_email) {
    document.getElementById('settingsTmsEmail').value = creds.tms_email;
  }
  document.getElementById('settingsTmsStatus').textContent = creds.tms_configured ? 'Configured' : 'Not configured';
  document.getElementById('settingsTmsStatus').style.color = creds.tms_configured ? '#16a34a' : '#94a3b8';

  // Clear password fields
  document.getElementById('settingsTmsPassword').value = '';
  document.getElementById('settingsTmsPassword').placeholder = creds.tms_configured ? '(saved \u2014 enter new to change)' : 'Enter password';

  // Load QBO API status
  await loadQboApiStatus();

  // Load email config
  await loadEmailConfig();
}

// ── Email (Gmail) Settings ──
async function loadEmailConfig() {
  const statusEl = document.getElementById('settingsEmailStatus');
  const addrEl = document.getElementById('settingsGmailAddress');
  const pwEl = document.getElementById('settingsGmailAppPassword');
  if (!statusEl) return;

  const cfg = await agentBridge.getEmailConfig();
  if (cfg.error) {
    statusEl.textContent = 'Could not load';
    return;
  }
  if (cfg.gmail_address) addrEl.value = cfg.gmail_address;
  pwEl.value = '';
  pwEl.placeholder = cfg.configured ? '(saved \u2014 enter new to change)' : '16-character app password';
  statusEl.textContent = cfg.configured ? ('Configured (' + cfg.gmail_address + ')') : 'Not configured';
  statusEl.style.color = cfg.configured ? '#16a34a' : '#94a3b8';
}

function showEmailResult(msg, success) {
  const el = document.getElementById('emailResultMsg');
  if (!el) return;
  el.textContent = msg;
  el.style.display = '';
  el.style.color = success ? '#15803d' : '#dc2626';
  el.style.background = success ? '#f0fdf4' : '#fef2f2';
  el.style.border = '1px solid ' + (success ? '#bbf7d0' : '#fecaca');
  setTimeout(function() { el.style.display = 'none'; }, 10000);
}

async function saveEmailConfig() {
  if (!state.agentConnected) { showEmailResult('Agent is offline.', false); return; }
  const btn = document.getElementById('emailSaveBtn');
  const btnText = document.getElementById('emailSaveBtnText');
  const addr = document.getElementById('settingsGmailAddress').value.trim();
  const pw = document.getElementById('settingsGmailAppPassword').value;

  if (!addr) { showEmailResult('Enter your Gmail address.', false); return; }

  const payload = { gmail_address: addr };
  if (pw) payload.gmail_app_password = pw;

  btn.disabled = true; btnText.textContent = 'Saving...';
  const result = await agentBridge.saveEmailConfig(payload);
  btn.disabled = false; btnText.textContent = 'Save';

  if (result.error) { showEmailResult('Failed: ' + result.error, false); return; }
  showEmailResult(result.configured ? 'Saved! Emails will be sent from ' + result.gmail_address + '.' : 'Saved (app password still needed).', result.configured);
  await loadEmailConfig();
}

async function testEmailConfig() {
  if (!state.agentConnected) { showEmailResult('Agent is offline.', false); return; }
  const btn = document.getElementById('emailTestBtn');
  const btnText = document.getElementById('emailTestBtnText');
  const addr = document.getElementById('settingsGmailAddress').value.trim();
  const pw = document.getElementById('settingsGmailAppPassword').value;

  const payload = {};
  if (addr) payload.gmail_address = addr;
  if (pw) payload.gmail_app_password = pw;
  if (addr) payload.to = addr; // send test to self

  btn.disabled = true; btnText.textContent = 'Sending...';
  const result = await agentBridge.testEmailConfig(payload);
  btn.disabled = false; btnText.textContent = 'Send Test';

  if (result.sent) {
    showEmailResult('Test email sent! Check your inbox at ' + (addr || 'the saved address') + '.', true);
  } else {
    showEmailResult('Test failed: ' + (result.error || 'unknown error'), false);
  }
}

async function settingsSaveAndConnect() {
  if (!state.agentConnected) {
    settingsShowResult('Agent is offline. Start the agent first.', false);
    return;
  }

  const btn = document.getElementById('settingsSaveBtn');
  const btnText = document.getElementById('settingsSaveBtnText');
  btn.disabled = true;
  btnText.textContent = 'Saving & connecting...';

  const data = {};
  const tmsEmail = document.getElementById('settingsTmsEmail').value.trim();
  const tmsPass = document.getElementById('settingsTmsPassword').value;

  if (tmsEmail) data.tms_email = tmsEmail;
  if (tmsPass) data.tms_password = tmsPass;

  if (Object.keys(data).length === 0) {
    settingsShowResult('Enter at least one credential to save.', false);
    btn.disabled = false;
    btnText.textContent = 'Save & Connect';
    return;
  }

  const result = await agentBridge.saveAndConnect(data);
  btn.disabled = false;
  btnText.textContent = 'Save & Connect';

  if (result.error) {
    settingsShowResult('Failed: ' + result.error, false);
    return;
  }

  // Show results
  let msg = 'Credentials saved. ';
  const r = result.results || {};
  if (r.qbo === 'logged_in') msg += 'QBO: Connected! ';
  else if (r.qbo) msg += 'QBO: ' + r.qbo + '. ';
  if (r.tms === 'logged_in') msg += 'TMS: Logged in! ';
  else if (r.tms === 'needs_manual_login') msg += 'TMS: Needs manual login (check Chrome for 2FA). ';
  else if (r.tms) msg += 'TMS: ' + r.tms + '. ';

  const allGood = r.qbo === 'logged_in' || r.tms === 'logged_in';
  settingsShowResult(msg, allGood);

  // Refresh status
  settingsLoad();
  agentHealthCheck();
}

function settingsShowResult(msg, success) {
  const el = document.getElementById('settingsResultMsg');
  el.textContent = msg;
  el.style.display = '';
  el.style.color = success ? '#16a34a' : '#dc2626';
  el.style.background = success ? '#f0fdf4' : '#fef2f2';
  el.style.padding = '10px 14px';
  el.style.borderRadius = '8px';
  el.style.border = '1px solid ' + (success ? '#bbf7d0' : '#fecaca');
  setTimeout(function() { el.style.display = 'none'; }, 10000);
}

// ── Selector Health Checks ──
async function runSelectorHealthCheck() {
  if (!state.agentConnected) {
    settingsShowResult('Agent is offline. Start the agent first.', false);
    return;
  }

  const btn = document.getElementById('healthCheckBtn');
  btn.disabled = true;
  btn.textContent = 'Checking...';

  const resultsDiv = document.getElementById('healthCheckResults');
  resultsDiv.style.display = '';

  const tms = await agentBridge.checkTmsSelectorHealth();
  document.getElementById('healthCheckTms').innerHTML = formatHealthResult('TMS', tms);

  btn.disabled = false;
  btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg> Run Check`;
}

function formatHealthResult(label, data) {
  if (data.status === 'offline') {
    return `<strong>${label}:</strong> <span style="color:#94a3b8;">Browser offline</span>`;
  }
  if (data.status === 'error' && data.error) {
    return `<strong>${label}:</strong> <span style="color:#dc2626;">Error: ${data.error}</span>`;
  }

  const statusColor = data.status === 'ok' ? '#16a34a' : data.status === 'warning' ? '#d97706' : '#dc2626';
  const statusIcon = data.status === 'ok' ? '&#10003;' : data.status === 'warning' ? '&#9888;' : '&#10007;';
  const pageType = data.page_type || 'unknown';

  let html = `<div style="display:flex; align-items:center; gap:8px; margin-bottom:6px;">`;
  html += `<strong>${label}</strong>`;
  html += `<span style="color:${statusColor}; font-weight:600;">${statusIcon} ${data.status.toUpperCase()}</span>`;
  html += `<span style="color:#94a3b8; font-size:0.72rem;">(${pageType})</span>`;
  html += `</div>`;

  if (data.checks && data.checks.length > 0) {
    for (const c of data.checks) {
      const icon = c.found ? '<span style="color:#16a34a;">&#10003;</span>' : '<span style="color:#dc2626;">&#10007;</span>';
      html += `<div style="margin-left:12px; font-size:0.78rem;">${icon} ${c.name}</div>`;
    }
  } else if (pageType === 'other') {
    html += `<div style="margin-left:12px; font-size:0.78rem; color:#94a3b8;">No checks for current page</div>`;
  }

  if (data.passed !== undefined) {
    html += `<div style="margin-top:4px; font-size:0.72rem; color:#94a3b8;">${data.passed}/${data.total} passed</div>`;
  }

  return html;
}

// ── Notification Settings ──
async function toggleNotifications(enabled) {
  if (enabled && 'Notification' in window) {
    const perm = await Notification.requestPermission();
    if (perm !== 'granted') {
      document.getElementById('settingsNotifyEnabled').checked = false;
      settingsShowResult('Browser notification permission was denied. Enable it in your browser settings.', false);
      return;
    }
  }
  localStorage.setItem('ngl_notifications_enabled', enabled ? '1' : '0');
  if (state.agentConnected) {
    await agentBridge.updateNotificationSettings(enabled);
  }
}

function loadNotificationState() {
  const enabled = localStorage.getItem('ngl_notifications_enabled') === '1';
  const checkbox = document.getElementById('settingsNotifyEnabled');
  if (checkbox) checkbox.checked = enabled;
}

// ── User Management (admin only) ──
async function loadUserList() {
  const container = document.getElementById('userList');
  if (!container) return;

  const result = await agentBridge.listUsers();
  if (result.error) {
    container.innerHTML = `<div style="color:#dc2626; font-size:0.82rem;">Failed to load users: ${result.error}</div>`;
    return;
  }

  const users = result.users || [];
  const currentUser = agentBridge.getCurrentUser();

  container.innerHTML = users.map(u => {
    const isYou = currentUser && currentUser.id === u.id;
    const statusColor = u.active ? '#16a34a' : '#94a3b8';
    const statusText = u.active ? 'Active' : 'Inactive';
    const roleBadge = u.role === 'admin'
      ? '<span style="background:#fef3c7; color:#92400e; padding:2px 8px; border-radius:4px; font-size:0.7rem; font-weight:600;">ADMIN</span>'
      : '<span style="background:#e0e7ff; color:#3730a3; padding:2px 8px; border-radius:4px; font-size:0.7rem; font-weight:600;">OPERATOR</span>';

    return `
      <div style="background:#fff; border:1px solid #e2e8f0; border-radius:10px; padding:14px; display:flex; align-items:center; gap:12px;">
        <div style="width:32px; height:32px; background:${u.active ? '#f0f9ff' : '#f1f5f9'}; border-radius:8px; display:flex; align-items:center; justify-content:center;">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="${u.active ? '#3b82f6' : '#94a3b8'}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>
          </svg>
        </div>
        <div style="flex:1; min-width:0;">
          <div style="font-weight:600; font-size:0.88rem; color:#0f172a;">
            ${u.displayName || u.username}${isYou ? ' <span style="color:#94a3b8; font-weight:400;">(you)</span>' : ''}
          </div>
          <div style="font-size:0.75rem; color:#64748b;">@${u.username} &middot; <span style="color:${statusColor};">${statusText}</span></div>
        </div>
        ${roleBadge}
        ${!isYou ? `
          <button onclick="window.openEditUserModal(${u.id}, '${u.username}', '${(u.displayName || '').replace(/'/g, "\\'")}', '${u.role}', ${u.active})"
            style="background:none; border:none; cursor:pointer; color:#64748b; padding:4px;" title="Edit">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
            </svg>
          </button>
        ` : ''}
      </div>`;
  }).join('');
}

function openAddUserModal() {
  document.getElementById('userModalTitle').textContent = 'Add User';
  document.getElementById('userModalId').value = '';
  document.getElementById('userModalUsername').value = '';
  document.getElementById('userModalUsername').disabled = false;
  document.getElementById('userModalDisplayName').value = '';
  document.getElementById('userModalPassword').value = '';
  document.getElementById('userModalPassword').placeholder = 'Enter password';
  document.getElementById('userModalPwHint').style.display = 'none';
  document.getElementById('userModalRole').value = 'operator';
  document.getElementById('userModalError').style.display = 'none';
  document.getElementById('userModal').classList.add('open');
}

function openEditUserModal(id, username, displayName, role, active) {
  document.getElementById('userModalTitle').textContent = 'Edit User';
  document.getElementById('userModalId').value = id;
  document.getElementById('userModalUsername').value = username;
  document.getElementById('userModalUsername').disabled = true;
  document.getElementById('userModalDisplayName').value = displayName;
  document.getElementById('userModalPassword').value = '';
  document.getElementById('userModalPassword').placeholder = '(unchanged)';
  document.getElementById('userModalPwHint').style.display = '';
  document.getElementById('userModalRole').value = role;
  document.getElementById('userModalError').style.display = 'none';
  document.getElementById('userModal').classList.add('open');
}

async function saveUser() {
  const id = document.getElementById('userModalId').value;
  const username = document.getElementById('userModalUsername').value.trim();
  const displayName = document.getElementById('userModalDisplayName').value.trim();
  const password = document.getElementById('userModalPassword').value;
  const role = document.getElementById('userModalRole').value;
  const errorEl = document.getElementById('userModalError');

  if (!id && !username) {
    errorEl.textContent = 'Username is required'; errorEl.style.display = ''; return;
  }
  if (!id && password.length < 4) {
    errorEl.textContent = 'Password must be at least 4 characters'; errorEl.style.display = ''; return;
  }

  let result;
  if (id) {
    // Edit existing
    const data = { displayName, role };
    if (password) data.password = password;
    result = await agentBridge.updateUser(id, data);
  } else {
    // Create new
    result = await agentBridge.createUser({ username, password, displayName, role });
  }

  if (result.error) {
    errorEl.textContent = result.error; errorEl.style.display = ''; return;
  }

  document.getElementById('userModal').classList.remove('open');
  loadUserList();
  settingsShowResult(id ? 'User updated.' : 'User created.', true);
}

async function doChangePassword() {
  const current = document.getElementById('changeCurrentPw').value;
  const newPw = document.getElementById('changeNewPw').value;
  const resultEl = document.getElementById('changePwResult');

  if (!current || !newPw) {
    resultEl.textContent = 'Both fields are required.';
    resultEl.style.color = '#dc2626'; resultEl.style.display = ''; return;
  }
  if (newPw.length < 4) {
    resultEl.textContent = 'New password must be at least 4 characters.';
    resultEl.style.color = '#dc2626'; resultEl.style.display = ''; return;
  }

  const result = await agentBridge.changePassword(current, newPw);
  if (result.error) {
    resultEl.textContent = result.error;
    resultEl.style.color = '#dc2626'; resultEl.style.display = ''; return;
  }

  resultEl.textContent = 'Password changed successfully!';
  resultEl.style.color = '#16a34a'; resultEl.style.display = '';
  document.getElementById('changeCurrentPw').value = '';
  document.getElementById('changeNewPw').value = '';
  setTimeout(() => { resultEl.style.display = 'none'; }, 5000);
}

// ── QBO API Connection ──
async function loadQboApiStatus() {
  if (!state.agentConnected) {
    document.getElementById('settingsQboApiStatus').textContent = 'Agent offline';
    return;
  }

  try {
    const status = await agentBridge.checkQBOStatus();

    // Update API connection status
    const api = status.api || {};
    const statusEl = document.getElementById('settingsQboApiStatus');
    const connectedInfo = document.getElementById('qboApiConnectedInfo');
    const connectBtn = document.getElementById('qboApiConnectBtn');
    const disconnectBtn = document.getElementById('qboApiDisconnectBtn');
    const reauthWarning = document.getElementById('qboApiReauthWarning');

    if (api.connected) {
      statusEl.textContent = 'Connected';
      statusEl.style.color = '#16a34a';
      connectedInfo.style.display = '';
      disconnectBtn.style.display = '';
      document.getElementById('qboApiConnectBtnText').textContent = 'Re-authorize';

      const realmInfo = document.getElementById('qboApiRealmInfo');
      realmInfo.textContent = `Company ID: ${api.realm_id || 'unknown'}${api.sandbox ? ' (Sandbox)' : ''}`;

      const tokenInfo = document.getElementById('qboApiTokenInfo');
      if (api.refresh_token_days_remaining != null) {
        tokenInfo.textContent = `Token expires in ${api.refresh_token_days_remaining} days`;
      }

      reauthWarning.style.display = api.needs_reauth_warning ? '' : 'none';
    } else {
      statusEl.textContent = 'Not connected \u2014 authorize below';
      statusEl.style.color = '#94a3b8';
      connectedInfo.style.display = 'none';
      disconnectBtn.style.display = 'none';
      reauthWarning.style.display = 'none';
      document.getElementById('qboApiConnectBtnText').textContent = 'Connect QBO API';
    }
  } catch (e) {
    document.getElementById('settingsQboApiStatus').textContent = 'Error loading status';
  }
}

let _oauthPollTimer = null;

async function connectQboApi() {
  if (!state.agentConnected) {
    settingsShowResult('Agent is offline. Start the agent first.', false);
    return;
  }
  try {
    const resp = await agentBridge._authFetch(`${agentBridge.baseUrl}/qbo/oauth/auth-url`);
    if (resp.ok) {
      const data = await resp.json();
      if (data.auth_url) {
        window.open(data.auth_url, '_blank');
        showOAuthPolling();
        return;
      }
    }
  } catch (e) {
    // Fallback to the manual authorize page
  }
  window.open(`${agentBridge.baseUrl}/qbo/oauth/authorize`, '_blank');
  showOAuthPolling();
}

function showOAuthPolling() {
  // Remove existing UI if any
  const existing = document.getElementById('qboOAuthPasteBox');
  if (existing) existing.remove();
  if (_oauthPollTimer) { clearInterval(_oauthPollTimer); _oauthPollTimer = null; }

  const container = document.getElementById('qboApiConnectBtn').parentElement;
  const box = document.createElement('div');
  box.id = 'qboOAuthPasteBox';
  box.style.cssText = 'margin-top:14px;background:#fff7ed;border:1px solid #fed7aa;border-radius:10px;padding:16px;text-align:center;';
  box.innerHTML = `
    <div id="qboOAuthSpinner" style="display:flex;align-items:center;justify-content:center;gap:10px;margin-bottom:8px;">
      <svg width="20" height="20" viewBox="0 0 24 24" style="animation:spin 1s linear infinite;">
        <circle cx="12" cy="12" r="10" fill="none" stroke="#ea580c" stroke-width="3" stroke-dasharray="50 20" stroke-linecap="round"/>
      </svg>
      <span style="font-weight:600;font-size:0.85rem;color:#c2410c;">Waiting for authorization...</span>
    </div>
    <p style="margin:0 0 10px;font-size:0.78rem;color:#94a3b8;">Complete the sign-in in the browser tab that just opened. This will update automatically.</p>
    <p id="qboOAuthResultMsg" style="display:none;margin:10px 0 0;padding:8px;border-radius:8px;font-size:0.82rem;"></p>
    <div id="qboOAuthManualFallback" style="display:none;margin-top:12px;border-top:1px solid #fed7aa;padding-top:12px;">
      <p style="margin:0 0 8px;font-weight:600;font-size:0.82rem;color:#64748b;">Having trouble? Paste the redirect URL manually:</p>
      <input type="text" id="qboOAuthRedirectUrl" placeholder="Paste the full redirect URL here..."
             style="width:100%;padding:9px 12px;border:1px solid #d1d5db;border-radius:8px;font-size:0.84rem;box-sizing:border-box;margin-bottom:10px;" />
      <button onclick="window.submitOAuthUrl()" id="qboOAuthSubmitBtn"
              style="background:#ea580c;color:#fff;padding:8px 20px;border:none;border-radius:8px;font-weight:600;font-size:0.84rem;cursor:pointer;">
        Complete Connection
      </button>
    </div>
  `;
  // Add spin animation if not already present
  if (!document.getElementById('_nglSpinStyle')) {
    const style = document.createElement('style');
    style.id = '_nglSpinStyle';
    style.textContent = '@keyframes spin{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}';
    document.head.appendChild(style);
  }
  container.appendChild(box);

  // Poll /qbo/status every 2 seconds for up to 5 minutes
  const startTime = Date.now();
  const TIMEOUT_MS = 5 * 60 * 1000; // 5 minutes

  _oauthPollTimer = setInterval(async () => {
    try {
      const status = await agentBridge.checkQBOStatus();
      if (status && status.api && status.api.connected) {
        clearInterval(_oauthPollTimer);
        _oauthPollTimer = null;
        // Show success
        const spinner = document.getElementById('qboOAuthSpinner');
        if (spinner) spinner.innerHTML = `
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#16a34a" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>
          </svg>
          <span style="font-weight:600;font-size:0.85rem;color:#16a34a;">QBO API Connected!</span>
        `;
        setTimeout(() => {
          document.getElementById('qboOAuthPasteBox')?.remove();
          loadQboApiStatus();
        }, 2000);
        return;
      }
    } catch { /* keep polling */ }

    // Check timeout
    if (Date.now() - startTime > TIMEOUT_MS) {
      clearInterval(_oauthPollTimer);
      _oauthPollTimer = null;
      const spinner = document.getElementById('qboOAuthSpinner');
      if (spinner) spinner.innerHTML = `
        <span style="font-weight:600;font-size:0.85rem;color:#dc2626;">Authorization timed out.</span>
      `;
      // Show manual paste fallback
      const fallback = document.getElementById('qboOAuthManualFallback');
      if (fallback) fallback.style.display = '';
    }
  }, 2000);
}

window.submitOAuthUrl = async function() {
  const url = document.getElementById('qboOAuthRedirectUrl').value.trim();
  const btn = document.getElementById('qboOAuthSubmitBtn');
  const msg = document.getElementById('qboOAuthResultMsg');
  if (!url) { msg.textContent = 'Please paste the URL first.'; msg.style.display=''; msg.style.color='#dc2626'; return; }

  let params;
  try { params = new URL(url).searchParams; } catch(e) {
    msg.textContent = 'Invalid URL. Copy the full URL from the address bar.';
    msg.style.display = ''; msg.style.color = '#dc2626'; return;
  }
  const code = params.get('code');
  const oauthState = params.get('state');
  const realmId = params.get('realmId');
  if (!code) {
    msg.textContent = 'No authorization code found. Make sure you clicked Connect on Intuit\'s page.';
    msg.style.display = ''; msg.style.color = '#dc2626'; return;
  }

  btn.disabled = true; btn.textContent = 'Connecting...';
  try {
    const resp = await fetch(`${agentBridge.baseUrl}/qbo/oauth/callback?code=${encodeURIComponent(code)}&state=${encodeURIComponent(oauthState || '')}&realmId=${encodeURIComponent(realmId || '')}`);
    const text = await resp.text();
    if (resp.ok && text.includes('Connected')) {
      msg.innerHTML = '<strong style="color:#16a34a;">QBO API Connected!</strong>';
      msg.style.display = ''; msg.style.background = '#f0fdf4'; msg.style.border = '1px solid #bbf7d0';
      setTimeout(() => { document.getElementById('qboOAuthPasteBox')?.remove(); loadQboApiStatus(); }, 2000);
    } else {
      msg.textContent = 'Connection failed: ' + text;
      msg.style.display = ''; msg.style.color = '#dc2626';
      btn.disabled = false; btn.textContent = 'Complete Connection';
    }
  } catch (e) {
    msg.textContent = 'Error: ' + e.message;
    msg.style.display = ''; msg.style.color = '#dc2626';
    btn.disabled = false; btn.textContent = 'Complete Connection';
  }
}

async function disconnectQboApi() {
  if (!state.agentConnected) return;

  try {
    const result = await agentBridge.disconnectQboApi();
    if (result.error) {
      settingsShowResult('Failed to disconnect: ' + result.error, false);
      return;
    }
    settingsShowResult('QBO API disconnected.', true);
    await loadQboApiStatus();
  } catch (e) {
    settingsShowResult('Failed to disconnect: ' + e.message, false);
  }
}

// ── Window assignments for inline HTML handlers ──
window.settingsSaveAndConnect = settingsSaveAndConnect;
window.settingsLoad = settingsLoad;
window.runSelectorHealthCheck = runSelectorHealthCheck;
window.toggleNotifications = toggleNotifications;
window.openAddUserModal = openAddUserModal;
window.openEditUserModal = openEditUserModal;
window.saveUser = saveUser;
window.doChangePassword = doChangePassword;
window.connectQboApi = connectQboApi;
window.disconnectQboApi = disconnectQboApi;
window.saveEmailConfig = saveEmailConfig;
window.testEmailConfig = testEmailConfig;
