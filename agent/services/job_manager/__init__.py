"""Background job orchestration — manages fetch & send jobs with SSE progress streaming."""

import asyncio
import json
import logging
import time
from typing import Optional

from config import DOWNLOADS_DIR, JOB_STATE_DIR
from services.qbo_browser import QBOBrowser
from services.tms_browser import TMSBrowser
from services.claude_classifier import ClaudeClassifier
from services.email_sender import EmailSender
from services.portal_uploader import PortalUploader

from services.job_manager.util import JobManagerUtilMixin
from services.job_manager.fetch_job import FetchJobMixin
from services.job_manager.send_job import SendJobMixin
from services.job_manager.send_qbo import SendQBOStandardMixin
from services.job_manager.send_oec import SendOECFlowMixin
from services.job_manager.send_portal import SendPortalUploadMixin

logger = logging.getLogger("ngl.job_manager")


# ──────────────────────────────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────────────────────────────

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
        self.do_sender_email: str = ""
        self.do_sender_source: str = ""  # "CSV", "TMS", or ""

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
            "doSenderEmail": self.do_sender_email,
            "doSenderSource": self.do_sender_source,
        }


class Job:
    """Represents a background fetch job."""

    def __init__(self, job_id: str, containers: list[ContainerRequest],
                 doc_types=None) -> None:
        self.id = job_id
        self.containers = containers
        self.doc_types = doc_types or ["invoice", "pod"]
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
        self._cc_override: Optional[list[str]] = None  # OEC: user-edited CC list

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


# ──────────────────────────────────────────────────────────────────────
# Facade class — combines all mixins
# ──────────────────────────────────────────────────────────────────────

class JobManager(
    JobManagerUtilMixin,
    FetchJobMixin,
    SendJobMixin,
    SendQBOStandardMixin,
    SendOECFlowMixin,
    SendPortalUploadMixin,
):
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
