// ══════════════════════════════════════════════════════════
//  AGENT CLIENT — pure API calls to Python server on localhost:8787
//  No DOM manipulation — just fetch calls and data handling.
// ══════════════════════════════════════════════════════════
import { state } from './state.js';
import { uid, triggerDownload } from './utils.js';
import { addLog } from './log.js';
import { LS_CUSTOMERS, LS_AUTH_TOKEN, LS_AUTH_USER } from './constants.js';

export const agentBridge = {
  baseUrl: 'http://localhost:8787',
  _authToken: null,  // JWT from login
  _currentUser: null,  // { id, username, displayName, role }

  // Hooks — set by other modules to break circular dependencies
  hooks: {
    onFileInjected: null,  // (name, blob) => void — called when a file is injected from agent
    onAuthRequired: null,  // () => void — called when login is needed
    onAuthSuccess: null,   // (user) => void — called after successful login
  },

  // ── Auth helpers ──
  _loadSavedAuth() {
    try {
      this._authToken = localStorage.getItem(LS_AUTH_TOKEN);
      const u = localStorage.getItem(LS_AUTH_USER);
      if (u) this._currentUser = JSON.parse(u);
    } catch { /* corrupt localStorage */ }
  },

  _saveAuth(token, user) {
    this._authToken = token;
    this._currentUser = user;
    if (token) {
      localStorage.setItem(LS_AUTH_TOKEN, token);
      localStorage.setItem(LS_AUTH_USER, JSON.stringify(user));
    }
  },

  _clearAuth() {
    this._authToken = null;
    this._currentUser = null;
    localStorage.removeItem(LS_AUTH_TOKEN);
    localStorage.removeItem(LS_AUTH_USER);
  },

  isLoggedIn() {
    return !!this._authToken;
  },

  getCurrentUser() {
    return this._currentUser;
  },

  async login(username, password, remember = false) {
    try {
      const res = await fetch(this.baseUrl + '/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password, remember }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        return { error: err.detail || 'Login failed' };
      }
      const data = await res.json();
      this._saveAuth(data.token, data.user);
      return { user: data.user };
    } catch (e) {
      return { error: 'Cannot connect to agent: ' + e.message };
    }
  },

  logout() {
    this._clearAuth();
    if (this.hooks.onAuthRequired) this.hooks.onAuthRequired();
  },

  async validateSession() {
    if (!this._authToken) return false;
    try {
      const res = await this._authFetch(this.baseUrl + '/auth/me');
      if (res.ok) {
        const data = await res.json();
        this._currentUser = data.user;
        localStorage.setItem(LS_AUTH_USER, JSON.stringify(data.user));
        return true;
      }
      // Token expired or invalid
      this._clearAuth();
      return false;
    } catch {
      // Agent offline — keep token, assume valid (will re-check when agent is back)
      return !!this._authToken;
    }
  },

  _authHeaders(extra = {}) {
    const headers = { ...extra };
    if (this._authToken) headers['Authorization'] = 'Bearer ' + this._authToken;
    return headers;
  },

  _authFetch(url, opts = {}) {
    opts.headers = this._authHeaders(opts.headers || {});
    return fetch(url, opts);
  },

  async checkHealth() {
    try {
      const res = await fetch(this.baseUrl + '/health', { signal: AbortSignal.timeout(3000) });
      if (!res.ok) return null;
      return await res.json();
    } catch { return null; }
  },

  async checkQBOStatus() {
    try {
      const res = await this._authFetch(this.baseUrl + '/qbo/status');
      return await res.json();
    } catch { return { status: 'error', loggedIn: false }; }
  },

  async disconnectQboApi() {
    try {
      const res = await this._authFetch(this.baseUrl + '/qbo/oauth/disconnect', { method: 'POST' });
      return await res.json();
    } catch (e) { return { error: e.message }; }
  },

  async checkTMSStatus() {
    try {
      const res = await this._authFetch(this.baseUrl + '/tms/status');
      return await res.json();
    } catch { return { status: 'error', loggedIn: false }; }
  },

  async openTMSLogin() {
    try {
      const res = await this._authFetch(this.baseUrl + '/tms/open-login', { method: 'POST' });
      return await res.json();
    } catch (e) { return { status: 'error', error: e.message }; }
  },

  async fetchMissing(containers, docTypes) {
    try {
      const res = await this._authFetch(this.baseUrl + '/jobs/fetch-missing', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ containers, doc_types: docTypes }),
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
      const tokenParam = self._authToken ? '?token=' + encodeURIComponent(self._authToken) : '';
      const source = new EventSource(self.baseUrl + '/jobs/' + jobId + '/stream' + tokenParam);
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
      const res = await this._authFetch(`${this.baseUrl}/files/${jobId}/${filename}`);
      if (!res.ok) return null;
      return await res.blob();
    } catch { return null; }
  },

  injectFile(blob, name) {
    const file = new File([blob], name, { type: 'application/pdf' });
    const existing = state.pdfs.find(p => p.name === name);
    if (existing) return; // deduplicate
    state.pdfs.push({ id: uid(), name, size: blob.size, file });

    // Notify listeners via hooks
    if (this.hooks.onFileInjected) {
      this.hooks.onFileInjected(name, blob);
    } else {
      addLog('success', `[Agent] Injected: ${name}`);
    }
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
      const res = await this._authFetch(this.baseUrl + '/files/save-output', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ files, openFolder: true }),
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } catch (e) { return { error: e.message }; }
  },

  // ── Customer Storage (localStorage) ──
  _custRead() {
    try { return JSON.parse(localStorage.getItem(LS_CUSTOMERS) || '{}'); } catch { return {}; }
  },
  _custWrite(data) {
    localStorage.setItem(LS_CUSTOMERS, JSON.stringify(data));
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
        const res = await this._authFetch(this.baseUrl + '/customers?' + params.toString());
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
        const res = await this._authFetch(this.baseUrl + '/customers/' + encodeURIComponent(code));
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

    if (!state.agentConnected) {
      return { error: 'Agent is offline — cannot save to cloud. Please make sure the agent is running and try again.' };
    }

    try {
      const res = await this._authFetch(this.baseUrl + '/customers', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      if (res.ok) {
        const created = await res.json();
        // Sync back to localStorage
        const all = this._custRead();
        all[codeUpper] = created;
        this._custWrite(all);
        created._savedTo = 'cloud';
        return created;
      }
      const err = await res.json().catch(() => ({}));
      return { error: err.detail || 'Cloud save failed (HTTP ' + res.status + ')' };
    } catch (e) {
      return { error: 'Could not reach the agent — customer was NOT saved. Check that the agent is running.' };
    }
  },

  async updateCustomer(code, data) {
    const codeUpper = code.toUpperCase();

    if (!state.agentConnected) {
      return { error: 'Agent is offline — cannot save to cloud. Please make sure the agent is running and try again.' };
    }

    try {
      const res = await this._authFetch(this.baseUrl + '/customers/' + encodeURIComponent(code), {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      if (res.ok) {
        const updated = await res.json();
        // Sync back to localStorage
        const all = this._custRead();
        all[codeUpper] = updated;
        this._custWrite(all);
        updated._savedTo = 'cloud';
        return updated;
      }
      const err = await res.json().catch(() => ({}));
      return { error: err.detail || 'Cloud save failed (HTTP ' + res.status + ')' };
    } catch (e) {
      return { error: 'Could not reach the agent — changes were NOT saved. Check that the agent is running.' };
    }
  },

  async deleteCustomer(code) {
    const codeUpper = code.toUpperCase();

    if (!state.agentConnected) {
      return { error: 'Agent is offline — cannot update cloud. Please make sure the agent is running and try again.' };
    }

    try {
      const res = await this._authFetch(this.baseUrl + '/customers/' + encodeURIComponent(code), { method: 'DELETE' });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        return { error: err.detail || 'Cloud delete failed (HTTP ' + res.status + ')' };
      }
    } catch (e) {
      return { error: 'Could not reach the agent — customer was NOT deactivated. Check that the agent is running.' };
    }

    // Sync localStorage
    const all = this._custRead();
    if (all[codeUpper]) { all[codeUpper].active = false; all[codeUpper].updatedAt = this._nowIso(); this._custWrite(all); }
    return { status: 'deleted', code: codeUpper };
  },

  async importCustomers(customers) {
    if (!state.agentConnected) {
      return { error: 'Agent is offline — cannot import to cloud. Please make sure the agent is running and try again.' };
    }

    try {
      const res = await this._authFetch(this.baseUrl + '/customers/import', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ customers }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        return { error: err.detail || 'Cloud import failed (HTTP ' + res.status + ')' };
      }
      const result = await res.json();
      // Sync back to localStorage from cloud
      const fresh = await this.getCustomers('', false);
      if (fresh.customers) {
        const all = {};
        for (const c of fresh.customers) all[c.code] = c;
        this._custWrite(all);
      }
      return { status: 'ok', created: result.created, updated: result.updated, total: (result.created || 0) + (result.updated || 0) };
    } catch (e) {
      return { error: 'Could not reach the agent — import was NOT saved. Check that the agent is running.' };
    }
  },

  async exportCustomers() {
    // Try agent first — it has the canonical data
    if (state.agentConnected) {
      try {
        const res = await this._authFetch(this.baseUrl + '/customers/export');
        if (res.ok) return await res.json();
      } catch {}
    }
    const all = this._custRead();
    return Object.values(all);
  },

  // ── Send Invoices ──
  async sendInvoices(invoices, testMode = false) {
    try {
      const res = await this._authFetch(this.baseUrl + '/jobs/send-invoices', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ invoices, testMode }),
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } catch (e) { return { error: e.message }; }
  },

  async approveSend(jobId, payload) {
    try {
      const opts = { method: 'POST' };
      if (payload) {
        opts.headers = { 'Content-Type': 'application/json' };
        opts.body = JSON.stringify(payload);
      }
      const res = await this._authFetch(this.baseUrl + '/jobs/' + jobId + '/approve-send', opts);
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } catch (e) { return { error: e.message }; }
  },

  async skipSend(jobId) {
    try {
      const res = await this._authFetch(this.baseUrl + '/jobs/' + jobId + '/skip-send', { method: 'POST' });
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
      const res = await this._authFetch(this.baseUrl + '/audit?' + params.toString());
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } catch (e) { return { entries: [], total: 0, error: e.message }; }
  },

  async exportAuditLog() {
    try {
      const res = await this._authFetch(this.baseUrl + '/audit/export');
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const blob = await res.blob();
      triggerDownload(blob, 'audit_log.csv');
    } catch (e) { console.error('Export audit failed:', e); }
  },

  async getAuditStats() {
    try {
      const res = await this._authFetch(this.baseUrl + '/audit/stats');
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } catch (e) { return { error: e.message }; }
  },

  // ── Settings / Credentials ──
  async getCredentials() {
    try {
      const res = await this._authFetch(this.baseUrl + '/settings/credentials');
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } catch (e) { return { error: e.message }; }
  },

  async saveAndConnect(data) {
    try {
      const res = await this._authFetch(this.baseUrl + '/settings/credentials/connect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } catch (e) { return { error: e.message }; }
  },

  // ── Email (Gmail) Settings ──
  async getEmailConfig() {
    try {
      const res = await this._authFetch(this.baseUrl + '/settings/email');
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } catch (e) { return { error: e.message }; }
  },

  async saveEmailConfig(data) {
    try {
      const res = await this._authFetch(this.baseUrl + '/settings/email', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } catch (e) { return { error: e.message }; }
  },

  async testEmailConfig(data) {
    try {
      const res = await this._authFetch(this.baseUrl + '/settings/email/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data || {}),
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } catch (e) { return { sent: false, error: e.message }; }
  },

  // ── Selector Health Checks ──
  async checkTmsSelectorHealth() {
    try {
      const res = await this._authFetch(this.baseUrl + '/tms/selector-health');
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } catch (e) { return { status: 'error', error: e.message }; }
  },

  // ── Notification Settings ──
  async getNotificationSettings() {
    try {
      const res = await this._authFetch(this.baseUrl + '/settings/notifications');
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } catch (e) { return { error: e.message }; }
  },

  async updateNotificationSettings(enabled) {
    try {
      const res = await this._authFetch(this.baseUrl + '/settings/notifications', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } catch (e) { return { error: e.message }; }
  },

  // ── User Management (admin) ──
  async listUsers() {
    try {
      const res = await this._authFetch(this.baseUrl + '/auth/users');
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } catch (e) { return { error: e.message }; }
  },

  async createUser(data) {
    try {
      const res = await this._authFetch(this.baseUrl + '/auth/users', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        return { error: err.detail || 'Failed to create user' };
      }
      return await res.json();
    } catch (e) { return { error: e.message }; }
  },

  async updateUser(userId, data) {
    try {
      const res = await this._authFetch(this.baseUrl + '/auth/users/' + userId, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        return { error: err.detail || 'Failed to update user' };
      }
      return await res.json();
    } catch (e) { return { error: e.message }; }
  },

  async deleteUser(userId) {
    try {
      const res = await this._authFetch(this.baseUrl + '/auth/users/' + userId, { method: 'DELETE' });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        return { error: err.detail || 'Failed to deactivate user' };
      }
      return await res.json();
    } catch (e) { return { error: e.message }; }
  },

  async changePassword(currentPassword, newPassword) {
    try {
      const res = await this._authFetch(this.baseUrl + '/auth/change-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ currentPassword, newPassword }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        return { error: err.detail || 'Failed to change password' };
      }
      return await res.json();
    } catch (e) { return { error: e.message }; }
  },
};

// Expose on window for dynamically generated inline onclick handlers
window.agentBridge = agentBridge;
