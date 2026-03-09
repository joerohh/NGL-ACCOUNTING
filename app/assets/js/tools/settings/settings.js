// ══════════════════════════════════════════════════════════
//  SETTINGS — Credentials management page
// ══════════════════════════════════════════════════════════
import { state } from '../../shared/state.js';
import { agentBridge } from '../../shared/agent-client.js';
import { agentHealthCheck } from '../../agent-ui.js';

export async function settingsLoad() {
  loadNotificationState();

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

// ── Window assignments for inline HTML handlers ──
window.settingsSaveAndConnect = settingsSaveAndConnect;
window.settingsLoad = settingsLoad;
window.runSelectorHealthCheck = runSelectorHealthCheck;
window.toggleNotifications = toggleNotifications;
