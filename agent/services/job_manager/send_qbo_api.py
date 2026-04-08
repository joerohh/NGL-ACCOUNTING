"""QBO API send mixin — hybrid: QBO API for lookup/verify + Gmail SMTP for send."""

import logging

from services.email_template import build_invoice_email_html

logger = logging.getLogger("ngl.job_manager")


class SendQBOApiMixin:
    """Send invoices using QBO API for lookup + Gmail SMTP for email delivery."""

    async def _send_qbo_api(self, job, invoice, customer: dict,
                             result, index: int) -> None:
        """Hybrid send: QBO API (search/verify/attachments) + Gmail (email with custom subject)."""
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
        required_docs = customer.get("requiredDocs", [])
        await self._emit_send(job, "checking_attachments", {
            "invoiceNumber": invoice.invoice_number,
        })

        att_check = await api.check_attachments(invoice_id, required_docs)
        result.attachments_found = att_check.get("found", [])
        result.attachments_missing = att_check.get("missing", [])

        all_attachments = att_check.get("attachments", [])
        if not all_attachments:
            result.status = "skipped_no_attachments"
            result.error = "No attachments found on invoice"
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

        # Step 4: Build email fields
        container = verification.get("found_container", invoice.container_number)
        subject = invoice.subject or f"[NGL_INV] {invoice.invoice_number} - Container#{container}"
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

        # Step 5: Test mode approval
        if job.test_mode:
            approved = await self._wait_for_approval(job, invoice, result, index,
                                                      to_emails, cc_emails, bcc_emails, subject)
            if not approved:
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

        # Download all linked attachments (POD, BOL, etc.) via direct API endpoint
        import httpx
        token = await api._token_manager.get_access_token()
        realm = api._token_manager.realm_id or api._realm_id
        base_url = api._base_url

        for att in all_attachments:
            att_id = att.get("id")
            if not att_id:
                continue
            try:
                url = f"{base_url}/v3/company/{realm}/download/{att_id}"
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        url,
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=30,
                    )
                if resp.status_code == 200:
                    email_attachments.append({
                        "filename": att.get("fileName", "attachment.pdf"),
                        "data": resp.content,
                    })
                else:
                    logger.warning("Failed to download attachment %s: %d",
                                   att.get("fileName"), resp.status_code)
            except Exception as e:
                logger.warning("Error downloading attachment %s: %s",
                               att.get("fileName"), e)

        if not email_attachments:
            result.status = "error"
            result.error = "Failed to download invoice PDF and attachments"
            await self._emit_send(job, "invoice_error", {
                "invoiceNumber": invoice.invoice_number,
                "error": result.error,
            })
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
            return

        # Extract customer name and ref# from invoice data
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
                ngl_ref = parts[0].strip()
                customer_ref = parts[1].strip() if len(parts) > 1 else ""

        due_date = invoice_data.get("DueDate", "")
        amount = str(invoice_data.get("TotalAmt", ""))

        body = build_invoice_email_html(
            invoice_number=invoice.invoice_number,
            container=container,
            customer_name=customer_name,
            amount=amount,
            due_date=due_date,
            ngl_ref=ngl_ref,
            customer_ref=customer_ref,
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
