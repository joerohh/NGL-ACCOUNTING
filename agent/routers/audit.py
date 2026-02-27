"""Audit log endpoints — query, export, and stats for invoice sending history."""

import csv
import io
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from config import AUDIT_LOG_FILE

logger = logging.getLogger("ngl.audit")

router = APIRouter(prefix="/audit", tags=["audit"])


def _read_audit_log() -> list[dict]:
    """Read all entries from the JSONL audit log."""
    if not AUDIT_LOG_FILE.exists():
        return []
    entries = []
    try:
        with open(AUDIT_LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError as e:
        logger.warning("Failed to read audit log: %s", e)
    return entries


@router.get("")
async def query_audit_log(
    date: str = Query("", description="Filter by date (YYYY-MM-DD)"),
    customer: str = Query("", description="Filter by customer code"),
    status: str = Query("", description="Filter by status (sent, skipped, error, mismatch, missing_docs)"),
    invoice: str = Query("", description="Filter by invoice number"),
    limit: int = Query(100, ge=1, le=1000, description="Max entries to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
):
    """Query the audit log with optional filters."""
    entries = _read_audit_log()

    # Apply filters
    if date:
        entries = [e for e in entries if e.get("timestamp", "").startswith(date)]
    if customer:
        customer_upper = customer.upper()
        entries = [e for e in entries if e.get("customerCode", "").upper() == customer_upper]
    if status:
        entries = [e for e in entries if e.get("status") == status]
    if invoice:
        entries = [e for e in entries if invoice.lower() in e.get("invoiceNumber", "").lower()]

    # Sort by timestamp descending (newest first)
    entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)

    total = len(entries)
    entries = entries[offset: offset + limit]

    return {"entries": entries, "total": total, "limit": limit, "offset": offset}


@router.get("/export")
async def export_audit_log():
    """Export the full audit log as a CSV download."""
    entries = _read_audit_log()
    entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)

    # Build CSV in memory
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
        # Convert lists to comma-separated strings for CSV
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
async def audit_stats():
    """Summary statistics for today's sending activity."""
    entries = _read_audit_log()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    today_entries = [e for e in entries if e.get("timestamp", "").startswith(today)]

    sent = sum(1 for e in today_entries if e.get("status") == "sent")
    skipped = sum(1 for e in today_entries if e.get("status") == "skipped")
    errors = sum(1 for e in today_entries if e.get("status") == "error")
    mismatches = sum(1 for e in today_entries if e.get("status") == "mismatch")
    missing_docs = sum(1 for e in today_entries if e.get("status") == "missing_docs")
    total = len(today_entries)

    return {
        "date": today,
        "total": total,
        "sent": sent,
        "skipped": skipped,
        "errors": errors,
        "mismatches": mismatches,
        "missingDocs": missing_docs,
        "successRate": f"{(sent / total * 100):.1f}%" if total > 0 else "N/A",
    }
