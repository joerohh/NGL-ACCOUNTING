# AR Dashboard — Make Build the Primary Empty-State UI

**Date:** 2026-06-03
**Status:** approved
**Owner:** Joseph Roh
**Related:**
- `docs/superpowers/specs/2026-05-20-ar-dashboard-design.md` — original R1/R2 spec
- `docs/superpowers/plans/2026-06-01-ar-dashboard-r2-build-engine.md` — R2 plan
- `memory/project_ar_dashboard_wip.md` — current status

---

## 1. Context

The current AR Dashboard empty state leads with a large drop zone for **loading a pre-built `AR_AGING_MM_DD_YYYY.xlsx`**, with a small text link `"Or build today's workbook from scratch →"` underneath. Clicking the link opens a modal containing five drop slots (yesterday's workbook + 4 daily source files).

That ordering reflects R1's original mental model where the dashboard was a *reader* of pre-built workbooks. R2 M2 changed the reality: the build engine is now the actual daily workflow — Jihyun (or whoever ends up using the tool) will be building the workbook from sources every morning, not pasting in a pre-built one. The empty state needs to match the new daily job.

## 2. Goal

Flip the empty state so that **building today's workbook is the headline action**, and loading a pre-built workbook becomes a smaller secondary affordance.

This is a focused UI/UX change. No engine, writer, or output-format changes are in scope.

## 3. Scope

### In scope
- Replace the current empty-state layout with the 5-drop UI inlined directly on the page.
- Move the "Drop AR aging workbook" zone to a smaller secondary section below the build flow.
- Eliminate the *drop step* of the build modal (its content moves to the page); preserve the **preview step** of the build modal (the summary + KPIs + Save flow).
- Add a "Build a different day" affordance to the loaded state so the user can return to the build flow without restarting the app.

### Out of scope
- Dashboard viewer redesign (the "Today's Worklist" concept) — parked until there's enough Jihyun-workflow observation to redesign responsibly. Reference mockup saved at `app/mockups/ar-dashboard-redesign-today.html`.
- Engine, writer, or output-format changes.
- Edit-in-place, detail panels, exception worklists — these were never part of this change.

## 4. Design

### 4.1 Empty-state layout

When `arState.loaded === false`, the AR Dashboard view renders:

1. **Page header** — `Build today's AR workbook` + subtitle + today's date.
2. **5 drop slots stacked vertically**:
   1. Yesterday's workbook (`AR_AGING_*.xlsx`)
   2. QBO Daily Collection (`NGL Transportation, Inc._Daily Collection Report.xlsx`)
   3. QBO Daily Schedule (`NGL Transportation, Inc._Daily Schedule List.xlsx`)
   4. TAB BANK Remittance (`Collection_Payment.xlsx`)
   5. TMS Reconcile (`APAR RECONCILE_*.xlsx`)
   Each slot supports drag-drop or click-to-browse. Multi-file drop on any zone auto-routes by filename match (same logic as the current modal).
3. **Build footer** — progress pill (`N of 5 ready`) + **Build →** primary button (disabled until all 5 are ready) + Cancel returns to a true empty state.
4. **Visual separator** (horizontal rule + space).
5. **Secondary "Already have a pre-built workbook?" section** — smaller heading + smaller drop zone that accepts `AR_AGING_MM_DD_YYYY.xlsx`. Behavior identical to the current primary drop zone.

### 4.2 Modal becomes preview-only

The current build modal has two visible states (`renderDrop()` and `renderPreview()`). After this change:
- `renderDrop()` is **deleted** — its content is the empty-state page.
- `renderPreview()` is **kept** — opens after the user clicks **Build →**. Shows today's totals, KPI tiles (new / paid / adjust / exception), and the Save flow.
- `arOpenBuildModal()` is **renamed** to `arOpenBuildPreview(result)` and is called only from the page-level Build button after the engine produces a `buildResult`.

### 4.3 Loaded state — return to the build flow

The existing loaded view already has a `Load different workbook →` link in the top data bar (`#arUnloadBtn`, `app/assets/js/tools/ar-dashboard/ar-dashboard-views.js:42`) that clears `arState` and re-renders the empty state. After this change the empty state IS the build flow, so the link continues to work — relabel it to `Build different day →` so it accurately describes where it goes. No new button is added; the existing click handler is unchanged.

### 4.4 State transitions

```
Empty state (page is the build flow)
    │
    │ user drops files into slots
    ▼
Empty state + N/5 ready
    │
    │ user has 5/5 + clicks "Build →"
    ▼
Engine runs (arBuildToday()) — same as today
    │
    ▼
Preview modal opens (renderPreview)
    │
    │ user clicks Save
    ▼
Workbook written via arBuildWriteWorkbook()
    │
    ▼
Workbook auto-loads → arState.loaded = true
    │
    ▼
Loaded view renders ("Build a different day" available to return)
```

## 5. Implementation outline

Files to modify:
- `app/assets/js/tools/ar-dashboard/ar-dashboard.js` — rewrite `renderEmptyState()` as a thin shell that mounts the build flow.
- `app/assets/js/tools/ar-dashboard/ar-dashboard-build-ui.js` — refactor in place. The slot definitions, file routing, parsing, drag/drop, and engine kickoff move into a new `arRenderBuildPage(view)` exported function the empty state calls. `renderPreview()` stays as a modal. `arOpenBuildModal()` is removed; the page-level renderer drives the same state machine. The preview modal is opened from the page-level Build button.
- `app/assets/js/tools/ar-dashboard/ar-dashboard-views.js` — relabel the existing `#arUnloadBtn` from `Load different workbook →` to `Build different day →` (one-line text change; click handler unchanged).
- `app/assets/css/styles.css` — add new `.ar-build-page*` classes for the page-level layout (centered column, max-width ~720px). Keep the existing `.ar-build-modal*` classes — they still drive the preview modal.

No engine or writer changes.

## 6. Acceptance criteria

1. Opening the AR Dashboard with no workbook loaded shows the 5-drop build UI as the primary surface; the pre-built drop zone is visually smaller and below.
2. Dropping all 5 files + clicking Build → opens the preview modal and shows correct totals (no regressions vs. v2.78.8).
3. Saving from the preview modal writes the workbook and auto-loads it into the dashboard.
4. The loaded view has a visible **Build a different day** affordance that returns to the empty state.
5. The secondary drop zone for pre-built workbooks still works (drop or click to load an existing `AR_AGING_*.xlsx`).
6. No regressions in the 99.71% correctness verified in v2.78.7/v2.78.8.

## 7. Open questions

None. Approved 2026-06-03.

## 8. Risks

- **Visual regression risk** — the existing modal CSS was tuned for an overlay context; reusing it for a full-page context may need padding/sizing adjustments. Catch via visual diff before ship.
- **State machine confusion** — eliminating the drop modal removes one layer of UI state. The page-level renderer needs to handle the same hover/drag/drop/error states the modal handled. Easy to miss an edge case.
- **No collision risk on the loaded view** — verified the loaded view only has the single `#arUnloadBtn` link; we are relabeling it, not adding a new control.
