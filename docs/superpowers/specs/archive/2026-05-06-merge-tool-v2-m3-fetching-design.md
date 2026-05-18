# Merge Tool V2 — M3 (Fetching + Ready) Design

**Date:** 2026-05-06
**Status:** Design approved → ready for implementation plan
**Mockups:**
- `app/mockups/merge-tool-m3-ready.html` — M3-specific (Will-fetch column, dynamic pill labels, Resume fetch, routing trace)
- `app/mockups/merge-tool-redesign.html` — parent v2.42 visual contract for sidebar + table chrome
**Builds on:** v2.46.0 (M2 — Review state) + v2.47.0 (invoice-grouping fix). Inherits the 4-state spine from `2026-05-05-merge-tool-ux-refinement-design.md`.

## Goal

Wire the existing fetch/POD pipeline into the v2 merge tool's Fetching and Ready states. Bake in the M3-specific design refinements: invoice-prefix-driven doc routing, per-row "Will fetch" visibility, IT/ITE chain extension for the TAB BANK workflow, error-resolution sidebar with auto-save and routing trace, and partial-completion handling for cancelled fetches.

## Why this matters — business context

The merge tool serves two business workflows (see `memory/project_merge_tool_use_cases.md` for the full picture):

1. **INI invoice sending** — bundle every invoice + POD into one large file for a single customer. Uses the **All in One** merge mode.
2. **TAB BANK posting** — per-invoice merges to obtain operational funding. NGL often submits invoices BEFORE the formal POD/BOL/POL is filed, so those formal docs are routinely missing on TAB BANK runs. The dispatcher's IT (pull-out) or ITE (return) ticket — physical proof of container movement — fills this gap as a last-resort automated fallback before the user has to chase the driver for a manual upload.

Every M3 design decision below traces back to one of these workflows.

## Non-Goals

- **Actual merge engine** — `Continue to Merge →` transitions to the M4 stub. Real PDF combining ships in M4.
- **Manual Merge mode** (drag-reorder of arbitrary PDFs) — out of scope.
- **Bulk PDF top-bar drop wiring** — the `pdf-drop-card` in the top bar is visible but inert in M3 (clicking does nothing). Wiring lands in M4 alongside merge.
- **New backend job endpoints** — M3 reuses `POST /jobs/fetch-missing` and `GET /jobs/{id}/stream`. The only agent change is extending the existing `_tms_pod_fallback()` function in `fetch_job.py` to add the IT/ITE chain steps and pre-decide doc-type from the INV# prefix.

## The doc-fetch routing rule

### Two signals encode "import vs export"

1. **Invoice # prefix** — `[Office][Type][YYMMDD][seq]F`. Position 2 is the type letter:
   - `M` → Import (POD)
   - `E` → Export (BOL/POL)
   - Examples: `LM26050100F` (LA Import), `PE26050103F` (Phoenix Export).
2. **WO# letter** — read from QBO's NGL REF# custom field; used as fallback when INV# prefix doesn't parse:
   - `M` → Import; `X` → Export; neither → unknown.

### Routing priority

1. Parse INV# pos-2. If `M` or `E`, use that.
2. Else parse WO# letter. If `M` or `X`, use that.
3. Else `unknown` — use the safety chain.

This overrides v2.44's WO#-primary rule because INV# is always on the row (parsed from the Excel) while WO# only becomes available after QBO is queried — so INV# lets the tool decide doc type **before** the first API call. WO# stays as the rescue path for malformed INV#s.

### Doc chain by routing decision

- **Import**: POD → BOL → POL → IT → manual upload
- **Export**: BOL → POL → ITE → manual upload
- **Unknown**: POD → BOL → POL → IT or ITE → manual upload

### Pill semantics on each row

| Color | State | Meaning |
|---|---|---|
| Green `ok` | First-choice doc found | Import row found POD; export row found BOL |
| Amber `fallback` | Later step in chain succeeded | Import row found BOL after POD missed; export row found POL or ITE |
| Red `miss` | Every step in chain failed | User must drop a PDF manually |
| Gray `queued` | Fetch hasn't started yet | Pre-fetch (Review) or Resume-fetch pending (post-cancel Ready) |

The second pill's **label** reflects what actually came back (`POD` / `BOL` / `POL` / `IT` / `ITE` / `—`), not the original target. The amber color tells the user "this isn't your first-choice doc"; the label tells them what they got instead.

### Backend change (the single agent file affected)

`agent/services/job_manager/fetch_job.py:_tms_pod_fallback()`:
- Add INV# pos-2 parsing as the **primary** type signal (currently uses WO# letter only)
- Extend the chain: after POL miss, attempt IT (imports) / ITE (exports) / both (unknown)
- SSE event payload gains a `chain_attempted: list[str]` field listing each step that was tried, so the sidebar's routing trace can render accurately

## State 1: Fetching (`subMode === 'fetching'`)

When the user clicks Fetch on Review (already wired in M2 to set `subMode='fetching'`), this state takes over.

### Render

- Top bar: Excel summary (left) + bulk PDF drop card (right, inert — see Non-Goals)
- **Routing summary band** (NEW): `Will fetch: N PODs · M BOL/POLs · K unknown` with chip legend
- Progress line: `Fetching X / N · <current container>` + progress bar + `Cancel` button
- Tabs: `All` · `Fetched` · `Failed` (counts update live)
- Toolbar: search, sort, filter-meta (`X / N fetched · Y failed`)
- Table:
  - No checkbox column (selection only opens up in Ready)
  - Columns: Container, Invoice #, Customer, **Will fetch**, Documents, Status

### Live updates via SSE

- Subscribes to `/jobs/{id}/stream` via existing `agentBridge.streamProgress(jobId, onEvent)` client method.
- Handles these events:
  - `row_updated` — mutates one row in `v2State.rows`, rewrites just that `<tr>` (focus-preserving pattern from M2)
  - `progress` — updates the progress bar and `Fetching X / N` label
  - `job_completed` — transitions to Ready
  - `job_cancelled` — transitions to Ready (partial)
- Pill changes get a 250ms CSS fade-and-slide transition; the row background gets a soft-yellow flash that fades to default over 1s. Both CSS-only.

### Cancel button → partial Ready

- Sends `POST /jobs/{id}/cancel`.
- Rows whose fetch was mid-flight get marked `queued` (revert from any partial state).
- Already-completed rows keep their pills.
- Transitions to Ready with the partial data.

## State 2: Ready (`subMode === 'ready'`)

### Render

- Top bar: Excel summary + bulk PDF drop card (still inert)
- **Routing summary band** (same as Fetching)
- **Action bar**:
  - Status text: live counts — `● 38 of 40 ready to merge · ● 2 need fixing` (or `· ● 5 queued` in partial-completion variant)
  - Sort dropdown
  - Primary action button (see two variants below)
- Tabs: `All` · `Errors` (· `Queued` only when partial-completion has queued rows)
- Toolbar: search, sort, filter-meta
- **NEW: `↻ Retry all errors` button** — rendered inside the toolbar when on the Errors tab. Disabled when no error rows exist.
- Table (with checkbox column):
  - Columns: ☐, Container, Invoice #, Customer, Will fetch, Documents, Status, (Fix Error button if error)

### Two variants of the primary action button

**Full fetch complete (no queued rows):**
- Button: `Continue to Merge →` (orange, with `[N selected]` count badge)
- Click → transitions to `merging` state (M4 stub)

**Cancelled mid-fetch (queued rows remain):**
- Button: `↻ Resume fetch` (blue, with `[N queued]` count badge)
- Discreet meta line below: `Last fetched: <container>`
- Click → re-POSTs queued container IDs to `/jobs/fetch-missing`, transitions back to Fetching

The button name was chosen specifically over "Run Merge" to make the M3 / M4 split read naturally — the click is "moving forward to the next step," not "irreversibly do the thing now."

### Errors-tab default-active rule (inherited from M2)

On entering Ready: if any rows have `status === 'error'`, set `activeTab = 'errors'`. Otherwise `'all'`.

### Sidebar auto-opens on first error (NEW)

On entering Ready: if any error rows exist, the sidebar auto-opens on the first one (`v2State.openSidebarRow = firstErrorIdx`). The error row in the table behind gets an orange left-edge marker + orange-tinted background to mark "this is what's currently open."

The backdrop is clickable to dismiss the sidebar; the user can then browse the table and re-click any error's `Fix Error` button to re-open the sidebar on a different row.

### Master + per-row checkbox sync

- Selectable rows: `status` ∈ {`ok`, `fallback`}
- Disabled rows: `status` ∈ {`error`, `queued`, `skipped`}
- Master checkbox toggles all selectable visible rows. Indeterminate state when partially checked.
- The action button's `[N selected]` badge updates live.

### Same-container dedup at fetch launch

Before posting to `/jobs/fetch-missing`, dedup the selected rows by container. One agent fetch per unique container; results apply to **all** invoice rows sharing that container. Honors the v2.47 invariant — every invoice row stays a separate entry in the table and gets its own merged PDF in M4 — without wasting API calls when one container appears on multiple invoice lines (the CONAIR drayage pattern).

## State 3: Error sidebar (renders inside Ready)

### Header

- Red ⚠ icon · "Fix Container Error" · subtitle: `<container> · Invoice <inv#>` · close (×) button

### Body sections (top to bottom)

1. **Customer** — friendly name (e.g., `EVERGREEN`)
2. **What Happened** — red block: `<doc-type> not found in TMS` + reason text + WO# in a code chip
3. **Routing trace** (NEW) — step-by-step fetch log for this row, populated from the SSE event's `chain_attempted` field:
   ```
   → INV# LM26050102F pos-2 = M → import
   → Plan: try POD → BOL → POL → IT
   ✗ QBO: no POD attachment
   ✗ TMS: no POD for WO LM2604220001
   ✗ TMS: no BOL
   ✗ TMS: no POL
   ✗ TMS: no IT
   ! Exhausted chain — manual upload required
   ```
4. **Resolve**:
   - `↻ Retry API call` button (POSTs single container to `/jobs/fetch-missing`)
   - "or upload manually" divider
   - Drop zone: `Drop <doc-name> for <container>` — accepts `.pdf` only. `<doc-name>` = "POD" for imports, "BOL or POL" for exports, "POD, BOL, or POL" for unknown.

### Footer

- `Skip this one` link · `← Prev` button · `Next Error → [N left]` button (orange, primary)
- When N=0: button flips to green `Done — close sidebar ✓`. Click closes the sidebar and dismisses the backdrop.

### Auto-save behavior

When the user resolves an error via Retry success or PDF drop:

1. Row is updated in `v2State.rows[i]` — pills flip from red to green/amber, status flips to "Fetched", `manualPodFile: File` blob attached if uploaded
2. The corresponding `<tr>` re-renders in place (with row-change animation)
3. The sidebar **stays open** but the body re-renders to a "✓ Resolved" view:
   - Header swaps red ⚠ icon for green ✓; gets a thin green tint
   - "What Happened" red block becomes a green "Resolved" block
   - Routing trace gains a final ✓ line (`✓ Manual upload: <filename>` or `✓ Retry succeeded: POD found in TMS`)
   - Resolve section is replaced by a compact summary: `Attached: <filename> · <size>` + small `Replace` link
4. Footer's Next Error count drops by 1; user clicks Next when ready to advance

### Skip behavior

- Sets `v2State.rows[i].skipped = true`
- Row stays disabled in the table (still can't be merge-selected)
- Status cell shows a small gray `Skipped` tag
- Errors-tab counter and sidebar's remaining-errors count drop by 1
- Re-clicking the row's `Fix Error` button clears the skip flag and re-opens the sidebar on that row

## Out-of-band UX rules

These rules are not visible in the static mockups but are required for correct behavior:

- **Animation timing** — pill fade-and-slide: 250ms ease-out. Row background flash: yellow tint fades to default over 1s. CSS-only, no JS animation library.
- **Cancel race protection** — when `subMode !== 'fetching'`, the SSE event handler ignores incoming events. Prevents a late `row_updated` from landing after a cancel-induced state transition.
- **Sidebar state across re-renders** — `v2State.openSidebarRow` survives full re-renders. Clicking a different error row's Fix button updates this field and re-renders the sidebar pane only.
- **`+ New Merge` defensive teardown** — calling `setStateV2('empty')` from any state must (a) call `eventSource.close()` if a fetch is active, (b) call `POST /jobs/{id}/cancel` to stop the agent, (c) clear `v2State.rows`, `openSidebarRow`, `skipped` flags, queued lists, and the EventSource handle. Wrapped in try/catch so it never throws — leftover state is preferable to a crash mid-cleanup.
- **Mass retry button** — POSTs every error-row container to `/jobs/fetch-missing` in a single batch. Reuses the same SSE handler the original fetch used. Does NOT touch already-resolved or skipped rows.
- **Manual file persistence across re-renders** — `manualPodFile: File` blob lives in `v2State.rows[i]`. Must survive sort/search/tab re-renders. Closing the sidebar does NOT clear it (the file stays attached to the row until the user explicitly Replaces or until `+ New Merge` resets state).

## Files affected (implementation surface)

### Frontend

- `app/index.html` — no structural changes (sidebar markup is rendered into `v2WorkArea` by JS, not a separate root container)
- `app/assets/css/styles.css` — append M3 block scoped to `#mergeToolViewV2`:
  - `.routing-summary` band styles
  - `.will-chip` (import / export / unknown variants)
  - `.detail-sidebar` + `.sidebar-backdrop` + `.ds-header/body/footer` (port from mockup CSS, scoped)
  - `.routing-trace` step list
  - `.merge-btn.resume` blue variant
  - Row-update animation keyframes (`@keyframes row-flash`)
- `app/assets/js/tools/merge/merge-v2.js` — most of the work:
  - Replace `renderFetching()` and `renderReady()` stubs with real markup
  - New `renderSidebar(rowIdx)` + `renderResolvedSidebar(rowIdx)`
  - New routing helpers: `parseInvType(inv)`, `parseWoType(wo)`, `routingDecisionFor(row)` returning `'import' | 'export' | 'unknown'`
  - SSE wiring via existing `agentBridge.streamProgress(jobId, onEvent)`
  - `handleRowUpdated(event)` — patches one row, animates the change
  - `handleJobCompleted()` / `handleJobCancelled()` — transition to Ready
  - `openSidebar(rowIdx)` / `closeSidebar()` / `advanceToNextError()` / `markRowSkipped(rowIdx)`
  - `retryRow(container)` and `retryAllErrors()`
  - `resumeFetch()` — re-POSTs queued containers
  - Defensive `setStateV2('empty')` teardown

### Backend

- `agent/services/job_manager/fetch_job.py:_tms_pod_fallback()`:
  - Read INV# from row metadata, parse pos-2 letter as primary type signal
  - Extend chain with `try_it_ticket()` / `try_ite_ticket()` calls after POL
  - Emit `chain_attempted` field on the SSE `row_updated` event for the routing trace

`merge-v2.js` is currently ~650 lines. After M3 it'll be ~1100-1300. If it crosses 1500, split along state-machine boundaries (one file per state group) — follow-up call, not part of this work.

## Risks and gotchas

- **TMS IT/ITE fetch capability** — adding IT/ITE to the chain requires the TMS browser/API service to know how to fetch those doc types. If the existing TMS REST endpoint doesn't expose IT/ITE, this will need a TMS browser fallback (Playwright). **Verify before plan-writing** — if the capability is missing, IT/ITE shipment may need to be deferred to a follow-up while M3's frontend ships with placeholder steps.
- **Same-container dedup mapping** — the SSE `row_updated` event currently keys by container, but v2State.rows is per-invoice. The handler must apply a single SSE event to **every** row sharing that container, not just the first match.
- **Routing trace data shape** — depends on the agent change to emit `chain_attempted`. If the agent change is descoped, the client will need to reconstruct the trace from the row's status fields (less accurate — won't show "TMS API timeout" vs "no doc found"). Plan should pick one path explicitly.
- **Cancel race** — see Out-of-band rule above; SSE event handler must check `subMode === 'fetching'` before mutating state.
- **EventSource cleanup** — `+ New Merge` (and any other state-clearing transition) must explicitly close the EventSource. A leaked one will keep delivering events into a destroyed view.
- **Manual file blob memory** — uploading a 10 MB POD into `v2State.rows[i].manualPodFile` is fine; uploading 50 of them (worst case for a TAB BANK batch where every row needs a manual file) is ~500 MB in browser memory. Acceptable for current usage but flag if batch sizes grow.

## Acceptance criteria

User can:

1. Drop an Excel → Review state shows the routing summary band correctly counting imports / exports / unknown rows, plus the per-row Will-fetch column with import/export/unknown chips.
2. Click Fetch → land on Fetching → watch live row updates with smooth fade-and-flash animations as each container completes.
3. Click Cancel mid-fetch → land on Ready with partial data → see the `↻ Resume fetch · N queued` button (blue) + `Last fetched: <container>` meta line → click Resume → fetch picks up where it left off.
4. Land on Ready with errors → sidebar auto-opens on the first error → see the routing trace explaining exactly why this row failed, step by step.
5. Click `↻ Retry API call` in the sidebar → on success, body re-renders to ✓ Resolved view, row's pills flip green/amber, footer's Next Error count drops by 1.
6. Drop a PDF in the sidebar's upload zone → same auto-save flow as Retry → "Attached: <filename>" summary in the body, sidebar header tints green, ⚠ icon swaps for ✓.
7. Click `Skip this one` → sidebar advances to next error, row gets a `Skipped` tag in the table, can't be merge-selected. Re-clicking the row's `Fix Error` button reverses the skip.
8. Click an export row's Fix Error button → sidebar shows export-specific routing trace (BOL → POL → ITE) and prompts for "Drop BOL or POL" instead of POD.
9. Switch to All tab → toggle individual checkboxes → master checkbox stays in sync → `Continue to Merge →` count updates live.
10. Click `Continue to Merge →` → land on Merging (M4 stub).
11. Click `↻ Retry all errors` on the Errors tab toolbar → all error containers re-fetched in one batch via the existing fetch-missing endpoint.
12. Click `+ New Merge` from any state (mid-fetch, sidebar-open, half-resolved) → no errors thrown, lands cleanly on Empty.
