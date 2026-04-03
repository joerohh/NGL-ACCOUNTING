"""TMS portal browser automation — fetches PODs via Playwright."""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from config import TMS_SELECTORS_FILE

from .login import TMSLoginMixin
from .search import TMSSearchMixin
from .documents import TMSDocumentsMixin
from .download import TMSDownloadMixin

logger = logging.getLogger("ngl.tms_browser")


def _load_selectors() -> dict:
    """Load TMS DOM selectors from the JSON config file."""
    if TMS_SELECTORS_FILE.exists():
        with open(TMS_SELECTORS_FILE, "r") as f:
            return json.load(f)
    return {}


# ------------------------------------------------------------------
# Structured Results
# ------------------------------------------------------------------
@dataclass
class StageResult:
    """Result of a single pipeline stage."""
    success: bool = False
    strategy_used: str = ""
    strategies_attempted: list = field(default_factory=list)
    error: str = ""
    data: dict = field(default_factory=dict)
    elapsed_s: float = 0.0


@dataclass
class PodRetrievalResult:
    """Full pipeline result for fetch_pod_and_do_sender."""
    pod_path: Optional[Path] = None
    do_sender_email: Optional[str] = None
    do_sender_source: str = ""  # "grid_api", "grid_dom", "detail_page", ""
    pod_download_succeeded: bool = False
    failure_stage: str = ""  # which stage failed (empty = success)
    failure_reason: str = ""
    stages: dict = field(default_factory=dict)  # stage_name → StageResult


class TMSBrowser(TMSLoginMixin, TMSSearchMixin, TMSDocumentsMixin, TMSDownloadMixin):
    """Controls a persistent Chrome browser to interact with the NGL TMS portal."""

    def __init__(self) -> None:
        self._shared_browser = None  # SharedBrowser reference (for lazy init / crash recovery)
        self._context = None
        self._page = None
        self._selectors: dict = _load_selectors()
        self._debug_step = 0
        self._last_do_sender_strategy: str = ""
        self._grid_do_sender: Optional[str] = None  # DO SENDER from grid (before navigation)
        self._recovery_lock = asyncio.Lock()  # prevents concurrent browser recovery

    # ------------------------------------------------------------------
    # Debug
    # ------------------------------------------------------------------
    async def _debug(self, label: str) -> None:
        """Save a debug screenshot + HTML to agent/debug/tms/."""
        from config import TMS_DEBUG_DIR

        self._debug_step += 1
        prefix = f"{self._debug_step:02d}_{label}"
        try:
            if self._page:
                screenshot_path = TMS_DEBUG_DIR / f"{prefix}.png"
                await self._page.screenshot(path=str(screenshot_path), full_page=True)

                html = await self._page.evaluate("""() => {
                    if (!document.body) return '<empty>';
                    // Try focused capture: grid body first (avoids truncation from notification headers)
                    const gridBody = document.querySelector('.ag-body-viewport')
                        || document.querySelector('[ref="eBodyViewport"]')
                        || document.querySelector('.ag-center-cols-viewport');
                    let gridHtml = '';
                    if (gridBody) {
                        gridHtml = '<!-- AG GRID BODY -->' + gridBody.outerHTML.substring(0, 100000);
                    }
                    // Also capture the main page structure (removing hidden/notification bloat)
                    const clone = document.body.cloneNode(true);
                    clone.querySelectorAll('.hidden, [style*="display: none"], [style*="display:none"]').forEach(el => el.remove());
                    // Remove notification/early-warning containers that consume 50K+ chars
                    clone.querySelectorAll('[class*="early-warning"], [class*="notification"], [class*="EarlyWarning"]').forEach(el => el.remove());
                    const bodyHtml = clone.outerHTML.substring(0, 150000);
                    return gridHtml + '\\n<!-- PAGE BODY -->' + bodyHtml;
                }""")
                html_path = TMS_DEBUG_DIR / f"{prefix}.html"
                html_path.write_text(html, encoding="utf-8")

                logger.info("TMS DEBUG [%s]: saved → %s", label, prefix)
        except Exception as e:
            logger.warning("TMS debug capture failed for '%s': %s", label, e)

    async def _capture_page_context(self) -> dict:
        """Capture rich page context for debugging."""
        ctx = {"url": "", "title": "", "viewport": "", "active_tab": "", "visible_inputs": []}
        try:
            if not self._page:
                return ctx
            ctx["url"] = self._page.url
            ctx["title"] = await self._page.title()
            ctx["viewport"] = await self._page.evaluate(
                "() => `${window.innerWidth}x${window.innerHeight}`"
            )
            ctx["active_tab"] = await self._page.evaluate("""() => {
                const sel = document.querySelector('[role="tab"][aria-selected="true"]');
                if (sel) return (sel.textContent || '').trim();
                const active = document.querySelector('.Mui-selected[role="tab"], .active[role="tab"]');
                if (active) return (active.textContent || '').trim();
                return '';
            }""")
            ctx["visible_inputs"] = await self._page.evaluate("""() => {
                const results = [];
                for (const inp of document.querySelectorAll('input')) {
                    const rect = inp.getBoundingClientRect();
                    if (rect.width === 0 || rect.height === 0) continue;
                    let labelText = '';
                    const label = inp.closest('label') || (inp.id && document.querySelector(`label[for="${inp.id}"]`));
                    if (label) labelText = (label.textContent || '').trim();
                    if (!labelText) {
                        const parent = inp.parentElement;
                        if (parent) {
                            for (const sib of parent.children) {
                                if (sib !== inp && sib.tagName !== 'INPUT') {
                                    const t = (sib.textContent || '').trim();
                                    if (t && t.length < 30) { labelText = t; break; }
                                }
                            }
                        }
                    }
                    results.push({
                        placeholder: inp.placeholder || '',
                        aria_label: inp.getAttribute('aria-label') || '',
                        bbox: { x: Math.round(rect.x), y: Math.round(rect.y),
                                w: Math.round(rect.width), h: Math.round(rect.height) },
                        nearby_label: labelText,
                        type: inp.type || 'text',
                        value: (inp.value || '').substring(0, 50),
                    });
                }
                return results;
            }""")
        except Exception as e:
            logger.warning("_capture_page_context failed: %s", e)
        return ctx

    async def _debug_rich(self, label: str) -> dict:
        """Save screenshot + HTML + rich page context. Returns the context dict."""
        await self._debug(label)
        ctx = await self._capture_page_context()
        logger.info(
            "TMS CONTEXT [%s]: url=%s title=%s viewport=%s tab=%s inputs=%d",
            label, ctx["url"], ctx["title"], ctx["viewport"],
            ctx["active_tab"], len(ctx["visible_inputs"]),
        )
        if ctx["visible_inputs"]:
            for i, inp in enumerate(ctx["visible_inputs"][:10]):
                logger.info(
                    "  input[%d]: placeholder=%r aria=%r bbox=%s label=%r",
                    i, inp["placeholder"], inp["aria_label"],
                    inp["bbox"], inp["nearby_label"],
                )
        return ctx

    def _make_error(self, step: str, reason: str, context: dict = None) -> dict:
        """Build a structured error dict for honest failure reporting."""
        err = {"error": True, "step": step, "reason": reason}
        if context:
            err["context"] = {
                "url": context.get("url", ""),
                "title": context.get("title", ""),
                "viewport": context.get("viewport", ""),
                "active_tab": context.get("active_tab", ""),
            }
        logger.error("TMS FAIL [%s]: %s", step, reason)
        return err

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    async def _click_tab(self, tab_name: str) -> bool:
        """Click a tab by its visible text (e.g. 'Detail Info', 'Document')."""
        from config import TMS_ACTION_DELAY_S

        clicked = await self._page.evaluate("""(tabName) => {
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
    # Work Order Validation
    # ------------------------------------------------------------------
    # Location code → dropdown label, Type code → URL segment + tab text
    LOCATION_MAP = {"L": "LA", "P": "PHX", "H": "HOU", "S": "SAV", "M": "MOB"}
    TYPE_MAP = {"M": ("imp", "IMPORT"), "E": ("exp", "EXPORT")}

    def parse_invoice_prefix(self, invoice_number: str) -> tuple:
        """Parse invoice number prefix to determine location and type.

        Returns (location_code, url_segment, tab_text) or (None, None, None).
        E.g. 'LM1234' → ('LA', 'imp', 'IMPORT'), 'PE5678' → ('PHX', 'exp', 'EXPORT')
        """
        if not invoice_number or len(invoice_number) < 2:
            return None, None, None
        prefix = invoice_number.strip().upper()[:2]
        loc = self.LOCATION_MAP.get(prefix[0])
        type_info = self.TYPE_MAP.get(prefix[1])
        if loc and type_info:
            return loc, type_info[0], type_info[1]
        return None, None, None


