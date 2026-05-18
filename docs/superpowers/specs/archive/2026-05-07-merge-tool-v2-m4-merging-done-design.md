# Merge Tool v2 — M4 (Merging + Done)

**Date:** 2026-05-07
**Status:** Design approved, ready for plan

## Goal

Ship M4 of the merge-tool v2 redesign: wire the merge engine into the new layout, add a dedicated **Merge screen** that doubles as the mode-picker and the Done state, persist completed merges as the user runs more modes in the same session, and write outputs into an organised on-disk folder structure under a user-chosen location.

This builds on M3 (Fetching + Ready) which shipped through v2.51.0. The 4-state spine from the parent spec (`docs/superpowers/specs/2026-05-05-merge-tool-ux-refinement-design.md`) is updated: the original separate **Merging** progress state and **Done** outputs state collapse into one screen that the user keeps returning to.

## Non-goals

- M5 (any items the parent spec deferred past Done).
- Changes to the Empty / Loading / Review / Fetching / Ready states beyond what's required to wire the new Continue-to-Merge button on Ready.
- Backwards compatibility with v1's merge tool. v1 still ships at the legacy `/merge` route; v2 is the new path.
- Email-the-output, share links, cloud sync, or other features beyond what's listed below.

## State machine update

Adds one new state group to v2:

```
Empty → Loading → Review → Fetching → Ready → Merge → Merge (with completed cards)
                                          ↑       │
                                          └───────┘  ← Back to Ready
                                          ↑       │
                                          └───────┘  ← + New Merge → Empty
```

Note that **Merge** is one screen, not two. Running a merge mode never leaves the screen; it triggers a brief in-card "merging…" progress overlay, then the card flips to its completed state. The screen accumulates completed cards as the user runs more modes.

## 6 merge modes

The Merge screen lays out 6 mode cards in two visually distinct groups, each with a title and a single-line description:

### Per-container outputs (one PDF per container)

| Mode | Description |
|---|---|
| **Per Container** | One PDF per container. Each file contains that container's invoice and its document combined. |
| **Per Container — Invoice Only** | One PDF per container, containing only the invoice. |
| **Per Container — Document Only** | One PDF per container, containing only the supporting document (POD, BOL, POL, IT, or ITE). |

### Single combined output (one PDF total)

| Mode | Description |
|---|---|
| **Combined PDF** | Single PDF with every invoice and document stacked into one big file. |
| **Invoice Only** | Single PDF containing all the invoices. |
| **Document Only** | Single PDF containing all the supporting documents. |

The 6-mode list is a deliberate departure from v1's 5 modes. v1 had `pod-only` + `bl-only` as separate modes; the new fetch chain (POD → BOL → POL → IT → ITE) hides that distinction, so the user-facing concept is just "the document" — collapsing both into a single `document-only` mode. The three per-container content variants are new and serve the Invoice Sender (one PDF per invoice) and portal-upload workflows.

## Flow

### Ready → Merge

The existing Run Merge button on Ready becomes **Continue to Merge**. It is enabled only when:

- Fetching is complete (no rows in `queued` state), AND
- At least 1 row is checked in the table.

Clicking it:

1. If any non-errored rows are unchecked → show the unchecked-rows confirmation popup (described below). On Continue, proceed; on Go Back, stay on Ready.
2. Otherwise → transition straight to Merge screen with the locked row selection.

### Merge screen — first visit

On first entry, all 6 mode cards are rendered in their **pickable** state: title, description, and a subtle hover lift. No card is "selected" by default.

Clicking any card:

1. The card flips to a **running** state with an inline spinner and a "Merging…" label.
2. The merge engine runs synchronously in the browser using pdf-lib (reusing the v1 engine — see "Engine reuse" below).
3. When complete, the card flips to its **completed** state with a green check mark, stats line, and three action buttons (described below). The other 5 cards stay pickable.
4. Files are written to disk under the user-chosen location (described below).

### Merge screen — accumulating completed cards

As the user runs more modes, completed cards persist alongside pickable cards. The screen never "resets" until the user clicks **+ New Merge** or **Back to Ready** + something on Ready that mutates the row set.

The 6 cards are always rendered in the same order, regardless of which ones are completed. Completed cards visually dominate (green check + stats + buttons take up more vertical space than a pickable card's title + description).

### Completed card content

```
✓ Per Container — Invoice + Document
38 PDFs · 312 pages · 24.5 MB · 2:34 PM
[Open File]   [Open Folder]   [Re-run]
```

- **Open File** — Opens the merged PDF in the OS default viewer. **Shown only for single-output modes** (Combined PDF, Invoice Only, Document Only). Hidden for the three Per Container modes — they produce many files, and opening 50+ PDFs at once is hostile; for those modes only Open Folder + Re-run are shown.
- **Open Folder** — Opens Explorer/Finder at the folder containing the merge output. Always shown.
- **Re-run** — Re-runs the merge with the current row selection. Silently overwrites the existing date folder for that mode (see "Same-day re-run behavior" below). Always shown.

### Back to Ready

A header button **← Back to Ready** is visible only on the Merge screen. Clicking it returns to Ready with:

- The fetched row table preserved (no re-fetch).
- The current row check selection preserved.
- All previously completed merge cards preserved in state — clicking Continue to Merge again returns to the same Merge screen with the same completed cards still present.

### + New Merge

The existing **+ New Merge** button (visible on Review, Ready, and Merge) resets the workflow to Empty. It clears the parsed Excel/PDF state, all merge results, all completed cards, and the row selection. (No confirmation popup — user already has clear intent when clicking this.)

## Pre-merge confirmation popup

Fires when the user clicks **Continue to Merge** on Ready and at least one row is unchecked. Modal centred both axes over a dimmed slate backdrop.

### Wording

```
ⓘ  Confirm merge selection

3 rows are unchecked and will not be included in this merge.

       [Go Back]    [Continue ▶]
```

The number is the count of unchecked rows (errored or not). No row list — just the count. If the count is zero, no popup is shown and we proceed straight to Merge.

### Visual specifications

- White card, `border-radius: 12px`, `box-shadow: 0 20px 40px rgba(0,0,0,0.15)`
- Centred via `position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%)`
- Width 440px, padding 28px
- Title in `#0f172a` (slate-900), 1rem, semibold
- Body text in `#475569` (slate-600), 0.88rem
- Backdrop `rgba(15,23,42,0.45)` covering the full viewport (matches existing M3 sidebar backdrop)
- **Continue** is the orange primary button (`#ea580c` background, white text), same shape as the existing Run Merge button
- **Go Back** is the neutral secondary (white background, `#e2e8f0` border, slate text), same shape as existing header buttons
- Buttons gap 8px, right-aligned

This same modal pattern is reused for any future confirmations (out of scope for M4 but worth noting for visual consistency).

## Default checkbox behavior

The check column on the Ready table changes from M3's "errored = disabled" to a softer interaction model:

- **Successful row** → checked by default, fully interactive.
- **Errored row** → unchecked by default but **interactive** (no longer disabled). The user can manually check it.
- **Errored row that gets fixed** (via sidebar Retry API or PDF drop) → flips to checked automatically (M3's auto-save trigger already handles this; we just remove the `disabled` attribute).

### What an errored-but-checked row produces

If an errored row is included in the merge (user explicitly checked it), the merge engine includes only the **invoice page** for that row. The missing document is silently skipped — no error log entry per row, just absent from the output. The Merge screen's stats line reflects this: page counts will show fewer pages than for fully-fetched rows.

This is a small but meaningful UX win: the user is no longer forced to either fix every error or skip the row entirely. They can choose "ship the invoices, deal with the missing PODs separately."

## File structure

Outputs are written under a single root folder named **`Merge Outputs/`**, inside a parent location chosen by the user.

```
[parent location]/
└── Merge Outputs/
    ├── Per Container/
    │   └── 2026-05/
    │       └── 2026-05-07/
    │           ├── KMTU3920184_LM26050100F.pdf
    │           ├── TCNU5839271_LM26050101F.pdf
    │           └── …
    │
    ├── Per Container — Invoice Only/
    │   └── 2026-05/
    │       └── 2026-05-07/
    │           └── …
    │
    ├── Combined PDF/
    │   └── 2026-05/
    │       └── 2026-05-07/
    │           └── Combined_2026-05-07.pdf
    │
    ├── Invoice Only/
    │   └── 2026-05/
    │       └── 2026-05-07/
    │           └── Invoices_2026-05-07.pdf
    │
    └── Document Only/
        └── 2026-05/
            └── 2026-05-07/
                └── Documents_2026-05-07.pdf
```

### Date format

- Month folder: `YYYY-MM` (e.g., `2026-05`)
- Date folder: `YYYY-MM-DD` (e.g., `2026-05-07`)

ISO format chosen so folders sort alphabetically in Explorer / Finder — most recent batch is always at the bottom of the listing.

### Output filenames

| Mode | Filename pattern |
|---|---|
| Per Container | `{container}_{INV#}.pdf` |
| Per Container — Invoice Only | `{container}_{INV#}_INV.pdf` |
| Per Container — Document Only | `{container}_{INV#}_{DOC}.pdf` where `{DOC}` = POD/BOL/POL/IT/ITE |
| Combined PDF | `Combined_{YYYY-MM-DD}.pdf` |
| Invoice Only | `Invoices_{YYYY-MM-DD}.pdf` |
| Document Only | `Documents_{YYYY-MM-DD}.pdf` |

If a row's container number is missing or empty, fall back to the WO# (matching the v2.47 invoice-grouping fix). If both are missing, fall back to the INV#.

### Location picker

The Merge screen header carries a small button labelled **Output location: `[short path]`** (e.g., `Output location: Desktop`). Clicking it opens the system folder picker. On first use, the default is the user's Desktop.

The chosen absolute path is persisted in `localStorage` under the key `mergeV2OutputLocation`. The path is restored on each session start. If the path no longer exists (e.g., user deleted the folder), the picker falls back to Desktop and prompts the user to re-pick on the next merge.

### Same-day re-run behavior

If the user runs the same mode twice on the same calendar day (e.g., morning Per Container, then afternoon Per Container after dropping a late PDF), the second run **silently overwrites** the existing date folder for that mode. The folder is cleared first, then re-populated.

This applies to both the Re-run button on a completed card AND clicking a previously-completed card on the Merge screen after a Back-to-Ready round-trip on the same day.

This was a deliberate trade-off: keeping a clean filesystem is more valuable than preserving intermediate runs. The user can always run the merge tool again from a fresh Excel file, and a different day creates a different date folder.

## Engine reuse

The v1 merge engine in `app/assets/js/tools/merge/merge.js` already implements the per-container, all-in-one, and by-type merges. M4 reuses these:

- `mergePerContainer()` → drives the new **Per Container** mode unchanged.
- `mergeAllInOne()` → drives the new **Combined PDF** mode unchanged.
- `mergeByType('invoice')` → drives the new **Invoice Only** mode unchanged.
- `mergeByType('pod')` and `mergeByType('bl')` are no longer used directly — replaced by:
- New `mergeDocumentOnly()` → produces a single PDF stacked from each row's fetched document (POD/BOL/POL/IT/ITE), in row order. Implementation is `mergeByType` generalised over the `classifyPdf()` document categories minus `invoice`.

Three new per-container content variants are needed:

- `mergePerContainerInvoiceOnly()` → one PDF per container with only the invoice.
- `mergePerContainerDocumentOnly()` → one PDF per container with only the document.

These are simple variants of `mergePerContainer()` — same iteration, but the inner PDF assembly only includes pages from the matching invoice or matching document.

## Files affected

This is mostly frontend work. Agent server gets one small extension.

### Frontend

- `app/index.html` — Merge screen markup (mode-card grid, popup), header buttons (Continue to Merge replaces Run Merge, Back to Ready, Output location).
- `app/assets/css/styles.css` — Mode card styling (pickable + completed states), popup styling, completed-card action buttons.
- `app/assets/js/tools/merge/merge-v2.js` — Most of the work:
  - State machine extension (Merge state, completed-cards data structure).
  - Mode card rendering (6 cards in 2 groups, with pickable / running / completed sub-states).
  - Continue-to-Merge button + unchecked-rows popup.
  - Mode-card click handler (run merge, update card state, write files).
  - Open File / Open Folder / Re-run handlers.
  - Output location picker + localStorage persistence.
  - Errored-row checkbox interaction model (remove `disabled`, default unchecked, auto-check on fix).

### Agent

- `agent/routers/files.py` — Extend the existing save endpoint (or add a new `/files/save-batch` endpoint) to:
  - Accept a nested relative path under a base location (e.g., `Merge Outputs/Per Container/2026-05/2026-05-07/foo.pdf`).
  - Create intermediate folders as needed.
  - Optionally clear an existing folder before writing (for the same-day overwrite behavior).

The agent already has `/files/save` for single-file writes; the new requirement is nested-path handling and folder clearing. No new dependencies.

## Acceptance criteria

User can:

1. From Ready with all rows checked, click **Continue to Merge** → land on the Merge screen with 6 mode cards in 2 groups, no popup.
2. From Ready with one or more rows unchecked, click **Continue to Merge** → see the confirmation popup with the count of unchecked rows → Continue lands on Merge, Go Back stays on Ready.
3. On the Merge screen, click any mode card → see brief in-card progress → card flips to completed with stats line and three action buttons. The other 5 cards remain pickable.
4. Click a second mode card → it runs → both cards now show as completed.
5. Click **Open File** on a completed card → the merged PDF opens in the OS default viewer (or the folder opens for per-container modes).
6. Click **Open Folder** on a completed card → Explorer / Finder opens to the folder containing that mode's output.
7. Click **Re-run** on a completed card → that mode re-runs and overwrites the existing date folder for that mode.
8. Click **← Back to Ready** → return to Ready with the same row selection AND the same completed cards preserved (clicking Continue to Merge again returns to the same Merge screen state).
9. Click **+ New Merge** → workflow resets to Empty.
10. Files are written under `[chosen location]/Merge Outputs/[Mode]/YYYY-MM/YYYY-MM-DD/...` with the filename patterns listed above.
11. Click the **Output location** button on the Merge screen header → system folder picker opens → chosen path is remembered for future sessions.
12. An errored row whose checkbox the user manually checks is included in the merge with only its invoice page.

## Risks and gotchas

- **Nested folder writes** — The agent's existing `/files/save` may not support nested paths or directory creation. This needs a small extension before frontend wiring.
- **Folder picker in Electron** — `window.showDirectoryPicker()` is the standard modern API. Verify the Electron version in use supports it without security flags. If not, fall back to an agent endpoint that opens a native dialog via Tk or pywebview.
- **Persisting the chosen location** — `localStorage` holds the path string. If we ever switch to the File System Access API with persistent file handles, the rules change (handles need IndexedDB and a permission prompt on each session). For M4 we stick with the path string + agent-side writes.
- **Re-run silent overwrite** — Must clear the target folder (not just write over individual files), otherwise stale files from a partial earlier run pollute the directory. The agent endpoint should accept an `overwrite_folder: true` flag.
- **Errored-but-checked row** — The merge engine must tolerate a row that has an invoice but no document. Currently `mergePerContainer` and friends iterate over rows and look up matched files; missing files already yield row-level skips with a failure log entry. We need to confirm the row-level skip doesn't fail the whole batch when the user explicitly checked the row.
- **State preservation across Back-to-Ready round-trip** — The completed-cards state must live on the v2 state object (not on Merge-screen-local DOM) so that clicking Back to Ready then Continue to Merge restores the same Merge screen.
- **Row-selection drift after Back-to-Ready** — If the user goes Back to Ready, changes which rows are checked, then clicks Continue to Merge, the previously-completed cards still represent the *old* row set on disk. The Merge screen still shows them as completed. We accept this on purpose — the completed card represents what's on disk right now, which is historical truth. If the user re-runs that mode, the new run uses the new row selection and overwrites the date folder. Stats line on a "stale" completed card is therefore allowed to mismatch the current row count.
- **Mode card re-render during run** — While one mode is running, the other 5 cards should remain visually responsive but their click handlers should be disabled (visual: subtle opacity dim). Avoid double-merge concurrency.

## Out-of-band UX rules

- **Continue to Merge button enabled only** when fetching is complete AND at least 1 row is checked. Otherwise greyed out with a tooltip ("Fix errors or check at least one row").
- **Mode card click disabled while a merge is in progress.** Visual: subtle dim on other cards; spinner only on the running card.
- **Re-run button on a completed card** behaves identically to clicking the card itself (same merge mode, same row selection, same overwrite).
- **Output location button** on the Merge screen header is rendered as a secondary button (white bg, gray border, with the short folder name and a folder icon).
- **Same-day overwrite** triggers no popup or confirmation — the user implicitly opted in by clicking Re-run or by clicking a completed card.
- **Zero rows checked at the time of Continue-to-Merge click** — button is disabled, so this is unreachable. (Defence-in-depth: the click handler also checks and silently no-ops if it somehow reaches the disabled state.)

## Open questions and follow-ups

None for M4 itself. Possible M5 candidates that are explicitly out of scope here:

- Email integration (open default mail client with the merged file pre-attached).
- Cross-session history (re-open a previous date's merge run).
- Per-mode default toggling (e.g., "always run Per Container automatically on Continue-to-Merge").

## Version target

v2.52.0 — first M4 release. May be followed by v2.53.x hardening passes if real-world use surfaces issues, matching the M3 cadence (v2.48 → v2.51).
