"""QBO Search mixin — invoice search via global search bar."""

import asyncio
import json
import logging

from config import DEBUG_DIR, QBO_BASE_URL

logger = logging.getLogger("ngl.qbo_browser")


class QBOSearchMixin:
    """Search for invoices in QBO via the global search bar."""

    async def search_invoice(self, invoice_number: str, *, page=None):
        """
        Search QBO for an invoice by number using the global search bar.

        Flow: Type invoice # → Press Enter → lands on Search Results page
        (table with DATE, TYPE, REF NO, CONTACT, etc.) → click the invoice row
        in that table → lands on actual Invoice Detail page.

        Returns the invoice detail page URL if found, None otherwise.

        Args:
            page: Optional Playwright page for parallel workers. Uses main page if None.
        """
        await self._ensure_page(page)
        p = page or self._page

        # Reset step counter for each new invoice search (only for main page)
        if p == self._page:
            self._debug_step = 0

        # Find the search bar — reuse current page if already on QBO, else load homepage
        search_input_sel = (
            '#global-search-input, '
            'input[placeholder*="search" i], '
            'input[placeholder*="navigate" i], '
            "input[data-id='global-search-input'], "
            "input[data-testid='global-search-input']"
        )

        search_input = None
        current_url = p.url if p else ""

        if QBO_BASE_URL in current_url:
            # Already on QBO — try to grab search bar without reloading (saves ~12s)
            try:
                search_input = await p.wait_for_selector(search_input_sel, timeout=3000)
                logger.info("Reusing existing QBO search bar (skipped homepage reload)")
            except Exception:
                search_input = None  # Fall through to full navigation

        if not search_input:
            # Full homepage navigation (first invoice, or search bar not found)
            # Try up to 2 times — QBO SPA can leave the page in a transient
            # state after a previous send, causing the search bar to be
            # temporarily invisible during React re-render.
            for attempt in range(2):
                await p.goto(QBO_BASE_URL, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(8)
                try:
                    search_input = await p.wait_for_selector(search_input_sel, timeout=15000)
                    break
                except Exception as e:
                    if attempt == 0:
                        logger.warning("Search bar not found on attempt 1 — reloading page: %s", e)
                        continue
                    logger.error("Could not find QBO search bar after 2 attempts: %s", e)
                    # Last resort: try state='attached' (visible check may be blocked by overlay)
                    try:
                        search_input = await p.wait_for_selector(search_input_sel, state="attached", timeout=5000)
                        logger.info("Found search bar via state='attached' fallback")
                    except Exception:
                        await self._debug(f"search_bar_NOT_FOUND_{invoice_number}", page=p)
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
            await self._debug(f"search_type_FAILED_{invoice_number}", page=p)
            return None

        # Press Enter to go to the full Search Results page
        # (The dropdown "quick results" just navigates to this same page anyway)
        try:
            search_input = await p.query_selector(search_input_sel)
            if search_input:
                await search_input.press("Enter")
            await p.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(4)  # Brief initial wait for page shell
        except Exception as e:
            logger.error("Failed to load search results page for %s: %s", invoice_number, e)
            await self._debug(f"search_page_FAILED_{invoice_number}", page=p)
            return None

        # Poll for data rows to appear (QBO loads table data asynchronously).
        # Instead of a fixed wait, check repeatedly until real rows show up.
        async def _wait_for_search_data(max_wait=20, poll_interval=2):
            """Poll until the search results table has actual data rows (not skeletons)."""
            elapsed = 0
            while elapsed < max_wait:
                row_count = await p.evaluate("""() => {
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
            exact_clicked = await p.evaluate("""() => {
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

        logger.info("Search results page for %s: %s (data_loaded=%s)", invoice_number, p.url, data_loaded)

        # Now find and click the invoice row in the results table.
        # Try up to 2 times with a short wait between — rows may still be rendering.
        async def _find_and_click_row():
            """Look for the invoice number in table cells and click its row."""
            return await p.evaluate("""(invoiceNum) => {
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
                await p.wait_for_load_state("domcontentloaded")

                # Wait for invoice detail page — detect "Review and send" button
                # instead of fixed 12s sleep (saves ~7s, timeout 15s for safety)
                for _ in range(30):  # 30 × 0.5s = 15s max wait
                    found_btn = await p.evaluate("""() => {
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

                await self._debug(f"invoice_detail_page_{invoice_number}", page=p)

                url = p.url
                # Verify we're actually on an invoice detail page (has txnId in URL)
                if "invoice" in url or "txnId" in url:
                    logger.info("Invoice detail page loaded: %s", url)
                    return url
                else:
                    logger.warning("After clicking row, landed on unexpected page: %s", url)
                    await self._debug(f"unexpected_page_{invoice_number}", page=p)
                    # Still return the URL — might be usable
                    return url
            else:
                logger.warning("Could not find invoice %s row in search results table", invoice_number)
                await self._debug(f"row_NOT_FOUND_{invoice_number}", page=p)

                # Check if the invoice number appears anywhere on the page (DOM issue vs truly not found)
                page_text_check = await p.evaluate("""(invoiceNum) => {
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
                page_info = await p.evaluate("""() => {
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
            await self._debug(f"row_click_FAILED_{invoice_number}", page=p)

        return None
