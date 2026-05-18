# Merge Tool — Download Errors as Excel

**Date:** 2026-05-14
**Status:** Design approved, ready for plan
**Target version:** v2.69.1 (bundled with the pending customer-list email-pill fix in commit `d3efb21`)

## Motivation

A co-worker who follows up on missing PODs wants a way to take the list of error rows out of the app and into Excel so he can work them offline (email the carrier, chase the warehouse, etc.). Today the only place errors are visible is the Ready screen's "Errors" tab in the running app. There's no export.

He saw a sample Excel I produced earlier and approved it, asking for one addition: **the invoice date column** (so he can sort/age the errors).

## Scope

A single "Download errors" button that exports the current error set to an `.xlsx` file. Compact column set, client-side only, no agent changes.

**Non-goals:**
- Re-engineering the error pane itself
- Storing/persisting the error report on disk
- Auto-emailing the report
- Including skipped rows or successfully-fetched rows

## Where the button lives

The button appears in **two places, with identical behavior** (same handler, same export):

1. **Ready screen, "Errors" tab toolbar.** Sits to the left of the existing "↻ Retry all errors" button. Visible only when the Errors tab is active AND there's at least one error row.
2. **Merge screen header** (the bar with "Choose a merge format" and the Output-location button). Sits to the right of the Output-location button. Visible only when (a) there is at least one error row AND (b) at least one merge mode has been completed (`Object.keys(v2State.completedModes).length > 0`). Picked this spot because the Merge screen has no single summary card — it has per-mode completion cards that come and go as the user runs different modes, so a header-level button is the only stable home.

**Button label:** `📥 Download errors (N)` where N is the error count.

## What counts as an "error" row

A row is considered an error if **either** of its fetch pills failed AND the user did not manually skip it:

```js
const isError = (r) =>
  !r.skipped &&
  r.fetchResult &&
  (r.fetchResult.podPill === 'miss' || r.fetchResult.invPill === 'miss');
```

This is slightly broader than today's Errors tab (which only checks `podPill === 'miss'`). Adding `invPill === 'miss'` catches the rare case where the QBO invoice fetch itself failed.

Skipped rows are deliberately excluded — the user already made a choice on those.

## Excel format

| # | Column         | Source                                                                 |
|---|----------------|------------------------------------------------------------------------|
| 1 | Row #          | `row.rowNum` (the source-sheet row number, 2-indexed)                  |
| 2 | Invoice Date   | `row.invoiceDate` — **new field**, captured from the DATE column        |
| 3 | Customer       | `row.customer`                                                         |
| 4 | Container #    | `row.containerNumber`                                                  |
| 5 | INV #          | `row.invoiceNumber`                                                    |
| 6 | WO #           | `row.workOrderNumber`                                                  |
| 7 | What's missing | derived — see below                                                    |
| 8 | Where we looked| `(fetchResult.chainAttempted \|\| []).join(' → ')`                     |
| 9 | Status detail  | `fetchResult.statusText` (or `''`)                                     |

**"What's missing" derivation:**

```js
const invMiss = fr.invPill === 'miss';
const podMiss = fr.podPill === 'miss';
const whatsMissing =
  invMiss && podMiss ? 'Invoice + POD missing'
  : invMiss           ? 'Invoice missing'
  : podMiss           ? 'POD missing'
                      : '';   // unreachable for error rows
```

**Engine:** SheetJS (`XLSX.utils.json_to_sheet` + `XLSX.writeFile`) — already loaded via CDN, used elsewhere in the app.

**Filename:** `merge-errors-YYYY-MM-DD.xlsx` (browser handles same-day collisions with `(1)`, `(2)`, etc.).

## Data sourcing — Invoice Date capture

The merge tool's Excel parser (`parseExcelFile()` in `app/assets/js/tools/merge/merge-v2.js`) currently extracts container/INV/WO/customer keys but **not** date. We add one more `findColumnKey()` lookup using the existing `CSV_ALIASES.invoiceDate` alias list (`['invoicedate', 'date', 'invdate', 'docdate', 'createdate', 'txndate', 'transactiondate']`), and store the value on each row as `row.invoiceDate`.

**Date value handling:**
- If the cell is a JS `Date` object (xlsx auto-parses some date cells), format it as `MM/DD/YYYY`.
- If the cell is a string, pass it through unchanged.
- If empty/missing, store `''` and emit `''` in the export.

No new alias is needed — `'date'` is already in `CSV_ALIASES.invoiceDate`.

## Implementation outline

All changes are in `app/assets/js/tools/merge/merge-v2.js` and `app/index.html` (button HTML only).

1. **Parser change** (`parseExcelFile()`) — detect DATE column, store `row.invoiceDate` on each row object.
2. **Row constructor change** (around line 250) — add `invoiceDate` field to the row shape.
3. **New module-level function** — `getErrorRows()` — returns the filtered error set. Used by both the export and any future error-count UI.
4. **New module-level function** — `buildErrorExportRows(errorRows)` — maps row objects to the 9-column object array described above.
5. **New module-level function** — `downloadErrorsXlsx()` — calls SheetJS, triggers browser download.
6. **New `window` handler** — `window.v2DownloadErrors = downloadErrorsXlsx`.
7. **UI hookup** — two button insertions:
   - Errors-tab toolbar in `renderReady()` (around line 1061, next to the mass-retry button).
   - Merge screen header in `renderMerge()` (around line 1407), inside the `.merge-screen-header` div, after the Output-location button.

## What we are NOT changing

- The agent server. No new endpoints.
- The Errors-tab filter logic. The tab still shows the same rows as today.
- The fetch flow. No new data is collected from QBO or TMS.
- Skipped-row behavior.
- Any other tool (Invoice Sender, Customer Manager).

## Risks

- **Date format inconsistency.** The source Excel may store DATE as a string (`"05/05/2026"`), a JS `Date` object (after xlsx parsing), or an Excel serial number. The format-as-`MM/DD/YYYY` rule above covers the first two; we'll add a small `formatInvoiceDate()` helper that also handles Excel serial numbers (e.g., `45822` → `2025-05-05`) via SheetJS's date code path if needed.
- **Existing rows in state without `invoiceDate`.** Not a concern — the parser is the only place rows are created, and we update the parser. Old in-memory rows from a previous session don't persist.

## Testing checklist

- Drop the sample `docs/NGL INVOICE 05.05.2026 (1).xlsx` manifest. Verify the parser picks up the DATE column.
- Fetch all rows. Force an error (e.g., a container that won't match in TMS). Confirm the Errors tab shows the row.
- Click "Download errors". Open the resulting `.xlsx`. Verify all 9 columns are present and populated for the error row.
- Run a merge with the remaining good rows. On the post-merge screen, verify the button is visible and exports the same data.
- Try a manifest without a DATE column. Verify the export still works; the Invoice Date column is just blank.
- Try a manifest where DATE is stored as a string vs a real Excel date. Verify both render as `MM/DD/YYYY`.

## Ship plan

Bundle into the pending v2.69.1 release:
1. Fix `desktop/package.json` from `"2.69.1.0"` → `"2.69.1"`.
2. Commit this feature on top of `d3efb21`.
3. Build via `runbuild.bat` (PowerShell + empty-stdin pattern).
4. Push, then `gh release create v2.69.1` with the installer and `latest.yml`.
