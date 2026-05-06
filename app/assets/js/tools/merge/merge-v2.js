// ══════════════════════════════════════════════════════════
//  MERGE TOOL V2 — state machine + render functions
//  Spec:   docs/superpowers/specs/2026-05-05-merge-tool-ux-refinement-design.md
//  Plan:   docs/superpowers/plans/2026-05-05-merge-tool-v2-m1-foundation.md
//  Mockup: app/mockups/merge-tool-redesign.html
//
//  M1 (Foundation): Empty + Loading states render; Review is a stub
//  showing the loaded file name. Real Excel parsing comes in M2.
// ══════════════════════════════════════════════════════════
import { escHtml } from '../../shared/utils.js';

// ── Module-local state ──
const v2State = {
  subMode: 'empty',        // empty | loading | review | fetching | ready | merging | done
  excelFile: null,         // File handle (M2 will parse it)
  pendingMode: null,       // mode about to run (M4)
  completedModes: [],      // mode keys that produced output this session (M4)
  lastCompletedMode: null, // for the Done banner / focus (M4)
};

// State group is what the header/toggle buttons key off.
// Within a group, sub-states (loading vs empty, fetching vs review) pick which renderer fires.
const STATE_GROUP = {
  empty: 's1', loading: 's1',
  review: 's2', fetching: 's2',
  ready: 's3',
  merging: 's4', done: 's4',
};

const STATES = {
  s1: () => v2State.subMode === 'loading' ? renderLoading() : renderEmpty(),
  s2: () => v2State.subMode === 'fetching' ? renderFetching() : renderReview(),
  s3: () => renderReady(),
  s4: () => v2State.subMode === 'merging' ? renderMerging() : renderDone(),
};

let _initialized = false;

// ── Public entry — called from app.js when the v2 view is shown ──
export function initMergeV2() {
  if (_initialized) {
    // Re-render in case state changed since last view (e.g., navigated away then back)
    setStateV2(v2State.subMode);
    return;
  }
  _initialized = true;
  // Wire the hidden Excel input — change event picks up the file from native picker
  const xinput = document.getElementById('v2ExcelInput');
  if (xinput) xinput.addEventListener('change', handleExcelChange);
  setStateV2('empty');
}

// ── Public state setter ──
export function setStateV2(name) {
  // Entering Empty resets the session (used by `+ New Merge` header button + Start over)
  if (name === 'empty') {
    v2State.completedModes = [];
    v2State.lastCompletedMode = null;
    v2State.excelFile = null;
    // Reset the file input value so a re-pick of the same file fires `change` again
    const xinput = document.getElementById('v2ExcelInput');
    if (xinput) xinput.value = '';
  }
  v2State.subMode = name;
  const group = STATE_GROUP[name] || name;
  // Header action button visibility — both stay hidden until M3/M4 actually need them
  const back = document.getElementById('v2BtnBackToReady');
  const fresh = document.getElementById('v2BtnNewMerge');
  if (back)  back.style.display  = (group === 's4') ? '' : 'none';
  if (fresh) fresh.style.display = (group === 's2' || group === 's3' || group === 's4') ? '' : 'none';
  // Render
  const renderer = STATES[group];
  const wa = document.getElementById('v2WorkArea');
  if (renderer && wa) wa.innerHTML = renderer();
}

// ── Excel drop / pick ──
function triggerExcel() {
  const xinput = document.getElementById('v2ExcelInput');
  if (xinput) xinput.click();
}

function handleExcelChange(e) {
  const file = e.target.files && e.target.files[0];
  if (!file) return;
  v2State.excelFile = file;
  setStateV2('loading');
  // M1: simulated transition. M2 will parse the workbook here and only
  // transition to 'review' once parsing finishes (success) or surface
  // an error inline (failure).
  setTimeout(() => {
    if (v2State.subMode === 'loading') setStateV2('review');
  }, 1200);
}

// ── State renderers ──

function renderEmpty() {
  return `
    <div class="centered-stage">
      <h1>Start a new merge</h1>
      <p class="subtitle">Upload your Excel manifest first — we'll match PDFs to it next.</p>
      <div class="big-drop kind-excel" onclick="window.v2TriggerExcel()">
        <div class="drop-icon">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="15" x2="21" y2="15"/><line x1="9" y1="3" x2="9" y2="21"/>
          </svg>
        </div>
        <div class="drop-title">Drop Excel Manifest</div>
        <div class="drop-help">Needs a Container Number column to match PDFs automatically</div>
        <div class="drop-types">.xlsx · .xls · .csv</div>
      </div>
      <div class="hint-chip"><span class="step-num">2</span> We'll check the manifest, then fetch from APIs</div>
    </div>
  `;
}

function renderLoading() {
  return `
    <div class="centered-stage">
      <h1>Start a new merge</h1>
      <p class="subtitle">Reading your manifest…</p>
      <div class="big-drop loading">
        <div class="big-spinner"></div>
        <div class="drop-title">Loading…</div>
        <div class="drop-help">Reading container numbers and checking for issues</div>
      </div>
      <div class="hint-chip"><span class="step-num">2</span> We'll check the manifest, then fetch from APIs</div>
    </div>
  `;
}

function renderReview() {
  // M1 stub — M2 replaces this with the real review card + table
  const fname = v2State.excelFile ? escHtml(v2State.excelFile.name) : '(no file)';
  return `
    <div class="centered-stage">
      <h1>Review (M2 stub)</h1>
      <p class="subtitle">Loaded file: <strong>${fname}</strong></p>
      <p class="subtitle" style="font-size:0.86rem; color:#94a3b8;">
        Excel parsing + pre-fetch validation is coming in Milestone 2.
      </p>
      <button class="merge-btn" onclick="window.v2SetState('empty')">Start over</button>
    </div>
  `;
}

// Placeholders — implemented in later milestones
function renderFetching() { return `<div class="centered-stage"><h1>Fetching (M3)</h1><p class="subtitle">Coming in Milestone 3.</p></div>`; }
function renderReady()    { return `<div class="centered-stage"><h1>Ready (M3)</h1><p class="subtitle">Coming in Milestone 3.</p></div>`; }
function renderMerging()  { return `<div class="centered-stage"><h1>Merging (M4)</h1><p class="subtitle">Coming in Milestone 4.</p></div>`; }
function renderDone()     { return `<div class="centered-stage"><h1>Done (M4)</h1><p class="subtitle">Coming in Milestone 4.</p></div>`; }

// ── Expose to inline onclick handlers in render strings ──
window.v2TriggerExcel = triggerExcel;
window.v2SetState = setStateV2;
window.initMergeV2 = initMergeV2;
