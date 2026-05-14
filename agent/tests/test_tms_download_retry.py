"""TMSApiClient.download_document retry behaviour.

The bug: on 2026-05-14 Joseph's machine, the agent successfully fetched
the WO metadata, identified the POD URL on storage.ngltrans.net, then
the actual file download hit a transient DNS failure (errno 11001 /
getaddrinfo). The previous code logged a warning and returned None, so
the POD silently dropped off the email — even though the safety
cascade had already found it.

After the fix download_document retries up to 3 times with backoff so a
brief DNS blip doesn't drop the doc.
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.tms_api import TMSApiClient


def _make_client():
    """Minimal TMSApiClient — we only exercise download_document."""
    c = TMSApiClient.__new__(TMSApiClient)
    return c


def _ok_response(body: bytes = b"%PDF-1.4\nfake pod"):
    resp = MagicMock()
    resp.status_code = 200
    resp.content = body
    return resp


def _make_async_cm(resp_or_exc):
    """`async with httpx.AsyncClient(...) as client:` mock."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=cm)
    cm.__aexit__ = AsyncMock(return_value=None)
    if isinstance(resp_or_exc, Exception):
        cm.get = AsyncMock(side_effect=resp_or_exc)
    else:
        cm.get = AsyncMock(return_value=resp_or_exc)
    return cm


URL = "https://storage.ngltrans.net/order/SM2604230038/SM2604230038_POD_1.pdf"


def test_download_succeeds_first_try():
    """Happy path — single HTTP call returns the bytes."""
    client = _make_client()
    with patch("services.tms_api.httpx.AsyncClient",
               return_value=_make_async_cm(_ok_response(b"%PDF-1.4\nhi"))):
        body = asyncio.run(client.download_document(URL))
    assert body == b"%PDF-1.4\nhi"


def test_download_recovers_after_transient_dns_blip():
    """Regression: first attempt hits getaddrinfo failed (httpx ConnectError),
    second attempt succeeds. POD bytes make it back."""
    client = _make_client()
    cms = [
        _make_async_cm(httpx.ConnectError("[Errno 11001] getaddrinfo failed")),
        _make_async_cm(_ok_response(b"%PDF-1.4\nrecovered")),
    ]

    async def run():
        with patch("services.tms_api.httpx.AsyncClient",
                   side_effect=lambda *a, **kw: cms.pop(0)):
            with patch("services.tms_api.asyncio.sleep", new=AsyncMock()):
                return await client.download_document(URL)

    body = asyncio.run(run())
    assert body == b"%PDF-1.4\nrecovered"


def test_download_returns_none_after_all_retries_exhaust():
    """All 3 attempts fail transient → returns None, email proceeds
    without the POD but the send doesn't crash."""
    client = _make_client()
    err = httpx.ConnectError("[Errno 11001] getaddrinfo failed")
    cms = [_make_async_cm(err) for _ in range(3)]

    async def run():
        with patch("services.tms_api.httpx.AsyncClient",
                   side_effect=lambda *a, **kw: cms.pop(0)):
            with patch("services.tms_api.asyncio.sleep", new=AsyncMock()):
                return await client.download_document(URL)

    assert asyncio.run(run()) is None


def test_download_404_returns_none_without_retrying():
    """HTTP 404 (or any non-200) is not transient — drop out immediately
    instead of wasting two more attempts on a doc that doesn't exist."""
    client = _make_client()
    resp = MagicMock()
    resp.status_code = 404
    resp.content = b""
    cm = _make_async_cm(resp)

    with patch("services.tms_api.httpx.AsyncClient", return_value=cm):
        with patch("services.tms_api.asyncio.sleep", new=AsyncMock()) as sleep_mock:
            body = asyncio.run(client.download_document(URL))

    assert body is None
    assert cm.get.await_count == 1
    sleep_mock.assert_not_awaited()


def test_download_empty_url_returns_none():
    client = _make_client()
    assert asyncio.run(client.download_document("")) is None
    assert asyncio.run(client.download_document(None)) is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
