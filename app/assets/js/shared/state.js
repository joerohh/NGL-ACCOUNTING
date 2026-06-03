// ══════════════════════════════════════════════════════════
//  GLOBAL STATE — shared across all modules
// ══════════════════════════════════════════════════════════

// ── Merge tool state ──
export const state = {
  activeTool: 'home',  // 'home' | 'merge' | 'invoice-sender'
  pdfs: [],            // Array<{id, name, size, file}>
  excelRows: [],       // Array<{containerNumber, invoiceNumber?, workOrderNumber?}>
  skippedRows: [],     // Array<{excelRow, containerNumber, invoiceNumber, priorRow}>
  mergeResults: [],    // Array<{containerNumber, bytes, filename, subfolder}>
  isProcessing: false,
  logCollapsed: true,
  agentConnected: false,
  qboConnected: false,           // QBO API token valid (set by agentHealthCheck)
  tmsLoggedIn: false,            // TMS browser session active (set by agentHealthCheck)
  _agentCustomersSynced: false,  // true after first customer sync to agent
  currentUser: null,   // { id, username, displayName, role } — set after login
  activeJobId: null,
  mergeMode: 'per-container',   // 'per-container' | 'all-in-one' | '{type}-only'
  sortOrder: 'excel',           // 'excel' | 'container' | 'invoice'
  _workersFailed: false,        // true if Web Workers unavailable (fallback to sequential)
  sortableInstance: null,        // SortableJS instance for manual merge
};

// ── Invoice sender state ──
// InvoiceRecord fields: id, invoiceNumber, customerName, invoiceDate, dueDate,
//   amount, email, poNumber, containerNumber, bolNumber, customerCode, subject,
//   emailOverride, subjectOverride, customerMatch, resolvedEmails, resolvedCc,
//   resolvedBcc, sendMethod, requiredDocs, customerActive, validationStatus,
//   sendStatus, sentAt, errorMessage, doSenderEmail (from CSV for OEC flow)
export const invoiceState = {
  csvLoaded: false,
  invoices: [],           // Array<InvoiceRecord>
  subjectTemplate: '[NGL_INV] {invoice_number} - Container#{container_number}',
  warehouseSubjectTemplate: 'Warehouse Invoice {invoice_number} - {customer_name}',
  selectedIds: new Set(),
  reviewingId: null,
  isProcessing: false,
  logCollapsed: true,
};

// ── Send progress state ──
export const sendState = {
  jobId: null,
  isRunning: false,
  testMode: false,
  results: [],
  sent: 0,
  skipped: 0,
  errors: 0,
  mismatches: 0,
  missingDocs: 0,
  startTime: null,       // Date.now() when send job starts
  completedCount: 0,     // invoices fully processed (for ETA calc)
  eventSource: null,     // SSE EventSource for send job progress
  // v2.62 — Results view (Fix 1 + Fix 2)
  currentTab: 'needs-attention',   // 'needs-attention' | 'sent' | 'all'
  activePanelInvoiceId: null,
  retry: {},                       // { [invoiceNumber]: { panelStage, attached: { POD: {name, file, stage, verifiedBy?, detectedAs?} } } }
};

// ── Chassis finder state ──
export const chassisState = {
  rows: [],              // [{date, woNo, billTo, contNo, chassis, mbl, ref, amount, invNo, cnee, status}]
  pdfs: [],              // uploaded PDFs for merge
  originalFilename: '',  // raw TMS filename for output naming
  isProcessing: false,
  jobId: null,
};

// ── Customer manager state ──
export const custState = {
  editingCode: null,   // null = creating, string = editing
  docMode: 'all',      // 'all' or 'specific'
  orGroups: [],        // e.g. [['bol','pol'], ['pl','do']]
  customers: [],       // cached list, refreshed by custLoadCustomers (v69: dup-code check)
};

// ── AR Dashboard state ──
export const arState = {
  loaded: false,                 // true once a workbook is parsed
  filename: null,                // currently-loaded filename
  model: null,                   // parsed in-memory model (see ar-dashboard-loader.js)
  activeTab: 'summary',          // which sub-tab is visible
  selectedRows: {},              // per-tab selection state, e.g. { collections: 'A0905186835' }
  exceptions: [],                // flat list of detected exceptions
  manualEntries: [],             // user-added rows (credit memos, warehouse, etc.)
  resolvedExceptions: new Set(), // exception IDs marked resolved in this session
  // R2 M2 — build flow state
  buildModalOpen: false,
  buildInputs: {                 // each slot: null OR { file, buf, parsed, error }
    yesterday: null,
    qbo_collection: null,
    qbo_schedule: null,
    tab_bank: null,
    tms_reconcile: null,
  },
  buildResult: null,             // the built model produced by arBuildToday()
  buildOpenKpi: null,            // which preview KPI is expanded ('new' | 'paid' | 'adjust' | 'exception')
  buildSaveFolder: null,         // remembered save folder (also persisted to localStorage)
};
