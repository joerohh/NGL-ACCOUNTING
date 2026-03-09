"""SharedBrowser — single Playwright Chrome process shared by QBO and TMS.

Instead of each service launching its own Chrome via launch_persistent_context(),
we launch ONE Chrome process and hand out isolated BrowserContext instances.
This saves ~300-500 MB of RAM by eliminating a redundant Chrome process.
"""

import asyncio
import logging
from typing import Optional

from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright

from config import BROWSER_PROFILE_DIR, TMS_PROFILE_DIR

logger = logging.getLogger("ngl.shared_browser")


class SharedBrowser:
    """Manages a single shared Chrome process with multiple isolated contexts."""

    def __init__(self) -> None:
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._contexts: dict[str, BrowserContext] = {}
        self._context_kwargs: dict[str, dict] = {}  # saved kwargs for crash recovery
        self._headless: bool = False
        self._relaunch_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self, *, headless: bool = False) -> None:
        """Launch the shared Chrome process."""
        self._headless = headless

        # Kill orphaned Chrome processes from previous runs
        from utils import kill_chrome_with_profile
        kill_chrome_with_profile(BROWSER_PROFILE_DIR)
        kill_chrome_with_profile(TMS_PROFILE_DIR)

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            channel="chrome",
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=ClearDataOnExit",
                "--hide-crash-restore-bubble",
                "--disable-session-crashed-bubble",
            ],
        )
        self._browser.on("disconnected", lambda: asyncio.ensure_future(self._on_disconnected()))
        logger.info("Shared browser started (headless=%s, pid=%s)", headless, self._browser_pid)

    @property
    def _browser_pid(self) -> str:
        """Get browser process PID for logging (best-effort)."""
        try:
            # Playwright doesn't directly expose PID, but we can get it from contexts
            return str(self._browser.contexts) if self._browser else "none"
        except Exception:
            return "unknown"

    async def _on_disconnected(self) -> None:
        """Handle browser process crash — relaunch and recreate contexts."""
        logger.warning("Shared browser disconnected — will relaunch on next operation")
        self._browser = None
        self._contexts.clear()

    # ------------------------------------------------------------------
    # Context management
    # ------------------------------------------------------------------
    async def create_context(self, name: str, **kwargs) -> BrowserContext:
        """Create a named browser context with isolated cookies/storage."""
        await self.ensure_running()
        if name in self._contexts:
            return self._contexts[name]
        ctx = await self._browser.new_context(**kwargs)
        self._contexts[name] = ctx
        self._context_kwargs[name] = kwargs
        logger.info("Created browser context: %s", name)
        return ctx

    async def get_or_create_context(self, name: str, **kwargs) -> BrowserContext:
        """Lazy context creation — returns existing or creates new."""
        if name in self._contexts:
            try:
                # Verify it's still alive by accessing pages
                _ = self._contexts[name].pages
                return self._contexts[name]
            except Exception:
                logger.warning("Context '%s' is dead — recreating", name)
                del self._contexts[name]
        # Use saved kwargs if available and none provided
        if not kwargs and name in self._context_kwargs:
            kwargs = self._context_kwargs[name]
        return await self.create_context(name, **kwargs)

    def has_context(self, name: str) -> bool:
        """Check if a named context exists."""
        return name in self._contexts

    # ------------------------------------------------------------------
    # Tab hibernation
    # ------------------------------------------------------------------
    async def hibernate_context(self, name: str) -> None:
        """Close all pages in a context but keep the context alive (preserves cookies).

        Next operation just calls context.new_page() to wake it up.
        """
        if name not in self._contexts:
            return
        ctx = self._contexts[name]
        closed = 0
        for page in list(ctx.pages):
            try:
                await page.close()
                closed += 1
            except Exception:
                pass
        if closed:
            logger.info("Hibernated context '%s' — closed %d page(s)", name, closed)

    async def close_context(self, name: str) -> None:
        """Fully close a context and remove it."""
        if name not in self._contexts:
            return
        ctx = self._contexts.pop(name)
        try:
            await ctx.close()
        except Exception:
            pass
        logger.info("Closed context: %s", name)

    # ------------------------------------------------------------------
    # Health / recovery
    # ------------------------------------------------------------------
    @property
    def is_running(self) -> bool:
        return self._browser is not None and self._browser.is_connected()

    async def ensure_running(self) -> None:
        """Relaunch the browser if it crashed."""
        if self.is_running:
            return
        async with self._relaunch_lock:
            if self.is_running:
                return  # another coroutine already relaunched
            logger.warning("Shared browser not running — relaunching...")
            try:
                if self._playwright:
                    await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
            self._browser = None
            self._contexts.clear()
            await self.start(headless=self._headless)
            logger.info("Shared browser relaunched successfully")

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------
    async def close(self) -> None:
        """Shut down everything — close all contexts, browser, and Playwright."""
        for name in list(self._contexts):
            try:
                await self._contexts[name].close()
            except Exception:
                pass
        self._contexts.clear()
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
        self._browser = None
        self._playwright = None
        logger.info("Shared browser shut down")
