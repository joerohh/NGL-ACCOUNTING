"""TMS Data Layer — layered cascade between tools and data sources.

QBO API (primary) → TMS API (fast fallback) → TMS browser (opt-in only).

See docs/superpowers/specs/2026-04-28-tms-data-layer-design.md.
"""

import asyncio
import logging
from pathlib import Path
from typing import Literal, Optional

from services.tms_data.browser_path import run_document_browser, run_enrich_browser
from services.tms_data.cascade import run_document, run_enrich
from services.tms_data.enriched_invoice import EnrichedInvoice, FailedRow
from services.tms_data.failed_rows import FailedRowsTracker

logger = logging.getLogger("ngl.tms_data")

Source = Literal["api", "browser"]


def _invoice_label(invoice_data: dict) -> str:
    """Return DocNumber, falling back to Id, falling back to '<unknown>'."""
    doc = invoice_data.get("DocNumber")
    if isinstance(doc, str) and doc.strip():
        return doc.strip()
    inv_id = invoice_data.get("Id")
    if inv_id:
        return str(inv_id)
    return "<unknown>"


class TMSDataLayer:
    """Single gateway between tools and the QBO/TMS sources.

    Tools call enrich_invoice / get_document / get_documents. Failures land
    in the per-job FailedRowsTracker; the UI reads them via get_failed_rows
    and the user can retry via retry_failed_row / retry_all_failed.
    """

    def __init__(self, qbo_api, tms_api, tms_browser) -> None:
        self._qbo_api = qbo_api
        self._tms_api = tms_api
        self._tms_browser = tms_browser
        self._failed = FailedRowsTracker()
        self._retry_ctx: dict[str, dict] = {}  # row_id -> {operation, invoice_data, force?, doc_type?, dest_dir?}
        # Per-job WO cache: (job_id, wo_no) -> wo_record. Avoids redundant TMS API
        # calls when the same job needs both enrich and document-fetch on one WO.
        self._wo_cache: dict[tuple[str, str], dict] = {}
        # In-flight tasks: (job_id, wo_no) -> asyncio.Task. Lets concurrent
        # coroutines that request the same WO await a single in-progress fetch
        # instead of each firing their own. Cleared on completion (success or
        # failure) so failed calls don't poison the slot for future retries.
        self._in_flight: dict[tuple[str, str], asyncio.Task] = {}

    # ── Per-row data access ────────────────────────────────────────

    class _CachedTmsApi:
        """Adapter that proxies tms_api but memoizes get_work_order per (job_id, wo_no).

        Both _wo_cache and _in_flight are shared across all _CachedTmsApi instances
        created within the same job (they live on the outer TMSDataLayer), so
        concurrent callers that request the same (job_id, wo_no) coalesce onto
        one Task and only hit the real API once.
        """

        def __init__(self, real_api, cache: dict, in_flight: dict, job_id: str) -> None:
            self._real = real_api
            self._cache = cache
            self._in_flight = in_flight
            self._job_id = job_id

        async def get_work_order(self, wo_no):
            key = (self._job_id, wo_no)
            if key in self._cache:
                return self._cache[key]
            if key not in self._in_flight:
                self._in_flight[key] = asyncio.create_task(
                    self._real.get_work_order(wo_no)
                )
            try:
                wo = await self._in_flight[key]
            finally:
                # Drop the task reference once resolved (success or failure) so
                # a failed call doesn't block subsequent retries.
                self._in_flight.pop(key, None)
            if wo is not None:
                self._cache[key] = wo
            return wo

        async def download_document(self, url):
            return await self._real.download_document(url)

        def is_configured(self):
            return self._real.is_configured()

    async def enrich_invoice(
        self,
        job_id: str,
        invoice_data: dict,
        source: Source = "api",
        force: bool = False,
    ) -> EnrichedInvoice:
        """Fill in missing chassis / CNEE / D/O sender from TMS.

        force=True bypasses the QBO-complete short-circuit (used by OEC for do_sender_email).
        Failures during the cascade are recorded in the failed-rows tracker
        but the partially-filled EnrichedInvoice is still returned.
        """
        if force and source == "browser":
            raise ValueError(
                "force=True is only supported for source='api'; "
                "the browser path has no QBO-complete short-circuit to bypass."
            )
        if source == "browser":
            enriched, err = await run_enrich_browser(invoice_data, self._tms_browser)
            failed_at = "tms_browser"
        else:
            cached = self._CachedTmsApi(self._tms_api, self._wo_cache, self._in_flight, job_id)
            enriched, err = await run_enrich(invoice_data, cached, force=force)
            failed_at = "tms_api"

        if err:
            row_id = self._failed.record_failure(
                job_id=job_id,
                invoice_number=_invoice_label(invoice_data),
                container_number=enriched.container_no,
                operation="enrich_invoice",
                doc_type=None,
                error_message=err,
                source=failed_at,
            )
            self._retry_ctx[row_id] = {
                "operation": "enrich_invoice",
                "invoice_data": invoice_data,
                "force": force,  # remember for retry
            }

        return enriched

    async def get_document(
        self,
        job_id: str,
        invoice_data: dict,
        doc_type: str,
        dest_dir: Path,
        source: Source = "api",
    ) -> Optional[Path]:
        """Fetch a single doc to disk. None if not found anywhere."""
        if source == "browser":
            invoice_number = str(invoice_data.get("DocNumber") or "")
            path, err = await run_document_browser(
                invoice_data, doc_type, dest_dir, self._tms_browser, invoice_number,
            )
            failed_at = "tms_browser"
        else:
            cached = self._CachedTmsApi(self._tms_api, self._wo_cache, self._in_flight, job_id)
            path, err = await run_document(
                invoice_data, doc_type, dest_dir, cached,
            )
            failed_at = "tms_api"

        if err:
            row_id = self._failed.record_failure(
                job_id=job_id,
                invoice_number=_invoice_label(invoice_data),
                container_number=None,
                operation="get_document",
                doc_type=doc_type,
                error_message=err,
                source=failed_at,
            )
            self._retry_ctx[row_id] = {
                "operation": "get_document",
                "invoice_data": invoice_data,
                "doc_type": doc_type,
                "dest_dir": dest_dir,
            }
        return path

    async def get_documents(
        self,
        job_id: str,
        invoice_data: dict,
        doc_types: list[str],
        dest_dir: Path,
        source: Source = "api",
    ) -> dict[str, Path]:
        """Fetch multiple docs. Returns dict of doc_type → path (only found ones)."""
        out: dict[str, Path] = {}
        for dt in doc_types:
            p = await self.get_document(job_id, invoice_data, dt, dest_dir, source)
            if p is not None:
                out[dt] = p
        return out

    # ── Failed-rows queries (more methods added in Tasks 11-13) ────

    def get_failed_rows(self, job_id: str) -> list[FailedRow]:
        return self._failed.get_rows(job_id)

    async def retry_failed_row(
        self,
        job_id: str,
        row_id: str,
        source: Source,
    ) -> bool:
        """Re-run a failed row's original op using the chosen source.

        Removes the row from the failed-rows list on success. Leaves it
        (with updated error) on continued failure.
        """
        row = self._failed.find_row(job_id, row_id)
        ctx = self._retry_ctx.get(row_id)
        if row is None or ctx is None:
            return False

        self._failed.remove_row(job_id, row_id)
        self._retry_ctx.pop(row_id, None)

        rows_before = len(self._failed.get_rows(job_id))

        if ctx["operation"] == "enrich_invoice":
            await self.enrich_invoice(
                job_id, ctx["invoice_data"],
                source=source, force=ctx.get("force", False),
            )
            rows_after = len(self._failed.get_rows(job_id))
            return rows_after == rows_before

        if ctx["operation"] == "get_document":
            path = await self.get_document(
                job_id, ctx["invoice_data"], ctx["doc_type"],
                ctx["dest_dir"], source=source,
            )
            rows_after = len(self._failed.get_rows(job_id))

            if path is not None:
                return True
            if rows_after > rows_before:
                # get_document already recorded a new failure (network error, etc.).
                return False
            # No path returned and no new failure recorded — WO genuinely has no such doc.
            new_row_id = self._failed.record_failure(
                job_id=job_id,
                invoice_number=_invoice_label(ctx["invoice_data"]),
                container_number=None,
                operation="get_document",
                doc_type=ctx["doc_type"],
                error_message=f"Document {ctx['doc_type']} not present on WO",
                source="tms_api" if source == "api" else "tms_browser",
            )
            self._retry_ctx[new_row_id] = ctx
            return False

        return False

    async def retry_all_failed(self, job_id: str, source: Source) -> dict:
        """Retry every row currently in the failed-rows list. Returns counts."""
        rows = self._failed.get_rows(job_id)
        succeeded = 0
        still_failed = 0
        for r in rows:
            ok = await self.retry_failed_row(job_id, r.row_id, source)
            if ok:
                succeeded += 1
            else:
                still_failed += 1
        return {"succeeded": succeeded, "still_failed": still_failed}

    def reset_for_new_job(self, job_id: str) -> None:
        """Clear all failed rows + retry context for a job."""
        rows = self._failed.get_rows(job_id)
        for r in rows:
            self._retry_ctx.pop(r.row_id, None)
        self._failed.reset(job_id)
        # Clear cached WO records for this job.
        for key in list(self._wo_cache.keys()):
            if key[0] == job_id:
                del self._wo_cache[key]
        # Clear in-flight task references for this job. Any coroutines already
        # awaiting these tasks will still receive their results — we just stop
        # tracking them here so the next job starts with a clean slate.
        for key in list(self._in_flight.keys()):
            if key[0] == job_id:
                del self._in_flight[key]
