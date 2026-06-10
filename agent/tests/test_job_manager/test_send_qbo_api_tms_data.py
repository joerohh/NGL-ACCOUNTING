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


@pytest.mark.skip(
    reason="Obsolete (2026-06-10): the TMS-direct rewrite of _send_qbo_api removes "
    "the QBO-attachment listing + dedup step from this code path. dedupe_attachments "
    "is preserved dead code (still used by the warehouse path) but is no longer "
    "exercised from _send_qbo_api. See docs/superpowers/specs/2026-06-10-tms-direct-email-design.md."
)
@pytest.mark.asyncio
async def test_dedup_drops_5x_duplicate_pod_before_email(tmp_path, monkeypatch):
    """TMS-008: 5 duplicate POD Attachables → email_attachments has only the invoice PDF + 1 POD."""
    from services.job_manager import JobManager, SendResult
    from services.qbo_api import QBOApiClient

    jm = JobManager(QBOApiClient(), classifier=MagicMock())
    jm.set_tms_data(None)  # skip TMS cascade — only test the dedup at line ~178
    jm._tms = MagicMock(name="tms_browser_should_not_be_called")
    jm._email_sender = AsyncMock()
    jm._email_sender.send_invoice_email = AsyncMock(return_value={"sent": True})
    jm._emit_send = AsyncMock()
    jm._wait_for_approval = AsyncMock(return_value=True)
    jm._qbo_api = AsyncMock()
    jm._qbo_api.search_invoice = AsyncMock(return_value={
        "Id": "1", "DocNumber": "INV-1", "DueDate": "2026-05-15",
        "TotalAmt": 100, "CustomerRef": {"name": "[CODE] Test Customer"},
        "CustomField": [{"Name": "NGL REF#", "StringValue": "WO/Y"}],
    })
    jm._qbo_api.verify_invoice_details = AsyncMock(return_value={
        "verified": True, "found_container": "ABCU1",
    })
    # 5 duplicates of the same POD: same filename, same size, distinct IDs.
    duplicates = [
        {"id": str(1000 + i), "fileName": "mm2603020032_ite_1775833088165.pdf",
         "size": 13_312, "contentType": "application/pdf",
         "tempDownloadUri": None, "docType": "pod"}
        for i in range(5)
    ]
    jm._qbo_api.check_attachments = AsyncMock(return_value={
        "found": ["pod"], "missing": [], "allPresent": True,
        "attachments": duplicates,
    })
    jm._qbo_api.download_invoice_pdf = AsyncMock(return_value=b"%PDF-invoice")
    jm._qbo_api.get_invoice_link = AsyncMock(return_value="https://qbo.example/pay/1")

    # Mock httpx so the attachment download for the kept POD succeeds.
    import httpx
    class _FakeResp:
        status_code = 200
        content = b"%PDF-pod-data"
    class _FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, *a, **kw): return _FakeResp()
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    job = MagicMock(id="dup-1", test_mode=False)
    invoice = MagicMock(invoice_number="INV-1", container_number="ABCU1",
                        do_sender_email=None, customer_code="CUST",
                        amount=None, subject=None)
    customer = {"emails": ["customer@example.com"], "ccEmails": [],
                "bccEmails": [], "requiredDocs": [], "sendMethod": "qbo_api"}
    result = SendResult(invoice_number="INV-1", container_number="ABCU1",
                        customer_code="CUST")

    await jm._send_qbo_api(job, invoice, customer, result, 0)

    # Assert the email send was called with no duplicate filenames.
    assert jm._email_sender.send_invoice_email.await_count == 1
    sent_kwargs = jm._email_sender.send_invoice_email.call_args.kwargs
    email_attachments = sent_kwargs["attachments"]
    filenames = [a["filename"] for a in email_attachments]
    assert len(filenames) == len(set(filenames)), (
        f"email_attachments contains duplicate filenames: {filenames}"
    )
    # Specifically: invoice PDF + exactly one POD copy (5 dupes collapsed to 1).
    assert filenames.count("mm2603020032_ite_1775833088165.pdf") == 1

    # Assert the SSE 'attachments_deduped' event was emitted.
    emit_calls = [c for c in jm._emit_send.await_args_list
                  if len(c.args) >= 2 and c.args[1] == "attachments_deduped"]
    assert len(emit_calls) == 1
    payload = emit_calls[0].args[2]
    assert payload["invoiceNumber"] == "INV-1"
    assert payload["kept"] == 1
    assert payload["skipped"] == 4
    assert len(payload["skippedFiles"]) == 4
