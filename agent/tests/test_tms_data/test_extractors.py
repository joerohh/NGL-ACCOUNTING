"""Tests for QBO and TMS WO field extractors."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from services.tms_data.extractors import (
    extract_wo_from_qbo,
    extract_chassis_from_qbo,
    extract_cnee_from_qbo,
)


class TestExtractWoFromQbo:
    def test_finds_wo_in_ngl_ref_field(self):
        invoice_data = {
            "CustomField": [
                {"Name": "NGL REF#", "StringValue": "LM2603300024/CUST-REF-99"},
            ],
        }
        assert extract_wo_from_qbo(invoice_data) == "LM2603300024"

    def test_returns_none_when_no_ref_field(self):
        invoice_data = {"CustomField": []}
        assert extract_wo_from_qbo(invoice_data) is None

    def test_returns_none_for_invalid_input(self):
        assert extract_wo_from_qbo({}) is None
        assert extract_wo_from_qbo(None) is None


class TestExtractChassisFromQbo:
    def test_returns_string_when_present(self):
        # _extract_chassis expects CONTAINER/CHASSIS slash-format where the part
        # before the slash matches the container regex. Plain "ABC1234" returns None.
        invoice_data = {
            "CustomField": [
                {"Name": "CNTR# / CHASSIS#", "StringValue": "TGBU6571759/ABC1234"},
            ],
        }
        result = extract_chassis_from_qbo(invoice_data)
        assert result == "ABC1234"

    def test_returns_none_when_absent(self):
        assert extract_chassis_from_qbo({}) is None


class TestExtractCneeFromQbo:
    def test_returns_none_when_absent(self):
        assert extract_cnee_from_qbo({}) is None

    def test_returns_none_for_invalid_input(self):
        assert extract_cnee_from_qbo(None) is None


from services.tms_data.extractors import (
    extract_chassis_from_tms_wo,
    extract_cnee_from_tms_wo,
    extract_container_from_tms_wo,
    extract_do_sender_from_tms_wo,
    extract_document_url_from_tms_wo,
)


SAMPLE_WO = {
    "wo_no": "LM2602170009",
    "category": "import",
    "container_no": "KKFU7654819",
    "chassis_no": "ABC1234",
    "billto": "ACME LOGISTICS",
    "do_sender": ["ops@example.com", "secondary@example.com"],
    "documents": [
        {"type_": "POD", "file_url": "https://cdn.example/pod.pdf"},
        {"type_": "BL", "file_url": "https://cdn.example/bl.pdf"},
        {"type_": "DO", "file_url": "https://cdn.example/do.pdf"},
    ],
}


class TestExtractChassisFromTmsWo:
    def test_finds_chassis_no(self):
        assert extract_chassis_from_tms_wo(SAMPLE_WO) == "ABC1234"

    def test_finds_chassis_alternate_name(self):
        assert extract_chassis_from_tms_wo({"chassis": "XYZ5678"}) == "XYZ5678"

    def test_finds_chassis_third_alternate(self):
        assert extract_chassis_from_tms_wo({"chassis_number": "QQQ9999"}) == "QQQ9999"

    def test_returns_none_when_absent(self):
        assert extract_chassis_from_tms_wo({}) is None

    def test_returns_none_for_non_dict(self):
        assert extract_chassis_from_tms_wo(None) is None
        assert extract_chassis_from_tms_wo("string") is None


class TestExtractCneeFromTmsWo:
    def test_finds_billto(self):
        assert extract_cnee_from_tms_wo(SAMPLE_WO) == "ACME LOGISTICS"

    def test_finds_consignee_alternate(self):
        assert extract_cnee_from_tms_wo({"consignee": "BETA CO"}) == "BETA CO"

    def test_returns_none_when_absent(self):
        assert extract_cnee_from_tms_wo({}) is None


class TestExtractContainerFromTmsWo:
    def test_finds_container(self):
        assert extract_container_from_tms_wo(SAMPLE_WO) == "KKFU7654819"

    def test_returns_none_when_absent(self):
        assert extract_container_from_tms_wo({}) is None


class TestExtractDoSenderFromTmsWo:
    def test_finds_first_email(self):
        assert extract_do_sender_from_tms_wo(SAMPLE_WO) == "ops@example.com"

    def test_returns_none_for_empty_array(self):
        assert extract_do_sender_from_tms_wo({"do_sender": []}) is None

    def test_returns_none_when_no_at_sign(self):
        assert extract_do_sender_from_tms_wo({"do_sender": ["not-an-email"]}) is None


class TestExtractDocumentUrlFromTmsWo:
    def test_finds_pod_url(self):
        assert extract_document_url_from_tms_wo(SAMPLE_WO, "POD") == "https://cdn.example/pod.pdf"

    def test_finds_bl_url(self):
        assert extract_document_url_from_tms_wo(SAMPLE_WO, "BL") == "https://cdn.example/bl.pdf"

    def test_case_insensitive_match(self):
        assert extract_document_url_from_tms_wo(SAMPLE_WO, "pod") == "https://cdn.example/pod.pdf"

    def test_returns_none_when_doc_type_missing(self):
        assert extract_document_url_from_tms_wo(SAMPLE_WO, "MISSING") is None

    def test_returns_none_for_empty_documents(self):
        assert extract_document_url_from_tms_wo({"documents": []}, "POD") is None
