"""Customer management endpoints — CRUD, bulk import/export."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config import CUSTOMERS_FILE

logger = logging.getLogger("ngl.customers")

router = APIRouter(prefix="/customers", tags=["customers"])

# File-level lock for safe concurrent writes
_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

VALID_SEND_METHODS = {"email", "qbo_invoice_only_then_pod_email", "portal_upload", "portal"}


class CustomerCreate(BaseModel):
    code: str
    name: str
    emails: list[str] = []
    ccEmails: list[str] = []
    bccEmails: list[str] = []
    requiredDocs: list[str] = []
    sendMethod: str = "email"
    notes: str = ""
    # OEC flow fields
    podEmailTo: list[str] = []
    podEmailCc: list[str] = []
    podEmailSubject: str = ""
    podEmailBody: str = ""
    # Portal flow fields
    portalUrl: str = ""
    portalClient: str = ""


class CustomerUpdate(BaseModel):
    name: str | None = None
    emails: list[str] | None = None
    ccEmails: list[str] | None = None
    bccEmails: list[str] | None = None
    requiredDocs: list[str] | None = None
    sendMethod: str | None = None
    notes: str | None = None
    active: bool | None = None
    # OEC flow fields
    podEmailTo: list[str] | None = None
    podEmailCc: list[str] | None = None
    podEmailSubject: str | None = None
    podEmailBody: str | None = None
    # Portal flow fields
    portalUrl: str | None = None
    portalClient: str | None = None


class BulkImportItem(BaseModel):
    code: str
    name: str
    emails: list[str] = []
    ccEmails: list[str] = []
    bccEmails: list[str] = []
    requiredDocs: list[str] = []
    sendMethod: str = "email"
    notes: str = ""
    podEmailTo: list[str] = []
    podEmailCc: list[str] = []
    podEmailSubject: str = ""
    podEmailBody: str = ""
    portalUrl: str = ""
    portalClient: str = ""


class BulkImportRequest(BaseModel):
    customers: list[BulkImportItem]


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def _read_customers() -> dict:
    """Read the customers JSON file. Returns empty dict if missing/invalid."""
    if not CUSTOMERS_FILE.exists():
        return {}
    try:
        return json.loads(CUSTOMERS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read customers file: %s", e)
        return {}


def _write_customers(data: dict) -> None:
    """Write the customers dict to the JSON file."""
    CUSTOMERS_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_send_method(method: str) -> str:
    """Return a valid send method, defaulting to 'email'."""
    return method if method in VALID_SEND_METHODS else "email"


def _apply_method_fields(cust: dict, req) -> None:
    """Copy method-specific fields onto the customer dict."""
    m = cust.get("sendMethod", "email")
    if m == "qbo_invoice_only_then_pod_email":
        cust["podEmailTo"] = [e.strip() for e in (getattr(req, "podEmailTo", None) or []) if e.strip()]
        cust["podEmailCc"] = [e.strip() for e in (getattr(req, "podEmailCc", None) or []) if e.strip()]
        cust["podEmailSubject"] = (getattr(req, "podEmailSubject", None) or "").strip()
        cust["podEmailBody"] = (getattr(req, "podEmailBody", None) or "").strip()
    else:
        for k in ("podEmailTo", "podEmailCc", "podEmailSubject", "podEmailBody"):
            cust.pop(k, None)
    if m in ("portal_upload", "portal"):
        cust["portalUrl"] = (getattr(req, "portalUrl", None) or "").strip()
        cust["portalClient"] = (getattr(req, "portalClient", None) or "").strip()
    else:
        for k in ("portalUrl", "portalClient"):
            cust.pop(k, None)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("")
async def list_customers(
    search: str = Query("", description="Filter by code or name (case-insensitive)"),
    active_only: bool = Query(True, alias="activeOnly", description="Only return active customers"),
):
    """List all customers, optionally filtered."""
    customers = _read_customers()
    results = []
    search_lower = search.lower()

    for cust in customers.values():
        if active_only and not cust.get("active", True):
            continue
        if search_lower and search_lower not in cust.get("code", "").lower() \
                and search_lower not in cust.get("name", "").lower():
            continue
        results.append(cust)

    results.sort(key=lambda c: c.get("code", ""))
    return {"customers": results, "total": len(results)}


@router.get("/export")
async def export_customers():
    """Export all customers as JSON."""
    customers = _read_customers()
    return JSONResponse(
        content=list(customers.values()),
        headers={"Content-Disposition": 'attachment; filename="customers.json"'},
    )


@router.get("/{code}")
async def get_customer(code: str):
    """Get a single customer by code."""
    customers = _read_customers()
    code_upper = code.upper()
    if code_upper not in customers:
        raise HTTPException(404, f"Customer not found: {code_upper}")
    return customers[code_upper]


@router.post("")
async def create_customer(req: CustomerCreate):
    """Create a new customer."""
    async with _lock:
        customers = _read_customers()
        code_upper = req.code.strip().upper()

        if not code_upper:
            raise HTTPException(400, "Customer code is required")

        if code_upper in customers:
            raise HTTPException(409, f"Customer already exists: {code_upper}")

        send_method = _validate_send_method(req.sendMethod)
        now = _now_iso()
        customers[code_upper] = {
            "code": code_upper,
            "name": req.name.strip(),
            "emails": [e.strip() for e in req.emails if e.strip()],
            "ccEmails": [e.strip() for e in req.ccEmails if e.strip()],
            "bccEmails": [e.strip() for e in req.bccEmails if e.strip()],
            "requiredDocs": req.requiredDocs,
            "sendMethod": send_method,
            "notes": req.notes.strip(),
            "active": True,
            "createdAt": now,
            "updatedAt": now,
        }
        _apply_method_fields(customers[code_upper], req)
        _write_customers(customers)

    logger.info("Created customer: %s", code_upper)
    return customers[code_upper]


@router.put("/{code}")
async def update_customer(code: str, req: CustomerUpdate):
    """Update an existing customer."""
    async with _lock:
        customers = _read_customers()
        code_upper = code.upper()

        if code_upper not in customers:
            raise HTTPException(404, f"Customer not found: {code_upper}")

        cust = customers[code_upper]

        if req.name is not None:
            cust["name"] = req.name.strip()
        if req.emails is not None:
            cust["emails"] = [e.strip() for e in req.emails if e.strip()]
        if req.ccEmails is not None:
            cust["ccEmails"] = [e.strip() for e in req.ccEmails if e.strip()]
        if req.bccEmails is not None:
            cust["bccEmails"] = [e.strip() for e in req.bccEmails if e.strip()]
        if req.requiredDocs is not None:
            cust["requiredDocs"] = req.requiredDocs
        if req.sendMethod is not None:
            cust["sendMethod"] = _validate_send_method(req.sendMethod)
        if req.notes is not None:
            cust["notes"] = req.notes.strip()
        if req.active is not None:
            cust["active"] = req.active
        _apply_method_fields(cust, req)

        cust["updatedAt"] = _now_iso()
        _write_customers(customers)

    logger.info("Updated customer: %s", code_upper)
    return cust


@router.delete("/{code}")
async def delete_customer(code: str):
    """Soft-delete a customer (sets active=false)."""
    async with _lock:
        customers = _read_customers()
        code_upper = code.upper()

        if code_upper not in customers:
            raise HTTPException(404, f"Customer not found: {code_upper}")

        customers[code_upper]["active"] = False
        customers[code_upper]["updatedAt"] = _now_iso()
        _write_customers(customers)

    logger.info("Soft-deleted customer: %s", code_upper)
    return {"status": "deleted", "code": code_upper}


@router.post("/import")
async def import_customers(req: BulkImportRequest):
    """Bulk import customers. Existing codes are updated, new codes are created."""
    async with _lock:
        customers = _read_customers()
        created = 0
        updated = 0
        now = _now_iso()

        for item in req.customers:
            code_upper = item.code.strip().upper()
            if not code_upper:
                continue

            send_method = _validate_send_method(item.sendMethod)

            if code_upper in customers:
                # Update existing
                cust = customers[code_upper]
                cust["name"] = item.name.strip()
                cust["emails"] = [e.strip() for e in item.emails if e.strip()]
                cust["ccEmails"] = [e.strip() for e in item.ccEmails if e.strip()]
                cust["bccEmails"] = [e.strip() for e in item.bccEmails if e.strip()]
                cust["requiredDocs"] = item.requiredDocs
                cust["sendMethod"] = send_method
                cust["notes"] = item.notes.strip()
                cust["updatedAt"] = now
                _apply_method_fields(cust, item)
                updated += 1
            else:
                # Create new
                customers[code_upper] = {
                    "code": code_upper,
                    "name": item.name.strip(),
                    "emails": [e.strip() for e in item.emails if e.strip()],
                    "ccEmails": [e.strip() for e in item.ccEmails if e.strip()],
                    "bccEmails": [e.strip() for e in item.bccEmails if e.strip()],
                    "requiredDocs": item.requiredDocs,
                    "sendMethod": send_method,
                    "notes": item.notes.strip(),
                    "active": True,
                    "createdAt": now,
                    "updatedAt": now,
                }
                _apply_method_fields(customers[code_upper], item)
                created += 1

        _write_customers(customers)

    logger.info("Bulk import: %d created, %d updated", created, updated)
    return {"status": "ok", "created": created, "updated": updated, "total": created + updated}
