# Merge Tool — Warehouse Invoice Support — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add warehouse as a third invoice type to the merge tool, alongside import/export. Bundle 4 cross-cutting fixes (INV#-only filenames everywhere, "Per Container" → "Per Invoice" rename, universal bulk-drop with multi-row attach, 7-day auto-cleanup + Settings · Storage card).

**Architecture:** Warehouse routing keys off INV# position-2 `W`. Agent fetches every QBO attachment, converts xlsx → PDF via `pywin32` + Excel COM (one Excel process per fetch job), no TMS fallback. Web app extends merge tool with warehouse filter tab, em-dash cells for irrelevant columns, universal side-panel customization, and a new Settings · Storage card backed by two new agent endpoints.

**Tech Stack:** Vanilla JS (ES modules, no build step), Python 3 + FastAPI agent on 127.0.0.1:8787, pdf-lib (CDN) for merge, pywin32 + Excel COM for xlsx conversion, pytest + pytest-asyncio for agent tests, Electron packaging via PyInstaller + electron-builder.

**Spec:** `docs/superpowers/specs/2026-05-18-merge-tool-warehouse-invoices-design.md`
**Mockups:**
- `app/mockups/v2.72-warehouse-review-mockup.html`
- `app/mockups/v2.72-settings-storage-mockup.html`

---

## Phase 1 — Agent foundation

The agent needs new dependencies, a new Excel converter, retry hardening, and storage tooling before the fetch job can be extended for warehouse. Order matters: each later task depends on earlier ones.

---

### Task 1: Add pywin32 dependency and PyInstaller hidden imports

**Files:**
- Modify: `agent/requirements.txt`
- Modify: `desktop/ngl-agent.spec`

- [ ] **Step 1: Add pywin32 to requirements**

Open `agent/requirements.txt`. Add a new line (alphabetical position is fine):

```
pywin32>=306
```

- [ ] **Step 2: Install in dev environment**

Run:
```bash
cd agent && pip install -r requirements.txt
```

Expected: pywin32 installs cleanly (or "already installed" if user did the POC).

- [ ] **Step 3: Add hidden imports to PyInstaller spec**

Open `desktop/ngl-agent.spec`. Find the `hiddenimports=[...]` list inside the `Analysis(...)` call and add these five entries:

```python
'win32com',
'win32com.client',
'pythoncom',
'win32api',
'pywintypes',
```

- [ ] **Step 4: Verify spec file parses**

Run:
```bash
cd desktop && python -c "exec(open('ngl-agent.spec').read())"
```

Expected: no SyntaxError. (The spec uses PyInstaller-specific globals so it won't execute fully outside PyInstaller — only checking parse.)

- [ ] **Step 5: Commit**

```bash
git add agent/requirements.txt desktop/ngl-agent.spec
git commit -m "deps(agent): add pywin32 for Excel COM conversion"
```

---

### Task 2: Add STORAGE_RETAIN_DAYS to config.py

**Files:**
- Modify: `agent/config.py:35` (next to existing `BACKUP_RETAIN_DAYS`)

- [ ] **Step 1: Add the constant**

In `agent/config.py`, immediately after the `BACKUP_RETAIN_DAYS = 30` line, add:

```python
STORAGE_RETAIN_DAYS = 7  # cleanup threshold for Merge Outputs + agent downloads
```

- [ ] **Step 2: Verify import works**

Run:
```bash
cd agent && python -c "from config import STORAGE_RETAIN_DAYS; print(STORAGE_RETAIN_DAYS)"
```

Expected output: `7`

- [ ] **Step 3: Commit**

```bash
git add agent/config.py
git commit -m "config(agent): add STORAGE_RETAIN_DAYS=7"
```

---

### Task 3: Create Excel converter module

**Files:**
- Create: `agent/services/excel_converter.py`

This module wraps Excel COM in an `ExcelSession` async context manager. One Excel process is shared across all xlsx files in a fetch job. Per-file conversion runs in `asyncio.to_thread` so the event loop stays responsive.

- [ ] **Step 1: Write the file**

Create `agent/services/excel_converter.py` with this content:

```python
"""Excel → PDF converter using pywin32 + Excel COM.

One Excel process per fetch job (warm-up cost ~8s, ~1-2s per file after).
COM is sync and blocks the event loop, so callers must run convert_xlsx_to_pdf()
inside asyncio.to_thread() — ExcelSession owns the dispatch lifecycle.

Page-setup rules from the POC (scratch/excel_to_pdf_test.py):
- Landscape if cols >= rows OR cols >= 8; else portrait
- Zoom=False, FitToPagesWide=1, FitToPagesTall=False → no column cut-off
- AutomationSecurity=3 (force-disable) → no macro prompts
- UpdateLinks=0 → no external link prompts
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("ngl.excel_converter")

# Per-file timeout to catch runaway conversions (corrupt files, infinite recalc loops)
PER_FILE_TIMEOUT_S = 30.0

# Excel COM constants
_xlPortrait = 1
_xlLandscape = 2
_xlTypePDF = 0
_msoAutomationSecurityForceDisable = 3


@dataclass
class ConvertResult:
    ok: bool
    pages: int = 0
    size_bytes: int = 0
    error: Optional[str] = None


def _check_excel_available() -> bool:
    """One-shot probe at agent startup. Returns True if Excel COM works."""
    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        try:
            excel = win32com.client.DispatchEx("Excel.Application")
            excel.Quit()
            return True
        finally:
            pythoncom.CoUninitialize()
    except Exception as e:
        logger.warning("Excel COM unavailable: %s", e)
        return False


# Module-level flag set by main.py on agent startup
EXCEL_AVAILABLE: bool = False


def set_excel_available(value: bool) -> None:
    """Called once by main.py lifespan after _check_excel_available()."""
    global EXCEL_AVAILABLE
    EXCEL_AVAILABLE = value


class ExcelSession:
    """Async context manager that owns a single Excel COM process.

    Usage:
        async with ExcelSession() as session:
            result = await session.convert(Path("a.xlsx"), Path("a.pdf"))
            result = await session.convert(Path("b.xlsx"), Path("b.pdf"))
    """

    def __init__(self) -> None:
        self._excel = None
        self._pythoncom = None

    async def __aenter__(self) -> "ExcelSession":
        await asyncio.to_thread(self._start_sync)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await asyncio.to_thread(self._stop_sync)

    def _start_sync(self) -> None:
        import pythoncom
        import win32com.client
        self._pythoncom = pythoncom
        pythoncom.CoInitialize()
        self._excel = win32com.client.DispatchEx("Excel.Application")
        self._excel.Visible = False
        self._excel.DisplayAlerts = False
        self._excel.AutomationSecurity = _msoAutomationSecurityForceDisable
        self._excel.AskToUpdateLinks = False

    def _stop_sync(self) -> None:
        try:
            if self._excel is not None:
                try:
                    self._excel.Quit()
                except Exception as e:
                    logger.warning("Excel Quit() raised: %s", e)
                self._excel = None
        finally:
            if self._pythoncom is not None:
                try:
                    self._pythoncom.CoUninitialize()
                except Exception:
                    pass
                self._pythoncom = None

    async def convert(self, src: Path, out: Path) -> ConvertResult:
        """Convert one xlsx file. Wraps the sync converter in a timeout."""
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._convert_sync, src, out),
                timeout=PER_FILE_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.error("Excel conversion timed out for %s", src.name)
            return ConvertResult(ok=False, error="conversion_timeout")

    def _convert_sync(self, src: Path, out: Path) -> ConvertResult:
        if self._excel is None:
            return ConvertResult(ok=False, error="session_not_started")

        wb = None
        try:
            wb = self._excel.Workbooks.Open(
                str(src.resolve()),
                UpdateLinks=0,
                ReadOnly=True,
                Password="",
                WriteResPassword="",
                IgnoreReadOnlyRecommended=True,
            )
        except Exception as e:
            msg = str(e).lower()
            if "password" in msg:
                return ConvertResult(ok=False, error="conversion_failed: password_protected")
            return ConvertResult(ok=False, error=f"conversion_failed: open_error: {e}")

        try:
            # Apply page setup to every sheet
            try:
                sheet_count = wb.Worksheets.Count
            except Exception:
                sheet_count = 0

            if sheet_count == 0:
                return ConvertResult(ok=False, error="conversion_failed: empty_workbook")

            applied = 0
            for i in range(1, sheet_count + 1):
                try:
                    ws = wb.Worksheets(i)
                    self._apply_page_setup(ws)
                    applied += 1
                except Exception as e:
                    logger.warning("Page-setup failed on sheet %d of %s: %s", i, src.name, e)

            if applied == 0:
                return ConvertResult(ok=False, error="conversion_failed: no_usable_sheets")

            try:
                wb.ExportAsFixedFormat(Type=_xlTypePDF, Filename=str(out.resolve()))
            except Exception as e:
                return ConvertResult(ok=False, error=f"conversion_failed: export_error: {e}")

        finally:
            try:
                wb.Close(SaveChanges=False)
            except Exception:
                pass

        if not out.exists():
            return ConvertResult(ok=False, error="conversion_failed: no_output_file")

        size = out.stat().st_size
        pages = _count_pdf_pages(out)
        return ConvertResult(ok=True, pages=pages, size_bytes=size)

    def _apply_page_setup(self, ws) -> None:
        try:
            used = ws.UsedRange
            rows = used.Rows.Count
            cols = used.Columns.Count
        except Exception:
            rows, cols = 0, 0

        ps = ws.PageSetup
        landscape = (cols >= rows) or (cols >= 8)
        ps.Orientation = _xlLandscape if landscape else _xlPortrait
        ps.Zoom = False
        ps.FitToPagesWide = 1
        ps.FitToPagesTall = False
        ps.LeftMargin = self._excel.InchesToPoints(0.5)
        ps.RightMargin = self._excel.InchesToPoints(0.5)
        ps.TopMargin = self._excel.InchesToPoints(0.5)
        ps.BottomMargin = self._excel.InchesToPoints(0.5)
        ps.CenterHorizontally = True

        if cols >= 2 and rows >= 3:
            try:
                if not ps.PrintTitleRows:
                    ps.PrintTitleRows = "$1:$2"
            except Exception:
                pass


def _count_pdf_pages(pdf_path: Path) -> int:
    """Cheap PDF page count by counting /Type /Page objects.

    Not a full PDF parser — good enough for telemetry."""
    try:
        data = pdf_path.read_bytes()
        return data.count(b"/Type /Page") - data.count(b"/Type /Pages")
    except Exception:
        return 0


async def convert_xlsx_to_pdf(src: Path, out: Path) -> ConvertResult:
    """One-shot conversion. Spawns + tears down its own session.

    For batches, prefer ExcelSession directly to amortize the ~8s start-up."""
    async with ExcelSession() as session:
        return await session.convert(src, out)


def kill_orphan_excel_processes() -> int:
    """Kill any leftover EXCEL.EXE from a previous run that crashed.

    Returns the number of processes killed. Safe to call at startup."""
    killed = 0
    try:
        import subprocess
        result = subprocess.run(
            ["taskkill", "/F", "/IM", "EXCEL.EXE"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            # taskkill prints one "SUCCESS:" line per process killed
            killed = result.stdout.count("SUCCESS:")
    except Exception as e:
        logger.debug("Orphan EXCEL.EXE cleanup skipped: %s", e)
    return killed
```

- [ ] **Step 2: Smoke-test the converter against the POC file**

Run:
```bash
cd agent && python -c "
import asyncio
from pathlib import Path
from services.excel_converter import convert_xlsx_to_pdf

src = Path('../app/assets/images/APRIL CHARGE 2026.xlsx')
out = Path('../scratch/_smoke.pdf')
out.unlink(missing_ok=True)

result = asyncio.run(convert_xlsx_to_pdf(src, out))
print(result)
print('out.exists:', out.exists())
"
```

Expected: `ConvertResult(ok=True, pages=20, size_bytes=...)` and `out.exists: True`. (Numbers should roughly match the POC: 20 pages, ~800 KB.)

- [ ] **Step 3: Verify the smoke output is a valid PDF**

Open `scratch/_smoke.pdf` in any PDF viewer. Expect to see all 20 tabs converted with no column cut-off, landscape orientation on wide sheets.

- [ ] **Step 4: Delete the smoke artifact**

```bash
rm "scratch/_smoke.pdf"
```

- [ ] **Step 5: Commit**

```bash
git add agent/services/excel_converter.py
git commit -m "feat(agent): add Excel COM converter module"
```

---

### Task 4: Add retry to download_attachment

**Files:**
- Modify: `agent/services/qbo_api/attachments.py:102-146`

Mirror the `get_invoice_link` retry pattern already in `invoices.py:48-118`. Retry only `_TRANSIENT_NETWORK_ERRORS`; HTTP 4xx/5xx and decode errors do not retry.

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_qbo_attachments_retry.py`:

```python
"""Retry behavior for QBO download_attachment."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.qbo_api.attachments import QBOAttachmentsMixin


class _Client(QBOAttachmentsMixin):
    """Minimal host so we can call the mixin method in tests."""
    def __init__(self):
        self._base_url = "https://example.invalid"
        self._realm_id = "0"
        self._token_manager = MagicMock()
        self._token_manager.get_access_token = AsyncMock(return_value="fake-token")


@pytest.mark.asyncio
async def test_download_attachment_retries_on_transient_error(tmp_path: Path):
    """First attempt fails with ConnectError; second succeeds."""
    client = _Client()
    calls = {"n": 0}

    real_get_resp = MagicMock()
    real_get_resp.status_code = 200
    real_get_resp.content = b"%PDF-1.4 fake-bytes"

    class _FakeHttpxClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("getaddrinfo failed")
            return real_get_resp

    with patch("httpx.AsyncClient", _FakeHttpxClient):
        result = await client.download_attachment(
            attachable_id="123",
            filename="x.pdf",
            download_dir=tmp_path,
            temp_download_uri="https://example.invalid/file",
        )

    assert result is not None
    assert result.read_bytes() == b"%PDF-1.4 fake-bytes"
    assert calls["n"] == 2  # one fail + one success


@pytest.mark.asyncio
async def test_download_attachment_gives_up_after_three_attempts(tmp_path: Path):
    """All three attempts fail with ConnectError → returns None, no exception."""
    client = _Client()
    calls = {"n": 0}

    class _FakeHttpxClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url, **kwargs):
            calls["n"] += 1
            raise httpx.ConnectError("getaddrinfo failed")

    with patch("httpx.AsyncClient", _FakeHttpxClient):
        result = await client.download_attachment(
            attachable_id="123",
            filename="x.pdf",
            download_dir=tmp_path,
            temp_download_uri="https://example.invalid/file",
        )

    assert result is None
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_download_attachment_does_not_retry_on_404(tmp_path: Path):
    """Non-200 HTTP response (e.g. 404) is permanent — return None immediately."""
    client = _Client()
    calls = {"n": 0}

    resp_404 = MagicMock()
    resp_404.status_code = 404
    resp_404.text = "not found"

    class _FakeHttpxClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url, **kwargs):
            calls["n"] += 1
            return resp_404

    with patch("httpx.AsyncClient", _FakeHttpxClient):
        result = await client.download_attachment(
            attachable_id="123",
            filename="x.pdf",
            download_dir=tmp_path,
            temp_download_uri="https://example.invalid/file",
        )

    assert result is None
    assert calls["n"] == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
cd agent && python -m pytest tests/test_qbo_attachments_retry.py -v
```

Expected: 3 failures (no retry logic yet).

- [ ] **Step 3: Implement retry in download_attachment**

Open `agent/services/qbo_api/attachments.py`. Add at the top of the file, after the existing imports:

```python
import asyncio

# Transient network errors that may resolve on retry (mirrors invoices.py).
_TRANSIENT_NETWORK_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
)
_DOWNLOAD_RETRY_ATTEMPTS = 3
_DOWNLOAD_RETRY_BACKOFF_SECONDS = (1.0, 3.0)  # before attempts 2 and 3
```

Then replace the entire `download_attachment` method (currently lines 102-146) with this:

```python
    async def download_attachment(self, attachable_id: str,
                                   filename: str,
                                   download_dir: Path,
                                   temp_download_uri: str = None) -> Optional[Path]:
        """Download an attachment file. Returns the saved file path or None.

        Retries up to 3 times on transient network errors (DNS blip, brief
        connection refusal, read timeout). HTTP 4xx/5xx and decode errors
        are treated as permanent and do not retry. Mirrors the retry pattern
        in services.qbo_api.invoices.get_invoice_link.
        """
        last_error: Optional[Exception] = None
        for attempt in range(_DOWNLOAD_RETRY_ATTEMPTS):
            try:
                return await self._download_attachment_once(
                    attachable_id, filename, download_dir, temp_download_uri,
                )
            except _TRANSIENT_NETWORK_ERRORS as e:
                last_error = e
                if attempt < _DOWNLOAD_RETRY_ATTEMPTS - 1:
                    backoff = _DOWNLOAD_RETRY_BACKOFF_SECONDS[attempt]
                    logger.warning(
                        "download_attachment attempt %d/%d failed for %s (%s) — retrying in %.1fs",
                        attempt + 1, _DOWNLOAD_RETRY_ATTEMPTS,
                        filename, type(e).__name__, backoff,
                    )
                    await asyncio.sleep(backoff)
                    continue
        logger.error(
            "download_attachment FAILED for %s after %d attempts. Last error: %s",
            filename, _DOWNLOAD_RETRY_ATTEMPTS, last_error,
        )
        return None

    async def _download_attachment_once(self, attachable_id: str,
                                         filename: str,
                                         download_dir: Path,
                                         temp_download_uri: str = None) -> Optional[Path]:
        """Single download attempt. Raises transient errors so the retry wrapper sees them."""
        download_url = temp_download_uri
        if not download_url:
            token = await self._token_manager.get_access_token()
            if not token:
                logger.error("No valid access token for attachment download")
                return None

            url = f"{self._base_url}/v3/company/{self._realm_id}/download/{attachable_id}"
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=30,
                )
            if resp.status_code != 200:
                logger.error("Attachment download failed: %d — %s", resp.status_code, resp.text)
                return None
            download_url = resp.content.decode("utf-8").strip()

        async with httpx.AsyncClient(follow_redirects=True) as client:
            file_resp = await client.get(download_url, timeout=30)
        if file_resp.status_code != 200:
            logger.error("Attachment file fetch failed: %d", file_resp.status_code)
            return None

        download_dir.mkdir(parents=True, exist_ok=True)
        file_path = download_dir / filename
        file_path.write_bytes(file_resp.content)
        logger.info("Downloaded attachment: %s (%d bytes)", filename, len(file_resp.content))
        return file_path
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd agent && python -m pytest tests/test_qbo_attachments_retry.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/services/qbo_api/attachments.py agent/tests/test_qbo_attachments_retry.py
git commit -m "feat(agent): add 3-attempt retry to download_attachment

Same pattern as get_invoice_link. Retries only transient network errors
(ConnectError, ConnectTimeout, ReadTimeout, RemoteProtocolError) with
1s and 3s backoffs. HTTP 4xx/5xx remain non-retryable.

Tests: tests/test_qbo_attachments_retry.py"
```

---

### Task 5: Create storage management module

**Files:**
- Create: `agent/services/storage.py`

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_storage.py`:

```python
"""Storage sweep + info functions."""

import time
from pathlib import Path

import pytest

from services.storage import sweep_old_outputs, sweep_old_downloads, get_storage_info


def _mtime_age_days(path: Path, days: int) -> None:
    """Backdate a path's mtime by N days."""
    ts = time.time() - (days * 86400)
    os.utime(path, (ts, ts))


# os import for the helper above
import os


def _make_dated_dir(root: Path, mode: str, year_month: str, day: str, content: bytes = b"x") -> Path:
    """Make root/Merge Outputs/<mode>/<year_month>/<day>/file.pdf with one file."""
    d = root / "Merge Outputs" / mode / year_month / day
    d.mkdir(parents=True, exist_ok=True)
    (d / "file.pdf").write_bytes(content)
    return d


def test_sweep_outputs_deletes_old_date_folders(tmp_path: Path):
    root = tmp_path
    fresh = _make_dated_dir(root, "Per Invoice", "2026-05", "2026-05-19")
    stale = _make_dated_dir(root, "Per Invoice", "2026-05", "2026-05-01")
    _mtime_age_days(stale, 14)
    _mtime_age_days(stale / "file.pdf", 14)

    result = sweep_old_outputs(root, retain_days=7)

    assert fresh.exists()
    assert not stale.exists()
    assert result.files_removed == 1
    assert result.bytes_freed > 0


def test_sweep_outputs_handles_multiple_modes(tmp_path: Path):
    root = tmp_path
    fresh = _make_dated_dir(root, "Per Invoice", "2026-05", "2026-05-19")
    stale1 = _make_dated_dir(root, "Per Container", "2026-04", "2026-04-10")
    stale2 = _make_dated_dir(root, "Combined PDF", "2026-04", "2026-04-15")
    for s in (stale1, stale2):
        _mtime_age_days(s, 14)
        _mtime_age_days(s / "file.pdf", 14)

    result = sweep_old_outputs(root, retain_days=7)

    assert fresh.exists()
    assert not stale1.exists()
    assert not stale2.exists()
    assert result.files_removed == 2


def test_sweep_outputs_does_not_touch_files_outside_merge_outputs(tmp_path: Path):
    root = tmp_path
    untouched = root / "some-other-file.pdf"
    untouched.write_bytes(b"hello")
    _mtime_age_days(untouched, 30)

    sweep_old_outputs(root, retain_days=7)

    assert untouched.exists()


def test_sweep_outputs_missing_root_is_noop(tmp_path: Path):
    """Sweeping a path that doesn't have Merge Outputs/ is fine."""
    root = tmp_path / "nope"
    result = sweep_old_outputs(root, retain_days=7)
    assert result.files_removed == 0


def test_sweep_downloads_deletes_old_job_dirs(tmp_path: Path):
    fresh = tmp_path / "abc123"
    fresh.mkdir()
    (fresh / "a.pdf").write_bytes(b"new")

    stale = tmp_path / "def456"
    stale.mkdir()
    (stale / "a.pdf").write_bytes(b"old")
    _mtime_age_days(stale, 14)
    _mtime_age_days(stale / "a.pdf", 14)

    result = sweep_old_downloads(tmp_path, retain_days=7)

    assert fresh.exists()
    assert not stale.exists()
    assert result.files_removed == 1


def test_get_storage_info_reports_totals(tmp_path: Path):
    output_root = tmp_path / "out"
    downloads_root = tmp_path / "dl"
    output_root.mkdir(); downloads_root.mkdir()

    _make_dated_dir(output_root, "Per Invoice", "2026-05", "2026-05-19", content=b"a" * 100)
    _make_dated_dir(output_root, "Per Invoice", "2026-05", "2026-05-18", content=b"a" * 50)
    (downloads_root / "job1").mkdir()
    (downloads_root / "job1" / "f.pdf").write_bytes(b"b" * 200)

    info = get_storage_info(output_root, downloads_root)

    assert info.output_size_bytes == 150
    assert info.output_file_count == 2
    assert info.downloads_size_bytes == 200
    assert info.downloads_batch_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd agent && python -m pytest tests/test_storage.py -v
```

Expected: ImportError or 7 failures (module doesn't exist yet).

- [ ] **Step 3: Implement the storage module**

Create `agent/services/storage.py`:

```python
"""Storage management — auto-cleanup of old merge outputs + agent temp downloads.

Two trees are tracked:
  1. <user-chosen output location>/Merge Outputs/<mode>/<YYYY-MM>/<YYYY-MM-DD>/
  2. agent/downloads/<job_id>/

Date subdirs older than STORAGE_RETAIN_DAYS are deleted entirely; files inside
the current week are never touched. The sweeps only descend into the two named
roots — anything else inside the user's chosen folder is left alone.
"""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("ngl.storage")


@dataclass
class SweepResult:
    files_removed: int = 0
    bytes_freed: int = 0
    folders_removed: int = 0


@dataclass
class StorageInfo:
    output_size_bytes: int = 0
    output_file_count: int = 0
    output_folder_count: int = 0
    downloads_size_bytes: int = 0
    downloads_batch_count: int = 0
    last_cleanup_ts: float = 0.0
    last_cleanup_freed_bytes: int = 0
    last_cleanup_files_removed: int = 0


def _dir_size(path: Path) -> tuple[int, int]:
    """Returns (total_bytes, file_count)."""
    total = 0
    count = 0
    if not path.exists():
        return 0, 0
    for entry in path.rglob("*"):
        if entry.is_file():
            try:
                total += entry.stat().st_size
                count += 1
            except OSError:
                pass
    return total, count


def _is_older_than(path: Path, threshold_ts: float) -> bool:
    try:
        return path.stat().st_mtime < threshold_ts
    except OSError:
        return False


def sweep_old_outputs(output_root: Path, retain_days: int) -> SweepResult:
    """Walk <output_root>/Merge Outputs/<mode>/<YYYY-MM>/<YYYY-MM-DD> and delete
    any date subdir older than retain_days.

    Skips entirely if Merge Outputs/ doesn't exist. Errors deleting individual
    folders are logged but don't abort the sweep.
    """
    result = SweepResult()
    base = output_root / "Merge Outputs"
    if not base.exists():
        return result

    threshold_ts = time.time() - (retain_days * 86400)

    for mode_dir in base.iterdir():
        if not mode_dir.is_dir():
            continue
        for month_dir in mode_dir.iterdir():
            if not month_dir.is_dir():
                continue
            for date_dir in month_dir.iterdir():
                if not date_dir.is_dir():
                    continue
                if _is_older_than(date_dir, threshold_ts):
                    size, count = _dir_size(date_dir)
                    try:
                        shutil.rmtree(date_dir)
                        result.bytes_freed += size
                        result.files_removed += count
                        result.folders_removed += 1
                        logger.info(
                            "Sweep removed %s (%d files, %d bytes)",
                            date_dir, count, size,
                        )
                    except OSError as e:
                        logger.warning("Failed to remove %s: %s", date_dir, e)
    return result


def sweep_old_downloads(downloads_root: Path, retain_days: int) -> SweepResult:
    """Walk <downloads_root>/<job_id>/ and delete any job dir older than retain_days."""
    result = SweepResult()
    if not downloads_root.exists():
        return result

    threshold_ts = time.time() - (retain_days * 86400)

    for job_dir in downloads_root.iterdir():
        if not job_dir.is_dir():
            continue
        if _is_older_than(job_dir, threshold_ts):
            size, count = _dir_size(job_dir)
            try:
                shutil.rmtree(job_dir)
                result.bytes_freed += size
                result.files_removed += count
                result.folders_removed += 1
                logger.info(
                    "Sweep removed download dir %s (%d files, %d bytes)",
                    job_dir, count, size,
                )
            except OSError as e:
                logger.warning("Failed to remove %s: %s", job_dir, e)
    return result


def get_storage_info(output_root: Path, downloads_root: Path,
                     last_cleanup_ts: float = 0.0,
                     last_cleanup_freed_bytes: int = 0,
                     last_cleanup_files_removed: int = 0) -> StorageInfo:
    """Compute current storage totals. Walks both trees, no caching."""
    info = StorageInfo()

    base = output_root / "Merge Outputs"
    if base.exists():
        for mode_dir in base.iterdir():
            if not mode_dir.is_dir():
                continue
            for month_dir in mode_dir.iterdir():
                if not month_dir.is_dir():
                    continue
                for date_dir in month_dir.iterdir():
                    if date_dir.is_dir():
                        info.output_folder_count += 1
        size, count = _dir_size(base)
        info.output_size_bytes = size
        info.output_file_count = count

    if downloads_root.exists():
        info.downloads_batch_count = sum(
            1 for p in downloads_root.iterdir() if p.is_dir()
        )
        size, _ = _dir_size(downloads_root)
        info.downloads_size_bytes = size

    info.last_cleanup_ts = last_cleanup_ts
    info.last_cleanup_freed_bytes = last_cleanup_freed_bytes
    info.last_cleanup_files_removed = last_cleanup_files_removed

    return info
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd agent && python -m pytest tests/test_storage.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/services/storage.py agent/tests/test_storage.py
git commit -m "feat(agent): add storage sweep + info module

sweep_old_outputs/sweep_old_downloads delete date-bucketed subdirs older
than retain_days. get_storage_info reports current totals for the
Settings > Storage card.

Tests: tests/test_storage.py (7 cases incl. safety guard that the sweep
only descends into Merge Outputs/ and the downloads root)."
```

---

### Task 6: Create storage router with HTTP endpoints

**Files:**
- Create: `agent/routers/storage.py`
- Modify: `agent/main.py` (register the router — see Task 7)

- [ ] **Step 1: Write the file**

Create `agent/routers/storage.py`:

```python
"""HTTP endpoints for the Settings · Storage card.

Endpoints:
  GET  /storage/info     → current size/count of output + downloads trees
  POST /storage/cleanup  → run the 7-day sweep immediately, return post-sweep info
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from config import DOWNLOADS_DIR, STORAGE_RETAIN_DAYS
from services.storage import (
    SweepResult,
    get_storage_info,
    sweep_old_downloads,
    sweep_old_outputs,
)

router = APIRouter(prefix="/storage", tags=["storage"])
logger = logging.getLogger("ngl.routers.storage")


# Process-level memo of the last cleanup result. Reset when the agent restarts.
_last_cleanup_ts: float = 0.0
_last_cleanup_freed_bytes: int = 0
_last_cleanup_files_removed: int = 0


def _resolve_output_root() -> Optional[Path]:
    """The user-chosen output location lives in the same JSON config the
    web app already writes to via /files endpoints. If unknown, return None
    and the endpoints report only the downloads-side info.

    The setting lives in APPDATA_DIR/data/settings.json under "output_location"
    when set; the frontend handles initial selection."""
    from config import APPDATA_DIR
    settings_file = APPDATA_DIR / "data" / "settings.json"
    if not settings_file.exists():
        return None
    try:
        import json
        with open(settings_file) as f:
            data = json.load(f)
        loc = data.get("output_location")
        return Path(loc) if loc else None
    except Exception as e:
        logger.warning("Could not read output_location from settings: %s", e)
        return None


@router.get("/info")
async def storage_info():
    """Return current storage totals for the Settings card."""
    output_root = _resolve_output_root() or DOWNLOADS_DIR.parent
    info = get_storage_info(
        output_root=output_root,
        downloads_root=DOWNLOADS_DIR,
        last_cleanup_ts=_last_cleanup_ts,
        last_cleanup_freed_bytes=_last_cleanup_freed_bytes,
        last_cleanup_files_removed=_last_cleanup_files_removed,
    )
    return {
        "output_root": str(output_root),
        "output_size_bytes": info.output_size_bytes,
        "output_file_count": info.output_file_count,
        "output_folder_count": info.output_folder_count,
        "downloads_size_bytes": info.downloads_size_bytes,
        "downloads_batch_count": info.downloads_batch_count,
        "last_cleanup_ts": info.last_cleanup_ts,
        "last_cleanup_freed_bytes": info.last_cleanup_freed_bytes,
        "last_cleanup_files_removed": info.last_cleanup_files_removed,
        "retain_days": STORAGE_RETAIN_DAYS,
    }


@router.post("/cleanup")
async def storage_cleanup():
    """Run the 7-day sweep on demand. Returns post-sweep info."""
    global _last_cleanup_ts, _last_cleanup_freed_bytes, _last_cleanup_files_removed

    output_root = _resolve_output_root()
    out_result = SweepResult()
    if output_root and output_root.exists():
        out_result = sweep_old_outputs(output_root, STORAGE_RETAIN_DAYS)

    dl_result = sweep_old_downloads(DOWNLOADS_DIR, STORAGE_RETAIN_DAYS)

    _last_cleanup_ts = time.time()
    _last_cleanup_freed_bytes = out_result.bytes_freed + dl_result.bytes_freed
    _last_cleanup_files_removed = out_result.files_removed + dl_result.files_removed

    return await storage_info()


def record_startup_sweep(out_result: SweepResult, dl_result: SweepResult) -> None:
    """Called by main.py lifespan after the startup sweep so /storage/info
    can report 'last cleanup ran at startup'."""
    global _last_cleanup_ts, _last_cleanup_freed_bytes, _last_cleanup_files_removed
    _last_cleanup_ts = time.time()
    _last_cleanup_freed_bytes = out_result.bytes_freed + dl_result.bytes_freed
    _last_cleanup_files_removed = out_result.files_removed + dl_result.files_removed
```

- [ ] **Step 2: Smoke-test the router parses**

Run:
```bash
cd agent && python -c "from routers.storage import router; print(len(router.routes), 'routes')"
```

Expected: `2 routes`

- [ ] **Step 3: Commit**

```bash
git add agent/routers/storage.py
git commit -m "feat(agent): add /storage/info and /storage/cleanup endpoints"
```

---

### Task 7: Wire startup hooks + storage router into main.py

**Files:**
- Modify: `agent/main.py:24` (router import)
- Modify: `agent/main.py:266-269` (lifespan startup section)
- Modify: `agent/main.py` (register router via `app.include_router(...)`)

- [ ] **Step 1: Add the import for the storage router**

Find `from routers import auth, jobs, files, qbo, customers, audit, tms, settings, chassis, retry` (currently line 24) and change it to:

```python
from routers import auth, jobs, files, qbo, customers, audit, tms, settings, chassis, retry, storage
```

- [ ] **Step 2: Add Excel + storage imports near the top**

After the existing `from config import (...)` block (ends around line 23), add:

```python
from services.excel_converter import (
    _check_excel_available, set_excel_available, kill_orphan_excel_processes,
)
from services.storage import sweep_old_outputs, sweep_old_downloads
from routers.storage import record_startup_sweep
```

- [ ] **Step 3: Wire Excel check + storage sweep into lifespan**

Find the existing housekeeping block:

```python
    # Housekeeping — clean old debug files, daily backup
    from utils import cleanup_old_debug_files, backup_data_files
    cleanup_old_debug_files(DEBUG_DIR)
    backup_data_files(DATA_DIR, BACKUP_DIR, BACKUP_RETAIN_DAYS)
```

Add immediately after it:

```python
    # Storage cleanup — 7-day sweep of Merge Outputs + agent downloads
    from config import STORAGE_RETAIN_DAYS, DOWNLOADS_DIR
    from routers.storage import _resolve_output_root
    _output_root = _resolve_output_root()
    if _output_root and _output_root.exists():
        _out_sweep = sweep_old_outputs(_output_root, STORAGE_RETAIN_DAYS)
        logger.info(
            "Storage sweep (outputs): removed %d files, freed %d bytes",
            _out_sweep.files_removed, _out_sweep.bytes_freed,
        )
    else:
        from services.storage import SweepResult
        _out_sweep = SweepResult()
    _dl_sweep = sweep_old_downloads(DOWNLOADS_DIR, STORAGE_RETAIN_DAYS)
    logger.info(
        "Storage sweep (downloads): removed %d files, freed %d bytes",
        _dl_sweep.files_removed, _dl_sweep.bytes_freed,
    )
    record_startup_sweep(_out_sweep, _dl_sweep)

    # Excel COM probe — sets module flag for the fetch job to check
    _killed = kill_orphan_excel_processes()
    if _killed:
        logger.info("Cleaned up %d orphan EXCEL.EXE processes", _killed)
    _excel_ok = _check_excel_available()
    set_excel_available(_excel_ok)
    logger.info("Excel converter: %s", "ready" if _excel_ok else "missing")
```

- [ ] **Step 4: Register the router with the FastAPI app**

Find the block of `app.include_router(...)` calls (search the file for `include_router`). Add this near the other registrations:

```python
app.include_router(storage.router)
```

- [ ] **Step 5: Add excel_converter status to /health**

Find the `/health` endpoint definition (search for `@app.get("/health")`). In its return dict, add:

```python
        "excel_converter": "ready" if EXCEL_AVAILABLE else "missing",
```

…and at the top of the same handler (or via a module-level import near other imports), add:

```python
from services.excel_converter import EXCEL_AVAILABLE
```

**Important:** read this value live each call — Python imports it as a name reference at module-load time, but because we mutate `set_excel_available()` before the first request, the import-time value is correct. To be safe and read live, do:

```python
import services.excel_converter as _excel
...
        "excel_converter": "ready" if _excel.EXCEL_AVAILABLE else "missing",
```

- [ ] **Step 6: Smoke-test the agent starts**

Run:
```bash
cd agent && python main.py &
sleep 5
curl -s http://127.0.0.1:8787/health | python -m json.tool
kill %1
```

Expected: JSON includes `"excel_converter": "ready"` (since you have Excel installed). Logs show "Storage sweep (...)" and "Excel converter: ready" lines.

- [ ] **Step 7: Smoke-test the storage endpoints**

Run:
```bash
cd agent && python main.py &
sleep 5
curl -s http://127.0.0.1:8787/storage/info | python -m json.tool
curl -s -X POST http://127.0.0.1:8787/storage/cleanup | python -m json.tool
kill %1
```

Expected: both return JSON with the storage info shape.

- [ ] **Step 8: Commit**

```bash
git add agent/main.py
git commit -m "feat(agent): wire storage sweep + Excel COM probe into startup

- 7-day sweep of Merge Outputs/ + agent/downloads/ at agent startup
- Excel availability probe (DispatchEx Excel.Application) sets module flag
- Orphan EXCEL.EXE killer runs first to clean up from any prior crash
- /health now includes excel_converter: ready|missing
- /storage/info + /storage/cleanup endpoints registered"
```

---

### Task 8: Add warehouse branch to fetch_job.py

**Files:**
- Modify: `agent/services/job_manager/fetch_job.py`

The fetch job currently runs two paths: an invoice fetch + a POD/BL/POL fetch with TMS fallback. Add a third path for warehouse rows that lists every QBO attachment, converts xlsx via `ExcelSession`, and skips the TMS fallback entirely.

- [ ] **Step 1: Add routing helper at the top of the file**

In `agent/services/job_manager/fetch_job.py`, after the existing imports, add:

```python
def _is_warehouse_row(invoice_number: str) -> bool:
    """Mirror of routingDecisionFor() — warehouse = INV# position-2 is 'W'."""
    if not invoice_number or len(invoice_number) < 2:
        return False
    return invoice_number[1].upper() == "W"
```

- [ ] **Step 2: Add the warehouse fetch branch**

Find the existing per-container loop in the fetch job (the function that fetches invoice + POD per container — currently around the `if want_pod:` block at line 230). Before the existing import-or-export logic, add a warehouse branch:

```python
        # Warehouse rows: fetch all QBO attachments, convert xlsx → PDF.
        # No TMS fallback, no safety cascade — warehouse is QBO-only.
        if _is_warehouse_row(container.invoice_number):
            await self._handle_warehouse_attachments(job, container, invoice_id, result)
            continue
```

(Adjust the `continue` target to whatever loop you're in. If the surrounding code is sequential rather than a loop, replace `continue` with the structural skip that prevents the import/export branch from running.)

- [ ] **Step 3: Implement _handle_warehouse_attachments**

Add this method to the `FetchJobMixin` class (or whichever class owns the fetch loop). Insert it adjacent to `_tms_pod_fallback`:

```python
    async def _handle_warehouse_attachments(
        self, job, container, invoice_id, result
    ) -> None:
        """Fetch every QBO attachment for a warehouse invoice. Convert any
        xlsx files to PDF. No TMS fallback, no safety cascade — warehouse is
        QBO-only.

        Populates result.warehouse_attachments and result.warehouse_failures."""
        from services.excel_converter import EXCEL_AVAILABLE, ExcelSession
        from services.qbo_api.attachments import classify_attachment

        api = self._qbo_api  # adjust to actual attribute name in the surrounding class

        attachments = await api.list_attachments(invoice_id)
        if not attachments:
            result.warehouse_attachments = []
            result.warehouse_failures = []
            await self._emit(job, "warehouse_empty", {
                "containerNumber": container.container_number,
                "invoiceNumber": container.invoice_number,
            })
            return

        # If Excel isn't installed, we can still pass PDF attachments through
        # but flag xlsx files as conversion-blocked.
        excel_session = None
        xlsx_present = any(
            att.get("fileName", "").lower().endswith((".xlsx", ".xls", ".xlsm"))
            for att in attachments
        )
        if xlsx_present and EXCEL_AVAILABLE:
            excel_session = ExcelSession()
            await excel_session.__aenter__()

        successes = []
        failures = []
        try:
            for att in attachments:
                fname = att.get("fileName", "unknown")
                lower = fname.lower()

                # Download the raw file first
                raw_path = await api.download_attachment(
                    att["id"], fname, job.download_dir,
                    temp_download_uri=att.get("tempDownloadUri"),
                )
                if not raw_path:
                    failures.append({"fileName": fname, "reason": "download_failed"})
                    continue

                if lower.endswith((".xlsx", ".xls", ".xlsm")):
                    if not EXCEL_AVAILABLE:
                        failures.append({"fileName": fname,
                                         "reason": "conversion_failed: excel_not_installed"})
                        continue
                    pdf_path = raw_path.with_suffix(".pdf")
                    conv = await excel_session.convert(raw_path, pdf_path)
                    if conv.ok:
                        successes.append({
                            "fileName": pdf_path.name,
                            "converted": True,
                            "pageCount": conv.pages,
                            "sizeBytes": conv.size_bytes,
                        })
                    else:
                        failures.append({"fileName": fname,
                                         "reason": conv.error or "conversion_failed"})
                elif lower.endswith(".pdf"):
                    successes.append({
                        "fileName": raw_path.name,
                        "converted": False,
                        "pageCount": 0,  # browser computes during merge
                        "sizeBytes": raw_path.stat().st_size,
                    })
                else:
                    failures.append({"fileName": fname, "reason": "unsupported_type"})
        finally:
            if excel_session is not None:
                await excel_session.__aexit__(None, None, None)

        result.warehouse_attachments = successes
        result.warehouse_failures = failures
        result.routing_type = "warehouse"
        result.pod_label = "Warehouse"

        await self._emit(job, "warehouse_fetched", {
            "containerNumber": container.container_number,
            "invoiceNumber": container.invoice_number,
            "attachmentCount": len(successes),
            "failureCount": len(failures),
        })
```

- [ ] **Step 4: Add the new result fields**

Find the dataclass / class definition for `FetchResult` (search for `class FetchResult`). Add these fields with defaults:

```python
    routing_type: str = ""             # 'import' | 'export' | 'warehouse'
    pod_label: str = ""                # 'POD' | 'BL' | 'POL' | 'Warehouse'
    warehouse_attachments: list = None # populated only for warehouse rows
    warehouse_failures: list = None
```

In its `__init__` (if it has one) or in the `__post_init__`, default the lists to empty:

```python
        if self.warehouse_attachments is None:
            self.warehouse_attachments = []
        if self.warehouse_failures is None:
            self.warehouse_failures = []
```

And in `to_dict()` add corresponding keys:

```python
            "routingType": self.routing_type,
            "podLabel": self.pod_label,
            "warehouseAttachments": self.warehouse_attachments,
            "warehouseFailures": self.warehouse_failures,
```

- [ ] **Step 5: Manual integration smoke test (requires real QBO + Excel)**

Start the agent, then in another shell:

```bash
curl -s -X POST http://127.0.0.1:8787/jobs/fetch \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ngl-local-dev-token" \
  -d '{
    "containers": [
      {"container_number": "", "invoice_number": "LW260515P01",
       "work_order_number": "", "customer_name": "INDIMEX GLOBAL LLC"}
    ],
    "docTypes": ["invoice", "pod"]
  }' | python -m json.tool
```

Watch the agent log for `warehouse_fetched`. Expected: a job-id is returned, after ~20s the job completes with one warehouse row populated (1 invoice + 1 converted xlsx).

- [ ] **Step 6: Commit**

```bash
git add agent/services/job_manager/fetch_job.py
git commit -m "feat(agent): warehouse fetch branch in fetch_job

Detects warehouse rows by INV# position-2='W' and routes them to a new
_handle_warehouse_attachments path. Lists every QBO attachment, runs
xlsx through Excel COM converter (one ExcelSession per fetch job),
passes PDFs through as-is. TMS fallback and POD safety cascade are
disabled for warehouse rows.

Adds routing_type, pod_label, warehouse_attachments, warehouse_failures
to FetchResult."
```

---

## Phase 2 — Web app shared utilities

These three tasks update pure helpers without touching UI. They unblock all subsequent UI work.

---

### Task 9: Extend routingDecisionFor() for warehouse

**Files:**
- Modify: `app/assets/js/shared/utils.js:126-162`

- [ ] **Step 1: Add the W letter to parseInvType**

In `app/assets/js/shared/utils.js`, change the body of `parseInvType` (lines 126-132) to:

```javascript
export function parseInvType(inv) {
  if (!inv || inv.length < 2) return null;
  const c = inv[1].toUpperCase();
  if (c === 'M') return 'import';
  if (c === 'E') return 'export';
  if (c === 'W') return 'warehouse';
  return null;
}
```

- [ ] **Step 2: Update routingDecisionFor for the new type**

Replace the body of `routingDecisionFor` (lines 152-162) with:

```javascript
export function routingDecisionFor(row) {
  const fromInv = parseInvType(row.invoiceNumber);
  if (fromInv === 'warehouse') {
    return { type: 'warehouse', expectedDoc: 'All QBO Docs' };
  }
  if (fromInv) {
    return { type: fromInv, expectedDoc: fromInv === 'import' ? 'POD' : 'BL/POL' };
  }
  // WO# letter fallback — does NOT route to warehouse (too risky for false positives)
  const fromWo = parseWoType(row.workOrderNumber);
  if (fromWo) {
    return { type: fromWo, expectedDoc: fromWo === 'import' ? 'POD' : 'BL/POL' };
  }
  return { type: 'unknown', expectedDoc: '?' };
}
```

- [ ] **Step 3: Verify in browser console**

Open `app/index.html` in a browser. Open DevTools console. Paste:

```javascript
const { routingDecisionFor } = await import('./assets/js/shared/utils.js');
console.log(routingDecisionFor({ invoiceNumber: 'LW260515P01' }));  // { type: 'warehouse', expectedDoc: 'All QBO Docs' }
console.log(routingDecisionFor({ invoiceNumber: 'LM2602170009' }));  // { type: 'import', expectedDoc: 'POD' }
console.log(routingDecisionFor({ invoiceNumber: 'LE2602170011' }));  // { type: 'export', expectedDoc: 'BL/POL' }
console.log(routingDecisionFor({ invoiceNumber: 'LX260515P01' }));   // { type: 'unknown', expectedDoc: '?' }
```

Expected: all 4 lines print the comment-indicated outputs.

- [ ] **Step 4: Commit**

```bash
git add app/assets/js/shared/utils.js
git commit -m "feat(merge): routingDecisionFor recognizes warehouse (W) INV# letter

INV# position-2='W' → { type: 'warehouse', expectedDoc: 'All QBO Docs' }.
WO# letter fallback does NOT route to warehouse — too risky for false
positives on legitimate WO#s containing W."
```

---

### Task 10: Rename mode metadata "Per Container" → "Per Invoice"

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2-output.js:17-62`

Mode `key` strings stay (`per-container`, `per-container-invoice`, `per-container-document`) for backward compat with saved state. Only `title`, `description`, and `subfolder` change.

- [ ] **Step 1: Update the MODES array**

In `app/assets/js/tools/merge/merge-v2-output.js`, replace the entire `MODES` array (lines 17-62) with:

```javascript
export const MODES = [
  // Per-invoice outputs (one PDF per invoice row)
  {
    key: 'per-container',
    group: 'per-container',
    title: 'Per Invoice',
    description: "One PDF per invoice. Each file contains that invoice and its supporting document combined.",
    subfolder: 'Per Invoice',
  },
  {
    key: 'per-container-invoice',
    group: 'per-container',
    title: 'Per Invoice — Invoice Only',
    description: 'One PDF per invoice, containing only the invoice itself.',
    subfolder: 'Per Invoice — Invoice Only',
  },
  {
    key: 'per-container-document',
    group: 'per-container',
    title: 'Per Invoice — Document Only',
    description: 'One PDF per invoice, containing only the supporting document — POD, BL, POL, IT, ITE, or warehouse attachments.',
    subfolder: 'Per Invoice — Document Only',
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
```

- [ ] **Step 2: Verify in browser**

Open `app/index.html`, go to the Merge Tool. Drop any Excel manifest to reach the Merge screen. Expected: the per-row mode cards now say "Per Invoice", "Per Invoice — Invoice Only", "Per Invoice — Document Only". Output folder names will update on the next merge.

- [ ] **Step 3: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2-output.js
git commit -m "ui(merge): rename Per Container mode/folder to Per Invoice

Tab labels and output subfolder names change. Mode key strings remain
(per-container, per-container-invoice, per-container-document) for
backward compat with saved state. Old runs under Per Container/ stay
where they are; the 7-day sweep empties them naturally."
```

---

### Task 11: Rewrite filename builder for INV#-only output

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2-output.js:96-125`

The new filename rule is: **INV# is the stem for all row types** (import, export, warehouse). Container number is dropped from filenames.

- [ ] **Step 1: Replace perContainerFilename**

In `app/assets/js/tools/merge/merge-v2-output.js`, replace `sanitizeFilenamePart` + `perContainerFilename` (lines 96-125) with:

```javascript
function sanitizeFilenamePart(s) {
  // Strip path separators and characters Windows rejects in filenames.
  return String(s || '').replace(/[\/\\:*?"<>|]/g, '_').trim();
}

export function perInvoiceFilename(row, modeKey) {
  // All row types name files by INV# only — container # is dropped from filenames.
  const inv = sanitizeFilenamePart(row.invoiceNumber);
  if (!inv) {
    throw new Error(`perInvoiceFilename: row ${row.rowNum || '?'} has no INV#`);
  }

  if (modeKey === 'per-container') {
    return `${inv}.pdf`;
  }
  if (modeKey === 'per-container-invoice') {
    return `${inv}_INV.pdf`;
  }
  if (modeKey === 'per-container-document') {
    // For warehouse rows, podLabel will be 'Warehouse' from the agent —
    // we want the short '_WH' suffix instead.
    const routingType = row.fetchResult?.routingType || row.routingType;
    const rawLabel = row.fetchResult?.podLabel;
    let docLabel;
    if (routingType === 'warehouse') {
      docLabel = 'WH';
    } else if (rawLabel && rawLabel !== '—') {
      docLabel = sanitizeFilenamePart(rawLabel);
    } else {
      docLabel = 'DOC';
    }
    return `${inv}_${docLabel}.pdf`;
  }
  throw new Error(`perInvoiceFilename: not a per-invoice mode: ${modeKey}`);
}

// Backward-compat alias — keep the old export name briefly so any caller that
// hasn't been updated yet still works. Remove in a follow-up cleanup.
export const perContainerFilename = perInvoiceFilename;
```

- [ ] **Step 2: Find all callers of perContainerFilename**

Run:
```bash
grep -rn "perContainerFilename" app/assets/js/
```

Expected output: at minimum the export in `merge-v2-output.js` and one or more imports in `merge-v2.js` / `merge-v2-engine.js`. Each caller can keep using `perContainerFilename` for now (the alias makes it work). Note them for follow-up.

- [ ] **Step 3: Update callers to use perInvoiceFilename**

For each caller listed in Step 2, change the import from `perContainerFilename` to `perInvoiceFilename` and update the call site. Example:

```javascript
// Before:
import { perContainerFilename } from './merge-v2-output.js';
const name = perContainerFilename(row, modeKey);

// After:
import { perInvoiceFilename } from './merge-v2-output.js';
const name = perInvoiceFilename(row, modeKey);
```

- [ ] **Step 4: Remove the backward-compat alias**

Once all callers are updated, delete the line `export const perContainerFilename = perInvoiceFilename;` from `merge-v2-output.js`.

- [ ] **Step 5: Verify in browser**

Open `app/index.html`, run a merge end-to-end with an import row whose INV# is `LM2602170009` and container `TEMU8809194`. Expected outputs:
- `LM2602170009.pdf` (per-invoice)
- `LM2602170009_INV.pdf` (invoice-only)
- `LM2602170009_POD.pdf` (document-only)

The container number `TEMU8809194` no longer appears in any filename.

- [ ] **Step 6: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2-output.js app/assets/js/tools/merge/merge-v2.js app/assets/js/tools/merge/merge-v2-engine.js
git commit -m "feat(merge): INV#-only filenames for all row types

perContainerFilename → perInvoiceFilename. Container number is dropped
from filenames for import/export as well as warehouse. Filename pattern:
  Per Invoice                  → <INV#>.pdf
  Per Invoice — Invoice Only   → <INV#>_INV.pdf
  Per Invoice — Document Only  → <INV#>_POD.pdf | _BL.pdf | _POL.pdf
                                  | _IT.pdf | _ITE.pdf | _WH.pdf

Warehouse rows use _WH (chosen over _DOCS because _DOCS reads too close
to the existing _DOC fallback)."
```

---

## Phase 3 — Merge tool UI

Five UI-facing tasks. Each verifies in the browser; no automated JS test framework exists.

---

### Task 12: Update manifest parser — container optional, INV# required

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js` (around line 258 where headers are parsed)

- [ ] **Step 1: Find the manifest header validation**

Run:
```bash
grep -n "containerKey\|containerNumber.*required\|missing container" app/assets/js/tools/merge/merge-v2.js
```

Find the block that currently rejects rows lacking a container number (typically a `.filter(...)` after the header parse, or a per-row check).

- [ ] **Step 2: Replace the container-required check**

Change the row-validity check so:
- INV# is required (existing behavior for warehouse rows; make it explicit for all rows).
- Container # is required **only** if the row would route to import/export (i.e., `routingDecisionFor(row).type !== 'warehouse'`).

Concretely, find the row-validation pass and replace it with:

```javascript
// Validate each row. INV# is required for ALL rows. Container # is required
// only for non-warehouse rows (warehouse INV#s have no real container).
const validRows = [];
const droppedRows = [];
for (const row of parsedRows) {
  const inv = (row.invoiceNumber || '').trim();
  if (!inv) {
    droppedRows.push({ row, reason: 'missing-inv' });
    continue;
  }
  const decision = routingDecisionFor(row);
  if (decision.type !== 'warehouse') {
    const cn = (row.containerNumber || '').trim();
    if (!cn) {
      droppedRows.push({ row, reason: 'missing-container' });
      continue;
    }
  }
  // Warehouse rows: strip whatever the exporter stuffed into container/WO
  if (decision.type === 'warehouse') {
    row.containerNumber = '';
    row.workOrderNumber = '';
  }
  row.routingType = decision.type;
  row.expectedDoc = decision.expectedDoc;
  validRows.push(row);
}
```

Make sure `routingDecisionFor` is imported at the top of the file:

```javascript
import { routingDecisionFor } from '../../shared/utils.js';
```

- [ ] **Step 3: Update the drop-zone copy**

Find the existing drop zone text (search for `"Needs a Container Number column"` — `merge-v2.js:357`). Change it to:

```html
<div class="drop-help">Needs an INV# column. Container # is optional (required only for import/export rows).</div>
```

- [ ] **Step 4: Verify in browser**

Open `app/index.html`, go to Merge Tool, drop an Excel that mixes import/export rows (with containers) and warehouse rows (no containers, INV#s like `LW260515P01`). Expected:
- Import/export rows show up in the table with their container.
- Warehouse rows show up with empty container — em-dash will be added in the next task.
- Any row missing INV# is reported in the dropped-rows summary.

- [ ] **Step 5: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge): manifest parser accepts warehouse rows (no container)

INV# is now required for all rows. Container # is required only for
import/export rows (routing decided by INV# letter). Warehouse rows
ignore container/WO entirely — any placeholder value the exporter
stuffed in those columns is dropped, never used."
```

---

### Task 13: Warehouse row display + filter tab + routing summary

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js` (filter tabs, routing summary band, row rendering — around lines 503, 583, 753)
- Modify: `app/assets/css/styles.css` (add warehouse badge, em-dash cell styles)

Visual reference: `app/mockups/v2.72-warehouse-review-mockup.html`.

- [ ] **Step 1: Add the CSS for warehouse type badge + em-dash cell**

In `app/assets/css/styles.css`, find the existing `.type-badge` block (or add a new `.type-badge.warehouse` rule):

```css
.type-badge.warehouse {
  background: #ffedd5;
  color: #9a3412;
  border: 1px solid #fed7aa;
}

.will-chip.whdocs {
  background: #ffedd5;
  color: #9a3412;
}

.na-cell {
  color: #cbd5e1;
  font-size: 1.05rem;
  font-weight: 400;
  user-select: none;
}
```

- [ ] **Step 2: Update the filter tabs to include Warehouse**

Find the filter-tab rendering in `merge-v2.js` (search for `filter-tabs` and the Import/Export/Unknown tab buttons). Add a Warehouse tab between Export and Unknown:

```javascript
const warehouseCount = v2State.rows.filter(r => r.routingType === 'warehouse').length;
// ...inside the tab-bar template:
`<button class="tab ${activeTab === 'warehouse' ? 'active' : ''}"
         onclick="window.v2SetFilterTab('warehouse')">
  Warehouse <span class="count">${warehouseCount}</span>
</button>`
```

Also update the filter-by-tab logic so `warehouse` filters to `r.routingType === 'warehouse'`.

- [ ] **Step 3: Update the routing summary band**

Find `routingSummaryBand()` in `merge-v2.js` (currently line 753). Update it to include warehouse:

```javascript
function routingSummaryBand() {
  const imports  = v2State.rows.filter(r => r.routingType === 'import').length;
  const exports_ = v2State.rows.filter(r => r.routingType === 'export').length;
  const warehouses = v2State.rows.filter(r => r.routingType === 'warehouse').length;
  const unknown  = v2State.rows.filter(r => r.routingType === 'unknown').length;
  return `
    <div class="routing-summary">
      <span class="label">Will fetch</span>
      <span class="group"><span class="chip import">POD</span> <strong>${imports}</strong> imports</span>
      <span class="group"><span class="chip export">BL/POL</span> <strong>${exports_}</strong> exports</span>
      <span class="group"><span class="chip warehouse">All QBO Docs</span> <strong>${warehouses}</strong> warehouse</span>
      ${unknown ? `<span class="group"><span class="chip unknown">?</span> <strong>${unknown}</strong> unknown</span>` : ''}
      <span class="hint">
        Decided by INV# letter (<strong class="new">M / E / W</strong>) · falls back to WO# letter when prefix is non-standard
      </span>
    </div>
  `;
}
```

- [ ] **Step 4: Update the row template to handle warehouse**

Find the table-row rendering (two places, lines 503 and 583 — review and merge screens). For each, gate the container/WO# cells on `routingType`:

```javascript
const isWarehouse = row.routingType === 'warehouse';
const containerCell = isWarehouse
  ? '<td><span class="na-cell">—</span></td>'
  : `<td><span class="mono">${escHtml(row.containerNumber)}</span></td>`;
const woCell = isWarehouse
  ? '<td><span class="na-cell">—</span></td>'
  : `<td><span class="mono">${escHtml(row.workOrderNumber || '—')}</span></td>`;

const badgeClass = `type-badge ${row.routingType}`;
const badgeText  = row.routingType === 'warehouse' ? 'Warehouse'
                 : row.routingType === 'import' ? 'Import'
                 : row.routingType === 'export' ? 'Export'
                 : 'Unknown';

const willChip = row.routingType === 'warehouse'
  ? '<span class="will-chip whdocs">All QBO Docs</span>'
  : row.routingType === 'import'
  ? '<span class="will-chip pod">POD</span>'
  : row.routingType === 'export'
  ? '<span class="will-chip bolpol">BL/POL</span>'
  : '<span class="will-chip">?</span>';
```

Use these variables in both row templates (review screen + merge screen).

- [ ] **Step 5: Verify in browser**

Open `app/index.html`, drop an Excel that mixes import + export + warehouse rows. Expected:
- Filter tabs show Warehouse with the correct count.
- Routing summary band shows the new `All QBO Docs` chip with the count.
- Warehouse rows display the orange `WAREHOUSE` badge, em-dashes in container/WO# cells, and `All QBO Docs` in the Will-fetch column.
- Clicking the Warehouse filter tab shows only warehouse rows.

- [ ] **Step 6: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js app/assets/css/styles.css
git commit -m "ui(merge): warehouse row display + filter tab + summary band

- New Warehouse filter tab between Export and Unknown
- Orange WAREHOUSE type badge, All QBO Docs will-chip
- Em-dash cells in Container # and WO # columns for warehouse rows
- Routing summary band hint copy updates to M/E/W"
```

---

### Task 14: Side-panel document customization (drag/remove/add/reset)

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js` (side panel render + interaction handlers)
- Modify: `app/assets/css/styles.css` (side panel styles)

Visual reference: `app/mockups/v2.72-warehouse-review-mockup.html` (both warehouse and import row examples).

Applies to **all** row types, not just warehouse. Each document in the side panel gets a drag handle, source tag, × remove button. Below the list: + Add document drop zone and Reset link.

- [ ] **Step 1: Add CSS for the side-panel attachment row + add-doc zone**

In `app/assets/css/styles.css`, add (use the mockup's CSS as the source — these are the relevant rules):

```css
.attachment-row {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 10px 8px 6px; background: #f8fafc;
  border: 1px solid #e2e8f0; border-radius: 7px;
  margin-bottom: 6px; font-size: 0.82rem;
  position: relative;
}
.attachment-row.fail   { background: #fef2f2; border-color: #fecaca; }
.attachment-row.manual { background: #eff6ff; border-color: #bfdbfe; }

.attachment-row .drag-handle {
  width: 14px; height: 18px;
  color: #cbd5e1; cursor: grab; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
}
.attachment-row:hover .drag-handle { color: #94a3b8; }

.attachment-row .name {
  flex: 1; min-width: 0;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  color: #0f172a; font-weight: 500;
}
.attachment-row .meta {
  color: #94a3b8; font-size: 0.74rem; white-space: nowrap;
}
.attachment-row .tag {
  font-size: 0.65rem; font-weight: 700;
  padding: 1px 5px; border-radius: 3px;
  letter-spacing: 0.04em; white-space: nowrap;
}
.attachment-row .tag.convert { background: #dbeafe; color: #1e40af; }
.attachment-row .tag.qbo     { background: #ecfccb; color: #4d7c0f; }
.attachment-row .tag.added   { background: #cffafe; color: #155e75; }
.attachment-row .tag.tms     { background: #fef3c7; color: #92400e; }

.attachment-row .remove-btn {
  width: 22px; height: 22px; flex-shrink: 0;
  border: 1px solid transparent; background: none;
  color: #cbd5e1; cursor: pointer; border-radius: 5px;
  display: inline-flex; align-items: center; justify-content: center;
}
.attachment-row .remove-btn:hover {
  color: #dc2626; background: #fee2e2; border-color: #fecaca;
}
.attachment-row .fail-reason {
  font-size: 0.74rem; color: #b91c1c; margin-top: 2px;
}

.add-doc-zone {
  display: flex; align-items: center; gap: 10px;
  width: 100%; margin-top: 10px;
  border: 1.5px dashed #cbd5e1; border-radius: 8px;
  background: transparent;
  color: #64748b;
  padding: 10px 14px;
  cursor: pointer; transition: border-color 0.12s, color 0.12s, background 0.12s;
  text-align: left; font-family: inherit;
}
.add-doc-zone:hover {
  border-color: #ea580c; color: #9a3412; background: #fff7ed;
}
.reset-row {
  display: flex; justify-content: space-between; align-items: center;
  padding: 6px 4px 0; font-size: 0.74rem; color: #94a3b8;
}
.reset-row .reset-link {
  color: #64748b; text-decoration: underline; cursor: pointer; font-weight: 500;
}
.reset-row .reset-link:hover { color: #ea580c; }
```

- [ ] **Step 2: Add per-row document state**

In `merge-v2.js`, ensure each row carries a `documents` array. After `fetchResult` is populated, normalize the doc list. Add a helper:

```javascript
function buildInitialDocList(row) {
  // Build canonical doc list for the side panel from fetchResult.
  // Each doc: { id, name, source: 'qbo'|'tms'|'added', converted, pageCount, sizeBytes, failReason }
  const docs = [];
  const fr = row.fetchResult;
  if (!fr) return docs;

  if (fr.invoicePath || row.routingType !== 'warehouse') {
    docs.push({ id: uid(), name: row.invoiceNumber + '_invoice.pdf', source: 'qbo',
                converted: false, pageCount: 1, sizeBytes: 0, failReason: null });
  }

  if (row.routingType === 'warehouse') {
    for (const att of (fr.warehouseAttachments || [])) {
      docs.push({
        id: uid(), name: att.fileName, source: 'qbo',
        converted: !!att.converted, pageCount: att.pageCount || 0,
        sizeBytes: att.sizeBytes || 0, failReason: null,
      });
    }
    for (const fail of (fr.warehouseFailures || [])) {
      docs.push({
        id: uid(), name: fail.fileName, source: 'qbo',
        converted: false, pageCount: 0, sizeBytes: 0,
        failReason: fail.reason,
      });
    }
  } else {
    if (fr.podFile) {
      docs.push({ id: uid(), name: fr.podFile, source: fr.podSource || 'qbo',
                  converted: false, pageCount: 0, sizeBytes: 0, failReason: null });
    }
  }
  row._originalDocs = JSON.parse(JSON.stringify(docs));  // for Reset
  return docs;
}
```

Call `row.documents = buildInitialDocList(row)` after each fetch completion so the panel can render from it.

- [ ] **Step 3: Render the side panel attachments from row.documents**

In the side-panel render function, replace any hard-coded "POD" / "Invoice" rows with a loop over `row.documents`. For each doc, render an `.attachment-row` with:

- six-dot drag handle (HTML straight from the mockup)
- ok or fail icon based on `failReason`
- name + meta line
- source tag and `XLSX → PDF` tag if `converted`
- × remove button calling `window.v2RemoveDoc(row.id, doc.id)`

After the loop:

```html
<button class="add-doc-zone" onclick="window.v2OpenAddDocPicker('${row.id}')">
  <svg class="plus-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 5v14M5 12h14"/></svg>
  <span class="label-line">Add document</span>
  <span class="help-line">drop a PDF or click to browse</span>
</button>
<div class="reset-row">
  <span>Drag handles to reorder · × to remove</span>
  <span class="reset-link" onclick="window.v2ResetDocs('${row.id}')">Reset</span>
</div>
```

- [ ] **Step 4: Implement the four action handlers**

In `merge-v2.js`, add:

```javascript
window.v2RemoveDoc = function(rowId, docId) {
  const row = v2State.rows.find(r => r.id === rowId);
  if (!row) return;
  row.documents = row.documents.filter(d => d.id !== docId);
  setStateV2(v2State.subMode);
};

window.v2OpenAddDocPicker = function(rowId) {
  const input = document.createElement('input');
  input.type = 'file'; input.accept = 'application/pdf'; input.multiple = true;
  input.onchange = e => window.v2AddDocsToRow(rowId, e.target.files);
  input.click();
};

window.v2AddDocsToRow = function(rowId, files) {
  const row = v2State.rows.find(r => r.id === rowId);
  if (!row) return;
  for (const f of files) {
    row.documents.push({
      id: uid(), name: f.name, source: 'added',
      converted: false, pageCount: 0, sizeBytes: f.size,
      failReason: null, _file: f,  // stash the actual File so merge-engine can read it
    });
  }
  setStateV2(v2State.subMode);
};

window.v2ResetDocs = function(rowId) {
  const row = v2State.rows.find(r => r.id === rowId);
  if (!row || !row._originalDocs) return;
  row.documents = JSON.parse(JSON.stringify(row._originalDocs));
  setStateV2(v2State.subMode);
};
```

- [ ] **Step 5: Wire drag-and-drop reordering**

Use the existing `SortableJS` CDN dependency (already used elsewhere — search `Sortable.create` for examples). Initialize Sortable on the attachment-list container after each render:

```javascript
if (window.Sortable) {
  const list = document.querySelector(`[data-doc-list="${row.id}"]`);
  if (list) {
    Sortable.create(list, {
      handle: '.drag-handle',
      onEnd: (evt) => {
        const moved = row.documents.splice(evt.oldIndex, 1)[0];
        row.documents.splice(evt.newIndex, 0, moved);
      },
    });
  }
}
```

Add `data-doc-list="${row.id}"` to the parent of the `.attachment-row` list in the panel template.

- [ ] **Step 6: Verify in browser**

Open `app/index.html`, fetch a row with at least 2 attachments. Click the row to open its side panel. Expected:
- Each doc has a drag handle, source tag, × button.
- Dragging a doc reorders the list.
- Clicking × removes that doc from this merge.
- Clicking "+ Add document" opens a file picker; selected files appear with the "Added by you" tag.
- Clicking "Reset" returns the list to what was originally fetched.
- Across all of the above, no network call is made (verify in Network tab).

- [ ] **Step 7: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js app/assets/css/styles.css
git commit -m "ui(merge): side-panel document customization for all row types

Each attachment row gets:
  - drag handle (Sortable.js reordering)
  - source tag (From QBO / From TMS / Added by you / XLSX → PDF)
  - × remove (excludes from this merge — never touches QBO)

Below the list:
  - + Add document drop zone
  - Reset link (local undo, no network)

Applies to import, export, and warehouse rows alike."
```

---

### Task 15: Side-panel error states (partial failure + zero attachments)

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js` (side panel banner rendering)
- Modify: `app/assets/css/styles.css` (banner styles — copy from mockup)

- [ ] **Step 1: Add CSS for the banners**

In `app/assets/css/styles.css`, add (copied from the mockup):

```css
.panel-error-banner {
  display: flex; align-items: flex-start; gap: 8px;
  background: #fef2f2; border-bottom: 1px solid #fecaca;
  padding: 10px 16px; color: #991b1b; font-size: 0.78rem;
}
.panel-error-banner svg { width: 14px; height: 14px; flex-shrink: 0; margin-top: 2px; }
.panel-error-banner strong { color: #7f1d1d; }

.panel-empty-banner {
  display: flex; align-items: flex-start; gap: 8px;
  background: #fffbeb; border-bottom: 1px solid #fde68a;
  padding: 10px 16px; color: #78350f; font-size: 0.78rem;
}
.panel-empty-banner svg { width: 14px; height: 14px; flex-shrink: 0; margin-top: 2px; }
```

- [ ] **Step 2: Add the banner logic to the side-panel template**

In the side-panel render function, before the `<div class="side-panel-body">` block, compute and render the banner:

```javascript
const failedDocs = row.documents.filter(d => d.failReason);
const successfulDocs = row.documents.filter(d => !d.failReason);

let banner = '';
if (successfulDocs.length === 0 && row.documents.length === 0) {
  // Zero attachments banner (warehouse — QBO returned nothing)
  banner = `
    <div class="panel-empty-banner">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"
           stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
      </svg>
      <span><strong>No documents on QBO.</strong> Upload one manually below — this row can't merge without it.</span>
    </div>
  `;
} else if (failedDocs.length > 0) {
  // Partial failure banner
  const n = failedDocs.length;
  banner = `
    <div class="panel-error-banner">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"
           stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
      </svg>
      <span><strong>${n} attachment${n > 1 ? 's' : ''} couldn't be converted.</strong>
        The merge will continue with what worked — drop in a replacement if you have one.</span>
    </div>
  `;
}
```

Insert `${banner}` immediately after `<div class="side-panel-header">…</div>` and before `<div class="side-panel-body">`.

- [ ] **Step 3: Render failed attachment rows in red**

In the existing attachment-row render loop (from Task 14), when `doc.failReason` is set, render:

```html
<div class="attachment-row fail">
  <span class="drag-handle" style="color:#fca5a5;">…drag svg…</span>
  <span class="icon fail">…alert svg…</span>
  <div style="flex:1; min-width:0;">
    <div class="name">${escHtml(doc.name)}</div>
    <div class="fail-reason">${escHtml(humanizeFailReason(doc.failReason))}</div>
  </div>
  <button class="remove-btn" onclick="window.v2RemoveDoc('${row.id}', '${doc.id}')">×</button>
</div>
```

Add a small helper:

```javascript
function humanizeFailReason(reason) {
  if (!reason) return '';
  if (reason.includes('password')) return "Couldn't convert — file is password-protected";
  if (reason.includes('corrupt')) return "Couldn't convert — file is corrupt";
  if (reason.includes('timeout')) return "Couldn't convert — took too long";
  if (reason.includes('empty')) return "Couldn't convert — workbook is empty";
  if (reason.includes('excel_not_installed')) return "Couldn't convert — Excel isn't installed";
  if (reason === 'download_failed') return "Couldn't download from QBO";
  if (reason === 'unsupported_type') return "Unsupported file type — only PDF and Excel are converted";
  return "Couldn't process this file";
}
```

- [ ] **Step 4: Verify in browser (manual)**

Force a failure scenario: drop a manifest with a warehouse row, then in the agent's `downloads/<job_id>/` add a fake xlsx that's password-protected. Run a fetch. Expected:
- Side panel shows the red "N attachments couldn't be converted" banner.
- The failed file row is red with the plain-English reason.
- Other (successful) attachments are unaffected.

For the zero-attachments banner, manually edit a row in DevTools to set `row.documents = []`, then re-open the panel. Expected: amber banner appears.

- [ ] **Step 5: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js app/assets/css/styles.css
git commit -m "ui(merge): side-panel error banners — partial failure + zero attachments

Red banner when one or more docs failed to convert: explains the merge
will continue without them. Failed file rows render in red with a
plain-English reason (humanizeFailReason).

Amber banner when QBO returned zero attachments: prompts manual upload."
```

---

### Task 16: Rebuild bulk-drop matcher (universal + multi-attach)

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2.js:694-751` (the existing `v2HandleBulkPdfDrop`)

The new rules:
1. Match by container number AND by INV#, case-insensitive substring.
2. A single PDF attaches to **all** matching rows (no first-match-wins).
3. Feedback message lists multi-attach cases.

- [ ] **Step 1: Replace the matcher**

In `app/assets/js/tools/merge/merge-v2.js`, replace the entire `v2HandleBulkPdfDrop` function (currently lines 694-750) with:

```javascript
async function v2HandleBulkPdfDrop(fileList) {
  const files = Array.from(fileList).filter(f => f.name.toLowerCase().endsWith('.pdf'));
  if (files.length === 0) {
    alert('Only .pdf files are accepted.');
    return;
  }

  // Build a lookup of every identifier from the manifest.
  // identifier = { idType: 'container'|'inv', value: 'lowercased', rowIdx: N }
  const identifiers = [];
  for (let i = 0; i < v2State.rows.length; i++) {
    const r = v2State.rows[i];
    const cn = (r.containerNumber || '').toLowerCase().trim();
    const inv = (r.invoiceNumber || '').toLowerCase().trim();
    if (cn) identifiers.push({ idType: 'container', value: cn, rowIdx: i });
    if (inv) identifiers.push({ idType: 'inv', value: inv, rowIdx: i });
  }

  const matchedSummary = [];  // [{ fileName, rowNums: [1,2,3], by: 'container'|'inv'|'both' }]
  const unmatched = [];

  for (const file of files) {
    const lower = file.name.toLowerCase();
    const hits = new Map();  // rowIdx → { container?: true, inv?: true }

    for (const id of identifiers) {
      if (lower.includes(id.value)) {
        const entry = hits.get(id.rowIdx) || {};
        entry[id.idType] = true;
        hits.set(id.rowIdx, entry);
      }
    }

    if (hits.size === 0) {
      unmatched.push(file.name);
      continue;
    }

    const rowNums = [];
    for (const [rowIdx, by] of hits.entries()) {
      const row = v2State.rows[rowIdx];
      // Attach a copy reference to every matching row. Same File object — fine.
      row.documents = row.documents || [];
      row.documents.push({
        id: uid(), name: file.name, source: 'added',
        converted: false, pageCount: 0, sizeBytes: file.size,
        failReason: null, _file: file,
      });
      // Promote row to "ready" so it isn't blocked by a previous miss
      if (row.fetchResult?.podPill === 'miss') {
        row.fetchResult.podPill = 'ok';
        row.fetchResult.statusText = 'Manual upload';
        row.selected = true;
      }
      rowNums.push(row.rowNum);
    }
    matchedSummary.push({ fileName: file.name, rowNums });
  }

  setStateV2(v2State.subMode);

  // Build the feedback message
  const multiAttach = matchedSummary.filter(m => m.rowNums.length > 1);
  const total = matchedSummary.length;
  let msg = `${total} of ${files.length} PDF${files.length === 1 ? '' : 's'} matched.`;
  if (multiAttach.length > 0) {
    msg += '\n\nMulti-row attaches:';
    for (const m of multiAttach) {
      msg += `\n  • ${m.fileName} → Rows ${m.rowNums.join(', ')}`;
    }
  }
  if (unmatched.length > 0) {
    const sample = unmatched.slice(0, 3).map(n => `\n  • ${n}`).join('');
    const more = unmatched.length > 3 ? `\n  …and ${unmatched.length - 3} more` : '';
    msg += `\n\nNo container # or INV# match in filename for:${sample}${more}`;
  }
  if (total > 0 || unmatched.length > 0) alert(msg);
}
window.v2HandleBulkPdfDrop = v2HandleBulkPdfDrop;
```

- [ ] **Step 2: Verify in browser — single-row match**

Open `app/index.html`, load a manifest with a warehouse row INV# `LW260515P01`. Bulk-drop a PDF named `LW260515P01_extra.pdf`. Expected: file attaches to that row; feedback says "1 of 1 matched".

- [ ] **Step 3: Verify in browser — multi-row match**

Load a manifest where the same container appears in 3 rows (different INV#s). Bulk-drop a single PDF named with that container number. Expected: file attaches to all 3 rows; feedback message includes the "Multi-row attaches" section listing the 3 rows.

- [ ] **Step 4: Verify in browser — INV# match for import row**

Load a manifest with an import row (container `TEMU8809194`, INV# `LM2602170009`). Bulk-drop a PDF named `LM2602170009.pdf` (INV# only). Expected: file attaches to that row via INV# match.

- [ ] **Step 5: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge): universal bulk-drop matcher with multi-row attach

Replaces 'first-match-wins per row, first row wins per file' with:
  - Match by container # AND by INV#, case-insensitive substring
  - One file attaches to ALL matching rows
  - Feedback lists multi-attach cases explicitly

Fixes the silent-loss case where v2.47 invoice-grouping dedupes by INV#
but the bulk-drop matcher only attached a POD to the first row sharing
a container, leaving the other invoices documentless."
```

---

## Phase 4 — Merge engine

### Task 17: Multi-attachment merge for warehouse rows

**Files:**
- Modify: `app/assets/js/tools/merge/merge-v2-engine.js`

Existing engine paths assume one invoice + one POD per row. Warehouse rows can have 1 invoice + N attachments. Update the per-row merge function to iterate `row.documents` in order rather than reading a hardcoded `invoicePath` + `podPath`.

- [ ] **Step 1: Find the per-row merge function**

```bash
grep -n "mergePerContainer\|mergePerInvoice\|async function merge" app/assets/js/tools/merge/merge-v2-engine.js
```

- [ ] **Step 2: Refactor to iterate row.documents**

For each per-invoice mode, the merge function now reads the row's `documents` array and concatenates the underlying PDFs in order. Each `doc` has either:
- `_file` (a File from manual add / bulk drop), or
- A name that maps to a file in `agent/downloads/<job_id>/` (already fetched by the agent).

For docs fetched by the agent, fetch the bytes via the existing `/files/{path}` endpoint (or the relative path the agent returns). For `_file` docs, read bytes via `readAsArrayBuffer(doc._file)`.

Pseudocode:

```javascript
async function mergeRowDocuments(row, modeKey, jobId) {
  // modeKey: 'per-container' | 'per-container-invoice' | 'per-container-document'
  const merged = await PDFDocument.create();
  const docs = (row.documents || []).filter(d => !d.failReason);

  // Determine which docs to include based on mode
  const include = docs.filter(d => {
    if (modeKey === 'per-container') return true;
    if (modeKey === 'per-container-invoice') return d.name.includes('_invoice') || d.source === 'qbo' && d.name === row.invoiceNumber + '_invoice.pdf';
    if (modeKey === 'per-container-document') return !d.name.includes('_invoice');
    return false;
  });

  for (const doc of include) {
    const bytes = doc._file
      ? await readAsArrayBuffer(doc._file)
      : await fetchDocBytes(jobId, doc.name);
    if (!bytes) continue;
    const src = await PDFDocument.load(bytes, { updateMetadata: false });
    const pages = await merged.copyPages(src, src.getPageIndices());
    for (const p of pages) merged.addPage(p);
  }

  return await merged.save({ updateFieldAppearances: false });
}
```

Implement `fetchDocBytes(jobId, fileName)` if not already present — it's a GET on `agentBridge`'s file endpoint that returns the PDF bytes for the given job download.

Replace all existing per-row merge call sites with this unified function.

- [ ] **Step 3: Update the merge orchestrator to pass jobId**

Find where the engine is invoked from `merge-v2.js`. The orchestrator should pass `v2State.jobId` (or whatever holds it) into the engine.

- [ ] **Step 4: Verify in browser — single warehouse row**

Drop a manifest with one warehouse row. Fetch. Click "Continue to Merge". Run Per Invoice. Expected: output PDF has invoice page + all warehouse attachments (in agent-returned order). Open it and visually confirm.

- [ ] **Step 5: Verify in browser — warehouse row with manual add**

Same setup, but before running merge, add a manual PDF via the side panel. Run Per Invoice. Expected: output PDF includes the manually-added file in its slot.

- [ ] **Step 6: Verify in browser — warehouse row with reordered docs**

Same setup, drag a doc to reorder. Run Per Invoice. Expected: output PDF reflects the new order.

- [ ] **Step 7: Verify in browser — backward compat with import/export**

Run a normal import or export merge. Expected: behavior unchanged (still produces invoice + POD/BL/POL).

- [ ] **Step 8: Commit**

```bash
git add app/assets/js/tools/merge/merge-v2-engine.js app/assets/js/tools/merge/merge-v2.js
git commit -m "feat(merge): engine iterates row.documents for per-row merge

Replaces the hardcoded invoice+POD assumption with iteration over each
row's documents array. Picks up warehouse multi-attachment, manual adds
from the side panel, and side-panel reordering automatically.

Backwards-compatible with import/export — their documents arrays still
hold invoice+POD/BL/POL and merge identically."
```

---

## Phase 5 — Settings · Storage card

### Task 18: Storage card HTML + CSS

**Files:**
- Modify: `app/index.html` (Settings section)
- Modify: `app/assets/css/styles.css`

Visual reference: `app/mockups/v2.72-settings-storage-mockup.html`.

- [ ] **Step 1: Find the Settings panel container in index.html**

```bash
grep -n "id=\"settingsContent\"\|class=\"settings-content\"\|<!-- Settings -->\|settings-card" app/index.html
```

Locate the structural anchor for the Settings page content (likely a `<section id="settingsContent">` or similar).

- [ ] **Step 2: Add the Storage card HTML**

Inside the Settings content container, add this card (use the existing card layout pattern from the file as a guide — wrap with `.panel-card`):

```html
<div class="panel-card" id="storageCard">
  <div class="section-label">
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#ea580c" stroke-width="2"
         stroke-linecap="round" stroke-linejoin="round">
      <ellipse cx="12" cy="5" rx="9" ry="3"/>
      <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/>
      <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>
    </svg>
    Storage
  </div>

  <div class="storage-row">
    <div style="min-width:0;">
      <div class="label">Saved merged files</div>
      <div class="path" id="storageOutputPath">…</div>
      <div class="meta" id="storageOutputMeta">…</div>
      <div class="size-bar"><div class="fill" id="storageOutputBar" style="width:0%;"></div></div>
    </div>
    <div>
      <button class="btn btn-secondary" onclick="window.settingsOpenOutputFolder()">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
        </svg>
        Open folder
      </button>
    </div>
  </div>

  <div class="storage-row">
    <div style="min-width:0;">
      <div class="label">Temporary app files</div>
      <div class="path" id="storageDownloadsPath">…</div>
      <div class="meta" id="storageDownloadsMeta">…</div>
      <div class="size-bar"><div class="fill" id="storageDownloadsBar" style="width:0%;"></div></div>
    </div>
  </div>

  <div class="cleanup-callout">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
      <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
    </svg>
    <div class="text">
      <strong>Auto-cleanup is on.</strong>
      Merged files and working files older than <strong>7 days</strong> are deleted automatically.
      The current week of output always stays.
      <span class="last" id="storageLastCleanup"></span>
    </div>
  </div>

  <div class="action-row">
    <button class="btn btn-secondary" onclick="window.settingsCleanupNow()">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="3 6 5 6 21 6"/>
        <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
      </svg>
      Clean up now
    </button>
    <button class="btn btn-ghost" onclick="window.settingsChangeOutputFolder()">Change output folder…</button>
  </div>

  <div class="subtle-footnote">
    Only the Merge Tool saves files to disk. Invoice Sender and Customer Manager don't.
  </div>
</div>
```

- [ ] **Step 3: Add the Storage card CSS**

In `app/assets/css/styles.css`, add (copied from the mockup):

```css
/* Storage card */
.storage-row {
  display: grid; grid-template-columns: 1fr auto;
  gap: 18px; align-items: center;
  padding: 12px 0; border-bottom: 1px solid #f1f5f9;
}
.storage-row:last-of-type { border-bottom: none; }
.storage-row .label { font-size: 0.82rem; font-weight: 700; color: #0f172a; margin-bottom: 2px; }
.storage-row .path {
  font-family: 'SF Mono', Consolas, 'Courier New', monospace;
  color: #475569; font-size: 0.82rem; word-break: break-all;
}
.storage-row .meta { color: #64748b; font-size: 0.78rem; margin-top: 4px; }
.storage-row .meta strong { color: #0f172a; font-weight: 700; }

.size-bar {
  margin-top: 10px; height: 8px; width: 100%;
  background: #f1f5f9; border-radius: 4px; overflow: hidden;
}
.size-bar .fill {
  height: 100%; background: linear-gradient(90deg, #ea580c, #f97316);
  border-radius: 4px;
}

.cleanup-callout {
  margin-top: 14px;
  background: #fff7ed;
  border: 1px solid #fed7aa; border-radius: 8px;
  padding: 12px 14px;
  display: flex; align-items: flex-start; gap: 12px;
}
.cleanup-callout svg { color: #ea580c; flex-shrink: 0; margin-top: 1px; }
.cleanup-callout .text { font-size: 0.83rem; color: #7c2d12; line-height: 1.5; }
.cleanup-callout .text .last { display: block; margin-top: 4px; color: #9a3412; font-size: 0.76rem; }

.action-row { margin-top: 14px; display: flex; gap: 8px; flex-wrap: wrap; }

.subtle-footnote {
  margin-top: 16px; padding-top: 10px;
  border-top: 1px solid #f8fafc;
  color: #cbd5e1; font-size: 0.72rem;
  font-style: italic; line-height: 1.4;
}
```

- [ ] **Step 4: Verify in browser**

Open `app/index.html`, navigate to Settings. Expected: the new Storage card is visible with placeholder dots. No JS yet — wiring is in Task 19.

- [ ] **Step 5: Commit**

```bash
git add app/index.html app/assets/css/styles.css
git commit -m "ui(settings): add Storage card markup + styles

Card layout: 'Saved merged files' + 'Temporary app files' rows with
size bars, 'Open folder' button, orange auto-cleanup callout, action
row (Clean up now + Change output folder), subtle italic footnote
clarifying that Invoice Sender and Customer Manager don't save files
to disk. Mockup: app/mockups/v2.72-settings-storage-mockup.html"
```

---

### Task 19: Wire Storage card JS

**Files:**
- Modify: `app/assets/js/tools/settings/settings.js`

- [ ] **Step 1: Add the loader and renderer**

In `app/assets/js/tools/settings/settings.js`, add:

```javascript
import { agentBridge } from '../../shared/agent-client.js';
import { fmtSize } from '../../shared/utils.js';

async function loadStorageInfo() {
  try {
    const info = await agentBridge.get('/storage/info');
    renderStorageInfo(info);
  } catch (e) {
    console.warn('Could not load /storage/info:', e);
    document.getElementById('storageOutputMeta').textContent = 'Unavailable — agent not connected.';
    document.getElementById('storageDownloadsMeta').textContent = '';
  }
}

function renderStorageInfo(info) {
  const outPath = document.getElementById('storageOutputPath');
  const outMeta = document.getElementById('storageOutputMeta');
  const outBar  = document.getElementById('storageOutputBar');
  const dlPath  = document.getElementById('storageDownloadsPath');
  const dlMeta  = document.getElementById('storageDownloadsMeta');
  const dlBar   = document.getElementById('storageDownloadsBar');
  const lastEl  = document.getElementById('storageLastCleanup');

  if (outPath) outPath.textContent = info.output_root;
  if (outMeta) outMeta.innerHTML =
    `<strong>${fmtSize(info.output_size_bytes)}</strong> · ${info.output_file_count} files · ${info.output_folder_count} folders`;
  if (outBar) {
    const pct = Math.min(100, info.output_size_bytes / (20 * 1024 * 1024 * 1024) * 100);
    outBar.style.width = `${pct.toFixed(1)}%`;
  }

  if (dlPath) dlPath.textContent = info.output_root.replace(/\\?$/, '') + '\\agent\\downloads';
  if (dlMeta) dlMeta.innerHTML =
    `<strong>${fmtSize(info.downloads_size_bytes)}</strong> · ${info.downloads_batch_count} batches`
    + ` <span style="color:#94a3b8;">· auto-cleaned by the same 7-day rule</span>`;
  if (dlBar) {
    const pct = Math.min(100, info.downloads_size_bytes / (5 * 1024 * 1024 * 1024) * 100);
    dlBar.style.width = `${pct.toFixed(1)}%`;
  }

  if (lastEl) {
    if (info.last_cleanup_ts > 0) {
      const dt = new Date(info.last_cleanup_ts * 1000);
      const when = dt.toLocaleString();
      lastEl.textContent = `Last cleanup ran ${when} · removed ${info.last_cleanup_files_removed} files (${fmtSize(info.last_cleanup_freed_bytes)})`;
    } else {
      lastEl.textContent = '';
    }
  }
}

window.settingsCleanupNow = async function() {
  const btn = document.querySelector('[onclick="window.settingsCleanupNow()"]');
  if (btn) btn.disabled = true;
  try {
    const info = await agentBridge.post('/storage/cleanup', {});
    renderStorageInfo(info);
    alert(`Cleanup complete — freed ${fmtSize(info.last_cleanup_freed_bytes)} across ${info.last_cleanup_files_removed} files.`);
  } catch (e) {
    alert('Cleanup failed: ' + e.message);
  } finally {
    if (btn) btn.disabled = false;
  }
};

window.settingsOpenOutputFolder = async function() {
  try {
    const info = await agentBridge.get('/storage/info');
    await agentBridge.openPath(info.output_root);  // existing helper
  } catch (e) {
    alert('Could not open the output folder: ' + e.message);
  }
};

window.settingsChangeOutputFolder = async function() {
  // Reuse the existing folder-picker flow if present in this file.
  if (typeof window.settingsPickOutputFolder === 'function') {
    await window.settingsPickOutputFolder();
    loadStorageInfo();
  } else {
    alert('Folder picker not available.');
  }
};

// Auto-load on settings page entry. Hook into whatever existing routine renders the page.
window.loadStorageInfo = loadStorageInfo;
```

- [ ] **Step 2: Call loadStorageInfo when the Settings page becomes visible**

Find the function in `app.js` (or wherever) that switches to the Settings tab and add a call to `window.loadStorageInfo()` at the end of it.

- [ ] **Step 3: Verify in browser**

Open `app/index.html`. Make sure the agent is running. Navigate to Settings. Expected:
- Storage card populates with real path, size, file count.
- Bars reflect the percentage.
- "Last cleanup ran …" line appears (from the agent's startup sweep).
- Clicking "Open folder" launches Explorer at the chosen location.
- Clicking "Clean up now" triggers a sweep; UI updates; success alert shows.

- [ ] **Step 4: Commit**

```bash
git add app/assets/js/tools/settings/settings.js
git commit -m "feat(settings): wire Storage card to /storage/info + /storage/cleanup

Loads on page entry, populates paths/sizes/last-cleanup line. Open folder
launches Explorer via agentBridge.openPath. Clean up now triggers
/storage/cleanup and refreshes the panel."
```

---

## Phase 6 — Integration test + verification

### Task 20: Excel converter unit tests

**Files:**
- Create: `agent/tests/test_excel_converter.py`

These tests are marked `requires_excel` so they only run on machines with Excel installed. Useful as a smoke test before each release.

- [ ] **Step 1: Add a pytest marker**

In `agent/tests/conftest.py` (create if absent — or `pytest.ini` / `pyproject.toml`), register the marker:

```python
import pytest

def pytest_configure(config):
    config.addinivalue_line("markers", "requires_excel: requires Microsoft Excel + pywin32")
```

- [ ] **Step 2: Write the test file**

Create `agent/tests/test_excel_converter.py`:

```python
"""Excel converter tests — require Microsoft Excel installed."""

import asyncio
from pathlib import Path

import pytest

from services.excel_converter import (
    _check_excel_available,
    convert_xlsx_to_pdf,
    ExcelSession,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_XLSX = REPO_ROOT / "app" / "assets" / "images" / "APRIL CHARGE 2026.xlsx"


@pytest.mark.requires_excel
def test_excel_is_available():
    assert _check_excel_available() is True


@pytest.mark.requires_excel
@pytest.mark.asyncio
async def test_convert_real_warehouse_xlsx(tmp_path: Path):
    """End-to-end conversion of the POC's APRIL CHARGE 2026 file."""
    assert SAMPLE_XLSX.exists(), f"Sample file missing: {SAMPLE_XLSX}"
    out = tmp_path / "april.pdf"

    result = await convert_xlsx_to_pdf(SAMPLE_XLSX, out)

    assert result.ok is True, f"Conversion failed: {result.error}"
    assert out.exists()
    assert result.size_bytes > 100_000  # POC produced ~800 KB
    # POC: 20 sheets → 20 pages. Allow a few-page wobble for Excel layout updates.
    assert 15 <= result.pages <= 30


@pytest.mark.requires_excel
@pytest.mark.asyncio
async def test_session_reuses_excel_across_files(tmp_path: Path):
    """ExcelSession should not relaunch Excel between convert() calls."""
    out1 = tmp_path / "a.pdf"
    out2 = tmp_path / "b.pdf"

    async with ExcelSession() as session:
        first = await session.convert(SAMPLE_XLSX, out1)
        second = await session.convert(SAMPLE_XLSX, out2)

    assert first.ok and second.ok
    assert out1.exists() and out2.exists()


@pytest.mark.requires_excel
@pytest.mark.asyncio
async def test_missing_source_returns_error(tmp_path: Path):
    missing = tmp_path / "does-not-exist.xlsx"
    out = tmp_path / "out.pdf"
    result = await convert_xlsx_to_pdf(missing, out)
    assert result.ok is False
    assert "open_error" in (result.error or "")
    assert not out.exists()
```

- [ ] **Step 3: Run the tests**

```bash
cd agent && python -m pytest tests/test_excel_converter.py -v -m requires_excel
```

Expected: 4 passed. (Takes ~30 seconds because of the Excel warm-up + two real conversions.)

- [ ] **Step 4: Commit**

```bash
git add agent/tests/test_excel_converter.py agent/tests/conftest.py
git commit -m "test(agent): Excel converter unit tests (@requires_excel)

Smoke test of _check_excel_available, end-to-end conversion of the POC
sample xlsx, session reuse, and missing-file error path."
```

---

### Task 21: Warehouse end-to-end integration test

**Files:**
- Create: `agent/tests/integration/test_warehouse_end_to_end.py`
- Create: `agent/tests/integration/__init__.py` (empty)

Marked `requires_excel` and `requires_qbo` — skipped on CI by default, run before each release.

- [ ] **Step 1: Add the qbo marker to conftest**

In `agent/tests/conftest.py`:

```python
def pytest_configure(config):
    config.addinivalue_line("markers", "requires_excel: requires Microsoft Excel + pywin32")
    config.addinivalue_line("markers", "requires_qbo: requires live QBO OAuth session")
```

- [ ] **Step 2: Create the test directory**

```bash
mkdir -p agent/tests/integration
touch agent/tests/integration/__init__.py
```

- [ ] **Step 3: Write the integration test**

Create `agent/tests/integration/test_warehouse_end_to_end.py`:

```python
"""Warehouse end-to-end — requires live QBO + Excel.

This is the scripted version of scratch/warehouse_full_run.py. It exercises
the full path: routing decision → list_attachments → download_attachment
→ Excel COM conversion → merged FetchResult.

Skipped by default — run before each release."""

from pathlib import Path

import pytest

from services.excel_converter import _check_excel_available


WAREHOUSE_INV = "LW260515P01"      # real warehouse invoice from POC
WAREHOUSE_TXN_ID = "391101"        # real QBO txnId


@pytest.mark.requires_excel
@pytest.mark.requires_qbo
@pytest.mark.asyncio
async def test_warehouse_fetch_end_to_end(tmp_path: Path):
    """Verify the agent path that the merge tool will exercise for warehouse rows.

    Asserts the fetch result contains:
      - routing_type == 'warehouse'
      - pod_label == 'Warehouse'
      - warehouse_attachments: at least 1 entry, all converted or pass-through
      - warehouse_failures: empty (for this known-good invoice)
    """
    assert _check_excel_available(), "Excel COM not available — cannot run."

    from services.qbo_api import QBOApiClient
    from services.qbo_api.attachments import classify_attachment
    from services.excel_converter import ExcelSession

    api = QBOApiClient()
    await api.load_tokens()

    invoice = await api.search_invoice(WAREHOUSE_INV)
    assert invoice is not None, f"Could not find {WAREHOUSE_INV} in QBO"
    invoice_id = invoice["Id"]

    attachments = await api.list_attachments(invoice_id)
    assert len(attachments) >= 1, "Expected at least one attachment on POC invoice"

    successes = []
    failures = []
    async with ExcelSession() as session:
        for att in attachments:
            fname = att["fileName"]
            lower = fname.lower()
            path = await api.download_attachment(
                att["id"], fname, tmp_path, temp_download_uri=att.get("tempDownloadUri"),
            )
            assert path is not None, f"Download failed for {fname}"

            if lower.endswith((".xlsx", ".xls", ".xlsm")):
                pdf = path.with_suffix(".pdf")
                result = await session.convert(path, pdf)
                if result.ok:
                    successes.append(pdf.name)
                else:
                    failures.append({"file": fname, "reason": result.error})
            elif lower.endswith(".pdf"):
                successes.append(path.name)
            else:
                failures.append({"file": fname, "reason": "unsupported"})

    assert len(successes) >= 1, "No attachments succeeded"
    assert len(failures) == 0, f"Unexpected failures: {failures}"
```

- [ ] **Step 4: Run the test (locally, with QBO logged in)**

```bash
cd agent && python -m pytest tests/integration/test_warehouse_end_to_end.py -v -m "requires_excel and requires_qbo"
```

Expected: 1 passed (takes ~20s).

- [ ] **Step 5: Commit**

```bash
git add agent/tests/integration/ agent/tests/conftest.py
git commit -m "test(agent): warehouse end-to-end integration test

Scripted port of scratch/warehouse_full_run.py. Marked
requires_excel + requires_qbo so it skips on CI by default."
```

---

## Phase 7 — Ship

### Task 22: Spec self-review pass — verify implementation matches spec

Before bumping the version, walk through the spec section-by-section and confirm every requirement is implemented.

- [ ] **Step 1: Open the spec**

Open `docs/superpowers/specs/2026-05-18-merge-tool-warehouse-invoices-design.md`.

- [ ] **Step 2: Check each section against the implementation**

For each of the 7 sections, confirm:

- **Section 1 (Scope & identification):** `routingDecisionFor` extended for W — Task 9 ✓. Manifest parser accepts container-less warehouse rows — Task 12 ✓. Routing hint copy mentions M/E/W — Task 13 ✓.
- **Section 2 (Fetch flow):** Warehouse branch added — Task 8 ✓. `download_attachment` retry — Task 4 ✓. `fetchResult` shape includes `warehouseAttachments`/`warehouseFailures` — Task 8 ✓.
- **Section 3 (Excel converter):** Module + `ExcelSession` + page-setup rules + failure handling — Task 3 ✓. Excel install check at startup + `/health` flag — Task 7 ✓. Orphan EXCEL.EXE cleanup — Task 7 ✓.
- **Section 4 (Review UI):** Filter tab + routing summary band — Task 13 ✓. Em-dash cells, orange WAREHOUSE badge — Task 13 ✓. Side panel customization — Task 14 ✓. Error states — Task 15 ✓.
- **Section 5 (Filenames + folders + bulk-drop):** Filename builder INV#-only — Task 11 ✓. Mode rename — Task 10 ✓. Bulk-drop matcher — Task 16 ✓.
- **Section 6 (Edge cases & testing):** Unit tests for converter + retry + storage — Tasks 4, 5, 20 ✓. Integration test — Task 21 ✓.
- **Section 7 (Storage management):** Cleanup module + endpoints + Settings card — Tasks 5, 6, 7, 18, 19 ✓.

If any item above is unchecked, do that task before proceeding. Otherwise continue.

---

### Task 23: Version bump, build, push, GitHub release

The user requires **every** rebuild to follow the full pipeline: bump VERSION, build agent + Electron installer, commit, push, create a GH release with the installer and `latest.yml` attached. This is per `feedback_always_push_and_release.md`. Use `runbuild.bat` (not `build-all.bat`) per `feedback_use_runbuild_for_rebuild.md`.

- [ ] **Step 1: Bump the version**

Open `desktop/VERSION`. Read the current version (e.g. `2.71.0`). Bump to `2.72.0`:

```bash
echo 2.72.0 > desktop/VERSION
```

The build script overwrites `desktop/package.json` from `desktop/VERSION` (per `reference_bump_version_script.md`). Don't hand-edit `package.json`.

- [ ] **Step 2: Run the JS syntax pre-check**

Per `reference_build_js_check.md`, the build refuses to package broken JS:

```bash
cd desktop && node check-js.js
```

Expected: "OK" or no errors. If errors, fix them before continuing.

- [ ] **Step 3: Run the full build**

```bash
cd desktop && powershell -Command "Start-Process -FilePath 'runbuild.bat' -RedirectStandardInput nul -Wait -NoNewWindow"
```

(The empty-stdin invocation pattern is required — `runbuild.bat` calls children that would otherwise read stdin.)

Expected: build completes with `desktop/dist/win-unpacked/NGL Accounting.exe` and `desktop/dist/NGL Accounting Setup 2.72.0.exe` (and `latest.yml`).

- [ ] **Step 4: Stage and commit everything**

```bash
git add -A
git status  # review what's staged
git commit -m "feat(merge/v2.72): warehouse invoice support + cross-cutting fixes

Adds warehouse as a third invoice type alongside import/export.

  - Routing by INV# position-2='W' (e.g. LW260515P01)
  - QBO-only fetch, no TMS fallback, no safety cascade
  - xlsx/xls/xlsm attachments converted via pywin32 + Excel COM
    (one ExcelSession per fetch job, ~8s warm-up, ~1-2s per file)
  - New /storage/info + /storage/cleanup endpoints
  - 3-attempt retry on QBO download_attachment

Cross-cutting fixes shipped alongside (also affect import/export):

  - Output filenames are INV#-only across all row types — container
    number is dropped from filenames
  - 'Per Container' tab + folder renamed 'Per Invoice'
  - Bulk-drop matcher rebuilt: matches by container OR INV# universally,
    one PDF can attach to multiple rows (fixes v2.47 silent-loss case
    where multiple invoices shared a container)
  - Side panel doc list: drag/remove/+add/Reset for all row types
  - 7-day auto-cleanup of Merge Outputs + agent/downloads at startup
  - New Settings > Storage card with size visibility + Clean up now

Spec: docs/superpowers/specs/2026-05-18-merge-tool-warehouse-invoices-design.md"
```

- [ ] **Step 5: Push to remote**

```bash
git push
```

Expected: pushes to `main`.

- [ ] **Step 6: Create the GitHub release**

```bash
gh release create v2.72.0 \
  "desktop/dist/NGL Accounting Setup 2.72.0.exe" \
  "desktop/dist/latest.yml" \
  --title "v2.72.0 — Warehouse invoice support" \
  --notes "$(cat <<'EOF'
## Summary
- Warehouse invoices: third invoice type in the merge tool, routed by INV# letter `W`. Excel attachments are auto-converted to PDF.
- INV#-only output filenames across all row types (no container in filename).
- "Per Container" renamed to "Per Invoice" (tab + folder).
- Bulk-drop matcher rebuilt: matches by container OR INV# universally; one PDF can attach to multiple rows.
- 7-day auto-cleanup of merged files + agent temp folder. New Settings > Storage card.
- Side panel docs are now reorderable/removable; you can add extra PDFs per row.

## Test plan
- [ ] Run a mixed import+export+warehouse batch end-to-end
- [ ] Drop a warehouse row PDF named by INV# only into bulk-drop, confirm match
- [ ] Drop a PDF that matches a container appearing in 3 invoices, confirm multi-row attach
- [ ] Open Settings > Storage, click Clean up now
- [ ] Verify Open folder launches Explorer at the right location

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: release URL printed.

- [ ] **Step 7: Verify the auto-updater payload**

Open the release page in a browser. Confirm both the `.exe` installer and `latest.yml` are attached.

- [ ] **Step 8: Final memory update**

Update `C:\Users\Joseph\.claude\projects\C--Users-Joseph-Desktop-NGL-ACCOUNTING-SERVICE\memory\project_warehouse_invoices_wip.md` status header from "spec approved" to "SHIPPED v2.72.0 on YYYY-MM-DD". Update `MEMORY.md` to match.

---

## Out of scope (deferred)

These were explicitly excluded from this milestone:
- Hard page-count cap on very large workbooks — relying on the 30s timeout.
- Migrating old `Per Container/` runs to `Per Invoice/` — sweep empties them naturally.
- Per-row file-size display on the Review screen.
- Per-customer storage retention overrides — global 7-day rule for now.
