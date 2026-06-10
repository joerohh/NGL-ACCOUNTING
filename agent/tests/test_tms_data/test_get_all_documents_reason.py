"""Verify get_all_documents_with_reason distinguishes tms_unreachable / wo_not_found / ok."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.tms_data import TMSDataLayer


@pytest.mark.asyncio
async def test_returns_unreachable_when_get_work_order_raises(tmp_path):
    tms_api = MagicMock()
    tms_api.get_work_order = AsyncMock(side_effect=RuntimeError("ConnectError"))
    layer = TMSDataLayer(qbo_api=None, tms_api=tms_api, tms_browser=None)

    paths, reason = await layer.get_all_documents_with_reason(
        job_id="j1",
        invoice_data={"CustomField": [{"Name": "NGL REF#", "StringValue": "LM2605280007/X"}]},
        dest_dir=tmp_path,
    )
    assert paths == {}
    assert reason == "tms_unreachable"


@pytest.mark.asyncio
async def test_returns_wo_not_found_when_get_work_order_returns_none(tmp_path):
    tms_api = MagicMock()
    tms_api.get_work_order = AsyncMock(return_value=None)
    layer = TMSDataLayer(qbo_api=None, tms_api=tms_api, tms_browser=None)

    paths, reason = await layer.get_all_documents_with_reason(
        job_id="j1",
        invoice_data={"CustomField": [{"Name": "NGL REF#", "StringValue": "LM2605280007/X"}]},
        dest_dir=tmp_path,
    )
    assert paths == {}
    assert reason == "wo_not_found"


@pytest.mark.asyncio
async def test_returns_ok_when_documents_returned(tmp_path):
    tms_api = MagicMock()
    tms_api.get_work_order = AsyncMock(return_value={
        "wo_no": "LM2605280007",
        "documents": [
            {"type_": "POD", "file_url": "https://x/pod.pdf"},
            {"type_": "DO", "file_url": "https://x/do.pdf"},
        ],
    })
    tms_api.download_document = AsyncMock(return_value=b"%PDF-content")
    layer = TMSDataLayer(qbo_api=None, tms_api=tms_api, tms_browser=None)

    paths, reason = await layer.get_all_documents_with_reason(
        job_id="j1",
        invoice_data={"CustomField": [{"Name": "NGL REF#", "StringValue": "LM2605280007/X"}]},
        dest_dir=tmp_path,
    )
    assert reason == "ok"
    assert set(paths.keys()) == {"pod", "do"}
