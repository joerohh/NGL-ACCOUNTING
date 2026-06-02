// AR build engine — Node verification harness.
//
// Mirrors tools/verify_ar_build.py: discovers test build folders, chains each
// build off the previous day's hand-built workbook (or the seed AR_AGING file
// at the assets root), runs the JS port, compares cell-by-cell against the
// hand-built target, prints a match-rate table.
//
// Read-only. Doesn't write output files.
//
// Usage:
//   node tools/verify_ar_build_js.mjs
//
// Env override:
//   AR_ASSETS=<path>   default: C:/Users/Joseph/Desktop/NGL ACCOUNTING SERVICE - TEST DATA/AR_AGING_assets

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);

// Load xlsx and expose as global before importing the engine modules
// (they read globalThis.XLSX so the same code runs in browser + Node).
globalThis.XLSX = require('./node_modules/xlsx');

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT = path.dirname(__dirname);

const ASSETS = process.env.AR_ASSETS
  || 'C:/Users/Joseph/Desktop/NGL ACCOUNTING SERVICE - TEST DATA/AR_AGING_assets';

// Dynamic imports so they happen AFTER globalThis.XLSX is wired.
const ENGINE_DIR = path.join(ROOT, 'app/assets/js/tools/ar-dashboard');
const buildMod = await import(pathToFileURL(path.join(ENGINE_DIR, 'ar-dashboard-build.js')).href);
const verifyMod = await import(pathToFileURL(path.join(ENGINE_DIR, 'ar-dashboard-build-verify.js')).href);
const loaderMod = await import(pathToFileURL(path.join(ENGINE_DIR, 'ar-dashboard-build-loader.js')).href);

const { arBuildToday } = buildMod;
const { arVerifyBuild } = verifyMod;
const {
  parseYesterdaysWorkbook,
  parseQboDailyCollection,
  parseQboDailySchedule,
  parseTabBankRemittance,
  parseTmsReconcile,
} = loaderMod;

// ---------------------------------------------------------------------------
// Build discovery (mirrors verify_ar_build.py:discover_builds)
// ---------------------------------------------------------------------------

const DATE_RE = /AR_AGING_(\d\d)_(\d\d)_(\d{4})/i;

function parseTargetDate(filename) {
  const m = path.basename(filename).match(DATE_RE);
  if (!m) return null;
  const [, mm, dd, yyyy] = m;
  const d = new Date(+yyyy, +mm - 1, +dd);
  return isNaN(d.getTime()) ? null : d;
}

function readBuf(p) {
  return new Uint8Array(fs.readFileSync(p));
}

function discoverBuilds() {
  const candidates = [];
  for (const entry of fs.readdirSync(ASSETS)) {
    const sub = path.join(ASSETS, entry);
    if (fs.statSync(sub).isDirectory()) candidates.push(sub);
  }
  // The weirdly-nested 5/19 folder
  const nested = path.join(ASSETS, '5/20 data (5/5.19');
  if (fs.existsSync(nested) && fs.statSync(nested).isDirectory()) {
    candidates.push(nested);
  }

  const builds = [];
  for (const folder of candidates) {
    let files;
    try { files = fs.readdirSync(folder); } catch { continue; }

    const targets = files.filter(f => f.startsWith('AR_AGING_') && f.endsWith('.xlsx'));
    let targetPath = targets.length ? path.join(folder, targets[0]) : null;
    let targetDate = targetPath ? parseTargetDate(targets[0]) : null;

    const tab = files.filter(f => f.startsWith('Collection_Payment') && f.endsWith('.xlsx'));
    const col = files.filter(f => f.includes('Daily Collection') && f.endsWith('.xlsx'));
    const sch = files.filter(f => f.includes('Daily Schedule') && f.endsWith('.xlsx'));
    const tms = files.filter(f => f.startsWith('APAR RECONCILE') && f.endsWith('.xlsx'));

    if (!(tab.length && col.length && sch.length && tms.length)) continue;

    // If no target in folder, peek at TAB BANK post_date to guess
    if (!targetPath) {
      try {
        const wb = globalThis.XLSX.read(readBuf(path.join(folder, tab[0])), { type: 'array', cellDates: true });
        const ws = wb.Sheets[wb.SheetNames[0]];
        const rows = globalThis.XLSX.utils.sheet_to_json(ws, { header: 1, defval: null, raw: true });
        for (const r of rows) {
          if (r && r[4] instanceof Date) { targetDate = r[4]; break; }
        }
        if (targetDate) {
          const mm = String(targetDate.getMonth() + 1).padStart(2, '0');
          const dd = String(targetDate.getDate()).padStart(2, '0');
          const guess = `AR_AGING_${mm}_${dd}_${targetDate.getFullYear()}.xlsx`;
          const guessPath = path.join(ASSETS, guess);
          if (fs.existsSync(guessPath)) targetPath = guessPath;
        }
      } catch { /* ignore */ }
    }
    if (!targetPath || !targetDate) continue;

    builds.push({
      folder,
      targetWorkbook: targetPath,
      targetDate,
      qboCollection: path.join(folder, col[0]),
      qboSchedule:   path.join(folder, sch[0]),
      tabBank:       path.join(folder, tab[0]),
      tmsReconcile:  path.join(folder, tms[0]),
    });
  }

  builds.sort((a, b) => a.targetDate - b.targetDate);
  return builds;
}

function findInputWorkbook(targetDate, allBuilds) {
  const cands = [];
  for (const b of allBuilds) {
    if (b.targetDate < targetDate) cands.push([b.targetDate, b.targetWorkbook]);
  }
  // Also look at loose AR_AGING files at the assets root
  for (const f of fs.readdirSync(ASSETS)) {
    const p = path.join(ASSETS, f);
    if (fs.statSync(p).isFile()) {
      const d = parseTargetDate(f);
      if (d && d < targetDate) cands.push([d, p]);
    }
  }
  if (cands.length === 0) return null;
  cands.sort((a, b) => a[0] - b[0]);
  return cands[cands.length - 1][1];
}

function buildDay(d) {
  const t = new Date(d.getTime());
  const dow = t.getDay();
  const add = dow === 5 ? 3 : 1;
  t.setDate(t.getDate() + add);
  return t;
}

// ---------------------------------------------------------------------------
// Run
// ---------------------------------------------------------------------------

function relpath(p) {
  return path.relative(ROOT, p).replaceAll('\\', '/');
}

function fmtDate(d) {
  return d.toISOString().slice(0, 10);
}

const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

function reportSummary(spec, comp, produced, ageDelta) {
  console.log('');
  console.log('='.repeat(78));
  console.log(`  Build ${fmtDate(spec.targetDate)} (${DOW[spec.targetDate.getDay()]}) -> target ${path.basename(spec.targetWorkbook)}`);
  console.log(`  Input chained from yesterday's workbook | aging delta = ${ageDelta} day(s)`);
  console.log('='.repeat(78));
  console.log(`  Match: ${comp.match_pct.toFixed(2)}%  (${comp.matched_perfect} / ${comp.target_count} perfect)`);
  console.log(`  Produced rows: ${comp.produced_count}  |  Target rows: ${comp.target_count}`);
  const byField = {};
  for (const [f, arr] of Object.entries(comp.field_diffs)) byField[f] = arr.length;
  console.log(`  Diffs:`);
  console.log(`    Field-level diffs: ${comp.matched_with_diffs} rows  |  by field: ${JSON.stringify(byField)}`);
  console.log(`    Only in produced (we have, target lacks): ${comp.only_produced.length}`);
  console.log(`    Only in target (target has, we don't):    ${comp.only_target.length}`);
  for (const f of ['amount', 'balance']) {
    const arr = comp.field_diffs[f];
    if (arr && arr.length) {
      console.log(`    ${f} sample diffs:`);
      for (const d of arr.slice(0, 3)) {
        console.log(`      ${d.inv}: produced=${d.produced}  target=${d.target}`);
      }
    }
  }
  if (comp.only_target.length) {
    console.log(`    Sample missing (only in target):`);
    for (const inv of comp.only_target.slice(0, 5)) console.log(`      ${inv}`);
  }
  console.log(`  TMS sheet rows: ${produced.tms_rows.length}  |  ADJUSTMENT rows: ${produced.adjustment_rows.length}`);
}

const builds = discoverBuilds();
console.log(`Discovered ${builds.length} builds:`);
for (const b of builds) {
  console.log(`  ${fmtDate(b.targetDate)}  <- ${path.basename(b.targetWorkbook)}  (${relpath(b.folder)})`);
}
console.log('');

const overall = [];
for (const spec of builds) {
  const inputWb = findInputWorkbook(spec.targetDate, builds);
  if (!inputWb) {
    console.log(`!! Skipping ${fmtDate(spec.targetDate)} — no input workbook found`);
    continue;
  }
  const inputDate = parseTargetDate(inputWb);
  let ageDelta = 1;
  if (inputDate) {
    const days = Math.round((buildDay(spec.targetDate) - buildDay(inputDate)) / 86400000);
    ageDelta = Math.max(1, days);
  }

  const yesterday = parseYesterdaysWorkbook(readBuf(inputWb));
  const qbo_collection = parseQboDailyCollection(readBuf(spec.qboCollection));
  const qbo_schedule = parseQboDailySchedule(readBuf(spec.qboSchedule));
  const tab_bank = parseTabBankRemittance(readBuf(spec.tabBank));
  const tms_reconcile = parseTmsReconcile(readBuf(spec.tmsReconcile));

  const produced = arBuildToday({
    yesterday, qbo_collection, qbo_schedule, tab_bank, tms_reconcile,
    target_date: spec.targetDate,
    age_delta: ageDelta,
  });
  const comp = arVerifyBuild(produced, readBuf(spec.targetWorkbook));
  reportSummary(spec, comp, produced, ageDelta);
  overall.push({ spec, comp, ageDelta });
}

console.log('');
console.log('='.repeat(78));
console.log(' SUMMARY '.padStart((78 + 9) / 2, '=').padEnd(78, '='));
console.log('='.repeat(78));
console.log(`${'Date'.padEnd(12)}  ${'Match %'.padStart(9)}  ${'Perfect'.padStart(8)}  ${'Diffs'.padStart(6)}  ${'OnlyProd'.padStart(9)}  ${'OnlyTgt'.padStart(8)}`);
for (const { spec, comp } of overall) {
  console.log(
    `${fmtDate(spec.targetDate).padEnd(12)}  ` +
    `${(comp.match_pct.toFixed(2) + '%').padStart(9)}  ` +
    `${String(comp.matched_perfect).padStart(8)}  ` +
    `${String(comp.matched_with_diffs).padStart(6)}  ` +
    `${String(comp.only_produced.length).padStart(9)}  ` +
    `${String(comp.only_target.length).padStart(8)}`
  );
}

// Python baselines for parity check (printed for comparison)
console.log('');
console.log('-'.repeat(78));
console.log('Python baseline targets (the JS port must match these numbers):');
const PY_BASELINE = {
  '2026-05-08': 93.76,
  '2026-05-11': 99.80,
  '2026-05-12': 99.81,
  '2026-05-13': 99.88,
  '2026-05-14': 99.95,
  '2026-05-19': 90.31,
};
let allMatch = true;
for (const { spec, comp } of overall) {
  const key = fmtDate(spec.targetDate);
  const py = PY_BASELINE[key];
  if (py == null) continue;
  const delta = comp.match_pct - py;
  const ok = Math.abs(delta) < 0.10;  // within 0.10 percentage points
  if (!ok) allMatch = false;
  console.log(
    `${key}  JS ${comp.match_pct.toFixed(2)}%  vs  Python ${py.toFixed(2)}%  ` +
    `(delta ${delta >= 0 ? '+' : ''}${delta.toFixed(2)})  ${ok ? 'OK' : 'GAP'}`
  );
}
console.log('');
console.log(allMatch ? 'GATE: PASS — JS port matches Python within 0.10pp on every cycle.'
                     : 'GATE: FAIL — at least one cycle differs by more than 0.10pp.');
process.exit(allMatch ? 0 : 1);
