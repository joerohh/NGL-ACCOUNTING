"""Tests for the public TMSDataLayer class."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from services.tms_data import TMSDataLayer


def _qbo_invoice(*, wo=None, chassis=None) -> dict:
    custom_fields = []
    if wo:
        custom_fields.append({"Name": "NGL REF#", "StringValue": f"{wo}/CUST-REF"})
    if chassis:
        # Use realistic CONTAINER/CHASSIS slash format that _extract_chassis parses.
        custom_fields.append({"Name": "CNTR# / CHASSIS#", "StringValue": f"TGBU6571759/{chassis}"})
    return {"CustomField": custom_fields}


@pytest.mark.asyncio
async def test_enrich_invoice_uses_api_by_default():
    qbo_api = AsyncMock()
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value={"chassis_no": "FROMTMS"})
    tms_browser = AsyncMock()

    dl = TMSDataLayer(qbo_api, tms_api, tms_browser)
    enriched = await dl.enrich_invoice("job-1", _qbo_invoice(wo="LM01"))

    assert enriched.chassis == "FROMTMS"
    assert enriched.sources["chassis"] == "tms_api"
    tms_browser.fetch_detail_info.assert_not_called()


@pytest.mark.asyncio
async def test_enrich_invoice_uses_browser_when_requested():
    qbo_api = AsyncMock()
    tms_api = AsyncMock()
    tms_browser = AsyncMock()
    tms_browser.fetch_detail_info = AsyncMock(return_value={"chassis": "FROMBROWSER"})

    dl = TMSDataLayer(qbo_api, tms_api, tms_browser)
    enriched = await dl.enrich_invoice("job-1", _qbo_invoice(wo="LM01"), source="browser")

    assert enriched.chassis == "FROMBROWSER"
    assert enriched.sources["chassis"] == "tms_browser"
    tms_api.get_work_order.assert_not_called()


@pytest.mark.asyncio
async def test_enrich_invoice_records_failure_in_failed_rows():
    qbo_api = AsyncMock()
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(side_effect=RuntimeError("DNS fail"))
    tms_browser = AsyncMock()

    dl = TMSDataLayer(qbo_api, tms_api, tms_browser)
    invoice = _qbo_invoice(wo="LM01")
    invoice["DocNumber"] = "LM26040454F"

    enriched = await dl.enrich_invoice("job-1", invoice)

    rows = dl.get_failed_rows("job-1")
    assert len(rows) == 1
    assert rows[0].invoice_number == "LM26040454F"
    assert rows[0].operation == "enrich_invoice"
    assert rows[0].failed_at_source == "tms_api"
    assert "DNS fail" in rows[0].error_message
