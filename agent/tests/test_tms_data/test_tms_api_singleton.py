"""Verify there is one TMSApiClient instance shared across main, routers, and JobManager."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import tms as tms_router
from services.job_manager import JobManager
from services.qbo_api import QBOApiClient


def test_set_tms_api_propagates_to_router():
    fake = MagicMock(name="tms_api_singleton")
    tms_router.set_tms_api(fake)
    assert tms_router._tms_api is fake


def test_set_tms_api_propagates_to_job_manager():
    fake = MagicMock(name="tms_api_singleton")
    jm = JobManager(QBOApiClient(), classifier=MagicMock())
    jm.set_tms_api(fake)
    assert jm._tms_api is fake


# ── Smoke test: /tms/api-test/{wo_no} must not NameError ────────────

FAKE_WO = {
    "wo_no": "TESTWO001",
    "category": "import",
    "container_no": "KKFU1234567",
    "do_sender": ["shipper@example.com"],
    "documents": [
        {"type_": "POD", "file_url": "https://cdn.example.com/pod.pdf"},
        {"type_": "BL",  "file_url": "https://cdn.example.com/bl.pdf"},
    ],
}


@pytest.fixture()
def api_test_client():
    """Minimal FastAPI app with the TMS router + a mocked _tms_api injected."""
    app = FastAPI()
    app.include_router(tms_router.router)

    # Build a mock that mimics TMSApiClient's interface
    mock_api = MagicMock()
    mock_api.is_configured.return_value = True
    mock_api.get_work_order = AsyncMock(return_value=FAKE_WO)
    mock_api.download_document = AsyncMock(return_value=b"%PDF-1.4 fake")
    # Static helpers work on instances too (Python allows calling @staticmethod on instance)
    mock_api.extract_pod_url = MagicMock(return_value="https://cdn.example.com/pod.pdf")
    mock_api.extract_do_sender = MagicMock(return_value="shipper@example.com")

    tms_router.set_tms_api(mock_api)
    yield TestClient(app)
    # Teardown: clear the injected client so other tests start clean
    tms_router.set_tms_api(None)


def test_api_test_endpoint_returns_200(api_test_client):
    """GET /tms/api-test/{wo_no} must return 200, not 500/NameError."""
    r = api_test_client.get("/tms/api-test/TESTWO001")
    assert r.status_code == 200, (
        f"Expected 200 but got {r.status_code}. Body: {r.text}"
    )
    data = r.json()
    assert data["woNo"] == "TESTWO001"
    assert data["containerNo"] == "KKFU1234567"
    assert data["doSender"] == "shipper@example.com"
    assert data["podUrl"] == "https://cdn.example.com/pod.pdf"
    assert data["podBytes"] > 0


def test_api_test_endpoint_503_when_client_not_injected():
    """GET /tms/api-test/{wo_no} must return 503 when _tms_api is None, not AttributeError."""
    app = FastAPI()
    app.include_router(tms_router.router)
    tms_router.set_tms_api(None)
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get("/tms/api-test/SOMEWO")
    assert r.status_code == 503


def test_api_status_endpoint_handles_uninjected_client(api_test_client):
    """tms_api_status returns gracefully when _tms_api is None (pre-startup race)."""
    from routers import tms as tms_router
    tms_router.set_tms_api(None)

    r = api_test_client.get("/tms/api-status")
    assert r.status_code == 200
    body = r.json()
    # Whatever the "not configured" shape is, it should NOT be a 500.
    assert "configured" in body or "status" in body
