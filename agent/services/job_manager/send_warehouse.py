"""Warehouse invoice send mixin — QBO-only, xlsx-as-is, no TMS, no doc rules."""

import logging
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from services.job_manager.util import normalize_email_list

logger = logging.getLogger("ngl.job_manager")


class SendWarehouseMixin:
    """Send invoices whose INV# position-2 letter is 'W'."""

    async def _send_warehouse_invoice(self, job, invoice, customer: dict, result, index: int) -> None:
        """Fetch invoice PDF + every QBO attachment; send via Gmail SMTP."""
        result.routing_type = "warehouse"
        api = self._qbo_api

        # Step 1 — find the invoice in QBO.
        invoice_data = await api.search_invoice(invoice.invoice_number)
        if not invoice_data:
            result.status = "error"
            result.error = f"QuickBooks couldn't find invoice {invoice.invoice_number}."
            await self._emit_send(job, "invoice_error", {
                "invoiceNumber": invoice.invoice_number,
                "error": result.error,
            })
            return

        invoice_id = invoice_data["Id"]

        # Step 2 — list non-invoice QBO attachments. The invoice PDF is fetched
        # separately and never appears in this list (QBO's Attachable endpoint
        # returns user-uploaded files only, not the invoice template render).
        attachments = await api.list_attachments(invoice_id) or []

        if not attachments:
            result.status = "missing_docs"
            result.error = ("No documents attached in QuickBooks. Add at least one "
                            "Excel or PDF backup to the QBO invoice, then resend.")
            result.warehouse_attachments = []
            result.warehouse_failures = []
            await self._emit_send(job, "warehouse_no_docs", {
                "invoiceNumber": invoice.invoice_number,
                "customerCode": invoice.customer_code,
            })
            return

        # Step 3 — download invoice PDF + every attachment into a temp dir.
        temp_dir = Path(tempfile.mkdtemp(prefix="ngl_wh_send_"))
        try:
            invoice_pdf_bytes = await api.download_invoice_pdf(invoice_id)
            invoice_pdf_path = temp_dir / f"invoice_{invoice.invoice_number}.pdf"
            invoice_pdf_path.write_bytes(invoice_pdf_bytes)

            successes: list[dict] = []
            failures: list[dict] = []
            for att in attachments:
                try:
                    path = await api.download_attachment(
                        att["id"], att["fileName"], temp_dir,
                        temp_download_uri=att.get("tempDownloadUri"),
                    )
                    if path and Path(path).exists():
                        successes.append({"fileName": att["fileName"],
                                          "sizeBytes": Path(path).stat().st_size})
                    else:
                        failures.append({"fileName": att["fileName"],
                                         "error": "download returned no path"})
                except Exception as e:
                    failures.append({"fileName": att["fileName"], "error": str(e)})

            result.warehouse_attachments = successes
            result.warehouse_failures = failures

            # Per spec: ANY attachment download failure blocks the send for that
            # row. The user must see the failure list before the customer gets
            # an incomplete email. Partial-success → block, not send.
            if failures:
                result.status = "error"
                first = failures[0]
                result.error = (
                    f"Couldn't download {first['fileName']} from QuickBooks. "
                    "Try again in a minute."
                )
                await self._emit_send(job, "invoice_error", {
                    "invoiceNumber": invoice.invoice_number,
                    "error": result.error,
                    "warehouseFailures": failures,
                })
                return

            # Step 4 — send via Gmail SMTP with original filenames + MIME from extension.
            email_attachments = [
                {"filename": invoice_pdf_path.name, "data": invoice_pdf_path.read_bytes()},
            ]
            for att in attachments:
                local = temp_dir / att["fileName"]
                if local.exists():
                    email_attachments.append({
                        "filename": att["fileName"], "data": local.read_bytes(),
                    })

            to = normalize_email_list(customer.get("emails", []))
            cc = normalize_email_list(customer.get("ccEmails", []))
            bcc = normalize_email_list(customer.get("bccEmails", []))

            send_outcome = await self._email_sender.send_invoice_email(
                to=to, cc=cc, bcc=bcc,
                subject=invoice.subject,
                body=f"Please find attached warehouse invoice {invoice.invoice_number}.",
                attachments=email_attachments,
            )

            if send_outcome.get("sent"):
                result.status = "sent"
                result.to_emails = to
                result.cc_emails = cc
                result.bcc_emails = bcc
                result.subject = invoice.subject
                result.timestamp = datetime.now(timezone.utc).isoformat()
                await self._emit_send(job, "invoice_sent", {
                    "invoiceNumber": invoice.invoice_number,
                    "to": to, "cc": cc,
                })
            else:
                result.status = "error"
                result.error = send_outcome.get("error", "Email send failed.")
                await self._emit_send(job, "invoice_error", {
                    "invoiceNumber": invoice.invoice_number,
                    "error": result.error,
                })
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
