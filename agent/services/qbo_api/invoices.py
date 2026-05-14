"""QBO API invoice operations — query, verify, send."""

import asyncio
import logging
import re
from typing import Optional

import httpx

logger = logging.getLogger("ngl.qbo_api.invoices")

# Errors that may resolve on their own within a few seconds (DNS blip,
# transient TLS error, brief connection refusal). For these we retry with
# a short backoff before giving up.
_TRANSIENT_NETWORK_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
)
_GET_INVOICE_LINK_RETRY_ATTEMPTS = 3
_GET_INVOICE_LINK_RETRY_BACKOFF_SECONDS = (1.0, 3.0)  # delays before attempts 2 and 3

# Container number pattern: 4 letters + 7 digits (e.g., ECMU7540543)
CONTAINER_PATTERN = re.compile(r"[A-Z]{4}\d{7}")


class QBOInvoicesMixin:
    """Invoice operations via the QBO REST API."""

    async def search_invoice(self, invoice_number: str) -> Optional[dict]:
        """Query QBO for an invoice by DocNumber. Returns the invoice dict or None."""
        query = f"SELECT * FROM Invoice WHERE DocNumber = '{invoice_number}'"
        data = await self._api_query(query)
        if not data:
            return None

        invoices = data.get("QueryResponse", {}).get("Invoice", [])
        if not invoices:
            logger.info("Invoice %s not found in QBO", invoice_number)
            return None

        invoice = invoices[0]
        logger.info("Found invoice %s (Id=%s, Total=$%s)",
                     invoice_number, invoice["Id"], invoice.get("TotalAmt"))
        return invoice

    async def get_invoice_link(self, invoice_id: str) -> str:
        """Get the customer-facing payment/review link for an invoice.

        Uses ?include=invoiceLink to fetch the QBO portal URL.
        Returns the link URL, or empty string if QBO doesn't have one OR
        if all retry attempts hit a transient network failure.

        Retries: this call sits in the hot path of every send (normal +
        Try Again). If it fails the resulting email is missing the
        "Review and pay invoice" button, which users notice immediately.
        We try up to 3 times with backoff to ride out transient DNS
        blips that previously killed the button on Joseph's machine.
        """
        token = await self._token_manager.get_access_token()
        if not token:
            return ""

        realm = self._token_manager.realm_id or self._realm_id
        url = f"{self._base_url}/v3/company/{realm}/invoice/{invoice_id}?include=invoiceLink"

        last_error: Optional[Exception] = None
        for attempt in range(_GET_INVOICE_LINK_RETRY_ATTEMPTS):
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    resp = await client.get(
                        url,
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Accept": "application/json",
                        },
                        timeout=15,
                    )
                if resp.status_code == 200:
                    link = resp.json().get("Invoice", {}).get("InvoiceLink", "")
                    if link:
                        logger.info(
                            "Got invoice link for %s%s",
                            invoice_id,
                            "" if attempt == 0 else f" (after {attempt + 1} attempts)",
                        )
                    return link or ""
                # Non-200 — don't retry (probably a permanent issue like
                # 401/403/404). Drop out of the loop and return "".
                logger.warning(
                    "get_invoice_link non-200 for %s: HTTP %s",
                    invoice_id, resp.status_code,
                )
                return ""
            except _TRANSIENT_NETWORK_ERRORS as e:
                last_error = e
                if attempt < _GET_INVOICE_LINK_RETRY_ATTEMPTS - 1:
                    backoff = _GET_INVOICE_LINK_RETRY_BACKOFF_SECONDS[attempt]
                    logger.warning(
                        "get_invoice_link attempt %d/%d failed for %s (%s) — retrying in %.1fs",
                        attempt + 1, _GET_INVOICE_LINK_RETRY_ATTEMPTS,
                        invoice_id, type(e).__name__, backoff,
                    )
                    await asyncio.sleep(backoff)
                    continue
                # Out of retries on a transient error
            except Exception as e:
                # Non-transient (e.g. JSON decode error) — don't retry
                logger.warning("get_invoice_link failed for %s: %s", invoice_id, e)
                return ""

        # All retries exhausted on transient errors
        logger.error(
            "get_invoice_link FAILED for %s after %d attempts — the sent email will be missing the 'Review and pay invoice' button. Last error: %s",
            invoice_id, _GET_INVOICE_LINK_RETRY_ATTEMPTS, last_error,
        )
        return ""

    async def verify_invoice_details(self, invoice_data: dict,
                                      expected_container: str,
                                      expected_amount: Optional[str] = None) -> dict:
        """Verify container number and amount from an invoice response.

        Returns: {verified, reason, found_container, found_amount, amount_note}
        """
        result = {
            "verified": False,
            "reason": "",
            "found_container": "",
            "found_amount": str(invoice_data.get("TotalAmt", "")),
            "amount_note": "",
        }

        # Extract container number from various locations in the invoice
        container = self._extract_container(invoice_data)
        result["found_container"] = container or ""

        if not container:
            result["reason"] = "Container number not found in invoice"
            return result

        if expected_container and container.upper() != expected_container.upper():
            result["reason"] = (
                f"Container mismatch: expected {expected_container}, "
                f"found {container}"
            )
            return result

        # Amount check (warning only — QBO is source of truth)
        if expected_amount:
            try:
                qbo_amt = float(invoice_data.get("TotalAmt", 0))
                csv_amt = float(expected_amount.replace(",", "").replace("$", ""))
                if abs(qbo_amt - csv_amt) > 0.01:
                    result["amount_note"] = (
                        f"Amount differs: QBO=${qbo_amt:.2f}, CSV=${csv_amt:.2f} "
                        f"(QBO is source of truth)"
                    )
            except (ValueError, TypeError):
                pass

        result["verified"] = True
        return result

    def _extract_container(self, invoice: dict) -> Optional[str]:
        """Extract container number from invoice data.

        Checks: CustomField, Line descriptions, PrivateNote, CustomerMemo.
        """
        # 1. Custom fields
        for field in invoice.get("CustomField", []):
            val = field.get("StringValue", "")
            match = CONTAINER_PATTERN.search(val.upper())
            if match:
                return match.group()

        # 2. Line item descriptions
        for line in invoice.get("Line", []):
            desc = line.get("Description", "")
            match = CONTAINER_PATTERN.search(desc.upper())
            if match:
                return match.group()

        # 3. Private note
        note = invoice.get("PrivateNote", "")
        match = CONTAINER_PATTERN.search(note.upper())
        if match:
            return match.group()

        # 4. Customer memo
        memo = invoice.get("CustomerMemo", {})
        memo_val = memo.get("value", "") if isinstance(memo, dict) else str(memo)
        match = CONTAINER_PATTERN.search(memo_val.upper())
        if match:
            return match.group()

        return None

    def _extract_chassis(self, invoice: dict) -> Optional[str]:
        """Extract chassis number from invoice data.

        The QBO 'CNTR# / CHASSIS#' custom field stores values like
        'TGBU6571759/NGLT201438'.  This returns the part after the slash.
        Falls back to Line descriptions, PrivateNote, CustomerMemo.
        """
        def _parse_chassis(text: str) -> Optional[str]:
            """Return chassis portion if text contains 'CONTAINER/CHASSIS'."""
            if "/" not in text:
                return None
            parts = text.split("/")
            # The chassis is after the slash; container is before
            chassis = parts[-1].strip()
            if chassis and CONTAINER_PATTERN.search(parts[0].upper()):
                return chassis
            return None

        # 1. Custom fields (primary — "CNTR# / CHASSIS#" field)
        for field in invoice.get("CustomField", []):
            val = field.get("StringValue", "")
            chassis = _parse_chassis(val)
            if chassis:
                return chassis

        # 2. Line item descriptions
        for line in invoice.get("Line", []):
            desc = line.get("Description", "")
            chassis = _parse_chassis(desc)
            if chassis:
                return chassis

        # 3. Private note
        chassis = _parse_chassis(invoice.get("PrivateNote", ""))
        if chassis:
            return chassis

        # 4. Customer memo
        memo = invoice.get("CustomerMemo", {})
        memo_val = memo.get("value", "") if isinstance(memo, dict) else str(memo)
        chassis = _parse_chassis(memo_val)
        if chassis:
            return chassis

        return None

    def _extract_cnee(self, invoice: dict) -> Optional[str]:
        """Extract CNEE (consignee) code from CustomerMemo 'Note to customer'.

        The memo contains an arrow-chain routing like:
            'Pickup and Delivery Return\\nWBCTWE02 --> NGLRIV01 --> WBCTWE02'
        The second code in the arrow chain is the CNEE.
        """
        memo = invoice.get("CustomerMemo", {})
        memo_val = memo.get("value", "") if isinstance(memo, dict) else str(memo)
        if not memo_val:
            return None

        for line in memo_val.splitlines():
            if "-->" in line:
                codes = [c.strip() for c in line.split("-->")]
                if len(codes) >= 2 and codes[1]:
                    return codes[1]
        return None

    async def send_invoice_email(self, invoice_id: str, sync_token: str,
                                  to_emails: list[str],
                                  cc_emails: list[str] = None,
                                  bcc_emails: list[str] = None,
                                  subject: str = "") -> dict:
        """Send an invoice email via QBO API.

        Steps:
        1. Update the invoice with email recipients
        2. Call the send endpoint

        Returns: {sent: bool, error: str|None}
        """
        # Step 1: Update email fields on the invoice
        update_payload = {
            "Id": invoice_id,
            "SyncToken": sync_token,
            "sparse": True,
            "BillEmail": {"Address": ", ".join(to_emails)},
        }
        if cc_emails:
            update_payload["BillEmailCc"] = {"Address": ", ".join(cc_emails)}
        if bcc_emails:
            update_payload["BillEmailBcc"] = {"Address": ", ".join(bcc_emails)}

        update_result = await self._api_post("invoice", update_payload)
        if not update_result:
            return {"sent": False, "error": "Failed to update invoice email fields"}

        # Get the new SyncToken from the update response
        updated_invoice = update_result.get("Invoice", {})
        new_sync_token = updated_invoice.get("SyncToken", sync_token)

        # Step 2: Send the invoice
        send_to = to_emails[0] if to_emails else ""
        send_result = await self._api_post(
            f"invoice/{invoice_id}/send",
            params={"sendTo": send_to} if send_to else None,
        )
        if not send_result:
            return {"sent": False, "error": "Send API call failed"}

        logger.info("Invoice %s sent via API to %s", invoice_id, send_to)
        return {"sent": True, "error": None}

    async def download_invoice_pdf(self, invoice_id: str) -> Optional[bytes]:
        """Download the invoice PDF from QBO API. Returns raw PDF bytes."""
        token = await self._token_manager.get_access_token()
        if not token:
            logger.error("No valid access token for PDF download")
            return None

        url = f"{self._base_url}/v3/company/{self._realm_id}/invoice/{invoice_id}/pdf"
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/pdf",
                },
                timeout=30,
            )

        if resp.status_code != 200:
            logger.error("PDF download failed: %d — %s", resp.status_code, resp.text)
            return None

        return resp.content
