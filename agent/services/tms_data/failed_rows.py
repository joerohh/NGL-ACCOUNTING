"""Per-job failed-rows tracker for the TMS Data Layer.

Each job (Invoice Sender batch, Chassis Finder run, etc.) accumulates failures
here. The tools' UIs read this list to render the Failed Rows box.
"""

import time
import uuid
from typing import Literal, Optional

from services.tms_data.enriched_invoice import FailedRow


class FailedRowsTracker:
    """In-memory store of failed rows, keyed by job_id."""

    def __init__(self) -> None:
        self._rows: dict[str, list[FailedRow]] = {}

    def record_failure(
        self,
        job_id: str,
        invoice_number: str,
        container_number: Optional[str],
        operation: Literal["enrich_invoice", "get_document"],
        doc_type: Optional[str],
        error_message: str,
        source: Literal["tms_api", "tms_browser"],
    ) -> str:
        """Record a failure. Returns the row_id (caller passes it back for retry)."""
        row_id = f"row-{uuid.uuid4().hex[:8]}"
        row = FailedRow(
            row_id=row_id,
            invoice_number=invoice_number,
            container_number=container_number,
            operation=operation,
            doc_type=doc_type,
            error_message=error_message,
            failed_at_source=source,
            timestamp=time.time(),
        )
        self._rows.setdefault(job_id, []).append(row)
        return row_id

    def get_rows(self, job_id: str) -> list[FailedRow]:
        """Return the current failed-rows list for a job (empty if unknown)."""
        return list(self._rows.get(job_id, []))

    def find_row(self, job_id: str, row_id: str) -> Optional[FailedRow]:
        """Look up a single failed row by job + row_id. None if not found."""
        for r in self._rows.get(job_id, []):
            if r.row_id == row_id:
                return r
        return None

    def remove_row(self, job_id: str, row_id: str) -> bool:
        """Remove a failed row (e.g., because retry succeeded). True if removed."""
        rows = self._rows.get(job_id)
        if not rows:
            return False
        for i, r in enumerate(rows):
            if r.row_id == row_id:
                rows.pop(i)
                return True
        return False

    def reset(self, job_id: str) -> None:
        """Clear all failed rows for a job (called when the job ends)."""
        self._rows.pop(job_id, None)
