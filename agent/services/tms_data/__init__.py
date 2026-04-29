"""TMS Data Layer — layered cascade between tools and data sources.

QBO API (primary) → TMS API (fast fallback) → TMS browser (opt-in only).

See docs/superpowers/specs/2026-04-28-tms-data-layer-design.md.
"""

import logging
from pathlib import Path
from typing import Literal, Optional

from services.tms_data.browser_path import run_document_browser, run_enrich_browser
from services.tms_data.cascade import run_document, run_enrich
from services.tms_data.enriched_invoice import EnrichedInvoice, FailedRow
from services.tms_data.failed_rows import FailedRowsTracker

logger = logging.getLogger("ngl.tms_data")

Source = Literal["api", "browser"]


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
        self._retry_ctx: dict[str, dict] = {}  # row_id -> {operation, invoice_data, doc_type, dest_dir}

    # ── Per-row data access ────────────────────────────────────────

    async def enrich_invoice(
        self,
        job_id: str,
        invoice_data: dict,
        source: Source = "api",
    ) -> EnrichedInvoice:
        """Fill in missing chassis / CNEE / D/O sender from TMS.

        Failures during the cascade are recorded in the failed-rows tracker
        but the partially-filled EnrichedInvoice is still returned.
        """
        if source == "browser":
            enriched, err = await run_enrich_browser(invoice_data, self._tms_browser)
            failed_at = "tms_browser"
        else:
            enriched, err = await run_enrich(invoice_data, self._tms_api)
            failed_at = "tms_api"

        if err:
            self._failed.record_failure(
                job_id=job_id,
                invoice_number=str(invoice_data.get("DocNumber") or ""),
                container_number=enriched.container_no,
                operation="enrich_invoice",
                doc_type=None,
                error_message=err,
                source=failed_at,
            )
            row_id_for_ctx = self._failed.get_rows(job_id)[-1].row_id
            self._retry_ctx[row_id_for_ctx] = {
                "operation": "enrich_invoice",
                "invoice_data": invoice_data,
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
            path, err = await run_document(
                invoice_data, doc_type, dest_dir, self._tms_api,
            )
            failed_at = "tms_api"

        if err:
            self._failed.record_failure(
                job_id=job_id,
                invoice_number=str(invoice_data.get("DocNumber") or ""),
                container_number=None,
                operation="get_document",
                doc_type=doc_type,
                error_message=err,
                source=failed_at,
            )
            row_id_for_ctx = self._failed.get_rows(job_id)[-1].row_id
            self._retry_ctx[row_id_for_ctx] = {
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

        # Remove the old failure entry first; new failures (if any) will be re-recorded.
        self._failed.remove_row(job_id, row_id)
        self._retry_ctx.pop(row_id, None)

        if ctx["operation"] == "enrich_invoice":
            await self.enrich_invoice(job_id, ctx["invoice_data"], source=source)
            # Success = no new failure for this invoice was just recorded.
            return not any(
                r.invoice_number == row.invoice_number and r.operation == "enrich_invoice"
                for r in self._failed.get_rows(job_id)
            )
        elif ctx["operation"] == "get_document":
            path = await self.get_document(
                job_id, ctx["invoice_data"], ctx["doc_type"],
                ctx["dest_dir"], source=source,
            )
            return path is not None
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
