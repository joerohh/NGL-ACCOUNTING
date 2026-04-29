"""Tests for cascade.run_enrich and run_document — TMS API path."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from services.tms_data.cascade import run_enrich


# Append new import for run_document tests below
# (imported inline in test functions per plan pattern)


# Helpers ────────────────────────────────────────────────────────────

def _qbo_invoice(*, wo=None, chassis=None, cnee=None) -> dict:
    """Build a minimal QBO invoice dict that the QBO extractors can parse."""
    custom_fields = []
    if wo:
        custom_fields.append({"Name": "NGL REF#", "StringValue": f"{wo}/CUST-REF"})
    if chassis:
        # _extract_chassis requires CONTAINER/CHASSIS slash format with a container
        # pattern in front. Use a realistic shape so the extractor returns chassis.
        custom_fields.append({"Name": "CNTR# / CHASSIS#", "StringValue": f"TGBU6571759/{chassis}"})
    invoice = {"CustomField": custom_fields}
    if cnee:
        invoice["CustomerMemo"] = {"value": f"Pickup\\nNGLPORT --> {cnee} --> NGLPORT"}
    return invoice


def _tms_wo(*, container=None, chassis=None, do_sender=None) -> dict:
    wo = {}
    if container:
        wo["container_no"] = container
    if chassis:
        wo["chassis_no"] = chassis
    if do_sender:
        wo["do_sender"] = [do_sender]
    return wo


# Tests ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_uses_qbo_when_complete():
    """When QBO has everything, no TMS API call is made."""
    invoice = _qbo_invoice(wo="LM2602170009", chassis="ABC1234", cnee="ACME")
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock()

    enriched, err = await run_enrich(invoice, tms_api)

    assert err is None
    assert enriched.wo_no == "LM2602170009"
    assert enriched.chassis == "ABC1234"
    assert enriched.sources["chassis"] == "qbo"
    tms_api.get_work_order.assert_not_called()


@pytest.mark.asyncio
async def test_falls_back_to_tms_api_for_missing_chassis():
    """QBO has WO# but no chassis. TMS API is called and chassis is filled in."""
    invoice = _qbo_invoice(wo="LM2602170009")  # no chassis
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value=_tms_wo(chassis="XYZ5678"))

    enriched, err = await run_enrich(invoice, tms_api)

    assert err is None
    assert enriched.chassis == "XYZ5678"
    assert enriched.sources["chassis"] == "tms_api"
    tms_api.get_work_order.assert_called_once_with("LM2602170009")


@pytest.mark.asyncio
async def test_no_tms_call_when_no_wo_number():
    """Without a WO#, the TMS API can't be called at all."""
    invoice = _qbo_invoice()  # no WO#
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock()

    enriched, err = await run_enrich(invoice, tms_api)

    assert err is None
    assert enriched.wo_no is None
    assert enriched.sources["chassis"] == "missing"
    tms_api.get_work_order.assert_not_called()


@pytest.mark.asyncio
async def test_tms_api_returns_none_marks_missing():
    """TMS API returns None (404) — fields stay missing, no error."""
    invoice = _qbo_invoice(wo="LM2602170009")
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value=None)

    enriched, err = await run_enrich(invoice, tms_api)

    assert err is None  # 404 is not a "failure" — just no data
    assert enriched.chassis is None
    assert enriched.sources["chassis"] == "missing"


@pytest.mark.asyncio
async def test_tms_api_raises_returns_error():
    """TMS API raises an exception — returns error string."""
    invoice = _qbo_invoice(wo="LM2602170009")
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(side_effect=RuntimeError("DNS failed"))

    enriched, err = await run_enrich(invoice, tms_api)

    assert err is not None
    assert "DNS failed" in err
    assert enriched.chassis is None


# run_document tests ────────────────────────────────────────────────────

from services.tms_data.cascade import run_document


@pytest.mark.asyncio
async def test_run_document_downloads_pod(tmp_path):
    invoice = _qbo_invoice(wo="LM2602170009")
    wo = {
        "documents": [{"type_": "POD", "file_url": "https://cdn.example/pod.pdf"}],
    }
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value=wo)
    tms_api.download_document = AsyncMock(return_value=b"%PDF-1.4 sample bytes")

    path, err = await run_document(invoice, "POD", tmp_path, tms_api)

    assert err is None
    assert path is not None
    assert path.exists()
    assert path.read_bytes() == b"%PDF-1.4 sample bytes"
    tms_api.download_document.assert_called_once_with("https://cdn.example/pod.pdf")


@pytest.mark.asyncio
async def test_run_document_no_wo_returns_none(tmp_path):
    invoice = _qbo_invoice()  # no WO#
    tms_api = AsyncMock()

    path, err = await run_document(invoice, "POD", tmp_path, tms_api)

    assert path is None
    assert err is not None
    assert "WO" in err  # mentions missing WO#


@pytest.mark.asyncio
async def test_run_document_doc_type_not_in_wo_returns_none(tmp_path):
    invoice = _qbo_invoice(wo="LM2602170009")
    wo = {"documents": [{"type_": "BL", "file_url": "https://cdn.example/bl.pdf"}]}
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value=wo)

    path, err = await run_document(invoice, "POD", tmp_path, tms_api)

    assert path is None
    assert err is None  # no error — just no POD on this WO


@pytest.mark.asyncio
async def test_run_document_get_wo_raises_returns_error(tmp_path):
    invoice = _qbo_invoice(wo="LM2602170009")
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(side_effect=RuntimeError("network down"))

    path, err = await run_document(invoice, "POD", tmp_path, tms_api)

    assert path is None
    assert err is not None
    assert "network down" in err


@pytest.mark.asyncio
async def test_run_document_download_returns_none_is_error(tmp_path):
    """download_document returning None means CDN failed — that's an error."""
    invoice = _qbo_invoice(wo="LM2602170009")
    wo = {"documents": [{"type_": "POD", "file_url": "https://cdn.example/pod.pdf"}]}
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value=wo)
    tms_api.download_document = AsyncMock(return_value=None)

    path, err = await run_document(invoice, "POD", tmp_path, tms_api)

    assert path is None
    assert err is not None
    assert "download" in err.lower()
