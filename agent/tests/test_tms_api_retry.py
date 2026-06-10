"""Verify get_work_order retries on transient network errors (3 attempts, 1s/3s backoff)."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.mark.asyncio
async def test_get_work_order_retries_three_times_on_connect_error():
    from services.tms_api import TMSApiClient

    client = TMSApiClient(client_id="x", client_secret="y", base_url="https://tms.test")
    client._token = "fake"
    client._token_expires_at = 9_999_999_999

    call_count = {"n": 0}

    async def mock_get(*args, **kwargs):
        call_count["n"] += 1
        raise httpx.ConnectError("simulated DNS blip")

    with patch.object(httpx.AsyncClient, "get", new=mock_get):
        with patch("asyncio.sleep", new=AsyncMock()):
            result = await client.get_work_order("LM2605280007")

    assert result is None
    assert call_count["n"] == 3, f"expected 3 attempts, got {call_count['n']}"


@pytest.mark.asyncio
async def test_get_work_order_returns_after_first_success():
    from services.tms_api import TMSApiClient

    client = TMSApiClient(client_id="x", client_secret="y", base_url="https://tms.test")
    client._token = "fake"
    client._token_expires_at = 9_999_999_999

    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = {"wo_no": "LM2605280007", "documents": []}
    mock_response.raise_for_status = MagicMock()

    call_count = {"n": 0}

    async def mock_get(*args, **kwargs):
        call_count["n"] += 1
        return mock_response

    with patch.object(httpx.AsyncClient, "get", new=mock_get):
        result = await client.get_work_order("LM2605280007")

    assert result == {"wo_no": "LM2605280007", "documents": []}
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_get_work_order_no_retry_on_404():
    from services.tms_api import TMSApiClient

    client = TMSApiClient(client_id="x", client_secret="y", base_url="https://tms.test")
    client._token = "fake"
    client._token_expires_at = 9_999_999_999

    mock_response = MagicMock(status_code=404)
    mock_response.raise_for_status = MagicMock()

    call_count = {"n": 0}

    async def mock_get(*args, **kwargs):
        call_count["n"] += 1
        return mock_response

    with patch.object(httpx.AsyncClient, "get", new=mock_get):
        result = await client.get_work_order("MISSING-WO")

    assert result is None
    assert call_count["n"] == 1, "404 must not retry"
