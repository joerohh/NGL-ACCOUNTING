"""TMS connection endpoints — login status and manual login trigger."""

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException

from config import TMS_DEBUG_DIR

logger = logging.getLogger("ngl.tms_router")

router = APIRouter(prefix="/tms", tags=["tms"])

# Injected by main.py on startup
_tms_browser = None


def set_tms_browser(tms):
    global _tms_browser
    _tms_browser = tms


@router.get("/status")
async def tms_status():
    """Passive check — just reads the current URL without navigating."""
    if not _tms_browser:
        return {"status": "not_configured", "loggedIn": False}

    try:
        url = _tms_browser.current_url
        logged_in = _tms_browser.is_logged_in()
        return {
            "status": "connected" if logged_in else "login_required",
            "loggedIn": logged_in,
            "currentUrl": url,
        }
    except Exception as e:
        return {
            "status": "error",
            "loggedIn": False,
            "error": str(e),
        }


@router.post("/open-login")
async def open_tms_login():
    """Open the TMS login page for manual Google SSO authentication."""
    if not _tms_browser:
        raise HTTPException(503, "TMS browser not initialized")

    try:
        url = await _tms_browser.open_login_page()
        return {
            "status": "login_page_opened",
            "url": url,
            "message": "Please log into TMS via Google SSO in the Chrome window.",
        }
    except Exception as e:
        raise HTTPException(500, f"Failed to open TMS login page: {e}")


@router.post("/wait-for-login")
async def wait_for_tms_login():
    """Wait for the user to complete Google SSO login (up to 2 minutes)."""
    if not _tms_browser:
        raise HTTPException(503, "TMS browser not initialized")

    try:
        success = await _tms_browser.wait_for_login(timeout_s=120)
        if success:
            return {"status": "logged_in", "message": "TMS login successful!"}
        else:
            return {"status": "timeout", "message": "Login timed out. Please try again."}
    except Exception as e:
        raise HTTPException(500, f"Error waiting for TMS login: {e}")


@router.get("/selector-health")
async def tms_selector_health():
    """Check if critical TMS DOM selectors are present on the current page."""
    from services.health_check import check_tms_selectors
    return await check_tms_selectors(_tms_browser)


@router.post("/test-search/{container}")
async def test_search(container: str):
    """Test endpoint: search TMS for a container and return the work order URL."""
    if not _tms_browser:
        raise HTTPException(503, "TMS browser not initialized")
    if not _tms_browser.is_logged_in():
        raise HTTPException(400, "TMS not logged in — log in first via /tms/open-login")

    try:
        url = await _tms_browser.search_container(container)
        return {"container": container, "work_order_url": url, "found": url is not None}
    except Exception as e:
        raise HTTPException(500, f"TMS search failed: {e}")


@router.post("/test-do-sender/{container}")
async def test_do_sender(container: str):
    """Test endpoint: fetch D/O sender email for a container."""
    if not _tms_browser:
        raise HTTPException(503, "TMS browser not initialized")
    if not _tms_browser.is_logged_in():
        raise HTTPException(400, "TMS not logged in")

    try:
        email = await _tms_browser.fetch_do_sender_email(container)
        return {"container": container, "do_sender_email": email, "found": email is not None}
    except Exception as e:
        raise HTTPException(500, f"D/O sender lookup failed: {e}")


@router.get("/diag-routes")
async def diagnose_routes():
    """Discover TMS route patterns from React Router config and JS source."""
    if not _tms_browser:
        raise HTTPException(503, "TMS browser not initialized")

    try:
        page = _tms_browser._page

        # Navigate to main page first if needed
        current_url = page.url
        if '/main/' not in current_url:
            await _tms_browser._navigate_to_main_page()
            import asyncio
            await asyncio.sleep(2)

        routes = await page.evaluate("""() => {
            const out = {
                currentUrl: window.location.href,
                routes: [],
                routerState: null,
                lm_analysis: null,
                scriptSources: [],
            };

            // Find React fiber root - try ALL elements with fiber keys
            let rootFiber = null;
            const allEls = document.querySelectorAll('*');
            for (const el of allEls) {
                const fk = Object.getOwnPropertyNames(el).find(
                    k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$')
                );
                if (fk) {
                    // Walk UP to find the topmost fiber
                    let f = el[fk];
                    while (f.return) f = f.return;
                    rootFiber = f;
                    break;
                }
            }

            if (!rootFiber) {
                out.error = 'no React fiber found on any element';
                return out;
            }

            // BFS through entire fiber tree to find route info
            const visited = new Set();
            const queue = [rootFiber];

            while (queue.length > 0) {
                const f = queue.shift();
                if (!f || visited.has(f)) continue;
                visited.add(f);
                if (visited.size > 2000) break;

                try {
                    const p = f.memoizedProps || {};

                    // React Router v6 Route component
                    if (p.path && typeof p.path === 'string') {
                        out.routes.push({
                            path: p.path,
                            type: f.type?.name || f.type?.displayName || String(f.type).substring(0, 50),
                        });
                    }

                    // React Router v6 router context
                    if (p.router) {
                        const r = p.router;
                        if (r.routes) {
                            const flatRoutes = (routes, prefix='') => {
                                const result = [];
                                for (const route of routes) {
                                    const fullPath = prefix + (route.path || '');
                                    if (route.path) result.push(fullPath);
                                    if (route.children) {
                                        result.push(...flatRoutes(route.children, fullPath + '/'));
                                    }
                                }
                                return result;
                            };
                            out.routerRoutes = flatRoutes(r.routes);
                        }
                        if (r.state) {
                            out.routerState = {
                                location: r.state.location,
                                matches: (r.state.matches || []).map(m => ({
                                    route_path: m.route?.path,
                                    pathname: m.pathname,
                                    params: m.params,
                                })),
                            };
                        }
                    }

                    // Look for navigate function or Zustand store
                    if (p.value && typeof p.value === 'object') {
                        if (typeof p.value.navigate === 'function') {
                            out.navigateFound = true;
                            out.navigateSource = p.value.navigate.toString().substring(0, 200);
                        }
                    }
                } catch(e) {}

                if (f.child) queue.push(f.child);
                if (f.sibling) queue.push(f.sibling);
            }

            // Get script sources
            const scripts = document.querySelectorAll('script[src]');
            out.scriptSources = Array.from(scripts).map(s => s.src);

            return out;
        }""")
        return routes
    except Exception as e:
        raise HTTPException(500, f"Route discovery failed: {e}")


@router.post("/diag-nav/{container}")
async def diagnose_navigation(container: str):
    """Diagnostic: filter grid by container then run focused navigation analysis.

    Returns detailed diagnostics about the React fiber handler invocation
    and navigation attempt, without going through the full search_container flow.
    """
    if not _tms_browser:
        raise HTTPException(503, "TMS browser not initialized")
    if not _tms_browser.is_logged_in():
        raise HTTPException(400, "TMS not logged in")

    try:
        page = _tms_browser._page
        diag = {"container": container, "steps": []}

        # Step 1: Navigate to main page
        on_main = await _tms_browser._navigate_to_main_page()
        diag["steps"].append({"step": "navigate_to_main", "success": on_main})
        if not on_main:
            return diag

        import asyncio
        await asyncio.sleep(2)

        # Step 2: Filter grid
        filter_result = await _tms_browser._filter_grid_by_container(container)
        diag["steps"].append({
            "step": "filter_grid",
            "success": filter_result.success,
            "error": filter_result.error,
        })
        if not filter_result.success:
            return diag

        wo_info = filter_result.data.get("wo_info", {})
        hit = wo_info.get("hit", {})
        wo_number = hit.get("text", "")
        diag["wo_number"] = wo_number
        diag["url_before"] = page.url

        # Step 3: Focused fiber diagnostic — find handlers, trace lM, invoke with monitoring
        fiber_diag = await page.evaluate("""(woText) => {
            const out = {
                wo_element_found: false,
                row_data: {},
                handlers_found: [],
                app_handler_found: false,
                app_handler_source: null,
                lm_trace: null,
                nav_intercepted: [],
                invoke_result: null,
                url_before: window.location.href,
                url_after: null,
                error: null,
            };

            // Find WO# element in grid
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
            let targetEl = null;
            while (walker.nextNode()) {
                if (walker.currentNode.textContent.trim() === woText) {
                    const el = walker.currentNode.parentElement;
                    if (el) {
                        const rect = el.getBoundingClientRect();
                        if (rect.top > 200 && rect.height > 0) {
                            targetEl = el; break;
                        }
                    }
                }
            }
            if (!targetEl) { out.error = 'WO# element not found in DOM'; return out; }
            out.wo_element_found = true;
            out.wo_element_tag = targetEl.tagName;
            out.wo_element_rect = targetEl.getBoundingClientRect();

            // Collect row data
            const woCell = targetEl.closest('[col-id][role="gridcell"]') || targetEl;
            const agRow = woCell.closest('.ag-row, [role="row"]');
            if (agRow) {
                agRow.querySelectorAll('[col-id]').forEach(cell => {
                    const cid = cell.getAttribute('col-id');
                    if (cid) out.row_data[cid] = (cell.textContent || '').trim();
                });
            }

            // Monkey-patch navigation methods to intercept any navigation
            const origPushState = window.history.pushState.bind(window.history);
            const origReplaceState = window.history.replaceState.bind(window.history);
            window.history.pushState = function(state, title, url) {
                out.nav_intercepted.push({method: 'pushState', url: String(url)});
                return origPushState(state, title, url);
            };
            window.history.replaceState = function(state, title, url) {
                out.nav_intercepted.push({method: 'replaceState', url: String(url)});
                return origReplaceState(state, title, url);
            };

            // Walk fiber tree
            let appHandler = null;
            let appHandlerInfo = null;
            let el = targetEl;
            for (let domDepth = 0; domDepth < 25 && el; domDepth++) {
                const fiberKey = Object.getOwnPropertyNames(el).find(
                    k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$')
                );
                if (fiberKey) {
                    let fiber = el[fiberKey];
                    for (let fd = 0; fd < 20 && fiber; fd++) {
                        const p = fiber.memoizedProps || {};
                        const clickNames = [
                            'onCellClicked', 'onRowClicked', 'onClick',
                            'onRowDoubleClicked', 'onCellDoubleClicked',
                        ];
                        for (const cn of clickNames) {
                            if (typeof p[cn] === 'function') {
                                const src = p[cn].toString().substring(0, 600);
                                out.handlers_found.push({
                                    domDepth, fiberDepth: fd,
                                    tag: el.tagName, handler: cn,
                                    source: src,
                                    has_wo_no: src.includes('wo_no'),
                                    has_Adn: src.includes('Adn') || src.includes('zdn'),
                                });

                                if (!appHandler && src.includes('wo_no') && !src.includes('Adn')) {
                                    appHandler = p[cn];
                                    appHandlerInfo = {domDepth, fiberDepth: fd, handler: cn};
                                    out.app_handler_found = true;
                                    out.app_handler_source = src;
                                }
                            }
                        }
                        fiber = fiber.return;
                    }
                }
                el = el.parentElement;
            }

            if (!appHandler) {
                out.error = 'No application handler (wo_no without Adn) found in fiber tree';
                // Restore originals
                window.history.pushState = origPushState;
                window.history.replaceState = origReplaceState;
                return out;
            }

            // Invoke the handler
            try {
                const rowIdx = parseInt(agRow?.getAttribute('row-index') || '0');
                const agEvent = {
                    gridParam: { data: out.row_data },
                    data: out.row_data,
                    node: { data: out.row_data, rowIndex: rowIdx },
                    rowIndex: rowIdx,
                };
                out.invoke_event = agEvent;
                appHandler(agEvent);
                out.invoke_result = 'success';
            } catch(e) {
                out.invoke_result = 'error: ' + e.message;
            }

            out.url_after = window.location.href;

            // Restore originals
            window.history.pushState = origPushState;
            window.history.replaceState = origReplaceState;

            return out;
        }""", wo_number)

        diag["fiber_diag"] = fiber_diag
        diag["url_after_immediate"] = page.url

        # Wait a bit for async navigation
        await asyncio.sleep(3)
        diag["url_after_3s"] = page.url

        # Check for detail markers
        marker = await _tms_browser._has_detail_markers()
        diag["detail_marker"] = marker
        diag["page_title"] = await page.evaluate("() => document.title")

        return diag
    except Exception as e:
        raise HTTPException(500, f"Navigation diagnostic failed: {e}")


@router.post("/test-document-tab")
async def test_document_tab():
    """Focused test: navigate to Document tab from current detail page and capture HTML.

    Assumes TMS is already on a work order detail page.
    Returns detailed diagnostics about the Document tab structure.
    """
    if not _tms_browser:
        raise HTTPException(503, "TMS browser not initialized")
    if not _tms_browser.is_logged_in():
        raise HTTPException(400, "TMS not logged in")

    page = _tms_browser._page
    diag = {"steps": [], "current_url": page.url}

    try:
        # Step 1: Verify we're on a detail page
        marker = await _tms_browser._has_detail_markers()
        diag["on_detail_page"] = bool(marker)
        diag["detail_marker"] = marker
        if not marker:
            diag["steps"].append({"step": "verify_detail_page", "success": False,
                                  "error": "Not on a detail page — navigate to a WO first"})
            return diag
        diag["steps"].append({"step": "verify_detail_page", "success": True})

        # Step 2: Try direct URL navigation to Document tab
        current_url = page.url
        if "/detail-info/" in current_url:
            doc_url = current_url.replace("/detail-info/", "/document/")
            logger.info("[DOC_TAB_TEST] Navigating to Document tab: %s", doc_url)
            await page.goto(doc_url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(3)
            diag["steps"].append({"step": "goto_document_url", "success": True,
                                  "url": doc_url, "arrived_at": page.url})
        else:
            # Fallback: click the tab
            clicked = await _tms_browser._click_tab("Document")
            diag["steps"].append({"step": "click_document_tab", "success": clicked})
            if not clicked:
                clicked = await _tms_browser._click_tab("Documents")
                diag["steps"].append({"step": "click_documents_tab", "success": clicked})
            await asyncio.sleep(3)

        # Step 3: Wait for content and capture
        await _tms_browser._debug_rich("document_tab_test")

        # Step 4: Analyze the Document tab DOM structure
        dom_analysis = await page.evaluate("""() => {
            const out = {
                url: window.location.href,
                title: document.title,
                page_text_preview: (document.body.innerText || '').substring(0, 2000),
                tables: [],
                all_buttons: [],
                all_links: [],
                iframes: [],
                doc_type_rows: [],
            };

            // Find all tables
            const tables = document.querySelectorAll('table');
            for (let ti = 0; ti < tables.length; ti++) {
                const table = tables[ti];
                const rows = table.querySelectorAll('tr');
                const tableInfo = {
                    index: ti,
                    row_count: rows.length,
                    class: table.className || '',
                    html_preview: table.outerHTML.substring(0, 3000),
                    rows_detail: [],
                };
                for (let ri = 0; ri < rows.length && ri < 20; ri++) {
                    const row = rows[ri];
                    const cells = row.querySelectorAll('td, th');
                    const rowInfo = {
                        index: ri,
                        cell_count: cells.length,
                        text: (row.textContent || '').trim().substring(0, 200),
                        has_links: row.querySelectorAll('a').length,
                        has_buttons: row.querySelectorAll('button').length,
                        has_inputs: row.querySelectorAll('input').length,
                        cell_texts: [],
                        link_details: [],
                    };
                    for (const cell of cells) {
                        rowInfo.cell_texts.push((cell.textContent || '').trim().substring(0, 100));
                    }
                    // Get link details in this row
                    for (const a of row.querySelectorAll('a')) {
                        rowInfo.link_details.push({
                            text: (a.textContent || '').trim().substring(0, 100),
                            href: (a.getAttribute('href') || '').substring(0, 200),
                            target: a.target || '',
                            onclick: a.getAttribute('onclick') || '',
                        });
                    }
                    // Get button details in this row
                    for (const btn of row.querySelectorAll('button')) {
                        rowInfo.link_details.push({
                            text: (btn.textContent || '').trim().substring(0, 100),
                            type: 'button',
                            onclick: btn.getAttribute('onclick') || '',
                            class: (btn.className || '').substring(0, 100),
                        });
                    }
                    tableInfo.rows_detail.push(rowInfo);
                }
                out.tables.push(tableInfo);
            }

            // Find all buttons on page
            for (const btn of document.querySelectorAll('button')) {
                const rect = btn.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                    out.all_buttons.push({
                        text: (btn.textContent || '').trim().substring(0, 100),
                        class: (btn.className || '').substring(0, 100),
                        x: Math.round(rect.x), y: Math.round(rect.y),
                        w: Math.round(rect.width), h: Math.round(rect.height),
                    });
                }
            }

            // Find all iframes (viewer might be an iframe)
            for (const iframe of document.querySelectorAll('iframe, embed, object')) {
                out.iframes.push({
                    tag: iframe.tagName,
                    src: (iframe.src || iframe.getAttribute('data') || '').substring(0, 300),
                    class: (iframe.className || '').substring(0, 100),
                    rect: (() => {
                        const r = iframe.getBoundingClientRect();
                        return { x: Math.round(r.x), y: Math.round(r.y),
                                 w: Math.round(r.width), h: Math.round(r.height) };
                    })(),
                });
            }

            return out;
        }""")

        diag["dom_analysis"] = dom_analysis
        diag["steps"].append({"step": "analyze_dom", "success": True,
                              "tables_found": len(dom_analysis.get("tables", [])),
                              "buttons_found": len(dom_analysis.get("all_buttons", [])),
                              "iframes_found": len(dom_analysis.get("iframes", []))})

        # Step 5: Try to list documents using existing method
        docs = await _tms_browser.list_documents()
        diag["documents"] = docs
        diag["steps"].append({"step": "list_documents", "success": len(docs) > 0,
                              "count": len(docs)})

        # Step 6: Check for POD row specifically
        pod_rows = [d for d in docs if d.get("type") == "POD"]
        diag["pod_rows"] = pod_rows
        diag["steps"].append({"step": "find_pod", "success": len(pod_rows) > 0,
                              "pod_has_file": pod_rows[0].get("has_file") if pod_rows else None})

        return diag

    except Exception as e:
        logger.error("Document tab test failed: %s", e)
        raise HTTPException(500, f"Document tab test failed: {e}")


@router.post("/test-pod-download/{container}")
async def test_pod_download(container: str):
    """Full focused test: search container → Document tab → find POD → download.

    This is the focused end-to-end test for the POD download pipeline.
    """
    if not _tms_browser:
        raise HTTPException(503, "TMS browser not initialized")
    if not _tms_browser.is_logged_in():
        raise HTTPException(400, "TMS not logged in")

    diag = {"container": container, "steps": []}

    try:
        # Reset debug step counter for clean captures
        _tms_browser._debug_step = 0

        # Step 1: Search container (navigate to detail page)
        url = await _tms_browser.search_container(container)
        diag["steps"].append({"step": "search_container", "success": url is not None,
                              "work_order_url": url})
        if not url:
            grid_email = _tms_browser._grid_do_sender
            diag["grid_do_sender"] = grid_email
            diag["steps"].append({"step": "search_failed",
                                  "note": "search_container returned None"})
            return diag

        # Step 2: Navigate to Document tab (direct URL)
        current_url = _tms_browser._page.url
        if "/detail-info/" in current_url:
            doc_url = current_url.replace("/detail-info/", "/document/")
        elif "/billing-info/" in current_url:
            doc_url = current_url.replace("/billing-info/", "/document/")
        else:
            doc_url = None

        if doc_url:
            logger.info("[POD_TEST] Navigating to Document tab: %s", doc_url)
            await _tms_browser._page.goto(doc_url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(3)
            diag["steps"].append({"step": "goto_document_url", "success": True,
                                  "url": doc_url})
        else:
            nav = await _tms_browser.navigate_to_documents_tab()
            diag["steps"].append({"step": "click_document_tab", "success": nav})
            if not nav:
                return diag

        await _tms_browser._debug_rich("pod_test_document_tab")

        # Step 3: List documents
        docs = await _tms_browser.list_documents()
        diag["documents"] = docs
        diag["steps"].append({"step": "list_documents", "count": len(docs)})

        # Step 4: Find POD row
        pod_row = None
        for doc in docs:
            if doc.get("type") == "POD":
                pod_row = doc
                break
        diag["pod_row"] = pod_row
        if not pod_row:
            diag["steps"].append({"step": "find_pod", "success": False,
                                  "error": "No POD row found"})
            return diag
        if not pod_row.get("has_file"):
            diag["steps"].append({"step": "find_pod", "success": False,
                                  "error": "POD row exists but no file uploaded"})
            return diag
        diag["steps"].append({"step": "find_pod", "success": True,
                              "filename": pod_row.get("filename", "")})

        # Step 5: Download POD
        download_dir = TMS_DEBUG_DIR / "test_downloads"
        download_dir.mkdir(parents=True, exist_ok=True)
        pod_path = await _tms_browser.download_document(
            "POD", download_dir, pod_row.get("filename", "")
        )
        if pod_path:
            valid, err = _tms_browser.validate_downloaded_file(pod_path)
            diag["steps"].append({"step": "download_pod", "success": valid,
                                  "path": str(pod_path),
                                  "size": pod_path.stat().st_size if pod_path.exists() else 0,
                                  "validation_error": err})
        else:
            diag["steps"].append({"step": "download_pod", "success": False,
                                  "error": "download_document returned None"})

        return diag

    except Exception as e:
        logger.error("POD download test failed: %s", e)
        diag["error"] = str(e)
        return diag


@router.post("/test-click-pod")
async def test_click_pod():
    """Click the POD row on the Document tab and capture viewer state.

    Assumes we're already on the Document tab (call test-document-tab first).
    """
    if not _tms_browser:
        raise HTTPException(503, "TMS browser not initialized")

    page = _tms_browser._page

    try:
        # Click the POD filename input to select the POD row
        result = await page.evaluate("""async () => {
            const out = {
                viewer_before: null,
                click_method: null,
                viewer_after: null,
                viewer_html: null,
                iframes: [],
                download_buttons: [],
                pdf_indicators: [],
                new_elements: [],
                error: null,
            };

            // Check viewer state before
            out.viewer_before = document.body.innerText.includes('No Document Selected')
                ? 'empty' : 'has_content';

            // Strategy 1: Click the POD filename input area
            const podInput = document.querySelector('input[name="file.POD.POD_file_name"]');
            if (!podInput) {
                out.error = 'POD file input not found';
                return out;
            }
            out.pod_filename = podInput.value;

            // Click on the POD row — try clicking the date cell or the row container
            const podInputRow = podInput.closest('[class*="flex"][class*="items-center"]');
            if (podInputRow) {
                // Find the date cell within this row
                const dateCell = podInputRow.querySelector('[width="150"]');
                if (dateCell) {
                    dateCell.click();
                    out.click_method = 'date_cell_click';
                } else {
                    podInputRow.click();
                    out.click_method = 'row_click';
                }
            } else {
                podInput.click();
                out.click_method = 'input_click';
            }

            // Wait for viewer to load
            await new Promise(r => setTimeout(r, 3000));

            // Check viewer state after
            const bodyText = document.body.innerText;
            out.viewer_after = bodyText.includes('No Document Selected')
                ? 'still_empty' : 'has_content';

            // Look for iframes / embeds
            for (const el of document.querySelectorAll('iframe, embed, object, canvas')) {
                const rect = el.getBoundingClientRect();
                out.iframes.push({
                    tag: el.tagName,
                    src: (el.src || el.getAttribute('data') || '').substring(0, 500),
                    w: Math.round(rect.width), h: Math.round(rect.height),
                });
            }

            // Look for download/save buttons
            for (const btn of document.querySelectorAll('button, a, [role="button"]')) {
                const text = (btn.textContent || '').trim().toLowerCase();
                const title = (btn.title || '').toLowerCase();
                const ariaLabel = (btn.getAttribute('aria-label') || '').toLowerCase();
                if (text.includes('download') || title.includes('download') ||
                    ariaLabel.includes('download') ||
                    text === 'copy' || text.includes('print')) {
                    const rect = btn.getBoundingClientRect();
                    if (rect.width > 0) {
                        out.download_buttons.push({
                            tag: btn.tagName,
                            text: (btn.textContent || '').trim().substring(0, 80),
                            title: btn.title || '',
                            ariaLabel: btn.getAttribute('aria-label') || '',
                            x: Math.round(rect.x), y: Math.round(rect.y),
                            w: Math.round(rect.width), h: Math.round(rect.height),
                        });
                    }
                }
            }

            // Look for any img elements with download/save icons
            for (const img of document.querySelectorAll('img')) {
                const src = (img.src || '').toLowerCase();
                const alt = (img.alt || '').toLowerCase();
                if (src.includes('download') || alt.includes('download') ||
                    src.includes('save') || alt.includes('save')) {
                    const rect = img.getBoundingClientRect();
                    out.download_buttons.push({
                        tag: 'IMG',
                        src: img.src.substring(0, 200),
                        alt: img.alt,
                        x: Math.round(rect.x), y: Math.round(rect.y),
                    });
                }
            }

            // Capture the right panel area (viewer)
            // Look for the panel that would contain the preview
            const rightPanel = document.querySelector('[class*="preview"], [class*="Preview"], [class*="viewer"], [class*="Viewer"]');
            if (rightPanel) {
                out.viewer_html = rightPanel.outerHTML.substring(0, 2000);
            }

            // Also capture the COPY button area and everything to its right
            const copyBtn = Array.from(document.querySelectorAll('button')).find(
                b => (b.textContent || '').trim() === 'COPY'
            );
            if (copyBtn) {
                const copyParent = copyBtn.parentElement;
                if (copyParent) {
                    out.copy_area_html = copyParent.parentElement
                        ? copyParent.parentElement.outerHTML.substring(0, 3000)
                        : copyParent.outerHTML.substring(0, 3000);
                }
            }

            // Get text of the right ~40% of the page
            const pageWidth = window.innerWidth;
            const rightElements = [];
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
            while (walker.nextNode()) {
                const el = walker.currentNode.parentElement;
                if (!el) continue;
                const rect = el.getBoundingClientRect();
                if (rect.left > pageWidth * 0.5 && rect.width > 0 && rect.height > 0) {
                    const text = walker.currentNode.textContent.trim();
                    if (text) rightElements.push(text);
                }
            }
            out.right_panel_text = rightElements.join(' | ').substring(0, 2000);

            return out;
        }""")

        # Also capture a screenshot
        await _tms_browser._debug_rich("after_pod_click")

        return result

    except Exception as e:
        logger.error("POD click test failed: %s", e)
        raise HTTPException(500, f"POD click test failed: {e}")
