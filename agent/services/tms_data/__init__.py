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

        return enriched

    # ── Failed-rows queries (more methods added in Tasks 11-13) ────

    def get_failed_rows(self, job_id: str) -> list[FailedRow]:
        return self._failed.get_rows(job_id)
