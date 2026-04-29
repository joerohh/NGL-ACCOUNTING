"""Tests for FailedRowsTracker — per-job failure state."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from services.tms_data.failed_rows import FailedRowsTracker


class TestRecordAndRetrieve:
    def test_empty_for_unknown_job(self):
        t = FailedRowsTracker()
        assert t.get_rows("unknown-job") == []

    def test_record_one_failure(self):
        t = FailedRowsTracker()
        row_id = t.record_failure(
            job_id="job-1",
            invoice_number="LM26040454F",
            container_number="KKFU7654819",
            operation="get_document",
            doc_type="POD",
            error_message="TMS API 500",
            source="tms_api",
        )
        assert row_id.startswith("row-")
        rows = t.get_rows("job-1")
        assert len(rows) == 1
        assert rows[0].row_id == row_id
        assert rows[0].operation == "get_document"
        assert rows[0].doc_type == "POD"
        assert rows[0].failed_at_source == "tms_api"

    def test_record_multiple_failures(self):
        t = FailedRowsTracker()
        t.record_failure("job-1", "INV1", "C1", "get_document", "POD", "err1", "tms_api")
        t.record_failure("job-1", "INV2", "C2", "enrich_invoice", None, "err2", "tms_api")
        rows = t.get_rows("job-1")
        assert len(rows) == 2

    def test_jobs_isolated(self):
        t = FailedRowsTracker()
        t.record_failure("job-A", "INV1", "C1", "get_document", "POD", "err", "tms_api")
        t.record_failure("job-B", "INV2", "C2", "get_document", "BL", "err", "tms_api")
        assert len(t.get_rows("job-A")) == 1
        assert len(t.get_rows("job-B")) == 1
        assert t.get_rows("job-A")[0].invoice_number == "INV1"
        assert t.get_rows("job-B")[0].invoice_number == "INV2"


class TestRemoveRow:
    def test_remove_existing_row_returns_true(self):
        t = FailedRowsTracker()
        row_id = t.record_failure("job-1", "INV1", "C1", "get_document", "POD", "err", "tms_api")
        assert t.remove_row("job-1", row_id) is True
        assert t.get_rows("job-1") == []

    def test_remove_unknown_row_returns_false(self):
        t = FailedRowsTracker()
        t.record_failure("job-1", "INV1", "C1", "get_document", "POD", "err", "tms_api")
        assert t.remove_row("job-1", "row-nonexistent") is False
        assert len(t.get_rows("job-1")) == 1

    def test_remove_unknown_job_returns_false(self):
        t = FailedRowsTracker()
        assert t.remove_row("unknown-job", "row-X") is False


class TestFindRow:
    def test_finds_existing_row(self):
        t = FailedRowsTracker()
        row_id = t.record_failure("job-1", "INV1", "C1", "get_document", "POD", "err", "tms_api")
        row = t.find_row("job-1", row_id)
        assert row is not None
        assert row.invoice_number == "INV1"

    def test_returns_none_for_unknown_row(self):
        t = FailedRowsTracker()
        assert t.find_row("job-1", "row-X") is None


class TestReset:
    def test_reset_clears_job(self):
        t = FailedRowsTracker()
        t.record_failure("job-1", "INV1", "C1", "get_document", "POD", "err", "tms_api")
        t.record_failure("job-1", "INV2", "C2", "get_document", "BL", "err", "tms_api")
        t.reset("job-1")
        assert t.get_rows("job-1") == []

    def test_reset_unknown_job_is_safe(self):
        t = FailedRowsTracker()
        t.reset("unknown-job")  # must not raise

    def test_reset_does_not_affect_other_jobs(self):
        t = FailedRowsTracker()
        t.record_failure("job-A", "INV1", "C1", "get_document", "POD", "err", "tms_api")
        t.record_failure("job-B", "INV2", "C2", "get_document", "BL", "err", "tms_api")
        t.reset("job-A")
        assert t.get_rows("job-A") == []
        assert len(t.get_rows("job-B")) == 1
