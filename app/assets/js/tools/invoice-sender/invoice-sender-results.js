// Invoice Sender — Results view (Fix 1 + Fix 2) for v2.62.
// Activated after a send job completes. Replaces the live progress table
// with a tabbed results table + diagnostic side panel + drop-zone retry flow.

import { sendState, invoiceState } from '../../shared/state.js';
import { escHtml } from '../../shared/utils.js';

const SLOT_LABELS = { pod: 'POD', bol: 'BOL', pol: 'POL', do: 'DO', pl: 'PL' };

export function showResultsView() {
  const el = document.getElementById('invSendResults');
  if (el) el.style.display = 'grid';
}

export function hideResultsView() {
  const el = document.getElementById('invSendResults');
  if (el) el.style.display = 'none';
  const inner = document.getElementById('invSendResults');
  if (inner) inner.classList.remove('panel-open');
}

export function renderResults() {
  // Stub — implemented in Task 8
  console.log('[v62] renderResults stub');
}

// Expose for invoice-sender.js + others
window.invShowResultsView = showResultsView;
window.invHideResultsView = hideResultsView;
window.invRenderResults = renderResults;
