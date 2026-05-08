// ══════════════════════════════════════════════════════════
//  AGENT UI — Agent panel DOM functions, health check, login buttons
// ══════════════════════════════════════════════════════════
import { state } from './shared/state.js';
import { escHtml } from './shared/utils.js';
import { addLog } from './shared/log.js';
import { agentBridge } from './shared/agent-client.js';
import { invUpdateGenerateBtn } from './tools/invoice-sender/invoice-sender.js';

function toggleAgentPanel() {
  const body  = document.getElementById('agentBody');
  const arrow = document.getElementById('agentToggleArrow');
  if (!body || !arrow) return;   // legacy panel removed in v2.55 — toggle is a no-op
  const isHidden = body.style.display === 'none';
  body.style.display = isHidden ? '' : 'none';
  arrow.classList.toggle('collapsed', !isHidden);
}


export async function agentHealthCheck() {
  const data = await agentBridge.checkHealth();
  // Legacy panel DOM nodes — may be null after v2.55 cutover. Every assignment below
  // is guarded so a missing panel becomes a no-op instead of crashing the health loop.
  const dot    = document.getElementById('agentDot');
  const text   = document.getElementById('agentStatusText');
  const clsStatus = document.getElementById('classifierStatus');
  const clsDetails = document.getElementById('classifierDetails');
  const clsUsage = document.getElementById('classifierUsage');
  const qboEl = document.getElementById('qboStatus');

  if (data && data.status === 'ok') {
    state.agentConnected = true;
    if (dot)  dot.className = 'agent-status-dot online';
    if (text) { text.textContent = 'Connected'; text.style.color = '#16a34a'; }
    updateHeaderAgentButtons(true);

    // Classifier status
    if (data.classifier === 'ready') {
      if (clsStatus)  { clsStatus.textContent = 'Ready'; clsStatus.style.color = '#16a34a'; }
      if (clsDetails) clsDetails.style.display = '';
      if (clsUsage)   clsUsage.textContent = `Calls: ${data.api_calls_today || 0}/${data.api_limit || 200} · ${data.estimated_cost_today || '$0.00'}`;
    } else {
      if (clsStatus)  { clsStatus.textContent = 'No key'; clsStatus.style.color = '#d97706'; }
      if (clsDetails) clsDetails.style.display = 'none';
    }

    // Session alert notifications from keep-alive auto-reconnect
    if (data.session_alerts) {
      if (data.session_alerts.tms_needs_login) {
        addLog('warning', '[Agent] TMS session expired — auto-login failed. Please log in manually in Chrome.');
        showBrowserNotification('TMS Session Expired', 'Auto-login failed. Please log in manually.');
      }
    }

    // One-time bidirectional customer sync when agent first connects.
    if (!state._agentCustomersSynced) {
      state._agentCustomersSynced = true;
      try {
        // Step 1: Pull from agent → localStorage (agent is source of truth)
        const agentRes = await agentBridge._authFetch(agentBridge.baseUrl + '/customers?activeOnly=false');
        if (agentRes.ok) {
          const agentData = await agentRes.json();
          const agentCustomers = agentData.customers || [];
          if (agentCustomers.length > 0) {
            const merged = agentBridge._custRead();
            for (const c of agentCustomers) {
              const key = (c.code || '').toUpperCase();
              if (!key) continue;
              const local = merged[key];
              if (!local || (c.updatedAt && (!local.updatedAt || c.updatedAt >= local.updatedAt))) {
                merged[key] = c;
              }
            }
            agentBridge._custWrite(merged);
          }
        }
        // Step 2: Push any localStorage-only entries back to agent
        const allCust = Object.values(agentBridge._custRead());
        if (allCust.length > 0) {
          agentBridge._authFetch(agentBridge.baseUrl + '/customers/import', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ customers: allCust }),
          }).catch(() => {});
        }
      } catch (_) {}
    }

    // Update Send QBO button state
    invUpdateGenerateBtn();

    // Passive QBO status — just reads current URL, no navigation
    if (!state.activeJobId) {
      const qbo = await agentBridge.checkQBOStatus();
      state.qboConnected = !!qbo.loggedIn;
      if (qboEl) {
        if (qbo.loggedIn) {
          qboEl.textContent = 'Connected';
          qboEl.style.color = '#16a34a';
        } else {
          qboEl.textContent = 'Not connected';
          qboEl.style.color = '#d97706';
        }
      }

      // Passive TMS browser status (fallback path — rare since v2.34)
      // Persistent login prompt is hidden by default; the in-flow prompt
      // (tms_login_required SSE event) still fires when a job actually
      // needs the browser.
      const tmsEl = document.getElementById('tmsStatus');
      const tmsLoginSection = document.getElementById('tmsLoginSection');
      if (tmsLoginSection) tmsLoginSection.style.display = 'none';
      const tmsData = await agentBridge.checkTMSStatus();
      state.tmsLoggedIn = !!tmsData.loggedIn;
      if (tmsEl) {
        if (tmsData.loggedIn) {
          tmsEl.textContent = 'Logged in';
          tmsEl.style.color = '#16a34a';
        } else {
          tmsEl.textContent = 'Idle';
          tmsEl.style.color = '#94a3b8';
        }
      }

      // TMS REST API status (primary path)
      const tmsApiEl = document.getElementById('tmsApiStatus');
      if (tmsApiEl) {
        const apiData = await agentBridge.checkTMSApiStatus();
        if (apiData.tokenOk) {
          tmsApiEl.textContent = 'Connected';
          tmsApiEl.style.color = '#16a34a';
          tmsApiEl.title = 'TMS REST API token OK — ' + (apiData.baseUrl || '');
        } else if (apiData.status === 'not_configured') {
          tmsApiEl.textContent = 'Not configured';
          tmsApiEl.style.color = '#94a3b8';
          tmsApiEl.title = apiData.message || '';
        } else {
          tmsApiEl.textContent = 'Error';
          tmsApiEl.style.color = '#dc2626';
          tmsApiEl.title = apiData.error || 'Could not reach TMS API';
        }
      }
    }
  } else {
    state.agentConnected = false;
    state.qboConnected = false;
    state.tmsLoggedIn = false;
    state._agentCustomersSynced = false;  // re-sync on next connect
    if (dot)        dot.className = 'agent-status-dot offline';
    if (text)       { text.textContent = 'Offline'; text.style.color = '#94a3b8'; }
    updateHeaderAgentButtons(false);
    if (clsStatus)  { clsStatus.textContent = '--'; clsStatus.style.color = '#94a3b8'; }
    if (clsDetails) clsDetails.style.display = 'none';
    if (qboEl)      { qboEl.textContent = '--'; qboEl.style.color = '#94a3b8'; }
    const tmsElOff = document.getElementById('tmsStatus');
    if (tmsElOff) { tmsElOff.textContent = '--'; tmsElOff.style.color = '#94a3b8'; }
    const tmsApiElOff = document.getElementById('tmsApiStatus');
    if (tmsApiElOff) { tmsApiElOff.textContent = '--'; tmsApiElOff.style.color = '#94a3b8'; }
    const tmsLoginOff = document.getElementById('tmsLoginSection');
    if (tmsLoginOff) tmsLoginOff.style.display = 'none';

    // Update Send QBO button state
    invUpdateGenerateBtn();
  }

  // Update home page connection cards
  _updateHomeConnections();
}

function _updateHomeConnections() {
  const setPill = (id, status, isConnected, showBtn) => {
    const pill = document.getElementById(id + 'Card');
    const stat = document.getElementById(id + 'Status');
    const dot  = document.getElementById(id + 'Dot');
    const btn  = document.getElementById(id + 'Btn');
    if (!pill) return;
    pill.className = 'status-pill' + (state.agentConnected ? (isConnected ? ' connected' : ' disconnected') : ' offline');
    if (stat) stat.textContent = status;
    if (dot) dot.className = 'status-pill-dot ' + (isConnected ? 'green' : state.agentConnected ? 'amber' : 'gray');
    if (btn) btn.style.display = (showBtn && state.agentConnected && !isConnected) ? '' : 'none';
  };

  if (!state.agentConnected) {
    setPill('homeQbo', 'Offline', false, false);
    setPill('homeTms', 'Offline', false, false);
    return;
  }

  // Read directly from state — agentHealthCheck updates state.qboConnected /
  // state.tmsLoggedIn after each agent + QBO + TMS poll. (Previously these
  // were derived by reading textContent of legacy panel DOM nodes that the
  // v2.55 cutover removed, which always returned null and made these pills
  // permanently stuck on "Not connected" / "Idle" even when the merge tool
  // was successfully talking to QBO.)
  setPill('homeQbo', state.qboConnected ? 'Connected' : 'Not connected', state.qboConnected, false);
  setPill('homeTms', state.tmsLoggedIn ? 'Logged in' : 'Idle', state.tmsLoggedIn, false);
}

function agentHeaderBtnClick() {
  if (state.agentConnected) {
    addLog('info', '[Agent] Agent server is connected and running.');
    return;
  }
  addLog('warn', '[Agent] Agent is offline. Run "Start Agent.bat" in the agent folder, then this will connect automatically.');
}

function updateHeaderAgentButtons(connected) {
  const dots  = [document.getElementById('agentDotMerge'), document.getElementById('agentDotInvoice'), document.getElementById('agentDotChassis')];
  const texts = [document.getElementById('agentTextMerge'), document.getElementById('agentTextInvoice'), document.getElementById('agentTextChassis')];
  const btns  = [document.getElementById('agentBtnMerge'), document.getElementById('agentBtnInvoice'), document.getElementById('agentBtnChassis')];

  dots.forEach(d => { if (d) d.className = 'agent-hdr-dot ' + (connected ? 'online' : 'offline'); });
  texts.forEach(t => {
    if (!t) return;
    t.textContent = connected ? 'Agent Online' : 'Agent Offline';
  });
  btns.forEach(b => {
    if (!b) return;
    b.title = connected ? 'Agent Server is connected' : 'Click to see how to start the Agent';
    b.style.borderColor = connected ? '#bbf7d0' : '#e2e8f0';
    b.style.background = connected ? '#f0fdf4' : '#fff';
  });
}

async function agentOpenTMSLogin() {
  if (!state.agentConnected) {
    addLog('error', '[Agent] Agent server is offline.');
    return;
  }

  // Legacy panel DOM nodes — may be null after v2.55 cutover. All updates guarded.
  const btn = document.getElementById('tmsLoginBtn');
  const btnText = document.getElementById('tmsLoginBtnText');
  const tmsEl = document.getElementById('tmsStatus');
  if (btn) btn.disabled = true;
  if (btnText) btnText.textContent = 'Opening Chrome...';

  addLog('info', '[Agent] Opening TMS login page in Chrome...');
  const result = await agentBridge.openTMSLogin();

  if (result.status === 'login_page_opened') {
    if (tmsEl) { tmsEl.textContent = 'Waiting...'; tmsEl.style.color = '#d97706'; }
    if (btnText) btnText.textContent = 'Waiting for Google SSO...';
    addLog('info', '[Agent] Chrome window opened — sign in with Google, then come back');

    try {
      const waitRes = await agentBridge._authFetch(agentBridge.baseUrl + '/tms/wait-for-login', { method: 'POST' });
      const waitData = await waitRes.json();
      if (waitData.status === 'logged_in') {
        if (tmsEl) { tmsEl.textContent = 'Logged in'; tmsEl.style.color = '#16a34a'; }
        const tmsLoginSection = document.getElementById('tmsLoginSection');
        if (tmsLoginSection) tmsLoginSection.style.display = 'none';
        addLog('success', '[Agent] TMS login successful!');
      } else {
        if (tmsEl) { tmsEl.textContent = 'Not logged in'; tmsEl.style.color = '#d97706'; }
        if (btnText) btnText.textContent = 'Open TMS Login';
        if (btn) btn.disabled = false;
        addLog('warning', '[Agent] TMS login timed out — try again');
      }
    } catch (e) {
      if (btnText) btnText.textContent = 'Open TMS Login';
      if (btn) btn.disabled = false;
      addLog('error', '[Agent] Error waiting for TMS login: ' + e.message);
    }
  } else {
    const errMsg = result.detail || result.error || 'Unknown error';
    addLog('error', '[Agent] Failed to open TMS login: ' + errMsg);
    if (btnText) btnText.textContent = 'Retry TMS Login';
    if (btn) btn.disabled = false;
  }
}


// ══════════════════════════════════════════════════════════
//  BROWSER NOTIFICATIONS
// ══════════════════════════════════════════════════════════
function showBrowserNotification(title, body) {
  if (localStorage.getItem('ngl_notifications_enabled') !== '1') return;
  if (!('Notification' in window)) return;
  if (Notification.permission === 'granted') {
    new Notification(title, { body, icon: 'assets/images/miniNGL Logo.ico' });
  }
}

// ══════════════════════════════════════════════════════════
//  AGENT-READY API
// ══════════════════════════════════════════════════════════
window.__nglAgent = {
  async processPayload(payload) {
    addLog('info', `[Agent] Received payload — ${payload.containerNumbers.length} containers`);
    return {
      received: true,
      containerCount: payload.containerNumbers.length,
      timestamp: new Date().toISOString(),
    };
  },

  getState() {
    return {
      hasExcel: state.excelRows.length > 0,
      mergeMode: state.mergeMode,
      sortOrder: state.sortOrder,
      pdfCount: state.pdfs.length,
      containerCount: state.excelRows.length,
      mergeResultCount: state.mergeResults.length,
      agentConnected: state.agentConnected,
      activeJobId: state.activeJobId,
      pdfNames: state.pdfs.map(p => p.name),
      containers: state.excelRows.map(r => ({
        containerNumber: r.containerNumber,
        invoiceNumber: r.invoiceNumber || null,
      })),
    };
  },

  // Programmatic access to agent functions
  injectFile: (blob, name) => agentBridge.injectFile(blob, name),
};

// ── Window assignments for inline HTML handlers ──
window.toggleAgentPanel = toggleAgentPanel;
window.agentOpenTMSLogin = agentOpenTMSLogin;
window.agentHeaderBtnClick = agentHeaderBtnClick;
