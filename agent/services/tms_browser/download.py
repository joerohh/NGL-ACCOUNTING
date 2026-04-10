"""TMSDownloadMixin — document download and POD retrieval."""

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

from config import TMS_DOWNLOADS_DIR

logger = logging.getLogger("ngl.tms_browser")


class TMSDownloadMixin:
    """Document download and high-level POD retrieval."""

    async def download_document(
        self, doc_type: str, download_dir: Path, filename: str = ""
    ) -> Optional[Path]:
        """Download a document from the Document tab by type.

        The Document tab uses a div-based layout (no tables, no <a> links).
        Each doc row has an input[name="file.{TYPE}.{TYPE}_file_name"].

        Strategies:
        A) Click the document row → intercept PDF network response
        B) After click, check viewer panel for iframe/embed → fetch its src
        C) Check TMS_DOWNLOADS_DIR for browser-downloaded file
        """
        try:
            await self._debug_rich("before_download")

            if not filename:
                filename = f"document_{doc_type}.pdf"
            if not filename.lower().endswith(".pdf"):
                filename += ".pdf"

            # Verify the file input exists and has a value
            input_id = f"file.{doc_type}.{doc_type}_file_name"
            file_value = await self._page.evaluate("""(inputId) => {
                const inp = document.getElementById(inputId);
                return inp ? (inp.value || '') : null;
            }""", input_id)

            if file_value is None:
                ctx = await self._debug_rich("no_file_input")
                self._make_error("download_document", f"No file input for doc type {doc_type}", ctx)
                return None
            if not file_value:
                ctx = await self._debug_rich("no_file_uploaded")
                self._make_error("download_document", f"No file uploaded for {doc_type}", ctx)
                return None

            logger.info("Download target: type=%s filename=%s", doc_type, filename)

            # Clear old files from downloads dir so we can detect new ones
            dl_before = set()
            for f in TMS_DOWNLOADS_DIR.glob("*"):
                dl_before.add(f.name)

            # ── Method A: Network intercept — click row, capture PDF response ──
            logger.info("Download Method A: network intercept for %s", doc_type)
            pdf_data_holder = {"data": None}

            async def _on_response(response):
                try:
                    ct = response.headers.get("content-type", "")
                    url = response.url
                    if "pdf" in ct or url.endswith(".pdf") or "download" in url.lower():
                        body = await response.body()
                        if len(body) >= 5 and body[:5] == b'%PDF-':
                            pdf_data_holder["data"] = body
                except Exception:
                    pass

            self._page.on("response", _on_response)
            try:
                # Click the date cell in the document row to select it
                clicked = await self._page.evaluate("""(docType) => {
                    // Find the doc type span
                    const spans = document.querySelectorAll('span');
                    for (const span of spans) {
                        const text = (span.textContent || '').trim();
                        if (text !== docType) continue;
                        // Walk up to the row container (flex items-center)
                        let row = span.closest('div.flex.items-center');
                        if (!row) continue;
                        // The row might be nested: outer > inner > columns
                        // Find the date column (div[width="150"])
                        const dateCol = row.querySelector('div[width="150"]');
                        if (dateCol) {
                            dateCol.click();
                            return 'date_click';
                        }
                        // Fallback: click the span itself
                        span.click();
                        return 'span_click';
                    }
                    return null;
                }""", doc_type)

                if clicked:
                    logger.info("Clicked %s row via %s", doc_type, clicked)
                    # Wait for network response with PDF data
                    for _ in range(20):  # 10 seconds max
                        if pdf_data_holder["data"]:
                            break
                        await asyncio.sleep(0.5)

                    if pdf_data_holder["data"]:
                        save_path = download_dir / filename
                        save_path.write_bytes(pdf_data_holder["data"])
                        logger.info(
                            "Downloaded via network intercept: %s (%d bytes)",
                            save_path, len(pdf_data_holder["data"]),
                        )
                        await self._debug("download_SUCCESS")
                        return save_path
                    else:
                        logger.info("Method A: no PDF captured via network intercept")
                else:
                    logger.warning("Method A: could not click %s row", doc_type)
            finally:
                self._page.remove_listener("response", _on_response)

            # ── Method B: Check viewer for iframe/embed/object ──
            logger.info("Download Method B: checking viewer for embedded content")
            await asyncio.sleep(2)

            viewer_src = await self._page.evaluate("""() => {
                for (const tag of ['iframe', 'embed', 'object']) {
                    for (const el of document.querySelectorAll(tag)) {
                        const src = el.src || el.getAttribute('data') || '';
                        if (src && src.length > 10) return src;
                    }
                }
                return null;
            }""")

            if viewer_src:
                logger.info("Method B: found viewer src=%s", viewer_src[:100])
                if "blob:" not in viewer_src:
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
                        }""", viewer_src)

                        if isinstance(content, dict) and "data" in content:
                            data = bytes(content["data"])
                            if len(data) >= 5 and data[:5] == b'%PDF-':
                                save_path = download_dir / filename
                                save_path.write_bytes(data)
                                logger.info(
                                    "Downloaded via viewer URL: %s (%d bytes)", save_path, len(data),
                                )
                                await self._debug("download_SUCCESS")
                                return save_path
                    except Exception as e:
                        logger.warning("Method B fetch failed: %s", e)
                else:
                    # Blob URL — try to read it from within the page context
                    try:
                        content = await self._page.evaluate("""async (blobUrl) => {
                            try {
                                const resp = await fetch(blobUrl);
                                const buf = await resp.arrayBuffer();
                                return { data: Array.from(new Uint8Array(buf)) };
                            } catch (e) {
                                return { error: e.message };
                            }
                        }""", viewer_src)

                        if isinstance(content, dict) and "data" in content:
                            data = bytes(content["data"])
                            if len(data) >= 5 and data[:5] == b'%PDF-':
                                save_path = download_dir / filename
                                save_path.write_bytes(data)
                                logger.info(
                                    "Downloaded via blob URL: %s (%d bytes)", save_path, len(data),
                                )
                                await self._debug("download_SUCCESS")
                                return save_path
                    except Exception as e:
                        logger.warning("Method B blob fetch failed: %s", e)

            # ── Method C: Check TMS downloads directory for new files ──
            logger.info("Download Method C: checking TMS downloads dir")
            await asyncio.sleep(3)
            for f in sorted(TMS_DOWNLOADS_DIR.glob("*"), key=os.path.getmtime, reverse=True):
                if f.name in dl_before:
                    continue  # Skip files that existed before the click
                if f.is_file() and f.stat().st_size > 100:
                    try:
                        with open(f, "rb") as fh:
                            magic = fh.read(5)
                        if magic != b"%PDF-":
                            logger.warning("Method C: %s not a PDF (magic: %r)", f.name, magic)
                            continue
                    except Exception:
                        pass
                    save_path = download_dir / filename
                    shutil.copy2(str(f), str(save_path))
                    logger.info(
                        "Downloaded from downloads dir: %s (%d bytes)", save_path, f.stat().st_size,
                    )
                    await self._debug("download_SUCCESS")
                    return save_path

            ctx = await self._debug_rich("download_all_failed")
            self._make_error("download_document", f"All download methods failed for {doc_type}", ctx)
            return None

        except Exception as e:
            logger.error("TMS document download failed: %s", e)
            await self._debug_rich("download_error")
            return None

    # ------------------------------------------------------------------
    # File Validation
    # ------------------------------------------------------------------
    @staticmethod
    def validate_downloaded_file(path: Path) -> tuple[bool, str]:
        """Validate a downloaded file: exists, non-zero, valid PDF.

        Returns (is_valid, error_message). error_message is empty on success.
        """
        if not path.exists():
            return False, f"File does not exist: {path}"

        size = path.stat().st_size
        if size == 0:
            try:
                path.unlink()
            except Exception:
                pass
            return False, f"File is zero bytes: {path}"

        try:
            with open(path, "rb") as f:
                magic = f.read(5)
            if magic != b"%PDF-":
                return False, f"Not a valid PDF (magic bytes: {magic!r}): {path}"
        except Exception as e:
            return False, f"Could not read file for validation: {e}"

        return True, ""

    # ------------------------------------------------------------------
    # High-level: Fetch POD for a container
    # ------------------------------------------------------------------
    async def fetch_pod_for_container(
        self, container_number: str, download_dir: Path,
        invoice_number: str = ""
    ) -> Optional[Path]:
        """End-to-end: search container → Documents tab → find POD → download."""
        result = await self.fetch_pod_and_do_sender(
            container_number, download_dir, invoice_number=invoice_number
        )
        return result[0]

    async def fetch_pod_and_do_sender(
        self, container_number: str, download_dir: Path,
        invoice_number: str = "", skip_do_sender: bool = False
    ) -> tuple[Optional[Path], Optional[str]]:
        """Single TMS trip: search → grab DO SENDER → Document tab → download POD.

        Returns (pod_path, do_sender_email). Either or both may be None.
        If skip_do_sender=True, skips the Detail Info tab extraction (faster).
        """
        container_number = container_number.strip()

        try:
            vp = await self._page.evaluate(
                "() => `${window.innerWidth}x${window.innerHeight}`"
            )
            logger.info("[POD_DO] Starting: container='%s' viewport=%s",
                        container_number, vp)
        except Exception:
            pass

        # Step 1: Search for the container
        work_order_url = await self.search_container(container_number, invoice_number=invoice_number)
        if not work_order_url:
            grid_email = self._grid_do_sender
            if grid_email:
                logger.info("[POD_DO] Navigation failed but got DO SENDER from grid: %s", grid_email)
            else:
                logger.warning("[POD_DO] search_container returned None for %s", container_number)
            return (None, grid_email)

        # Step 2: Grab DO SENDER from Detail Info tab (skippable for email-only flows)
        do_sender = None
        if skip_do_sender:
            do_sender = self._grid_do_sender
            logger.info("[POD_DO] Skipping DO SENDER extraction (skip_do_sender=True), grid=%s", do_sender)
            # Go straight to Document tab via URL (skip detail page load)
            doc_url = work_order_url.replace("/detail-info/", "/document/")
            logger.info("[POD_DO] Direct nav to Document tab: %s", doc_url)
            try:
                await self._page.goto(doc_url, wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(2)
                docs_found = True
            except Exception as e:
                logger.warning("[POD_DO] Direct doc URL failed: %s — falling back", e)
                docs_found = False
            if not docs_found:
                docs_found = await self.navigate_to_documents_tab()
        else:
            do_sender = await self._extract_do_sender()
            if do_sender:
                logger.info("[POD_DO] D/O sender for %s: %s", container_number, do_sender)
            else:
                if self._grid_do_sender:
                    do_sender = self._grid_do_sender
                    logger.info("[POD_DO] D/O sender from grid fallback: %s", do_sender)
                else:
                    logger.warning("[POD_DO] No D/O sender found for %s", container_number)

            # Step 3: Navigate to Document tab
            docs_found = await self.navigate_to_documents_tab()
        if not docs_found:
            self._make_error("navigate_documents", f"Documents tab failed for {container_number}")
            return (None, do_sender)

        # Step 4: List documents and find POD
        docs = await self.list_documents()
        logger.info("Document table for %s: %d rows found", container_number, len(docs))

        pod_row = None
        for doc in docs:
            if doc.get("type") == "POD":
                pod_row = doc
                break

        if pod_row is None:
            ctx = await self._debug_rich("pod_row_missing")
            self._make_error("find_pod", f"No POD row in document table for {container_number}", ctx)
            return (None, do_sender)

        if not pod_row.get("has_file"):
            ctx = await self._debug_rich("pod_no_file_uploaded")
            self._make_error(
                "find_pod",
                f"POD row exists for {container_number} but no document uploaded yet",
                ctx,
            )
            return (None, do_sender)

        logger.info(
            "POD found for %s: %s",
            container_number, pod_row.get("filename", ""),
        )
        await self._debug_rich("before_pod_download")

        # Step 5: Download the POD
        pod_path = await self.download_document(
            "POD", download_dir, pod_row.get("filename", "")
        )

        # Step 6: Validate downloaded file
        if pod_path is None:
            ctx = await self._debug_rich("pod_download_returned_none")
            self._make_error("download_pod", f"download_document returned None for {container_number}", ctx)
            return (None, do_sender)

        valid, error_msg = self.validate_downloaded_file(pod_path)
        if not valid:
            self._make_error("download_pod", error_msg)
            return (None, do_sender)

        logger.info(
            "POD downloaded and verified for %s: %s (%d bytes)",
            container_number, pod_path.name, pod_path.stat().st_size,
        )
        return (pod_path, do_sender)
