"""QuickBooks Online browser automation via Playwright.

QBOBrowser combines all mixin classes into one unified interface.
Import as: ``from services.qbo_browser import QBOBrowser``
"""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Optional

from playwright.async_api import BrowserContext, Page

from config import (
    BROWSER_DOWNLOADS_DIR,
    DEBUG_DIR,
    SELECTORS_FILE,
)

from .login import QBOLoginMixin
from .search import QBOSearchMixin
from .download import QBODownloadMixin
from .invoice import QBOInvoiceMixin
from .send import QBOSendMixin

logger = logging.getLogger("ngl.qbo_browser")


def _load_selectors() -> dict:
    """Load QBO DOM selectors from the JSON config file."""
    if SELECTORS_FILE.exists():
        with open(SELECTORS_FILE, "r") as f:
            return json.load(f)
    return {}


class QBOBrowser(
    QBOLoginMixin,
    QBOSearchMixin,
    QBODownloadMixin,
    QBOInvoiceMixin,
    QBOSendMixin,
):
    """Controls a persistent Chrome browser to interact with QuickBooks Online."""

    def __init__(self) -> None:
        self._shared_browser = None  # SharedBrowser reference (for crash recovery)
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._selectors: dict = _load_selectors()
        self._debug_step = 0  # auto-incrementing step counter for debug files
        self._worker_pages: list[Page] = []
        self._page_pool: Optional[asyncio.Queue] = None
        self._recovery_lock = asyncio.Lock()  # prevents concurrent browser recovery

    # ------------------------------------------------------------------
    # Debug helpers
    # ------------------------------------------------------------------
    async def _debug(self, label: str, html_selector: str = "body", *, page=None) -> None:
        """Save a screenshot + relevant DOM HTML for debugging.

        Files are saved to agent/debug/ with incrementing step numbers:
          01_search_bar_found.png
          01_search_bar_found.html
        """
        p = page or self._page
        self._debug_step += 1
        prefix = f"{self._debug_step:02d}_{label}"
        try:
            # Screenshot
            screenshot_path = DEBUG_DIR / f"{prefix}.png"
            await p.screenshot(path=str(screenshot_path), full_page=True)

            # DOM snapshot — capture the relevant portion of the page
            html = await p.evaluate("""(selector) => {
                const el = document.querySelector(selector);
                if (!el) return '<no element matched: ' + selector + '>';
                return el.outerHTML;
            }""", html_selector)
            html_path = DEBUG_DIR / f"{prefix}.html"
            html_path.write_text(html, encoding="utf-8")

            logger.info("DEBUG [%s]: screenshot + HTML saved → %s", label, prefix)
        except Exception as e:
            logger.warning("DEBUG capture failed for '%s': %s", label, e)

    # ------------------------------------------------------------------
    # Page pool (for parallel fetch jobs)
    # ------------------------------------------------------------------
    async def _ensure_page(self, page=None) -> None:
        """Ensure the given page (or main page) is alive."""
        if page and page != self._page:
            try:
                await page.evaluate("() => true")
            except Exception:
                # Check if pool was invalidated by a recovery
                if not self._page_pool:
                    raise RuntimeError("Worker page invalidated — browser was recovered")
                raise RuntimeError("Worker page is no longer available")
        else:
            await self._ensure_browser()

    async def create_worker_pages(self, count: int) -> None:
        """Create additional browser pages for parallel fetch operations."""
        await self._ensure_browser()  # make sure context is alive first
        self._worker_pages = []
        self._page_pool = asyncio.Queue()
        # Main page goes into the pool too
        await self._page_pool.put(self._page)
        for _ in range(count):
            new_page = await self._context.new_page()
            self._worker_pages.append(new_page)
            await self._page_pool.put(new_page)
        logger.info("Created %d worker pages (pool size: %d)", count, self._page_pool.qsize())

    async def acquire_page(self) -> Page:
        """Get a page from the pool (blocks until one is available)."""
        if not self._page_pool:
            return self._page
        return await self._page_pool.get()

    async def release_page(self, page: Page) -> None:
        """Return a page to the pool."""
        if self._page_pool:
            await self._page_pool.put(page)

    async def close_worker_pages(self) -> None:
        """Close all worker pages and drain the pool."""
        if self._page_pool:
            while not self._page_pool.empty():
                try:
                    self._page_pool.get_nowait()
                except asyncio.QueueEmpty:
                    break
        for wp in self._worker_pages:
            try:
                await wp.close()
            except Exception:
                pass
        self._worker_pages = []
        self._page_pool = None
        logger.info("Closed worker pages")

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------
    async def screenshot(self, path: Path, *, page=None) -> None:
        """Take a screenshot for debugging."""
        p = page or self._page
        if p:
            await p.screenshot(path=str(path))

    @property
    def current_url(self) -> str:
        return self._page.url if self._page else ""

    @staticmethod
    def _cleanup_browser_downloads() -> None:
        """Remove stray auto-downloaded files from the browser downloads temp dir."""
        for f in BROWSER_DOWNLOADS_DIR.glob("*"):
            try:
                f.unlink()
            except Exception:
                pass

    @staticmethod
    def _is_uuid_filename(filename: str) -> bool:
        """Check if a filename looks like a UUID (e.g. a094186d-19b2-43cd-bfda-bbcb02e2dd3d)."""
        name = Path(filename).stem  # strip extension
        return bool(re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', name, re.I))
