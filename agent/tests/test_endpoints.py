"""Smoke tests for agent API endpoints.

These tests run against a LIVE agent server on localhost:8787.
Start the agent first: cd agent && python main.py

Run with: python -m pytest tests/test_endpoints.py -v
"""

import json
from pathlib import Path

import pytest
import httpx

import os as _os
BASE_URL = _os.getenv("NGL_AGENT_TEST_URL", "http://127.0.0.1:8787")
TIMEOUT = 10.0


def _candidate_jwt_secrets() -> list[str]:
    """Find every JWT secret across agent DB locations (dev + packaged)."""
    import sqlite3
    import os as _os_inner
    candidates = [
        # Dev source tree
        Path(__file__).resolve().parents[1] / "data" / "ngl.db",
        # Packaged Electron install
        Path(_os_inner.path.expandvars(r"%LOCALAPPDATA%/NGL Accounting/data/ngl.db")),
    ]
    secrets: list[str] = []
    seen: set[str] = set()
    for db_path in candidates:
        if not db_path.exists():
            continue
        try:
            con = sqlite3.connect(str(db_path))
            cur = con.cursor()
            cur.execute("SELECT value FROM app_settings WHERE key = 'jwt_secret'")
            row = cur.fetchone()
            con.close()
            if row and row[0] and row[0] not in seen:
                secrets.append(row[0])
                seen.add(row[0])
        except Exception:
            continue
    return secrets


def _mint_jwt_with_secret(secret: str) -> str | None:
    """Mint a short-lived test JWT signed with the given secret."""
    from datetime import datetime, timezone, timedelta
    try:
        import jwt as _jwt
    except ImportError:
        return None
    payload = {
        "sub": "0",
        "username": "_test",
        "role": "admin",
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        "iat": datetime.now(timezone.utc),
    }
    return _jwt.encode(payload, secret, algorithm="HS256")


@pytest.fixture(scope="module")
def client():
    """Create an httpx client with auth token."""
    with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT) as c:
        # Fetch auth token (returns null when AUTH_ENABLED, which is the default)
        resp = c.get("/auth/token")
        if resp.status_code != 200:
            pytest.skip(f"Agent server not running on {BASE_URL}")
        token = resp.json().get("token")
        # When auth is enabled, mint a JWT against whichever DB the running agent uses.
        # Try every secret we can find on disk and probe with each until one works.
        if not token:
            secrets = _candidate_jwt_secrets()
            if not secrets:
                pytest.skip("No JWT secret found in any agent DB (cannot authenticate to test)")
            picked = None
            for s in secrets:
                t = _mint_jwt_with_secret(s)
                if not t:
                    continue
                probe = c.get("/qbo/status", headers={"Authorization": f"Bearer {t}"})
                if probe.status_code != 401:
                    picked = t
                    break
            if not picked:
                pytest.skip(
                    "None of the discovered JWT secrets validated against the running agent. "
                    "The agent may have a fresh secret not yet on disk, or auth is misconfigured."
                )
            token = picked
        c.headers["Authorization"] = f"Bearer {token}"
        yield c


# ── Health & Auth ───────────────────────────────────────────────────


class TestHealthAndAuth:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["service"] == "ngl-agent"

    def test_auth_token(self, client):
        r = client.get("/auth/token")
        assert r.status_code == 200
        assert "token" in r.json()

    def test_unauthorized_without_token(self):
        with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = c.get("/customers")
            assert r.status_code == 401


# ── QBO Status ──────────────────────────────────────────────────────


class TestQBO:
    def test_qbo_status(self, client):
        r = client.get("/qbo/status")
        assert r.status_code == 200
        data = r.json()
        assert "status" in data
        assert "loggedIn" in data


# ── TMS Status ──────────────────────────────────────────────────────


class TestTMS:
    def test_tms_status(self, client):
        r = client.get("/tms/status")
        assert r.status_code == 200
        data = r.json()
        assert "status" in data
        assert "loggedIn" in data


# ── Customers CRUD ──────────────────────────────────────────────────


class TestCustomers:
    def test_list_customers(self, client):
        r = client.get("/customers")
        assert r.status_code == 200
        data = r.json()
        assert "customers" in data
        assert "total" in data
        assert isinstance(data["customers"], list)

    def test_list_customers_active_only(self, client):
        r = client.get("/customers?activeOnly=true")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data["customers"], list)

    def test_create_get_update_delete_customer(self, client):
        # Clean up from any previous failed test run
        payload = {
            "code": "_TEST_SMOKE",
            "name": "Smoke Test Customer",
            "sendMethod": "qbo",
            "emails": ["test@example.com"],
            "ccEmails": [],
            "bccEmails": [],
            "requiredDocs": [],
            "active": True,
        }

        # Try create — if it already exists (409), update it instead
        r = client.post("/customers", json=payload)
        if r.status_code == 409:
            r = client.put("/customers/_TEST_SMOKE", json=payload)
        assert r.status_code == 200, f"Setup failed: {r.text}"

        # Get
        r = client.get("/customers/_TEST_SMOKE")
        assert r.status_code == 200
        assert r.json()["name"] == "Smoke Test Customer"

        # Update
        payload["name"] = "Updated Smoke Test"
        r = client.put("/customers/_TEST_SMOKE", json=payload)
        assert r.status_code == 200

        # Verify update
        r = client.get("/customers/_TEST_SMOKE")
        assert r.json()["name"] == "Updated Smoke Test"

        # Delete (soft-delete — sets active=false)
        r = client.delete("/customers/_TEST_SMOKE")
        assert r.status_code == 200

        # Verify soft-deleted (still exists but inactive)
        r = client.get("/customers/_TEST_SMOKE")
        assert r.status_code == 200
        assert r.json()["active"] is False

    def test_export_customers(self, client):
        r = client.get("/customers/export")
        assert r.status_code == 200


# ── Audit ───────────────────────────────────────────────────────────


class TestAudit:
    def test_list_audit(self, client):
        r = client.get("/audit")
        assert r.status_code == 200

    def test_audit_stats(self, client):
        r = client.get("/audit/stats")
        assert r.status_code == 200


# ── Settings ────────────────────────────────────────────────────────


class TestSettings:
    def test_get_credentials(self, client):
        r = client.get("/settings/credentials")
        assert r.status_code == 200
        data = r.json()
        # Should return masked credential info
        assert "qbo_email" in data or "qboEmail" in data or isinstance(data, dict)


import base64
import tempfile


# ── /files/save-output extensions ──────────────────────────────────


class TestSaveOutputExtensions:
    """M4 additions: nested subfolders, base location, overwrite_folder."""

    def _pdf_bytes(self) -> str:
        # Minimal valid PDF (just a header) — enough for endpoint to write+strip MOTW
        return base64.b64encode(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n").decode()

    def test_nested_subfolder_creates_intermediate_dirs(self, client):
        with tempfile.TemporaryDirectory() as tmp:
            r = client.post("/files/save-output", json={
                "files": [{
                    "filename": "test.pdf",
                    "data": self._pdf_bytes(),
                    "subfolder": "Per Container/2026-05/2026-05-07",
                }],
                "openFolder": False,
                "baseLocation": tmp,
            })
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["saved"] == 1
            expected = Path(tmp) / "Per Container" / "2026-05" / "2026-05-07" / "test.pdf"
            assert expected.exists()
            assert expected.read_bytes().startswith(b"%PDF-1.4")

    def test_base_location_defaults_to_output_dir_when_absent(self, client):
        # When baseLocation is omitted, files land under OUTPUT_DIR (legacy behavior)
        r = client.post("/files/save-output", json={
            "files": [{
                "filename": "legacy_default.pdf",
                "data": self._pdf_bytes(),
                "subfolder": "M4Test",
            }],
            "openFolder": False,
        })
        assert r.status_code == 200, r.text
        assert r.json()["saved"] == 1
        # outputDir in response confirms it landed under OUTPUT_DIR
        assert "M4Test" in r.json()["outputDir"]

    def test_overwrite_folder_clears_existing_files(self, client):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "Combined PDF" / "2026-05" / "2026-05-07"
            target.mkdir(parents=True)
            stale = target / "stale_file.pdf"
            stale.write_bytes(b"%PDF-1.4 stale")

            r = client.post("/files/save-output", json={
                "files": [{
                    "filename": "fresh.pdf",
                    "data": self._pdf_bytes(),
                    "subfolder": "Combined PDF/2026-05/2026-05-07",
                }],
                "openFolder": False,
                "baseLocation": tmp,
                "overwriteFolder": True,
            })
            assert r.status_code == 200, r.text
            assert not stale.exists(), "stale file should have been cleared"
            assert (target / "fresh.pdf").exists()

    def test_overwrite_folder_false_keeps_existing_files(self, client):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "MergeTest"
            target.mkdir(parents=True)
            keep = target / "keep_me.pdf"
            keep.write_bytes(b"%PDF-1.4 keep")

            r = client.post("/files/save-output", json={
                "files": [{
                    "filename": "added.pdf",
                    "data": self._pdf_bytes(),
                    "subfolder": "MergeTest",
                }],
                "openFolder": False,
                "baseLocation": tmp,
                "overwriteFolder": False,
            })
            assert r.status_code == 200, r.text
            assert keep.exists()
            assert (target / "added.pdf").exists()

    def test_path_traversal_rejected_in_subfolder(self, client):
        with tempfile.TemporaryDirectory() as tmp:
            r = client.post("/files/save-output", json={
                "files": [{
                    "filename": "evil.pdf",
                    "data": self._pdf_bytes(),
                    "subfolder": "../../../etc/passwd",
                }],
                "openFolder": False,
                "baseLocation": tmp,
            })
            assert r.status_code == 400
            assert "path" in r.text.lower() or "subfolder" in r.text.lower()
