"""Tests for the invoice email template — defensive amount rendering."""
import sys
from pathlib import Path

# Ensure the agent package is importable when running this file directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.email_template import build_invoice_email_html


def _build(amount):
    """Tiny wrapper to keep call sites readable."""
    return build_invoice_email_html(
        invoice_number="SM26050083F",
        container="ONEU1928762",
        customer_name="HL LOGISTICS AMERICA CORP.",
        amount=amount,
    )


# ── Happy path: numeric amount renders as currency ────────────────

def test_numeric_amount_renders_as_currency():
    html = _build("1234.56")
    assert "$1,234.56" in html


def test_numeric_int_renders_as_currency():
    html = _build(2450)
    assert "$2,450.00" in html


# ── The bug we're fixing: non-numeric amount must NOT leak ────────

def test_customer_code_in_amount_field_does_not_leak_as_dollar_string():
    """Regression: Lorena's Excel mapped a customer-code column ("BILL")
    onto the amount field, then the email body rendered "$HLLOGI01" in
    the giant amount-due box. The template must NEVER prepend "$" to
    non-numeric junk."""
    html = _build("HLLOGI01")
    assert "$HLLOGI01" not in html, \
        f"Expected the customer code NOT to render as $HLLOGI01, but it did:\n{html[:600]}"


def test_arbitrary_string_amount_does_not_leak():
    html = _build("totally bogus value")
    assert "$totally bogus value" not in html
    assert "$totally" not in html


def test_empty_amount_does_not_render_dollar_sign():
    html = _build("")
    # Should not produce a bare "$" or "$ " in the amount box
    assert ">$<" not in html and "> $<" not in html


def test_none_amount_does_not_crash_and_does_not_leak():
    html = _build(None)
    assert "$None" not in html


# ── Edge cases that should still work ─────────────────────────────

def test_zero_amount_renders():
    html = _build("0")
    assert "$0.00" in html


def test_negative_amount_renders():
    html = _build("-50.25")
    assert "-50.25" in html or "$-50.25" in html or "($50.25)" in html


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
