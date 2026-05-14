"""get_invoice_link retry behaviour — rides out transient DNS / connect
errors that were dropping the 'Review and pay invoice' button from sent
emails on Joseph's machine (errno 11001 / WSAHOST_NOT_FOUND).
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.qbo_api import QBOApiClient


def _make_client(token="t0", realm="r0", base="https://qbo.test"):
    """Tiny QBOApiClient stand-in just for get_invoice_link tests."""
    client = QBOApiClient.__new__(QBOApiClient)
    token_mgr = MagicMock()
    token_mgr.get_access_token = AsyncMock(return_value=token)
    token_mgr.realm_id = realm
    client._token_manager = token_mgr
    client._realm_id = realm
    client._base_url = base
    return client


def _ok_response(link="https://qbo.example/portal/abc"):
    resp = MagicMock()
    resp.status_code = 200
    resp.json = MagicMock(return_value={"Invoice": {"InvoiceLink": link}})
    return resp


def _make_async_cm(resp):
    """Return an object usable as `async with httpx.AsyncClient(...) as client:`
    where `client.get(...)` returns `resp` (or raises if resp is an exception)."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=cm)
    cm.__aexit__ = AsyncMock(return_value=None)
    if isinstance(resp, Exception):
        cm.get = AsyncMock(side_effect=resp)
    else:
        cm.get = AsyncMock(return_value=resp)
    return cm


def test_get_invoice_link_succeeds_first_try():
    """Happy path — one HTTP call, returns the link."""
    client = _make_client()
    with patch("services.qbo_api.invoices.httpx.AsyncClient",
               return_value=_make_async_cm(_ok_response("https://link.test/1"))):
        link = asyncio.run(client.get_invoice_link("inv-1"))
    assert link == "https://link.test/1"


def test_get_invoice_link_retries_then_succeeds_after_transient_dns_failure():
    """The bug we're fixing: errno 11001 (DNS) failed once, then worked.
    With retries, the second attempt succeeds and returns the link."""
    client = _make_client()
    # First call: ConnectError (mirrors httpx wrapping a getaddrinfo failure).
    # Second call: success.
    cms = [
        _make_async_cm(httpx.ConnectError("[Errno 11001] getaddrinfo failed")),
        _make_async_cm(_ok_response("https://link.test/recovered")),
    ]

    async def run():
        with patch("services.qbo_api.invoices.httpx.AsyncClient",
                   side_effect=lambda *a, **kw: cms.pop(0)):
            # Speed up the test by stubbing asyncio.sleep
            with patch("services.qbo_api.invoices.asyncio.sleep", new=AsyncMock()):
                return await client.get_invoice_link("inv-1")

    link = asyncio.run(run())
    assert link == "https://link.test/recovered", \
        "Retry should have recovered after the first attempt's DNS failure"


def test_get_invoice_link_returns_empty_after_all_retries_fail():
    """If every retry hits a transient error, returns ''. Email goes out
    without the button but the send still completes."""
    client = _make_client()
    err = httpx.ConnectError("[Errno 11001] getaddrinfo failed")
    cms = [_make_async_cm(err) for _ in range(3)]

    async def run():
        with patch("services.qbo_api.invoices.httpx.AsyncClient",
                   side_effect=lambda *a, **kw: cms.pop(0)):
            with patch("services.qbo_api.invoices.asyncio.sleep", new=AsyncMock()):
                return await client.get_invoice_link("inv-1")

    link = asyncio.run(run())
    assert link == ""


def test_get_invoice_link_non_200_response_returns_empty_without_retrying():
    """A non-200 (e.g. 401 expired token) is not transient — don't waste
    time retrying. Return '' immediately."""
    client = _make_client()
    bad_resp = MagicMock()
    bad_resp.status_code = 401
    bad_resp.json = MagicMock(return_value={})

    get_mock = AsyncMock(return_value=bad_resp)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=cm)
    cm.__aexit__ = AsyncMock(return_value=None)
    cm.get = get_mock

    with patch("services.qbo_api.invoices.httpx.AsyncClient", return_value=cm):
        with patch("services.qbo_api.invoices.asyncio.sleep", new=AsyncMock()) as sleep_mock:
            link = asyncio.run(client.get_invoice_link("inv-1"))

    assert link == ""
    # Called only once — no retry on non-transient errors
    assert get_mock.await_count == 1
    sleep_mock.assert_not_awaited()


def test_get_invoice_link_returns_empty_when_qbo_has_no_link():
    """QBO returned 200 but the Invoice has no InvoiceLink field (e.g.,
    invoice isn't enabled for online payment). Don't retry — that's a
    real answer from the server."""
    client = _make_client()
    resp = MagicMock()
    resp.status_code = 200
    resp.json = MagicMock(return_value={"Invoice": {}})

    with patch("services.qbo_api.invoices.httpx.AsyncClient",
               return_value=_make_async_cm(resp)):
        link = asyncio.run(client.get_invoice_link("inv-1"))
    assert link == ""


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
