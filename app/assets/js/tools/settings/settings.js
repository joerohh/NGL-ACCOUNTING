// ══════════════════════════════════════════════════════════
//  SETTINGS — Connections (QBO / TMS / Gmail), Preferences, Advanced
// ══════════════════════════════════════════════════════════
import { state } from '../../shared/state.js';
import { agentBridge } from '../../shared/agent-client.js';
import { agentHealthCheck } from '../../agent-ui.js';

// ── Entry point ──
export async function settingsLoad() {
  loadNotificationState();

  if (!state.agentConnected) {
    const qboS = document.getElementById('qboConnStatus');
    const tmsS = document.getElementById('tmsConnStatus');
    const gmailS = document.getElementById('gmailConnStatus');
    if (qboS) qboS.textContent = 'Agent offline';
    if (tmsS) tmsS.textContent = 'Agent offline';
    if (gmailS) gmailS.textContent = 'Agent offline';
    return;
  }

  await Promise.all([
    loadQboConnRow(),
    loadTmsConnRow(),
    loadGmailConnRow(),
  ]);
}

// ── Row loaders ──
async function loadQboConnRow() {
  const statusEl = document.getElementById('qboConnStatus');
  const pillEl = document.getElementById('qboConnPill');
  const actionEl = document.getElementById('qboConnAction');
  if (!statusEl || !pillEl || !actionEl) return;

  try {
    const status = await agentBridge.checkQBOStatus();
    const api = (status && status.api) || {};

    if (api.connected) {
      let statusText = 'Connected';
      if (api.realm_id) statusText += ` — Company ${api.realm_id}${api.sandbox ? ' (Sandbox)' : ''}`;
      if (api.refresh_token_days_remaining != null) {
        statusText += ` · token expires in ${api.refresh_token_days_remaining}d`;
      }
      statusEl.textContent = statusText;
      pillEl.textContent = api.needs_reauth_warning ? 'Re-auth soon' : 'Connected';
      pillEl.className = 'status-pill ' + (api.needs_reauth_warning ? 'pill-warn' : 'pill-ok');
      actionEl.textContent = api.needs_reauth_warning ? 'Re-authorize' : 'Disconnect';
      window.qboConnAction = api.needs_reauth_warning ? connectQboApi : disconnectQboApi;
    } else {
      statusEl.textContent = 'Not connected';
      pillEl.textContent = 'Not connected';
      pillEl.className = 'status-pill pill-off';
      actionEl.textContent = 'Connect';
      window.qboConnAction = connectQboApi;
    }
  } catch (e) {
    statusEl.textContent = 'Could not load';
    pillEl.textContent = 'Error';
    pillEl.className = 'status-pill pill-warn';
    actionEl.textContent = 'Connect';
    window.qboConnAction = connectQboApi;
  }
}

async function loadTmsConnRow() {
  const statusEl = document.getElementById('tmsConnStatus');
  const pillEl = document.getElementById('tmsConnPill');
  if (!statusEl || !pillEl) return;

  const creds = await agentBridge.getCredentials();
  if (creds.error) {
    statusEl.textContent = 'Could not load';
    pillEl.textContent = 'Error';
    pillEl.className = 'status-pill pill-warn';
    return;
  }

  if (creds.tms_configured) {
    statusEl.textContent = creds.tms_email || 'Configured';
    pillEl.textContent = 'Configured';
    pillEl.className = 'status-pill pill-ok';
  } else {
    statusEl.textContent = 'Not configured';
    pillEl.textContent = 'Not set';
    pillEl.className = 'status-pill pill-warn';
  }

  // Pre-fill the modal inputs for when user opens Edit
  const emailInput = document.getElementById('settingsTmsEmail');
  const pwInput = document.getElementById('settingsTmsPassword');
  if (emailInput && creds.tms_email) emailInput.value = creds.tms_email;
  if (pwInput) {
    pwInput.value = '';
    pwInput.placeholder = creds.tms_configured ? '(saved — enter new to change)' : 'Enter password';
  }
}

async function loadGmailConnRow() {
  const statusEl = document.getElementById('gmailConnStatus');
  const pillEl = document.getElementById('gmailConnPill');
  if (!statusEl || !pillEl) return;

  const cfg = await agentBridge.getEmailConfig();
  if (cfg.error) {
    statusEl.textContent = 'Could not load';
    pillEl.textContent = 'Error';
    pillEl.className = 'status-pill pill-warn';
    return;
  }

  if (cfg.configured) {
    statusEl.textContent = cfg.gmail_address || 'Configured';
    pillEl.textContent = 'Configured';
    pillEl.className = 'status-pill pill-ok';
  } else {
    statusEl.textContent = 'App password not set';
    pillEl.textContent = 'Not set';
    pillEl.className = 'status-pill pill-warn';
  }

  // Pre-fill the modal inputs
  const addrInput = document.getElementById('settingsGmailAddress');
  const pwInput = document.getElementById('settingsGmailAppPassword');
  if (addrInput && cfg.gmail_address) addrInput.value = cfg.gmail_address;
  if (pwInput) {
    pwInput.value = '';
    pwInput.placeholder = cfg.configured ? '(saved — enter new to change)' : '16-character app password';
  }
}

// ── Email (Gmail) Settings helpers ──
async function loadEmailConfig() {
  // Backwards-compatible thin wrapper used by other callers (if any).
  return loadGmailConnRow();
}

function showEmailResult(msg, success) {
  const el = document.getElementById('emailResultMsg');
  if (!el) return;
  el.textContent = msg;
  el.style.display = '';
  el.style.color = success ? '#15803d' : '#dc2626';
  el.style.background = success ? '#f0fdf4' : '#fef2f2';
  el.style.border = '1px solid ' + (success ? '#bbf7d0' : '#fecaca');
  el.style.padding = '8px 10px';
  el.style.borderRadius = '8px';
  setTimeout(function() { el.style.display = 'none'; }, 10000);
}

async function saveEmailConfig() {
  if (!state.agentConnected) { showEmailResult('Agent is offline.', false); return { ok: false }; }
  const btn = document.getElementById('emailSaveBtn');
  const btnText = document.getElementById('emailSaveBtnText');
  const addr = document.getElementById('settingsGmailAddress').value.trim();
  const pw = document.getElementById('settingsGmailAppPassword').value;

  if (!addr) { showEmailResult('Enter your Gmail address.', false); return { ok: false }; }

  const payload = { gmail_address: addr };
  if (pw) payload.gmail_app_password = pw;

  if (btn) btn.disabled = true;
  if (btnText) btnText.textContent = 'Saving...';
  const result = await agentBridge.saveEmailConfig(payload);
  if (btn) btn.disabled = false;
  if (btnText) btnText.textContent = 'Save';

  if (result.error) { showEmailResult('Failed: ' + result.error, false); return { ok: false }; }
  showEmailResult(
    result.configured ? 'Saved! Emails will be sent from ' + result.gmail_address + '.' : 'Saved (app password still needed).',
    !!result.configured
  );
  await loadGmailConnRow();
  return { ok: true, configured: !!result.configured };
}

async function testEmailConfig() {
  if (!state.agentConnected) { showEmailResult('Agent is offline.', false); return; }
  const btn = document.getElementById('emailTestBtn');
  const origText = btn ? btn.textContent : 'Test';
  const addr = document.getElementById('settingsGmailAddress').value.trim();
  const pw = document.getElementById('settingsGmailAppPassword').value;

  const payload = {};
  if (addr) payload.gmail_address = addr;
  if (pw) payload.gmail_app_password = pw;
  if (addr) payload.to = addr; // send test to self

  if (btn) { btn.disabled = true; btn.textContent = 'Sending...'; }
  const result = await agentBridge.testEmailConfig(payload);
  if (btn) { btn.disabled = false; btn.textContent = origText || 'Test'; }

  if (result.sent) {
    showEmailResult('Test email sent! Check your inbox at ' + (addr || 'the saved address') + '.', true);
  } else {
    showEmailResult('Test failed: ' + (result.error || 'unknown error'), false);
  }
}

function settingsShowResult(msg, success) {
  const el = document.getElementById('settingsResultMsg');
  if (!el) return;
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
  if (btn) { btn.disabled = true; btn.textContent = 'Checking...'; }

  const resultsDiv = document.getElementById('healthCheckResults');
  if (resultsDiv) resultsDiv.style.display = '';

  const tms = await agentBridge.checkTmsSelectorHealth();
  const tmsEl = document.getElementById('healthCheckTms');
  if (tmsEl) tmsEl.innerHTML = formatHealthResult('TMS', tms);

  if (btn) { btn.disabled = false; btn.textContent = 'Run Check'; }
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
      const cb = document.getElementById('settingsNotifyEnabled');
      if (cb) cb.checked = false;
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

// ── QBO API Connection ──
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

  // Attach next to the QBO action button (inside the Connections card row)
  const actionBtn = document.getElementById('qboConnAction');
  const container = actionBtn ? actionBtn.closest('.connections-card') : null;
  if (!container) {
    // Fallback: settings result message
    settingsShowResult('Waiting for QBO authorization in your browser…', true);
    return;
  }
  const box = document.createElement('div');
  box.id = 'qboOAuthPasteBox';
  box.style.cssText = 'margin:10px 0 14px;background:#fff7ed;border:1px solid #fed7aa;border-radius:10px;padding:16px;text-align:center;';
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
  container.parentElement.insertBefore(box, container.nextSibling);

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
          loadQboConnRow();
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
      setTimeout(() => { document.getElementById('qboOAuthPasteBox')?.remove(); loadQboConnRow(); }, 2000);
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
};

async function disconnectQboApi() {
  if (!state.agentConnected) return;

  try {
    const result = await agentBridge.disconnectQboApi();
    if (result.error) {
      settingsShowResult('Failed to disconnect: ' + result.error, false);
      return;
    }
    settingsShowResult('QBO API disconnected.', true);
    await loadQboConnRow();
  } catch (e) {
    settingsShowResult('Failed to disconnect: ' + e.message, false);
  }
}

// ── TMS / Gmail modal open helpers + save handlers ──
function openTmsEditModal() {
  // Refresh the pre-filled email value if creds are loaded
  document.getElementById('tmsEditError').style.display = 'none';
  document.getElementById('tmsEditModal').classList.add('open');
}

function openGmailEditModal() {
  document.getElementById('gmailEditError').style.display = 'none';
  const emailResult = document.getElementById('emailResultMsg');
  if (emailResult) emailResult.style.display = 'none';
  document.getElementById('gmailEditModal').classList.add('open');
}

async function saveTmsCredentials() {
  const email = document.getElementById('settingsTmsEmail').value.trim();
  const password = document.getElementById('settingsTmsPassword').value;
  const errEl = document.getElementById('tmsEditError');
  errEl.style.display = 'none';

  if (!email) {
    errEl.textContent = 'Email is required';
    errEl.style.display = '';
    return;
  }
  if (!state.agentConnected) {
    errEl.textContent = 'Agent is offline. Start the agent first.';
    errEl.style.display = '';
    return;
  }

  const payload = { tms_email: email };
  if (password) payload.tms_password = password;

  const result = await agentBridge.saveAndConnect(payload);
  if (result.error) {
    errEl.textContent = 'Failed: ' + result.error;
    errEl.style.display = '';
    return;
  }

  document.getElementById('tmsEditModal').classList.remove('open');
  settingsShowResult('TMS credentials saved.', true);
  await loadTmsConnRow();
  agentHealthCheck();
}

async function saveGmailCredentials() {
  const result = await saveEmailConfig();
  if (result && result.ok) {
    // Brief delay so user sees the success message inside the modal before close
    setTimeout(() => {
      document.getElementById('gmailEditModal').classList.remove('open');
      loadGmailConnRow();
    }, 800);
  }
}

// ── Window assignments for inline HTML handlers ──
window.settingsLoad = settingsLoad;
window.runSelectorHealthCheck = runSelectorHealthCheck;
window.toggleNotifications = toggleNotifications;
window.connectQboApi = connectQboApi;
window.disconnectQboApi = disconnectQboApi;
window.saveEmailConfig = saveEmailConfig;
window.testEmailConfig = testEmailConfig;
window.openTmsEditModal = openTmsEditModal;
window.openGmailEditModal = openGmailEditModal;
window.saveTmsCredentials = saveTmsCredentials;
window.saveGmailCredentials = saveGmailCredentials;
