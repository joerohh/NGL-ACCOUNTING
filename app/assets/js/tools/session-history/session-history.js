// ══════════════════════════════════════════════════════════
//  SESSION HISTORY — in-memory job log with CSV export
// ══════════════════════════════════════════════════════════
import { escHtml } from '../../shared/utils.js';
import { state } from '../../shared/state.js';
import { agentBridge } from '../../shared/agent-client.js';

const sessionJobs = [];

/**
 * Push a completed job summary into session history.
 * @param {object} summary - { type, jobId, timestamp, total, sent, skipped, errors, ... , results[] }
 */
export function pushSessionJob(summary) {
  sessionJobs.push({
    ...summary,
    timestamp: summary.timestamp || new Date().toISOString(),
  });
}

/**
 * Render the session history view.
 */
export function renderSessionHistory() {
  const list = document.getElementById('sessionJobList');
  const exportBtn = document.getElementById('exportSessionCsvBtn');
  if (!list) return;

  if (sessionJobs.length === 0) {
    list.innerHTML = '<div class="session-empty">No jobs completed this session.</div>';
    if (exportBtn) exportBtn.disabled = true;
    return;
  }

  if (exportBtn) exportBtn.disabled = false;

  list.innerHTML = sessionJobs.map((job, idx) => {
    const time = _fmtTime(job.timestamp);
    const typeLabel = job.type === 'fetch' ? 'Fetch' : 'Send';
    const typeClass = job.type === 'fetch' ? 'type-fetch' : 'type-send';
    const successCount = job.type === 'fetch'
      ? (job.invoicesDownloaded || 0) + (job.podsDownloaded || 0)
      : (job.sent || 0);
    const errorCount = job.errors || 0;

    // Summary chips
    let chips = '';
    if (job.type === 'fetch') {
      chips += `<span class="sh-chip">Invoices: <strong>${job.invoicesDownloaded || 0}</strong></span>`;
      chips += `<span class="sh-chip">PODs: <strong>${job.podsDownloaded || 0}</strong></span>`;
      if (job.podsMissing > 0) chips += `<span class="sh-chip sh-chip-warn">PODs Missing: <strong>${job.podsMissing}</strong></span>`;
    } else {
      chips += `<span class="sh-chip">Sent: <strong>${job.sent || 0}</strong></span>`;
      if (job.skipped > 0) chips += `<span class="sh-chip">Skipped: <strong>${job.skipped}</strong></span>`;
      if (job.mismatches > 0) chips += `<span class="sh-chip sh-chip-warn">Mismatches: <strong>${job.mismatches}</strong></span>`;
    }
    if (errorCount > 0) chips += `<span class="sh-chip sh-chip-err">Errors: <strong>${errorCount}</strong></span>`;

    // Per-container detail rows (collapsed by default)
    let detailRows = '';
    if (job.results && job.results.length > 0) {
      detailRows = job.results.map(r => {
        if (job.type === 'fetch') {
          const status = r.error ? 'error' : (r.podMissing ? 'warning' : 'success');
          const statusText = r.error || (r.podMissing ? 'POD Missing' : 'OK');
          return `<tr class="sh-detail-${status}">
            <td>${escHtml(r.containerNumber || '--')}</td>
            <td>${escHtml(r.invoiceNumber || '--')}</td>
            <td>${escHtml(r.invoiceFile || '--')}</td>
            <td>${escHtml(r.podFile || '--')}</td>
            <td>${escHtml(statusText)}</td>
          </tr>`;
        } else {
          const status = r.status === 'sent' ? 'success' : (r.status === 'error' ? 'error' : 'warning');
          return `<tr class="sh-detail-${status}">
            <td>${escHtml(r.invoiceNumber || '--')}</td>
            <td>${escHtml(r.containerNumber || '--')}</td>
            <td>${escHtml(r.customerCode || '--')}</td>
            <td>${escHtml(r.status || '--')}</td>
            <td style="max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml((r.toEmails || []).join(', ') || '--')}</td>
            <td>${escHtml(r.error || '--')}</td>
          </tr>`;
        }
      }).join('');
    }

    const fetchHeader = `<tr><th>Container</th><th>Invoice #</th><th>Invoice File</th><th>POD File</th><th>Status</th></tr>`;
    const sendHeader = `<tr><th>Invoice #</th><th>Container</th><th>Customer</th><th>Status</th><th>To</th><th>Error</th></tr>`;

    return `
      <div class="sh-job-card">
        <div class="sh-job-header" onclick="toggleSessionJobDetail(${idx})">
          <div class="sh-job-meta">
            <span class="sh-type-badge ${typeClass}">${typeLabel}</span>
            <span class="sh-time">${time}</span>
            <span class="sh-total">${job.total} item${job.total !== 1 ? 's' : ''}</span>
          </div>
          <div class="sh-job-chips">${chips}</div>
          <div class="sh-job-actions">
            <button class="btn btn-secondary sh-export-btn" onclick="event.stopPropagation(); exportSessionCsv(${idx})" title="Export CSV">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
              </svg>
              CSV
            </button>
            <svg class="sh-chevron" id="shChevron${idx}" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#94a3b8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="6 9 12 15 18 9"/>
            </svg>
          </div>
        </div>
        <div class="sh-job-detail" id="shDetail${idx}" style="display:none;">
          <table class="sh-detail-table">
            ${job.type === 'fetch' ? fetchHeader : sendHeader}
            ${detailRows || `<tr><td colspan="6" style="text-align:center; color:#94a3b8;">No detail data captured</td></tr>`}
          </table>
        </div>
      </div>`;
  }).reverse().join('');
}

/**
 * Toggle detail panel for a job card.
 */
function toggleSessionJobDetail(idx) {
  const detail = document.getElementById('shDetail' + idx);
  const chevron = document.getElementById('shChevron' + idx);
  if (!detail) return;
  const isHidden = detail.style.display === 'none';
  detail.style.display = isHidden ? '' : 'none';
  if (chevron) chevron.style.transform = isHidden ? 'rotate(180deg)' : '';
}

/**
 * Export a single job's results as CSV.
 */
function exportSessionCsv(idx) {
  const job = sessionJobs[idx];
  if (!job || !job.results || job.results.length === 0) return;

  let csv = '';
  if (job.type === 'fetch') {
    csv = 'Container Number,Invoice Number,Invoice File,POD File,POD Missing,Needs Review,Error\n';
    csv += job.results.map(r =>
      [r.containerNumber, r.invoiceNumber, r.invoiceFile || '', r.podFile || '',
       r.podMissing ? 'Yes' : 'No', r.needsReview ? 'Yes' : 'No', r.error || '']
        .map(v => '"' + String(v).replace(/"/g, '""') + '"').join(',')
    ).join('\n');
  } else {
    csv = 'Invoice Number,Container Number,Customer Code,Status,To Emails,Error,Timestamp\n';
    csv += job.results.map(r =>
      [r.invoiceNumber, r.containerNumber, r.customerCode, r.status,
       (r.toEmails || []).join('; '), r.error || '', r.timestamp || '']
        .map(v => '"' + String(v).replace(/"/g, '""') + '"').join(',')
    ).join('\n');
  }

  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  const ts = new Date().toISOString().slice(0, 10);
  a.download = `${job.type}_job_${ts}_${idx + 1}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function _fmtTime(ts) {
  if (!ts) return '--';
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true });
  } catch { return ts.slice(0, 16); }
}

/**
 * Fetch full job results from the API and push to session history.
 * Called from agent-ui.js (fetch jobs) and invoice-sender.js (send jobs).
 */
export async function fetchJobResultsForHistory(jobId, type, summaryEvent) {
  const summary = {
    type,
    jobId,
    timestamp: new Date().toISOString(),
    total: summaryEvent.total || 0,
    invoicesDownloaded: summaryEvent.invoicesDownloaded || 0,
    podsDownloaded: summaryEvent.podsDownloaded || 0,
    podsMissing: summaryEvent.podsMissing || 0,
    sent: summaryEvent.sent || 0,
    skipped: summaryEvent.skipped || 0,
    errors: summaryEvent.errors || 0,
    mismatches: summaryEvent.mismatches || 0,
    results: [],
  };
  try {
    const res = await agentBridge._authFetch(agentBridge.baseUrl + '/jobs/' + jobId + '/status');
    if (res.ok) {
      const data = await res.json();
      summary.results = data.results || [];
    }
  } catch (_) { /* proceed without detailed results */ }
  pushSessionJob(summary);
  if (state.activeTool === 'session-history') renderSessionHistory();
}

/**
 * Export ALL session jobs as a single CSV.
 */
function exportAllSessionCsv() {
  if (sessionJobs.length === 0) return;

  let csv = 'Job #,Type,Timestamp,Container Number,Invoice Number,Status,Error\n';
  sessionJobs.forEach((job, idx) => {
    if (!job.results) return;
    job.results.forEach(r => {
      const status = job.type === 'fetch'
        ? (r.error ? 'error' : (r.podMissing ? 'pod_missing' : 'ok'))
        : (r.status || '--');
      csv += [idx + 1, job.type, job.timestamp,
        r.containerNumber || '', r.invoiceNumber || '', status, r.error || '']
        .map(v => '"' + String(v).replace(/"/g, '""') + '"').join(',') + '\n';
    });
  });

  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `session_history_${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// Window assignments for inline HTML handlers
window.toggleSessionJobDetail = toggleSessionJobDetail;
window.exportSessionCsv = exportSessionCsv;
window.exportAllSessionCsv = exportAllSessionCsv;
