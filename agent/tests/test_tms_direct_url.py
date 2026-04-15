"""Unit tests for direct-URL TMS fetch methods.

These tests mock the Playwright page and TMS's helper methods to verify the
orchestration logic in fetch_do_sender_by_wo and fetch_doc_by_wo. No real
browser is spawned.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.tms_browser import TMSBrowser


@pytest.fixture
def tms():
    t = TMSBrowser()
    # Fake Playwright page — only the methods called by the tested functions
    t._page = MagicMock()
    t._page.url = "https://tms.ngltrans.net/main/imp"
    t._page.goto = AsyncMock()
    t._page.evaluate = AsyncMock()
    # Silence debug I/O
    t._debug = AsyncMock()
    t._debug_rich = AsyncMock()
    return t


# ── fetch_do_sender_by_wo ────────────────────────────────────────────


class TestFetchDoSenderByWo:
    @pytest.mark.asyncio
    async def test_happy_path_extracts_email(self, tms):
        # Page reports markers + container present + extraction returns email
        tms._has_detail_markers = AsyncMock(return_value="DETAIL INFO")
        tms._page.evaluate = AsyncMock(return_value=True)  # container check
        tms._extract_do_sender = AsyncMock(return_value="patricia.velasco@oecgroup.com")

        result = await tms.fetch_do_sender_by_wo(
            "LM2603300024", "import", "WHSU6868645", "LM26040290F",
        )

        assert result == "patricia.velasco@oecgroup.com"
        assert tms._last_do_sender_strategy == "direct_url"
        tms._page.goto.assert_awaited_once()
        call_args = tms._page.goto.call_args
        assert "/bc-detail/detail-info/import/LM2603300024" in call_args.args[0]

    @pytest.mark.asyncio
    async def test_verify_fails_returns_none(self, tms):
        tms._has_detail_markers = AsyncMock(return_value=None)
        tms._page.evaluate = AsyncMock(return_value=False)
        tms._extract_do_sender = AsyncMock(return_value="should-not-be-called@x.com")

        result = await tms.fetch_do_sender_by_wo(
            "LM2603300024", "import", "WHSU6868645", "LM26040290F",
        )

        assert result is None
        assert tms._last_do_sender_strategy == ""
        tms._extract_do_sender.assert_not_called()

    @pytest.mark.asyncio
    async def test_extract_returns_empty_means_none(self, tms):
        tms._has_detail_markers = AsyncMock(return_value="DETAIL INFO")
        tms._page.evaluate = AsyncMock(return_value=True)
        tms._extract_do_sender = AsyncMock(return_value=None)

        result = await tms.fetch_do_sender_by_wo(
            "LM2603300024", "import", "WHSU6868645", "LM26040290F",
        )

        assert result is None
        assert tms._last_do_sender_strategy == ""

    @pytest.mark.asyncio
    async def test_goto_exception_returns_none(self, tms):
        tms._page.goto = AsyncMock(side_effect=Exception("network error"))

        result = await tms.fetch_do_sender_by_wo(
            "LM2603300024", "import", "WHSU6868645", "LM26040290F",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_no_page_url_returns_none(self, tms):
        tms._page.url = ""
        result = await tms.fetch_do_sender_by_wo(
            "LM2603300024", "import", "WHSU6868645", "LM26040290F",
        )
        assert result is None
        tms._page.goto.assert_not_called()

    @pytest.mark.asyncio
    async def test_builds_export_url(self, tms):
        tms._has_detail_markers = AsyncMock(return_value="DETAIL INFO")
        tms._page.evaluate = AsyncMock(return_value=True)
        tms._extract_do_sender = AsyncMock(return_value="e@x.com")

        await tms.fetch_do_sender_by_wo(
            "LE2603300055", "export", "ABCD1234567", "LE26040291F",
        )

        call_args = tms._page.goto.call_args
        assert "/bc-detail/detail-info/export/LE2603300055" in call_args.args[0]


# ── fetch_doc_by_wo ──────────────────────────────────────────────────


class TestFetchDocByWo:
    @pytest.mark.asyncio
    async def test_happy_path_downloads_pod(self, tms, tmp_path):
        tms._page.evaluate = AsyncMock(return_value=True)  # doc tab loaded + container match
        tms._has_detail_markers = AsyncMock(return_value=None)  # doc tab — not required
        tms.list_documents = AsyncMock(return_value=[
            {"type": "DO", "has_file": True, "filename": "do.pdf"},
            {"type": "POD", "has_file": True, "filename": "pod.pdf"},
            {"type": "BL", "has_file": False, "filename": ""},
        ])
        expected_path = tmp_path / "pod.pdf"
        tms.download_document = AsyncMock(return_value=expected_path)

        result = await tms.fetch_doc_by_wo(
            "LM2603300024", "import", "pod", "WHSU6868645", "LM26040290F", tmp_path,
        )

        assert result == expected_path
        # URL should go to document tab
        call_args = tms._page.goto.call_args
        assert "/bc-detail/document/import/LM2603300024" in call_args.args[0]
        # download_document called with uppercase doc_type + filename
        tms.download_document.assert_awaited_once_with("POD", tmp_path, "pod.pdf")

    @pytest.mark.asyncio
    async def test_doc_type_case_insensitive(self, tms, tmp_path):
        tms._page.evaluate = AsyncMock(return_value=True)
        tms.list_documents = AsyncMock(return_value=[
            {"type": "POD", "has_file": True, "filename": "pod.pdf"},
        ])
        tms.download_document = AsyncMock(return_value=tmp_path / "pod.pdf")

        # Call with 'POD' uppercase — should still work
        result = await tms.fetch_doc_by_wo(
            "LM2603300024", "import", "POD", "WHSU6868645", "LM26040290F", tmp_path,
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_missing_doc_type_returns_none(self, tms, tmp_path):
        tms._page.evaluate = AsyncMock(return_value=True)
        tms.list_documents = AsyncMock(return_value=[
            {"type": "POD", "has_file": True, "filename": "pod.pdf"},
        ])
        tms.download_document = AsyncMock()

        result = await tms.fetch_doc_by_wo(
            "LM2603300024", "import", "bl", "WHSU6868645", "LM26040290F", tmp_path,
        )
        assert result is None
        tms.download_document.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_file_returns_none(self, tms, tmp_path):
        tms._page.evaluate = AsyncMock(return_value=True)
        tms.list_documents = AsyncMock(return_value=[
            {"type": "POD", "has_file": False, "filename": ""},
        ])
        tms.download_document = AsyncMock()

        result = await tms.fetch_doc_by_wo(
            "LM2603300024", "import", "pod", "WHSU6868645", "LM26040290F", tmp_path,
        )
        assert result is None
        tms.download_document.assert_not_called()

    @pytest.mark.asyncio
    async def test_tab_not_loaded_returns_none(self, tms, tmp_path):
        tms._page.evaluate = AsyncMock(return_value=False)  # file inputs never appear
        tms.list_documents = AsyncMock()
        tms.download_document = AsyncMock()

        result = await tms.fetch_doc_by_wo(
            "LM2603300024", "import", "pod", "WHSU6868645", "LM26040290F", tmp_path,
        )
        assert result is None
        tms.list_documents.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_pod_by_wo_delegates_to_fetch_doc_by_wo(self, tms, tmp_path):
        tms.fetch_doc_by_wo = AsyncMock(return_value=tmp_path / "pod.pdf")

        result = await tms.fetch_pod_by_wo(
            "LM2603300024", "import", "WHSU6868645", "LM26040290F", tmp_path,
        )
        assert result == tmp_path / "pod.pdf"
        tms.fetch_doc_by_wo.assert_awaited_once_with(
            "LM2603300024", "import", "pod", "WHSU6868645", "LM26040290F", tmp_path,
        )
