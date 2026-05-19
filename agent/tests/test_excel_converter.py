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
