"""Per-invoice retry — self-contained recovery flow with background QBO upload."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.job_manager import JobManager
from services.qbo_api import QBOApiClient


def _make_mgr():
    """Build a JobManager with mocked dependencies."""
    qbo = MagicMock(spec=QBOApiClient)
    qbo.search_invoice = AsyncMock(return_value={"Id": "qbo-123", "DocNumber": "LM26040864F"})
    qbo.download_invoice_pdf = AsyncMock(return_value=b"%PDF-1.4\ninvoice bytes")
    qbo.upload_attachment = AsyncMock(return_value={"Id": "att-1"})

    email = MagicMock()
    email.send_invoice_email = AsyncMock(return_value={"sent": True, "error": None})

    classifier = MagicMock()

    mgr = JobManager(qbo_api=qbo, classifier=classifier, email_sender=email)
    return mgr, qbo, email


@pytest.fixture
def fake_customer():
    return {
        "code": "APEXMA01", "name": "APEX MARITIME",
        "emails": ["ap@apex.com"],
        "ccEmails": ["billing@apex.com"], "bccEmails": [],
        "requiredDocs": ["pod"], "sendMethod": "email", "active": True,
    }


def test_retry_invoice_sends_with_caller_files(tmp_path, fake_customer):
    """Retry attaches caller-supplied files inline + the invoice PDF; sends email."""
    pod = tmp_path / "pod.pdf"
    pod.write_bytes(b"%PDF-1.4\nfake pod")

    mgr, qbo, email = _make_mgr()

    with patch("services.job_manager.retry_invoice.get_customer", return_value=fake_customer):
        result = asyncio.run(mgr.retry_invoice(
            invoice={
                "invoice_number": "LM26040864F",
                "invoice_id": "qbo-123",
                "container_number": "TRHU4593053",
                "customer_code": "APEXMA01",
                "amount": "1250.00",
                "subject": None,  # let helper build default
                "do_sender_email": None,
            },
            files=[{"slot": "POD", "path": str(pod), "verified_by": "filename"}],
        ))

    assert result["status"] == "sent"
    # Email was sent
    email.send_invoice_email.assert_called_once()
    call_kwargs = email.send_invoice_email.call_args.kwargs or email.send_invoice_email.call_args[1]
    # Verify CC includes ar@ngltrans.net + customer cc
    assert "ar@ngltrans.net" in call_kwargs["cc"]
    assert "billing@apex.com" in call_kwargs["cc"]
    # Verify To went to customer primary email
    assert "ap@apex.com" in call_kwargs["to"]
    # Verify attachments include invoice PDF + the dropped POD
    attachments = call_kwargs["attachments"]
    assert len(attachments) == 2  # invoice + POD
    filenames = [a["filename"] for a in attachments]
    assert any("LM26040864F" in f for f in filenames)  # invoice
    assert "pod.pdf" in filenames


def test_retry_invoice_bypasses_required_docs_gate(tmp_path, fake_customer):
    """Even though customer requires POD, retry trusts the caller-supplied file."""
    pod = tmp_path / "pod.pdf"
    pod.write_bytes(b"%PDF-1.4\nfake")
    mgr, qbo, email = _make_mgr()

    with patch("services.job_manager.retry_invoice.get_customer", return_value=fake_customer):
        result = asyncio.run(mgr.retry_invoice(
            invoice={
                "invoice_number": "LM26040864F",
                "invoice_id": "qbo-123",
                "container_number": "TRHU4593053",
                "customer_code": "APEXMA01",
                "amount": "1250.00",
                "subject": None, "do_sender_email": None,
            },
            files=[{"slot": "POD", "path": str(pod), "verified_by": "filename"}],
        ))

    assert result["status"] == "sent"
    # No "missing_docs" gate fired
    email.send_invoice_email.assert_called_once()


def test_retry_schedules_background_qbo_upload(tmp_path, fake_customer):
    """After email success, retry kicks off QBO upload as fire-and-forget."""
    pod = tmp_path / "pod.pdf"
    pod.write_bytes(b"%PDF-1.4\nfake")
    mgr, qbo, email = _make_mgr()

    async def run():
        with patch("services.job_manager.retry_invoice.get_customer", return_value=fake_customer):
            result = await mgr.retry_invoice(
                invoice={
                    "invoice_number": "LM26040864F",
                    "invoice_id": "qbo-123",
                    "container_number": "TRHU4593053",
                    "customer_code": "APEXMA01",
                    "amount": "1250.00",
                    "subject": None, "do_sender_email": None,
                },
                files=[{"slot": "POD", "path": str(pod), "verified_by": "filename"}],
            )
            # Let any scheduled background task finish
            await asyncio.sleep(0.1)
            return result

    result = asyncio.run(run())
    assert result["status"] == "sent"
    qbo.upload_attachment.assert_called_once()
    call = qbo.upload_attachment.call_args
    assert call.kwargs.get("invoice_id") == "qbo-123" or call.args[0] == "qbo-123"


def test_retry_email_failure_returns_error(tmp_path, fake_customer):
    """If email send fails, retry returns error status."""
    pod = tmp_path / "pod.pdf"
    pod.write_bytes(b"%PDF-1.4\nfake")
    mgr, qbo, email = _make_mgr()
    email.send_invoice_email = AsyncMock(return_value={"sent": False, "error": "SMTP timeout"})

    with patch("services.job_manager.retry_invoice.get_customer", return_value=fake_customer):
        result = asyncio.run(mgr.retry_invoice(
            invoice={
                "invoice_number": "LM26040864F",
                "invoice_id": "qbo-123",
                "container_number": "TRHU4593053",
                "customer_code": "APEXMA01",
                "amount": "1250.00",
                "subject": None, "do_sender_email": None,
            },
            files=[{"slot": "POD", "path": str(pod), "verified_by": "filename"}],
        ))

    assert result["status"] == "error"
    assert "SMTP timeout" in (result.get("error") or "")
    # Background upload should NOT be scheduled if email failed
    qbo.upload_attachment.assert_not_called()


def test_retry_customer_not_found_returns_error(tmp_path):
    """If customer code lookup fails, retry returns error without attempting send."""
    pod = tmp_path / "pod.pdf"
    pod.write_bytes(b"%PDF-1.4\nfake")
    mgr, qbo, email = _make_mgr()

    with patch("services.job_manager.retry_invoice.get_customer", return_value=None):
        result = asyncio.run(mgr.retry_invoice(
            invoice={
                "invoice_number": "LM26040864F",
                "invoice_id": "qbo-123",
                "container_number": "TRHU4593053",
                "customer_code": "UNKNOWN99",
                "amount": "1250.00",
                "subject": None, "do_sender_email": None,
            },
            files=[{"slot": "POD", "path": str(pod), "verified_by": "filename"}],
        ))

    assert result["status"] == "error"
    email.send_invoice_email.assert_not_called()
