# Merge Tool — Trailer Vans + Filtering + Queue/Error UX

**Date:** 2026-06-02
**Status:** Approved for implementation
**Mockup:** `app/mockups/merge-filters.html`

## Context

Two bugs from Lorena's batch (May errors workbook, 67 rows) and a missing invoice type:

1. **Queued retries process one-by-one.** Single-row "Try Again" clicks during a running fetch pile up in `v2State.queuedRetries`; `processQueuedRetries` (`merge-v2.js:2480`) drains them sequentially after the main fetch ends, firing a separate `fetchMissing` job per row.
2. **Errored / queued rows stay checked by default after fetch.** "Continue to Merge" includes errored rows that will merge invoice-only with no POD/BL. User wants to filter and exclude them quickly.
3. **Trailer van invoices (INV# 2nd letter `V`) aren't routed.** Currently classified as `unknown`, run the safety chain against TMS using fake container IDs like `T1022` or the literal word `Special`, and always fail. 25 of 67 May-errors rows were vans.

## Scope — what ships in this release

1. **Trailer van routing** — new `routingType: 'van'`
2. **Batched queued retries** — mechanical fix to `processQueuedRetries`
3. **Unified filter system** — Type chips with embedded doc-type pills · Customer dropdown · column-header sort · master checkbox · hybrid status-scoped action buttons
4. **Read-only sidebar during fetch** — rows clickable; sidebar shows live fetch state, retry/skip/upload disabled until row settles
5. **Progress strip with timer + pause + cancel** — lives in the status-tabs row during the Fetching state

## Out of scope (separate follow-ups)

- **QBO duplication glitch** — when a TMS WO update fires, QBO ends up with multiple copies of the same POD, which means the merge tool stitches dupes into final PDFs. Logged as a separate investigation.
- **Pre-fetch status tabs polish** — currently shows muted Errors/Queued tabs with `—` counts; consider hiding if the de-emphasized state proves confusing in practice.

---

## 1. Trailer van routing

### Detection

INV# 2nd letter = `V` → `routingType: 'van'`. Extends `parseInvType()` (`app/assets/js/shared/utils.js:126`).

### Fetch behavior

| Step | Action |
|---|---|
| 1 | Pull invoice PDF from QBO (same as every routing type) |
| 2 | Navigate directly to `/bc-detail/document/van/{WO#}` on TMS using WO# from manifest (skip MAIN grid container search — trailer vans don't have ocean container numbers) |
| 3 | Try docs in order: **POD → POL → BL → IT → ITE** |
| 4 | If TMS Document tab has none of those types → flag row as error ("TMS Docs not found") |

### UI surface

| Element | Treatment |
|---|---|
| Will Fetch chip | `TMS Docs` (orange, same palette as the existing `POD` chip) |
| Filter chip label | `Vans` |
| `expectedDoc` value in state | `'?'` (because the chain tries multiple) |
| Filename | `{date}_{INV#}_{WO#}_merged.pdf` — drops the unreliable "container" field (trailer IDs / `Special` / PO numbers); WO# is the stable identifier |

### Agent side

`agent/services/tms_browser/search.py` already supports `van` in `_MAIN_TO_DETAIL_TYPE` (line 567) and the direct-URL navigation pattern works for any type segment. The Document-tab listing + download paths at `documents.py` work identically. No new agent code needed beyond plumbing the `van` type through the existing fetch dispatcher.

---

## 2. Batched queued retries (Issue 1 fix)

### Current behavior (broken)

```js
// merge-v2.js:2480-2489
async function processQueuedRetries() {
  const queue = v2State.queuedRetries.slice();
  v2State.queuedRetries = [];
  for (const { rowIdx } of queue) {
    try { await v2RetryRow(rowIdx); }       // each iteration = one fetchMissing job
    catch (err) { console.warn(...); }
  }
}
```

Eleven queued single-row retries = eleven sequential `fetchMissing` jobs. Each waits for its own SSE stream to complete before the next starts.

### Fixed behavior

Mirror the batching pattern from `v2ResumeFetch` (`merge-v2.js:2151-2189`): dedup by container, fire a single `agentBridge.fetchMissing(containers, ['pod'])`, wire one SSE stream that routes per-container events to the correct rows.

```js
async function processQueuedRetries() {
  const queue = v2State.queuedRetries.slice();
  v2State.queuedRetries = [];
  if (queue.length === 0) return;

  // Resolve rowIdx → row; dedup by container
  const seen = new Set();
  const containers = [];
  const rowsByContainer = new Map();
  for (const { rowIdx } of queue) {
    const row = v2State.rows[rowIdx];
    if (!row) continue;
    const key = row.containerNumber.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    containers.push({ containerNumber: row.containerNumber, invoiceNumber: row.invoiceNumber });
    rowsByContainer.set(key, row);
  }
  if (containers.length === 0) return;

  // One job for the whole batch
  v2State.jobIncludesInvoice = false;
  v2State.jobIncludesDoc = true;
  const result = await agentBridge.fetchMissing(containers, ['pod']);
  if (result.error || !result.jobId) return;
  // Reuse the existing SSE stream handler — it already patches rows by container
  v2State.jobId = result.jobId;
  openSseStream(result.jobId);
}
```

Same code path that already powers "Resume fetch" and the toolbar's "Retry all errors" button — both already glitch-free.

---

## 3. Filter system

### Layout (single row, both pre-fetch and post-fetch)

```
[Type chip · Type chip · Type chip · Type chip · Type chip · Type chip]   ← row 1
[Customer dropdown] [Search input] [Reset]                                ← row 2
[Status tabs] [Progress strip when fetching] [Action buttons]             ← row 3
[Sortable table]
```

### Type chips

Each chip embeds the WILL FETCH pill inline with the identifying name first:

| Chip | Pill | Behavior |
|---|---|---|
| `All` | — | Everything |
| `Imports` | `POD` (orange) | INV# starts with `M` |
| `Exports` | `BL/POL` (blue) | INV# starts with `E` |
| `Warehouse` | `All QBO Docs` (grey) | INV# starts with `W` |
| `Vans` | `TMS Docs` (orange) | INV# starts with `V` — NEW |
| `Unknown` | `?` (grey) | Anything else |

Replaces the existing standalone `WILL FETCH` summary band — the chip itself teaches what each type fetches. Chips apply in both pre-fetch and post-fetch views.

### Customer dropdown

Populates dynamically from the manifest's unique `customer` values, sorted A–Z with row counts: `NVH USA, INC (8)`. `All customers (N)` is the default.

### Sort

Click column header to sort. First click → ascending (`▲`), second → descending (`▼`), third → clear. Active column tints orange.

Sortable columns: `CONTAINER`, `INVOICE #`, `CUSTOMER`, `STATUS` (post-fetch only). Replaces the previous "Sort" dropdown.

### Master checkbox

Top-left cell of the table header (where row checkboxes live). Behavior:

- Empty: no visible rows checked
- Filled: all visible rows checked
- Indeterminate dash: some visible rows checked

Click flips the visible set. Workflow: filter to `Errors` → click master → all errors uncheck → switch to `All` → only OK rows are still selected → click `Continue to Merge`.

### Selection meta

Right side of the filter bar:

```
12 of 33 · Vans · NVH USA, INC · 30 selected
```

`30 selected` is the global selection count (mergeable rows, excluding queued).

### Status tabs

Tabs: `All` · `Errors` · `Queued`. Counts respect the active Type + Customer + Search filters.

- **Post-fetch:** all three interactive
- **Pre-fetch:** `All` interactive; `Errors` and `Queued` muted with `—` counts and a tooltip "Status filters become active after the fetch runs"

### Hybrid action buttons

Three buttons surface in `.actions-wrap` based on the active status tab. `Continue to Merge` is always present post-fetch; the secondary buttons appear contextually:

| Status tab | Buttons shown (post-fetch) |
|---|---|
| `All` | `Retry errors (X)` (if errors exist) · `Resume fetch (X queued)` (if queued exist) · `Continue to Merge (N)` |
| `Errors` | `Retry errors (X)` · `Continue to Merge (N)` |
| `Queued` | `Resume fetch (X queued)` · `Continue to Merge (N)` |

Pre-fetch: just `Start fetch (N)`.

`Retry errors` and `Resume fetch` both fire single batched `fetchMissing` jobs (already implemented as `v2RetryAllErrors` / `v2ResumeFetch`). `Continue to Merge` count = selected rows that have a `fetchResult` (mergeable); queued rows never count toward merge.

### Queued row checkbox semantics

Queued rows are interactive. Unchecking removes the row from the next Resume fetch attempt. Tooltip on a queued row's checkbox: `Uncheck to remove from queue (won't be fetched on Resume)`.

`Resume fetch` button count = selected queued rows. If you uncheck all queued rows, the button disappears.

---

## 4. Read-only sidebar during fetch

### Current behavior

Rows are non-interactive during the Fetching state because `interactive = hasFetch && !isSkipped` at `merge-v2.js:620` returns false for rows without a `fetchResult`. User can't peek at row details mid-fetch.

### Fixed behavior

Rows become clickable during Fetching. Clicking opens the existing `v2DetailSidebar` (`merge-v2.js:1539`) in read-only mode:

- Header icon + title reflects fetch state (`Fetching Container` for the in-flight row, `Container Queued` for waiting rows)
- `panel-empty-banner` (existing orange variant) explains the row is being fetched live
- `Customer` section: same as today
- `What's Happening` section uses the existing `.happened-block` styling, orange variant — "Pulling documents now" / "Waiting in queue"
- `Routing trace` section uses the existing `.routing-trace` with arrow markers — shows the planned chain with the active step highlighted
- `Resolve` section: full-width `Retry API call` button disabled, dashed upload zone dimmed, divider "OR UPLOAD MANUALLY" preserved — unlocks once the row settles
- Footer: `Skip this one` link + `← Prev` + `Next Error` all disabled with tooltips

Layout uses the existing `v2DetailSidebar` CSS verbatim (`app/assets/css/styles.css:1951-2143`) — no new sidebar styles introduced.

---

## 5. Progress strip + timer + pause/cancel

### Layout

Lives in the status-tabs row during the Fetching state. Status tabs on the left, progress strip in the middle filling the available width, action buttons (Pause + Cancel) on the right.

```
[All 33] [Errors 7] [Queued 7]   [⏱ 12 / 33 ████████░░░░░░ · 00:42 elapsed · ~01:23 left · ~3.2s/row]   [⏸ Pause] [✕ Cancel]
```

### Components

| Element | Source / behavior |
|---|---|
| Spinner | Animated CSS spinner using the existing `.spinner` class |
| `N / Total` | `fetchProgress / fetchTotal` from `v2State` |
| Progress bar | `flex: 1` to fill width; fill % = `progress / total` |
| Elapsed | Ticks live every 1s; reads `Date.now() - fetchStartedAt` |
| ETA | `(total - done) * msPerRow` where `msPerRow` is a rolling average |
| Rate | `~${msPerRow / 1000}s/row` |

### Pause button

Currently no pause primitive in the agent. Two paths:

1. **Soft pause (recommended for v1):** Stop accepting new fetch dispatches client-side. In-flight requests run to completion. `agentBridge.pauseJob(jobId)` posts to a new `/jobs/{id}/pause` endpoint that flips a flag in the job dispatcher — `_run_next_container()` checks the flag before each container and waits.
2. **Hard pause:** Same as above plus cancel the in-flight `fetch_one_container` task. More invasive; defer until users actually need it.

Ships with soft pause. Button label flips to `▶ Resume` when paused.

### Cancel button

Posts to `/jobs/{id}/cancel` (already exists for in-flight job cancellation). Confirms with the user first ("Cancel the fetch? Rows already fetched keep their data; queued rows stay queued."). On confirm, agent cancels in-flight tasks, SSE stream closes, UI moves to the Ready (post-fetch) state with whatever data was already collected.

---

## File changes

### Frontend

| File | Change |
|---|---|
| `app/assets/js/shared/utils.js` | Add `V` → `'van'` in `parseInvType`; add `routingDecisionFor` case for `van` returning `{ type: 'van', expectedDoc: '?' }`; update `renderInvoiceNumberHtml` to highlight `V` letter |
| `app/assets/js/tools/merge/merge-v2.js` | Add `van` to `routingTypeFilter` values; add Van chip with `TMS Docs` pill; replace `processQueuedRetries` with batched version; add Customer dropdown + dynamic option population; replace Sort dropdown with sortable column headers; add master checkbox + selection state tracking; add status-scoped action button visibility; allow row clicks during Fetching state; add progress strip with timer, pause, cancel; make queued row checkboxes interactive; update filename rule for van rows in `merge-v2-output.js` |
| `app/assets/js/tools/merge/merge-v2-engine.js` | No structural changes — van rows merge using the same path as other types once the doc files exist on disk |
| `app/assets/css/styles.css` | Add `.will-chip.van` orange variant; add Customer dropdown styling; add sortable column header styling; add progress strip styling; add Pause/Cancel button styling |

### Backend (agent)

| File | Change |
|---|---|
| `agent/services/job_manager/` | Add Van routing branch: skip MAIN grid container search, navigate directly to `/bc-detail/document/van/{wo}`, list documents, try POD → POL → BL → IT → ITE in order |
| `agent/routers/jobs.py` | Add `POST /jobs/{id}/pause` endpoint that flips a pause flag on the job |
| `agent/services/job_manager/dispatcher.py` | Honor pause flag between container dispatches |

### Tests

| File | Change |
|---|---|
| `agent/tests/test_job_manager/test_fetch_job_van_routing.py` | New — verify van rows skip the grid search and try the chain in order |
| `agent/tests/test_routers_jobs.py` | New test — pause flag stops new dispatches; cancel works post-pause |

---

## Acceptance criteria

- [ ] A V-prefix invoice in the May errors workbook ends with `routingType: 'van'`, `Will Fetch: TMS Docs`, and either fetches successfully or flags "TMS Docs not found"
- [ ] Filename for a Van row is `{date}_{INV#}_{WO#}_merged.pdf` (no container/trailer ID in the name)
- [ ] During a fetch, clicking 10 individual "Try Again" buttons in error sidebars and waiting for the cycle to finish results in **one** batched fetch job for all 10 rows (not 10 sequential jobs)
- [ ] Post-fetch, clicking the master checkbox on the Errors tab unchecks all visible error rows; switching to All tab shows them unchecked
- [ ] Clicking a Customer name in the dropdown filters the table; clicking a column header sorts the visible rows
- [ ] During a fetch, clicking any row opens the existing sidebar in read-only mode; Retry / Skip / upload all show disabled with tooltips
- [ ] Pause button pauses the fetch (no new container dispatches); Resume resumes; Cancel ends the fetch and moves to Ready state
