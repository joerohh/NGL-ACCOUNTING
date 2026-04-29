"""Tests for GET /jobs/{job_id}/failed-rows endpoint."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Ensure agent/ on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers import jobs as jobs_router
from services.tms_data import TMSDataLayer
from services.tms_data.enriched_invoice import FailedRow


@pytest.fixture
def app():
    """Minimal FastAPI app with the jobs router and a real TMSDataLayer mounted."""
    app = FastAPI()
    app.include_router(jobs_router.router)
    app.state.tms_data = TMSDataLayer(
        qbo_api=MagicMock(),
        tms_api=AsyncMock(),
        tms_browser=AsyncMock(),
    )
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def test_get_failed_rows_unknown_job_returns_empty_list(client):
    r = client.get("/jobs/does-not-exist/failed-rows")
    assert r.status_code == 200
    assert r.json() == {"rows": []}


def test_get_failed_rows_returns_recorded_failures(client, app):
    layer = app.state.tms_data
    layer._failed._rows.setdefault("job-x", []).append(
        FailedRow(
            row_id="row-test1",
            invoice_number="INV-1",
            container_number="ABCU0000001",
            operation="get_document",
            doc_type="POD",
            error_message="boom",
            failed_at_source="tms_api",
            timestamp=1.0,
        )
    )
    r = client.get("/jobs/job-x/failed-rows")
    assert r.status_code == 200
    body = r.json()
    assert len(body["rows"]) == 1
    assert body["rows"][0]["invoice_number"] == "INV-1"
    assert body["rows"][0]["doc_type"] == "POD"
    assert body["rows"][0]["failed_at_source"] == "tms_api"


def test_get_failed_rows_503_when_tms_data_missing():
    """If app.state.tms_data is missing, return 503 not 500."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import jobs as jobs_router

    app = FastAPI()
    app.include_router(jobs_router.router)
    # NOTE: do NOT set app.state.tms_data
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get("/jobs/anything/failed-rows")
    assert r.status_code == 503


def test_retry_failed_row_invalid_source_rejected(client):
    r = client.post("/jobs/j/failed-rows/row-1/retry?source=carrier-pigeon")
    assert r.status_code == 422 or r.status_code == 400


def test_retry_failed_row_unknown_returns_succeeded_false(client, app):
    r = client.post("/jobs/no-such/failed-rows/row-x/retry?source=api")
    assert r.status_code == 200
    assert r.json()["succeeded"] is False


def test_retry_all_failed_returns_counts(client, app):
    from services.tms_data.enriched_invoice import FailedRow
    layer = app.state.tms_data
    layer._failed._rows["job-batch"] = [
        FailedRow("r1", "INV-1", None, "enrich_invoice", None, "e", "tms_api", 1.0),
        FailedRow("r2", "INV-2", None, "enrich_invoice", None, "e", "tms_api", 2.0),
    ]
    layer._retry_ctx["r1"] = {"operation": "enrich_invoice", "invoice_data": {}, "force": False}
    layer._retry_ctx["r2"] = {"operation": "enrich_invoice", "invoice_data": {}, "force": False}

    r = client.post("/jobs/job-batch/failed-rows/retry-all?source=api")
    assert r.status_code == 200
    body = r.json()
    assert "succeeded" in body and "still_failed" in body
