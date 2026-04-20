"""Chassis lookup endpoints — batch QBO chassis extraction for INI-Chassis-Finder."""

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("ngl.chassis_router")

router = APIRouter(prefix="/chassis", tags=["chassis"])

# Injected by main.py on startup
_job_manager = None


def set_job_manager(jm):
    global _job_manager
    _job_manager = jm


class ChassisRow(BaseModel):
    invoiceNumber: str
    containerNumber: str = ""


class ChassisLookupRequest(BaseModel):
    rows: list[ChassisRow]


@router.post("/lookup")
async def chassis_lookup(req: ChassisLookupRequest):
    """Start a chassis lookup job — queries QBO for each invoice's chassis number."""
    if not _job_manager:
        raise HTTPException(503, "Agent not initialized")

    if not req.rows:
        raise HTTPException(400, "No rows provided")

    rows = [r.model_dump() for r in req.rows]
    job = _job_manager.create_chassis_job(rows)
    _job_manager.start_chassis_job(job.id)

    return {"jobId": job.id, "total": job.total, "status": "running"}
