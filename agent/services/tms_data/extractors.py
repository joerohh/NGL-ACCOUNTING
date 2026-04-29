"""Pure field extractors for QBO invoices and TMS WO records.

QBO extractors delegate to existing helpers in the codebase to avoid duplication.
TMS WO extractors live here because the TMS API client only exposes a subset.
"""

from typing import Optional

from services.job_manager.util import extract_wo_from_invoice
from services.qbo_api.invoices import QBOInvoicesMixin


# Single shared instance — the chassis/CNEE methods are pure (no I/O, no state).
_qbo_helper = QBOInvoicesMixin()


# ── QBO invoice extractors ──────────────────────────────────────────

def extract_wo_from_qbo(invoice_data) -> Optional[str]:
    """Pull the WO# from a QBO invoice's NGL REF# custom field."""
    return extract_wo_from_invoice(invoice_data)


def extract_chassis_from_qbo(invoice_data) -> Optional[str]:
    """Pull chassis number from a QBO invoice (custom field)."""
    if not isinstance(invoice_data, dict):
        return None
    try:
        return _qbo_helper._extract_chassis(invoice_data)
    except Exception:
        return None


def extract_cnee_from_qbo(invoice_data) -> Optional[str]:
    """Pull CNEE from QBO invoice CustomerMemo (arrow-chain routing)."""
    if not isinstance(invoice_data, dict):
        return None
    try:
        return _qbo_helper._extract_cnee(invoice_data)
    except Exception:
        return None
