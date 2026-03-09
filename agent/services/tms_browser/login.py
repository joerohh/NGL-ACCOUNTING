"""TMSLoginMixin — browser lifecycle and login flow."""

import asyncio
import logging

from playwright.async_api import BrowserContext

from config import (
    TMS_LOGIN_URL,
    TMS_PROFILE_DIR,
    TMS_DOWNLOADS_DIR,
    TMS_VIEWPORT,
)
from utils import save_cookies_async, restore_cookies

logger = logging.getLogger("ngl.tms_browser")


class TMSLoginMixin:
    """Browser lifecycle: init, close, login, keep-alive."""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def set_shared_browser(self, shared_browser) -> None:
        """Store SharedBrowser reference for lazy context creation."""
        self._shared_browser = shared_browser

    @property
    def is_initialized(self) -> bool:
        """True if TMS has an active browser context."""
        return self._context is not None

    async def init(self, *, context: BrowserContext = None, shared_browser=None) -> None:
        """Initialize with a browser context from SharedBrowser.

        If context is provided, uses it directly.
        If not provided (lazy init / crash recovery), creates via shared_browser.
        """
        if shared_browser is not None:
            self._shared_browser = shared_browser

        # Clean stale downloads
        for f in TMS_DOWNLOADS_DIR.glob("*"):
            try:
                f.unlink()
            except Exception:
                pass

        # Accept provided context or create from shared browser
        if context is not None:
            self._context = context
        elif hasattr(self, '_shared_browser') and self._shared_browser:
            self._context = await self._shared_browser.get_or_create_context(
                "tms",
                viewport=TMS_VIEWPORT,
                accept_downloads=True,
            )
        else:
            raise RuntimeError("TMS init requires either a context or shared_browser reference")

        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = await self._context.new_page()

        # Restore saved session cookies
        cookie_file = TMS_PROFILE_DIR / "_session_cookies.json"
        restored = await restore_cookies(self._context, cookie_file)
        if restored:
            logger.info("TMS browser initialized with %d restored cookies", restored)
        else:
            logger.info("TMS browser initialized (shared browser)")

    async def _ensure_browser(self) -> None:
        """Recreate context/page if the browser has crashed.

        Also handles lazy initialization — if TMS was never init'd,
        creates the context on demand via SharedBrowser.
        """
        needs_relaunch = False
        if not self._page or not self._context:
            needs_relaunch = True
        else:
            try:
                await self._page.evaluate("() => true")
            except Exception:
                needs_relaunch = True

        if needs_relaunch:
            logger.warning("TMS browser needs recovery — reinitializing...")
            if hasattr(self, '_shared_browser') and self._shared_browser:
                await self._shared_browser.ensure_running()
            await self.init()
            logger.info("TMS browser recovered successfully")

    async def close(self) -> None:
        """Save cookies and close the TMS context.

        Does NOT shut down Playwright — SharedBrowser owns that.
        """
        if not self._context:
            return
        cookie_file = TMS_PROFILE_DIR / "_session_cookies.json"
        try:
            await save_cookies_async(self._context, cookie_file)
        except Exception as e:
            logger.warning("Could not save TMS cookies: %s", e)

        try:
            await self._context.close()
        except Exception:
            pass
        self._context = None
        self._page = None
        logger.info("TMS browser closed (cookies saved)")

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------
    @property
    def current_url(self) -> str:
        """Return the current page URL (or empty string)."""
        try:
            if self._page and not self._page.is_closed():
                return self._page.url
        except Exception:
            pass
        return ""

    def is_logged_in(self) -> bool:
        """Check if TMS session is active by examining current URL."""
        url = self.current_url.lower()
        if not url:
            return False
        return "tms.ngltrans.net" in url and "sign-in" not in url

    async def open_login_page(self) -> str:
        """Navigate to TMS login page for manual Google SSO."""
        await self._ensure_browser()
        await self._page.goto(TMS_LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)
        await self._debug("login_page")
        return self._page.url

    async def wait_for_login(self, timeout_s: int = 120) -> bool:
        """Poll until user completes Google SSO login (up to timeout)."""
        elapsed = 0
        while elapsed < timeout_s:
            if self.is_logged_in():
                logger.info("TMS login detected")
                await self._debug("logged_in")
                return True
            await asyncio.sleep(3)
            elapsed += 3
        logger.warning("TMS login timed out after %ds", timeout_s)
        return False

    async def auto_login(self, email: str = "", password: str = "") -> bool:
        """Attempt automated TMS login via Google SSO.

        Returns True if login succeeds, False if 2FA or other challenge
        prevents automated completion (Chrome stays open for manual 2FA).
        """
        from config import TMS_EMAIL, TMS_PASSWORD
        email = email or TMS_EMAIL
        password = password or TMS_PASSWORD

        if not email or not password:
            logger.info("TMS auto-login skipped — no credentials configured")
            return False

        await self._ensure_browser()
        logger.info("TMS auto-login: attempting with %s...", email)

        try:
            await self._page.goto(TMS_LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)

            if self.is_logged_in():
                logger.info("TMS auto-login: already logged in after navigation")
                return True

            google_btn = None
            google_selectors = [
                'button:has-text("Google")',
                'a:has-text("Google")',
                'button:has-text("Sign in with Google")',
                'button:has-text("Continue with Google")',
                '[data-provider="google"]',
                '.google-login-btn',
            ]
            for sel in google_selectors:
                try:
                    google_btn = await self._page.wait_for_selector(sel, timeout=3000)
                    if google_btn:
                        break
                except Exception:
                    continue

            if google_btn:
                await google_btn.click()
                await asyncio.sleep(3)

            google_email_input = await self._page.wait_for_selector(
                'input[type="email"], input#identifierId', timeout=10000
            )
            if not google_email_input:
                logger.warning("TMS auto-login: Google email input not found")
                return False

            await google_email_input.fill(email)
            await asyncio.sleep(0.5)

            next_btn = await self._page.query_selector(
                '#identifierNext button, button:has-text("Next")'
            )
            if next_btn:
                await next_btn.click()
            else:
                await self._page.keyboard.press("Enter")
            await asyncio.sleep(3)

            google_pass_input = await self._page.wait_for_selector(
                'input[type="password"], input[name="Passwd"]', timeout=10000
            )
            if not google_pass_input:
                logger.warning("TMS auto-login: Google password input not found")
                return False

            await google_pass_input.fill(password)
            await asyncio.sleep(0.5)

            pass_next = await self._page.query_selector(
                '#passwordNext button, button:has-text("Next")'
            )
            if pass_next:
                await pass_next.click()
            else:
                await self._page.keyboard.press("Enter")
            await asyncio.sleep(5)

            page_text = await self._page.evaluate("() => document.body.innerText.substring(0, 2000)")
            two_fa_indicators = [
                "2-step verification", "verify it's you", "verification code",
                "security key", "confirm your recovery", "check your phone",
                "authenticator",
            ]
            if any(ind in page_text.lower() for ind in two_fa_indicators):
                logger.info("TMS auto-login: Google 2FA detected — falling back to manual")
                return False

            for _ in range(10):
                if self.is_logged_in():
                    logger.info("TMS auto-login: SUCCESS — logged in")
                    cookie_file = TMS_PROFILE_DIR / "_session_cookies.json"
                    await save_cookies_async(self._context, cookie_file)
                    return True
                await asyncio.sleep(2)

            logger.warning("TMS auto-login: did not reach TMS after Google SSO (url: %s)", self.current_url)
            return False

        except Exception as e:
            logger.error("TMS auto-login failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # Keep-alive (prevents session timeout)
    # ------------------------------------------------------------------
    async def keep_alive(self) -> bool:
        """Perform a lightweight page interaction to prevent session timeout."""
        try:
            if not self._page or not self._context:
                return False
            await self._page.evaluate("() => true")
            url = self.current_url.lower()
            if "sign-in" in url or not url:
                logger.warning("TMS session expired during keep-alive")
                return False
            await self._page.evaluate("() => { window.scrollBy(0, 1); window.scrollBy(0, -1); }")
            cookie_file = TMS_PROFILE_DIR / "_session_cookies.json"
            await save_cookies_async(self._context, cookie_file)
            return True
        except Exception as e:
            logger.warning("TMS keep-alive failed: %s", e)
            return False
