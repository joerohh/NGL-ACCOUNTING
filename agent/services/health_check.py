"""Selector health checks for QBO and TMS browsers.

Checks critical DOM selectors on the current page without navigating.
Returns a report of which selectors were found/missing.
"""

import logging
from typing import Optional

logger = logging.getLogger("ngl.health_check")


# -- QBO selector checks ---------------------------------------------------

# Selectors grouped by the page they should appear on
QBO_CHECKS = {
    "login_page": {
        "description": "QBO Login Page",
        "url_match": "accounts.intuit.com",
        "selectors": {
            "email_input": "input#ius-userid",
            "password_input": "input#ius-password",
            "sign_in_button": "button[data-testid='ius-sign-in-submit-btn']",
        },
    },
    "app_page": {
        "description": "QBO App (any page)",
        "url_match": "qbo.intuit.com",
        "selectors": {
            "global_search": (
                "#global-search-input, "
                'input[placeholder*="search" i], '
                'input[placeholder*="navigate" i], '
                "input[data-id='global-search-input']"
            ),
            "navigation": "[data-testid='qbo-nav'], .qbo-nav, #globalNavigation",
        },
    },
    "invoice_page": {
        "description": "QBO Invoice Detail",
        "url_match": "invoice",
        "selectors": {
            "attachments_area": (
                "[data-testid='attachments'], "
                ".attachments-section, "
                ".txn-attachments"
            ),
        },
    },
}


# -- TMS selector checks ---------------------------------------------------

TMS_CHECKS = {
    "login_page": {
        "description": "TMS Login Page",
        "url_match": "sign-in",
        "selectors": {
            "google_sso_button": (
                'button:has-text("Google"), '
                'a:has-text("Google"), '
                '[data-provider="google"]'
            ),
        },
    },
    "main_page": {
        "description": "TMS Main Grid",
        "url_match": "/main/",
        "selectors": {
            "ag_grid": ".ag-root-wrapper, .ag-root, [role='grid']",
            "grid_rows": ".ag-row, [role='row']",
            "sidebar": "div.cursor-pointer",
        },
    },
    "detail_page": {
        "description": "TMS Work Order Detail",
        "url_match": "/bc-detail/",
        "selectors": {
            "detail_markers": (
                "text='DETAIL INFO', text='BILLING INFO', "
                "[role='tab']"
            ),
        },
    },
    "document_tab": {
        "description": "TMS Document Tab",
        "url_match": "/document/",
        "selectors": {
            "file_inputs": "input[type='search'][readonly]",
            "save_button": "button:has-text('SAVE')",
        },
    },
}


async def check_qbo_selectors(qbo_browser) -> dict:
    """Run selector health checks against the current QBO page.

    Returns:
        {
            "status": "ok" | "warning" | "error" | "offline",
            "current_url": "...",
            "page_type": "app_page" | "login_page" | ...,
            "checks": [
                {"name": "global_search", "selector": "...", "found": True},
                ...
            ],
            "passed": 2,
            "failed": 0,
            "total": 2,
        }
    """
    result = {
        "status": "offline",
        "current_url": "",
        "page_type": "unknown",
        "checks": [],
        "passed": 0,
        "failed": 0,
        "total": 0,
    }

    if not qbo_browser or not qbo_browser._page:
        return result

    try:
        await qbo_browser._page.evaluate("() => true")
    except Exception:
        return result

    url = qbo_browser._page.url or ""
    result["current_url"] = url

    # Determine which check group applies based on current URL
    matched_group = None
    for group_name, group in QBO_CHECKS.items():
        if group["url_match"] in url:
            matched_group = (group_name, group)
            # Don't break — later matches (more specific) override

    if not matched_group:
        result["status"] = "ok"
        result["page_type"] = "other"
        return result

    group_name, group = matched_group
    result["page_type"] = group_name

    for sel_name, selector in group["selectors"].items():
        found = await _check_selector(qbo_browser._page, selector)
        result["checks"].append({
            "name": sel_name,
            "selector": selector[:80],
            "found": found,
        })
        result["total"] += 1
        if found:
            result["passed"] += 1
        else:
            result["failed"] += 1

    result["status"] = "ok" if result["failed"] == 0 else (
        "warning" if result["failed"] < result["total"] else "error"
    )
    return result


async def check_tms_selectors(tms_browser) -> dict:
    """Run selector health checks against the current TMS page."""
    result = {
        "status": "offline",
        "current_url": "",
        "page_type": "unknown",
        "checks": [],
        "passed": 0,
        "failed": 0,
        "total": 0,
    }

    if not tms_browser or not tms_browser._page:
        return result

    try:
        await tms_browser._page.evaluate("() => true")
    except Exception:
        return result

    url = (tms_browser._page.url or "").lower()
    result["current_url"] = tms_browser._page.url or ""

    matched_group = None
    for group_name, group in TMS_CHECKS.items():
        if group["url_match"] in url:
            matched_group = (group_name, group)

    if not matched_group:
        result["status"] = "ok"
        result["page_type"] = "other"
        return result

    group_name, group = matched_group
    result["page_type"] = group_name

    for sel_name, selector in group["selectors"].items():
        # TMS uses some text-based selectors — handle differently
        if selector.startswith("text="):
            found = await _check_text_selectors(tms_browser._page, selector)
        else:
            found = await _check_selector(tms_browser._page, selector)
        result["checks"].append({
            "name": sel_name,
            "selector": selector[:80],
            "found": found,
        })
        result["total"] += 1
        if found:
            result["passed"] += 1
        else:
            result["failed"] += 1

    result["status"] = "ok" if result["failed"] == 0 else (
        "warning" if result["failed"] < result["total"] else "error"
    )
    return result


async def _check_selector(page, selector: str) -> bool:
    """Check if any element matching the CSS selector exists on the page.

    Handles comma-separated selectors (any one match = pass).
    """
    try:
        # Use querySelectorAll with each part of the comma-separated selector
        count = await page.evaluate("""(sel) => {
            try {
                return document.querySelectorAll(sel).length;
            } catch(e) {
                // If the combined selector fails, try each part
                const parts = sel.split(',').map(s => s.trim());
                for (const part of parts) {
                    try {
                        if (document.querySelectorAll(part).length > 0) return 1;
                    } catch(e2) {}
                }
                return 0;
            }
        }""", selector)
        return count > 0
    except Exception:
        return False


async def _check_text_selectors(page, selector: str) -> bool:
    """Check text-based selectors like "text='DETAIL INFO', text='BILLING INFO'"."""
    try:
        parts = [s.strip() for s in selector.split(",")]
        for part in parts:
            if part.startswith("text="):
                text = part[6:-1] if part[5] == "'" else part[5:]
                found = await page.evaluate("""(searchText) => {
                    return document.body.innerText.includes(searchText);
                }""", text)
                if found:
                    return True
            else:
                count = await page.evaluate("""(sel) => {
                    try { return document.querySelectorAll(sel).length; }
                    catch(e) { return 0; }
                }""", part)
                if count > 0:
                    return True
        return False
    except Exception:
        return False
