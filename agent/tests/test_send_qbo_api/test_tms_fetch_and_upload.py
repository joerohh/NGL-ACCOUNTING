"""Tests for SendQBOApiMixin._tms_fetch_and_upload_missing_docs after the M2 correction.

The cascade is now unconditional for non-OEC (no requiredDocs gate at the call site).
This module verifies the rewritten method body:
  - calls TMSDataLayer.get_all_documents (not get_documents)
  - dedupes downloaded docs against existing QBO attachments by docType
  - skips the "invoice" doc type
  - uploads only the missing doc types to QBO
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from services.job_manager.send_qbo_api import SendQBOApiMixin


def _make_mixin(tms_data, send_event=None):
    """Build a minimal mixin instance with the dependencies the method touches."""
    m = SendQBOApiMixin()
    m._tms_data = tms_data
    m._emit_send = AsyncMock() if send_event is None else send_event
    m._emit_failed_rows_changed = AsyncMock()
    return m


def _make_invoice(invoice_number="LM26040724F", container="HASU4865550"):
    inv = MagicMock()
    inv.invoice_number = invoice_number
    inv.container_number = container
    return inv


def _make_job():
    job = MagicMock()
    job.id = "job-correction"
    return job


@pytest.mark.asyncio
async def test_uploads_all_tms_docs_when_qbo_has_none(tmp_path):
    """No existing QBO attachments → every TMS doc is uploaded (POD + DO + IT + ITE)."""
    pod_path = tmp_path / "LM2604130046_pod.pdf"; pod_path.write_bytes(b"POD")
    do_path = tmp_path / "LM2604130046_do.pdf"; do_path.write_bytes(b"DO")
    it_path = tmp_path / "LM2604130046_it.pdf"; it_path.write_bytes(b"IT")
    ite_path = tmp_path / "LM2604130046_ite.pdf"; ite_path.write_bytes(b"ITE")

    tms_data = MagicMock()
    tms_data.get_failed_rows = MagicMock(return_value=[])
    tms_data.get_all_documents = AsyncMock(return_value={
        "pod": pod_path, "do": do_path, "it": it_path, "ite": ite_path,
    })

    api = MagicMock()
    api.upload_attachment = AsyncMock(return_value=True)

    mixin = _make_mixin(tms_data)
    uploaded = await mixin._tms_fetch_and_upload_missing_docs(
        job=_make_job(), invoice=_make_invoice(), api=api, invoice_id="123",
        verification={"found_container": "HASU4865550"}, temp_dir=tmp_path,
        invoice_data={"DocNumber": "LM26040724F"}, existing_attachments=[],
    )

    assert sorted(uploaded) == ["do", "it", "ite", "pod"]
    assert api.upload_attachment.await_count == 4


@pytest.mark.asyncio
async def test_dedupes_against_existing_qbo_attachments(tmp_path):
    """If QBO already has POD, only the other TMS docs are uploaded."""
    pod_path = tmp_path / "LM2604130046_pod.pdf"; pod_path.write_bytes(b"POD")
    do_path = tmp_path / "LM2604130046_do.pdf"; do_path.write_bytes(b"DO")

    tms_data = MagicMock()
    tms_data.get_failed_rows = MagicMock(return_value=[])
    tms_data.get_all_documents = AsyncMock(return_value={
        "pod": pod_path, "do": do_path,
    })

    api = MagicMock()
    api.upload_attachment = AsyncMock(return_value=True)

    existing = [{"docType": "pod", "fileName": "Existing POD.pdf"}]

    mixin = _make_mixin(tms_data)
    uploaded = await mixin._tms_fetch_and_upload_missing_docs(
        job=_make_job(), invoice=_make_invoice(), api=api, invoice_id="123",
        verification={}, temp_dir=tmp_path,
        invoice_data={"DocNumber": "LM26040724F"}, existing_attachments=existing,
    )

    assert uploaded == ["do"]
    assert api.upload_attachment.await_count == 1
    # The path actually uploaded was the DO, not the POD
    args, _ = api.upload_attachment.call_args
    assert args[1] == do_path


@pytest.mark.asyncio
async def test_skips_invoice_doc_type(tmp_path):
    """Even if TMS returns an 'invoice' type, it is NEVER uploaded back to QBO."""
    inv_path = tmp_path / "LM2604130046_invoice.pdf"; inv_path.write_bytes(b"INV")
    pod_path = tmp_path / "LM2604130046_pod.pdf"; pod_path.write_bytes(b"POD")

    tms_data = MagicMock()
    tms_data.get_failed_rows = MagicMock(return_value=[])
    tms_data.get_all_documents = AsyncMock(return_value={
        "invoice": inv_path, "pod": pod_path,
    })

    api = MagicMock()
    api.upload_attachment = AsyncMock(return_value=True)

    mixin = _make_mixin(tms_data)
    uploaded = await mixin._tms_fetch_and_upload_missing_docs(
        job=_make_job(), invoice=_make_invoice(), api=api, invoice_id="123",
        verification={}, temp_dir=tmp_path,
        invoice_data={"DocNumber": "LM26040724F"}, existing_attachments=[],
    )

    assert uploaded == ["pod"]
    assert api.upload_attachment.await_count == 1
