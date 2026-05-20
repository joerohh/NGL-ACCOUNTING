// ══════════════════════════════════════════════════════════
//  STORAGE TAB — file location, sizes, cleanup
// ══════════════════════════════════════════════════════════
import { state } from '../../shared/state.js';
import { agentBridge } from '../../shared/agent-client.js';
import { fmtSize } from '../../shared/utils.js';

export async function storageLoad() {
  if (!state.agentConnected) {
    // Storage card has '...' placeholders that read fine when offline; no special UI needed.
    return;
  }
  await loadStorageInfo();
}

// ── Storage Card ──
async function loadStorageInfo() {
  try {
    const res = await agentBridge._authFetch(agentBridge.baseUrl + '/storage/info');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const info = await res.json();
    renderStorageInfo(info);
  } catch (e) {
    console.warn('Could not load /storage/info:', e);
    const meta = document.getElementById('storageOutputMeta');
    if (meta) meta.textContent = 'Unavailable — agent not connected.';
    const dlMeta = document.getElementById('storageDownloadsMeta');
    if (dlMeta) dlMeta.textContent = '';
  }
}

function renderStorageInfo(info) {
  const outPath = document.getElementById('storageOutputPath');
  const outMeta = document.getElementById('storageOutputMeta');
  const outBar  = document.getElementById('storageOutputBar');
  const dlPath  = document.getElementById('storageDownloadsPath');
  const dlMeta  = document.getElementById('storageDownloadsMeta');
  const dlBar   = document.getElementById('storageDownloadsBar');
  const lastEl  = document.getElementById('storageLastCleanup');

  if (outPath) outPath.textContent = info.output_root;
  if (outMeta) outMeta.innerHTML =
    `<strong>${fmtSize(info.output_size_bytes)}</strong> · ${info.output_file_count} files · ${info.output_folder_count} folders`;
  if (outBar) {
    // 20 GB scale = 100% (arbitrary visual scale)
    const pct = Math.min(100, info.output_size_bytes / (20 * 1024 * 1024 * 1024) * 100);
    outBar.style.width = `${pct.toFixed(1)}%`;
  }

  if (dlPath) dlPath.textContent = 'agent/downloads';
  if (dlMeta) dlMeta.innerHTML =
    `<strong>${fmtSize(info.downloads_size_bytes)}</strong> · ${info.downloads_batch_count} batches`
    + ` <span style="color:#94a3b8;">· auto-cleaned by the same ${info.retain_days || 7}-day rule</span>`;
  if (dlBar) {
    const pct = Math.min(100, info.downloads_size_bytes / (5 * 1024 * 1024 * 1024) * 100);
    dlBar.style.width = `${pct.toFixed(1)}%`;
  }

  if (lastEl) {
    if (info.last_cleanup_ts && info.last_cleanup_ts > 0) {
      const dt = new Date(info.last_cleanup_ts * 1000);
      const when = dt.toLocaleString();
      lastEl.textContent = `Last cleanup ran ${when} · removed ${info.last_cleanup_files_removed} files (${fmtSize(info.last_cleanup_freed_bytes)})`;
    } else {
      lastEl.textContent = '';
    }
  }
}

// ── Window-exposed action handlers (called from inline onclick attributes) ──

window.settingsCleanupNow = async function() {
  const btn = document.querySelector('[onclick="window.settingsCleanupNow()"]');
  if (btn) btn.disabled = true;
  try {
    const res = await agentBridge._authFetch(agentBridge.baseUrl + '/storage/cleanup', { method: 'POST' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const info = await res.json();
    renderStorageInfo(info);
    alert(`Cleanup complete — freed ${fmtSize(info.last_cleanup_freed_bytes)} across ${info.last_cleanup_files_removed} files.`);
  } catch (e) {
    alert('Cleanup failed: ' + e.message);
  } finally {
    if (btn) btn.disabled = false;
  }
};

window.settingsOpenOutputFolder = async function() {
  try {
    const res = await agentBridge._authFetch(agentBridge.baseUrl + '/storage/info');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const info = await res.json();
    if (typeof agentBridge.openPath === 'function') {
      await agentBridge.openPath(info.output_root);
    } else {
      alert('Open folder action not available on this agent build.');
    }
  } catch (e) {
    alert('Could not open the output folder: ' + e.message);
  }
};

window.settingsChangeOutputFolder = async function() {
  if (typeof agentBridge.pickFolder === 'function') {
    const picked = await agentBridge.pickFolder();
    if (picked && picked.path) loadStorageInfo();
  } else {
    alert('Folder picker not available.');
  }
};

window.loadStorageInfo = loadStorageInfo;
