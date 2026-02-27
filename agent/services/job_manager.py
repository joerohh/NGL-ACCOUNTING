"""Background job orchestration — manages fetch & send jobs with SSE progress streaming."""

import asyncio
import json
import logging
import os
import shutil
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import (
    DOWNLOADS_DIR, JOB_STATE_DIR, DEBUG_DIR,
    QBO_ACTION_DELAY_S, MAX_BATCH_SIZE,
    CUSTOMERS_FILE, AUDIT_LOG_FILE,
)
from services.qbo_browser import QBOBrowser
from services.tms_browser import TMSBrowser
from services.claude_classifier import ClaudeClassifier
from services.email_sender import EmailSender
from services.portal_uploader import PortalUploader
from utils import strip_motw

logger = logging.getLogger("ngl.job_manager")


class ContainerRequest:
    """A single container's fetch request."""

    def __init__(self, container_number: str, invoice_number: str) -> None:
        self.container_number = container_number
        self.invoice_number = invoice_number


class FetchResult:
    """Result for a single container after processing."""

    def __init__(self, container_number: str, invoice_number: str) -> None:
        self.container_number = container_number
        self.invoice_number = invoice_number
        self.invoice_file: Optional[str] = None
        self.pod_file: Optional[str] = None
        self.pod_missing: bool = False
        self.needs_review: bool = False
        self.error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "containerNumber": self.container_number,
            "invoiceNumber": self.invoice_number,
            "invoiceFile": self.invoice_file,
            "podFile": self.pod_file,
            "podMissing": self.pod_missing,
            "needsReview": self.needs_review,
            "error": self.error,
        }


class SendRequest:
    """A single invoice send request."""

    def __init__(self, invoice_number: str, container_number: str,
                 customer_code: str, amount: str = "", subject: str = "",
                 do_sender_email: str = "") -> None:
        self.invoice_number = invoice_number
        self.container_number = container_number
        self.customer_code = customer_code
        self.amount = amount
        self.subject = subject
        self.do_sender_email = do_sender_email


class SendResult:
    """Result for a single invoice send attempt."""

    def __init__(self, invoice_number: str, container_number: str,
                 customer_code: str) -> None:
        self.invoice_number = invoice_number
        self.container_number = container_number
        self.customer_code = customer_code
        self.status: str = "pending"  # sent, skipped, skipped_no_attachments, error, mismatch, missing_docs
        self.to_emails: list[str] = []
        self.cc_emails: list[str] = []
        self.bcc_emails: list[str] = []
        self.subject: str = ""
        self.attachments_found: list[str] = []
        self.attachments_missing: list[str] = []
        self.error: Optional[str] = None
        self.timestamp: str = ""

    def to_dict(self) -> dict:
        return {
            "invoiceNumber": self.invoice_number,
            "containerNumber": self.container_number,
            "customerCode": self.customer_code,
            "status": self.status,
            "toEmails": self.to_emails,
            "ccEmails": self.cc_emails,
            "bccEmails": self.bcc_emails,
            "subject": self.subject,
            "attachmentsFound": self.attachments_found,
            "attachmentsMissing": self.attachments_missing,
            "error": self.error,
            "timestamp": self.timestamp,
        }


class Job:
    """Represents a background fetch job."""

    def __init__(self, job_id: str, containers: list[ContainerRequest]) -> None:
        self.id = job_id
        self.containers = containers
        self.status = "pending"  # pending, running, paused, completed, failed
        self.progress = 0
        self.total = len(containers)
        self.results: list[FetchResult] = []
        self.events: asyncio.Queue = asyncio.Queue()
        self.download_dir = DOWNLOADS_DIR / job_id
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self._task: Optional[asyncio.Task] = None
        self.created_at = time.time()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "progress": self.progress,
            "total": self.total,
            "results": [r.to_dict() for r in self.results],
        }

    def _save_state(self) -> None:
        """Persist job state to disk for crash recovery."""
        state_file = JOB_STATE_DIR / f"{self.id}.json"
        state = {
            "id": self.id,
            "status": self.status,
            "progress": self.progress,
            "total": self.total,
            "results": [r.to_dict() for r in self.results],
            "remaining": [
                {"containerNumber": c.container_number, "invoiceNumber": c.invoice_number}
                for c in self.containers[self.progress :]
            ],
        }
        with open(state_file, "w") as f:
            json.dump(state, f, indent=2)


class SendJob:
    """Represents a background invoice-sending job."""

    def __init__(self, job_id: str, invoices: list[SendRequest],
                 test_mode: bool = False) -> None:
        self.id = job_id
        self.invoices = invoices
        self.status = "pending"  # pending, running, paused, completed, failed
        self.progress = 0
        self.total = len(invoices)
        self.results: list[SendResult] = []
        self.events: asyncio.Queue = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        self.created_at = time.time()
        self.test_mode = test_mode
        # For test mode: used to wait for user approval before clicking Send
        self._approval_event: Optional[asyncio.Event] = None
        self._approval_decision: Optional[bool] = None  # True=send, False=skip

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": "send",
            "status": self.status,
            "progress": self.progress,
            "total": self.total,
            "results": [r.to_dict() for r in self.results],
        }

    def _save_state(self) -> None:
        """Persist job state to disk for crash recovery."""
        state_file = JOB_STATE_DIR / f"{self.id}.json"
        state = {
            "id": self.id,
            "type": "send",
            "status": self.status,
            "progress": self.progress,
            "total": self.total,
            "results": [r.to_dict() for r in self.results],
            "remaining": [
                {
                    "invoiceNumber": inv.invoice_number,
                    "containerNumber": inv.container_number,
                    "customerCode": inv.customer_code,
                    "amount": inv.amount,
                }
                for inv in self.invoices[self.progress:]
            ],
        }
        with open(state_file, "w") as f:
            json.dump(state, f, indent=2)


class JobManager:
    """Manages background fetch & send jobs — coordinates QBO browser + Claude classifier."""

    def __init__(self, qbo: QBOBrowser, classifier: ClaudeClassifier,
                 email_sender: Optional["EmailSender"] = None,
                 portal_uploader: Optional["PortalUploader"] = None,
                 tms_browser: Optional["TMSBrowser"] = None) -> None:
        self._qbo = qbo
        self._classifier = classifier
        self._email_sender = email_sender
        self._portal_uploader = portal_uploader
        self._tms = tms_browser
        self._jobs: dict[str, Job] = {}

    def create_job(self, containers: list[dict]) -> Job:
        """Create a new fetch job from a list of {containerNumber, invoiceNumber}."""
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

    def get_job(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def start_job(self, job_id: str) -> None:
        """Start a job running in the background."""
        job = self._jobs.get(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")
        if job.status == "running":
            raise ValueError(f"Job {job_id} is already running")
        job._task = asyncio.create_task(self._run_job(job))

    async def _emit(self, job: Job, event_type: str, data: dict) -> None:
        """Push an SSE event to the job's event queue."""
        event = {"type": event_type, "timestamp": time.time(), **data}
        await job.events.put(event)

    async def _run_job(self, job: Job) -> None:
        """Process all containers in a job sequentially."""
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

        for i, container in enumerate(job.containers):
            if job.status == "paused":
                await self._emit(job, "job_paused", {
                    "progress": job.progress,
                    "total": job.total,
                    "message": "Job paused by user",
                })
                job._save_state()
                return  # Exit cleanly — don't mark as completed

            job.progress = i
            result = FetchResult(container.container_number, container.invoice_number)

            await self._emit(job, "container_start", {
                "containerNumber": container.container_number,
                "invoiceNumber": container.invoice_number,
                "index": i,
                "total": job.total,
            })

            try:
                # Step 1: Search for the invoice in QBO
                await self._emit(job, "searching", {
                    "containerNumber": container.container_number,
                    "invoiceNumber": container.invoice_number,
                })

                invoice_url = await self._qbo.search_invoice(container.invoice_number)
                if not invoice_url:
                    result.error = f"Invoice {container.invoice_number} not found in QBO"
                    await self._emit(job, "not_found", {
                        "containerNumber": container.container_number,
                        "invoiceNumber": container.invoice_number,
                    })
                    job.results.append(result)
                    job._save_state()
                    continue

                # Step 2: Download the invoice PDF
                await self._emit(job, "downloading_invoice", {
                    "containerNumber": container.container_number,
                })

                inv_path = await self._qbo.download_invoice_pdf(job.download_dir)
                if inv_path:
                    # Classify with Claude
                    await self._emit(job, "classifying", {
                        "containerNumber": container.container_number,
                        "file": inv_path.name,
                    })
                    classification = await self._classifier.classify(inv_path)

                    # Rename with container number
                    new_name = f"{container.container_number}_invoice.pdf"
                    new_path = job.download_dir / new_name
                    inv_path.rename(new_path)
                    strip_motw(new_path)
                    result.invoice_file = new_name

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
                pod_path = await self._qbo.find_and_download_pod(job.download_dir)

                if pod_path:
                    # Classify POD
                    pod_classification = await self._classifier.classify(pod_path)
                    new_name = f"{container.container_number}_pod.pdf"
                    new_path = job.download_dir / new_name
                    pod_path.rename(new_path)
                    strip_motw(new_path)
                    result.pod_file = new_name

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

        # Job finished
        job.progress = job.total
        job.status = "completed"
        job._save_state()

        # Summary
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

    # ------------------------------------------------------------------
    # Send job management
    # ------------------------------------------------------------------

    @staticmethod
    def _load_customers() -> dict:
        """Load customer profiles from the JSON file."""
        if not CUSTOMERS_FILE.exists():
            return {}
        try:
            return json.loads(CUSTOMERS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read customers file: %s", e)
            return {}

    @staticmethod
    def _write_audit_log(entry: dict) -> None:
        """Append a single audit log entry to the JSONL file."""
        try:
            with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.error("Failed to write audit log: %s", e)

    def create_send_job(self, invoices: list[dict], test_mode: bool = False) -> SendJob:
        """Create a new send job from a list of invoice dicts."""
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
            )
            for inv in invoices
        ]
        job = SendJob(job_id, requests, test_mode=test_mode)
        self._jobs[job_id] = job
        mode_label = " [TEST MODE]" if test_mode else ""
        logger.info("Created send job %s%s for %d invoices", job_id, mode_label, len(requests))
        return job

    def approve_current_send(self, job_id: str, approve: bool) -> None:
        """Approve or skip the current invoice in a test-mode send job."""
        job = self._jobs.get(job_id)
        if not job or not isinstance(job, SendJob):
            raise ValueError(f"Send job {job_id} not found")
        if not job.test_mode:
            raise ValueError(f"Job {job_id} is not in test mode")
        if not job._approval_event:
            raise ValueError(f"Job {job_id} is not waiting for approval")
        job._approval_decision = approve
        job._approval_event.set()

    def start_send_job(self, job_id: str) -> None:
        """Start a send job running in the background."""
        job = self._jobs.get(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")
        if job.status == "running":
            raise ValueError(f"Job {job_id} is already running")
        job._task = asyncio.create_task(self._run_send_job(job))

    async def _emit_send(self, job: SendJob, event_type: str, data: dict) -> None:
        """Push an SSE event to the send job's event queue."""
        event = {"type": event_type, "timestamp": time.time(), **data}
        await job.events.put(event)

    async def _run_send_job(self, job: SendJob) -> None:
        """Process all invoices in a send job — dispatches to method-specific handlers."""
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

        # Verify QBO login before starting
        logged_in = await self._qbo.is_logged_in()
        if not logged_in:
            job.status = "paused"
            await self._emit_send(job, "login_required", {
                "message": "QBO session expired. Please log in and resume.",
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

            # Check QBO session is still alive every 5 invoices
            if i > 0 and i % 5 == 0:
                still_logged_in = await self._qbo.is_logged_in()
                if not still_logged_in:
                    job.status = "paused"
                    await self._emit_send(job, "login_required", {
                        "message": f"QBO session expired after invoice {i}/{job.total}. Please log in and resume.",
                        "progress": i,
                        "total": job.total,
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

                # Step 2: Dispatch based on send method
                method = customer.get("sendMethod", "email")

                if method == "qbo_invoice_only_then_pod_email":
                    await self._send_oec_flow(job, invoice, customer, result, i)
                elif method in ("portal_upload", "portal"):
                    await self._send_portal_upload(job, invoice, customer, result, i)
                else:
                    # Default: standard QBO email (handles "email" and "qbo_standard")
                    await self._send_qbo_standard(job, invoice, customer, result, i)

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

    # ------------------------------------------------------------------
    # Send method handlers
    # ------------------------------------------------------------------

    async def _send_qbo_standard(self, job: SendJob, invoice: SendRequest,
                                  customer: dict, result: SendResult, index: int) -> None:
        """Standard QBO email send — all attachments, standard recipients."""
        customer_emails = customer.get("emails", [])
        if not customer_emails:
            result.status = "skipped"
            result.error = f"No emails configured for customer: {invoice.customer_code}"
            await self._emit_send(job, "invoice_skipped", {
                "invoiceNumber": invoice.invoice_number,
                "reason": "no_emails",
                "customerCode": invoice.customer_code,
            })
            return

        # Search QBO for the invoice
        await self._emit_send(job, "searching_invoice", {
            "invoiceNumber": invoice.invoice_number,
        })

        invoice_url = await self._qbo.search_invoice(invoice.invoice_number)
        if not invoice_url:
            result.status = "error"
            result.error = f"Invoice {invoice.invoice_number} not found in QBO"
            await self._emit_send(job, "invoice_not_found", {
                "invoiceNumber": invoice.invoice_number,
            })
            return

        # Verify invoice details match CSV
        await self._emit_send(job, "verifying_invoice", {
            "invoiceNumber": invoice.invoice_number,
            "containerNumber": invoice.container_number,
        })

        verification = await self._qbo.verify_invoice_details(
            invoice.container_number, invoice.amount or None
        )
        if not verification.get("verified"):
            result.status = "mismatch"
            result.error = verification.get("reason", "Verification failed")
            await self._emit_send(job, "invoice_mismatch", {
                "invoiceNumber": invoice.invoice_number,
                "containerNumber": invoice.container_number,
                "reason": result.error,
            })
            return

        # Check attachments
        required_docs = customer.get("requiredDocs", [])
        await self._emit_send(job, "checking_attachments", {
            "invoiceNumber": invoice.invoice_number,
        })

        att_check = await self._qbo.check_attachments_on_page(required_docs)
        result.attachments_found = att_check.get("found", [])
        result.attachments_missing = att_check.get("missing", [])

        # Strict rule: NEVER send if zero attachments on the invoice page
        total_attachments = len(att_check.get("attachments", []))
        if total_attachments == 0:
            result.status = "skipped_no_attachments"
            result.error = "No attachments found on invoice page"
            await self._emit_send(job, "invoice_skipped", {
                "invoiceNumber": invoice.invoice_number,
                "reason": "no_attachments",
            })
            return

        if required_docs and not att_check.get("allPresent"):
            result.status = "missing_docs"
            result.error = f"Missing required docs: {', '.join(result.attachments_missing)}"
            await self._emit_send(job, "invoice_missing_docs", {
                "invoiceNumber": invoice.invoice_number,
                "found": result.attachments_found,
                "missing": result.attachments_missing,
            })
            return

        # Click "Review and Send"
        await self._emit_send(job, "opening_send_form", {
            "invoiceNumber": invoice.invoice_number,
        })

        form_opened = await self._qbo.click_review_and_send()
        if not form_opened:
            result.status = "error"
            result.error = "Failed to open Review and Send form"
            await self._emit_send(job, "invoice_error", {
                "invoiceNumber": invoice.invoice_number,
                "error": result.error,
            })
            return

        # Give the send form extra time to fully render (attachments load async)
        await asyncio.sleep(3)

        # Build and fill the form
        subject = invoice.subject or f"[NGL_INV] {invoice.invoice_number} - Container#{invoice.container_number}"
        to_emails = customer_emails
        cc_emails = ["ar@ngltrans.net"] + customer.get("ccEmails", [])
        bcc_emails = customer.get("bccEmails", [])

        result.to_emails = to_emails
        result.cc_emails = cc_emails
        result.bcc_emails = bcc_emails
        result.subject = subject

        await self._emit_send(job, "filling_send_form", {
            "invoiceNumber": invoice.invoice_number,
            "toEmails": to_emails,
            "subject": subject,
        })

        detail_att_count = len(att_check.get("attachments", []))
        fill_result = await self._qbo.fill_send_form(
            to_emails, cc_emails, subject, bcc_emails,
            expected_attachment_count=detail_att_count,
        )

        # Recovery: if attachments missing on send form, go Back → Select All → retry
        if fill_result.get("filled") and not fill_result.get("attachmentsFull", True) and detail_att_count > 0:
            logger.warning("Attachments incomplete on send form — attempting recovery")
            await self._emit_send(job, "retrying_attachments", {
                "invoiceNumber": invoice.invoice_number,
            })

            back_ok = await self._qbo.click_back_from_send_form()
            if back_ok:
                await asyncio.sleep(3)
                await self._qbo.select_all_attachments()
                await asyncio.sleep(2)

                form_ok = await self._qbo.click_review_and_send()
                if form_ok:
                    await asyncio.sleep(3)
                    fill_result = await self._qbo.fill_send_form(
                        to_emails, cc_emails, subject, bcc_emails,
                        expected_attachment_count=detail_att_count,
                    )

        if not fill_result.get("filled"):
            result.status = "error"
            result.error = "Failed to fill send form"
            await self._emit_send(job, "invoice_error", {
                "invoiceNumber": invoice.invoice_number,
                "error": result.error,
            })
            return

        # Click Send (or wait for approval in test mode)
        if job.test_mode:
            approved = await self._wait_for_approval(job, invoice, result, index,
                                                      to_emails, cc_emails, bcc_emails, subject)
            if not approved:
                return  # result already set by _wait_for_approval

        await self._emit_send(job, "sending_invoice", {
            "invoiceNumber": invoice.invoice_number,
        })

        sent = await self._qbo.click_send_invoice()
        if sent:
            result.status = "sent"
            await self._emit_send(job, "invoice_sent", {
                "invoiceNumber": invoice.invoice_number,
                "containerNumber": invoice.container_number,
                "toEmails": to_emails,
                "subject": subject,
            })
        else:
            result.status = "error"
            result.error = "Send button click failed or no confirmation"
            await self._emit_send(job, "invoice_error", {
                "invoiceNumber": invoice.invoice_number,
                "error": result.error,
            })

    async def _send_oec_flow(self, job: SendJob, invoice: SendRequest,
                              customer: dict, result: SendResult, index: int) -> None:
        """OEC flow: Send invoice-only via QBO, then POD via separate Gmail email."""
        customer_emails = customer.get("emails", [])
        if not customer_emails:
            result.status = "skipped"
            result.error = f"No emails configured for customer: {invoice.customer_code}"
            await self._emit_send(job, "invoice_skipped", {
                "invoiceNumber": invoice.invoice_number,
                "reason": "no_emails",
                "customerCode": invoice.customer_code,
            })
            return

        # Check Gmail sender is configured
        if not self._email_sender:
            result.status = "skipped"
            result.error = "Gmail not configured — add GMAIL_ADDRESS and GMAIL_APP_PASSWORD to .env"
            await self._emit_send(job, "invoice_skipped", {
                "invoiceNumber": invoice.invoice_number,
                "reason": "gmail_not_configured",
            })
            return

        # Search QBO for the invoice
        await self._emit_send(job, "searching_invoice", {
            "invoiceNumber": invoice.invoice_number,
        })

        invoice_url = await self._qbo.search_invoice(invoice.invoice_number)
        if not invoice_url:
            result.status = "error"
            result.error = f"Invoice {invoice.invoice_number} not found in QBO"
            await self._emit_send(job, "invoice_not_found", {
                "invoiceNumber": invoice.invoice_number,
            })
            return

        # Verify invoice details
        await self._emit_send(job, "verifying_invoice", {
            "invoiceNumber": invoice.invoice_number,
            "containerNumber": invoice.container_number,
        })

        verification = await self._qbo.verify_invoice_details(
            invoice.container_number, invoice.amount or None
        )
        if not verification.get("verified"):
            result.status = "mismatch"
            result.error = verification.get("reason", "Verification failed")
            await self._emit_send(job, "invoice_mismatch", {
                "invoiceNumber": invoice.invoice_number,
                "containerNumber": invoice.container_number,
                "reason": result.error,
            })
            return

        # OEC flow: send invoice-only via QBO (no attachments required).
        # The QBO invoice itself is the document — no PDF attachments needed.
        # If attachments exist on the page, deselect them so only the invoice is sent.
        await self._emit_send(job, "checking_attachments", {
            "invoiceNumber": invoice.invoice_number,
        })

        att_check = await self._qbo.check_attachments_on_page(["invoice", "pod"])
        result.attachments_found = att_check.get("found", [])
        result.attachments_missing = att_check.get("missing", [])
        has_pod = "pod" in result.attachments_found
        total_attachments = len(att_check.get("attachments", []))

        # ── Part A: Send invoice-only via QBO ──
        await self._emit_send(job, "oec_qbo_sending", {
            "invoiceNumber": invoice.invoice_number,
        })

        # If there are file attachments on the page, deselect them all
        # — we only want the bare QBO invoice, zero attachments
        if total_attachments > 0:
            await self._qbo.deselect_all_attachments()
            await asyncio.sleep(1)

        # Click Review and Send
        form_opened = await self._qbo.click_review_and_send()
        if not form_opened:
            result.status = "error"
            result.error = "Failed to open Review and Send form"
            await self._emit_send(job, "invoice_error", {
                "invoiceNumber": invoice.invoice_number,
                "error": result.error,
            })
            return

        await asyncio.sleep(3)

        # Fill form with customer's standard QBO email settings
        subject = invoice.subject or f"[NGL_INV] {invoice.invoice_number} - Container#{invoice.container_number}"
        to_emails = customer_emails
        cc_emails = ["ar@ngltrans.net"] + customer.get("ccEmails", [])
        bcc_emails = customer.get("bccEmails", [])

        result.to_emails = to_emails
        result.cc_emails = cc_emails
        result.bcc_emails = bcc_emails
        result.subject = subject

        await self._emit_send(job, "filling_send_form", {
            "invoiceNumber": invoice.invoice_number,
            "toEmails": to_emails,
            "subject": subject,
        })

        fill_result = await self._qbo.fill_send_form(
            to_emails, cc_emails, subject, bcc_emails,
            expected_attachment_count=0,  # No attachments — just the QBO invoice
        )

        if not fill_result.get("filled"):
            result.status = "error"
            result.error = "Failed to fill send form"
            await self._emit_send(job, "invoice_error", {
                "invoiceNumber": invoice.invoice_number,
                "error": result.error,
            })
            return

        # Test mode approval for QBO send
        if job.test_mode:
            # Override attachments_found for the approval card — OEC sends
            # zero file attachments, only the QBO invoice itself
            saved_att = result.attachments_found
            result.attachments_found = ["QBO invoice (no file attachments)"]
            approved = await self._wait_for_approval(job, invoice, result, index,
                                                      to_emails, cc_emails, bcc_emails, subject)
            result.attachments_found = saved_att  # restore for POD logic
            if not approved:
                return

        await self._emit_send(job, "sending_invoice", {
            "invoiceNumber": invoice.invoice_number,
        })

        sent = await self._qbo.click_send_invoice()
        if not sent:
            result.status = "error"
            result.error = "QBO send button click failed"
            await self._emit_send(job, "invoice_error", {
                "invoiceNumber": invoice.invoice_number,
                "error": result.error,
            })
            return

        await self._emit_send(job, "oec_qbo_sent", {
            "invoiceNumber": invoice.invoice_number,
        })

        # ── Part B: Download POD and send via Gmail ──
        temp_dir = Path(tempfile.mkdtemp(prefix="ngl_pod_"))
        pod_path = None

        # Try 1: Download POD from QBO (if it exists on the invoice page)
        if has_pod:
            await self._emit_send(job, "oec_downloading_pod", {
                "invoiceNumber": invoice.invoice_number,
            })
            await asyncio.sleep(QBO_ACTION_DELAY_S)
            await self._qbo.search_invoice(invoice.invoice_number)
            await asyncio.sleep(2)
            pod_path = await self._qbo.find_and_download_pod(temp_dir)

        # Try 2: Fetch POD from TMS portal (if QBO didn't have it or download failed)
        if not pod_path and self._tms:
            if self._tms.is_logged_in():
                await self._emit_send(job, "tms_fetching_pod", {
                    "invoiceNumber": invoice.invoice_number,
                    "containerNumber": invoice.container_number,
                })
                pod_path = await self._tms.fetch_pod_for_container(
                    invoice.container_number, temp_dir
                )
                if pod_path:
                    await self._emit_send(job, "tms_pod_downloaded", {
                        "invoiceNumber": invoice.invoice_number,
                        "fileName": pod_path.name,
                    })
                else:
                    await self._emit_send(job, "tms_pod_not_found", {
                        "invoiceNumber": invoice.invoice_number,
                        "containerNumber": invoice.container_number,
                    })
            else:
                await self._emit_send(job, "tms_login_required", {
                    "invoiceNumber": invoice.invoice_number,
                    "error": "TMS not logged in — log in via Agent panel to enable POD fetching",
                })

        # No POD found anywhere
        if not pod_path:
            source = "QBO or TMS" if self._tms else "QBO"
            result.status = "sent"
            result.error = f"QBO invoice sent but no POD found ({source}) — send POD manually"
            await self._emit_send(job, "oec_pod_email_failed", {
                "invoiceNumber": invoice.invoice_number,
                "error": f"No POD found in {source} — send POD manually",
            })
            return

        # If D/O sender email is missing, try to look it up from TMS
        if not invoice.do_sender_email and self._tms and self._tms.is_logged_in():
            await self._emit_send(job, "tms_fetching_pod", {
                "invoiceNumber": invoice.invoice_number,
                "containerNumber": invoice.container_number,
                "message": "Looking up D/O sender from TMS...",
            })
            tms_do_sender = await self._tms.fetch_do_sender_email(invoice.container_number)
            if tms_do_sender:
                invoice.do_sender_email = tms_do_sender
                logger.info("D/O sender from TMS for %s: %s", invoice.container_number, tms_do_sender)

        # Build POD email recipients
        pod_to = list(customer.get("podEmailTo", []))
        if invoice.do_sender_email:
            pod_to.append(invoice.do_sender_email)
        pod_cc = list(customer.get("podEmailCc", []))
        pod_subject = customer.get("podEmailSubject", "") or f"POD — {invoice.container_number}"
        pod_body = customer.get("podEmailBody", "") or f"Please find attached the Proof of Delivery for container {invoice.container_number}."

        # Template token replacement
        token_map = {
            "{invoice_number}": invoice.invoice_number,
            "{container_number}": invoice.container_number,
            "{customer_name}": customer.get("name", ""),
            "{customer_code}": invoice.customer_code,
        }
        for token, value in token_map.items():
            pod_subject = pod_subject.replace(token, value)
            pod_body = pod_body.replace(token, value)

        # Test mode approval for POD email
        if job.test_mode:
            job._approval_event = asyncio.Event()
            job._approval_decision = None
            await self._emit_send(job, "awaiting_approval", {
                "invoiceNumber": invoice.invoice_number,
                "containerNumber": invoice.container_number,
                "customerCode": invoice.customer_code,
                "toEmails": pod_to,
                "ccEmails": pod_cc,
                "bccEmails": [],
                "subject": pod_subject,
                "emailBody": pod_body,
                "attachmentsFound": ["POD"],
                "index": index,
                "total": job.total,
                "message": "OEC POD email ready — review recipients before sending",
                "flowType": "oec_pod_email",
            })
            logger.info("Test mode: waiting for approval on OEC POD email for %s", invoice.invoice_number)
            try:
                await asyncio.wait_for(job._approval_event.wait(), timeout=300)
            except asyncio.TimeoutError:
                result.status = "skipped"
                result.error = "POD email approval timed out (5 minutes)"
                await self._emit_send(job, "invoice_skipped", {
                    "invoiceNumber": invoice.invoice_number,
                    "reason": "approval_timeout",
                })
                job._approval_event = None
                return
            approved = job._approval_decision is True
            job._approval_event = None
            job._approval_decision = None
            if not approved:
                result.status = "sent"  # QBO part already sent, just skipping POD email
                result.error = "QBO sent but POD email skipped by user"
                await self._emit_send(job, "invoice_skipped", {
                    "invoiceNumber": invoice.invoice_number,
                    "reason": "user_skipped_pod_email",
                })
                return

        await self._emit_send(job, "oec_sending_pod_email", {
            "invoiceNumber": invoice.invoice_number,
            "to": pod_to,
            "cc": pod_cc,
        })

        email_result = await self._email_sender.send_pod_email(
            to=pod_to,
            cc=pod_cc,
            subject=pod_subject,
            body=pod_body,
            pod_path=pod_path,
        )

        if email_result.get("sent"):
            result.status = "sent"
            await self._emit_send(job, "oec_pod_email_sent", {
                "invoiceNumber": invoice.invoice_number,
                "to": pod_to,
                "cc": pod_cc,
            })
        else:
            result.status = "error"
            result.error = f"QBO sent but POD email failed: {email_result.get('error', 'Unknown')}"
            await self._emit_send(job, "oec_pod_email_failed", {
                "invoiceNumber": invoice.invoice_number,
                "error": email_result.get("error", "Unknown error"),
            })

        # Cleanup temp dir
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass

    async def _send_portal_upload(self, job: SendJob, invoice: SendRequest,
                                   customer: dict, result: SendResult, index: int) -> None:
        """Portal upload flow: Download invoice+POD from QBO, merge, upload to portal."""
        if not self._portal_uploader:
            result.status = "skipped"
            result.error = "Portal uploader not configured — check TranzAct credentials in .env"
            await self._emit_send(job, "invoice_skipped", {
                "invoiceNumber": invoice.invoice_number,
                "reason": "portal_not_configured",
            })
            return

        portal_url = customer.get("portalUrl", "")
        portal_client = customer.get("portalClient", "")
        if not portal_url or not portal_client:
            result.status = "skipped"
            result.error = "Portal URL or client name not configured for this customer"
            await self._emit_send(job, "invoice_skipped", {
                "invoiceNumber": invoice.invoice_number,
                "reason": "portal_not_configured",
                "customerCode": invoice.customer_code,
            })
            return

        # Search QBO for the invoice
        await self._emit_send(job, "searching_invoice", {
            "invoiceNumber": invoice.invoice_number,
        })

        invoice_url = await self._qbo.search_invoice(invoice.invoice_number)
        if not invoice_url:
            result.status = "error"
            result.error = f"Invoice {invoice.invoice_number} not found in QBO"
            await self._emit_send(job, "invoice_not_found", {
                "invoiceNumber": invoice.invoice_number,
            })
            return

        # Download invoice + POD from QBO
        await self._emit_send(job, "portal_downloading", {
            "invoiceNumber": invoice.invoice_number,
        })

        temp_dir = Path(tempfile.mkdtemp(prefix="ngl_portal_"))

        inv_path = await self._qbo.download_invoice_pdf(temp_dir)
        if not inv_path:
            result.status = "error"
            result.error = "Could not download invoice PDF from QBO"
            await self._emit_send(job, "portal_upload_failed", {
                "invoiceNumber": invoice.invoice_number,
                "error": result.error,
            })
            return

        await asyncio.sleep(QBO_ACTION_DELAY_S)
        pod_path = await self._qbo.find_and_download_pod(temp_dir)
        if not pod_path:
            result.status = "missing_docs"
            result.error = "POD not found — cannot create combined PDF for portal"
            await self._emit_send(job, "portal_upload_failed", {
                "invoiceNumber": invoice.invoice_number,
                "error": result.error,
            })
            return

        # Merge invoice + POD into one PDF
        await self._emit_send(job, "portal_merging", {
            "invoiceNumber": invoice.invoice_number,
        })

        try:
            from PyPDF2 import PdfMerger
            merged_path = temp_dir / f"{invoice.invoice_number}_combined.pdf"
            merger = PdfMerger()
            merger.append(str(inv_path))
            merger.append(str(pod_path))
            merger.write(str(merged_path))
            merger.close()
        except Exception as e:
            result.status = "error"
            result.error = f"Failed to merge PDFs: {e}"
            await self._emit_send(job, "portal_upload_failed", {
                "invoiceNumber": invoice.invoice_number,
                "error": result.error,
            })
            return

        # Test mode approval for portal upload
        if job.test_mode:
            job._approval_event = asyncio.Event()
            job._approval_decision = None
            await self._emit_send(job, "awaiting_approval", {
                "invoiceNumber": invoice.invoice_number,
                "containerNumber": invoice.container_number,
                "customerCode": invoice.customer_code,
                "toEmails": [portal_client],
                "ccEmails": [],
                "bccEmails": [],
                "subject": f"Portal upload: {portal_client}",
                "attachmentsFound": ["Invoice + POD (merged)"],
                "index": index,
                "total": job.total,
                "message": "PDF merged — ready to upload to portal. Approve to proceed.",
                "flowType": "portal_upload",
            })
            logger.info("Test mode: waiting for approval on portal upload for %s", invoice.invoice_number)
            try:
                await asyncio.wait_for(job._approval_event.wait(), timeout=300)
            except asyncio.TimeoutError:
                result.status = "skipped"
                result.error = "Portal upload approval timed out (5 minutes)"
                await self._emit_send(job, "invoice_skipped", {
                    "invoiceNumber": invoice.invoice_number,
                    "reason": "approval_timeout",
                })
                job._approval_event = None
                return
            approved = job._approval_decision is True
            job._approval_event = None
            job._approval_decision = None
            if not approved:
                result.status = "skipped"
                result.error = "Portal upload skipped by user"
                await self._emit_send(job, "invoice_skipped", {
                    "invoiceNumber": invoice.invoice_number,
                    "reason": "user_skipped",
                })
                return

        # Upload to portal
        await self._emit_send(job, "portal_uploading", {
            "invoiceNumber": invoice.invoice_number,
            "portalUrl": portal_url,
            "portalClient": portal_client,
        })

        upload_result = await self._portal_uploader.upload_to_tranzact(
            portal_url=portal_url,
            client_name=portal_client,
            pdf_path=merged_path,
        )

        if upload_result.get("uploaded"):
            result.status = "sent"
            await self._emit_send(job, "portal_upload_success", {
                "invoiceNumber": invoice.invoice_number,
                "portalClient": portal_client,
            })
        else:
            result.status = "error"
            result.error = f"Portal upload failed: {upload_result.get('error', 'Unknown')}"
            await self._emit_send(job, "portal_upload_failed", {
                "invoiceNumber": invoice.invoice_number,
                "error": upload_result.get("error", "Unknown error"),
            })

        # Cleanup temp dir
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Shared helpers for send handlers
    # ------------------------------------------------------------------

    async def _wait_for_approval(self, job: SendJob, invoice: SendRequest,
                                  result: SendResult, index: int,
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

    async def event_stream(self, job_id: str):
        """Async generator yielding SSE events for a job."""
        job = self._jobs.get(job_id)
        if not job:
            return

        while True:
            try:
                event = await asyncio.wait_for(job.events.get(), timeout=30)
                yield {
                    "event": event["type"],
                    "data": json.dumps(event),
                }
                if event["type"] in ("job_complete", "send_job_complete", "login_required", "job_paused"):
                    break
            except asyncio.TimeoutError:
                # Send keepalive
                yield {"event": "keepalive", "data": "{}"}
