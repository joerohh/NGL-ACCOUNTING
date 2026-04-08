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
    document.getElementById('settingsQboStatus').textContent = 'Error loading';
    return;
  }

  // Pre-fill emails (never pre-fill passwords)
  if (creds.qbo_email) {
    document.getElementById('settingsQboEmail').value = creds.qbo_email;
  }
  document.getElementById('settingsQboStatus').textContent = creds.qbo_configured ? 'Configured' : 'Not configured';
  document.getElementById('settingsQboStatus').style.color = creds.qbo_configured ? '#16a34a' : '#94a3b8';

  if (creds.tms_email) {
    document.getElementById('settingsTmsEmail').value = creds.tms_email;
  }
  document.getElementById('settingsTmsStatus').textContent = creds.tms_configured ? 'Configured' : 'Not configured';
  document.getElementById('settingsTmsStatus').style.color = creds.tms_configured ? '#16a34a' : '#94a3b8';

  // Clear password fields
  document.getElementById('settingsQboPassword').value = '';
  document.getElementById('settingsTmsPassword').value = '';
  document.getElementById('settingsQboPassword').placeholder = creds.qbo_configured ? '(saved \u2014 enter new to change)' : 'Enter password';
  document.getElementById('settingsTmsPassword').placeholder = creds.tms_configured ? '(saved \u2014 enter new to change)' : 'Enter password';

  // Load QBO API status
  await loadQboApiStatus();
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
  const qboEmail = document.getElementById('settingsQboEmail').value.trim();
  const qboPass = document.getElementById('settingsQboPassword').value;
  const tmsEmail = document.getElementById('settingsTmsEmail').value.trim();
  const tmsPass = document.getElementById('settingsTmsPassword').value;

  if (qboEmail) data.qbo_email = qboEmail;
  if (qboPass) data.qbo_password = qboPass;
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
  if (r.qbo === 'logged_in') msg += 'QBO: Logged in! ';
  else if (r.qbo === 'needs_manual_login') msg += 'QBO: Needs manual login (check Chrome for 2FA). ';
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

  const [qbo, tms] = await Promise.all([
    agentBridge.checkQboSelectorHealth(),
    agentBridge.checkTmsSelectorHealth(),
  ]);

  document.getElementById('healthCheckQbo').innerHTML = formatHealthResult('QBO', qbo);
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
    const mode = status.mode || 'browser';

    // Update mode toggle buttons
    updateModeToggle(mode);

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
      statusEl.textContent = mode === 'api' ? 'Not connected \u2014 authorize below' : 'Not connected';
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

function updateModeToggle(mode) {
  const browserBtn = document.getElementById('qboModeBrowserBtn');
  const apiBtn = document.getElementById('qboModeApiBtn');

  if (mode === 'api') {
    apiBtn.style.background = '#16a34a';
    apiBtn.style.color = '#fff';
    apiBtn.style.fontWeight = '600';
    browserBtn.style.background = '#f8fafc';
    browserBtn.style.color = '#64748b';
    browserBtn.style.fontWeight = '500';
  } else {
    browserBtn.style.background = '#ea580c';
    browserBtn.style.color = '#fff';
    browserBtn.style.fontWeight = '600';
    apiBtn.style.background = '#f8fafc';
    apiBtn.style.color = '#64748b';
    apiBtn.style.fontWeight = '500';
  }
}

async function setQboMode(mode) {
  if (!state.agentConnected) {
    settingsShowResult('Agent is offline. Start the agent first.', false);
    return;
  }

  updateModeToggle(mode);

  try {
    const result = await agentBridge.setQboMode(mode);
    if (result.error) {
      settingsShowResult('Failed to set QBO mode: ' + result.error, false);
      return;
    }
    settingsShowResult(`QBO mode set to: ${mode === 'api' ? 'API (Official)' : 'Browser (Playwright)'}`, true);
    await loadQboApiStatus();
  } catch (e) {
    settingsShowResult('Failed to set QBO mode: ' + e.message, false);
  }
}

function connectQboApi() {
  if (!state.agentConnected) {
    settingsShowResult('Agent is offline. Start the agent first.', false);
    return;
  }
  // Open the OAuth authorization page in a new tab
  window.open(`${agentBridge.baseUrl}/qbo/oauth/authorize`, '_blank');
  settingsShowResult('Opening QBO authorization page... After authorizing, come back and click Reload.', true);
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
window.setQboMode = setQboMode;
window.connectQboApi = connectQboApi;
window.disconnectQboApi = disconnectQboApi;
