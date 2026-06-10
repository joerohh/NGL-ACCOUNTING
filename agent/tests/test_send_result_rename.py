"""Verify SendResult exposes attachments_emailed (the renamed field)."""
from services.job_manager import SendResult


def test_send_result_has_attachments_emailed_field():
    r = SendResult("INV-1", "ABCU000", "CUST")
    assert hasattr(r, "attachments_emailed")
    assert r.attachments_emailed == []


def test_to_dict_emits_attachments_emailed_key():
    r = SendResult("INV-1", "ABCU000", "CUST")
    r.attachments_emailed = ["pod", "do"]
    d = r.to_dict()
    assert "attachmentsEmailed" in d
    assert d["attachmentsEmailed"] == ["pod", "do"]
    assert "attachmentsFound" not in d


def test_attachments_missing_still_exists_for_warehouse_oec():
    r = SendResult("INV-1", "ABCU000", "CUST")
    assert hasattr(r, "attachments_missing")
    assert r.to_dict()["attachmentsMissing"] == []
