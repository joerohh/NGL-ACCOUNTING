"""Audit log endpoints — query, export, and stats for invoice sending history."""

import csv
import io
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

import services.database as db

logger = logging.getLogger("ngl.audit")

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
async def query_audit(
    date: str = Query("", description="Filter by date (YYYY-MM-DD)"),
    customer: str = Query("", description="Filter by customer code"),
    status: str = Query("", description="Filter by status (sent, skipped, error, mismatch, missing_docs)"),
    invoice: str = Query("", description="Filter by invoice number"),
    limit: int = Query(100, ge=1, le=1000, description="Max entries to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
):
    return db.query_audit_log(date, customer, status, invoice, limit, offset)


@router.get("/export")
async def export_audit():
    """Export the full audit log as a CSV download."""
    entries = db.get_all_audit_entries()

    output = io.StringIO()
    fieldnames = [
        "timestamp", "invoiceNumber", "containerNumber", "customerCode",
        "status", "toEmails", "ccEmails", "subject",
        "attachmentsFound", "attachmentsMissing", "error",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()

    for entry in entries:
        row = dict(entry)
        for key in ("toEmails", "ccEmails", "attachmentsFound", "attachmentsMissing"):
            if isinstance(row.get(key), list):
                row[key] = ", ".join(row[key])
        writer.writerow(row)

    output.seek(0)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="audit_log_{today}.csv"'},
    )


@router.get("/stats")
async def stats():
    return db.audit_stats()
