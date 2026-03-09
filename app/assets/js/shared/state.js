// ══════════════════════════════════════════════════════════
//  GLOBAL STATE — shared across all modules
// ══════════════════════════════════════════════════════════

// ── Merge tool state ──
export const state = {
  activeTool: 'home',  // 'home' | 'merge' | 'invoice-sender'
  mode: 'idle',        // 'idle' | 'auto' | 'manual'
  pdfs: [],            // Array<{id, name, size, file}>
  excelRows: [],       // Array<{containerNumber, invoiceNumber?}>
  mergeResults: [],    // Array<{containerNumber, bytes, filename, subfolder}>
  isProcessing: false,
  logCollapsed: true,
  agentConnected: false,
  _agentCustomersSynced: false,  // true after first customer sync to agent
  currentUser: null,   // { id, username, displayName, role } — set after login
  activeJobId: null,
  mergeMode: 'per-container',   // 'per-container' | 'all-in-one' | 'invoices-only' | 'pods-only'
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
  subjectTemplate: 'Invoice {invoice_number} - {customer_name}',
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
};

// ── Customer manager state ──
export const custState = {
  editingCode: null,   // null = creating, string = editing
  docMode: 'all',      // 'all' or 'specific'
  orGroups: [],        // e.g. [['bol','pol'], ['pl','do']]
};
