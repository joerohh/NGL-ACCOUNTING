"""Verify _is_warehouse_row is importable from the package, not just fetch_job."""

from services.job_manager import _is_warehouse_row


def test_warehouse_helper_reexported_at_package_level() -> None:
    assert _is_warehouse_row("LW260515P01") is True
    assert _is_warehouse_row("LM2602170009") is False
    assert _is_warehouse_row(None) is False


from services.job_manager import SendResult


def test_send_result_carries_warehouse_fields() -> None:
    r = SendResult(invoice_number="LW260515P01", container_number="",
                   customer_code="PACCS01")
    r.routing_type = "warehouse"
    r.warehouse_attachments = [{"fileName": "detail.xlsx"}]
    r.warehouse_failures = []

    d = r.to_dict()
    assert d["routingType"] == "warehouse"
    assert d["warehouseAttachments"] == [{"fileName": "detail.xlsx"}]
    assert d["warehouseFailures"] == []
