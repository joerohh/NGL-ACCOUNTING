"""QBO API attachment operations — list, classify, download, upload."""

import base64
import io
import json
import logging
import re
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger("ngl.qbo_api.attachments")

# Filename patterns for document classification
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
        """
        attachments = await self.list_attachments(invoice_id)

        # Classify all attachments
        found_types = set()
        for att in attachments:
            found_types.add(att["docType"])

        # Return ALL detected types (consistent with browser path)
        found = sorted(found_types)
        # Missing = required docs not present in found types
        missing = []
        for req in required_docs:
            parts = [p.strip().lower() for p in req.split('/') if p.strip()]
            if not any(p in found_types for p in parts):
                missing.append(req)

        return {
            "found": found,
            "missing": missing,
            "allPresent": len(missing) == 0,
            "attachments": attachments,
        }

    async def download_attachment(self, attachable_id: str,
                                   filename: str,
                                   download_dir: Path,
                                   temp_download_uri: str = None) -> Optional[Path]:
        """Download an attachment file. Returns the saved file path or None.

        The QBO /download/{id} endpoint returns a redirect URL as plain text,
        not the actual file bytes. We prefer tempDownloadUri when available.
        """
        try:
            download_url = temp_download_uri
            if not download_url:
                # /download/{id} returns a redirect URL as text — need to follow it
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
                download_url = resp.content.decode("utf-8").strip()

            # Fetch the actual file bytes from the redirect URL
            async with httpx.AsyncClient(follow_redirects=True) as client:
                file_resp = await client.get(download_url, timeout=30)
            if file_resp.status_code != 200:
                logger.error("Attachment file fetch failed: %d", file_resp.status_code)
                return None

            download_dir.mkdir(parents=True, exist_ok=True)
            file_path = download_dir / filename
            file_path.write_bytes(file_resp.content)
            logger.info("Downloaded attachment: %s (%d bytes)", filename, len(file_resp.content))
            return file_path
        except Exception as e:
            logger.error("Error downloading attachment %s: %s", filename, e)
            return None

    async def upload_attachment(self, invoice_id: str, file_path: Path,
                                 include_on_send: bool = False) -> Optional[dict]:
        """Upload a file and attach it to an invoice.

        Uses QBO's multipart upload endpoint with base64-encoded file content.
        Returns the created Attachable dict or None on failure.
        """
        try:
            token = await self._token_manager.get_access_token()
            if not token:
                logger.error("No valid access token for attachment upload")
                return None

            realm = self._token_manager.realm_id or self._realm_id
            url = f"{self._base_url}/v3/company/{realm}/upload"

            file_bytes = file_path.read_bytes()
            filename = file_path.name

            # Detect content type from extension
            ext = file_path.suffix.lower()
            content_type = {
                ".pdf": "application/pdf",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
            }.get(ext, "application/octet-stream")

            metadata = json.dumps({
                "AttachableRef": [{
                    "EntityRef": {
                        "type": "Invoice",
                        "value": str(invoice_id),
                    },
                    "IncludeOnSend": include_on_send,
                }],
                "FileName": filename,
                "ContentType": content_type,
            })

            # QBO requires field names file_metadata_01 / file_content_01
            # and base64-encoded file data with Content-Transfer-Encoding header
            boundary = "-------------NGL_Upload_Boundary"
            body = io.BytesIO()

            # Part 1: JSON metadata
            body.write(f"--{boundary}\r\n".encode())
            body.write(b'Content-Disposition: form-data; name="file_metadata_01"\r\n')
            body.write(b"Content-Type: application/json\r\n\r\n")
            body.write(metadata.encode())
            body.write(b"\r\n")

            # Part 2: file content (base64 encoded)
            body.write(f"--{boundary}\r\n".encode())
            body.write(f'Content-Disposition: form-data; name="file_content_01"\r\n'.encode())
            body.write(f"Content-Type: {content_type}\r\n".encode())
            body.write(b"Content-Transfer-Encoding: base64\r\n\r\n")
            body.write(base64.b64encode(file_bytes))
            body.write(b"\r\n")

            body.write(f"--{boundary}--\r\n".encode())

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                        "Content-Type": f"multipart/form-data; boundary={boundary}",
                    },
                    content=body.getvalue(),
                    timeout=30,
                )

            if resp.status_code == 401:
                logger.warning("QBO upload 401 — refreshing token and retrying")
                await self._token_manager._refresh_access_token()
                token = await self._token_manager.get_access_token()
                if not token:
                    return None
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        url,
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Accept": "application/json",
                            "Content-Type": f"multipart/form-data; boundary={boundary}",
                        },
                        content=body.getvalue(),
                        timeout=30,
                    )

            if resp.status_code not in (200, 201):
                logger.error("QBO upload failed: %d — %s", resp.status_code, resp.text[:500])
                return None

            data = resp.json()
            att_list = data.get("AttachableResponse", [])
            if not att_list:
                logger.error("QBO upload returned no AttachableResponse: %s", resp.text[:300])
                return None

            attachable = att_list[0].get("Attachable", {})
            logger.info("Uploaded attachment %s to invoice %s (ID: %s)",
                        filename, invoice_id, attachable.get("Id"))
            return attachable

        except Exception as e:
            logger.error("Error uploading attachment %s: %s", file_path.name, e)
            return None
