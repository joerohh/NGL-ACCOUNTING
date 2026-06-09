// AR Dashboard — Tool #4
// Reconciliation cockpit. Loads a pre-built AR_AGING_*.xlsx workbook and presents
// it as 10 tabs + Exceptions worklist + cross-tool actions + inline editing.
//
// State: arState (see shared/state.js)
// Spec:  docs/superpowers/specs/2026-05-20-ar-dashboard-design.md

import { arState } from '../../shared/state.js';
import './ar-dashboard-model.js';
import './ar-dashboard-exceptions.js';
import './ar-dashboard-loader.js';
import './ar-dashboard-build-writer.js';
import { arRenderBuildPage } from './ar-dashboard-build-ui.js';
import { arRenderLoaded } from './ar-dashboard-views.js';

export function initArDashboard() {
  const view = document.getElementById('arDashboardView');
  if (!view) return;

  if (!arState.loaded) {
    renderEmptyState(view);
  } else {
    renderLoaded(view);
  }
}

function renderEmptyState(view) {
  const today = new Date().toLocaleDateString('en-US', {
    weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
  });
  view.innerHTML = `
    <div class="ar-empty-shell">
      <div class="ar-empty-header">
        <h1>Build today's AR workbook</h1>
        <p class="subtitle">Drop the daily files; the engine reconciles them against yesterday's workbook.</p>
        <p class="date-line">${today}</p>
      </div>
      <div id="arBuildPageHost"></div>
      <div class="ar-empty-divider">
        <span>or</span>
      </div>
      <div class="ar-empty-secondary">
        <h2>Already have a pre-built workbook?</h2>
        <div class="ar-secondary-drop" id="arPrebuiltDropZone">
          <div class="drop-icon">📄</div>
          <div class="drop-title">Drop AR_AGING_MM_DD_YYYY.xlsx</div>
          <div class="drop-help">or click to browse · accepts .xlsx · .xls</div>
        </div>
        <input type="file" id="arFileInput" accept=".xlsx,.xls" style="display:none" />
      </div>
    </div>
  `;
  // Mount the build flow into its host
  const host = view.querySelector('#arBuildPageHost');
  arRenderBuildPage(host);

  // Secondary pre-built drop zone — keep the existing behavior
  const dz = view.querySelector('#arPrebuiltDropZone');
  const fi = view.querySelector('#arFileInput');
  dz.addEventListener('click', () => fi.click());
  fi.addEventListener('change', handleFileSelected);
  dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('drop-hover'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('drop-hover'));
  dz.addEventListener('drop', e => {
    e.preventDefault();
    dz.classList.remove('drop-hover');
    const file = e.dataTransfer.files && e.dataTransfer.files[0];
    if (file && typeof window.arLoadWorkbook === 'function') {
      window.arLoadWorkbook(file);
    }
  });
}

function renderLoaded(view) {
  arRenderLoaded(view);
}

function handleFileSelected(e) {
  const file = e.target.files && e.target.files[0];
  if (!file) return;
  // Loader is implemented in Phase B.
  if (typeof window.arLoadWorkbook === 'function') {
    window.arLoadWorkbook(file);
  } else {
    console.warn('AR loader not yet implemented');
  }
}

// Expose to window for inline-event handlers consistent with other tools.
window.initArDashboard = initArDashboard;
