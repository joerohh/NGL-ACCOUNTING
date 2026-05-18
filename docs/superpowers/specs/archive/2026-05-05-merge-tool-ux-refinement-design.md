# Merge Tool UX Refinement — Design

**Date:** 2026-05-05
**Status:** Design approved (mockup) → ready for implementation plan
**Mockup:** `app/mockups/merge-tool-redesign.html`
**Builds on:** v2.42 design checkpoint (commit `150da19`) — same 4-state structure, this spec adds refinements from a follow-up review session.

## Goal

Refine the v2.42 four-state merge-tool redesign so each screen reads cleaner and the most common interactions take fewer clicks. Focus areas this round: error-resolution sidebar, table chrome, Done-state outputs card, and global navigation.

The 4-state spine itself (Empty → Review → Ready → Done) is unchanged. This spec only describes the deltas; anything not mentioned here matches the v2.42 mockup.

## Non-Goals

- **Manual Merge mode** (drag-reorder of arbitrary PDFs) — out of scope. Will live as a separate sub-tool in a later pass.
- **Mode names / set of modes** — keeping the existing 5 (Per Container / All in One / Invoices Only / PODs Only / BLs Only). User confirmed names read fine.
- **Underlying merge engine** — pdf-lib calls, fetch logic, TMS fallback chain (v2.44) — all unchanged.
- **Empty / Loading / Review / Fetching / Merging states** beyond the table-chrome changes listed below.
- **Status Log / Failure Report panels** from the legacy single-screen merge tool — not re-introduced. Their roles are taken over by inline Review issue cards, the Ready error sidebar, and the Done outputs card.
- **Cancel / abort handling for `+ New Merge` clicked mid-fetch or mid-merge** — out of scope; if hit, the running job is allowed to complete (or canceled by the existing Cancel button on the progress line). Not designing a new abort flow here.

## Refinements

### 1. Sidebar — auto-save, no Save button

The detail sidebar (slides in from the right when an error row is clicked) currently has a footer with `Skip · Prev · Next · Save & Resolve`. New behavior:

- **Auto-save fires on the resolution event itself** — successful Retry API or PDF dropped into the in-sidebar upload zone. No separate save click.
- **Sidebar stays open** after auto-save (no auto-advance). User decides when to move on.
- **Footer is rebuilt:** `Skip this one` (small text link) · `← Prev` (small) · **`Next Error · [N left] →`** (large orange primary button, right-aligned, with a count badge of remaining errors). When on the last error, the button flips to green and reads `Done — close sidebar`.

### 2. Default tab on Ready

When the Ready state has at least one error row, the table opens with the **Errors** tab active by default — not All. Reduces clicks-to-resolution. When there are zero errors, defaults to All as before.

(Mockup demonstrates the All view because that's where the checkbox interaction is visible. Real-app rule: errors-active-on-load when errors exist.)

### 3. Document indicator — filled pills

Replace the dot-plus-label per-doc indicator (`● INV ● POD`) with filled colored pills carrying just the label:

| State | Background | Text | Meaning |
|---|---|---|---|
| `ok` | `#dcfce7` | `#15803d` | Document fetched successfully |
| `fallback` | `#fef3c7` | `#92400e` | Fetched via TMS fallback (e.g., POD not in QBO) |
| `miss` | `#fee2e2` | `#b91c1c` | Searched and not found (hard miss) |
| `queued` | `#f1f5f9` | `#94a3b8` | Not searched yet (pre-fetch / queued) |

Two-state distinction (`miss` vs `queued`) is new — currently the codebase only has "not present." Queued rows in the Fetching state should render `queued` pills, not green `ok`.

### 4. Toolbar — search expands

Above the merge table, the search input + sort dropdown + filter-meta line had a large empty gap (search was fixed at 320px; meta was right-aligned with `margin-left: auto`). Fix:

- Search input: `flex: 1; min-width: 240px` — absorbs the gap by growing
- Search font-size: bumped to `0.92rem` for legibility
- Toolbar font-size: bumped to `0.86rem`
- Filter-meta stays right-aligned

### 5. Remove leftmost ✓/✗/spinner column on post-fetch tables

In Fetching / Merging states, the leftmost column held a status icon (`✓` for fetched, `✗` for failed, spinner for in-progress, `—` for queued). It duplicated what the **Status** column already conveyed and read as cryptic. Drop the column from these states.

In **Ready** state, the leftmost column is a real selection checkbox (see #6) — separate concern, keep it.

In **Review** state (pre-fetch), the leftmost checkbox column is also kept — it's used to deselect duplicates before fetch.

### 6. Ready state — selection checkboxes + live count

The Ready table gets a checkbox column on the left:

- Master checkbox in the header (`<th class="check-col">`) toggles every selectable row at once
- Each fetched (green) row has a checked checkbox by default
- Each error row has a **disabled** checkbox with a tooltip "Fix the error before this can be merged" — the row can't be included until resolved
- The primary `Run Merge` button gets a count badge: `Run Merge · [N selected]` that updates live as the user toggles checkboxes
- Master checkbox state syncs: when all selectable rows are checked, master is checked; when any selectable row is unchecked, master goes unchecked

### 7. Fix Error button — soft red

The per-row Fix Error button moves from "filled saturated red" to the same palette as the `miss` doc-pill:

- Background `#fee2e2`, text `#b91c1c`, border `1px solid #fca5a5`
- Hover: bg `#fecaca`, border `#f87171`, text `#991b1b`
- No saturated drop-shadow

Reads as "needs attention" without screaming.

### 8. Done state — drop the success banner, split outputs into two groups

Two changes wrapped together:

- **Remove the green success banner** at the top of the Done state. The completed row in the outputs list already shows the same info (✓ check, name, file stats).
- **Outputs card splits into two visually distinct groups** with their own headers:
  - **`✓ Completed`** — green-tinted section header with a count badge and a section-level `Open all` button (opens the parent folder containing every completed merge in this session — e.g., `NGL_Merged_2026-05-05/`). Each row shows name, stats, and per-row `Open Folder` + `View` buttons.
  - **`Run another format`** — neutral section header with an "X available" count badge. Each row shows name + teaser. **No `Run This Merge` button per row** — the row is clickable, hover lights up the orange border + "Run →" hint at the right edge.

This collapses issues A (banner duplication + 5-mode flat list), C (Run button noise), and D (done-vs-available not popping) into a single layout change.

### 9. Done-state output rows — drop the file path

Completed rows previously read `38 PDFs · 312 pages · 24.5 MB · /Per-Container/`. The trailing path was wordy and redundant once the `Open Folder` button is right next to it. Drop the path from the visible meta line — keep it as a tooltip on the `Open Folder` button. Result:

```
38 PDFs · 312 pages · 24.5 MB    [Open Folder] [View]
```

### 10. Header navigation — Back to Ready, New Merge

Two new buttons in the tool-header (`.tool-actions` area, before `Agent online` and `Switch Tool`):

- **`← Back to Ready`** — visible only on the Done state (`s4`). Returns to the Ready state with the existing fetched table preserved (no re-fetch).
- **`+ New Merge`** — visible on Review (`s2`), Ready (`s3`), and Done (`s4`). Resets the entire workflow: clears `completedModes`, `lastCompletedMode`, and the parsed Excel/PDF state, then transitions to Empty.

Styling: secondary buttons (white bg, gray border). The `New Merge` button gets a subtle orange tint (`color: #ea580c; border-color: #fed7aa`) since it's the more action-oriented of the two.

## Out-of-band UX rules (not in mockup)

These are rules for the implementation that the static mockup doesn't fully demonstrate:

- **Errors-tab default** (#2) — fires only when errors exist on entering Ready; otherwise All.
- **Master checkbox sync** (#6) — clicking individual checkboxes must update master's checked state.
- **Auto-save trigger conditions** (#1) — fires on (a) successful Retry API response, or (b) `<input type="file">` change event in the sidebar upload zone. The auto-save updates the row's status to "Fetched" (or equivalent) and re-renders the row.
- **Back-to-Ready preserves state** (#10) — the Ready state on return must show the same fetched table that existed before the last merge ran. No re-fetch unless user explicitly clicks somewhere to re-fetch.

## Files affected (implementation surface)

This is a frontend-only change. Agent server code is untouched.

- **`app/index.html`** — tool-header markup (new buttons), state-rendering containers
- **`app/assets/css/styles.css`** — pills, toolbar, sidebar footer, fix-error button, output-group styling, header-action-btn, check-col styling
- **`app/assets/js/merge.js`** — most of the work:
  - state machine (`subMode`, `setState`, `STATES`, `STATE_GROUP`)
  - row-render functions (`rowFetched`, `rowFailedClickable`, etc.) with `withCheck` parameter
  - sidebar logic (auto-save trigger, footer button state, advance-to-next-error)
  - selection logic (`toggleSelectAll`, `updateSelectionCount`)
  - outputs card grouping (`outputsMarkup` split into completed + pending groups)
  - header button visibility (show/hide on state change)

`merge.js` is currently ~640 lines. After this work it will likely grow. If it crosses ~900 lines, consider splitting along state-machine boundaries (one file per state group) — but that's a follow-up call, not part of this work.

## Risks and gotchas

- **Existing v2.44 POD-fallback chain** must keep working. The auto-save (#1) and pill-state mapping (#3) need to map TMS-fallback success to `fallback` (amber), not `ok` (green). Currently the merge tool already has the data — just needs to surface it.
- **Selection state** (#6) is new state that wasn't tracked before. The `state` object will need a `selectedContainers: Set<string>` (or equivalent) so that toggling is preserved across re-renders.
- **Back-to-Ready** (#10) requires not destroying the parsed `excelRows` / `mergeResults` when transitioning into the merging state. Verify the existing state object survives a Ready → Merging → Done → Ready round-trip.
- **Mockup default tab** (#2) — the static mockup currently shows All as active to demonstrate checkboxes. Implementation must wire the real rule (Errors-active-when-errors-exist).

## Acceptance criteria

User can:
1. Drop an Excel, see pre-fetch validation, fix duplicates / missing INV# in Review.
2. Run fetch; watch progress; see queued rows render with `queued` (gray) pills, not `ok`.
3. Land on Ready with the Errors tab active when errors exist; the rendered rows have a Fix Error button styled in the soft-red palette.
4. Click a Fix Error row → sidebar opens → drop a PDF → row auto-saves, sidebar stays open with Next Error promoted to a large orange button showing remaining count.
5. Switch to All tab; toggle individual checkboxes and the master checkbox; watch the Run Merge count badge update live; error rows have disabled checkboxes.
6. Click Run Merge → land on Merging → land on Done.
7. Done state shows the outputs card with two groups (Completed + Run another format), no banner, no path on completed rows.
8. Click any pending row to run another mode (no per-row buttons).
9. Click `← Back to Ready` in the header to return to the Ready table with state preserved.
10. Click `+ New Merge` to reset and start over.
