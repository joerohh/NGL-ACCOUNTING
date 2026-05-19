"""Unit tests for the warehouse routing helper in fetch_job.

Integration tests against live QBO live in Task 21.
"""

from services.job_manager.fetch_job import _is_warehouse_row


def test_warehouse_row_detected() -> None:
    assert _is_warehouse_row("LW260515P01") is True


def test_warehouse_row_case_insensitive() -> None:
    assert _is_warehouse_row("lw260515p01") is True


def test_import_row_not_warehouse() -> None:
    assert _is_warehouse_row("LM2602170009") is False


def test_export_row_not_warehouse() -> None:
    assert _is_warehouse_row("LE2602170011") is False


def test_empty_string_not_warehouse() -> None:
    assert _is_warehouse_row("") is False


def test_none_not_warehouse() -> None:
    assert _is_warehouse_row(None) is False


def test_single_char_not_warehouse() -> None:
    # Too short — INV# pos-2 doesn't exist.
    assert _is_warehouse_row("W") is False


def test_w_in_pos_1_not_warehouse() -> None:
    # Warehouse rule is position-2 only, not position-1.
    assert _is_warehouse_row("WM123") is False
