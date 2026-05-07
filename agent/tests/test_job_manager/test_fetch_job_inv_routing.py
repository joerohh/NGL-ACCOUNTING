"""Unit tests for INV#-primary routing + IT/ITE chain in _tms_pod_fallback."""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.job_manager import ContainerRequest, Job, JobManager


def _make_jm():
    """Construct a JobManager with the same wiring as the existing fallback tests."""
    from services.qbo_api import QBOApiClient
    jm = JobManager(QBOApiClient(), classifier=MagicMock())
    jm._tms = MagicMock(name="tms_browser_should_not_be_called")
    jm._tms_api = MagicMock()
    jm._emit = AsyncMock()
    return jm


@pytest.fixture
def job_manager():
    """JobManager with mocked TMS data layer that we control per-test."""
    jm = _make_jm()
    jm._tms_data = MagicMock()
    jm._tms_data.get_document = AsyncMock(return_value=None)  # default: TMS has nothing
    return jm


@pytest.fixture
def job():
    """Minimal Job for use in _tms_pod_fallback calls."""
    requests = [ContainerRequest(container_number="TCLU8830712", invoice_number="LM26050100F")]
    return Job("test-job-1", requests)


@pytest.fixture
def container():
    return ContainerRequest(container_number="TCLU8830712", invoice_number="LM26050100F")


@pytest.fixture
def dest_path(tmp_path):
    return tmp_path / "tcl_pod.pdf"


@pytest.mark.asyncio
async def test_inv_letter_M_uses_import_chain(job_manager, job, container, dest_path):
    """INV# starting with 'LM' → primary signal says import → POD → BOL → POL → IT chain."""
    container.invoice_number = "LM26050100F"
    invoice_data = {"Id": "1", "DocNumber": "LM26050100F"}

    result_type, chain = await job_manager._tms_pod_fallback(job, container, invoice_data, dest_path)

    # All four import doc types tried in order
    attempted_types = [c["type"] for c in chain]
    assert attempted_types == ["POD", "BOL", "POL", "IT"], (
        f"Expected import chain [POD, BOL, POL, IT], got {attempted_types}"
    )
    assert result_type is None
    # All outcomes are tms_miss (mocked TMS returned None)
    assert all(c["outcome"] == "tms_miss" for c in chain)


@pytest.mark.asyncio
async def test_inv_letter_E_uses_export_chain(job_manager, job, container, dest_path):
    """INV# starting with 'PE' → primary signal says export → BOL → POL → ITE chain (no POD)."""
    container.invoice_number = "PE26050103F"
    invoice_data = {"Id": "1", "DocNumber": "PE26050103F"}

    result_type, chain = await job_manager._tms_pod_fallback(job, container, invoice_data, dest_path)

    attempted_types = [c["type"] for c in chain]
    assert attempted_types == ["BOL", "POL", "ITE"], (
        f"Expected export chain [BOL, POL, ITE], got {attempted_types}"
    )
    assert result_type is None


@pytest.mark.asyncio
async def test_inv_letter_E_first_choice_BOL_succeeds(job_manager, job, container, dest_path, tmp_path):
    """Export row's first try (BOL) succeeds → chain stops, result is 'BOL'."""
    container.invoice_number = "PE26050103F"
    invoice_data = {"Id": "1", "DocNumber": "PE26050103F"}

    # Mock TMS to return a real file on the FIRST call (BOL)
    fake_bol = tmp_path / "fake_bol.pdf"
    fake_bol.write_bytes(b"%PDF-1.4 fake")
    job_manager._tms_data.get_document = AsyncMock(return_value=fake_bol)

    result_type, chain = await job_manager._tms_pod_fallback(job, container, invoice_data, dest_path)

    assert result_type == "BOL"
    assert chain == [{"type": "BOL", "outcome": "tms_hit"}]
    assert dest_path.exists()


@pytest.mark.asyncio
async def test_garbled_inv_falls_back_to_wo_letter(job_manager, job, container, dest_path):
    """Non-standard INV# prefix (pos-2 not M or E) → falls back to WO# letter routing."""
    # Pick a prefix whose pos-2 is neither M nor E — "ZZ" is safely outside the routing alphabet.
    container.invoice_number = "ZZ-WEIRD-1234"
    # WO# in QBO custom field — passed via invoice_data
    invoice_data = {
        "Id": "1",
        "DocNumber": "ZZ-WEIRD-1234",
        "CustomField": [{"Name": "NGL REF#", "StringValue": "LM2605040008/CUSTOMER"}],
    }

    result_type, chain = await job_manager._tms_pod_fallback(job, container, invoice_data, dest_path)

    attempted_types = [c["type"] for c in chain]
    # WO contains M → import chain
    assert attempted_types == ["POD", "BOL", "POL", "IT"]


@pytest.mark.asyncio
async def test_garbled_inv_garbled_wo_uses_safety_chain(job_manager, job, container, dest_path):
    """Both signals fail → safety chain POD → BOL → POL → IT → ITE (5 types)."""
    container.invoice_number = "GARBAGE"
    invoice_data = {"Id": "1", "DocNumber": "GARBAGE"}  # no CustomField → no WO# extracted

    result_type, chain = await job_manager._tms_pod_fallback(job, container, invoice_data, dest_path)

    attempted_types = [c["type"] for c in chain]
    assert attempted_types == ["POD", "BOL", "POL", "IT", "ITE"]


@pytest.mark.asyncio
async def test_inv_overrides_wo_when_they_disagree(job_manager, job, container, dest_path):
    """INV# says export but WO# says import → INV# wins (it's primary)."""
    container.invoice_number = "PE26050200F"  # export
    invoice_data = {
        "Id": "1",
        "DocNumber": "PE26050200F",
        "CustomField": [{"Name": "NGL REF#", "StringValue": "LM2605040999/CUST"}],  # WO says import
    }

    result_type, chain = await job_manager._tms_pod_fallback(job, container, invoice_data, dest_path)

    attempted_types = [c["type"] for c in chain]
    # INV# wins → export chain
    assert attempted_types == ["BOL", "POL", "ITE"]


@pytest.mark.asyncio
async def test_chain_attempted_records_errors_separately_from_misses(
    job_manager, job, container, dest_path
):
    """An exception during a fetch attempt is recorded as 'tms_error', not 'tms_miss'."""
    container.invoice_number = "PE26050103F"
    invoice_data = {"Id": "1", "DocNumber": "PE26050103F"}

    call_count = {"n": 0}

    async def flaky(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("connection reset")
        return None

    job_manager._tms_data.get_document = AsyncMock(side_effect=flaky)

    result_type, chain = await job_manager._tms_pod_fallback(job, container, invoice_data, dest_path)

    assert chain[0] == {"type": "BOL", "outcome": "tms_error"}
    assert chain[1] == {"type": "POL", "outcome": "tms_miss"}
    assert chain[2] == {"type": "ITE", "outcome": "tms_miss"}
    assert result_type is None


@pytest.mark.asyncio
async def test_no_tms_data_returns_empty_chain(container, dest_path):
    """When TMS layer isn't configured, return (None, []) without trying anything."""
    jm = _make_jm()
    jm._tms_data = None  # explicitly disabled
    job = Job("t", [container])
    invoice_data = {"Id": "1"}

    result_type, chain = await jm._tms_pod_fallback(job, container, invoice_data, dest_path)

    assert result_type is None
    assert chain == []
