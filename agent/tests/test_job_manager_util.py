"""Unit tests for agent/services/job_manager/util.py pure helpers."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.job_manager.util import (
    extract_wo_from_invoice,
    normalize_email_list,
    validate_and_append_email,
)


# ── extract_wo_from_invoice ─────────────────────────────────────────


class TestExtractWoFromInvoice:
    def test_parses_standard_ngl_ref_format(self):
        data = {"CustomField": [
            {"Name": "NGL REF#/Your REF#", "StringValue": "LM2603300024/TB00192691"},
        ]}
        assert extract_wo_from_invoice(data) == "LM2603300024"

    def test_parses_when_name_is_lowercase(self):
        data = {"CustomField": [
            {"Name": "ngl ref#/your ref#", "StringValue": "LM2603300024/TB00192691"},
        ]}
        assert extract_wo_from_invoice(data) == "LM2603300024"

    def test_strips_whitespace(self):
        data = {"CustomField": [
            {"Name": "NGL REF#", "StringValue": "  LM2603300024 / TB00192691  "},
        ]}
        assert extract_wo_from_invoice(data) == "LM2603300024"

    def test_ignores_non_ref_custom_fields(self):
        data = {"CustomField": [
            {"Name": "Memo", "StringValue": "something/else"},
            {"Name": "NGL REF#/Your REF#", "StringValue": "LM2603300024/TB00192691"},
        ]}
        assert extract_wo_from_invoice(data) == "LM2603300024"

    def test_returns_none_when_field_missing(self):
        data = {"CustomField": [
            {"Name": "Memo", "StringValue": "no slash here"},
        ]}
        assert extract_wo_from_invoice(data) is None

    def test_returns_none_when_no_slash(self):
        data = {"CustomField": [
            {"Name": "NGL REF#", "StringValue": "LM2603300024"},
        ]}
        assert extract_wo_from_invoice(data) is None

    def test_returns_none_when_value_empty(self):
        data = {"CustomField": [
            {"Name": "NGL REF#", "StringValue": ""},
        ]}
        assert extract_wo_from_invoice(data) is None

    def test_returns_none_when_wo_blank_before_slash(self):
        data = {"CustomField": [
            {"Name": "NGL REF#", "StringValue": "/TB00192691"},
        ]}
        assert extract_wo_from_invoice(data) is None

    def test_returns_none_when_custom_field_list_absent(self):
        assert extract_wo_from_invoice({}) is None

    def test_returns_none_when_custom_field_is_null(self):
        assert extract_wo_from_invoice({"CustomField": None}) is None

    def test_returns_none_for_non_dict_input(self):
        assert extract_wo_from_invoice(None) is None
        assert extract_wo_from_invoice("not a dict") is None
        assert extract_wo_from_invoice([]) is None

    def test_handles_missing_name_key(self):
        data = {"CustomField": [
            {"StringValue": "LM2603300024/TB00192691"},
        ]}
        # No "Name" key — shouldn't raise, just skip
        assert extract_wo_from_invoice(data) is None

    def test_handles_missing_string_value_key(self):
        data = {"CustomField": [
            {"Name": "NGL REF#"},
        ]}
        assert extract_wo_from_invoice(data) is None

    def test_export_wo_format(self):
        # Export WOs have LE prefix
        data = {"CustomField": [
            {"Name": "NGL REF#", "StringValue": "LE2603300055/XYZ123"},
        ]}
        assert extract_wo_from_invoice(data) == "LE2603300055"


# ── normalize_email_list ─────────────────────────────────────────────


class TestNormalizeEmailList:
    def test_passes_through_clean_emails(self):
        assert normalize_email_list(["a@x.com", "b@y.com"]) == ["a@x.com", "b@y.com"]

    def test_splits_comma_separated(self):
        result = normalize_email_list(["a@x.com, b@y.com"])
        assert result == ["a@x.com", "b@y.com"]

    def test_strips_whitespace(self):
        assert normalize_email_list(["  a@x.com  "]) == ["a@x.com"]

    def test_skips_empty_strings(self):
        assert normalize_email_list(["", "a@x.com", "   "]) == ["a@x.com"]

    def test_handles_empty_list(self):
        assert normalize_email_list([]) == []


# ── validate_and_append_email ────────────────────────────────────────


class TestValidateAndAppendEmail:
    def test_appends_valid_email(self):
        cc = ["existing@x.com"]
        assert validate_and_append_email(cc, "new@x.com") is True
        assert cc == ["existing@x.com", "new@x.com"]

    def test_rejects_empty_email(self):
        cc = []
        assert validate_and_append_email(cc, "") is False
        assert validate_and_append_email(cc, None) is False
        assert cc == []

    def test_rejects_invalid_format(self):
        cc = []
        assert validate_and_append_email(cc, "not-an-email") is False
        assert validate_and_append_email(cc, "missing@tld") is False
        assert validate_and_append_email(cc, "@nolocal.com") is False
        assert cc == []

    def test_strips_whitespace_before_validating(self):
        cc = []
        assert validate_and_append_email(cc, "  valid@x.com  ") is True
        assert cc == ["valid@x.com"]

    def test_deduplicates_case_insensitive(self):
        cc = ["Existing@X.com"]
        assert validate_and_append_email(cc, "existing@x.com") is False
        assert cc == ["Existing@X.com"]  # unchanged
