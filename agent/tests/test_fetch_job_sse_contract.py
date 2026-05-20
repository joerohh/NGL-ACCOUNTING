"""SSE event contract tests for the fetch job.

These guard the agent → browser handoff: the merge tool's `buildInitialDocList`
in merge-v2.js reads `fetchResult.invoiceFile`, `fetchResult.podFile`, and
`fetchResult.warehouseAttachments` to decide which files to merge. Those
fields arrive over the SSE stream inside the `container_complete` event's
`result` payload. If the agent stops including any of them, the browser will
build an empty document list and every merge will silently produce zero files
(this exact bug shipped in v2.72 and was fixed in v2.74).

Each test below captures every event _emit() was called with and asserts the
container_complete payload carries the fields the browser depends on.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.job_manager import ContainerRequest, Job, JobManager, FetchResult


def _make_event_capture():
    """Return (mock, getter) — mock for _emit, getter returns list of events."""
    captured: list[dict] = []

    async def _capture(job, event_type, data):
        captured.append({"type": event_type, **data})

    return AsyncMock(side_effect=_capture), lambda: captured


def _find_event(events: list[dict], event_type: str) -> dict | None:
    for e in events:
        if e.get("type") == event_type:
            return e
    return None


def _build_job_manager(tmp_path: Path, classifier_doc_type: str = "invoice") -> tuple[JobManager, list[dict]]:
    """Return (job_manager, captured_events_list) wired up with stub QBO/TMS deps."""
    from services.qbo_api import QBOApiClient

    classifier = MagicMock()
    classification = MagicMock()
    classification.needs_review = False
    classification.doc_type = classifier_doc_type
    classification.confidence = 0.99
    classifier.classify = AsyncMock(return_value=classification)

    jm = JobManager(QBOApiClient(), classifier=classifier)
    jm._tms_data = None
    emit_mock, get_events = _make_event_capture()
    jm._emit = emit_mock
    return jm, get_events


@pytest.mark.asyncio
async def test_container_complete_carries_invoice_file_for_import_export(tmp_path: Path) -> None:
    """Import/export row: container_complete.result must include the invoiceFile and
    podFile fields the browser uses to find the downloaded PDFs on disk.

    Regression guard for v2.74 — these fields used to be in result.to_dict()
    but the browser ignored evt.result entirely, so merges produced zero files.
    """
    jm, get_events = _build_job_manager(tmp_path)

    fake_pdf = b"%PDF-1.4 fake invoice"
    api = MagicMock()
    api.search_invoice = AsyncMock(return_value={"Id": "42", "DocNumber": "LM2602170009"})
    api.download_invoice_pdf = AsyncMock(return_value=fake_pdf)
    # Return one POD attachment so the QBO path completes without TMS fallback.
    api.list_attachments = AsyncMock(return_value=[
        {"id": "9", "fileName": "pod.pdf", "docType": "pod", "tempDownloadUri": None},
    ])

    async def _fake_download(att_id, fname, dest_dir, temp_download_uri=None):
        p = Path(dest_dir) / fname
        p.write_bytes(b"%PDF-1.4 fake pod")
        return p

    api.download_attachment = AsyncMock(side_effect=_fake_download)
    jm._qbo_api = api

    container = ContainerRequest(container_number="ABCD1234567", invoice_number="LM2602170009")
    job = Job("ie-test-1", [container])
    job.download_dir = tmp_path
    job.download_dir.mkdir(parents=True, exist_ok=True)
    result = FetchResult(container.container_number, container.invoice_number)

    await jm._process_one_container(job, container, result)

    events = get_events()
    cc = _find_event(events, "container_complete")
    assert cc is not None, "agent must emit container_complete"
    payload = cc.get("result")
    assert payload is not None, "container_complete must include the full result payload"

    # These are the fields buildInitialDocList() in merge-v2.js reads.
    # Browser merges break silently if any of them goes missing.
    assert "invoiceFile" in payload, "container_complete.result must include invoiceFile"
    assert "podFile" in payload, "container_complete.result must include podFile"
    assert "podMissing" in payload, "container_complete.result must include podMissing"
    assert "warehouseAttachments" in payload, "container_complete.result must include warehouseAttachments"
    assert "warehouseFailures" in payload, "container_complete.result must include warehouseFailures"
    assert "routingType" in payload, "container_complete.result must include routingType"

    # And the values must be what the browser will need to find files on disk.
    assert payload["invoiceFile"] == "ABCD1234567_invoice.pdf"
    assert payload["podFile"] == "ABCD1234567_pod.pdf"
    assert payload["podMissing"] is False


@pytest.mark.asyncio
async def test_container_complete_carries_warehouse_attachments(tmp_path: Path) -> None:
    """Warehouse row: container_complete.result must include the warehouseAttachments
    array. The warehouse path emits NO pod_found/pod_missing, so this event is the
    only place fetchResult fields land — the browser code paths for warehouse rows
    depend entirely on this payload.
    """
    jm, get_events = _build_job_manager(tmp_path)

    fake_pdf = b"%PDF-1.4 fake invoice"
    api = MagicMock()
    api.search_invoice = AsyncMock(return_value={"Id": "1", "DocNumber": "LW260515P01"})
    api.download_invoice_pdf = AsyncMock(return_value=fake_pdf)
    api.list_attachments = AsyncMock(return_value=[
        {"id": "11", "fileName": "rate.pdf", "docType": "other", "tempDownloadUri": None},
    ])

    async def _fake_download(att_id, fname, dest_dir, temp_download_uri=None):
        p = Path(dest_dir) / fname
        p.write_bytes(b"%PDF-1.4 fake attachment")
        return p

    api.download_attachment = AsyncMock(side_effect=_fake_download)
    jm._qbo_api = api

    container = ContainerRequest(container_number="WH001", invoice_number="LW260515P01")
    job = Job("wh-test-1", [container])
    job.download_dir = tmp_path
    job.download_dir.mkdir(parents=True, exist_ok=True)
    result = FetchResult(container.container_number, container.invoice_number)

    await jm._process_one_container(job, container, result)

    events = get_events()
    cc = _find_event(events, "container_complete")
    assert cc is not None, "warehouse path must still emit container_complete"
    payload = cc.get("result")
    assert payload is not None, "container_complete must include the full result payload"

    assert payload.get("routingType") == "warehouse"
    assert payload.get("invoiceFile") == "WH001_invoice.pdf"
    assert isinstance(payload.get("warehouseAttachments"), list)
    assert len(payload["warehouseAttachments"]) == 1
    assert payload["warehouseAttachments"][0]["fileName"] == "rate.pdf"
    assert payload.get("warehouseFailures") == []
    # Warehouse rows do not have a separate POD — podFile stays None.
    assert payload.get("podFile") is None
    assert payload.get("podMissing") is False


@pytest.mark.asyncio
async def test_pod_found_event_includes_file_name(tmp_path: Path) -> None:
    """pod_found event must carry the renamed on-disk filename in `file`. The
    browser uses it as an early hint for the doc list while waiting for the
    final container_complete event."""
    jm, get_events = _build_job_manager(tmp_path)

    api = MagicMock()
    api.search_invoice = AsyncMock(return_value={"Id": "42", "DocNumber": "LM2602170009"})
    api.download_invoice_pdf = AsyncMock(return_value=b"%PDF-1.4 fake")
    api.list_attachments = AsyncMock(return_value=[
        {"id": "9", "fileName": "pod.pdf", "docType": "pod", "tempDownloadUri": None},
    ])

    async def _fake_download(att_id, fname, dest_dir, temp_download_uri=None):
        p = Path(dest_dir) / fname
        p.write_bytes(b"%PDF-1.4 fake pod")
        return p

    api.download_attachment = AsyncMock(side_effect=_fake_download)
    jm._qbo_api = api

    container = ContainerRequest(container_number="XYZU9876543", invoice_number="LM2602170009")
    job = Job("pod-test-1", [container])
    job.download_dir = tmp_path
    job.download_dir.mkdir(parents=True, exist_ok=True)
    result = FetchResult(container.container_number, container.invoice_number)

    await jm._process_one_container(job, container, result)

    events = get_events()
    pf = _find_event(events, "pod_found")
    assert pf is not None, "agent must emit pod_found when a POD is downloaded from QBO"
    assert pf.get("file") == "XYZU9876543_pod.pdf", (
        "pod_found must include the renamed on-disk filename — browser uses this "
        "as an early hint while waiting for container_complete"
    )
