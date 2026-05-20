"""Unit tests for agent/services/database.py functions."""

from pathlib import Path

import pytest

# Add agent/ to path so imports work
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _setup_clean_db(monkeypatch, tmp_path):
    """Helper: redirect database module to a temp DB and run init_db().

    Also stubs out _maybe_use_supabase so the local SQLite implementations
    of user-functions stay in place — otherwise, if the .env has Supabase
    configured, init_db() will swap module-level functions over to the cloud
    client and our local SQLite assertions become meaningless.
    """
    from services import database as db
    test_db = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_FILE", test_db)
    # Prevent Supabase swap-over during init_db()
    monkeypatch.setattr(db, "_maybe_use_supabase", lambda: None)
    # Force the module to drop its cached connection so the next _get_conn() opens the new file
    db._conn = None
    db.init_db()
    return db


def test_create_google_user_defaults_to_operator(monkeypatch, tmp_path):
    """Without an explicit role, create_google_user creates an operator."""
    db = _setup_clean_db(monkeypatch, tmp_path)
    user = db.create_google_user("test1@ngltrans.net", "Test One")
    assert user["role"] == "operator"
    assert user["username"] == "test1@ngltrans.net"
    assert user["displayName"] == "Test One"
    assert user["active"] is True


def test_create_google_user_admin_role(monkeypatch, tmp_path):
    """When role='admin' is passed, the user is created as admin."""
    db = _setup_clean_db(monkeypatch, tmp_path)
    user = db.create_google_user("test2@ngltrans.net", "Test Two", role="admin")
    assert user["role"] == "admin"


def test_create_google_user_rejects_invalid_role(monkeypatch, tmp_path):
    """Invalid role raises ValueError before any SQL runs."""
    db = _setup_clean_db(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        db.create_google_user("test3@ngltrans.net", "Test Three", role="superuser")
