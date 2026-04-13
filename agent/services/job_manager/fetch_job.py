"""Fetch job mixin — create, start, and process container fetch jobs via QBO API."""

import asyncio
import logging
import time
import uuid

from config import (
    DOWNLOADS_DIR, DEBUG_DIR,
    MAX_BATCH_SIZE, CONTAINER_TIMEOUT_S,
    FETCH_CONCURRENCY,
)
from utils import strip_motw

logger = logging.getLogger("ngl.job_manager")


class FetchJobMixin:
    """Handles fetch job lifecycle: create, start, process containers via QBO API."""

    def create_job(self, containers: list[dict], *, doc_types=None):
        """Create a new fetch job from a list of {containerNumber, invoiceNumber}."""
        from services.job_manager import ContainerRequest, Job

        if len(containers) > MAX_BATCH_SIZE:
            raise ValueError(
                f"Batch too large: {len(containers)} containers (max {MAX_BATCH_SIZE}). "
                "Split into smaller batches to avoid excessive API usage."
            )

        job_id = str(uuid.uuid4())[:8]
        requests = [
            ContainerRequest(
                container_number=c["containerNumber"],
                invoice_number=c["invoiceNumber"],
            )
            for c in containers
        ]
        job = Job(job_id, requests, doc_types=doc_types)
        self._jobs[job_id] = job
        logger.info("Created job %s for %d containers (doc_types=%s)", job_id, len(requests), job.doc_types)
        return job

    def get_job(self, job_id: str):
        return self._jobs.get(job_id)

    def start_job(self, job_id: str) -> None:
        """Start a job running in the background."""
        job = self._jobs.get(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")
        if job.status == "running":
            raise ValueError(f"Job {job_id} is already running")
        job._task = asyncio.create_task(self._run_job(job))

    async def _emit(self, job, event_type: str, data: dict) -> None:
        """Push an SSE event to the job's event queue."""
        event = {"type": event_type, "timestamp": time.time(), **data}
        await job.events.put(event)

    async def _process_one_container(self, job, container, result) -> None:
        """Process a single container via QBO API: search, download invoice, download POD."""
        want_invoice = "invoice" in job.doc_types
        want_pod = "pod" in job.doc_types
        api = self._qbo_api

        # Step 1: Search for the invoice in QBO
        search_term = container.invoice_number or container.container_number
        if not container.invoice_number:
            logger.warning(
                "No invoice number for container %s — searching by container number instead",
                container.container_number,
            )

        await self._emit(job, "searching", {
            "containerNumber": container.container_number,
            "invoiceNumber": search_term,
        })

        invoice_data = await api.search_invoice(search_term)
        if not invoice_data:
            result.error = f"Invoice {container.invoice_number} not found in QBO"
            await self._emit(job, "not_found", {
                "containerNumber": container.container_number,
                "invoiceNumber": container.invoice_number,
            })
            return

        invoice_id = invoice_data["Id"]

        # Step 2: Download the invoice PDF (only if invoice type is requested)
        if want_invoice:
            await self._emit(job, "downloading_invoice", {
                "containerNumber": container.container_number,
            })

            pdf_bytes = await api.download_invoice_pdf(invoice_id)
            if pdf_bytes:
                new_name = f"{container.container_number}_invoice.pdf"
                new_path = job.download_dir / new_name
                new_path.write_bytes(pdf_bytes)
                strip_motw(new_path)
                result.invoice_file = new_name

                # Classify with Claude
                await self._emit(job, "classifying", {
                    "containerNumber": container.container_number,
                    "file": new_name,
                })
                classification = await self._classifier.classify(new_path)

                if classification.needs_review:
                    result.needs_review = True
                    await self._emit(job, "review_needed", {
                        "containerNumber": container.container_number,
                        "file": new_name,
                        "classified_as": classification.doc_type,
                        "confidence": classification.confidence,
                    })
            else:
                result.error = "Failed to download invoice PDF"
                await self._emit(job, "download_failed", {
                    "containerNumber": container.container_number,
                    "type": "invoice",
                })

        # Step 3: Check for POD attachment (only if POD type is requested)
        if want_pod:
            await self._emit(job, "checking_pod", {
                "containerNumber": container.container_number,
            })

            attachments = await api.list_attachments(invoice_id)
            pod_att = next((a for a in attachments if a.get("docType") == "pod"), None)

            if pod_att:
                pod_path = await api.download_attachment(
                    pod_att["id"], pod_att["fileName"], job.download_dir
                )
                if pod_path:
                    new_name = f"{container.container_number}_pod.pdf"
                    new_path = job.download_dir / new_name
                    if pod_path != new_path:
                        pod_path.rename(new_path)
                    strip_motw(new_path)
                    result.pod_file = new_name

                    # Classify POD
                    pod_classification = await self._classifier.classify(new_path)
                    if pod_classification.needs_review:
                        result.needs_review = True

                    await self._emit(job, "pod_found", {
                        "containerNumber": container.container_number,
                        "file": new_name,
                    })
                else:
                    result.pod_missing = True
                    await self._emit(job, "pod_missing", {
                        "containerNumber": container.container_number,
                        "message": f"POD found but download failed for container {container.container_number}",
                    })
            else:
                result.pod_missing = True
                await self._emit(job, "pod_missing", {
                    "containerNumber": container.container_number,
                    "message": f"No POD found in QBO for container {container.container_number}",
                })

        # Step 4: Emit container complete
        await self._emit(job, "container_complete", {
            "containerNumber": container.container_number,
            "result": result.to_dict(),
        })

    async def _run_job(self, job) -> None:
        """Process all containers in a job — parallel when FETCH_CONCURRENCY > 1."""
        from services.job_manager import FetchResult

        job.status = "running"
        await self._emit(job, "job_started", {"total": job.total})

        # Clear old debug files so each run starts fresh
        for f in DEBUG_DIR.glob("*"):
            try:
                f.unlink()
            except Exception:
                pass

        # Verify QBO API connection before starting
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

        concurrency = min(FETCH_CONCURRENCY, job.total)

        if concurrency > 1:
            await self._run_job_parallel(job, concurrency)
        else:
            await self._run_job_sequential(job)

    async def _run_job_sequential(self, job) -> None:
        """Sequential processing — one container at a time."""
        from services.job_manager import FetchResult

        for i, container in enumerate(job.containers):
            if job.status == "paused":
                await self._emit(job, "job_paused", {
                    "progress": job.progress,
                    "total": job.total,
                    "message": "Job paused by user",
                })
                job._save_state()
                return

            job.progress = i
            result = FetchResult(container.container_number, container.invoice_number)

            await self._emit(job, "container_start", {
                "containerNumber": container.container_number,
                "invoiceNumber": container.invoice_number,
                "index": i,
                "total": job.total,
            })

            try:
                await asyncio.wait_for(
                    self._process_one_container(job, container, result),
                    timeout=CONTAINER_TIMEOUT_S,
                )

            except asyncio.TimeoutError:
                logger.error(
                    "Container %s timed out after %ds",
                    container.container_number, CONTAINER_TIMEOUT_S,
                )
                result.error = f"Timed out after {CONTAINER_TIMEOUT_S}s"
                await self._emit(job, "container_error", {
                    "containerNumber": container.container_number,
                    "error": result.error,
                })

            except Exception as e:
                logger.error(
                    "Error processing container %s: %s",
                    container.container_number, e,
                )
                result.error = str(e)
                await self._emit(job, "container_error", {
                    "containerNumber": container.container_number,
                    "error": str(e),
                })

            job.results.append(result)
            job._save_state()
            await asyncio.sleep(1.0)

        await self._finish_job(job)

    async def _run_job_parallel(self, job, concurrency: int) -> None:
        """Parallel processing — multiple containers at once using async semaphore."""
        from services.job_manager import FetchResult

        logger.info("Starting parallel fetch: %d containers, concurrency=%d", job.total, concurrency)

        sem = asyncio.Semaphore(concurrency)
        completed_count = 0

        async def process_one(i: int, container):
            nonlocal completed_count

            if job.status == "paused":
                return

            async with sem:
                if job.status == "paused":
                    return

                result = FetchResult(container.container_number, container.invoice_number)

                await self._emit(job, "container_start", {
                    "containerNumber": container.container_number,
                    "invoiceNumber": container.invoice_number,
                    "index": i,
                    "total": job.total,
                })

                try:
                    await asyncio.wait_for(
                        self._process_one_container(job, container, result),
                        timeout=CONTAINER_TIMEOUT_S,
                    )
                except asyncio.TimeoutError:
                    logger.error(
                        "Container %s timed out after %ds",
                        container.container_number, CONTAINER_TIMEOUT_S,
                    )
                    result.error = f"Timed out after {CONTAINER_TIMEOUT_S}s"
                    await self._emit(job, "container_error", {
                        "containerNumber": container.container_number,
                        "error": result.error,
                    })
                except Exception as e:
                    logger.error(
                        "Error processing container %s: %s",
                        container.container_number, e,
                    )
                    result.error = str(e)
                    await self._emit(job, "container_error", {
                        "containerNumber": container.container_number,
                        "error": str(e),
                    })

                job.results.append(result)
                completed_count += 1
                job.progress = completed_count
                job._save_state()

                await asyncio.sleep(1.0)

        tasks = [
            asyncio.create_task(process_one(i, c))
            for i, c in enumerate(job.containers)
        ]
        await asyncio.gather(*tasks)

        if job.status == "paused":
            await self._emit(job, "job_paused", {
                "progress": job.progress,
                "total": job.total,
                "message": "Job paused by user",
            })
            job._save_state()
            return

        await self._finish_job(job)

    async def _finish_job(self, job) -> None:
        """Mark job as completed and emit summary."""
        job.progress = job.total
        job.status = "completed"
        job._save_state()

        pod_missing_count = sum(1 for r in job.results if r.pod_missing)
        error_count = sum(1 for r in job.results if r.error)
        review_count = sum(1 for r in job.results if r.needs_review)

        await self._emit(job, "job_complete", {
            "total": job.total,
            "docTypes": job.doc_types,
            "invoicesDownloaded": sum(1 for r in job.results if r.invoice_file),
            "podsDownloaded": sum(1 for r in job.results if r.pod_file),
            "podsMissing": pod_missing_count,
            "errors": error_count,
            "needsReview": review_count,
        })

        # Desktop notification
        try:
            from services.notifier import notify
            if error_count > 0:
                notify("Fetch Job Done", f"{job.total} containers — {error_count} errors")
            else:
                notify("Fetch Job Done", f"{job.total} containers processed successfully")
        except Exception:
            pass
