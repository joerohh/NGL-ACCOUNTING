# Merge Tool — Warehouse Invoice Support — Design Spec

**Status:** Approved 2026-05-19. Ready for implementation plan.
**Mockups:**
- `app/mockups/v2.72-warehouse-review-mockup.html` — Review screen with warehouse rows, side panel customization, error states.
- `app/mockups/v2.72-settings-storage-mockup.html` — new Settings · Storage card.

**Proof-of-concept scripts** (kept under `scratch/` for reference, will be deleted after implementation lands):
- `scratch/excel_to_pdf_test.py` — Excel COM conversion of a real warehouse xlsx.
- `scratch/warehouse_merge_test.py` — pdf-lib stitching of invoice + converted xlsx.
- `scratch/warehouse_full_run.py` — full end-to-end against real QBO invoice 391101.

**Real test artifacts:**
- QBO txnId **391101** = INDIMEX GLOBAL LLC, INV# `LW260515P01`, attachment `APRIL CHARGE 2026.xlsx`.
- Converted POC PDF: 20 pages, fit-to-width landscape, ~8s conversion.
- Full-run PDF (invoice + converted xlsx): 21 pages, 16.4s total.

---

## Goal

Add **warehouse** as a third invoice type to the merge tool, alongside import and export.

A warehouse invoice has:
- An INV# of the form `[location-letter]W[number]` — e.g. `LW260515P01` (L = Los Angeles, W = warehouse).
- **No real container number, no work-order number.** Exporters often echo the INV# into those columns as placeholder padding; we ignore whatever they wrote.
- Documents stored only on QBO — never on TMS.
- One or more QBO attachments: PDFs **or** Excel files (`.xlsx`/`.xls`/`.xlsm`), any combination.

The merge tool must:
1. Detect warehouse rows from the INV# letter rule.
2. Fetch all QBO attachments (no TMS fallback).
3. Convert any Excel attachments to PDF without cutting off content.
4. Stack everything into the existing merge modes.

While we're touching this code, four cross-cutting improvements ship together — each benefits import/export too, so we don't want to do them twice:
- Universal INV#-only output filenames (drops container from import/export filenames).
- Bulk-drop matching by either container **or** INV#, with one PDF allowed to attach to multiple matching rows.
- "Per Container" tab + folder rename to "Per Invoice".
- 7-day auto-cleanup of merged outputs and agent temp files, with a new Settings · Storage card.

---

## Section 1 — Scope & identification

### Routing rule (extended)

| INV# position-2 letter | Type | Expected doc |
|---|---|---|
| `M` | Import | POD |
| `E` | Export | BL / POL |
| **`W`** | **Warehouse** (new) | **All QBO docs** |
| else | Unknown | (falls back to WO# letter as today) |

`routingDecisionFor()` in `app/assets/js/shared/utils.js` returns `{ type: 'warehouse', expectedDoc: 'All QBO Docs' }` for `W` at position 2.

WO# letter fallback (`parseWoType`) **does not** add a `W` branch — too risky for false positives on legitimate WO#s that happen to contain `W`.

### Manifest parsing

- Container # column is **optional** (was required). INV# column is **required**.
- Per row: container # is required for import/export rows only. Warehouse rows ignore container/WO entirely — any placeholder the exporter stuffed in those columns is dropped.
- **Mixed batches** (warehouse + import + export rows in one Excel) are allowed; each row routes independently.
- Routing-hint copy on the Review screen updates to:
  > *"Decided by INV# letter (M/E/W) · falls back to WO# letter when prefix is non-standard"*

---

## Section 2 — Fetch flow (agent side)

**File:** `agent/services/job_manager/fetch_job.py`

For warehouse rows:
- Fetch invoice PDF from QBO (same code path as today).
- List **all** attachments on the QBO invoice (`list_attachments`).
- Take **every** attachment regardless of `classify_attachment()` label — QBO marks xlsx as `other` and that's fine.
- For each `.xlsx`/`.xls`/`.xlsm` → run Excel COM converter (Section 3); for each `.pdf` → pass through as-is; for any other extension → skip and surface as `unsupported_type` in the panel error list.
- **TMS fallback is disabled** for warehouse rows. `_tms_pod_fallback` is never called.
- POD/BL/POL safety cascade is skipped — those are import/export concepts.

**Final order of attachments in the merged PDF:** invoice page first, then attachments in QBO upload order.

### Hardening — `download_attachment` retry

In `agent/services/qbo_api/attachments.py`, wrap `download_attachment` with a 3-attempt retry:
- Backoffs: 1s, 3s.
- Retry only transient network errors: `httpx.ConnectError`, `ConnectTimeout`, `ReadTimeout`, `RemoteProtocolError`.
- Same pattern as the existing `get_invoice_link` retry.

POC observed `getaddrinfo failed` on first attempt, second worked. Worth fixing while we're in this file — benefits import/export too.

### `fetchResult` shape for warehouse rows

```js
{
  status: 'found' | 'not_found' | 'partial',
  routingType: 'warehouse',
  podLabel: 'Warehouse',
  attachments: [{ fileName, converted, pageCount, sizeBytes }, ...],
  conversionFailures: [{ fileName, reason }],
  invoicePages: 1,
  totalPages: N,
}
```

---

## Section 3 — Excel → PDF converter (new agent component)

**New file:** `agent/services/excel_converter.py`
**New dependency:** `pywin32` added to `agent/requirements.txt`.
**PyInstaller bundling:** `desktop/ngl-agent.spec` adds hidden imports: `win32com`, `win32com.client`, `pythoncom`, `win32api`, `pywintypes`.

### Public API

```python
def convert_xlsx_to_pdf(src: Path, out: Path) -> ConvertResult:
    """Returns ConvertResult(ok, pages, size_bytes, error)."""
```

### Page-setup rules per worksheet (from POC)

| Setting | Value | Why |
|---|---|---|
| Orientation | Landscape if `cols >= rows` OR `cols >= 8`; else portrait | Wide tables (e.g. 20-col Handling tab) read better landscape |
| `Zoom` | `False` | Required so `FitToPages` takes effect |
| `FitToPagesWide` | `1` | All columns fit one page width — the "no cut-off" guarantee |
| `FitToPagesTall` | `False` | Unlimited vertical — keeps font readable |
| Margins | 0.5" all sides | Standard |
| `CenterHorizontally` | `True` | Centers narrow sheets |
| `PrintTitleRows` | `"$1:$2"` if unset | Repeats header rows on every page |
| Export | `ExportAsFixedFormat(Type=0)` on the **workbook** | Single PDF, all tabs in order |

### Lifecycle: one Excel process per fetch job

POC: ~8s to spawn Excel, ~1-2s per file after warm-up. Spawn-per-file would be slow on a batch.

- Wrap as `class ExcelSession` async context manager.
- Convert each xlsx via `asyncio.to_thread` (COM is sync and blocks the event loop).

### Excel install check

- At agent startup, attempt `DispatchEx("Excel.Application")` once. Set module-level `EXCEL_AVAILABLE` flag.
- Expose in `/health` as `"excel_converter": "ready" | "missing"`.
- Frontend banner on agent panel when missing:
  > *"Warehouse invoices won't work — Microsoft Excel is not installed on this machine."*
- If unavailable: import/export still works normally; warehouse rows flip to manual-upload state at fetch time.

### Failure handling

| Failure | Behavior |
|---|---|
| Excel not installed | Pre-flight at job start; all warehouse rows in batch → manual-upload state |
| Password-protected xlsx | Pass `Password=""` + `WriteResPassword=""`. 30s timeout. On hang → kill process → report `conversion_timeout` |
| Corrupt xlsx | COM error caught → reported as `conversion_failed: corrupt_file` |
| External links | `UpdateLinks=0` on open (already in POC) |
| Macro warnings | Suppressed via `excel.AutomationSecurity = 3` (force-disable, no prompts) |
| Excel crashes | Catch, restart `ExcelSession`, retry once, then give up |
| Per-file timeout | 30s wall-clock via `asyncio.wait_for` |
| Empty workbook (no sheets, no data) | Skip with `conversion_failed: empty_workbook`. Other attachments on the row continue. |
| Workbook with only chart sheets | Convert anyway. Output may look unusual but PDF is valid. |
| User already has Excel open in their session | No conflict. `DispatchEx` spawns a fresh isolated Excel process. POC verified. |

### Cleanup

- `Quit()` + `CoUninitialize()` in `__aexit__` and all error paths.
- Startup hook kills orphan `EXCEL.EXE` processes from previous runs (matched by command-line tag).
- Converted PDFs live in the same `agent/downloads/<job_id>/` directory as the originals; storage cleanup in Section 7 handles them.

---

## Section 4 — Review UI changes

### Filter tabs (above the table)

Add a `Warehouse` tab next to Import / Export / Unknown. NGL orange accent.

### "Will fetch" routing summary band

Add an `All QBO Docs` chip with the warehouse row count. Hint copy updates to mention `M / E / W`.

### Table per-row changes for warehouse rows

| Column | Display |
|---|---|
| Type badge | Orange `WAREHOUSE` pill (`#ffedd5` / `#9a3412`) |
| Container # | Soft-grey em-dash `—` (placeholder INV# value is ignored) |
| WO # | Soft-grey em-dash `—` |
| INV # | Normal — only real ID |
| Will fetch | `All QBO Docs` chip |
| Status | Normal (Ready / Verify / Missing) |

### Side panel (selected row detail) — applies to **all** row types

Apply to import/export rows too — these are not warehouse-specific affordances:

- Each doc has a **drag handle** (six-dot icon) for reordering.
- Each doc has a **× remove** button (excludes from this merge only — never touches QBO).
- A **+ Add document** drop zone after the doc list (slim, dashed border, single-line).
- Source tags on each doc: `From QBO`, `From TMS`, `Added by you`, plus `XLSX → PDF` for converted files.
- A **Reset** link returns the list to what was originally fetched (local undo, no network call).

### Error states in the side panel

**Partial failure** (some converted, some failed):
- Red banner at panel top: *"N attachment(s) couldn't be converted. The merge will continue with what worked — drop in a replacement if you have one."*
- Failed file row: red background, ⚠ icon, inline reason (e.g. *"Couldn't convert — file is password-protected"*).

**Zero attachments** (QBO returned nothing):
- Amber banner: *"No documents on QBO. Upload one manually below — this row can't merge without it."*
- Body shows `QBO attachments: 0 found`, `TMS fallback: disabled (warehouse)`.
- Add-doc zone highlighted amber as the primary call-to-action.
- Hint that bulk-drop above the table also works (matches INV# in filename).

---

## Section 5 — Output filenames, mode rename, and folder layout

### Filename rule — INV# only, all three row types

The output filename uses **only** the INV#. Container number is dropped from filenames for import/export as well, not just warehouse.

| Mode | Filename |
|---|---|
| Per Invoice (full bundle) | `<INV#>.pdf` |
| Per Invoice — Invoice Only | `<INV#>_INV.pdf` |
| Per Invoice — Document Only · import | `<INV#>_POD.pdf` |
| Per Invoice — Document Only · export | `<INV#>_BL.pdf` or `<INV#>_POL.pdf` |
| Per Invoice — Document Only · warehouse | `<INV#>_WH.pdf` |
| Combined PDF / Invoice Only / Document Only (single-output modes) | unchanged — `<Combined\|Invoices\|Documents>_YYYY-MM-DD.pdf` |

**Why `_WH` and not `_DOCS`:** `_DOCS` is too close to the existing `_DOC` fallback and reads as the same family. `_WH` matches the established 2-3 letter abbreviation pattern (POD, BL, POL, IT, ITE) and mirrors the INV# letter rule (M/E/**W**).

**Implementation note:** the existing builder in `app/assets/js/tools/merge/merge-v2-output.js` (`perContainerFilename`, will be renamed `perInvoiceFilename`) currently emits `${container}_${inv}` patterns. Update it to emit just `${inv}` with the appropriate suffix. Drop the `stem = container || wo || inv` fallback chain; INV# is required for all rows, so it's always available.

### Mode rename: "Per Container" → "Per Invoice"

UI tab labels and folder names move from `Per Container` to `Per Invoice`:

| Old name | New name |
|---|---|
| `Per Container` | `Per Invoice` |
| `Per Container — Invoice Only` | `Per Invoice — Invoice Only` |
| `Per Container — Document Only` | `Per Invoice — Document Only` |

Mode `key` strings in `merge-v2-output.js MODES` stay as `per-container`, `per-container-invoice`, `per-container-document` to avoid touching saved state and the engine module. Only the `title` and `subfolder` fields change. This keeps the rename UI-only.

**Description copy** for `per-container-document` updates to:
> *"One PDF per invoice, containing only the supporting document — POD, BL, POL, IT, ITE, or warehouse attachments."*

### Folder layout

Outputs continue to land at:
```
[user-chosen location]/Merge Outputs/[Mode subfolder]/YYYY-MM/YYYY-MM-DD/
```

All three row types (import, export, warehouse) write to the **same** subfolder per mode. No warehouse-specific subtree.

**Migration:** old runs in `Per Container/2026-04/...` stay where they are. New runs after the update land in `Per Invoice/...`. No files moved. The 7-day cleanup sweep (Section 7) handles both old and new folder trees, so the old `Per Container/` tree naturally empties out within a few weeks.

### Bulk-drop matching — universal + multi-row attach

The Review screen's bulk PDF drop zone today only matches by container number, and "first match wins per row, first row wins per file." This is rebuilt for all row types:

- Match by **container number** (existing behavior) **and** by **INV#** (new). Case-insensitive substring match in either case.
- **One PDF attaches to all matching rows.** If container `TEMU8809194` appears in 3 rows (chassis + storage + move invoices), one `TEMU8809194_pod.pdf` attaches to all 3 — no more silent loss when multiple invoices share a container.
- If a filename happens to contain both a container from row A and an INV# from row B (vanishingly unlikely in practice — container and INV# formats don't collide), it attaches to both row A and row B. Multi-attach handles the case naturally; no tie-breaking needed.
- Manual override always works — dropping a file into a specific row's side-panel `+ Add document` zone forces it onto that row regardless of filename.

Post-drop feedback message:
> *"3 of 4 PDFs matched. TEMU8809194_pod.pdf attached to 3 rows (Rows 1, 2, 3 — same container)."*

---

## Section 6 — Edge cases & testing

### Additional edge cases not already in Section 3's failure table

| Case | Behavior |
|---|---|
| Mixed batch (warehouse + import + export) | No special handling. Rows flow through the fetch queue independently. Outputs land in same folders per mode. |
| Filename matches multiple rows by container or INV# | Attach to all matching rows (Section 5 multi-attach rule). |
| Warehouse row's INV# coincidentally appears in another row's container | Practically impossible — formats differ — but multi-attach rule handles it safely. |
| Bulk drop into a manifest with no warehouse rows but a file named `LW260515P01.pdf` | Falls through to unmatched bucket — no INV# in manifest to match against. |

### Testing

- **Unit:** `routingDecisionFor()` returns `warehouse` for `M`/`E`/`W` at INV# position 2. Edge cases: 1-char INV#, lowercase letter, missing letter.
- **Unit:** Filename builder produces `<INV#>.pdf` / `<INV#>_INV.pdf` / `<INV#>_WH.pdf` for warehouse rows; verify container dropped from import/export filenames as well.
- **Unit:** Bulk-drop matcher — one file matches multiple rows (same container, 3 INV#s) attaches to all 3; both container-named and INV#-named filenames find their rows.
- **Integration:** `agent/tests/integration/test_warehouse_end_to_end.py` — scripted port of `scratch/warehouse_full_run.py`. Marked `@pytest.mark.requires_excel`; skipped on CI / machines without Excel.
- **Smoke:** `download_attachment` retry — mock `httpx` to fail with `ConnectError` once, succeed on retry, verify 1s backoff applied.

---

## Section 7 — Storage management

The merge tool is the only feature that writes meaningful data to disk. Today nothing cleans it up; over a year of normal use, the `agent/downloads/` folder alone could reach 15+ GB. Add automatic cleanup and a visibility surface in Settings.

### Cleanup rules

- **7-day retention** for both `[user-chosen location]/Merge Outputs/` and `agent/downloads/`.
- Sweep runs at agent startup. Walks each storage tree and deletes any date-bucketed subdirectory older than 7 days.
- Files inside the current week are **never touched** — the user always has a full week of batches to review.
- Sweep deletes contents only inside the two tracked roots (`Merge Outputs/` and `agent/downloads/`). It never touches other files in the user's chosen folder.

### New Settings · Storage card

Mockup: `app/mockups/v2.72-settings-storage-mockup.html`.

Card contents:
- **Saved merged files** row — path, total size, file/folder count, size bar, **"Open folder"** button.
- **Temporary app files** row — path, total size, batch count, size bar. (No button — informational.)
- Orange auto-cleanup callout — plain-English explainer of the 7-day rule + "Last cleanup ran at [time] · removed N files (M MB)".
- Action row — **"Clean up now"** (manual sweep on demand) and **"Change output folder…"** (existing folder-picker flow).
- Subtle italic footnote: *"Only the Merge Tool saves files to disk. Invoice Sender and Customer Manager don't."* Sits at the bottom of the card, light-grey to read as a quiet aside.

### Agent endpoints

- `GET /storage/info` → `{ output_size_bytes, output_file_count, output_folder_count, downloads_size_bytes, downloads_batch_count, last_cleanup_ts, last_cleanup_freed_bytes, last_cleanup_files_removed }`. Frontend caches this on Settings load.
- `POST /storage/cleanup` → triggers the sweep on-demand, returns the same info shape with updated post-cleanup values.

### Config constants (`agent/config.py`)

```python
STORAGE_RETAIN_DAYS = 7  # cleanup threshold for Merge Outputs and downloads
```

### Implementation file

New module `agent/services/storage.py` with:
- `sweep_old_outputs(output_root: Path, retain_days: int) -> SweepResult`
- `sweep_old_downloads(downloads_root: Path, retain_days: int) -> SweepResult`
- `get_storage_info(output_root: Path, downloads_root: Path) -> StorageInfo`

Sweep wired into `agent/main.py` lifespan alongside the existing `cleanup_old_debug_files()` and `backup_data_files()` calls.

---

## File inventory (for the implementation plan)

### Web app

| File | Change |
|---|---|
| `app/assets/js/shared/utils.js` | Extend `routingDecisionFor()` to recognize `W` at INV# position 2 |
| `app/assets/js/tools/merge/merge-v2.js` | Rebuild bulk-drop matcher (multi-row attach, INV# + container, new feedback message). Update routing-hint copy. Side panel customization (drag/remove/add/reset). Warehouse row display (em-dash cells, orange badge). Filter tabs update. Routing summary band update. |
| `app/assets/js/tools/merge/merge-v2-output.js` | Filename builder → INV#-only with `_INV`/`_POD`/`_BL`/`_POL`/`_IT`/`_ITE`/`_WH` suffix. Rename mode `title` + `subfolder` fields ("Per Container" → "Per Invoice"). Update document-only description copy. |
| `app/assets/js/tools/merge/merge-v2-engine.js` | Handle warehouse rows with multi-attachment ordered concatenation (invoice + N attachments). |
| `app/assets/js/tools/settings/settings.js` | Wire new Storage card — fetch `/storage/info`, render rows, wire "Open folder" and "Clean up now" actions. |
| `app/index.html` | Storage card HTML structure inside the Settings panel. |
| `app/assets/css/styles.css` | Storage card styles, warehouse type badge, `WH` chip, em-dash cell, side-panel drag handle / remove button / add-doc zone / reset row / error banners. |

### Agent

| File | Change |
|---|---|
| `agent/services/job_manager/fetch_job.py` | Warehouse branch: list all attachments, run `ExcelSession` for xlsx, skip TMS fallback + safety cascade, build `fetchResult` per Section 2 shape |
| `agent/services/qbo_api/attachments.py` | 3-attempt retry on `download_attachment` (1s + 3s backoff, transient errors only) |
| `agent/services/excel_converter.py` | **NEW** — `ExcelSession` context manager + `convert_xlsx_to_pdf()` |
| `agent/services/storage.py` | **NEW** — sweep + storage-info functions |
| `agent/routers/storage.py` | **NEW** — `/storage/info` and `/storage/cleanup` endpoints |
| `agent/main.py` | Wire Excel startup check (`EXCEL_AVAILABLE`), wire storage sweep into lifespan, wire orphan `EXCEL.EXE` cleanup |
| `agent/config.py` | `STORAGE_RETAIN_DAYS = 7` constant |
| `agent/requirements.txt` | Add `pywin32` |
| `desktop/ngl-agent.spec` | PyInstaller hidden imports for `pywin32` modules |

### Tests

| File | Purpose |
|---|---|
| `agent/tests/test_excel_converter.py` | Unit — page-setup rules, failure modes, lifecycle |
| `agent/tests/integration/test_warehouse_end_to_end.py` | Integration — full QBO → convert → merge run, `@pytest.mark.requires_excel` |
| `agent/tests/test_qbo_attachments_retry.py` | Smoke — `download_attachment` retry behavior |
| `agent/tests/test_storage_cleanup.py` | Unit — sweep correctness (deletes ≥7d old, preserves <7d, never touches files outside the tracked roots) |

---

## Out of scope (explicitly deferred)

- Hard cap on page count for very large workbooks — relying on the per-file 30s timeout is sufficient for real-world warehouse files.
- Migrating old `Per Container/` folder contents to the new `Per Invoice/` tree — the 7-day cleanup empties them naturally.
- Showing per-row file size in the Review screen — could be useful but isn't asked for.
- Per-customer or per-tenant retention policy override — global 7-day rule for now.
