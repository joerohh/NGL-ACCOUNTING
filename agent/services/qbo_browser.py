"""QuickBooks Online browser automation via Playwright."""

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, BrowserContext, Page

from config import (
    BROWSER_PROFILE_DIR,
    BROWSER_DOWNLOADS_DIR,
    DEBUG_DIR,
    QBO_BASE_URL,
    QBO_LOGIN_URL,
    QBO_ACTION_DELAY_S,
    QBO_RETRY_COUNT,
    QBO_RETRY_BACKOFF_S,
    SELECTORS_FILE,
    DOWNLOADS_DIR,
)
from utils import strip_motw, kill_chrome_with_profile, save_cookies_async, restore_cookies

logger = logging.getLogger("ngl.qbo_browser")


def _load_selectors() -> dict:
    """Load QBO DOM selectors from the JSON config file."""
    if SELECTORS_FILE.exists():
        with open(SELECTORS_FILE, "r") as f:
            return json.load(f)
    return {}


class QBOBrowser:
    """Controls a persistent Chrome browser to interact with QuickBooks Online."""

    def __init__(self) -> None:
        self._playwright = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._selectors: dict = _load_selectors()
        self._debug_step = 0  # auto-incrementing step counter for debug files

    async def _debug(self, label: str, html_selector: str = "body") -> None:
        """Save a screenshot + relevant DOM HTML for debugging.

        Files are saved to agent/debug/ with incrementing step numbers:
          01_search_bar_found.png
          01_search_bar_found.html
        """
        self._debug_step += 1
        prefix = f"{self._debug_step:02d}_{label}"
        try:
            # Screenshot
            screenshot_path = DEBUG_DIR / f"{prefix}.png"
            await self._page.screenshot(path=str(screenshot_path), full_page=True)

            # DOM snapshot — capture the relevant portion of the page
            html = await self._page.evaluate("""(selector) => {
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

    # ------------------------------------------------------------------
    # Invoice search & download
    # ------------------------------------------------------------------
    async def search_invoice(self, invoice_number: str) -> Optional[str]:
        """
        Search QBO for an invoice by number using the global search bar.

        Flow: Type invoice # → Press Enter → lands on Search Results page
        (table with DATE, TYPE, REF NO, CONTACT, etc.) → click the invoice row
        in that table → lands on actual Invoice Detail page.

        Returns the invoice detail page URL if found, None otherwise.
        """
        await self._ensure_browser()

        # Reset step counter for each new invoice search
        self._debug_step = 0

        # Find the search bar — reuse current page if already on QBO, else load homepage
        search_input_sel = (
            'input[placeholder*="search" i], '
            'input[placeholder*="navigate" i], '
            "input[data-testid='global-search-input']"
        )

        search_input = None
        current_url = self._page.url if self._page else ""

        if QBO_BASE_URL in current_url:
            # Already on QBO — try to grab search bar without reloading (saves ~12s)
            try:
                search_input = await self._page.wait_for_selector(search_input_sel, timeout=3000)
                logger.info("Reusing existing QBO search bar (skipped homepage reload)")
            except Exception:
                search_input = None  # Fall through to full navigation

        if not search_input:
            # Full homepage navigation (first invoice, or search bar not found)
            await self._page.goto(QBO_BASE_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(8)
            try:
                search_input = await self._page.wait_for_selector(search_input_sel, timeout=15000)
            except Exception as e:
                logger.error("Could not find QBO search bar after homepage load: %s", e)
                await self._debug(f"search_bar_NOT_FOUND_{invoice_number}")
                return None

        try:
            await search_input.click()
            await asyncio.sleep(0.3)
            await search_input.fill("")
            await asyncio.sleep(0.2)
            await search_input.type(invoice_number, delay=50)
            logger.info("Typed '%s' into QBO search bar", invoice_number)
            await asyncio.sleep(1.5)
        except Exception as e:
            logger.error("Failed to type in QBO search bar: %s", e)
            await self._debug(f"search_type_FAILED_{invoice_number}")
            return None

        # Press Enter to go to the full Search Results page
        # (The dropdown "quick results" just navigates to this same page anyway)
        try:
            search_input = await self._page.query_selector(search_input_sel)
            if search_input:
                await search_input.press("Enter")
            await self._page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(4)  # Brief initial wait for page shell
        except Exception as e:
            logger.error("Failed to load search results page for %s: %s", invoice_number, e)
            await self._debug(f"search_page_FAILED_{invoice_number}")
            return None

        # Poll for data rows to appear (QBO loads table data asynchronously).
        # Instead of a fixed wait, check repeatedly until real rows show up.
        async def _wait_for_search_data(max_wait=20, poll_interval=2):
            """Poll until the search results table has actual data rows (not skeletons)."""
            elapsed = 0
            while elapsed < max_wait:
                row_count = await self._page.evaluate("""() => {
                    const rows = document.querySelectorAll('tr, [role="row"]');
                    let dataRows = 0;
                    for (const r of rows) {
                        const text = (r.textContent || '').trim();
                        // Skip header rows and empty/skeleton rows
                        if (text.length > 50 && !text.startsWith('Date') && !text.startsWith('DATE')) {
                            dataRows++;
                        }
                    }
                    return dataRows;
                }""")
                if row_count > 0:
                    logger.info("Search results loaded: %d data row(s) after ~%ds", row_count, elapsed)
                    return True
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval
            return False

        data_loaded = await _wait_for_search_data()

        # If no data rows appeared, try clicking "Search exact words instead" link
        # QBO's default fuzzy search sometimes fails on invoice numbers like LM26020580F
        if not data_loaded:
            logger.info("No data rows after polling — trying 'exact words' search for %s", invoice_number)
            exact_clicked = await self._page.evaluate("""() => {
                const links = document.querySelectorAll('a, button, span[role="button"]');
                for (const el of links) {
                    const text = (el.textContent || '').toLowerCase();
                    if (text.includes('exact word') || text.includes('exact match')) {
                        el.click();
                        return true;
                    }
                }
                return false;
            }""")
            if exact_clicked:
                logger.info("Clicked 'exact words' link — waiting for results")
                await asyncio.sleep(3)
                data_loaded = await _wait_for_search_data(max_wait=15)

        logger.info("Search results page for %s: %s (data_loaded=%s)", invoice_number, self._page.url, data_loaded)

        # Now find and click the invoice row in the results table.
        # Try up to 2 times with a short wait between — rows may still be rendering.
        async def _find_and_click_row():
            """Look for the invoice number in table cells and click its row."""
            return await self._page.evaluate("""(invoiceNum) => {
                // Look for table rows/cells containing the invoice number
                const allCells = document.querySelectorAll('td, [role="cell"], [role="gridcell"]');
                for (const cell of allCells) {
                    const text = (cell.textContent || '').trim();
                    if (text === invoiceNum) {
                        // Found the REF NO cell. Click the row to open the invoice.
                        const row = cell.closest('tr, [role="row"]');
                        if (row) {
                            row.click();
                            return { clicked: 'row', tag: row.tagName, text: text };
                        }
                        // No row? Click the cell itself.
                        cell.click();
                        return { clicked: 'cell', tag: cell.tagName, text: text };
                    }
                }
                // Fallback: Look for a link with the invoice number
                const links = document.querySelectorAll('a');
                for (const a of links) {
                    const text = (a.textContent || '').trim();
                    if (text.includes(invoiceNum) && a.href && a.href.includes('invoice')) {
                        a.click();
                        return { clicked: 'link', tag: 'A', href: a.href, text: text };
                    }
                }
                return null;
            }""", invoice_number)

        try:
            row_clicked = await _find_and_click_row()

            # Retry once if the first attempt missed (rows may still be rendering)
            if not row_clicked:
                logger.info("Row not found on first attempt for %s — retrying after 4s", invoice_number)
                await asyncio.sleep(4)
                row_clicked = await _find_and_click_row()

            if row_clicked:
                logger.info("Clicked search result: %s", row_clicked)
                await self._page.wait_for_load_state("domcontentloaded")

                # Wait for invoice detail page — detect "Review and send" button
                # instead of fixed 12s sleep (saves ~7s, timeout 15s for safety)
                for _ in range(30):  # 30 × 0.5s = 15s max wait
                    found_btn = await self._page.evaluate("""() => {
                        const els = document.querySelectorAll('a, button, [role="button"]');
                        for (const el of els) {
                            const text = (el.textContent || '').trim().toLowerCase();
                            if (text.includes('review and send') || text.includes('review & send')) return true;
                        }
                        return false;
                    }""")
                    if found_btn:
                        break
                    await asyncio.sleep(0.5)

                await self._debug(f"invoice_detail_page_{invoice_number}")

                url = self._page.url
                # Verify we're actually on an invoice detail page (has txnId in URL)
                if "invoice" in url or "txnId" in url:
                    logger.info("Invoice detail page loaded: %s", url)
                    return url
                else:
                    logger.warning("After clicking row, landed on unexpected page: %s", url)
                    await self._debug(f"unexpected_page_{invoice_number}")
                    # Still return the URL — might be usable
                    return url
            else:
                logger.warning("Could not find invoice %s row in search results table", invoice_number)
                await self._debug(f"row_NOT_FOUND_{invoice_number}")

                # Check if the invoice number appears anywhere on the page (DOM issue vs truly not found)
                page_text_check = await self._page.evaluate("""(invoiceNum) => {
                    const bodyText = document.body.innerText || '';
                    const found = bodyText.includes(invoiceNum);
                    const noResults = bodyText.toLowerCase().includes('no results') ||
                                     bodyText.toLowerCase().includes('no match') ||
                                     bodyText.toLowerCase().includes('0 results');
                    return { foundOnPage: found, noResultsMessage: noResults };
                }""", invoice_number)

                if page_text_check.get("noResultsMessage"):
                    logger.error("Invoice %s: QBO search returned NO RESULTS — invoice may not exist in QBO", invoice_number)
                elif page_text_check.get("foundOnPage"):
                    logger.error("Invoice %s: Found on page but could not click the row — QBO table structure may have changed", invoice_number)
                else:
                    logger.error("Invoice %s: Not visible on search results page — may be on a different page or filtered out", invoice_number)

                # Dump the page structure for debugging
                page_info = await self._page.evaluate("""() => {
                    const rows = document.querySelectorAll('tr, [role="row"]');
                    return Array.from(rows).slice(0, 10).map(r => ({
                        tag: r.tagName,
                        text: (r.textContent || '').substring(0, 200).replace(/\\s+/g, ' '),
                        className: (r.className || '').substring(0, 100),
                    }));
                }""")
                dump_path = DEBUG_DIR / f"{self._debug_step + 1:02d}_table_rows_{invoice_number}.json"
                dump_path.write_text(json.dumps(page_info, indent=2), encoding="utf-8")

        except Exception as e:
            logger.error("Error clicking invoice row for %s: %s", invoice_number, e)
            await self._debug(f"row_click_FAILED_{invoice_number}")

        return None

    async def _download_attachment(self, link, download_dir: Path, label: str,
                                    original_filename: str = "") -> Optional[Path]:
        """Download an attachment by clicking its link on the invoice page.
        QBO attachment links either trigger a direct download or open in a new tab.
        original_filename: the link text (e.g. 'lm2601120027_pod.pdf') to use as filename
        when QBO gives us a UUID-named download.
        """
        await self._debug(f"before_download_{label}")

        # Detect if this link opens a new tab (target="_blank" or external doc URL)
        target = (await link.get_attribute("target") or "").strip()
        href = (await link.get_attribute("href") or "").strip()
        opens_new_tab = target == "_blank" or "financialdocument" in href

        for attempt in range(QBO_RETRY_COUNT):
            try:
                # ── Direct fetch (for new-tab links) ─────────────────────
                # Fetch the PDF via JS fetch() on the current page — never
                # click the link, never open a new tab, zero Chrome downloads.
                if opens_new_tab and href:
                    logger.info("%s: fetching PDF directly via href (no click, no new tab)", label)
                    content = await self._page.evaluate("""async (url) => {
                        const resp = await fetch(url, { credentials: 'include' });
                        if (!resp.ok) return { error: resp.status };
                        const buf = await resp.arrayBuffer();
                        return Array.from(new Uint8Array(buf));
                    }""", href)

                    if isinstance(content, dict) and "error" in content:
                        logger.warning("Direct fetch failed for %s: HTTP %s", label, content["error"])
                    elif len(content) >= 5 and bytes(content[:5]) == b'%PDF-':
                        filename = original_filename or f"{label}.pdf"
                        if not filename.lower().endswith('.pdf'):
                            filename += '.pdf'
                        dest = download_dir / filename
                        dest.write_bytes(bytes(content))
                        strip_motw(dest)
                        logger.info("Downloaded %s (direct fetch — no new tab): %s", label, dest.name)
                        await self._debug(f"download_SUCCESS_{label}")
                        return dest
                    else:
                        logger.warning("Direct fetch returned non-PDF for %s (first bytes: %s)",
                                       label, bytes(content[:20]) if content else b'empty')

                # ── Method 1: expect_download (same-page download links) ─
                if not opens_new_tab:
                    try:
                        async with self._page.expect_download(timeout=5000) as download_info:
                            await link.click()
                        download = await download_info.value
                        suggested = download.suggested_filename
                        if original_filename and self._is_uuid_filename(suggested):
                            filename = original_filename
                        else:
                            filename = suggested
                        if not filename.lower().endswith('.pdf'):
                            filename += '.pdf'
                        dest = download_dir / filename
                        await download.save_as(str(dest))
                        strip_motw(dest)
                        logger.info("Downloaded %s (method 1 - direct): %s", label, dest.name)
                        await self._debug(f"download_SUCCESS_{label}")
                        self._cleanup_browser_downloads()
                        return dest
                    except Exception as e:
                        logger.info("Download method 1 failed for %s: %s", label, e)

                # ── Method 2: Check for new tab (fallback if click opened one)
                self._cleanup_browser_downloads()
                await asyncio.sleep(2)
                pages = self._context.pages
                if len(pages) > 1:
                    new_page = pages[-1]
                    await new_page.wait_for_load_state("load", timeout=15000)
                    pdf_url = new_page.url
                    logger.info("Fallback: new tab found with URL: %s", pdf_url[:200])

                    if pdf_url and pdf_url != "about:blank":
                        content = await new_page.evaluate("""async (url) => {
                            const resp = await fetch(url);
                            const buf = await resp.arrayBuffer();
                            return Array.from(new Uint8Array(buf));
                        }""", pdf_url)

                        if len(content) < 5 or bytes(content[:5]) != b'%PDF-':
                            logger.warning("New tab content is not PDF for %s", label)
                            await new_page.close()
                        else:
                            filename = original_filename or f"{label}.pdf"
                            if not filename.lower().endswith('.pdf'):
                                filename += '.pdf'
                            dest = download_dir / filename
                            dest.write_bytes(bytes(content))
                            strip_motw(dest)
                            await new_page.close()
                            logger.info("Downloaded %s (method 2 - new tab fetch): %s", label, dest.name)
                            await self._debug(f"download_SUCCESS_{label}")
                            self._cleanup_browser_downloads()
                            return dest
                    else:
                        await new_page.close()

            except Exception as e:
                backoff = QBO_RETRY_BACKOFF_S * (2 ** attempt)
                logger.warning("%s download attempt %d failed: %s (retry in %.1fs)",
                               label, attempt + 1, e, backoff)
                await self._debug(f"download_attempt{attempt+1}_FAILED_{label}")
                await asyncio.sleep(backoff)
                while len(self._context.pages) > 1:
                    await self._context.pages[-1].close()

        await self._debug(f"download_ALL_FAILED_{label}")
        logger.error("Failed to download %s after %d attempts", label, QBO_RETRY_COUNT)
        self._cleanup_browser_downloads()
        return None

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

    async def download_invoice_pdf(self, download_dir: Path) -> Optional[Path]:
        """
        Download the invoice PDF from the currently-open invoice page.

        The QBO invoice page has a bottom action bar with "Print or download"
        which opens a dropdown with: Print | Download | Print packing slip.
        We click "Print or download" → then click "Download" in the dropdown.

        Fallback: Look for invoice attachments in the page (files like *_it.pdf).
        """
        await self._ensure_browser()
        await self._debug("invoice_page_before_download")

        # Remember the invoice URL so we can verify we stay on it
        invoice_url = self._page.url

        # Strategy 1: Click "Print or download" in bottom bar → "Download"
        try:
            # The "Print or download" link is in the bottom action bar of the invoice
            pod_link = await self._page.query_selector(
                'a:has-text("Print or download"), '
                'button:has-text("Print or download")'
            )
            if pod_link:
                await pod_link.click()
                await asyncio.sleep(1.5)
                await self._debug("print_download_dropdown_opened")

                # Now click the "Download" option in the dropdown
                try:
                    async with self._page.expect_download(timeout=20000) as download_info:
                        # The dropdown shows plain text items: "Print", "Download", "Print packing slip"
                        # Use evaluate to find and click the exact "Download" text
                        clicked = await self._page.evaluate("""() => {
                            // Look for menu items / list items containing exactly "Download"
                            const candidates = document.querySelectorAll(
                                'li, [role="menuitem"], [role="option"], a, button, div, span'
                            );
                            for (const el of candidates) {
                                const text = (el.textContent || '').trim();
                                // Match "Download" exactly (not "Print or download")
                                if (text === 'Download') {
                                    el.click();
                                    return { clicked: true, tag: el.tagName, text: text };
                                }
                            }
                            return null;
                        }""")
                        if not clicked:
                            raise Exception("Could not find 'Download' option in dropdown")
                        logger.info("Clicked Download option: %s", clicked)

                    download = await download_info.value
                    inv_filename = download.suggested_filename
                    if not inv_filename.lower().endswith('.pdf'):
                        inv_filename += '.pdf'
                    dest = download_dir / inv_filename
                    await download.save_as(str(dest))
                    strip_motw(dest)
                    logger.info("Downloaded invoice PDF via 'Print or download' → Download: %s", dest.name)
                    await self._debug("download_SUCCESS_invoice")
                    self._cleanup_browser_downloads()
                    return dest
                except Exception as e:
                    logger.info("Download via dropdown failed: %s", e)
                    await self._debug("download_dropdown_FAILED")

                    # Press Escape to close the dropdown without navigating away
                    await self._page.keyboard.press("Escape")
                    await asyncio.sleep(0.5)
            else:
                logger.info("'Print or download' link not found in bottom bar")
                await self._debug("print_or_download_NOT_FOUND")
        except Exception as e:
            logger.info("Print or download flow failed: %s, trying attachments", e)
            await self._debug("print_download_flow_FAILED")

        # Make sure we're still on the invoice page (not navigated away)
        current = self._page.url
        if current != invoice_url and "invoice" not in current and "txnId" not in current:
            logger.warning("Page navigated away from invoice (%s), going back", current)
            await self._page.goto(invoice_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(5)

        # Strategy 2: Look for an invoice attachment in the Attachments section
        await self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(3)
        await self._debug("scrolled_to_bottom_for_invoice")

        # Dump all links on the page so we can see what's available
        link_dump = await self._page.evaluate("""() => {
            const links = Array.from(document.querySelectorAll('a'));
            return links.map(a => ({
                text: (a.innerText || '').trim().substring(0, 100),
                href: (a.href || '').substring(0, 200),
                className: (a.className && typeof a.className === 'string') ? a.className.substring(0, 100) : '',
                visible: a.offsetParent !== null,
            })).filter(l => l.text.length > 0);
        }""")
        link_dump_path = DEBUG_DIR / f"{self._debug_step + 1:02d}_all_links_for_invoice.json"
        link_dump_path.write_text(json.dumps(link_dump, indent=2), encoding="utf-8")
        logger.info("Dumped %d links on invoice page to debug", len(link_dump))

        all_links = await self._page.query_selector_all("a")
        for link in all_links:
            try:
                text = (await link.inner_text()).strip().lower()
            except Exception:
                continue
            if text.endswith(".pdf") and ("_it." in text or "invoice" in text):
                logger.info("Found invoice attachment: %s", text)
                return await self._download_attachment(link, download_dir, "invoice",
                                                       original_filename=text)

        await self._debug("invoice_attachment_NOT_FOUND")
        logger.warning("Could not download invoice PDF — no suitable method found")
        return None

    async def find_and_download_pod(self, download_dir: Path) -> Optional[Path]:
        """
        Look for a POD attachment on the current invoice page.
        QBO invoices have an "Attachments" section at the bottom with file links.
        POD files are typically named like: *_pod.pdf
        Returns the file path if found and downloaded, None if no POD exists.
        """
        await self._ensure_browser()

        # Verify we're still on the invoice page (not navigated away)
        current = self._page.url
        if "invoice" not in current and "txnId" not in current:
            logger.warning("POD check: not on invoice page (%s), cannot check attachments", current)
            await self._debug("pod_check_WRONG_PAGE")
            return None

        # Scroll to bottom of the invoice page to ensure Attachments section is visible
        await self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(3)
        await self._debug("scrolled_for_pod_check")

        # Dump all links so we can see what attachments are on this page
        link_dump = await self._page.evaluate("""() => {
            const links = Array.from(document.querySelectorAll('a'));
            return links.map(a => ({
                text: (a.innerText || '').trim().substring(0, 100),
                href: (a.href || '').substring(0, 200),
                className: (a.className && typeof a.className === 'string') ? a.className.substring(0, 100) : '',
                visible: a.offsetParent !== null,
            })).filter(l => l.text.length > 0);
        }""")
        link_dump_path = DEBUG_DIR / f"{self._debug_step + 1:02d}_all_links_for_pod.json"
        link_dump_path.write_text(json.dumps(link_dump, indent=2), encoding="utf-8")
        logger.info("Dumped %d links for POD search", len(link_dump))

        pod_keywords = ["_pod", "pod.", "proof_of_delivery", "proof-of-delivery"]
        all_links = await self._page.query_selector_all("a")
        pod_link = None
        pod_name = ""

        for link in all_links:
            try:
                text = (await link.inner_text()).strip().lower()
            except Exception:
                continue
            if not text.endswith(".pdf"):
                continue
            if any(kw in text for kw in pod_keywords):
                pod_link = link
                pod_name = text
                break

        if not pod_link:
            await self._debug("pod_NOT_FOUND")
            logger.info("No POD attachment found on this invoice")
            return None

        logger.info("Found POD attachment link: %s", pod_name)
        await self._debug(f"pod_found_{pod_name}")
        return await self._download_attachment(pod_link, download_dir, "POD",
                                               original_filename=pod_name)

    # ------------------------------------------------------------------
    # Invoice sending — verify, check attachments, fill form, send
    # ------------------------------------------------------------------

    async def verify_invoice_details(
        self,
        expected_container: str,
        expected_amount: Optional[str] = None,
    ) -> dict:
        """Verify the currently-open invoice page matches expected data.

        Reads the page for container number (top-right badge) and amount,
        then compares against the expected values from the CSV.

        Returns: { verified: bool, reason: str|None, found_container: str|None, found_amount: str|None }
        """
        await self._ensure_browser()
        await self._debug("verify_invoice_start")

        result = {
            "verified": False,
            "reason": None,
            "found_container": None,
            "found_amount": None,
        }

        try:
            page_data = await self._page.evaluate("""() => {
                const data = { container: null, amount: null, invoiceNumber: null };

                // Container number — look for badge/pill elements in top-right area
                // QBO shows it like "ECMU7540543" in a highlight badge
                const allText = document.body.innerText || '';

                // Look for container number patterns (4 letters + 7 digits, e.g. CMAU6645700)
                const containerMatch = allText.match(/\\b([A-Z]{4}\\d{7})\\b/);
                if (containerMatch) data.container = containerMatch[1];

                // Amount — look for dollar amounts on the page
                // QBO shows the total prominently (e.g. "$3,451.00")
                const amountMatches = allText.match(/\\$([\\d,]+\\.\\d{2})/g);
                if (amountMatches && amountMatches.length > 0) {
                    // Take the most prominent/largest amount (likely the total)
                    data.amount = amountMatches[0];
                }

                // Invoice number from the page title / header
                const titleEl = document.querySelector('h1, [class*="title"], [class*="Title"]');
                if (titleEl) data.invoiceNumber = (titleEl.textContent || '').trim();

                return data;
            }""")

            result["found_container"] = page_data.get("container")
            result["found_amount"] = page_data.get("amount")

            # Compare container number
            if expected_container:
                found_cntr = (page_data.get("container") or "").upper().strip()
                expected_cntr = expected_container.upper().strip()
                if found_cntr and found_cntr != expected_cntr:
                    result["reason"] = f"Container mismatch: expected {expected_cntr}, found {found_cntr}"
                    logger.warning("Invoice verification FAILED: %s", result["reason"])
                    await self._debug("verify_MISMATCH_container")
                    return result
                elif not found_cntr:
                    logger.info("Could not extract container number from page — skipping container check")

            # Compare amount (optional, with tolerance for formatting)
            if expected_amount:
                found_amt = (page_data.get("amount") or "").replace("$", "").replace(",", "")
                expected_amt = expected_amount.replace("$", "").replace(",", "")
                try:
                    if found_amt and abs(float(found_amt) - float(expected_amt)) > 0.01:
                        result["reason"] = f"Amount mismatch: expected ${expected_amt}, found ${found_amt}"
                        logger.warning("Invoice verification FAILED: %s", result["reason"])
                        await self._debug("verify_MISMATCH_amount")
                        return result
                except ValueError:
                    logger.info("Could not parse amounts for comparison — skipping")

            result["verified"] = True
            logger.info("Invoice verified: container=%s, amount=%s",
                        result["found_container"], result["found_amount"])
            await self._debug("verify_SUCCESS")
            return result

        except Exception as e:
            result["reason"] = f"Verification error: {e}"
            logger.error("Invoice verification error: %s", e)
            await self._debug("verify_ERROR")
            return result

    async def check_attachments_on_page(self, required_docs: list[str]) -> dict:
        """Check which attachment types are present on the current invoice page.

        Scrolls to the attachments section, reads all attachment filenames,
        classifies them by type, and clicks "Select All" to include everything.

        Args:
            required_docs: list of required doc types, e.g. ["invoice", "pod", "bol"]

        Returns: { found: list[str], missing: list[str], allPresent: bool, attachments: list[dict] }
        """
        await self._ensure_browser()

        # Scroll to bottom to see attachments
        await self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1.5)

        # Read all attachment filenames from the page
        attachments_data = await self._page.evaluate("""() => {
            const attachments = [];
            const links = document.querySelectorAll('a');
            for (const a of links) {
                const text = (a.innerText || '').trim();
                if (text.toLowerCase().endsWith('.pdf')) {
                    attachments.push({ name: text, href: a.href || '' });
                }
            }
            return attachments;
        }""")

        # Classify each attachment by filename pattern
        doc_type_patterns = {
            "pod": ["_pod", "pod.", "proof_of_delivery", "proof-of-delivery"],
            "pol": ["_pol", "pol.", "proof_of_loading"],
            "bol": ["_bol", "bol.", "bill_of_lading", "bill-of-lading", "_bl.", "_bl_"],
            "pl": ["_pl.", "_pl_", "packing_list", "packing-list"],
            "do": ["_do.", "_do_", "_do2.", "delivery_order"],
            "invoice": ["_it.", "_it_", "invoice", "_inv."],
        }

        found_types = set()
        classified = []
        for att in attachments_data:
            name_lower = att["name"].lower()
            doc_type = "other"
            for dtype, patterns in doc_type_patterns.items():
                if any(p in name_lower for p in patterns):
                    doc_type = dtype
                    found_types.add(dtype)
                    break
            classified.append({"name": att["name"], "type": doc_type})

        # Also check for "Invoice PDF" text (QBO sometimes shows this as a non-link label)
        page_text = await self._page.evaluate("() => document.body.innerText || ''")
        if "Invoice PDF" in page_text:
            found_types.add("invoice")

        # Click "Select All" on the invoice EDIT page to ensure all attachments
        # are included in the email.  The checkboxes live HERE (not on the send form).
        select_result = await self._page.evaluate("""() => {
            // Strategy 1: data-testid
            const cb = document.querySelector('input[data-testid="attachments_checkbox"]');
            if (cb) {
                if (cb.checked) return { result: 'already_checked' };
                cb.click();
                return { result: 'select_all_clicked' };
            }

            // Strategy 2: checkbox near "Select All" text
            const labels = document.querySelectorAll('label, span, div, td');
            for (const lbl of labels) {
                const text = (lbl.textContent || '').trim();
                if (text === 'Select All') {
                    const nearCb = lbl.querySelector('input[type="checkbox"]') ||
                                   lbl.closest('div,td,label')?.querySelector('input[type="checkbox"]');
                    if (nearCb && !nearCb.checked) { nearCb.click(); return { result: 'near_cb_clicked' }; }
                    if (nearCb && nearCb.checked) return { result: 'already_checked' };
                    lbl.click();
                    return { result: 'label_clicked' };
                }
            }

            // Strategy 3: check all unchecked boxes near .pdf filenames
            const unchecked = document.querySelectorAll('input[type="checkbox"]:not(:checked)');
            let clicked = 0;
            for (const uc of unchecked) {
                const parent = uc.closest('div, li, tr, label');
                if (parent && (parent.textContent || '').toLowerCase().includes('.pdf')) {
                    uc.click();
                    clicked++;
                }
            }
            if (clicked > 0) return { result: 'individual_clicked', count: clicked };

            // Check if all are already checked
            const checked = document.querySelectorAll('input[type="checkbox"]:checked');
            const pdfChecked = Array.from(checked).filter(c => {
                const p = c.closest('div, li, tr, label');
                return p && (p.textContent || '').toLowerCase().includes('.pdf');
            });
            if (pdfChecked.length > 0) return { result: 'all_already_checked', count: pdfChecked.length };

            return { result: 'not_found' };
        }""")

        logger.info("Attachment Select All on edit page: %s", select_result)
        if isinstance(select_result, dict) and select_result.get("result") in (
            "select_all_clicked", "near_cb_clicked", "label_clicked", "individual_clicked"
        ):
            await asyncio.sleep(1)

        # Determine what's missing (supports OR groups like "bol/pol")
        found_list = sorted(found_types)
        missing_list = []
        for req in required_docs:
            parts = [p.strip() for p in req.split('/') if p.strip()]
            if not any(p in found_types for p in parts):
                missing_list.append(req)

        result = {
            "found": found_list,
            "missing": missing_list,
            "allPresent": len(missing_list) == 0,
            "attachments": classified,
        }

        logger.info("Attachment check: found=%s, missing=%s", found_list, missing_list)
        await self._debug("check_attachments_done")
        return result

    async def select_all_attachments(self) -> dict:
        """Click 'Select All' in the Attachments section on the invoice edit page.

        Standalone helper so it can be called for recovery (Back → re-select → retry).
        Returns the JS evaluation result dict.
        """
        await self._ensure_browser()

        # Scroll to bottom to see attachments
        await self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(2)

        select_result = await self._page.evaluate("""() => {
            const cb = document.querySelector('input[data-testid="attachments_checkbox"]');
            if (cb) {
                if (cb.checked) return { result: 'already_checked' };
                cb.click();
                return { result: 'select_all_clicked' };
            }
            const labels = document.querySelectorAll('label, span, div, td');
            for (const lbl of labels) {
                const text = (lbl.textContent || '').trim();
                if (text === 'Select All') {
                    const nearCb = lbl.querySelector('input[type="checkbox"]') ||
                                   lbl.closest('div,td,label')?.querySelector('input[type="checkbox"]');
                    if (nearCb && !nearCb.checked) { nearCb.click(); return { result: 'near_cb_clicked' }; }
                    if (nearCb && nearCb.checked) return { result: 'already_checked' };
                    lbl.click();
                    return { result: 'label_clicked' };
                }
            }
            const unchecked = document.querySelectorAll('input[type="checkbox"]:not(:checked)');
            let clicked = 0;
            for (const uc of unchecked) {
                const parent = uc.closest('div, li, tr, label');
                if (parent && (parent.textContent || '').toLowerCase().includes('.pdf')) {
                    uc.click();
                    clicked++;
                }
            }
            if (clicked > 0) return { result: 'individual_clicked', count: clicked };
            return { result: 'not_found' };
        }""")

        logger.info("select_all_attachments: %s", select_result)
        if isinstance(select_result, dict) and select_result.get("result") in (
            "select_all_clicked", "near_cb_clicked", "label_clicked", "individual_clicked"
        ):
            await asyncio.sleep(1)
        return select_result if isinstance(select_result, dict) else {"result": "unknown"}

    async def deselect_all_attachments(self) -> dict:
        """Uncheck all attachment checkboxes on the invoice edit page.

        Returns: { result: str, count: int }
        """
        await self._ensure_browser()

        # Scroll to bottom to see attachments
        await self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(2)

        deselect_result = await self._page.evaluate("""() => {
            // First try to uncheck the "Select All" master checkbox
            const masterCb = document.querySelector('input[data-testid="attachments_checkbox"]');
            if (masterCb && masterCb.checked) {
                masterCb.click();
                return { result: 'master_unchecked' };
            }

            // Fall back: uncheck all individual checkboxes near .pdf filenames
            const checked = document.querySelectorAll('input[type="checkbox"]:checked');
            let unchecked = 0;
            for (const cb of checked) {
                const parent = cb.closest('div, li, tr, label');
                if (parent && (parent.textContent || '').toLowerCase().includes('.pdf')) {
                    cb.click();
                    unchecked++;
                }
            }
            if (unchecked > 0) return { result: 'individually_unchecked', count: unchecked };
            return { result: 'none_checked' };
        }""")

        logger.info("deselect_all_attachments: %s", deselect_result)
        if isinstance(deselect_result, dict) and deselect_result.get("result") != "none_checked":
            await asyncio.sleep(1)
        return deselect_result if isinstance(deselect_result, dict) else {"result": "unknown"}

    async def select_specific_attachments(self, types: list[str]) -> dict:
        """Check only the attachment checkboxes matching the given doc types.

        Uses the same classification patterns as check_attachments_on_page().
        Call deselect_all_attachments() first to start from a clean state.

        Args:
            types: list of doc types to select, e.g. ["invoice"] or ["invoice", "pod"]

        Returns: { result: str, selected: list[str], count: int }
        """
        await self._ensure_browser()

        # Scroll to bottom to see attachments
        await self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(2)

        select_result = await self._page.evaluate("""(types) => {
            const patterns = {
                pod: ["_pod", "pod.", "proof_of_delivery", "proof-of-delivery"],
                pol: ["_pol", "pol.", "proof_of_loading"],
                bol: ["_bol", "bol.", "bill_of_lading", "bill-of-lading", "_bl.", "_bl_"],
                pl: ["_pl.", "_pl_", "packing_list", "packing-list"],
                do: ["_do.", "_do_", "_do2.", "delivery_order"],
                invoice: ["_it.", "_it_", "invoice", "_inv."],
            };

            function classifyName(name) {
                const lower = name.toLowerCase();
                for (const [dtype, pats] of Object.entries(patterns)) {
                    if (pats.some(p => lower.includes(p))) return dtype;
                }
                return "other";
            }

            const selected = [];
            const unchecked = document.querySelectorAll('input[type="checkbox"]:not(:checked)');
            for (const cb of unchecked) {
                const parent = cb.closest('div, li, tr, label');
                if (!parent) continue;
                const text = (parent.textContent || '').trim();
                // Find .pdf filename in the parent text
                const pdfMatch = text.match(/[\\w\\-\\.]+\\.pdf/i);
                if (!pdfMatch) continue;
                const docType = classifyName(pdfMatch[0]);
                if (types.includes(docType)) {
                    cb.click();
                    selected.push(pdfMatch[0]);
                }
            }

            // Also check already-checked boxes that match
            const checked = document.querySelectorAll('input[type="checkbox"]:checked');
            for (const cb of checked) {
                const parent = cb.closest('div, li, tr, label');
                if (!parent) continue;
                const text = (parent.textContent || '').trim();
                const pdfMatch = text.match(/[\\w\\-\\.]+\\.pdf/i);
                if (!pdfMatch) continue;
                const docType = classifyName(pdfMatch[0]);
                if (types.includes(docType)) {
                    selected.push(pdfMatch[0]);
                }
            }

            return { result: selected.length > 0 ? 'selected' : 'none_found', selected: selected, count: selected.length };
        }""", types)

        logger.info("select_specific_attachments(%s): %s", types, select_result)
        if isinstance(select_result, dict) and select_result.get("count", 0) > 0:
            await asyncio.sleep(1)
        return select_result if isinstance(select_result, dict) else {"result": "unknown"}

    async def click_back_from_send_form(self) -> bool:
        """Click the 'Back' link on the send form to return to the invoice edit page.

        Returns True if navigation back succeeded.
        """
        await self._ensure_browser()
        try:
            clicked = await self._page.evaluate("""() => {
                const candidates = document.querySelectorAll('a, button, [role="button"]');
                for (const el of candidates) {
                    const text = (el.textContent || '').trim().toLowerCase();
                    if (text === 'back') {
                        el.click();
                        return true;
                    }
                }
                return false;
            }""")
            if clicked:
                logger.info("Clicked 'Back' from send form")
                await self._page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(3)
                return True
            else:
                logger.warning("Could not find 'Back' link on send form")
                return False
        except Exception as e:
            logger.error("Failed to click Back: %s", e)
            return False

    async def click_review_and_send(self) -> bool:
        """Click the 'Review and send' button on the invoice detail page.

        Returns True if the send form loaded, False otherwise.
        """
        await self._ensure_browser()

        try:
            # Find and click "Review and send" button
            clicked = await self._page.evaluate("""() => {
                const candidates = document.querySelectorAll('a, button, [role="button"], input[type="button"]');
                for (const el of candidates) {
                    const text = (el.textContent || el.innerText || el.value || '').trim().toLowerCase();
                    if (text.includes('review and send') || text.includes('review & send')) {
                        el.click();
                        return { clicked: true, tag: el.tagName, text: text };
                    }
                }
                return null;
            }""")

            if not clicked:
                logger.error("Could not find 'Review and send' button")
                await self._debug("review_send_NOT_FOUND")
                return False

            logger.info("Clicked 'Review and send': %s", clicked)
            await self._page.wait_for_load_state("domcontentloaded")

            # Poll for Subject field instead of fixed 8s sleep (saves ~5s)
            for _ in range(24):  # 24 × 0.5s = 12s max wait
                has_subj = await self._page.evaluate("""() => {
                    const inputs = document.querySelectorAll('input, textarea');
                    for (const inp of inputs) {
                        const label = (inp.getAttribute('aria-label') || '').toLowerCase();
                        const name = (inp.getAttribute('name') || '').toLowerCase();
                        if (label.includes('subject') || name.includes('subject')) return true;
                    }
                    return false;
                }""")
                if has_subj:
                    break
                await asyncio.sleep(0.5)

            # Verify the send form loaded by checking for the Subject field
            has_subject = await self._page.evaluate("""() => {
                const inputs = document.querySelectorAll('input, textarea');
                for (const inp of inputs) {
                    const label = (inp.getAttribute('aria-label') || '').toLowerCase();
                    const name = (inp.getAttribute('name') || '').toLowerCase();
                    const placeholder = (inp.getAttribute('placeholder') || '').toLowerCase();
                    if (label.includes('subject') || name.includes('subject') || placeholder.includes('subject')) {
                        return true;
                    }
                }
                // Also check for "Subject" label text near an input
                const labels = document.querySelectorAll('label, td, th, span');
                for (const lbl of labels) {
                    if ((lbl.textContent || '').trim().toLowerCase() === 'subject') return true;
                }
                return false;
            }""")

            if has_subject:
                logger.info("Send form loaded — Subject field found")
                return True
            else:
                # Double-check: wait a bit more and retry — QBO React may still be rendering
                await asyncio.sleep(3)
                has_subject_retry = await self._page.evaluate("""() => {
                    const inputs = document.querySelectorAll('input, textarea');
                    for (const inp of inputs) {
                        const label = (inp.getAttribute('aria-label') || '').toLowerCase();
                        const name = (inp.getAttribute('name') || '').toLowerCase();
                        const placeholder = (inp.getAttribute('placeholder') || '').toLowerCase();
                        if (label.includes('subject') || name.includes('subject') || placeholder.includes('subject')) {
                            return true;
                        }
                    }
                    const labels = document.querySelectorAll('label, td, th, span');
                    for (const lbl of labels) {
                        if ((lbl.textContent || '').trim().toLowerCase() === 'subject') return true;
                    }
                    return false;
                }""")
                if has_subject_retry:
                    logger.info("Send form loaded on retry — Subject field found")
                    return True
                else:
                    logger.error("Send form did NOT load — Subject field not found after retry")
                    await self._debug("review_send_FAILED_no_subject")
                    return False

        except Exception as e:
            logger.error("Failed to click 'Review and send': %s", e)
            await self._debug("review_send_FAILED")
            return False

    async def fill_send_form(
        self,
        to_emails: list[str],
        cc_emails: list[str],
        subject: str,
        bcc_emails: list[str] | None = None,
        expected_attachment_count: int = 0,
    ) -> dict:
        """Fill in the email form on the QBO 'Review and Send' screen.

        Args:
            to_emails: recipient email addresses for the To field
            cc_emails: CC email addresses (always includes ar@ngltrans.net)
            subject: the formatted subject line
            bcc_emails: BCC email addresses (optional)
            expected_attachment_count: number of attachments found on the invoice detail
                page — used to wait for the send form's attachment list to fully render

        Returns: { filled: bool, toEmails: list, ccEmails: list, subject: str }
        """
        await self._ensure_browser()

        result = {
            "filled": False,
            "toEmails": to_emails,
            "ccEmails": cc_emails,
            "subject": subject,
        }

        try:
            filled = {"to": False, "cc": False, "bcc": False, "subject": False, "attachments": False}

            # --- Verify attachments appear on the send form ---
            # Attachment checkboxes were already clicked on the invoice EDIT page
            # (in check_attachments_on_page).  The send form only shows a flat list
            # of attached files — no checkboxes.  We verify the expected count here.
            MAX_ATT_VERIFY = 4
            ATT_VERIFY_WAIT = [0, 3, 4, 5]
            att_info = {"count": 0, "names": []}

            for verify_attempt in range(MAX_ATT_VERIFY):
                if verify_attempt > 0:
                    await asyncio.sleep(ATT_VERIFY_WAIT[verify_attempt])

                att_info = await self._page.evaluate("""() => {
                    const pdfTexts = Array.from(document.querySelectorAll('span, a, div, label'))
                        .filter(el => {
                            const text = (el.textContent || '').trim().toLowerCase();
                            return text.endsWith('.pdf') && text.length < 100;
                        });
                    return {
                        count: pdfTexts.length,
                        names: pdfTexts.map(el => (el.textContent || '').trim()).slice(0, 10),
                    };
                }""")

                logger.info("Send form attachment verify %d/%d: %d items %s",
                            verify_attempt + 1, MAX_ATT_VERIFY,
                            att_info["count"], att_info["names"])

                if expected_attachment_count == 0 or att_info["count"] >= expected_attachment_count:
                    filled["attachments"] = True
                    break
            else:
                logger.warning("Send form shows %d/%d expected attachments after %d checks",
                               att_info["count"], expected_attachment_count, MAX_ATT_VERIFY)
                filled["attachments"] = att_info["count"] > 0

            # --- Fill TO field (CRITICAL — abort if not found) ---
            to_input = await self._page.query_selector("#email_to")
            if not to_input:
                to_input = await self._page.query_selector('input[name="email_to"]')
            if not to_input:
                # Last attempt: wait for React to render and retry
                await asyncio.sleep(2)
                to_input = await self._page.query_selector("#email_to")
                if not to_input:
                    to_input = await self._page.query_selector('input[name="email_to"]')
            if to_input:
                await to_input.fill(", ".join(to_emails))
                filled["to"] = True
                logger.info("Filled To: %s", to_emails)
            else:
                logger.error("CRITICAL: Could not find To field — aborting form fill")
                await self._debug("fill_form_NO_TO_FIELD")
                result["filled"] = False
                return result

            # --- Click CC/BCC toggle to reveal CC and BCC fields ---
            try:
                cc_toggle = await self._page.query_selector("a.ccbcc-toggle")
                if not cc_toggle:
                    # Fallback: find by text content
                    cc_toggle = await self._page.evaluate("""() => {
                        const links = document.querySelectorAll('a, button, span');
                        for (const el of links) {
                            const text = (el.textContent || '').trim().toLowerCase();
                            if (text.includes('cc') && text.includes('bcc')) {
                                el.click();
                                return true;
                            }
                        }
                        return false;
                    }""")
                    if cc_toggle:
                        logger.info("Clicked CC/BCC toggle via JS fallback")
                else:
                    await cc_toggle.click()
                    logger.info("Clicked CC/BCC toggle")

                # Wait for CC input to appear after React re-renders
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.warning("Could not click CC/BCC toggle: %s", e)

            # --- Fill CC field ---
            if cc_emails:
                cc_input = await self._page.query_selector("#email_cc")
                if not cc_input:
                    cc_input = await self._page.query_selector('input[name="email_cc"]')
                if not cc_input:
                    # Wait a bit more for React to render
                    await asyncio.sleep(1)
                    cc_input = await self._page.query_selector("#email_cc")
                if cc_input:
                    await cc_input.fill(", ".join(cc_emails))
                    filled["cc"] = True
                    logger.info("Filled CC: %s", cc_emails)
                else:
                    logger.warning("Could not find CC field (#email_cc)")
            else:
                filled["cc"] = True  # No CC needed

            # --- Fill BCC field ---
            if bcc_emails:
                bcc_input = await self._page.query_selector("#email_bcc")
                if not bcc_input:
                    bcc_input = await self._page.query_selector('input[name="email_bcc"]')
                if bcc_input:
                    await bcc_input.fill(", ".join(bcc_emails))
                    filled["bcc"] = True
                    logger.info("Filled BCC: %s", bcc_emails)
                else:
                    logger.warning("Could not find BCC field (#email_bcc)")
            else:
                filled["bcc"] = True  # No BCC needed

            # --- Fill SUBJECT field ---
            subject_input = await self._page.query_selector("#email_subject")
            if not subject_input:
                subject_input = await self._page.query_selector('input[name="email_subject"]')
            if subject_input:
                await subject_input.fill(subject)
                filled["subject"] = True
                logger.info("Filled Subject: %s", subject)
            else:
                logger.warning("Could not find Subject field (#email_subject)")

            logger.info("Form fill results: %s", filled)
            await asyncio.sleep(0.5)
            await self._debug("fill_form_done")

            # Only mark as filled if the critical To field was populated
            result["filled"] = filled["to"]
            result["attachmentsFull"] = filled["attachments"]
            if not filled["to"]:
                logger.error("Form fill incomplete — To field was not filled")
            return result

        except Exception as e:
            logger.error("Failed to fill send form: %s", e)
            await self._debug("fill_form_FAILED")
            result["filled"] = False
            return result

    async def click_send_invoice(self) -> bool:
        """Click the green 'Send invoice' button (NOT 'Send and fund').

        Returns True if the send appeared to succeed, False otherwise.
        """
        await self._ensure_browser()

        # Remember current URL to detect navigation after send
        pre_send_url = self._page.url

        try:
            clicked = await self._page.evaluate("""() => {
                // Look for the green "Send invoice" button (bottom-right)
                // Avoid "Send and fund" button
                const buttons = document.querySelectorAll('button, a, [role="button"], input[type="submit"]');
                for (const btn of buttons) {
                    const text = (btn.textContent || btn.innerText || btn.value || '').trim().toLowerCase();
                    // Match "send invoice" but NOT "send and fund"
                    if (text === 'send invoice' || text === 'send') {
                        // Extra check: skip if it says "fund"
                        if (text.includes('fund')) continue;
                        btn.click();
                        return { clicked: true, tag: btn.tagName, text: text };
                    }
                }
                return null;
            }""")

            if not clicked:
                logger.error("Could not find 'Send invoice' button")
                await self._debug("send_button_NOT_FOUND")
                return False

            logger.info("Clicked 'Send invoice' button: %s", clicked)

            # Poll for URL change instead of fixed 8s sleep (saves ~5s)
            for _ in range(20):  # 20 × 0.5s = 10s max wait
                await asyncio.sleep(0.5)
                post_send_url = self._page.url
                if post_send_url != pre_send_url:
                    logger.info("Send successful — page navigated from %s to %s",
                               pre_send_url[:80], post_send_url[:80])
                    await self._debug("send_SUCCESS")
                    return True

            # Check for any error messages on the page
            errors = await self._page.evaluate("""() => {
                const errorEls = document.querySelectorAll('[class*="error" i], [class*="alert" i], [role="alert"]');
                return Array.from(errorEls).map(e => (e.textContent || '').trim()).filter(t => t.length > 0);
            }""")

            if errors:
                logger.error("Send form has errors: %s", errors)
                await self._debug("send_ERRORS_on_page")
                return False

            # No navigation but no errors — assume success (some QBO versions stay on page)
            logger.info("Send button clicked, no errors detected — assuming success")
            await self._debug("send_ASSUMED_SUCCESS")
            return True

        except Exception as e:
            logger.error("Failed to click Send invoice: %s", e)
            await self._debug("send_FAILED")
            return False

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------
    async def screenshot(self, path: Path) -> None:
        """Take a screenshot for debugging."""
        if self._page:
            await self._page.screenshot(path=str(path))

    @property
    def current_url(self) -> str:
        return self._page.url if self._page else ""
