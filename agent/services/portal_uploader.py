"""TranzAct portal uploader — browser automation for invoice uploads."""

import asyncio
import logging
from pathlib import Path
from typing import Optional

from playwright.async_api import BrowserContext, Page

from config import DEBUG_DIR

logger = logging.getLogger("ngl.portal_uploader")


class PortalUploader:
    """Upload invoices to carrier portals via browser automation."""

    def __init__(self, browser_context: BrowserContext,
                 username: str, password: str) -> None:
        self._context = browser_context
        self._username = username
        self._password = password
        self._page: Optional[Page] = None
        self._debug_step = 0

    async def _debug(self, label: str) -> None:
        """Save a debug screenshot."""
        self._debug_step += 1
        prefix = f"portal_{self._debug_step:02d}_{label}"
        try:
            if self._page:
                screenshot_path = DEBUG_DIR / f"{prefix}.png"
                await self._page.screenshot(path=str(screenshot_path), full_page=True)
                logger.info("PORTAL DEBUG [%s]: screenshot saved", label)
        except Exception as e:
            logger.warning("Portal debug capture failed for '%s': %s", label, e)

    async def _get_page(self) -> Page:
        """Get or create the portal browser page."""
        if self._page and not self._page.is_closed():
            return self._page
        self._page = await self._context.new_page()
        return self._page

    async def _login_if_needed(self, portal_url: str) -> bool:
        """Navigate to portal and login if session expired.

        Returns True if logged in successfully.
        """
        page = await self._get_page()
        await page.goto(portal_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)
        await self._debug("portal_loaded")

        # Check if we're on a login page
        page_text = await page.evaluate("() => document.body.innerText || ''")
        page_url = page.url.lower()

        is_login_page = (
            "sign in" in page_text.lower()
            or "log in" in page_text.lower()
            or "username" in page_text.lower()
            or "login" in page_url
            or "signin" in page_url
        )

        if not is_login_page:
            logger.info("Portal session active — no login needed")
            return True

        if not self._username or not self._password:
            logger.error("Portal login required but no credentials configured")
            return False

        # Attempt login
        logger.info("Portal login required — attempting automated login")
        try:
            # Find and fill username field
            username_filled = await page.evaluate("""(username) => {
                const inputs = document.querySelectorAll('input[type="text"], input[type="email"], input[name*="user"], input[id*="user"], input[name*="email"]');
                for (const inp of inputs) {
                    inp.value = username;
                    inp.dispatchEvent(new Event('input', { bubbles: true }));
                    inp.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
                return false;
            }""", self._username)

            if not username_filled:
                await self._debug("portal_no_username_field")
                return False

            # Find and fill password field
            password_filled = await page.evaluate("""(password) => {
                const inputs = document.querySelectorAll('input[type="password"]');
                for (const inp of inputs) {
                    inp.value = password;
                    inp.dispatchEvent(new Event('input', { bubbles: true }));
                    inp.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
                return false;
            }""", self._password)

            if not password_filled:
                await self._debug("portal_no_password_field")
                return False

            # Click submit/login button
            await page.evaluate("""() => {
                const buttons = document.querySelectorAll('button[type="submit"], input[type="submit"], button');
                for (const btn of buttons) {
                    const text = (btn.textContent || btn.value || '').toLowerCase();
                    if (text.includes('sign in') || text.includes('log in') || text.includes('login') || text.includes('submit')) {
                        btn.click();
                        return true;
                    }
                }
                // Last resort: submit the form
                const form = document.querySelector('form');
                if (form) { form.submit(); return true; }
                return false;
            }""")

            await page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(5)
            await self._debug("portal_after_login")

            # Verify login succeeded
            new_url = page.url.lower()
            if "login" not in new_url and "signin" not in new_url:
                logger.info("Portal login successful")
                return True
            else:
                logger.error("Portal login appears to have failed")
                return False

        except Exception as e:
            logger.error("Portal login failed: %s", e)
            await self._debug("portal_login_error")
            return False

    async def upload_to_tranzact(
        self,
        portal_url: str,
        client_name: str,
        pdf_path: Path,
    ) -> dict:
        """Upload a combined invoice+POD PDF to the TranzAct portal.

        Steps:
        1. Navigate to portal_url
        2. Login if needed
        3. Navigate to Tools → Invoice Upload
        4. Select client by name
        5. Upload the PDF

        Returns: { uploaded: bool, error: str|None }
        """
        if not pdf_path.exists():
            return {"uploaded": False, "error": f"PDF file not found: {pdf_path}"}

        try:
            # Step 1: Login
            logged_in = await self._login_if_needed(portal_url)
            if not logged_in:
                return {"uploaded": False, "error": "Portal login failed — check credentials in .env"}

            page = await self._get_page()

            # Step 2: Navigate to Invoice Upload
            # Look for "Tools" menu or direct "Invoice Upload" link
            nav_result = await page.evaluate("""() => {
                const links = document.querySelectorAll('a, button, [role="menuitem"]');
                for (const el of links) {
                    const text = (el.textContent || '').trim().toLowerCase();
                    if (text.includes('tools') || text.includes('invoice upload')) {
                        el.click();
                        return { found: true, text: text };
                    }
                }
                return { found: false };
            }""")

            if not nav_result.get("found"):
                await self._debug("portal_no_tools_menu")
                return {"uploaded": False, "error": "Could not find Tools menu or Invoice Upload link"}

            await asyncio.sleep(3)

            # If we clicked "Tools", now look for "Invoice Upload" submenu
            if "tools" in nav_result.get("text", ""):
                await page.evaluate("""() => {
                    const links = document.querySelectorAll('a, button, [role="menuitem"]');
                    for (const el of links) {
                        const text = (el.textContent || '').trim().toLowerCase();
                        if (text.includes('invoice upload')) {
                            el.click();
                            return true;
                        }
                    }
                    return false;
                }""")
                await asyncio.sleep(3)

            await self._debug("portal_invoice_upload_page")

            # Step 3: Select client
            client_selected = await page.evaluate("""(clientName) => {
                // Look for a select/dropdown containing the client name
                const selects = document.querySelectorAll('select');
                for (const sel of selects) {
                    for (const opt of sel.options) {
                        if (opt.text.toUpperCase().includes(clientName.toUpperCase())) {
                            sel.value = opt.value;
                            sel.dispatchEvent(new Event('change', { bubbles: true }));
                            return { found: true, value: opt.text };
                        }
                    }
                }
                // Try clicking a list item or radio button
                const items = document.querySelectorAll('li, label, div[role="option"]');
                for (const item of items) {
                    if ((item.textContent || '').toUpperCase().includes(clientName.toUpperCase())) {
                        item.click();
                        return { found: true, value: item.textContent.trim() };
                    }
                }
                return { found: false };
            }""", client_name)

            if not client_selected.get("found"):
                await self._debug("portal_client_not_found")
                return {"uploaded": False, "error": f"Client '{client_name}' not found in portal dropdown"}

            logger.info("Selected portal client: %s", client_selected.get("value"))
            await asyncio.sleep(2)

            # Step 4: Look for file upload area
            # Try to find the "Submit one or more, multi-page files" option first
            await page.evaluate("""() => {
                const elements = document.querySelectorAll('a, button, label, div, span');
                for (const el of elements) {
                    const text = (el.textContent || '').toLowerCase();
                    if (text.includes('multi-page') || text.includes('submit one or more')) {
                        el.click();
                        return true;
                    }
                }
                return false;
            }""")
            await asyncio.sleep(2)

            # Step 5: Upload the file
            file_input = await page.query_selector('input[type="file"]')
            if not file_input:
                await self._debug("portal_no_file_input")
                return {"uploaded": False, "error": "Could not find file upload input on portal"}

            await file_input.set_input_files(str(pdf_path))
            await asyncio.sleep(3)
            await self._debug("portal_file_selected")

            # Step 6: Click submit/upload button
            submit_clicked = await page.evaluate("""() => {
                const buttons = document.querySelectorAll('button, input[type="submit"]');
                for (const btn of buttons) {
                    const text = (btn.textContent || btn.value || '').toLowerCase();
                    if (text.includes('upload') || text.includes('submit') || text.includes('send')) {
                        btn.click();
                        return true;
                    }
                }
                return false;
            }""")

            if not submit_clicked:
                await self._debug("portal_no_submit_button")
                return {"uploaded": False, "error": "Could not find submit/upload button on portal"}

            await asyncio.sleep(5)
            await self._debug("portal_after_upload")

            # Check for success indicators
            page_text = await page.evaluate("() => document.body.innerText || ''")
            text_lower = page_text.lower()
            if "success" in text_lower or "uploaded" in text_lower or "complete" in text_lower:
                logger.info("Portal upload successful for %s", pdf_path.name)
                return {"uploaded": True, "error": None}

            # If no clear success, check for error indicators
            if "error" in text_lower or "failed" in text_lower or "invalid" in text_lower:
                return {"uploaded": False, "error": "Portal reported an error after upload — check screenshot"}

            # Ambiguous — assume success if no error
            logger.warning("Portal upload result unclear — assuming success. Check debug screenshots.")
            return {"uploaded": True, "error": None}

        except Exception as e:
            logger.error("Portal upload failed: %s", e)
            await self._debug("portal_upload_error")
            return {"uploaded": False, "error": str(e)}

    async def close(self) -> None:
        """Close the portal page (not the browser context)."""
        if self._page and not self._page.is_closed():
            await self._page.close()
        self._page = None
