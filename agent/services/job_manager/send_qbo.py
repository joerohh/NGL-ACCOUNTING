"""QBO standard send mixin — sends invoices with all attachments via QBO email."""

import asyncio
import logging

from config import QBO_ACTION_DELAY_S

logger = logging.getLogger("ngl.job_manager")


class SendQBOStandardMixin:
    """Standard QBO email send — all attachments, standard recipients."""

    async def _send_qbo_standard(self, job, invoice, customer: dict,
                                  result, index: int) -> None:
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

        # Log amount discrepancy as warning (QBO is source of truth)
        if verification.get("amount_note"):
            await self._emit_send(job, "invoice_amount_warning", {
                "invoiceNumber": invoice.invoice_number,
                "note": verification["amount_note"],
            })

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

        # >>> DRY RUN — stop before sending so we can verify the form <<<
        logger.info("DRY RUN: form filled successfully, skipping actual send")
        result.status = "dry_run"
        await self._emit_send(job, "invoice_sent", {
            "invoiceNumber": invoice.invoice_number,
            "containerNumber": invoice.container_number,
            "toEmails": to_emails,
            "subject": subject,
            "dryRun": True,
        })
        return
        # >>> END DRY RUN — remove this block to resume normal sending <<<

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
