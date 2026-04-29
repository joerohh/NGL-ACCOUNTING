"""Tests for browser_path.run_enrich and run_document — TMS browser path."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from services.tms_data.browser_path import run_enrich_browser, run_document_browser


def _qbo_invoice(*, wo=None) -> dict:
    custom_fields = []
    if wo:
        custom_fields.append({"Name": "NGL REF#", "StringValue": f"{wo}/CUST-REF"})
    return {"CustomField": custom_fields}


@pytest.mark.asyncio
async def test_browser_enrich_requires_wo():
    invoice = _qbo_invoice()  # no WO#
    tms_browser = AsyncMock()

    enriched, err = await run_enrich_browser(invoice, tms_browser)

    assert err is not None
    assert "WO" in err


@pytest.mark.asyncio
async def test_browser_enrich_uses_browser_when_wo_present():
    invoice = _qbo_invoice(wo="LM2602170009")
    tms_browser = AsyncMock()
    tms_browser.fetch_detail_info = AsyncMock(return_value={
        "container_no": "KKFU7654819",
        "chassis": "BROWSERCHX",
        "do_sender_email": "browser@example.com",
    })

    enriched, err = await run_enrich_browser(invoice, tms_browser)

    assert err is None
    assert enriched.chassis == "BROWSERCHX"
    assert enriched.sources["chassis"] == "tms_browser"
    assert enriched.do_sender_email == "browser@example.com"
    assert enriched.sources["do_sender_email"] == "tms_browser"


@pytest.mark.asyncio
async def test_browser_enrich_browser_raises_returns_error():
    invoice = _qbo_invoice(wo="LM2602170009")
    tms_browser = AsyncMock()
    tms_browser.fetch_detail_info = AsyncMock(side_effect=RuntimeError("not logged in"))

    enriched, err = await run_enrich_browser(invoice, tms_browser)

    assert err is not None
    assert "not logged in" in err


@pytest.mark.asyncio
async def test_browser_document_uses_fetch_doc_by_wo(tmp_path):
    invoice = _qbo_invoice(wo="LM2602170009")
    tms_browser = AsyncMock()
    expected_path = tmp_path / "LM2602170009_POD.pdf"
    expected_path.write_bytes(b"browser pod")
    tms_browser.fetch_doc_by_wo = AsyncMock(return_value=expected_path)
    tms_browser.bc_detail_type_segment = lambda inv_no: "import"

    path, err = await run_document_browser(invoice, "POD", tmp_path, tms_browser, "LM26040454F")

    assert err is None
    assert path == expected_path


@pytest.mark.asyncio
async def test_browser_document_returns_none_when_browser_returns_none(tmp_path):
    invoice = _qbo_invoice(wo="LM2602170009")
    tms_browser = AsyncMock()
    tms_browser.fetch_doc_by_wo = AsyncMock(return_value=None)
    tms_browser.bc_detail_type_segment = lambda inv_no: "import"

    path, err = await run_document_browser(invoice, "POD", tmp_path, tms_browser, "LM26040454F")

    assert path is None
    assert err is None  # browser said "no doc", not an error
