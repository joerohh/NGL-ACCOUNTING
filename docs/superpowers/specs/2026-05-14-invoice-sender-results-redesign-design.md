# Invoice Sender — Results-view Redesign (Design Spec)

**Date:** 2026-05-14
**Authors:** Joseph + Claude (brainstorm session)
**Supersedes (partially):** `2026-05-05-invoice-sender-ux-overhaul.md` Fix 1 + Fix 2 surfaces
**Status:** Approved — ready for implementation planning

---

## 1. Context

v2.62 shipped the Invoice Sender UX overhaul Fix 1 + Fix 2 (tabbed results view, side detail panel, retry-fixed CTA). v2.63 hot-fixed the broken JS. The post-send page now has **three overlapping result UIs stacked on top of each other**:

1. The old **"Send Complete"** summary card with six 1.5rem colored numbers (`invShowSendResults()` writing into `#invSendProgressPanel`).
2. The old **filter dropdown + 8-column invoice table** (`#invSendFilterWrap` + `#invTableContainer`).
3. The new **v2.62 tabbed results component** (`#invSendResults`) rendered below the Status Log.

The user (Lorena and the accounting team) sees the new tabbed view AT THE BOTTOM of the page, below the Status Log, while two older summary surfaces above it duplicate the same headline information. Reference screenshot: `app/assets/images/needs attention.png`.

## 2. Goal

Replace the three stacked surfaces with **one combined HUD** that:

- Lives where the old invoice table lives (immediately above Status Log).
- Always shows the user's data — pre-send (uploaded rows), during-send (live row updates), and post-send (results).
- Surfaces every blocker — pre-send validation issues + post-send failures + TMS fetch failures — in a single "Needs Attention" tab.
- Uses the same color tokens, palette, and component idioms as the merge tool, so the two tools feel like one app.
- Speaks plain English in every user-facing string. Raw technical detail is one click away in the side panel for support.

## 3. Out of scope

- **Fix 3** (Customer Manager redesign — removing the "Invoice" required-doc gate and dup-code detection). Lives in its own design.
- **Fix 4** (CSV import error UX). Lives in its own design.
- Changes to the agent's QBO REST or TMS routing. Behavior unchanged.
- Changes to how PDFs are matched to invoices. Existing matching logic stays.
- Session History / Settings / Customer Manager views.

## 4. UX decisions (locked)

| Decision | Choice |
| --- | --- |
| Tab visibility | Always present (pre-send shows All Invoices populated; Needs Attention reflects validation issues) |
| Needs Attention scope | All blockers: pre-send validation + post-send failures + TMS fetch failures merged in |
| Old Send Complete stat card | Killed. 4 useful items move to the tab-bar toolbar |
| Banner behavior during active send | **Hidden** — progress strip above the card carries the load |
| Banner severity | Amber for soft warning, red for hard error, green for all-clean |
| Action button on failed rows | **"Resolve"** — outlined orange, opens the detail panel |
| Row click | Whole row also clickable, same effect as Resolve |
| Error copy rule | Plain English everywhere; technical detail in side panel under a Copy-able "Technical detail" section |

## 5. DOM structure

### 5.1 New element

A single `#invResultsSection` replaces both the old table area AND the bottom-of-page v2.62 component. Lives between the existing Failed Rows Box position and the Status Log:

```
<div id="invProgressStrip"  style="display:none;">      ← visible only during active send
  Sending invoices… 47 / 120  · 39% complete
  Started 10:34 AM · Elapsed 5m 47s · ~7m remaining · 7.4s/invoice
  [progress bar]
</div>

<div id="invResultsSection">                            ← always visible
  <div id="invSendAlertBanner">…</div>                  ← hidden during send; shows post-send (amber/red/green per severity)
  <div class="v62-tabbar">
    <div class="v62-tabs"> Needs Attention | Sent | All Invoices </div>
    <div class="v62-toolbar"> Started · Total · Avg/inv · ⬇ Report · 📋 Audit </div>
  </div>
  <div id="invResultsTableWrap"></div>                  ← rendered by invoice-sender-results.js per active tab
  <div id="invDetailPanel">…</div>                      ← Fix 2 side panel — unchanged
</div>
```

### 5.2 Deleted

- `invShowSendResults()` function in `invoice-sender.js` and the `Send Complete` card it writes into `#invSendProgressPanel`.
- `#invSendFilterWrap` (the old "All Invoices / Not Sent / Sent / …" dropdown).
- `#invTableContainer` + `#invTableBody` (the old 8-column table with the "Upload a TMS Excel" empty state).
- `#invSummaryBar` and `#invSendStatusBar` (the old colored count pills — counts move into tab labels).
- `#invFailedRowsBox` standalone surface (TMS Data Layer fetch failures). Those rows merge into the Needs Attention tab as another row category.

### 5.3 Kept unchanged

- `#invProgressContainer` is repurposed → renamed `#invProgressStrip`, with new contents (started-at, elapsed, ETA, avg/inv).
- `#invDetailPanel` (Fix 2 side panel) — markup unchanged, contents updated for the expanded pill taxonomy.
- Status Log — unchanged, still collapsible, still below the new section.
- Left sidebar (Upload Excel, Upload PDFs, Send button, etc.) — unchanged.

## 6. State machine

The component reads `invoiceState.invoices[]` and `sendState`. No new state container.

| Stage | Trigger | Progress strip | Banner | Toolbar | Default tab | Row badges |
| --- | --- | --- | --- | --- | --- | --- |
| **Idle** | Page loaded, `invoices.length === 0` | hidden | hidden | hidden | All Invoices (empty state) | — |
| **Uploaded** | Excel parsed, validation done | hidden | hidden | hidden | Needs Attention if validation failures > 0; else All Invoices | Pre-send validation pills only |
| **Sending** | First SSE event after Send clicked | **visible** with started-at, elapsed, ETA, avg/inv | hidden | hidden | Stays on user's current tab | Live per-row: Queued → Sending… → Sent / failure pill |
| **Complete — with failures** | SSE `done`, `failedCount > 0` | hidden | **visible** (amber or red) with Retry CTA | visible | Auto-switch to Needs Attention | Final pills |
| **Complete — all clean** | SSE `done`, `failedCount === 0` | hidden | **visible** (green) | visible | Auto-switch to Sent | All `Sent` |

### 6.1 Empty-state copy

Rendered inside `#invResultsTableWrap` per active tab when row count for that tab is 0:

- **All Invoices (no Excel yet):** `📋 Upload a TMS Excel to see invoices here.`
- **Needs Attention (none flagged):** `✓ Nothing flagged. Upload an Excel to get started.` (pre-send) / `✓ Nothing to fix. All invoices sent cleanly.` (post-send)
- **Sent (no sends yet):** `No invoices sent yet.`

## 7. Pill taxonomy

Three severity colors mirror the merge tool `.val-badge` palette:

- **Amber** (`#fef3c7` bg / `#fde68a` border / `#92400e` text) — soft warning, user can fix in-app.
- **Red** (`#fef2f2` bg / `#fca5a5` border / `#b91c1c` text) — hard error, usually system-level.
- **Green** (`#f0fdf4` bg / `#bbf7d0` border / `#15803d` text) — clean.

### 7.1 Pre-send pills (assigned by JS validator on Excel parse)

| Pill | When it triggers |
| --- | --- |
| 🟡 Missing INV# | INV# cell blank or invalid |
| 🟡 Missing [Field] — names the field; "2 Fields Empty" if multiple | Container, Customer Code, Amount, or Bill-To is empty |
| 🟡 Duplicate INV# | Two parsed rows share an INV# value |
| 🟡 No PDF Match | Excel row's container has no PDF in the dropped attachments |
| 🟡 No Customer Match | Customer code doesn't match any customer in the DB |
| 🟡 No Email on File | Matched customer has an empty email field |
| 🟡 Customer Needs Review | Matched customer has `needsReview = true` |

### 7.2 During-send pills (transient)

| Pill | When it triggers |
| --- | --- |
| 🔵 Sending… | Agent currently processing this row |
| ⚪ Queued | Row in send queue, not yet picked up |

### 7.3 Post-send pills (final state)

| Pill | When it triggers | Severity |
| --- | --- | --- |
| 🟢 Sent | QBO returned 200 on send | green |
| 🟡 POD Missing | `missing_docs: ['pod']` | amber |
| 🟡 POD + BOL Missing (lists which docs) | `missing_docs` with 2+ items | amber |
| 🟡 Amount Mismatch / Customer Mismatch | Pre-send QBO check found a discrepancy; dynamic per field | amber |
| 🔴 QuickBooks Timed Out | Per-row request times out (504 / network timeout) | red |
| 🔴 Signed Out of QuickBooks | QBO returns 401 Unauthorized | red |
| 🔴 No Internet | Network error, DNS failure, or QBO returns 5xx connection error | red |
| 🔴 Unexpected Error | Catch-all — any agent error string we don't pattern-match | red |

**Dynamic pill names:** "Missing Field" and "Mismatch" pills pick the most specific label per row. The `badgeFor()` function in `invoice-sender-results.js` is extended to read row state and pick the explicit reason.

**"Unexpected Error" safety net:** any error response we haven't categorized still gets a friendly badge instead of leaking technical text into the table.

## 8. Banner

### 8.1 When it shows

Hidden during Idle, Uploaded, and Sending. Visible during Complete (both variants).

### 8.2 Severity rule

- **Green (all clean):** `failedCount === 0` post-send.
- **Red (hard error):** any failed row matches connection / auth / timeout patterns, OR `failedCount / totalCount ≥ 0.25`.
- **Amber (soft warning):** otherwise.

### 8.3 Copy

| Severity | Title | Sub-line | Right-side button(s) |
| --- | --- | --- | --- |
| Green | "All N invoices sent successfully" | "Started 10:34 AM · finished 10:48 AM · total 14m 32s" | `📋 View Audit` |
| Amber | "N invoices need a fix before they can send" | "M sent successfully · finished at HH:MM" | `↻ Retry the Fixed Ones (N)` |
| Red | "N invoices couldn't send — looks like [reason]" | "M invoices sent before the problem started. [Action] below, then click Retry." | `[Action button]` + `↻ Retry These (N)` |

The "reason" string in the red title is a plain-English label keyed off the dominant error pattern: `you got signed out of QuickBooks` / `the connection to QuickBooks went down` / `QuickBooks was unresponsive` / `something unexpected went wrong`. The action button matches: `Sign back in to QuickBooks` / `Check connection` / etc.

## 9. Resolve flow

Each failed row has a `Resolve` outlined-orange button on the right. Clicking it (or anywhere on the row) opens `#invDetailPanel` for that row.

### 9.1 Detail panel content (per pill)

The panel renders three sections:

1. **"What's wrong"** — plain-English explanation, 1–2 sentences.
2. **"What to do"** — plain-English action, 1–2 sentences.
3. **Action button** — primary CTA matching the pill:
   - `Missing INV# / Missing Field` → inline editable form → Save.
   - `Duplicate INV#` → "Keep this one / Keep the other / Keep both" picker.
   - `No PDF Match` → drop zone scoped to this container.
   - `No Customer Match` → existing-customer picker + "Create new customer" link.
   - `No Email on File` → inline email input + "Save and update Customer Manager".
   - `Customer Needs Review` → "Open customer profile" deep-link.
   - `POD Missing / Docs Missing` → drop zone OR "Try fetching again" (re-run TMS lookup).
   - `Amount Mismatch / Customer Mismatch` → "Use Excel value / Use QBO value / Open invoice in QBO".
   - `QuickBooks Timed Out / Unexpected Error` → "Try Again".
   - `Signed Out of QuickBooks` → "Sign back in to QuickBooks" (kicks the QBO OAuth flow).
   - `No Internet` → "Check connection · Try Again".

4. **"Technical detail (for support)"** — collapsible, monospace, with a 📋 Copy button. Contains the raw error string, HTTP status, trace IDs. Always present for failed rows, even when we have a friendly explanation, so support can pull it from a screenshot or copy-paste.

### 9.2 Retry mechanics

The banner's "Retry the Fixed Ones (N)" button kicks a new send job containing exactly the rows whose validation/error condition has been cleared since the last send. Rows that still have the same failure are excluded. Existing send-job machinery is reused; no new agent endpoint.

## 10. Toolbar

Lives on the right of the tab bar. Hidden during Idle / Uploaded / Sending. Shown when post-send.

Contents:

```
Started 10:34 AM · Total 14m 32s · 7.3s/inv · ⬇ Report · 📋 Audit
```

- `Started` — anchor timestamp from `sendState.startTime`.
- `Total` — `endTime − startTime`, formatted via the existing `_fmtDuration()`.
- `Avg/inv` — `total / processedCount`.
- `Report` button — triggers the existing `agentBridge.exportAuditLog()`.
- `Audit` button — triggers the existing `invLoadAuditLog()`.

## 11. Progress strip (during send only)

Replaces the old `#invProgressContainer`. Contents:

```
Sending invoices…  47 / 120                            39% complete
[████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░]
Started at 10:34 AM · Elapsed 5m 47s · ~7m remaining · 7.4s/invoice
```

- Renders on first SSE event for the active job.
- Updates per row (via the existing per-row SSE event handlers).
- ETA is calculated as `(avg/inv) × remainingCount`.
- Removed on `done` SSE event — timing migrates into the toolbar.

## 12. Color tokens (canonical list)

All values mirror `app/assets/css/styles.css` merge tool selectors. New invoice-sender CSS reuses these exact values:

| Role | Token |
| --- | --- |
| Primary action | `#ea580c` (hover `#c2410c`) |
| Soft warning bg | `#fffbeb` |
| Soft warning border | `#fde68a` |
| Soft warning solid | `#f59e0b` |
| Soft warning text | `#92400e` (sub `#b45309`) |
| Hard error bg | `#fef2f2` |
| Hard error border | `#fca5a5` |
| Hard error solid | `#dc2626` (hover `#b91c1c`) |
| Hard error text | `#b91c1c` (deep `#7f1d1d`) |
| Clean bg | `#f0fdf4` |
| Clean border | `#bbf7d0` |
| Clean solid | `#16a34a` |
| Clean text | `#15803d` (deep `#166534`) |
| Body text | `#0f172a` / `#1e293b` |
| Secondary text | `#475569` / `#64748b` |
| Borders | `#e2e8f0` / `#cbd5e1` |

## 13. Files touched

- `app/index.html` — DOM restructure (delete the old surfaces, add `#invResultsSection`, rename `#invProgressContainer` to `#invProgressStrip` with new contents).
- `app/assets/css/styles.css` — add styles for the progress strip, the tab bar toolbar, the action button (Resolve), and the new pill variants. Reuse merge-tool tokens directly.
- `app/assets/js/tools/invoice-sender/invoice-sender.js`:
   - Delete `invShowSendResults()` and references.
   - Delete `_renderSendTally()` and references to `#invSummaryBar` / `#invSendStatusBar`.
   - Add `invValidateRowsOnUpload()` to assign pre-send pills the moment Excel is parsed.
   - Rewire `#invProgressStrip` rendering (move from `invProgressContainer` and extend with timer info).
   - Re-route TMS Failed Rows Box population into the same `invoiceState.invoices[]` array with a new validation pill.
- `app/assets/js/tools/invoice-sender/invoice-sender-results.js`:
   - Extend `badgeFor()` with the full pill taxonomy from §7.
   - Add `getPlainEnglishError(row)` helper that returns `{ title, body, action, raw }` for the detail panel.
   - Extend `showResultsView()` / `renderResults()` to handle the new state machine — banner severity selection, toolbar visibility, auto-tab-switch.
   - Add `Resolve` button to each failed row in the table renderer.
   - Add the dynamic pill name logic for "Missing Field" / "POD + BOL Missing" / "Amount Mismatch".

No agent-side changes.

## 14. Test scenarios (manual smoke set)

The PR should pass each of these in the packaged Electron app before being released:

1. **Cold start, no Excel** — All Invoices tab shows the upload-prompt empty state. No progress strip. No banner.
2. **Excel with 5 validation issues (one of each category)** — Needs Attention shows 5 rows, each with the right pill. Whole row + Resolve button both open the right detail-panel content.
3. **Send a clean batch of 5** — progress strip shows started-at + elapsed; row badges tick Queued → Sending… → Sent. On done, green banner + toolbar + auto-switch to Sent tab.
4. **Send a batch with 2 doc-missing failures and 1 QBO timeout** — amber banner, "Retry the Fixed Ones (3)" disabled until at least one is resolved.
5. **Send a batch, QBO returns 401 on first row** — red banner ("looks like you got signed out…"), Sign-Back-In button works, Retry covers exactly the 401 rows.
6. **Send a batch where ≥25% fail** — red banner triggered by threshold even if errors aren't auth/network.
7. **Excel parses, then user uploads PDFs after** — pre-send pills update to clear "No PDF Match" rows as PDFs arrive.
8. **Mid-send the user opens the side panel for a not-yet-sent row** — panel renders without errors; closes cleanly.
9. **All clean, 120 sent** — green banner, View Audit button works.

## 15. Migration / rollout

No data migration. The state objects (`invoiceState`, `sendState`) already hold the data we need. The redesign is purely DOM + render-function changes.

Ship in a single version bump (e.g., v2.64) per the standard rebuild pipeline. Per `feedback_always_push_and_release.md` and `feedback_use_runbuild_for_rebuild.md`: bump VERSION → `runbuild.bat` via PowerShell → manual smoke-test of the packaged installer → commit → push → `gh release create` with installer + latest.yml.

Per `feedback_app_not_website.md`, all testing happens in the packaged Electron app — not the browser running `app/index.html` directly.

## 16. Open items / known follow-ups

- **Fix 3 (Customer Manager)** — pending. The "Customer Needs Review" + "No Customer Match" + "No Email on File" pills depend on Customer Manager surfacing the right edit affordances. If Customer Manager fields aren't ready, those pills still work — they just deep-link into the existing Customer Manager view.
- **Fix 4 (CSV import errors)** — pending. CSV-side errors (file parse failures, header mismatch, etc.) stay in the Status Log for now. They're not row-level issues, so they don't fit the Needs Attention model.
- **POD Validator feature** — already on the backlog as a separate plan. Independent of this redesign.
