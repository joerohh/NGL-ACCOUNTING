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

    Handles both `/Type /Page` (with space) and `/Type/Page` (no space) —
    Excel's exporter omits the space, so the simple count failed for our
    real warehouse files until this was fixed. Not a full PDF parser —
    good enough for telemetry."""
    try:
        data = pdf_path.read_bytes()
        page_with    = data.count(b"/Type /Page")
        page_without = data.count(b"/Type/Page")
        pages_with    = data.count(b"/Type /Pages")
        pages_without = data.count(b"/Type/Pages")
        return (page_with + page_without) - (pages_with + pages_without)
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
