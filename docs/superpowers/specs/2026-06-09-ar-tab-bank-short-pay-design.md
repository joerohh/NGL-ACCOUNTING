# AR Dashboard — TAB BANK Short-Pay Detection (R2 M3b)

**Date:** 2026-06-09
**Status:** draft — awaits user review
**Owner:** Joseph Roh
**Related:**
- `docs/superpowers/specs/2026-05-20-ar-dashboard-design.md` — original R1/R2 spec (§4.3 locked rule, §5.4 cat #9 TAB BANK posting errors, §5.5 Overpayment Workflow)
- `docs/superpowers/specs/2026-06-09-ar-tab-bank-exceptions-mvp-design.md` — sibling spec for posting gaps + customer mismatches (MVP just shipped to v2.79.0)
- `memory/project_ar_dashboard_wip.md` — current status
- Real-world example: `PROGRAMvsManual/NGL Mail - sample Tab Bank error.pdf` (TAB BANK posted A0905182272/3 as SUSPENSE on 5/18, corrected to real invoices on 6/4)

---

## 1. Context

The v2.79.0 build vs Jihyun's 06/08/2026 hand-built workbook revealed that **8 invoices were dropped from the program's AR sheet** that Jihyun kept on hers, all matching the same fingerprint:

| INV# | Yest balance | Jihyun today | Check# | Memo |
|---|---|---|---|---|
| LM26040200F | $1,802.56 | $35 | A0906038795 | Short paid |
| LM26040682F | $441.00 | $105 | 208064 | Short paid |
| LM26040683F | $507.50 | $105 | 208064 | Short paid |
| LM26040684F | $441.00 | $105 | 208064 | Short paid |
| LM26040685F | $441.00 | $105 | 208064 | Short paid |
| LM26040686F | $441.00 | $105 | 208064 | Short paid |
| LM26040688F | $441.00 | $105 | 208064 | Short paid |
| PE26050012F | $965.00 | $40 | 18813 | Short paid |

Root cause: **the engine trusts QBO Collection arithmetic completely**. When QBO posts a payment as covering the full invoice amount, Phase 2 reduces the balance to zero and drops the row from AR — even when the actual TAB BANK deposit was less than the QBO-posted amount. Jihyun catches these by eye when she sees TAB BANK's amount column show less than the QBO Collection amount for the same check#.

The shipped MVP detector (`detectTabBankExceptions`) catches *posting gaps* (check# in one, not the other) and *customer name mismatches*, but does not compare the **dollar amounts per check#**. This spec adds that comparison and updates Phase 2 to keep short-paid invoices on AR.

A second motivator: the TAB BANK error email (forwarded by Jihyun 2026-06-09) shows TAB BANK *reposts* the same check# weeks later when the original was wrongly applied to SUSPENSE / equipment numbers / wrong customers. The 5/18 file had 7 rows for A0905182272/3 all marked SUSPENSE; the 6/4 file had the correct application. The shipped MVP filter only excludes `NON-FACTORED` rows — it does not exclude `SUSPENSE`, which means SUSPENSE garbage rows feed into the check# index and produce noise.

## 2. Goal

For every check# shared between TAB BANK and QBO Collection, compare the actual TAB BANK deposit amount against the QBO-posted total. When they disagree by more than $0.01, surface the discrepancy as an exception AND keep the affected invoice(s) on AR with a `Short paid MM/DD/YYYY #checkno` memo and a corrected balance.

## 3. Scope

### In scope

- **SUSPENSE filter:** TAB BANK rows where `debtor_name` is exactly `SUSPENSE` (case-insensitive) are excluded from the check# index. They go into a new `tab_bank_suspense_row` exception so Jihyun sees them.
- **Per-check#-amount comparison:** for each check# in both `tbIndex` and `qboIndex`:
  - `tbTotal = sum of TAB BANK amount column across rows with this check# (post-filter)`
  - `qboTotal = sum of QBO Collection amount across Invoice lines with this check#`
  - If `qboTotal − tbTotal > 0.01` → **short pay** → emit `short_pay` exception + leave the invoices on AR with adjusted balance.
  - If `tbTotal − qboTotal > 0.01` → **overpay** → emit `overpay` exception (handled by future Overpayment Workflow; we only DETECT, no auto-credit).
- **Phase 2 engine change:** when a short pay is detected, do NOT drop the invoice. Recompute balance using TAB BANK as truth. Generate the `Short paid MM/DD/YYYY #checkno` memo.
- **Allocation strategy:** for a single-invoice short pay, the math is trivial (one balance, one shortage). For a multi-invoice short pay (one check covering N invoices), distribute the shortage **evenly across the N invoices** as the v1 default. See §4.4.

### Out of scope

- **Auto-allocation by TAB BANK chargeback column:** TAB BANK has `chargeback_amount` and `collected_amount` columns per row. We do NOT use these in v1 because the locked rule (spec §4.3) says TAB BANK's amount columns are unreliable. Future revision once we have data showing they're trustworthy for this specific case.
- **Overpayment auto-credit creation:** the 2 negative-balance "overpaid" rows Jihyun creates by hand today (LM26060374F = −$35, LM26060375F = −$30) stay manual. The Overpayment Workflow modal is a separate spec.
- **Cross-day TAB BANK reapplication tracking:** the email's 5/18 → 6/4 reposting case requires comparing TAB BANK files across days. Out of scope. The SUSPENSE filter catches the *symptom* (SUSPENSE rows excluded); the cause needs its own design.
- **Customer-DB-backed SUSPENSE matching:** if a SUSPENSE row has a real check# that matches QBO, we just flag it. We do not try to figure out which customer it belongs to. That's cat #1 territory.

## 4. Design

### 4.1 New filters and helpers

**SUSPENSE filter:** extend the pre-filter in `detectTabBankExceptions`:

```js
const tbRows = tab_bank.rows.filter(r =>
  r &&
  r.check != null && r.check !== '' &&
  r.desc !== 'NON-FACTORED' &&
  (r.debtor_name || '').toString().trim().toUpperCase() !== 'SUSPENSE'
);
const suspenseCount = tab_bank.rows.filter(r =>
  r && (r.debtor_name || '').toString().trim().toUpperCase() === 'SUSPENSE'
).length;
```

If `suspenseCount > 0`, emit ONE info-level exception per check# that appears in the SUSPENSE rows (deduped by check#) so Jihyun can audit them.

### 4.2 Check# index change — multi-row support

The existing MVP indexes TAB BANK as `Map<check_no, single_row>`. That breaks for the email's pattern (same check# on 6+ rows). Change to:

```js
const tbIndex = new Map(); // check_no → array of rows
for (const r of tbRows) {
  const key = String(r.check);
  if (!tbIndex.has(key)) tbIndex.set(key, []);
  tbIndex.get(key).push(r);
}
```

`qboIndex` already groups multi-line.

For the existing MVP customer_mismatch detection: when there are multiple TAB BANK rows for the same check#, use the **first non-empty `debtor_name`** as the canonical name for comparison. (The email shows 6 of 7 SUSPENSE-row checks have `debtor_name = SUSPENSE`, so they'd be pre-filtered anyway.)

### 4.3 Short-pay / overpay detection

After the existing posting-gap and customer-mismatch passes, add a new pass:

```js
for (const [checkNo, tbRows_] of tbIndex) {
  const qboLines = qboIndex.get(checkNo);
  if (!qboLines || qboLines.length === 0) continue; // posting gap, already emitted

  const tbTotal  = tbRows_.reduce((s, r) => s + num(r.amount), 0);
  const qboTotal = qboLines.reduce((s, l) => s + num(l.amount), 0);
  const diff = qboTotal - tbTotal;

  if (diff > 0.01) {
    // Short pay — emit exception + flag invoices for Phase 2 to keep on AR
    out.push({
      kind: 'short_pay',
      check_no: checkNo,
      message: `Check# ${checkNo} short-paid $${diff.toFixed(2)} — TAB BANK $${tbTotal.toFixed(2)}, QBO $${qboTotal.toFixed(2)} (${qboLines.length} invoice${qboLines.length === 1 ? '' : 's'})`,
      amount: diff,
      shortage: diff,
      tb_deposit: tbTotal,
      qbo_applied: qboTotal,
      affected_invs: qboLines.map(l => l.invoice_or_ref).filter(Boolean),
      tab_bank_row: tbRows_[0],
      qbo_row: qboLines[0],
      tab_bank_customer: tbRows_[0].debtor_name || null,
      qbo_customer: qboLines[0].customer || null,
    });
  } else if (diff < -0.01) {
    // Overpay — detect only; Overpayment Workflow handles fix
    out.push({
      kind: 'overpay',
      check_no: checkNo,
      message: `Check# ${checkNo} OVERPAID by $${(-diff).toFixed(2)} — TAB BANK $${tbTotal.toFixed(2)}, QBO $${qboTotal.toFixed(2)}`,
      amount: -diff,
      overage: -diff,
      tb_deposit: tbTotal,
      qbo_applied: qboTotal,
      affected_invs: qboLines.map(l => l.invoice_or_ref).filter(Boolean),
      tab_bank_row: tbRows_[0],
      qbo_row: qboLines[0],
      tab_bank_customer: tbRows_[0].debtor_name || null,
      qbo_customer: qboLines[0].customer || null,
    });
  }
}
```

### 4.4 Allocation strategy (default: even split)

For a `short_pay` exception with N affected invoices, distribute the shortage **evenly**: `per_invoice_shortage = round(diff / N, 2)`, with the last invoice absorbing rounding remainder (so totals reconcile exactly).

**Why even split as default:** Jihyun's 06/08 workbook shows exactly this pattern for check# 208064 — 7 invoices, $105 short EACH (flat per-invoice). For check# A0906038795 single invoice (LM26040200F, $35 short). For check# 18813 single invoice (PE26050012F, $40 short). The flat-per-invoice convention is consistent for the visible cases.

**Open question for Jihyun (§7):** is this her actual convention, or is she using TAB BANK's per-row `chargeback_amount` column? If the latter, we need to switch the allocation strategy.

### 4.5 Phase 2 engine integration

The current engine Phase 2 has this branch:

```js
const newPaid = num(row.paid) + totalApplied;
const newBalance = num(row.amount) - newPaid;
if (Math.abs(newBalance) < AMT_EPS || newBalance < -AMT_EPS) {
  todayAr.delete(inv);
  continue;
}
```

The engine needs to know about short pays BEFORE running Phase 2 so it can keep the invoice on AR. Cleanest approach:

1. **Run `detectTabBankExceptions` BEFORE Phase 2** (move it from Phase 5b to Phase 1b, between cloning yesterday and applying collections).
2. Build a map `shortPayByInvoice: Map<inv_no, per_invoice_shortage>` from the exceptions.
3. In Phase 2: when about to delete an invoice because `Math.abs(newBalance) < AMT_EPS`, check `shortPayByInvoice`. If a shortage is recorded for this invoice → **keep it on AR with `balance = shortage`, `paid = amount − shortage`, memo = `Short paid MM/DD/YYYY #checkno`**.
4. The TAB BANK exceptions still attach to the build return at the end.

### 4.6 New exception kinds

Extend the kind enum:

| kind | meaning |
|---|---|
| `posting_gap_qbo_missing` | (MVP) bank deposit, no QBO post |
| `posting_gap_tab_missing` | (MVP) QBO post, no bank record |
| `customer_mismatch` | (MVP) check# in both, normalized names differ |
| `info_all_non_factored` | (MVP) TAB BANK file all NON-FACTORED, gap detection skipped |
| **`short_pay`** | NEW — TAB BANK deposit < QBO posted on same check# |
| **`overpay`** | NEW — TAB BANK deposit > QBO posted on same check# (detect only) |
| **`tab_bank_suspense_row`** | NEW — check# appears as SUSPENSE in TAB BANK, surfaced for audit |

Sort order (most urgent first):
1. `short_pay` (real money the engine would otherwise lose)
2. `overpay` (real money received but not credited)
3. `posting_gap_qbo_missing` (bank deposit, no QBO post)
4. `posting_gap_tab_missing` (QBO post, no bank record)
5. `tab_bank_suspense_row` (audit-only, low priority)
6. `customer_mismatch` (data quality)
7. `info_all_non_factored` (informational)

### 4.7 Preview modal updates

Existing KPI tile keeps the same `Suspense / exceptions` label and the same alert styling. The detail table grows two columns to show short-pay context:

| Issue | Check # | Shortage / Overage | TAB BANK $ | QBO $ | Affected INV# | Customer |
|---|---|---|---|---|---|---|
| Short pay | 208064 | $735.00 | $2,418.50 | $3,153.50 | LM26040682F + 6 more | IDC LOGISTICS |
| Overpay | A0906082889 | $35.00 | $237,306.80 | $237,271.80 | LM26040236F + 12 more | AMNEX SOLUTIONS |
| Customer name mismatch | 13175 | — | $50,850 | — | (single line) | 3 PLUS LOGISTICS CO |

Top 50 rows cap stays.

### 4.8 EXCEPTIONS sheet column additions

Extend the writer to handle the new fields. Add two columns to the existing `EXCEPTIONS_HEADERS`:

```js
const EXCEPTIONS_HEADERS = [
  'Kind', 'Check #', 'Amount', 'TAB BANK $', 'QBO $', 'Affected INV#s',
  'TAB BANK Customer', 'QBO Customer', 'Detected Issue', 'Notes',
];
const EXCEPTIONS_WIDTHS = [28, 14, 14, 14, 14, 40, 28, 28, 60, 30];
```

For `short_pay` / `overpay` rows: `TAB BANK $` = `tb_deposit`, `QBO $` = `qbo_applied`, `Affected INV#s` = comma-joined `affected_invs`. For other kinds: those three columns are blank.

## 5. Implementation outline (rough — actual plan comes from writing-plans)

Files to modify:

- `app/assets/js/tools/ar-dashboard/ar-dashboard-build.js`
  - Extend pre-filter to exclude SUSPENSE
  - Change `tbIndex` to `Map<check_no, rows[]>`
  - Add short_pay / overpay / tab_bank_suspense_row detection passes
  - Move `detectTabBankExceptions` call to BEFORE Phase 2 in `arBuildToday`
  - Build `shortPayByInvoice` map from exceptions; consume in Phase 2
- `app/assets/js/tools/ar-dashboard/ar-dashboard-build-ui.js`
  - Add `tabBankExceptionLabel` cases for new kinds
  - Update `renderKpiDetail('exception', ...)` table to include `Shortage/Overage`, `TAB BANK $`, `QBO $`, `Affected INV#`
- `app/assets/js/tools/ar-dashboard/ar-dashboard-build-writer.js`
  - Extend `EXCEPTIONS_HEADERS` / `EXCEPTIONS_WIDTHS`
  - Populate the new columns from the exception fields
- `tools/test_tab_bank_exceptions.mjs`
  - New tests for SUSPENSE filter, short_pay (single-invoice + multi-invoice even-split), overpay detection, tab_bank_suspense_row
- `desktop/VERSION` → 2.79.1 (patch — building on 2.79.0 MVP)

## 6. Verification approach

When this ships, re-run the 06/08/2026 program build against the same inputs Jihyun used. Acceptance:

1. **8 missing short-pay invoices reappear in PROGRAM AR_today** at exactly Jihyun's balances:
   - LM26040200F at $35, LM26040682F at $105, LM26040683F at $105, LM26040684F at $105, LM26040685F at $105, LM26040686F at $105, LM26040688F at $105, PE26050012F at $40
2. **All 8 carry the memo** `Short paid 06/08/2026 #<checkno>` matching Jihyun's column-by-column.
3. The 2 overpayment credits (LM26060374F, LM26060375F) **still don't appear in PROGRAM AR** — those are Overpayment Workflow territory, properly out of scope.
4. **EXCEPTIONS sheet contains:** 3 short_pay rows (for check#s A0906038795, 208064, 18813) and at least 1 overpay row (for check#s involved in the 2 overpayment credits).
5. **No regression in the existing 6 baseline build days** — re-run `tools/verify_ar_build_js.mjs`, baseline parity unchanged on `today_ar` / `tms_rows` / `adjustment_rows`.
6. AR_today cell-level match % on 06/08/2026 jumps from **86.07% (v2.79.0)** to ≥ **99.5%** (the 4 ADJUSTMENT misses + 2 overpayment credits + 1 aging off-by-1 + 3 manual-memo diffs will keep us below 100%, all expected).

## 7. Open questions

| # | Question | Why it matters |
|---|---|---|
| Q1 | Is the per-invoice short-pay allocation **flat (equal split)** or **proportional (chargeback per row from TAB BANK)**? | Determines whether we trust TAB BANK's `chargeback_amount` column or compute even split. Jihyun's 06/08 data shows flat — but that may be incidental. |
| Q2 | When TAB BANK shows a check# applied to **SUSPENSE** with a non-zero `amount`, should the engine ALSO apply that amount to whatever invoice QBO has it on? Or treat the SUSPENSE row as zero-collected until corrected? | Affects how the 5/18 → 6/4 reapplication pattern surfaces. Current spec: ignore SUSPENSE entirely, treat as zero. |
| Q3 | Should the `Short paid` memo include the per-invoice shortage amount (`Short paid 06/08/2026 #208064 -$105`) or just the check# (`Short paid 06/08/2026 #208064`)? | Jihyun's workbook uses the bare `#checkno` form. Spec defaults to matching her format. |

## 8. Risks

- **Even-split allocation is wrong for some real cases.** If a TAB BANK chargeback applies $735 to one specific invoice (not split), even-split gives every invoice the wrong balance. Mitigation: surface `affected_invs` in the EXCEPTIONS sheet so Jihyun can audit; on Q1 resolution, switch to per-row strategy if needed.
- **Phase 2 engine change touches the most-tested code path.** Existing 99.71% real-world correctness baseline relies on Phase 2's existing arithmetic. Adding the `shortPayByInvoice` lookup is additive (only changes the delete-branch), but any regression here costs real money. Mitigation: keep the existing `Math.abs(newBalance) < AMT_EPS` test, only intercept when shortage is recorded for that specific invoice.
- **SUSPENSE filter could mask real check#s that Jihyun reconciles by hand.** If a SUSPENSE row's check# is in QBO and the deposit IS real, we'd skip its short-pay check. Mitigation: surface every SUSPENSE check# as `tab_bank_suspense_row` so Jihyun reviews it even when filtered.
- **Performance.** Adding a third pass over tbIndex × qboIndex is still O(n + m). No risk.

## 9. Ship discipline

Owner-only preview build (`joe.r@ngltrans.net` gating, same as v2.78.6 + v2.79.0). No GH release until verification §6 passes against the 06/08 inputs. If verification passes AND Jihyun signs off on her own next morning's build, then push for coworkers — that's the first TAB BANK detection feature she'd actually rely on in production.
