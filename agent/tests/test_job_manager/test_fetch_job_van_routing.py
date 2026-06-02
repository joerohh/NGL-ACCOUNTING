"""Tests for trailer-van (V-prefix) routing in the fetch job.

Van invoices have INV# 2nd letter 'V' (e.g. SV26050013F). Their "container"
field is unreliable (trailer IDs like T1022, PO numbers, or the word "Special"),
so TMS lookup uses WO# directly. The doc chain is POD -> POL -> BL -> IT -> ITE
(POL comes BEFORE BL for vans — a different order than imports).
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.job_manager import ContainerRequest, Job, JobManager
from services.job_manager.fetch_job import _is_van_row, _is_warehouse_row


# --- Helpers ----------------------------------------------------------------

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
    """JobManager with mocked TMS data layer that returns nothing by default."""
    jm = _make_jm()
    jm._tms_data = MagicMock()
    jm._tms_data.get_document = AsyncMock(return_value=None)
    return jm


@pytest.fixture
def container():
    """A van-shaped container — trailer ID, V-prefix invoice."""
    return ContainerRequest(container_number="T1022", invoice_number="SV26050013F")


@pytest.fixture
def job(container):
    return Job("test-van-1", [container])


@pytest.fixture
def dest_path(tmp_path):
    return tmp_path / "van_pod.pdf"


# --- Unit tests: _is_van_row ------------------------------------------------

def test_is_van_row_recognises_V_prefix():
    assert _is_van_row("SV26050013F") is True
    assert _is_van_row("PV2605260003") is True
    assert _is_van_row("LV99999999X") is True


def test_is_van_row_rejects_non_V_prefixes():
    assert _is_van_row("MM26050039F") is False  # import
    assert _is_van_row("SW04302026TRL") is False  # warehouse
    assert _is_van_row("SE26050006F") is False  # export
    assert _is_van_row("") is False
    assert _is_van_row("X") is False  # too short


def test_is_van_row_handles_none_safely():
    """_is_van_row should not blow up on a None invoice number."""
    assert _is_van_row(None) is False


def test_is_van_row_handles_lowercase():
    """Invoice prefix is case-insensitive — both uppercase V and lowercase v match."""
    assert _is_van_row("sv26050013f") is True
    assert _is_van_row("Sv26050013F") is True


def test_van_and_warehouse_are_mutually_exclusive():
    """A row should never be both van AND warehouse — V and W don't overlap."""
    for inv in [
        "SV26050013F", "PV2605260003",       # van
        "SW04302026TRL", "PW260430CC01",     # warehouse
        "MM26050039F", "SE26050006F",        # import / export
    ]:
        assert not (_is_van_row(inv) and _is_warehouse_row(inv)), (
            f"Invoice {inv!r} should not be classified as both van and warehouse"
        )


# --- Integration tests: TMS chain order for vans ---------------------------

@pytest.mark.asyncio
async def test_van_invoice_walks_POD_POL_BL_IT_ITE_chain(
    job_manager, job, container, dest_path
):
    """V-prefix invoices should request docs in order POD -> POL -> BL -> IT -> ITE.

    Note this is a DIFFERENT order than imports (POD -> BL -> POL -> IT) — POL
    comes BEFORE BL for vans per spec.
    """
    container.invoice_number = "SV26050013F"
    invoice_data = {"Id": "1", "DocNumber": "SV26050013F"}

    success_type, chain = await job_manager._tms_pod_fallback(
        job, container, invoice_data, dest_path,
    )

    attempted_types = [c["type"] for c in chain]
    assert attempted_types == ["POD", "POL", "BL", "IT", "ITE"], (
        f"Van chain wrong order. Got {attempted_types}"
    )
    assert success_type is None  # all missed (mock returns None)
    assert len(chain) == 5
    assert all(step["outcome"] == "tms_miss" for step in chain)


@pytest.mark.asyncio
async def test_van_chain_stops_at_first_hit(
    job_manager, job, container, dest_path, tmp_path
):
    """Once a doc type succeeds, the chain should stop and not try later types.

    Mock TMS so POD misses but POL hits — BL / IT / ITE should never be tried.
    """
    container.invoice_number = "SV26050013F"
    invoice_data = {"Id": "1", "DocNumber": "SV26050013F"}

    requested: list[str] = []

    async def fake_get_document(job_id, invoice_data, doc_type, dest_dir, source="api"):
        requested.append(doc_type)
        if doc_type == "POL":
            fake = Path(dest_dir) / "pol.pdf"
            fake.write_bytes(b"%PDF-1.4 fake")
            return fake
        return None

    job_manager._tms_data.get_document = AsyncMock(side_effect=fake_get_document)

    success_type, chain = await job_manager._tms_pod_fallback(
        job, container, invoice_data, dest_path,
    )

    # POD missed, POL hit, BL/IT/ITE never tried
    assert requested == ["POD", "POL"], f"Chain didn't stop at first hit. Got {requested}"
    assert success_type == "POL"
    assert chain == [
        {"type": "POD", "outcome": "tms_miss"},
        {"type": "POL", "outcome": "tms_hit"},
    ]
    assert dest_path.exists()


@pytest.mark.asyncio
async def test_van_chain_pod_hit_returns_immediately(
    job_manager, job, container, dest_path, tmp_path
):
    """POD on the first try should short-circuit the rest of the chain."""
    container.invoice_number = "SV26050013F"
    invoice_data = {"Id": "1", "DocNumber": "SV26050013F"}

    fake_pod = tmp_path / "fake_pod.pdf"
    fake_pod.write_bytes(b"%PDF-1.4 pod")
    job_manager._tms_data.get_document = AsyncMock(return_value=fake_pod)

    success_type, chain = await job_manager._tms_pod_fallback(
        job, container, invoice_data, dest_path,
    )

    assert success_type == "POD"
    assert chain == [{"type": "POD", "outcome": "tms_hit"}]
    assert dest_path.exists()


@pytest.mark.asyncio
async def test_van_inv_overrides_wo_letter(job_manager, job, container, dest_path):
    """A V-prefix INV# wins even when the WO# would otherwise route as import.

    INV# is the primary signal; WO# is only consulted when INV# pos-2 isn't
    M/E/V. So a V-prefix INV# with an "M"-containing WO# still walks the van
    chain, not the import chain.
    """
    container.invoice_number = "SV26050013F"
    invoice_data = {
        "Id": "1",
        "DocNumber": "SV26050013F",
        "CustomField": [{"Name": "NGL REF#", "StringValue": "LM2605040008/CUST"}],
    }

    _success, chain = await job_manager._tms_pod_fallback(
        job, container, invoice_data, dest_path,
    )

    attempted_types = [c["type"] for c in chain]
    assert attempted_types == ["POD", "POL", "BL", "IT", "ITE"], (
        f"V-prefix INV# should override WO#-based routing. Got {attempted_types}"
    )
