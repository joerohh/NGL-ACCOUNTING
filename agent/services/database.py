"""SQLite database for customers, audit log, and users.

Replaces customers.json and audit_log.jsonl with a proper database.
On first run, automatically migrates existing JSON/JSONL data into SQLite.
Original files are renamed to .bak (never deleted).
"""

import json
import logging
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import bcrypt

from config import DATA_DIR

logger = logging.getLogger("ngl.database")

DB_FILE = DATA_DIR / "ngl.db"

_conn: Optional[sqlite3.Connection] = None


def _get_conn() -> sqlite3.Connection:
    """Get or create the module-level SQLite connection."""
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(str(DB_FILE), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
    return _conn


def init_db() -> None:
    """Create tables if they don't exist, then run migration if needed."""
    conn = _get_conn()

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS customers (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            send_method TEXT NOT NULL DEFAULT 'email',
            emails TEXT NOT NULL DEFAULT '[]',
            cc_emails TEXT NOT NULL DEFAULT '[]',
            bcc_emails TEXT NOT NULL DEFAULT '[]',
            required_docs TEXT NOT NULL DEFAULT '[]',
            notes TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            pod_email_to TEXT NOT NULL DEFAULT '[]',
            pod_email_cc TEXT NOT NULL DEFAULT '[]',
            pod_email_subject TEXT NOT NULL DEFAULT '',
            pod_email_body TEXT NOT NULL DEFAULT '',
            portal_url TEXT NOT NULL DEFAULT '',
            portal_client TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            invoice_number TEXT NOT NULL DEFAULT '',
            container_number TEXT NOT NULL DEFAULT '',
            customer_code TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            to_emails TEXT NOT NULL DEFAULT '[]',
            cc_emails TEXT NOT NULL DEFAULT '[]',
            bcc_emails TEXT NOT NULL DEFAULT '[]',
            subject TEXT NOT NULL DEFAULT '',
            attachments_found TEXT NOT NULL DEFAULT '[]',
            attachments_missing TEXT NOT NULL DEFAULT '[]',
            error TEXT,
            job_id TEXT,
            do_sender_email TEXT NOT NULL DEFAULT '',
            do_sender_source TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
        CREATE INDEX IF NOT EXISTS idx_audit_customer ON audit_log(customer_code);
        CREATE INDEX IF NOT EXISTS idx_audit_invoice ON audit_log(invoice_number);

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            display_name TEXT NOT NULL DEFAULT '',
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'operator',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)

    conn.commit()

    # Schema migrations — add columns that didn't exist in earlier versions
    _run_migrations(conn)

    logger.info("Database schema initialized: %s", DB_FILE)

    # Migrate existing data files if the tables are empty
    _migrate_if_needed(conn)

    # If Supabase is configured, swap customer functions to cloud DB
    _maybe_use_supabase()


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Add columns introduced in later versions (idempotent)."""
    # audit_log.username — tracks which user performed the action
    cols = {r[1] for r in conn.execute("PRAGMA table_info(audit_log)").fetchall()}
    if "username" not in cols:
        conn.execute("ALTER TABLE audit_log ADD COLUMN username TEXT NOT NULL DEFAULT ''")
        conn.commit()
        logger.info("Migration: added 'username' column to audit_log")

    # Note: admin user creation is handled by the first-run setup wizard


def _seed_default_admin(conn: sqlite3.Connection) -> None:
    """Create a default admin account on first run."""
    import os
    now = datetime.now(timezone.utc).isoformat()
    admin_user = os.getenv("NGL_ADMIN_USERNAME", "admin")
    admin_pass = os.getenv("NGL_ADMIN_PASSWORD", "admin")
    admin_name = os.getenv("NGL_ADMIN_DISPLAY", "Admin")
    pw_hash = bcrypt.hashpw(admin_pass.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    conn.execute("""
        INSERT INTO users (username, display_name, password_hash, role, active, created_at, updated_at)
        VALUES (?, ?, ?, ?, 1, ?, ?)
    """, (admin_user, admin_name, pw_hash, "admin", now, now))
    conn.commit()
    logger.info("Seeded default admin user: %s", admin_user)


# ──────────────────────────────────────────────────────────────────────
# App Settings (key-value store for runtime config)
# ──────────────────────────────────────────────────────────────────────

def get_setting(key: str) -> Optional[str]:
    """Get an app setting by key. Returns None if not set."""
    conn = _get_conn()
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    """Set an app setting (upsert)."""
    conn = _get_conn()
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
        (key, value, value),
    )
    conn.commit()


def get_or_create_jwt_secret() -> str:
    """Return a persistent JWT signing secret, generating one on first run."""
    existing = get_setting("jwt_secret")
    if existing:
        return existing
    secret = secrets.token_urlsafe(48)
    set_setting("jwt_secret", secret)
    logger.info("Generated new JWT signing secret")
    return secret


def get_user_count() -> int:
    """Return the total number of users (active + inactive)."""
    conn = _get_conn()
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def _migrate_if_needed(conn: sqlite3.Connection) -> None:
    """Import data from customers.json and audit_log.jsonl if tables are empty."""
    customers_json = DATA_DIR / "customers.json"
    audit_jsonl = DATA_DIR / "audit_log.jsonl"

    # Skip entirely if no legacy files exist (common case after first migration)
    if not customers_json.exists() and not audit_jsonl.exists():
        return

    # Migrate customers
    row = conn.execute("SELECT COUNT(*) FROM customers").fetchone()
    if row[0] == 0 and customers_json.exists():
        _migrate_customers(conn, customers_json)

    # Migrate audit log
    row = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()
    if row[0] == 0 and audit_jsonl.exists():
        _migrate_audit_log(conn, audit_jsonl)


def _migrate_customers(conn: sqlite3.Connection, json_file: Path) -> None:
    """Import customers from JSON file into SQLite."""
    try:
        data = json.loads(json_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to read customers.json for migration: %s", e)
        return

    count = 0
    now = datetime.now(timezone.utc).isoformat()

    for code, cust in data.items():
        try:
            conn.execute("""
                INSERT OR IGNORE INTO customers
                (code, name, send_method, emails, cc_emails, bcc_emails,
                 required_docs, notes, active, created_at, updated_at,
                 pod_email_to, pod_email_cc, pod_email_subject, pod_email_body,
                 portal_url, portal_client)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                cust.get("code", code).upper(),
                cust.get("name", ""),
                cust.get("sendMethod", "email"),
                json.dumps(cust.get("emails", [])),
                json.dumps(cust.get("ccEmails", [])),
                json.dumps(cust.get("bccEmails", [])),
                json.dumps(cust.get("requiredDocs", [])),
                cust.get("notes", ""),
                1 if cust.get("active", True) else 0,
                cust.get("createdAt", now),
                cust.get("updatedAt", now),
                json.dumps(cust.get("podEmailTo", [])),
                json.dumps(cust.get("podEmailCc", [])),
                cust.get("podEmailSubject", ""),
                cust.get("podEmailBody", ""),
                cust.get("portalUrl", ""),
                cust.get("portalClient", ""),
            ))
            count += 1
        except Exception as e:
            logger.warning("Failed to migrate customer %s: %s", code, e)

    conn.commit()
    logger.info("Migrated %d customers from JSON to SQLite", count)

    # Rename original file to .bak
    bak = json_file.with_suffix(".json.bak")
    json_file.rename(bak)
    logger.info("Renamed %s → %s", json_file.name, bak.name)


def _migrate_audit_log(conn: sqlite3.Connection, jsonl_file: Path) -> None:
    """Import audit log entries from JSONL file into SQLite."""
    count = 0
    try:
        with open(jsonl_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    _insert_audit_entry(conn, entry, commit=False)
                    count += 1
                except (json.JSONDecodeError, Exception) as e:
                    logger.warning("Failed to migrate audit entry: %s", e)
    except OSError as e:
        logger.error("Failed to read audit_log.jsonl for migration: %s", e)
        return

    conn.commit()
    logger.info("Migrated %d audit log entries from JSONL to SQLite", count)

    # Rename original file to .bak
    bak = jsonl_file.with_suffix(".jsonl.bak")
    jsonl_file.rename(bak)
    logger.info("Renamed %s → %s", jsonl_file.name, bak.name)


# ──────────────────────────────────────────────────────────────────────
# Customer CRUD
# ──────────────────────────────────────────────────────────────────────

def _row_to_customer(row: sqlite3.Row) -> dict:
    """Convert a SQLite Row to the camelCase dict the API returns."""
    return {
        "code": row["code"],
        "name": row["name"],
        "sendMethod": row["send_method"],
        "emails": json.loads(row["emails"]),
        "ccEmails": json.loads(row["cc_emails"]),
        "bccEmails": json.loads(row["bcc_emails"]),
        "requiredDocs": json.loads(row["required_docs"]),
        "notes": row["notes"],
        "active": bool(row["active"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "podEmailTo": json.loads(row["pod_email_to"]),
        "podEmailCc": json.loads(row["pod_email_cc"]),
        "podEmailSubject": row["pod_email_subject"],
        "podEmailBody": row["pod_email_body"],
        "portalUrl": row["portal_url"],
        "portalClient": row["portal_client"],
    }


def list_customers(search: str = "", active_only: bool = True) -> list[dict]:
    """List customers, optionally filtered by search and active status."""
    conn = _get_conn()
    clauses = []
    params = []

    if active_only:
        clauses.append("active = 1")
    if search:
        clauses.append("(UPPER(code) LIKE ? OR UPPER(name) LIKE ?)")
        pattern = f"%{search.upper()}%"
        params.extend([pattern, pattern])

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM customers {where} ORDER BY code", params
    ).fetchall()
    return [_row_to_customer(r) for r in rows]


def get_customer(code: str) -> Optional[dict]:
    """Get a single customer by code. Returns None if not found."""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM customers WHERE code = ?", (code.upper(),)).fetchone()
    return _row_to_customer(row) if row else None


def customer_exists(code: str) -> bool:
    conn = _get_conn()
    row = conn.execute("SELECT 1 FROM customers WHERE code = ?", (code.upper(),)).fetchone()
    return row is not None


def create_customer(data: dict) -> dict:
    """Insert a new customer. data uses camelCase keys."""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    code = data["code"].upper()

    conn.execute("""
        INSERT INTO customers
        (code, name, send_method, emails, cc_emails, bcc_emails,
         required_docs, notes, active, created_at, updated_at,
         pod_email_to, pod_email_cc, pod_email_subject, pod_email_body,
         portal_url, portal_client)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        code,
        data.get("name", ""),
        data.get("sendMethod", "email"),
        json.dumps(data.get("emails", [])),
        json.dumps(data.get("ccEmails", [])),
        json.dumps(data.get("bccEmails", [])),
        json.dumps(data.get("requiredDocs", [])),
        data.get("notes", ""),
        now, now,
        json.dumps(data.get("podEmailTo", [])),
        json.dumps(data.get("podEmailCc", [])),
        data.get("podEmailSubject", ""),
        data.get("podEmailBody", ""),
        data.get("portalUrl", ""),
        data.get("portalClient", ""),
    ))
    conn.commit()
    return get_customer(code)


def update_customer(code: str, data: dict) -> Optional[dict]:
    """Update a customer. Only keys present in data are changed."""
    conn = _get_conn()
    code = code.upper()

    existing = get_customer(code)
    if not existing:
        return None

    field_map = {
        "name": "name",
        "sendMethod": "send_method",
        "emails": "emails",
        "ccEmails": "cc_emails",
        "bccEmails": "bcc_emails",
        "requiredDocs": "required_docs",
        "notes": "notes",
        "active": "active",
        "podEmailTo": "pod_email_to",
        "podEmailCc": "pod_email_cc",
        "podEmailSubject": "pod_email_subject",
        "podEmailBody": "pod_email_body",
        "portalUrl": "portal_url",
        "portalClient": "portal_client",
    }

    sets = []
    params = []
    for camel, col in field_map.items():
        if camel in data and data[camel] is not None:
            val = data[camel]
            if isinstance(val, list):
                val = json.dumps(val)
            elif isinstance(val, bool):
                val = 1 if val else 0
            sets.append(f"{col} = ?")
            params.append(val)

    if not sets:
        return existing

    sets.append("updated_at = ?")
    params.append(datetime.now(timezone.utc).isoformat())
    params.append(code)

    conn.execute(f"UPDATE customers SET {', '.join(sets)} WHERE code = ?", params)
    conn.commit()
    return get_customer(code)


def soft_delete_customer(code: str) -> bool:
    """Set active=0 for a customer. Returns True if found."""
    conn = _get_conn()
    code = code.upper()
    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        "UPDATE customers SET active = 0, updated_at = ? WHERE code = ?",
        (now, code),
    )
    conn.commit()
    return cursor.rowcount > 0


def get_all_customers_dict() -> dict:
    """Return all customers as {code: dict} — used by job manager for lookups."""
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM customers").fetchall()
    return {r["code"]: _row_to_customer(r) for r in rows}


def bulk_import_customers(items: list[dict]) -> dict:
    """Bulk import: update existing, create new. Returns {created, updated}."""
    conn = _get_conn()
    created = 0
    updated = 0
    now = datetime.now(timezone.utc).isoformat()

    for item in items:
        code = item.get("code", "").strip().upper()
        if not code:
            continue

        exists = customer_exists(code)
        if exists:
            update_customer(code, item)
            updated += 1
        else:
            item["code"] = code
            create_customer(item)
            created += 1

    return {"created": created, "updated": updated, "total": created + updated}


# ──────────────────────────────────────────────────────────────────────
# Audit log
# ──────────────────────────────────────────────────────────────────────

def _insert_audit_entry(conn: sqlite3.Connection, entry: dict, commit: bool = True) -> None:
    """Insert a single audit log entry (camelCase keys from SendResult.to_dict())."""
    conn.execute("""
        INSERT INTO audit_log
        (timestamp, invoice_number, container_number, customer_code,
         status, to_emails, cc_emails, bcc_emails, subject,
         attachments_found, attachments_missing, error,
         do_sender_email, do_sender_source, username)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        entry.get("timestamp", datetime.now(timezone.utc).isoformat()),
        entry.get("invoiceNumber", ""),
        entry.get("containerNumber", ""),
        entry.get("customerCode", ""),
        entry.get("status", ""),
        json.dumps(entry.get("toEmails", [])),
        json.dumps(entry.get("ccEmails", [])),
        json.dumps(entry.get("bccEmails", [])),
        entry.get("subject", ""),
        json.dumps(entry.get("attachmentsFound", [])),
        json.dumps(entry.get("attachmentsMissing", [])),
        entry.get("error"),
        entry.get("doSenderEmail", ""),
        entry.get("doSenderSource", ""),
        entry.get("username", ""),
    ))
    if commit:
        conn.commit()


def write_audit_entry(entry: dict) -> None:
    """Write a single audit log entry to the database."""
    try:
        conn = _get_conn()
        _insert_audit_entry(conn, entry)
    except Exception as e:
        logger.error("Failed to write audit log entry: %s", e)


def _row_to_audit_entry(row: sqlite3.Row) -> dict:
    """Convert a SQLite Row to the camelCase dict the API returns."""
    return {
        "id": row["id"],
        "timestamp": row["timestamp"],
        "invoiceNumber": row["invoice_number"],
        "containerNumber": row["container_number"],
        "customerCode": row["customer_code"],
        "status": row["status"],
        "toEmails": json.loads(row["to_emails"]),
        "ccEmails": json.loads(row["cc_emails"]),
        "bccEmails": json.loads(row["bcc_emails"]),
        "subject": row["subject"],
        "attachmentsFound": json.loads(row["attachments_found"]),
        "attachmentsMissing": json.loads(row["attachments_missing"]),
        "error": row["error"],
        "doSenderEmail": row["do_sender_email"],
        "doSenderSource": row["do_sender_source"],
        "username": row["username"] if "username" in row.keys() else "",
    }


def query_audit_log(
    date: str = "",
    customer: str = "",
    status: str = "",
    invoice: str = "",
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """Query audit log with filters. Returns {entries, total, limit, offset}."""
    conn = _get_conn()
    clauses = []
    params = []

    if date:
        clauses.append("timestamp LIKE ?")
        params.append(f"{date}%")
    if customer:
        clauses.append("UPPER(customer_code) = ?")
        params.append(customer.upper())
    if status:
        clauses.append("status = ?")
        params.append(status)
    if invoice:
        clauses.append("UPPER(invoice_number) LIKE ?")
        params.append(f"%{invoice.upper()}%")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    # Total count
    total = conn.execute(f"SELECT COUNT(*) FROM audit_log {where}", params).fetchone()[0]

    # Paginated results
    rows = conn.execute(
        f"SELECT * FROM audit_log {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()

    return {
        "entries": [_row_to_audit_entry(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def audit_stats() -> dict:
    """Summary statistics for today's sending activity."""
    conn = _get_conn()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    rows = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM audit_log WHERE timestamp LIKE ? GROUP BY status",
        (f"{today}%",),
    ).fetchall()

    counts = {r["status"]: r["cnt"] for r in rows}
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
    """Check if an invoice was successfully sent within the last N hours.

    Used to prevent duplicate sends when SSE connection drops and the
    frontend loses track of send status.
    """
    conn = _get_conn()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    row = conn.execute(
        "SELECT 1 FROM audit_log WHERE invoice_number = ? AND status = 'sent' "
        "AND timestamp > ? LIMIT 1",
        (invoice_number, cutoff),
    ).fetchone()
    return row is not None


def get_all_audit_entries() -> list[dict]:
    """Return all audit entries sorted newest first — for CSV export."""
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM audit_log ORDER BY timestamp DESC").fetchall()
    return [_row_to_audit_entry(r) for r in rows]


def close_db() -> None:
    """Close the database connection."""
    global _conn
    if _conn:
        _conn.close()
        _conn = None
        logger.info("Database connection closed")


# ──────────────────────────────────────────────────────────────────────
# Users
# ──────────────────────────────────────────────────────────────────────

def _row_to_user(row: sqlite3.Row) -> dict:
    """Convert a SQLite Row to the dict the API returns (no password_hash)."""
    return {
        "id": row["id"],
        "username": row["username"],
        "displayName": row["display_name"],
        "role": row["role"],
        "active": bool(row["active"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def authenticate_user(username: str, password: str) -> Optional[dict]:
    """Verify username + password. Returns user dict or None."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM users WHERE username = ? AND active = 1", (username,)
    ).fetchone()
    if not row:
        return None
    if not bcrypt.checkpw(password.encode("utf-8"), row["password_hash"].encode("utf-8")):
        return None
    return _row_to_user(row)


def get_user_by_id(user_id: int) -> Optional[dict]:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _row_to_user(row) if row else None


def get_user_by_username(username: str) -> Optional[dict]:
    """Look up a user by username (case-insensitive). Returns user dict or None."""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return _row_to_user(row) if row else None


def create_google_user(email: str, display_name: str) -> dict:
    """Create an operator account for a Google-authenticated user (no password)."""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    # Random password hash — account is Google-only, password login won't work
    pw_hash = bcrypt.hashpw(secrets.token_bytes(32), bcrypt.gensalt()).decode("utf-8")
    conn.execute("""
        INSERT INTO users (username, display_name, password_hash, role, active, created_at, updated_at)
        VALUES (?, ?, ?, 'operator', 1, ?, ?)
    """, (email, display_name, pw_hash, now, now))
    conn.commit()
    return get_user_by_id(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def list_users(active_only: bool = True) -> list[dict]:
    conn = _get_conn()
    if active_only:
        rows = conn.execute("SELECT * FROM users WHERE active = 1 ORDER BY username").fetchall()
    else:
        rows = conn.execute("SELECT * FROM users ORDER BY username").fetchall()
    return [_row_to_user(r) for r in rows]


def create_user(username: str, password: str, display_name: str = "", role: str = "operator") -> dict:
    """Create a new user. Raises ValueError if username taken."""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    pw_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    try:
        conn.execute("""
            INSERT INTO users (username, display_name, password_hash, role, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?)
        """, (username.strip(), display_name.strip() or username.strip(), pw_hash, role, now, now))
        conn.commit()
    except sqlite3.IntegrityError:
        raise ValueError(f"Username '{username}' already exists")
    return get_user_by_id(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def update_user(user_id: int, data: dict) -> Optional[dict]:
    """Update user fields. Supports: displayName, role, active, password."""
    conn = _get_conn()
    existing = get_user_by_id(user_id)
    if not existing:
        return None

    sets = []
    params = []

    if "displayName" in data:
        sets.append("display_name = ?")
        params.append(data["displayName"].strip())
    if "role" in data and data["role"] in ("admin", "operator"):
        sets.append("role = ?")
        params.append(data["role"])
    if "active" in data:
        sets.append("active = ?")
        params.append(1 if data["active"] else 0)
    if "password" in data and data["password"]:
        pw_hash = bcrypt.hashpw(data["password"].encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        sets.append("password_hash = ?")
        params.append(pw_hash)

    if not sets:
        return existing

    sets.append("updated_at = ?")
    params.append(datetime.now(timezone.utc).isoformat())
    params.append(user_id)

    conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()
    return get_user_by_id(user_id)


def delete_user(user_id: int) -> bool:
    """Soft-delete a user (set active=0). Returns True if found."""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        "UPDATE users SET active = 0, updated_at = ? WHERE id = ?", (now, user_id)
    )
    conn.commit()
    return cursor.rowcount > 0


# ──────────────────────────────────────────────────────────────────────
# Supabase override: if configured, swap customer functions to cloud DB
# ──────────────────────────────────────────────────────────────────────

def _maybe_use_supabase() -> None:
    """If SUPABASE_URL is set, override customer functions with Supabase versions.

    Also runs a one-time migration: pushes SQLite customers to Supabase
    if Supabase table is empty but SQLite has data.
    """
    from config import SUPABASE_URL, SUPABASE_SERVICE_KEY
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return

    try:
        from services.supabase_client import (
            list_customers as sb_list,
            get_customer as sb_get,
            customer_exists as sb_exists,
            create_customer as sb_create,
            update_customer as sb_update,
            soft_delete_customer as sb_delete,
            get_all_customers_dict as sb_all_dict,
            bulk_import_customers as sb_bulk_import,
            migrate_to_supabase,
            # Audit log
            write_audit_entry as sb_write_audit,
            query_audit_log as sb_query_audit,
            audit_stats as sb_audit_stats,
            get_all_audit_entries as sb_get_all_audit,
            was_recently_sent as sb_was_recently_sent,
            migrate_audit_to_supabase,
            # Users
            sb_authenticate_user,
            sb_get_user_by_id,
            sb_list_users,
            sb_create_user,
            sb_update_user,
            sb_delete_user,
            migrate_users_to_supabase,
        )
    except Exception as e:
        logger.error("Failed to import supabase_client: %s", e)
        return

    # One-time migration: push SQLite data to Supabase if cloud table is empty
    try:
        existing_cloud = sb_list("", False)
        if not existing_cloud:
            local_customers = list_customers("", False)
            if local_customers:
                count = migrate_to_supabase(local_customers)
                logger.info("One-time migration: pushed %d customers to Supabase", count)
    except Exception as e:
        logger.warning("Supabase customer migration failed (will use cloud anyway): %s", e)

    # One-time migration: push SQLite audit entries to Supabase
    try:
        test_audit = sb_query_audit(limit=1)
        if test_audit["total"] == 0:
            local_audit = get_all_audit_entries()
            if local_audit:
                count = migrate_audit_to_supabase(local_audit)
                logger.info("One-time migration: pushed %d audit entries to Supabase", count)
    except Exception as e:
        logger.warning("Supabase audit migration failed (will use cloud anyway): %s", e)

    # One-time migration: push SQLite users to Supabase
    try:
        cloud_users = sb_list_users(False)
        if not cloud_users:
            conn = _get_conn()
            local_rows = conn.execute("SELECT * FROM users").fetchall()
            if local_rows:
                raw_users = [dict(r) for r in local_rows]
                count = migrate_users_to_supabase(raw_users)
                logger.info("One-time migration: pushed %d users to Supabase", count)
    except Exception as e:
        logger.warning("Supabase user migration failed (will use cloud anyway): %s", e)

    # Override module-level customer functions
    import services.database as _self
    _self.list_customers = sb_list
    _self.get_customer = sb_get
    _self.customer_exists = sb_exists
    _self.create_customer = sb_create
    _self.update_customer = sb_update
    _self.soft_delete_customer = sb_delete
    _self.get_all_customers_dict = sb_all_dict
    _self.bulk_import_customers = sb_bulk_import

    # Override audit log functions
    _self.write_audit_entry = sb_write_audit
    _self.query_audit_log = sb_query_audit
    _self.audit_stats = sb_audit_stats
    _self.get_all_audit_entries = sb_get_all_audit
    _self.was_recently_sent = sb_was_recently_sent

    # Override user functions
    _self.authenticate_user = sb_authenticate_user
    _self.get_user_by_id = sb_get_user_by_id
    _self.list_users = sb_list_users
    _self.create_user = sb_create_user
    _self.update_user = sb_update_user
    _self.delete_user = sb_delete_user

    logger.info("Customer data source: Supabase (%s)", SUPABASE_URL)
    logger.info("Audit log data source: Supabase")
    logger.info("User data source: Supabase")
