// ══════════════════════════════════════════════════════════
//  LOGGING — shared log functions for all tools
// ══════════════════════════════════════════════════════════
import { LOG_COLORS, LOG_PREFIXES, escHtml } from './utils.js';
import { state, invoiceState } from './state.js';

/**
 * Create a log module bound to specific DOM elements and a state object.
 * Eliminates duplication between merge and invoice log panels.
 */
function createLogModule(ids, stateObj) {
  const mod = {
    addLog(level, message) {
      const log = document.getElementById(ids.log);
      const time = new Date().toLocaleTimeString('en-US', { hour12: false });
      const div = document.createElement('div');
      div.style.color = LOG_COLORS[level] || LOG_COLORS.info;
      div.innerHTML =
        `<span style="color:#475569;">${time}</span> ` +
        `<span style="font-weight:700;">${LOG_PREFIXES[level]}</span> ` +
        escHtml(message);
      log.appendChild(div);
      log.scrollTop = log.scrollHeight;
      // Auto-expand on error so the user can see what went wrong
      if (level === 'error' && stateObj.logCollapsed) mod.toggleLog();
    },

    clearLog() {
      document.getElementById(ids.log).innerHTML = '';
      mod.addLog('info', '// Log cleared');
    },

    toggleLog() {
      const body = document.getElementById(ids.body);
      const arrow = document.getElementById(ids.arrow);
      stateObj.logCollapsed = !stateObj.logCollapsed;
      if (stateObj.logCollapsed) {
        body.classList.add('collapsed');
        arrow.classList.add('collapsed');
      } else {
        body.classList.remove('collapsed');
        arrow.classList.remove('collapsed');
        body.style.maxHeight = '250px';
      }
    },

    setProgress(pct, label) {
      const bar = document.getElementById(ids.bar);
      const lbl = document.getElementById(ids.label);
      const con = document.getElementById(ids.container);
      if (pct === null) {
        con.style.display = 'none';
        return;
      }
      con.style.display = 'block';
      bar.style.width = pct + '%';
      if (lbl && label !== undefined) lbl.textContent = label;
    },
  };
  return mod;
}

// ── Merge Tool Log ──
const mergeLog = createLogModule({
  log: 'statusLog', body: 'statusLogBody', arrow: 'logToggleArrow',
  bar: 'progressBar', label: 'progressLabel', container: 'progressContainer',
}, state);

export const addLog      = mergeLog.addLog;
export const clearLog    = mergeLog.clearLog;
export const toggleLog   = mergeLog.toggleLog;
export const setProgress = mergeLog.setProgress;

// ── Invoice Sender Log ──
const invLog = createLogModule({
  log: 'invStatusLog', body: 'invStatusLogBody', arrow: 'invLogToggleArrow',
  bar: 'invProgressBar', label: 'invProgressLabel', container: 'invProgressContainer',
}, invoiceState);

export const invAddLog      = invLog.addLog;
export const invClearLog    = invLog.clearLog;
export const invToggleLog   = invLog.toggleLog;
export const invSetProgress = invLog.setProgress;

// ── Window assignments for inline HTML handlers ──
window.toggleLog = toggleLog;
window.clearLog = clearLog;
window.invToggleLog = invToggleLog;
window.invClearLog = invClearLog;
