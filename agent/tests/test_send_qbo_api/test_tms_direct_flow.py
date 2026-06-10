"""End-to-end tests for the TMS-direct standard email send flow (2026-06-10).

The new flow:
  1. Fetch every TMS doc with a file_url
  2. If customer requires POD and TMS returned none -> status=pod_missing, no email
  3. If TMS unreachable -> status=tms_unreachable, no email
  4. Otherwise: email = QBO invoice PDF + every TMS doc
  5. NO uploads to QBO

These tests exercise SendJobMixin._send_qbo_api as it stands after Task 7.
"""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_jm(tms_return_paths=None, tms_reason="ok"):
    from services.job_manager import JobManager
    from services.qbo_api import QBOApiClient

    jm = JobManager(QBOApiClient(), classifier=MagicMock())
    jm._emit_send = AsyncMock()
    jm._emit_failed_rows_changed = AsyncMock()
    jm._write_audit_log = MagicMock()

    layer = MagicMock()
    layer.get_all_documents_with_reason = AsyncMock(
        return_value=(tms_return_paths or {}, tms_reason),
    )
    layer.get_failed_rows = MagicMock(return_value=[])
    jm.set_tms_data(layer)

    email_sender = MagicMock()
    email_sender.send_invoice_email = AsyncMock(return_value={"sent": True})
    jm._email_sender = email_sender

    return jm, layer, email_sender


def _make_invoice():
    inv = MagicMock(
        invoice_number="LM26060242F",
        container_number="TEMU7671557",
        customer_code="PROMAR01",
        amount=None,
        subject=None,
        do_sender_email=None,
        is_resend=False,
    )
    return inv


def _make_api(invoice_id="400638", invoice_pdf_bytes=b"%PDF-invoice"):
    api = MagicMock()
    api.search_invoice = AsyncMock(return_value={
        "Id": invoice_id, "DocNumber": "LM26060242F",
        "CustomField": [{"Name": "NGL REF#", "StringValue": "LM2605280007/ASN616574"}],
        "CustomerRef": {"name": "[PROMAR01] PRO-MART"},
        "TotalAmt": "100.00", "DueDate": "2026-07-05",
    })
    api.verify_invoice_details = AsyncMock(return_value={
        "verified": True, "found_container": "TEMU7671557",
    })
    api.download_invoice_pdf = AsyncMock(return_value=invoice_pdf_bytes)
    api.get_invoice_link = AsyncMock(return_value="https://qbo.example/pay")
    api.upload_attachment = AsyncMock(side_effect=AssertionError("upload_attachment must NOT be called"))
    return api


@pytest.mark.asyncio
async def test_email_includes_all_tms_docs_plus_invoice_pdf(tmp_path):
    """Happy path: TMS returns 4 docs, email payload has 5 items (4 TMS + invoice PDF)."""
    from services.job_manager import SendResult

    tms_paths = {}
    for dt in ("pod", "do", "it", "ite"):
        p = tmp_path / f"LM2605280007_{dt}.pdf"
        p.write_bytes(f"%PDF-{dt}".encode())
        tms_paths[dt] = p

    jm, layer, email_sender = _make_jm(tms_return_paths=tms_paths)
    jm._qbo_api = _make_api()
    customer = {
        "code": "PROMAR01", "name": "PRO-MART",
        "emails": ["accountspayable@promartinc.com"],
        "sendMethod": "email", "requiredDocs": [],
    }
    result = SendResult("LM26060242F", "TEMU7671557", "PROMAR01")
    job = MagicMock(id="job-1", test_mode=False)

    await jm._send_qbo_api(job, _make_invoice(), customer, result, 0)

    assert result.status == "sent"
    assert email_sender.send_invoice_email.await_count == 1
    call_kwargs = email_sender.send_invoice_email.await_args.kwargs
    attached_names = [a["filename"] for a in call_kwargs["attachments"]]
    assert "LM26060242F.pdf" in attached_names
    for dt in ("pod", "do", "it", "ite"):
        assert any(f"LM2605280007_{dt}.pdf" == n for n in attached_names), \
            f"missing {dt} in {attached_names}"
    assert sorted(result.attachments_emailed) == ["do", "it", "ite", "pod"]


@pytest.mark.asyncio
async def test_pod_missing_when_required_and_tms_has_no_pod(tmp_path):
    """Customer.required_docs has 'pod', TMS returns no POD -> status=pod_missing, no email."""
    from services.job_manager import SendResult

    tms_paths = {}
    for dt in ("do", "it"):
        p = tmp_path / f"LM2605280007_{dt}.pdf"
        p.write_bytes(b"%PDF-x")
        tms_paths[dt] = p

    jm, layer, email_sender = _make_jm(tms_return_paths=tms_paths)
    jm._qbo_api = _make_api()
    customer = {
        "code": "PROMAR01", "name": "PRO-MART",
        "emails": ["accountspayable@promartinc.com"],
        "sendMethod": "email", "requiredDocs": ["pod"],
    }
    result = SendResult("LM26060242F", "TEMU7671557", "PROMAR01")
    job = MagicMock(id="job-1", test_mode=False)

    await jm._send_qbo_api(job, _make_invoice(), customer, result, 0)

    assert result.status == "pod_missing"
    assert email_sender.send_invoice_email.await_count == 0
    assert "POD" in (result.error or "")


@pytest.mark.asyncio
async def test_tms_unreachable_holds_send(tmp_path):
    """TMS API failure -> status=tms_unreachable, no email."""
    from services.job_manager import SendResult

    jm, layer, email_sender = _make_jm(tms_return_paths={}, tms_reason="tms_unreachable")
    jm._qbo_api = _make_api()
    customer = {
        "code": "PROMAR01", "name": "PRO-MART",
        "emails": ["accountspayable@promartinc.com"],
        "sendMethod": "email", "requiredDocs": [],
    }
    result = SendResult("LM26060242F", "TEMU7671557", "PROMAR01")
    job = MagicMock(id="job-1", test_mode=False)

    await jm._send_qbo_api(job, _make_invoice(), customer, result, 0)

    assert result.status == "tms_unreachable"
    assert email_sender.send_invoice_email.await_count == 0
    assert "TMS" in (result.error or "")


@pytest.mark.asyncio
async def test_no_qbo_upload_attempted(tmp_path):
    """The new flow must never call upload_attachment. api.upload_attachment side_effect=AssertionError."""
    from services.job_manager import SendResult

    tms_paths = {}
    for dt in ("pod", "do"):
        p = tmp_path / f"LM2605280007_{dt}.pdf"
        p.write_bytes(b"%PDF-x")
        tms_paths[dt] = p

    jm, layer, email_sender = _make_jm(tms_return_paths=tms_paths)
    jm._qbo_api = _make_api()  # api.upload_attachment will raise AssertionError if called
    customer = {
        "code": "PROMAR01", "name": "PRO-MART",
        "emails": ["accountspayable@promartinc.com"],
        "sendMethod": "email", "requiredDocs": [],
    }
    result = SendResult("LM26060242F", "TEMU7671557", "PROMAR01")
    job = MagicMock(id="job-1", test_mode=False)

    await jm._send_qbo_api(job, _make_invoice(), customer, result, 0)

    assert result.status == "sent"


@pytest.mark.asyncio
async def test_wo_not_found_proceeds_when_pod_not_required(tmp_path):
    """TMS returns wo_not_found, customer doesn't require POD -> send the invoice PDF alone."""
    from services.job_manager import SendResult

    jm, layer, email_sender = _make_jm(tms_return_paths={}, tms_reason="wo_not_found")
    jm._qbo_api = _make_api()
    customer = {
        "code": "PROMAR01", "name": "PRO-MART",
        "emails": ["accountspayable@promartinc.com"],
        "sendMethod": "email", "requiredDocs": [],
    }
    result = SendResult("LM26060242F", "TEMU7671557", "PROMAR01")
    job = MagicMock(id="job-1", test_mode=False)

    await jm._send_qbo_api(job, _make_invoice(), customer, result, 0)

    assert result.status == "sent"
    assert email_sender.send_invoice_email.await_count == 1
    attached = email_sender.send_invoice_email.await_args.kwargs["attachments"]
    assert len(attached) == 1  # invoice PDF only
    assert result.attachments_emailed == []
