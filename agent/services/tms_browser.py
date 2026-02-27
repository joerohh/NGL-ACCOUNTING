"""TMS portal browser automation — fetches PODs via Playwright."""

import asyncio
import json
import logging
import os
import shutil
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
    # Helpers
    # ------------------------------------------------------------------
    async def _find_cont_search_input(self):
        """Locate the CONT # search input on the Work Order detail page.

        The CONT # input sits near a "CONT #" label/button in the top-right
        area of the page.  We use a TreeWalker to find the label text, then
        walk sibling elements to find the adjacent <input>.
        """
        return await self._page.evaluate("""() => {
            // Strategy 1: TreeWalker — find text node "CONT #" then nearby input
            const walker = document.createTreeWalker(
                document.body, NodeFilter.SHOW_TEXT, null
            );
            while (walker.nextNode()) {
                const txt = walker.currentNode.textContent.trim().toUpperCase();
                if (txt === 'CONT #' || txt === 'CONT#') {
                    // Walk up to the nearest container element
                    let el = walker.currentNode.parentElement;
                    for (let i = 0; i < 5 && el; i++) {
                        // Check siblings for an input
                        const parent = el.parentElement;
                        if (!parent) break;
                        const input = parent.querySelector('input');
                        if (input) return true;  // found it — will use same logic to interact
                        el = parent;
                    }
                }
            }

            // Strategy 2: Any button/label containing "CONT" near an input
            const btns = document.querySelectorAll('button, label, span, div');
            for (const btn of btns) {
                const text = (btn.textContent || '').trim().toUpperCase();
                if (text === 'CONT #' || text === 'CONT#' || text === 'CONT') {
                    const parent = btn.parentElement;
                    if (parent) {
                        const input = parent.querySelector('input');
                        if (input) return true;
                    }
                }
            }
            return false;
        }""")

    async def _get_cont_input(self):
        """Return a Playwright ElementHandle for the CONT # input field."""
        return await self._page.evaluate_handle("""() => {
            // Find text node "CONT #" then nearby input
            const walker = document.createTreeWalker(
                document.body, NodeFilter.SHOW_TEXT, null
            );
            while (walker.nextNode()) {
                const txt = walker.currentNode.textContent.trim().toUpperCase();
                if (txt === 'CONT #' || txt === 'CONT#') {
                    let el = walker.currentNode.parentElement;
                    for (let i = 0; i < 5 && el; i++) {
                        const parent = el.parentElement;
                        if (!parent) break;
                        const input = parent.querySelector('input');
                        if (input) return input;
                        el = parent;
                    }
                }
            }
            // Fallback: button/label near input
            const btns = document.querySelectorAll('button, label, span, div');
            for (const btn of btns) {
                const text = (btn.textContent || '').trim().toUpperCase();
                if (text === 'CONT #' || text === 'CONT#' || text === 'CONT') {
                    const parent = btn.parentElement;
                    if (parent) {
                        const input = parent.querySelector('input');
                        if (input) return input;
                    }
                }
            }
            return null;
        }""")

    async def _click_tab(self, tab_name: str) -> bool:
        """Click a tab by its visible text (e.g. 'Detail Info', 'Document')."""
        clicked = await self._page.evaluate("""(tabName) => {
            // MUI tabs use role="tab" or button elements
            const candidates = document.querySelectorAll(
                '[role="tab"], button, a, span'
            );
            for (const el of candidates) {
                const text = (el.textContent || '').trim();
                if (text === tabName) {
                    el.click();
                    return true;
                }
            }
            // Case-insensitive fallback
            const lower = tabName.toLowerCase();
            for (const el of candidates) {
                const text = (el.textContent || '').trim().toLowerCase();
                if (text === lower) {
                    el.click();
                    return true;
                }
            }
            return false;
        }""", tab_name)
        if clicked:
            await asyncio.sleep(TMS_ACTION_DELAY_S)
        return clicked

    # ------------------------------------------------------------------
    # Container Search
    # ------------------------------------------------------------------
    async def search_container(self, container_number: str) -> Optional[str]:
        """Search TMS for a container number and navigate to its work order.

        Flow:
        1. Go to MAIN page (/order/impreg) — the work order list
        2. If CONT # input not visible, click first table row to open a detail page
        3. Find the CONT # input, type the container number, press Enter
        4. Verify the container loaded

        Returns the work order URL if found, None otherwise.
        """
        await self._ensure_browser()
        if not self.is_logged_in():
            logger.error("TMS not logged in — cannot search")
            return None

        try:
            main_path = self._selectors.get("navigation", {}).get("main_page_path", "/order/impreg")

            # Step 1: Navigate to MAIN page
            await self._page.goto(
                TMS_URL + main_path,
                wait_until="domcontentloaded",
                timeout=30000,
            )
            await asyncio.sleep(TMS_ACTION_DELAY_S)
            await self._debug("main_page")

            # Step 2: Check if CONT # input is already visible
            has_cont_input = await self._find_cont_search_input()

            if not has_cont_input:
                # Need to click a table row to open a work order detail page
                logger.info("CONT # input not found — clicking first table row")
                row_sel = self._selectors.get("work_order", {}).get("table_rows", "table tbody tr")
                first_row = await self._page.query_selector(row_sel)
                if not first_row:
                    await self._debug("no_table_rows")
                    logger.error("No work order rows found on MAIN page")
                    return None

                await first_row.click()
                await asyncio.sleep(TMS_ACTION_DELAY_S + 1)
                await self._debug("clicked_first_row")

                # Now check again for the CONT # input
                has_cont_input = await self._find_cont_search_input()
                if not has_cont_input:
                    await self._debug("still_no_cont_input")
                    logger.error("CONT # input not found even after opening a work order")
                    return None

            # Step 3: Get the input handle, clear, type, and press Enter
            cont_input = await self._get_cont_input()
            if not cont_input:
                await self._debug("cont_input_handle_fail")
                logger.error("Could not get handle for CONT # input")
                return None

            # Use Playwright methods on the JSHandle
            await cont_input.as_element().click(click_count=3)  # select all
            await cont_input.as_element().fill("")
            await cont_input.as_element().type(container_number, delay=50)
            await self._debug("cont_typed")

            await cont_input.as_element().press("Enter")
            await asyncio.sleep(TMS_ACTION_DELAY_S + 1)
            await self._page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(TMS_ACTION_DELAY_S)
            await self._debug("after_cont_search")

            # Step 4: Verify container loaded — check page text
            page_text = await self._page.evaluate("() => document.body.innerText || ''")
            if container_number.upper() in page_text.upper():
                work_order_url = self._page.url
                logger.info("Navigated to work order for %s: %s", container_number, work_order_url)
                return work_order_url
            else:
                await self._debug("container_not_found")
                logger.warning("Container %s not found in TMS after search", container_number)
                return None

        except Exception as e:
            logger.error("TMS container search failed for %s: %s", container_number, e)
            await self._debug("search_error")
            return None

    # ------------------------------------------------------------------
    # D/O Sender extraction
    # ------------------------------------------------------------------
    async def fetch_do_sender_email(self, container_number: str) -> Optional[str]:
        """Search TMS for a container and extract the D/O SENDER email.

        Flow:
        1. search_container() to navigate to the WO
        2. Click "Detail Info" tab
        3. TreeWalker + MUI fallbacks to read the DO SENDER input value

        Returns the email string if found, None otherwise.
        """
        work_order_url = await self.search_container(container_number)
        if not work_order_url:
            return None

        try:
            do_sender_label = self._selectors.get("work_order", {}).get("do_sender_label", "DO SENDER")
            detail_tab = self._selectors.get("work_order", {}).get("tabs", {}).get("detail_info", "Detail Info")
            await self._click_tab(detail_tab)
            await self._debug("detail_info_tab")

            do_sender = await self._page.evaluate("""(labelText) => {
                const upperLabel = labelText.toUpperCase();

                // Strategy 1: TreeWalker — find "DO SENDER" text, walk up to find input
                const walker = document.createTreeWalker(
                    document.body, NodeFilter.SHOW_TEXT, null
                );
                while (walker.nextNode()) {
                    const txt = walker.currentNode.textContent.trim().toUpperCase();
                    if (txt.includes(upperLabel)) {
                        let el = walker.currentNode.parentElement;
                        for (let i = 0; i < 6 && el; i++) {
                            const inputs = el.querySelectorAll('input');
                            for (const inp of inputs) {
                                const val = (inp.value || '').trim();
                                if (val && val.includes('@')) return val;
                            }
                            el = el.parentElement;
                        }
                    }
                }

                // Strategy 2: MUI FormControl label association
                const labels = document.querySelectorAll('label, .MuiInputLabel-root, .MuiFormLabel-root');
                for (const lbl of labels) {
                    const text = (lbl.textContent || '').toUpperCase();
                    if (text.includes(upperLabel)) {
                        const formCtrl = lbl.closest('.MuiFormControl-root');
                        if (formCtrl) {
                            const inp = formCtrl.querySelector('input');
                            if (inp) {
                                const val = (inp.value || '').trim();
                                if (val && val.includes('@')) return val;
                                if (val) return val;
                            }
                        }
                        const parent = lbl.parentElement;
                        if (parent) {
                            const inp = parent.querySelector('input');
                            if (inp && inp.value) return inp.value.trim();
                        }
                    }
                }

                // Strategy 3: Last resort — single non-NGL email input on page
                const allInputs = document.querySelectorAll('input');
                const emailInputs = [];
                for (const inp of allInputs) {
                    const val = (inp.value || '').trim();
                    if (val && val.includes('@') && !val.includes('ngltrans.net')) {
                        emailInputs.push(val);
                    }
                }
                if (emailInputs.length === 1) return emailInputs[0];

                return null;
            }""", do_sender_label)

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
        """Click the 'Document' tab on the current work order page."""
        try:
            tab_name = self._selectors.get("work_order", {}).get("tabs", {}).get("document", "Document")
            clicked = await self._click_tab(tab_name)
            if not clicked:
                await self._debug("no_document_tab")
                logger.warning("Could not find Document tab")
                return False

            await self._debug("document_tab")
            logger.info("Navigated to Document tab")
            return True

        except Exception as e:
            logger.error("Failed to navigate to Document tab: %s", e)
            await self._debug("document_tab_error")
            return False

    async def list_documents(self) -> list[dict]:
        """Parse the fixed-row document table on the Document tab.

        TMS has a fixed set of doc type rows: DO, POD, POL, BL, IT, ITE,
        CF, CFS, WAREHOUSE-BL, WAREHOUSE-INBOUND.  Each row has columns:
        DATE, DOCUMENT, UPDATED BY, VERIF, CK, BROWSE, SAVE.

        The BROWSE column contains either a "Browse" button (no file uploaded)
        or a filename link (file exists and can be downloaded).

        Returns: [{ type, name, has_file, row_index, filename }]
        """
        try:
            fixed_types = self._selectors.get("documents", {}).get(
                "fixed_doc_types",
                ["DO", "POD", "POL", "BL", "IT", "ITE", "CF", "CFS", "WAREHOUSE-BL", "WAREHOUSE-INBOUND"],
            )
            browse_text = self._selectors.get("documents", {}).get("browse_button_text", "Browse")

            docs = await self._page.evaluate("""(args) => {
                const { fixedTypes, browseText } = args;
                const results = [];
                const rows = document.querySelectorAll('table tbody tr');
                let index = 0;

                for (const row of rows) {
                    const cells = row.querySelectorAll('td');
                    if (cells.length < 2) { index++; continue; }

                    // First cell typically has the doc type text (DOCUMENT column)
                    // Try to match against known fixed doc types
                    const rowText = (row.textContent || '').trim().toUpperCase();
                    let docType = '';
                    for (const ft of fixedTypes) {
                        // Check if this row's text starts with or contains the doc type
                        if (rowText.includes(ft)) {
                            docType = ft;
                            break;
                        }
                    }

                    // Check the BROWSE column for a filename (not just the "Browse" button)
                    let hasFile = false;
                    let filename = '';
                    // Look for links/anchors in the row that aren't the Browse button
                    const anchors = row.querySelectorAll('a');
                    for (const a of anchors) {
                        const aText = (a.textContent || '').trim();
                        // A filename link is any link that ISN'T the "Browse" button text
                        if (aText && aText !== browseText && aText.length > 2) {
                            hasFile = true;
                            filename = aText;
                            break;
                        }
                    }

                    // Also check for file-like text in cells (e.g. "pod_document.pdf")
                    if (!hasFile) {
                        for (const cell of cells) {
                            const cellText = (cell.textContent || '').trim();
                            if (cellText.match(/\\.[a-zA-Z]{2,4}$/) && cellText !== browseText) {
                                hasFile = true;
                                filename = cellText;
                                break;
                            }
                        }
                    }

                    if (docType) {
                        results.push({
                            type: docType,
                            name: filename || docType,
                            has_file: hasFile,
                            row_index: index,
                            filename: filename,
                        });
                    }
                    index++;
                }
                return results;
            }""", {"fixedTypes": fixed_types, "browseText": browse_text})

            logger.info("Found %d document rows on TMS page", len(docs))
            for doc in docs:
                logger.info("  %s: has_file=%s, filename=%s", doc["type"], doc["has_file"], doc.get("filename", ""))
            return docs

        except Exception as e:
            logger.error("Failed to list TMS documents: %s", e)
            await self._debug("list_documents_error")
            return []

    async def download_document(self, row_index: int, download_dir: Path) -> Optional[Path]:
        """Download a document from the Document tab by row index.

        Three-tier strategy (matching QBO browser pattern):
        A) Direct fetch via href — fastest, no UI interaction
        B) Click filename link → check for new tab → fetch from tab URL
        C) Check TMS_DOWNLOADS_DIR for browser-downloaded file

        Returns the path to the downloaded file, or None on failure.
        """
        try:
            # Get the filename link's href and text from the target row
            link_info = await self._page.evaluate("""(rowIndex) => {
                const rows = document.querySelectorAll('table tbody tr');
                if (rowIndex >= rows.length) return null;
                const row = rows[rowIndex];
                const anchors = row.querySelectorAll('a');
                for (const a of anchors) {
                    const text = (a.textContent || '').trim();
                    if (text && text !== 'Browse' && text.length > 2) {
                        return {
                            href: a.href || '',
                            text: text,
                            target: a.target || '',
                        };
                    }
                }
                return null;
            }""", row_index)

            if not link_info:
                logger.warning("No downloadable file link at row %d", row_index)
                await self._debug("no_file_link")
                return None

            filename = link_info.get("text", "document.pdf")
            if not filename.lower().endswith(".pdf"):
                filename += ".pdf"
            href = link_info.get("href", "")

            # ── Method A: Direct fetch via JS fetch() ────────────────────
            if href:
                logger.info("Download Method A: direct fetch via href for %s", filename)
                try:
                    content = await self._page.evaluate("""async (url) => {
                        try {
                            const resp = await fetch(url, { credentials: 'include' });
                            if (!resp.ok) return { error: resp.status };
                            const buf = await resp.arrayBuffer();
                            return { data: Array.from(new Uint8Array(buf)) };
                        } catch (e) {
                            return { error: e.message };
                        }
                    }""", href)

                    if isinstance(content, dict) and "data" in content:
                        data = bytes(content["data"])
                        if len(data) >= 5 and data[:5] == b'%PDF-':
                            save_path = download_dir / filename
                            save_path.write_bytes(data)
                            logger.info("TMS document downloaded (Method A): %s", save_path)
                            return save_path
                        else:
                            logger.warning("Method A: response is not a PDF (first bytes: %s)", data[:20])
                    else:
                        logger.warning("Method A failed: %s", content.get("error", "unknown"))
                except Exception as e:
                    logger.warning("Method A exception: %s", e)

            # ── Method B: Click link → new tab → fetch from tab URL ──────
            logger.info("Download Method B: clicking file link for %s", filename)
            try:
                async with self._context.expect_page(timeout=10000) as new_page_info:
                    await self._page.evaluate("""(rowIndex) => {
                        const rows = document.querySelectorAll('table tbody tr');
                        if (rowIndex >= rows.length) return;
                        const row = rows[rowIndex];
                        const anchors = row.querySelectorAll('a');
                        for (const a of anchors) {
                            const text = (a.textContent || '').trim();
                            if (text && text !== 'Browse' && text.length > 2) {
                                a.click();
                                return;
                            }
                        }
                    }""", row_index)

                new_page = await new_page_info.value
                await asyncio.sleep(3)
                tab_url = new_page.url

                if tab_url and "blob:" not in tab_url:
                    # Fetch PDF from the new tab's URL
                    content = await new_page.evaluate("""async () => {
                        try {
                            const resp = await fetch(window.location.href, { credentials: 'include' });
                            if (!resp.ok) return { error: resp.status };
                            const buf = await resp.arrayBuffer();
                            return { data: Array.from(new Uint8Array(buf)) };
                        } catch (e) {
                            return { error: e.message };
                        }
                    }""")

                    if isinstance(content, dict) and "data" in content:
                        data = bytes(content["data"])
                        if len(data) >= 5 and data[:5] == b'%PDF-':
                            save_path = download_dir / filename
                            save_path.write_bytes(data)
                            logger.info("TMS document downloaded (Method B): %s", save_path)
                            await new_page.close()
                            return save_path

                await new_page.close()
            except Exception as e:
                logger.warning("Method B failed: %s", e)

            # ── Method C: Check browser downloads directory ──────────────
            logger.info("Download Method C: checking TMS downloads dir")
            await asyncio.sleep(3)  # give browser time to finish download
            for f in sorted(TMS_DOWNLOADS_DIR.glob("*"), key=os.path.getmtime, reverse=True):
                if f.is_file() and f.stat().st_size > 100:
                    save_path = download_dir / filename
                    shutil.copy2(str(f), str(save_path))
                    logger.info("TMS document downloaded (Method C): %s", save_path)
                    return save_path

            logger.error("All download methods failed for row %d", row_index)
            await self._debug("download_all_failed")
            return None

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
        The return value None with a logged warning distinguishes between:
        - Container not found in TMS
        - POD row exists but no file uploaded (flagged clearly)
        - POD file exists and download failed
        """
        # Step 1: Search for the container
        work_order_url = await self.search_container(container_number)
        if not work_order_url:
            return None

        # Step 2: Navigate to Document tab
        docs_found = await self.navigate_to_documents_tab()
        if not docs_found:
            return None

        # Step 3: List documents and find POD
        docs = await self.list_documents()
        pod_row = None
        for doc in docs:
            if doc.get("type") == "POD":  # exact match — fixed rows
                pod_row = doc
                break

        if pod_row is None:
            logger.warning("No POD row found in document table for %s", container_number)
            await self._debug("pod_row_missing")
            return None

        if not pod_row.get("has_file"):
            logger.warning(
                "POD row exists for %s but NO DOCUMENT UPLOADED — "
                "the POD has not been uploaded to TMS yet",
                container_number,
            )
            await self._debug("pod_no_file_uploaded")
            return None

        logger.info(
            "POD found for %s at row %d: %s",
            container_number, pod_row["row_index"], pod_row.get("filename", ""),
        )

        # Step 4: Download the POD
        pod_path = await self.download_document(pod_row["row_index"], download_dir)
        return pod_path
