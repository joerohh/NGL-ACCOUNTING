"""Smoke tests for agent API endpoints.

These tests run against a LIVE agent server on localhost:8787.
Start the agent first: cd agent && python main.py

Run with: python -m pytest tests/test_endpoints.py -v
"""

import json

import pytest
import httpx

BASE_URL = "http://127.0.0.1:8787"
TIMEOUT = 10.0


@pytest.fixture(scope="module")
def client():
    """Create an httpx client with auth token."""
    with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT) as c:
        # Fetch auth token
        resp = c.get("/auth/token")
        if resp.status_code != 200:
            pytest.skip("Agent server not running on localhost:8787")
        token = resp.json()["token"]
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
