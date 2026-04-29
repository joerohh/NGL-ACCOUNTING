"""Verify the per-job WO cache prevents redundant TMS API calls within a job."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.tms_data import TMSDataLayer


@pytest.fixture
def invoice_data():
    return {
        "DocNumber": "INV-WO-CACHE",
        "Id": "1",
        "CustomField": [{"Name": "NGL REF#", "StringValue": "WO77/CUST"}],
    }


@pytest.mark.asyncio
async def test_enrich_then_get_document_uses_cached_wo(tmp_path, invoice_data):
    qbo, tms_api, browser = MagicMock(), AsyncMock(), AsyncMock()
    tms_api.get_work_order.return_value = {
        "container_no": "TGBU0000001",
        "do_sender": ["s@x.com"],
        "documents": [{"type_": "POD", "file_url": "https://example/pod.pdf"}],
    }
    tms_api.download_document.return_value = b"%PDF-fake"

    layer = TMSDataLayer(qbo, tms_api, browser)

    await layer.enrich_invoice("job-cache", invoice_data, force=True)
    await layer.get_document("job-cache", invoice_data, "POD", tmp_path)

    assert tms_api.get_work_order.await_count == 1, \
        "Second call to same WO# in same job must reuse cached fetch"


@pytest.mark.asyncio
async def test_reset_for_new_job_clears_wo_cache(tmp_path, invoice_data):
    qbo, tms_api, browser = MagicMock(), AsyncMock(), AsyncMock()
    tms_api.get_work_order.return_value = {"container_no": "X"}

    layer = TMSDataLayer(qbo, tms_api, browser)

    await layer.enrich_invoice("job-A", invoice_data, force=True)
    layer.reset_for_new_job("job-A")
    await layer.enrich_invoice("job-A", invoice_data, force=True)

    assert tms_api.get_work_order.await_count == 2


@pytest.mark.asyncio
async def test_different_jobs_do_not_share_cache(tmp_path, invoice_data):
    qbo, tms_api, browser = MagicMock(), AsyncMock(), AsyncMock()
    tms_api.get_work_order.return_value = {"container_no": "X"}
    layer = TMSDataLayer(qbo, tms_api, browser)

    await layer.enrich_invoice("job-A", invoice_data, force=True)
    await layer.enrich_invoice("job-B", invoice_data, force=True)

    assert tms_api.get_work_order.await_count == 2
