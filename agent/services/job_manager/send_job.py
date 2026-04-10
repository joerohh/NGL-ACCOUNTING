"""Send job mixin — create, start, approve, and orchestrate send jobs."""

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from config import (
    DEBUG_DIR, QBO_ACTION_DELAY_S, MAX_BATCH_SIZE, SEND_TIMEOUT_S,
)

logger = logging.getLogger("ngl.job_manager")


class SendJobMixin:
    """Handles send job lifecycle: create, approve, start, dispatch to method handlers."""

    def create_send_job(self, invoices: list[dict], test_mode: bool = False):
        """Create a new send job from a list of invoice dicts."""
        from services.job_manager import SendRequest, SendJob

        if len(invoices) > MAX_BATCH_SIZE:
            raise ValueError(
                f"Batch too large: {len(invoices)} invoices (max {MAX_BATCH_SIZE}). "
                "Split into smaller batches."
            )

        job_id = str(uuid.uuid4())[:8]
        requests = [
            SendRequest(
                invoice_number=inv["invoiceNumber"],
                container_number=inv["containerNumber"],
                customer_code=inv["customerCode"],
                amount=inv.get("amount", ""),
                subject=inv.get("subject", ""),
                do_sender_email=inv.get("doSenderEmail", ""),
                is_resend=inv.get("isResend", False),
            )
            for inv in invoices
        ]
        job = SendJob(job_id, requests, test_mode=test_mode)
        self._jobs[job_id] = job
        mode_label = " [TEST MODE]" if test_mode else ""
        logger.info("Created send job %s%s for %d invoices", job_id, mode_label, len(requests))
        return job

    def approve_current_send(self, job_id: str, approve: bool,
                              cc_override: Optional[list[str]] = None) -> None:
        """Approve or skip the current invoice in a test-mode send job.

        cc_override: if provided, replaces the CC list for the OEC POD email.
        """
        from services.job_manager import SendJob

        job = self._jobs.get(job_id)
        if not job or not isinstance(job, SendJob):
            raise ValueError(f"Send job {job_id} not found")
        if not job.test_mode:
            raise ValueError(f"Job {job_id} is not in test mode")
        if not job._approval_event:
            raise ValueError(f"Job {job_id} is not waiting for approval")
        job._approval_decision = approve
        job._cc_override = cc_override
        if cc_override is not None:
            logger.info("[APPROVAL] CC override provided: %s", cc_override)
        job._approval_event.set()

    def start_send_job(self, job_id: str) -> None:
        """Start a send job running in the background."""
        job = self._jobs.get(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")
        if job.status == "running":
            raise ValueError(f"Job {job_id} is already running")
        job._task = asyncio.create_task(self._run_send_job(job))

    async def _emit_send(self, job, event_type: str, data: dict) -> None:
        """Push an SSE event to the send job's event queue."""
        event = {"type": event_type, "timestamp": time.time(), **data}
        await job.events.put(event)

    async def _run_send_job(self, job) -> None:
        """Process all invoices in a send job — dispatches to method-specific handlers."""
        from services.job_manager import SendResult

        job.status = "running"
        await self._emit_send(job, "send_job_started", {"total": job.total})

        # Clear old debug files
        for f in DEBUG_DIR.glob("*"):
            try:
                f.unlink()
            except Exception:
                pass

        # Load customer profiles once at job start
        customers = self._load_customers()

        # Verify QBO API connection before starting
        if not self._qbo_api or not self._qbo_api.is_connected:
            job.status = "paused"
            await self._emit_send(job, "login_required", {
                "message": "QBO API not connected. Please authorize via Settings.",
            })
            return

        token = await self._qbo_api.token_manager.get_access_token()
        if not token:
            job.status = "paused"
            await self._emit_send(job, "login_required", {
                "message": "QBO API token expired. Please re-authorize via Settings.",
            })
            return

        for i, invoice in enumerate(job.invoices):
            # Check pause
            if job.status == "paused":
                await self._emit_send(job, "job_paused", {
                    "progress": job.progress,
                    "total": job.total,
                    "message": "Job paused by user",
                })
                job._save_state()
                return

            job.progress = i
            result = SendResult(invoice.invoice_number, invoice.container_number,
                                invoice.customer_code)
            result.timestamp = datetime.now(timezone.utc).isoformat()

            await self._emit_send(job, "invoice_start", {
                "invoiceNumber": invoice.invoice_number,
                "containerNumber": invoice.container_number,
                "customerCode": invoice.customer_code,
                "index": i,
                "total": job.total,
            })

            try:
                # Step 1: Look up customer
                customer = customers.get(invoice.customer_code.upper())
                if not customer or not customer.get("active", True):
                    result.status = "skipped"
                    result.error = f"Customer code not found: {invoice.customer_code}"
                    await self._emit_send(job, "invoice_skipped", {
                        "invoiceNumber": invoice.invoice_number,
                        "reason": "unknown_customer",
                        "customerCode": invoice.customer_code,
                    })
                    job.results.append(result)
                    self._write_audit_log(result.to_dict())
                    job._save_state()
                    continue

                # Step 2: Dispatch based on send method (with timeout)
                method = customer.get("sendMethod", "email")

                async def _dispatch_send():
                    if method == "qbo_invoice_only_then_pod_email":
                        await self._send_oec_flow(job, invoice, customer, result, i)
                    elif method in ("portal_upload", "portal"):
                        await self._send_portal_upload(job, invoice, customer, result, i)
                    else:
                        await self._send_qbo_api(job, invoice, customer, result, i)

                await asyncio.wait_for(_dispatch_send(), timeout=SEND_TIMEOUT_S)

            except asyncio.TimeoutError:
                logger.error(
                    "Invoice %s timed out after %ds",
                    invoice.invoice_number, SEND_TIMEOUT_S,
                )
                result.status = "error"
                result.error = f"Timed out after {SEND_TIMEOUT_S}s"
                await self._emit_send(job, "invoice_error", {
                    "invoiceNumber": invoice.invoice_number,
                    "error": result.error,
                })

            except Exception as e:
                logger.error("Error sending invoice %s: %s", invoice.invoice_number, e)
                result.status = "error"
                result.error = str(e)
                await self._emit_send(job, "invoice_error", {
                    "invoiceNumber": invoice.invoice_number,
                    "error": str(e),
                })

            result.timestamp = datetime.now(timezone.utc).isoformat()
            job.results.append(result)
            self._write_audit_log(result.to_dict())
            job._save_state()
            await asyncio.sleep(QBO_ACTION_DELAY_S)

        # Job finished
        job.progress = job.total
        job.status = "completed"
        job._save_state()

        # Summary
        sent_count = sum(1 for r in job.results if r.status == "sent")
        skipped_count = sum(1 for r in job.results if r.status == "skipped")
        error_count = sum(1 for r in job.results if r.status == "error")
        mismatch_count = sum(1 for r in job.results if r.status == "mismatch")
        missing_docs_count = sum(1 for r in job.results if r.status == "missing_docs")
        no_attachments_count = sum(1 for r in job.results if r.status == "skipped_no_attachments")

        await self._emit_send(job, "send_job_complete", {
            "total": job.total,
            "sent": sent_count,
            "skipped": skipped_count,
            "errors": error_count,
            "mismatches": mismatch_count,
            "missingDocs": missing_docs_count,
            "noAttachments": no_attachments_count,
        })

        # Desktop notification
        try:
            from services.notifier import notify
            if error_count > 0:
                notify("Send Job Done", f"{sent_count} sent, {error_count} errors out of {job.total}")
            else:
                notify("Send Job Done", f"{sent_count}/{job.total} invoices sent successfully")
        except Exception:
            pass

    async def _wait_for_approval(self, job, invoice, result, index: int,
                                  to_emails: list, cc_emails: list,
                                  bcc_emails: list, subject: str) -> bool:
        """Wait for user approval in test mode. Returns True if approved, False if skipped."""
        job._approval_event = asyncio.Event()
        job._approval_decision = None

        await self._emit_send(job, "awaiting_approval", {
            "invoiceNumber": invoice.invoice_number,
            "containerNumber": invoice.container_number,
            "customerCode": invoice.customer_code,
            "toEmails": to_emails,
            "ccEmails": cc_emails,
            "bccEmails": bcc_emails,
            "subject": subject,
            "attachmentsFound": result.attachments_found,
            "index": index,
            "total": job.total,
            "message": "Form filled — review QBO browser and approve or skip",
        })

        logger.info("Test mode: waiting for approval on %s", invoice.invoice_number)
        try:
            await asyncio.wait_for(job._approval_event.wait(), timeout=300)
        except asyncio.TimeoutError:
            result.status = "skipped"
            result.error = "Approval timed out (5 minutes)"
            await self._emit_send(job, "invoice_skipped", {
                "invoiceNumber": invoice.invoice_number,
                "reason": "approval_timeout",
            })
            job._approval_event = None
            return False

        approved = job._approval_decision is True
        job._approval_event = None
        job._approval_decision = None

        if not approved:
            result.status = "skipped"
            result.error = "Skipped by user in test mode"
            await self._emit_send(job, "invoice_skipped", {
                "invoiceNumber": invoice.invoice_number,
                "reason": "user_skipped",
                "customerCode": invoice.customer_code,
            })
            return False

        await self._emit_send(job, "approval_confirmed", {
            "invoiceNumber": invoice.invoice_number,
        })
        return True
