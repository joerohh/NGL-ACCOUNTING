"""QBO API attachment operations — list, classify, download."""

import logging
import re
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger("ngl.qbo_api.attachments")

# Filename patterns for document classification (same as qbo_browser/invoice.py)
DOC_PATTERNS = {
    "pod": [r"_pod", r"proof.of.delivery", r"pod\b"],
    "bol": [r"_bol", r"bill.of.lading", r"_bl\."],
    "invoice": [r"_it\.", r"_inv\.", r"invoice"],
    "packing_list": [r"_pl\.", r"packing.list"],
    "do": [r"_do\.", r"delivery.order"],
}


def classify_attachment(filename: str) -> str:
    """Classify a document by its filename. Returns type string or 'other'."""
    lower = filename.lower()
    for doc_type, patterns in DOC_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, lower):
                return doc_type
    return "other"


class QBOAttachmentsMixin:
    """Attachment operations via the QBO REST API."""

    async def list_attachments(self, invoice_id: str) -> list[dict]:
        """List all attachments linked to an invoice.

        Returns list of dicts: [{id, fileName, contentType, size, docType}, ...]
        """
        query = (
            f"SELECT * FROM Attachable WHERE "
            f"AttachableRef.EntityRef.value = '{invoice_id}'"
        )
        data = await self._api_query(query)
        if not data:
            return []

        attachables = data.get("QueryResponse", {}).get("Attachable", [])
        results = []
        for att in attachables:
            filename = att.get("FileName", "unknown")
            results.append({
                "id": att.get("Id"),
                "fileName": filename,
                "contentType": att.get("ContentType", ""),
                "size": att.get("Size", 0),
                "tempDownloadUri": att.get("TempDownloadUri"),
                "docType": classify_attachment(filename),
            })

        logger.info("Found %d attachments for invoice %s", len(results), invoice_id)
        return results

    async def check_attachments(self, invoice_id: str,
                                 required_docs: list[str]) -> dict:
        """Check if required documents are present on an invoice.

        Returns: {found, missing, allPresent, attachments}
        Same shape as qbo_browser's check_attachments_on_page() for compatibility.
        """
        attachments = await self.list_attachments(invoice_id)

        # Classify all attachments
        found_types = set()
        for att in attachments:
            found_types.add(att["docType"])

        # Check required docs
        found = [doc for doc in required_docs if doc.lower() in found_types]
        missing = [doc for doc in required_docs if doc.lower() not in found_types]

        return {
            "found": found,
            "missing": missing,
            "allPresent": len(missing) == 0,
            "attachments": attachments,
        }

    async def download_attachment(self, attachable_id: str,
                                   filename: str,
                                   download_dir: Path) -> Optional[Path]:
        """Download an attachment file. Returns the saved file path or None."""
        token = await self._token_manager.get_access_token()
        if not token:
            logger.error("No valid access token for attachment download")
            return None

        url = f"{self._base_url}/v3/company/{self._realm_id}/download/{attachable_id}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )

        if resp.status_code != 200:
            logger.error("Attachment download failed: %d — %s", resp.status_code, resp.text)
            return None

        download_dir.mkdir(parents=True, exist_ok=True)
        file_path = download_dir / filename
        file_path.write_bytes(resp.content)
        logger.info("Downloaded attachment: %s (%d bytes)", filename, len(resp.content))
        return file_path
