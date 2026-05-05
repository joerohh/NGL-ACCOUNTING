"""OEC POD email mixin — sends POD/D-O email BEFORE the QBO invoice email.

As of the OEC flow reorder, this runs FIRST. It sets ``result.pod_status``
(``sent``/``failed``/``skipped``) but does NOT set ``result.status`` —
that's owned by the invoice-email step that runs afterwards.
"""

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

from config import TMS_FETCH_TIMEOUT_S
from services.job_manager.util import (
    normalize_email_list,
    validate_and_append_email,
)
from services.qbo_api.dedup import dedupe_attachments

logger = logging.getLogger("ngl.job_manager")


class SendOECFlowMixin:
    """OEC POD email: fetch POD from QBO/TMS, send to POD recipients."""

    async def _send_oec_pod_email(self, job, invoice, customer: dict,
                                   result, index: int) -> None:
        """Send the POD/D-O email FIRST in the OEC flow.

        Runs BEFORE _send_qbo_api. Uses TMSDataLayer for both the POD download
        and the D/O sender lookup. Falls back to the local cache when the layer
        can't find a D/O sender (hard invariant #11). Sets result.pod_status
        only — does NOT set result.status (owned by the invoice email step).
        """
        api = self._qbo_api

        invoice_data = await api.search_invoice(invoice.invoice_number)
        if not invoice_data:
            logger.warning("[OEC_POD] Invoice %s not found in QBO — skipping POD email",
                           invoice.invoice_number)
            result.pod_status = "skipped"
            return

        invoice_id = invoice_data["Id"]
        verification = await api.verify_invoice_details(
            invoice_data, invoice.container_number, invoice.amount or None
        )
        container = (verification.get("found_container")
                     or invoice.container_number or "")

        # Look for POD already attached to QBO before going to TMS.
        att_check = await api.check_attachments(invoice_id, ["invoice", "pod"])
        # WORKAROUND(TMS-008): see docs/tms-workarounds.md — drop duplicate Attachable records, pick newest POD
        all_attachments = await self._dedup_and_emit(
            job, invoice.invoice_number, att_check.get("attachments", []),
        )
        temp_dir = Path(tempfile.mkdtemp(prefix="ngl_pod_"))
        pod_path = None
        pod_source = None

        # Pick the newest POD via highest int(id) — same tie-breaker as the dedup helper.
        pod_candidates = [a for a in all_attachments
                          if a.get("docType") == "pod" and a.get("id")]
        if pod_candidates:
            chosen = max(pod_candidates, key=lambda a: int(a["id"]))
            await self._emit_send(job, "oec_downloading_pod", {
                "invoiceNumber": invoice.invoice_number,
            })
            pod_path = await api.download_attachment(
                chosen["id"], chosen.get("fileName", "pod.pdf"), temp_dir
            )
            if pod_path:
                pod_source = "QBO"
                logger.info("POD downloaded from QBO API: %s", pod_path.name)

        # ── TMS Data Layer: D/O sender + POD (if not already from QBO) ──
        csv_do_sender = invoice.do_sender_email or ""
        layer_do_sender_source = ""
        tms_failure_reason = ""

        if self._tms_data:
            rows_before = len(self._tms_data.get_failed_rows(job.id))
            try:
                enriched = await asyncio.wait_for(
                    self._tms_data.enrich_invoice(
                        job.id, invoice_data, source="api", force=True,
                    ),
                    timeout=TMS_FETCH_TIMEOUT_S,
                )
                if enriched.do_sender_email and not invoice.do_sender_email:
                    invoice.do_sender_email = enriched.do_sender_email
                    layer_do_sender_source = "TMS API"
                    logger.info("[OEC_POD] D/O sender from TMS API: %s",
                                enriched.do_sender_email)

                if not pod_path:
                    # Each operation gets its own full TMS_FETCH_TIMEOUT_S budget;
                    # combined worst-case is 2 * TMS_FETCH_TIMEOUT_S.
                    tms_pod = await asyncio.wait_for(
                        self._tms_data.get_document(
                            job.id, invoice_data, "POD", temp_dir, source="api",
                        ),
                        timeout=TMS_FETCH_TIMEOUT_S,
                    )
                    if tms_pod and tms_pod.exists():
                        pod_path = tms_pod
                        pod_source = "TMS API"
                        await self._emit_send(job, "tms_pod_downloaded", {
                            "invoiceNumber": invoice.invoice_number,
                            "fileName": pod_path.name,
                            "strategy": "api",
                        })
            except asyncio.TimeoutError:
                tms_failure_reason = f"TMS lookup timed out after {TMS_FETCH_TIMEOUT_S}s"
                logger.warning("[OEC_POD] TMS Data Layer timed out for %s",
                               invoice.invoice_number)
                await self._emit_send(job, "tms_fetch_timeout", {
                    "invoiceNumber": invoice.invoice_number,
                    "message": tms_failure_reason,
                })

            # Surface only when the data layer actually recorded a new failure.
            if len(self._tms_data.get_failed_rows(job.id)) > rows_before:
                await self._emit_failed_rows_changed(job, "added")
        else:
            tms_failure_reason = "TMSDataLayer not configured"
            logger.warning("[OEC_POD] %s — skipping TMS lookup", tms_failure_reason)
            await self._emit_send(job, "tms_not_available", {
                "invoiceNumber": invoice.invoice_number,
                "message": tms_failure_reason,
            })

        # ── Local D/O sender cache fallback (hard invariant #11) ──
        if not invoice.do_sender_email and not csv_do_sender:
            cached = self._get_cached_do_sender(invoice.container_number)
            if cached:
                invoice.do_sender_email = cached
                layer_do_sender_source = "Cache"
                logger.info("[OEC_POD] D/O sender from CACHE: %s for %s",
                            cached, invoice.container_number)
                await self._emit_send(job, "do_sender_from_cache", {
                    "invoiceNumber": invoice.invoice_number,
                    "containerNumber": invoice.container_number,
                    "doSenderEmail": cached,
                    "message": f"D/O sender found in cache: {cached}",
                })

        # ── Save TMS-derived D/O senders to cache for future fallback ──
        if invoice.do_sender_email and layer_do_sender_source == "TMS API":
            self._save_do_sender_cache(
                invoice.container_number, invoice.do_sender_email,
                source="TMS API", strategy="data_layer",
            )

        # ── Determine D/O sender source label ──
        do_sender_source = ""
        if invoice.do_sender_email:
            if csv_do_sender:
                do_sender_source = "CSV"
            else:
                do_sender_source = layer_do_sender_source or "TMS"
            await self._emit_send(job, "oec_do_sender_resolved", {
                "invoiceNumber": invoice.invoice_number,
                "doSenderEmail": invoice.do_sender_email,
                "doSenderSource": do_sender_source,
            })
        else:
            await self._emit_send(job, "oec_do_sender_missing", {
                "invoiceNumber": invoice.invoice_number,
                "containerNumber": invoice.container_number,
                "message": f"D/O Sender email not found — {tms_failure_reason or 'missing from TMS, cache, and CSV'}",
                "failureReason": tms_failure_reason,
            })

        result.do_sender_email = invoice.do_sender_email or ""
        result.do_sender_source = do_sender_source

        # No POD found anywhere — POD email skipped, invoice email continues.
        if not pod_path:
            source = "QBO or TMS"
            result.pod_status = "skipped"
            result.error = f"No POD found ({source}) — D/O email skipped, invoice will still send"
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

        validate_and_append_email(pod_cc, invoice.do_sender_email, label="D/O SENDER")

        logger.info("[POD_EMAIL] Final recipients for %s:", invoice.invoice_number)
        logger.info("[POD_EMAIL]   TO: %s", pod_to)
        logger.info("[POD_EMAIL]   CC: %s", pod_cc)

        pod_subject = customer.get("podEmailSubject", "") or f"POD — {invoice.container_number}"
        pod_body = customer.get("podEmailBody", "") or \
            f"Please find attached the Proof of Delivery for container {invoice.container_number}."

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
            result.pod_status = "skipped"
            result.error = "No podEmailTo recipients configured — D/O email skipped"
            logger.error("[POD_EMAIL] SKIP: no TO recipients for %s", invoice.invoice_number)
            await self._emit_send(job, "oec_pod_email_failed", {
                "invoiceNumber": invoice.invoice_number,
                "error": result.error,
            })
            return

        if not pod_path or not pod_path.exists():
            result.pod_status = "skipped"
            result.error = f"POD file missing or deleted: {pod_path}"
            logger.error("[POD_EMAIL] SKIP: POD file not on disk for %s", invoice.invoice_number)
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
                result.pod_status = "skipped"
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
                result.pod_status = "skipped"
                result.error = "POD email skipped by user"
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

        # ── Send POD email — POD-only attachment (hard invariant #2) ──
        # HARD RULE: D/O email carries the POD PDF ONLY — no invoice PDF, no extras.
        # send_pod_email takes a single pod_path — enforced by signature.
        logger.info("[POD_EMAIL] Sending POD email for %s (POD-only attachment: %s)...",
                    invoice.invoice_number, pod_path.name)
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
            result.pod_status = "sent"
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
            result.pod_status = "failed"
            result.error = f"POD email failed: {email_result.get('error', 'Unknown')}"
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
