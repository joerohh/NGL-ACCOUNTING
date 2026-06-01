# AR Dashboard — Design

**Date:** 2026-05-20
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

### Release 1 — Dashboard (ships first)

- Loads a pre-built workbook (created by hand or by the build engine)
- Reconciliation cockpit (Summary tab + Exceptions worklist)
- All view tabs (AR Register, Collections, Overdue, Partial Pays, New Invoices, TMS, Adjustments, Suspense, Customers)
- Multi-period views: Today + Week (Month/Quarter/Year deferred to R3)
- Cross-tool actions: Email customer (jumps to Invoice Sender), Copy TAB BANK report

### Release 2 — Build Engine (ships next)

- Auto-fetch QBO Daily Collection + Daily Schedule via QBO REST API
- Manual drops for TAB BANK Remittance + TMS Reconcile
- Auto-locate yesterday's workbook
- Build engine with preview + save
- Writes daily snapshots to Supabase (unified data layer foundation)

### Release 3 — Insights & Management Q&A (later)

- Week/Month/Quarter/Year views
- Per-customer history pages
- "Ask AI" tab for management Q&A
- Older-invoices write-off workflow

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
  ar-dashboard-actions.js  copy-to-clipboard, email-jumps, mark-resolved
  ar-dashboard-supabase.js Supabase sync (R2)
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
```

### Shared data model (in-memory)

```js
{
  today_date, yesterday_date,
  ar_register: [{ inv, customer, customer_id, amount, paid, balance, aging,
                  aging_bucket, date, ref, mbl, equipment, wo, memo, status,
                  prev_balance, prev_paid, manual_flag }, ...],
  collections: [{ payment_date, check_no, customer_id, customer, invoices: [...] }],
  schedule: [...],
  tms_rows: [...],
  adjustments: [...],
  suspense: [{ check_no, debtor_name, amount, candidate_customers: [...] }],
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

**Phase 1 — Carry forward.** Clone yesterday's AR register as starting state.

**Phase 2 — Apply yesterday's collections.** For each QBO Daily Collection invoice line:

```
paid     = QBO Amount - QBO Open Balance
balance  = invoice_amount - new_paid
if balance ~= 0   -> drop row (full pay)
if balance < 0    -> drop row (overpay, handled separately)
if balance > 0    -> keep, update PAID + BALANCE + MEMO "Short paid MM/DD/YYYY #check#"
```

**Critical rule:** payment classification uses **QBO arithmetic only**. TAB BANK `Pmt Type` column is **IGNORED** because TAB BANK only knows pre-revision invoice amounts — its Shortpay/Overpay flags are wrong whenever an invoice has been revised.

**Phase 3 — Add new invoices from Schedule.** For each row in QBO Daily Schedule, add to AR with `PAID = 0`, `AGING = 0`, `AR_STATUS = "A.0~29"`.

**Phase 4 — Age all rows.** Aging delta = calendar days between consecutive **build days** (not workbook dates):

```python
def build_day(workbook_date):
    if workbook_date.weekday() == 4:  # Friday workbook
        return workbook_date + timedelta(days=3)   # built following Monday
    return workbook_date + timedelta(days=1)        # built next day

aging_delta = (build_day(today) - build_day(yesterday)).days
```

**Friday workbooks bump aging by 3 (built Monday); all others by 1.**

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

- **Multi-day Monday pull.** On Monday she pulls a 4-day QBO Daily Collection range (Fri-Sat-Sun-Mon). Algorithm processes the entire range without filtering. Mon's portion gets re-fetched on Tuesday for the Mon workbook (single-day "in case of late updates").
- **Warehouse invoices.** Not in TMS Reconcile. They appear in QBO Schedule and get added to AR via Phase 3; no ADJUSTMENT comparison. Manual entry path also exists for invoices that never get into QBO Schedule.
- **TMS data errors.** Detected in Phase 5 by comparing TMS `TOTAL_AMT` vs QBO Schedule amount. Any discrepancy flagged in Exceptions worklist — no auto-threshold, defer to human judgment.
- **NON-FACTORED customers.** TAB BANK shows their checks as `SUSPENSE / NON-FACTORED`. We don't use TAB BANK's customer matching for these; QBO Collection's customer field is authoritative. Detected by `DESC = "NON-FACTORED"` tag.

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

Remaining ~0.05–7% delta is consistently **manual touches** (TMS data error sanity-checks, manual memo entries, customer-requested adjustments). The dashboard's role is to surface these for review, not to reproduce them deterministically.

Verification script: `tools/verify_ar_build.py`. Read-only, runs against all build folders.

---

## 5. UI Structure

### 5.1 The dashboard is a reconciliation cockpit

The associate's job is **balancing the books on the receiving side**. The UI's primary surface is the **Exceptions worklist** — every mismatch the algorithm could detect between TAB BANK, QBO Collection, QBO Schedule, TMS, and the AR register. Each row is a task she works to closed.

### 5.2 Tabs

| Tab | Purpose |
|---|---|
| **Summary** | Reconciliation cockpit. Exceptions worklist top, KPIs strip, aging bar, period selector |
| **AR Register** | Full register lookup (sortable, filterable, searchable) |
| **Collections** | What left the AR yesterday (grouped by check#, with per-check reconciliation check) |
| **Overdue** | Call list — AR rows with balance > 0 and aging > 30, sorted oldest first |
| **Partial Pays** | Open invoices with PAID > 0 (the short-pay survivors) |
| **New Invoices** | Today's Schedule additions |
| **TMS** | TMS Reconcile rows that matched (no adjustment) |
| **Adjustments** | TMS-driven amount changes |
| **Suspense** | TAB BANK Unapplied Cash worklist |
| **Customers** | Per-customer rollup with drill-in |

### 5.3 Multi-period views

Time-range selector at top of Summary: `[Today] [Week] [Month] [Quarter] [Year]`

Switching changes the KPIs, activity feed, aging context — but **Exceptions worklist always stays at "today"** (that's the daily work).

Release 1 ships with Today + Week only. Month/Quarter/Year defer to Release 3 once enough historical workbooks exist.

### 5.4 Exceptions worklist categories

| # | Category | Trigger | Action affordances |
|---|---|---|---|
| 1 | Bank suspense | TAB BANK Unapplied Cash row, debtor name not in customer DB | Match to customer + paste-ready snippet for TAB BANK portal |
| 2 | Short pays | Open Balance > 0 after posting | Call/email customer · write-off · keep open |
| 3 | Over pays | Open Balance < 0 after posting | Apply to another invoice · create credit memo |
| 4 | Posting gaps | TAB BANK has check# but QBO has nothing posted for it (or vice versa) | Post in QBO · un-apply · hold |
| 5 | Amount disagreements | TMS amount ≠ QBO Schedule amount (new); OR TMS amount ≠ yesterday's AR (old) | Show both, default to TMS, confirm or override |
| 6 | Customer name mismatches | TAB BANK debtor name ≠ QBO customer for same check# | Confirm correct customer |
| 7 | Missing TMS records | Invoice in QBO Schedule but not in TMS (warehouse?) | Add manually with custom amount · confirm warehouse |
| 8 | NON-FACTORED informational | TAB BANK SUSPENSE/NON-FACTORED tag | Collapsed by default; no action needed |

### 5.5 Two-pane split (list tabs)

Every list tab uses a 2-pane layout: table left + detail panel right (`grid-template-columns: 1fr 320-340px`). When no row is selected, the panel shows a useful "Today summary" empty state with quick actions (e.g., "Copy TAB BANK report") — never a "select a row" placeholder.

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

Mockups will need a refresh after this spec to lead with the Exceptions worklist (currently they emphasize KPI strip — the spec inverts that priority).

---

## 6. Cross-Tool Actions

| Action | Where | What it does |
|---|---|---|
| Copy TAB BANK report | Summary quick actions + Collections header | Generates daily summary she pastes back to TAB BANK portal (format TBD with co-worker) |
| Email customer | Overdue tab row action | Jumps to Invoice Sender pre-filled with customer + overdue invoices |
| Open invoice in QBO | Per-invoice row action | Deep link to QBO via existing QBO REST API integration |
| Add manual entry | AR Register + Exceptions | Add credit memo / warehouse / custom row; persists day-over-day |
| Mark suspense resolved | Suspense tab row action | Flag in Supabase so it doesn't reappear tomorrow |
| Rebuild today | Settings + data bar | Re-fetch sources, rebuild workbook (same-day overwrite) |

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
| **Phase 1** — Schema design | During AR Dashboard R2 | Define canonical `ar_invoices`, `ar_payments`, `ar_exceptions`, `ar_daily_snapshots`, `ar_manual_entries` |
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
| Wed-Fri | next morning | `AR_AGING_yesterday` | Yesterday single day |

Sat/Sun: no build (rolls into Monday's pull).

**Aging cadence:** delta = calendar days between consecutive build days. Friday workbook → 3-day bump (built Monday). All others → 1-day bump.

---

## 9. Open Questions

Items pending co-worker / management confirmation, not blocking implementation start:

1. **TAB BANK report format** — exact columns + grouping for the "Copy TAB BANK report" action. Pending co-worker (off today).
2. **Memo source** — on 5/18, 7 invoices got memo `"001260514"` that isn't in any source file. Suspected manual entry from a 6th source (TMS portal? customer email?). Pending co-worker.
3. **TMS data error workflow** — when TMS `TOTAL_AMT` differs from QBO Schedule, what determines the "real" amount? Customer's usual rate? Manual gut-check? Pending co-worker.
4. **"Unaccounted for" rows** — should the dashboard flag AR rows that don't appear in any source file (e.g., the `PM26050241F` credit memo from manual entry several days back)? Pending co-worker.
5. **Manual write-offs** — does she ever write off uncollectible invoices, and where? Pending co-worker.
6. **Management Q&A use cases** — what specific questions does management actually want to ask? Pending management input before Phase 3 of forward architecture.

---

## 10. Implementation Sequencing

| Release | Scope | Estimated effort |
|---|---|---|
| **R1** | Dashboard reads existing workbook · Reconciliation cockpit · Exceptions worklist · Today + Week views · Cross-tool actions (email, copy TAB BANK report) · Supabase reads for customer matching | Largest piece — most of the UI |
| **R2** | Build engine · QBO API auto-fetch · Manual drops · Preview/save flow · Daily snapshot writes to Supabase | Smaller — pipeline already verified |
| **R3** | Month/Quarter/Year views · Per-customer history pages · Older-invoices write-off workflow · Manual entries UI polish | Medium |
| **R4** | Management "Ask AI" tab · Multi-tool Supabase event writes (Merge, Invoice Sender) · Schema-aware Claude prompt | Larger — strategic initiative |

Each release ships independently. R1 standalone is already useful (saves time on lookup + surfacing exceptions). R2 unlocks the workflow automation win. R3 + R4 unlock the management-intelligence layer.

---

## 11. Risks & Considerations

- **Data privacy** — management Q&A reveals customer-level data; confirm with leadership what they're allowed to see.
- **LLM hallucination** — strong guardrails required; answers must always cite the underlying data rows.
- **Schema drift** — as tools evolve, the canonical schema needs disciplined migrations.
- **Tool adoption** — Supabase writes must be a hard part of each tool, not optional, or the Q&A layer is useless.
- **Workbook compatibility** — schema for the auxiliary sheets (COL, COL (INV), TMS, ADJUSTMENT) is reverse-engineered from observed workbooks; needs confirmation that future variations don't break the parser.

---

## 12. Acceptance Criteria

**Release 1:**

- [ ] Dashboard loads any well-formed `AR_AGING_*.xlsx` workbook
- [ ] All 9 tabs render with real data
- [ ] Exceptions worklist correctly classifies rows into the 8 categories
- [ ] "Copy TAB BANK report" produces a valid clipboard string
- [ ] Period selector switches Today / Week and updates all KPIs
- [ ] Two-pane split works on every list tab with sensible empty states

**Release 2:**

- [ ] Build engine reproduces hand-built workbooks at ≥99% match on clean single-day cycles
- [ ] Build modal flow works (drops, fetches, preview, save)
- [ ] Daily snapshots persist to Supabase
- [ ] Auto-locate finds yesterday's workbook with override path

**Release 3+:**

- [ ] Month/Quarter/Year aggregates compute correctly from historical workbooks
- [ ] Management "Ask AI" tab returns accurate answers with citations
- [ ] Other tools (Merge, Invoice Sender) writing to unified schema
