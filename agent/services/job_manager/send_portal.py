"""Portal upload mixin — download from QBO API, merge, upload to customer portal."""

import logging
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger("ngl.job_manager")


class SendPortalUploadMixin:
    """Portal upload flow: Download invoice+POD from QBO API, merge, upload to portal."""

    async def _send_portal_upload(self, job, invoice, customer: dict,
                                   result, index: int) -> None:
        """Portal upload flow: Download invoice+POD from QBO API, merge, upload to portal."""
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

        api = self._qbo_api

        # Step 1: Search QBO for the invoice
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

        # Step 2: Download invoice PDF + POD from QBO API
        await self._emit_send(job, "portal_downloading", {
            "invoiceNumber": invoice.invoice_number,
        })

        temp_dir = Path(tempfile.mkdtemp(prefix="ngl_portal_"))

        # Download invoice PDF
        invoice_pdf_bytes = await api.download_invoice_pdf(invoice_id)
        if not invoice_pdf_bytes:
            result.status = "error"
            result.error = "Could not download invoice PDF from QBO"
            await self._emit_send(job, "portal_upload_failed", {
                "invoiceNumber": invoice.invoice_number,
                "error": result.error,
            })
            return

        inv_path = temp_dir / f"{invoice.invoice_number}.pdf"
        inv_path.write_bytes(invoice_pdf_bytes)

        # Find and download POD from attachments
        att_check = await api.check_attachments(invoice_id, ["pod"])
        all_attachments = att_check.get("attachments", [])

        pod_path = None
        for att in all_attachments:
            if att.get("docType") == "pod" and att.get("id"):
                pod_path = await api.download_attachment(
                    att["id"], att.get("fileName", "pod.pdf"), temp_dir
                )
                if pod_path:
                    break

        if not pod_path:
            result.status = "missing_docs"
            result.error = "POD not found — cannot create combined PDF for portal"
            await self._emit_send(job, "portal_upload_failed", {
                "invoiceNumber": invoice.invoice_number,
                "error": result.error,
            })
            return

        # Step 3: Merge invoice + POD into one PDF
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

        # Step 4: Test mode approval
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
                import asyncio as _asyncio
                await _asyncio.wait_for(job._approval_event.wait(), timeout=300)
            except Exception:
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

        # Step 5: Upload to portal
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
