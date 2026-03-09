"""Fetch job mixin — create, start, and process container fetch jobs."""

import asyncio
import logging
import time
import uuid

from config import (
    DOWNLOADS_DIR, DEBUG_DIR,
    QBO_ACTION_DELAY_S, MAX_BATCH_SIZE, CONTAINER_TIMEOUT_S,
    FETCH_CONCURRENCY,
)
from utils import strip_motw

logger = logging.getLogger("ngl.job_manager")


class FetchJobMixin:
    """Handles fetch job lifecycle: create, start, process containers."""

    def create_job(self, containers: list[dict]):
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
        job = Job(job_id, requests)
        self._jobs[job_id] = job
        logger.info("Created job %s for %d containers", job_id, len(requests))
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

    async def _process_one_container(self, job, container, result, *, page=None) -> None:
        """Process a single container: search, download invoice, classify, check POD.

        Args:
            page: Playwright page to use for this container (for parallel workers).
        """
        # Step 1: Search for the invoice in QBO
        await self._emit(job, "searching", {
            "containerNumber": container.container_number,
            "invoiceNumber": container.invoice_number,
        })

        invoice_url = await self._qbo.search_invoice(container.invoice_number, page=page)
        if not invoice_url:
            result.error = f"Invoice {container.invoice_number} not found in QBO"
            await self._emit(job, "not_found", {
                "containerNumber": container.container_number,
                "invoiceNumber": container.invoice_number,
            })
            return

        # Step 2: Download the invoice PDF
        await self._emit(job, "downloading_invoice", {
            "containerNumber": container.container_number,
        })

        inv_path = await self._qbo.download_invoice_pdf(job.download_dir, page=page)
        if inv_path:
            # Rename immediately to container-specific name (prevents conflicts in parallel mode)
            new_name = f"{container.container_number}_invoice.pdf"
            new_path = job.download_dir / new_name
            inv_path.rename(new_path)
            strip_motw(new_path)
            result.invoice_file = new_name

            # Classify with Claude (can run while other workers use the browser)
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

        # Step 3: Check for POD attachment
        await self._emit(job, "checking_pod", {
            "containerNumber": container.container_number,
        })

        await asyncio.sleep(QBO_ACTION_DELAY_S)
        pod_path = await self._qbo.find_and_download_pod(job.download_dir, page=page)

        if pod_path:
            # Rename immediately to container-specific name
            new_name = f"{container.container_number}_pod.pdf"
            new_path = job.download_dir / new_name
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

        # Verify QBO login before starting
        logged_in = await self._qbo.is_logged_in()
        if not logged_in:
            job.status = "paused"
            await self._emit(job, "login_required", {
                "message": "QBO session expired. Please log in and resume.",
            })
            return

        concurrency = min(FETCH_CONCURRENCY, job.total)

        if concurrency > 1:
            await self._run_job_parallel(job, concurrency)
        else:
            await self._run_job_sequential(job)

    async def _run_job_sequential(self, job) -> None:
        """Original sequential processing — one container at a time."""
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
            await asyncio.sleep(QBO_ACTION_DELAY_S)

        await self._finish_job(job)

    async def _run_job_parallel(self, job, concurrency: int) -> None:
        """Parallel processing — multiple containers at once using browser page pool."""
        from services.job_manager import FetchResult

        # Create extra pages (main page + N-1 workers)
        await self._qbo.create_worker_pages(concurrency - 1)
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

                page = await self._qbo.acquire_page()
                try:
                    result = FetchResult(container.container_number, container.invoice_number)

                    await self._emit(job, "container_start", {
                        "containerNumber": container.container_number,
                        "invoiceNumber": container.invoice_number,
                        "index": i,
                        "total": job.total,
                    })

                    try:
                        await asyncio.wait_for(
                            self._process_one_container(job, container, result, page=page),
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

                    # Small delay between QBO actions to avoid rate limiting
                    await asyncio.sleep(QBO_ACTION_DELAY_S)
                finally:
                    await self._qbo.release_page(page)

        try:
            tasks = [
                asyncio.create_task(process_one(i, c))
                for i, c in enumerate(job.containers)
            ]
            await asyncio.gather(*tasks)
        finally:
            # Always clean up worker pages
            await self._qbo.close_worker_pages()

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
        """Mark job as completed and emit summary (shared by sequential and parallel)."""
        job.progress = job.total
        job.status = "completed"
        job._save_state()

        pod_missing_count = sum(1 for r in job.results if r.pod_missing)
        error_count = sum(1 for r in job.results if r.error)
        review_count = sum(1 for r in job.results if r.needs_review)

        await self._emit(job, "job_complete", {
            "total": job.total,
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
