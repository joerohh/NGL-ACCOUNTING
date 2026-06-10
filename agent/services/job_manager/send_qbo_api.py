"""QBO API send mixin — hybrid: QBO API for lookup/verify + Gmail SMTP for send."""

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from config import RESEND_NOTICE, TMS_FETCH_TIMEOUT_S
from services.email_template import build_invoice_email_html
from services.job_manager.util import (
    normalize_email_list,
    validate_and_append_email,
)
from services.qbo_api.dedup import dedupe_attachments

logger = logging.getLogger("ngl.job_manager")


class SendQBOApiMixin:
    """Send invoices using QBO API for lookup + Gmail SMTP for email delivery."""

    @staticmethod
    def _cleanup_temp(temp_dir):
        """Silently remove a temp directory if it exists."""
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)

    async def _dedup_and_emit(self, job, invoice_number: str,
                              attachments: list[dict]) -> list[dict]:
        """Run dedupe_attachments, log INFO if any skipped, and emit SSE event.

        Returns the kept list. SSE event 'attachments_deduped' is emitted only
        when skipped > 0.
        """
        kept, skipped = dedupe_attachments(attachments)
        if skipped:
            logger.info(
                "Deduped attachments for %s: kept %d of %d (skipped %d TMS duplicates)",
                invoice_number, len(kept), len(attachments), len(skipped),
            )
            await self._emit_send(job, "attachments_deduped", {
                "invoiceNumber": invoice_number,
                "kept": len(kept),
                "skipped": len(skipped),
                "skippedFiles": [a.get("fileName", "") for a in skipped],
            })
        return kept

    async def _tms_fetch_and_upload_missing_docs(
        self, job, invoice, api, invoice_id, verification, temp_dir,
        invoice_data: dict, existing_attachments: list[dict],
    ) -> list[str]:
        """DISABLED from standard email send (2026-06-10). Preserved as dead code.

        See docs/superpowers/specs/2026-06-10-tms-direct-email-design.md for context.

        To re-enable: (1) fix DOC_PATTERNS in attachments.py so DO/IT/ITE recognize
        TMS-style `_<type>_<ms-timestamp>.pdf` filenames; (2) add retry to
        upload_attachment in attachments.py mirroring download_document's policy;
        (3) persist cascade failures to the audit_log row (new column or status).

        Replaced by direct TMS→email attachment in _send_qbo_api after a silent
        upload failure on PROMAR01 LM26060242F emailed the invoice without a POD
        on 2026-06-08.

        ---

        Fetch every TMS document for the WO; upload to QBO any not already attached.

        TMS is the source of truth for supporting documents. QBO holds only the
        invoice PDF. We compute the dedupe set up front (existing QBO docTypes +
        the QBO-owned 'invoice' type) and pass it as `skip_types` to the cascade
        so docs we'd skip aren't downloaded over the network. Remaining downloads
        and the corresponding QBO uploads run in parallel via asyncio.gather.

        Per-doc download failures land in the data layer's FailedRowsTracker;
        the UI exposes them in the Failed Rows box with explicit Retry buttons.
        We never auto-fall-back to the browser — the user must opt in.
        """
        import asyncio

        if not self._tms_data:
            logger.warning("TMSDataLayer not configured — skipping doc fetch for %s",
                           invoice.invoice_number)
            return []

        container = verification.get("found_container") or invoice.container_number or ""

        # Compute skip_types up front: every QBO docType (lowercased) + the
        # QBO-owned 'invoice' type. Cascade skips downloads for these.
        skip_types = {
            (a.get("docType") or "").lower()
            for a in (existing_attachments or [])
            if a.get("docType")
        }
        skip_types.add("invoice")

        await self._emit_send(job, "tms_fetching_docs", {
            "invoiceNumber": invoice.invoice_number,
            "containerNumber": container,
            "docTypes": [],  # populated as docs come back via uploading_doc_to_qbo events
        })

        rows_before = len(self._tms_data.get_failed_rows(job.id))
        fetched = await self._tms_data.get_all_documents(
            job.id, invoice_data, temp_dir, source="api", skip_types=skip_types,
        )
        if len(self._tms_data.get_failed_rows(job.id)) > rows_before:
            await self._emit_failed_rows_changed(job, "added")

        if not fetched:
            return []

        # Filter out paths that don't exist on disk (defensive).
        valid_uploads = [(dt, p) for dt, p in fetched.items() if p and p.exists()]
        if not valid_uploads:
            return []

        async def _upload_one(dt: str, path) -> Optional[str]:
            await self._emit_send(job, "uploading_doc_to_qbo", {
                "invoiceNumber": invoice.invoice_number,
                "docType": dt,
                "fileName": path.name,
            })
            if await api.upload_attachment(invoice_id, path):
                await self._emit_send(job, "doc_uploaded_to_qbo", {
                    "invoiceNumber": invoice.invoice_number,
                    "docType": dt,
                    "fileName": path.name,
                })
                return dt
            await self._emit_send(job, "doc_upload_failed", {
                "invoiceNumber": invoice.invoice_number,
                "docType": dt,
                "error": "QBO upload API returned no result",
            })
            return None

        results = await asyncio.gather(*[_upload_one(dt, p) for dt, p in valid_uploads])
        return [r for r in results if r is not None]

    async def _send_qbo_api(self, job, invoice, customer: dict,
                             result, index: int) -> None:
        """Standard email send: QBO API for the invoice PDF + TMS API for supporting docs + Gmail SMTP for delivery.

        Flow (see docs/superpowers/specs/2026-06-10-tms-direct-email-design.md):
          1. Search QBO for the invoice -> invoice_id, customer fields, ref fields
          2. Verify invoice details (container#, amount)
          3. Fetch every TMS doc with a file_url for the WO
          4. If customer requires POD and TMS returned none -> status=pod_missing, hold
          5. If TMS unreachable after retries -> status=tms_unreachable, hold
          6. Download invoice PDF from QBO
          7. Email = invoice PDF + every TMS doc
          8. Audit row records attachments_emailed = [tms doc types]

        OEC flow is dispatched separately by send_job.py — this method only handles
        the non-OEC standard email path. Warehouse and portal use their own mixins.
        """
        customer_emails = normalize_email_list(customer.get("emails", []))
        if not customer_emails:
            result.status = "skipped"
            result.error = f"No emails configured for customer: {invoice.customer_code}"
            await self._emit_send(job, "invoice_skipped", {
                "invoiceNumber": invoice.invoice_number,
                "reason": "no_emails",
                "customerCode": invoice.customer_code,
            })
            return

        api = self._qbo_api

        # Step 1: Search QBO for the invoice.
        await self._emit_send(job, "searching_invoice", {
            "invoiceNumber": invoice.invoice_number,
        })

        invoice_data = await api.search_invoice(invoice.invoice_number)
        if not invoice_data:
            result.status = "error"
            result.error = f"Invoice {invoice.invoice_number} not found in QBO"
            await self._emit_send(job, "invoice_not_found", {
                "invoiceNumber": invoice.invoice_number,
            })
            return

        invoice_id = invoice_data["Id"]

        # Step 2: Verify invoice details.
        await self._emit_send(job, "verifying_invoice", {
            "invoiceNumber": invoice.invoice_number,
            "containerNumber": invoice.container_number,
        })

        verification = await api.verify_invoice_details(
            invoice_data, invoice.container_number, invoice.amount or None
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

        if verification.get("amount_note"):
            await self._emit_send(job, "invoice_amount_warning", {
                "invoiceNumber": invoice.invoice_number,
                "note": verification["amount_note"],
            })

        # OEC fork: invoice email carries the invoice PDF ONLY. The POD already
        # went out in the preceding D/O email (see send_oec.py). Skip TMS fetch,
        # CC the D/O sender, and reconcile status with the D/O leg's pod_status.
        is_oec = customer.get("sendMethod") == "qbo_invoice_only_then_pod_email"
        if is_oec:
            await self._send_qbo_api_oec(job, invoice, customer, result, index,
                                         api, invoice_data, invoice_id, verification,
                                         customer_emails)
            return

        # Step 3: Fetch every TMS doc with a file_url. TMS is the source of truth
        # for supporting docs in the new flow — we never upload to QBO from here.
        await self._emit_send(job, "tms_fetching_docs", {
            "invoiceNumber": invoice.invoice_number,
            "containerNumber": verification.get("found_container") or invoice.container_number or "",
            "docTypes": [],
        })

        temp_dir = None
        if not self._tms_data:
            logger.warning("TMSDataLayer not configured — skipping TMS doc fetch for %s",
                           invoice.invoice_number)
            tms_paths: dict = {}
            tms_reason = "tms_unreachable"
        else:
            temp_dir = Path(tempfile.mkdtemp(prefix="ngl_docs_"))
            try:
                rows_before = len(self._tms_data.get_failed_rows(job.id))
                tms_paths, tms_reason = await asyncio.wait_for(
                    self._tms_data.get_all_documents_with_reason(
                        job.id, invoice_data, temp_dir, source="api",
                    ),
                    timeout=TMS_FETCH_TIMEOUT_S,
                )
                if len(self._tms_data.get_failed_rows(job.id)) > rows_before:
                    await self._emit_failed_rows_changed(job, "added")
            except asyncio.TimeoutError:
                logger.warning("TMS doc fetch timed out after %ds for %s",
                               TMS_FETCH_TIMEOUT_S, invoice.invoice_number)
                tms_paths, tms_reason = {}, "tms_unreachable"

        # Step 4: TMS unreachable -> hold send.
        if tms_reason == "tms_unreachable":
            result.status = "tms_unreachable"
            result.error = "TMS unreachable after retries — supporting docs could not be fetched"
            await self._emit_send(job, "tms_fetch_error", {
                "invoiceNumber": invoice.invoice_number,
                "error": result.error,
            })
            self._cleanup_temp(temp_dir)
            return

        # Step 5: POD required but missing -> hold send.
        required_docs = [d.lower() for d in customer.get("requiredDocs", [])]
        pod_required = "pod" in required_docs
        if pod_required and "pod" not in tms_paths:
            result.status = "pod_missing"
            wo_no = ""
            for f in invoice_data.get("CustomField", []) or []:
                if "REF" in (f.get("Name") or "").upper():
                    val = (f.get("StringValue") or "").split("/", 1)[0].strip()
                    if val:
                        wo_no = val
                        break
            result.error = f"POD not yet available on TMS for WO {wo_no or '<unknown>'}"
            await self._emit_send(job, "invoice_pod_missing", {
                "invoiceNumber": invoice.invoice_number,
                "woNo": wo_no,
            })
            self._cleanup_temp(temp_dir)
            return

        # Step 6: Build email fields.
        container = verification.get("found_container") or invoice.container_number or ""
        subject = invoice.subject or f"[NGL_INV] {invoice.invoice_number} - Container#{container}"
        to_emails = customer_emails
        cc_emails = ["ar@ngltrans.net"] + normalize_email_list(customer.get("ccEmails", []))
        bcc_emails = normalize_email_list(customer.get("bccEmails", []))

        result.to_emails = to_emails
        result.cc_emails = cc_emails
        result.bcc_emails = bcc_emails
        result.subject = subject
        result.attachments_emailed = sorted(tms_paths.keys())

        await self._emit_send(job, "filling_send_form", {
            "invoiceNumber": invoice.invoice_number,
            "toEmails": to_emails,
            "subject": subject,
        })

        # Test mode approval gate (unchanged from old flow).
        if job.test_mode:
            approved = await self._wait_for_approval(
                job, invoice, result, index,
                to_emails, cc_emails, bcc_emails, subject,
                attachments_display=result.attachments_emailed,
            )
            if not approved:
                self._cleanup_temp(temp_dir)
                return

        # Step 7: Download invoice PDF + build attachments list.
        await self._emit_send(job, "downloading_attachments", {
            "invoiceNumber": invoice.invoice_number,
            "count": len(tms_paths) + 1,  # +1 for invoice PDF
        })

        email_attachments: list[dict] = []

        invoice_pdf = await api.download_invoice_pdf(invoice_id)
        if invoice_pdf:
            email_attachments.append({
                "filename": f"{invoice.invoice_number}.pdf",
                "data": invoice_pdf,
            })

        for doc_type, path in tms_paths.items():
            try:
                email_attachments.append({
                    "filename": path.name,
                    "data": path.read_bytes(),
                })
            except Exception as e:
                logger.warning("Failed to read TMS doc %s for email: %s", path, e)

        if not email_attachments:
            result.status = "error"
            result.error = "Failed to download invoice PDF and TMS attachments"
            await self._emit_send(job, "invoice_error", {
                "invoiceNumber": invoice.invoice_number,
                "error": result.error,
            })
            self._cleanup_temp(temp_dir)
            return

        # Step 8: Send via Gmail SMTP.
        await self._emit_send(job, "sending_invoice", {
            "invoiceNumber": invoice.invoice_number,
            "method": "gmail",
        })

        if not self._email_sender:
            result.status = "error"
            result.error = "Gmail email sender not configured"
            await self._emit_send(job, "invoice_error", {
                "invoiceNumber": invoice.invoice_number,
                "error": result.error,
            })
            self._cleanup_temp(temp_dir)
            return

        # Build email body (unchanged from old flow).
        customer_name = invoice_data.get("CustomerRef", {}).get("name", "")
        if "] " in customer_name:
            customer_name = customer_name.split("] ", 1)[1]

        ngl_ref = ""
        customer_ref = ""
        for field in invoice_data.get("CustomField", []):
            name = field.get("Name", "").upper()
            val = field.get("StringValue", "")
            if "REF" in name and "/" in val:
                parts = val.split("/", 1)
                if not ngl_ref:
                    ngl_ref = parts[0].strip()
                customer_ref = parts[1].strip() if len(parts) > 1 else ""
                break

        due_date = invoice_data.get("DueDate", "")
        amount = str(invoice_data.get("TotalAmt", ""))
        invoice_link = await api.get_invoice_link(invoice_id)

        body = build_invoice_email_html(
            invoice_number=invoice.invoice_number,
            container=container,
            customer_name=customer_name,
            amount=amount,
            due_date=due_date,
            ngl_ref=ngl_ref,
            customer_ref=customer_ref,
            invoice_link=invoice_link,
            resend_notice=RESEND_NOTICE,
        )

        send_result = await self._email_sender.send_invoice_email(
            to=to_emails,
            cc=cc_emails,
            bcc=bcc_emails,
            subject=subject,
            body=body,
            attachments=email_attachments,
        )

        if send_result.get("sent"):
            result.status = "sent"
            result.error = None
            await self._emit_send(job, "invoice_sent", {
                "invoiceNumber": invoice.invoice_number,
                "containerNumber": container,
                "toEmails": to_emails,
                "subject": subject,
                "method": "gmail",
                "attachmentCount": len(email_attachments),
            })
        else:
            result.status = "error"
            result.error = send_result.get("error", "Gmail send failed")
            await self._emit_send(job, "invoice_error", {
                "invoiceNumber": invoice.invoice_number,
                "error": result.error,
            })

        self._cleanup_temp(temp_dir)

    async def _send_qbo_api_oec(self, job, invoice, customer: dict, result, index: int,
                                 api, invoice_data: dict, invoice_id: str,
                                 verification: dict, customer_emails: list[str]) -> None:
        """OEC invoice-email leg: invoice PDF only, CC D/O sender, status reconciles with pod_status.

        The D/O email (carrying the POD) has already run in send_oec.py before this method is
        reached. This method sends the QBO invoice email with just the invoice PDF attached.
        """
        container = verification.get("found_container") or invoice.container_number or ""
        subject = invoice.subject or f"[NGL_INV] {invoice.invoice_number} - Container#{container}"
        to_emails = customer_emails
        cc_emails = ["ar@ngltrans.net"] + normalize_email_list(customer.get("ccEmails", []))
        added = validate_and_append_email(
            cc_emails, invoice.do_sender_email, label="D/O SENDER (invoice email)"
        )
        await self._emit_send(job, "oec_invoice_cc_built", {
            "invoiceNumber": invoice.invoice_number,
            "ccEmails": cc_emails,
            "doSenderEmail": invoice.do_sender_email or "",
            "doSenderIncluded": added,
        })
        bcc_emails = normalize_email_list(customer.get("bccEmails", []))

        result.to_emails = to_emails
        result.cc_emails = cc_emails
        result.bcc_emails = bcc_emails
        result.subject = subject
        result.attachments_emailed = ["invoice"]

        await self._emit_send(job, "filling_send_form", {
            "invoiceNumber": invoice.invoice_number,
            "toEmails": to_emails,
            "subject": subject,
        })

        if job.test_mode:
            approved = await self._wait_for_approval(
                job, invoice, result, index,
                to_emails, cc_emails, bcc_emails, subject,
                attachments_display=["invoice"],
            )
            if not approved:
                return

        await self._emit_send(job, "downloading_attachments", {
            "invoiceNumber": invoice.invoice_number,
            "count": 1,
        })

        invoice_pdf = await api.download_invoice_pdf(invoice_id)
        if not invoice_pdf:
            result.status = "error"
            result.error = "Failed to download invoice PDF from QBO"
            await self._emit_send(job, "invoice_error", {
                "invoiceNumber": invoice.invoice_number,
                "error": result.error,
            })
            return

        email_attachments = [{
            "filename": f"{invoice.invoice_number}.pdf",
            "data": invoice_pdf,
        }]

        await self._emit_send(job, "sending_invoice", {
            "invoiceNumber": invoice.invoice_number,
            "method": "gmail",
        })

        if not self._email_sender:
            result.status = "error"
            result.error = "Gmail email sender not configured"
            await self._emit_send(job, "invoice_error", {
                "invoiceNumber": invoice.invoice_number,
                "error": result.error,
            })
            return

        customer_name = invoice_data.get("CustomerRef", {}).get("name", "")
        if "] " in customer_name:
            customer_name = customer_name.split("] ", 1)[1]

        ngl_ref = ""
        customer_ref = ""
        for field in invoice_data.get("CustomField", []):
            name = field.get("Name", "").upper()
            val = field.get("StringValue", "")
            if "REF" in name and "/" in val:
                parts = val.split("/", 1)
                if not ngl_ref:
                    ngl_ref = parts[0].strip()
                customer_ref = parts[1].strip() if len(parts) > 1 else ""
                break

        due_date = invoice_data.get("DueDate", "")
        amount = str(invoice_data.get("TotalAmt", ""))
        invoice_link = await api.get_invoice_link(invoice_id)

        body = build_invoice_email_html(
            invoice_number=invoice.invoice_number,
            container=container,
            customer_name=customer_name,
            amount=amount,
            due_date=due_date,
            ngl_ref=ngl_ref,
            customer_ref=customer_ref,
            invoice_link=invoice_link,
            resend_notice=RESEND_NOTICE,
        )

        send_result = await self._email_sender.send_invoice_email(
            to=to_emails,
            cc=cc_emails,
            bcc=bcc_emails,
            subject=subject,
            body=body,
            attachments=email_attachments,
        )

        if send_result.get("sent"):
            if result.pod_status in ("failed", "skipped"):
                result.status = "sent_no_pod"
            else:
                result.status = "sent"
                result.error = None
            await self._emit_send(job, "invoice_sent", {
                "invoiceNumber": invoice.invoice_number,
                "containerNumber": container,
                "toEmails": to_emails,
                "subject": subject,
                "method": "gmail",
                "attachmentCount": 1,
                "podStatus": result.pod_status or "",
            })
        else:
            result.status = "error"
            result.error = send_result.get("error", "Gmail send failed")
            await self._emit_send(job, "invoice_error", {
                "invoiceNumber": invoice.invoice_number,
                "error": result.error,
            })
