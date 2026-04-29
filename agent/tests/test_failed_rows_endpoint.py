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
