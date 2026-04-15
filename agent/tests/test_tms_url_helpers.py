"""Unit tests for TMS invoice-prefix parsing + bc-detail URL type mapping.

These tests construct TMSBrowser but never call async methods that would
spawn Playwright — they exercise pure-Python helpers only.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.tms_browser import TMSBrowser


@pytest.fixture
def tms():
    return TMSBrowser()


# ── parse_invoice_prefix ─────────────────────────────────────────────


class TestParseInvoicePrefix:
    def test_la_import(self, tms):
        assert tms.parse_invoice_prefix("LM26040290F") == ("LA", "imp", "IMPORT")

    def test_la_export(self, tms):
        assert tms.parse_invoice_prefix("LE26040290F") == ("LA", "exp", "EXPORT")

    def test_phoenix_import(self, tms):
        assert tms.parse_invoice_prefix("PM26040290F") == ("PHX", "imp", "IMPORT")

    def test_houston_export(self, tms):
        assert tms.parse_invoice_prefix("HE26040290F") == ("HOU", "exp", "EXPORT")

    def test_case_insensitive(self, tms):
        assert tms.parse_invoice_prefix("lm26040290f") == ("LA", "imp", "IMPORT")

    def test_strips_whitespace(self, tms):
        assert tms.parse_invoice_prefix("  LM26040290F  ") == ("LA", "imp", "IMPORT")

    def test_empty_returns_nones(self, tms):
        assert tms.parse_invoice_prefix("") == (None, None, None)

    def test_none_returns_nones(self, tms):
        assert tms.parse_invoice_prefix(None) == (None, None, None)

    def test_short_input_returns_nones(self, tms):
        assert tms.parse_invoice_prefix("L") == (None, None, None)

    def test_unknown_location_returns_nones(self, tms):
        assert tms.parse_invoice_prefix("ZM26040290F") == (None, None, None)

    def test_unknown_type_returns_nones(self, tms):
        assert tms.parse_invoice_prefix("LZ26040290F") == (None, None, None)


# ── bc_detail_type_segment ───────────────────────────────────────────


class TestBcDetailTypeSegment:
    def test_import_invoice_returns_import(self, tms):
        assert tms.bc_detail_type_segment("LM26040290F") == "import"

    def test_export_invoice_returns_export(self, tms):
        assert tms.bc_detail_type_segment("LE26040290F") == "export"

    def test_export_invoice_any_location_returns_export(self, tms):
        # All locations that export: P, H, S, M
        assert tms.bc_detail_type_segment("PE26040290F") == "export"
        assert tms.bc_detail_type_segment("HE26040290F") == "export"

    def test_unknown_invoice_returns_none(self, tms):
        # Non-import/export (e.g., if a VAN-type prefix existed)
        assert tms.bc_detail_type_segment("LX26040290F") is None

    def test_empty_returns_none(self, tms):
        assert tms.bc_detail_type_segment("") is None

    def test_none_returns_none(self, tms):
        assert tms.bc_detail_type_segment(None) is None

    def test_builds_expected_url_pattern(self, tms):
        """Smoke test: confirm type segment + WO# build the URL we see in the browser."""
        invoice = "LM26040290F"
        wo_no = "LM2603300024"
        detail_type = tms.bc_detail_type_segment(invoice)
        assert detail_type == "import"
        url_fragment = f"/bc-detail/detail-info/{detail_type}/{wo_no}"
        assert url_fragment == "/bc-detail/detail-info/import/LM2603300024"

    def test_document_url_pattern(self, tms):
        """Document tab URL uses same type segment."""
        detail_type = tms.bc_detail_type_segment("LE26040291F")
        url_fragment = f"/bc-detail/document/{detail_type}/LE2603300055"
        assert url_fragment == "/bc-detail/document/export/LE2603300055"
