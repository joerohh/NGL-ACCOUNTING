"""QBO Invoice mixin — verify details, check/select attachments."""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger("ngl.qbo_browser")


class QBOInvoiceMixin:
    """Verify invoice details and manage attachments on the invoice page."""

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

            # Compare amount (informational — QBO is source of truth)
            if expected_amount:
                found_amt = (page_data.get("amount") or "").replace("$", "").replace(",", "")
                expected_amt = expected_amount.replace("$", "").replace(",", "")
                try:
                    if found_amt and abs(float(found_amt) - float(expected_amt)) > 0.01:
                        note = f"Amount differs: Excel ${expected_amt}, QBO ${found_amt} — using QBO amount"
                        result["amount_note"] = note
                        logger.warning("Invoice amount note: %s", note)
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
