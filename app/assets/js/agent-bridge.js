'use strict';
// ══════════════════════════════════════════════════════════
//  AGENT BRIDGE — communicates with Python server on localhost:8787
// ══════════════════════════════════════════════════════════
const agentBridge = {
  baseUrl: 'http://localhost:8787',

  async checkHealth() {
    try {
      const res = await fetch(this.baseUrl + '/health', { signal: AbortSignal.timeout(3000) });
      if (!res.ok) return null;
      return await res.json();
    } catch { return null; }
  },

  async checkQBOStatus() {
    try {
      const res = await fetch(this.baseUrl + '/qbo/status');
      return await res.json();
    } catch { return { status: 'error', loggedIn: false }; }
  },

  async openQBOLogin() {
    try {
      const res = await fetch(this.baseUrl + '/qbo/open-login', { method: 'POST' });
      return await res.json();
    } catch (e) { return { status: 'error', error: e.message }; }
  },

  async checkTMSStatus() {
    try {
      const res = await fetch(this.baseUrl + '/tms/status');
      return await res.json();
    } catch { return { status: 'error', loggedIn: false }; }
  },

  async openTMSLogin() {
    try {
      const res = await fetch(this.baseUrl + '/tms/open-login', { method: 'POST' });
      return await res.json();
    } catch (e) { return { status: 'error', error: e.message }; }
  },

  async fetchMissing(containers) {
    try {
      const res = await fetch(this.baseUrl + '/jobs/fetch-missing', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ containers }),
      });
      return await res.json();
    } catch (e) { return { error: e.message }; }
  },

  streamProgress(jobId, onEvent) {
    const self = this;
    let retries = 0;
    const MAX_RETRIES = 5;
    const RETRY_DELAYS = [2000, 4000, 8000, 15000, 30000]; // escalating backoff
    let intentionallyClosed = false;
    let currentSource = null;

    function connect() {
      const source = new EventSource(self.baseUrl + '/jobs/' + jobId + '/stream');
      currentSource = source;

      // Terminal events that should close the stream permanently
      const terminalEvents = ['login_required', 'job_paused', 'job_complete', 'send_job_complete'];

      source.onmessage = (e) => {
        retries = 0; // Reset retry counter on any successful message
        try { onEvent(JSON.parse(e.data)); } catch {}
      };

      // All named event types
      const namedEvents = [
        'container_start', 'searching', 'downloading_invoice', 'classifying',
        'checking_pod', 'pod_found', 'pod_missing', 'container_complete',
        'container_error', 'not_found', 'review_needed', 'download_failed',
        'job_started',
        // Send job events
        'send_job_started', 'invoice_start', 'searching_invoice', 'invoice_not_found',
        'verifying_invoice', 'invoice_mismatch', 'checking_attachments',
        'invoice_missing_docs', 'opening_send_form', 'filling_send_form',
        'awaiting_approval', 'approval_confirmed', 'sending_invoice',
        'invoice_sent', 'invoice_skipped', 'invoice_error',
        'retrying_attachments',
        // OEC flow events
        'oec_qbo_sending', 'oec_qbo_sent', 'oec_downloading_pod',
        'oec_sending_pod_email', 'oec_pod_email_sent', 'oec_pod_email_failed',
        // Portal flow events
        'portal_downloading', 'portal_merging', 'portal_uploading',
        'portal_upload_success', 'portal_upload_failed',
        // TMS flow events
        'tms_fetching_pod', 'tms_pod_downloaded', 'tms_pod_not_found', 'tms_login_required',
      ];

      namedEvents.forEach(name => {
        source.addEventListener(name, e => {
          retries = 0;
          try { onEvent(JSON.parse(e.data)); } catch {}
        });
      });

      // Terminal events: close permanently, no reconnect
      terminalEvents.forEach(name => {
        source.addEventListener(name, e => {
          intentionallyClosed = true;
          try { onEvent(JSON.parse(e.data)); } catch {}
          source.close();
        });
      });

      source.onerror = () => {
        source.close();
        if (intentionallyClosed) return;

        if (retries < MAX_RETRIES) {
          const delay = RETRY_DELAYS[retries] || 30000;
          retries++;
          console.warn('[SSE] Connection lost, retrying in ' + delay + 'ms (attempt ' + retries + '/' + MAX_RETRIES + ')');
          onEvent({ type: 'connection_warning', message: 'Connection lost — reconnecting (attempt ' + retries + '/' + MAX_RETRIES + ')...' });
          setTimeout(connect, delay);
        } else {
          console.error('[SSE] Max retries reached — giving up');
          onEvent({ type: 'connection_lost', message: 'Connection to agent lost after ' + MAX_RETRIES + ' retries. Check if the agent server is running.' });
        }
      };
    }

    connect();

    // Return a wrapper that lets callers close the stream
    return {
      close() {
        intentionallyClosed = true;
        if (currentSource) currentSource.close();
      }
    };
  },

  async getFile(jobId, filename) {
    try {
      const res = await fetch(`${this.baseUrl}/files/${jobId}/${filename}`);
      if (!res.ok) return null;
      return await res.blob();
    } catch { return null; }
  },

  injectFile(blob, name) {
    const file = new File([blob], name, { type: 'application/pdf' });
    const existing = state.pdfs.find(p => p.name === name);
    if (existing) return; // deduplicate
    state.pdfs.push({ id: uid(), name, size: blob.size, file });
    addLog('success', `[Agent] Injected: ${name}`);
    renderPdfQueue();
    if (state.mode === 'auto') renderContainerGroups();
    updateQueueCount();
  },

  async saveToFolder(mergeResults) {
    // Convert each merged PDF to base64 and send to agent for local save
    const files = [];
    for (const r of mergeResults) {
      // Chunk the conversion to avoid blowing the call stack on large PDFs
      const bytes = new Uint8Array(r.bytes);
      let binary = '';
      const CHUNK = 8192;
      for (let i = 0; i < bytes.length; i += CHUNK) {
        binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
      }
      files.push({ filename: r.filename, data: btoa(binary), subfolder: r.subfolder || '' });
    }
    try {
      const res = await fetch(this.baseUrl + '/files/save-output', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ files, openFolder: true }),
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } catch (e) { return { error: e.message }; }
  },

  // ── Customer Storage (localStorage) ──
  _custKey: 'ngl_customers',
  _custRead() {
    try { return JSON.parse(localStorage.getItem(this._custKey) || '{}'); } catch { return {}; }
  },
  _custWrite(data) {
    localStorage.setItem(this._custKey, JSON.stringify(data));
  },
  _nowIso() { return new Date().toISOString(); },

  // ── Customer Management (localStorage + agent sync) ──
  async getCustomers(search = '', activeOnly = true) {
    // Try agent first
    if (state.agentConnected) {
      try {
        const params = new URLSearchParams();
        if (search) params.set('search', search);
        params.set('activeOnly', activeOnly);
        const res = await fetch(this.baseUrl + '/customers?' + params.toString());
        if (res.ok) return await res.json();
      } catch {}
    }
    // Fallback: localStorage
    const all = this._custRead();
    let results = Object.values(all);
    if (activeOnly) results = results.filter(c => c.active !== false);
    const sl = search.toLowerCase();
    if (sl) results = results.filter(c => (c.code || '').toLowerCase().includes(sl) || (c.name || '').toLowerCase().includes(sl));
    results.sort((a, b) => (a.code || '').localeCompare(b.code || ''));
    return { customers: results, total: results.length };
  },

  async getCustomer(code) {
    if (state.agentConnected) {
      try {
        const res = await fetch(this.baseUrl + '/customers/' + encodeURIComponent(code));
        if (res.ok) return await res.json();
      } catch {}
    }
    const all = this._custRead();
    return all[code.toUpperCase()] || null;
  },

  // Valid sendMethod values
  _validSendMethods: ['email', 'qbo_invoice_only_then_pod_email', 'portal_upload', 'portal'],

  _normalizeSendMethod(m) {
    return this._validSendMethods.includes(m) ? m : 'email';
  },

  // Copy method-specific fields onto a customer object
  _applyMethodFields(cust, data) {
    const m = cust.sendMethod;
    if (m === 'qbo_invoice_only_then_pod_email') {
      cust.podEmailTo = data.podEmailTo || [];
      cust.podEmailCc = data.podEmailCc || [];
      cust.podEmailSubject = data.podEmailSubject || '';
      cust.podEmailBody = data.podEmailBody || '';
    } else {
      delete cust.podEmailTo; delete cust.podEmailCc;
      delete cust.podEmailSubject; delete cust.podEmailBody;
    }
    if (m === 'portal_upload' || m === 'portal') {
      cust.portalUrl = data.portalUrl || '';
      cust.portalClient = data.portalClient || '';
    } else {
      delete cust.portalUrl; delete cust.portalClient;
    }
  },

  async createCustomer(data) {
    const codeUpper = (data.code || '').trim().toUpperCase();
    if (!codeUpper) return { error: 'Customer code is required' };

    // Try agent first (source of truth when connected)
    if (state.agentConnected) {
      try {
        const res = await fetch(this.baseUrl + '/customers', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(data),
        });
        if (res.ok) {
          const created = await res.json();
          // Sync back to localStorage
          const all = this._custRead();
          all[codeUpper] = created;
          this._custWrite(all);
          return created;
        }
        const err = await res.json().catch(() => ({}));
        return { error: err.detail || 'Agent create failed (HTTP ' + res.status + ')' };
      } catch (e) {
        // Agent unreachable — fall through to localStorage
      }
    }

    // Fallback: localStorage only
    const now = this._nowIso();
    const cust = {
      code: codeUpper, name: (data.name || '').trim(),
      emails: data.emails || [], ccEmails: data.ccEmails || [], bccEmails: data.bccEmails || [],
      requiredDocs: data.requiredDocs || [],
      sendMethod: this._normalizeSendMethod(data.sendMethod),
      notes: (data.notes || '').trim(), active: true, createdAt: now, updatedAt: now,
    };
    this._applyMethodFields(cust, data);
    const all = this._custRead();
    if (all[codeUpper]) return { error: 'Customer already exists: ' + codeUpper };
    all[codeUpper] = cust;
    this._custWrite(all);
    return cust;
  },

  async updateCustomer(code, data) {
    const codeUpper = code.toUpperCase();

    // Try agent first (source of truth when connected)
    if (state.agentConnected) {
      try {
        const res = await fetch(this.baseUrl + '/customers/' + encodeURIComponent(code), {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(data),
        });
        if (res.ok) {
          const updated = await res.json();
          // Sync back to localStorage
          const all = this._custRead();
          all[codeUpper] = updated;
          this._custWrite(all);
          return updated;
        }
        const err = await res.json().catch(() => ({}));
        return { error: err.detail || 'Agent update failed (HTTP ' + res.status + ')' };
      } catch (e) {
        // Agent unreachable — fall through to localStorage
      }
    }

    // Fallback: localStorage only
    const all = this._custRead();
    const cust = all[codeUpper];
    if (!cust) return { error: 'Customer not found: ' + codeUpper };
    if (data.name != null) cust.name = data.name.trim();
    if (data.emails != null) cust.emails = data.emails;
    if (data.ccEmails != null) cust.ccEmails = data.ccEmails;
    if (data.bccEmails != null) cust.bccEmails = data.bccEmails;
    if (data.requiredDocs != null) cust.requiredDocs = data.requiredDocs;
    if (data.sendMethod != null) cust.sendMethod = this._normalizeSendMethod(data.sendMethod);
    if (data.notes != null) cust.notes = data.notes.trim();
    if (data.active != null) cust.active = data.active;
    this._applyMethodFields(cust, data);
    cust.updatedAt = this._nowIso();
    all[codeUpper] = cust;
    this._custWrite(all);
    return cust;
  },

  async deleteCustomer(code) {
    const codeUpper = code.toUpperCase();
    // Agent first
    if (state.agentConnected) {
      try { await fetch(this.baseUrl + '/customers/' + encodeURIComponent(code), { method: 'DELETE' }); } catch {}
    }
    // Sync localStorage
    const all = this._custRead();
    if (all[codeUpper]) { all[codeUpper].active = false; all[codeUpper].updatedAt = this._nowIso(); this._custWrite(all); }
    return { status: 'deleted', code: codeUpper };
  },

  async importCustomers(customers) {
    const all = this._custRead();
    let created = 0, updated = 0;
    const now = this._nowIso();
    for (const item of customers) {
      const codeUpper = (item.code || '').trim().toUpperCase();
      if (!codeUpper) continue;
      const sendMethod = this._normalizeSendMethod(item.sendMethod);
      if (all[codeUpper]) {
        Object.assign(all[codeUpper], {
          name: (item.name || '').trim(), emails: item.emails || [], ccEmails: item.ccEmails || [],
          bccEmails: item.bccEmails || [], requiredDocs: item.requiredDocs || [],
          sendMethod, notes: (item.notes || '').trim(), updatedAt: now,
        });
        this._applyMethodFields(all[codeUpper], item);
        updated++;
      } else {
        all[codeUpper] = {
          code: codeUpper, name: (item.name || '').trim(),
          emails: item.emails || [], ccEmails: item.ccEmails || [], bccEmails: item.bccEmails || [],
          requiredDocs: item.requiredDocs || [], sendMethod,
          notes: (item.notes || '').trim(), active: true, createdAt: now, updatedAt: now,
        };
        this._applyMethodFields(all[codeUpper], item);
        created++;
      }
    }
    this._custWrite(all);
    // Sync to agent
    if (state.agentConnected) {
      try { await fetch(this.baseUrl + '/customers/import', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ customers }) }); } catch {}
    }
    return { status: 'ok', created, updated, total: created + updated };
  },

  async exportCustomers() {
    // Try agent first — it has the canonical data
    if (state.agentConnected) {
      try {
        const res = await fetch(this.baseUrl + '/customers/export');
        if (res.ok) return await res.json();
      } catch {}
    }
    const all = this._custRead();
    return Object.values(all);
  },

  // ── Send Invoices ──
  async sendInvoices(invoices, testMode = false) {
    try {
      const res = await fetch(this.baseUrl + '/jobs/send-invoices', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ invoices, testMode }),
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } catch (e) { return { error: e.message }; }
  },

  async approveSend(jobId) {
    try {
      const res = await fetch(this.baseUrl + '/jobs/' + jobId + '/approve-send', { method: 'POST' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } catch (e) { return { error: e.message }; }
  },

  async skipSend(jobId) {
    try {
      const res = await fetch(this.baseUrl + '/jobs/' + jobId + '/skip-send', { method: 'POST' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } catch (e) { return { error: e.message }; }
  },

  // ── Audit Log ──
  async getAuditLog(filters = {}) {
    try {
      const params = new URLSearchParams();
      if (filters.date) params.set('date', filters.date);
      if (filters.customer) params.set('customer', filters.customer);
      if (filters.status) params.set('status', filters.status);
      if (filters.invoice) params.set('invoice', filters.invoice);
      if (filters.limit) params.set('limit', filters.limit);
      if (filters.offset) params.set('offset', filters.offset);
      const res = await fetch(this.baseUrl + '/audit?' + params.toString());
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } catch (e) { return { entries: [], total: 0, error: e.message }; }
  },

  async exportAuditLog() {
    try {
      const res = await fetch(this.baseUrl + '/audit/export');
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const blob = await res.blob();
      triggerDownload(blob, 'audit_log.csv');
    } catch (e) { console.error('Export audit failed:', e); }
  },

  async getAuditStats() {
    try {
      const res = await fetch(this.baseUrl + '/audit/stats');
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } catch (e) { return { error: e.message }; }
  },
};


// ── Agent Panel UI ──
function toggleAgentPanel() {
  const body  = document.getElementById('agentBody');
  const arrow = document.getElementById('agentToggleArrow');
  const isHidden = body.style.display === 'none';
  body.style.display = isHidden ? '' : 'none';
  arrow.classList.toggle('collapsed', !isHidden);
}


async function agentHealthCheck() {
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

    // One-time bidirectional customer sync when agent first connects.
    // Agent's disk file (customers.json) is source of truth — pull it into
    // localStorage so every device gets the same data. Then push any
    // localStorage-only entries back to the agent.
    if (!state._agentCustomersSynced) {
      state._agentCustomersSynced = true;
      try {
        // Step 1: Pull from agent → localStorage (agent is source of truth)
        const agentRes = await fetch(agentBridge.baseUrl + '/customers?activeOnly=false');
        if (agentRes.ok) {
          const agentData = await agentRes.json();
          const agentCustomers = agentData.customers || [];
          if (agentCustomers.length > 0) {
            const merged = agentBridge._custRead();
            for (const c of agentCustomers) {
              const key = (c.code || '').toUpperCase();
              if (!key) continue;
              // Agent wins if it has a newer updatedAt, or if local doesn't have this customer
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
          fetch(agentBridge.baseUrl + '/customers/import', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ customers: allCust }),
          }).catch(() => {});
        }
      } catch (_) {}
    }

    // Fetch button enabled whenever agent is connected
    // (the job itself checks QBO login and pauses if needed)
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
  // Not connected — try to open the agent server, show brief toast if can't
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
      const waitRes = await fetch(agentBridge.baseUrl + '/qbo/wait-for-login', { method: 'POST' });
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
      const waitRes = await fetch(agentBridge.baseUrl + '/tms/wait-for-login', { method: 'POST' });
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
    const res = await fetch(agentBridge.baseUrl + '/jobs/' + state.activeJobId + '/pause', { method: 'POST' });
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
      if (r.invoiceFile) statusText += 'Invoice ✓ ';
      if (r.podFile) statusText += 'POD ✓';
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
  // Add a visual flag to the container group in the queue
  const groups = document.querySelectorAll('.container-group');
  for (const group of groups) {
    const header = group.querySelector('.container-group-header span');
    if (header && header.textContent.trim() === containerNumber) {
      // Add POD Missing flag if not already there
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
