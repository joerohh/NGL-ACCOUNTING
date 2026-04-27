"""TMS REST API client — NGL in-house TMS (api.flow.ngltrans.net).

Replaces Playwright browser automation for POD fetch + D/O sender lookup.

Auth: OAuth 2.0 client_credentials → 1hr Bearer token (cached in-memory).
Endpoints used:
  - POST /oauth/token
  - GET  /api/v1/work-orders/{wo_no}
  - GET  {documents[].file_url}  (Bearer-authed file download)
"""

import logging
import time
from typing import Optional

import httpx

from config import TMS_API_BASE_URL, TMS_API_CLIENT_ID, TMS_API_CLIENT_SECRET

logger = logging.getLogger("ngl.tms_api")

REFRESH_BUFFER_S = 60  # refresh token if < 1min remaining


class TMSApiClient:
    def __init__(self, client_id: str = "", client_secret: str = "",
                 base_url: str = "") -> None:
        self._client_id = client_id or TMS_API_CLIENT_ID
        self._client_secret = client_secret or TMS_API_CLIENT_SECRET
        self._base_url = (base_url or TMS_API_BASE_URL).rstrip("/")
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0

    def is_configured(self) -> bool:
        return bool(self._client_id and self._client_secret)

    async def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - REFRESH_BUFFER_S:
            return self._token
        url = f"{self._base_url}/oauth/token"
        payload = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "grant_type": "client_credentials",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(url, json=payload)
            r.raise_for_status()
            data = r.json()
        self._token = data["access_token"]
        self._token_expires_at = time.time() + int(data.get("expires_in", 3600))
        logger.info("[TMS_API] obtained token (expires in %ss)",
                    data.get("expires_in"))
        return self._token

    async def get_work_order(self, wo_no: str) -> Optional[dict]:
        if not wo_no:
            return None
        if not self.is_configured():
            logger.warning("[TMS_API] not configured — missing TMS_CLIENT_ID/SECRET")
            return None
        try:
            token = await self._get_token()
        except Exception as e:
            logger.warning("[TMS_API] token fetch failed: %s", e)
            return None
        url = f"{self._base_url}/api/v1/work-orders/{wo_no}"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.get(url, headers={"Authorization": f"Bearer {token}"})
                if r.status_code == 404:
                    logger.info("[TMS_API] WO %s not found (404)", wo_no)
                    return None
                if r.status_code >= 500:
                    logger.warning("[TMS_API] WO %s server error %s", wo_no, r.status_code)
                    return None
                r.raise_for_status()
                return r.json()
        except Exception as e:
            logger.warning("[TMS_API] get_work_order(%s) failed: %s", wo_no, e)
            return None

    async def download_document(self, file_url: str) -> Optional[bytes]:
        """Download a TMS document. file_url points to public CloudFront/S3
        (no auth header needed; sending Bearer can confuse the CDN)."""
        if not file_url:
            return None
        try:
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                r = await client.get(file_url)
                if r.status_code != 200:
                    logger.warning("[TMS_API] download HTTP %s for %s",
                                   r.status_code, file_url)
                    return None
                return r.content
        except Exception as e:
            logger.warning("[TMS_API] download_document(%s) failed: %s", file_url, e)
            return None

    @staticmethod
    def extract_pod_url(wo: dict) -> Optional[str]:
        if not isinstance(wo, dict):
            return None
        for doc in wo.get("documents") or []:
            if not isinstance(doc, dict):
                continue
            if doc.get("type_") == "POD" and doc.get("file_url"):
                return doc["file_url"]
        return None

    @staticmethod
    def extract_document_url(wo: dict, doc_type: str) -> Optional[str]:
        if not isinstance(wo, dict):
            return None
        for doc in wo.get("documents") or []:
            if not isinstance(doc, dict):
                continue
            if doc.get("type_") == doc_type and doc.get("file_url"):
                return doc["file_url"]
        return None

    @staticmethod
    def extract_do_sender(wo: dict) -> Optional[str]:
        if not isinstance(wo, dict):
            return None
        senders = wo.get("do_sender") or []
        if isinstance(senders, list) and senders:
            first = senders[0]
            if isinstance(first, str) and "@" in first:
                return first.strip()
        return None
