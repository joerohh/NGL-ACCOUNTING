// ══════════════════════════════════════════════════════════
//  AGENT UI — Agent panel DOM functions, health check, login buttons
// ══════════════════════════════════════════════════════════
import { state } from './shared/state.js';
import { escHtml } from './shared/utils.js';
import { addLog } from './shared/log.js';
import { agentBridge } from './shared/agent-client.js';
import { invUpdateGenerateBtn } from './tools/invoice-sender/invoice-sender.js';
import { classifyPdf, renderContainerGroups } from './tools/merge/merge.js';

function toggleAgentPanel() {
  const body  = document.getElementById('agentBody');
  const arrow = document.getElementById('agentToggleArrow');
  const isHidden = body.style.display === 'none';
  body.style.display = isHidden ? '' : 'none';
  arrow.classList.toggle('collapsed', !isHidden);
}


export async function agentHealthCheck() {
  const data = await agentBridge.checkHealth();
  const dot    = document.getElementById('agentDot');
  const text   = document.getElementById('agentStatusText');
  const fetchBtn = document.getElementById('fetchMissingBtn');
  const clsStatus = document.getElementById('classifierStatus');
  const clsDetails = document.getElementById('classifierDetails');
  const clsUsage = document.getElementById('classifierUsage');
  const qboEl = document.getElementById('qboStatus');
  const qboLoginSection = document.getElementById('qboLoginSection');

  if (data && data.status === 'ok') {
    state.agentConnected = true;
    dot.className = 'agent-status-dot online';
    text.textContent = 'Connected';
    text.style.color = '#16a34a';
    updateHeaderAgentButtons(true);

    // Classifier status
    if (data.classifier === 'ready') {
      clsStatus.textContent = 'Ready';
      clsStatus.style.color = '#16a34a';
      clsDetails.style.display = '';
      clsUsage.textContent = `Calls: ${data.api_calls_today || 0}/${data.api_limit || 200} · ${data.estimated_cost_today || '$0.00'}`;
    } else {
      clsStatus.textContent = 'No key';
      clsStatus.style.color = '#d97706';
      clsDetails.style.display = 'none';
    }

    // Session alert notifications from keep-alive auto-reconnect
    if (data.session_alerts) {
      if (data.session_alerts.qbo_needs_login) {
        addLog('warning', '[Agent] QBO session expired — auto-login failed. Please log in manually in Chrome.');
        showBrowserNotification('QBO Session Expired', 'Auto-login failed. Please log in manually.');
      }
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

    // Fetch button enabled whenever agent is connected
    fetchBtn.disabled = false;

    // Update Send QBO button state
    invUpdateGenerateBtn();

    // Passive QBO status — just reads current URL, no navigation
    if (!state.activeJobId) {
      const qbo = await agentBridge.checkQBOStatus();
      if (qbo.loggedIn) {
        qboEl.textContent = 'Logged in';
        qboEl.style.color = '#16a34a';
        qboLoginSection.style.display = 'none';
      } else {
        qboEl.textContent = 'Not logged in';
        qboEl.style.color = '#d97706';
        qboLoginSection.style.display = '';
      }

      // Passive TMS status
      const tmsEl = document.getElementById('tmsStatus');
      const tmsLoginSection = document.getElementById('tmsLoginSection');
      if (tmsEl) {
        const tmsData = await agentBridge.checkTMSStatus();
        if (tmsData.loggedIn) {
          tmsEl.textContent = 'Logged in';
          tmsEl.style.color = '#16a34a';
          if (tmsLoginSection) tmsLoginSection.style.display = 'none';
        } else {
          tmsEl.textContent = 'Not logged in';
          tmsEl.style.color = '#d97706';
          if (tmsLoginSection) tmsLoginSection.style.display = '';
        }
      }
    }
  } else {
    state.agentConnected = false;
    state._agentCustomersSynced = false;  // re-sync on next connect
    dot.className = 'agent-status-dot offline';
    text.textContent = 'Offline';
    text.style.color = '#94a3b8';
    updateHeaderAgentButtons(false);
    fetchBtn.disabled = true;
    clsStatus.textContent = '--';
    clsStatus.style.color = '#94a3b8';
    clsDetails.style.display = 'none';
    qboEl.textContent = '--';
    qboEl.style.color = '#94a3b8';
    qboLoginSection.style.display = 'none';
    const tmsElOff = document.getElementById('tmsStatus');
    if (tmsElOff) { tmsElOff.textContent = '--'; tmsElOff.style.color = '#94a3b8'; }
    const tmsLoginOff = document.getElementById('tmsLoginSection');
    if (tmsLoginOff) tmsLoginOff.style.display = 'none';

    // Update Send QBO button state
    invUpdateGenerateBtn();
  }

  // Update home page connection cards
  _updateHomeConnections();
}

function _updateHomeConnections() {
  const setCard = (id, status, isConnected, showBtn) => {
    const card = document.getElementById(id + 'Card');
    const stat = document.getElementById(id + 'Status');
    const dot  = document.getElementById(id + 'Dot');
    const btn  = document.getElementById(id + 'Btn');
    if (!card) return;
    card.className = 'connection-card' + (state.agentConnected ? (isConnected ? ' connected' : ' disconnected') : ' offline');
    if (stat) { stat.textContent = status; stat.style.color = isConnected ? '#16a34a' : state.agentConnected ? '#d97706' : '#94a3b8'; }
    if (dot) dot.className = 'connection-dot ' + (isConnected ? 'green' : state.agentConnected ? 'amber' : 'gray');
    if (btn) btn.style.display = (showBtn && state.agentConnected && !isConnected) ? '' : 'none';
  };

  if (!state.agentConnected) {
    setCard('homeQbo', 'Agent offline', false, false);
    setCard('homeTms', 'Agent offline', false, false);
    setCard('homeCls', 'Agent offline', false, false);
    setCard('homeAgent', 'Offline', false, false);
    return;
  }

  // Agent server
  setCard('homeAgent', 'Running on :8787', true, false);

  // QBO
  const qboText = document.getElementById('qboStatus');
  const qboLoggedIn = qboText && qboText.textContent === 'Logged in';
  setCard('homeQbo', qboLoggedIn ? 'Logged in' : 'Not logged in', qboLoggedIn, true);

  // TMS
  const tmsText = document.getElementById('tmsStatus');
  const tmsLoggedIn = tmsText && tmsText.textContent === 'Logged in';
  setCard('homeTms', tmsLoggedIn ? 'Logged in' : 'Not logged in', tmsLoggedIn, true);

  // Classifier
  const clsText = document.getElementById('classifierStatus');
  const clsReady = clsText && clsText.textContent === 'Ready';
  setCard('homeCls', clsReady ? 'Ready' : 'No API key', clsReady, false);
}

function agentHeaderBtnClick() {
  if (state.agentConnected) {
    addLog('info', '[Agent] Agent server is connected and running.');
    return;
  }
  addLog('warn', '[Agent] Agent is offline. Run "Start Agent.bat" in the agent folder, then this will connect automatically.');
}

function updateHeaderAgentButtons(connected) {
  const dots  = [document.getElementById('agentDotMerge'), document.getElementById('agentDotInvoice')];
  const texts = [document.getElementById('agentTextMerge'), document.getElementById('agentTextInvoice')];
  const btns  = [document.getElementById('agentBtnMerge'), document.getElementById('agentBtnInvoice')];

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

async function agentOpenQBOLogin() {
  if (!state.agentConnected) {
    addLog('error', '[Agent] Agent server is offline. Start it with: python main.py');
    return;
  }

  const btn = document.getElementById('qboLoginBtn');
  const btnText = document.getElementById('qboLoginBtnText');
  const qboEl = document.getElementById('qboStatus');
  btn.disabled = true;
  btnText.textContent = 'Opening Chrome...';

  addLog('info', '[Agent] Opening QBO login page in Chrome...');
  const result = await agentBridge.openQBOLogin();

  if (result.status === 'login_page_opened') {
    qboEl.textContent = 'Waiting...';
    qboEl.style.color = '#d97706';
    btnText.textContent = 'Waiting for you to log in...';
    addLog('info', '[Agent] Chrome window opened — log in to QuickBooks there, then come back');

    // Poll wait-for-login endpoint (waits up to 2 min server-side)
    try {
      const waitRes = await agentBridge._authFetch(agentBridge.baseUrl + '/qbo/wait-for-login', { method: 'POST' });
      const waitData = await waitRes.json();
      if (waitData.status === 'logged_in') {
        qboEl.textContent = 'Logged in';
        qboEl.style.color = '#16a34a';
        document.getElementById('qboLoginSection').style.display = 'none';
        document.getElementById('fetchMissingBtn').disabled = false;
        addLog('success', '[Agent] QBO login successful!');
      } else {
        qboEl.textContent = 'Not logged in';
        qboEl.style.color = '#d97706';
        btnText.textContent = 'Open QBO Login';
        btn.disabled = false;
        addLog('warning', '[Agent] QBO login timed out — try again');
      }
    } catch (e) {
      btnText.textContent = 'Open QBO Login';
      btn.disabled = false;
      addLog('error', '[Agent] Error waiting for login: ' + e.message);
    }
  } else {
    const errMsg = result.detail || result.error || 'Unknown error';
    addLog('error', '[Agent] Failed to open QBO login: ' + errMsg);
    btnText.textContent = 'Retry QBO Login';
    btn.disabled = false;
    // Show inline error so user doesn't need to check the log
    let errDiv = document.getElementById('qboLoginError');
    if (!errDiv) {
      errDiv = document.createElement('div');
      errDiv.id = 'qboLoginError';
      errDiv.style.cssText = 'font-size:0.75rem; color:#dc2626; margin-top:8px; padding:6px 8px; background:#fef2f2; border-radius:6px;';
      btn.parentElement.appendChild(errDiv);
    }
    errDiv.textContent = 'Chrome failed to open. The agent will relaunch it — click Retry.';
  }
}

async function agentOpenTMSLogin() {
  if (!state.agentConnected) {
    addLog('error', '[Agent] Agent server is offline.');
    return;
  }

  const btn = document.getElementById('tmsLoginBtn');
  const btnText = document.getElementById('tmsLoginBtnText');
  const tmsEl = document.getElementById('tmsStatus');
  btn.disabled = true;
  btnText.textContent = 'Opening Chrome...';

  addLog('info', '[Agent] Opening TMS login page in Chrome...');
  const result = await agentBridge.openTMSLogin();

  if (result.status === 'login_page_opened') {
    tmsEl.textContent = 'Waiting...';
    tmsEl.style.color = '#d97706';
    btnText.textContent = 'Waiting for Google SSO...';
    addLog('info', '[Agent] Chrome window opened — sign in with Google, then come back');

    try {
      const waitRes = await agentBridge._authFetch(agentBridge.baseUrl + '/tms/wait-for-login', { method: 'POST' });
      const waitData = await waitRes.json();
      if (waitData.status === 'logged_in') {
        tmsEl.textContent = 'Logged in';
        tmsEl.style.color = '#16a34a';
        document.getElementById('tmsLoginSection').style.display = 'none';
        addLog('success', '[Agent] TMS login successful!');
      } else {
        tmsEl.textContent = 'Not logged in';
        tmsEl.style.color = '#d97706';
        btnText.textContent = 'Open TMS Login';
        btn.disabled = false;
        addLog('warning', '[Agent] TMS login timed out — try again');
      }
    } catch (e) {
      btnText.textContent = 'Open TMS Login';
      btn.disabled = false;
      addLog('error', '[Agent] Error waiting for TMS login: ' + e.message);
    }
  } else {
    const errMsg = result.detail || result.error || 'Unknown error';
    addLog('error', '[Agent] Failed to open TMS login: ' + errMsg);
    btnText.textContent = 'Retry TMS Login';
    btn.disabled = false;
  }
}

async function agentFetchMissing() {
  if (!state.agentConnected) {
    addLog('error', '[Agent] Agent server is offline');
    return;
  }
  if (state.excelRows.length === 0) {
    addLog('error', '[Agent] Upload an Excel manifest first');
    return;
  }

  // Find containers with missing invoices or PODs
  const missing = [];
  for (const row of state.excelRows) {
    const matched = state.pdfs.filter(p =>
      p.name.toLowerCase().includes(row.containerNumber.toLowerCase())
    );
    const hasInvoice = matched.some(p => classifyPdf(p.name) === 'invoice');
    const hasPod     = matched.some(p => classifyPdf(p.name) === 'pod');
    if (!hasInvoice || !hasPod) {
      missing.push({
        containerNumber: row.containerNumber,
        invoiceNumber: row.invoiceNumber || '',
      });
    }
  }

  if (missing.length === 0) {
    addLog('success', '[Agent] All containers have both Invoice and POD — nothing to fetch!');
    return;
  }

  addLog('info', `[Agent] Fetching ${missing.length} containers with missing documents`);

  // Show immediate UI feedback BEFORE the server call
  const fetchBtn = document.getElementById('fetchMissingBtn');
  const pauseBtn = document.getElementById('pauseJobBtn');
  const progressArea = document.getElementById('agentProgressArea');
  const progressList = document.getElementById('agentProgressList');
  const progressCount = document.getElementById('agentProgressCount');
  fetchBtn.disabled = true;
  fetchBtn.innerHTML =
    '<svg class="spinner" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg> Starting...';
  progressArea.style.display = '';
  progressList.innerHTML = '<div style="color:#94a3b8; font-size:0.78rem; padding:8px 0;">Connecting to agent server...</div>';
  progressCount.textContent = '';

  // Start the fetch job
  const result = await agentBridge.fetchMissing(missing);
  if (result.error) {
    addLog('error', '[Agent] Failed to start fetch: ' + result.error);
    resetFetchButton();
    progressArea.style.display = 'none';
    return;
  }

  state.activeJobId = result.jobId;
  addLog('info', `[Agent] Job started — fetching ${result.total} containers`);
  fetchBtn.innerHTML =
    '<svg class="spinner" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg> Fetching...';
  pauseBtn.style.display = '';
  progressList.innerHTML = '';

  // Stream progress
  agentBridge.streamProgress(result.jobId, async (event) => {
    handleAgentEvent(event, result.jobId);
  });
}

function resetFetchButton() {
  const fetchBtn = document.getElementById('fetchMissingBtn');
  fetchBtn.disabled = false;
  fetchBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg> Auto-Fetch Missing';
  document.getElementById('pauseJobBtn').style.display = 'none';
}

async function agentPauseJob() {
  if (!state.activeJobId) return;
  const pauseBtn = document.getElementById('pauseJobBtn');
  pauseBtn.disabled = true;
  pauseBtn.innerHTML = '<svg class="spinner" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>';
  try {
    const res = await agentBridge._authFetch(agentBridge.baseUrl + '/jobs/' + state.activeJobId + '/pause', { method: 'POST' });
    const data = await res.json();
    addLog('info', `[Agent] Job paused at ${data.progress}/${data.total}`);
  } catch (e) {
    addLog('error', '[Agent] Failed to pause: ' + e.message);
  }
}

async function handleAgentEvent(event, jobId) {
  const list = document.getElementById('agentProgressList');

  switch (event.type) {
    case 'container_start':
      document.getElementById('agentProgressCount').textContent = `(${event.index + 1}/${event.total})`;
      list.innerHTML += `
        <div class="agent-progress-item" id="agent-prog-${event.containerNumber}">
          <span class="status-icon"><svg class="spinner" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#ea580c" stroke-width="2"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg></span>
          <span style="font-family:monospace; font-weight:600;">${escHtml(event.containerNumber)}</span>
          <span style="margin-left:auto; color:#94a3b8;" id="agent-status-${event.containerNumber}">Searching...</span>
        </div>`;
      break;

    case 'searching':
      updateAgentStatus(event.containerNumber, 'Searching QBO...');
      break;

    case 'downloading_invoice':
      updateAgentStatus(event.containerNumber, 'Downloading invoice...');
      break;

    case 'classifying':
      updateAgentStatus(event.containerNumber, 'Classifying...');
      break;

    case 'checking_pod':
      updateAgentStatus(event.containerNumber, 'Checking for POD...');
      break;

    case 'pod_found':
      addLog('success', `[Agent] POD found for ${event.containerNumber}`);
      break;

    case 'pod_missing':
      addLog('warning', `[Agent] POD Missing: ${event.containerNumber} — not found in QBO`);
      markPodMissing(event.containerNumber);
      break;

    case 'not_found':
      updateAgentStatus(event.containerNumber, 'Not found in QBO', '#dc2626');
      addLog('warning', `[Agent] Invoice not found in QBO: ${event.invoiceNumber}`);
      break;

    case 'container_complete': {
      const r = event.result;
      let statusText = '';
      let statusColor = '#16a34a';
      if (r.invoiceFile) statusText += 'Invoice \u2713 ';
      if (r.podFile) statusText += 'POD \u2713';
      if (r.podMissing) { statusText += 'POD Missing'; statusColor = '#d97706'; }
      if (r.error) { statusText = r.error; statusColor = '#dc2626'; }
      updateAgentStatus(event.containerNumber, statusText, statusColor, true);

      // Inject downloaded files into the web app
      if (r.invoiceFile) {
        const blob = await agentBridge.getFile(jobId, r.invoiceFile);
        if (blob) agentBridge.injectFile(blob, r.invoiceFile);
      }
      if (r.podFile) {
        const blob = await agentBridge.getFile(jobId, r.podFile);
        if (blob) agentBridge.injectFile(blob, r.podFile);
      }
      break;
    }

    case 'login_required':
      addLog('error', '[Agent] QBO session expired — please log in again');
      document.getElementById('qboStatus').textContent = 'Not logged in';
      document.getElementById('qboStatus').style.color = '#d97706';
      document.getElementById('qboLoginSection').style.display = '';
      resetFetchButton();
      state.activeJobId = null;
      break;

    case 'job_paused':
      addLog('info', `[Agent] Job paused at ${event.progress}/${event.total}`);
      resetFetchButton();
      state.activeJobId = null;
      break;

    case 'job_complete':
      addLog('info', `[Agent] ──── Fetch Complete ────`);
      addLog('success', `[Agent] Invoices: ${event.invoicesDownloaded} · PODs: ${event.podsDownloaded}`);
      if (event.podsMissing > 0)
        addLog('warning', `[Agent] PODs Missing: ${event.podsMissing} — not found in QBO`);
      if (event.errors > 0)
        addLog('error', `[Agent] Errors: ${event.errors}`);
      resetFetchButton();
      state.activeJobId = null;
      renderContainerGroups();
      break;

    case 'connection_warning':
      addLog('warning', '[Agent] ' + event.message);
      break;

    case 'connection_lost':
      addLog('error', '[Agent] ' + event.message);
      resetFetchButton();
      state.activeJobId = null;
      break;
  }
}

function updateAgentStatus(containerNumber, text, color, done) {
  const el = document.getElementById('agent-status-' + containerNumber);
  if (!el) return;
  el.textContent = text;
  if (color) el.style.color = color;
  if (done) {
    const icon = document.querySelector(`#agent-prog-${containerNumber} .status-icon`);
    if (icon) {
      const isError = color === '#dc2626';
      icon.innerHTML = isError
        ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#dc2626" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>'
        : '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#22c55e" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>';
    }
  }
}

function markPodMissing(containerNumber) {
  const groups = document.querySelectorAll('.container-group');
  for (const group of groups) {
    const header = group.querySelector('.container-group-header span');
    if (header && header.textContent.trim() === containerNumber) {
      if (!group.querySelector('.pod-missing-flag')) {
        const flag = document.createElement('span');
        flag.className = 'pod-missing-flag';
        flag.innerHTML = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg> POD Missing';
        group.querySelector('.container-group-header').appendChild(flag);
      }
      break;
    }
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
      mode: state.mode,
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
  fetchMissing: agentFetchMissing,
  injectFile: (blob, name) => agentBridge.injectFile(blob, name),
};

// ── Window assignments for inline HTML handlers ──
window.toggleAgentPanel = toggleAgentPanel;
window.agentOpenQBOLogin = agentOpenQBOLogin;
window.agentOpenTMSLogin = agentOpenTMSLogin;
window.agentFetchMissing = agentFetchMissing;
window.agentPauseJob = agentPauseJob;
window.agentHeaderBtnClick = agentHeaderBtnClick;
