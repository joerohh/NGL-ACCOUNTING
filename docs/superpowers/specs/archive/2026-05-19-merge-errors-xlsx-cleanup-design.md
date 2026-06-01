# Merge Errors XLSX Export — Cleanup Design

**Date:** 2026-05-19
**Status:** Approved
**Scope:** Frontend only — `app/assets/js/tools/merge/merge-v2.js`

## Problem

The "Download errors as Excel" button on the Merge tool's Review/Ready screens produces a 9-column report in which three of the columns carry little or no useful information for the user, and one of them is actively broken:

| Column | Real-world behavior |
|---|---|
| `What's missing` | Almost always reads `"Invoice + POD missing"` because most error batches share the same failure mode. Repetitive but informative. |
| `Where we looked` | Only populated on POD-only-miss rows. **And on those rows it currently renders `[object Object] → [object Object] → …`** because the code joins an array of step objects instead of their `type` field. |
| `Status detail` | Generic strings like `"Error"` or `"Needs PDF"`. Mostly duplicates `What's missing`. |

The reference batch `app/assets/images/merge-errors-2026-05-19.xlsx` (42 rows) demonstrates both issues: 41 of 42 rows have empty `Where we looked` and the value `"Error"` in `Status detail`; the one row that does populate `Where we looked` (row 150, TRINION AMERICA INC) shows the `[object Object]` bug.

## Goals

1. Replace the three weak columns with a single, plain-English **`Issue`** column that always conveys the failure clearly.
2. Fix the `[object Object]` bug at the same time so the chain information surfaces correctly when it matters.
3. Keep the existing filename pattern, sheet name, and trigger button untouched — purely a column/content change.

## Non-goals

- No changes to the on-screen Routing Trace, sidebar pills, or the live merge UI.
- No changes to backend payloads or `chain_attempted` shape — the fix is in how the frontend serializes them for the xlsx.
- No new failure categories or telemetry.

## Design

### Column structure (7 columns, was 9)

| Row # | Invoice Date | Customer | Container # | INV # | WO # | Issue |

The first six columns are identical to today. The seventh — `Issue` — replaces the trio `What's missing` / `Where we looked` / `Status detail`.

### `Issue` column logic

Computed per error row from the existing `fetchResult` shape:

- `fr.invPill === 'miss'` → invoice missing
- `fr.podPill === 'miss'` → POD missing
- `fr.chainAttempted` → array of `{type, outcome}` step objects (may be empty)
- `fr.statusText` → terse status text
- `fr.message` → optional human message on errors

Decision table:

| Failure state | `Issue` text |
|---|---|
| Invoice miss + POD miss | `Invoice and POD not found in QBO` |
| Invoice miss only | `Invoice not found in QBO` |
| POD miss, chain non-empty | `POD not found — tried POD → BL → POL` (chain types joined with `→`) |
| POD miss, chain empty | `POD not found` |
| Neither pill is `miss` but row is in error set (defensive fallback) | `Error: <fr.message>` if message present, otherwise `Error` |

### The `[object Object]` fix

`chainAttempted` items are objects of shape `{type: 'POD'|'BL'|'POL'|'IT'|'ITE', outcome: 'tms_hit'|'tms_miss'|'tms_error'}` (see `merge-v2.js:1679-1686` which already reads them correctly for the on-screen trace).

In the export, the chain string is built as:

```js
const chain = Array.isArray(fr.chainAttempted)
  ? fr.chainAttempted.map(s => s && s.type).filter(Boolean).join(' → ')
  : '';
```

Joining `step.type` instead of the whole object eliminates the `[object Object]` rendering. The `.filter(Boolean)` guards against malformed steps without `type`.

### What stays the same

- Filename: `merge-errors-YYYY-MM-DD.xlsx`
- Sheet name: `Errors`
- Trigger: existing "Download errors as Excel" button (`window.v2DownloadErrors`)
- Selection of error rows: same `getErrorRows()` filter (rows whose fetch produced a POD miss or invoice miss, not manually skipped)

## Implementation surface

Single function rewrite in `app/assets/js/tools/merge/merge-v2.js`:

- `buildErrorExportRows(errorRows)` at line 2628 — change the return shape from 9 keys to 7 keys, with the new `Issue` value computed inline.

No other file touched. `downloadErrorsXlsx()` and `getErrorRows()` keep their current behavior.

## Verification

After implementation, regenerate the export against the existing reference batch and confirm:

1. Output has 7 columns with the new `Issue` header.
2. All 41 invoice-and-POD-miss rows read `Invoice and POD not found in QBO`.
3. Row 150 reads `POD not found — tried POD → BL → POL → IT → ITE` (no `[object Object]`).
4. Filename, sheet name, and download flow are unchanged.

A preview xlsx demonstrating the target output already exists at
`app/assets/images/merge-errors-2026-05-19-PREVIEW.xlsx`.

## Rollout

Code change only — **do not rebuild or ship**. The user is bundling this with other pending changes for the next release. After the implementation lands, the change sits in the working tree (or as a commit) until the bundled release goes out.
