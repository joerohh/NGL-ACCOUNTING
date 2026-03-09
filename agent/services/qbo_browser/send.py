"""QBO Send mixin — fill email form and send invoices."""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger("ngl.qbo_browser")


class QBOSendMixin:
    """Navigate the send form, fill fields, and send invoices."""

    async def click_back_from_send_form(self) -> bool:
        """Click the 'Back' link on the send form to return to the invoice edit page.

        Returns True if navigation back succeeded.
        """
        await self._ensure_browser()
        try:
            clicked = await self._page.evaluate("""() => {
                const candidates = document.querySelectorAll('a, button, [role="button"]');
                for (const el of candidates) {
                    const text = (el.textContent || '').trim().toLowerCase();
                    if (text === 'back') {
                        el.click();
                        return true;
                    }
                }
                return false;
            }""")
            if clicked:
                logger.info("Clicked 'Back' from send form")
                await self._page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(3)
                return True
            else:
                logger.warning("Could not find 'Back' link on send form")
                return False
        except Exception as e:
            logger.error("Failed to click Back: %s", e)
            return False

    async def click_review_and_send(self) -> bool:
        """Click the 'Review and send' button on the invoice detail page.

        Returns True if the send form loaded, False otherwise.
        """
        await self._ensure_browser()

        try:
            # Find and click "Review and send" button
            clicked = await self._page.evaluate("""() => {
                const candidates = document.querySelectorAll('a, button, [role="button"], input[type="button"]');
                for (const el of candidates) {
                    const text = (el.textContent || el.innerText || el.value || '').trim().toLowerCase();
                    if (text.includes('review and send') || text.includes('review & send')) {
                        el.click();
                        return { clicked: true, tag: el.tagName, text: text };
                    }
                }
                return null;
            }""")

            if not clicked:
                logger.error("Could not find 'Review and send' button")
                await self._debug("review_send_NOT_FOUND")
                return False

            logger.info("Clicked 'Review and send': %s", clicked)
            await self._page.wait_for_load_state("domcontentloaded")

            # Poll for Subject field instead of fixed 8s sleep (saves ~5s)
            for _ in range(24):  # 24 × 0.5s = 12s max wait
                has_subj = await self._page.evaluate("""() => {
                    const inputs = document.querySelectorAll('input, textarea');
                    for (const inp of inputs) {
                        const label = (inp.getAttribute('aria-label') || '').toLowerCase();
                        const name = (inp.getAttribute('name') || '').toLowerCase();
                        if (label.includes('subject') || name.includes('subject')) return true;
                    }
                    return false;
                }""")
                if has_subj:
                    break
                await asyncio.sleep(0.5)

            # Verify the send form loaded by checking for the Subject field
            has_subject = await self._page.evaluate("""() => {
                const inputs = document.querySelectorAll('input, textarea');
                for (const inp of inputs) {
                    const label = (inp.getAttribute('aria-label') || '').toLowerCase();
                    const name = (inp.getAttribute('name') || '').toLowerCase();
                    const placeholder = (inp.getAttribute('placeholder') || '').toLowerCase();
                    if (label.includes('subject') || name.includes('subject') || placeholder.includes('subject')) {
                        return true;
                    }
                }
                // Also check for "Subject" label text near an input
                const labels = document.querySelectorAll('label, td, th, span');
                for (const lbl of labels) {
                    if ((lbl.textContent || '').trim().toLowerCase() === 'subject') return true;
                }
                return false;
            }""")

            if has_subject:
                logger.info("Send form loaded — Subject field found")
                return True
            else:
                # Double-check: wait a bit more and retry — QBO React may still be rendering
                await asyncio.sleep(3)
                has_subject_retry = await self._page.evaluate("""() => {
                    const inputs = document.querySelectorAll('input, textarea');
                    for (const inp of inputs) {
                        const label = (inp.getAttribute('aria-label') || '').toLowerCase();
                        const name = (inp.getAttribute('name') || '').toLowerCase();
                        const placeholder = (inp.getAttribute('placeholder') || '').toLowerCase();
                        if (label.includes('subject') || name.includes('subject') || placeholder.includes('subject')) {
                            return true;
                        }
                    }
                    const labels = document.querySelectorAll('label, td, th, span');
                    for (const lbl of labels) {
                        if ((lbl.textContent || '').trim().toLowerCase() === 'subject') return true;
                    }
                    return false;
                }""")
                if has_subject_retry:
                    logger.info("Send form loaded on retry — Subject field found")
                    return True
                else:
                    logger.error("Send form did NOT load — Subject field not found after retry")
                    await self._debug("review_send_FAILED_no_subject")
                    return False

        except Exception as e:
            logger.error("Failed to click 'Review and send': %s", e)
            await self._debug("review_send_FAILED")
            return False

    async def fill_send_form(
        self,
        to_emails: list[str],
        cc_emails: list[str],
        subject: str,
        bcc_emails: Optional[list[str]] = None,
        expected_attachment_count: int = 0,
    ) -> dict:
        """Fill in the email form on the QBO 'Review and Send' screen.

        Args:
            to_emails: recipient email addresses for the To field
            cc_emails: CC email addresses (always includes ar@ngltrans.net)
            subject: the formatted subject line
            bcc_emails: BCC email addresses (optional)
            expected_attachment_count: number of attachments found on the invoice detail
                page — used to wait for the send form's attachment list to fully render

        Returns: { filled: bool, toEmails: list, ccEmails: list, subject: str }
        """
        await self._ensure_browser()

        result = {
            "filled": False,
            "toEmails": to_emails,
            "ccEmails": cc_emails,
            "subject": subject,
        }

        try:
            filled = {"to": False, "cc": False, "bcc": False, "subject": False, "attachments": False}

            # --- Verify attachments appear on the send form ---
            # Attachment checkboxes were already clicked on the invoice EDIT page
            # (in check_attachments_on_page).  The send form only shows a flat list
            # of attached files — no checkboxes.  We verify the expected count here.
            MAX_ATT_VERIFY = 4
            ATT_VERIFY_WAIT = [0, 3, 4, 5]
            att_info = {"count": 0, "names": []}

            for verify_attempt in range(MAX_ATT_VERIFY):
                if verify_attempt > 0:
                    await asyncio.sleep(ATT_VERIFY_WAIT[verify_attempt])

                att_info = await self._page.evaluate("""() => {
                    const pdfTexts = Array.from(document.querySelectorAll('span, a, div, label'))
                        .filter(el => {
                            const text = (el.textContent || '').trim().toLowerCase();
                            return text.endsWith('.pdf') && text.length < 100;
                        });
                    return {
                        count: pdfTexts.length,
                        names: pdfTexts.map(el => (el.textContent || '').trim()).slice(0, 10),
                    };
                }""")

                logger.info("Send form attachment verify %d/%d: %d items %s",
                            verify_attempt + 1, MAX_ATT_VERIFY,
                            att_info["count"], att_info["names"])

                if expected_attachment_count == 0 or att_info["count"] >= expected_attachment_count:
                    filled["attachments"] = True
                    break
            else:
                logger.warning("Send form shows %d/%d expected attachments after %d checks",
                               att_info["count"], expected_attachment_count, MAX_ATT_VERIFY)
                filled["attachments"] = att_info["count"] > 0

            # --- Fill TO field (CRITICAL — abort if not found) ---
            to_input = await self._page.query_selector("#email_to")
            if not to_input:
                to_input = await self._page.query_selector('input[name="email_to"]')
            if not to_input:
                # Last attempt: wait for React to render and retry
                await asyncio.sleep(2)
                to_input = await self._page.query_selector("#email_to")
                if not to_input:
                    to_input = await self._page.query_selector('input[name="email_to"]')
            if to_input:
                await to_input.fill(", ".join(to_emails))
                filled["to"] = True
                logger.info("Filled To: %s", to_emails)
            else:
                logger.error("CRITICAL: Could not find To field — aborting form fill")
                await self._debug("fill_form_NO_TO_FIELD")
                result["filled"] = False
                return result

            # --- Click CC/BCC toggle to reveal CC and BCC fields ---
            try:
                cc_toggle = await self._page.query_selector("a.ccbcc-toggle")
                if not cc_toggle:
                    # Fallback: find by text content
                    cc_toggle = await self._page.evaluate("""() => {
                        const links = document.querySelectorAll('a, button, span');
                        for (const el of links) {
                            const text = (el.textContent || '').trim().toLowerCase();
                            if (text.includes('cc') && text.includes('bcc')) {
                                el.click();
                                return true;
                            }
                        }
                        return false;
                    }""")
                    if cc_toggle:
                        logger.info("Clicked CC/BCC toggle via JS fallback")
                else:
                    await cc_toggle.click()
                    logger.info("Clicked CC/BCC toggle")

                # Wait for CC input to appear after React re-renders
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.warning("Could not click CC/BCC toggle: %s", e)

            # --- Fill CC field ---
            if cc_emails:
                cc_input = await self._page.query_selector("#email_cc")
                if not cc_input:
                    cc_input = await self._page.query_selector('input[name="email_cc"]')
                if not cc_input:
                    # Wait a bit more for React to render
                    await asyncio.sleep(1)
                    cc_input = await self._page.query_selector("#email_cc")
                if cc_input:
                    await cc_input.fill(", ".join(cc_emails))
                    filled["cc"] = True
                    logger.info("Filled CC: %s", cc_emails)
                else:
                    logger.warning("Could not find CC field (#email_cc)")
            else:
                filled["cc"] = True  # No CC needed

            # --- Fill BCC field ---
            if bcc_emails:
                bcc_input = await self._page.query_selector("#email_bcc")
                if not bcc_input:
                    bcc_input = await self._page.query_selector('input[name="email_bcc"]')
                if bcc_input:
                    await bcc_input.fill(", ".join(bcc_emails))
                    filled["bcc"] = True
                    logger.info("Filled BCC: %s", bcc_emails)
                else:
                    logger.warning("Could not find BCC field (#email_bcc)")
            else:
                filled["bcc"] = True  # No BCC needed

            # --- Fill SUBJECT field ---
            subject_input = await self._page.query_selector("#email_subject")
            if not subject_input:
                subject_input = await self._page.query_selector('input[name="email_subject"]')
            if subject_input:
                await subject_input.fill(subject)
                filled["subject"] = True
                logger.info("Filled Subject: %s", subject)
            else:
                logger.warning("Could not find Subject field (#email_subject)")

            logger.info("Form fill results: %s", filled)
            await asyncio.sleep(0.5)
            await self._debug("fill_form_done")

            # Only mark as filled if the critical To field was populated
            result["filled"] = filled["to"]
            result["attachmentsFull"] = filled["attachments"]
            if not filled["to"]:
                logger.error("Form fill incomplete — To field was not filled")
            return result

        except Exception as e:
            logger.error("Failed to fill send form: %s", e)
            await self._debug("fill_form_FAILED")
            result["filled"] = False
            return result

    async def click_send_invoice(self) -> bool:
        """Click the green 'Send invoice' button (NOT 'Send and fund').

        Returns True if the send appeared to succeed, False otherwise.
        """
        await self._ensure_browser()

        # Remember current URL to detect navigation after send
        pre_send_url = self._page.url

        try:
            clicked = await self._page.evaluate("""() => {
                // Look for the green "Send invoice" button (bottom-right)
                // Avoid "Send and fund" button
                const buttons = document.querySelectorAll('button, a, [role="button"], input[type="submit"]');
                for (const btn of buttons) {
                    const text = (btn.textContent || btn.innerText || btn.value || '').trim().toLowerCase();
                    // Match "send invoice" but NOT "send and fund"
                    if (text === 'send invoice' || text === 'send') {
                        // Extra check: skip if it says "fund"
                        if (text.includes('fund')) continue;
                        btn.click();
                        return { clicked: true, tag: btn.tagName, text: text };
                    }
                }
                return null;
            }""")

            if not clicked:
                logger.error("Could not find 'Send invoice' button")
                await self._debug("send_button_NOT_FOUND")
                return False

            logger.info("Clicked 'Send invoice' button: %s", clicked)

            # Poll for URL change instead of fixed 8s sleep (saves ~5s)
            for _ in range(20):  # 20 × 0.5s = 10s max wait
                await asyncio.sleep(0.5)
                post_send_url = self._page.url
                if post_send_url != pre_send_url:
                    logger.info("Send successful — page navigated from %s to %s",
                               pre_send_url[:80], post_send_url[:80])
                    await self._debug("send_SUCCESS")
                    return True

            # Check for any error messages on the page
            errors = await self._page.evaluate("""() => {
                const errorEls = document.querySelectorAll('[class*="error" i], [class*="alert" i], [role="alert"]');
                return Array.from(errorEls).map(e => (e.textContent || '').trim()).filter(t => t.length > 0);
            }""")

            if errors:
                logger.error("Send form has errors: %s", errors)
                await self._debug("send_ERRORS_on_page")
                return False

            # No navigation but no errors — assume success (some QBO versions stay on page)
            logger.info("Send button clicked, no errors detected — assuming success")
            await self._debug("send_ASSUMED_SUCCESS")
            return True

        except Exception as e:
            logger.error("Failed to click Send invoice: %s", e)
            await self._debug("send_FAILED")
            return False
