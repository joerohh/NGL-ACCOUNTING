"""Unit tests for the warehouse routing helper in fetch_job.

Integration tests against live QBO live in Task 21.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.job_manager import ContainerRequest, Job, JobManager, FetchResult
from services.job_manager.fetch_job import _is_warehouse_row


def test_warehouse_row_detected() -> None:
    assert _is_warehouse_row("LW260515P01") is True


def test_warehouse_row_case_insensitive() -> None:
    assert _is_warehouse_row("lw260515p01") is True


def test_import_row_not_warehouse() -> None:
    assert _is_warehouse_row("LM2602170009") is False


def test_export_row_not_warehouse() -> None:
    assert _is_warehouse_row("LE2602170011") is False


def test_empty_string_not_warehouse() -> None:
    assert _is_warehouse_row("") is False


def test_none_not_warehouse() -> None:
    assert _is_warehouse_row(None) is False


def test_single_char_not_warehouse() -> None:
    # Too short — INV# pos-2 doesn't exist.
    assert _is_warehouse_row("W") is False


def test_w_in_pos_1_not_warehouse() -> None:
    # Warehouse rule is position-2 only, not position-1.
    assert _is_warehouse_row("WM123") is False


@pytest.mark.asyncio
async def test_warehouse_row_downloads_invoice_then_attachments(tmp_path) -> None:
    """Warehouse row: invoice PDF still downloads (Step 2), THEN attachments (Step 2.5).

    Confirms the v2.72 fix — the warehouse branch must NOT short-circuit
    before the invoice PDF is on disk. After processing, the result should
    have both invoice_file set AND warehouse_attachments populated.
    """
    from services.qbo_api import QBOApiClient

    container = ContainerRequest(
        container_number="WH001", invoice_number="LW260515P01",
    )
    job = Job("wh-test-1", [container])

    # Mock classifier — returns a non-review-needed result so we don't trip review path.
    classifier = MagicMock()
    classification = MagicMock()
    classification.needs_review = False
    classification.doc_type = "invoice"
    classification.confidence = 0.99
    classifier.classify = AsyncMock(return_value=classification)

    jm = JobManager(QBOApiClient(), classifier=classifier)
    jm._tms_data = None  # warehouse skips TMS entirely
    jm._emit = AsyncMock()

    # Mock the QBO API surface used by _process_one_container.
    fake_pdf_bytes = b"%PDF-1.4 fake invoice"
    api = MagicMock()
    api.search_invoice = AsyncMock(return_value={"Id": "123", "DocNumber": "LW260515P01"})
    api.download_invoice_pdf = AsyncMock(return_value=fake_pdf_bytes)
    api.list_attachments = AsyncMock(return_value=[
        {"id": "1", "fileName": "rate.pdf", "docType": "other", "tempDownloadUri": None},
    ])

    # download_attachment writes a fake file to the requested dir and returns the path.
    async def _fake_download(att_id, fname, dest_dir, temp_download_uri=None):
        from pathlib import Path
        p = Path(dest_dir) / fname
        p.write_bytes(b"%PDF-1.4 fake attachment")
        return p

    api.download_attachment = AsyncMock(side_effect=_fake_download)
    jm._qbo_api = api

    # Redirect job.download_dir to tmp_path so we don't pollute real DOWNLOADS_DIR.
    job.download_dir = tmp_path
    job.download_dir.mkdir(parents=True, exist_ok=True)

    result = FetchResult(container.container_number, container.invoice_number)
    # doc_types defaults to invoice+pod; warehouse branch should still run.
    await jm._process_one_container(job, container, result)

    # Invoice PDF should have been fetched FIRST (Step 2), before warehouse short-circuit.
    assert result.invoice_file == "WH001_invoice.pdf", (
        f"invoice_file should be set by Step 2 download, got {result.invoice_file!r}"
    )
    assert (tmp_path / "WH001_invoice.pdf").exists()

    # Warehouse attachments should also be populated (Step 2.5).
    assert result.routing_type == "warehouse"
    assert result.pod_label == "Warehouse"
    assert len(result.warehouse_attachments) == 1
    assert result.warehouse_attachments[0]["fileName"] == "rate.pdf"
    assert result.warehouse_failures == []

    # POD path must NOT have run — pod_file stays None, pod_missing stays False.
    assert result.pod_file is None
    assert result.pod_missing is False
