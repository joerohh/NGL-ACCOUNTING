// ══════════════════════════════════════════════════════════════════
//  ██ APP — Navigation, Init & Responsive Layout (Entry Point) ██
// ══════════════════════════════════════════════════════════════════
import { state, invoiceState } from './shared/state.js';
import { addLog, invAddLog } from './shared/log.js';
import { setupDrop } from './shared/dom-helpers.js';
import { LS_CUSTOMERS } from './shared/constants.js';
import { agentHealthCheck } from './agent-ui.js';
import { renderPdfQueue, setMode, handleExcelFile, handlePdfFiles } from './tools/merge/merge.js';
import { invInitDropZones } from './tools/invoice-sender/invoice-sender.js';
import { custLoadCustomers } from './tools/customers/customers.js';
import { settingsLoad } from './tools/settings/settings.js';

// Prevent browser from opening files dropped anywhere on the page
document.addEventListener('dragover', function(e) { e.preventDefault(); });
document.addEventListener('drop', function(e) { e.preventDefault(); });


// ── Tool Navigation ──
function switchTool(tool) {
  state.activeTool = tool;

  // Close any open tool switcher menus
  document.querySelectorAll('.tool-switcher-menu').forEach(m => m.classList.remove('open'));

  // Hide all views
  document.getElementById('homeView').style.display = 'none';
  document.getElementById('mergeToolView').style.display = 'none';
  document.getElementById('invoiceSenderView').style.display = 'none';
  document.getElementById('customerView').style.display = 'none';
  document.getElementById('settingsView').style.display = 'none';

  // Show selected view
  if (tool === 'home') {
    document.getElementById('homeView').style.display = '';
    refreshHomeMetrics();
  } else if (tool === 'merge') {
    document.getElementById('mergeToolView').style.display = '';
  } else if (tool === 'invoice-sender') {
    document.getElementById('invoiceSenderView').style.display = '';
    invInitDropZones();
  } else if (tool === 'customers') {
    document.getElementById('customerView').style.display = '';
    custLoadCustomers();
  } else if (tool === 'settings') {
    document.getElementById('settingsView').style.display = '';
    settingsLoad();
  }

  // Update sidebar subtitle
  const subtitles = {
    'home': 'Accounting Suite',
    'merge': 'Merging Tool',
    'invoice-sender': 'Invoice Sender',
    'customers': 'Customer Management',
    'settings': 'Settings',
  };
  document.getElementById('headerSubtitle').textContent = subtitles[tool] || '';

  // Show/hide merge-specific controls in sidebar footer
  const mergeControls = document.getElementById('mergeToolControls');
  mergeControls.style.display = tool === 'merge' ? 'flex' : 'none';

  // Update sidebar nav active states
  document.getElementById('navMerge').classList.toggle('active', tool === 'merge');
  document.getElementById('navInvoiceSender').classList.toggle('active', tool === 'invoice-sender');
  document.getElementById('navCustomers').classList.toggle('active', tool === 'customers');
  document.getElementById('navSettings').classList.toggle('active', tool === 'settings');
}

// ── Home Dashboard Metrics ──
function refreshHomeMetrics() {
  // Pending invoices from sender state (guard against TDZ during init)
  let pending = 0;
  try { pending = (invoiceState && invoiceState.invoices) ? invoiceState.invoices.length : 0; } catch (_) {}
  document.getElementById('metricPending').textContent = pending || '--';

  // PDFs merged today (from merge results)
  const merged = (state.mergeResults) ? state.mergeResults.length : 0;
  document.getElementById('metricMerged').textContent = merged;

  // Emails sent — try to fetch from agent audit stats
  document.getElementById('metricSent').textContent = '--';
  if (state.agentConnected) {
    agentBridge._authFetch('http://localhost:8787/audit/stats')
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data && data.sent !== undefined) {
          document.getElementById('metricSent').textContent = data.sent;
        }
      })
      .catch(() => {});
  }

  // Customer count from localStorage
  try {
    const custData = JSON.parse(localStorage.getItem(LS_CUSTOMERS) || '{}');
    const activeCount = Object.values(custData).filter(c => c.active !== false).length;
    document.getElementById('metricCustomers').textContent = activeCount || '--';
  } catch {
    document.getElementById('metricCustomers').textContent = '--';
  }
}

// ── Tool Switcher Dropdown ──
function toggleToolSwitcher(btn) {
  const menu = btn.nextElementSibling;
  const isOpen = menu.classList.contains('open');
  // Close all menus first
  document.querySelectorAll('.tool-switcher-menu').forEach(m => m.classList.remove('open'));
  if (!isOpen) menu.classList.add('open');
}

// Close tool switcher when clicking outside
document.addEventListener('click', function(e) {
  if (!e.target.closest('.tool-switcher')) {
    document.querySelectorAll('.tool-switcher-menu').forEach(m => m.classList.remove('open'));
  }
});


// ══════════════════════════════════════════════════════════
//  DROP ZONE EVENTS  (setupDrop is in shared/dom-helpers.js)
// ══════════════════════════════════════════════════════════
setupDrop('excelDropZone', files => {
  const excel = files.find(f => /\.(xlsx|xls|csv)$/i.test(f.name));
  if (excel) handleExcelFile(excel);
  else {
    const pdfs = files.filter(f => /\.pdf$/i.test(f.name));
    if (pdfs.length) { addLog('warning', 'Dropped PDFs in the Excel zone — adding to PDF queue'); handlePdfFiles(pdfs); }
    else addLog('warning', 'Expected an .xlsx, .xls, or .csv file');
  }
});

setupDrop('pdfDropZone', files => {
  const excel = files.find(f => /\.(xlsx|xls|csv)$/i.test(f.name));
  if (excel) { addLog('info', 'Detected Excel file in PDF zone — processing as manifest'); handleExcelFile(excel); }
  handlePdfFiles(files.filter(f => /\.pdf$/i.test(f.name)));
});


// ══════════════════════════════════════════════════════════
//  RESPONSIVE GRID
// ══════════════════════════════════════════════════════════
function applyResponsiveLayout() {
  const grid = document.getElementById('mainGrid');
  if (window.innerWidth < 900) {
    grid.style.gridTemplateColumns = '1fr';
  } else {
    grid.style.gridTemplateColumns = '1fr 1fr';
  }
}
window.addEventListener('resize', applyResponsiveLayout);
applyResponsiveLayout();


// ══════════════════════════════════════════════════════════
//  INIT
// ══════════════════════════════════════════════════════════
renderPdfQueue();
setMode('idle');
addLog('info', '// NGL Transportation Accounting v2.1');
addLog('info', '// 100% client-side — no files leave your machine');
addLog('info', '// Drop Excel for Auto Mode · Drop PDFs for Manual Mode');
addLog('info', '// AI Agent panel available — start agent with: python main.py');

// Check agent health on load, then every 15 seconds
agentHealthCheck();
setInterval(agentHealthCheck, 15000);

// Start on home page
switchTool('home');

// Initialize Invoice Sender drop zones
invInitDropZones();
invAddLog('info', '// Invoice Sending Tool ready');
invAddLog('info', '// Upload a CSV export and PDF attachments to get started');

// ── Window assignments for inline HTML handlers ──
window.switchTool = switchTool;
window.toggleToolSwitcher = toggleToolSwitcher;
window.refreshHomeMetrics = refreshHomeMetrics;
