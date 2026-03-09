"""Customer management endpoints — CRUD, bulk import/export."""

import logging
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from services.database import (
    list_customers as db_list_customers,
    get_customer as db_get_customer,
    customer_exists,
    create_customer as db_create_customer,
    update_customer as db_update_customer,
    soft_delete_customer,
    bulk_import_customers,
)

logger = logging.getLogger("ngl.customers")

router = APIRouter(prefix="/customers", tags=["customers"])


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
    podEmailTo: list[str] = []
    podEmailCc: list[str] = []
    podEmailSubject: str = ""
    podEmailBody: str = ""
    portalUrl: str = ""
    portalClient: str = ""


class CustomerUpdate(BaseModel):
    name: Optional[str] = None
    emails: Optional[List[str]] = None
    ccEmails: Optional[List[str]] = None
    bccEmails: Optional[List[str]] = None
    requiredDocs: Optional[List[str]] = None
    sendMethod: Optional[str] = None
    notes: Optional[str] = None
    active: Optional[bool] = None
    podEmailTo: Optional[List[str]] = None
    podEmailCc: Optional[List[str]] = None
    podEmailSubject: Optional[str] = None
    podEmailBody: Optional[str] = None
    portalUrl: Optional[str] = None
    portalClient: Optional[str] = None


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
# Helpers
# ---------------------------------------------------------------------------

def _validate_send_method(method: str) -> str:
    return method if method in VALID_SEND_METHODS else "email"


def _clean_emails(emails: list[str]) -> list[str]:
    return [e.strip() for e in emails if e.strip()]


def _prepare_customer_data(req) -> dict:
    """Convert a Pydantic model to the dict format expected by the database."""
    data = {}
    fields = req.model_fields_set if hasattr(req, "model_fields_set") else set(vars(req).keys())
    for field in fields:
        val = getattr(req, field, None)
        if val is None:
            continue
        if field in ("emails", "ccEmails", "bccEmails", "podEmailTo", "podEmailCc"):
            data[field] = _clean_emails(val)
        elif field in ("name", "notes", "podEmailSubject", "podEmailBody", "portalUrl", "portalClient"):
            data[field] = val.strip() if isinstance(val, str) else val
        elif field == "sendMethod":
            data[field] = _validate_send_method(val)
        else:
            data[field] = val
    return data


def _apply_method_fields(data: dict, is_update: bool = False) -> dict:
    """Clear method-specific fields that don't apply to the current send method.

    For updates: explicitly set irrelevant fields to empty so the DB clears them.
    For creates: just remove them (DB defaults handle it).
    """
    m = data.get("sendMethod", "email")
    if m != "qbo_invoice_only_then_pod_email":
        for k in ("podEmailTo", "podEmailCc", "podEmailSubject", "podEmailBody"):
            if is_update:
                data[k] = [] if k in ("podEmailTo", "podEmailCc") else ""
            else:
                data.pop(k, None)
    if m not in ("portal_upload", "portal"):
        for k in ("portalUrl", "portalClient"):
            if is_update:
                data[k] = ""
            else:
                data.pop(k, None)
    return data


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("")
async def list_customers(
    search: str = Query("", description="Filter by code or name (case-insensitive)"),
    active_only: bool = Query(True, alias="activeOnly", description="Only return active customers"),
):
    results = db_list_customers(search, active_only)
    return {"customers": results, "total": len(results)}


@router.get("/export")
async def export_customers():
    results = db_list_customers("", False)
    return JSONResponse(
        content=results,
        headers={"Content-Disposition": 'attachment; filename="customers.json"'},
    )


@router.get("/{code}")
async def get_customer(code: str):
    cust = db_get_customer(code)
    if not cust:
        raise HTTPException(404, f"Customer not found: {code.upper()}")
    return cust


@router.post("")
async def create_customer(req: CustomerCreate):
    code_upper = req.code.strip().upper()
    if not code_upper:
        raise HTTPException(400, "Customer code is required")
    if customer_exists(code_upper):
        raise HTTPException(409, f"Customer already exists: {code_upper}")

    data = _prepare_customer_data(req)
    data["code"] = code_upper
    data = _apply_method_fields(data)
    cust = db_create_customer(data)
    logger.info("Created customer: %s", code_upper)
    return cust


@router.put("/{code}")
async def update_customer(code: str, req: CustomerUpdate):
    code_upper = code.upper()
    if not db_get_customer(code_upper):
        raise HTTPException(404, f"Customer not found: {code_upper}")

    data = _prepare_customer_data(req)
    data = _apply_method_fields(data, is_update=True)
    cust = db_update_customer(code_upper, data)
    logger.info("Updated customer: %s", code_upper)
    return cust


@router.delete("/{code}")
async def delete_customer(code: str):
    code_upper = code.upper()
    if not soft_delete_customer(code_upper):
        raise HTTPException(404, f"Customer not found: {code_upper}")
    logger.info("Soft-deleted customer: %s", code_upper)
    return {"status": "deleted", "code": code_upper}


@router.post("/import")
async def import_customers(req: BulkImportRequest):
    items = []
    for item in req.customers:
        data = _prepare_customer_data(item)
        data["code"] = item.code.strip().upper()
        data = _apply_method_fields(data)
        items.append(data)

    # Run in thread pool to avoid blocking the event loop
    # (Supabase client uses synchronous httpx calls)
    import asyncio
    result = await asyncio.to_thread(bulk_import_customers, items)
    logger.info("Bulk import: %d created, %d updated", result["created"], result["updated"])
    return {"status": "ok", **result}
