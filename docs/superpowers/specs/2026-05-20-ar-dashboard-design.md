# AR Dashboard — Design

**Date:** 2026-05-20
**Updated:** 2026-06-01 — folded in AR associate (Jihyun) workflow input
**Author:** Joseph (with Claude)
**Status:** Design — ready for implementation planning
**Scope:** Tool #4 in NGL Accounting (alongside Merge Tool, Invoice Sender, Customers)

---

## 1. Overview

A daily reconciliation cockpit for the AR associate. Replaces a ~3-4 hour/morning manual workbook-building routine with a single-click build + an exceptions worklist that surfaces every discrepancy between TAB BANK, QBO, TMS, and the AR register.

**Primary user:** the AR associate. Comes in every morning, builds today's AR aging workbook, works through exceptions, sends collection emails, copies a daily TAB BANK summary report, posts payments.

**Primary job-to-be-done:** *"balance the books — make sure everything on the receiving side adds up."*

**Secondary user:** management. Needs to ask interrogative questions about AR trends, customer payment behavior, lane/customer profitability without depending on the associate's local files.

---

## 2. Scope

### Release 1 — Dashboard + write capability (ships first)

- Loads a pre-built workbook (created by hand or by the build engine)
- Reconciliation cockpit (Summary tab + Exceptions worklist with 10 categories)
- All view tabs (AR Register, Collections, Overdue, Partial Pays, New Invoices, TMS, Adjustments, Suspense, Customers)
- Multi-period views: Today + Week (Month/Quarter/Year deferred to R3)
- **Inline editing of any AR row** (amount, paid, balance, memo, status) with day-over-day persistence in Supabase
- **Per-row memo notes** — freeform, persisted across rebuilds
- **Overpayment workflow modal** — guided 4-step process (push to TMS/QBO → create credit → land in AR → memo)
- Cross-tool actions: Email customer · Email TAB BANK on posting error · Open in QBO

### Release 2 — Build Engine (ships next)

- Auto-fetch QBO Daily Collection + Daily Schedule via QBO REST API
- Manual drops for TAB BANK Remittance + TMS Reconcile
- Auto-locate yesterday's workbook
- Build engine with preview + save
- **Build respects R1's inline edits as overrides** — won't clobber manual corrections on next morning's rebuild
- Writes daily snapshots to Supabase (unified data layer foundation)

### Release 3 — Insights & Management Q&A (later)

- Week/Month/Quarter/Year views
- Per-customer history pages
- "Ask AI" tab for management Q&A
- Older-invoices write-off workflow polish

### Out of scope

- Real-time live refresh during the day (build is once-per-morning)
- TAB BANK report ingestion automation (no API; manual drop persists)
- Warehouse invoice auto-detection (handled via manual entry for now)

---

## 3. Architecture

### Placement

4th tab in the left sidebar of the Electron app, alongside Merge / Invoice Sender / Customers.

### Code shape (mirrors existing tools)

```
app/assets/js/tools/ar-dashboard/
  ar-dashboard.js          state machine + render
  ar-dashboard-loader.js   parse workbook -> in-memory model
  ar-dashboard-build.js    build engine (R2)
  ar-dashboard-views.js    render Summary, Collections, Overdue, etc.
  ar-dashboard-actions.js  copy-to-clipboard, email-jumps, mark-resolved, edit-row, overpayment-modal
  ar-dashboard-supabase.js Supabase sync (R/W from R1)
```

Pipeline is **fully client-side** for Release 1 + 2 (SheetJS already loaded). Agent endpoints only handle the two QBO API fetches.

### Storage — Supabase (NOT SQLite)

**Decision:** AR data lives in Supabase, not local SQLite. Rationale: SQLite is per-machine and survives only as long as the individual does. If the associate quits, all historical AR data + audit trail would be lost. Supabase is already in the NGL stack (customers, users), is multi-user, has automatic backups, and supports the future management Q&A use case.

The Excel workbook becomes a human-readable backup / audit artifact, not the source of truth.

### Supabase tables (proposed)

```
ar_invoices         every invoice ever seen (immutable + status updates)
ar_payments         every payment / check# received
ar_daily_snapshots  one row per build day, JSON blob of full AR register
ar_exceptions       every flagged exception + resolution + resolved_by + resolved_at
ar_manual_entries   credit memos, warehouse, custom rows added by associate
ar_row_overrides    R1 inline edits — preserved across rebuilds (column-level override on ar_invoices)
ar_memos            per-row freeform memo notes — preserved across rebuilds
```

### Shared data model (in-memory)

```js
{
  today_date, yesterday_date,
  ar_register: [{ inv, customer, customer_id, amount, paid, balance, aging,
                  aging_bucket, date, ref, mbl, equipment, wo, memo, status,
                  prev_balance, prev_paid, manual_flag, edited, edit_audit }, ...],
  collections: [{ payment_date, check_no, customer_id, customer, invoices: [...] }],
  schedule: [...],
  tms_rows: [...],
  adjustments: [...],
  suspense: [{ check_no, debtor_name, amount, candidate_customers: [...], uc_origin_date, posted_date }],
  exceptions: [{ category, severity, invoice, customer, details, suggested_action }],
  customer_rollup: [...],
  diff: { paid_off: [...], new_today: [...], partial_pays: [...], amount_changes: [...] }
}
```

---

## 4. Input Flow & Build Engine (Release 2)

### 4.1 Inputs

| # | Input | Source | UI |
|---|---|---|---|
| 1 | Yesterday's workbook | Auto-locate from saved output folder | Filename + path + [Change] override |
| 2 | QBO Daily Collection | QBO REST API auto-fetch | [Fetch] button → status |
| 3 | QBO Daily Schedule | QBO REST API auto-fetch | [Fetch] button → status |
| 4 | TAB BANK Remittance | Manual drop (no API) | Drop zone |
| 5 | TMS Reconcile | Manual drop (no API) | Drop zone |

### 4.2 Morning gesture

User clicks AR Dashboard tab → if today's workbook isn't built, dashboard shows a `[Build today's workbook]` CTA → modal opens with 5-input checklist → drops complete → `[Run build]` → preview → `[Save & open dashboard]`.

Mockup: `app/mockups/ar-dashboard-build-flow.html`.

### 4.3 The pipeline — 6 phases (empirically verified at 99%+ across 7 builds)

**Phase 1 — Carry forward.** Clone yesterday's AR register as starting state. Apply any `ar_row_overrides` from Supabase so manual edits survive.

**Phase 2 — Apply yesterday's collections.** For each QBO Daily Collection invoice line:

```
paid     = QBO Amount - QBO Open Balance
balance  = invoice_amount - new_paid
if balance ~= 0   -> drop row (full pay)
if balance < 0    -> drop row (overpay — handled in §6.1 Overpayment Workflow)
if balance > 0    -> keep, update PAID + BALANCE + MEMO "Short paid MM/DD/YYYY #check#"
```

**Critical rule (confirmed by Jihyun 2026-06-01):** payment classification uses **QBO arithmetic only**. TAB BANK's `Pmt Type` column **AND** TAB BANK's `Short Pay` / `Over Pay` amount columns are **IGNORED** because TAB BANK only knows pre-revision invoice amounts — both its classification flags and its amount columns are wrong whenever an invoice has been revised. **Source of truth = the AR balance + the actual deposit amount in TAB BANK.**

**Phase 3 — Add new invoices from Schedule.** For each row in QBO Daily Schedule, add to AR with `PAID = 0`, `AGING = 0`, `AR_STATUS = "A.0~29"`. Re-apply `ar_memos` (freeform notes) on top so per-row memos survive the rebuild.

**Phase 4 — Age all rows.** Aging delta = calendar days between consecutive **build days** (not workbook dates):

```python
def build_day(workbook_date):
    if workbook_date.weekday() == 4:  # Friday workbook
        return workbook_date + timedelta(days=3)   # built following Monday
    return workbook_date + timedelta(days=1)        # built next day
# Post-holiday workbook: bump by N = number of skipped calendar days

aging_delta = (build_day(today) - build_day(yesterday)).days
```

**Friday workbooks bump aging by 3 (built Monday); post-holiday workbooks bump by N; all others by 1.**

**Phase 5 — TMS reconciliation.** Two semantic rules based on TMS column semantics (empirically reverse-engineered):

- **For NEW invoices** (in QBO Schedule): use `TMS TOTAL_AMT` as the settled amount. If `TMS TOTAL_AMT ≠ QBO Schedule Amount` → flag for review (any size difference, no threshold).
- **For OLD invoices** (already on yesterday's AR): `TMS TOTAL_AMT` is the **delta** (= "Amount Difference" column on ADJUSTMENT sheet). If `TOTAL_AMT != 0` → row goes to ADJUSTMENT sheet, AR row updated to `TMS INV_AMT` (= "Revised Invoice Amount" column).

**Phase 6 — Build auxiliary output sheets.**

| Sheet | Content |
|---|---|
| `AR_<today>` | New register from Phases 1–5 |
| `AR_<yesterday>` | Yesterday's register carried forward for diff reference |
| `COL` | QBO Daily Collection cleaned (band rows -> blank rows; col 0 shifted left) |
| `COL (INV)` | Same data as COL, compacted (band-row blanks removed); optional `PARTIAL PAID` / `SHORT PAID` tag column when there are flagged rows |
| `Schedule` | QBO Daily Schedule 1:1 |
| `TMS` | TMS Reconcile rows whose amount matched (no ADJUSTMENT needed) |
| `ADJUSTMENT` | TMS Reconcile rows with `TOTAL_AMT != 0` |

### 4.4 Edge cases

- **Multi-day Monday / post-holiday pull (confirmed by Jihyun 2026-06-01).** On Monday she pulls a 4-day QBO Daily Collection range (Fri-Sat-Sun-Mon). **Same pattern after any public holiday** — the post-holiday workbook covers the full holiday range as a single multi-day pull. Algorithm processes the entire range without filtering. Mon's portion gets re-fetched on Tuesday for the Mon workbook (single-day "in case of late updates").
- **Warehouse invoices.** Not in TMS Reconcile. They appear in QBO Schedule and get added to AR via Phase 3; no ADJUSTMENT comparison. Manual entry path also exists for invoices that never get into QBO Schedule.
- **TMS data errors.** Detected in Phase 5 by comparing TMS `TOTAL_AMT` vs QBO Schedule amount. Any discrepancy flagged in Exceptions worklist — no auto-threshold, defer to human judgment.
- **NON-FACTORED customers.** TAB BANK shows their checks as `SUSPENSE / NON-FACTORED`. We don't use TAB BANK's customer matching for these; QBO Collection's customer field is authoritative. Detected by `DESC = "NON-FACTORED"` tag.
- **TAB BANK posting errors (NEW — Jihyun 2026-06-01).** TAB BANK occasionally (a) assigns the wrong check# to an invoice, or (b) lists the same check# across multiple Pmt Type rows for one customer with conflicting status (Payment + Unapplied Cash + Overpay simultaneously). Detected by: a check# whose deposit amount doesn't match the AR balance for that invoice, OR the same check# appearing on multiple TAB BANK rows for the same customer with conflicting Pmt Type. Workflow: dashboard flags the row as "awaiting TAB BANK correction," provides a paste-ready email body, and excludes the bad row from posting until cleared. **Real-world example:** CHK# 65252 was applied to LM26030418F when it should have been applied to LM26030418F using CHK# 65282 — Jihyun emailed TAB BANK to correct (2026-05-21).
- **Unapplied Cash (UC) reclassification (NEW — Jihyun 2026-06-01).** TAB BANK initially classifies a payment as Unapplied Cash when no remittance is on file. When Jihyun submits the remittance later (sometimes days later, depending on customer confirmation speed), TAB BANK lists the same check# again on the later date — with Payment status. The earlier UC row and the later Payment row are the **same money posting later**, not a duplicate. Dashboard should link them by check# + amount + customer and clear the original UC when its matching Payment row lands. Authoritative post date = the date TAB BANK reflects the Payment classification (i.e. the later date), not the original deposit date. **Real-world example:** check A0904306208 for SUSPENSE/UC001 — deposited 4/30 as UC, reposted 5/4 as Payment when remittance arrived.

### 4.5 Verification evidence

The build engine was empirically verified against 7 hand-built target workbooks (5/8, 5/11, 5/12, 5/13, 5/14, 5/18, 5/19). Match rates:

| Date | Day | Match % | Notes |
|---|---|---|---|
| 2026-05-08 | Fri (built Mon) | 93.76% | Multi-day range; residual = manual touches |
| 2026-05-11 | Mon (built Tue) | 99.80% | |
| 2026-05-12 | Tue | 99.81% | |
| 2026-05-13 | Wed | 99.88% | |
| 2026-05-14 | Thu | 99.95% | |
| 2026-05-18 | Mon (built Tue) | 98.97% | NON-FACTORED manual carry-forward |
| 2026-05-19 | Tue | 99.93% | |

Remaining ~0.05–7% delta is consistently **manual touches** (TMS data error sanity-checks, manual memo entries, customer-requested adjustments, TAB BANK error corrections). The dashboard's role is to surface these for review AND give her the tools to fix them in place, not to reproduce them deterministically.

Verification script: `tools/verify_ar_build.py`. Read-only, runs against all build folders.

---

## 5. UI Structure

### 5.1 The dashboard is a reconciliation cockpit

The associate's job is **balancing the books on the receiving side**. The UI's primary surface is the **Exceptions worklist** — every mismatch the algorithm could detect between TAB BANK, QBO Collection, QBO Schedule, TMS, and the AR register. Each row is a task she works to closed.

### 5.2 Tabs

| Tab | Purpose |
|---|---|
| **Summary** | Reconciliation cockpit. Exceptions worklist top, KPIs strip, aging bar, period selector |
| **AR Register** | Full register lookup (sortable, filterable, searchable, **inline-editable**) |
| **Collections** | What left the AR yesterday (grouped by check#, with per-check reconciliation check) |
| **Overdue** | Call list — AR rows with balance > 0 and aging > 30, sorted oldest first |
| **Partial Pays** | Open invoices with PAID > 0 (the short-pay survivors) |
| **New Invoices** | Today's Schedule additions |
| **TMS** | TMS Reconcile rows that matched (no adjustment) |
| **Adjustments** | TMS-driven amount changes |
| **Suspense** | TAB BANK Unapplied Cash worklist (with UC reclassification linkage) |
| **Customers** | Per-customer rollup with drill-in |

### 5.3 Multi-period views

Time-range selector at top of Summary: `[Today] [Week] [Month] [Quarter] [Year]`

Switching changes the KPIs, activity feed, aging context — but **Exceptions worklist always stays at "today"** (that's the daily work).

Release 1 ships with Today + Week only. Month/Quarter/Year defer to Release 3 once enough historical workbooks exist.

### 5.4 Exceptions worklist categories

| # | Category | Trigger | Action affordances |
|---|---|---|---|
| 1 | Bank suspense | TAB BANK Unapplied Cash row, debtor name not in customer DB | Match to customer + paste-ready snippet for TAB BANK portal |
| 2 | Short pays | Open Balance > 0 after posting | Call/email customer · write-off · keep open · edit memo |
| 3 | Over pays | Open Balance < 0 after posting | **Open Overpayment Workflow modal** (4-step guided process — see §6.1) |
| 4 | Posting gaps | TAB BANK has check# but QBO has nothing posted for it (or vice versa) | Post in QBO · un-apply · hold |
| 5 | Amount disagreements | TMS amount ≠ QBO Schedule amount (new); OR TMS amount ≠ yesterday's AR (old). Only fires when TMS has the row — warehouse-only invoices (QBO-only) can't disagree. | Default to TMS (confirmed Jihyun 2026-06-02 — "TMS is generally the standard since rate is usually adjusted in TMS"). Confirm or override |
| 6 | Customer name mismatches | TAB BANK debtor name ≠ QBO customer for same check# | Confirm correct customer |
| 7 | Missing TMS records | Invoice in QBO Schedule but not in TMS. **Warehouse invoices are normal here** — they only ever come through QBO (no TMS work orders exist for warehouse). Confirmed Jihyun 2026-06-02. | If warehouse customer: auto-accept QBO amount, no action. If not warehouse: surface for manual review |
| 8 | NON-FACTORED informational | TAB BANK SUSPENSE/NON-FACTORED tag | Collapsed by default; no action needed |
| **9** | **TAB BANK posting error** (NEW) | Same check# on multiple TAB BANK rows with conflicting status, OR check# applied to wrong invoice (deposit amount ≠ AR balance) | **Email TAB BANK to correct** (paste-ready) · mark "awaiting TAB BANK correction" · exclude from posting until cleared |
| **10** | **UC awaiting reclassification** (NEW) | Prior-day UC row that hasn't been reposted yet — Jihyun is awaiting customer remittance confirmation | Informational; collapsed by default; auto-clears when matching Payment row appears (link by check# + customer + amount). **Display:** small visible pill on the resolving Payment row (e.g. `↩ from UC 4/30`) — NOT memo text. Confirmed Jihyun 2026-06-02. |

### 5.5 Two-pane split (list tabs)

Every list tab uses a 2-pane layout: table left + detail panel right (`grid-template-columns: 1fr 320-340px`). When no row is selected, the panel shows a useful "Today summary" empty state with quick actions (e.g., jump to top exception, email overdue customers) — never a "select a row" placeholder.

### 5.6 Visual tokens

- White panels, slate text (`#1e293b`), borders `#e2e8f0`
- NGL orange accent `#ea580c`
- Section headings: 4px solid orange left bar + bold 0.95rem
- Dense layout (panels 12px padding, table rows 5-7px)
- No math notation in copy (use colored dot + label, not "× 14")

### 5.7 Mockups committed

- `app/mockups/ar-dashboard-build-flow.html` — build modal + preview
- `app/mockups/ar-dashboard-summary-v2.html` — Summary tab with Exceptions banner
- `app/mockups/ar-dashboard-collections-v3-compare.html` — Collections tab pattern
- `app/mockups/ar-dashboard-overdue-v3.html` — Overdue tab two-pane split

**Mockup refresh needed (post 2026-06-01 spec update):**

1. Lead with the Exceptions worklist (currently they emphasize KPI strip — the spec inverts that priority).
2. Show inline-edit affordance on AR Register table rows.
3. Show per-row memo input affordance.
4. Mock up the Overpayment Workflow modal (§6.1).
5. Add the TAB BANK posting error exception category (#9) with the paste-ready email body affordance.
6. Show how UC rows link to their later Payment rows (lineage indicator).

### 5.8 Inline editing & memos (NEW — Jihyun 2026-06-01)

The associate needs to make manual corrections to AR rows — TAB BANK errors, custom memo notes for customer-specific situations, hand-applied adjustments. The dashboard must support edit-in-place on **every AR Register row**.

**Editable fields:** `amount`, `paid`, `balance`, `memo`, `ar_status`.

**Persistence:** edits write to Supabase (`ar_row_overrides` for column-level overrides; `ar_memos` for freeform memo text). Build engine respects these as overrides — won't clobber manual corrections on next morning's rebuild.

**Audit trail:** every edit records `edited_by`, `edited_at`, `original_value`, `new_value` in Supabase. Edited rows show a small "edited" indicator (orange pip) in the table; hover or click reveals the audit history row.

**Per-row memo specifically:** freeform text per AR row. Persists day-over-day automatically — does not require re-entry after rebuild. Used for things like:

- **Customer-supplied invoice numbers** (e.g., MSC's `001260514`) as proof of approval. MSC used to print these on the remittance; recently switched to NGL's invoice numbers, so Jihyun now enters them manually for record-keeping.
- Manual notes about partial-pay arrangements
- Cross-references to credit memos (overpayment source)
- Reminders for the next morning (*"call AP @ XYZ for memo number"*)

---

## 6. Cross-Tool Actions

| Action | Where | What it does |
|---|---|---|
| Email customer | Overdue tab row action | Jumps to Invoice Sender pre-filled with customer + overdue invoices |
| **Email TAB BANK** (NEW) | Exception category 9 row action | Generates paste-ready email body asking TAB BANK to correct the wrong check# / duplicate posting; flags row as "awaiting correction" |
| Open invoice in QBO | Per-invoice row action | Deep link to QBO via existing QBO REST API integration |
| **Edit AR row** (NEW) | Any AR Register row | Inline edit-in-place — fields, memo, status. Writes override to Supabase. Survives rebuild. |
| **Edit memo** (NEW) | Any AR Register row | Per-row freeform memo input; persists day-over-day. |
| Add manual entry | AR Register + Exceptions | Add credit memo / warehouse / custom row; persists day-over-day |
| **Overpayment workflow** (NEW) | Exception category 3 row action | Opens guided 4-step modal — see §6.1 |
| Mark suspense resolved | Suspense tab row action | Flag in Supabase so it doesn't reappear tomorrow |
| Rebuild today | Settings + data bar | Re-fetch sources, rebuild workbook (same-day overwrite, preserves overrides) |

### 6.1 Overpayment Workflow (NEW — Jihyun 2026-06-01)

Overpayments are the most complex daily case. Per Jihyun: *"short payment processing is simple, but overpayment processing is very complex."*

The dashboard provides a guided modal that walks the associate through her actual process.

**Trigger:** Exception category 3 (Over pays) row clicked, OR a manually-detected overpayment from any AR row.

**The 4 steps** (locked with Jihyun 2026-06-02 — fully manual in TMS + QBO, dashboard guides):

1. **Confirm overpayment.** Modal shows: TAB BANK deposit amount, AR balance, computed overpayment (= deposit − balance), source check#/ACH#, customer, and the original invoice. User confirms amount or adjusts.

2. **Bump the original invoice in TMS, reissue, sync.**
   - For TMS-tracked invoices: deep-link to TMS billing page (`/bc-detail/billing-info/{type}/{woNo}`). User adds the overpaid amount as a positive line to the original invoice, reissues, syncs with QuickBooks.
   - For **warehouse invoices** (no TMS work order): deep-link to QBO invoice; user makes the adjustment in QBO directly.
   - Dashboard tracks completion via a "done" checkbox.

3. **Create a new credit invoice in TMS as a negative value, issue, sync.**
   - Deep-link to TMS "new invoice" form (or warehouse: deep-link to QBO new invoice).
   - User enters the same overpaid amount as a NEGATIVE value, issues, syncs with QuickBooks.
   - This is the credit invoice that will land as a negative-balance row in tomorrow's AR.
   - Dashboard tracks completion via a "done" checkbox.

4. **Memo ready to paste + persist locally.**
   - Modal displays the memo text auto-formatted as: `Overpaid MM/DD/YYYY #{check_or_ach} for {original_invoice}` (e.g. `Overpaid 06/01/2026 #A0906015834 for LM26030031F`). Locked format per Jihyun 2026-06-02.
   - Copy-to-clipboard button — user pastes into the new credit invoice's memo field in TMS/QBO.
   - Dashboard writes the credit memo row into `ar_manual_entries` (so tomorrow's build doesn't double-count it).

**Real-world example referenced by Jihyun:** container MRKU8294420 / invoice PM25080065F (2026-08 cycle).

**Cross-tool integration:** Steps 2 + 3 use existing TMS deep-link infrastructure (same as Invoice Sender → TMS Document tab). Step 4 writes to Supabase. No QBO API call required from the dashboard — the QBO sync in steps 2 + 3 happens via TMS's existing QuickBooks sync, or (for warehouse) the user does it in QBO directly.

---

## 7. Forward Architecture — Management Q&A (deferred to R3+)

The AR Dashboard becomes the **first tool to write into a unified Supabase schema** that eventually serves the entire NGL toolset. Once 2+ tools are writing data, management gets an "Ask AI" interface that can ask cross-tool questions.

### 7.1 Vision

```
            ┌─────────────────────┐
            │ Management Q&A UI   │   admin-role-gated, mobile-friendly
            │  ("Ask AI")         │
            └──────────┬──────────┘
                       │
            ┌──────────▼──────────┐
            │  Claude API +       │   read-only SQL generation,
            │  schema-aware       │   answers cite underlying rows
            │  prompt             │
            └──────────┬──────────┘
                       │
            ┌──────────▼──────────┐
            │      Supabase       │
            │  (unified schema)   │
            └──────────▲──────────┘
                       │
       ┌──────────┬────┴────┬──────────┬──────────┐
       │          │         │          │          │
  ┌────▼────┐ ┌──▼────┐ ┌──▼─────┐ ┌──▼─────┐ ┌──▼──────┐
  │ Merge   │ │ Inv   │ │ Cust   │ │ AR     │ │ Future  │
  │ Tool    │ │ Sender│ │ Mgr    │ │ Dash   │ │ Tools   │
  └─────────┘ └───────┘ └────────┘ └────────┘ └─────────┘
```

### 7.2 Sequencing

| Phase | When | What |
|---|---|---|
| **Phase 1** — Schema design | During AR Dashboard R1 (R/W from day 1) | Define canonical `ar_invoices`, `ar_payments`, `ar_exceptions`, `ar_daily_snapshots`, `ar_manual_entries`, `ar_row_overrides`, `ar_memos` |
| **Phase 2** — Wire other tools | Incremental (Merge Tool, Invoice Sender) | Each tool writes events to Supabase (`send_log`, `merge_log`) |
| **Phase 3** — Q&A UI | After 2+ tools writing | Admin-only "Ask AI" tab + Claude API + schema-aware prompt |
| **Phase 4** — BI dashboards | Optional | Pre-canned reports for common questions |

### 7.3 Guardrails

- **Read-only access for the LLM** — Supabase RLS grants the management role `SELECT` only; no UPDATE/DELETE possible
- **Always cite underlying rows** — LLM answers must include the supporting data, never just a natural-language claim, to prevent hallucinated aggregates
- **Schema-aware prompt** — give Claude full table/column descriptions + example queries + safe-query examples

### 7.4 Cost

- Supabase free tier handles years of AR data (well under 500MB)
- Claude API ~$0.01–0.05 per management question; figure $50–200/month at heavy use

---

## 8. Workflow Cadence (clarified with co-worker)

**Workbook dating:** named for the **activity day**, built the morning **after**.

| Day | Build day | Workbook produced | QBO Collection range |
|---|---|---|---|
| Mon | Mon morning | `AR_AGING_Fri_DD` (Friday's workbook) | Fri-Mon multi-day range |
| Tue | Tue morning | `AR_AGING_Mon_DD` (Monday's workbook) | Mon single day (refreshed view) |
| Wed-Fri | Next morning | `AR_AGING_yesterday` | Yesterday single day |
| Day after public holiday | Next business day morning | Workbook for the last business day before the holiday | Multi-day range covering the holiday |

Sat/Sun **and public holidays**: no build (rolls into next business day's pull). Confirmed by Jihyun 2026-06-01.

**Aging cadence:** delta = calendar days between consecutive build days. Friday workbook → 3-day bump (built Monday). **Post-holiday workbook → bump by N days = number of skipped calendar days.** All other days → 1-day bump.

---

## 9. Open Questions

### Resolved 2026-06-01 (via Jihyun)

- ✅ **Memo source (e.g. `001260514`).** Customer (MSC in observed case) sends back their own invoice number for approved invoices; Jihyun records it in the memo field as proof of approval. They previously printed it on the remittance but recently switched back to NGL's invoice numbers. **Not a 6th data source — just per-customer correspondence captured manually via per-row memo input (§5.8).**
- ✅ **Multi-day pull cadence.** Confirmed Mon Fri-to-Mon pull pattern; extended to all public holidays.
- ✅ **TAB BANK Pmt Type / amount columns ignored.** Reconfirmed: source of truth = AR balance + actual deposit amount. TAB BANK's classification flags and amount columns are unreliable.
- ✅ **TAB BANK posting errors happen.** Real-world examples documented (CHK 65252 → 65282 reassignment, multi-row check duplications). Workflow added (§5.4 cat 9 + §6 Email TAB BANK action).
- ✅ **UC reclassification timing.** Same check# appears on later date as Payment; dashboard links by check#+customer+amount (§4.4 + §5.4 cat 10).
- ✅ **Overpayment workflow.** Documented as 4-step guided modal (§6.1).
- ✅ **Manual edits requirement.** Inline editing on every AR row with day-over-day persistence (§5.8).

### Resolved 2026-06-02 (Jihyun's follow-up answers)

- ✅ **Q1 — TMS vs QBO amount disagreement.** "I use the most recently updated amount as the reference. Since rate is usually adjusted in TMS, TMS is generally the standard (except for warehouses)." → Default = TMS. Warehouse invoices have no TMS work orders (QBO-only), so the disagreement category never fires for them. (§5.4 cat 5 + cat 7 updated.)
- ✅ **Q2 — Write-offs.** "This decision is made by the manager Elly. I usually just receive the final result from Elly and then update the AR accordingly." → No dedicated workflow needed. M4 inline edit on `ar_status` field is sufficient; `WRITE_OFF` rows hide from active worklists but remain in the workbook for audit.
- ✅ **Q3 — Overpayment process** is FULLY MANUAL: Jihyun adds the overpaid amount to the original invoice in TMS, reissues, syncs to QBO. THEN creates a new credit invoice in TMS with the same amount as a negative value, issues, syncs to QBO. Dashboard guides via 4-step checklist with deep-links — no automation. (§6.1 rewritten.)
- ✅ **Q4 — Overpayment memo format.** `Overpaid MM/DD/YYYY #{check} for {inv}` (e.g. `Overpaid 06/01/2026 #A0906015834 for LM26030031F`). Note the original "OVER PAY · CHK#..." sample was incorrect. (§6.1 Step 4 updated.)
- ✅ **Q5 — UC reclassification display.** Small visible pill on the new Payment row showing where it came from (e.g. `↩ from UC 4/30`), NOT text in the memo column. (§5.4 cat 10 updated.)

### Dropped 2026-06-01

- ~~"Copy TAB BANK report" cross-tool action~~ — was a phantom feature from the original brainstorm. Confirmed there is no daily summary report Jihyun sends back to TAB BANK. The "TAB BANK report" they receive each morning IS the Collection_Payment.xlsx remittance file, which is an INPUT we already handle (Phase 5 reconciliation + TAB BANK source drop).

### Still open

1. **"Unaccounted for" rows.** Partially answered. Confirmed she wants flag + edit. The specific `PM26050241F` case is per-customer memo (resolved by §5.8). But should the dashboard *automatically* flag rows that don't appear in any source file (vs leaving them silent under "manual entry")? Defer until a real case surfaces in production.
2. **Management Q&A use cases** — what specific questions does management actually want to ask? Pending management input before R4.

---

## 10. Implementation Sequencing

| Release | Scope | Estimated effort |
|---|---|---|
| **R1** | Dashboard reads existing workbook · Reconciliation cockpit · Exceptions worklist (10 categories incl. TAB BANK errors + UC reclassification) · Today + Week views · Cross-tool actions (email customer, email TAB BANK on error, open in QBO) · **Inline editing + per-row memo persistence + Overpayment Workflow modal** · Supabase R/W (customers + edits + memos + manual entries + resolved exceptions) | Largest piece — full UI + Supabase R/W layer |
| **R2** | Build engine · QBO API auto-fetch · Manual drops · Preview/save flow · Daily snapshot writes to Supabase · Build respects R1 inline edits + memos as overrides | Medium — pipeline already verified |
| **R3** | Month/Quarter/Year views · Per-customer history pages · Older-invoices write-off workflow polish · Manual entries UI polish | Medium |
| **R4** | Management "Ask AI" tab · Multi-tool Supabase event writes (Merge, Invoice Sender) · Schema-aware Claude prompt | Larger — strategic initiative |

Each release ships independently. R1 standalone is already useful (replaces lookup + surfaces exceptions + lets her FIX them in place). R2 unlocks the one-click morning build. R3 + R4 unlock the management-intelligence layer.

---

## 11. Risks & Considerations

- **Data privacy** — management Q&A reveals customer-level data; confirm with leadership what they're allowed to see.
- **LLM hallucination** — strong guardrails required; answers must always cite the underlying data rows.
- **Schema drift** — as tools evolve, the canonical schema needs disciplined migrations.
- **Tool adoption** — Supabase writes must be a hard part of each tool, not optional, or the Q&A layer is useless.
- **Workbook compatibility** — schema for the auxiliary sheets (COL, COL (INV), TMS, ADJUSTMENT) is reverse-engineered from observed workbooks; needs confirmation that future variations don't break the parser.
- **Override drift** — R1's inline-edit overrides + memos must survive R2's build engine without producing stale ghosts. Need a clear lifecycle for when an override expires (e.g., once the underlying invoice is paid off / removed).

---

## 12. Acceptance Criteria

**Release 1:**

- [ ] Dashboard loads any well-formed `AR_AGING_*.xlsx` workbook
- [ ] All 10 tabs render with real data
- [ ] Exceptions worklist correctly classifies rows into the 10 categories
- [ ] Period selector switches Today / Week and updates all KPIs
- [ ] Two-pane split works on every list tab with sensible empty states
- [ ] **Inline edits on AR rows persist day-over-day** (survive a same-workbook re-load AND the R2 rebuild)
- [ ] **Per-row memos persist** across sessions (load from Supabase on workbook load)
- [ ] **Overpayment Workflow modal** completes all 4 steps and writes credit to `ar_manual_entries`
- [ ] **TAB BANK posting error category** surfaces wrong-check# / duplicated rows with a paste-ready email body
- [ ] **UC reclassification linkage** clears prior-day UC rows when matching Payment row appears (or marks for manual confirm — depending on Q7)

**Release 2:**

- [ ] Build engine reproduces hand-built workbooks at ≥99% match on clean single-day cycles
- [ ] Build modal flow works (drops, fetches, preview, save)
- [ ] Daily snapshots persist to Supabase
- [ ] Auto-locate finds yesterday's workbook with override path
- [ ] R1 inline edits + memos survive rebuild (override layer respected)

**Release 3+:**

- [ ] Month/Quarter/Year aggregates compute correctly from historical workbooks
- [ ] Management "Ask AI" tab returns accurate answers with citations
- [ ] Other tools (Merge, Invoice Sender) writing to unified schema
