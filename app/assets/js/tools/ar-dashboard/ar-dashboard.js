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
  view.innerHTML = `
    <div class="centered-stage">
      <h1>Load today's AR aging workbook</h1>
      <p class="subtitle">Drop or pick an <code>AR_AGING_MM_DD_YYYY.xlsx</code>. We'll parse the sheets and surface today's reconciliation exceptions.</p>
      <div class="big-drop kind-excel" id="arDropZone">
        <div class="drop-icon">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="15" x2="21" y2="15"/><line x1="9" y1="3" x2="9" y2="21"/>
          </svg>
        </div>
        <div class="drop-title">Drop AR aging workbook</div>
        <div class="drop-help">Pre-built daily workbook with AR Register, Collections, Schedule, TMS, ADJUSTMENT sheets.</div>
        <div class="drop-types">.xlsx · .xls</div>
      </div>
      <input type="file" id="arFileInput" accept=".xlsx,.xls" style="display:none" />
    </div>
  `;
  const dz = view.querySelector('#arDropZone');
  const fi = view.querySelector('#arFileInput');
  dz.addEventListener('click', () => fi.click());
  fi.addEventListener('change', handleFileSelected);
  dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('drop-hover'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('drop-hover'));
  dz.addEventListener('drop', e => {
    e.preventDefault();
    dz.classList.remove('drop-hover');
    const file = e.dataTransfer.files && e.dataTransfer.files[0];
    if (file) {
      if (typeof window.arLoadWorkbook === 'function') window.arLoadWorkbook(file);
      else console.warn('AR loader not yet implemented');
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
