"""Verify the merge-tool fetch job falls back to TMS when QBO has no POD."""

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
    jm._emit = AsyncMock()
    return jm


@pytest.mark.asyncio
async def test_pod_fallback_returns_pod_first(tmp_path):
    """If TMS has POD, the chain stops there and returns 'POD'."""
    layer = MagicMock()

    async def fake_get_document(job_id, invoice_data, doc_type, dest_dir, source="api"):
        if doc_type == "POD":
            p = Path(dest_dir) / f"WO1_{doc_type}.pdf"
            p.write_bytes(b"%PDF-pod")
            return p
        return None

    layer.get_document = AsyncMock(side_effect=fake_get_document)

    jm = _make_jm(layer)
    job = MagicMock(id="j1")
    container = MagicMock(container_number="ABCU1234567")
    dest = tmp_path / "ABCU1234567_pod.pdf"

    result = await jm._tms_pod_fallback(job, container, {"Id": "1"}, dest)

    assert result == "POD"
    assert dest.exists()
    assert dest.read_bytes() == b"%PDF-pod"
    # POD succeeded, BOL/POL never tried
    assert layer.get_document.call_count == 1


@pytest.mark.asyncio
async def test_pod_fallback_falls_through_to_bol_for_export(tmp_path):
    """Export WOs lack POD but have BOL — chain should reach and return 'BOL'."""
    layer = MagicMock()

    async def fake_get_document(job_id, invoice_data, doc_type, dest_dir, source="api"):
        if doc_type == "BOL":
            p = Path(dest_dir) / f"LE_{doc_type}.pdf"
            p.write_bytes(b"%PDF-bol")
            return p
        return None

    layer.get_document = AsyncMock(side_effect=fake_get_document)

    jm = _make_jm(layer)
    job = MagicMock(id="j1")
    container = MagicMock(container_number="EXPU0000001")
    dest = tmp_path / "EXPU0000001_pod.pdf"

    result = await jm._tms_pod_fallback(job, container, {"Id": "2"}, dest)

    assert result == "BOL"
    assert dest.exists()
    assert dest.read_bytes() == b"%PDF-bol"
    # POD attempted, BOL succeeded — no POL call
    assert layer.get_document.call_count == 2


@pytest.mark.asyncio
async def test_pod_fallback_falls_through_to_pol(tmp_path):
    """If only POL exists, chain reaches the third doc type and uses it."""
    layer = MagicMock()

    async def fake_get_document(job_id, invoice_data, doc_type, dest_dir, source="api"):
        if doc_type == "POL":
            p = Path(dest_dir) / f"LE_{doc_type}.pdf"
            p.write_bytes(b"%PDF-pol")
            return p
        return None

    layer.get_document = AsyncMock(side_effect=fake_get_document)

    jm = _make_jm(layer)
    job = MagicMock(id="j1")
    container = MagicMock(container_number="EXPU0000002")
    dest = tmp_path / "EXPU0000002_pod.pdf"

    result = await jm._tms_pod_fallback(job, container, {"Id": "3"}, dest)

    assert result == "POL"
    assert layer.get_document.call_count == 3


@pytest.mark.asyncio
async def test_pod_fallback_returns_none_when_nothing_found(tmp_path):
    """All three doc types missing → None, dest not created."""
    layer = MagicMock()
    layer.get_document = AsyncMock(return_value=None)

    jm = _make_jm(layer)
    job = MagicMock(id="j1")
    container = MagicMock(container_number="MISS0000001")
    dest = tmp_path / "MISS0000001_pod.pdf"

    result = await jm._tms_pod_fallback(job, container, {"Id": "4"}, dest)

    assert result is None
    assert not dest.exists()
    assert layer.get_document.call_count == 3


@pytest.mark.asyncio
async def test_pod_fallback_returns_none_when_layer_not_configured(tmp_path):
    """Without _tms_data injected, fallback is a no-op."""
    jm = _make_jm(layer=None)
    jm._tms_data = None  # explicit
    job = MagicMock(id="j1")
    container = MagicMock(container_number="NOTMS00001")
    dest = tmp_path / "NOTMS00001_pod.pdf"

    result = await jm._tms_pod_fallback(job, container, {"Id": "5"}, dest)

    assert result is None
    assert not dest.exists()


def _wo_invoice(wo_no: str) -> dict:
    """Build the minimal QBO invoice shape that extract_wo_from_qbo expects."""
    return {"CustomField": [{"Name": "NGL REF#", "StringValue": f"{wo_no}/CUSTREF"}]}


@pytest.mark.asyncio
async def test_pod_fallback_export_wo_skips_pod_uses_bol(tmp_path):
    """WO# containing X → export → tries BOL first; never asks for POD."""
    layer = MagicMock()
    calls: list[str] = []

    async def fake_get_document(job_id, invoice_data, doc_type, dest_dir, source="api"):
        calls.append(doc_type)
        if doc_type == "BOL":
            p = Path(dest_dir) / f"WO_{doc_type}.pdf"
            p.write_bytes(b"%PDF-bol")
            return p
        return None

    layer.get_document = AsyncMock(side_effect=fake_get_document)

    jm = _make_jm(layer)
    job = MagicMock(id="j1")
    container = MagicMock(container_number="EXPU0000001")
    dest = tmp_path / "EXPU0000001_pod.pdf"

    result = await jm._tms_pod_fallback(
        job, container, _wo_invoice("LX2604170040"), dest,
    )

    assert result == "BOL"
    assert dest.exists()
    assert "POD" not in calls
    assert calls == ["BOL"]


@pytest.mark.asyncio
async def test_pod_fallback_export_wo_falls_through_to_pol(tmp_path):
    """Export WO with no BOL still tries POL."""
    layer = MagicMock()
    calls: list[str] = []

    async def fake_get_document(job_id, invoice_data, doc_type, dest_dir, source="api"):
        calls.append(doc_type)
        if doc_type == "POL":
            p = Path(dest_dir) / f"WO_{doc_type}.pdf"
            p.write_bytes(b"%PDF-pol")
            return p
        return None

    layer.get_document = AsyncMock(side_effect=fake_get_document)

    jm = _make_jm(layer)
    job = MagicMock(id="j1")
    container = MagicMock(container_number="EXPU0000002")
    dest = tmp_path / "EXPU0000002_pod.pdf"

    result = await jm._tms_pod_fallback(
        job, container, _wo_invoice("LX2604170099"), dest,
    )

    assert result == "POL"
    assert calls == ["BOL", "POL"]
    assert "POD" not in calls


@pytest.mark.asyncio
async def test_pod_fallback_import_wo_only_tries_pod(tmp_path):
    """WO# containing M (and no X) → import → POD only; no BOL or POL probe."""
    layer = MagicMock()
    calls: list[str] = []

    async def fake_get_document(job_id, invoice_data, doc_type, dest_dir, source="api"):
        calls.append(doc_type)
        return None  # nothing on TMS

    layer.get_document = AsyncMock(side_effect=fake_get_document)

    jm = _make_jm(layer)
    job = MagicMock(id="j1")
    container = MagicMock(container_number="IMPU0000001")
    dest = tmp_path / "IMPU0000001_pod.pdf"

    result = await jm._tms_pod_fallback(
        job, container, _wo_invoice("LM2604170040"), dest,
    )

    assert result is None
    assert calls == ["POD"]


@pytest.mark.asyncio
async def test_pod_fallback_unknown_wo_tries_full_chain(tmp_path):
    """WO# with neither X nor M (e.g., 'LE…') → safety chain POD→BOL→POL."""
    layer = MagicMock()
    calls: list[str] = []

    async def fake_get_document(job_id, invoice_data, doc_type, dest_dir, source="api"):
        calls.append(doc_type)
        return None

    layer.get_document = AsyncMock(side_effect=fake_get_document)

    jm = _make_jm(layer)
    job = MagicMock(id="j1")
    container = MagicMock(container_number="UNKU0000001")
    dest = tmp_path / "UNKU0000001_pod.pdf"

    result = await jm._tms_pod_fallback(
        job, container, _wo_invoice("LE2603300055"), dest,
    )

    assert result is None
    assert calls == ["POD", "BOL", "POL"]


@pytest.mark.asyncio
async def test_pod_fallback_continues_past_timeout(tmp_path):
    """A TimeoutError on POD shouldn't abort the chain — should still try BOL."""
    import asyncio as _asyncio
    layer = MagicMock()

    async def fake_get_document(job_id, invoice_data, doc_type, dest_dir, source="api"):
        if doc_type == "POD":
            raise _asyncio.TimeoutError()
        if doc_type == "BOL":
            p = Path(dest_dir) / f"LE_{doc_type}.pdf"
            p.write_bytes(b"%PDF-bol")
            return p
        return None

    # Patch wait_for so the inner TimeoutError propagates without a real timeout.
    layer.get_document = AsyncMock(side_effect=fake_get_document)

    jm = _make_jm(layer)
    job = MagicMock(id="j1")
    container = MagicMock(container_number="TOUT0000001")
    dest = tmp_path / "TOUT0000001_pod.pdf"

    result = await jm._tms_pod_fallback(job, container, {"Id": "6"}, dest)

    assert result == "BOL"
    assert dest.exists()
