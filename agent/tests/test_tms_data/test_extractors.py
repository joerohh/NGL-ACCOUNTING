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
