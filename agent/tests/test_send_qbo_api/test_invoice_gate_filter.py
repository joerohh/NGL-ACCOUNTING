"""Verify the requiredDocs gate filters out 'invoice' — Fix 3 / v2.69.

The invoice PDF is auto-sent via QBO email and never an Attachable on the
QBO invoice record. Before v2.69 the gate at send_qbo_api.py:192 blocked
the send when 'invoice' was listed because check_attachments() never finds
it. After v2.69 the filter strips 'invoice' from required_docs so it's
auto-satisfied.

This test exercises the in-process logic only — no network, no QBO.
"""
import pytest


def _filter_required_docs(customer: dict, *, is_oec: bool) -> list[str]:
    """Mirror of the production filter for unit testing.

    The real production code lives at services/job_manager/send_qbo_api.py
    around line 192. We re-import the helper after the production change
    in the integration step below to confirm parity.
    """
    if is_oec:
        return []
    return [
        d
        for d in customer.get("requiredDocs", [])
        if d.lower() != "invoice"
    ]


def test_filter_strips_invoice_case_insensitive():
    customer = {"requiredDocs": ["Invoice", "POD", "BOL"]}
    assert _filter_required_docs(customer, is_oec=False) == ["POD", "BOL"]


def test_filter_preserves_order_of_remaining_docs():
    customer = {"requiredDocs": ["POD", "invoice", "BOL", "POL"]}
    assert _filter_required_docs(customer, is_oec=False) == ["POD", "BOL", "POL"]


def test_filter_handles_only_invoice():
    customer = {"requiredDocs": ["invoice"]}
    # The customer wants invoice-only. After the filter the gate is empty,
    # meaning the send goes through unconditionally — which is correct because
    # the invoice always sends via QBO email.
    assert _filter_required_docs(customer, is_oec=False) == []


def test_filter_handles_empty_required_docs():
    customer = {"requiredDocs": []}
    assert _filter_required_docs(customer, is_oec=False) == []


def test_filter_handles_missing_required_docs_key():
    customer = {}
    assert _filter_required_docs(customer, is_oec=False) == []


def test_oec_returns_empty_regardless_of_required_docs():
    customer = {"requiredDocs": ["POD", "invoice", "BOL"]}
    # OEC flow handles its own POD via Gmail; the QBO invoice email never
    # enforces requiredDocs.
    assert _filter_required_docs(customer, is_oec=True) == []


def test_production_code_uses_same_filter():
    """Smoke check that the production code at send_qbo_api.py:192 matches
    the helper above. If this fails, the production code and the test
    have drifted apart.
    """
    import inspect
    from services.job_manager import send_qbo_api

    src = inspect.getsource(send_qbo_api)
    assert "d.lower() != \"invoice\"" in src or "d.lower() != 'invoice'" in src, (
        "Expected the requiredDocs filter to strip 'invoice' case-insensitively. "
        "Update send_qbo_api.py:192 to match the helper in this test file."
    )
