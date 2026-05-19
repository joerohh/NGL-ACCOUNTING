"""Retry behavior for QBO download_attachment."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.qbo_api.attachments import QBOAttachmentsMixin


class _Client(QBOAttachmentsMixin):
    """Minimal host so we can call the mixin method in tests."""
    def __init__(self):
        self._base_url = "https://example.invalid"
        self._realm_id = "0"
        self._token_manager = MagicMock()
        self._token_manager.get_access_token = AsyncMock(return_value="fake-token")


@pytest.mark.asyncio
async def test_download_attachment_retries_on_transient_error(tmp_path: Path):
    """First attempt fails with ConnectError; second succeeds."""
    client = _Client()
    calls = {"n": 0}

    real_get_resp = MagicMock()
    real_get_resp.status_code = 200
    real_get_resp.content = b"%PDF-1.4 fake-bytes"

    class _FakeHttpxClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("getaddrinfo failed")
            return real_get_resp

    with patch("httpx.AsyncClient", _FakeHttpxClient):
        result = await client.download_attachment(
            attachable_id="123",
            filename="x.pdf",
            download_dir=tmp_path,
            temp_download_uri="https://example.invalid/file",
        )

    assert result is not None
    assert result.read_bytes() == b"%PDF-1.4 fake-bytes"
    assert calls["n"] == 2  # one fail + one success


@pytest.mark.asyncio
async def test_download_attachment_gives_up_after_three_attempts(tmp_path: Path):
    """All three attempts fail with ConnectError → returns None, no exception."""
    client = _Client()
    calls = {"n": 0}

    class _FakeHttpxClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url, **kwargs):
            calls["n"] += 1
            raise httpx.ConnectError("getaddrinfo failed")

    with patch("httpx.AsyncClient", _FakeHttpxClient):
        result = await client.download_attachment(
            attachable_id="123",
            filename="x.pdf",
            download_dir=tmp_path,
            temp_download_uri="https://example.invalid/file",
        )

    assert result is None
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_download_attachment_does_not_retry_on_404(tmp_path: Path):
    """Non-200 HTTP response (e.g. 404) is permanent — return None immediately."""
    client = _Client()
    calls = {"n": 0}

    resp_404 = MagicMock()
    resp_404.status_code = 404
    resp_404.text = "not found"

    class _FakeHttpxClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url, **kwargs):
            calls["n"] += 1
            return resp_404

    with patch("httpx.AsyncClient", _FakeHttpxClient):
        result = await client.download_attachment(
            attachable_id="123",
            filename="x.pdf",
            download_dir=tmp_path,
            temp_download_uri="https://example.invalid/file",
        )

    assert result is None
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_download_attachment_returns_none_on_non_transient_error(tmp_path: Path):
    """A non-transient exception (e.g. ValueError from decode) should NOT retry —
    return None immediately, preserving the legacy contract."""
    client = _Client()
    calls = {"n": 0}

    class _FakeHttpxClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url, **kwargs):
            calls["n"] += 1
            raise ValueError("simulated non-transient error")

    with patch("httpx.AsyncClient", _FakeHttpxClient):
        result = await client.download_attachment(
            attachable_id="123",
            filename="x.pdf",
            download_dir=tmp_path,
            temp_download_uri="https://example.invalid/file",
        )

    assert result is None
    assert calls["n"] == 1  # no retry on non-transient
