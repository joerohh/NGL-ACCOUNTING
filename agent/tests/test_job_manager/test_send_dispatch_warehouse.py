"""Verify the send dispatcher routes warehouse invoices to the warehouse flow.

This test calls the REAL _run_send_job_inner method (which contains the dispatch
block) so that deleting the `if is_warehouse:` branch in send_job.py causes the
test to fail — not a shadow re-implementation of the decision.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.job_manager import JobManager, SendJob, SendRequest
from services.qbo_api import QBOApiClient


@pytest.mark.asyncio
async def test_warehouse_invoice_dispatches_to_warehouse_flow() -> None:
    """Warehouse INV# (pos-2 == 'W') must be routed to _send_warehouse_invoice,
    NOT _send_qbo_api, even when the customer's sendMethod is 'email'."""

    # --- Build a minimal SendJob with one warehouse invoice ---
    invoice = SendRequest(
        invoice_number="LW260515P01",
        container_number="",
        customer_code="PACCS01",
        subject="Warehouse Invoice LW260515P01 - Pacific",
    )
    # Mark is_resend=True so the duplicate-send guard is bypassed without
    # needing to mock was_recently_sent.
    invoice.is_resend = True

    job = SendJob(job_id="test-wh-01", invoices=[invoice])

    # Customer with sendMethod="email" — would normally go to _send_qbo_api.
    customer_dict = {
        "PACCS01": {
            "code": "PACCS01",
            "name": "Pacific Cold Storage Inc.",
            "emails": ["billing@pacificcoldstorage.com"],
            "active": True,
            "sendMethod": "email",
        }
    }

    # --- Build a JobManager with minimal real dependencies ---
    mock_qbo = MagicMock(spec=QBOApiClient)
    mock_qbo.is_connected = True
    mock_qbo.token_manager = MagicMock()
    mock_qbo.token_manager.get_access_token = AsyncMock(return_value="tok_test")

    mock_email_sender = MagicMock()  # truthy — passes the email-sender guard

    jm = JobManager(mock_qbo, classifier=MagicMock(), email_sender=mock_email_sender)

    # --- Replace all dispatch targets with AsyncMocks ---
    jm._send_warehouse_invoice = AsyncMock()
    jm._send_qbo_api = AsyncMock()
    jm._send_oec_pod_email = AsyncMock()
    jm._send_portal_upload = AsyncMock()

    # --- Mock infrastructure so the loop body can complete cleanly ---
    jm._write_audit_log = MagicMock()
    jm._emit_send = AsyncMock()
    jm._tms_data = None  # skip tms_data.reset_for_new_job

    # Patch customer loader, disk writes, and the 1-second inter-invoice sleep
    # so the test is fast and isolated from DB / filesystem.
    with (
        patch.object(type(jm), "_load_customers", staticmethod(lambda: customer_dict)),
        patch.object(job, "_save_state", MagicMock()),
        patch("services.job_manager.send_job.asyncio.sleep", AsyncMock()),
    ):
        await jm._run_send_job_inner(job)

    # --- Verify dispatch ---
    jm._send_warehouse_invoice.assert_awaited_once()
    jm._send_qbo_api.assert_not_awaited()
    jm._send_oec_pod_email.assert_not_awaited()
    jm._send_portal_upload.assert_not_awaited()
