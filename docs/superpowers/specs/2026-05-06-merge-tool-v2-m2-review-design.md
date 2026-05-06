# Merge Tool V2 — Milestone 2 (Review state) — Design

**Date:** 2026-05-06
**Status:** Design approved → ready for implementation plan
**Project:** Merge-tool UX redesign (5-milestone port of v2.42 mockup)
**Builds on:** M1 foundation (v2.45.0, 2026-05-05) — Settings toggle, view container, scoped CSS, state machine with Empty / Loading / Review-stub
**Mockup:** `app/mockups/merge-tool-redesign.html` (sections 1193-1257 are the Review state)
**Parent spec:** `docs/superpowers/specs/2026-05-05-merge-tool-ux-refinement-design.md`

## Goal

Replace the M1 Review-state stub with a working pre-fetch review screen. After the user drops an Excel manifest, M2 parses it, runs validation (duplicate detection + missing-invoice detection), and displays the result in either:

- a **success card** if every row is clean, or
- a **table** with All / Issues tabs, sort, and search if any row has a problem.

The Fetch button at the end of M2 transitions to the M3 fetching stub. Actual document fetching is M3's job.

## Non-Goals

- Document fetching (QBO / TMS calls) — M3.
- Customer-name lookup from QBO or TMS when the Excel doesn't supply one — out of scope per the user; the Customer column shows whatever the Excel has and a dash otherwise.
- Headerless Excel files (e.g., a QBO export with no first-row column names) — out of scope. Expected files include a header row. (Both sample files we tested do.)
- Touching the legacy merge tool (`merge.js`, `state.excelRows`, `mergeToolView`) — v2 stays isolated behind the beta toggle.
- Manual Merge mode (drag-reorder of arbitrary PDFs) — separate sub-tool, later milestone.
- Selection-state handoff to the agent for fetching — recorded in v2 state, but consumed in M3.

## Data model

A new `rows[]` array on the existing module-local `v2State` in `merge-v2.js`. Each row:

```js
{
  rowNum: 5,                    // 1-indexed Excel row (data row 1 = sheet row 2)
  containerNumber: 'KKFU7654819',
  invoiceNumber: '24-13892',    // '' when missing
  customer: 'FREIGHT FLEX LLC', // '' when no column matched OR cell empty
  selected: true,               // checkbox state — auto-set by validation, user can toggle
  status: 'ok',                 // 'ok' | 'dup-same-inv' | 'dup-diff-inv' | 'miss-inv'
  statusReason: '',             // human text shown under the badge
}
```

`v2State` also gains:

- `searchQuery: ''` — current search box value
- `sortMode: 'excel'` — `'excel' | 'container' | 'invoice' | 'issues-first'`
- `activeTab: 'all'` — `'all' | 'issues'`
- `excelHeaders: []` — captured for diagnostics (not displayed; useful when alias matching fails)

The legacy `state.excelRows` is **not touched**. v2 owns its own state until M5 (cut-over) removes the legacy tool.

## Excel parsing

`handleExcelChange()` in `merge-v2.js` already wires the file picker. Replace the M1 simulated-load `setTimeout` with a real parser:

1. Read the file as `ArrayBuffer` (use the same `readAsArrayBuffer()` helper from `shared/utils.js`).
2. Parse with the global `XLSX` (already loaded via CDN in `index.html` for the legacy tool).
3. Read sheet 1 as JSON.
4. **Fail fast** if the sheet is empty or has no recognizable Container column — show an inline error in the loading-state card and stay there until the user picks another file. (Replaces the M1 unconditional 1.2s transition.)
5. Run column detection (next section).
6. Build `rows[]` (one entry per Excel row, preserving sheet order).
7. Run validation (next section).
8. Transition to the `review` state.

### Column detection

Reuse `findColumnKey()` and `normalizeHeader()` from `shared/utils.js`. Use the existing alias lists in `CSV_ALIASES`:

| v2 column | First-pass aliases | Fallback aliases |
|---|---|---|
| Container | `CSV_ALIASES.containerNumber` (`equipment`, `cont`, `cntr`, `containernumber`, …) | — |
| Invoice # | `CSV_ALIASES.invoiceNumber` (`inv`, `invoice`, `invoiceno`, …) | — |
| Customer | `CSV_ALIASES.customerName` (`customer`, `name`, `client`, `clientname`, …) | `CSV_ALIASES.customerCode` (`billto`, `customercode`, `custid`, …) |

Customer detection prefers the friendly-name list first; if no friendly-name header is found, it falls back to the code list. The two sample files this targets:

- **`NGL INVOICE 05.05.2026 (1).xlsx`** — `EQUIPMENT` → container, `INV #` → invoice, `NAME` → customer (displays `FREIGHT FLEX LLC`).
- **`idea nouva weekly 04.13-04.19_formatted.xlsx`** — `CONT NO` → container, `INV#` → invoice, `BILLTO` → customer (displays `IDEANU01` because no friendly-name column exists).

If no Container column is detected, parsing fails and the loading state shows `Couldn't find a Container column. Looked for: Container, Cont #, Equipment, …`. The user replaces the file or fixes the headers.

If no Invoice column is detected, parsing succeeds but every row is tagged `miss-inv` (the entire batch will fetch by container).

If no Customer column is detected, every row's `customer` is `''`.

## Validation rules

After parsing, walk `rows[]` once in Excel order, maintaining `Map<containerLower, firstRowNum>`. For each row:

| Situation | `status` | `selected` default | `statusReason` |
|---|---|---|---|
| First time we see this container, invoice # present | `ok` | `true` | `''` |
| First time we see this container, invoice # missing | `miss-inv` | `true` | `No invoice number — we'll search by container instead` |
| Container seen earlier, invoice # matches first occurrence | `dup-same-inv` | `false` | `Exact duplicate of row {N} — will be skipped` |
| Container seen earlier, invoice # differs from first occurrence (or one of them is missing) | `dup-diff-inv` | `false` | `Same container as row {N}, but different invoice number` |

`{N}` is the `rowNum` of the first occurrence. Container comparison is case-insensitive (`.toLowerCase()`). Empty / whitespace-only container cells are skipped (not added to `rows[]` at all — same as the legacy parser).

**Status priority** (when more than one rule matches): `dup-same-inv` > `dup-diff-inv` > `miss-inv` > `ok`. A duplicate that's also missing invoice # is shown as a duplicate.

## UI rendering

`renderReview()` returns one of two layouts depending on whether issues exist.

### Top bar (always shown)

```
┌──────────────────────────────────────────────┐
│ [XLS]  NGL INVOICE 05.05.2026.xlsx           │
│        40 unique containers · 3 issues       │
│                              [Replace]       │
└──────────────────────────────────────────────┘
```

Reuses `.top-bar` + `.file-summary` styles already shipped in M1. The `Replace` button calls `setStateV2('empty')` (which already resets state).

### A. Success path (zero issues)

When `rows.every(r => r.status === 'ok')`:

```
┌─────────────────── Top bar ───────────────────┐

       ✓  All 40 rows checked out
       Ready to fetch documents.

       [ Fetch 40 Documents → ]

       Show all 40 rows ▼
```

A new `.review-success-card` block (large green card, centered, similar visual weight to the empty-state's big-drop). The Fetch button is the only primary action. The "Show all N rows ▼" link reveals the table inline (no new state) — same toolbar, search, sort, and checkbox columns as the issues path, but with no tabs row (since there are zero issues, there's nothing to filter to). Clicking the link again collapses the table.

The success card is rendered inside `.centered-stage` to match the rest of the v2 layout system.

### B. Issues path (one or more issues)

```
┌─────────────────── Top bar ───────────────────┐
┌─────────────────── Summary banner (yellow) ──┐
│  3 issues in your manifest — review the rows │
│  below, then start the fetch.                │
│                       ● 37 ok  ●  3 issues   │
└──────────────────────────────────────────────┘
┌─ Tabs ──────────────────────────────────────┐
│ [All 40] [Issues 3]              [Fetch 38] │
└─────────────────────────────────────────────┘
┌─ Toolbar ───────────────────────────────────┐
│ [search…           ] [Sort: Issues first ▼] │
│                       40 unique · 3 issues  │
└─────────────────────────────────────────────┘
┌─ Table ─────────────────────────────────────┐
│ ☐  Row  Container  Invoice  Customer  Valid │
│ ☑   4  KKFU76548…  24-13892  KMTC LINE  ─  │
│ ☐  17  KKFU76548…  24-13905  KMTC LINE  ⚠ DUP — Same container as row 4… │
│ ☑  31  TCLU88307…  ─        EVERGREEN  ⚠ MISSING — No invoice number… │
│ …                                           │
└─────────────────────────────────────────────┘
```

CSS is already in `styles.css` from M1: `.review-card`, `.val-badge.ok|dup|miss`, `.merge-table`, `.tabs-row`, `.toolbar`, `.check-col`, etc. — all scoped to `#mergeToolViewV2`. Nothing new to write.

#### Tabs

- `All [N]` — every parsed row.
- `Issues [N]` — only rendered when at least one row has `status !== 'ok'`. The tab gets the `has-issues` modifier class so the count badge renders red.
- **Default-active rule:** if `Issues` is rendered, it opens active. Otherwise `All` is the only tab and is active by default. (The success-path expansion described above renders the table with no tabs row at all.)
- The active tab is stored in `v2State.activeTab` and toggled by clicks.

#### Fetch button

Lives at the right end of the tabs row (per mockup). Label: `Fetch [N] Documents`, where `N` is the count of `selected` rows (master selectable rows excluding deselected ones).

- If `N === 0`, the button is disabled with a `title` of `Check at least one row to fetch`.
- Click → `setStateV2('fetching')`. M2 stops there; the M1 fetching stub renders.

#### Toolbar

- **Search** — case-insensitive substring match on `containerNumber`. Updates on `input` event (no debounce — 200 rows is trivial). Empty query = show all rows for the active tab.
- **Sort dropdown** — four options:
  - `Excel order` *(default)* — by `rowNum`.
  - `Container #` — `localeCompare` with `{ numeric: true }`.
  - `Invoice #` — same; empty invoices sort last.
  - `Issues first` — `status === 'ok'` rows last; among issues, sort by `rowNum`. Within the ok group, also sort by `rowNum`.
- **Filter-meta line** (right side) — `40 unique · 3 issues` style summary. Updates with the underlying counts; doesn't change with search/sort.

#### Table

Six columns:

| # | Column | Notes |
|---|---|---|
| 1 | ☐ | `<input type="checkbox" class="row-check">`. Header row has a "select all" checkbox that toggles every visible (filtered) row's `selected`. |
| 2 | Row | `rowNum`. Muted color. |
| 3 | Container | Monospace. |
| 4 | Invoice # | Monospace + muted. Shows `— missing —` (red) when empty. |
| 5 | Customer | Plain. Shows `—` (muted) when empty. |
| 6 | Validation | The `.val-badge` (none for `ok`, `Duplicate` for `dup-*`, `Missing Inv #` for `miss-inv`) plus the `statusReason` line under it. |

Issue rows get the `.row-issue` class for the tinted background.

The table body is re-rendered on any change (tab, search, sort, master-select toggle). We do not re-render the whole `renderReview()` block — only the `<tbody>` — so the search input doesn't lose focus mid-keystroke.

### Selection — master and per-row

- Per-row checkbox toggles `rows[i].selected` and re-renders the tbody (so the master checkbox visual + Fetch button count update).
- Master checkbox in the header has three visual states managed manually:
  - **Checked** if every row in the *currently visible* set has `selected === true`.
  - **Unchecked** if every visible row has `selected === false`.
  - **Indeterminate** otherwise (CSS handles via `el.indeterminate = true`).
- Clicking master sets every visible row's `selected` to either `true` or `false` (depending on its prior state — like a typical "select all" toggle).
- The Fetch button count is `rows.filter(r => r.selected).length`. (Counts across all rows, not just visible ones — selection persists through search/sort.)

## State transitions

| From | Trigger | To | Notes |
|---|---|---|---|
| `empty` | User picks Excel file | `loading` | M1 already wires this. |
| `loading` | Parse + validate succeed | `review` | Replaces M1's hardcoded `setTimeout`. |
| `loading` | Parse fails (no rows / no Container column) | `loading` (with error message) | New: stay put, show inline error, let user replace. |
| `review` | Click `Replace` | `empty` | Already wired in M1 via `setStateV2('empty')`. |
| `review` | Click `Fetch [N] Documents` | `fetching` | M1 stub renders. |

The `+ New Merge` header button (visible on `review`/`ready`/`done` per parent spec) already routes to `empty` from M1 — no change needed.

## Files affected

Frontend-only, beta-toggle scoped:

- **`app/assets/js/tools/merge/merge-v2.js`** — most of the work. Real `renderReview()`, real Excel parsing in `handleExcelChange`, validation, sort/search, selection helpers, table re-render function.
  - At ~160 lines today, expected ~450-500 after M2.
- **`app/assets/css/styles.css`** — one new block: `.review-success-card` (the all-clear green card). Existing `#mergeToolViewV2`-scoped rules cover everything else.
- **`app/index.html`** — no changes expected. The hidden file inputs and `v2WorkArea` container are already in place.
- No new CDN dependencies. `XLSX` is already loaded for the legacy tool.

## Risks and gotchas

- **Search input losing focus** — re-rendering the entire `renderReview()` markup on every keystroke would clobber focus. Only the `<tbody>` re-renders during search/sort/select; the surrounding chrome (toolbar, tabs, summary banner) stays mounted.
- **Selection persists across sort/search** — works because `selected` lives on the row object, not the DOM. Re-rendering the tbody just reads the same flag.
- **Master checkbox `indeterminate`** — must be set imperatively in JS (`el.indeterminate = true`) because HTML attributes don't support it. Also re-applied after every tbody re-render.
- **Empty Excel / no Container column** — parsing must fail loudly in the `loading` state. The user shouldn't end up in a Review state with zero rows.
- **`miss-inv` + duplicate combo** — status priority ensures duplicates show as duplicates even if invoice is also missing.
- **Row dedup behavior diverges from legacy** — the legacy parser silently skipped any container it had already seen. v2 surfaces them. This is intentional and the whole point of M2.
- **Customer column showing a code** — `BILLTO`-style files (Idea Nouva) display the code (e.g., `IDEANU01`). User confirmed this is acceptable.

## Acceptance criteria

A user can:

1. Drop a header-prefixed Excel manifest (NGL INVOICE or Idea Nouva format).
2. See either a green "All N rows checked out" success card OR a yellow summary banner + tabs + table, depending on whether issues exist.
3. From the success card, click `Fetch N Documents` → land on the M3 stub.
4. From the success card, click `Show all N rows` → the table expands inline.
5. From the issues path, see Issues tab open by default.
6. Switch between All and Issues tabs and watch the row count change.
7. Type in the search box and watch matching containers filter live.
8. Pick any of the four sort options and see the table reorder.
9. Toggle individual checkboxes — Fetch button count updates live.
10. Toggle the master checkbox — all visible rows toggle together; master shows indeterminate when partial.
11. Click `Replace` to drop a different file and start over.
12. Click `+ New Merge` in the header to do the same.

## Forward-compat notes for M3

The selection state (`rows[].selected`) is the input M3 will hand to the agent's fetch endpoint. M3 will:

- Walk `rows.filter(r => r.selected)` in Excel order.
- Send each container + invoice pair to the existing fetch pipeline (preserves v2.44 TMS POD fallback).
- Render the same success-card vs. error-table split rule on the Ready state: zero failures → green "All N fetched" card; any failures → table with Errors tab open by default.

The validation tags (`miss-inv`, `dup-*`) are NOT consumed by M3 — they only matter for pre-fetch UX. The actual fetch sees a flat list of selected containers.
