"""v2.69: ensure the customer router accepts and persists sendMethod='custom'.

Before the fix to VALID_SEND_METHODS, the router silently coerced any
unknown sendMethod to 'email'. This test pins the contract so a future
maintainer who tweaks the validator can't accidentally re-introduce the
silent downgrade.
"""
from unittest.mock import patch


def test_custom_send_method_passes_validation():
    """The validator must let 'custom' through unchanged."""
    from routers.customers import _validate_send_method
    assert _validate_send_method("custom") == "custom"


def test_known_send_methods_pass_validation():
    """Sanity: the existing whitelist still works."""
    from routers.customers import _validate_send_method
    for m in ("email", "qbo_invoice_only_then_pod_email", "portal_upload", "portal", "custom"):
        assert _validate_send_method(m) == m, f"{m} should pass through unchanged"


def test_unknown_send_method_still_coerces_to_email():
    """Defensive: any other unknown value still falls back to email."""
    from routers.customers import _validate_send_method
    assert _validate_send_method("totally_made_up") == "email"
    assert _validate_send_method("") == "email"


def test_valid_send_methods_set_includes_custom():
    """Pin VALID_SEND_METHODS membership directly so source-grep audits
    catch any future deletion."""
    from routers.customers import VALID_SEND_METHODS
    assert "custom" in VALID_SEND_METHODS
