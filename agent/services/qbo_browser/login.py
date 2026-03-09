"""QBO Login mixin — browser lifecycle, login, keep-alive."""

import asyncio
import logging

from playwright.async_api import async_playwright

from config import (
    BROWSER_PROFILE_DIR,
    BROWSER_DOWNLOADS_DIR,
    QBO_BASE_URL,
    QBO_LOGIN_URL,
)
from utils import kill_chrome_with_profile, save_cookies_async, restore_cookies

logger = logging.getLogger("ngl.qbo_browser")


class QBOLoginMixin:
    """Browser lifecycle, login helpers, and keep-alive."""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def init(self) -> None:
        """Launch Google Chrome with a persistent profile (cookies survive restarts)."""
        # Clean up any previous instance
        try:
            if self._context:
                await self._context.close()
        except Exception:
            pass
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass

        # Kill orphaned Chrome processes that may be locking the profile directory
        kill_chrome_with_profile(BROWSER_PROFILE_DIR)

        # Clean stale browser downloads from previous sessions
        for f in BROWSER_DOWNLOADS_DIR.glob("*"):
            try:
                f.unlink()
            except Exception:
                pass

        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            channel="chrome",  # Use installed Google Chrome instead of bundled Chromium
            user_data_dir=str(BROWSER_PROFILE_DIR),
            headless=False,  # user needs to see the browser for first login
            args=[
                "--disable-blink-features=AutomationControlled",
                # Prevent Chrome from clearing cookies / session data
                "--disable-features=ClearDataOnExit",
                # Don't show "Chrome didn't shut down correctly" bar after force-kill
                "--hide-crash-restore-bubble",
                "--disable-session-crashed-bubble",
            ],
            viewport={"width": 1920, "height": 960},
            accept_downloads=True,
            downloads_path=str(BROWSER_DOWNLOADS_DIR),
        )
        # Use first page or create one
        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = await self._context.new_page()

        # Restore saved session cookies (so QBO login persists across restarts)
        cookie_file = BROWSER_PROFILE_DIR / "_session_cookies.json"
        restored = await restore_cookies(self._context, cookie_file)
        if restored:
            logger.info("QBO browser initialized with %d restored cookies", restored)
        else:
            logger.info("QBO browser initialized (profile: %s)", BROWSER_PROFILE_DIR)

    async def _ensure_browser(self) -> None:
        """Re-launch Chrome if the browser/page has been closed or crashed."""
        needs_relaunch = False
        if not self._page or not self._context:
            needs_relaunch = True
        else:
            try:
                # Actually try to use the page — evaluate JS to confirm it's alive
                await self._page.evaluate("() => true")
            except Exception:
                needs_relaunch = True

        if needs_relaunch:
            logger.warning("Browser appears closed — relaunching Chrome...")
            await self.init()
            logger.info("Browser relaunched successfully")

    async def close(self) -> None:
        """Shut down browser — save cookies first, then close properly.

        We save all cookies (including session-only ones) to a JSON file so they
        can be restored on next startup.  Then we close the context properly so
        Chrome doesn't leave orphaned processes locking the profile directory.
        """
        cookie_file = BROWSER_PROFILE_DIR / "_session_cookies.json"
        try:
            if self._context:
                await save_cookies_async(self._context, cookie_file)
        except Exception as e:
            logger.warning("Could not save QBO cookies: %s", e)

        try:
            if self._context:
                await self._context.close()
        except Exception:
            pass
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        self._context = None
        self._page = None
        self._playwright = None
        logger.info("QBO browser closed (cookies saved)")

    # ------------------------------------------------------------------
    # Keep-alive (prevents session timeout)
    # ------------------------------------------------------------------
    async def keep_alive(self) -> bool:
        """Perform a lightweight page interaction to prevent QBO session timeout.

        Returns True if the session is still active, False if logged out.
        """
        try:
            if not self._page or not self._context:
                return False
            await self._page.evaluate("() => true")
            url = self._page.url
            if "sign-in" in url or "accounts.intuit.com" in url:
                logger.warning("QBO session expired during keep-alive")
                return False
            if "qbo.intuit.com" not in url:
                # On about:blank or other page — not necessarily expired, just not on QBO
                return True
            # Tiny scroll to simulate user activity
            await self._page.evaluate("() => { window.scrollBy(0, 1); window.scrollBy(0, -1); }")
            # Save cookies periodically (not just on shutdown)
            cookie_file = BROWSER_PROFILE_DIR / "_session_cookies.json"
            await save_cookies_async(self._context, cookie_file)
            return True
        except Exception as e:
            logger.warning("QBO keep-alive failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # Login helpers
    # ------------------------------------------------------------------
    async def is_logged_in(self) -> bool:
        """Check whether the current QBO session is still active.

        First checks the current URL — if we're already on a QBO app page,
        we skip navigation entirely (avoids unnecessary page loads that could
        trigger re-auth or slow things down).

        Only navigates to QBO if we're on a non-QBO page (e.g. about:blank on
        fresh start).
        """
        try:
            await self._ensure_browser()
            url = self._page.url

            # Already on a QBO app page? Session is active — no need to navigate.
            if url and "qbo.intuit.com" in url and "sign-in" not in url:
                logger.info("QBO session is active (url: %s)", url)
                return True

            # Already on a sign-in page? Session expired.
            if url and ("sign-in" in url or "accounts.intuit.com" in url):
                logger.info("QBO session expired (on sign-in page: %s)", url)
                return False

            # Not on QBO at all (e.g. about:blank) — navigate to check
            await self._page.goto(QBO_BASE_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(8)
            url = self._page.url

            if "qbo.intuit.com" in url and "sign-in" not in url:
                logger.info("QBO session is active (url: %s)", url)
                return True
            if "sign-in" in url or "accounts.intuit.com" in url:
                logger.info("QBO session expired (redirected to: %s)", url)
                return False
            logger.info("QBO login status uncertain (url: %s) — assuming logged out", url)
            return False
        except Exception as e:
            logger.warning("Error checking QBO login: %s", e)
            return False

    async def open_login_page(self) -> str:
        """Navigate the browser to QBO login so the user can sign in manually."""
        await self._ensure_browser()
        await self._page.goto(QBO_LOGIN_URL, wait_until="domcontentloaded", timeout=20000)
        logger.info("Opened QBO login page for manual authentication")
        return self._page.url

    async def wait_for_login(self, timeout_s: int = 120) -> bool:
        """Wait up to timeout_s for the user to complete login and land on QBO."""
        await self._ensure_browser()
        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(3)
            url = self._page.url
            # User has landed on a QBO page (not sign-in) — success
            if "qbo.intuit.com" in url and "sign-in" not in url:
                logger.info("User logged into QBO successfully (url: %s)", url)
                return True
        logger.warning("Timed out waiting for QBO login (%ds)", timeout_s)
        return False

    async def auto_login(self, email: str = "", password: str = "") -> bool:
        """Attempt automated QBO login using stored credentials.

        Returns True if login succeeds, False if 2FA/CAPTCHA is detected
        or login fails (caller should fall back to manual).
        """
        from config import QBO_EMAIL, QBO_PASSWORD, QBO_LOGIN_URL
        email = email or QBO_EMAIL
        password = password or QBO_PASSWORD

        if not email or not password:
            logger.info("QBO auto-login skipped — no credentials configured")
            return False

        await self._ensure_browser()
        logger.info("QBO auto-login: attempting with %s...", email)

        try:
            # Navigate to login page
            await self._page.goto(QBO_LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)

            url = self._page.url
            if "qbo.intuit.com" in url and "sign-in" not in url:
                logger.info("QBO auto-login: already logged in after navigation")
                return True

            # Use selectors from selectors.json
            sel = self._selectors.get("login", {})
            email_sel = sel.get("email_input", "input#ius-userid")
            password_sel = sel.get("password_input", "input#ius-password")
            submit_sel = sel.get("sign_in_button", "button[data-testid='ius-sign-in-submit-btn']")

            # Fill email
            email_input = await self._page.wait_for_selector(email_sel, timeout=10000)
            if not email_input:
                logger.warning("QBO auto-login: email input not found")
                return False
            await email_input.fill(email)
            await asyncio.sleep(0.5)

            # Check if password field is on same page
            password_input = await self._page.query_selector(password_sel)
            if password_input and await password_input.is_visible():
                await password_input.fill(password)
                await asyncio.sleep(0.5)
                submit_btn = await self._page.query_selector(submit_sel)
                if submit_btn:
                    await submit_btn.click()
                else:
                    await self._page.keyboard.press("Enter")
            else:
                # Two-step: submit email first, then password
                submit_btn = await self._page.query_selector(submit_sel)
                if submit_btn:
                    await submit_btn.click()
                else:
                    await self._page.keyboard.press("Enter")
                await asyncio.sleep(3)
                password_input = await self._page.wait_for_selector(password_sel, timeout=10000)
                if not password_input:
                    logger.warning("QBO auto-login: password field not found after email step")
                    return False
                await password_input.fill(password)
                await asyncio.sleep(0.5)
                submit_btn = await self._page.query_selector(submit_sel)
                if submit_btn:
                    await submit_btn.click()
                else:
                    await self._page.keyboard.press("Enter")

            # Wait for result
            await asyncio.sleep(5)

            # Check for 2FA/MFA
            page_text = await self._page.evaluate("() => document.body.innerText.substring(0, 2000)")
            two_fa_indicators = [
                "verify your identity", "verification code", "two-step verification",
                "enter the code", "security code", "we sent a code",
                "check your email", "authenticator app",
            ]
            if any(ind in page_text.lower() for ind in two_fa_indicators):
                logger.info("QBO auto-login: 2FA/MFA detected — falling back to manual")
                return False

            # Check for CAPTCHA
            captcha_indicators = ["captcha", "robot", "verify you're human", "challenge"]
            if any(ind in page_text.lower() for ind in captcha_indicators):
                logger.info("QBO auto-login: CAPTCHA detected — falling back to manual")
                return False

            # Check if we landed on QBO
            url = self._page.url
            if "qbo.intuit.com" in url and "sign-in" not in url:
                logger.info("QBO auto-login: SUCCESS — logged in at %s", url)
                cookie_file = BROWSER_PROFILE_DIR / "_session_cookies.json"
                await save_cookies_async(self._context, cookie_file)
                return True

            # Some logins redirect slowly
            await asyncio.sleep(5)
            url = self._page.url
            if "qbo.intuit.com" in url and "sign-in" not in url:
                logger.info("QBO auto-login: SUCCESS (delayed) — logged in at %s", url)
                cookie_file = BROWSER_PROFILE_DIR / "_session_cookies.json"
                await save_cookies_async(self._context, cookie_file)
                return True

            logger.warning("QBO auto-login: did not reach QBO dashboard (url: %s)", url)
            return False

        except Exception as e:
            logger.error("QBO auto-login failed: %s", e)
            return False
