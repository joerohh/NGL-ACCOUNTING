"""QBO Download mixin — attachment and invoice PDF downloads."""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Optional

from config import (
    BROWSER_DOWNLOADS_DIR,
    DEBUG_DIR,
    QBO_RETRY_COUNT,
    QBO_RETRY_BACKOFF_S,
)
from utils import strip_motw

logger = logging.getLogger("ngl.qbo_browser")


class QBODownloadMixin:
    """Download invoice PDFs and POD attachments from QBO."""

    async def _download_attachment(self, link, download_dir: Path, label: str,
                                    original_filename: str = "", *, page=None) -> Optional[Path]:
        """Download an attachment by clicking its link on the invoice page.
        QBO attachment links either trigger a direct download or open in a new tab.
        original_filename: the link text (e.g. 'lm2601120027_pod.pdf') to use as filename
        when QBO gives us a UUID-named download.

        Args:
            page: Optional Playwright page for parallel workers. Uses main page if None.
        """
        p = page or self._page
        await self._debug(f"before_download_{label}", page=p)

        # Detect if this link opens a new tab (target="_blank" or external doc URL)
        target = (await link.get_attribute("target") or "").strip()
        href = (await link.get_attribute("href") or "").strip()
        opens_new_tab = target == "_blank" or "financialdocument" in href

        for attempt in range(QBO_RETRY_COUNT):
            # Snapshot current pages so we can detect truly new tabs
            pages_before = set(id(pg) for pg in self._context.pages)

            try:
                # ── Direct fetch (for new-tab links) ─────────────────────
                # Fetch the PDF via JS fetch() on the current page — never
                # click the link, never open a new tab, zero Chrome downloads.
                if opens_new_tab and href:
                    logger.info("%s: fetching PDF directly via href (no click, no new tab)", label)
                    content = await p.evaluate("""async (url) => {
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
                        await self._debug(f"download_SUCCESS_{label}", page=p)
                        return dest
                    else:
                        logger.warning("Direct fetch returned non-PDF for %s (first bytes: %s)",
                                       label, bytes(content[:20]) if content else b'empty')

                # ── Method 1: expect_download (same-page download links) ─
                if not opens_new_tab:
                    try:
                        async with p.expect_download(timeout=5000) as download_info:
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
                        await self._debug(f"download_SUCCESS_{label}", page=p)
                        self._cleanup_browser_downloads()
                        return dest
                    except Exception as e:
                        logger.info("Download method 1 failed for %s: %s", label, e)

                # ── Method 2: Check for new tab (fallback if click opened one)
                self._cleanup_browser_downloads()
                await asyncio.sleep(2)
                new_tabs = [pg for pg in self._context.pages if id(pg) not in pages_before]
                if new_tabs:
                    new_page = new_tabs[-1]
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
                            await self._debug(f"download_SUCCESS_{label}", page=p)
                            self._cleanup_browser_downloads()
                            return dest
                    else:
                        await new_page.close()

            except Exception as e:
                backoff = QBO_RETRY_BACKOFF_S * (2 ** attempt)
                logger.warning("%s download attempt %d failed: %s (retry in %.1fs)",
                               label, attempt + 1, e, backoff)
                await self._debug(f"download_attempt{attempt+1}_FAILED_{label}", page=p)
                await asyncio.sleep(backoff)
                # Close any unexpected new tabs (but not our worker pages)
                known = set(id(pg) for pg in [self._page] + self._worker_pages)
                for pg in self._context.pages:
                    if id(pg) not in known:
                        try:
                            await pg.close()
                        except Exception:
                            pass

        await self._debug(f"download_ALL_FAILED_{label}", page=p)
        logger.error("Failed to download %s after %d attempts", label, QBO_RETRY_COUNT)
        self._cleanup_browser_downloads()
        return None

    async def download_invoice_pdf(self, download_dir: Path, *, page=None) -> Optional[Path]:
        """
        Download the invoice PDF from the currently-open invoice page.

        The QBO invoice page has a bottom action bar with "Print or download"
        which opens a dropdown with: Print | Download | Print packing slip.
        We click "Print or download" → then click "Download" in the dropdown.

        Fallback: Look for invoice attachments in the page (files like *_it.pdf).

        Args:
            page: Optional Playwright page for parallel workers. Uses main page if None.
        """
        await self._ensure_page(page)
        p = page or self._page
        await self._debug("invoice_page_before_download", page=p)

        # Remember the invoice URL so we can verify we stay on it
        invoice_url = p.url

        # Strategy 1: Click "Print or download" in bottom bar → "Download"
        try:
            # The "Print or download" link is in the bottom action bar of the invoice
            pod_link = await p.query_selector(
                'a:has-text("Print or download"), '
                'button:has-text("Print or download")'
            )
            if pod_link:
                await pod_link.click()
                await asyncio.sleep(1.5)
                await self._debug("print_download_dropdown_opened", page=p)

                # Now click the "Download" option in the dropdown
                try:
                    async with p.expect_download(timeout=20000) as download_info:
                        # The dropdown shows plain text items: "Print", "Download", "Print packing slip"
                        # Use evaluate to find and click the exact "Download" text
                        clicked = await p.evaluate("""() => {
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
                    await self._debug("download_SUCCESS_invoice", page=p)
                    self._cleanup_browser_downloads()
                    return dest
                except Exception as e:
                    logger.info("Download via dropdown failed: %s", e)
                    await self._debug("download_dropdown_FAILED", page=p)

                    # Press Escape to close the dropdown without navigating away
                    await p.keyboard.press("Escape")
                    await asyncio.sleep(0.5)
            else:
                logger.info("'Print or download' link not found in bottom bar")
                await self._debug("print_or_download_NOT_FOUND", page=p)
        except Exception as e:
            logger.info("Print or download flow failed: %s, trying attachments", e)
            await self._debug("print_download_flow_FAILED", page=p)

        # Make sure we're still on the invoice page (not navigated away)
        current = p.url
        if current != invoice_url and "invoice" not in current and "txnId" not in current:
            logger.warning("Page navigated away from invoice (%s), going back", current)
            await p.goto(invoice_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(5)

        # Strategy 2: Look for an invoice attachment in the Attachments section
        await p.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(3)
        await self._debug("scrolled_to_bottom_for_invoice", page=p)

        # Dump all links on the page so we can see what's available
        link_dump = await p.evaluate("""() => {
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

        all_links = await p.query_selector_all("a")
        for link in all_links:
            try:
                text = (await link.inner_text()).strip().lower()
            except Exception:
                continue
            if text.endswith(".pdf") and "invoice" in text:
                logger.info("Found invoice attachment: %s", text)
                return await self._download_attachment(link, download_dir, "invoice",
                                                       original_filename=text, page=p)

        await self._debug("invoice_attachment_NOT_FOUND", page=p)
        logger.warning("Could not download invoice PDF — no suitable method found")
        return None

    async def find_and_download_pod(self, download_dir: Path, *, page=None) -> Optional[Path]:
        """
        Look for a POD attachment on the current invoice page.
        QBO invoices have an "Attachments" section at the bottom with file links.
        POD files are typically named like: *_pod.pdf
        Returns the file path if found and downloaded, None if no POD exists.

        Args:
            page: Optional Playwright page for parallel workers. Uses main page if None.
        """
        await self._ensure_page(page)
        p = page or self._page

        # Verify we're still on the invoice page (not navigated away)
        current = p.url
        if "invoice" not in current and "txnId" not in current:
            logger.warning("POD check: not on invoice page (%s), cannot check attachments", current)
            await self._debug("pod_check_WRONG_PAGE", page=p)
            return None

        # Scroll to bottom of the invoice page to ensure Attachments section is visible
        await p.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(3)
        await self._debug("scrolled_for_pod_check", page=p)

        # Dump all links so we can see what attachments are on this page
        link_dump = await p.evaluate("""() => {
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
        all_links = await p.query_selector_all("a")
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
            await self._debug("pod_NOT_FOUND", page=p)
            logger.info("No POD attachment found on this invoice")
            return None

        logger.info("Found POD attachment link: %s", pod_name)
        await self._debug(f"pod_found_{pod_name}", page=p)
        return await self._download_attachment(pod_link, download_dir, "POD",
                                               original_filename=pod_name, page=p)
