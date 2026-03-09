"""Supabase REST client for shared customer data.

Uses httpx to call Supabase's PostgREST API with the service_role key.
All functions match the signatures in database.py so they can be swapped in transparently.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from config import SUPABASE_URL, SUPABASE_SERVICE_KEY

logger = logging.getLogger("ngl.supabase")

_BASE = f"{SUPABASE_URL}/rest/v1"
_HEADERS = {
    "apikey": SUPABASE_SERVICE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}
_TIMEOUT = 15.0


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

# Supabase JSONB columns come back as native Python lists — no json.loads needed.
# We only need camelCase ↔ snake_case conversion.

_SNAKE_TO_CAMEL = {
    "code": "code",
    "name": "name",
    "send_method": "sendMethod",
    "emails": "emails",
    "cc_emails": "ccEmails",
    "bcc_emails": "bccEmails",
    "required_docs": "requiredDocs",
    "notes": "notes",
    "active": "active",
    "created_at": "createdAt",
    "updated_at": "updatedAt",
    "pod_email_to": "podEmailTo",
    "pod_email_cc": "podEmailCc",
    "pod_email_subject": "podEmailSubject",
    "pod_email_body": "podEmailBody",
    "portal_url": "portalUrl",
    "portal_client": "portalClient",
}

_CAMEL_TO_SNAKE = {v: k for k, v in _SNAKE_TO_CAMEL.items()}


def _sb_row_to_customer(row: dict) -> dict:
    """Convert a Supabase row (snake_case) to camelCase dict for the API."""
    return {camel: row.get(snake, "" if camel != "active" else True)
            for snake, camel in _SNAKE_TO_CAMEL.items()}


def _customer_to_sb_row(data: dict) -> dict:
    """Convert camelCase input dict to snake_case for Supabase writes."""
    row = {}
    for camel, val in data.items():
        snake = _CAMEL_TO_SNAKE.get(camel)
        if snake:
            row[snake] = val
    return row


def _check_response(resp: httpx.Response, operation: str) -> None:
    """Raise a clear error if the Supabase request failed."""
    if resp.status_code >= 400:
        logger.error("Supabase %s failed (%d): %s", operation, resp.status_code, resp.text)
        resp.raise_for_status()


# ──────────────────────────────────────────────────────────────────────
# Customer CRUD  (same signatures as database.py)
# ──────────────────────────────────────────────────────────────────────

def list_customers(search: str = "", active_only: bool = True) -> list[dict]:
    """List customers, optionally filtered by search and active status."""
    params = "select=*&order=code"
    if active_only:
        params += "&active=eq.true"
    if search:
        pattern = f"%{search}%"
        params += f"&or=(code.ilike.{pattern},name.ilike.{pattern})"

    resp = httpx.get(f"{_BASE}/customers?{params}", headers=_HEADERS, timeout=_TIMEOUT)
    _check_response(resp, "list_customers")
    return [_sb_row_to_customer(r) for r in resp.json()]


def get_customer(code: str) -> Optional[dict]:
    """Get a single customer by code."""
    resp = httpx.get(
        f"{_BASE}/customers?code=eq.{code.upper()}&limit=1",
        headers=_HEADERS, timeout=_TIMEOUT,
    )
    _check_response(resp, "get_customer")
    rows = resp.json()
    return _sb_row_to_customer(rows[0]) if rows else None


def customer_exists(code: str) -> bool:
    """Check if a customer exists."""
    headers = {**_HEADERS, "Prefer": "count=exact"}
    resp = httpx.head(
        f"{_BASE}/customers?code=eq.{code.upper()}",
        headers=headers, timeout=_TIMEOUT,
    )
    count = resp.headers.get("content-range", "*/0").split("/")[-1]
    return int(count) > 0


def create_customer(data: dict) -> dict:
    """Insert a new customer."""
    now = datetime.now(timezone.utc).isoformat()
    row = _customer_to_sb_row(data)
    row["code"] = data["code"].upper()
    row["active"] = True
    row["created_at"] = now
    row["updated_at"] = now

    resp = httpx.post(
        f"{_BASE}/customers", headers=_HEADERS, timeout=_TIMEOUT,
        json=row,
    )
    _check_response(resp, "create_customer")
    return _sb_row_to_customer(resp.json()[0])


def update_customer(code: str, data: dict) -> Optional[dict]:
    """Update a customer. Only keys present in data are changed."""
    code = code.upper()
    row = _customer_to_sb_row(data)
    if not row:
        return get_customer(code)

    row["updated_at"] = datetime.now(timezone.utc).isoformat()

    resp = httpx.patch(
        f"{_BASE}/customers?code=eq.{code}",
        headers=_HEADERS, timeout=_TIMEOUT,
        json=row,
    )
    _check_response(resp, "update_customer")
    rows = resp.json()
    return _sb_row_to_customer(rows[0]) if rows else None


def soft_delete_customer(code: str) -> bool:
    """Set active=false for a customer."""
    code = code.upper()
    now = datetime.now(timezone.utc).isoformat()
    resp = httpx.patch(
        f"{_BASE}/customers?code=eq.{code}",
        headers=_HEADERS, timeout=_TIMEOUT,
        json={"active": False, "updated_at": now},
    )
    _check_response(resp, "soft_delete_customer")
    return len(resp.json()) > 0


def get_all_customers_dict() -> dict:
    """Return all customers as {code: dict} — used by job manager for lookups."""
    resp = httpx.get(
        f"{_BASE}/customers?select=*", headers=_HEADERS, timeout=_TIMEOUT,
    )
    _check_response(resp, "get_all_customers_dict")
    return {r["code"]: _sb_row_to_customer(r) for r in resp.json()}


def bulk_import_customers(items: list[dict]) -> dict:
    """Bulk import: update existing, create new."""
    created = 0
    updated = 0

    for item in items:
        code = item.get("code", "").strip().upper()
        if not code:
            continue

        if customer_exists(code):
            update_customer(code, item)
            updated += 1
        else:
            item["code"] = code
            create_customer(item)
            created += 1

    return {"created": created, "updated": updated, "total": created + updated}


# ──────────────────────────────────────────────────────────────────────
# Migration: push SQLite data to Supabase (one-time)
# ──────────────────────────────────────────────────────────────────────

def migrate_to_supabase(sqlite_customers: list[dict]) -> int:
    """Push existing SQLite customers to Supabase. Skips duplicates (upsert).

    Args:
        sqlite_customers: list of camelCase customer dicts from SQLite.

    Returns:
        Number of customers upserted.
    """
    if not sqlite_customers:
        return 0

    rows = []
    for cust in sqlite_customers:
        row = _customer_to_sb_row(cust)
        row["code"] = cust["code"].upper()
        row["active"] = cust.get("active", True)
        row["created_at"] = cust.get("createdAt", datetime.now(timezone.utc).isoformat())
        row["updated_at"] = cust.get("updatedAt", datetime.now(timezone.utc).isoformat())
        rows.append(row)

    # Supabase upsert: on conflict (code), update all fields
    headers = {**_HEADERS, "Prefer": "return=representation,resolution=merge-duplicates"}
    resp = httpx.post(
        f"{_BASE}/customers?on_conflict=code",
        headers=headers, timeout=30.0,
        json=rows,
    )
    _check_response(resp, "migrate_to_supabase")
    count = len(resp.json())
    logger.info("Migrated %d customers to Supabase", count)
    return count
