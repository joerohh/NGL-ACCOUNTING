"""QBO OAuth 2.0 token management — authorize, refresh, persist."""

import json
import logging
import secrets
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import httpx

logger = logging.getLogger("ngl.qbo_api.oauth")

# Intuit OAuth endpoints
AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
REVOKE_URL = "https://developer.api.intuit.com/v2/oauth2/tokens/revoke"

# Refresh proactively when access token has < 5 minutes remaining
REFRESH_BUFFER_S = 300
# Warn when refresh token has < 7 days remaining
REFRESH_TOKEN_WARN_DAYS = 7


class QBOTokenManager:
    """Manages QBO OAuth tokens — persistence, refresh, and authorization flow."""

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str,
                 tokens_file: Path) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._tokens_file = tokens_file
        self._tokens: Optional[dict] = None
        self._state: Optional[str] = None  # CSRF protection for OAuth flow
        self._load_tokens()

    # ------------------------------------------------------------------
    # Token persistence (Supabase = source of truth, local file = offline cache)
    # ------------------------------------------------------------------
    def _load_tokens(self) -> None:
        """Load tokens: try Supabase first, fall back to local file cache."""
        try:
            from services.supabase_client import load_qbo_tokens
            remote = load_qbo_tokens()
            if remote:
                self._tokens = remote
                self._write_local_cache()
                logger.info("Loaded QBO tokens from Supabase (shared)")
                return
        except Exception as e:
            logger.warning("Supabase token load failed, using local cache: %s", e)

        if self._tokens_file.exists():
            try:
                with open(self._tokens_file, "r") as f:
                    self._tokens = json.load(f)
                logger.info("Loaded QBO tokens from local cache")
            except Exception as e:
                logger.warning("Failed to load QBO tokens from cache: %s", e)
                self._tokens = None

    def _refresh_from_remote(self) -> None:
        """Re-fetch latest tokens from Supabase (used before refresh to avoid stale refresh_token)."""
        try:
            from services.supabase_client import load_qbo_tokens
            remote = load_qbo_tokens()
            if remote:
                self._tokens = remote
                self._write_local_cache()
        except Exception as e:
            logger.warning("Could not re-fetch latest tokens from Supabase: %s", e)

    def _write_local_cache(self) -> None:
        """Write tokens to local file (cache for offline startup)."""
        if self._tokens:
            self._tokens_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._tokens_file, "w") as f:
                json.dump(self._tokens, f, indent=2)

    def _save_tokens(self) -> None:
        """Persist tokens to Supabase (source of truth) + local file cache."""
        if not self._tokens:
            return
        try:
            from services.supabase_client import upsert_qbo_tokens
            upsert_qbo_tokens(self._tokens)
        except Exception as e:
            logger.error("Failed to save QBO tokens to Supabase: %s", e)
        self._write_local_cache()
        logger.info("Saved QBO tokens (Supabase + local cache)")

    # ------------------------------------------------------------------
    # Authorization URL (step 1 of OAuth flow)
    # ------------------------------------------------------------------
    def get_authorization_url(self) -> str:
        """Build the Intuit authorization URL the user should be redirected to."""
        self._state = secrets.token_urlsafe(32)
        params = {
            "client_id": self._client_id,
            "response_type": "code",
            "scope": "com.intuit.quickbooks.accounting",
            "redirect_uri": self._redirect_uri,
            "state": self._state,
        }
        return f"{AUTH_URL}?{urlencode(params)}"

    # ------------------------------------------------------------------
    # Exchange auth code for tokens (step 2 of OAuth flow)
    # ------------------------------------------------------------------
    async def exchange_code(self, code: str, state: str, realm_id: str) -> bool:
        """Exchange the authorization code for access + refresh tokens."""
        if self._state and state != self._state:
            logger.error("OAuth state mismatch — possible CSRF attack")
            return False

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self._redirect_uri,
                },
                auth=(self._client_id, self._client_secret),
                headers={"Accept": "application/json"},
            )

        if resp.status_code != 200:
            logger.error("Token exchange failed: %d — %s", resp.status_code, resp.text)
            return False

        data = resp.json()
        now = time.time()
        self._tokens = {
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "access_token_expires_at": now + data.get("expires_in", 3600),
            "refresh_token_expires_at": now + data.get("x_refresh_token_expires_in", 8726400),
            "realm_id": realm_id,
            "created_at": now,
        }
        self._save_tokens()
        self._state = None
        logger.info("QBO OAuth authorization successful (realm: %s)", realm_id)
        return True

    # ------------------------------------------------------------------
    # Get a valid access token (auto-refreshes if needed)
    # ------------------------------------------------------------------
    async def get_access_token(self) -> Optional[str]:
        """Return a valid access token, refreshing if necessary."""
        if not self._tokens:
            return None

        now = time.time()
        expires_at = self._tokens.get("access_token_expires_at", 0)

        # Proactively refresh if within buffer
        if now >= expires_at - REFRESH_BUFFER_S:
            refreshed = await self._refresh_access_token()
            if not refreshed:
                return None

        return self._tokens.get("access_token")

    async def _refresh_access_token(self) -> bool:
        """Use the refresh token to get a new access token."""
        # Re-fetch latest from Supabase first — another install may have just refreshed.
        # QBO rotates refresh tokens, so an outdated refresh_token will fail.
        self._refresh_from_remote()

        refresh_token = self._tokens.get("refresh_token") if self._tokens else None
        if not refresh_token:
            logger.error("No refresh token available — re-authorization required")
            return False

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    TOKEN_URL,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                    },
                    auth=(self._client_id, self._client_secret),
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as e:
            logger.error("Token refresh request failed (network/timeout): %s", e)
            return False

        if resp.status_code != 200:
            logger.error("Token refresh failed: %d — %s", resp.status_code, resp.text)
            self._tokens = None
            self._save_tokens()
            return False

        data = resp.json()
        now = time.time()
        self._tokens["access_token"] = data["access_token"]
        self._tokens["refresh_token"] = data["refresh_token"]  # rolling refresh
        self._tokens["access_token_expires_at"] = now + data.get("expires_in", 3600)
        self._tokens["refresh_token_expires_at"] = now + data.get("x_refresh_token_expires_in", 8726400)
        self._save_tokens()
        logger.info("QBO access token refreshed successfully")
        return True

    # ------------------------------------------------------------------
    # Revoke tokens (disconnect)
    # ------------------------------------------------------------------
    async def revoke(self) -> bool:
        """Revoke the current tokens and clear stored credentials."""
        if not self._tokens:
            return True

        token = self._tokens.get("refresh_token") or self._tokens.get("access_token")
        if token:
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    await client.post(
                        REVOKE_URL,
                        json={"token": token},
                        auth=(self._client_id, self._client_secret),
                        headers={"Accept": "application/json"},
                    )
            except Exception as e:
                logger.warning("Token revocation request failed: %s", e)

        self._tokens = None
        if self._tokens_file.exists():
            self._tokens_file.unlink()
        try:
            from services.supabase_client import delete_qbo_tokens
            delete_qbo_tokens()
        except Exception as e:
            logger.warning("Failed to clear Supabase tokens: %s", e)
        logger.info("QBO API tokens revoked and cleared (Supabase + local)")
        return True

    # ------------------------------------------------------------------
    # Status checks
    # ------------------------------------------------------------------
    @property
    def is_connected(self) -> bool:
        """Whether we have tokens (may still need refresh)."""
        return self._tokens is not None and "access_token" in self._tokens

    @property
    def realm_id(self) -> Optional[str]:
        return self._tokens.get("realm_id") if self._tokens else None

    @property
    def refresh_token_days_remaining(self) -> Optional[int]:
        """Days until the refresh token expires (None if no tokens)."""
        if not self._tokens:
            return None
        expires_at = self._tokens.get("refresh_token_expires_at", 0)
        remaining = (expires_at - time.time()) / 86400
        return max(0, int(remaining))

    @property
    def needs_reauth_warning(self) -> bool:
        """True if refresh token is within 7 days of expiring."""
        days = self.refresh_token_days_remaining
        return days is not None and days <= REFRESH_TOKEN_WARN_DAYS
