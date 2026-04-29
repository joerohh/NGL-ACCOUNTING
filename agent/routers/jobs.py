"""Job endpoints — create fetch jobs and stream progress."""

from dataclasses import asdict
from typing import Literal, Optional
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])

# These get injected by main.py on startup
_job_manager = None


def set_job_manager(jm):
    global _job_manager
    _job_manager = jm


class ContainerItem(BaseModel):
    containerNumber: str
    invoiceNumber: str


class FetchRequest(BaseModel):
    containers: list[ContainerItem]
    doc_types: list[str] = ["invoice", "pod"]


class SendInvoiceItem(BaseModel):
    invoiceNumber: str
    containerNumber: str
    customerCode: str
    amount: str = ""
    subject: str = ""
    doSenderEmail: str = ""
    isResend: bool = False


class SendRequest(BaseModel):
    invoices: list[SendInvoiceItem]
    testMode: bool = False


@router.post("/fetch-missing")
async def create_fetch_job(req: FetchRequest):
    """Start a new background job to fetch missing invoices & PODs from QBO."""
    if not _job_manager:
        raise HTTPException(503, "Agent not initialized")

    if not req.containers:
        raise HTTPException(400, "No containers provided")

    containers = [c.model_dump() for c in req.containers]
    try:
        job = _job_manager.create_job(containers, doc_types=req.doc_types)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _job_manager.start_job(job.id)

    return {"jobId": job.id, "total": job.total, "status": "running"}


@router.get("/{job_id}/status")
async def get_job_status(job_id: str):
    """Get current status of a fetch job."""
    if not _job_manager:
        raise HTTPException(503, "Agent not initialized")

    job = _job_manager.get_job(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id} not found")

    return job.to_dict()


@router.get("/{job_id}/stream")
async def stream_job_events(job_id: str):
    """SSE stream of real-time progress events for a job."""
    if not _job_manager:
        raise HTTPException(503, "Agent not initialized")

    job = _job_manager.get_job(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id} not found")

    return EventSourceResponse(_job_manager.event_stream(job_id))


@router.post("/{job_id}/pause")
async def pause_job(job_id: str):
    """Pause a running fetch job."""
    if not _job_manager:
        raise HTTPException(503, "Agent not initialized")

    job = _job_manager.get_job(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id} not found")

    if job.status != "running":
        raise HTTPException(400, f"Job is not running (status: {job.status})")

    job.status = "paused"
    return {"jobId": job.id, "status": "paused", "progress": job.progress, "total": job.total}


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str):
    """Force-cancel a running send job — clears the stuck 'running' flag
    so a new send can be started, even if the underlying Playwright call is wedged.
    """
    if not _job_manager:
        raise HTTPException(503, "Agent not initialized")

    try:
        result = await _job_manager.cancel_send_job(job_id)
        return {"jobId": job_id, **result}
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/send-invoices")
async def create_send_job(req: SendRequest):
    """Start a new background job to send invoices through QBO."""
    if not _job_manager:
        raise HTTPException(503, "Agent not initialized")

    if not req.invoices:
        raise HTTPException(400, "No invoices provided")

    invoices = [inv.model_dump() for inv in req.invoices]
    try:
        job = _job_manager.create_send_job(invoices, test_mode=req.testMode)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _job_manager.start_send_job(job.id)

    return {"jobId": job.id, "total": job.total, "status": "running", "testMode": req.testMode}


@router.post("/{job_id}/approve-send")
async def approve_send(job_id: str, request: Request):
    """Approve sending the current invoice in test mode.

    Accepts optional JSON body: { "ccOverride": ["email1", "email2"] }
    to override the CC list for OEC POD emails.
    """
    if not _job_manager:
        raise HTTPException(503, "Agent not initialized")

    # Parse optional CC override from request body
    cc_override: Optional[list[str]] = None
    try:
        body = await request.json()
        if body and "ccOverride" in body:
            raw = body["ccOverride"]
            if isinstance(raw, list):
                cc_override = [e.strip() for e in raw if isinstance(e, str) and e.strip()]
    except Exception:
        pass  # No body or invalid JSON — that's fine, proceed without override

    try:
        _job_manager.approve_current_send(job_id, approve=True, cc_override=cc_override)
        return {"status": "approved", "jobId": job_id,
                "ccOverride": cc_override if cc_override else None}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{job_id}/skip-send")
async def skip_send(job_id: str):
    """Skip (reject) sending the current invoice in test mode."""
    if not _job_manager:
        raise HTTPException(503, "Agent not initialized")
    try:
        _job_manager.approve_current_send(job_id, approve=False)
        return {"status": "skipped", "jobId": job_id}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/{job_id}/failed-rows")
async def get_failed_rows(job_id: str, request: Request):
    """List failed rows for a job. UI polls this on SSE event or page load."""
    layer = getattr(request.app.state, "tms_data", None)
    if layer is None:
        raise HTTPException(503, "TMS Data Layer not initialized")
    rows = layer.get_failed_rows(job_id)
    return {"rows": [asdict(r) for r in rows]}


@router.post("/{job_id}/failed-rows/{row_id}/retry")
async def retry_failed_row(
    job_id: str,
    row_id: str,
    request: Request,
    source: Literal["api", "browser"] = Query(..., description="api or browser"),
):
    """Retry one failed row using the chosen source."""
    layer = getattr(request.app.state, "tms_data", None)
    if layer is None:
        raise HTTPException(503, "TMS Data Layer not initialized")
    ok = await layer.retry_failed_row(job_id, row_id, source)
    return {"succeeded": ok}


@router.post("/{job_id}/failed-rows/retry-all")
async def retry_all_failed(
    job_id: str,
    request: Request,
    source: Literal["api", "browser"] = Query(..., description="api or browser"),
):
    """Retry every currently-failed row in the job. Returns counts."""
    layer = getattr(request.app.state, "tms_data", None)
    if layer is None:
        raise HTTPException(503, "TMS Data Layer not initialized")
    return await layer.retry_all_failed(job_id, source)
