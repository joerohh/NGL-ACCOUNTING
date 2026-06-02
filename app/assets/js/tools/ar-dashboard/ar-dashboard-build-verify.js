// AR Dashboard — build verification harness.
//
// Compares a produced build model (from ar-dashboard-build.js) against a
// hand-built reference workbook, cell-by-cell, on the fields the engine
// actually computes. Ports compare_ar() from tools/verify_ar_build.py.
//
// Pure ESM, no imports from arState. Reusable in browser console and in
// the Node verification harness at tools/verify_ar_build_js.mjs.

import { parseYesterdaysWorkbook } from './ar-dashboard-build-loader.js';

const AMT_EPS = 0.01;
const COMPARE_FIELDS = ['amount', 'paid', 'balance', 'aging', 'memo', 'ar_status'];

function valuesDiffer(a, b) {
  if (a == null && b == null) return false;
  if (typeof a === 'number' && typeof b === 'number') {
    return Math.abs(a - b) > AMT_EPS;
  }
  if (a == null || b == null) {
    // Treat blank strings as equivalent to null
    if (typeof a === 'string' && a.trim() === '') return false;
    if (typeof b === 'string' && b.trim() === '') return false;
    return true;
  }
  return a !== b;
}

// Compare produced rows against target rows (both as { inv: row } maps).
export function arCompareAr(produced, targetWorkbookBuf) {
  const target = parseYesterdaysWorkbook(targetWorkbookBuf);
  const targetAr = target.ar_register;
  const targetByInv = new Map();
  for (const r of targetAr) {
    if (r.inv) targetByInv.set(r.inv, r);
  }
  const producedByInv = new Map();
  for (const r of produced) {
    if (r.inv) producedByInv.set(r.inv, r);
  }
  const prodInvs = new Set(producedByInv.keys());
  const tgtInvs = new Set(targetByInv.keys());
  const onlyProduced = [...prodInvs].filter(i => !tgtInvs.has(i)).sort();
  const onlyTarget = [...tgtInvs].filter(i => !prodInvs.has(i)).sort();
  const common = [...prodInvs].filter(i => tgtInvs.has(i));

  const fieldDiffs = {};
  let matchedPerfect = 0;
  let matchedWithDiffs = 0;
  for (const inv of common) {
    const p = producedByInv.get(inv);
    const t = targetByInv.get(inv);
    const diffs = [];
    for (const f of COMPARE_FIELDS) {
      if (valuesDiffer(p[f], t[f])) {
        diffs.push([f, p[f], t[f]]);
      }
    }
    if (diffs.length === 0) {
      matchedPerfect++;
    } else {
      matchedWithDiffs++;
      for (const [f, pv, tv] of diffs) {
        if (!fieldDiffs[f]) fieldDiffs[f] = [];
        fieldDiffs[f].push({ inv, produced: pv, target: tv });
      }
    }
  }

  const matchPct = matchedPerfect / Math.max(1, targetAr.length) * 100;
  return {
    produced_count: produced.length,
    target_count: targetAr.length,
    only_produced: onlyProduced,
    only_target: onlyTarget,
    matched_perfect: matchedPerfect,
    matched_with_diffs: matchedWithDiffs,
    field_diffs: fieldDiffs,
    match_pct: matchPct,
  };
}

export function arVerifyBuild(builtModel, referenceWorkbookBuf) {
  return arCompareAr(builtModel.today_ar, referenceWorkbookBuf);
}

if (typeof window !== 'undefined') {
  window.arVerifyBuild = arVerifyBuild;
  window.arCompareAr = arCompareAr;
}
