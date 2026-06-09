# AR Dashboard — TAB BANK Short-Pay Detection (R2 M3b)

**Date:** 2026-06-09
**Status:** approved (open questions resolved by user 2026-06-09)
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

- **SUSPENSE handling:** TAB BANK rows where `debtor_name` is exactly `SUSPENSE` (case-insensitive) cannot be applied to any AR invoice (the customer is unidentified). They get listed as `tab_bank_suspense_row` exceptions in the EXCEPTIONS sheet so Jihyun can manually identify the customer and apply the deposit. **They are NOT silently ignored — the `amount` they carry is real money.** They just can't be auto-matched until Jihyun resolves the customer.
- **Per-invoice direct match:** for each invoice on AR, look in TAB BANK for rows where the `invoice` column matches this invoice's INV#, and sum the **`collected_amount`** column. That sum is the actual deposit applied to this invoice (Jihyun's locked workflow per 2026-06-09: "she takes the amount per invoice"). Per-check# totals are computed by summing across rows but NOT used for allocation — the per-invoice numbers come straight from TAB BANK's per-row data.
- **Short pay detection:** if `invoice_amount_owed − (yesterday's paid + TAB BANK collected_amount) > $0.01` → emit `short_pay` exception AND keep the invoice on AR with adjusted balance + `Short paid MM/DD/YYYY #checkno` memo.
- **Overpay detection:** if the same calculation gives `< −$0.01` → emit `overpay` exception (handled by future Overpayment Workflow; we only DETECT, no auto-credit).
- **Phase 2 engine change:** Phase 2 currently uses only QBO Collection arithmetic. After this change, Phase 2 uses **TAB BANK `collected_amount` as the per-invoice source of truth** when present, falling back to QBO Collection only when an invoice has no matching TAB BANK row.

### Out of scope

- **Overpayment auto-credit creation:** the 2 negative-balance "overpaid" rows Jihyun creates by hand today (LM26060374F = −$35, LM26060375F = −$30) stay manual. The Overpayment Workflow modal is a separate spec.
- **Cross-day TAB BANK reapplication tracking:** the email's 5/18 → 6/4 reposting case requires comparing TAB BANK files across days. Out of scope. The SUSPENSE filter catches the *symptom* (SUSPENSE rows excluded); the cause needs its own design.
- **Customer-DB-backed SUSPENSE matching:** if a SUSPENSE row has a real check# that matches QBO, we just flag it. We do not try to figure out which customer it belongs to. That's cat #1 territory.

## 4. Design

### 4.1 New filters and helpers

**SUSPENSE handling:** extend the pre-filter in `detectTabBankExceptions`:

```js
// Filter out from per-invoice auto-matching, but DO emit each as an exception
const tbRows = tab_bank.rows.filter(r =>
  r &&
  r.check != null && r.check !== '' &&
  r.desc !== 'NON-FACTORED' &&
  (r.debtor_name || '').toString().trim().toUpperCase() !== 'SUSPENSE'
);
const suspenseRows = tab_bank.rows.filter(r =>
  r && (r.debtor_name || '').toString().trim().toUpperCase() === 'SUSPENSE'
);
```

Every SUSPENSE row generates a `tab_bank_suspense_row` exception carrying the deposit `amount`, `check`, the (likely fake) `invoice` field, the `desc`, and the `pmt_type`. These are the unidentified deposits Jihyun must reconcile by hand. They are NOT included in any per-check# or per-invoice totals.

### 4.2 Two indexes — check# and INV#

The existing MVP indexes TAB BANK as `Map<check_no, single_row>`. We need both check#-level (for posting-gap and customer-mismatch detection from MVP) AND per-invoice (for short-pay detection). Change to:

```js
// check# → array of TAB BANK rows (for MVP detections)
const tbByCheck = new Map();
for (const r of tbRows) {
  const key = String(r.check);
  if (!tbByCheck.has(key)) tbByCheck.set(key, []);
  tbByCheck.get(key).push(r);
}

// invoice → array of TAB BANK rows (for short-pay detection)
const tbByInvoice = new Map();
for (const r of tbRows) {
  const inv = r.invoice;
  if (inv == null || inv === '') continue;
  const key = String(inv).trim();
  if (!tbByInvoice.has(key)) tbByInvoice.set(key, []);
  tbByInvoice.get(key).push(r);
}
```

For the existing MVP customer_mismatch detection: when there are multiple TAB BANK rows for the same check#, use the **first non-empty `debtor_name`** as the canonical name for comparison.

**MVP correction:** the existing MVP's posting-gap detection sums `amount` per check#. That's wrong per the 06/04 SUSPENSE example, where the `amount` column inflates because the same deposit is repeated across multiple SUSPENSE rows. Posting-gap detection should sum **`collected_amount`** instead — that net-balances correctly. Apply this fix as part of this slice.

### 4.3 Short-pay / overpay detection — per invoice

Per-invoice direct match using TAB BANK's `collected_amount` column. For each invoice currently on AR (cloned from yesterday in Phase 1):

```js
const tbAppliedByInv = new Map(); // inv → { sum_collected, check_nos, rows }
for (const [inv, rows] of tbByInvoice) {
  const sumCollected = rows.reduce((s, r) => s + num(r.collected_amount), 0);
  const checkNos = [...new Set(rows.map(r => r.check).filter(Boolean).map(String))];
  tbAppliedByInv.set(inv, { sum_collected: sumCollected, check_nos: checkNos, rows });
}

for (const [inv, arRow] of todayAr) {
  const tb = tbAppliedByInv.get(inv);
  if (!tb || tb.sum_collected === 0) continue; // no TAB BANK activity on this invoice

  const owed = num(arRow.amount) - num(arRow.paid); // expected to be paid (carry-forward balance)
  const collected = tb.sum_collected;
  const diff = owed - collected; // positive = short; negative = over

  if (diff > 0.01) {
    out.push({
      kind: 'short_pay',
      check_no: tb.check_nos.join(', '),
      message: `${inv} short-paid by $${diff.toFixed(2)} — TAB BANK applied $${collected.toFixed(2)}, balance owed $${owed.toFixed(2)}`,
      amount: diff,
      shortage: diff,
      tb_collected: collected,
      ar_balance_owed: owed,
      affected_invs: [inv],
      tab_bank_row: tb.rows[0],
      qbo_row: null,
      tab_bank_customer: tb.rows[0].debtor_name || null,
      qbo_customer: arRow.company || null,
    });
  } else if (diff < -0.01) {
    out.push({
      kind: 'overpay',
      check_no: tb.check_nos.join(', '),
      message: `${inv} OVERPAID by $${(-diff).toFixed(2)} — TAB BANK applied $${collected.toFixed(2)}, balance owed $${owed.toFixed(2)}`,
      amount: -diff,
      overage: -diff,
      tb_collected: collected,
      ar_balance_owed: owed,
      affected_invs: [inv],
      tab_bank_row: tb.rows[0],
      qbo_row: null,
      tab_bank_customer: tb.rows[0].debtor_name || null,
      qbo_customer: arRow.company || null,
    });
  }
}
```

After this pass, emit one exception per SUSPENSE row in `suspenseRows`:

```js
for (const r of suspenseRows) {
  out.push({
    kind: 'tab_bank_suspense_row',
    check_no: String(r.check || ''),
    message: `Check# ${r.check} for $${num(r.amount).toFixed(2)} marked SUSPENSE in TAB BANK — customer not identified by bank. Investigate which customer/invoice this belongs to.`,
    amount: num(r.amount),
    tb_collected: num(r.collected_amount),
    affected_invs: r.invoice ? [r.invoice] : [],
    tab_bank_row: r,
    qbo_row: null,
    tab_bank_customer: 'SUSPENSE',
    qbo_customer: null,
  });
}
```

### 4.4 No allocation needed — per-row TAB BANK data is the truth

Per Jihyun's locked workflow (2026-06-09): "she takes the amount per invoice". TAB BANK records each application as a separate row tied to a specific invoice via the `invoice` column. The `collected_amount` field on that row IS the per-invoice amount.

For the 06/08/2026 check# 208064 case (7 invoices), TAB BANK has 7 rows, each tied to one of LM26040682F / 683F / 684F / 685F / 686F / 688F / one more, with `collected_amount` ≈ $336 (or $402.50 for the $507.50 invoice). The engine doesn't need to split a check total — it reads the per-row applied amount directly.

For check# A0906038795 (single invoice LM26040200F): one TAB BANK row, `collected_amount` = $1,767.56, `chargeback_amount` = $35, leaving balance $35.

For check# 18813 (single invoice PE26050012F): one TAB BANK row, `collected_amount` = $925, balance $40.

**No even split, no per-row chargeback math. Just read the row that matches the invoice.**

The `chargeback_amount` column is informational only in this design — we compute the shortage as `owed − collected` instead of trusting TAB BANK's recorded chargeback. (This makes the engine self-consistent: shortage on the AR row is derived from the AR row's own balance, not from TAB BANK's record of what it thinks the invoice was for, which is stale per the locked rule.)

### 4.5 Phase 2 engine integration

The current Phase 2 uses QBO Collection arithmetic only:

```js
const newPaid = num(row.paid) + totalApplied;  // totalApplied from QBO Collection
const newBalance = num(row.amount) - newPaid;
if (Math.abs(newBalance) < AMT_EPS || newBalance < -AMT_EPS) {
  todayAr.delete(inv);
  continue;
}
```

After this change:

1. **Run `detectTabBankExceptions` BEFORE Phase 2** (move it from Phase 5b to Phase 1b, between cloning yesterday and applying collections). It builds `tbByInvoice` and computes `tbAppliedByInv` as a side-effect that Phase 2 consumes.

2. **Phase 2 prefers TAB BANK collected_amount over QBO Collection amount.** For each invoice in `collectionByInvoice` (which is keyed off QBO Collection):

   ```js
   const tb = tbAppliedByInv.get(inv);
   const totalApplied = tb && tb.sum_collected > 0
     ? tb.sum_collected
     : lines.reduce((s, l) => s + num(l.amount), 0); // fallback to QBO
   const checkNo = tb && tb.check_nos.length > 0
     ? tb.check_nos[0]
     : lines[0].txn_number;
   ```

3. The rest of the Phase 2 logic (compute newBalance, drop if ≈ 0, keep with memo if > 0) stays the same. The crucial difference: when QBO posted "full payment" but TAB BANK only collected partial, `totalApplied` reflects the TAB BANK number, `newBalance` ends up > 0, and the invoice stays on AR with the short-pay memo automatically.

4. **Phase 2 also processes invoices that have TAB BANK rows but no QBO Collection lines.** This is the "QBO didn't post but money came in" case — surfaced as `posting_gap_qbo_missing` by the MVP, and now also applied to the AR balance (so the next day's AR reflects the deposit even though QBO is behind). We extend the Phase 2 loop to walk `tbAppliedByInv.keys()` in addition to `collectionByInvoice.keys()`, applying any invoice that has TAB BANK activity but no QBO match.

5. The TAB BANK exceptions still attach to the build return at the end (Phase 5b becomes Phase 5c — same return shape).

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

All resolved by user 2026-06-09.

| # | Question | Resolution |
|---|---|---|
| Q1 | Allocation strategy? | **TAB BANK per-row `collected_amount`** — no allocation math, just read the per-row value. Jihyun's workflow: "she takes the amount per invoice". |
| Q2 | SUSPENSE handling? | **Real money but unknown customer.** Surface each SUSPENSE row as a `tab_bank_suspense_row` exception with full data (check#, amount, fake invoice, desc, pmt_type) so Jihyun can identify and apply manually. Do NOT auto-apply, do NOT include in totals. |
| Q3 | Memo format? | **Match Jihyun's exact format:** `Short paid MM/DD/YYYY #checkno`. No dollar amount appended. |

## 8. Risks

- **Phase 2 engine change touches the most-tested code path.** Existing 99.71% real-world correctness baseline relies on Phase 2's existing arithmetic. Switching from QBO-only to TAB-BANK-preferred-with-QBO-fallback is the biggest change in this slice. Mitigation: the fallback to QBO when no TAB BANK row exists for an invoice preserves the existing behavior for the ~95% of invoices that have no TAB BANK row this build day. Regression testing on the 6 baseline build days catches drift.
- **TAB BANK `collected_amount` is wrong for some real cases.** If a TAB BANK row records `collected_amount` differently from how Jihyun reads it (e.g. includes chargeback, or is signed differently), we'd compute the wrong balance. Mitigation: the §6 verification approach explicitly compares the 8 missing-invoice balances against Jihyun's hand-built values. Discrepancy here triggers a switch to using `amount − chargeback_amount` or another formula.
- **Phase 2 processing TAB-BANK-only invoices is new behavior.** Today's engine only walks `collectionByInvoice`. After this change, Phase 2 also walks `tbAppliedByInv.keys()` to catch invoices that have TAB BANK activity but no QBO post. If a TAB BANK row was misapplied to a fake invoice number (equipment number, etc.), we'd try to apply it to an AR row that doesn't exist — `todayAr.get(inv)` returns undefined and we'd correctly skip. Safe.
- **Performance.** Adding two indexes (tbByCheck, tbByInvoice) and one extra Phase 2 walk is still O(n + m). No risk.

## 9. Ship discipline

Owner-only preview build (`joe.r@ngltrans.net` gating, same as v2.78.6 + v2.79.0). No GH release until verification §6 passes against the 06/08 inputs. If verification passes AND Jihyun signs off on her own next morning's build, then push for coworkers — that's the first TAB BANK detection feature she'd actually rely on in production.
