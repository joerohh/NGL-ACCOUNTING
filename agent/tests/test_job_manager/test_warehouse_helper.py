"""Verify _is_warehouse_row is importable from the package, not just fetch_job."""

from services.job_manager import _is_warehouse_row


def test_warehouse_helper_reexported_at_package_level() -> None:
    assert _is_warehouse_row("LW260515P01") is True
    assert _is_warehouse_row("LM2602170009") is False
    assert _is_warehouse_row(None) is False
