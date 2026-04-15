"""OEC POD email mixin — sends separate POD email after standard invoice send."""

import asyncio
import logging
import re
import shutil
import tempfile
from pathlib import Path

from config import TMS_FETCH_TIMEOUT_S
from services.job_manager.util import normalize_email_list

logger = logging.getLogger("ngl.job_manager")


class SendOECFlowMixin:
    """OEC POD email: fetch POD from QBO/TMS, send to POD recipients."""

    async def _send_oec_pod_email(self, job, invoice, customer: dict,
                                   result, index: int) -> None:
        """Send a separate POD email for OEC after the invoice was already sent.

        Called AFTER _send_qbo_api succeeds. The invoice email is already done —
        this only handles the POD delivery to separate recipients.
        """
        api = self._qbo_api

        # Look up invoice in QBO to get attachments
        invoice_data = await api.search_invoice(invoice.invoice_number)
        if not invoice_data:
            logger.warning("[OEC_POD] Invoice %s not found in QBO — skipping POD email",
                           invoice.invoice_number)
            return

        invoice_id = invoice_data["Id"]

        # Get container number from verification or CSV
        verification = await api.verify_invoice_details(
            invoice_data, invoice.container_number, invoice.amount or None
        )
        container = (verification.get("found_container")
                     or invoice.container_number or "")

        # Check QBO attachments for POD
        att_check = await api.check_attachments(invoice_id, ["invoice", "pod"])
        all_attachments = att_check.get("attachments", [])

        temp_dir = Path(tempfile.mkdtemp(prefix="ngl_pod_"))
        pod_path = None
        pod_source = None

        for att in all_attachments:
            if att.get("docType") == "pod" and att.get("id"):
                await self._emit_send(job, "oec_downloading_pod", {
                    "invoiceNumber": invoice.invoice_number,
                })
                pod_path = await api.download_attachment(
                    att["id"], att.get("fileName", "pod.pdf"), temp_dir
                )
                if pod_path:
                    pod_source = "QBO"
                    logger.info("POD downloaded from QBO API: %s", pod_path.name)
                break

        # ── TMS lookup for POD and D/O sender ──
        csv_do_sender = invoice.do_sender_email or ""
        tms_failure_reason = ""
        tms_attempted = False

        if not self._tms:
            tms_failure_reason = "TMS browser not initialized"
            logger.warning("[OEC_TMS] TMS browser is None — skipping TMS lookup")
            await self._emit_send(job, "tms_not_available", {
                "invoiceNumber": invoice.invoice_number,
                "message": "TMS browser not initialized — D/O sender lookup skipped",
            })
        else:
            try:
                pod_path_new, tms_failure_new, tms_attempted_new = await asyncio.wait_for(
                    self._oec_tms_lookup(job, invoice, pod_path, temp_dir),
                    timeout=TMS_FETCH_TIMEOUT_S,
                )
                if pod_path_new:
                    pod_path = pod_path_new
                    pod_source = pod_source or "TMS"
                if tms_failure_new:
                    tms_failure_reason = tms_failure_new
                tms_attempted = tms_attempted_new
            except asyncio.TimeoutError:
                tms_failure_reason = f"TMS lookup timed out after {TMS_FETCH_TIMEOUT_S}s"
                logger.warning("[OEC_TMS] TMS lookup timed out for %s",
                               invoice.invoice_number)
                await self._emit_send(job, "tms_fetch_timeout", {
                    "invoiceNumber": invoice.invoice_number,
                    "message": tms_failure_reason,
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

        # No POD found anywhere — invoice was sent but POD email can't go out
        if not pod_path:
            source = "QBO or TMS" if self._tms else "QBO"
            result.status = "sent_no_pod"
            result.error = f"Invoice sent but no POD found ({source}) — send POD manually"
            await self._emit_send(job, "oec_pod_email_failed", {
                "invoiceNumber": invoice.invoice_number,
                "error": f"No POD found in {source} — send POD manually",
            })
            return

        # ── Build POD email recipients ──
        pod_to = normalize_email_list(customer.get("podEmailTo", []))
        pod_cc = normalize_email_list(customer.get("podEmailCc", []))

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
                result.error = "Invoice sent but POD email skipped by user"
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
            result.error = f"Invoice sent but POD email failed: {email_result.get('error', 'Unknown')}"
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

    async def _oec_tms_lookup(self, job, invoice, pod_path, temp_dir):
        """TMS lookup for OEC flow. Runs inside a timeout wrapper.

        Returns (pod_path_or_None, failure_reason_or_empty, tms_attempted: bool).
        pod_path_or_None: a new POD Path if one was downloaded from TMS, else None.
        Mutates invoice.do_sender_email in place when TMS finds a D/O sender.
        """
        tms_failure_reason = ""
        tms_attempted = False
        new_pod_path = None

        # Login guard
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
                return None, tms_failure_reason, False

        if not self._tms.is_logged_in():
            return None, "TMS not logged in", False

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
                invoice.container_number, temp_dir,
                invoice_number=invoice.invoice_number,
            )
            logger.info("[OEC_TMS] fetch_pod_and_do_sender returned: pod=%s, do_sender='%s'",
                        tms_pod.name if tms_pod else None,
                        tms_do_sender or "")
            if tms_pod:
                new_pod_path = tms_pod
                await self._emit_send(job, "tms_pod_downloaded", {
                    "invoiceNumber": invoice.invoice_number,
                    "fileName": tms_pod.name,
                })
            else:
                await self._emit_send(job, "tms_pod_not_found", {
                    "invoiceNumber": invoice.invoice_number,
                    "containerNumber": invoice.container_number,
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
                invoice.container_number,
                invoice_number=invoice.invoice_number,
            )
            logger.info("[OEC_TMS] fetch_do_sender_email returned: '%s'",
                        tms_do_sender or "")

        # Common DO-sender assignment
        if tms_do_sender and not invoice.do_sender_email:
            invoice.do_sender_email = tms_do_sender
            logger.info("[OEC_TMS] DO SENDER from TMS assigned: %s → invoice.do_sender_email",
                        tms_do_sender)
        elif tms_do_sender and invoice.do_sender_email:
            logger.info("[OEC_TMS] DO SENDER from TMS '%s' ignored — CSV already has '%s'",
                        tms_do_sender, invoice.do_sender_email)
        elif not tms_do_sender:
            tms_failure_reason = "TMS extraction returned no D/O sender (search or field extraction failed)"
            logger.warning("[OEC_TMS] TMS returned no DO SENDER for %s",
                           invoice.container_number)
            await self._emit_send(job, "tms_do_sender_extraction_failed", {
                "invoiceNumber": invoice.invoice_number,
                "containerNumber": invoice.container_number,
                "message": tms_failure_reason,
            })

        logger.info("[OEC_TMS] After TMS: invoice.do_sender_email = '%s'",
                    invoice.do_sender_email or "")
        return new_pod_path, tms_failure_reason, tms_attempted
