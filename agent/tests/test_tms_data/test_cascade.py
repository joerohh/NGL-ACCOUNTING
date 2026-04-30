"""Tests for cascade.run_enrich and run_document — TMS API path."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from services.tms_data.cascade import run_enrich


# Append new import for run_document tests below
# (imported inline in test functions per plan pattern)


# Helpers ────────────────────────────────────────────────────────────

def _qbo_invoice(*, wo=None, chassis=None, cnee=None) -> dict:
    """Build a minimal QBO invoice dict that the QBO extractors can parse."""
    custom_fields = []
    if wo:
        custom_fields.append({"Name": "NGL REF#", "StringValue": f"{wo}/CUST-REF"})
    if chassis:
        # _extract_chassis requires CONTAINER/CHASSIS slash format with a container
        # pattern in front. Use a realistic shape so the extractor returns chassis.
        custom_fields.append({"Name": "CNTR# / CHASSIS#", "StringValue": f"TGBU6571759/{chassis}"})
    invoice = {"CustomField": custom_fields}
    if cnee:
        invoice["CustomerMemo"] = {"value": f"Pickup\\nNGLPORT --> {cnee} --> NGLPORT"}
    return invoice


def _tms_wo(*, container=None, chassis=None, do_sender=None) -> dict:
    wo = {}
    if container:
        wo["container_no"] = container
    if chassis:
        wo["chassis_no"] = chassis
    if do_sender:
        wo["do_sender"] = [do_sender]
    return wo


# Tests ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_uses_qbo_when_complete():
    """When QBO has everything, no TMS API call is made."""
    invoice = _qbo_invoice(wo="LM2602170009", chassis="ABC1234", cnee="ACME")
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock()

    enriched, err = await run_enrich(invoice, tms_api)

    assert err is None
    assert enriched.wo_no == "LM2602170009"
    assert enriched.chassis == "ABC1234"
    assert enriched.sources["chassis"] == "qbo"
    tms_api.get_work_order.assert_not_called()


@pytest.mark.asyncio
async def test_falls_back_to_tms_api_for_missing_chassis():
    """QBO has WO# but no chassis. TMS API is called and chassis is filled in."""
    invoice = _qbo_invoice(wo="LM2602170009")  # no chassis
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value=_tms_wo(chassis="XYZ5678"))

    enriched, err = await run_enrich(invoice, tms_api)

    assert err is None
    assert enriched.chassis == "XYZ5678"
    assert enriched.sources["chassis"] == "tms_api"
    tms_api.get_work_order.assert_called_once_with("LM2602170009")


@pytest.mark.asyncio
async def test_no_tms_call_when_no_wo_number():
    """Without a WO#, the TMS API can't be called at all."""
    invoice = _qbo_invoice()  # no WO#
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock()

    enriched, err = await run_enrich(invoice, tms_api)

    assert err is None
    assert enriched.wo_no is None
    assert enriched.sources["chassis"] == "missing"
    tms_api.get_work_order.assert_not_called()


@pytest.mark.asyncio
async def test_tms_api_returns_none_marks_missing():
    """TMS API returns None (404) — fields stay missing, no error."""
    invoice = _qbo_invoice(wo="LM2602170009")
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value=None)

    enriched, err = await run_enrich(invoice, tms_api)

    assert err is None  # 404 is not a "failure" — just no data
    assert enriched.chassis is None
    assert enriched.sources["chassis"] == "missing"


@pytest.mark.asyncio
async def test_tms_api_raises_returns_error():
    """TMS API raises an exception — returns error string."""
    invoice = _qbo_invoice(wo="LM2602170009")
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(side_effect=RuntimeError("DNS failed"))

    enriched, err = await run_enrich(invoice, tms_api)

    assert err is not None
    assert "DNS failed" in err
    assert enriched.chassis is None


# run_document tests ────────────────────────────────────────────────────

from services.tms_data.cascade import run_document


@pytest.mark.asyncio
async def test_run_document_downloads_pod(tmp_path):
    invoice = _qbo_invoice(wo="LM2602170009")
    wo = {
        "documents": [{"type_": "POD", "file_url": "https://cdn.example/pod.pdf"}],
    }
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value=wo)
    tms_api.download_document = AsyncMock(return_value=b"%PDF-1.4 sample bytes")

    path, err = await run_document(invoice, "POD", tmp_path, tms_api)

    assert err is None
    assert path is not None
    assert path.exists()
    assert path.read_bytes() == b"%PDF-1.4 sample bytes"
    tms_api.download_document.assert_called_once_with("https://cdn.example/pod.pdf")


@pytest.mark.asyncio
async def test_run_document_no_wo_returns_none(tmp_path):
    invoice = _qbo_invoice()  # no WO#
    tms_api = AsyncMock()

    path, err = await run_document(invoice, "POD", tmp_path, tms_api)

    assert path is None
    assert err is not None
    assert "WO" in err  # mentions missing WO#


@pytest.mark.asyncio
async def test_run_document_doc_type_not_in_wo_returns_none(tmp_path):
    invoice = _qbo_invoice(wo="LM2602170009")
    wo = {"documents": [{"type_": "BL", "file_url": "https://cdn.example/bl.pdf"}]}
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value=wo)

    path, err = await run_document(invoice, "POD", tmp_path, tms_api)

    assert path is None
    assert err is None  # no error — just no POD on this WO


@pytest.mark.asyncio
async def test_run_document_get_wo_raises_returns_error(tmp_path):
    invoice = _qbo_invoice(wo="LM2602170009")
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(side_effect=RuntimeError("network down"))

    path, err = await run_document(invoice, "POD", tmp_path, tms_api)

    assert path is None
    assert err is not None
    assert "network down" in err


@pytest.mark.asyncio
async def test_run_document_download_returns_none_is_error(tmp_path):
    """download_document returning None means CDN failed — that's an error."""
    invoice = _qbo_invoice(wo="LM2602170009")
    wo = {"documents": [{"type_": "POD", "file_url": "https://cdn.example/pod.pdf"}]}
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value=wo)
    tms_api.download_document = AsyncMock(return_value=None)

    path, err = await run_document(invoice, "POD", tmp_path, tms_api)

    assert path is None
    assert err is not None
    assert "download" in err.lower()


# force=True tests ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_enrich_force_calls_tms_even_when_qbo_complete():
    """force=True bypasses the QBO-complete short-circuit so do_sender is fetched."""
    # QBO already has chassis + cnee — without force, TMS wouldn't be called.
    invoice = _qbo_invoice(wo="LM2602170009", chassis="CHX9999", cnee="ACME")
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value=_tms_wo(
        container="TGBU6571759", do_sender="sender@example.com"
    ))

    enriched, err = await run_enrich(invoice, tms_api, force=True)

    assert err is None
    assert tms_api.get_work_order.await_count == 1, "force=True must hit TMS API"
    assert enriched.do_sender_email == "sender@example.com"
    assert enriched.sources["do_sender_email"] == "tms_api"


@pytest.mark.asyncio
async def test_run_enrich_default_skips_tms_when_qbo_complete():
    """Without force, QBO chassis+cnee means no TMS call (optimization preserved)."""
    invoice = _qbo_invoice(wo="LM2602170009", chassis="CHX9999", cnee="ACME")
    tms_api = AsyncMock()

    enriched, err = await run_enrich(invoice, tms_api)

    assert err is None
    assert tms_api.get_work_order.await_count == 0, "default path must skip TMS"
    assert enriched.do_sender_email is None


from services.tms_data.cascade import run_all_documents


@pytest.mark.asyncio
async def test_run_all_documents_downloads_every_file_url(tmp_path):
    """All docs with a file_url are downloaded; the dict is keyed by lowercased type_."""
    invoice = _qbo_invoice(wo="LM2604130046")
    wo = {
        "documents": [
            {"type_": "POD", "file_url": "https://tms/pod.pdf"},
            {"type_": "DO", "file_url": "https://tms/do.pdf"},
            {"type_": "IT", "file_url": "https://tms/it.pdf"},
            {"type_": "ITE", "file_url": "https://tms/ite.pdf"},
            {"type_": "MEMO", "file_url": ""},  # no file_url — must be skipped
        ],
    }
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value=wo)
    tms_api.download_document = AsyncMock(side_effect=[b"PODBYTES", b"DOBYTES", b"ITBYTES", b"ITEBYTES"])

    paths, per_doc_errors, top_error = await run_all_documents(invoice, tmp_path, tms_api)

    assert top_error is None
    assert per_doc_errors == {}
    assert set(paths.keys()) == {"pod", "do", "it", "ite"}
    assert paths["pod"].read_bytes() == b"PODBYTES"
    assert paths["pod"].name.endswith("_pod.pdf")
    tms_api.get_work_order.assert_awaited_once_with("LM2604130046")
    assert tms_api.download_document.await_count == 4


@pytest.mark.asyncio
async def test_run_all_documents_no_wo_returns_top_error(tmp_path):
    """If the QBO invoice has no WO#, no API calls happen and top_error is set."""
    invoice = _qbo_invoice()  # no WO
    tms_api = AsyncMock()

    paths, per_doc_errors, top_error = await run_all_documents(invoice, tmp_path, tms_api)

    assert paths == {}
    assert per_doc_errors == {}
    assert top_error is not None
    assert "WO" in top_error
    tms_api.get_work_order.assert_not_called()


@pytest.mark.asyncio
async def test_run_all_documents_partial_download_failure_records_per_doc(tmp_path):
    """One doc downloads fine, another fails — each tracked separately."""
    invoice = _qbo_invoice(wo="LM2604130046")
    wo = {
        "documents": [
            {"type_": "POD", "file_url": "https://tms/pod.pdf"},
            {"type_": "DO", "file_url": "https://tms/do.pdf"},
        ],
    }
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value=wo)
    tms_api.download_document = AsyncMock(side_effect=[b"PODBYTES", None])

    paths, per_doc_errors, top_error = await run_all_documents(invoice, tmp_path, tms_api)

    assert top_error is None
    assert set(paths.keys()) == {"pod"}
    assert "do" in per_doc_errors
    assert "no data" in per_doc_errors["do"].lower()


@pytest.mark.asyncio
async def test_run_all_documents_skip_types_filters_before_download(tmp_path):
    """skip_types in the cascade prevents downloading those types entirely."""
    invoice = _qbo_invoice(wo="LM2604130046")
    wo = {
        "documents": [
            {"type_": "POD", "file_url": "https://tms/pod.pdf"},
            {"type_": "DO", "file_url": "https://tms/do.pdf"},
            {"type_": "IT", "file_url": "https://tms/it.pdf"},
        ],
    }
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value=wo)
    tms_api.download_document = AsyncMock(return_value=b"BYTES")

    paths, per_doc_errors, top_error = await run_all_documents(
        invoice, tmp_path, tms_api, skip_types={"pod", "do"},
    )

    assert top_error is None
    assert per_doc_errors == {}
    assert set(paths.keys()) == {"it"}
    # Only IT should have been downloaded — skipped types never hit the network
    assert tms_api.download_document.await_count == 1
    args, _ = tms_api.download_document.call_args
    assert args[0] == "https://tms/it.pdf"


@pytest.mark.asyncio
async def test_run_all_documents_downloads_in_parallel(tmp_path):
    """Multiple downloads run concurrently via asyncio.gather, not serially."""
    import asyncio

    invoice = _qbo_invoice(wo="LM2604130046")
    wo = {
        "documents": [
            {"type_": "POD", "file_url": "https://tms/pod.pdf"},
            {"type_": "DO", "file_url": "https://tms/do.pdf"},
            {"type_": "IT", "file_url": "https://tms/it.pdf"},
        ],
    }

    # Track concurrency: each download takes 50ms; if serial, total >= 150ms.
    # Parallel via gather -> total <= ~80ms (with overhead).
    concurrent = 0
    max_concurrent = 0

    async def slow_download(url):
        nonlocal concurrent, max_concurrent
        concurrent += 1
        max_concurrent = max(max_concurrent, concurrent)
        await asyncio.sleep(0.05)
        concurrent -= 1
        return b"BYTES"

    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value=wo)
    tms_api.download_document = slow_download

    paths, per_doc_errors, top_error = await run_all_documents(invoice, tmp_path, tms_api)

    assert top_error is None
    assert set(paths.keys()) == {"pod", "do", "it"}
    assert max_concurrent >= 2, f"Expected concurrent downloads, got max={max_concurrent}"
