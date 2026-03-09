"""TMSSearchMixin — container search, grid filtering, WO navigation."""

import asyncio
import logging
import time
from typing import Optional

from config import TMS_ACTION_DELAY_S

logger = logging.getLogger("ngl.tms_browser")


class TMSSearchMixin:
    """Grid filtering, detail page navigation, container search."""

    # ------------------------------------------------------------------
    # Stage 2: Sidebar Navigation to MAIN page
    # ------------------------------------------------------------------
    async def _navigate_to_main_page(self) -> bool:
        """Navigate to the MAIN page.

        First tries direct URL navigation, falls back to sidebar click.
        """
        try:
            # If already on MAIN, skip navigation
            if "/main/" in self._page.url:
                page_text = await self._page.evaluate(
                    "() => (document.body.innerText || '').substring(0, 500)"
                )
                if "MAIN" in page_text.upper():
                    logger.info("Already on MAIN page")
                    return True

            # Strategy 1: Direct URL navigation
            base = self._page.url.split("//")[0] + "//" + self._page.url.split("//")[1].split("/")[0]
            try:
                await self._page.goto(base + "/main/imp", wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(2)
                page_text = await self._page.evaluate(
                    "() => (document.body.innerText || '').substring(0, 500)"
                )
                if "MAIN" in page_text.upper():
                    logger.info("Navigated to MAIN page via direct URL")
                    await self._debug("main_page")
                    return True
                logger.info("Direct URL goto didn't reach MAIN — trying sidebar")
            except Exception as e:
                logger.info("Direct URL goto failed: %s — trying sidebar", e)

            # Strategy 2: Sidebar navigation (fallback)
            hamburger = await self._page.query_selector(
                'img[alt="Hamburger Icon"], img[alt*="ambuger"]'
            )
            if not hamburger:
                hamburger = await self._page.query_selector(
                    '.fixed.top-0 div.cursor-pointer'
                )
            if hamburger:
                await hamburger.click()
                await asyncio.sleep(0.8)
                logger.info("Clicked hamburger to expand sidebar")
            else:
                logger.warning("Hamburger icon not found — trying sidebar navigation anyway")

            await self._debug("sidebar_expanded")

            clicked = await self._page.evaluate("""() => {
                const candidates = document.querySelectorAll(
                    'a, div[role="button"], div.cursor-pointer, span, li, button'
                );
                for (const el of candidates) {
                    const text = (el.textContent || '').trim();
                    if (/^main$/i.test(text)) {
                        el.click();
                        return 'exact: ' + text;
                    }
                }

                const links = document.querySelectorAll('a[href*="/main"]');
                for (const link of links) {
                    const href = link.getAttribute('href') || '';
                    if (href.includes('/main/')) {
                        link.click();
                        return 'href: ' + href;
                    }
                }

                const walker = document.createTreeWalker(
                    document.body, NodeFilter.SHOW_TEXT, null
                );
                while (walker.nextNode()) {
                    const txt = walker.currentNode.textContent.trim();
                    if (/^main$/i.test(txt)) {
                        const parent = walker.currentNode.parentElement;
                        if (parent) {
                            const clickable = parent.closest(
                                'a, div[role="button"], div.cursor-pointer, li, button'
                            ) || parent;
                            clickable.click();
                            return 'text_node: ' + txt;
                        }
                    }
                }

                return null;
            }""")

            if clicked:
                logger.info("Clicked MAIN sidebar item via: %s", clicked)
                await asyncio.sleep(TMS_ACTION_DELAY_S + 1)
                await self._page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(TMS_ACTION_DELAY_S)
                await self._debug("main_page")

                page_text = await self._page.evaluate(
                    "() => (document.body.innerText || '').substring(0, 500)"
                )
                if "MAIN" in page_text.upper():
                    logger.info("Successfully navigated to MAIN page")
                    return True
                else:
                    logger.warning("Clicked sidebar but page may not be MAIN — text: %s", page_text[:100])
            else:
                logger.warning("Could not find 'Main' in sidebar — trying icon-by-icon fallback")

            # Fallback: iterate sidebar icons
            sidebar_sel = self._selectors.get("navigation", {}).get("sidebar_icons", "div.cursor-pointer")
            icons = await self._page.query_selector_all(sidebar_sel)
            logger.info("Found %d sidebar icons for fallback navigation", len(icons))

            for i, icon in enumerate(icons):
                try:
                    await icon.click()
                    await asyncio.sleep(TMS_ACTION_DELAY_S + 0.5)
                    heading = await self._page.evaluate("""() => {
                        const headings = document.querySelectorAll('h1, h2, h3, h4, h5, h6, .text-2xl, .text-xl');
                        for (const h of headings) {
                            const text = (h.textContent || '').trim().toUpperCase();
                            if (text) return text;
                        }
                        return '';
                    }""")
                    logger.info("Sidebar icon %d → heading: %s", i, heading)
                    if "MAIN" in heading.upper():
                        await self._debug("main_page")
                        logger.info("Found MAIN page at sidebar icon index %d", i)
                        return True
                except Exception:
                    continue

            logger.error("Could not navigate to MAIN page via any method")
            await self._debug("main_page_failed")
            return False

        except Exception as e:
            logger.error("Failed to navigate to MAIN page: %s", e)
            await self._debug("main_page_error")
            return False

    # ------------------------------------------------------------------
    # Stage 2b: Grid filtering
    # ------------------------------------------------------------------
    async def _filter_grid_by_container(self, container_number: str):
        """Type container number into the CONT# column filter and verify results.

        Returns StageResult with data containing 'wo_info' (first WO hit) and
        'typed_value' (what ended up in the filter input).
        """
        from . import StageResult

        t0 = time.monotonic()
        result = StageResult()

        # Find the CONT# column filter input
        cont_filter = await self._page.evaluate("""() => {
            const inputs = document.querySelectorAll('input');
            for (const inp of inputs) {
                const aria = (inp.getAttribute('aria-label') || '').toUpperCase();
                if (aria.includes('CONT') && aria.includes('FILTER')) {
                    const rect = inp.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        return {
                            x: rect.left + rect.width / 2,
                            y: rect.top + rect.height / 2,
                            aria: inp.getAttribute('aria-label'),
                            width: rect.width,
                        };
                    }
                }
            }
            return null;
        }""")

        if not cont_filter:
            ctx = await self._debug_rich("cont_filter_not_found")
            result.error = "CONT# column filter not found on MAIN page"
            result.elapsed_s = time.monotonic() - t0
            self._make_error("find_cont_filter", result.error, ctx)
            return result

        logger.info(
            "Found CONT# filter at (%d, %d) aria='%s'",
            cont_filter["x"], cont_filter["y"], cont_filter.get("aria"),
        )
        result.strategies_attempted.append("cont_filter_aria")
        result.strategy_used = "cont_filter_aria"

        # Type container number
        await self._page.mouse.click(cont_filter["x"], cont_filter["y"])
        await asyncio.sleep(0.3)
        await self._page.keyboard.press("Control+a")
        await asyncio.sleep(0.1)
        await self._page.keyboard.press("Backspace")
        await asyncio.sleep(0.2)
        await self._page.keyboard.type(container_number, delay=50)
        await asyncio.sleep(0.5)
        await self._page.keyboard.press("Enter")
        await asyncio.sleep(2)
        await self._debug_rich("cont_filter_typed")

        # Verify filter value
        typed_value = await self._page.evaluate("""() => {
            const inputs = document.querySelectorAll('input');
            for (const inp of inputs) {
                const aria = (inp.getAttribute('aria-label') || '').toUpperCase();
                if (aria.includes('CONT') && aria.includes('FILTER')) {
                    return inp.value;
                }
            }
            return null;
        }""")
        logger.info("CONT# filter value: '%s' (expected: '%s', match=%s)",
                    typed_value, container_number,
                    typed_value == container_number if typed_value else 'N/A')

        result.data["typed_value"] = typed_value

        # Find the first WO# in filtered results
        wo_info = await self._page.evaluate("""() => {
            const woPattern = /^[LPH][MXRN]\\d{7,}$/;

            const walker = document.createTreeWalker(
                document.body, NodeFilter.SHOW_TEXT, null
            );
            const woHits = [];
            while (walker.nextNode()) {
                const txt = walker.currentNode.textContent.trim();
                if (woPattern.test(txt)) {
                    const el = walker.currentNode.parentElement;
                    if (!el) continue;
                    const rect = el.getBoundingClientRect();
                    if (rect.height > 0 && rect.width > 0 && rect.top > 200) {
                        const cell = el.closest('[role="gridcell"]') || el.closest('.ag-cell') || el;
                        const cellHtml = cell ? cell.outerHTML.substring(0, 500) : '';
                        let href = '';
                        const closestA = el.closest('a');
                        if (closestA && closestA.href) {
                            href = closestA.getAttribute('href') || '';
                        } else if (cell) {
                            const link = cell.querySelector('a');
                            if (link) href = link.getAttribute('href') || '';
                        }
                        woHits.push({
                            text: txt,
                            x: rect.left + rect.width / 2,
                            y: rect.top + rect.height / 2,
                            top: rect.top,
                            tag: el.tagName,
                            cellHtml: cellHtml,
                            href: href,
                        });
                    }
                }
            }
            woHits.sort((a, b) => a.top - b.top);
            if (woHits.length > 0) return { hit: woHits[0], total: woHits.length };

            // Fallback: ARIA grid cells
            const woLoose = /[LPH][MXRN]\\d{7,}/;
            const rows = document.querySelectorAll('[role="row"]');
            for (const row of rows) {
                const cells = row.querySelectorAll('[role="gridcell"], td');
                for (const cell of cells) {
                    const cellText = (cell.textContent || '').trim();
                    if (woLoose.test(cellText)) {
                        const rect = cell.getBoundingClientRect();
                        if (rect.height > 0 && rect.width > 0 && rect.top > 200) {
                            const cellHtml = cell.outerHTML.substring(0, 500);
                            let href = '';
                            const link = cell.querySelector('a');
                            if (link) href = link.getAttribute('href') || '';
                            return {
                                hit: {
                                    text: cellText.match(woLoose)[0],
                                    x: rect.left + rect.width / 2,
                                    y: rect.top + rect.height / 2,
                                    top: rect.top,
                                    tag: 'ARIA',
                                    cellHtml: cellHtml,
                                    href: href,
                                },
                                total: 1,
                            };
                        }
                    }
                }
            }

            const noRows = document.querySelector('.ag-overlay-no-rows-center');
            if (noRows) return { hit: null, total: 0, noRows: true };

            return { hit: null, total: 0 };
        }""")

        result.data["wo_info"] = wo_info

        if not wo_info or not wo_info.get("hit"):
            ctx = await self._debug_rich("no_filtered_rows")
            no_rows_msg = " (AG Grid shows 'No Rows')" if wo_info and wo_info.get("noRows") else ""
            filter_note = ""
            if typed_value and typed_value != container_number:
                filter_note = f" Filter value mismatch: typed='{typed_value}' expected='{container_number}'."
            result.error = (
                f"No work orders found for container '{container_number}'{no_rows_msg}.{filter_note}"
                f" Verify the container number is correct and exists in TMS."
            )
            result.elapsed_s = time.monotonic() - t0
            self._make_error("filter_results", result.error, ctx)
            return result

        hit = wo_info["hit"]
        logger.info(
            "Filtered grid: %d WO(s) visible, first WO# %s at (%d, %d) tag=%s",
            wo_info["total"], hit["text"], hit["x"], hit["y"], hit["tag"],
        )
        if hit.get("href"):
            logger.info("WO# cell has href: %s", hit["href"])

        result.success = True
        result.elapsed_s = time.monotonic() - t0
        return result

    # ------------------------------------------------------------------
    # Stage 3: Work Order Detail Navigation
    # ------------------------------------------------------------------
    async def _has_detail_markers(self):
        """Check if page has detail page markers unique to the WO detail page.

        Uses tab labels that ONLY appear on the detail view.
        Excludes markers that also appear on the MAIN grid:
        - 'WO #' — grid column header
        - 'PULL OUT' — AG Grid column group header on MAIN page
        """
        return await self._page.evaluate("""() => {
            const text = (document.body.innerText || '').toUpperCase();
            // Tab labels unique to the detail page:
            const markers = ['DETAIL INFO', 'BILLING INFO'];
            for (const m of markers) {
                if (text.includes(m)) return m;
            }
            return null;
        }""")

    async def _check_navigated(self, url_before: str) -> bool:
        """Quick check: did we navigate away from the grid?"""
        if self._page.url != url_before:
            return True
        marker = await self._has_detail_markers()
        if marker:
            logger.info("Navigation detected via content marker '%s' (URL unchanged)", marker)
            return True
        return False

    async def _verify_detail_page(self, url_before: str) -> bool:
        """Strict checkpoint — verifies we're on a real detail page."""
        try:
            current_url = self._page.url
            url_changed = current_url != url_before
            marker = await self._has_detail_markers()

            # SPA may take time to render — retry for up to 6 seconds
            if not url_changed and not marker:
                for _ in range(12):
                    await asyncio.sleep(0.5)
                    current_url = self._page.url
                    url_changed = current_url != url_before
                    marker = await self._has_detail_markers()
                    if url_changed or marker:
                        break
                if not url_changed and not marker:
                    ctx = await self._debug_rich("verify_fail_url_unchanged")
                    self._make_error("verify_detail_page", "URL unchanged and no detail markers after WO# click", ctx)
                    return False

            if not marker:
                for _ in range(6):
                    await asyncio.sleep(0.5)
                    marker = await self._has_detail_markers()
                    if marker:
                        break
                if not marker:
                    ctx = await self._debug_rich("verify_fail_no_marker")
                    self._make_error(
                        "verify_detail_page",
                        f"No detail page markers found (url={current_url}, url_changed={url_changed})",
                        ctx,
                    )
                    return False

            ready_state = await self._page.evaluate("() => document.readyState")
            if ready_state != "complete":
                for _ in range(10):
                    await asyncio.sleep(0.5)
                    ready_state = await self._page.evaluate("() => document.readyState")
                    if ready_state == "complete":
                        break

            logger.info(
                "Detail page verified: url=%s url_changed=%s marker=%s readyState=%s",
                current_url, url_changed, marker, ready_state,
            )
            await self._debug_rich("detail_page_verified")
            return True

        except Exception as e:
            logger.error("_verify_detail_page exception: %s", e)
            await self._debug_rich("verify_detail_exception")
            return False

    # Map MAIN page URL segments to detail page route segments
    _MAIN_TO_DETAIL_TYPE = {
        "imp": "import",
        "exp": "export",
        "van": "van",
        "brokerage": "brokerage",
        "barechassis": "bare-chassis",
    }

    async def _navigate_to_work_order(self, wo_info: dict):
        """Navigate from MAIN grid to a work order detail page.

        Strategies tried in order (first success wins):
        1. direct_url        - Navigate to /bc-detail/detail-info/{type}/{woNo}
        2. playwright_click  - Playwright locator click on WO# text
        3. playwright_dblclick - Double-click on WO# text
        4. coord_dblclick    - Double-click at WO# coordinates

        Each strategy verifies navigation via URL change + detail page markers.
        """
        from . import StageResult

        t0 = time.monotonic()
        result = StageResult()
        hit = wo_info["hit"]
        wo_number = hit["text"]
        url_before = self._page.url

        # Log WO# cell HTML for diagnostics
        cell_html = hit.get("cellHtml", "")
        if cell_html:
            logger.info("[NAV] WO# cellHtml: %s", cell_html[:300])

        # Determine WO type from current MAIN page URL
        wo_type = "import"  # default
        for seg, detail_type in self._MAIN_TO_DETAIL_TYPE.items():
            if f"/main/{seg}" in url_before:
                wo_type = detail_type
                break
        logger.info("[NAV] WO# %s, type=%s, url_before=%s", wo_number, wo_type, url_before)

        # -- Strategy 1: direct_url --
        # Navigate directly to the detail page using discovered route pattern
        strategy = "direct_url"
        result.strategies_attempted.append(strategy)
        detail_url = f"/bc-detail/detail-info/{wo_type}/{wo_number}"
        logger.info("[NAV] Strategy %s: goto %s", strategy, detail_url)
        try:
            base_url = url_before.split("/main/")[0] if "/main/" in url_before else url_before.rsplit("/", 1)[0]
            full_url = base_url.rstrip("/") + detail_url
            await self._page.goto(full_url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(2)
            if await self._verify_detail_page(url_before):
                result.success = True
                result.strategy_used = strategy
                result.elapsed_s = time.monotonic() - t0
                logger.info("[NAV] SUCCESS via %s -> %s", strategy, self._page.url)
                return result
            else:
                logger.info("[NAV] %s: page loaded but detail markers not found", strategy)
        except Exception as e:
            logger.warning("[NAV] %s failed: %s", strategy, e)

        # -- Strategy 2: playwright_click --
        strategy = "playwright_click"
        result.strategies_attempted.append(strategy)
        logger.info("[NAV] Strategy %s: locator click on '%s'", strategy, wo_number)
        try:
            wo_locator = self._page.get_by_text(wo_number, exact=True)
            count = await wo_locator.count()
            for i in range(count):
                box = await wo_locator.nth(i).bounding_box()
                if box and box["y"] > 200:
                    await wo_locator.nth(i).click()
                    await asyncio.sleep(3)
                    await self._page.wait_for_load_state("domcontentloaded")
                    await asyncio.sleep(1)
                    if await self._verify_detail_page(url_before):
                        result.success = True
                        result.strategy_used = strategy
                        result.elapsed_s = time.monotonic() - t0
                        logger.info("[NAV] SUCCESS via %s -> %s", strategy, self._page.url)
                        return result
                    break
        except Exception as e:
            logger.warning("[NAV] %s failed: %s", strategy, e)

        # -- Strategy 3: playwright_dblclick --
        strategy = "playwright_dblclick"
        result.strategies_attempted.append(strategy)
        logger.info("[NAV] Strategy %s: locator dblclick on '%s'", strategy, wo_number)
        try:
            wo_locator = self._page.get_by_text(wo_number, exact=True)
            count = await wo_locator.count()
            for i in range(count):
                box = await wo_locator.nth(i).bounding_box()
                if box and box["y"] > 200:
                    await wo_locator.nth(i).dblclick()
                    await asyncio.sleep(3)
                    await self._page.wait_for_load_state("domcontentloaded")
                    await asyncio.sleep(1)
                    if await self._verify_detail_page(url_before):
                        result.success = True
                        result.strategy_used = strategy
                        result.elapsed_s = time.monotonic() - t0
                        logger.info("[NAV] SUCCESS via %s -> %s", strategy, self._page.url)
                        return result
                    break
        except Exception as e:
            logger.warning("[NAV] %s failed: %s", strategy, e)

        # -- Strategy 4: coord_dblclick --
        strategy = "coord_dblclick"
        result.strategies_attempted.append(strategy)
        logger.info("[NAV] Strategy %s: double-click at (%d, %d)", strategy, hit["x"], hit["y"])
        try:
            await self._page.mouse.dblclick(hit["x"], hit["y"])
            await asyncio.sleep(3)
            await self._page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(1)
            if await self._verify_detail_page(url_before):
                result.success = True
                result.strategy_used = strategy
                result.elapsed_s = time.monotonic() - t0
                logger.info("[NAV] SUCCESS via %s -> %s", strategy, self._page.url)
                return result
        except Exception as e:
            logger.warning("[NAV] %s failed: %s", strategy, e)

        # -- Strategy 5: href_goto --
        if hit.get("href"):
            strategy = "href_goto"
            result.strategies_attempted.append(strategy)
            href = hit["href"]
            if href.startswith("/"):
                base = url_before.split("/main/")[0] if "/main/" in url_before else url_before.rsplit("/", 1)[0]
                href = base.rstrip("/") + href
            logger.info("[NAV] Strategy %s: navigating via href -> %s", strategy, href)
            try:
                await self._page.goto(href, wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(2)
                if await self._verify_detail_page(url_before):
                    result.success = True
                    result.strategy_used = strategy
                    result.elapsed_s = time.monotonic() - t0
                    logger.info("[NAV] SUCCESS via %s -> %s", strategy, self._page.url)
                    return result
            except Exception as e:
                logger.warning("[NAV] %s failed: %s", strategy, e)

        # All strategies failed
        ctx = await self._debug_rich("all_nav_strategies_failed")
        result.error = (
            f"All navigation strategies failed for WO# {wo_number}. "
            f"URL stayed at {self._page.url}. Strategies tried: {result.strategies_attempted}"
        )
        result.elapsed_s = time.monotonic() - t0
        self._make_error("navigate_to_wo", result.error, ctx)
        return result

    # ------------------------------------------------------------------
    # Container Search (orchestrator for grid filter + WO nav)
    # ------------------------------------------------------------------
    async def search_container(self, container_number: str) -> Optional[str]:
        """Search TMS for a container number and navigate to its work order.

        Flow:
        1. Navigate to MAIN page via sidebar
        2. Type container number into the CONT# column filter (AG Grid)
        3. Wait for grid to filter down to matching rows
        4. Extract DO SENDER from grid data (stored in self._grid_do_sender)
        5. Try navigation strategies to open the work order detail page
        6. Verify navigation to detail page

        Returns the work order URL if found, None otherwise.
        Grid-extracted DO SENDER is stored in self._grid_do_sender.
        """
        raw = container_number
        container_number = container_number.strip()
        if raw != container_number:
            logger.info("TMS: container normalized %r → '%s'", raw, container_number)
        self._grid_do_sender = None

        await self._ensure_browser()

        vp = await self._page.evaluate(
            "() => `${window.innerWidth}x${window.innerHeight}`"
        )
        logger.info("TMS search starting: container='%s' (len=%d) viewport=%s",
                     container_number, len(container_number), vp)

        if not self.is_logged_in():
            logger.error("TMS not logged in — cannot search")
            return None

        try:
            # Step 1: Navigate to MAIN page
            on_main = await self._navigate_to_main_page()
            if not on_main:
                ctx = await self._debug_rich("main_page_fail")
                self._make_error("navigate_to_main", "Failed to reach MAIN page", ctx)
                return None

            await asyncio.sleep(2)

            # Step 2: Filter grid by container number
            filter_result = await self._filter_grid_by_container(container_number)
            if not filter_result.success:
                return None

            # Step 3: Extract DO SENDER from grid before navigation
            self._grid_do_sender = await self._extract_do_sender_from_grid()
            if self._grid_do_sender:
                logger.info("DO SENDER pre-extracted from grid: '%s'", self._grid_do_sender)

            # Step 4: Navigate to work order detail page
            wo_info = filter_result.data["wo_info"]
            nav_result = await self._navigate_to_work_order(wo_info)
            if not nav_result.success:
                grid_note = ""
                if self._grid_do_sender:
                    grid_note = f" (DO SENDER was extracted from grid: {self._grid_do_sender})"
                logger.warning(
                    "WO navigation failed for container %s%s. "
                    "Strategies tried: %s",
                    container_number, grid_note, nav_result.strategies_attempted,
                )
                return None

            # Step 5: Verify the correct container loaded
            # Check both visible text AND input field values (CONT# is often in an input)
            container_found = await self._page.evaluate("""(cont) => {
                const upper = cont.toUpperCase();
                // Check visible text
                if ((document.body.innerText || '').toUpperCase().includes(upper)) return true;
                // Check input values (CONT# field on detail page is an input)
                for (const inp of document.querySelectorAll('input, textarea')) {
                    if ((inp.value || '').toUpperCase().includes(upper)) return true;
                }
                return false;
            }""", container_number)
            if container_found:
                work_order_url = self._page.url
                logger.info("Navigated to work order for %s: %s", container_number, work_order_url)
                return work_order_url
            else:
                ctx = await self._debug_rich("container_not_found")
                self._make_error(
                    "verify_container",
                    f"Container {container_number} not found in page text or input values after navigation",
                    ctx,
                )
                return None

        except Exception as e:
            logger.error("TMS container search failed for %s: %s", container_number, e)
            await self._debug_rich("search_error")
            return None
