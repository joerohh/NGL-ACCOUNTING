"""Tests for the public TMSDataLayer class."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from services.tms_data import TMSDataLayer


def _qbo_invoice(*, wo=None, chassis=None) -> dict:
    custom_fields = []
    if wo:
        custom_fields.append({"Name": "NGL REF#", "StringValue": f"{wo}/CUST-REF"})
    if chassis:
        # Use realistic CONTAINER/CHASSIS slash format that _extract_chassis parses.
        custom_fields.append({"Name": "CNTR# / CHASSIS#", "StringValue": f"TGBU6571759/{chassis}"})
    return {"CustomField": custom_fields}


@pytest.mark.asyncio
async def test_enrich_invoice_uses_api_by_default():
    qbo_api = AsyncMock()
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value={"chassis_no": "FROMTMS"})
    tms_browser = AsyncMock()

    dl = TMSDataLayer(qbo_api, tms_api, tms_browser)
    enriched = await dl.enrich_invoice("job-1", _qbo_invoice(wo="LM01"))

    assert enriched.chassis == "FROMTMS"
    assert enriched.sources["chassis"] == "tms_api"
    tms_browser.fetch_detail_info.assert_not_called()


@pytest.mark.asyncio
async def test_enrich_invoice_uses_browser_when_requested():
    qbo_api = AsyncMock()
    tms_api = AsyncMock()
    tms_browser = AsyncMock()
    tms_browser.fetch_detail_info = AsyncMock(return_value={"chassis": "FROMBROWSER"})

    dl = TMSDataLayer(qbo_api, tms_api, tms_browser)
    enriched = await dl.enrich_invoice("job-1", _qbo_invoice(wo="LM01"), source="browser")

    assert enriched.chassis == "FROMBROWSER"
    assert enriched.sources["chassis"] == "tms_browser"
    tms_api.get_work_order.assert_not_called()


@pytest.mark.asyncio
async def test_enrich_invoice_records_failure_in_failed_rows():
    qbo_api = AsyncMock()
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(side_effect=RuntimeError("DNS fail"))
    tms_browser = AsyncMock()

    dl = TMSDataLayer(qbo_api, tms_api, tms_browser)
    invoice = _qbo_invoice(wo="LM01")
    invoice["DocNumber"] = "LM26040454F"

    enriched = await dl.enrich_invoice("job-1", invoice)

    rows = dl.get_failed_rows("job-1")
    assert len(rows) == 1
    assert rows[0].invoice_number == "LM26040454F"
    assert rows[0].operation == "enrich_invoice"
    assert rows[0].failed_at_source == "tms_api"
    assert "DNS fail" in rows[0].error_message


@pytest.mark.asyncio
async def test_get_document_via_api(tmp_path):
    qbo_api = AsyncMock()
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value={
        "documents": [{"type_": "POD", "file_url": "https://cdn.example/pod.pdf"}],
    })
    tms_api.download_document = AsyncMock(return_value=b"pod data")
    tms_browser = AsyncMock()

    dl = TMSDataLayer(qbo_api, tms_api, tms_browser)
    invoice = _qbo_invoice(wo="LM01")
    invoice["DocNumber"] = "LM26040454F"

    path = await dl.get_document("job-1", invoice, "POD", tmp_path)

    assert path is not None
    assert path.read_bytes() == b"pod data"
    assert dl.get_failed_rows("job-1") == []


@pytest.mark.asyncio
async def test_get_document_records_failure_on_api_error(tmp_path):
    qbo_api = AsyncMock()
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(side_effect=RuntimeError("network"))
    tms_browser = AsyncMock()

    dl = TMSDataLayer(qbo_api, tms_api, tms_browser)
    invoice = _qbo_invoice(wo="LM01")
    invoice["DocNumber"] = "LM26040454F"

    path = await dl.get_document("job-1", invoice, "POD", tmp_path)

    assert path is None
    rows = dl.get_failed_rows("job-1")
    assert len(rows) == 1
    assert rows[0].operation == "get_document"
    assert rows[0].doc_type == "POD"


@pytest.mark.asyncio
async def test_get_documents_returns_dict_of_found(tmp_path):
    qbo_api = AsyncMock()
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value={
        "documents": [
            {"type_": "POD", "file_url": "https://cdn.example/pod.pdf"},
            {"type_": "BL", "file_url": "https://cdn.example/bl.pdf"},
        ],
    })

    async def _download(url):
        return b"pod" if "pod" in url else b"bl"
    tms_api.download_document = AsyncMock(side_effect=_download)
    tms_browser = AsyncMock()

    dl = TMSDataLayer(qbo_api, tms_api, tms_browser)
    invoice = _qbo_invoice(wo="LM01")
    invoice["DocNumber"] = "LM26040454F"

    paths = await dl.get_documents("job-1", invoice, ["POD", "BL", "MISSING"], tmp_path)

    assert "POD" in paths
    assert "BL" in paths
    assert "MISSING" not in paths
    assert paths["POD"].read_bytes() == b"pod"


@pytest.mark.asyncio
async def test_retry_failed_row_with_browser_succeeds(tmp_path):
    """A row that failed on TMS API can be retried via browser and the success removes it from the box."""
    qbo_api = AsyncMock()
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(side_effect=RuntimeError("api down"))
    tms_browser = AsyncMock()
    tms_browser.fetch_doc_by_wo = AsyncMock(return_value=tmp_path / "POD_browser.pdf")
    (tmp_path / "POD_browser.pdf").write_bytes(b"pod browser")
    tms_browser.bc_detail_type_segment = lambda inv_no: "import"

    dl = TMSDataLayer(qbo_api, tms_api, tms_browser)
    invoice = _qbo_invoice(wo="LM01")
    invoice["DocNumber"] = "LM26040454F"

    # Original API attempt fails, row recorded
    await dl.get_document("job-1", invoice, "POD", tmp_path)
    assert len(dl.get_failed_rows("job-1")) == 1
    row_id = dl.get_failed_rows("job-1")[0].row_id

    # Note: real implementation needs to remember the original invoice + dest_dir
    # for the retry. For this test we just verify the retry method exists and
    # routes correctly via source="browser".
    succeeded = await dl.retry_failed_row("job-1", row_id, source="browser")

    assert succeeded is True
    assert dl.get_failed_rows("job-1") == []


@pytest.mark.asyncio
async def test_retry_failed_row_unknown_id_returns_false():
    qbo_api = AsyncMock()
    tms_api = AsyncMock()
    tms_browser = AsyncMock()

    dl = TMSDataLayer(qbo_api, tms_api, tms_browser)
    succeeded = await dl.retry_failed_row("job-1", "row-nonexistent", source="api")

    assert succeeded is False


def test_reset_for_new_job_clears_rows():
    qbo_api = AsyncMock()
    tms_api = AsyncMock()
    tms_browser = AsyncMock()

    dl = TMSDataLayer(qbo_api, tms_api, tms_browser)
    dl._failed.record_failure(
        "job-1", "INV1", "C1", "get_document", "POD", "err", "tms_api",
    )
    dl.reset_for_new_job("job-1")

    assert dl.get_failed_rows("job-1") == []


# force=True tests ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_enrich_invoice_force_kwarg_forwarded():
    """TMSDataLayer.enrich_invoice forwards force=True to run_enrich."""
    qbo_api = AsyncMock()
    tms_api = AsyncMock()
    tms_api.get_work_order = AsyncMock(return_value={"do_sender": ["x@y.com"]})
    tms_browser = AsyncMock()

    dl = TMSDataLayer(qbo_api, tms_api, tms_browser)
    # QBO already has chassis + cnee — force=True is needed to make TMS get called.
    invoice = {
        "DocNumber": "INV-100",
        "CustomField": [
            {"Name": "NGL REF#", "StringValue": "WO9/CUST-REF"},
            {"Name": "CNTR# / CHASSIS#", "StringValue": "TGBU6571759/CHX1"},
        ],
        "CustomerMemo": {"value": "Pickup\nNGLPORT --> CNEE --> NGLPORT"},
    }
    enriched = await dl.enrich_invoice("job-1", invoice, force=True)
    assert tms_api.get_work_order.await_count == 1
    assert enriched.do_sender_email == "x@y.com"


@pytest.mark.asyncio
async def test_enrich_invoice_force_with_browser_source_raises():
    """force=True + source='browser' is meaningless and must fail loudly."""
    qbo, tms_api, browser = MagicMock(), AsyncMock(), AsyncMock()
    layer = TMSDataLayer(qbo, tms_api, browser)
    with pytest.raises(ValueError, match="force=True is only supported"):
        await layer.enrich_invoice("job", {"DocNumber": "X"}, source="browser", force=True)


@pytest.mark.asyncio
async def test_retry_enrich_does_not_cross_contaminate_on_empty_docnumber():
    """Two invoices with empty DocNumber must not collide on retry."""
    qbo, tms_api, browser = MagicMock(), AsyncMock(), AsyncMock()
    tms_api.get_work_order.side_effect = RuntimeError("network down")

    layer = TMSDataLayer(qbo, tms_api, browser)
    inv_a = {"DocNumber": "", "Id": "10", "CustomField": [{"Name": "NGL REF#", "StringValue": "WO-A/X"}]}
    inv_b = {"DocNumber": "", "Id": "11", "CustomField": [{"Name": "NGL REF#", "StringValue": "WO-B/Y"}]}

    await layer.enrich_invoice("j", inv_a, force=True)
    await layer.enrich_invoice("j", inv_b, force=True)

    rows = layer.get_failed_rows("j")
    assert len(rows) == 2
    row_a = rows[0]

    # Retry only row_a; row_b's failure must remain.
    tms_api.get_work_order.side_effect = None
    tms_api.get_work_order.return_value = {"container_no": "X"}
    ok = await layer.retry_failed_row("j", row_a.row_id, source="api")
    assert ok is True

    remaining = layer.get_failed_rows("j")
    assert len(remaining) == 1, "row_b's failure must not have been removed"


@pytest.mark.asyncio
async def test_failed_row_uses_id_when_docnumber_empty():
    """Empty DocNumber should fall back to QBO Id so the UI shows something."""
    qbo, tms_api, browser = MagicMock(), AsyncMock(), AsyncMock()
    tms_api.get_work_order.side_effect = RuntimeError("boom")

    layer = TMSDataLayer(qbo, tms_api, browser)
    invoice_data = {
        "DocNumber": "",
        "Id": "QBO-42",
        "CustomField": [{"Name": "NGL REF#", "StringValue": "WO/X"}],
    }

    await layer.enrich_invoice("j", invoice_data, force=True)
    rows = layer.get_failed_rows("j")
    assert rows[0].invoice_number == "QBO-42"


def test_invoice_label_none_docnumber_falls_back_to_id():
    from services.tms_data import _invoice_label
    assert _invoice_label({"DocNumber": None, "Id": "99"}) == "99"


def test_invoice_label_whitespace_docnumber_falls_back_to_id():
    from services.tms_data import _invoice_label
    assert _invoice_label({"DocNumber": "   ", "Id": "99"}) == "99"


def test_invoice_label_no_id_returns_unknown():
    from services.tms_data import _invoice_label
    assert _invoice_label({"DocNumber": "", "Id": None}) == "<unknown>"


@pytest.mark.asyncio
async def test_retry_get_document_records_when_doc_absent_on_wo(tmp_path):
    """If the WO exists but has no doc of the requested type, record it as still-failed."""
    qbo, tms_api, browser = MagicMock(), AsyncMock(), AsyncMock()
    # First call: error (creates failed row)
    tms_api.get_work_order.side_effect = RuntimeError("fail")
    layer = TMSDataLayer(qbo, tms_api, browser)
    invoice_data = {
        "DocNumber": "INV-Z",
        "Id": "9",
        "CustomField": [{"Name": "NGL REF#", "StringValue": "WO99/X"}],
    }
    await layer.get_document("j", invoice_data, "POD", tmp_path)
    rows_before = layer.get_failed_rows("j")
    assert len(rows_before) == 1
    row_id = rows_before[0].row_id

    # Retry: WO exists but has no POD on it. run_document returns (None, None) — not an error.
    tms_api.get_work_order.side_effect = None
    tms_api.get_work_order.return_value = {"documents": []}

    ok = await layer.retry_failed_row("j", row_id, source="api")
    assert ok is False, "POD genuinely absent on WO — retry must report failure"
    rows_after = layer.get_failed_rows("j")
    assert len(rows_after) == 1
    assert "not present" in rows_after[0].error_message.lower() \
        or "no doc" in rows_after[0].error_message.lower()
