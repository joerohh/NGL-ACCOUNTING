"""Data shapes returned by the TMS Data Layer."""

from dataclasses import dataclass, field
from typing import Literal, Optional

# Where each enriched field came from. "missing" means none of the sources had it.
FieldSource = Literal["qbo", "tms_api", "tms_browser", "missing"]


@dataclass
class EnrichedInvoice:
    """A QBO invoice enriched with data filled in from TMS where missing.

    Each field is the best value found across the cascade. The `sources` dict
    records which source provided each value, so the UI can show provenance.
    """
    wo_no: Optional[str]
    container_no: Optional[str]
    chassis: Optional[str]
    cnee: Optional[str]
    do_sender_email: Optional[str]
    sources: dict[str, FieldSource] = field(default_factory=dict)


@dataclass
class FailedRow:
    """One row in a job's failed-rows list. The UI shows these with Retry buttons."""
    row_id: str                           # opaque ID assigned by the data layer
    invoice_number: str
    container_number: Optional[str]
    operation: Literal["enrich_invoice", "get_document"]
    doc_type: Optional[str]               # populated only for get_document failures
    error_message: str                    # one-line preview for the UI
    failed_at_source: Literal["tms_api", "tms_browser"]
    timestamp: float                      # unix seconds
