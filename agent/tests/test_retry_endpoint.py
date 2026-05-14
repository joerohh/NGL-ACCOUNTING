"""Retry router endpoint tests — verify-file + retry-invoice multipart paths."""
import io
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# We import the router module directly and mount it on a test FastAPI app
# rather than spinning up the full agent app (which would require .env, QBO, etc.)
from fastapi import FastAPI
from routers import retry as retry_router


@pytest.fixture
def client():
    """Build a minimal FastAPI app with just the retry router."""
    app = FastAPI()
    app.include_router(retry_router.router)
    return TestClient(app)


@pytest.fixture
def mock_classifier_result():
    """Returns a function that builds a ClassificationResult-shaped object."""
    def _make(doc_type="pod", confidence=0.93, container_hint=None, needs_review=False, skipped_api=False):
        return type("CR", (), {
            "doc_type": doc_type, "confidence": confidence,
            "container_hint": container_hint, "needs_review": needs_review,
            "skipped_api": skipped_api,
        })()
    return _make


def test_verify_file_match(client, mock_classifier_result):
    """File matches slot → matches_slot=True, detected_as=None."""
    fake_pdf = b"%PDF-1.4\n" + b"\x00" * 50
    with patch("routers.retry.ClaudeClassifier") as MockClf:
        instance = MockClf.return_value
        instance.classify = AsyncMock(return_value=mock_classifier_result(doc_type="pod", confidence=0.93))
        r = client.post(
            "/jobs/verify-file?slot=POD",
            files={"file": ("pod.pdf", io.BytesIO(fake_pdf), "application/pdf")},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["doc_type"] == "pod"
    assert body["matches_slot"] is True
    assert body["detected_as"] is None
    assert body["confidence"] == 0.93


def test_verify_file_mismatch(client, mock_classifier_result):
    """File doesn't match slot → matches_slot=False, detected_as=classifier output."""
    fake_pdf = b"%PDF-1.4\n" + b"\x00" * 50
    with patch("routers.retry.ClaudeClassifier") as MockClf:
        instance = MockClf.return_value
        instance.classify = AsyncMock(return_value=mock_classifier_result(doc_type="invoice", confidence=0.85))
        r = client.post(
            "/jobs/verify-file?slot=POD",
            files={"file": ("scan.pdf", io.BytesIO(fake_pdf), "application/pdf")},
        )
    body = r.json()
    assert body["matches_slot"] is False
    assert body["detected_as"] == "invoice"


def test_verify_file_bol_slot_accepts_pod_doctype(client, mock_classifier_result):
    """The BOL slot accepts doc_type='pod' (classifier collapses BOL→POD per existing logic)."""
    fake_pdf = b"%PDF-1.4\n" + b"\x00" * 50
    with patch("routers.retry.ClaudeClassifier") as MockClf:
        instance = MockClf.return_value
        instance.classify = AsyncMock(return_value=mock_classifier_result(doc_type="pod", confidence=0.9))
        r = client.post(
            "/jobs/verify-file?slot=BOL",
            files={"file": ("bol.pdf", io.BytesIO(fake_pdf), "application/pdf")},
        )
    body = r.json()
    assert body["matches_slot"] is True  # BOL slot accepts pod classification


def test_verify_file_unknown_slot_rejected(client):
    fake_pdf = b"%PDF-1.4\n" + b"\x00" * 50
    r = client.post(
        "/jobs/verify-file?slot=WIDGET",
        files={"file": ("x.pdf", io.BytesIO(fake_pdf), "application/pdf")},
    )
    assert r.status_code == 400


def test_retry_invoice_one_slot(client):
    """Single POD upload → calls job_manager.retry_invoice with one file."""
    fake_pdf = b"%PDF-1.4\n" + b"\x00" * 50
    fake_jm = MagicMock()
    fake_jm.retry_invoice = AsyncMock(return_value={
        "status": "sent", "error": None, "attachments_sent": 2,
    })
    retry_router.set_job_manager(fake_jm)

    invoice_json = json.dumps({
        "invoice_number": "LM26040864F",
        "invoice_id": "qbo-123",
        "container_number": "TRHU4593053",
        "customer_code": "APEXMA01",
        "amount": "1250.00",
        "subject": None,
        "do_sender_email": None,
    })
    slots_json = json.dumps([{"slot": "POD", "verified_by": "filename"}])

    r = client.post(
        "/jobs/retry-invoice",
        data={"invoice": invoice_json, "slots": slots_json},
        files={"pod_file": ("pod.pdf", io.BytesIO(fake_pdf), "application/pdf")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "sent"
    assert body["attachments_sent"] == 2
    fake_jm.retry_invoice.assert_called_once()


def test_retry_invoice_missing_file_for_slot(client):
    fake_jm = MagicMock()
    fake_jm.retry_invoice = AsyncMock(return_value={"status": "sent"})
    retry_router.set_job_manager(fake_jm)
    invoice_json = json.dumps({
        "invoice_number": "X", "invoice_id": "i", "container_number": "c",
        "customer_code": "C", "amount": "0", "subject": None, "do_sender_email": None,
    })
    slots_json = json.dumps([{"slot": "POD", "verified_by": "filename"}])
    r = client.post(
        "/jobs/retry-invoice",
        data={"invoice": invoice_json, "slots": slots_json},
        # no pod_file uploaded
    )
    assert r.status_code == 400


def test_retry_invoice_no_job_manager_returns_503(client):
    """If job manager not injected, endpoint returns 503."""
    retry_router.set_job_manager(None)
    fake_pdf = b"%PDF-1.4"
    invoice_json = json.dumps({
        "invoice_number": "X", "invoice_id": "i", "container_number": "c",
        "customer_code": "C", "amount": "0", "subject": None, "do_sender_email": None,
    })
    slots_json = json.dumps([{"slot": "POD", "verified_by": "filename"}])
    r = client.post(
        "/jobs/retry-invoice",
        data={"invoice": invoice_json, "slots": slots_json},
        files={"pod_file": ("p.pdf", io.BytesIO(fake_pdf), "application/pdf")},
    )
    assert r.status_code == 503
