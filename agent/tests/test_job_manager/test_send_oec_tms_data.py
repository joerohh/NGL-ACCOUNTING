"""Verify OEC POD-email step uses TMSDataLayer and preserves hard invariants."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_jm(layer):
    from services.job_manager import JobManager
    from services.qbo_api import QBOApiClient

    jm = JobManager(QBOApiClient(), classifier=MagicMock())
    jm.set_tms_data(layer)
    jm._tms = MagicMock(name="tms_browser_should_not_be_called")
    jm._tms_api = MagicMock()
    jm._email_sender = AsyncMock()
    jm._email_sender.send_pod_email = AsyncMock(return_value={"sent": True})
    jm._emit_send = AsyncMock()
    jm._emit_failed_rows_changed = AsyncMock()
    jm._get_cached_do_sender = MagicMock(return_value=None)
    jm._save_do_sender_cache = MagicMock()
    jm._qbo_api = AsyncMock()
    return jm


@pytest.mark.asyncio
async def test_oec_calls_enrich_with_force_true(tmp_path):
    """OEC must pass force=True so do_sender is fetched even if QBO has chassis+CNEE."""
    from services.tms_data.enriched_invoice import EnrichedInvoice
    layer = MagicMock()
    layer.enrich_invoice = AsyncMock(return_value=EnrichedInvoice(
        wo_no="WO1", container_no="ABCU1", chassis="C/X", cnee="CN",
        do_sender_email="dosender@example.com",
        sources={"do_sender_email": "tms_api"},
    ))
    layer.get_document = AsyncMock(return_value=tmp_path / "pod.pdf")
    (tmp_path / "pod.pdf").write_bytes(b"%PDF")

    jm = _make_jm(layer)
    jm._qbo_api.search_invoice = AsyncMock(return_value={"Id": "1", "DocNumber": "INV-1"})
    jm._qbo_api.verify_invoice_details = AsyncMock(return_value={"found_container": "ABCU1"})
    jm._qbo_api.check_attachments = AsyncMock(return_value={"attachments": []})

    job = MagicMock(id="oec-1", test_mode=False)
    invoice = MagicMock(invoice_number="INV-1", container_number="ABCU1",
                        do_sender_email=None, customer_code="OEC", amount=None)
    customer = {
        "podEmailTo": ["pod@cust.com"], "podEmailCc": [],
        "podEmailSubject": "POD", "podEmailBody": "POD body",
    }
    from services.job_manager import SendResult
    result = SendResult(invoice_number="INV-1", container_number="ABCU1",
                        customer_code="OEC")

    await jm._send_oec_pod_email(job, invoice, customer, result, 0)

    args, kwargs = layer.enrich_invoice.call_args
    assert kwargs.get("force") is True or (len(args) > 3 and args[3] is True)


@pytest.mark.asyncio
async def test_oec_invoice_do_sender_email_set_from_layer(tmp_path):
    """Hard invariant #4: D/O sender from data layer must populate invoice.do_sender_email."""
    from services.tms_data.enriched_invoice import EnrichedInvoice
    layer = MagicMock()
    layer.enrich_invoice = AsyncMock(return_value=EnrichedInvoice(
        wo_no="WO1", container_no="ABCU1", chassis=None, cnee=None,
        do_sender_email="dosender@example.com",
        sources={"do_sender_email": "tms_api"},
    ))
    layer.get_document = AsyncMock(return_value=tmp_path / "pod.pdf")
    (tmp_path / "pod.pdf").write_bytes(b"%PDF")

    jm = _make_jm(layer)
    jm._qbo_api.search_invoice = AsyncMock(return_value={"Id": "1", "DocNumber": "INV-1"})
    jm._qbo_api.verify_invoice_details = AsyncMock(return_value={"found_container": "ABCU1"})
    jm._qbo_api.check_attachments = AsyncMock(return_value={"attachments": []})

    job = MagicMock(id="oec-2", test_mode=False)
    invoice = MagicMock(invoice_number="INV-1", container_number="ABCU1",
                        do_sender_email=None, customer_code="OEC", amount=None)
    customer = {
        "podEmailTo": ["pod@cust.com"], "podEmailCc": [],
        "podEmailSubject": "POD", "podEmailBody": "POD body",
    }
    from services.job_manager import SendResult
    result = SendResult(invoice_number="INV-1", container_number="ABCU1",
                        customer_code="OEC")

    await jm._send_oec_pod_email(job, invoice, customer, result, 0)

    assert invoice.do_sender_email == "dosender@example.com"


@pytest.mark.asyncio
async def test_oec_falls_back_to_cache_when_data_layer_returns_no_do_sender(tmp_path):
    """Hard invariant #11: D/O sender cache is the final fallback."""
    from services.tms_data.enriched_invoice import EnrichedInvoice
    layer = MagicMock()
    layer.enrich_invoice = AsyncMock(return_value=EnrichedInvoice(
        wo_no="WO1", container_no="ABCU1", chassis=None, cnee=None,
        do_sender_email=None, sources={"do_sender_email": "missing"},
    ))
    layer.get_document = AsyncMock(return_value=tmp_path / "pod.pdf")
    (tmp_path / "pod.pdf").write_bytes(b"%PDF")

    jm = _make_jm(layer)
    jm._get_cached_do_sender = MagicMock(return_value="cached@example.com")
    jm._qbo_api.search_invoice = AsyncMock(return_value={"Id": "1", "DocNumber": "INV-1"})
    jm._qbo_api.verify_invoice_details = AsyncMock(return_value={"found_container": "ABCU1"})
    jm._qbo_api.check_attachments = AsyncMock(return_value={"attachments": []})

    job = MagicMock(id="oec-3", test_mode=False)
    invoice = MagicMock(invoice_number="INV-1", container_number="ABCU1",
                        do_sender_email=None, customer_code="OEC", amount=None)
    customer = {
        "podEmailTo": ["pod@cust.com"], "podEmailCc": [],
        "podEmailSubject": "POD", "podEmailBody": "POD body",
    }
    from services.job_manager import SendResult
    result = SendResult(invoice_number="INV-1", container_number="ABCU1",
                        customer_code="OEC")

    await jm._send_oec_pod_email(job, invoice, customer, result, 0)

    assert invoice.do_sender_email == "cached@example.com"
