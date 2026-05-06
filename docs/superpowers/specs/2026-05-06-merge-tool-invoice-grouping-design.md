# Merge Tool — Invoice Grouping Fix — Design

**Date:** 2026-05-06
**Status:** Design approved (brainstormed) → ready for implementation plan
**Mockup:** [`app/mockups/merge-v2-invoice-grouping-mockup.html`](../../../app/mockups/merge-v2-invoice-grouping-mockup.html)
**Triggered by:** 2026-05-05 batch — out of 110 invoice rows in `docs/no sav.xlsx`, the merge tool produced only 100 PDFs because 10 second-invoices on shared containers were silently dropped. See also: [`POD_Issues_05.05.2026.xlsx`](../../POD_Issues_05.05.2026.xlsx) for the related downstream POD-quality issues this batch surfaced.

## Goal

Stop treating **container** as the unit of work in the merge tool. Switch to **invoice** as the unit. Every Excel invoice row produces its own merged PDF, named after its invoice number. Container/work-order are used as fallback identifiers only when the invoice number is missing.

## Background

### The bug, in one sentence

[`merge.js:170`](../../../app/assets/js/tools/merge/merge.js#L170) deduplicates Excel rows by container number, silently dropping any row whose container has already been seen.

### The real-world billing pattern this breaks

NGL routinely splits one physical container move into two AR invoices to two different customers. Example from the 2026-05-05 batch:

| Container | WO # | INV # | Customer | Amount |
|---|---|---|---|---|
| CAAU7378645 | PM2604260005 | PM26050062F | MSC Mediterranean Shipping | $1,730 |
| CAAU7378645 | PM2604260005 | PM26050063F | CONAIR Corp – Drayage | $173 |

Same physical move, two real billings, two real invoices, two real customers. The merge tool today sees the second row as a "duplicate container" and drops it. Across the 05.05 batch, this happened 10 times — 9 CONAIR drayage companions and 1 True Value side-charge.

The user only noticed because they cross-referenced the Excel against the file count and asked "I'm missing 10, which ones?". With no failure-report entry, no log line, no warning — the data loss was invisible.

### v2's current handling

The Beta merge tool ([`merge-v2.js:201`](../../../app/assets/js/tools/merge/merge-v2.js#L201)) does keep all rows but classifies same-container-different-invoice rows as `dup-diff-inv`, defaults the checkbox to **off**, and warns "Same container as row X, but different invoice number." A user who clicks straight through the Review state without manually re-checking each `dup-diff-inv` row also drops the data — silently, again. Same root cause: the model treats container as the unit of work.

## Scope

Both tools are fixed:

- **v1 (`merge.js` — production today)** — defensive fix to stop the data loss now, in the next rebuild.
- **v2 (`merge-v2.js` — Beta toggle, M2 in flight)** — same logical change, applied to v2's row-validator and Review state.

Both tools share `app/assets/js/shared/utils.js` for the alias table, so the new `workOrderNumber` aliases land once and benefit both.

## Design

### 1. Excel parsing — keep every row

**v1:** Delete the `seen.has(cn.toLowerCase())` dedup check at [`merge.js:170-172`](../../../app/assets/js/tools/merge/merge.js#L170-L172). Push every row into `state.excelRows[]`. The "Excel loaded" status text changes from `"100 containers · 100 invoice numbers"` to `"110 invoices · 100 unique containers"` so duplicate-container rows are visible at a glance.

**v2:** Already keeps every row in [`parseExcelFile()`](../../../app/assets/js/tools/merge/merge-v2.js#L139). No change to parsing — only to validation (next section).

### 2. Duplicate detection — INV# only

The new rule:

> **A row is a true duplicate of a previous row if and only if its INV# matches that previous row's INV#.**

Container and WO# do not factor into duplicate detection. This correctly preserves the CONAIR-pattern (same container + same WO# + different INV# = two distinct billings, both kept).

| Scenario | Treatment |
|---|---|
| Two rows with the same INV# | Second row marked `dup-same-inv` (v2's existing status name), defaulted unchecked, will be skipped |
| Two rows with different INV# (anything else identical) | Both kept, both selected, both produce a merged PDF |
| Row with no INV# | Never considered a duplicate; flagged for review (see section 4) |

**v1:** New status added to the failure-report area: `"Row N skipped — duplicate of row M (same INV# {inv})"`. The merge proceeds with the kept rows.

**v2:** The existing `dup-same-inv` status keeps its current treatment (defaulted unchecked, "will be skipped" message). The existing `dup-diff-inv` status is **removed** — those rows now classify as plain `ok` and select by default. No more "Same container as row X, but different invoice number" warning.

### 3. Filename rule

Every merged output uses:

```
{date}_{key}_{container}_merged.pdf
```

Where `{key}` resolves through this fallback chain:

1. **INV#** if the row has one (almost all rows)
2. **WO#** if the row has no INV# but does have a WO#
3. **(omitted)** — when both INV# and WO# are missing, the `{key}_` segment is dropped entirely and the filename collapses to `{date}_{container}_merged.pdf`

In the common case (INV# present), the container appears as the trailing segment and serves as a visual scan tag. In the collapsed case (no INV#, no WO#), the container *is* the identifier — there is no second segment. Examples (using rows from `no sav.xlsx`):

| Row data | Output filename |
|---|---|
| INV `LM26050106F`, container `DRYU9802611` | `05.05_LM26050106F_DRYU9802611_merged.pdf` |
| INV `LM26050107F`, same container | `05.05_LM26050107F_DRYU9802611_merged.pdf` |
| No INV, WO `LM2604150031`, container `DRYU9802611` | `05.05_LM2604150031_DRYU9802611_merged.pdf` |
| No INV, no WO, container `DRYU9802611` | `05.05_DRYU9802611_merged.pdf` |

Both tools build their filenames with this same template. The shared `getDatePrefix()` helper continues to produce the `MM.DD` date prefix.

### 4. Missing INV# — soft flag, still merge

A row with no INV# value:

- Stays selected by default
- Receives a yellow `VERIFY` badge (v2) or a yellow warning banner on its container card (v1)
- Carries a clear message: `"No invoice number — please check before sending. Will merge with WO# as filename key."`
- Produces its merged PDF using the WO# (or container) fallback in the filename

Reasoning: a blank INV# usually means an upstream Excel-export glitch, not "this row shouldn't be billed." Hard-skipping forces a re-run of the whole merge to fix one row; soft-flagging lets the user finish in one pass and triage at the end. The yellow color is impossible to miss.

**v2:** Reuse the existing `miss-inv` status from [`merge-v2.js:211`](../../../app/assets/js/tools/merge/merge-v2.js#L211); only the `selected` default and the message text change.

**v1:** New section in the post-parse status report listing the rows that need verification, plus inline yellow tag on the container-group card.

### 5. WO# column — new fuzzy alias

Add to [`utils.js:48`](../../../app/assets/js/shared/utils.js#L48) `CSV_ALIASES`:

```js
workOrderNumber: ['workordernumber', 'workorder', 'workorderno', 'workorderid',
                  'wo', 'wono', 'wonumber', 'woid', 'wonum'],
```

This catches `WO #`, `WO#`, `WO`, `Workorder No`, `Work Order Number`, etc. — all variants normalize (lowercase, strip non-alphanumeric) to one of the nine aliases above.

The merge tool reads this column when present. When absent, it's silently treated as "no WO# anywhere" — INV# and container alone remain sufficient. CSV/XLSX files that don't have a WO# column continue to work unchanged.

INV# and container fuzzy matching already work via the existing `invoiceNumber` (12 aliases) and `containerNumber` (19 aliases) entries — no change there.

### 6. PDF-to-row matching — unchanged

The merge tool continues to match input PDFs to Excel rows by container substring (`pdf.name.toLowerCase().includes(row.containerNumber.toLowerCase())`). When two rows share the same container, both rows pull the same set of source PDFs and produce two merged PDFs with **identical content** — different filenames (different INV# in the name), same bytes inside. This matches reality: both invoices on a shared container are billing for the same physical move and need the same supporting documents.

### 7. UI rendering

**v1 — `renderContainerGroups()` ([merge.js:313](../../../app/assets/js/tools/merge/merge.js#L313)):**

The function currently builds one card per `state.excelRows[]` entry. With dedup removed, a container with two invoices renders as two stacked cards. Each card prominently displays its INV# (today shown only as a small "Invoice #:" subtitle). When the row is missing an INV#, the card gets a yellow `⚠ Verify` banner across the top.

**v2 — Review-state table ([merge-v2.js renderReview](../../../app/assets/js/tools/merge/merge-v2.js)):**

The existing table layout (Row · Container · Invoice # · Customer · Validation) stays. New optional **WO #** column appears between Invoice # and Customer when the parsed Excel had a WO# column. Validation column shows the badge per row's status:

- `ok` → green `OK` badge (no message)
- `miss-inv` → yellow `VERIFY` badge with the soft-flag message
- `dup-same-inv` → gray `EXACT DUP` badge, row defaulted unchecked

The mockup at [`merge-v2-invoice-grouping-mockup.html`](../../../app/mockups/merge-v2-invoice-grouping-mockup.html) shows the BEFORE/AFTER side by side using the actual `no sav.xlsx` rows.

## Non-Goals

- **Rebuilding the underlying merge engine** (pdf-lib calls, Web Workers, save options) — unchanged
- **Adding INV# matching to the PDF-to-row logic** — source PDFs in the user's workflow are container-named, not INV#-named; INV# matching would only help if upstream renaming happened, which is out of scope
- **Surfacing POD-content quality issues** (the 21-of-110 problem found this morning) — saved as the **POD Validator** future feature, see [`memory/project_pod_validator_feature.md`](../../../../C:/Users/Joseph/.claude/projects/c--Users-Joseph-Desktop-NGL-ACCOUNTING-SERVICE/memory/project_pod_validator_feature.md). Not part of this fix.
- **Splitting v2's M2 mid-flight** — v2 changes target the validator and Review state only; M2's success-card and the in-progress UX work remain on track
- **Changing the failure report format in v1** beyond adding the new "duplicate INV#" and "no INV#" lines

## Verification — what success looks like

1. Re-running the merge against `docs/no sav.xlsx` produces **110 PDFs**, not 100. The 10 previously-missing files appear with names like `05.05_PM26050063F_CAAU7378645_merged.pdf`.
2. Hand-crafted test Excel with two rows having the same INV# produces **one** PDF (the second is shown as `dup-same-inv` and skipped).
3. Hand-crafted test Excel with one row missing the INV# but having a WO# produces a PDF named with the WO# *and* shows the yellow `VERIFY` flag in the UI before merge.
4. Hand-crafted test Excel without any WO# column still works end-to-end (no error, no warning about the missing column).
5. v2 Review-state table no longer shows the "Same container as row X, but different invoice number" warning. Two such rows display as plain `OK` rows, both checked.

## Open questions

None — all design choices confirmed by user during brainstorming.

## Implementation surface (preview, not the plan)

These files will be touched. The implementation plan (next step) will break this into ordered tasks.

| File | Change |
|---|---|
| `app/assets/js/shared/utils.js` | Add `workOrderNumber` to `CSV_ALIASES`; export `WO_ALIASES` shortcut |
| `app/assets/js/tools/merge/merge.js` | Remove container dedup; parse WO# column; new filename template; new flag/skip status messages; container-groups UI tweaks |
| `app/assets/js/tools/merge/merge-v2.js` | Remove `dup-diff-inv` classification; update `miss-inv` defaults; new filename template; optional WO# column in Review table |
| `desktop/VERSION` | Bump (per [`feedback_version_bump.md`](../../../../C:/Users/Joseph/.claude/projects/c--Users-Joseph-Desktop-NGL-ACCOUNTING-SERVICE/memory/feedback_version_bump.md)) |
| Test/manual verification | Re-run against `docs/no sav.xlsx` and confirm 110-PDF output |
