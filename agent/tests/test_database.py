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


def test_sb_get_user_by_username_returns_user_when_found(monkeypatch):
    """sb_get_user_by_username returns a user dict when Supabase finds one."""
    from services import supabase_client as sc
    class FakeResp:
        status_code = 200
        text = ""
        def raise_for_status(self): pass
        def json(self):
            return [{
                "id": 1, "username": "alice@ngltrans.net", "display_name": "Alice",
                "role": "operator", "active": True,
                "created_at": "2026-05-19T00:00:00Z", "updated_at": "2026-05-19T00:00:00Z",
                "password_hash": "$2b$12$..."
            }]
    monkeypatch.setattr(sc.httpx, "get", lambda *a, **k: FakeResp())
    result = sc.sb_get_user_by_username("alice@ngltrans.net")
    assert result is not None
    assert result["username"] == "alice@ngltrans.net"
    assert result["role"] == "operator"


def test_sb_get_user_by_username_returns_none_when_missing(monkeypatch):
    """sb_get_user_by_username returns None when Supabase has no match."""
    from services import supabase_client as sc
    class FakeResp:
        status_code = 200
        text = ""
        def raise_for_status(self): pass
        def json(self):
            return []
    monkeypatch.setattr(sc.httpx, "get", lambda *a, **k: FakeResp())
    result = sc.sb_get_user_by_username("nobody@ngltrans.net")
    assert result is None


def test_sb_create_google_user_rejects_invalid_role():
    """sb_create_google_user rejects roles outside admin/operator."""
    import pytest
    from services import supabase_client as sc
    with pytest.raises(ValueError):
        sc.sb_create_google_user("test@ngltrans.net", "Test", role="superuser")


def test_update_user_blocks_demoting_last_admin(monkeypatch, tmp_path):
    """The only active admin cannot be demoted to operator."""
    db = _setup_clean_db(monkeypatch, tmp_path)
    # Create exactly one admin
    admin = db.create_user("solo_admin", "abcd1234", "Solo Admin", "admin")
    with pytest.raises(ValueError, match="last active admin"):
        db.update_user(admin["id"], {"role": "operator"})


def test_update_user_blocks_deactivating_last_admin(monkeypatch, tmp_path):
    """The only active admin cannot be deactivated."""
    db = _setup_clean_db(monkeypatch, tmp_path)
    admin = db.create_user("solo_admin", "abcd1234", "Solo Admin", "admin")
    with pytest.raises(ValueError, match="last active admin"):
        db.update_user(admin["id"], {"active": False})


def test_update_user_allows_demoting_when_other_admins_exist(monkeypatch, tmp_path):
    """When two admins exist, either can be demoted to operator."""
    db = _setup_clean_db(monkeypatch, tmp_path)
    admin_a = db.create_user("admin_a", "abcd1234", "Admin A", "admin")
    admin_b = db.create_user("admin_b", "abcd1234", "Admin B", "admin")
    result = db.update_user(admin_a["id"], {"role": "operator"})
    assert result is not None
    assert result["role"] == "operator"


def test_update_user_allows_deactivating_when_other_admins_exist(monkeypatch, tmp_path):
    """When two admins exist, either can be deactivated."""
    db = _setup_clean_db(monkeypatch, tmp_path)
    admin_a = db.create_user("admin_a", "abcd1234", "Admin A", "admin")
    admin_b = db.create_user("admin_b", "abcd1234", "Admin B", "admin")
    result = db.update_user(admin_a["id"], {"active": False})
    assert result is not None
    assert result["active"] is False
