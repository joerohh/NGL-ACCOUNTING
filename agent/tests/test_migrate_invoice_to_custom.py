"""Tests for migrate_invoice_to_custom() — v2.69 data fixup.

Customers with 'invoice' in their requiredDocs should be moved onto
sendMethod='custom' so they show correctly in the new dropdown.
The migration must:
  - move 'email' customers with 'invoice' in requiredDocs to 'custom'
  - leave OEC customers alone (their flow doesn't use requiredDocs)
  - leave portal_upload customers alone (separate path)
  - leave customers without 'invoice' in requiredDocs alone
  - be idempotent (running twice is a no-op)
  - preserve the full requiredDocs list (including 'invoice')

Patches list_customers / update_customer to avoid hitting a real DB.
"""
from unittest.mock import patch, MagicMock


@patch("services.database.update_customer")
@patch("services.database.list_customers")
def test_migrates_email_customer_with_invoice(mock_list, mock_update):
    from services.database import migrate_invoice_to_custom

    mock_list.return_value = [
        {"code": "APEXMA01", "sendMethod": "email", "requiredDocs": ["invoice"]},
    ]
    migrate_invoice_to_custom()

    mock_update.assert_called_once_with("APEXMA01", {
        "sendMethod": "custom",
        "requiredDocs": ["invoice"],
    })


@patch("services.database.update_customer")
@patch("services.database.list_customers")
def test_migrates_customer_with_invoice_and_other_docs(mock_list, mock_update):
    from services.database import migrate_invoice_to_custom

    mock_list.return_value = [
        {"code": "X01", "sendMethod": "email", "requiredDocs": ["invoice", "pod"]},
    ]
    migrate_invoice_to_custom()

    mock_update.assert_called_once_with("X01", {
        "sendMethod": "custom",
        "requiredDocs": ["invoice", "pod"],
    })


@patch("services.database.update_customer")
@patch("services.database.list_customers")
def test_does_not_touch_oec_customers(mock_list, mock_update):
    from services.database import migrate_invoice_to_custom

    mock_list.return_value = [
        {"code": "OEC01", "sendMethod": "qbo_invoice_only_then_pod_email",
         "requiredDocs": ["invoice", "pod"]},
    ]
    migrate_invoice_to_custom()

    mock_update.assert_not_called()


@patch("services.database.update_customer")
@patch("services.database.list_customers")
def test_does_not_touch_portal_upload_customers(mock_list, mock_update):
    from services.database import migrate_invoice_to_custom

    mock_list.return_value = [
        {"code": "PORT01", "sendMethod": "portal_upload",
         "requiredDocs": ["invoice"]},
    ]
    migrate_invoice_to_custom()

    mock_update.assert_not_called()


@patch("services.database.update_customer")
@patch("services.database.list_customers")
def test_does_not_touch_customers_without_invoice(mock_list, mock_update):
    from services.database import migrate_invoice_to_custom

    mock_list.return_value = [
        {"code": "STD01", "sendMethod": "email", "requiredDocs": ["pod"]},
        {"code": "STD02", "sendMethod": "email", "requiredDocs": []},
    ]
    migrate_invoice_to_custom()

    mock_update.assert_not_called()


@patch("services.database.update_customer")
@patch("services.database.list_customers")
def test_is_idempotent_on_already_migrated_customers(mock_list, mock_update):
    """Second run after migration sees customers already on 'custom'. No-op."""
    from services.database import migrate_invoice_to_custom

    mock_list.return_value = [
        {"code": "APEXMA01", "sendMethod": "custom", "requiredDocs": ["invoice"]},
    ]
    migrate_invoice_to_custom()

    mock_update.assert_not_called()


@patch("services.database.update_customer")
@patch("services.database.list_customers")
def test_case_insensitive_invoice_match(mock_list, mock_update):
    """'Invoice' (capital I) should also trigger migration."""
    from services.database import migrate_invoice_to_custom

    mock_list.return_value = [
        {"code": "CASE01", "sendMethod": "email", "requiredDocs": ["Invoice"]},
    ]
    migrate_invoice_to_custom()

    mock_update.assert_called_once_with("CASE01", {
        "sendMethod": "custom",
        "requiredDocs": ["Invoice"],
    })


@patch("services.database.update_customer")
@patch("services.database.list_customers")
def test_migration_calls_list_customers_with_no_filter(mock_list, mock_update):
    """Must scan all customers including inactive — defensive."""
    from services.database import migrate_invoice_to_custom

    mock_list.return_value = []
    migrate_invoice_to_custom()

    mock_list.assert_called_once_with("", False)
