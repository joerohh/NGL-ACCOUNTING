# Merge Errors XLSX Export — Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the three weak columns in the merge tool's error export with a single plain-English `Issue` column, and fix the `[object Object]` chain rendering bug at the same time.

**Architecture:** Single-function rewrite in `app/assets/js/tools/merge/merge-v2.js`. Introduce a small pure helper `buildIssueText(fetchResult)` that produces the human-readable Issue string, then rewrite `buildErrorExportRows()` to emit 7 columns (was 9) using that helper. No other files touched. Verification is done by regenerating the export against an existing reference batch.

**Tech Stack:** Vanilla ES modules (no build step), SheetJS (`window.XLSX`) for the actual `.xlsx` write. Verification uses Python 3 + `openpyxl` (already available in this environment).

**Spec:** `docs/superpowers/specs/2026-05-19-merge-errors-xlsx-cleanup-design.md`

**Reference data:** `app/assets/images/merge-errors-2026-05-19.xlsx` (42-row real-world batch including the buggy row 150).

**Rollout note:** Code change only — **do not rebuild or ship**. The user is bundling this with other pending changes for the next release. The implementation ends at "commit" — no version bump, no PyInstaller, no electron-builder, no GitHub release.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `app/assets/js/tools/merge/merge-v2.js` | Merge tool state machine, render functions, error export | Modify: replace `buildErrorExportRows()` (line 2628) and add `buildIssueText()` helper just above it |

No new files. No file split. The existing file already houses all sibling export helpers (`getErrorRows`, `downloadErrorsXlsx`) so the new helper belongs alongside them.

---

## Task 1: Add the `buildIssueText` pure helper

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js` — insert new function immediately above `buildErrorExportRows` at line 2627

- [ ] **Step 1: Open the file and locate the insertion point**

Read `app/assets/js/tools/merge/merge-v2.js` around lines 2615–2655 to confirm:
- `getErrorRows()` exists at ~2619
- `buildErrorExportRows()` exists at ~2628
- The comment `// ── Error export helpers ──` is at ~2615

The new helper goes between `getErrorRows` and `buildErrorExportRows`.

- [ ] **Step 2: Insert the helper**

Use Edit to add this function. Place it immediately after the closing brace of `getErrorRows()` and before the `// Maps error rows to the 9-column structure for the Excel export.` comment:

```javascript
// Builds a single plain-English description of why a row failed, suitable
// for the "Issue" column of the error xlsx export. Reads the row's
// fetchResult; falls back to a generic Error if the shape is unexpected.
function buildIssueText(fr) {
  fr = fr || {};
  const invMiss = fr.invPill === 'miss';
  const podMiss = fr.podPill === 'miss';
  const chain = Array.isArray(fr.chainAttempted)
    ? fr.chainAttempted.map(s => s && s.type).filter(Boolean).join(' → ')
    : '';

  if (invMiss && podMiss) return 'Invoice and POD not found in QBO';
  if (invMiss)            return 'Invoice not found in QBO';
  if (podMiss)            return chain ? `POD not found — tried ${chain}` : 'POD not found';

  // Defensive fallback: row landed in the error set but neither pill is "miss".
  // Surface the message if we have one, otherwise a plain "Error".
  return fr.message ? `Error: ${fr.message}` : 'Error';
}
```

Also update the comment on `buildErrorExportRows` from `// Maps error rows to the 9-column structure for the Excel export.` to `// Maps error rows to the 7-column structure for the Excel export.` (one-word change — defer the actual function body rewrite to Task 2).

- [ ] **Step 3: Verify the helper in isolation with node**

The helper is pure and has no module imports, so we can copy-paste it into a node REPL and assert it directly. Run from the repo root:

```bash
node -e '
function buildIssueText(fr) {
  fr = fr || {};
  const invMiss = fr.invPill === "miss";
  const podMiss = fr.podPill === "miss";
  const chain = Array.isArray(fr.chainAttempted)
    ? fr.chainAttempted.map(s => s && s.type).filter(Boolean).join(" → ")
    : "";
  if (invMiss && podMiss) return "Invoice and POD not found in QBO";
  if (invMiss)            return "Invoice not found in QBO";
  if (podMiss)            return chain ? `POD not found — tried ${chain}` : "POD not found";
  return fr.message ? `Error: ${fr.message}` : "Error";
}
const cases = [
  [{ invPill: "miss", podPill: "miss" }, "Invoice and POD not found in QBO"],
  [{ invPill: "miss", podPill: "ok"   }, "Invoice not found in QBO"],
  [{ invPill: "ok",   podPill: "miss", chainAttempted: [{type:"POD"},{type:"BL"},{type:"POL"}] }, "POD not found — tried POD → BL → POL"],
  [{ invPill: "ok",   podPill: "miss", chainAttempted: [] }, "POD not found"],
  [{ invPill: "ok",   podPill: "miss" }, "POD not found"],
  [{ invPill: "ok",   podPill: "ok", message: "boom" }, "Error: boom"],
  [{ invPill: "ok",   podPill: "ok" }, "Error"],
  [null, "Error"],
  // The historical bug: chainAttempted is an array of objects, not strings.
  // Helper must extract .type, not stringify the whole object.
  [{ invPill: "ok", podPill: "miss",
     chainAttempted: [{type:"POD",outcome:"tms_miss"},{type:"BL",outcome:"tms_miss"},{type:"POL",outcome:"tms_miss"},{type:"IT",outcome:"tms_miss"},{type:"ITE",outcome:"tms_miss"}] },
   "POD not found — tried POD → BL → POL → IT → ITE"],
  // Malformed step (no .type) is filtered out, not rendered as undefined.
  [{ invPill: "ok", podPill: "miss", chainAttempted: [{type:"POD"},{outcome:"tms_miss"},{type:"BL"}] },
   "POD not found — tried POD → BL"],
];
let failed = 0;
for (const [input, expected] of cases) {
  const got = buildIssueText(input);
  if (got !== expected) {
    console.error("FAIL:", JSON.stringify(input), "\n  expected:", expected, "\n       got:", got);
    failed++;
  }
}
if (failed) { console.error(failed, "failure(s)"); process.exit(1); }
else console.log("All", cases.length, "cases passed.");
'
```

Expected output: `All 10 cases passed.`

If any case fails, fix the helper in `merge-v2.js` until all 10 pass.

- [ ] **Step 4: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge): add buildIssueText helper for error export"
```

---

## Task 2: Rewrite `buildErrorExportRows` to emit 7 columns

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js:2628-2651` (the existing `buildErrorExportRows` function body)

- [ ] **Step 1: Replace the function body**

Use Edit. The current function is:

```javascript
function buildErrorExportRows(errorRows) {
  return errorRows.map(r => {
    const fr = r.fetchResult || {};
    const invMiss = fr.invPill === 'miss';
    const podMiss = fr.podPill === 'miss';
    const whatsMissing =
      invMiss && podMiss ? 'Invoice + POD missing'
      : invMiss          ? 'Invoice missing'
      : podMiss          ? 'POD missing'
                         : '';
    const chain = Array.isArray(fr.chainAttempted) ? fr.chainAttempted.join(' → ') : '';
    return {
      'Row #':            r.rowNum,
      'Invoice Date':     r.invoiceDate || '',
      'Customer':         r.customer || '',
      'Container #':      r.containerNumber || '',
      'INV #':            r.invoiceNumber || '',
      'WO #':             r.workOrderNumber || '',
      "What's missing":   whatsMissing,
      'Where we looked':  chain,
      'Status detail':    fr.statusText || '',
    };
  });
}
```

Replace its body so it returns 7 keys (in the order below) and delegates to `buildIssueText`:

```javascript
function buildErrorExportRows(errorRows) {
  return errorRows.map(r => ({
    'Row #':        r.rowNum,
    'Invoice Date': r.invoiceDate || '',
    'Customer':     r.customer || '',
    'Container #':  r.containerNumber || '',
    'INV #':        r.invoiceNumber || '',
    'WO #':         r.workOrderNumber || '',
    'Issue':        buildIssueText(r.fetchResult),
  }));
}
```

Object insertion order in modern JS is preserved, and SheetJS's `XLSX.utils.json_to_sheet` honors that order for column placement — so listing the keys in the right order is sufficient. No additional config needed.

- [ ] **Step 2: Sanity-check the file still parses**

Run the repo's pre-build JS gate (recorded in user memory under `desktop/check-js.js`):

```bash
node desktop/check-js.js
```

Expected: no syntax errors reported. If it complains about a different unrelated file, that's pre-existing and out of scope; if it complains about `merge-v2.js`, fix the syntax before continuing.

- [ ] **Step 3: Verify the export end-to-end against the reference batch**

We can't run the real merge flow without QBO/TMS, but we can simulate the export logic against synthetic fetchResult rows that match the reference batch (`app/assets/images/merge-errors-2026-05-19.xlsx`). Run from the repo root:

```bash
node -e '
// Inline the two helpers so we can exercise them without loading the full module.
function buildIssueText(fr) {
  fr = fr || {};
  const invMiss = fr.invPill === "miss";
  const podMiss = fr.podPill === "miss";
  const chain = Array.isArray(fr.chainAttempted)
    ? fr.chainAttempted.map(s => s && s.type).filter(Boolean).join(" → ")
    : "";
  if (invMiss && podMiss) return "Invoice and POD not found in QBO";
  if (invMiss)            return "Invoice not found in QBO";
  if (podMiss)            return chain ? `POD not found — tried ${chain}` : "POD not found";
  return fr.message ? `Error: ${fr.message}` : "Error";
}
function buildErrorExportRows(errorRows) {
  return errorRows.map(r => ({
    "Row #":        r.rowNum,
    "Invoice Date": r.invoiceDate || "",
    "Customer":     r.customer || "",
    "Container #":  r.containerNumber || "",
    "INV #":        r.invoiceNumber || "",
    "WO #":         r.workOrderNumber || "",
    "Issue":        buildIssueText(r.fetchResult),
  }));
}
// Three rows mirroring the real reference batch shapes:
// 1) Invoice + POD missing (matches 41 of the 42 reference rows)
// 2) POD-only miss with chain (matches row 150 — the [object Object] bug)
// 3) Defensive fallback
const rows = [
  { rowNum: 3, invoiceDate: "05/04/2026", customer: "3PLUS LOGISTICS - GA",
    containerNumber: "ONEU1850052", invoiceNumber: "SM26050013F", workOrderNumber: "SM2604200019",
    fetchResult: { invPill: "miss", podPill: "miss", statusText: "Error" } },
  { rowNum: 150, invoiceDate: "05/04/2026", customer: "TRINION AMERICA INC",
    containerNumber: "CA05042026", invoiceNumber: "CA05042026", workOrderNumber: "CA05042026",
    fetchResult: { invPill: "ok", podPill: "miss",
      chainAttempted: [{type:"POD",outcome:"tms_miss"},{type:"BL",outcome:"tms_miss"},{type:"POL",outcome:"tms_miss"},{type:"IT",outcome:"tms_miss"},{type:"ITE",outcome:"tms_miss"}],
      statusText: "Needs PDF" } },
  { rowNum: 200, invoiceDate: "05/04/2026", customer: "TEST CO",
    containerNumber: "XYZ", invoiceNumber: "T1", workOrderNumber: "W1",
    fetchResult: { invPill: "ok", podPill: "ok", message: "Some unexpected case", statusText: "Error" } },
];
const out = buildErrorExportRows(rows);
console.log("Column count:", Object.keys(out[0]).length);
console.log("Column order:", Object.keys(out[0]).join(" | "));
for (const r of out) {
  console.log(r["Row #"], "|", r["Customer"], "|", r["Issue"]);
}
const expectedHeaders = ["Row #","Invoice Date","Customer","Container #","INV #","WO #","Issue"];
const gotHeaders = Object.keys(out[0]);
if (JSON.stringify(gotHeaders) !== JSON.stringify(expectedHeaders)) {
  console.error("FAIL: headers mismatch"); process.exit(1);
}
const expectedIssues = [
  "Invoice and POD not found in QBO",
  "POD not found — tried POD → BL → POL → IT → ITE",
  "Error: Some unexpected case",
];
const gotIssues = out.map(r => r["Issue"]);
if (JSON.stringify(gotIssues) !== JSON.stringify(expectedIssues)) {
  console.error("FAIL: issues mismatch\n expected:", expectedIssues, "\n      got:", gotIssues);
  process.exit(1);
}
console.log("OK");
'
```

Expected output ends with `OK`. The headers and three Issue strings must match exactly:
- `Invoice and POD not found in QBO`
- `POD not found — tried POD → BL → POL → IT → ITE`
- `Error: Some unexpected case`

If anything fails, fix `buildErrorExportRows` or `buildIssueText` in the file until this passes.

- [ ] **Step 4: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge): collapse error xlsx to 7-column Issue format"
```

---

## Task 3: End-to-end manual verification

This step requires a human / agent with file-system access; it is not strictly required for the code change to be correct, but it confirms the user-facing output looks right before bundling for release.

**Files:**
- Read-only: `app/assets/images/merge-errors-2026-05-19.xlsx` (reference batch)
- Compare against: `app/assets/images/merge-errors-2026-05-19-PREVIEW.xlsx` (already produced during brainstorming — represents the target output)

- [ ] **Step 1: Confirm the preview xlsx exists and reflects the spec**

Run:

```bash
python -c "
import sys, io, openpyxl
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
wb = openpyxl.load_workbook(r'app/assets/images/merge-errors-2026-05-19-PREVIEW.xlsx')
ws = wb['Errors']
rows = list(ws.iter_rows(values_only=True))
print('Header:', rows[0])
print('Row count (data only):', len(rows) - 1)
# Find row 150
for r in rows[1:]:
    if r[0] == 150:
        print('Row 150 Issue:', r[6])
        break
"
```

Expected output:
```
Header: ('Row #', 'Invoice Date', 'Customer', 'Container #', 'INV #', 'WO #', 'Issue')
Row count (data only): 42
Row 150 Issue: POD not found — tried POD → BL → POL → IT → ITE
```

If the preview xlsx is missing or doesn't match, regenerate it (the brainstorming session's regen script is in the conversation transcript; it reads the original `merge-errors-2026-05-19.xlsx` and writes a 7-column version). No code commit needed for this preview file — it lives under `assets/images/` purely as documentation.

- [ ] **Step 2: Note that real-tool verification is deferred**

A full end-to-end check (running the merge tool against a real Excel manifest, hitting the QBO+TMS errors, clicking "Download errors as Excel") cannot be done here without live QBO/TMS sessions. The owner of the bundled release will verify visually when they run the next batch.

Document this in the commit log if it isn't already (the Task 2 commit message is sufficient).

- [ ] **Step 3: No commit needed for this task**

This task is verification-only; no files changed. Move on.

---

## Self-Review Notes

- **Spec coverage:**
  - 7-column structure → Task 2 Step 1
  - Issue text decision table → Task 1 (helper) + Task 1 Step 3 verification
  - `[object Object]` fix → Task 1 helper uses `.map(s => s.type)`, verified in Task 1 Step 3 and Task 2 Step 3
  - Filename / sheet name / button unchanged → not touched in any task (covered implicitly)
  - Verification against reference batch → Task 3
  - "No rebuild/ship" rollout → header rollout note + plan terminates after Task 3 with no build/release tasks
- **Placeholder scan:** none.
- **Type consistency:** helper is named `buildIssueText` in both Task 1 (definition) and Task 2 (call site).
- **No new test framework introduced** — the project has no frontend JS test infra, and the spec's verification is well-defined in terms of the reference batch + an inline `node -e` assertion script that needs no dependencies.
