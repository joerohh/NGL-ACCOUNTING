"""QBO API send mixin — hybrid: QBO API for lookup/verify + Gmail SMTP for send."""

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

from config import RESEND_NOTICE, TMS_FETCH_TIMEOUT_S
from services.email_template import build_invoice_email_html
from services.job_manager.util import (
    normalize_email_list,
    validate_and_append_email,
)

logger = logging.getLogger("ngl.job_manager")


class SendQBOApiMixin:
    """Send invoices using QBO API for lookup + Gmail SMTP for email delivery."""

    @staticmethod
    def _cleanup_temp(temp_dir):
        """Silently remove a temp directory if it exists."""
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)

    async def _tms_fetch_and_upload_missing_docs(
        self, job, invoice, api, invoice_id, verification, temp_dir,
        missing_docs, invoice_data: dict,
    ) -> list[str]:
        """Fetch each missing required doc via the TMS Data Layer, upload to QBO.

        Failures are accumulated in the data layer's per-job FailedRowsTracker; the
        UI shows them in the Failed Rows box with explicit Retry buttons. We never
        auto-fall-back to the browser — the user must opt in.
        """
        if not self._tms_data:
            logger.warning("TMSDataLayer not configured — skipping doc fetch for %s",
                           invoice.invoice_number)
            return []

        # Filter out non-fetchable types.
        types_to_fetch = [
            (m or "").lower() for m in missing_docs
            if (m or "").lower() and (m or "").lower() != "invoice"
        ]
        if not types_to_fetch:
            return []

        container = verification.get("found_container") or invoice.container_number or ""
        await self._emit_send(job, "tms_fetching_docs", {
            "invoiceNumber": invoice.invoice_number,
            "containerNumber": container,
            "docTypes": types_to_fetch,
        })

        fetched = await self._tms_data.get_documents(
            job.id, invoice_data, types_to_fetch, temp_dir, source="api",
        )

        # Surface any failures the layer just recorded.
        await self._emit_failed_rows_changed(job, "added")

        uploaded: list[str] = []
        for dt in types_to_fetch:
            path = fetched.get(dt)
            if not (path and path.exists()):
                await self._emit_send(job, "tms_doc_not_found", {
                    "invoiceNumber": invoice.invoice_number,
                    "docType": dt,
                })
                continue

            await self._emit_send(job, "uploading_doc_to_qbo", {
                "invoiceNumber": invoice.invoice_number,
                "docType": dt,
                "fileName": path.name,
            })
            if await api.upload_attachment(invoice_id, path):
                uploaded.append(dt)
                await self._emit_send(job, "doc_uploaded_to_qbo", {
                    "invoiceNumber": invoice.invoice_number,
                    "docType": dt,
                    "fileName": path.name,
                })
            else:
                await self._emit_send(job, "doc_upload_failed", {
                    "invoiceNumber": invoice.invoice_number,
                    "docType": dt,
                    "error": "QBO upload API returned no result",
                })

        return uploaded

    async def _send_qbo_api(self, job, invoice, customer: dict,
                             result, index: int) -> None:
        """Hybrid send: QBO API (search/verify/attachments) + Gmail (email with custom subject)."""
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

        # Step 1: Search for invoice in QBO
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

        # Step 2: Verify invoice details
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

        # Step 3: Check attachments
        # OEC flow: the D/O email (already sent before this step) carries the POD.
        # The QBO invoice email attaches invoice PDF only, so we don't enforce
        # requiredDocs here — no need to fail the invoice send because POD isn't
        # on the QBO record yet.
        is_oec = customer.get("sendMethod") == "qbo_invoice_only_then_pod_email"
        required_docs = [] if is_oec else customer.get("requiredDocs", [])
        await self._emit_send(job, "checking_attachments", {
            "invoiceNumber": invoice.invoice_number,
        })

        att_check = await api.check_attachments(invoice_id, required_docs)
        result.attachments_found = att_check.get("found", [])
        result.attachments_missing = att_check.get("missing", [])
        all_attachments = att_check.get("attachments", [])

        # Step 3b: Auto-fetch missing docs from TMS and upload to QBO.
        # Missing docs come from customer.requiredDocs vs what's attached to the
        # QBO invoice. For OEC customers the D/O email step already handled TMS
        # lookup + POD — so we skip the fetch-and-upload here.
        missing_docs = [m for m in (result.attachments_missing or []) if (m or "").lower() != "invoice"]
        temp_dir = None

        logger.info("Attachment check for %s: found=%s, missing=%s, tms_available=%s",
                     invoice.invoice_number, result.attachments_found,
                     result.attachments_missing, bool(self._tms_data))
        for a in all_attachments:
            logger.info("  -> '%s' classified as '%s'", a.get("fileName"), a.get("docType"))

        if missing_docs and self._tms_data and not is_oec:
            temp_dir = Path(tempfile.mkdtemp(prefix="ngl_docs_"))
            try:
                uploaded = await asyncio.wait_for(
                    self._tms_fetch_and_upload_missing_docs(
                        job, invoice, api, invoice_id, verification, temp_dir,
                        missing_docs, invoice_data=invoice_data,
                    ),
                    timeout=TMS_FETCH_TIMEOUT_S,
                )
                if uploaded:
                    att_check = await api.check_attachments(invoice_id, required_docs)
                    result.attachments_found = att_check.get("found", [])
                    result.attachments_missing = att_check.get("missing", [])
                    all_attachments = att_check.get("attachments", [])
            except asyncio.TimeoutError:
                logger.warning("TMS doc fetch timed out after %ds for %s — skipping",
                               TMS_FETCH_TIMEOUT_S, invoice.invoice_number)
                await self._emit_send(job, "tms_fetch_timeout", {
                    "invoiceNumber": invoice.invoice_number,
                    "message": f"TMS doc fetch timed out after {TMS_FETCH_TIMEOUT_S}s",
                })
            except Exception as e:
                logger.warning("TMS doc fetch failed for %s: %s",
                               invoice.invoice_number, e)
                await self._emit_send(job, "tms_fetch_error", {
                    "invoiceNumber": invoice.invoice_number,
                    "error": str(e),
                })

        # OEC's invoice email attaches ONLY the invoice PDF (no QBO attachments),
        # so an empty attachment list on QBO is fine for OEC — don't abort.
        if not all_attachments and not is_oec:
            result.status = "skipped_no_attachments"
            result.error = "No attachments found on invoice"
            await self._emit_send(job, "invoice_skipped", {
                "invoiceNumber": invoice.invoice_number,
                "reason": "no_attachments",
            })
            self._cleanup_temp(temp_dir)
            return

        if required_docs and not att_check.get("allPresent"):
            result.status = "missing_docs"
            result.error = f"Missing required docs: {', '.join(result.attachments_missing)}"
            await self._emit_send(job, "invoice_missing_docs", {
                "invoiceNumber": invoice.invoice_number,
                "found": result.attachments_found,
                "missing": result.attachments_missing,
            })
            self._cleanup_temp(temp_dir)
            return

        # Step 4: Build email fields
        container = verification.get("found_container") or invoice.container_number or ""
        # Use subject as-is if provided (frontend handles [NGL_INV_REVISED] prefix for resends)
        subject = invoice.subject or f"[NGL_INV] {invoice.invoice_number} - Container#{container}"
        to_emails = customer_emails
        cc_emails = ["ar@ngltrans.net"] + normalize_email_list(customer.get("ccEmails", []))
        bcc_emails = normalize_email_list(customer.get("bccEmails", []))

        # OEC: also CC the D/O sender (resolved in the preceding POD email step)
        if is_oec:
            added = validate_and_append_email(
                cc_emails, invoice.do_sender_email, label="D/O SENDER (invoice email)"
            )
            await self._emit_send(job, "oec_invoice_cc_built", {
                "invoiceNumber": invoice.invoice_number,
                "ccEmails": cc_emails,
                "doSenderEmail": invoice.do_sender_email or "",
                "doSenderIncluded": added,
            })

        result.to_emails = to_emails
        result.cc_emails = cc_emails
        result.bcc_emails = bcc_emails
        result.subject = subject

        await self._emit_send(job, "filling_send_form", {
            "invoiceNumber": invoice.invoice_number,
            "toEmails": to_emails,
            "subject": subject,
        })

        # Step 5: Test mode approval
        if job.test_mode:
            # For OEC, the email will attach only the invoice PDF (POD/D-O doc
            # goes out separately). Show that accurately in the approval preview
            # instead of the QBO attachment list which would mislead the user.
            attachments_display = ["invoice"] if is_oec else result.attachments_found
            approved = await self._wait_for_approval(
                job, invoice, result, index,
                to_emails, cc_emails, bcc_emails, subject,
                attachments_display=attachments_display,
            )
            if not approved:
                self._cleanup_temp(temp_dir)
                return

        # Step 6: Download invoice PDF + all attachments from QBO
        await self._emit_send(job, "downloading_attachments", {
            "invoiceNumber": invoice.invoice_number,
            "count": len(all_attachments) + 1,  # +1 for invoice PDF
        })

        email_attachments = []

        # Download the invoice PDF
        invoice_pdf = await api.download_invoice_pdf(invoice_id)
        if invoice_pdf:
            email_attachments.append({
                "filename": f"{invoice.invoice_number}.pdf",
                "data": invoice_pdf,
            })

        # Download all linked attachments (POD, BOL, etc.)
        # OEC flow (qbo_invoice_only_then_pod_email): send invoice PDF only —
        # POD/DO go out in the separate POD email.
        import httpx

        # HARD RULE: OEC invoice email carries the invoice PDF ONLY.
        # POD/D-O doc goes out separately in the preceding D/O email.
        attachments_to_email = [] if is_oec else all_attachments

        for att in attachments_to_email:
            fname = att.get("fileName", "attachment.pdf")
            try:
                download_url = att.get("tempDownloadUri")
                if not download_url:
                    # Fallback: /download/{id} returns a redirect URL as text
                    att_id = att.get("id")
                    if not att_id:
                        continue
                    token = await api._token_manager.get_access_token()
                    realm = api._token_manager.realm_id or api._realm_id
                    url = f"{api._base_url}/v3/company/{realm}/download/{att_id}"
                    async with httpx.AsyncClient(timeout=60.0) as client:
                        resp = await client.get(
                            url,
                            headers={"Authorization": f"Bearer {token}"},
                            timeout=30,
                        )
                    if resp.status_code != 200:
                        logger.warning("Failed to get download URL for %s: %d", fname, resp.status_code)
                        continue
                    download_url = resp.content.decode("utf-8").strip()

                # Fetch the actual file bytes
                async with httpx.AsyncClient(follow_redirects=True) as client:
                    file_resp = await client.get(download_url, timeout=30)
                if file_resp.status_code == 200:
                    email_attachments.append({
                        "filename": fname,
                        "data": file_resp.content,
                    })
                else:
                    logger.warning("Failed to download attachment %s: %d", fname, file_resp.status_code)
            except Exception as e:
                logger.warning("Error downloading attachment %s: %s", fname, e)

        if not email_attachments:
            result.status = "error"
            result.error = "Failed to download invoice PDF and attachments"
            await self._emit_send(job, "invoice_error", {
                "invoiceNumber": invoice.invoice_number,
                "error": result.error,
            })
            self._cleanup_temp(temp_dir)
            return

        # Step 7: Send via Gmail SMTP
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

        # Extract customer name and ref# from invoice data
        customer_name = invoice_data.get("CustomerRef", {}).get("name", "")
        if "] " in customer_name:
            customer_name = customer_name.split("] ", 1)[1]

        # Parse "NGL REF#/Your REF#" custom field for the email body.
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

        # Get QBO payment portal link for the "Review and pay" button
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

        # HARD RULE for OEC: invoice email carries ONLY the invoice PDF.
        # Guard against regressions that might sneak extra attachments back in.
        if is_oec and len(email_attachments) != 1:
            logger.error(
                "[OEC_INVOICE] Attachment rule violation for %s: expected 1 (invoice PDF), got %d — %s",
                invoice.invoice_number, len(email_attachments),
                [a.get("filename") for a in email_attachments],
            )
            email_attachments = [a for a in email_attachments
                                 if a.get("filename") == f"{invoice.invoice_number}.pdf"]

        send_result = await self._email_sender.send_invoice_email(
            to=to_emails,
            cc=cc_emails,
            bcc=bcc_emails,
            subject=subject,
            body=body,
            attachments=email_attachments,
        )

        if send_result.get("sent"):
            # For OEC, reconcile final status with the preceding POD email result.
            if is_oec and result.pod_status == "failed":
                result.status = "sent_no_pod"
            elif is_oec and result.pod_status == "skipped":
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
                "attachmentCount": len(email_attachments),
                "podStatus": result.pod_status if is_oec else "",
            })
        else:
            result.status = "error"
            result.error = send_result.get("error", "Gmail send failed")
            await self._emit_send(job, "invoice_error", {
                "invoiceNumber": invoice.invoice_number,
                "error": result.error,
            })

        self._cleanup_temp(temp_dir)
