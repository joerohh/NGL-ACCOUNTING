"""OEC send mixin — invoice-only via QBO, then POD via separate Gmail email."""

import asyncio
import logging
import re
import shutil
import tempfile
from pathlib import Path

from config import QBO_ACTION_DELAY_S

logger = logging.getLogger("ngl.job_manager")


class SendOECFlowMixin:
    """OEC flow: Send invoice-only via QBO, then POD via separate Gmail email."""

    async def _send_oec_flow(self, job, invoice, customer: dict,
                              result, index: int) -> None:
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

        # Log amount discrepancy as warning (QBO is source of truth)
        if verification.get("amount_note"):
            await self._emit_send(job, "invoice_amount_warning", {
                "invoiceNumber": invoice.invoice_number,
                "note": verification["amount_note"],
            })

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

        # Download POD now (during first QBO visit) so we don't revisit later
        temp_dir = Path(tempfile.mkdtemp(prefix="ngl_pod_"))
        pod_path = None
        pod_source = None  # Track where POD came from: "QBO" or "TMS"
        if has_pod:
            await self._emit_send(job, "oec_downloading_pod", {
                "invoiceNumber": invoice.invoice_number,
            })
            pod_path = await self._qbo.find_and_download_pod(temp_dir)
            if pod_path:
                pod_source = "QBO"
                logger.info("POD downloaded from QBO during first visit: %s", pod_path.name)

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

        # ── Part B: Fetch DO SENDER from TMS (and POD if not on QBO) ──
        # POD was already downloaded from QBO during first visit (above) if it existed.
        # Now consult TMS for DO SENDER (always) and POD (if missing from QBO).
        csv_do_sender = invoice.do_sender_email or ""  # preserve CSV value before TMS may overwrite
        tms_failure_reason = ""  # track why TMS failed for UI
        tms_attempted = False

        if not self._tms:
            tms_failure_reason = "TMS browser not initialized"
            logger.warning("[OEC_TMS] TMS browser is None — skipping TMS lookup")
            await self._emit_send(job, "tms_not_available", {
                "invoiceNumber": invoice.invoice_number,
                "message": "TMS browser not initialized — D/O sender lookup skipped",
            })
        else:
            # If TMS is not logged in, pause and wait for user to log in
            if not self._tms.is_logged_in():
                await self._emit_send(job, "tms_login_required", {
                    "invoiceNumber": invoice.invoice_number,
                    "message": "TMS login required to fetch POD/DO SENDER — please log in now",
                })
                await self._tms.open_login_page()
                logged_in = await self._tms.wait_for_login(timeout_s=120)
                if logged_in:
                    await self._emit_send(job, "tms_logged_in", {
                        "message": "TMS login successful — continuing",
                    })
                else:
                    tms_failure_reason = "TMS login timed out (2 min)"
                    await self._emit_send(job, "tms_login_timeout", {
                        "invoiceNumber": invoice.invoice_number,
                        "message": "TMS login timed out (2 min) — skipping TMS lookup",
                    })

            # Now attempt TMS fetch if logged in
            if self._tms.is_logged_in():
                tms_attempted = True
                logger.info("[OEC_TMS] TMS logged in — fetching for %s (pod_path=%s, csv_do_sender='%s')",
                            invoice.container_number, "exists" if pod_path else "NONE",
                            invoice.do_sender_email or "")

                if not pod_path:
                    # Need both POD and DO SENDER — single trip
                    await self._emit_send(job, "tms_fetching_pod", {
                        "invoiceNumber": invoice.invoice_number,
                        "containerNumber": invoice.container_number,
                    })
                    tms_pod, tms_do_sender = await self._tms.fetch_pod_and_do_sender(
                        invoice.container_number, temp_dir
                    )
                    logger.info("[OEC_TMS] fetch_pod_and_do_sender returned: pod=%s, do_sender='%s'",
                                tms_pod.name if tms_pod else None,
                                tms_do_sender or "")
                    if tms_pod:
                        pod_path = tms_pod
                        pod_source = "TMS"
                        await self._emit_send(job, "tms_pod_downloaded", {
                            "invoiceNumber": invoice.invoice_number,
                            "fileName": pod_path.name,
                        })
                    else:
                        await self._emit_send(job, "tms_pod_not_found", {
                            "invoiceNumber": invoice.invoice_number,
                            "containerNumber": invoice.container_number,
                        })
                    if tms_do_sender and not invoice.do_sender_email:
                        invoice.do_sender_email = tms_do_sender
                        logger.info("[OEC_TMS] DO SENDER from TMS assigned: %s → invoice.do_sender_email",
                                    tms_do_sender)
                    elif tms_do_sender and invoice.do_sender_email:
                        logger.info("[OEC_TMS] DO SENDER from TMS '%s' ignored — CSV already has '%s'",
                                    tms_do_sender, invoice.do_sender_email)
                    elif not tms_do_sender:
                        tms_failure_reason = "TMS extraction returned no D/O sender (search may have failed or field was empty)"
                        logger.warning("[OEC_TMS] TMS returned no DO SENDER for %s",
                                       invoice.container_number)
                        await self._emit_send(job, "tms_do_sender_extraction_failed", {
                            "invoiceNumber": invoice.invoice_number,
                            "containerNumber": invoice.container_number,
                            "message": tms_failure_reason,
                        })
                else:
                    # POD already from QBO — just fetch DO SENDER
                    logger.info("[OEC_TMS] POD from QBO — fetching DO SENDER only for %s",
                                invoice.container_number)
                    await self._emit_send(job, "tms_fetching_do_sender", {
                        "invoiceNumber": invoice.invoice_number,
                        "containerNumber": invoice.container_number,
                    })
                    tms_do_sender = await self._tms.fetch_do_sender_email(
                        invoice.container_number
                    )
                    logger.info("[OEC_TMS] fetch_do_sender_email returned: '%s'",
                                tms_do_sender or "")
                    if tms_do_sender and not invoice.do_sender_email:
                        invoice.do_sender_email = tms_do_sender
                        logger.info("[OEC_TMS] DO SENDER from TMS assigned: %s → invoice.do_sender_email",
                                    tms_do_sender)
                    elif tms_do_sender and invoice.do_sender_email:
                        logger.info("[OEC_TMS] DO SENDER from TMS '%s' ignored — CSV already has '%s'",
                                    tms_do_sender, invoice.do_sender_email)
                    elif not tms_do_sender:
                        tms_failure_reason = "TMS extraction returned no D/O sender (container search or field extraction failed)"
                        logger.warning("[OEC_TMS] TMS returned no DO SENDER for %s",
                                       invoice.container_number)
                        await self._emit_send(job, "tms_do_sender_extraction_failed", {
                            "invoiceNumber": invoice.invoice_number,
                            "containerNumber": invoice.container_number,
                            "message": tms_failure_reason,
                        })

                logger.info("[OEC_TMS] After TMS: invoice.do_sender_email = '%s'",
                            invoice.do_sender_email or "")
            elif not tms_failure_reason:
                tms_failure_reason = "TMS not logged in"
                await self._emit_send(job, "tms_not_logged_in", {
                    "invoiceNumber": invoice.invoice_number,
                    "message": "TMS not logged in — D/O sender lookup skipped",
                })

        # ── Cache fallback: if TMS failed, check local cache ──
        if not invoice.do_sender_email and not csv_do_sender:
            cached = self._get_cached_do_sender(invoice.container_number)
            if cached:
                invoice.do_sender_email = cached
                logger.info("[OEC_TMS] D/O sender from CACHE: %s for %s",
                            cached, invoice.container_number)
                await self._emit_send(job, "do_sender_from_cache", {
                    "invoiceNumber": invoice.invoice_number,
                    "containerNumber": invoice.container_number,
                    "doSenderEmail": cached,
                    "message": f"D/O sender found in cache: {cached}",
                })

        # ── Cache successful TMS lookups for future fallback ──
        if invoice.do_sender_email and not csv_do_sender:
            # Only cache if the value came from TMS (not CSV) — and TMS was actually used
            if tms_attempted:
                strategy = getattr(self._tms, '_last_do_sender_strategy', '') if self._tms else ''
                self._save_do_sender_cache(
                    invoice.container_number,
                    invoice.do_sender_email,
                    source="TMS",
                    strategy=strategy,
                )

        # ── Determine D/O sender source and emit status event ──
        do_sender_source = ""
        if invoice.do_sender_email:
            if csv_do_sender:
                do_sender_source = "CSV"
            elif self._get_cached_do_sender(invoice.container_number) == invoice.do_sender_email and not tms_attempted:
                do_sender_source = "Cache"
            else:
                do_sender_source = "TMS"
            await self._emit_send(job, "oec_do_sender_resolved", {
                "invoiceNumber": invoice.invoice_number,
                "doSenderEmail": invoice.do_sender_email,
                "doSenderSource": do_sender_source,
            })
        else:
            await self._emit_send(job, "oec_do_sender_missing", {
                "invoiceNumber": invoice.invoice_number,
                "containerNumber": invoice.container_number,
                "message": f"D/O Sender email not found — {tms_failure_reason or 'missing from TMS and CSV'}",
                "failureReason": tms_failure_reason,
            })

        # Record D/O sender in result for audit log
        result.do_sender_email = invoice.do_sender_email or ""
        result.do_sender_source = do_sender_source

        # No POD found anywhere — QBO invoice was sent but POD email can't go out
        if not pod_path:
            source = "QBO or TMS" if self._tms else "QBO"
            result.status = "sent_no_pod"
            result.error = f"QBO invoice sent but no POD found ({source}) — send POD manually"
            await self._emit_send(job, "oec_pod_email_failed", {
                "invoiceNumber": invoice.invoice_number,
                "error": f"No POD found in {source} — send POD manually",
            })
            return

        # ── Build POD email recipients ──
        pod_to = list(customer.get("podEmailTo", []))
        pod_cc = list(customer.get("podEmailCc", []))

        logger.info("[POD_EMAIL] Building CC list for %s:", invoice.invoice_number)
        logger.info("[POD_EMAIL]   Customer podEmailCc: %s", customer.get("podEmailCc", []))
        logger.info("[POD_EMAIL]   DO SENDER email on invoice: '%s'", invoice.do_sender_email or "")

        # Validate DO SENDER email before adding to CC
        _email_re = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')

        if invoice.do_sender_email:
            do_email = invoice.do_sender_email.strip()
            if do_email and _email_re.match(do_email):
                pod_cc.append(do_email)
                logger.info("[POD_EMAIL] CC field: added DO SENDER '%s' — valid email", do_email)
            else:
                logger.warning("[POD_EMAIL] CC field: SKIPPED DO SENDER '%s' — "
                               "failed email validation (blank=%s, has_at=%s)",
                               do_email, not do_email, '@' in do_email if do_email else False)
        else:
            logger.info("[POD_EMAIL] CC field: no DO SENDER email available for this invoice")

        # Update result with actual POD email recipients (overrides QBO-step values)
        result.to_emails = pod_to
        result.cc_emails = pod_cc

        logger.info("[POD_EMAIL] Final recipients for %s:", invoice.invoice_number)
        logger.info("[POD_EMAIL]   TO: %s", pod_to)
        logger.info("[POD_EMAIL]   CC: %s", pod_cc)

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

        # ── Pre-send verification ──
        if not pod_to:
            result.status = "error"
            result.error = "No podEmailTo recipients configured — cannot send POD email"
            logger.error("[POD_EMAIL] ABORT: no TO recipients for %s", invoice.invoice_number)
            await self._emit_send(job, "oec_pod_email_failed", {
                "invoiceNumber": invoice.invoice_number,
                "error": result.error,
            })
            return

        if not pod_path or not pod_path.exists():
            result.status = "error"
            result.error = f"POD file missing or deleted: {pod_path}"
            logger.error("[POD_EMAIL] ABORT: POD file not on disk for %s", invoice.invoice_number)
            await self._emit_send(job, "oec_pod_email_failed", {
                "invoiceNumber": invoice.invoice_number,
                "error": result.error,
            })
            return

        logger.info("[POD_EMAIL] Pre-send verification PASSED:")
        logger.info("[POD_EMAIL]   TO: %s", pod_to)
        logger.info("[POD_EMAIL]   CC: %s", pod_cc)
        logger.info("[POD_EMAIL]   Subject: %s", pod_subject)
        logger.info("[POD_EMAIL]   POD file: %s (%d bytes)",
                    pod_path.name, pod_path.stat().st_size)
        logger.info("[POD_EMAIL]   POD source: %s", pod_source)

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
                "podSource": pod_source,
                "index": index,
                "total": job.total,
                "message": "OEC POD email ready — review recipients before sending",
                "flowType": "oec_pod_email",
                "doSenderEmail": invoice.do_sender_email or "",
                "doSenderSource": do_sender_source,
                "doSenderMissing": not bool(invoice.do_sender_email),
                "tmsFailureReason": tms_failure_reason if not invoice.do_sender_email else "",
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
            cc_override = job._cc_override
            job._approval_event = None
            job._approval_decision = None
            job._cc_override = None
            if not approved:
                result.status = "sent_no_pod"
                result.error = "QBO sent but POD email skipped by user"
                await self._emit_send(job, "invoice_skipped", {
                    "invoiceNumber": invoice.invoice_number,
                    "reason": "user_skipped_pod_email",
                })
                return

            # Apply CC override from user's editable field
            if cc_override is not None:
                logger.info("[POD_EMAIL] Applying CC override from user: %s (was: %s)",
                            cc_override, pod_cc)
                pod_cc = cc_override
                result.cc_emails = pod_cc

        # ── Send POD email ──
        logger.info("[POD_EMAIL] Sending POD email for %s...", invoice.invoice_number)
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
            logger.info("[POD_EMAIL] SUCCESS: POD email sent for %s", invoice.invoice_number)
            logger.info("[POD_EMAIL]   TO: %s", pod_to)
            logger.info("[POD_EMAIL]   CC: %s (DO SENDER included: %s)",
                        pod_cc, bool(invoice.do_sender_email))
            await self._emit_send(job, "oec_pod_email_sent", {
                "invoiceNumber": invoice.invoice_number,
                "to": pod_to,
                "cc": pod_cc,
                "doSenderEmail": invoice.do_sender_email or "",
                "doSenderIncluded": bool(invoice.do_sender_email),
            })
        else:
            result.status = "error"
            result.error = f"QBO sent but POD email failed: {email_result.get('error', 'Unknown')}"
            logger.error("[POD_EMAIL] FAILED: %s — %s", invoice.invoice_number, result.error)
            await self._emit_send(job, "oec_pod_email_failed", {
                "invoiceNumber": invoice.invoice_number,
                "error": email_result.get("error", "Unknown error"),
            })

        # Cleanup temp dir
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass
