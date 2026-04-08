"""QBO API invoice operations — query, verify, send."""

import logging
import re
from typing import Optional

import httpx

logger = logging.getLogger("ngl.qbo_api.invoices")

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
        async with httpx.AsyncClient() as client:
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
