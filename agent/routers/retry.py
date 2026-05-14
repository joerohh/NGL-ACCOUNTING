"""Retry + verification endpoints for in-app invoice recovery (Fix 2 of v2.62)."""
import json
import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from services.claude_classifier import ClaudeClassifier

logger = logging.getLogger("ngl.retry")
router = APIRouter(prefix="/jobs", tags=["retry"])

# Injected by main.py at startup
_job_manager = None


def set_job_manager(jm):
    global _job_manager
    _job_manager = jm


# Map upload slot → classifier doc_type that the slot accepts.
# Note: the classifier collapses BOL→POD, so the BOL slot also accepts "pod".
SLOT_ACCEPTS = {
    "POD": {"pod"},
    "BOL": {"pod", "bol"},   # classifier currently returns 'pod' for BOLs too
    "POL": {"pol"},
    "DO":  {"do"},
    "PL":  {"pl"},
}


@router.post("/verify-file")
async def verify_file(slot: str, file: UploadFile = File(...)) -> dict:
    """Classify an uploaded file and report whether it matches the requested slot.

    Query: slot (POD/BOL/POL/DO/PL)
    Body: multipart with 'file' field
    Returns: {doc_type, confidence, matches_slot, detected_as, container_hint, skipped_api}
    """
    slot_upper = slot.upper()
    if slot_upper not in SLOT_ACCEPTS:
        raise HTTPException(400, f"Unknown slot: {slot}")

    suffix = Path(file.filename or "upload").suffix or ".pdf"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = Path(tmp.name)

    try:
        clf = ClaudeClassifier()
        result = await clf.classify(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    accepts = SLOT_ACCEPTS[slot_upper]
    matches = result.doc_type in accepts
    return {
        "doc_type": result.doc_type,
        "confidence": result.confidence,
        "matches_slot": matches,
        "detected_as": None if matches else result.doc_type,
        "container_hint": result.container_hint,
        "skipped_api": result.skipped_api,
    }


@router.post("/retry-invoice")
async def retry_invoice(
    request: Request,
    invoice: str = Form(...),
    slots: str = Form(...),
) -> dict:
    """Multipart retry endpoint.

    Form fields:
        invoice: JSON string with invoice metadata
        slots: JSON array of {slot, verified_by}
        <slot>_file: file blob per slot (e.g. pod_file, bol_file). Slot name is lowercase.

    Returns: {status, error, attachments_sent}
    """
    if not _job_manager:
        raise HTTPException(503, "Agent not initialized")

    try:
        invoice_dict = json.loads(invoice)
        slots_list = json.loads(slots)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"Bad JSON in form: {e}")

    if not isinstance(slots_list, list):
        raise HTTPException(400, "'slots' must be a JSON array")

    form = await request.form()
    files_for_job = []
    for slot_meta in slots_list:
        slot_name = slot_meta.get("slot", "").upper()
        if not slot_name:
            raise HTTPException(400, "slot missing in slots[]")
        field_name = f"{slot_name.lower()}_file"
        upload = form.get(field_name)
        # Starlette's UploadFile lacks a stable isinstance check across versions —
        # duck-type on `.read` instead.
        if upload is None or not hasattr(upload, "read"):
            raise HTTPException(400, f"Missing file for slot {slot_name} (expected form field '{field_name}')")
        suffix = Path(upload.filename or "f").suffix or ".pdf"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(await upload.read())
            files_for_job.append({
                "slot": slot_name,
                "path": tmp.name,
                "verified_by": slot_meta.get("verified_by", "filename"),
            })

    try:
        result = await _job_manager.retry_invoice(invoice=invoice_dict, files=files_for_job)
        return result
    except Exception as exc:
        logger.error("retry_invoice failed: %s", exc, exc_info=True)
        raise HTTPException(500, f"Retry failed: {exc}")
