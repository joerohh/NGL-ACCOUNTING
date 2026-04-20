// ══════════════════════════════════════════════════════════════════
//  INI CHASSIS FINDER — Reformat TMS Excel + fetch chassis from QBO
// ══════════════════════════════════════════════════════════════════
import { chassisState, state } from '../../shared/state.js';
import { agentBridge } from '../../shared/agent-client.js';
import { escHtml, triggerDownload, normalizeHeader, findColumnKey, CSV_ALIASES } from '../../shared/utils.js';

// ── Raw TMS column aliases (for flexible header matching) ──
const RAW_ALIASES = {
  date:       ['date'],
  woNo:       ['wo#', 'wono', 'wonumber', 'wo', 'workorder', 'workordernumber', 'workorderno', 'wonum', 'wknumber'],
  billTo:     ['id', 'billto', ...CSV_ALIASES.customerCode],
  contNo:     [...CSV_ALIASES.containerNumber, 'equipment'],
  mbl:        ['mblbooking#', 'mbl#', 'mblbookingnumber', 'mbl', 'booking#', 'bookingnumber', 'booking', 'mblno', 'mbookingno', 'mblbooking'],
  ref:        ['ref#', 'refno', 'ref', 'refnumber', 'reference', 'referencenumber'],
  amount:     ['invamt', ...CSV_ALIASES.amount],
  invNo:      CSV_ALIASES.invoiceNumber,
};

// ── Template column order ──
const TEMPLATE_COLS = ['DATE', 'W/O NO', 'BILLTO', 'CONT NO', 'CHASSIS #', 'MBL#', 'REF#', 'AMOUNT', 'INV#', 'CNEE'];

// ══════════════════════════════════════════════════════════
//  Excel Parsing
// ══════════════════════════════════════════════════════════

function parseRawExcel(file) {
  const reader = new FileReader();
  reader.onload = (e) => {
    try {
      const wb = XLSX.read(e.target.result, { type: 'array' });
      const ws = wb.Sheets[wb.SheetNames[0]];
      const json = XLSX.utils.sheet_to_json(ws, { defval: '' });

      if (!json.length) {
        chLog('error', 'Excel file is empty.');
        return;
      }

      const headers = Object.keys(json[0]);

      // Map raw columns to template fields
      const colMap = {};
      for (const [field, aliases] of Object.entries(RAW_ALIASES)) {
        const found = findColumnKey(headers, aliases);
        if (found) colMap[field] = found;
      }

      // Validate required columns
      if (!colMap.contNo && !colMap.invNo) {
        chLog('error', 'Could not find Container or Invoice column in the Excel file.');
        return;
      }

      // Build rows in template format
      const rows = [];
      for (const raw of json) {
        const invNo = String(colMap.invNo ? raw[colMap.invNo] : '').trim();
        if (!invNo) continue; // skip rows without invoice number

        rows.push({
          date:    colMap.date   ? raw[colMap.date]   : '',
          woNo:    colMap.woNo   ? String(raw[colMap.woNo]).trim()   : '',
          billTo:  colMap.billTo ? String(raw[colMap.billTo]).trim() : '',
          contNo:  colMap.contNo ? String(raw[colMap.contNo]).trim() : '',
          chassis: '',  // will be filled by QBO lookup
          mbl:     colMap.mbl    ? String(raw[colMap.mbl]).trim()    : '',
          ref:     colMap.ref    ? String(raw[colMap.ref]).trim()    : '',
          amount:  colMap.amount ? raw[colMap.amount]  : '',
          invNo,
          cnee:    colMap.billTo ? String(raw[colMap.billTo]).trim() : '',
          status:  'pending',  // pending | searching | found | not_found | error
        });
      }

      chassisState.rows = rows;
      chassisState.originalFilename = file.name;
      chassisState.isProcessing = false;
      chassisState.jobId = null;

      chLog('success', `Parsed ${rows.length} rows from ${file.name}`);
      chLog('info', `Columns mapped: ${Object.entries(colMap).map(([k, v]) => `${k}=${v}`).join(', ')}`);

      renderChassisTable();
      updateChassisButtons();
    } catch (err) {
      chLog('error', 'Failed to parse Excel: ' + err.message);
    }
  };
  reader.readAsArrayBuffer(file);
}

// ══════════════════════════════════════════════════════════
//  Table Rendering
// ══════════════════════════════════════════════════════════

function renderChassisTable() {
  const tbody = document.getElementById('chassisTableBody');
  const container = document.getElementById('chassisTableContainer');
  if (!tbody || !container) return;

  if (!chassisState.rows.length) {
    container.style.display = 'none';
    return;
  }
  container.style.display = '';

  tbody.innerHTML = chassisState.rows.map((r, i) => {
    const statusBadge = getStatusBadge(r.status, r.chassis);
    const dateStr = formatDate(r.date);
    const amtStr = typeof r.amount === 'number' ? '$' + r.amount.toFixed(2) : escHtml(r.amount);

    return `<tr id="chRow${i}">
      <td style="white-space:nowrap;">${escHtml(dateStr)}</td>
      <td style="font-family:monospace; font-size:0.8rem;">${escHtml(r.woNo)}</td>
      <td>${escHtml(r.billTo)}</td>
      <td style="font-family:monospace; font-size:0.8rem;">${escHtml(r.contNo)}</td>
      <td style="font-family:monospace; font-size:0.8rem;" id="chChassis${i}">${r.chassis ? escHtml(r.chassis) : statusBadge}</td>
      <td style="font-size:0.8rem;">${escHtml(r.mbl)}</td>
      <td style="font-size:0.8rem;">${escHtml(r.ref)}</td>
      <td style="font-family:monospace; text-align:right;">${amtStr}</td>
      <td style="font-family:monospace; font-size:0.8rem; font-weight:600;">${escHtml(r.invNo)}</td>
      <td id="chCnee${i}">${escHtml(r.cnee)}</td>
    </tr>`;
  }).join('');

  // Update summary
  const summary = document.getElementById('chassisSummary');
  if (summary) {
    const found = chassisState.rows.filter(r => r.chassis).length;
    const total = chassisState.rows.length;
    summary.textContent = found > 0 ? `${found}/${total} chassis found` : `${total} rows loaded`;
  }
}

function getStatusBadge(status, chassis) {
  if (chassis) return `<span style="color:#16a34a; font-weight:600;">${escHtml(chassis)}</span>`;
  switch (status) {
    case 'pending':   return '<span style="color:#94a3b8;">—</span>';
    case 'searching': return '<span style="color:#ea580c;">Searching...</span>';
    case 'found':     return '<span style="color:#16a34a;">Found</span>';
    case 'not_found': return '<span style="color:#dc2626;">Not Found</span>';
    case 'error':     return '<span style="color:#dc2626;">Error</span>';
    default:          return '<span style="color:#94a3b8;">—</span>';
  }
}

function formatDate(val) {
  if (!val) return '';
  if (val instanceof Date) {
    return `${(val.getMonth() + 1).toString().padStart(2, '0')}/${val.getDate().toString().padStart(2, '0')}/${val.getFullYear()}`;
  }
  // If it's a serial number (Excel date), convert it
  if (typeof val === 'number' && val > 40000 && val < 60000) {
    const d = new Date((val - 25569) * 86400 * 1000);
    return `${(d.getMonth() + 1).toString().padStart(2, '0')}/${d.getDate().toString().padStart(2, '0')}/${d.getFullYear()}`;
  }
  return String(val);
}

// ══════════════════════════════════════════════════════════
//  Chassis Lookup (QBO)
// ══════════════════════════════════════════════════════════

async function startChassisLookup() {
  if (chassisState.isProcessing) return;
  if (!chassisState.rows.length) return;

  // Check agent + QBO status
  if (!state.agentConnected) {
    chLog('error', 'Agent is offline. Start the agent first.');
    return;
  }

  chassisState.isProcessing = true;
  updateChassisButtons();

  // Reset statuses
  for (const row of chassisState.rows) {
    row.status = 'pending';
    row.chassis = '';
  }
  renderChassisTable();

  const rows = chassisState.rows.map(r => ({
    invoiceNumber: r.invNo,
    containerNumber: r.contNo,
  }));

  chLog('info', `Starting chassis lookup for ${rows.length} invoices...`);

  try {
    const res = await fetch(agentBridge.baseUrl + '/chassis/lookup', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...agentBridge._authHeaders(),
      },
      body: JSON.stringify({ rows }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }

    const data = await res.json();
    chassisState.jobId = data.jobId;
    chLog('info', `Job ${data.jobId} started — ${data.total} invoices`);

    // Stream SSE events
    agentBridge.streamProgress(data.jobId, handleChassisEvent);

  } catch (err) {
    chLog('error', 'Failed to start chassis lookup: ' + err.message);
    chassisState.isProcessing = false;
    updateChassisButtons();
  }
}

function handleChassisEvent(event) {
  const type = event.type;

  if (type === 'chassis_progress') {
    const idx = chassisState.rows.findIndex(r => r.invNo === event.invoiceNumber);
    if (idx >= 0) {
      chassisState.rows[idx].status = 'searching';
      updateChassisRow(idx);
    }
    updateProgressBar(event.index, event.total);
  }

  else if (type === 'chassis_found') {
    const idx = chassisState.rows.findIndex(r => r.invNo === event.invoiceNumber);
    if (idx >= 0) {
      chassisState.rows[idx].chassis = event.chassisNumber;
      if (event.cnee) chassisState.rows[idx].cnee = event.cnee;
      chassisState.rows[idx].status = 'found';
      updateChassisRow(idx);
    }
    chLog('success', `${event.invoiceNumber} → chassis: ${event.chassisNumber}${event.cnee ? ', cnee: ' + event.cnee : ''}`);
  }

  else if (type === 'chassis_not_found') {
    const idx = chassisState.rows.findIndex(r => r.invNo === event.invoiceNumber);
    if (idx >= 0) {
      if (event.cnee) chassisState.rows[idx].cnee = event.cnee;
      chassisState.rows[idx].status = 'not_found';
      updateChassisRow(idx);
    }
    chLog('warning', `${event.invoiceNumber} — no chassis found (${event.reason || 'unknown'})`);
  }

  else if (type === 'chassis_error') {
    const idx = chassisState.rows.findIndex(r => r.invNo === event.invoiceNumber);
    if (idx >= 0) {
      chassisState.rows[idx].status = 'error';
      updateChassisRow(idx);
    }
    chLog('error', `${event.invoiceNumber} — ${event.error}`);
  }

  else if (type === 'chassis_complete') {
    chassisState.isProcessing = false;
    updateProgressBar(event.total, event.total);
    updateChassisButtons();
    chLog('success', `Chassis lookup complete: ${event.found}/${event.total} found, ${event.errors} errors`);
  }

  else if (type === 'login_required') {
    chassisState.isProcessing = false;
    updateChassisButtons();
    chLog('error', 'QBO not connected. Please authorize via Settings.');
  }
}

function updateChassisRow(idx) {
  const r = chassisState.rows[idx];

  // Update chassis cell
  const chassisCell = document.getElementById(`chChassis${idx}`);
  if (chassisCell) {
    chassisCell.innerHTML = r.chassis
      ? `<span style="color:#16a34a; font-weight:600;">${escHtml(r.chassis)}</span>`
      : getStatusBadge(r.status);
  }

  // Update CNEE cell if we got it from QBO
  const cneeCell = document.getElementById(`chCnee${idx}`);
  if (cneeCell && r.cnee) {
    cneeCell.textContent = r.cnee;
  }

  // Update summary
  const summary = document.getElementById('chassisSummary');
  if (summary) {
    const found = chassisState.rows.filter(r => r.chassis).length;
    summary.textContent = `${found}/${chassisState.rows.length} chassis found`;
  }
}

function updateProgressBar(current, total) {
  const bar = document.getElementById('chassisProgressBar');
  const text = document.getElementById('chassisProgressText');
  if (!bar || !text) return;

  const pct = total > 0 ? Math.round((current / total) * 100) : 0;
  bar.style.width = pct + '%';
  text.textContent = `${current} / ${total}`;

  const container = document.getElementById('chassisProgressContainer');
  if (container) container.style.display = total > 0 ? '' : 'none';
}

// ══════════════════════════════════════════════════════════
//  Excel Download
// ══════════════════════════════════════════════════════════

function downloadChassisExcel() {
  if (!chassisState.rows.length) return;

  const data = chassisState.rows.map(r => ({
    'DATE': formatDate(r.date),
    'W/O NO': r.woNo,
    'BILLTO': r.billTo,
    'CONT NO': r.contNo,
    'CHASSIS #': r.chassis || '',
    'MBL#': r.mbl,
    'REF#': r.ref,
    'AMOUNT': typeof r.amount === 'number' ? r.amount : parseFloat(String(r.amount).replace(/[$,]/g, '')) || r.amount,
    'INV#': r.invNo,
    'CNEE': r.cnee,
  }));

  const ws = XLSX.utils.json_to_sheet(data, { header: TEMPLATE_COLS });

  // Set column widths
  ws['!cols'] = [
    { wch: 12 },  // DATE
    { wch: 16 },  // W/O NO
    { wch: 12 },  // BILLTO
    { wch: 14 },  // CONT NO
    { wch: 14 },  // CHASSIS #
    { wch: 18 },  // MBL#
    { wch: 8 },   // REF#
    { wch: 10 },  // AMOUNT
    { wch: 14 },  // INV#
    { wch: 12 },  // CNEE
  ];

  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, 'Sheet1');

  // Generate filename from original or default
  let filename = 'chassis_report.xlsx';
  if (chassisState.originalFilename) {
    filename = chassisState.originalFilename.replace(/\.[^.]+$/, '') + '_formatted.xlsx';
  }

  const buf = XLSX.write(wb, { type: 'array', bookType: 'xlsx' });
  const blob = new Blob([buf], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
  triggerDownload(blob, filename);

  chLog('success', `Downloaded: ${filename}`);
}

// ══════════════════════════════════════════════════════════
//  UI Helpers
// ══════════════════════════════════════════════════════════

function updateChassisButtons() {
  const findBtn = document.getElementById('chassisFindBtn');
  const downloadBtn = document.getElementById('chassisDownloadBtn');
  const hasRows = chassisState.rows.length > 0;
  const hasChassis = chassisState.rows.some(r => r.chassis);

  if (findBtn) {
    findBtn.disabled = !hasRows || chassisState.isProcessing;
    findBtn.textContent = chassisState.isProcessing ? 'Searching...' : 'Find Chassis Numbers';
  }
  if (downloadBtn) {
    downloadBtn.style.display = hasRows ? '' : 'none';
  }
}

function chLog(level, msg) {
  const log = document.getElementById('chassisLog');
  if (!log) return;
  log.style.display = '';
  const color = { info: '#94a3b8', success: '#4ade80', error: '#f87171', warning: '#fbbf24' }[level] || '#94a3b8';
  const prefix = { info: '[INFO]', success: '[OK]  ', error: '[ERR] ', warning: '[WARN]' }[level] || '[INFO]';
  log.innerHTML += `<div style="color:${color}; font-family:monospace; font-size:0.8rem; padding:2px 0;">${prefix} ${escHtml(msg)}</div>`;
  log.scrollTop = log.scrollHeight;
}

function resetChassisFinder() {
  chassisState.rows = [];
  chassisState.pdfs = [];
  chassisState.originalFilename = '';
  chassisState.isProcessing = false;
  chassisState.jobId = null;

  const tbody = document.getElementById('chassisTableBody');
  if (tbody) tbody.innerHTML = '';
  const container = document.getElementById('chassisTableContainer');
  if (container) container.style.display = 'none';
  const log = document.getElementById('chassisLog');
  if (log) { log.innerHTML = ''; log.style.display = 'none'; }
  const progress = document.getElementById('chassisProgressContainer');
  if (progress) progress.style.display = 'none';

  // Reset drop zone
  const dropContent = document.getElementById('chassisExcelDropContent');
  const loadedState = document.getElementById('chassisExcelLoadedState');
  if (dropContent) dropContent.style.display = '';
  if (loadedState) loadedState.style.display = 'none';

  updateChassisButtons();
}

// ══════════════════════════════════════════════════════════
//  Drop Zone + File Input Handlers
// ══════════════════════════════════════════════════════════

let _chassisDropInitialized = false;
export function chassisInitDropZones() {
  if (_chassisDropInitialized) return;
  const dropZone = document.getElementById('chassisExcelDropZone');
  if (!dropZone) return;
  _chassisDropInitialized = true;

  dropZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropZone.classList.add('drag-over');
  });
  dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
  dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.classList.remove('drag-over');
    const file = e.dataTransfer.files[0];
    if (file && /\.(xlsx|xls|csv)$/i.test(file.name)) {
      handleChassisExcel(file);
    }
  });
}

function handleChassisExcel(file) {
  // Show loaded state
  const dropContent = document.getElementById('chassisExcelDropContent');
  const loadedState = document.getElementById('chassisExcelLoadedState');
  const nameEl = document.getElementById('chassisExcelFileName');

  if (dropContent) dropContent.style.display = 'none';
  if (loadedState) loadedState.style.display = 'flex';
  if (nameEl) nameEl.textContent = file.name;

  parseRawExcel(file);
}

// ── Global exports for inline onclick handlers ──
window.chassisExcelInputChange = function(input) {
  if (input.files[0]) handleChassisExcel(input.files[0]);
  input.value = '';
};
window.startChassisLookup = startChassisLookup;
window.downloadChassisExcel = downloadChassisExcel;
window.resetChassisFinder = resetChassisFinder;

export { startChassisLookup, downloadChassisExcel, resetChassisFinder };
