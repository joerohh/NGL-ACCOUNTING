"""Tests for EnrichedInvoice and FailedRow dataclasses."""

import sys
import time
from pathlib import Path

# Add agent/ to path so imports work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from services.tms_data.enriched_invoice import EnrichedInvoice, FailedRow


class TestEnrichedInvoice:
    def test_construct_with_all_fields(self):
        ei = EnrichedInvoice(
            wo_no="LM2602170009",
            container_no="KKFU7654819",
            chassis="ABC1234",
            cnee="ACME LOGISTICS",
            do_sender_email="ops@example.com",
            sources={
                "wo_no": "qbo",
                "container_no": "qbo",
                "chassis": "tms_api",
                "cnee": "qbo",
                "do_sender_email": "tms_api",
            },
        )
        assert ei.wo_no == "LM2602170009"
        assert ei.chassis == "ABC1234"
        assert ei.sources["chassis"] == "tms_api"

    def test_construct_with_missing_fields(self):
        ei = EnrichedInvoice(
            wo_no=None,
            container_no=None,
            chassis=None,
            cnee=None,
            do_sender_email=None,
            sources={
                "wo_no": "missing",
                "container_no": "missing",
                "chassis": "missing",
                "cnee": "missing",
                "do_sender_email": "missing",
            },
        )
        assert ei.wo_no is None
        assert ei.sources["chassis"] == "missing"


class TestFailedRow:
    def test_construct_for_enrich_failure(self):
        fr = FailedRow(
            row_id="row-abc123",
            invoice_number="LM26040454F",
            container_number="KKFU7654819",
            operation="enrich_invoice",
            doc_type=None,
            error_message="TMS API 500 Internal Server Error",
            failed_at_source="tms_api",
            timestamp=time.time(),
        )
        assert fr.operation == "enrich_invoice"
        assert fr.doc_type is None

    def test_construct_for_document_failure(self):
        fr = FailedRow(
            row_id="row-def456",
            invoice_number="LM26040454F",
            container_number="KKFU7654819",
            operation="get_document",
            doc_type="POD",
            error_message="POD URL returned 404",
            failed_at_source="tms_api",
            timestamp=time.time(),
        )
        assert fr.doc_type == "POD"
        assert fr.failed_at_source == "tms_api"
