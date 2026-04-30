"""Verify send_qbo_api uses TMSDataLayer.get_all_documents instead of browser calls."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_layer():
    layer = MagicMock()
    layer.get_all_documents = AsyncMock(return_value={})
    layer.get_failed_rows = MagicMock(return_value=[])
    return layer


@pytest.mark.asyncio
async def test_fetch_and_upload_uses_tms_data_get_all_documents(mock_layer, tmp_path):
    """The send mixin calls TMSDataLayer.get_all_documents — never tms_browser.fetch_*."""
    from services.job_manager import JobManager
    from services.qbo_api import QBOApiClient

    jm = JobManager(QBOApiClient(), classifier=MagicMock())
    jm.set_tms_data(mock_layer)
    jm._tms = MagicMock(name="tms_browser_should_not_be_called")
    jm._emit_send = AsyncMock()
    jm._emit_failed_rows_changed = AsyncMock()

    job = MagicMock(id="job-1", test_mode=False)
    invoice = MagicMock(invoice_number="INV-1", container_number="ABCU0000001",
                        do_sender_email=None, customer_code="CUST")
    api = MagicMock()
    api.upload_attachment = AsyncMock(return_value=True)
    invoice_data = {"DocNumber": "INV-1", "Id": "1",
                    "CustomField": [{"Name": "NGL REF#", "StringValue": "WO/X"}]}
    verification = {"found_container": "ABCU0000001"}

    # Pretend the data layer returns 2 docs from TMS.
    mock_layer.get_all_documents.return_value = {
        "pod": tmp_path / "pod.pdf",
        "bl": tmp_path / "bl.pdf",
    }
    (tmp_path / "pod.pdf").write_bytes(b"%PDF-pod")
    (tmp_path / "bl.pdf").write_bytes(b"%PDF-bl")

    uploaded = await jm._tms_fetch_and_upload_missing_docs(
        job, invoice, api, "1", verification, tmp_path,
        invoice_data=invoice_data, existing_attachments=[],
    )

    assert mock_layer.get_all_documents.await_count == 1
    args, kwargs = mock_layer.get_all_documents.call_args
    # The call passes (job_id, invoice_data, dest_dir, source="api")
    assert kwargs.get("source") == "api"
    assert sorted(uploaded) == ["bl", "pod"]
    # Browser must not be touched on the API path.
    assert not jm._tms.fetch_doc_by_wo.called
    assert not jm._tms.fetch_pod_and_do_sender.called
