"""Per-invoice retry mixin — recovery flow for failed invoice sends (Fix 2 of v2.62).

Self-contained: skips TMS cascade, requiredDocs gate, OEC routing, approval flow.
Trusts caller-supplied files (already verified by filename or Claude AI in the UI).
Schedules a fire-and-forget background upload of each file to QBO so it persists
beyond this email.
"""
import asyncio
import logging
from pathlib import Path

from services.database import get_customer
from services.email_template import build_invoice_email_html
from services.job_manager.util import normalize_email_list

logger = logging.getLogger("ngl.job_manager")


class RetryInvoiceMixin:
    """Adds retry_invoice() to JobManager."""

    async def retry_invoice(self, invoice: dict, files: list[dict]) -> dict:
        """Retry sending a single invoice with caller-supplied attachments.

        Args:
            invoice: dict with keys invoice_number, invoice_id, container_number,
                     customer_code, amount, subject (optional), do_sender_email (optional)
            files: list of {slot, path, verified_by} dicts. slot is 'POD'/'BOL'/etc.

        Returns:
            {status: 'sent'|'error', error: str|None, attachments_sent: int}
        """
        invoice_number = invoice["invoice_number"]
        invoice_id = invoice["invoice_id"]
        container = invoice.get("container_number") or ""
        customer_code = invoice["customer_code"]

        # Customer lookup
        customer = get_customer(customer_code)
        if not customer:
            logger.warning("retry_invoice: customer not found: %s", customer_code)
            return {"status": "error", "error": f"Customer {customer_code} not found", "attachments_sent": 0}

        if not self._email_sender:
            return {"status": "error", "error": "Email sender not configured", "attachments_sent": 0}

        # If the frontend passed invoice_number as the fallback for invoice_id
        # (because the row didn't carry a QBO entity Id), resolve to the real
        # QBO Id now. QBO entity IDs are short numeric strings; invoice numbers
        # are alphanumeric like LM26040864F.
        if invoice_id == invoice_number or not invoice_id.isdigit():
            looked_up = await self._qbo_api.search_invoice(invoice_number)
            if not looked_up or not looked_up.get("Id"):
                return {
                    "status": "error",
                    "error": f"Invoice {invoice_number} not found in QuickBooks",
                    "attachments_sent": 0,
                }
            invoice_id = looked_up["Id"]
            logger.info("retry_invoice: resolved %s → QBO Id %s", invoice_number, invoice_id)

        # Build email_attachments list: invoice PDF + caller files
        email_attachments = []

        # Invoice PDF (from QBO)
        invoice_pdf = await self._qbo_api.download_invoice_pdf(invoice_id)
        if invoice_pdf:
            email_attachments.append({
                "filename": f"{invoice_number}.pdf",
                "data": invoice_pdf,
            })
        else:
            logger.warning("retry_invoice: could not download invoice PDF for %s", invoice_number)

        # Caller-supplied files (read from disk)
        for f in files:
            path = Path(f["path"])
            try:
                data = path.read_bytes()
            except OSError as e:
                logger.error("retry_invoice: failed to read %s: %s", path, e)
                return {"status": "error", "error": f"Could not read uploaded file: {path.name}", "attachments_sent": 0}
            email_attachments.append({"filename": path.name, "data": data})

        if not email_attachments:
            return {"status": "error", "error": "No attachments could be assembled", "attachments_sent": 0}

        # Build email metadata
        to_emails = normalize_email_list(customer.get("emails", []))
        cc_emails = ["ar@ngltrans.net"] + normalize_email_list(customer.get("ccEmails", []))
        bcc_emails = normalize_email_list(customer.get("bccEmails", []))

        if not to_emails:
            return {"status": "error", "error": f"No primary email for customer {customer_code}", "attachments_sent": 0}

        subject = invoice.get("subject") or f"[NGL_INV] {invoice_number} - Container#{container}"

        # Build HTML body
        body = build_invoice_email_html(
            invoice_number=invoice_number,
            container=container,
            customer_name=customer.get("name", ""),
            amount=invoice.get("amount", ""),
        )

        # Send
        send_result = await self._email_sender.send_invoice_email(
            to=to_emails,
            cc=cc_emails,
            bcc=bcc_emails,
            subject=subject,
            body=body,
            attachments=email_attachments,
        )

        if not send_result.get("sent"):
            return {
                "status": "error",
                "error": send_result.get("error") or "Send failed",
                "attachments_sent": 0,
            }

        # Email sent — schedule background QBO uploads (fire-and-forget)
        for f in files:
            asyncio.create_task(self._background_qbo_upload(
                invoice_id=invoice_id,
                file_path=Path(f["path"]),
                slot=f.get("slot", ""),
            ))

        return {
            "status": "sent",
            "error": None,
            "attachments_sent": len(email_attachments),
        }

    async def _background_qbo_upload(self, invoice_id: str, file_path: Path, slot: str) -> None:
        """Fire-and-forget upload to QBO. Swallows errors — caller never awaits success."""
        try:
            await self._qbo_api.upload_attachment(
                invoice_id=invoice_id,
                file_path=file_path,
                include_on_send=False,
            )
            logger.info(
                "background QBO upload OK — invoice=%s slot=%s file=%s",
                invoice_id, slot, file_path.name,
            )
        except Exception as exc:
            logger.warning(
                "background QBO upload FAILED — invoice=%s slot=%s file=%s err=%s",
                invoice_id, slot, file_path.name, exc,
            )
