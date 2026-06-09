# AR Dashboard — TAB BANK Exceptions MVP (Posting Gaps + Customer Mismatches)

**Date:** 2026-06-09
**Status:** approved
**Owner:** Joseph Roh
**Related:**
- `docs/superpowers/specs/2026-05-20-ar-dashboard-design.md` — original R1/R2 spec (TAB BANK use cases §5.2, §5.4 cat #4/#6/#8)
- `docs/superpowers/plans/2026-06-01-ar-dashboard-r2-build-engine.md` — R2 plan
- `memory/project_ar_dashboard_wip.md` — current status (R2 M2 shipped owner-gated v2.78.6)

---

## 1. Context

The AR build engine currently loads the TAB BANK Remittance file (`Collection_Payment.xlsx`) but does nothing with it — `tab_bank` is parsed and passed through to the writer for sheet-copy, but no detection or arithmetic runs against it. The build engine is 99.71% correct on real-world inputs (verified 2026-06-03 against Jihyun's 06/02/2026 hand-built workbook), but the gap left is exactly the class of errors TAB BANK was meant to catch: bank-side deposits that don't match the books.

This spec is the **first slice of TAB BANK detection** — the two cheapest, most-obvious mismatch categories. It deliberately excludes the deeper categories (cat #1 bank suspense needing customer DB, cat #9 posting errors needing amount/balance comparison, overpayment workflow) — each of those gets its own design.

**Locked rule from Jihyun (already in spec §4.3):** TAB BANK's `Pmt Type` column AND its `Short Pay` / `Over Pay` *amount* columns are ALWAYS ignored — TAB BANK only knows pre-revision invoice amounts. Source of truth is AR balance + the actual deposit amount in TAB BANK. This MVP respects that — we use only `check`, `debtor_name`, `desc`, and (display-only) `amount` from TAB BANK.

## 2. Goal

Detect two TAB BANK-vs-QBO mismatch categories during the daily build and surface them to Jihyun in two places: the build preview modal (pre-save) and an EXCEPTIONS sheet in the saved workbook (post-save / audit).

## 3. Scope

### In scope

- **Posting gaps (cat #4):** check# present in one source, missing in the other
  - `posting_gap_qbo_missing` — bank deposit with no QBO payment posted (highest priority — money in bank, not in books)
  - `posting_gap_tab_missing` — QBO payment with no TAB BANK record
- **Customer name mismatches (cat #6):** same check#, different normalized customer names
- **NON-FACTORED filter (cat #8 lite):** TAB BANK rows where `desc === 'NON-FACTORED'` are excluded from gap detection (informational only — they don't go through the standard posting flow and would false-positive as QBO-missing)
- **Preview modal KPI tile** — populate the existing "Suspense / exceptions" tile with real exception count; click expands to a detail table
- **EXCEPTIONS sheet** — new sheet in `AR_AGING_*.xlsx`, positioned between ADJUSTMENT and AR_yesterday

### Out of scope

- **Cat #1 bank suspense** (TAB BANK UC rows for debtors not in customer DB) — needs customer DB integration, separate design
- **Cat #9 TAB BANK posting errors** (wrong-check# reassignments, multi-row duplications, deposit amount ≠ AR balance) — needs deposit-vs-balance amount comparison, separate design
- **Overpayment Workflow** — needs guided modal, separate design
- **Email TAB BANK action** — depends on cat #9 detection landing first
- **Editing / resolving exceptions inline** — R2 M4 (edit-in-place) territory

## 4. Design

### 4.1 Engine

New function `detectTabBankExceptions(tab_bank, qbo_collection)` lives in `app/assets/js/tools/ar-dashboard/ar-dashboard-build.js`. Called from `arBuildToday()` after Phase 5 (TMS reconcile). Result attaches to the build return as `tab_bank_exceptions`.

**Signature:**

```js
detectTabBankExceptions(tab_bank, qbo_collection) → Array<{
  kind: 'posting_gap_qbo_missing' | 'posting_gap_tab_missing' | 'customer_mismatch',
  check_no: string,
  message: string,
  amount: number | null,
  tab_bank_row: object | null,
  qbo_row: object | null,
  tab_bank_customer: string | null,
  qbo_customer: string | null,
}>
```

### 4.2 Detection logic

**Pre-filtering:**
1. Skip TAB BANK rows where `desc === 'NON-FACTORED'` (informational)
2. Skip TAB BANK rows with no `check` (can't compare without a key)
3. Skip QBO Collection rows where `txn_type !== 'Invoice'` (header / sub-total rows)

**Build two indexes keyed by `check_no`:**
- `tbIndex: Map<check_no, tab_bank_row>` (one row per check# — TAB BANK is one row per deposit)
- `qboIndex: Map<check_no, qbo_invoice_lines[]>` (multiple Invoice lines possible when one check pays several invoices)

**Posting gap detection (set difference):**
- For each `check_no` in `tbIndex` but not in `qboIndex` → emit `posting_gap_qbo_missing` with message `"Bank received $X.XX on check# Y from CUSTOMER but no QBO payment recorded"`
- For each `check_no` in `qboIndex` but not in `tbIndex` → emit `posting_gap_tab_missing` with message `"QBO has a $X.XX payment on check# Y but TAB BANK has no record"`

**Customer name mismatch (set intersection):**
- For each `check_no` in both: normalize both customer names per §4.3 and compare
- On mismatch → emit `customer_mismatch` with message `"Check# Y — TAB BANK says CUSTOMER_A, QBO says CUSTOMER_B"`

QBO customer source: the `name` field on the QBO Invoice line (first occurrence). TAB BANK customer source: `debtor_name`.

### 4.3 Customer name normalization

Conservative normalizer — prefer false negatives over false positives so we don't spam Jihyun with format-only noise:

1. Uppercase the entire string
2. Strip leading `\d+-` prefix (QBO customer field has `id-name` format)
3. Strip trailing entity suffixes: `INC`, `LLC`, `LTD`, `CO`, `CORP`, `CORPORATION` and `.`-period variants. Repeat once (so `ACME LOGISTICS, INC.` reduces correctly)
4. Strip non-alphanumeric characters except spaces
5. Collapse multiple spaces to single, trim
6. If both sides reduce to the same string → match (no exception)
7. Otherwise → flag as mismatch

We will tune the normalizer based on Jihyun's first week of real exceptions:
- Too many false positives → tighten (e.g. add fuzzy match for transposed words)
- Misses real mismatches → loosen (e.g. remove suffix-strip)

### 4.4 Preview modal surface

The existing "Suspense / exceptions" KPI tile in `ar-dashboard-build-ui.js → previewModalHtml()` already shows `exceptions.length`. Two wire-up changes:

- Replace the placeholder `const exceptions = []` with `const exceptions = r.tab_bank_exceptions || []`
- Replace the empty-state branch of `renderKpiDetail('exception', ...)` with a real table:

| Issue | Check # | Amount | TAB BANK Customer | QBO Customer |
|---|---|---|---|---|
| Bank deposit, no QBO post | 12345 | $1,200.00 | ACME LOGISTICS | — |
| QBO post, no bank deposit | 67890 | $850.00 | — | GLOBAL FREIGHT |
| Customer name mismatch | 11111 | $450.00 | ACME LOGISTICS | XYZ TRANSPORT |

**Sort order (most urgent first):**
1. `posting_gap_qbo_missing` (money in bank, not in books)
2. `posting_gap_tab_missing` (QBO posted, no bank record)
3. `customer_mismatch`

KPI tile retains its existing `alert` class so it pulls visual attention when count > 0.

### 4.5 EXCEPTIONS workbook sheet

New sheet name: `EXCEPTIONS`. Position in `arBuildWriteWorkbook` output: between `ADJUSTMENT` and `AR_yesterday`.

**Columns:**

| Col | Header | Source |
|---|---|---|
| A | Kind | `kind` displayed as plain English ("Bank deposit, no QBO post", "QBO post, no bank record", "Customer name mismatch") |
| B | Check # | `check_no` |
| C | Amount | `amount` formatted as currency |
| D | TAB BANK Customer | `tab_bank_customer` (blank if N/A) |
| E | QBO Customer | `qbo_customer` (blank if N/A) |
| F | Detected Issue | `message` (full English explanation) |
| G | Notes | empty — for Jihyun to write resolution notes |

**Formatting (matches TMS / ADJUSTMENT sheet patterns):**
- Calibri 10pt body
- Bordered + centered header row
- Frozen row 1
- Autofilter on the header row
- Column widths sized to content (will tune in writer)

**Row sort:** same as preview detail — qbo_missing → tab_missing → customer_mismatch.

## 5. Implementation outline

Files to modify (no new files):

- `app/assets/js/tools/ar-dashboard/ar-dashboard-build.js` — add `detectTabBankExceptions(...)`, call from `arBuildToday()` after Phase 5, attach to return
- `app/assets/js/tools/ar-dashboard/ar-dashboard-build-ui.js` — wire `r.tab_bank_exceptions` into the KPI tile + replace the empty branch of `renderKpiDetail('exception', ...)` with the real table
- `app/assets/js/tools/ar-dashboard/ar-dashboard-build-writer.js` — add `writeExceptionsSheet(workbook, exceptions)` helper, insert into sheet pipeline before AR_yesterday
- `desktop/VERSION` — bump to 2.79.0 (minor — new detection capability)

No new files. No engine refactor. No writer refactor — just an additional sheet writer that follows existing patterns.

## 6. Verification approach

When this ships, run the build engine against Jihyun's last 5 hand-built workbooks + their TAB BANK / QBO inputs (build-2026-05-11 through build-2026-05-15 in test data). For each:

1. Capture emitted exceptions
2. Compare against any handwritten flags or memos in Jihyun's hand-built workbook
3. Categorize each emitted exception as: true positive / false positive / matches Jihyun's handwritten note

**Acceptance:**
- No silent skips (every TAB BANK row not filtered as NON-FACTORED is either matched or surfaced as an exception)
- False-positive rate < 10% across the 5 test days
- Real-world correctness on M2's 99.71% baseline is unchanged (no engine regression in the existing 6 phases)

## 7. Open questions

None. Approved 2026-06-09.

## 8. Risks

- **Customer-name normalizer aggression** — over-stripping suffixes could mask real mismatches (e.g. "ACME LOGISTICS INC" vs "ACME LOGISTICS LLC" — different legal entities, same trade name). Mitigation: spec §4.3 explicitly favors false negatives and commits to tuning after Jihyun's first week.
- **QBO customer field format drift** — if QBO changes the `id-name` format, the prefix-strip step breaks silently. Mitigation: regex is `^\d+-` so any non-numeric prefix is left alone; an unparseable name will simply not normalize and likely mismatch, surfacing as a false-positive that Jihyun can flag.
- **TAB BANK file with all NON-FACTORED rows** — would yield zero TAB BANK index entries and falsely flag every QBO payment as `posting_gap_tab_missing`. Mitigation: if all TAB BANK rows are filtered out, emit a single info exception ("TAB BANK file appears to contain only NON-FACTORED rows — gap detection skipped") and skip gap detection.
- **Performance** — detection is O(n) over TAB BANK + O(m) over QBO Collection. n + m typically < 1000 rows. No risk.

## 9. Ship discipline

This slice ships to the **owner-only path** (`joe.r@ngltrans.net`) for first-week verification, same gating model as v2.78.6. No GH release until Joe + Jihyun confirm the exceptions match her gut. Coworkers stay on v2.78.8 until owner verification passes.
