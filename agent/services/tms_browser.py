"""TMS portal browser automation — fetches PODs via Playwright."""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, BrowserContext, Page

from config import (
    TMS_URL,
    TMS_LOGIN_URL,
    TMS_PROFILE_DIR,
    TMS_DOWNLOADS_DIR,
    TMS_DEBUG_DIR,
    TMS_SELECTORS_FILE,
    TMS_ACTION_DELAY_S,
)

logger = logging.getLogger("ngl.tms_browser")


def _load_selectors() -> dict:
    """Load TMS DOM selectors from the JSON config file."""
    if TMS_SELECTORS_FILE.exists():
        with open(TMS_SELECTORS_FILE, "r") as f:
            return json.load(f)
    return {}


class TMSBrowser:
    """Controls a persistent Chrome browser to interact with the NGL TMS portal."""

    def __init__(self) -> None:
        self._playwright = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._selectors: dict = _load_selectors()
        self._debug_step = 0

    # ------------------------------------------------------------------
    # Debug
    # ------------------------------------------------------------------
    async def _debug(self, label: str) -> None:
        """Save a debug screenshot + HTML to agent/debug/tms/."""
        self._debug_step += 1
        prefix = f"{self._debug_step:02d}_{label}"
        try:
            if self._page:
                screenshot_path = TMS_DEBUG_DIR / f"{prefix}.png"
                await self._page.screenshot(path=str(screenshot_path), full_page=True)

                html = await self._page.evaluate("""() => {
                    return document.body ? document.body.outerHTML.substring(0, 50000) : '<empty>';
                }""")
                html_path = TMS_DEBUG_DIR / f"{prefix}.html"
                html_path.write_text(html, encoding="utf-8")

                logger.info("TMS DEBUG [%s]: saved → %s", label, prefix)
        except Exception as e:
            logger.warning("TMS debug capture failed for '%s': %s", label, e)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def init(self) -> None:
        """Launch Chrome with a persistent profile (separate from QBO)."""
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

        # Clean stale downloads
        for f in TMS_DOWNLOADS_DIR.glob("*"):
            try:
                f.unlink()
            except Exception:
                pass

        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            channel="chrome",
            user_data_dir=str(TMS_PROFILE_DIR),
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=ClearDataOnExit",
                "--hide-crash-restore-bubble",
                "--disable-session-crashed-bubble",
            ],
            viewport={"width": 1920, "height": 960},
            accept_downloads=True,
            downloads_path=str(TMS_DOWNLOADS_DIR),
        )

        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = await self._context.new_page()
        logger.info("TMS browser initialized (profile: %s)", TMS_PROFILE_DIR)

    async def _ensure_browser(self) -> None:
        """Re-launch Chrome if the browser/page has been closed or crashed."""
        needs_relaunch = False
        if not self._page or not self._context:
            needs_relaunch = True
        else:
            try:
                await self._page.evaluate("() => true")
            except Exception:
                needs_relaunch = True

        if needs_relaunch:
            logger.warning("TMS browser appears closed — relaunching...")
            await self.init()
            logger.info("TMS browser relaunched successfully")

    async def close(self) -> None:
        """Shut down — disconnect without closing Chrome so Google SSO persists."""
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        self._context = None
        self._page = None
        self._playwright = None
        logger.info("TMS browser closed")

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

    # ------------------------------------------------------------------
    # Keep-alive (prevents session timeout)
    # ------------------------------------------------------------------
    async def keep_alive(self) -> bool:
        """Perform a lightweight page interaction to prevent session timeout.

        Returns True if the session is still active, False if logged out.
        """
        try:
            if not self._page or not self._context:
                return False
            await self._page.evaluate("() => true")
            url = self.current_url.lower()
            if "sign-in" in url or not url:
                logger.warning("TMS session expired during keep-alive")
                return False
            # Tiny scroll to simulate activity — prevents idle timeout
            await self._page.evaluate("() => { window.scrollBy(0, 1); window.scrollBy(0, -1); }")
            return True
        except Exception as e:
            logger.warning("TMS keep-alive failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # Container Search
    # ------------------------------------------------------------------
    async def search_container(self, container_number: str) -> Optional[str]:
        """Search TMS for a container number and navigate to its work order.

        Returns the work order URL if found, None otherwise.
        """
        await self._ensure_browser()
        if not self.is_logged_in():
            logger.error("TMS not logged in — cannot search")
            return None

        try:
            # Navigate to TMS home/main page
            await self._page.goto(TMS_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(TMS_ACTION_DELAY_S)
            await self._debug("home_page")

            # Find the container search input
            search_input = await self._page.query_selector(
                "input[placeholder*='container' i], input[placeholder*='search' i], "
                "input[type='search'], input[name*='container' i], input[name*='search' i]"
            )

            if not search_input:
                # Try broader search — any prominent text input
                search_input = await self._page.query_selector(
                    "input[type='text']:not([type='hidden'])"
                )

            if not search_input:
                await self._debug("no_search_input")
                logger.error("Could not find container search input on TMS")
                return None

            # Clear and type the container number
            await search_input.click()
            await search_input.fill("")
            await search_input.type(container_number, delay=50)
            await asyncio.sleep(2)
            await self._debug("search_typed")

            # Wait for autocomplete dropdown and click the matching result
            autocomplete_clicked = await self._page.evaluate("""(containerNum) => {
                // Look for dropdown/autocomplete items
                const selectors = [
                    '[class*="autocomplete"] li',
                    '[class*="dropdown"] li',
                    '[class*="suggestion"]',
                    '[role="option"]',
                    '[role="listbox"] [role="option"]',
                    '.search-results li',
                    '.search-result',
                    'ul li a',
                ];
                for (const sel of selectors) {
                    const items = document.querySelectorAll(sel);
                    for (const item of items) {
                        const text = (item.textContent || '').toUpperCase();
                        if (text.includes(containerNum.toUpperCase())) {
                            item.click();
                            return { found: true, text: item.textContent.trim() };
                        }
                    }
                }
                return { found: false };
            }""", container_number)

            if not autocomplete_clicked.get("found"):
                # Try pressing Enter as fallback
                await search_input.press("Enter")
                await asyncio.sleep(3)
                await self._debug("search_enter_pressed")

                # Check if we landed on a results/work order page
                page_text = await self._page.evaluate("() => document.body.innerText || ''")
                if container_number.upper() not in page_text.upper():
                    await self._debug("container_not_found")
                    logger.warning("Container %s not found in TMS", container_number)
                    return None

            await self._page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(TMS_ACTION_DELAY_S)
            await self._debug("work_order_page")

            work_order_url = self._page.url
            logger.info("Navigated to work order for %s: %s", container_number, work_order_url)
            return work_order_url

        except Exception as e:
            logger.error("TMS container search failed for %s: %s", container_number, e)
            await self._debug("search_error")
            return None

    # ------------------------------------------------------------------
    # D/O Sender extraction
    # ------------------------------------------------------------------
    async def fetch_do_sender_email(self, container_number: str) -> Optional[str]:
        """Search TMS for a container and extract the D/O SENDER email from the work order.

        Returns the email string if found, None otherwise.
        """
        # Navigate to the work order for this container
        work_order_url = await self.search_container(container_number)
        if not work_order_url:
            return None

        try:
            # Make sure we're on the Detail Info tab (it's the default/first tab)
            await self._page.evaluate("""() => {
                const tabs = document.querySelectorAll('a, [role="tab"], button, span');
                for (const tab of tabs) {
                    const text = (tab.textContent || '').trim().toLowerCase();
                    if (text === 'detail info' || text === 'details') {
                        tab.click();
                        return true;
                    }
                }
                return false;
            }""")
            await asyncio.sleep(1)

            # Extract the D/O SENDER field value
            # From the TMS UI: it's a text input labeled "* DO SENDER"
            do_sender = await self._page.evaluate("""() => {
                // Strategy 1: Find label containing "DO SENDER" and get the associated input
                const labels = document.querySelectorAll('label, span, div, td');
                for (const label of labels) {
                    const text = (label.textContent || '').trim().toUpperCase();
                    if (text.includes('DO SENDER') || text.includes('D/O SENDER')) {
                        // Check sibling/next input
                        const parent = label.closest('div, td, tr, fieldset, .form-group');
                        if (parent) {
                            const input = parent.querySelector('input, textarea, select');
                            if (input && input.value) return input.value.trim();
                        }
                        // Try next sibling
                        let next = label.nextElementSibling;
                        while (next) {
                            if (next.tagName === 'INPUT' || next.tagName === 'TEXTAREA') {
                                if (next.value) return next.value.trim();
                            }
                            const inp = next.querySelector('input, textarea');
                            if (inp && inp.value) return inp.value.trim();
                            next = next.nextElementSibling;
                        }
                    }
                }

                // Strategy 2: Find any input whose preceding text contains "DO SENDER"
                const allInputs = document.querySelectorAll('input[type="text"], input:not([type])');
                for (const inp of allInputs) {
                    const val = (inp.value || '').trim();
                    if (!val || !val.includes('@')) continue;
                    // Check label association
                    const prevSib = inp.previousElementSibling;
                    if (prevSib) {
                        const prevText = (prevSib.textContent || '').toUpperCase();
                        if (prevText.includes('DO SENDER') || prevText.includes('D/O SENDER')) {
                            return val;
                        }
                    }
                    // Check parent label
                    const parentLabel = inp.closest('label');
                    if (parentLabel && parentLabel.textContent.toUpperCase().includes('DO SENDER')) {
                        return val;
                    }
                }

                // Strategy 3: Look for any input with an email value near "DO SENDER" text
                const bodyText = document.body.innerHTML;
                const doSenderMatch = bodyText.match(/DO\\s*SENDER[^<]*<[^>]*(?:input|textarea)[^>]*value=["']([^"']+@[^"']+)["']/i);
                if (doSenderMatch) return doSenderMatch[1].trim();

                return null;
            }""")

            if do_sender:
                logger.info("D/O sender found for %s: %s", container_number, do_sender)
                await self._debug("do_sender_found")
                return do_sender
            else:
                logger.info("No D/O sender email found on TMS for %s", container_number)
                await self._debug("do_sender_not_found")
                return None

        except Exception as e:
            logger.error("Failed to extract D/O sender for %s: %s", container_number, e)
            await self._debug("do_sender_error")
            return None

    # ------------------------------------------------------------------
    # Work Order Validation
    # ------------------------------------------------------------------
    def validate_work_order(self, work_order_text: str) -> dict:
        """Parse work order prefix to determine office and type.

        Prefix format: first letter = office, second letter = type
        L = Los Angeles, P = Phoenix, H = Houston
        M = Import, X = Export
        """
        result = {"valid": False, "office": None, "type": None}
        if not work_order_text or len(work_order_text) < 2:
            return result

        prefix = work_order_text.strip().upper()[:2]
        offices = {"L": "Los Angeles", "P": "Phoenix", "H": "Houston"}
        types = {"M": "Import", "X": "Export"}

        if prefix[0] in offices and prefix[1] in types:
            result["valid"] = True
            result["office"] = offices[prefix[0]]
            result["type"] = types[prefix[1]]

        return result

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------
    async def navigate_to_documents_tab(self) -> bool:
        """Click the Documents tab on the current work order page."""
        try:
            # Try clicking a Documents tab/link
            docs_clicked = await self._page.evaluate("""() => {
                const selectors = [
                    'a', 'button', '[role="tab"]', 'li a', 'nav a',
                    '[class*="tab"]', 'span',
                ];
                for (const sel of selectors) {
                    const items = document.querySelectorAll(sel);
                    for (const item of items) {
                        const text = (item.textContent || '').trim().toLowerCase();
                        if (text === 'documents' || text === 'docs' || text === 'files') {
                            item.click();
                            return true;
                        }
                    }
                }
                return false;
            }""")

            if not docs_clicked:
                await self._debug("no_documents_tab")
                logger.warning("Could not find Documents tab")
                return False

            await asyncio.sleep(TMS_ACTION_DELAY_S)
            await self._debug("documents_tab")
            logger.info("Navigated to Documents tab")
            return True

        except Exception as e:
            logger.error("Failed to navigate to Documents tab: %s", e)
            await self._debug("documents_tab_error")
            return False

    async def list_documents(self) -> list[dict]:
        """Scrape the documents list on the current page.

        Returns: [{ type: str, name: str, has_file: bool, row_index: int }]
        """
        try:
            docs = await self._page.evaluate("""() => {
                const results = [];
                // Try table rows first
                const rows = document.querySelectorAll('table tbody tr, [class*="document-row"], [class*="file-row"]');
                let index = 0;
                for (const row of rows) {
                    const cells = row.querySelectorAll('td');
                    const text = (row.textContent || '').trim();
                    if (!text) continue;

                    let name = '';
                    let docType = '';
                    let hasFile = false;

                    if (cells.length >= 2) {
                        docType = (cells[0].textContent || '').trim();
                        name = (cells[1].textContent || '').trim();
                    } else {
                        name = text;
                    }

                    // Check if there's a download link or file indicator
                    const links = row.querySelectorAll('a[href], button, [class*="download"]');
                    hasFile = links.length > 0 || text.toLowerCase().includes('.pdf');

                    // Detect document type from text
                    const upperText = text.toUpperCase();
                    if (upperText.includes('POD') || upperText.includes('PROOF OF DELIVERY')) {
                        docType = docType || 'POD';
                    } else if (upperText.includes('BOL') || upperText.includes('BILL OF LADING')) {
                        docType = docType || 'BOL';
                    } else if (upperText.includes('DO') || upperText.includes('DELIVERY ORDER')) {
                        docType = docType || 'DO';
                    } else if (upperText.includes('PL') || upperText.includes('PACKING LIST')) {
                        docType = docType || 'PL';
                    }

                    results.push({
                        type: docType,
                        name: name.substring(0, 200),
                        has_file: hasFile,
                        row_index: index,
                    });
                    index++;
                }
                return results;
            }""")

            logger.info("Found %d documents on TMS page", len(docs))
            return docs

        except Exception as e:
            logger.error("Failed to list TMS documents: %s", e)
            await self._debug("list_documents_error")
            return []

    async def download_document(self, row_index: int, download_dir: Path) -> Optional[Path]:
        """Click a document row to open it, then download the PDF.

        Returns the path to the downloaded file, or None on failure.
        """
        try:
            # Click the document row to open viewer
            row_clicked = await self._page.evaluate("""(rowIndex) => {
                const rows = document.querySelectorAll('table tbody tr, [class*="document-row"], [class*="file-row"]');
                let index = 0;
                for (const row of rows) {
                    if (!row.textContent.trim()) continue;
                    if (index === rowIndex) {
                        // Try clicking a link within the row first
                        const link = row.querySelector('a[href], button, [class*="view"], [class*="download"]');
                        if (link) {
                            link.click();
                        } else {
                            row.click();
                        }
                        return true;
                    }
                    index++;
                }
                return false;
            }""", row_index)

            if not row_clicked:
                logger.warning("Could not click document row %d", row_index)
                return None

            await asyncio.sleep(TMS_ACTION_DELAY_S)
            await self._debug("document_viewer")

            # Try to find and click a download button in the viewer
            async with self._page.expect_download(timeout=15000) as download_info:
                download_clicked = await self._page.evaluate("""() => {
                    const selectors = [
                        'a[download]',
                        'a[href*="download"]',
                        'button:has-text("Download")',
                        '[aria-label*="download" i]',
                        '[title*="download" i]',
                        'a[href*=".pdf"]',
                    ];
                    for (const sel of selectors) {
                        try {
                            const el = document.querySelector(sel);
                            if (el) {
                                el.click();
                                return { found: true, selector: sel };
                            }
                        } catch {}
                    }
                    return { found: false };
                }""")

                if not download_clicked.get("found"):
                    await self._debug("no_download_button")
                    logger.warning("Could not find download button in viewer")
                    return None

            download = await download_info.value
            suggested_name = download.suggested_filename or "document.pdf"
            save_path = download_dir / suggested_name
            await download.save_as(str(save_path))

            logger.info("TMS document downloaded: %s", save_path)
            return save_path

        except Exception as e:
            logger.error("TMS document download failed: %s", e)
            await self._debug("download_error")
            return None

    # ------------------------------------------------------------------
    # High-level: Fetch POD for a container
    # ------------------------------------------------------------------
    async def fetch_pod_for_container(
        self, container_number: str, download_dir: Path
    ) -> Optional[Path]:
        """End-to-end: search container → Documents tab → find POD → download.

        Returns path to the downloaded POD PDF, or None if not found.
        """
        # Step 1: Search for the container
        work_order_url = await self.search_container(container_number)
        if not work_order_url:
            return None

        # Step 2: Navigate to Documents tab
        docs_found = await self.navigate_to_documents_tab()
        if not docs_found:
            return None

        # Step 3: List documents and find POD
        docs = await self.list_documents()
        pod_row = None
        for doc in docs:
            doc_type = (doc.get("type") or "").upper()
            doc_name = (doc.get("name") or "").upper()
            if "POD" in doc_type or "POD" in doc_name or "PROOF OF DELIVERY" in doc_name:
                if doc.get("has_file"):
                    pod_row = doc
                    break

        if pod_row is None:
            logger.warning("No POD document found in TMS for container %s", container_number)
            await self._debug("pod_not_found")
            return None

        logger.info("POD found at row %d: %s", pod_row["row_index"], pod_row.get("name", ""))

        # Step 4: Download the POD
        pod_path = await self.download_document(pod_row["row_index"], download_dir)
        return pod_path
