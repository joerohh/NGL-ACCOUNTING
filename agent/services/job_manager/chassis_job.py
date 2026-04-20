"""Chassis lookup job mixin — batch-query QBO for chassis numbers."""

import asyncio
import logging
import time
import uuid

from config import CONTAINER_TIMEOUT_S

logger = logging.getLogger("ngl.job_manager")


class ChassisJobMixin:
    """Handles chassis lookup job lifecycle: create, start, process rows via QBO API."""

    def create_chassis_job(self, rows: list[dict]):
        """Create a new chassis lookup job from [{invoiceNumber, containerNumber}]."""
        from services.job_manager import ChassisRequest, ChassisJob

        job_id = f"ch-{uuid.uuid4().hex[:6]}"
        requests = [
            ChassisRequest(
                invoice_number=r["invoiceNumber"],
                container_number=r.get("containerNumber", ""),
            )
            for r in rows
        ]
        job = ChassisJob(job_id, requests)
        self._jobs[job_id] = job
        logger.info("Created chassis job %s for %d rows", job_id, len(requests))
        return job

    def start_chassis_job(self, job_id: str) -> None:
        """Start a chassis job running in the background."""
        job = self._jobs.get(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")
        if job.status == "running":
            raise ValueError(f"Job {job_id} is already running")
        job._task = asyncio.create_task(self._run_chassis_job(job))

    async def _run_chassis_job(self, job) -> None:
        """Process all rows — query QBO for each invoice and extract chassis."""
        job.status = "running"
        await self._emit(job, "job_started", {"total": job.total, "type": "chassis"})

        # Verify QBO connection
        if not self._qbo_api or not self._qbo_api.is_connected:
            job.status = "paused"
            await self._emit(job, "login_required", {
                "message": "QBO API not connected. Please authorize via Settings.",
            })
            return

        token = await self._qbo_api.token_manager.get_access_token()
        if not token:
            job.status = "paused"
            await self._emit(job, "login_required", {
                "message": "QBO API token expired. Please re-authorize via Settings.",
            })
            return

        from services.job_manager import ChassisResult

        for i, req in enumerate(job.requests):
            job.progress = i

            await self._emit(job, "chassis_progress", {
                "invoiceNumber": req.invoice_number,
                "containerNumber": req.container_number,
                "index": i,
                "total": job.total,
            })

            result = ChassisResult(req.invoice_number, req.container_number)

            try:
                await asyncio.wait_for(
                    self._lookup_one_chassis(job, req, result),
                    timeout=CONTAINER_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                result.error = f"Timed out after {CONTAINER_TIMEOUT_S}s"
                await self._emit(job, "chassis_error", {
                    "invoiceNumber": req.invoice_number,
                    "error": result.error,
                })
            except Exception as e:
                logger.error("Chassis lookup failed for %s: %s", req.invoice_number, e)
                result.error = str(e)
                await self._emit(job, "chassis_error", {
                    "invoiceNumber": req.invoice_number,
                    "error": str(e),
                })

            job.results.append(result)
            await asyncio.sleep(0.3)  # light rate-limit for QBO API

        # Finish
        job.progress = job.total
        job.status = "completed"

        found = sum(1 for r in job.results if r.chassis_number)
        errors = sum(1 for r in job.results if r.error)

        await self._emit(job, "chassis_complete", {
            "total": job.total,
            "found": found,
            "notFound": job.total - found - errors,
            "errors": errors,
            "results": [r.to_dict() for r in job.results],
        })

        try:
            from services.notifier import notify
            notify("Chassis Lookup Done", f"{found}/{job.total} chassis numbers found")
        except Exception:
            pass

    async def _lookup_one_chassis(self, job, req, result) -> None:
        """Look up a single chassis number via QBO API."""
        api = self._qbo_api

        invoice_data = await api.search_invoice(req.invoice_number)
        if not invoice_data:
            result.error = f"Invoice {req.invoice_number} not found in QBO"
            await self._emit(job, "chassis_not_found", {
                "invoiceNumber": req.invoice_number,
                "containerNumber": req.container_number,
                "reason": "invoice_not_found",
            })
            return

        chassis = api._extract_chassis(invoice_data)
        cnee = api._extract_cnee(invoice_data)

        if cnee:
            result.cnee = cnee
            logger.info("CNEE found: %s → %s", req.invoice_number, cnee)

        if chassis:
            result.chassis_number = chassis
            logger.info("Chassis found: %s → %s", req.invoice_number, chassis)
            await self._emit(job, "chassis_found", {
                "invoiceNumber": req.invoice_number,
                "containerNumber": req.container_number,
                "chassisNumber": chassis,
                "cnee": cnee or "",
            })
        else:
            logger.info("No chassis in QBO for %s", req.invoice_number)
            await self._emit(job, "chassis_not_found", {
                "invoiceNumber": req.invoice_number,
                "containerNumber": req.container_number,
                "reason": "no_chassis_field",
                "cnee": cnee or "",
            })
