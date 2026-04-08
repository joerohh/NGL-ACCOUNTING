"""QBO API client — official REST API replacement for Playwright browser automation.

Import as: ``from services.qbo_api import QBOApiClient``
"""

import logging
from typing import Optional

import httpx

from config import (
    QBO_CLIENT_ID,
    QBO_CLIENT_SECRET,
    QBO_REDIRECT_URI,
    QBO_REALM_ID,
    QBO_API_BASE_URL,
    QBO_API_BASE_URL_SANDBOX,
    QBO_USE_SANDBOX,
    QBO_TOKENS_FILE,
)

from .oauth import QBOTokenManager
from .invoices import QBOInvoicesMixin
from .attachments import QBOAttachmentsMixin

logger = logging.getLogger("ngl.qbo_api")


class QBOApiClient(QBOInvoicesMixin, QBOAttachmentsMixin):
    """QBO REST API client — drop-in replacement for QBOBrowser.

    Exposes the same method names as QBOBrowser so the job manager
    can switch between browser and API mode transparently.
    """

    def __init__(self) -> None:
        self._token_manager = QBOTokenManager(
            client_id=QBO_CLIENT_ID,
            client_secret=QBO_CLIENT_SECRET,
            redirect_uri=QBO_REDIRECT_URI,
            tokens_file=QBO_TOKENS_FILE,
        )
        self._base_url = QBO_API_BASE_URL_SANDBOX if QBO_USE_SANDBOX else QBO_API_BASE_URL
        self._realm_id = QBO_REALM_ID

    # ------------------------------------------------------------------
    # Core HTTP helpers (used by mixin methods)
    # ------------------------------------------------------------------
    async def _api_query(self, query: str) -> Optional[dict]:
        """Execute a QBO query (SELECT statement). Returns parsed JSON or None."""
        token = await self._token_manager.get_access_token()
        if not token:
            logger.error("No valid access token — QBO API not connected")
            return None

        realm = self._token_manager.realm_id or self._realm_id
        url = f"{self._base_url}/v3/company/{realm}/query"

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                url,
                params={"query": query},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
                timeout=30,
            )

        if resp.status_code == 401:
            logger.warning("QBO API 401 — attempting token refresh and retry")
            await self._token_manager._refresh_access_token()
            token = await self._token_manager.get_access_token()
            if not token:
                return None
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    url,
                    params={"query": query},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                    },
                    timeout=30,
                )

        if resp.status_code != 200:
            logger.error("QBO query failed: %d — %s", resp.status_code, resp.text[:500])
            return None

        return resp.json()

    async def _api_post(self, endpoint: str, payload: dict = None,
                         params: dict = None) -> Optional[dict]:
        """POST to a QBO API endpoint. Returns parsed JSON or None."""
        token = await self._token_manager.get_access_token()
        if not token:
            logger.error("No valid access token — QBO API not connected")
            return None

        realm = self._token_manager.realm_id or self._realm_id
        url = f"{self._base_url}/v3/company/{realm}/{endpoint}"

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

        async with httpx.AsyncClient() as client:
            if payload:
                headers["Content-Type"] = "application/json"
                resp = await client.post(url, json=payload, headers=headers,
                                          params=params, timeout=30)
            else:
                resp = await client.post(url, headers=headers,
                                          params=params, timeout=30)

        if resp.status_code == 401:
            logger.warning("QBO API 401 on POST — attempting token refresh and retry")
            await self._token_manager._refresh_access_token()
            token = await self._token_manager.get_access_token()
            if not token:
                return None
            headers["Authorization"] = f"Bearer {token}"
            async with httpx.AsyncClient() as client:
                if payload:
                    resp = await client.post(url, json=payload, headers=headers,
                                              params=params, timeout=30)
                else:
                    resp = await client.post(url, headers=headers,
                                              params=params, timeout=30)

        if resp.status_code not in (200, 201):
            logger.error("QBO POST %s failed: %d — %s", endpoint, resp.status_code, resp.text[:500])
            return None

        return resp.json()

    # ------------------------------------------------------------------
    # Status / connection info
    # ------------------------------------------------------------------
    @property
    def is_connected(self) -> bool:
        return self._token_manager.is_connected

    @property
    def token_manager(self) -> QBOTokenManager:
        return self._token_manager

    def get_status(self) -> dict:
        """Get current API connection status."""
        tm = self._token_manager
        return {
            "mode": "api",
            "connected": tm.is_connected,
            "realm_id": tm.realm_id,
            "sandbox": QBO_USE_SANDBOX,
            "refresh_token_days_remaining": tm.refresh_token_days_remaining,
            "needs_reauth_warning": tm.needs_reauth_warning,
        }
