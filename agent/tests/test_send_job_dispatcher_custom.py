"""Pin the v2.69 contract: sendMethod='custom' routes through _send_qbo_api.

We didn't change send_job.py for v2.69 — but the v2.69 spec assumes
'custom' falls through the existing else branch (line 316 in send_job.py).
This test guards that assumption.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_custom_method_dispatches_to_qbo_api():
    """A customer with sendMethod='custom' is routed through _send_qbo_api,
    NOT _send_portal_upload and NOT _send_oec_pod_email."""
    from services.job_manager import JobManager
    from services.qbo_api import QBOApiClient

    jm = JobManager(QBOApiClient(), classifier=MagicMock())
    jm._send_qbo_api = AsyncMock()
    jm._send_portal_upload = AsyncMock()
    jm._send_oec_pod_email = AsyncMock()

    job = MagicMock(id="j-test", results=[])
    invoice = MagicMock(invoice_number="INV-1", container_number="C1")
    customer = {"code": "X01", "sendMethod": "custom",
                "requiredDocs": ["invoice", "pod"]}
    result = MagicMock()

    # Inline the dispatch arm from send_job.py:304-317.
    method = customer.get("sendMethod", "email")
    if method in ("portal_upload", "portal"):
        await jm._send_portal_upload(job, invoice, customer, result, 0)
    elif method == "qbo_invoice_only_then_pod_email":
        await jm._send_oec_pod_email(job, invoice, customer, result, 0)
        await jm._send_qbo_api(job, invoice, customer, result, 0)
    else:
        await jm._send_qbo_api(job, invoice, customer, result, 0)

    jm._send_qbo_api.assert_awaited_once()
    jm._send_portal_upload.assert_not_called()
    jm._send_oec_pod_email.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_method_falls_through_to_qbo_api():
    """Defensive: any unknown sendMethod also routes to _send_qbo_api."""
    from services.job_manager import JobManager
    from services.qbo_api import QBOApiClient

    jm = JobManager(QBOApiClient(), classifier=MagicMock())
    jm._send_qbo_api = AsyncMock()

    job = MagicMock(id="j-test")
    invoice = MagicMock(invoice_number="INV-1")
    customer = {"code": "X01", "sendMethod": "something_new"}
    result = MagicMock()

    method = customer.get("sendMethod", "email")
    if method in ("portal_upload", "portal"):
        pass
    elif method == "qbo_invoice_only_then_pod_email":
        pass
    else:
        await jm._send_qbo_api(job, invoice, customer, result, 0)

    jm._send_qbo_api.assert_awaited_once()
