# AR Dashboard R2 — Build Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **For Claude:** This plan is governed by `feedback_ar_correctness_critical.md` in memory — 99–100% correctness is non-negotiable. Every milestone gates on numerical verification against the 7 reference workbooks. No "looks right" — must be proven.

**Goal:** Replace Jihyun's ~3-4 hour/morning manual AR aging workbook build with a single-click in-browser pipeline that ingests 5 source files and produces today's `AR_AGING_*.xlsx` with the same 99%+ accuracy as her hand-built workbooks. The existing R1 read-only dashboard work folds in as Milestone 5 (the cockpit on top).

**Spec:** `docs/superpowers/specs/2026-05-20-ar-dashboard-design.md` (updated 2026-06-01 with Jihyun's input)

**Reframe note (2026-06-01):** The original R1 (read-only dashboard) shipped as v2.75.x but the user determined that without the build engine, R1 alone is just a fancy Excel viewer. R2 (this plan) IS the project. R1's UI work (10 tabs, exception detection, Summary cockpit) is the layer that wraps around R2's output.

**Scope explicitly in R2:**
- 6-phase build pipeline ported from `tools/verify_ar_build.py` to JavaScript
- 5-input drop UI (yesterday's workbook + 2 QBO + TAB BANK + TMS Reconcile)
- Summary preview before save (counts of new / paid off / amount changed / exceptions)
- Edit-in-place on the preview (amount / paid / balance / memo / status)
- Save to a remembered folder with auto-named filename `AR_AGING_MM_DD_YYYY.xlsx`
- Edge-case detection: multi-day pulls, TAB BANK posting errors (flag only — no auto-email per 2026-06-01 user direction), UC reclassification linkage, overpayment workflow
- Integration with the existing R1 cockpit (Summary + tabs + Exceptions worklist)

**Scope explicitly NOT in R2 (deferred):**
- Supabase R/W (no cloud day 1 — workbook is source of truth, memos carry forward via Phase 1)
- Multi-user / cross-machine sync
- Audit trail of who-edited-what
- "Full workbook viewer" preview mode (user picked summary-only for now)
- Auto-email to TAB BANK on posting errors (detection stays; email deferred)
- Auto-fetch QBO files via API (M5 nice-to-have, not blocking)

**Rollout:** Bundled into v2.76.0+. Standard ship pipeline: bump VERSION → `runbuild.bat` → commit + push → `gh release create` with installer + `latest.yml`. Admin-gated until proven on a real production week.

---

## Correctness Requirements (read first)

Governs every milestone. Cannot ship a milestone without all of these:

1. **Per-milestone verification.** Outputs compared cell-by-cell against the 7 reference workbooks (`tools/verify_ar_build.py` is the reference). Single-day cycles: ≥99% match. Multi-day Mondays: ≥93% match (residual = manual touches, surfaced as exceptions, not silent diffs).
2. **Every exception detector has test cases.** Both the happy-path trigger and edge cases (null balance, zero balance, near-zero floating-point noise, missing columns).
3. **"It looks right" is not acceptable.** Must be proven numerically. If we can't verify against real data, it doesn't ship.
4. **Surface unknowns, don't silence them.** If the engine can't classify confidently, the row goes to the worklist as "needs review" — never default-resolve.
5. **No silent skips.** Malformed source file → build aborts with a clear error pointing Jihyun at what's wrong. Don't proceed with partial data.
6. **Verification gates rollout.** Don't move to the next milestone until the current one passes its match-rate target on real test workbooks.

---

## Reference Test Data

Located in `C:/Users/Joseph/Desktop/NGL ACCOUNTING SERVICE - TEST DATA/AR_AGING_assets/`:

| Folder | Inputs | Hand-built target |
|---|---|---|
| `build-2026-05-11` | 5/8 sources | `AR_AGING_05_08_2026.xlsx` |
| `build-2026-05-12` | 5/11 sources | `AR_AGING_05_11_2026.xlsx` |
| `build-2026-05-13` | 5/12 sources | `AR_AGING_05_12_2026.xlsx` |
| `build-2026-05-14` | 5/13 sources | `AR_AGING_05_13_2026.xlsx` |
| `build-2026-05-15` | 5/14 sources | `AR_AGING_05_14_2026.xlsx` |
| `5.18` | 5/18 sources | `AR_AGING_05_18_2026.xlsx` |
| `5/20 data (5/5.19/` | 5/19 sources | `AR_AGING_05_19_2026.xlsx` |

These were used to verify the Python pipeline at 93.76–99.95% match. JS port must match these numbers.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `app/assets/js/tools/ar-dashboard/ar-dashboard-build.js` | Build engine — 6-phase pipeline, the heart of R2 | **Create** |
| `app/assets/js/tools/ar-dashboard/ar-dashboard-build-loader.js` | Parse the 5 source xlsx files into normalized in-memory shapes | **Create** |
| `app/assets/js/tools/ar-dashboard/ar-dashboard-build-writer.js` | Write the output workbook (7 sheets, match Jihyun's format) | **Create** |
| `app/assets/js/tools/ar-dashboard/ar-dashboard-build-verify.js` | Optional dev-mode comparison vs a hand-built reference workbook | **Create** |
| `app/assets/js/tools/ar-dashboard/ar-dashboard-build-ui.js` | 5-drop modal + preview screen + edit-in-place + save flow | **Create** |
| `app/assets/js/tools/ar-dashboard/ar-dashboard-overpayment.js` | Overpayment workflow modal (4-step guided process) | **Create** |
| `app/assets/js/tools/ar-dashboard/ar-dashboard.js` | Add "Build today's workbook" CTA when no workbook loaded | Modify |
| `app/assets/js/tools/ar-dashboard/ar-dashboard-views.js` | Wire edit affordances + overpayment modal trigger into existing tabs | Modify |
| `app/assets/js/tools/ar-dashboard/ar-dashboard-exceptions.js` | Add detectors for TAB BANK posting errors + UC reclassification (cats 9 + 10 from spec) | Modify |
| `app/assets/css/styles.css` | Build modal, preview screen, edit-cell, overpayment modal styles | Modify |
| `app/assets/js/shared/state.js` | Extend `arState` with build-flow fields (`buildState`, `previewModel`, `pendingEdits`) | Modify |

---

## Milestone 1 — POC: prove the pipeline runs in browser

**Goal:** Port the 6-phase build logic from `tools/verify_ar_build.py` to JavaScript. Run it against the 7 reference workbooks. No UI yet — console-only via `window.arBuildToday(filePaths)`. Gate on hitting the same match rates as the Python script.

### Task 1: Create the build engine skeleton + the 6 phase functions

**Files:**
- Create: `app/assets/js/tools/ar-dashboard/ar-dashboard-build.js`
- Create: `app/assets/js/tools/ar-dashboard/ar-dashboard-build-loader.js`

- [ ] **Step 1.1: Skim `tools/verify_ar_build.py` to confirm phase boundaries**
  Read the Python reference. Note where it parses each source file, where each phase function lives, and what it asserts.

- [ ] **Step 1.2: Create `ar-dashboard-build-loader.js` with parsers for all 5 source files**
  Each parser returns a normalized JS object. Shape mirrors what verify_ar_build.py builds in memory.
  - `parseYesterdaysWorkbook(file)` → reuses the existing loader from R1 (`ar-dashboard-loader.js`)
  - `parseQboDailyCollection(file)` → returns `{ rows: [...] }` (one row per Invoice / Payment line)
  - `parseQboDailySchedule(file)` → returns `{ rows: [...] }`
  - `parseTabBankRemittance(file)` → returns `{ rows: [...] }`
  - `parseTmsReconcile(file)` → returns `{ rows: [...] }`
  - Each parser **aborts loudly** on malformed input. No silent skips.

- [ ] **Step 1.3: Create `ar-dashboard-build.js` with the 6-phase pipeline**
  - `arBuildToday({ yesterday_workbook, qbo_collection, qbo_schedule, tab_bank, tms_reconcile })` returns the new model.
  - Phase 1: clone yesterday's AR register
  - Phase 2: apply QBO collections (QBO arithmetic only — ignore TAB BANK Pmt Type / amount columns per spec §4.3)
  - Phase 3: add new invoices from Schedule
  - Phase 4: age all rows by `(today_build_date - yesterday_build_date)` calendar days
  - Phase 5: TMS reconciliation (NEW invoices: TMS TOTAL_AMT = settled amount; OLD invoices: TMS TOTAL_AMT = delta)
  - Phase 6: build auxiliary output sheets (AR_<today>, AR_<yesterday>, COL, COL (INV), Schedule, TMS, ADJUSTMENT)

- [ ] **Step 1.4: Expose `window.arBuildToday` for console testing**
  Takes the 5 input objects, returns the built model. Console-only — no UI yet.

### Task 2: Verification harness against the 7 reference workbooks

**Files:**
- Create: `app/assets/js/tools/ar-dashboard/ar-dashboard-build-verify.js`

- [ ] **Step 2.1: Port the cell-by-cell comparison logic from `verify_ar_build.py` to JS**
  Compare two models (JS build output vs hand-built target) cell-by-cell across all 7 sheets. Report:
  - Total rows compared
  - Cells that match
  - Cells that differ (with row + column + expected vs actual)
  - Overall match percentage

- [ ] **Step 2.2: Expose `window.arVerifyBuild(builtModel, referenceModel)` for console testing**

- [ ] **Step 2.3: Verification dry-run — run against each of the 7 test folders**
  Load the inputs for each folder, run `arBuildToday`, compare against the hand-built target, log the match percentage. Target match rates:
  - 2026-05-11 (Mon): ≥99%
  - 2026-05-12 (Tue): ≥99%
  - 2026-05-13 (Wed): ≥99%
  - 2026-05-14 (Thu): ≥99%
  - 2026-05-18 (Mon, multi-day): ≥93%
  - 2026-05-19 (Tue): ≥99%

- [ ] **Step 2.4: If any cycle fails the target, debug and fix the pipeline before moving to M2**
  This is the gate. Don't proceed until the match rates are hit.

- [ ] **Step 2.5: Commit M1**
  ```bash
  git add app/assets/js/tools/ar-dashboard/ar-dashboard-build.js \
          app/assets/js/tools/ar-dashboard/ar-dashboard-build-loader.js \
          app/assets/js/tools/ar-dashboard/ar-dashboard-build-verify.js
  git commit -m "feat(ar-dashboard): M1 build engine POC — 6-phase pipeline + verification harness"
  ```

---

## Milestone 2 — 5-drop input UI + summary preview + save

**Goal:** Wrap the verified pipeline from M1 in a usable UI. She drops 5 files, sees a summary of changes, edits anything wrong, hits Save. Output workbook lands in her remembered folder, named with today's date.

### Task 3: Build modal — 5-drop input form

**Files:**
- Create: `app/assets/js/tools/ar-dashboard/ar-dashboard-build-ui.js`
- Modify: `app/assets/js/tools/ar-dashboard/ar-dashboard.js` (CTA on empty state)
- Modify: `app/assets/css/styles.css`

- [ ] **Step 3.1: Add "Build today's workbook" CTA to the empty state**
  When no workbook is loaded, the empty state shows a second option below the existing drop zone: "Or build today's workbook from scratch →" → opens the build modal.

- [ ] **Step 3.2: Create the build modal with 5 drop zones**
  Each zone shows: expected filename pattern, drop area, parsed-state preview ("✓ 142 rows" or "✗ couldn't parse").
  - Yesterday's workbook (auto-pre-fills from saved folder if found)
  - QBO Daily Collection Report
  - QBO Daily Schedule List
  - TAB BANK Collection_Payment
  - TMS Reconcile (APAR RECONCILE)

- [ ] **Step 3.3: Per-file validation**
  When a file is dropped, run its parser from M1's loader. On success, show ✓ + row count. On failure, show the specific error from the parser. Never silently accept a malformed file.

- [ ] **Step 3.4: "Run build" button (disabled until all 5 files parsed OK)**
  Click → runs `arBuildToday`, transitions modal to preview state.

### Task 4: Summary preview screen

**Files:**
- Modify: `app/assets/js/tools/ar-dashboard/ar-dashboard-build-ui.js`

- [ ] **Step 4.1: Render summary counts**
  After build, replace modal body with summary:
  - `12 new invoices added` (click → list of new INV#s)
  - `8 invoices paid off` (click → list of paid INV#s)
  - `3 amounts adjusted` (click → list of revisions)
  - `5 exceptions to review` (click → jumps to Summary tab worklist)
  - `Today's AR total: $X · Yesterday's: $Y · net change: $Z`

- [ ] **Step 4.2: Click-through detail popovers**
  Each clickable line opens an inline detail showing the actual rows. No save, no edit yet — just visibility.

- [ ] **Step 4.3: "Save" + "Go back" buttons at the bottom**
  - "Go back" → returns to the 5-drop input form (preserves dropped files)
  - "Save workbook" → calls writer module (Task 5), writes the .xlsx to disk, closes modal, loads the dashboard with the new workbook

### Task 5: Output workbook writer

**Files:**
- Create: `app/assets/js/tools/ar-dashboard/ar-dashboard-build-writer.js`

- [ ] **Step 5.1: SheetJS write for all 7 output sheets**
  - `AR_<today>` — new register from phases 1–5
  - `AR_<yesterday>` — yesterday's carried-forward register
  - `COL` — QBO Daily Collection cleaned (band rows → blank rows; col 0 shifted)
  - `COL (INV)` — compacted COL with optional PARTIAL PAID / SHORT PAID tag column
  - `Schedule` — QBO Daily Schedule 1:1
  - `TMS` — TMS rows where amounts matched
  - `ADJUSTMENT` — TMS rows with `TOTAL_AMT != 0`

- [ ] **Step 5.2: Match Jihyun's column widths, header row formatting, sheet order**
  Open one of the reference workbooks in Excel, note widths + formatting. Apply via SheetJS `!cols`, `!rows`, `!merges` as needed. Match exact sheet order.

- [ ] **Step 5.3: Filename + save path**
  - Filename: `AR_AGING_MM_DD_YYYY.xlsx` (today's date)
  - Save folder: remembered from Settings → preferences (default: same folder as yesterday's workbook)

- [ ] **Step 5.4: Verification gate — built workbook re-loaded by the R1 loader produces an identical model**
  Round-trip test: write the workbook, re-parse it with the existing R1 loader, compare the parsed model cell-by-cell to the in-memory build model. Must match 100% (writing and reading are inverses).

- [ ] **Step 5.5: Commit M2**
  ```bash
  git add app/assets/js/tools/ar-dashboard/ar-dashboard-build-ui.js \
          app/assets/js/tools/ar-dashboard/ar-dashboard-build-writer.js \
          app/assets/js/tools/ar-dashboard/ar-dashboard.js \
          app/assets/css/styles.css
  git commit -m "feat(ar-dashboard): M2 — 5-drop UI + summary preview + save"
  ```

---

## Milestone 3 — Edge case hardening

**Goal:** Catch the 5 weird cases that come up in real builds. Each gets a dedicated detector + visible exception in the worklist. **The correctness rule applies hardest here** — these are the cases where money goes missing if we don't catch them.

### Task 6: TAB BANK posting error detection (cat 9)

**Files:**
- Modify: `app/assets/js/tools/ar-dashboard/ar-dashboard-exceptions.js`

- [ ] **Step 6.1: Detector for "same check# on multiple TAB BANK rows for same customer with conflicting status"**
  Per spec §4.4. Real-world example: CHK 65252 appeared 3 times for IDEA NUOVA (Payment + UC + Overpay).

- [ ] **Step 6.2: Detector for "check# applied to invoice where deposit ≠ AR balance"**
  Real-world example: CHK 65252 → 65282 reassignment for LM26030418F.

- [ ] **Step 6.3: Exclude detected error rows from posting until cleared**
  Build engine writes them to a separate "needs TAB BANK fix" pile in the AR register, NOT posted as Payment. Visible in worklist.

- [ ] **Step 6.4: Test cases against test workbooks**
  Find or construct synthetic TAB BANK files with both error patterns. Verify detector fires.

### Task 7: UC reclassification linkage (cat 10)

**Files:**
- Modify: `app/assets/js/tools/ar-dashboard/ar-dashboard-exceptions.js`
- Modify: `app/assets/js/tools/ar-dashboard/ar-dashboard-build.js`

- [ ] **Step 7.1: During build, scan yesterday's UC rows against today's Payment rows**
  Match key: check# + customer + amount (within $0.01).

- [ ] **Step 7.2: When matched, link them**
  - Today's Payment row gets a `linked_from_uc` field with yesterday's UC row reference
  - Yesterday's UC row gets a `linked_to_payment` field
  - Build engine clears the original UC row from today's register (it's been resolved)

- [ ] **Step 7.3: Display = small visible pill on the Payment row (locked 2026-06-02)**
  Render a small chip next to the Payment row (e.g. `↩ from UC 4/30`). NOT text in the memo column. Tooltip on hover shows the original UC row's full detail (date, check#, amount). Yesterday's UC row stays in the workbook but is marked resolved so it drops out of the Suspense worklist.

### Task 8: Overpayment workflow modal (cat 3)

**Files:**
- Create: `app/assets/js/tools/ar-dashboard/ar-dashboard-overpayment.js`
- Modify: `app/assets/js/tools/ar-dashboard/ar-dashboard-views.js`

Per Jihyun's Q3 answer, this is a **guided checklist** for a fully manual process — no automation. She does both invoice updates in TMS herself; the dashboard just routes her, holds her place, and prepares the memo for paste.

- [ ] **Step 8.1: Modal with 4 guided steps**
  - **Step 1 — Confirm overpayment.** Show deposit amount, AR balance, computed overpayment, check#/ACH#, customer, original invoice. User confirms.
  - **Step 2 — Bump original invoice in TMS.** Deep-link to TMS billing page; user adds overpaid amount as positive line, reissues, syncs to QBO. Warehouse path: deep-link to QBO invoice instead (no TMS work order). Done checkbox.
  - **Step 3 — Create new credit invoice in TMS.** Deep-link to TMS new-invoice form; user enters same amount as NEGATIVE value, issues, syncs to QBO. Warehouse path: deep-link to QBO new invoice. Done checkbox.
  - **Step 4 — Memo ready + persist locally.** Display auto-formatted memo, copy-to-clipboard button. Write credit memo row into AR register so tomorrow's build doesn't double-count.

- [ ] **Step 8.2: Wire trigger from Overpays exception in Summary worklist + Over Pays detail pane**

- [ ] **Step 8.3: Memo format (locked 2026-06-02)**
  Format: `Overpaid MM/DD/YYYY #{check_or_ach} for {original_inv}`
  Example: `Overpaid 06/01/2026 #A0906015834 for LM26030031F`

- [ ] **Step 8.4: Warehouse routing**
  Detect warehouse case by absence of TMS work order for the invoice. When warehouse, Step 2 + Step 3 deep-links go to QBO (not TMS).

- [ ] **Step 8.5: Test against the AMNEX example**
  Use PM25080065F → PM25100383F as a test case. Verify the 4 steps produce the same output structure.

### Task 9: Build aborts on malformed input

**Files:**
- Modify: `app/assets/js/tools/ar-dashboard/ar-dashboard-build-loader.js`
- Modify: `app/assets/js/tools/ar-dashboard/ar-dashboard-build-ui.js`

- [ ] **Step 9.1: Per-parser validation rules**
  - Each parser asserts required columns exist
  - Each parser checks row count is plausible (>0 for non-empty files)
  - Each parser detects "this is the wrong file" (e.g., dropped TMS file in the QBO slot)

- [ ] **Step 9.2: Build modal blocks until all 5 inputs parse cleanly**
  Show specific error per failed file. Don't allow "Run build" until all green.

- [ ] **Step 9.3: Commit M3**
  ```bash
  git add app/assets/js/tools/ar-dashboard/ar-dashboard-exceptions.js \
          app/assets/js/tools/ar-dashboard/ar-dashboard-build.js \
          app/assets/js/tools/ar-dashboard/ar-dashboard-overpayment.js \
          app/assets/js/tools/ar-dashboard/ar-dashboard-views.js \
          app/assets/js/tools/ar-dashboard/ar-dashboard-build-loader.js \
          app/assets/js/tools/ar-dashboard/ar-dashboard-build-ui.js
  git commit -m "feat(ar-dashboard): M3 — TAB BANK errors + UC reclass + overpayment workflow + abort-on-malformed"
  ```

---

## Milestone 4 — Edit-in-place on the preview

**Goal:** Jihyun reviews the summary preview, spots something wrong (an amount, a memo, a missing manual row), edits it in place, and the workbook gets saved with her edits baked in. **No Supabase day 1** — workbook is source of truth, edits carry forward via Phase 1's natural register carry-forward.

### Task 10: Click-to-edit on preview detail tables

**Files:**
- Modify: `app/assets/js/tools/ar-dashboard/ar-dashboard-build-ui.js`
- Modify: `app/assets/css/styles.css`

- [ ] **Step 10.1: When she clicks a summary line ("12 new invoices added") and the detail list opens, every cell in the table is editable**
  Editable fields: amount, paid, balance, memo, ar_status.

- [ ] **Step 10.2: Edits update the in-memory build model immediately**
  Visible "edited" indicator (orange pip) on changed rows.

- [ ] **Step 10.3: Edits persist into the saved workbook**
  Writer (Task 5) uses the edited model, not the unedited one.

### Task 11: Add manual entries (credit memos, warehouse rows, custom rows)

**Files:**
- Modify: `app/assets/js/tools/ar-dashboard/ar-dashboard-build-ui.js`

- [ ] **Step 11.1: "Add row" button on the preview**
  Opens a small inline form: customer, INV#, amount, memo. Validates customer exists.

- [ ] **Step 11.2: New row joins the AR register before save**
  Marked as `manual_flag: true` so it's visible as user-added.

- [ ] **Step 11.3: Test scenarios**
  - Add a credit memo with negative amount → confirm it shows in next-morning rebuild
  - Add a warehouse invoice missing from QBO Schedule → confirm it persists

- [ ] **Step 11.4: Commit M4**
  ```bash
  git add app/assets/js/tools/ar-dashboard/ar-dashboard-build-ui.js \
          app/assets/css/styles.css
  git commit -m "feat(ar-dashboard): M4 — edit-in-place + add manual rows before save"
  ```

---

## Milestone 5 — Cockpit integration

**Goal:** The R1 dashboard becomes the cockpit AROUND the build engine. Same UI you've been testing — Summary cockpit, 10 tabs, exception worklist — but now loads fresh-built data from M2 instead of a hand-built workbook.

### Task 12: Wire build flow into the existing dashboard

**Files:**
- Modify: `app/assets/js/tools/ar-dashboard/ar-dashboard.js`
- Modify: `app/assets/js/tools/ar-dashboard/ar-dashboard-views.js`

- [ ] **Step 12.1: After "Save workbook" in the build modal, transition to loaded state**
  Reuse `arRenderLoaded` from R1 with the just-built model.

- [ ] **Step 12.2: Add "Build today's" button to the data bar in loaded state**
  Lets her re-build mid-day if a source file got updated.

- [ ] **Step 12.3: Auto-detect yesterday's workbook on empty state**
  Scan the saved folder for the latest `AR_AGING_*.xlsx`. If found, pre-fill it as the "yesterday's workbook" input.

### Task 13: Update exception detection for build-engine outputs

**Files:**
- Modify: `app/assets/js/tools/ar-dashboard/ar-dashboard-exceptions.js`

- [ ] **Step 13.1: Wire cats 9 (TAB BANK error) + 10 (UC reclassification) into the dispatcher**
  Already implemented in M3. Just confirm they show up in the Summary worklist.

- [ ] **Step 13.2: Verify exception counts match what verify_ar_build.py would report**
  For each test workbook, the JS exception count must match the Python count.

### Task 14: Settings — save folder preference

**Files:**
- Modify: `app/assets/js/tools/settings/settings.js`
- Modify: `app/index.html` (Settings → AR Dashboard section)

- [ ] **Step 14.1: Add "AR Dashboard save folder" field to Settings**
  Folder picker. Default: empty (prompts on first save). Remembered in localStorage.

- [ ] **Step 14.2: Use this preference in M2's writer**

- [ ] **Step 14.3: Commit M5**
  ```bash
  git add app/assets/js/tools/ar-dashboard/ar-dashboard.js \
          app/assets/js/tools/ar-dashboard/ar-dashboard-views.js \
          app/assets/js/tools/ar-dashboard/ar-dashboard-exceptions.js \
          app/assets/js/tools/settings/settings.js \
          app/index.html
  git commit -m "feat(ar-dashboard): M5 — cockpit integration + Settings folder preference"
  ```

---

## Final ship pipeline (after M5 passes verification)

- [ ] Bump VERSION to 2.76.0
- [ ] Run `runbuild.bat` to build the installer
- [ ] Verify installer launches + AR Dashboard loads + build flow works end-to-end
- [ ] Run final verification: rebuild all 7 reference workbooks; confirm match rates hold
- [ ] Test sharing the installer with Jihyun manually (no GH release until she signs off)
- [ ] Jihyun-acceptance gate: she runs the build on 2-3 real morning cycles and confirms output matches what she'd have built by hand
- [ ] Once she signs off, `gh release create v2.76.0` to publish to all users
- [ ] Drop the admin gate so co-workers can use it (separate small commit)

---

## Jihyun's answers (locked 2026-06-02)

All 5 follow-up questions answered. No more pending defaults — these are the real rules:

| Q | Locked answer | Affects |
|---|---|---|
| Q1 TMS vs QBO amount | TMS wins when TMS has the row. Warehouses have no TMS work orders (QBO-only) so the comparison never fires for them. | Engine already correct — no change needed |
| Q2 Write-off process | No dedicated workflow. Elly decides; Jihyun manually updates `ar_status` to `WRITE_OFF` via inline edit. Rows hide from active worklists, stay for audit. | M4 Task 10 + filter rule |
| Q3 Overpayment process | FULLY MANUAL: bump original invoice in TMS → reissue → QBO sync; then create new credit invoice in TMS as negative → issue → QBO sync. Dashboard guides via 4-step checklist, no automation. | M3 Task 8 — see updated steps below |
| Q4 Overpayment memo | `Overpaid MM/DD/YYYY #{check} for {inv}` (e.g. `Overpaid 06/01/2026 #A0906015834 for LM26030031F`). Note: the original "OVER PAY · CHK#..." sample was wrong. | M3 Task 8 Step 8.3 |
| Q5 UC reclass display | Small visible pill on the Payment row (e.g. `↩ from UC 4/30`), NOT memo text. | M3 Task 7 Step 7.3 |

---

## Risks

1. **Output workbook format mismatch.** SheetJS may not perfectly reproduce Jihyun's column widths, band-row blanks, and header formatting. Mitigation: Task 5 Step 2 explicitly compares formatting; iterate until she signs off.
2. **In-browser performance with 5 large files.** The 5/19 workbook had 4149 rows. Parsing 5 files of that size in the browser may stutter. Mitigation: profile in M1; consider Web Worker if needed (project already has worker infra in the Merge tool).
3. **Hand-built workbooks may have inconsistencies between days.** Verification target rates are based on the Python script's observed numbers — but Jihyun's hand-build process may have changed over time. Mitigation: when verification falls below target, inspect the diff; if the residual is "Jihyun did something the script doesn't model," flag as exception rather than chasing perfection.
4. **TAB BANK error patterns may be more varied than the 2 we know about.** Real production data may surface a third or fourth glitch pattern. Mitigation: Task 6 Step 4 includes constructed test cases; in production, anything we don't recognize goes to a generic "TAB BANK row needs review" bucket.

---

## Self-Review Notes

- **Spec coverage:** §1–§12 of the AR Dashboard design spec are all reflected. The build engine pipeline (§4.3) maps to M1. The 5-input flow (§4.1) maps to M2. The edge cases (§4.4) map to M3. Inline editing (§5.8) maps to M4. The 10-tab cockpit (§5) maps to M5.
- **Correctness rule enforced:** Every milestone has explicit verification before the next can start. M1 must hit match rates against all 7 test workbooks. M2 round-trips the writer through the reader. M3 has test cases. M4 has scenario tests. M5 confirms exception counts match Python.
- **No silent skips anywhere.** Parsers abort loudly. Detectors surface unknowns to the worklist.
- **Jihyun dependencies documented.** Each open question has a default + clear "what changes if her answer differs" note.
- **Scope explicitly bounded.** Supabase, multi-user, audit trail, full-workbook preview, auto-email, auto-fetch QBO — all explicitly out of R2 scope.
