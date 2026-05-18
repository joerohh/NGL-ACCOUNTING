# Merge Tool v2 — M4 (Merging + Done) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship M4 of the merge-tool v2 redesign — replace the placeholder Merging/Done states with a single Merge screen that picks a mode, runs the merge, accumulates completed cards, and writes outputs into a structured `Merge Outputs/` folder under a user-chosen location.

**Architecture:** Frontend-heavy. The Merge screen is a card-grid UI rendered by `merge-v2.js`. Merge logic lives in a new `merge-v2-engine.js` module that fetches PDFs from the agent's `/files/{jobId}/{filename}` endpoint and uses `pdf-lib` (already in the page) to assemble outputs. A new `merge-v2-output.js` module owns filename building, folder path building, and the save flow. The agent's existing `/files/save-output` endpoint gets extended to handle nested subfolder paths, a custom base location, and folder overwrite. A new `/files/pick-folder` endpoint opens a native Tk dialog so the user can choose where `Merge Outputs/` lives.

**Tech Stack:** Vanilla JS ES modules, `pdf-lib` (CDN), Python FastAPI, Pydantic, Tkinter (stdlib for folder picker), pytest.

**Spec:** `docs/superpowers/specs/2026-05-07-merge-tool-v2-m4-merging-done-design.md`

---

## File Structure

### Created files

- `app/assets/js/tools/merge/merge-v2-engine.js` — 6 merge functions (Per Container × 3 content variants, Combined PDF, Invoice Only, Document Only). Pure-ish: takes rows + a fetched-file resolver + a mode key and returns `{ files: [{filename, bytes}], stats }`.
- `app/assets/js/tools/merge/merge-v2-output.js` — Filename builders for each mode, mode metadata (display name + description + group + filename pattern), save flow that POSTs merged bytes to the agent.

### Modified files

- `app/assets/js/tools/merge/merge-v2.js` (1838 lines → adds ~400 lines)
  - State extension: `outputLocation`, `completedModes`, `runningMode`, `confirmPopupOpen`.
  - Replace `renderMerging()` and `renderDone()` stubs with a single `renderMerge()` that handles both pickable and completed cards in one screen.
  - `v2ClickContinueMerge`: popup flow + transition to merge state.
  - `v2ClickModeCard`: run merge for that mode → write files → flip card to completed.
  - `v2OpenOutputFolder`, `v2OpenOutputFile`, `v2RerunMode`, `v2ChangeOutputLocation`.
  - `v2ToggleFetchRow`: remove the disabled-when-errored guard; allow toggling errored rows.
- `app/assets/js/shared/agent-client.js` (~330 lines → +40 lines): `saveBatchOutput`, `pickFolder`, `openPath` methods.
- `app/index.html` — add `Output location` button to the Merge screen header section.
- `app/assets/css/styles.css` — mode-card grid, completed-card layout, confirmation modal, output-location button.
- `agent/routers/files.py` — extend `save-output` for nested paths + base location + overwrite; add `pick-folder` and `open-path` endpoints.
- `agent/tests/test_endpoints.py` — pytest cases for the extended/new endpoints.
- `desktop/VERSION` — bump to 2.52.0 in the final task.

### Why split engine + output into their own files

`merge-v2.js` is already at 1838 lines. Adding all of M4 inline pushes it past 2500. The natural split is:
- **State + render** stays in `merge-v2.js` (UI-coupled).
- **Engine** (pdf-lib calls, no DOM) → `merge-v2-engine.js`.
- **Output** (filename rules, save flow) → `merge-v2-output.js`.

This mirrors the v1 codebase pattern and keeps each file focused on one job.

---

## Task 1: Agent — extend `/files/save-output` for nested subfolders, base location, overwrite

**Files:**
- Modify: `agent/routers/files.py`
- Modify: `agent/tests/test_endpoints.py`

The current endpoint accepts a single-level `subfolder` (sanitised via `Path(item.subfolder).name` which strips slashes). M4 needs:
- Multi-level subfolder paths (e.g. `Per Container/2026-05/2026-05-07`)
- Optional `base_location` absolute path (defaults to `OUTPUT_DIR`)
- Optional per-request `overwrite_folder` flag — if true, clear the target folder before writing
- Path traversal still rejected (`..` segments, absolute paths inside `subfolder`)

- [ ] **Step 1: Write the failing tests**

Append to `agent/tests/test_endpoints.py`:

```python
import base64
import tempfile
from pathlib import Path


# ── /files/save-output extensions ──────────────────────────────────


class TestSaveOutputExtensions:
    """M4 additions: nested subfolders, base location, overwrite_folder."""

    def _pdf_bytes(self) -> str:
        # Minimal valid PDF (just a header) — enough for endpoint to write+strip MOTW
        return base64.b64encode(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n").decode()

    def test_nested_subfolder_creates_intermediate_dirs(self, client):
        with tempfile.TemporaryDirectory() as tmp:
            r = client.post("/files/save-output", json={
                "files": [{
                    "filename": "test.pdf",
                    "data": self._pdf_bytes(),
                    "subfolder": "Per Container/2026-05/2026-05-07",
                }],
                "openFolder": False,
                "baseLocation": tmp,
            })
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["saved"] == 1
            expected = Path(tmp) / "Per Container" / "2026-05" / "2026-05-07" / "test.pdf"
            assert expected.exists()
            assert expected.read_bytes().startswith(b"%PDF-1.4")

    def test_base_location_defaults_to_output_dir_when_absent(self, client):
        # When baseLocation is omitted, files land under OUTPUT_DIR (legacy behavior)
        r = client.post("/files/save-output", json={
            "files": [{
                "filename": "legacy_default.pdf",
                "data": self._pdf_bytes(),
                "subfolder": "M4Test",
            }],
            "openFolder": False,
        })
        assert r.status_code == 200, r.text
        assert r.json()["saved"] == 1
        # outputDir in response confirms it landed under OUTPUT_DIR
        assert "M4Test" in r.json()["outputDir"]

    def test_overwrite_folder_clears_existing_files(self, client):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "Combined PDF" / "2026-05" / "2026-05-07"
            target.mkdir(parents=True)
            stale = target / "stale_file.pdf"
            stale.write_bytes(b"%PDF-1.4 stale")

            r = client.post("/files/save-output", json={
                "files": [{
                    "filename": "fresh.pdf",
                    "data": self._pdf_bytes(),
                    "subfolder": "Combined PDF/2026-05/2026-05-07",
                }],
                "openFolder": False,
                "baseLocation": tmp,
                "overwriteFolder": True,
            })
            assert r.status_code == 200, r.text
            assert not stale.exists(), "stale file should have been cleared"
            assert (target / "fresh.pdf").exists()

    def test_overwrite_folder_false_keeps_existing_files(self, client):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "MergeTest"
            target.mkdir(parents=True)
            keep = target / "keep_me.pdf"
            keep.write_bytes(b"%PDF-1.4 keep")

            r = client.post("/files/save-output", json={
                "files": [{
                    "filename": "added.pdf",
                    "data": self._pdf_bytes(),
                    "subfolder": "MergeTest",
                }],
                "openFolder": False,
                "baseLocation": tmp,
                "overwriteFolder": False,
            })
            assert r.status_code == 200, r.text
            assert keep.exists()
            assert (target / "added.pdf").exists()

    def test_path_traversal_rejected_in_subfolder(self, client):
        with tempfile.TemporaryDirectory() as tmp:
            r = client.post("/files/save-output", json={
                "files": [{
                    "filename": "evil.pdf",
                    "data": self._pdf_bytes(),
                    "subfolder": "../../../etc/passwd",
                }],
                "openFolder": False,
                "baseLocation": tmp,
            })
            assert r.status_code == 400
            assert "path" in r.text.lower() or "subfolder" in r.text.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Start the agent server first, then:

```bash
cd agent && python -m pytest tests/test_endpoints.py::TestSaveOutputExtensions -v
```

Expected: 5 failures (endpoint lacks `baseLocation`, `overwriteFolder`, multi-level subfolder support).

- [ ] **Step 3: Replace `agent/routers/files.py` with the extended implementation**

Replace the entire `SaveFileItem`, `SaveOutputRequest`, and `save_output` block (lines 70-129) with:

```python
class SaveFileItem(BaseModel):
    filename: str
    data: str  # base64-encoded PDF bytes
    subfolder: str = ""  # may be multi-level: "Per Container/2026-05/2026-05-07"


class SaveOutputRequest(BaseModel):
    files: list[SaveFileItem]
    openFolder: bool = True
    baseLocation: str | None = None  # absolute path; falls back to OUTPUT_DIR if absent
    overwriteFolder: bool = False    # if True, clear the target subfolder before writing


def _safe_subfolder(subfolder: str, base: Path) -> Path:
    """Resolve a multi-level subfolder under base, rejecting path-traversal attempts."""
    if not subfolder:
        return base
    # Normalize separators and split into parts
    parts = [p for p in subfolder.replace("\\", "/").split("/") if p not in ("", ".")]
    for part in parts:
        if part == ".." or part.startswith("/") or ":" in part:
            raise HTTPException(400, f"Invalid subfolder path component: {part}")
    target = base.joinpath(*parts) if parts else base
    # Final sanity check: target must be inside base
    try:
        target.resolve().relative_to(base.resolve())
    except ValueError:
        raise HTTPException(400, "Subfolder path escapes base location")
    return target


@router.post("/save-output")
async def save_output(req: SaveOutputRequest):
    """Save merged PDFs into [baseLocation]/[subfolder]/. Supports nested paths and overwrite."""
    if not req.files:
        raise HTTPException(400, "No files provided")

    # Resolve base location — user-chosen path or OUTPUT_DIR fallback
    if req.baseLocation:
        base = Path(req.baseLocation)
        if not base.is_absolute():
            raise HTTPException(400, "baseLocation must be an absolute path")
        base.mkdir(parents=True, exist_ok=True)
    else:
        base = OUTPUT_DIR

    saved = []
    open_dir = base

    # Group files by subfolder so we can apply overwriteFolder once per folder
    by_folder: dict[str, list[SaveFileItem]] = {}
    for item in req.files:
        by_folder.setdefault(item.subfolder, []).append(item)

    for subfolder, items in by_folder.items():
        target_dir = _safe_subfolder(subfolder, base)

        # Overwrite mode: clear the target folder first (only if it exists and is non-empty)
        if req.overwriteFolder and target_dir.exists() and target_dir.is_dir():
            for child in target_dir.iterdir():
                try:
                    if child.is_file():
                        child.unlink()
                    elif child.is_dir():
                        # Recursively remove subdirs (rare, but possible from prior runs)
                        import shutil
                        shutil.rmtree(child)
                except Exception as e:
                    logger.warning("Failed to clear %s: %s", child, e)

        target_dir.mkdir(parents=True, exist_ok=True)
        open_dir = target_dir

        for item in items:
            safe_name = Path(item.filename).name
            if not safe_name:
                continue
            dest = target_dir / safe_name
            try:
                pdf_bytes = base64.b64decode(item.data)
                dest.write_bytes(pdf_bytes)
                strip_motw(dest)
                saved.append({"name": safe_name, "size": len(pdf_bytes), "path": str(dest)})
                logger.info("Saved merged file: %s (%d bytes) -> %s", safe_name, len(pdf_bytes), target_dir)
            except Exception as e:
                logger.error("Failed to save %s: %s", safe_name, e)
                saved.append({"name": safe_name, "error": str(e)})

    if req.openFolder and saved:
        try:
            subprocess.Popen(["explorer", str(open_dir)])
        except Exception:
            pass

    return {
        "status": "ok",
        "saved": len([s for s in saved if "error" not in s]),
        "total": len(req.files),
        "outputDir": str(open_dir),
        "files": saved,
    }
```

- [ ] **Step 4: Restart the agent + run tests to verify they pass**

```bash
# Kill existing agent (Ctrl+C in its terminal) then:
cd agent && python main.py &
cd agent && python -m pytest tests/test_endpoints.py::TestSaveOutputExtensions -v
```

Expected: 5 passes.

- [ ] **Step 5: Commit**

```bash
git add agent/routers/files.py agent/tests/test_endpoints.py
git commit -m "feat(agent/files): nested subfolders + base location + overwrite for save-output"
```

---

## Task 2: Agent — add `/files/pick-folder` and `/files/open-path` endpoints

**Files:**
- Modify: `agent/routers/files.py`
- Modify: `agent/tests/test_endpoints.py`

Two helpers the Merge screen needs:
- `pick-folder` — opens a native Tk folder dialog, returns the chosen absolute path (or `null` if user cancels).
- `open-path` — opens a folder OR file with the OS default handler (Explorer for folders, default app for PDFs).

`pick-folder` blocks the agent thread while the dialog is open — that's fine since the user is mid-interaction.

- [ ] **Step 1: Write the failing tests**

Append to `agent/tests/test_endpoints.py`:

```python
class TestFolderPickerAndOpenPath:
    """M4: pick-folder and open-path helpers."""

    def test_open_path_rejects_missing_path(self, client):
        r = client.post("/files/open-path", json={"path": "/this/does/not/exist/123abc"})
        assert r.status_code == 404

    def test_open_path_accepts_existing_dir(self, client):
        with tempfile.TemporaryDirectory() as tmp:
            r = client.post("/files/open-path", json={"path": tmp})
            # On CI/headless this may fail to spawn explorer; endpoint should still return 200
            # because we use a fire-and-forget subprocess.Popen.
            assert r.status_code == 200
            assert r.json()["status"] == "ok"

    def test_open_path_accepts_existing_file(self, client):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "test.txt"
            f.write_text("hello")
            r = client.post("/files/open-path", json={"path": str(f)})
            assert r.status_code == 200

    # pick-folder is intentionally NOT auto-tested — it opens a blocking GUI dialog.
    # Manual QA only. We just verify the endpoint exists and rejects bad input.
    def test_pick_folder_endpoint_exists(self, client):
        # We don't actually invoke it (would block on Tk dialog).
        # Just verify the route is registered by hitting OPTIONS or a malformed POST.
        r = client.options("/files/pick-folder")
        # FastAPI returns 405 for OPTIONS on a POST-only route by default,
        # OR 200 with the allowed methods listed. Either confirms the route exists.
        assert r.status_code in (200, 405)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd agent && python -m pytest tests/test_endpoints.py::TestFolderPickerAndOpenPath -v
```

Expected: 4 failures (endpoints don't exist yet).

- [ ] **Step 3: Add the new endpoints to `agent/routers/files.py`**

Append to the bottom of `agent/routers/files.py`:

```python
class OpenPathRequest(BaseModel):
    path: str


@router.post("/open-path")
async def open_path(req: OpenPathRequest):
    """Open a folder in Explorer or a file in its default app. Fire-and-forget."""
    target = Path(req.path)
    if not target.exists():
        raise HTTPException(404, f"Path not found: {req.path}")

    try:
        if target.is_dir():
            subprocess.Popen(["explorer", str(target)])
        else:
            # os.startfile is Windows-only and uses the file's default handler
            import os
            os.startfile(str(target))
    except Exception as e:
        logger.warning("open-path failed for %s: %s", target, e)
        raise HTTPException(500, f"Failed to open: {e}")

    return {"status": "ok", "path": str(target)}


@router.post("/pick-folder")
async def pick_folder():
    """Show a native folder picker and return the chosen absolute path (or null on cancel)."""
    import tkinter as tk
    from tkinter import filedialog

    # Tk requires a hidden root window. Withdraw it so it doesn't flash on screen.
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)  # ensure dialog appears above the Electron window
    try:
        chosen = filedialog.askdirectory(
            title="Choose where Merge Outputs/ should live",
            mustexist=False,
        )
    finally:
        root.destroy()

    return {"status": "ok", "path": chosen if chosen else None}
```

- [ ] **Step 4: Restart the agent + run tests to verify they pass**

```bash
# Restart agent, then:
cd agent && python -m pytest tests/test_endpoints.py::TestFolderPickerAndOpenPath -v
```

Expected: 4 passes.

- [ ] **Step 5: Commit**

```bash
git add agent/routers/files.py agent/tests/test_endpoints.py
git commit -m "feat(agent/files): add pick-folder + open-path endpoints for M4"
```

---

## Task 3: Frontend — extend `agent-client.js` with new methods

**Files:**
- Modify: `app/assets/js/shared/agent-client.js`

Add three methods to `agentBridge`: `saveBatchOutput` (extended save with base location + overwrite), `pickFolder`, `openPath`.

- [ ] **Step 1: Add the new methods**

Find the existing `saveToFolder` method (around line 282) and INSERT the following new methods immediately after its closing `},`:

```javascript
  async saveBatchOutput({ files, baseLocation, overwriteFolder, openFolder }) {
    // M4: extended save with multi-level subfolders, custom base location, overwrite.
    // `files` is [{ filename, data: base64, subfolder }] — same shape as saveToFolder.
    try {
      const res = await this._authFetch(this.baseUrl + '/files/save-output', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          files,
          baseLocation: baseLocation || null,
          overwriteFolder: !!overwriteFolder,
          openFolder: openFolder !== false,  // default true
        }),
      });
      if (!res.ok) {
        const text = await res.text().catch(() => '');
        throw new Error(`HTTP ${res.status}${text ? ': ' + text : ''}`);
      }
      return await res.json();
    } catch (e) { return { error: e.message }; }
  },

  async pickFolder() {
    // M4: open a native folder picker dialog. Returns { path } or { path: null } on cancel.
    try {
      const res = await this._authFetch(this.baseUrl + '/files/pick-folder', {
        method: 'POST',
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } catch (e) { return { error: e.message }; }
  },

  async openPath(path) {
    // M4: ask the agent to open a folder (Explorer) or file (default app).
    try {
      const res = await this._authFetch(this.baseUrl + '/files/open-path', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path }),
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } catch (e) { return { error: e.message }; }
  },
```

- [ ] **Step 2: Manual smoke check**

Open the app in Electron, open DevTools console, with the agent running:

```javascript
await agentBridge.pickFolder()
// → opens native dialog. Pick anything. Should return { status: 'ok', path: 'C:\\...' }

await agentBridge.openPath('C:\\Users\\Joseph\\Desktop')
// → Explorer should open at Desktop.
```

- [ ] **Step 3: Commit**

```bash
git add app/assets/js/shared/agent-client.js
git commit -m "feat(agent-client): add saveBatchOutput, pickFolder, openPath for M4"
```

---

## Task 4: Frontend — create `merge-v2-output.js` with mode metadata + filename builders + save flow

**Files:**
- Create: `app/assets/js/tools/merge/merge-v2-output.js`

This module owns:
- The **6-mode metadata table** (key, group, display name, description, filename pattern hint).
- **Filename builders** per mode (single output vs per-container).
- **Path builder** that produces the subfolder string `Mode Name/YYYY-MM/YYYY-MM-DD`.
- **Save flow** that base64-encodes merged bytes and POSTs them to the agent.

- [ ] **Step 1: Create the file with full content**

Create `app/assets/js/tools/merge/merge-v2-output.js`:

```javascript
// ══════════════════════════════════════════════════════════
//  MERGE TOOL V2 — output module
//  - 6-mode metadata
//  - filename + path builders
//  - save flow (base64 + POST to agent)
//  Spec: docs/superpowers/specs/2026-05-07-merge-tool-v2-m4-merging-done-design.md
// ══════════════════════════════════════════════════════════
import { agentBridge } from '../../shared/agent-client.js';

// ── Mode metadata ──
//   key:         stable identifier used in state + filenames
//   group:       'per-container' | 'combined' (drives which row of cards on the screen)
//   title:       display name on the card
//   description: one-line subtitle on the card
//   subfolder:   first-level folder under "Merge Outputs/"

export const MODES = [
  // Per-container outputs (one PDF per container)
  {
    key: 'per-container',
    group: 'per-container',
    title: 'Per Container',
    description: "One PDF per container. Each file contains that container's invoice and its document combined.",
    subfolder: 'Per Container',
  },
  {
    key: 'per-container-invoice',
    group: 'per-container',
    title: 'Per Container — Invoice Only',
    description: 'One PDF per container, containing only the invoice.',
    subfolder: 'Per Container — Invoice Only',
  },
  {
    key: 'per-container-document',
    group: 'per-container',
    title: 'Per Container — Document Only',
    description: 'One PDF per container, containing only the supporting document (POD, BOL, POL, IT, or ITE).',
    subfolder: 'Per Container — Document Only',
  },
  // Single combined output (one PDF total)
  {
    key: 'combined',
    group: 'combined',
    title: 'Combined PDF',
    description: 'Single PDF with every invoice and document stacked into one big file.',
    subfolder: 'Combined PDF',
  },
  {
    key: 'invoice-only',
    group: 'combined',
    title: 'Invoice Only',
    description: 'Single PDF containing all the invoices.',
    subfolder: 'Invoice Only',
  },
  {
    key: 'document-only',
    group: 'combined',
    title: 'Document Only',
    description: 'Single PDF containing all the supporting documents.',
    subfolder: 'Document Only',
  },
];

export function modeByKey(key) {
  return MODES.find(m => m.key === key) || null;
}

// ── Date helpers ──

export function dateFolderParts(d = new Date()) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return {
    monthFolder: `${y}-${m}`,           // "2026-05"
    dateFolder:  `${y}-${m}-${day}`,    // "2026-05-07"
    dateStamp:   `${y}-${m}-${day}`,    // same — used in filenames
  };
}

// ── Path builder ──
//   Returns the subfolder relative to baseLocation, e.g.:
//     "Per Container/2026-05/2026-05-07"

export function subfolderFor(modeKey, when = new Date()) {
  const mode = modeByKey(modeKey);
  if (!mode) throw new Error(`Unknown mode key: ${modeKey}`);
  const { monthFolder, dateFolder } = dateFolderParts(when);
  return `Merge Outputs/${mode.subfolder}/${monthFolder}/${dateFolder}`;
}

// ── Filename builders ──
//   Per-container modes: one filename per row.
//   Single-output modes: one filename total.

function sanitizeFilenamePart(s) {
  // Strip path separators and characters Windows rejects in filenames.
  return String(s || '').replace(/[\/\\:*?"<>|]/g, '_').trim();
}

export function perContainerFilename(row, modeKey) {
  // Use container as primary, fall back to WO# then INV#.
  const container = sanitizeFilenamePart(row.containerNumber);
  const inv = sanitizeFilenamePart(row.invoiceNumber);
  const wo = sanitizeFilenamePart(row.workOrderNumber);
  const stem = container || wo || inv || `row-${row.rowNum}`;
  const invSuffix = inv ? `_${inv}` : '';

  if (modeKey === 'per-container') {
    return `${stem}${invSuffix}.pdf`;
  }
  if (modeKey === 'per-container-invoice') {
    return `${stem}${invSuffix}_INV.pdf`;
  }
  if (modeKey === 'per-container-document') {
    const docLabel = sanitizeFilenamePart(row.fetchResult?.podLabel || 'DOC');
    return `${stem}${invSuffix}_${docLabel}.pdf`;
  }
  throw new Error(`perContainerFilename: not a per-container mode: ${modeKey}`);
}

export function singleOutputFilename(modeKey, when = new Date()) {
  const { dateStamp } = dateFolderParts(when);
  const map = {
    'combined':      `Combined_${dateStamp}.pdf`,
    'invoice-only':  `Invoices_${dateStamp}.pdf`,
    'document-only': `Documents_${dateStamp}.pdf`,
  };
  const f = map[modeKey];
  if (!f) throw new Error(`singleOutputFilename: not a single-output mode: ${modeKey}`);
  return f;
}

// ── Save flow ──
//   files: [{ filename, bytes: Uint8Array }]
//   baseLocation: absolute path the user picked (or null → agent uses OUTPUT_DIR)
//   modeKey: drives the subfolder
//   openFolder: whether to open Explorer at the target after writing

export async function saveMergedFiles({ files, modeKey, baseLocation, openFolder = false }) {
  if (!Array.isArray(files) || files.length === 0) {
    return { error: 'No files to save' };
  }
  const subfolder = subfolderFor(modeKey);

  // Convert each Uint8Array → base64. Chunk to avoid call-stack blowups on large PDFs.
  const items = files.map(f => {
    const bytes = f.bytes instanceof Uint8Array ? f.bytes : new Uint8Array(f.bytes);
    let binary = '';
    const CHUNK = 8192;
    for (let i = 0; i < bytes.length; i += CHUNK) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
    }
    return { filename: f.filename, data: btoa(binary), subfolder };
  });

  return agentBridge.saveBatchOutput({
    files: items,
    baseLocation,
    overwriteFolder: true,   // M4 spec: same-day overwrite is the default
    openFolder,
  });
}
```

- [ ] **Step 2: Smoke-test the helpers**

Open the app, open DevTools console:

```javascript
const m = await import('./assets/js/tools/merge/merge-v2-output.js');
m.MODES.length === 6 ? '✓ 6 modes' : '✗';
m.subfolderFor('per-container', new Date('2026-05-07'));
// → 'Merge Outputs/Per Container/2026-05/2026-05-07'
m.perContainerFilename({ containerNumber: 'KMTU3920184', invoiceNumber: 'LM26050100F' }, 'per-container');
// → 'KMTU3920184_LM26050100F.pdf'
m.singleOutputFilename('combined', new Date('2026-05-07'));
// → 'Combined_2026-05-07.pdf'
```

- [ ] **Step 3: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2-output.js
git commit -m "feat(merge/v2/m4): add output module — mode metadata, filename + path builders, save flow"
```

---

## Task 5: Frontend — create `merge-v2-engine.js` with the 6 merge functions

**Files:**
- Create: `app/assets/js/tools/merge/merge-v2-engine.js`

This module fetches each row's PDF files from the agent (`/files/{jobId}/{filename}`), assembles them with `pdf-lib` per the selected mode, and returns `{ files: [{filename, bytes}], stats }`.

The agent stores fetched files at `DOWNLOADS_DIR / {jobId} / {container}_invoice.pdf` and `DOWNLOADS_DIR / {jobId} / {container}_pod.pdf` (the `_pod` filename is used regardless of whether it's a POD/BOL/POL/IT/ITE — the routing letter is preserved in `row.fetchResult.podLabel`).

The engine treats an "errored row" (`fetchResult.podPill === 'miss'`) as having only the invoice file — the document is silently absent.

- [ ] **Step 1: Create the file**

Create `app/assets/js/tools/merge/merge-v2-engine.js`:

```javascript
// ══════════════════════════════════════════════════════════
//  MERGE TOOL V2 — engine module
//  pdf-lib-based merging across the 6 v2 modes.
//  Spec: docs/superpowers/specs/2026-05-07-merge-tool-v2-m4-merging-done-design.md
// ══════════════════════════════════════════════════════════
import { agentBridge } from '../../shared/agent-client.js';
import {
  MODES, modeByKey,
  perContainerFilename, singleOutputFilename,
} from './merge-v2-output.js';

// ── Fetch a single agent file as ArrayBuffer ──
async function fetchAgentFile(jobId, filename) {
  const url = `${agentBridge.baseUrl}/files/${encodeURIComponent(jobId)}/${encodeURIComponent(filename)}`;
  const res = await agentBridge._authFetch(url);
  if (!res.ok) {
    if (res.status === 404) return null;   // file not present (e.g., document missing)
    throw new Error(`Fetch ${filename} failed: HTTP ${res.status}`);
  }
  return await res.arrayBuffer();
}

// ── Pre-load both files for one row. Returns { invoiceBuf, docBuf } (either may be null). ──
async function preloadRowFiles(jobId, row) {
  const cn = row.containerNumber;
  const [invoiceBuf, docBuf] = await Promise.all([
    fetchAgentFile(jobId, `${cn}_invoice.pdf`),
    // Errored rows skip the doc fetch entirely — saves a 404 round-trip.
    row.fetchResult?.podPill === 'miss'
      ? Promise.resolve(null)
      : fetchAgentFile(jobId, `${cn}_pod.pdf`),
  ]);
  return { invoiceBuf, docBuf };
}

// ── Concatenate page-arrays into one PDFDocument and serialize ──
async function concatPages(pageGroups) {
  const { PDFDocument } = PDFLib;
  const out = await PDFDocument.create();
  for (const group of pageGroups) {
    if (!group) continue;
    const src = await PDFDocument.load(group, { ignoreEncryption: true, updateMetadata: false });
    const pages = await out.copyPages(src, src.getPageIndices());
    pages.forEach(p => out.addPage(p));
  }
  return await out.save({ updateFieldAppearances: false });
}

// ── Per-container modes ──
async function runPerContainer(rows, jobId, modeKey, onProgress) {
  const { PDFDocument } = PDFLib;
  const files = [];
  let totalPages = 0;
  let totalBytes = 0;

  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    onProgress?.({ done: i, total: rows.length, current: row.containerNumber });

    const { invoiceBuf, docBuf } = await preloadRowFiles(jobId, row);
    if (!invoiceBuf && !docBuf) continue;  // nothing to merge for this row

    let bufs;
    if (modeKey === 'per-container')               bufs = [invoiceBuf, docBuf];
    else if (modeKey === 'per-container-invoice')  bufs = [invoiceBuf];
    else if (modeKey === 'per-container-document') bufs = [docBuf];
    else throw new Error(`runPerContainer: not a per-container mode: ${modeKey}`);

    bufs = bufs.filter(Boolean);
    if (bufs.length === 0) continue;

    const merged = await concatPages(bufs);
    files.push({ filename: perContainerFilename(row, modeKey), bytes: merged });
    totalBytes += merged.byteLength;

    // Quick page-count tally (re-load just to read length — cheap on already-merged tiny PDFs)
    const tally = await PDFDocument.load(merged, { ignoreEncryption: true });
    totalPages += tally.getPageCount();
  }

  onProgress?.({ done: rows.length, total: rows.length, current: '' });
  return { files, stats: { fileCount: files.length, totalPages, totalBytes } };
}

// ── Single-output modes ──
async function runCombined(rows, jobId, modeKey, onProgress) {
  const { PDFDocument } = PDFLib;
  const out = await PDFDocument.create();
  let totalPages = 0;

  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    onProgress?.({ done: i, total: rows.length, current: row.containerNumber });

    const { invoiceBuf, docBuf } = await preloadRowFiles(jobId, row);

    let bufs;
    if (modeKey === 'combined')           bufs = [invoiceBuf, docBuf];
    else if (modeKey === 'invoice-only')  bufs = [invoiceBuf];
    else if (modeKey === 'document-only') bufs = [docBuf];
    else throw new Error(`runCombined: not a combined mode: ${modeKey}`);

    for (const buf of bufs) {
      if (!buf) continue;
      const src = await PDFDocument.load(buf, { ignoreEncryption: true, updateMetadata: false });
      const pages = await out.copyPages(src, src.getPageIndices());
      pages.forEach(p => out.addPage(p));
      totalPages += pages.length;
    }
  }

  onProgress?.({ done: rows.length, total: rows.length, current: '' });

  if (totalPages === 0) {
    return { files: [], stats: { fileCount: 0, totalPages: 0, totalBytes: 0 } };
  }

  const bytes = await out.save({ updateFieldAppearances: false });
  return {
    files: [{ filename: singleOutputFilename(modeKey), bytes }],
    stats: { fileCount: 1, totalPages, totalBytes: bytes.byteLength },
  };
}

// ── Public dispatcher ──
//   rows: filtered subset (selected, non-skipped, sorted as user wants on Ready)
//   jobId: the v2State.jobId from the fetch
//   modeKey: one of MODES[].key
//   onProgress: optional ({ done, total, current }) => void

export async function runMergeMode({ rows, jobId, modeKey, onProgress }) {
  const mode = modeByKey(modeKey);
  if (!mode) throw new Error(`Unknown mode: ${modeKey}`);
  if (!jobId) throw new Error('runMergeMode: jobId is required');
  if (!rows || rows.length === 0) {
    return { files: [], stats: { fileCount: 0, totalPages: 0, totalBytes: 0 } };
  }
  if (mode.group === 'per-container') {
    return runPerContainer(rows, jobId, modeKey, onProgress);
  }
  return runCombined(rows, jobId, modeKey, onProgress);
}
```

- [ ] **Step 2: Smoke-test the engine in DevTools**

After running a real fetch in the app (so `v2State.jobId` and rows are populated), open DevTools and run:

```javascript
const eng = await import('./assets/js/tools/merge/merge-v2-engine.js');
const v2 = (await import('./assets/js/tools/merge/merge-v2.js'));
// Grab the rows from the live state — we'll cheat by reading the global mirror
const rows = window.__v2State?.rows?.filter(r => r.selected && r.fetchResult?.podPill !== 'miss') || [];
const result = await eng.runMergeMode({
  rows: rows.slice(0, 2),   // just first 2 for speed
  jobId: window.__v2State.jobId,
  modeKey: 'per-container',
  onProgress: (p) => console.log('progress', p),
});
console.log(result.stats);
console.log(result.files.map(f => f.filename));
```

Expected: 2 progress logs, then `{ fileCount: 2, totalPages: <some>, totalBytes: <some> }` and 2 PDF filenames.

(If `window.__v2State` doesn't exist yet, expose it temporarily by adding `window.__v2State = v2State;` at the top of `merge-v2.js`. Remove before final commit.)

- [ ] **Step 3: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2-engine.js
git commit -m "feat(merge/v2/m4): add engine module — 6 merge mode functions over pdf-lib"
```

---

## Task 6: Frontend — extend `v2State` with M4 fields + localStorage for output location

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js`

Add the M4 state fields and a localStorage helper for the chosen output folder.

- [ ] **Step 1: Replace the `v2State` initial object**

In `app/assets/js/tools/merge/merge-v2.js`, find the `v2State` declaration (lines 17-41) and replace it with:

```javascript
// ── Module-local state ──
const v2State = {
  subMode: 'empty',          // empty | loading | review | fetching | ready | merge
  excelFile: null,
  excelHeaders: [],
  rows: [],
  loadingError: null,
  searchQuery: '',
  sortMode: 'excel',
  activeTab: 'all',
  showAllInSuccess: false,
  // M3: fetch + sidebar
  jobId: null,
  eventSource: null,
  fetchProgress: 0,
  fetchTotal: 0,
  fetchCurrentContainer: '',
  lastFetchedContainer: '',
  openSidebarRow: null,
  queuedRetries: [],
  completedContainers: null,
  // M4: merge screen
  outputLocation: null,           // absolute path; null → agent uses OUTPUT_DIR
  completedModes: {},             // { modeKey: { stats, files: [{filename, path}], completedAt: Date } }
  runningMode: null,              // modeKey of in-progress merge (null = nothing running)
  mergeProgress: { done: 0, total: 0, current: '' },
  confirmPopup: null,             // null | { uncheckedCount, onContinue, onCancel }
  pendingMode: null,              // unused — kept for backward compat
  lastCompletedMode: null,        // unused — kept for backward compat
};

// ── localStorage for output location ──
const LS_OUTPUT_LOCATION = 'mergeV2OutputLocation';
function loadSavedOutputLocation() {
  try { return localStorage.getItem(LS_OUTPUT_LOCATION) || null; } catch { return null; }
}
function saveOutputLocation(path) {
  try {
    if (path) localStorage.setItem(LS_OUTPUT_LOCATION, path);
    else localStorage.removeItem(LS_OUTPUT_LOCATION);
  } catch {}
}
```

- [ ] **Step 2: Wire the saved location on init**

Find `export function initMergeV2()` and update its body to load the saved location:

```javascript
export function initMergeV2() {
  if (_initialized) {
    setStateV2(v2State.subMode);
    return;
  }
  _initialized = true;
  v2State.outputLocation = loadSavedOutputLocation();
  const xinput = document.getElementById('v2ExcelInput');
  if (xinput) xinput.addEventListener('change', handleExcelChange);
  setStateV2('empty');
}
```

- [ ] **Step 3: Reset M4 fields on `setStateV2('empty')`**

Find the empty-state reset block in `setStateV2` (around lines 96-114) and add these resets:

```javascript
    v2State.completedModes = {};
    v2State.runningMode = null;
    v2State.mergeProgress = { done: 0, total: 0, current: '' };
    v2State.confirmPopup = null;
    // outputLocation is preserved across runs — survives the reset.
```

Place these inside the existing `if (name === 'empty')` block, alongside the other field resets (after `v2State.completedContainers = null;`).

- [ ] **Step 4: Update STATES table to use a single Merge state**

Replace the existing `STATE_GROUP` and `STATES` blocks (lines 45-57) with:

```javascript
const STATE_GROUP = {
  empty: 's1', loading: 's1',
  review: 's2', fetching: 's2',
  ready: 's3',
  merge: 's4',
};

const STATES = {
  s1: () => v2State.subMode === 'loading' ? renderLoading() : renderEmpty(),
  s2: () => v2State.subMode === 'fetching' ? renderFetching() : renderReview(),
  s3: () => renderReady(),
  s4: () => renderMerge(),
};
```

(We collapse Merging + Done into one state called `merge`. The single `renderMerge` function handles both the running merge and the completed-cards display.)

- [ ] **Step 5: Update the comment on `subMode`**

Find line 18 and replace its comment to match the new state list:

```javascript
  subMode: 'empty',          // empty | loading | review | fetching | ready | merge
```

- [ ] **Step 6: Replace stub `renderMerging` and `renderDone` with a placeholder `renderMerge`**

Find lines 1243-1244 (the two stubs) and replace BOTH with a single placeholder that we'll fill in Task 8:

```javascript
function renderMerge() { return `<div class="centered-stage"><h1>Merge (M4)</h1><p class="subtitle">Stub — replaced in Task 8.</p></div>`; }
```

- [ ] **Step 7: Update Continue-to-Merge handler to set the new state name**

Find `function v2ClickContinueMerge` (around line 1323) and update it to use the new state name:

```javascript
function v2ClickContinueMerge() {
  setStateV2('merge');   // M4 stub — Task 7 wires the popup
}
```

- [ ] **Step 8: Verify nothing broke**

Run the app, drop an Excel, fetch some containers, click Continue to Merge. You should see "Merge (M4) — Stub" instead of the old "Merging (M4)". All other states still work normally.

- [ ] **Step 9: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge/v2/m4): extend v2State with completedModes + outputLocation, collapse to single merge state"
```

---

## Task 7: Frontend — pre-merge confirmation popup

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js`
- Modify: `app/assets/css/styles.css`

The popup fires when the user clicks Continue to Merge with at least one row unchecked. It's a modal centered both axes with the NGL aesthetic.

- [ ] **Step 1: Add popup CSS to `app/assets/css/styles.css`**

Append at the end of the file:

```css
/* ── M4: confirmation modal ─────────────────────────── */
.v2-modal-backdrop {
  position: fixed; inset: 0;
  background: rgba(15, 23, 42, 0.45);
  z-index: 300;
}
.v2-modal {
  position: fixed;
  top: 50%; left: 50%;
  transform: translate(-50%, -50%);
  width: 440px; max-width: 90vw;
  background: #ffffff;
  border-radius: 12px;
  box-shadow: 0 20px 40px rgba(0, 0, 0, 0.15);
  padding: 28px;
  z-index: 301;
  font-family: inherit;
}
.v2-modal-title {
  display: flex; align-items: center; gap: 8px;
  font-size: 1rem; font-weight: 600; color: #0f172a;
  margin-bottom: 12px;
}
.v2-modal-title .icon {
  width: 22px; height: 22px;
  border-radius: 50%;
  background: #fef3c7; color: #92400e;
  display: inline-flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: 0.78rem;
}
.v2-modal-body {
  font-size: 0.88rem; color: #475569;
  line-height: 1.5;
  margin-bottom: 20px;
}
.v2-modal-actions {
  display: flex; justify-content: flex-end; gap: 8px;
}
.v2-modal-actions .btn-secondary {
  background: #ffffff; border: 1px solid #e2e8f0;
  color: #475569; padding: 8px 14px;
  border-radius: 7px; font-size: 0.86rem;
  cursor: pointer; font-family: inherit;
}
.v2-modal-actions .btn-secondary:hover { border-color: #cbd5e1; color: #0f172a; }
.v2-modal-actions .btn-primary {
  background: #ea580c; border: none;
  color: white; padding: 8px 16px;
  border-radius: 7px; font-size: 0.86rem; font-weight: 600;
  cursor: pointer; font-family: inherit;
  display: inline-flex; align-items: center; gap: 6px;
}
.v2-modal-actions .btn-primary:hover { background: #c2410c; }
```

- [ ] **Step 2: Add the popup render + handlers to `merge-v2.js`**

Insert this block immediately above `function renderMerge()`:

```javascript
// ── M4: pre-merge confirmation popup ──
function renderConfirmPopup() {
  if (!v2State.confirmPopup) return '';
  const { uncheckedCount } = v2State.confirmPopup;
  return `
    <div class="v2-modal-backdrop" onclick="window.v2CancelConfirm()"></div>
    <div class="v2-modal" role="dialog" aria-modal="true">
      <div class="v2-modal-title"><span class="icon">ⓘ</span> Confirm merge selection</div>
      <div class="v2-modal-body">
        ${uncheckedCount} row${uncheckedCount === 1 ? '' : 's'} ${uncheckedCount === 1 ? 'is' : 'are'} unchecked and will not be included in this merge.
      </div>
      <div class="v2-modal-actions">
        <button class="btn-secondary" onclick="window.v2CancelConfirm()">Go Back</button>
        <button class="btn-primary" onclick="window.v2AcceptConfirm()">Continue ▶</button>
      </div>
    </div>
  `;
}

function v2AcceptConfirm() {
  const cb = v2State.confirmPopup?.onContinue;
  v2State.confirmPopup = null;
  if (cb) cb();
}
function v2CancelConfirm() {
  v2State.confirmPopup = null;
  setStateV2(v2State.subMode);   // re-render to drop the popup
}

window.v2AcceptConfirm = v2AcceptConfirm;
window.v2CancelConfirm = v2CancelConfirm;
```

- [ ] **Step 3: Update `v2ClickContinueMerge` to invoke the popup when needed**

Replace the stub (added in Task 6 step 7) with the real handler:

```javascript
function v2ClickContinueMerge() {
  // Count unchecked rows that COULD have been merged (have fetchResult, not skipped).
  // Errored-but-unchecked rows count too — they could be opted into via the new interactive checkbox.
  const candidateRows = v2State.rows.filter(r => r.fetchResult && !r.skipped);
  const uncheckedCount = candidateRows.filter(r => !r.selected).length;

  const proceed = () => setStateV2('merge');

  if (uncheckedCount > 0) {
    v2State.confirmPopup = { uncheckedCount, onContinue: proceed };
    setStateV2(v2State.subMode);   // re-render Ready with the popup overlay
    return;
  }
  proceed();
}
```

- [ ] **Step 4: Render the popup overlay on the Ready screen**

Find `function renderReady()` and locate its return statement (around line 982). Append `${renderConfirmPopup()}` to the returned template:

```javascript
  return `
    ${topBarWithDrop()}
    ${routingSummaryBand()}
    ${actionBar}
    ${tabsHtml}
    ${toolbarHtml}
    ${tableHtml}
    ${sidebarHtml}
    ${renderConfirmPopup()}
  `;
```

- [ ] **Step 5: Manual test**

In the app, drop an Excel, fetch some rows, then on the Ready screen:
- Uncheck a row → click Continue to Merge → popup shows "1 row is unchecked..." → Go Back closes it, Continue moves to merge state.
- All rows checked → click Continue to Merge → no popup, straight to merge state.

- [ ] **Step 6: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js app/assets/css/styles.css
git commit -m "feat(merge/v2/m4): pre-merge confirmation popup with NGL modal aesthetic"
```

---

## Task 8: Frontend — render the Merge screen (mode card grid + completed cards)

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js`
- Modify: `app/assets/css/styles.css`

This is the main M4 visual: 6 cards in 2 groups, each in pickable / running / completed state. Plus the output-location button in the header.

- [ ] **Step 1: Add Merge screen CSS**

Append to `app/assets/css/styles.css`:

```css
/* ── M4: merge screen ──────────────────────────────── */
.merge-screen-header {
  display: flex; align-items: center; gap: 12px;
  margin-bottom: 16px;
}
.merge-screen-header .header-spacer { flex: 1; }
.output-location-btn {
  background: white; border: 1px solid #e2e8f0;
  border-radius: 7px; padding: 7px 12px;
  font-size: 0.82rem; color: #475569;
  cursor: pointer; font-family: inherit;
  display: inline-flex; align-items: center; gap: 6px;
}
.output-location-btn:hover { border-color: #cbd5e1; color: #0f172a; }
.output-location-btn .label-prefix {
  color: #94a3b8; font-size: 0.74rem;
  text-transform: uppercase; letter-spacing: 0.04em;
}
.output-location-btn .path-text {
  font-family: 'SF Mono', Consolas, monospace;
  font-size: 0.78rem; color: #1e293b;
  max-width: 260px; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap;
}

.mode-group { margin-bottom: 22px; }
.mode-group-label {
  font-size: 0.74rem; font-weight: 700; color: #64748b;
  text-transform: uppercase; letter-spacing: 0.05em;
  margin-bottom: 10px;
}
.mode-grid {
  display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px;
}

.mode-card {
  background: white; border: 1px solid #e2e8f0;
  border-radius: 10px; padding: 16px;
  cursor: pointer; transition: border-color 0.15s, box-shadow 0.15s;
  font-family: inherit; text-align: left;
  display: flex; flex-direction: column; gap: 6px;
  min-height: 110px;
}
.mode-card:hover {
  border-color: #fed7aa;
  box-shadow: 0 2px 8px rgba(234, 88, 12, 0.08);
}
.mode-card .title {
  font-size: 0.92rem; font-weight: 600; color: #0f172a;
}
.mode-card .description {
  font-size: 0.8rem; color: #64748b; line-height: 1.45;
}

/* Running state — spinner inline, dim other cards */
.mode-card.running {
  border-color: #ea580c;
  background: #fff7ed;
  cursor: wait;
}
.mode-card.running .title::before {
  content: '⟳';
  display: inline-block; margin-right: 6px;
  animation: spin 1s linear infinite;
  color: #ea580c;
}
.mode-card.dim { opacity: 0.45; cursor: not-allowed; pointer-events: none; }
@keyframes spin { from { transform: rotate(0); } to { transform: rotate(360deg); } }

/* Completed state — green check, stats, action buttons */
.mode-card.completed {
  border-color: #86efac;
  background: #f0fdf4;
  cursor: default;
}
.mode-card.completed:hover { box-shadow: none; }
.mode-card.completed .title::before {
  content: '✓ ';
  color: #16a34a;
  font-weight: 800;
}
.mode-card .stats-line {
  font-size: 0.78rem; color: #475569;
  margin-top: 4px;
}
.mode-card .completed-actions {
  display: flex; gap: 6px; margin-top: 10px; flex-wrap: wrap;
}
.mode-card .card-action-btn {
  background: white; border: 1px solid #cbd5e1;
  border-radius: 6px; padding: 5px 11px;
  font-size: 0.78rem; color: #475569;
  cursor: pointer; font-family: inherit;
}
.mode-card .card-action-btn:hover {
  border-color: #ea580c; color: #ea580c;
}

.merge-running-banner {
  background: #fff7ed; border: 1px solid #fed7aa;
  border-radius: 8px; padding: 10px 16px;
  font-size: 0.86rem; color: #c2410c;
  margin-bottom: 16px;
  display: flex; align-items: center; gap: 10px;
}
```

- [ ] **Step 2: Replace the stub `renderMerge` with the full implementation**

In `merge-v2.js`, replace the stub `renderMerge` (added in Task 6) with:

```javascript
// ── M4: Merge screen (pickable + completed cards) ──
function renderMerge() {
  const isRunning = !!v2State.runningMode;

  // Header: output location + (back to ready already wired by setStateV2)
  const locText = v2State.outputLocation
    ? shortenPath(v2State.outputLocation)
    : 'Desktop (default)';
  const headerHtml = `
    <div class="merge-screen-header">
      <h2 style="margin:0; font-size:1.1rem; font-weight:600;">Choose a merge format</h2>
      <span class="header-spacer"></span>
      <button class="output-location-btn" onclick="window.v2ChangeOutputLocation()" title="Change where files are saved">
        <span class="label-prefix">Output:</span>
        <span class="path-text">${escHtml(locText)}</span>
      </button>
    </div>
  `;

  // Optional running banner with progress
  let runningBanner = '';
  if (isRunning) {
    const p = v2State.mergeProgress;
    const pct = p.total > 0 ? Math.round((p.done / p.total) * 100) : 0;
    runningBanner = `
      <div class="merge-running-banner">
        <span>⟳ Merging ${escHtml(modeNameOf(v2State.runningMode))}…</span>
        <span style="color:#94a3b8;">${p.done} / ${p.total}${p.current ? ` · ${escHtml(p.current)}` : ''}</span>
        <span style="margin-left:auto; font-weight:600;">${pct}%</span>
      </div>
    `;
  }

  // Group cards by group (per-container vs combined)
  const perCont = MODES_LIST.filter(m => m.group === 'per-container');
  const combined = MODES_LIST.filter(m => m.group === 'combined');

  return `
    ${headerHtml}
    ${runningBanner}
    <div class="mode-group">
      <div class="mode-group-label">Per-container outputs (one PDF per container)</div>
      <div class="mode-grid">
        ${perCont.map(m => renderModeCard(m, isRunning)).join('')}
      </div>
    </div>
    <div class="mode-group">
      <div class="mode-group-label">Single combined output (one PDF total)</div>
      <div class="mode-grid">
        ${combined.map(m => renderModeCard(m, isRunning)).join('')}
      </div>
    </div>
  `;
}

function renderModeCard(mode, anyRunning) {
  const completed = v2State.completedModes[mode.key];
  const running = v2State.runningMode === mode.key;
  const dim = anyRunning && !running && !completed;

  if (running) {
    return `
      <div class="mode-card running">
        <div class="title">${escHtml(mode.title)}</div>
        <div class="description">${escHtml(mode.description)}</div>
      </div>
    `;
  }
  if (completed) {
    const stats = formatCompletedStats(completed);
    const showOpenFile = (mode.group === 'combined');   // hide for per-container
    return `
      <div class="mode-card completed">
        <div class="title">${escHtml(mode.title)}</div>
        <div class="stats-line">${escHtml(stats)}</div>
        <div class="completed-actions">
          ${showOpenFile ? `<button class="card-action-btn" onclick="window.v2OpenOutputFile('${mode.key}')">Open File</button>` : ''}
          <button class="card-action-btn" onclick="window.v2OpenOutputFolder('${mode.key}')">Open Folder</button>
          <button class="card-action-btn" onclick="window.v2RerunMode('${mode.key}')">Re-run</button>
        </div>
      </div>
    `;
  }
  // Pickable
  return `
    <button class="mode-card ${dim ? 'dim' : ''}" onclick="window.v2ClickModeCard('${mode.key}')">
      <div class="title">${escHtml(mode.title)}</div>
      <div class="description">${escHtml(mode.description)}</div>
    </button>
  `;
}

function modeNameOf(modeKey) {
  const m = MODES_LIST.find(x => x.key === modeKey);
  return m ? m.title : modeKey;
}

function formatCompletedStats(completed) {
  const { stats, completedAt } = completed;
  const sizeMb = (stats.totalBytes / (1024 * 1024)).toFixed(1);
  const time = completedAt instanceof Date
    ? completedAt.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
    : '';
  const fileLabel = stats.fileCount === 1 ? '1 PDF' : `${stats.fileCount} PDFs`;
  return `${fileLabel} · ${stats.totalPages} pages · ${sizeMb} MB${time ? ` · ${time}` : ''}`;
}

function shortenPath(p) {
  if (!p) return '';
  if (p.length <= 36) return p;
  // Keep drive + last two folders
  const parts = p.replace(/\\/g, '/').split('/').filter(Boolean);
  if (parts.length <= 3) return p;
  return `${parts[0]}\\…\\${parts[parts.length - 2]}\\${parts[parts.length - 1]}`;
}
```

- [ ] **Step 3: Import the modes list at the top of `merge-v2.js`**

Find the import block at the top of the file (around lines 10-14) and add the modes import:

```javascript
import { MODES as MODES_LIST, modeByKey } from './merge-v2-output.js';
```

- [ ] **Step 4: Stub out the action handlers (Task 9 wires them)**

Add these stub handlers just below the `renderModeCard` function:

```javascript
// Stubs — wired in Task 9
function v2ClickModeCard(modeKey) { console.log('TODO: click', modeKey); }
function v2OpenOutputFile(modeKey) { console.log('TODO: open file', modeKey); }
function v2OpenOutputFolder(modeKey) { console.log('TODO: open folder', modeKey); }
function v2RerunMode(modeKey) { console.log('TODO: rerun', modeKey); }
function v2ChangeOutputLocation() { console.log('TODO: change location'); }

window.v2ClickModeCard = v2ClickModeCard;
window.v2OpenOutputFile = v2OpenOutputFile;
window.v2OpenOutputFolder = v2OpenOutputFolder;
window.v2RerunMode = v2RerunMode;
window.v2ChangeOutputLocation = v2ChangeOutputLocation;
```

- [ ] **Step 5: Visual smoke test**

Run the app, fetch some containers, click Continue to Merge → should land on a screen with 6 mode cards in 2 groups + an "Output: Desktop (default)" button. Cards should hover-light orange. Clicking does nothing yet (logs to console).

- [ ] **Step 6: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js app/assets/css/styles.css
git commit -m "feat(merge/v2/m4): render Merge screen — 6 mode cards in 2 groups + output location button"
```

---

## Task 9: Frontend — wire mode-card click, completed-state action handlers, and output-location picker

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js`

Replace the Task 8 stubs with real handlers.

- [ ] **Step 1: Import the engine + saveMergedFiles at the top of `merge-v2.js`**

Add to the import block:

```javascript
import { runMergeMode } from './merge-v2-engine.js';
import { saveMergedFiles, subfolderFor } from './merge-v2-output.js';
```

(`MODES_LIST` and `modeByKey` were already imported in Task 8.)

- [ ] **Step 2: Replace the stub handlers with the real ones**

Find the stub block from Task 8 step 4 and replace with:

```javascript
async function v2ClickModeCard(modeKey) {
  // Block re-entry while another merge is running.
  if (v2State.runningMode) return;

  // Build the row set: selected, non-skipped, non-errored OR errored-but-checked.
  // Engine handles the case where document is missing — silently produces invoice-only entry.
  const rows = v2State.rows.filter(r => r.selected && r.fetchResult && !r.skipped);
  if (rows.length === 0) {
    alert('No rows selected. Go back to Ready and check at least one row.');
    return;
  }

  v2State.runningMode = modeKey;
  v2State.mergeProgress = { done: 0, total: rows.length, current: '' };
  setStateV2('merge');   // shows running banner + spinner card

  try {
    const result = await runMergeMode({
      rows,
      jobId: v2State.jobId,
      modeKey,
      onProgress: (p) => {
        v2State.mergeProgress = p;
        // Update only the banner — avoid full re-render mid-merge
        const banner = document.querySelector('.merge-running-banner');
        if (banner) {
          const pct = p.total > 0 ? Math.round((p.done / p.total) * 100) : 0;
          banner.innerHTML = `
            <span>⟳ Merging ${escHtml(modeNameOf(modeKey))}…</span>
            <span style="color:#94a3b8;">${p.done} / ${p.total}${p.current ? ' · ' + escHtml(p.current) : ''}</span>
            <span style="margin-left:auto; font-weight:600;">${pct}%</span>
          `;
        }
      },
    });

    if (result.files.length === 0) {
      alert('Merge produced no files. Check the row selection and try again.');
      v2State.runningMode = null;
      setStateV2('merge');
      return;
    }

    // Write to disk
    const saveRes = await saveMergedFiles({
      files: result.files,
      modeKey,
      baseLocation: v2State.outputLocation,
      openFolder: false,   // don't auto-open; user clicks Open Folder if they want
    });
    if (saveRes.error) {
      alert(`Save failed: ${saveRes.error}`);
      v2State.runningMode = null;
      setStateV2('merge');
      return;
    }

    // Record completion. Pull file paths back from the agent response so Open File works.
    v2State.completedModes[modeKey] = {
      stats: result.stats,
      files: (saveRes.files || []).filter(f => !f.error),
      outputDir: saveRes.outputDir,
      completedAt: new Date(),
    };
    v2State.runningMode = null;
    v2State.mergeProgress = { done: 0, total: 0, current: '' };
    setStateV2('merge');
  } catch (err) {
    console.error('Merge failed:', err);
    alert(`Merge failed: ${err.message}`);
    v2State.runningMode = null;
    setStateV2('merge');
  }
}

function v2OpenOutputFile(modeKey) {
  const completed = v2State.completedModes[modeKey];
  if (!completed || completed.files.length === 0) return;
  // Single-output modes have exactly one file. We hide the button for per-container modes.
  const target = completed.files[0]?.path;
  if (target) agentBridge.openPath(target);
}

function v2OpenOutputFolder(modeKey) {
  const completed = v2State.completedModes[modeKey];
  if (!completed) return;
  if (completed.outputDir) agentBridge.openPath(completed.outputDir);
}

function v2RerunMode(modeKey) {
  // Re-run uses the same row selection. saveMergedFiles already passes overwriteFolder: true.
  // Drop the existing completion record so the card flips back to running while it works.
  delete v2State.completedModes[modeKey];
  v2ClickModeCard(modeKey);
}

async function v2ChangeOutputLocation() {
  const res = await agentBridge.pickFolder();
  if (res.error) { alert(`Couldn't open folder picker: ${res.error}`); return; }
  if (!res.path) return;   // user cancelled
  v2State.outputLocation = res.path;
  saveOutputLocation(res.path);
  setStateV2('merge');
}

window.v2ClickModeCard = v2ClickModeCard;
window.v2OpenOutputFile = v2OpenOutputFile;
window.v2OpenOutputFolder = v2OpenOutputFolder;
window.v2RerunMode = v2RerunMode;
window.v2ChangeOutputLocation = v2ChangeOutputLocation;
```

- [ ] **Step 3: Manual end-to-end test**

1. Drop an Excel, fetch containers (use a small test set — 2-3 rows).
2. Click Continue to Merge → land on Merge screen.
3. Click "Output:" button → native picker opens → pick a test folder (e.g., `C:\Users\[you]\Desktop\m4-test`).
4. Click "Per Container" card → progress banner shows → card flips to completed with stats.
5. Click "Open Folder" → Explorer opens at the date folder, with N PDFs inside.
6. Click "Combined PDF" card → it runs → both cards now show as completed.
7. On the Combined card, click "Open File" → the PDF opens in your default viewer.
8. Click "Re-run" on Per Container → it runs again → the date folder gets cleared and re-written (verify in Explorer that the PDFs have a fresh modified time).

- [ ] **Step 4: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge/v2/m4): wire mode-card click + Open File/Folder/Re-run + output location picker"
```

---

## Task 10: Frontend — make errored-row checkboxes interactive on Ready

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js`

Per spec §"Default checkbox behavior": errored rows should be unchecked by default but interactive (not disabled).

- [ ] **Step 1: Update the Ready row markup to remove the `disabled` attribute**

Find `function fetchRowMarkup` (or the Ready row renderer). Search for the existing line that renders the per-row checkbox (around line 518):

```javascript
                onchange="window.v2ToggleFetchRow(${rowIdx}, this.checked)" />
```

The full existing line in the renderer is built earlier — locate the `checkable` variable (around line 504) and the checkbox rendering. Replace this block:

```javascript
  const checkable = !!row.fetchResult && row.fetchResult.podPill !== 'miss' && !row.skipped;
  const checkAttr = checkable && row.selected ? 'checked' : '';
  const disabled  = !checkable ? 'disabled' : '';
```

with:

```javascript
  // M4: errored rows are unchecked by default but still INTERACTIVE.
  // Skipped (dedup) rows remain disabled — they can't be merged at all.
  const hasFetch  = !!row.fetchResult;
  const isError   = row.fetchResult?.podPill === 'miss';
  const isSkipped = !!row.skipped;
  const interactive = hasFetch && !isSkipped;
  const checkAttr = interactive && row.selected ? 'checked' : '';
  const disabled  = interactive ? '' : 'disabled';
  const errorTitle = isError ? ' title="This row is missing a document. If checked, only the invoice page will be merged."' : '';
```

Then in the same function locate the `<input type=checkbox>` line and add `${errorTitle}` before the closing `/>`:

```javascript
      <input type="checkbox" class="row-check" ${checkAttr} ${disabled}${errorTitle}
             onchange="window.v2ToggleFetchRow(${rowIdx}, this.checked)" />
```

- [ ] **Step 2: Update `v2ToggleAllReady` so master-check covers errored rows too**

Find `function v2ToggleAllReady` (around line 1305) and replace:

```javascript
function v2ToggleAllReady(checked) {
  for (const row of v2State.rows) {
    if (row.fetchResult && !row.skipped) {
      row.selected = !!checked;
    }
  }
  setStateV2('ready');
}
```

(The change: drop the `&& row.fetchResult.podPill !== 'miss'` clause — errored rows now toggle with the master too.)

- [ ] **Step 3: Update the Continue-to-Merge enabled count**

Find the line in `renderReady` that builds the selected count for the Continue button (around line 880):

```javascript
  const selected = ready.filter(r => r.selected).length;
```

Replace with:

```javascript
  // M4: errored rows can also be selected; they'll merge with invoice-page-only.
  const selectableRows = all.filter(r => r.fetchResult && !r.skipped);
  const selected = selectableRows.filter(r => r.selected).length;
```

And on the live count badge updater (around line 1320 in `v2ToggleFetchRow`), update the filter:

```javascript
function v2ToggleFetchRow(rowIdx, checked) {
  const row = v2State.rows[rowIdx];
  if (!row) return;
  row.selected = !!checked;
  const cnt = document.querySelector('#v2BtnContinueMerge .sel-count');
  if (cnt) {
    cnt.textContent = v2State.rows.filter(r => r.selected && r.fetchResult && !r.skipped).length;
  }
}
```

- [ ] **Step 4: Manual test**

1. Drop an Excel and fetch containers — pick a set that has at least one error.
2. On Ready, the errored row's checkbox should now be **unchecked but clickable** (not greyed out).
3. Tick the errored row → the Continue to Merge count should bump by 1 → click Continue → no popup if everything else is checked → land on Merge.
4. Run Per Container → check the output folder: the errored container's PDF should contain only the invoice page (no document).

- [ ] **Step 5: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge/v2/m4): make errored-row checkboxes interactive (invoice-only inclusion)"
```

---

## Task 11: Frontend — wire Back-to-Ready button + fix New Merge confirmation flow

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js`

The Back-to-Ready button is already in the markup (`v2BtnBackToReady`) and `setStateV2` toggles its visibility for `s4`. M4 needs that button to actually go back. Also: New Merge should NOT silently nuke completed merges — confirm if any modes have been completed.

- [ ] **Step 1: Wire the Back-to-Ready button onclick**

Find the existing `v2BtnBackToReady` button in `app/index.html` (around the v2 tool header area). Verify it has an onclick attribute. If not, add `onclick="window.v2BackToReady()"`. Use Grep first to find it.

```bash
# Run this once to confirm the button's current attributes
```

Use Grep:
```
pattern: v2BtnBackToReady
glob: app/index.html
output_mode: content
-n: true
```

If the button lacks an `onclick`, edit the HTML to add it:

```html
<button id="v2BtnBackToReady" class="header-action-btn" style="display:none;"
        onclick="window.v2BackToReady()">← Back to Ready</button>
```

- [ ] **Step 2: Add the handler to `merge-v2.js`**

Add near the other window-exposed handlers (after `v2ChangeOutputLocation` from Task 9):

```javascript
function v2BackToReady() {
  // Preserves rows + completedModes — user can come back and run more modes.
  setStateV2('ready');
}
window.v2BackToReady = v2BackToReady;
```

- [ ] **Step 3: Add a confirmation to New Merge when completed modes exist**

Find the existing `v2BtnNewMerge` button handler. Search for `setStateV2('empty')` in `merge-v2.js` — the New Merge button likely calls `setStateV2('empty')` directly. We want to wrap that with a confirm if completedModes is non-empty.

If there's an existing handler function (e.g., `v2NewMerge`), update it. Otherwise, add this:

```javascript
function v2NewMerge() {
  const completedCount = Object.keys(v2State.completedModes || {}).length;
  if (completedCount > 0) {
    if (!confirm(`This will reset the workflow and clear ${completedCount} completed merge${completedCount === 1 ? '' : 's'} from the Merge screen. (Files on disk are kept.) Continue?`)) {
      return;
    }
  }
  setStateV2('empty');
}
window.v2NewMerge = v2NewMerge;
```

Update `app/index.html` so the New Merge button's onclick points at `window.v2NewMerge()` instead of `setStateV2('empty')` directly:

Use Grep first to find the button:
```
pattern: v2BtnNewMerge
glob: app/index.html
output_mode: content
-n: true
```

Then edit the onclick to read `onclick="window.v2NewMerge()"`.

- [ ] **Step 4: Manual test**

1. Run a couple of merges → Back to Ready works → Continue to Merge again → completed cards still present.
2. Click New Merge → confirm dialog ("clear 2 completed merges...") → cancel keeps state, OK resets.

- [ ] **Step 5: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js app/index.html
git commit -m "feat(merge/v2/m4): wire Back-to-Ready button + confirm New Merge when completed modes exist"
```

---

## Task 12: Polish, version bump, build, push, release

**Files:**
- Modify: `desktop/VERSION`
- Build artifacts (no commit)

Per project memory: every rebuild MUST bump version, build, commit, push, and publish a GH release with installer + latest.yml. The `runbuild.bat` flow handles agent + Electron build.

- [ ] **Step 1: Run a full manual QA pass against the spec acceptance criteria**

Walk through each AC from the design spec one by one:

1. From Ready with all rows checked, click Continue to Merge → land on Merge with 6 cards, no popup.
2. From Ready with rows unchecked, click Continue to Merge → popup appears with the count.
3. Click any mode card → progress → completes with stats + buttons.
4. Click a second mode card → both show as completed.
5. Click Open File on a single-output completed card → PDF opens.
6. Click Open Folder on any completed card → Explorer opens at the date folder.
7. Click Re-run → mode re-runs → date folder is cleared and re-written.
8. Click Back to Ready → state preserved, Continue to Merge restores the same Merge screen.
9. Click + New Merge → confirm dialog, then reset.
10. Files land at `[chosen location]/Merge Outputs/[Mode]/YYYY-MM/YYYY-MM-DD/...`.
11. Click Output location → picker opens → chosen path persists across app restart.
12. Errored row checked → invoice-only included in Per Container output.

If any criterion fails, fix and re-test before proceeding.

- [ ] **Step 2: Bump VERSION**

Edit `desktop/VERSION` → change to `2.52.0`.

```bash
git add desktop/VERSION
git commit -m "chore: bump version to 2.52.0 (M4 merging + done)"
```

- [ ] **Step 3: Run the rebuild**

Per project preference (use `runbuild.bat`, the non-interactive sibling of `build-all.bat`):

```bash
cd desktop && cmd.exe /c runbuild.bat
```

Expected output: `===AGENT_BUILD_OK===` followed by `===ELECTRON_BUILD_DONE===`. The installer lands at `desktop/dist/NGL-Accounting-Setup-2.52.0.exe` and `latest.yml` at `desktop/dist/latest.yml`.

If the build fails, fix the error and retry. Don't push a half-built release.

- [ ] **Step 4: Push the commits**

```bash
git push origin main
```

- [ ] **Step 5: Publish the GitHub release**

```bash
gh release create v2.52.0 \
  "desktop/dist/NGL-Accounting-Setup-2.52.0.exe" \
  "desktop/dist/latest.yml" \
  --title "v2.52.0 — Merge Tool v2 M4 (Merging + Done)" \
  --notes "$(cat <<'EOF'
## Merge Tool v2 — M4 (Merging + Done)

The Merge screen replaces the placeholder Merging/Done states. Pick from 6 merge formats organised in 2 groups, accumulate completed merges as you go, and choose where outputs land on disk.

### What's new
- **6 merge modes** in 2 groups — Per Container (× 3 content variants) + Single Combined (× 3 content variants).
- **Output location picker** — choose any folder (default Desktop). Path persists across sessions.
- **Structured output folders** — `Merge Outputs/[Mode]/YYYY-MM/YYYY-MM-DD/...` for clean organisation across batches.
- **Same-day re-runs overwrite** — repeating a mode on the same day silently replaces the previous run's files.
- **Errored rows can now be merged** — checkbox is interactive; checking an errored row produces an invoice-only entry.
- **Pre-merge confirmation popup** — fires only when at least one row is unchecked.
- **Back to Ready** preserves the row table AND the completed merge cards across the round-trip.

### Spec
docs/superpowers/specs/2026-05-07-merge-tool-v2-m4-merging-done-design.md
EOF
)"
```

- [ ] **Step 6: Verify the release**

Open the [Releases page](https://github.com/joerohh/NGL-Accounting-Service/releases) (or whatever the actual repo URL is — `gh release view v2.52.0` prints it). Confirm both `.exe` and `latest.yml` are attached. The auto-updater inside the running app should detect the new version and offer to update on next launch.

---

## Summary

12 tasks across 3 layers:
- Agent (Tasks 1-2): nested-path save endpoint + folder picker + open-path.
- Frontend infrastructure (Tasks 3-6): agent-client extensions, output module, engine module, state extension.
- UI + flow (Tasks 7-11): popup, Merge screen, mode-click handlers, errored checkbox, navigation.
- Ship (Task 12): QA + version + build + push + release.

Each task is self-contained, includes the exact code, and ends with a focused commit. Total estimated effort: 4-6 hours of coding plus 1 hour of QA + release.
