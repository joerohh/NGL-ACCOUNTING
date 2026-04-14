"""Supabase REST client for shared customer data.

Uses httpx to call Supabase's PostgREST API with the service_role key.
All functions match the signatures in database.py so they can be swapped in transparently.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
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


# ──────────────────────────────────────────────────────────────────────
# Audit log  (same signatures as database.py)
# ──────────────────────────────────────────────────────────────────────

def write_audit_entry(entry: dict) -> None:
    """Write a single audit log entry to Supabase."""
    row = {
        "timestamp": entry.get("timestamp", datetime.now(timezone.utc).isoformat()),
        "invoice_number": entry.get("invoiceNumber", ""),
        "container_number": entry.get("containerNumber", ""),
        "customer_code": entry.get("customerCode", ""),
        "status": entry.get("status", ""),
        "to_emails": entry.get("toEmails", []),
        "cc_emails": entry.get("ccEmails", []),
        "bcc_emails": entry.get("bccEmails", []),
        "subject": entry.get("subject", ""),
        "attachments_found": entry.get("attachmentsFound", []),
        "attachments_missing": entry.get("attachmentsMissing", []),
        "error": entry.get("error"),
        "job_id": entry.get("jobId", ""),
        "do_sender_email": entry.get("doSenderEmail", ""),
        "do_sender_source": entry.get("doSenderSource", ""),
        "username": entry.get("username", ""),
    }
    try:
        headers = {**_HEADERS, "Prefer": "return=minimal"}
        resp = httpx.post(f"{_BASE}/audit_log", headers=headers, timeout=_TIMEOUT, json=row)
        _check_response(resp, "write_audit_entry")
    except Exception as e:
        logger.error("Failed to write audit entry to Supabase: %s", e)


def _sb_row_to_audit(row: dict) -> dict:
    """Convert Supabase audit row to camelCase dict."""
    return {
        "id": row.get("id"),
        "timestamp": row.get("timestamp", ""),
        "invoiceNumber": row.get("invoice_number", ""),
        "containerNumber": row.get("container_number", ""),
        "customerCode": row.get("customer_code", ""),
        "status": row.get("status", ""),
        "toEmails": row.get("to_emails", []),
        "ccEmails": row.get("cc_emails", []),
        "bccEmails": row.get("bcc_emails", []),
        "subject": row.get("subject", ""),
        "attachmentsFound": row.get("attachments_found", []),
        "attachmentsMissing": row.get("attachments_missing", []),
        "error": row.get("error"),
        "doSenderEmail": row.get("do_sender_email", ""),
        "doSenderSource": row.get("do_sender_source", ""),
        "username": row.get("username", ""),
    }


def query_audit_log(
    date: str = "",
    customer: str = "",
    status: str = "",
    invoice: str = "",
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """Query audit log with filters from Supabase."""
    params = "select=*"
    count_params = "select=id"

    filters = ""
    if date:
        filters += f"&timestamp=gte.{date}T00:00:00&timestamp=lt.{date}T23:59:59.999Z"
    if customer:
        filters += f"&customer_code=ilike.{customer.upper()}"
    if status:
        filters += f"&status=eq.{status}"
    if invoice:
        filters += f"&invoice_number=ilike.*{invoice.upper()}*"

    # Get total count
    count_headers = {**_HEADERS, "Prefer": "count=exact"}
    count_resp = httpx.head(
        f"{_BASE}/audit_log?{count_params}{filters}",
        headers=count_headers, timeout=_TIMEOUT,
    )
    total_str = count_resp.headers.get("content-range", "*/0").split("/")[-1]
    total = int(total_str) if total_str != "*" else 0

    # Get paginated results
    resp = httpx.get(
        f"{_BASE}/audit_log?{params}{filters}&order=timestamp.desc&limit={limit}&offset={offset}",
        headers=_HEADERS, timeout=_TIMEOUT,
    )
    _check_response(resp, "query_audit_log")

    return {
        "entries": [_sb_row_to_audit(r) for r in resp.json()],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def audit_stats() -> dict:
    """Summary statistics for today's sending activity from Supabase."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    resp = httpx.get(
        f"{_BASE}/audit_log?select=status&timestamp=gte.{today}T00:00:00&timestamp=lt.{today}T23:59:59.999Z",
        headers=_HEADERS, timeout=_TIMEOUT,
    )
    _check_response(resp, "audit_stats")
    rows = resp.json()

    counts = {}
    for r in rows:
        s = r.get("status", "")
        counts[s] = counts.get(s, 0) + 1

    total = sum(counts.values())
    sent = counts.get("sent", 0)

    return {
        "date": today,
        "total": total,
        "sent": sent,
        "skipped": counts.get("skipped", 0),
        "errors": counts.get("error", 0),
        "mismatches": counts.get("mismatch", 0),
        "missingDocs": counts.get("missing_docs", 0),
        "successRate": f"{(sent / total * 100):.1f}%" if total > 0 else "N/A",
    }


def was_recently_sent(invoice_number: str, hours: int = 6) -> bool:
    """Check if an invoice was successfully sent within the last N hours."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    resp = httpx.get(
        f"{_BASE}/audit_log?select=id&invoice_number=eq.{invoice_number}"
        f"&status=eq.sent&timestamp=gte.{cutoff}&limit=1",
        headers=_HEADERS, timeout=_TIMEOUT,
    )
    _check_response(resp, "was_recently_sent")
    return len(resp.json()) > 0


def get_all_audit_entries() -> list:
    """Return all audit entries sorted newest first — for CSV export."""
    resp = httpx.get(
        f"{_BASE}/audit_log?select=*&order=timestamp.desc",
        headers=_HEADERS, timeout=30.0,
    )
    _check_response(resp, "get_all_audit_entries")
    return [_sb_row_to_audit(r) for r in resp.json()]


def migrate_audit_to_supabase(sqlite_entries: list) -> int:
    """Push existing SQLite audit entries to Supabase (one-time)."""
    if not sqlite_entries:
        return 0

    rows = []
    for e in sqlite_entries:
        rows.append({
            "timestamp": e.get("timestamp", ""),
            "invoice_number": e.get("invoiceNumber", ""),
            "container_number": e.get("containerNumber", ""),
            "customer_code": e.get("customerCode", ""),
            "status": e.get("status", ""),
            "to_emails": e.get("toEmails", []),
            "cc_emails": e.get("ccEmails", []),
            "bcc_emails": e.get("bccEmails", []),
            "subject": e.get("subject", ""),
            "attachments_found": e.get("attachmentsFound", []),
            "attachments_missing": e.get("attachmentsMissing", []),
            "error": e.get("error"),
            "do_sender_email": e.get("doSenderEmail", ""),
            "do_sender_source": e.get("doSenderSource", ""),
            "username": e.get("username", ""),
        })

    # Batch insert
    headers = {**_HEADERS, "Prefer": "return=minimal"}
    resp = httpx.post(f"{_BASE}/audit_log", headers=headers, timeout=60.0, json=rows)
    _check_response(resp, "migrate_audit_to_supabase")
    logger.info("Migrated %d audit entries to Supabase", len(rows))
    return len(rows)


# ──────────────────────────────────────────────────────────────────────
# Users  (same signatures as database.py)
# ──────────────────────────────────────────────────────────────────────

def _sb_row_to_user(row: dict) -> dict:
    """Convert Supabase user row to camelCase dict (no password_hash)."""
    return {
        "id": row.get("id"),
        "username": row.get("username", ""),
        "displayName": row.get("display_name", ""),
        "role": row.get("role", "operator"),
        "active": bool(row.get("active", True)),
        "createdAt": row.get("created_at", ""),
        "updatedAt": row.get("updated_at", ""),
    }


def sb_authenticate_user(username: str, password: str) -> Optional[dict]:
    """Verify username + password against Supabase users table."""
    import bcrypt
    resp = httpx.get(
        f"{_BASE}/users?username=eq.{username}&active=eq.true&limit=1&select=*",
        headers=_HEADERS, timeout=_TIMEOUT,
    )
    _check_response(resp, "authenticate_user")
    rows = resp.json()
    if not rows:
        return None
    row = rows[0]
    if not bcrypt.checkpw(password.encode("utf-8"), row["password_hash"].encode("utf-8")):
        return None
    return _sb_row_to_user(row)


def sb_get_user_by_id(user_id: int) -> Optional[dict]:
    resp = httpx.get(
        f"{_BASE}/users?id=eq.{user_id}&limit=1",
        headers=_HEADERS, timeout=_TIMEOUT,
    )
    _check_response(resp, "get_user_by_id")
    rows = resp.json()
    return _sb_row_to_user(rows[0]) if rows else None


def sb_list_users(active_only: bool = True) -> list:
    params = "select=*&order=username"
    if active_only:
        params += "&active=eq.true"
    resp = httpx.get(f"{_BASE}/users?{params}", headers=_HEADERS, timeout=_TIMEOUT)
    _check_response(resp, "list_users")
    return [_sb_row_to_user(r) for r in resp.json()]


def sb_create_user(username: str, password: str, display_name: str = "", role: str = "operator") -> dict:
    """Create a new user in Supabase. Raises ValueError if username taken."""
    import bcrypt
    now = datetime.now(timezone.utc).isoformat()
    pw_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    row = {
        "username": username.strip(),
        "display_name": (display_name.strip() or username.strip()),
        "password_hash": pw_hash,
        "role": role,
        "active": True,
        "created_at": now,
        "updated_at": now,
    }

    resp = httpx.post(f"{_BASE}/users", headers=_HEADERS, timeout=_TIMEOUT, json=row)
    if resp.status_code == 409 or (resp.status_code >= 400 and "duplicate" in resp.text.lower()):
        raise ValueError(f"Username '{username}' already exists")
    _check_response(resp, "create_user")
    return _sb_row_to_user(resp.json()[0])


def sb_update_user(user_id: int, data: dict) -> Optional[dict]:
    """Update user fields in Supabase."""
    import bcrypt
    row = {}
    if "displayName" in data:
        row["display_name"] = data["displayName"].strip()
    if "role" in data and data["role"] in ("admin", "operator"):
        row["role"] = data["role"]
    if "active" in data:
        row["active"] = bool(data["active"])
    if "password" in data and data["password"]:
        pw_hash = bcrypt.hashpw(data["password"].encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        row["password_hash"] = pw_hash

    if not row:
        return sb_get_user_by_id(user_id)

    row["updated_at"] = datetime.now(timezone.utc).isoformat()

    resp = httpx.patch(
        f"{_BASE}/users?id=eq.{user_id}",
        headers=_HEADERS, timeout=_TIMEOUT, json=row,
    )
    _check_response(resp, "update_user")
    rows = resp.json()
    return _sb_row_to_user(rows[0]) if rows else None


def sb_delete_user(user_id: int) -> bool:
    """Soft-delete user in Supabase."""
    now = datetime.now(timezone.utc).isoformat()
    resp = httpx.patch(
        f"{_BASE}/users?id=eq.{user_id}",
        headers=_HEADERS, timeout=_TIMEOUT,
        json={"active": False, "updated_at": now},
    )
    _check_response(resp, "delete_user")
    return len(resp.json()) > 0


# ──────────────────────────────────────────────────────────────────────
# QBO OAuth tokens (shared across all installs — single row)
# ──────────────────────────────────────────────────────────────────────

def load_qbo_tokens() -> Optional[dict]:
    """Load the shared QBO OAuth tokens from Supabase. Returns None if not connected."""
    try:
        resp = httpx.get(
            f"{_BASE}/qbo_tokens?id=eq.1&limit=1",
            headers=_HEADERS, timeout=_TIMEOUT,
        )
        _check_response(resp, "load_qbo_tokens")
        rows = resp.json()
        if not rows:
            return None
        row = rows[0]
        import time
        return {
            "access_token": row["access_token"],
            "refresh_token": row["refresh_token"],
            "realm_id": row["realm_id"],
            "access_token_expires_at": _iso_to_ts(row["expires_at"]),
            "refresh_token_expires_at": row.get("refresh_token_expires_at_ts") or (time.time() + 8726400),
            "created_at": _iso_to_ts(row.get("updated_at")) or time.time(),
        }
    except Exception as e:
        logger.warning("Failed to load QBO tokens from Supabase: %s", e)
        return None


def upsert_qbo_tokens(tokens: dict) -> bool:
    """Save QBO OAuth tokens to Supabase (upsert into single-row table)."""
    try:
        row = {
            "id": 1,
            "realm_id": tokens.get("realm_id", ""),
            "access_token": tokens["access_token"],
            "refresh_token": tokens["refresh_token"],
            "expires_at": _ts_to_iso(tokens.get("access_token_expires_at", 0)),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        headers = {**_HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"}
        resp = httpx.post(
            f"{_BASE}/qbo_tokens?on_conflict=id",
            headers=headers, timeout=_TIMEOUT, json=row,
        )
        _check_response(resp, "upsert_qbo_tokens")
        return True
    except Exception as e:
        logger.error("Failed to save QBO tokens to Supabase: %s", e)
        return False


def delete_qbo_tokens() -> bool:
    """Remove QBO tokens from Supabase (called on disconnect)."""
    try:
        resp = httpx.delete(
            f"{_BASE}/qbo_tokens?id=eq.1",
            headers=_HEADERS, timeout=_TIMEOUT,
        )
        _check_response(resp, "delete_qbo_tokens")
        return True
    except Exception as e:
        logger.error("Failed to delete QBO tokens from Supabase: %s", e)
        return False


def _ts_to_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _iso_to_ts(iso: Optional[str]) -> Optional[float]:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def migrate_users_to_supabase(sqlite_users: list) -> int:
    """Push existing SQLite users to Supabase (one-time, preserves password hashes)."""
    if not sqlite_users:
        return 0

    # Need to read the raw rows with password_hash from SQLite
    # This function receives raw dicts with password_hash included
    rows = []
    for u in sqlite_users:
        rows.append({
            "username": u["username"],
            "display_name": u.get("display_name", u.get("displayName", "")),
            "password_hash": u["password_hash"],
            "role": u.get("role", "operator"),
            "active": bool(u.get("active", True)),
            "created_at": u.get("created_at", u.get("createdAt", datetime.now(timezone.utc).isoformat())),
            "updated_at": u.get("updated_at", u.get("updatedAt", datetime.now(timezone.utc).isoformat())),
        })

    headers = {**_HEADERS, "Prefer": "return=representation,resolution=merge-duplicates"}
    resp = httpx.post(
        f"{_BASE}/users?on_conflict=username",
        headers=headers, timeout=30.0, json=rows,
    )
    _check_response(resp, "migrate_users_to_supabase")
    count = len(resp.json())
    logger.info("Migrated %d users to Supabase", count)
    return count
