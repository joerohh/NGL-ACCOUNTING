"""Tests for the warehouse send flow."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.job_manager import JobManager, SendJob, SendRequest, SendResult
from services.qbo_api import QBOApiClient


def _make_job_and_invoice(subject="Warehouse Invoice LW260515P01 - Pacific Cold Storage Inc."):
    invoice = SendRequest(
        invoice_number="LW260515P01",
        container_number="",
        customer_code="PACCS01",
        subject=subject,
    )
    job = SendJob("send-wh-1", [invoice], test_mode=False)
    return job, invoice


def _make_customer():
    return {
        "code": "PACCS01",
        "name": "Pacific Cold Storage Inc.",
        "emails": ["billing@pacificcoldstorage.com"],
        "ccEmails": [],
        "bccEmails": [],
        "active": True,
        "sendMethod": "email",
    }


def _make_jm(api_mock):
    classifier = MagicMock()
    email_sender = MagicMock()
    email_sender.send_invoice_email = AsyncMock(return_value={"sent": True, "error": None})
    jm = JobManager(QBOApiClient(), classifier=classifier, email_sender=email_sender)
    jm._qbo_api = api_mock
    jm._emit_send = AsyncMock()
    return jm, email_sender


@pytest.mark.asyncio
async def test_warehouse_send_with_attachments(tmp_path) -> None:
    job, invoice = _make_job_and_invoice()
    customer = _make_customer()
    result = SendResult(invoice.invoice_number, "", invoice.customer_code)

    api = MagicMock()
    api.search_invoice = AsyncMock(return_value={"Id": "42", "DocNumber": "LW260515P01"})
    api.download_invoice_pdf = AsyncMock(return_value=b"%PDF-1.4 invoice")
    api.list_attachments = AsyncMock(return_value=[
        {"id": "1", "fileName": "Storage_Detail.xlsx", "docType": "other"},
        {"id": "2", "fileName": "Receiving_Report.pdf", "docType": "other"},
    ])

    async def _fake_download(att_id, fname, dest_dir, temp_download_uri=None):
        p = Path(dest_dir) / fname
        p.write_bytes(b"fake bytes")
        return p

    api.download_attachment = AsyncMock(side_effect=_fake_download)

    jm, email_sender = _make_jm(api)

    await jm._send_warehouse_invoice(job, invoice, customer, result, 0)

    assert result.status == "sent"
    assert result.routing_type == "warehouse"
    assert sorted([a["fileName"] for a in result.warehouse_attachments]) == \
        ["Receiving_Report.pdf", "Storage_Detail.xlsx"]
    assert result.warehouse_failures == []

    # Email was sent with the invoice PDF + both attachments (3 total).
    email_sender.send_invoice_email.assert_awaited_once()
    kwargs = email_sender.send_invoice_email.await_args.kwargs
    sent_filenames = sorted([a["filename"] for a in kwargs["attachments"]])
    assert sent_filenames == ["Receiving_Report.pdf", "Storage_Detail.xlsx", "invoice_LW260515P01.pdf"]
    assert kwargs["subject"] == "Warehouse Invoice LW260515P01 - Pacific Cold Storage Inc."
    assert kwargs["to"] == ["billing@pacificcoldstorage.com"]


@pytest.mark.asyncio
async def test_warehouse_send_with_no_attachments_blocks_send() -> None:
    job, invoice = _make_job_and_invoice()
    customer = _make_customer()
    result = SendResult(invoice.invoice_number, "", invoice.customer_code)

    api = MagicMock()
    api.search_invoice = AsyncMock(return_value={"Id": "42", "DocNumber": "LW260515P01"})
    api.download_invoice_pdf = AsyncMock(return_value=b"%PDF-1.4 invoice")
    api.list_attachments = AsyncMock(return_value=[])  # zero attachments
    api.download_attachment = AsyncMock()

    jm, email_sender = _make_jm(api)

    await jm._send_warehouse_invoice(job, invoice, customer, result, 0)

    assert result.status == "missing_docs"
    assert "Add at least one" in result.error
    email_sender.send_invoice_email.assert_not_awaited()
    api.download_attachment.assert_not_awaited()


@pytest.mark.asyncio
async def test_warehouse_send_invoice_not_found_in_qbo() -> None:
    job, invoice = _make_job_and_invoice()
    customer = _make_customer()
    result = SendResult(invoice.invoice_number, "", invoice.customer_code)

    api = MagicMock()
    api.search_invoice = AsyncMock(return_value=None)
    api.list_attachments = AsyncMock()

    jm, email_sender = _make_jm(api)

    await jm._send_warehouse_invoice(job, invoice, customer, result, 0)

    assert result.status == "error"
    assert "couldn't find invoice" in result.error.lower()
    api.list_attachments.assert_not_awaited()
    email_sender.send_invoice_email.assert_not_awaited()


@pytest.mark.asyncio
async def test_warehouse_send_partial_download_failure_blocks(tmp_path) -> None:
    """Per spec: any attachment download failure blocks the send."""
    job, invoice = _make_job_and_invoice()
    customer = _make_customer()
    result = SendResult(invoice.invoice_number, "", invoice.customer_code)

    api = MagicMock()
    api.search_invoice = AsyncMock(return_value={"Id": "42", "DocNumber": "LW260515P01"})
    api.download_invoice_pdf = AsyncMock(return_value=b"%PDF-1.4 invoice")
    api.list_attachments = AsyncMock(return_value=[
        {"id": "1", "fileName": "Storage_Detail.xlsx", "docType": "other"},
        {"id": "2", "fileName": "Receiving_Report.pdf", "docType": "other"},
    ])

    async def _fake_download(att_id, fname, dest_dir, temp_download_uri=None):
        if fname == "Receiving_Report.pdf":
            raise RuntimeError("QBO 503")
        p = Path(dest_dir) / fname
        p.write_bytes(b"ok")
        return p

    api.download_attachment = AsyncMock(side_effect=_fake_download)

    jm, email_sender = _make_jm(api)

    await jm._send_warehouse_invoice(job, invoice, customer, result, 0)

    assert result.status == "error"
    assert "Receiving_Report.pdf" in result.error
    assert len(result.warehouse_failures) == 1
    assert result.warehouse_failures[0]["fileName"] == "Receiving_Report.pdf"
    email_sender.send_invoice_email.assert_not_awaited()
