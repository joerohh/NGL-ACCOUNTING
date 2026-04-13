"""TMSDocumentsMixin — DO SENDER extraction, document tab, document listing."""

import asyncio
import logging
import re
from typing import Optional

from config import TMS_ACTION_DELAY_S

logger = logging.getLogger("ngl.tms_browser")


class TMSDocumentsMixin:
    """DO SENDER extraction, document tab navigation, document listing."""

    # ------------------------------------------------------------------
    # Stage 1: Grid DO SENDER Extraction (bypass navigation)
    # ------------------------------------------------------------------
    async def _extract_do_sender_from_grid(self) -> Optional[str]:
        """Extract DO SENDER email directly from the AG Grid's filtered row.

        Must be called after the grid has been filtered to show the target container.
        Tries two approaches:
        1. AG Grid API — reads all row data regardless of column visibility
        2. DOM scroll — scrolls grid to DO SENDER column and reads the cell text

        Returns the validated email string, or None.
        """
        try:
            # ── Approach 1: AG Grid API ──
            api_result = await self._page.evaluate("""() => {
                const out = { data: null, method: null, debug: [] };

                const sels = ['[class*="ag-theme"]', '.ag-root-wrapper', '.ag-root'];
                let gridEl = null;
                for (const s of sels) {
                    gridEl = document.querySelector(s);
                    if (gridEl) { out.debug.push('grid: ' + s); break; }
                }
                if (!gridEl) { out.debug.push('no grid element'); return out; }

                const props = [];
                try {
                    for (const k of Object.getOwnPropertyNames(gridEl)) {
                        if (k.startsWith('__')) props.push(k);
                    }
                } catch(e) {}
                out.debug.push('props: ' + props.slice(0, 8).join(', '));

                let api = null;

                // Path A: Direct AG Grid properties
                for (const key of props) {
                    try {
                        const obj = gridEl[key];
                        if (!obj || typeof obj !== 'object') continue;
                        if (typeof obj.getDisplayedRowCount === 'function') {
                            api = obj; out.debug.push('API via ' + key); break;
                        }
                        for (const sub of ['gridApi', 'api', 'beans']) {
                            const c = obj[sub];
                            if (c && typeof c.getDisplayedRowCount === 'function') {
                                api = c; out.debug.push('API via ' + key + '.' + sub); break;
                            }
                        }
                        if (api) break;
                    } catch (e) {}
                }

                // Path B: React fiber traversal
                if (!api) {
                    const fk = props.find(
                        k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$')
                    );
                    if (fk) {
                        let fiber = gridEl[fk];
                        for (let d = 0; d < 50 && fiber; d++) {
                            try {
                                const p = fiber.memoizedProps || {};
                                for (const pk of ['gridApi', 'api']) {
                                    if (p[pk] && typeof p[pk].getDisplayedRowCount === 'function') {
                                        api = p[pk]; out.debug.push('API via fiber.' + pk + '@' + d); break;
                                    }
                                }
                                if (api) break;
                                if (p.gridOptions && p.gridOptions.api &&
                                    typeof p.gridOptions.api.getDisplayedRowCount === 'function') {
                                    api = p.gridOptions.api;
                                    out.debug.push('API via fiber.gridOptions.api@' + d); break;
                                }
                                const sn = fiber.stateNode;
                                if (sn && sn !== gridEl && typeof sn === 'object') {
                                    for (const sk of ['api', 'gridApi']) {
                                        if (sn[sk] && typeof sn[sk].getDisplayedRowCount === 'function') {
                                            api = sn[sk]; out.debug.push('API via stateNode.' + sk + '@' + d); break;
                                        }
                                    }
                                    if (api) break;
                                }
                            } catch (e) {}
                            fiber = fiber.return;
                        }
                    }
                }

                if (api) {
                    try {
                        const count = api.getDisplayedRowCount();
                        out.debug.push('rows: ' + count);
                        if (count > 0) {
                            const node = api.getDisplayedRowAtIndex(0);
                            if (node && node.data) {
                                const data = {};
                                for (const [k, v] of Object.entries(node.data)) {
                                    data[k] = v == null ? '' : String(v).substring(0, 300);
                                }
                                out.data = data;
                                out.method = 'gridApi';
                            }
                        }
                    } catch (e) { out.debug.push('API error: ' + e.message); }
                }
                return out;
            }""")

            if api_result:
                for d in api_result.get("debug", []):
                    logger.info("[GRID_DO_SENDER] %s", d)

                if api_result.get("data"):
                    data = api_result["data"]
                    logger.info("[GRID_DO_SENDER] Row fields: %s", list(data.keys()))
                    for key, val in data.items():
                        norm = key.upper().replace(' ', '').replace('_', '')
                        if 'DOSENDER' in norm or norm == 'DOSEND' or norm == 'SENDTO':
                            val_str = str(val).strip()
                            if val_str and '@' in val_str:
                                match = re.search(
                                    r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}',
                                    val_str,
                                )
                                if match:
                                    logger.info("[GRID_DO_SENDER] Found via API field '%s': %s",
                                                key, match.group(0))
                                    self._last_do_sender_strategy = f"grid_api_{key}"
                                    return match.group(0)
                            logger.info("[GRID_DO_SENDER] Field '%s' value='%s' — not a valid email",
                                        key, val_str[:50])
                            break

            # ── Approach 2: Scroll grid to DO SENDER column ──
            logger.info("[GRID_DO_SENDER] Trying DOM scroll approach")
            scroll_info = await self._page.evaluate("""() => {
                const headerCells = document.querySelectorAll('.ag-header-cell');
                for (const cell of headerCells) {
                    const textEl = cell.querySelector(
                        '.ag-header-cell-text, .ag-header-cell-label'
                    );
                    const text = textEl
                        ? (textEl.textContent || '').trim().toUpperCase()
                        : (cell.textContent || '').trim().toUpperCase();
                    if (text === 'DO SENDER') {
                        const colId = cell.getAttribute('col-id');
                        const left = cell.offsetLeft || 0;
                        const vp = document.querySelector(
                            '.ag-center-cols-viewport, .ag-body-viewport'
                        );
                        if (vp) vp.scrollLeft = Math.max(0, left - 200);
                        return { colId: colId, left: left, scrolled: true };
                    }
                }
                return { scrolled: false };
            }""")

            if not scroll_info or not scroll_info.get("scrolled"):
                logger.info("[GRID_DO_SENDER] DO SENDER column not found in grid headers")
                return None

            col_id = scroll_info.get("colId")
            logger.info("[GRID_DO_SENDER] Column col-id=%s, scrolled to left=%s",
                        col_id, scroll_info.get("left"))

            await asyncio.sleep(0.3)

            cell_val = await self._page.evaluate("""(colId) => {
                const rows = document.querySelectorAll('.ag-row, [role="row"]');
                for (const row of rows) {
                    const ri = row.getAttribute('row-index');
                    if (ri === null || ri === undefined) continue;
                    const cell = row.querySelector('[col-id="' + colId + '"]');
                    if (cell) {
                        const rect = cell.getBoundingClientRect();
                        if (rect.height > 0 && rect.top > 100) {
                            const inp = cell.querySelector('input');
                            return inp ? (inp.value || '') : (cell.textContent || '').trim();
                        }
                    }
                }
                return null;
            }""", col_id)

            # Scroll grid back to left
            await self._page.evaluate("""() => {
                const vp = document.querySelector(
                    '.ag-center-cols-viewport, .ag-body-viewport'
                );
                if (vp) vp.scrollLeft = 0;
            }""")
            await asyncio.sleep(0.3)

            if cell_val and '@' in str(cell_val):
                match = re.search(
                    r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}',
                    str(cell_val),
                )
                if match:
                    logger.info("[GRID_DO_SENDER] Found via DOM scroll: %s", match.group(0))
                    self._last_do_sender_strategy = f"grid_dom_{col_id}"
                    return match.group(0)

            logger.info("[GRID_DO_SENDER] No DO SENDER email in grid (cell=%s)", cell_val)
            return None

        except Exception as e:
            logger.warning("[GRID_DO_SENDER] Exception: %s", e)
            return None

    # ------------------------------------------------------------------
    # Stage 4: DO SENDER Extraction from Detail Page
    # ------------------------------------------------------------------
    async def _extract_do_sender(self) -> Optional[str]:
        """Read the DO SENDER email from the current work order detail page.

        Clicks the Detail Info tab, then searches for the DO SENDER field
        using 3 label-based strategies.
        """
        self._last_do_sender_strategy = ""
        try:
            current_url = self._page.url
            page_ctx = await self._capture_page_context()
            logger.info("[DO_SENDER] Starting extraction — URL: %s, tab: %s",
                        current_url, page_ctx.get("active_tab", "unknown"))

            do_sender_label = self._selectors.get("work_order", {}).get(
                "do_sender_label", "DO SENDER"
            )
            detail_tab = self._selectors.get("work_order", {}).get(
                "tabs", {}
            ).get("detail_info", "Detail Info")

            tab_clicked = await self._click_tab(detail_tab)
            logger.info("[DO_SENDER] Clicked '%s' tab: %s", detail_tab, tab_clicked)

            detail_visible = False
            for attempt in range(10):
                detail_visible = await self._page.evaluate("""() => {
                    const text = (document.body.innerText || '').toUpperCase();
                    return text.includes('DO SENDER') || text.includes('CONSIGNEE')
                        || text.includes('SHIPPER') || text.includes('NOTIFY');
                }""")
                if detail_visible:
                    break
                await asyncio.sleep(0.2)

            logger.info("[DO_SENDER] Detail Info visible = %s (after %d polls)",
                        detail_visible, attempt + 1)

            if not detail_visible:
                logger.warning("[DO_SENDER] Detail Info section not visible")

            try:
                await self._page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:
                pass

            await self._debug_rich("detail_info_visible")

            extraction = await self._page.evaluate("""(labelText) => {
                const upperLabel = labelText.toUpperCase();
                const result = {
                    strategy: null,
                    value: null,
                    labelFound: false,
                    labelElement: null,
                    valueElement: null,
                    debug: []
                };

                // Strategy 1: TreeWalker — find label text, read nearby value
                const walker = document.createTreeWalker(
                    document.body, NodeFilter.SHOW_TEXT, null
                );
                while (walker.nextNode()) {
                    const txt = walker.currentNode.textContent.trim().toUpperCase();
                    if (!txt.includes(upperLabel)) continue;

                    result.labelFound = true;
                    const labelEl = walker.currentNode.parentElement;
                    if (!labelEl) continue;
                    result.labelElement = labelEl.tagName + '.' +
                        (labelEl.className || '').toString().substring(0, 80);
                    result.debug.push('S1: found label in <' + labelEl.tagName +
                        '> at (' + labelEl.getBoundingClientRect().top + ')');

                    let el = labelEl;
                    for (let i = 0; i < 8 && el; i++) {
                        const inputs = el.querySelectorAll('input, textarea');
                        for (const inp of inputs) {
                            const val = (inp.value || '').trim();
                            if (val && val.includes('@')) {
                                result.strategy = 'S1_input';
                                result.value = val;
                                result.valueElement = 'INPUT[value=' + val + ']';
                                return result;
                            }
                        }

                        const textEls = el.querySelectorAll(
                            'span, div, p, td, .MuiTypography-root, .MuiInputBase-input'
                        );
                        for (const te of textEls) {
                            if (te === labelEl) continue;
                            if (te.contains(labelEl)) continue;
                            const teText = (te.textContent || '').trim();
                            if (teText && teText.includes('@') &&
                                teText !== txt &&
                                !teText.toUpperCase().includes(upperLabel)) {
                                const emailMatch = teText.match(
                                    /[a-zA-Z0-9._%+\\-]+@[a-zA-Z0-9.\\-]+\\.[a-zA-Z]{2,}/
                                );
                                if (emailMatch) {
                                    result.strategy = 'S1_text';
                                    result.value = emailMatch[0];
                                    result.valueElement = te.tagName + '.' +
                                        (te.className || '').toString().substring(0, 80);
                                    return result;
                                }
                            }
                        }
                        el = el.parentElement;
                    }
                }

                // Strategy 2: MUI FormControl label association
                const labels = document.querySelectorAll(
                    'label, .MuiInputLabel-root, .MuiFormLabel-root, ' +
                    '.MuiTypography-root, [class*="label"], [class*="Label"]'
                );
                for (const lbl of labels) {
                    const text = (lbl.textContent || '').toUpperCase().trim();
                    if (!text.includes(upperLabel)) continue;

                    result.labelFound = true;
                    result.debug.push('S2: label match in <' + lbl.tagName +
                        '> class=' + (lbl.className || '').toString().substring(0, 60));

                    const formCtrl = lbl.closest(
                        '.MuiFormControl-root, [class*="formControl"], [class*="FormControl"]'
                    );
                    if (formCtrl) {
                        const inp = formCtrl.querySelector('input, textarea');
                        if (inp) {
                            const val = (inp.value || inp.textContent || '').trim();
                            if (val) {
                                result.strategy = 'S2_mui_input';
                                result.value = val;
                                result.valueElement = 'INPUT in FormControl';
                                return result;
                            }
                        }
                        const display = formCtrl.querySelector(
                            '.MuiInputBase-input, .MuiInput-input, ' +
                            '[class*="input"], [class*="value"], span, p'
                        );
                        if (display && display !== lbl) {
                            const val = (display.textContent || '').trim();
                            if (val && val.includes('@')) {
                                result.strategy = 'S2_mui_text';
                                result.value = val;
                                result.valueElement = display.tagName;
                                return result;
                            }
                        }
                    }

                    const parent = lbl.parentElement;
                    if (parent) {
                        const inp = parent.querySelector('input, textarea');
                        if (inp && inp.value) {
                            result.strategy = 'S2_parent_input';
                            result.value = inp.value.trim();
                            result.valueElement = 'INPUT in parent';
                            return result;
                        }
                        const nextSib = lbl.nextElementSibling;
                        if (nextSib) {
                            const sibInp = nextSib.querySelector('input, textarea');
                            if (sibInp && sibInp.value) {
                                result.strategy = 'S2_sibling_input';
                                result.value = sibInp.value.trim();
                                return result;
                            }
                            const sibText = (nextSib.textContent || '').trim();
                            if (sibText && sibText.includes('@')) {
                                result.strategy = 'S2_sibling_text';
                                result.value = sibText;
                                return result;
                            }
                        }
                    }
                }

                // Strategy 3: Table/grid row label-value pairs
                const allCells = document.querySelectorAll('td, th, dt, dd, [role="cell"]');
                for (let i = 0; i < allCells.length; i++) {
                    const cellText = (allCells[i].textContent || '').toUpperCase().trim();
                    if (!cellText.includes(upperLabel)) continue;
                    result.labelFound = true;
                    result.debug.push('S3: found label in cell[' + i + ']');
                    const nextCell = allCells[i + 1] || allCells[i].nextElementSibling;
                    if (nextCell) {
                        const val = (nextCell.textContent || '').trim();
                        if (val && val.includes('@')) {
                            result.strategy = 'S3_table_cell';
                            result.value = val;
                            return result;
                        }
                        const inp = nextCell.querySelector('input');
                        if (inp && inp.value && inp.value.includes('@')) {
                            result.strategy = 'S3_table_input';
                            result.value = inp.value.trim();
                            return result;
                        }
                    }
                }

                return result;
            }""", do_sender_label)

            strategy = extraction.get("strategy")
            raw_value = extraction.get("value")
            label_found = extraction.get("labelFound", False)
            debug_notes = extraction.get("debug", [])

            logger.info("[DO_SENDER] Label found = %s", label_found)
            for note in debug_notes:
                logger.info("[DO_SENDER]   %s", note)

            if not label_found:
                logger.warning("[DO_SENDER] FAIL: DO SENDER label NOT found in page DOM")
                await self._debug_rich("do_sender_label_not_found")
                return None

            if not raw_value:
                logger.warning("[DO_SENDER] FAIL: DO SENDER label found but no value extracted")
                await self._debug_rich("do_sender_value_empty")
                return None

            logger.info("[DO_SENDER] Extracted = '%s' (via %s)", raw_value, strategy)

            # Validate email
            email_pattern = re.compile(
                r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$'
            )
            do_sender = raw_value.strip()

            if not email_pattern.match(do_sender):
                email_match = re.search(
                    r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}',
                    do_sender,
                )
                if email_match:
                    do_sender = email_match.group(0)

            if not do_sender or '@' not in do_sender:
                logger.warning("[DO_SENDER] FAIL: extracted value '%s' is not a valid email", do_sender)
                await self._debug_rich("do_sender_invalid_email")
                return None

            if not email_pattern.match(do_sender):
                logger.warning("[DO_SENDER] FAIL: '%s' does not match email pattern", do_sender)
                await self._debug_rich("do_sender_invalid_email")
                return None

            logger.info("[DO_SENDER] SUCCESS: strategy=%s email=%s", strategy, do_sender)
            self._last_do_sender_strategy = strategy or ""
            await self._debug("do_sender_found")
            return do_sender

        except Exception as e:
            logger.error("[DO_SENDER] Exception: %s", e, exc_info=True)
            await self._debug("do_sender_error")
            return None

    # ------------------------------------------------------------------
    # Standalone DO SENDER fetch
    # ------------------------------------------------------------------
    async def fetch_do_sender_email(self, container_number: str,
                                    invoice_number: str = "") -> Optional[str]:
        """Search TMS for a container and extract the D/O SENDER email.

        Standalone version — navigates to the work order first.
        Uses a two-tier approach:
        1. Extract DO SENDER from the MAIN grid row data (fast, no navigation needed)
        2. Navigate to work order detail page and extract from Detail Info tab (fallback)
        """
        container_number = container_number.strip()
        logger.info("[DO_SENDER_FETCH] Standalone fetch: container='%s' invoice='%s'",
                    container_number, invoice_number)

        work_order_url = await self.search_container(
            container_number, invoice_number=invoice_number
        )

        # Tier 1: Grid-extracted DO SENDER (captured during search_container filtering)
        if self._grid_do_sender:
            logger.info("[DO_SENDER_FETCH] SUCCESS (from grid): %s = %s",
                        container_number, self._grid_do_sender)
            return self._grid_do_sender

        if not work_order_url:
            logger.warning("[DO_SENDER_FETCH] search_container returned None and no grid DO SENDER")
            await self._debug("do_sender_search_failed")
            return None

        # Tier 2: Detail page extraction
        await self._debug("wo_detail_loaded")
        do_sender = await self._extract_do_sender()
        if do_sender:
            logger.info("[DO_SENDER_FETCH] SUCCESS (from detail page): %s = %s",
                        container_number, do_sender)
        else:
            logger.warning("[DO_SENDER_FETCH] No D/O sender found for %s", container_number)
        return do_sender

    # ------------------------------------------------------------------
    # Stage 5: Document Tab Navigation
    # ------------------------------------------------------------------
    async def navigate_to_documents_tab(self) -> bool:
        """Navigate to the Document tab via direct URL or tab click."""
        try:
            # Strategy 1: Direct URL navigation (most reliable)
            current_url = self._page.url
            doc_url = None
            for seg in ("/detail-info/", "/billing-info/", "/memo/", "/tracking/"):
                if seg in current_url:
                    doc_url = current_url.replace(seg, "/document/")
                    break
            if doc_url:
                logger.info("Navigating to Document tab via URL: %s", doc_url)
                await self._page.goto(doc_url, wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(0.5)
            else:
                # Strategy 2: Click the tab
                tab_names = ["Document", "Documents"]
                clicked = False
                for name in tab_names:
                    clicked = await self._click_tab(name)
                    if clicked:
                        logger.info("Clicked Document tab using name: %s", name)
                        break
                if not clicked:
                    ctx = await self._debug_rich("no_document_tab")
                    self._make_error("navigate_documents_tab", "Could not find Document tab", ctx)
                    return False

            # Verify: look for file input elements (div-based layout, NOT tables)
            content_loaded = False
            for attempt in range(10):
                content_loaded = await self._page.evaluate("""() => {
                    // Document tab uses input[name^="file."] for each doc row
                    const fileInputs = document.querySelectorAll('input[name^="file."]');
                    return fileInputs.length >= 3;
                }""")
                if content_loaded:
                    break
                await asyncio.sleep(0.2)

            if not content_loaded:
                logger.warning("Document tab opened but file inputs not found")

            await self._debug_rich("document_tab_opened")
            logger.info("Document tab opened, content_loaded=%s", content_loaded)
            return True

        except Exception as e:
            logger.error("Failed to navigate to Document tab: %s", e)
            await self._debug_rich("document_tab_error")
            return False

    # ------------------------------------------------------------------
    # Stage 6: Document listing
    # ------------------------------------------------------------------
    async def list_documents(self) -> list[dict]:
        """Parse document rows from the Document tab's div-based layout.

        Each document row has a file input with name pattern:
          input[name="file.{TYPE}.{TYPE}_file_name"]
        The value is the filename (non-empty = file uploaded).
        """
        try:
            docs = await self._page.evaluate("""() => {
                const results = [];
                // Find all file inputs — pattern: file.{TYPE}.{TYPE}_file_name
                const inputs = document.querySelectorAll('input[type="search"][readonly]');
                for (const inp of inputs) {
                    const name = inp.name || '';
                    const match = name.match(/^file\\.([A-Z\\-]+)\\.[A-Z\\-]+_file_name$/);
                    if (!match) continue;

                    const docType = match[1];
                    const filename = (inp.value || '').trim();
                    const hasFile = filename.length > 0;

                    // Walk up to find the row container and extract date + updater
                    let date = '';
                    let updater = '';
                    // The input is inside a div[width="202"] (BROWSE col).
                    // Sibling columns at the same level hold date/type/updater.
                    const browseCol = inp.closest('div[width="202"]');
                    if (browseCol) {
                        const row = browseCol.parentElement;
                        if (row) {
                            // Date is in div[width="150"]
                            const dateCol = row.querySelector('div[width="150"]');
                            if (dateCol) {
                                date = (dateCol.textContent || '').trim().replace(/MM\\/DD\\/YY.*/, '');
                            }
                            // Updater is in div[width="120"]
                            const updCol = row.querySelector('div[width="120"]');
                            if (updCol) {
                                updater = (updCol.textContent || '').trim();
                            }
                        }
                    }

                    results.push({
                        type: docType,
                        name: filename || docType,
                        has_file: hasFile,
                        filename: filename,
                        input_id: inp.id || '',
                        date: date,
                        updater: updater,
                    });
                }
                return results;
            }""")

            logger.info("Found %d document rows on TMS page", len(docs))
            for doc in docs:
                logger.info(
                    "  %s: has_file=%s, filename=%s, date=%s",
                    doc["type"], doc["has_file"], doc.get("filename", ""), doc.get("date", ""),
                )
            return docs

        except Exception as e:
            logger.error("Failed to list TMS documents: %s", e)
            await self._debug("list_documents_error")
            return []
